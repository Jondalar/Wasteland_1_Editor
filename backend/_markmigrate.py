#!/usr/bin/env python3
"""_markmigrate.py — bring the STORED IR documents up to date and give them their marks, once.

    ./_markmigrate.py            report only
    ./_markmigrate.py --write    do it

WHAT THIS FOUND, and it is the reason the script reads the .bin rather than the .json. All three
stored documents were written by OLDER decoders (ir_version None, 2, 2 against today's 3), so
`sync_from_json` has been refusing them by design — the .json on disk was never the source of
anything, it was a stale by-product. Rebuilding it from the .json would preserve that staleness, so
the bytes are the input and `wl_persist.save_record` is the path: the same one every editor save
takes from now on, marks included.

THE BYTES MOVE, and that is checked rather than assumed. Today's serializer packs out slack the
older one left (9207 → 9119, 5591 → 5583, 5584 → 5584). `verify_equivalent` compares every (grp, id)
the engine can dispatch and reports 0 differences for all three, so the game reads exactly what it
read before. This is not a change this script introduces — it is armed already, and would happen on
the next ordinary edit of each area. Doing it here makes it visible and testable instead.

NOT MIGRATED: the 40 areas that have only bytes in `baseline/`. A document for those is easy to
build and would change the same way — but they are the cart build's input and nobody has edited
them, so re-emitting them is a decision (P4's gate: semantic signature over byte identity), not a
migration's call.
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wl_ir                                                          # noqa: E402
import wl_marks                                                       # noqa: E402
import wl_persist                                                     # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "..", "work")


def main():
    write = "--write" in sys.argv
    force = "--force" in sys.argv   # re-derive marks even if the document already has some.
    # Safe only while nothing REFERENCES a mark: it throws away every name, including hand-given
    # ones. Used once, on 2026-08-08, to replace the positional names remark() handed out before
    # it shared mark()'s naming rule.
    bins = sorted(glob.glob(os.path.join(WORK, "side*", "s*_record_id*.bin")))
    if not bins:
        print("no stored records found")
        return 0

    done = skipped = 0
    for b in bins:
        name = os.path.basename(b)
        side = int(name[1])
        aid = int(name.split("id")[1].split(".")[0])
        j = b[:-4] + ".json"
        raw = open(b, "rb").read()

        was = None
        if os.path.isfile(j):
            with open(j) as fh:
                old = json.load(fh)
            was = (old.get("meta") or {}).get("ir_version")
            if wl_marks.is_marked(old) and not force:
                print(f"  {name:26} already marked")
                skipped += 1
                continue

        blob, ir = wl_persist.normalize(raw, side, aid)
        bad = wl_persist.verify_equivalent(raw, blob, side, aid)
        if bad:
            print(f"  {name:26} REFUSED — engine would read different bytes: {bad[0]}")
            return 1

        probe = json.loads(json.dumps(ir))
        rep = wl_marks.mark(probe, f"s{side}a{aid:02d}")
        named = rep["rec_refs"] + rep["str_refs"]
        print(f"  {name:26} ir_version {was} → {wl_ir.IR_VERSION}   "
              f"{len(raw)} → {len(blob)} B ({len(blob) - len(raw):+d}, 0 engine-diffs)   "
              f"{named} refs named" + ("" if write else "   (dry run)"))

        if write:
            if force and os.path.isfile(j):
                os.remove(j)          # no carry(): the old names are what we are replacing
            wl_persist.save_record(b, raw, side, aid)     # the ordinary save path, marks and all
            done += 1

    if not write:
        print(f"\n{len(bins) - skipped} would be migrated, {skipped} already marked "
              f"— rerun with --write")
        return 0
    print(f"\n{done} migrated, {skipped} already marked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
