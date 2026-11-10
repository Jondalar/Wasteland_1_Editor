#!/usr/bin/env python3
"""Decode the item and skill NAMES out of the user's own game disk.

Why this exists: the upstream editor carried these 130 names as Python literals. They are not
facts about a file format — they are the 1988 game's own prose, and prose is the one thing this
project must never ship. So the tables live in `data/names.json`, and this script writes that
file from the engine blob on side 1 of YOUR disk. No game text is distributed with the editor;
everything it shows, it read off your disk.

The engine keeps its strings in the same 5-bit codec the areas use, with the character table at
$29E4 and the offset table at $2A20. Item names sit at message id = item id + $24; skills are
message id = skill id, no bias. Both verified byte-exact against the shipped tables.

--machine apple reads the Apple II release instead. Same codec, same biases, same 172-string
pool — the only difference is how you get at it, and the alphabet's two transposed letters
(docs/APPLE2.md §2). All 95 item names and all 36 skill names come back identical to the C64's.

    python3 tools/wl_gen_names.py                      # WL_DISKS / WL_DISK1 name the image
    python3 tools/wl_gen_names.py --disk side1.g64 --out data/names.json
    python3 tools/wl_gen_names.py --machine apple --disks /path/to/dsks
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "backend"))

import wl_extract_boot_assets as boot      # noqa: E402  the fastloader replay + ABI
import wl_g64decode                        # noqa: E402
import wl_a2disk                           # noqa: E402  Apple II .dsk geometry
import wl_text5_encode as tenc             # noqa: E402  the 5-bit pool walker

ENGINE_TRACK, ENGINE_IDX, ENGINE_DEST, ENGINE_END = boot.ASSETS["engine"]
CHAR_TABLE = 0x29E4                        # 62-byte 5-bit alphabet
ITEM_BIAS  = 0x24                          # message id = item id + $24
N_ITEMS    = 95
N_SKILLS   = 36                            # index 0 is empty in the engine pool

# The Apple II pool. There is no fastloader to replay here and no engine image to rebuild: the
# pool starts inside one known sector of master 1 and runs on from there — but it runs on in the
# loader's DESCENDING order, so the flat .dsk is the wrong thing to read. Reading it flat yields
# a correct-looking alphabet, a correct group count and pure noise for every string, which is
# the trap: the count matching is not the codec working.
#
#   T05/S00 + $8A  the 62-byte alphabet (60 of 62 bytes shared with the C64's)
#   T05/S00 + $C6  the offset table, i.e. alphabet + $3C, exactly as on the C64
#   8 sectors      covers the largest OT pointer ($06C7); 16 are read for slack
#
# Verified: 43 groups / 172 strings, and all 95 item plus all 36 skill names byte-identical to
# the ones decoded from the C64 side-1 engine.
APPLE_POOL_TS  = (5, 0)
APPLE_POOL_OFF = 0x8A
APPLE_POOL_SEC = 16


def engine_image(disk: str) -> bytes:
    """Replay the boot fastloader over the disk and return the engine as a $0200-based image."""
    dec = wl_g64decode.decode_g64(disk)     # {track: {phys_sector: 256 B}}
    t, i, page = ENGINE_TRACK, ENGINE_IDX, ENGINE_DEST >> 8
    body = bytearray()
    while True:
        phys = boot.idx2phys(t, i)
        blk = dec.get(t, {}).get(phys)
        if blk is None or len(blk) != 256:
            sys.exit(f"cannot read t{t}s{phys} from {disk} — is this side 1 of the game?")
        body += blk
        if page == ENGINE_END:
            break
        page += 1
        nx = boot.step_down(t, i, 1)
        if nx is None:
            sys.exit("ran off the start of the disk before the engine was complete")
        t, i = nx
    return bytes(body)


def decode_pool(engine: bytes) -> list[str]:
    base = CHAR_TABLE - ENGINE_DEST
    return list(tenc.enumerate_strings(engine, base, engine[base:base + 62]))


def apple_pool(disk: str) -> list[str]:
    """The Apple pool, read as the loader reads it: a descending sector stream from T05/S00."""
    im = wl_a2disk.load(disk)
    buf, cur = bytearray(), APPLE_POOL_TS
    for _ in range(APPLE_POOL_SEC):
        if cur is None:
            break
        buf += wl_a2disk.sec(im, *cur)
        cur = wl_a2disk.step_down(*cur, 1)
    base = APPLE_POOL_OFF
    return list(tenc.enumerate_strings(bytes(buf), base, bytes(buf[base:base + 62])))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--disk", help="side-1 image (default: WL_DISK1, or a 's1' file in WL_DISKS)")
    ap.add_argument("--machine", choices=("c64", "apple"),
                    default=os.environ.get("WL_MACHINE", "c64"),
                    help="which release to read (default: c64, or WL_MACHINE)")
    ap.add_argument("--out", default=None, help="default: data/names.json")
    a = ap.parse_args()

    apple = a.machine == "apple"
    ext = ".dsk" if apple else ".g64"
    disk = a.disk or os.environ.get("WL_DISK1")
    if not disk:
        d = os.environ.get("WL_DISKS")
        if d and os.path.isdir(d):
            # 's1' names the C64 side; the Apple master 1 is wastmst1.dsk, so a trailing 1
            # counts too — and wastboot.dsk, which is master 1 plus boot code, does not match.
            disk = next((os.path.join(d, f) for f in sorted(os.listdir(d))
                         if f.lower().endswith(ext)
                         and ("s1" in f.lower() or (apple and f.lower().endswith("1" + ext)))),
                        None)
    if not disk or not os.path.isfile(disk):
        sys.exit(f"no side-1 {ext} image. Set WL_DISKS=/path/to/your/images or pass --disk.\n"
                 "This project ships no game data — see README, 'Bring your own disks'.")

    pool = apple_pool(disk) if apple else decode_pool(engine_image(disk))
    if len(pool) < N_ITEMS + ITEM_BIAS:
        sys.exit(f"the engine text pool decoded to only {len(pool)} strings — too short to hold "
                 f"the item table. Wrong disk, or a modified engine?")

    # An item name is stored as stem + singular suffix + plural suffix, separated by the codec's
    # $0A — the same shape the foe names use. The display name is stem + SINGULAR:
    #   "Ax<0a><0a>es<0a>"      -> "Ax"    + ""   = "Ax"
    #   "Kni<0a>fe<0a>ves<0a>"  -> "Kni"   + "fe" = "Knife"
    # Taking the stem alone silently produced "Kni" for seven entries.
    def stem(s):
        parts = s.split("<0a>")
        return (parts[0] + (parts[1] if len(parts) > 1 else "")).strip()
    clean = lambda s: s.replace("<0a>", "").strip()
    names = {
        "_source": f"decoded from {os.path.basename(disk)} by tools/wl_gen_names.py — "
                   + (f"Apple engine text pool at T{APPLE_POOL_TS[0]:02d}/S{APPLE_POOL_TS[1]:02X}"
                      f"+${APPLE_POOL_OFF:02X}" if apple
                      else f"engine text pool at ${CHAR_TABLE:04X}"),
        "items":  {str(i): stem(pool[i + ITEM_BIAS]) for i in range(N_ITEMS)},
        "skills": [clean(pool[i]) for i in range(N_SKILLS)],
    }
    out = Path(a.out) if a.out else HERE.parent / "data" / "names.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(names, indent=1, ensure_ascii=False))
    print(f"{out}: {len(names['items'])} items, {len(names['skills'])} skills")
    print(f"  items[0..3] : {[names['items'][str(i)] for i in range(4)]}")
    print(f"  skills[0..3]: {names['skills'][:4]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
