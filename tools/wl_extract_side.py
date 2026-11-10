#!/usr/bin/env python3
"""
Wasteland SIDE-1 area extractor — STATIC, reads rawscan/ (all 684 sectors).
Per-directory sector-step correction calibrated against the byte-verified Desert
Nomads ground truth: MAP dir (T35/S10) = +0, GFX dir (T35/S4) = +1.
out_len solved by decoding until the input run (start[off:] + `len` sectors) is
exhausted (engine stops on the $CE input-sector countdown, not a fixed size).
Outputs: inventory table + decoded .bin per entry + an ASCII render of each map.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from wl_decompress import WC8EC
from wl_directory import C930_START      # loader $C930 per-track start block-index (zone-boundary fix)

ROOT = os.environ.get('C64RE_PROJECT_DIR') or os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
BASE = f'{ROOT}/analysis/disk/wasteland_s1[ea_interplay_1988](!)'
RAW  = f'{BASE}/rawscan'
OUT  = f'{BASE}/areas'
def sec(t, p): return open(f"{RAW}/t{t}s{p}.bin", "rb").read()

ILV = {'a':[0,10,20,9,19,8,18,7,17,6,16,5,15,4,14,3,13,2,12,1,11],
       'b':[0,8,16,5,13,2,10,18,7,15,4,12,1,9,17,6,14,3,11],
       'c':[0,7,14,3,10,17,6,13,2,9,16,5,12,1,8,15,4,11],
       'd':[0,6,12,1,7,13,2,8,14,3,9,15,4,10,16,5,11]}
def secs(t): return 21 if t<=17 else 19 if t<=24 else 18 if t<=30 else 17
def zone(t): return 'a' if t<=17 else 'b' if t<=24 else 'c' if t<=30 else 'd'
def i2p(t,i): return ILV[zone(t)][i]
def step_down(t,i,n):
    for _ in range(n):
        i-=1
        if i<0:
            t-=1
            if t==18: t-=1
            if t<1: return None
            i=C930_START[t]            # loader $C930[track], NOT secs(t)-1 (zone-last tracks reserve top sectors)
    return (t,i)

class EndOfInput(Exception): pass
class BR:
    def __init__(s,d,p=0): s.d=d; s.p=p; s.c=0; s.b=0; s.reads=0
    def bit(s):
        if s.c==0:
            if s.p>=len(s.d): raise EndOfInput()
            s.b=s.d[s.p]; s.p+=1; s.c=8; s.reads+=1   # 1 stream byte = 1 codec refill ($CE/$E5)
        s.c-=1; return (s.b>>s.c)&1
    def bits(s,n):
        v=0
        for _ in range(n): v=(v<<1)|s.bit()
        return v

def decode_until_eoi(buf, ce_len, cap=16384):
    """Faithful $C800 codec. Stops at min($CE==0 after ce_len*256 input-byte refills,
    output page==$CD i.e. len==cap). $CE is checked BEFORE emit, so the in-progress
    symbol at the boundary is discarded. RLE replay reads no input. ce_len = dir len byte."""
    br=BR(buf); out=bytearray(); run=0; runv=0; budget=ce_len*256
    D=[br.bits(8) for _ in range(20)]          # 20-byte dict preload (counts toward $CE)
    try:
        while True:
            if run>0: a=runv; run-=1
            elif br.bit()==0: a=br.bits(8)
            else:
                code=br.bits(3); a=None
                if code<=1: a=D[code]
                else:
                    for lo,hi in ((2,8),(9,16),(17,19)):
                        code=(code<<1)|br.bit()
                        for y in range(hi,lo-1,-1):
                            if code==WC8EC[y]: a=D[y]; break
                        if a is not None: break
                    if a is None:
                        run=br.bits(8); runv=br.bits(8); run=(run-1)&0xFF; a=runv
            if br.reads>=budget: break          # ldx $CE; beq -> discard this symbol, stop
            out.append(a)
            if len(out)>=cap: break             # output page == $CD
    except EndOfInput:
        pass
    return bytes(out)

def build_stream(start_t, start_i, off, extra):
    parts=[sec(start_t, i2p(start_t,start_i))[off:]]
    t,i=start_t,start_i
    for _ in range(extra):
        nx=step_down(t,i,1)
        if nx is None: break
        t,i=nx; parts.append(sec(t,i2p(t,i)))
    return b"".join(parts)

def classify(b):
    if not b: return "EMPTY"
    n=len(b); mc=sum(1 for x in b if x in (0x55,0xAA,0xFF))/n; lo=sum(1 for x in b if x<0x60)/n
    if mc>0.12: return f"GFX/pixels (mc={mc:.2f})"
    if lo>0.6:  return f"MAP/terrain (lo={lo:.2f})"
    return f"data/struct (mc={mc:.2f} lo={lo:.2f})"

def read_dir(track, idx):
    p=i2p(track, idx); b=sec(track,p)
    ents=[]
    for n in range(64):
        o=n*4; off,stp,ln,sd=b[o],b[o+1],b[o+2],b[o+3]
        if ln==0 and off==0 and stp==0: continue
        ents.append((n,off,stp,ln,sd))
    return (b[254],b[255]), ents, p

def render_map(b, label, width=64):
    """ASCII render: map each distinct terrain byte to a glyph."""
    glyphs=" .,:;+oxX#%@=-~^*ABCDEFGHJKLMNPQRSTUVWZ0123456789"
    seen={};
    def g(v):
        if v not in seen: seen[v]=glyphs[len(seen)%len(glyphs)]
        return seen[v]
    rows=[]
    for r in range(0, min(len(b), width*40), width):
        rows.append("".join(g(x) for x in b[r:r+width]))
    return rows, seen

if __name__=="__main__":
    os.makedirs(OUT, exist_ok=True)
    # dir-key -> (track, idx, step_correction, kind, $CD cap)  caps: map $EE->4096, gfx $DE->3584, str $F0->4096
    DIRS=[("MAP",35,13,0,"map",4096), ("GFX",35,12,0,"gfx",3456), ("STR/party",35,10,0,"str",4096)]  # GFX corr 0 (off-by-one fix); cap 3456=$DD80-$D000 so gfx overshoot can't clobber the $DD80 block-mover
    print("=== SANITY vs ground truth ===")
    ref=open(f"{BASE}/area_desert_nomads_decompressed.bin","rb").read()
    mp=decode_until_eoi(build_stream(21,3,208,4),2,4096); gf=decode_until_eoi(build_stream(24,16,8,12),10,3584)
    print(f"  DN map {len(mp)}B match={mp==ref[:1135]} | DN gfx {len(gf)}B match={gf==ref[1135:4719]}\n")

    inventory=[]
    for (key,track,idx,corr,kind,cap) in DIRS:
        base, ents, phys = read_dir(track, idx)
        print(f"========== {key} dir  T{track}/S{phys}(idx{idx})  base=T{base[0]}/idx{base[1]}  corr={corr:+d}  {len(ents)} entries ==========")
        for (n,off,stp,ln,sd) in ents:
            if n==63: continue
            s=step_down(base[0],base[1],stp+corr)
            if not s: print(f"  id{n:2d}: off={off} step={stp} len={ln} -> off-disk"); continue
            st,si=s
            dec=decode_until_eoi(build_stream(st,si,off,ln+2),ln,cap)
            cls=classify(dec)
            fn=f"{OUT}/s1_{key.lower().split('/')[0]}_id{n:02d}.bin"
            open(fn,"wb").write(dec)
            print(f"  id{n:2d}: T{st}/phys{i2p(st,si)} off={off:3d} len={ln} -> {len(dec):5d}B  {cls}")
            inventory.append((key,n,st,i2p(st,si),off,ln,len(dec),cls))

    # render the start area (DN map = MAP id8) + neighbours
    print("\n========== START AREA render: MAP id8 (Desert Nomads overworld) ==========")
    dn=decode_until_eoi(build_stream(21,3,208,4),2,4096)
    for w in (64,48,32):
        rows,_=render_map(dn,"DN",w)
        # crude periodicity score: how many rows repeat-ish
        print(f"\n-- width {w} (first {min(len(rows),24)} rows of {len(dn)}B) --")
        for ln in rows[:24]: print("  "+ln)
        break  # show width 64 only by default
