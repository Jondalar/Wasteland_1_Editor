#!/usr/bin/env python3
"""Refuse to let game data into the repository.

The whole premise of this project is that it contains no Wasteland content. That premise is
one careless `git add -A` away from being false, and once a blob is in the history it is
effectively permanent. So this is a real check, not a comment: run it before every commit, and
in CI.

    python3 tools/check_clean.py            # inspect what git TRACKS (and what is staged)
    python3 tools/check_clean.py --staged   # staged only — use it as a pre-commit hook

Exit 0 = clean. Exit 1 = something got in; the file is named.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Extensions that are game data by definition, wherever they appear.
BAD_EXT = {".g64", ".d64", ".d81", ".t64", ".tap", ".crt", ".prg", ".bin", ".wlsave",
           ".dsk", ".po", ".nib", ".2mg", ".woz"}   # Apple II images too
# Paths that only ever hold generated content.
BAD_DIR = ("data/", "data_a2/")   # one generated root per machine (--apple writes the second)
BAD_NAME = {"names.json", "INVENTORY.json"}
# A source file is not allowed to carry a long list of game prose again. These are the two
# tables that were inlined upstream; the decoder lives in tools/wl_gen_names.py now.
BAD_SYMBOL = ("_ITEM_NAMES = {", "_SKILL_NAMES = [")


def git(*a: str) -> list[str]:
    r = subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)
    return [l for l in r.stdout.splitlines() if l.strip()]


def main() -> int:
    staged_only = "--staged" in sys.argv
    files = git("diff", "--cached", "--name-only") if staged_only else git("ls-files")
    bad: list[tuple[str, str]] = []
    for f in files:
        p = Path(f)
        if p.suffix.lower() in BAD_EXT:
            bad.append((f, f"a {p.suffix} file is game data"))
        elif any(f.startswith(d) for d in BAD_DIR):
            bad.append((f, "data/ is generated on the user's machine"))
        elif p.name in BAD_NAME:
            bad.append((f, f"{p.name} is decoded from the user's own disk"))
    me = Path(__file__).resolve().relative_to(ROOT).as_posix()
    for f in files:
        if f == me:
            continue                      # this file names the patterns it looks for
        if f.endswith(".py") and (ROOT / f).is_file():
            try:
                s = (ROOT / f).read_text(errors="replace")
            except OSError:
                continue
            for sym in BAD_SYMBOL:
                if sym in s:
                    bad.append((f, f"{sym.split(' ')[0]} inlined again — it belongs in data/names.json"))

    scope = "staged" if staged_only else "tracked"
    if bad:
        print(f"REFUSED: {len(bad)} {scope} file(s) carry game data\n")
        for f, why in bad:
            print(f"  {f}\n      {why}")
        print("\nNothing from the original game may be committed here. See README, 'What this repo is not'.")
        return 1
    print(f"clean — {len(files)} {scope} files, no game data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
