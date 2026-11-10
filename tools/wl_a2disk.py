#!/usr/bin/env python3
"""
Apple II disk geometry for the Wasteland extractor — STATIC, no runtime/trace.

Wasteland was written on the Apple II and ported to the C64. The data is the same data:
the directory format, the $C8EC-equivalent codec, the area records and the item table all
carry over (see docs/APPLE2.md). What does NOT carry over is the 1541 — and that is this
module. It is the Apple half of what wl_directory.py + wl_g64decode.py do for the C64, and
it is smaller because the Apple imposes almost nothing:

    a DOS 3.3 .dsk is 143,360 B = 35 tracks x 16 sectors x 256 B, in LOGICAL order,
    so track t sector s sits at (t*16 + s) * 256.

No GCR to undo, no zone table (16 sectors on every track, not 21/19/18/17), no interleave
(the sector NUMBER *is* the block index, so i2p is the identity), no $C930 reserved top
sectors, and no track-18 skip — the Apple masters carry no DOS 3.3 VTOC to step around,
track 17 sector 0 is game data like any other.

What DOES carry over is the step-DOWN discipline: a multi-sector asset continues at the
PREVIOUS sector and underflows to the previous track, exactly like the C64's $FD0B. The
successor of T05/S00 is T04/S0F.

  Evidence for the ordering: the engine's string offset table at image offset $050C6 is
  monotonic and stops exactly at the sector boundary $050FF; a sweep of all 560 sectors
  found exactly one continuation, T04/S0F, whose first words continue the sequence.

  Evidence for the directory locations in DIRS: the id sets of all six directories were
  compared against the C64 baseline INVENTORY.json, per side and per kind — map, gfx, str
  (both sectors), record, scene, areatrack, all four sides. 0 differences. The two the
  format comparison had not pinned down are settled by that run: scene sits on T33/S0E
  (docs/APPLE2.md did not list it at all), and areatrack really is on T33/S0F rather than
  continuing the T34 run.
"""
from __future__ import annotations

SPT = 16                      # sectors per track — uniform, no zones
TRACKS = 35
SECTOR = 256
IMAGE_SIZE = TRACKS * SPT * SECTOR      # 143,360


def load(path):
    """Read a .dsk and refuse anything that is not 35x16x256 in logical order."""
    with open(path, 'rb') as f:
        im = f.read()
    if len(im) != IMAGE_SIZE:
        raise SystemExit(
            f"{path}: {len(im)} B — not a 143,360 B DOS 3.3 .dsk (35 tracks x 16 x 256).\n"
            f"  A .nib, .woz or 2mg is a different container and this reader does not decode it.")
    return im


def sec(im, t, p):
    """The 256 bytes of track t, sector p. `p` is both the physical and the logical
    sector: the Apple stores them in order, so there is no interleave to undo."""
    o = (t * SPT + p) * SECTOR
    return im[o:o + SECTOR]


def i2p(t, i):
    """Block index -> physical sector. Identity on the Apple; the C64's ILV table is a
    1541 artefact. Kept as a function so the extractor can call it either way."""
    return i


def step_down(t, i, n):
    """Apply the Apple's step-down n times from (track t, sector i): the same descent as
    the C64 loader's $FD0B, minus the zone table and the track-18 skip. Returns None when
    it runs off the start of the disk."""
    for _ in range(n):
        i -= 1
        if i < 0:
            t -= 1
            if t < 0:
                return None
            i = SPT - 1
    return (t, i)


# Where each directory lives. The C64 keeps all of them on T35 except scene (T34/idx16);
# the Apple keeps them on T34 except scene and areatrack, which sit on T33. Entry
# semantics are unchanged from the C64 — 4 bytes per id, and the base track/sector in
# bytes 254/255 — so wl_extract_side.read_dir reads these as they are.
#
# T34/S0B is the one slot that holds something else entirely: the C64 keeps its shared
# scene charset there, the Apple keeps the depacker. There is no per-side charset to
# extract on this machine.
DIRS = {
    'str_hi':    (34, 0x09),   # ids 64..127 — sides 2 and 4 only, like the C64
    'str':       (34, 0x0A),   # ids 0..63
    'gfx':       (34, 0x0C),
    'map':       (34, 0x0D),
    'record':    (34, 0x0E),   # [track][blk][len][side], raw — not codec-compressed
    'scene':     (33, 0x0E),
    'areatrack': (33, 0x0F),   # [track][blk][len][side], raw
}
