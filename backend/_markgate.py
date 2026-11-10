#!/usr/bin/env python3
"""_markgate.py — the gate for P1.5 (IR_NAMED_REFERENCES.md §5).

    .bin ──record_to_ir──► IR ──mark──► IR' ──resolve──► IR ──ir_to_record──► .bin

If the 43 shipped areas come back byte-identical to the hashes frozen before P1, the naming lost
nothing. Every difference is a reference resolved wrong.

The second gate the first one cannot see: a mark must have exactly one target and a target must be
reachable. Bytes can be identical while a mark quietly points at the wrong record of the right
shape — so the marks are counted and checked separately.

    ./_markgate.py            check all 43 (re-derived from bytes: proves the CODE)
    ./_markgate.py --stored   check the persisted documents (proves the FILES)
    ./_markgate.py -v         plus per-area numbers
"""
import glob
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wl_ir                                                          # noqa: E402
import wl_marks                                                       # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.path.join(HERE, "_hexgate_snapshot.json")
BASE = os.path.join(HERE, "..", "..", "baseline")
WORK = os.path.join(HERE, "..", "work")


def areas():
    for f in sorted(glob.glob(os.path.join(BASE, "side*", "s*_record_id*.bin"))):
        b = os.path.basename(f)
        yield f, b, int(b[1]), int(b.split("id")[1].split(".")[0])


def stored():
    """The gate for what is actually PERSISTED: every stored document must be marked, and resolving
    it must reproduce the .bin lying next to it.

    The other gate re-derives its documents from bytes each run, so it can only ever prove that the
    code is right. This one proves that the files are — which is a different claim, and the one that
    was quietly false before P1.5: all five stored documents were written by older decoders and
    compiled to something other than their own bytes."""
    docs = sorted(glob.glob(os.path.join(WORK, "side*", "s*_record_id*.json")))
    bins = sorted(glob.glob(os.path.join(WORK, "side*", "s*_record_id*.bin")))
    bad, marks = [], 0

    for b in bins:
        if not os.path.isfile(b[:-4] + ".json"):
            bad.append((os.path.basename(b), "bytes without a document"))

    for j in docs:
        name = os.path.basename(j)
        with open(j) as fh:
            ir = json.load(fh)
        if not wl_marks.is_marked(ir):
            bad.append((name, "not marked"))
            continue
        v = (ir.get("meta") or {}).get("ir_version")
        if v != wl_ir.IR_VERSION:
            bad.append((name, f"written by IR version {v}, decoder is {wl_ir.IR_VERSION}"))
            continue
        m = [rr["ref"] for recs in ir["groups"].values() for rr in recs.values() if rr.get("ref")]
        m += [s for s in (ir.get("string_marks") or []) if s]
        if len(m) != len(set(m)):
            bad.append((name, f"{len(m) - len(set(m))} duplicate mark(s)"))
        marks += len(m)
        want = open(j[:-5] + ".bin", "rb").read()
        try:
            got = wl_ir.ir_to_record(wl_marks.resolve(ir))
        except ValueError as e:
            bad.append((name, f"does not compile: {str(e)[:60]}"))
            continue
        if got != want:
            bad.append((name, f"compiles to {len(got)} B, its .bin is {len(want)} B"
                        if len(got) != len(want) else "same length, different bytes"))

    print(f"stored documents      {len(docs)} for {len(bins)} records")
    print(f"marks on disk         {marks}")
    print(f"document → .bin       {'ALL identical' if not bad else f'NO — {len(bad)} failing'}")
    for n, why in bad[:10]:
        print(f"  {n:26} {why}")
    if bad:
        print("\nFAIL", file=sys.stderr)
        return 1
    print("\nPASS")
    return 0


def main():
    if "--insert" in sys.argv:
        return _insert_demo()
    if "--stored" in sys.argv:
        return stored()
    verbose = "-v" in sys.argv
    snap = json.load(open(SNAP))["hashes"]
    bad, tot = [], {"str": 0, "rec": 0, "sent": 0, "pool": 0, "marks": 0}
    dangling = []

    for path, name, side, aid in areas():
        raw = open(path, "rb").read()
        ir = wl_ir.record_to_ir(raw, side, aid)
        area = f"s{side}a{aid:02d}"

        rep = wl_marks.mark(ir, area)
        marked = json.loads(json.dumps(ir))            # the document as it would be persisted

        # every mark unique, every reference resolvable — checked on the marked document, because
        # after resolve() there is nothing left to check
        marks = [rr["ref"] for recs in marked["groups"].values() for rr in recs.values()]
        marks += marked.get("string_marks") or []
        dupes = len(marks) - len(set(marks))

        try:
            out = wl_ir.ir_to_record(wl_marks.resolve(ir))
        except ValueError as e:
            bad.append((name, str(e)[:70]))
            continue

        h = hashlib.sha256(out).hexdigest()[:16]
        if h != snap.get(name):
            bad.append((name, f"bytes moved  {snap.get(name)} → {h}"))
        if dupes:
            bad.append((name, f"{dupes} duplicate mark(s)"))

        for k, v in (("str", "str_refs"), ("rec", "rec_refs"),
                     ("sent", "sentinels"), ("pool", "out_of_pool")):
            tot[k] += rep[v]
        dangling += [f"{name} {d}" for d in rep["dangling"]]
        tot["marks"] += len(marks)
        if verbose:
            print(f"  {name:24} {rep['records']:4} rec  {rep['strings']:4} str  "
                  f"→ {rep['rec_refs']:4} + {rep['str_refs']:4} marked, {rep['left']:3} left as-is")

    print(f"\nmarks handed out          {tot['marks']}")
    print(f"references named          {tot['rec']} record + {tot['str']} string = "
          f"{tot['rec'] + tot['str']}")
    print(f"left as numbers           {tot['sent']} sentinel + {tot['pool']} out-of-pool")
    print(f"DANGLING in the shipped data  {len(dangling)}"
          + ("".join(f"\n  {d}" for d in dangling) if dangling else ""))
    print(f"\nareas: {len(list(areas()))}   byte-identical: "
          f"{'ALL' if not bad else f'NO — {len(bad)} failing'}")
    for n, why in bad[:10]:
        print(f"  {n:24} {why}")
    if bad:
        print("\nFAIL", file=sys.stderr)
        return 1
    print("\nPASS")
    return 0


# ── the demonstration the byte gate cannot give: INSERT a record in the middle of a group and
# watch the references follow. Today this is impossible — one record more shifts its successors
# and nothing pulls the pointers along. Kept here rather than in a test file because it is the
# reason the module exists, and it should be re-runnable by hand.
#
#   ./_markgate.py --insert
def _insert_demo():
    import wl_ir as _ir
    raw = open(os.path.join(BASE, "side1", "s1_record_id10.bin"), "rb").read()

    def refs_to_g1(ir):
        out = {}
        for g, recs in ir["groups"].items():
            for cid, rr in recs.items():
                for k in ("done", "alt", "after", "next", "victory", "use", "advance", "cancel"):
                    p = rr.get(k)
                    if isinstance(p, dict) and p.get("op") == 1 and isinstance(p.get("id"), int):
                        out[(g, cid, k)] = p["id"]
        return out

    before = refs_to_g1(_ir.record_to_ir(raw, 1, 10))
    ir = _ir.record_to_ir(raw, 1, 10)
    wl_marks.mark(ir, "highpool")
    old = {int(k): v for k, v in ir["groups"]["1"].items()}
    ir["groups"]["1"] = {str(k if k < 8 else k + 1): v for k, v in sorted(old.items())}
    ir["groups"]["1"]["8"] = {"op": "SAY", "messages": ["highpool/txt/tearful-boy-watches"],
                              "ref": "highpool/inserted", "type": "quest"}
    out = _ir.ir_to_record(wl_marks.resolve(ir))
    after = refs_to_g1(_ir.record_to_ir(out, 1, 10))

    below = above = wrong = 0
    for key, a in before.items():
        want = a + 1 if a >= 8 else a
        if after.get(key) != want:
            wrong += 1
        elif a >= 8:
            above += 1
        else:
            below += 1
    print(f"inserted one SAY at grp1[8] of Highpool — {len(raw)} B → {len(out)} B")
    print(f"  references into grp1      {len(before)}")
    print(f"  below the insert, as-is   {below}")
    print(f"  above it, pulled along    {above}")
    print(f"  wrong                     {wrong}")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
