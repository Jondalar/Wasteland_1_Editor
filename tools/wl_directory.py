#!/usr/bin/env python3
"""
Wasteland area-directory decoder — STATIC, no runtime/trace.

The engine's resolve_id_to_ts ($2790) + W2759 reveal the on-disk directory:
each directory SECTOR is a chapter covering a track-band. Per id (4 bytes):
  byte0 = in-sector byte OFFSET where this area's compressed bitstream starts
  byte1 = SECTOR-STEP count: # of $FD0B "step-down" hops from the base T/S
  byte2 = LEN ($CE) = extra input sectors the decompressor may stream past start
  byte3 = SIDE (1-4) the data lives on (checked by W1897 via save-desc magic)
Base T/S = the dir sector's last two bytes: track=$5AFE(off254), idx=$5AFF(off255).

$FD0B steps DOWN: idx--, on underflow track-- (skipping T18) and idx=secs(track)-1.
The $C800 decompressor then streams from $5A00+offset, auto-advancing sectors
(WC8C5) in the same step-down order.

Directory sectors found on side 1 (loaded by different engine callers via immediate
idx into T35/T34): T35 idx10/13/12/15(=phys S9/S10/S4/S5) + T34 idx16(=phys S11) + more.
VERIFIED: T35/S10 id8 -> T21 phys5 off208 -> decodes byte-identical to the Desert
Nomads map (the starting overworld). See tools/wl_decompress.py.
"""
ILV = {  # idx -> physical sector, per 1541 zone
    'a': [0,10,20,9,19,8,18,7,17,6,16,5,15,4,14,3,13,2,12,1,11],   # T1-17  (21)
    'b': [0,8,16,5,13,2,10,18,7,15,4,12,1,9,17,6,14,3,11],          # T19-24 (19)
    'c': [0,7,14,3,10,17,6,13,2,9,16,5,12,1,8,15,4,11],             # T25-30 (18)
    'd': [0,6,12,1,7,13,2,8,14,3,9,15,4,10,16,5,11],                # T31-35 (17)
}
def secs(t): return 21 if t<=17 else 19 if t<=24 else 18 if t<=30 else 17
def zone(t): return 'a' if t<=17 else 'b' if t<=24 else 'c' if t<=30 else 'd'
def idx2phys(t,i): return ILV[zone(t)][i]
def phys2idx(t,p): return ILV[zone(t)].index(p)

# $C930[track] = the loader's per-track START block-index when crossing INTO a
# track (zone_step_down $FD0B: `lda $C930,x`).  NOT uniformly secs(t)-1: each disk
# ZONE's LAST track reserves its top sector(s) -> T17=0, T24=17, T30=16; interior
# tracks = secs(t)-1.  Source = the $C600 resident (resident_C600.prg, offset
# $332+t); verified byte-for-byte vs the GOLD disk trace's $C930 reads
# (live_mq4st63n, pc=$FD17) AND a byte-perfect combat-overlay replay
# ($7E00-$C5FF, 72/72 pages).  This is THE fix for the zone-boundary off-by-one.
C930_START = [None,
    20,20,20,20,20,20,20,20,20,20,20,20,20,20,20,20,  # T1-16  zone a interior
    0,                                                # T17    zone a LAST (reserves all but blk0)
    18,18,18,18,18,18,                                # T18-23 (T18 dir/skipped; zone b interior)
    17,                                               # T24    zone b LAST (reserves top sector)
    17,17,17,17,17,                                   # T25-29 zone c interior
    16,                                               # T30    zone c LAST (reserves top sector)
    16,16,16,16,16]                                   # T31-35 zone d interior

def step_down(t, i, n):
    """Apply $FD0B n times from (track t, block-index i).  On a track crossing the
    new block-index comes from the loader's $C930 table (C930_START), NOT secs-1 —
    zone-last tracks (T17/T24/T30) reserve their top sector(s)."""
    for _ in range(n):
        i -= 1
        if i < 0:
            t -= 1
            if t == 18: t -= 1          # loader skips the directory track (cpx #$12)
            if t < 1: return None        # ran off the start of the disk
            i = C930_START[t]            # loader's $C930[track] top block-index
    return (t, i)

def stream_sectors(t, i, extra):
    """Start (t,i) plus `extra` more step-down sectors = the input run."""
    seq = [(t, i)]
    ct, ci = t, i
    for _ in range(extra):
        nx = step_down(ct, ci, 1)
        if nx is None: break
        ct, ci = nx; seq.append((ct, ci))
    return seq

def decode_dir(sector_bytes, max_ids=64):
    """Return [(id, start_track, start_idx, start_phys, offset, len, side, sectors)]
    for every non-empty entry in a directory sector. sectors = [(track,phys),...]."""
    b = sector_bytes
    bt, bi = b[254], b[255]
    if not (1 <= bt <= 35 and 0 <= bi < secs(bt)):
        return None, (bt, bi)
    out = []
    for idn in range(max_ids):
        o = idn*4
        off, step, ln, side = b[o], b[o+1], b[o+2], b[o+3]
        if ln == 0 and off == 0 and step == 0:      # empty slot
            continue
        st = step_down(bt, bi, step)
        if st is None: continue
        t, i = st
        if not (1 <= t <= 35 and 0 <= i < secs(t)): continue
        seq = stream_sectors(t, i, ln)
        phys = [(tt, idx2phys(tt, ii)) for tt, ii in seq]
        out.append((idn, t, i, idx2phys(t, i), off, ln, side, phys))
    return (bt, bi), out

if __name__ == "__main__":
    import glob, os, sys
    G = sys.argv[1] if len(sys.argv) > 1 else \
        'analysis/g64/wasteland_s1[ea_interplay_1988](!)'
    print(f"# Directory scan: {G}\n")
    # candidate dir sectors: all of T35 + T34 (engine loads dirs from these)
    cands = [(35, s) for s in range(17)] + [(34, s) for s in range(17)]
    for t, s in cands:
        f = f"{G}/track-{t}/t{t:02d}s{s:02d}.bin"
        if not os.path.exists(f): continue
        b = open(f, 'rb').read()
        base, ents = decode_dir(b)
        if base is None or not ents or len(ents) < 4:
            continue
        # heuristic: a real dir has many side-1 entries with plausible offsets
        side1 = [e for e in ents if e[6] == 1]
        if len(side1) < 4: continue
        idx = phys2idx(t, s)
        print(f"=== T{t}/S{s} (idx{idx})  base=T{base[0]}/idx{base[1]}  "
              f"{len(ents)} entries ===")
        for (idn, st, si, sp, off, ln, side, phys) in ents:
            tracks = sorted(set(p[0] for p in phys))
            print(f"  id{idn:2d}: T{st} phys{sp} off={off:3d} len={ln:2d} "
                  f"side={side}  tracks={tracks}")
        print()
