#!/usr/bin/env python3
"""_hexgate.py — the automatic gate for P1 of META_MODEL_PLAN.md.

Three questions, all answered by measurement rather than judgement:

  1. Did the emitted bytes change?   Every one of the 43 shipped areas is decoded and re-emitted;
     the result is hashed and compared against a snapshot taken before the work started. Naming a
     field must not move a single byte — a decoder that now understands `LOOT.term` has to emit the
     same `$FF` it used to copy blindly. Any hash that moves is a regression, full stop.

  2. How much is still unnamed INSIDE the group records?   The bytes carried by the fields listed in
     UNMODELLED below, plus every opaque record. That number is the progress bar (META_MODEL.md §4)
     and it must only ever fall.

     It is a LIST, not a scan for a JSON type, and the difference is the whole point. The first
     version counted every `hex:` string, i.e. the ENCODING: re-emitting EVENT's `args` as a list of
     ints dropped the count by 400 B with all 43 hashes unmoved and the gate printing PASS — a
     rename indistinguishable from the work. Byte counts now come from the VALUE whatever type it
     wears, so a field leaves the budget only by leaving the tuple: one reviewable line in a diff.
     Anything hex the walk meets that is in neither list is reported as UNREGISTERED and fails, so
     bytes cannot be retired by moving them to a key nobody counts either. And a name that is in the
     budget but no longer in the data fails as well: naming `EVENT.args` and re-labelling it are the
     same event to a byte counter, so the gate makes you write down which one it was.

     That last line is the limit of the instrument, and it is worth stating rather than dressing up:
     a counter measures bytes, not understanding. It can force the claim into the diff; whether the
     claim is true stays a reading.

  3. What is not in that budget at all?   The blobs the IR still carries whole OUTSIDE the group
     records — the foe tables, the text-table header, the descriptor tail, grp16's 6502. ~22 KB,
     forty times the budget, printed every run so that §4's percentage cannot be misread as the
     model's coverage of the game.

    ./_hexgate.py --snapshot      write the baseline hashes (run once, before any change)
    ./_hexgate.py                 check against it and print the hex budget
"""
import collections
import glob
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wl_ir                                                          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.path.join(HERE, "_hexgate_snapshot.json")
BASE = os.path.join(HERE, "..", "..", "baseline")


def areas():
    for f in sorted(glob.glob(os.path.join(BASE, "side*", "s*_record_id*.bin"))):
        b = os.path.basename(f)
        yield f, b, int(b[1]), int(b.split("id")[1].split(".")[0])


# THE BUDGET. Record fields that still carry bytes nobody has identified — by name, so that retiring
# one is a line in a diff rather than a change of JSON type. Every OPAQUE.* record joins them
# implicitly: a record with no model at all is unmodelled by definition.
UNMODELLED = ("EVENT.args", "HIREABLE.unknown")

# NOT in the budget and never was: the blobs the IR carries whole, outside the group records. They
# are unnamed too, and bigger than everything above put together — but the snapshot the budget
# ratchets against was taken over the records only, so they are reported beside it rather than
# folded into it. Widening the ratchet means a new snapshot, which is a decision, not a bugfix.
OUT_OF_SCOPE = ("descriptor.tail", "foe_names_raw", "foe_names_term", "foe_records_raw",
                "foe_table_extra", "foe_table_header", "foes.record", "grp16.code", "text_table")


def blen(o):
    """The bytes an IR value carries, whatever JSON type it wears: hex string, byte list, one byte.

    Type-agnostic ON PURPOSE — see question 2. `"hex:0a0b"`, `[10, 11]` and `{"a": 10, "b": 11}` are
    the same two unnamed bytes, and only one of them used to count."""
    if isinstance(o, str):
        return (len(o) - 4) // 2 if o.startswith("hex:") else 0
    if isinstance(o, (list, tuple)):
        return sum(1 if isinstance(v, int) else blen(v) for v in o)
    if isinstance(o, dict):
        return sum(blen(v) for v in o.values())
    return 1 if isinstance(o, int) else 0


def budget(o, op, path, out, stray):
    """One record: the UNMODELLED fields into `out`, any OTHER hex string into `stray`."""
    key = f"{op}.{path}"
    if path and key in UNMODELLED:
        out[key] += blen(o)
        return
    if isinstance(o, dict):
        for k, v in o.items():
            budget(v, op, k if not path else f"{path}.{k}", out, stray)
    elif isinstance(o, list):
        for v in o:
            budget(v, op, path, out, stray)
    elif op == "OPAQUE":
        out[key] += blen(o)
    elif isinstance(o, str) and o.startswith("hex:") and blen(o):
        stray[key] += blen(o)


def outside(o, path, out, stray):
    """Every hex: string OUTSIDE the group records, keyed by where in the document it sits."""
    if isinstance(o, dict):
        for k, v in o.items():
            if not path and k in ("groups", "opaque"):
                continue                                      # question 2 walks those
            outside(v, k if not path else f"{path}.{k}", out, stray)
    elif isinstance(o, list):
        for v in o:
            outside(v, path, out, stray)
    elif isinstance(o, str) and o.startswith("hex:") and blen(o):
        (out if path in OUT_OF_SCOPE else stray)[path] += blen(o)


def measure():
    """(emitted-hash per area, budget per op.field, out-of-scope per path, unregistered, bytes)."""
    hashes, hexb, outb, stray, total = {}, collections.Counter(), collections.Counter(), \
        collections.Counter(), 0
    for f, name, side, aid in areas():
        raw = open(f, "rb").read()
        total += len(raw)
        ir = wl_ir.record_to_ir(raw, side, aid)
        out = wl_ir.ir_to_record(ir)
        hashes[name] = hashlib.sha256(out).hexdigest()[:16]
        for g, recs in ir.get("groups", {}).items():
            for _cid, rr in recs.items():
                budget(rr, rr.get("op", f"grp{g}"), "", hexb, stray)
        budget(ir.get("opaque") or {}, "OPAQUE", "", hexb, stray)
        outside(ir, "", outb, stray)
    return hashes, hexb, outb, stray, total


def main():
    hashes, hexb, outb, stray, total = measure()
    hexsum, outsum = sum(hexb.values()), sum(outb.values())

    if "--snapshot" in sys.argv:
        json.dump({"hashes": hashes, "hex": dict(hexb), "hex_total": hexsum,
                   "outside": dict(outb), "outside_total": outsum,
                   "record_bytes": total}, open(SNAP, "w"), indent=1, sort_keys=True)
        print(f"snapshot written: {len(hashes)} areas, {hexsum} hex bytes of {total}")
        return 0

    if not os.path.isfile(SNAP):
        print("no snapshot — run ./_hexgate.py --snapshot first", file=sys.stderr)
        return 2
    snap = json.load(open(SNAP))

    moved = [k for k, v in hashes.items() if snap["hashes"].get(k) != v]
    missing = sorted(set(snap["hashes"]) - set(hashes))
    ok = not moved and not missing

    print(f"areas: {len(hashes)}   emitted bytes unchanged: "
          f"{'YES' if ok else 'NO — ' + ', '.join(moved[:6])}")
    if missing:
        print(f"  MISSING areas: {missing}")

    was = snap["hex_total"]
    print(f"\nunnamed IN THE GROUP RECORDS:  {hexsum} B of {total}  = {100 * hexsum / total:.2f}%"
          f"   (was {was}, {'-' if hexsum <= was else '+'}{abs(was - hexsum)})")
    old = snap.get("hex", {})
    for k in sorted(set(old) | set(hexb), key=lambda k: -max(old.get(k, 0), hexb.get(k, 0))):
        o, n = old.get(k, 0), hexb.get(k, 0)
        if o == n and n == 0:
            continue
        mark = "  " if o == n else ("✓ " if n < o else "! ")
        print(f"  {mark}{k:28} {n:6} B" + (f"   (was {o})" if o != n else ""))

    # The rest of the document, so the line above is never read as coverage of the game. Ratcheted
    # only once a snapshot carries it — the current one predates the measurement.
    ow = snap.get("outside_total")
    print(f"\noutside them, carried whole:   {outsum} B of {total}  = {100 * outsum / total:.2f}%"
          f"   ({'was ' + str(ow) if ow is not None else 'not ratcheted — no baseline'})")
    for k, v in outb.most_common():
        print(f"    {k:28} {v:6} B")
    print(f"\n  → unnamed altogether: {hexsum + outsum} B = {100 * (hexsum + outsum) / total:.2f}%")

    if stray:
        print("\nUNREGISTERED hex — neither in the budget nor declared out of scope:", file=sys.stderr)
        for k, v in stray.most_common():
            print(f"    {k:28} {v:6} B", file=sys.stderr)
        print("FAIL: name it, or add it to UNMODELLED / OUT_OF_SCOPE where the diff shows it",
              file=sys.stderr)
        return 1
    gone = [k for k in UNMODELLED if k not in hexb]
    if gone:
        print(f"\nDECLARED BUT ABSENT: {', '.join(gone)}", file=sys.stderr)
        print("FAIL: the budget still names a field the data no longer has. If it was NAMED, retire"
              "\n      it from UNMODELLED in this same diff; if it was only re-typed or re-labelled,"
              "\n      the bytes are still unnamed and the field has to keep its name.", file=sys.stderr)
        return 1
    if hexsum > was:
        print("\nFAIL: the unnamed remainder GREW", file=sys.stderr)
        return 1
    if ow is not None and outsum > ow:
        print("\nFAIL: the blobs outside the records GREW — bytes cannot move out of the budget",
              file=sys.stderr)
        return 1
    if not ok:
        print("\nFAIL: emitted bytes moved — naming a field must not change what it emits",
              file=sys.stderr)
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
