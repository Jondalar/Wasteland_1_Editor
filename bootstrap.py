#!/usr/bin/env python3
"""One command to turn your own Wasteland disks into an editable data set.

This repository contains NO game data — not a byte. Everything the editor shows, it reads out
of the disk images you supply here. That is the whole point: the editor can be shared, the game
cannot.

    python3 bootstrap.py --disks /path/to/your/g64s
    python3 bootstrap.py --disk1 s1.g64 --disk2 s2.g64 --disk3 s3.g64 --disk4 s4.g64
    python3 bootstrap.py --apple --disks /path/to/your/dsks     # the Apple II release

What it writes, all of it under data/ and all of it git-ignored:

    data/baseline/side1..4/   201 assets — maps, tilesets, area records, portraits, scripts,
                              scene handlers — depacked with the game's own $C8EC codec
    data/baseline/INVENTORY.json
    data/names.json           the 95 item and 36 skill names, decoded from your side-1 engine
    data/assets/areanames/    the area-name master data, if you pass --areanames
    data/assets/title/        the WASTELAND logo and the globe
    data/assets/endings/      the Cochise dome, the skeletons, the finale
                              (--apple: no logo — that screen has none — and one dome more,
                               because the Apple stores a still and an animated copy)
    data/work/side1..4/       empty; the editor writes your edits here and never touches baseline

You need preservation-grade .g64 images of all four sides. A .g64 keeps the bit-level track
data, which is what the loader's own directory walk needs; a .d64 has already thrown that away.
Dump them from your own disks, or use images you already own. Nothing here helps you obtain the
game, and that is deliberate.

--apple reads the Apple II release from four 143,360 B DOS 3.3 .dsk images instead. There is no
GCR on that machine, so an ordinary .dsk is already preservation-grade. It writes data_a2/ — one
data root per machine, because the record window differs and the backend reads it once at
startup. 197 assets rather than 201: the Apple keeps a depacker where the C64 keeps its charset.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE / "tools"
DATA = Path(os.environ.get("WL_DATA", HERE / "data")).resolve()


def run(script: str, env: dict, *args: str) -> None:
    r = subprocess.run([sys.executable, str(TOOLS / script), *args],
                       env={**os.environ, **env}, cwd=str(HERE))
    if r.returncode:
        sys.exit(f"\n{script} failed (exit {r.returncode}) — nothing further was written.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--disks", help="directory holding your four .g64 images")
    for n in (1, 2, 3, 4):
        ap.add_argument(f"--disk{n}", help=f"side {n} .g64")
    ap.add_argument("--areanames", help="area_names.json to install as the area-name master data "
                                        "(default: WL_AREANAMES)")
    ap.add_argument("--apple", action="store_true",
                    help="read the Apple II release (.dsk) instead; writes data_a2/ by default")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing data/ (your edits in data/work/ are kept)")
    a = ap.parse_args()

    # One machine per data root, and one per server run — the record window differs ($3400 on the
    # C64, $D000 on the Apple) and the backend reads it once at startup. Keeping the two roots
    # apart is what lets every filename below stay the same for both.
    data = DATA if os.environ.get("WL_DATA") or not a.apple else (HERE / "data_a2").resolve()

    env = {}
    if a.apple:
        env["WL_MACHINE"] = "apple"
    if a.disks:
        env["WL_DISKS"] = str(Path(a.disks).resolve())
    for n in (1, 2, 3, 4):
        v = getattr(a, f"disk{n}")
        if v:
            env[f"WL_DISK{n}"] = str(Path(v).resolve())
    if not env:
        sys.exit("say where your disk images are: --disks <dir>  or  --disk1 … --disk4\n"
                 "(this project ships none — see the README)")

    base = data / "baseline"
    if base.exists() and any(base.rglob("*.bin")) and not a.force:
        sys.exit(f"{base} already holds extracted assets. Re-run with --force to redo it.\n"
                 f"(data/work/ is never touched by this script.)")

    print("== 1/4  extracting the four sides ==")
    env["WL_BASELINE_OUT"] = str(base)
    env["WL_SRC"] = "g64"
    run("wl_extract_baseline.py", env)

    print("\n== 2/4  decoding the item and skill names from your engine blob ==")
    run("wl_gen_names.py", env, "--out", str(data / "names.json"))

    print("\n== 3/4  the title screen and the endgame scenes ==")
    # Blobs the engine loads by hardcoded track and block, so they are in no directory and the
    # baseline sweep never saw them. Without them the World tab's Title and Endings panes are
    # empty — which they were on every data root this project has ever produced.
    # Six on the C64, five on the Apple; tools/wl_extract_title_endgame.py has both tables and
    # the provenance of every number in them.
    env["WL_ASSETS_OUT"] = str(data / "assets")
    run("wl_extract_title_endgame.py", env)

    # Area names are master data, not extracted data: they are not in the disks in a form this
    # project can read, so they come from a file you point at. Without one the editor shows bare
    # ids, which is a poor first run but not a broken one.
    src = a.areanames or os.environ.get("WL_AREANAMES")
    if src:
        src = Path(src)
        if not src.is_file():
            sys.exit(f"--areanames: {src} is not a file")
        dst = data / "assets" / "areanames" / "area_names.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        n = len(json.loads(dst.read_text()))
        print(f"\n== area names ==\n  {dst}  ({n} entries, from {src})")
    else:
        print("\n== area names ==\n  none given (--areanames / WL_AREANAMES) — the editor will "
              "show area ids")

    print("\n== 4/4  preparing the working copies ==")
    # The root says which machine it holds. backend/wl_machine.py reads this to pick the record
    # window ($3400 or $D000); nothing else selects it, so a root cannot be opened as the wrong
    # machine by starting the server with the wrong flag.
    (data / "MACHINE").write_text("apple\n" if a.apple else "c64\n")
    for n in (1, 2, 3, 4):
        (data / "work" / f"side{n}").mkdir(parents=True, exist_ok=True)
    for sub in ("assets/areanames", "assets/paragraphs", "assets/title", "assets/endings"):
        (data / sub).mkdir(parents=True, exist_ok=True)
    n_assets = len(list(base.rglob("*.bin")))
    root = data.name
    print(f"  {root}/work/side1..4  ready (empty — the editor writes your edits there)")
    print(f"  {root}/assets/…       ready (empty — optional, see README)")

    print(f"\nDone. {n_assets} assets in {base}.")
    start = "bash run.sh" if data == DATA else f"WL_DATA={root} bash run.sh"
    print(f"Start the editor:  {start}     then open http://127.0.0.1:8080")
    return 0


if __name__ == "__main__":
    sys.exit(main())
