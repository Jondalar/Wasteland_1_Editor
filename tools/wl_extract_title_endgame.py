#!/usr/bin/env python3
"""The title screen and the endgame scenes — the assets the World tab's Title and Endings
panes want, and which nothing was extracting.

These blobs are not in any directory. The engine loads them by hardcoded track and block,
so there is nothing to look up: the addresses below ARE the data, reversed in the parent
cartridge project and carried over here with their provenance.

  TITLE, side 1
    T4/blk1   2 pages RAW           -> $CA00   the WASTELAND logo, 64 glyphs of 8 B
    T5/blk16  codec, $CE budget 20  -> $8A00   the globe, 6400 B, an anim-object in the same
                                               container as a portrait ($A3 - $8A = 25 pages)

  ENDGAME, side 4 — the Cochise-Core handler loads these through a table-indexed loader
  (key tables at $CCE5 track / $CCE8 block / $CCEB budget / $CCEE dest page), which is why a
  scan for immediate loads never finds them.
    T3/blk2   codec, budget 11      -> $3400, stop $4200   the Cochise dome
    T2/blk12  codec, budget 13      -> $3400, stop $4500   the ranger-death skeletons
    T1/blk20  codec, budget 13      -> $3400, stop $4400   the all-dead finale
    T3/blk4   2 pages RAW           -> $3400               the skeleton stage, unused by the editor

APPLE II
--------
The same pictures exist, in the same anim-object container as an Apple portrait
(backend/wl_portrait_a2.py), packed with the same codec, and off-directory just like the C64's.
They are NOT the same bytes and they are NOT at the same track/block, so this is a second table,
not a parameterised first one. What they are and how each number was arrived at:

  TITLE, master 1 (wastmst1.dsk)
    T04/S06   codec, budget 22  ->  6687 B   the globe

    Read straight out of the Apple title module, which the crack disk keeps at T34/S08..S0F and
    which runs at $6000 (the original boot path is overwritten on these images, the module is
    not). At $61EC it does, verbatim:

        61EC  lda #$00 / sta $BC ; lda #$6C / sta $BD    the blob lands at $6C00
        61F6  clc / adc #$1C / sta $D0                   stop page $88  -> cap 7168
        61FB  ldx #$06 / stx $0F ; ldy #$04 / sty $0E    track 4, sector 6
        6203  jsr $0A5E                                  read the first sector
        6206  lda #$16 / sta $D1                         the codec budget: 22 input sectors
        620C  ldx $BC / ldy $BD / jsr $407C              -> $0A82, the depacker at $FF00

    $D1/$D0 are the C64 codec's $CE/$CD, three zero-page bytes higher — the same +3 the depacker
    itself carries (docs/APPLE2.md §4). The budget bites before the cap: 22 sectors of input
    yield 6687 B, well short of 7168.

  ENDGAME, master 4 (wastmst4.dsk) — tracks 0 to 3, which no directory on that disk reaches
    T03/S0F  codec, budget 15  ->  4560 B   the Cochise dome, 1 animation slot
    T02/S0C  codec, budget 12  ->  3554 B   the same dome — byte-identical base stream — but a
                                            different animation over it, 2 slots
    T02/S00  codec, budget 16  ->  5162 B   the death silhouette (the C64's "skeletons")
    T01/S00  codec, budget 17  ->  5163 B   the all-dead finale (ranch, mountains, ranger)

    These four were NOT read out of the engine — see 'What is still open' below. Each was found
    by depacking every sector start on all five disks and keeping the ones whose first bytes are
    a well-formed anim-object header, then rendering the result and comparing it against the C64
    blob it corresponds to. They match picture for picture.

    The budgets are measured, not guessed, and three of the four are forced:

      * the LOWER bound is completeness. Walking the base stream and every animation slot to its
        delay-0 restart gives the object's true extent (4374 / 3518 / 5143 / 4948 B); re-running
        the depacker and counting input bytes gives what that costs (14.44 / 11.84 / 15.91 /
        16.05 sectors). Anything smaller truncates the picture.
      * the UPPER bound is the next blob down. The streams descend, and these four tile tracks
        3..0 end to end: T03/S0F..S01, [4 sectors of a stale duplicate], T02/S0C..S01,
        T02/S00..T01/S01, T01/S00..T00/S00. For dome_anim, skeletons and finale the two bounds
        meet on one number. Only the dome has slack (15 or 16), and 15 is the one that stops
        before T03/S00, where another codec stream ('FF 00 80 …') begins.

    The same method reproduces the title globe's 22 exactly, which is the check that it works.

    The stale duplicate is T03/S00 + T02/S0F..S0D: T02/S0E is byte-identical to T02/S0C and
    T02/S0D to T02/S0B, and no start offset in any of those four sectors opens a well-formed
    object. Depacking from them yields the dome picture with a corrupted right edge — the codec
    partially resynchronises — which is why a header scan alone is not enough to place a blob.

  What is still open: the Apple engine's own load table for the four endgame blobs. The title
  globe's is quoted above. For the endgame nothing was found: the resident ($0800-$36FF plus
  $D000-$D3FF / $F100-$F2FF / $F700-$FEFF, loaded by the title module's five hardcoded reads) has
  exactly one place that sets the codec budget ($2CD4) and it is the directory-driven path
  ($2CC5, called four times: str / scene / map / gfx). A search of all five images for every
  other write to $D1, for every call to the depacker wrapper $0A82 and its hi-res-hole stub
  $407C, and for every call to the raw sector loader $2D4C found nothing outside that path and
  the title module. So the code that loads these four has not been read, and the budgets above
  rest on the tiling argument rather than on an instruction.

  There is no Apple counterpart to the C64 'logo'. The Apple title screen draws its text with the
  engine's ordinary renderer — the title module loads exactly one asset, the globe — so that
  entry is absent from the Apple table rather than faked.

    python3 tools/wl_extract_title_endgame.py            # WL_DISKS / WL_DISK1..4
    WL_MACHINE=apple python3 tools/wl_extract_title_endgame.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wl_extract_side as X                                     # noqa: E402
import wl_extract_baseline as B                                 # noqa: E402
import wl_a2disk                                                # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
OUT = os.environ.get('WL_ASSETS_OUT') or f'{ROOT}/data/assets'
MACHINE = os.environ.get('WL_MACHINE', 'c64')

# name -> (side, track, block, kind, arg)   kind 'raw' -> arg = pages, 'codec' -> (budget, cap)
JOBS = [
    ('title',   'loadA_intro_image.bin', 1, 4, 1,  'raw',   2),
    ('title',   'loadB_intro_text.bin',  1, 5, 16, 'codec', (20, 6400)),
    ('endings', 'endgame_t3b2.bin',      4, 3, 2,  'codec', (11, 0x4200 - 0x3400)),
    ('endings', 'endgame_t2b12.bin',     4, 2, 12, 'codec', (13, 0x4500 - 0x3400)),
    ('endings', 'endgame_t1b20.bin',     4, 1, 20, 'codec', (13, 0x4400 - 0x3400)),
    ('endings', 'endgame_t3b4.bin',      4, 3, 4,  'raw',   2),
]

# The Apple table. Same shape; `block` is a plain sector number here, because the Apple stores its
# sectors in order and wl_a2disk.i2p is the identity. No raw jobs: every Apple asset is codec.
# 16384 as a cap means "no cap" — the budget is what stops these, and only the globe's stop page
# is known ($88, from the title module).
JOBS_A2 = [
    ('title',   'a2_title_globe.bin',    1, 4, 0x06, 'codec', (22, 0x8800 - 0x6C00)),
    ('endings', 'a2_end_dome.bin',       4, 3, 0x0F, 'codec', (15, 16384)),
    ('endings', 'a2_end_dome_anim.bin',  4, 2, 0x0C, 'codec', (12, 16384)),
    ('endings', 'a2_end_skeletons.bin',  4, 2, 0x00, 'codec', (16, 16384)),
    ('endings', 'a2_end_finale.bin',     4, 1, 0x00, 'codec', (17, 16384)),
]


def _raw(track, blk, pages):
    """`pages` whole sectors from (track, blk), stepping down — the loader's page copy."""
    out = bytearray()
    t, i = track, blk
    for _ in range(pages):
        out += X.sec(t, X.i2p(t, i))
        nx = X.step_down(t, i, 1)
        if nx is None:
            break
        t, i = nx
    return bytes(out)


def main() -> int:
    jobs = JOBS
    if MACHINE != 'c64':
        jobs = JOBS_A2
        # The only two pieces of 1541 in the decode path, exactly as wl_extract_baseline swaps
        # them: both are module globals in wl_extract_side, so build_stream picks these up.
        X.i2p = wl_a2disk.i2p
        X.step_down = wl_a2disk.step_down
    written = 0
    for group, fn, side, track, blk, kind, arg in jobs:
        X.sec = B.src_sec(side)                       # point the decoder at this side's image
        if kind == 'raw':
            data = _raw(track, blk, arg)
        else:
            budget, cap = arg
            data = X.decode_until_eoi(X.build_stream(track, blk, 0, budget + 2), budget, cap)
        d = os.path.join(OUT, group)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, fn), 'wb') as f:
            f.write(data)
        where = f"T{track}/blk{blk:<2d}" if MACHINE == 'c64' else f"T{track:02d}/S{blk:02X}"
        print(f"  {group}/{fn:24s} side {side} {where} -> {len(data):5d} B")
        written += 1
    print(f"{written} assets in {OUT}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
