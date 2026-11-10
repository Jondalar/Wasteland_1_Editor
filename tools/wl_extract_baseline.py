#!/usr/bin/env python3
"""
Wasteland ASSET BASELINE extractor — ALL 4 sides, from the pristine never-played
blank_s{N}.d64 copies (standard .d64 sectors, no g64/GCR needed).

Table-driven: each side's T35 holds its own MAP(idx13)/GFX(idx12)/STR(idx10)/
DESC(idx14) directories (entries with side-byte==N, len>0). Decode path is the
byte-verified Desert-Nomads pipeline reused from wl_extract_side (i2p de-interleave
+ step_down + the $C8EC depacker). Per-kind step-correction: MAP +0, GFX +1, STR +0.

Outputs baseline/side{N}/s{N}_{kind}_id{NN}.bin + an INVENTORY.json. Side 1 acts as
a regression gate against the existing areas/ extraction.

WL_MACHINE=apple reads the Apple II release instead (five .dsk images, DOS 3.3 logical
order). Same directories, same entry semantics, same depacker — only the disk geometry
changes, and that lives entirely in wl_a2disk.py. The output filenames are identical, so
an Apple data root is a drop-in for a C64 one; keep them in separate roots (WL_DATA).
See docs/APPLE2.md.

Usage: python3 wl_extract_baseline.py
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
import wl_extract_side as X   # reuse the proven decode logic
import wl_g64decode            # canonical g64 (GCR) source
import wl_a2disk               # Apple II .dsk geometry (no GCR, no zones, no interleave)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
# 'g64' is the DEFAULT here: a .g64 is what a preservation-grade dump of an original disk is, and
# it needs nothing but the file itself. ('d64' reads the vanilla playable copies the game's own
# Utils disk writes — the upstream default, but those only exist if you made them.)
SRC  = os.environ.get('WL_SRC', 'g64')
OUT  = os.environ.get('WL_BASELINE_OUT') or f'{ROOT}/data/baseline'
# 'c64' (default) or 'apple'. The machine decides three things and nothing else: which
# container the sectors come out of, where the directories sit, and whether there is a
# charset to extract. Everything downstream — entry semantics, step-down, depacker — is
# shared, which is the finding docs/APPLE2.md rests on.
MACHINE = os.environ.get('WL_MACHINE', 'c64')

def _disk(n, ext):
    """Side n's image. WL_DISK1..4 name the files outright; WL_DISKS points at a directory and
    any filename containing 's<n>' (or 'side<n>') wins. Nothing found -> a clear error, never a
    silent half-extraction."""
    env = os.environ.get(f'WL_DISK{n}')
    if env:
        return env
    d = os.environ.get('WL_DISKS')
    if d and os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            lf = f.lower()
            # 's2'/'side2' name the C64 sides; the Apple masters are wastmst2.dsk, so a
            # trailing side digit counts too (and wastboot.dsk, which is master 1 plus boot
            # code, correctly matches nothing).
            if lf.endswith('.' + ext) and (f's{n}' in lf or f'side{n}' in lf
                                           or lf.endswith(f'{n}.{ext}')):
                return os.path.join(d, f)
    raise SystemExit(
        f"no side-{n} .{ext} image found.\n"
        f"  Point at your own disk images:  WL_DISKS=/path/to/images  (or WL_DISK{n}=<file>)\n"
        f"  This project ships no game data; see README section 'Bring your own disks'.")

G64   = lambda n: _disk(n, 'g64')
BLANK = lambda n: _disk(n, 'd64')
DSK   = lambda n: _disk(n, 'dsk')

SPT = [21]*17 + [19]*7 + [18]*6 + [17]*5     # sectors/track, tracks 1..35
def toff(t): return sum(SPT[:t-1]) * 256
def d64_sec(d64, t, p): return d64[toff(t)+p*256 : toff(t)+p*256+256]

# kind -> (dir idx, step-correction, $CD cap)  caps: map $EE->4096, str $F0->4096
# CAVEAT (2026-07-10): str has TWO caps at runtime. area_change_check picks the buffer from bit7
# of the str id: bit7=0 -> $CA00/$CD=$D0 (1536 B), bit7=1 -> $E000/$CD=$F0 (4096 B). The $C800
# codec stops at $CD, so the 8 blobs longer than 1536 B lose their tail in the SMALL window. We
# decode with the LARGE cap here (both slots share one LUT key, e.g. str 59 -> death overlay) and
# let wl_build_lut.py emit a truncated kind-$0B row for the small one. See STR_WINDOW_CAP.md.
# gfx corr=0 (NOT +1): gold trace live_mq4st63n proves area-0 gfx starts at T25/blk7 =
# step_down(base=T25/idx7, step+0); the old +1 was an off-by-one (started one sector too far ->
# garbage tile-charset -> broken multicolor charmap). Same resolution rule as map (corr 0).
# gfx cap = 3456 ($DD80-$D000), NOT 3584: our codec OVERSHOOTS the gold length (gold area-0 gfx
# = 3400 B = $D000-$DD47); 3584 B would write $D000-$DDFF and CLOBBER the $DD80 block-mover (-> the
# game JAMs when movement calls jsr $DD80). Cap at the $DD80 boundary so gfx ends <= $DD7F.
KINDS = [('map', 0, 4096), ('gfx', 0, 3456), ('str', 0, 4096)]   # kind, step-correction, $CD cap

# Where each directory sits: (track, block-index). The C64 keeps all of them on T35 except
# scene, which is on T34/idx16. Block index, not physical sector — i2p resolves it.
C64_DIRS = {
    'str_hi':    (35, 9),    # ids 64..127, sides 2+4 only
    'str':       (35, 10),
    'charset':   (35, 11),   # 1 raw sector, shared per side (the Apple has none: depacker)
    'gfx':       (35, 12),
    'map':       (35, 13),
    'record':    (35, 14),
    'areatrack': (35, 15),
    'scene':     (34, 16),
}
DIRS = C64_DIRS if MACHINE == 'c64' else wl_a2disk.DIRS


def cap_warn(n, kind, e, dec, cap):
    """The caps above are the C64's $CD destination-page limits — facts about the C64's
    memory map, not about the data. On the C64 they are load-bearing (a gfx blob that runs
    past $DD80 clobbers the block-mover and the game jams). On the Apple they are a number
    we carried over with no evidence behind it: the Apple engine was never disassembled.

    So say so when one actually bites. A map at 4096 is a full 64x64 grid and means
    nothing; anything else landing exactly on its cap was probably cut short."""
    if MACHINE == 'apple' and kind != 'map' and len(dec) == cap:
        print(f"  WARNING side{n} {kind} id{e}: stopped exactly at the C64 cap {cap} — "
              f"this blob is probably truncated. The Apple's real limit is unknown.",
              file=sys.stderr)

def src_sec(n):
    """sec(t,phys)->256B reader for side n.
    d64 (DEFAULT) = blank_s{n}.d64 = the vanilla, unused INITIAL game state = the
      disk you actually play on. Correct source for maps + savestate.
    g64 = the 4 original disk sides (masters). Fallback: the d64 blanks have a few
      fill-band-zeroed sectors (T24/S11, T30/S11, T17-18) that fall inside some
      gfx/str/record runs (e.g. side1 gfx id0 zeros from byte ~2648); g64 has them
      intact. MAPS (idx13) are byte-identical d64==g64. See wasteland-asset-baseline.
    dsk = the Apple II masters. Nothing to undo: 35x16x256 in logical order."""
    if MACHINE == 'apple':
        im = wl_a2disk.load(DSK(n))
        return lambda t, p: wl_a2disk.sec(im, t, p)
    if SRC == 'g64':
        dec = wl_g64decode.decode_g64(G64(n))      # {track:{phys_sector:256B}}
        return lambda t, p: dec[t][p]
    d64 = open(BLANK(n), 'rb').read()
    return lambda t, p: d64_sec(d64, t, p)

def extract_side(n):
    X.sec = src_sec(n)                             # point the decoder at this side's source
    if MACHINE == 'apple':
        # The only two pieces of 1541 in the decode path. Both are module globals in
        # wl_extract_side, so build_stream and read_dir pick these up as they are called.
        X.i2p = wl_a2disk.i2p                      # identity: no interleave on the Apple
        X.step_down = wl_a2disk.step_down          # 16 sectors/track, no zones, no T18 skip
    outdir = f'{OUT}/side{n}'; os.makedirs(outdir, exist_ok=True)
    inv = []
    for kind, corr, cap in KINDS:
        base, ents, phys = X.read_dir(*DIRS[kind])
        for (e, off, stp, ln, sd) in ents:
            if e == 63 or ln == 0:
                continue
            s = X.step_down(base[0], base[1], stp + corr)
            if not s:
                inv.append(dict(side=n, kind=kind, id=e, status='off-disk', off=off, step=stp, len=ln, sidebyte=sd))
                continue
            st, si = s
            dec = X.decode_until_eoi(X.build_stream(st, si, off, ln + 2), ln, cap)
            cap_warn(n, kind, e, dec, cap)
            cls = X.classify(dec)
            fn = f'{outdir}/s{n}_{kind}_id{e:02d}.bin'
            open(fn, 'wb').write(dec)
            inv.append(dict(side=n, kind=kind, id=e, track=st, phys=X.i2p(st, si),
                            off=off, step=stp, len=ln, sidebyte=sd, out=len(dec),
                            classify=cls, file=os.path.relpath(fn, ROOT)))

    # STR HIGH-ID directory (ids 64-127) — the SECOND str directory sector.
    # resolve_id_to_ts ($2790) steps the str dir DOWN one block-idx for id>=$40
    # (zone_step_down $FD0B: dex + $C930 interleave). id*4 wraps mod 256 = 64 ids/sector, so
    # the str directory is 2 sectors: blk10 (ids 0-63, the KINDS loop above) + blk9 (ids 64-127,
    # here). blk9 = ILV['d'][9] = t35s3. ONLY STR uses it (portraits / shared pool > 63); MAP/GFX/
    # RECORD are area-id-keyed (0-63) and SCENE's high sector (T34/blk15) is empty on all sides.
    # Sides 2 (id78=Christina) + 4 (ids 64-77) carry entries; sides 1+3 empty (base T/S = 0,0).
    # e here = actual id 64+e; e==63 = the phantom footer (id 127 = the base-T/S bytes), skip.
    # Was the completeness gap: our extraction only read the base sector -> Christina rendered a
    # side-4 robot (loader `& $3F` collapsed 78->14). See cart_EF/docs/port/EXTRACTION_COMPLETENESS.md.
    hbase, hents, hphys = X.read_dir(*DIRS['str_hi'])
    if 1 <= hbase[0] <= 35:                            # valid dir (sides 2+4); 1+3 = (0,0) -> skip
        for (e, off, stp, ln, sd) in hents:
            if e == 63 or ln == 0:                     # 63 = phantom (real id 127 = footer)
                continue
            rid = 64 + e
            s = X.step_down(hbase[0], hbase[1], stp)    # str corr = 0
            if not s:
                inv.append(dict(side=n, kind='str', id=rid, status='off-disk',
                                off=off, step=stp, len=ln, sidebyte=sd))
                continue
            st, si = s
            dec = X.decode_until_eoi(X.build_stream(st, si, off, ln + 2), ln, 4096)
            cap_warn(n, 'str', rid, dec, 4096)
            fn = f'{outdir}/s{n}_str_id{rid:02d}.bin'
            open(fn, 'wb').write(dec)
            inv.append(dict(side=n, kind='str', id=rid, track=st, phys=X.i2p(st, si),
                            off=off, step=stp, len=ln, sidebyte=sd, out=len(dec),
                            classify=X.classify(dec), file=os.path.relpath(fn, ROOT)))

    # AREA RECORD (idx14): the per-area logical area = map-cell grid + relational
    # tables. RAW (uncompressed) — loaded by the engine via load_one_part -> $FF37.
    # Entry format DIFFERS from the codec dirs: b0=TRACK, b1=BLOCK-IDX, b2=LEN(sectors).
    # Verified command-by-command against trace live_mpybxx11 (id0 -> T20/blk9, 35
    # sectors raw = the $BB-run record; classify MAP/terrain). NOT decoded, NOT
    # base/step/off. Read `len` full sectors from (track,blk) descending (step_down).
    rbase, rents, rphys = X.read_dir(*DIRS['record'])
    for (e, trk, blk, ln, sd) in rents:
        if e == 63 or ln == 0 or sd != n:
            continue
        raw = bytearray(); t, i = trk, blk
        for _ in range(ln):
            raw += X.sec(t, X.i2p(t, i))
            nx = X.step_down(t, i, 1)
            if nx is None:
                break
            t, i = nx
        fn = f'{outdir}/s{n}_record_id{e:02d}.bin'
        open(fn, 'wb').write(bytes(raw))
        inv.append(dict(side=n, kind='record', id=e, track=trk, blk=blk,
                        len=ln, sidebyte=sd, out=len(raw),
                        classify=X.classify(bytes(raw)), file=os.path.relpath(fn, ROOT)))

    # AREA-TRACK (idx15): per-area-"track"/sub-level data block -> $3100 (set_dir_ptr_3100,
    # kind $0F). RAW like the record (writer pc $FF37, NOT the $C800 codec — verified byte-exact
    # vs disk trace live_mq4st63n $3100). Loaded by area_track_change on $75 change; read at +$73
    # by block2 $144E + combat $BC8E. Only 4 game-wide: s1 id0+1, s2 id2, s4 id4 (special areas).
    # Same dir-entry format as record: b0=TRACK, b1=BLOCK-IDX, b2=LEN(sectors). See docs/loader/AREA_LOAD.md.
    tbase, tents, tphys = X.read_dir(*DIRS['areatrack'])
    for (e, trk, blk, ln, sd) in tents:
        if e == 63 or ln == 0 or sd != n:
            continue
        raw = bytearray(); t, i = trk, blk
        for _ in range(ln):
            raw += X.sec(t, X.i2p(t, i))
            nx = X.step_down(t, i, 1)
            if nx is None:
                break
            t, i = nx
        fn = f'{outdir}/s{n}_areatrack_id{e:02d}.bin'
        open(fn, 'wb').write(bytes(raw))
        inv.append(dict(side=n, kind='areatrack', id=e, track=trk, blk=blk,
                        len=ln, sidebyte=sd, out=len(raw),
                        classify=X.classify(bytes(raw)), file=os.path.relpath(fn, ROOT)))

    # SCENE (idx16, kind $10): per-area special-handler CODE -> $CA00. CODEC-compressed,
    # dir on T34/blk16 (NOT T35!). Entry format = codec (off, step, len, side); corr=0.
    # Loaded at runtime by load_scene_overlay (block3 $04F3) which on disk runs the $C800
    # codec; the EF pre-decodes it here so the codec never runs. Verified byte-exact vs gold
    # trace live_mq4st63n (id3: off76/step7/len1 -> T25/blk8 -> 334 B at $CA00). Same decode
    # path as map/gfx/str. SPARSE: only scene-areas (Ranger Center etc.) have entries.
    cbase, cents, cphys = X.read_dir(*DIRS['scene'])
    for (e, off, stp, ln, sd) in cents:
        if e == 63 or ln == 0 or sd != n:
            continue
        s = X.step_down(cbase[0], cbase[1], stp)            # corr 0
        if not s:
            inv.append(dict(side=n, kind='scene', id=e, status='off-disk', off=off, step=stp, len=ln, sidebyte=sd))
            continue
        st, si = s
        dec = X.decode_until_eoi(X.build_stream(st, si, off, ln + 2), ln, 4096)
        cap_warn(n, 'scene', e, dec, 4096)
        fn = f'{outdir}/s{n}_scene_id{e:02d}.bin'
        open(fn, 'wb').write(dec)
        inv.append(dict(side=n, kind='scene', id=e, track=st, phys=X.i2p(st, si),
                        off=off, step=stp, len=ln, sidebyte=sd, out=len(dec),
                        classify=X.classify(dec), file=os.path.relpath(fn, ROOT)))

    # SHARED scene CHARSET (idx11): direct (T35, block-idx 11), 1 sector RAW -> $C800.
    # Per-side shared font, NOT id-keyed (load_scene_overlay -> load_to_C800, count=1).
    # Verified in the area-load manifest (trace live_mpybxx11): T35/blk11 -> $C800.
    # The Apple has nothing here — its T34/S0B holds the depacker — so DIRS omits the key
    # and no charset is written. Where the Apple keeps its font is not known; it was not
    # part of the format comparison. See docs/APPLE2.md 'Open'.
    if 'charset' in DIRS:
        ct, ci = DIRS['charset']
        cs = X.sec(ct, X.i2p(ct, ci))
        fn = f'{outdir}/s{n}_charset.bin'
        open(fn, 'wb').write(cs)
        inv.append(dict(side=n, kind='charset', shared=True, track=ct, blk=ci, out=len(cs),
                        classify=X.classify(cs), file=os.path.relpath(fn, ROOT)))
    return inv

if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    allinv = []
    for n in (1, 2, 3, 4):
        inv = extract_side(n)
        allinv += inv
        kc = {}
        for r in inv:
            kc[r['kind']] = kc.get(r['kind'], 0) + 1
        print(f"SIDE {n}: " + ", ".join(f"{k}={v}" for k, v in sorted(kc.items())))
    json.dump(allinv, open(f'{OUT}/INVENTORY.json', 'w'), indent=1)
    # classify sanity histogram
    from collections import Counter
    c = Counter(r.get('classify', 'n/a').split('(')[0].strip() for r in allinv if 'classify' in r)
    print("\nclassify histogram:", dict(c))
    print("total assets:", sum(1 for r in allinv if 'out' in r), "-> baseline/INVENTORY.json")
