#!/usr/bin/env python3
"""_questmigrate.py — turn the 233 quest ANNOTATIONS into beats that own their records.

    before   {"kind": "ref",  "ref": "s2a34:grp2[0]", "note": "…"}
    after    {"kind": "beat", "phase": "placed", "at": {"side": 2, "area": 34},
              "records": ["s2a34/mushroom-bishop-explains"], "note": "…",
              "from": "ref:s2a34:grp2[0]"}

WHY. A `ref` step points at a TABLE POSITION. Insert a record before it in that group and the step
silently means a different record — the same class of breakage the marks fixed for the records
themselves. It also has the direction backwards: the quest annotates the world instead of owning
its part of it.

WHAT IS DELIBERATELY MISSING. `bind` — the quest binding (SPEC §2.4 form C). WL1's records carry no
quest state, so there is nothing to bind these to, and /api/design/place refuses a bind with
`501 quest binding not wired` anyway. Migrated beats are `placed` WITHOUT a bind; binds get added
per beat once the engine-side questlog exists.

    ./_questmigrate.py           report only
    ./_questmigrate.py --write   do it (.bak_v2 is written first)
"""
import glob
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wl_area                                                        # noqa: E402
import wl_ir                                                          # noqa: E402
import wl_marks                                                       # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
QUESTS = os.path.join(HERE, "..", "annotations", "quests.json")
REF_RE = re.compile(r"^s(\d+)a(\d+):grp(\d+)\[(\d+)\]$")

_CACHE = {}


def marked_ir(side, area):
    """The area's marked document. Prefers the stored one; derives it for the areas that have only
    bytes. Deriving is safe because `mark()` is a pure function of the content and `remark()` now
    shares its naming rule — an area migrated later lands on the same names."""
    key = (side, area)
    if key in _CACHE:
        return _CACHE[key]
    work = os.path.join(ROOT, "editor", "work", f"side{side}", f"s{side}_record_id{area:02d}.json")
    if os.path.isfile(work):
        with open(work) as fh:
            ir = json.load(fh)
        if not wl_marks.is_marked(ir):
            ir = None
    else:
        ir = None
    if ir is None:
        b = os.path.join(ROOT, "baseline", f"side{side}", f"s{side}_record_id{area:02d}.bin")
        if not os.path.isfile(b):
            _CACHE[key] = None
            return None
        ir = wl_ir.record_to_ir(open(b, "rb").read(), side, area)
        wl_marks.mark(ir, f"s{side}a{area:02d}")
    _CACHE[key] = ir
    return ir


_CELLS = {}


def cells_of(side, area, grp, cid):
    """Every map cell that dispatches this record — nibble == grp AND cellid == id.

    A beat without a cell is a legitimate stage in the model ("Hülse", `wl_qvalidate` warns
    `unbound-beat`), but most of the shipped ones DO sit on a cell and the first migration simply
    did not look. A record reached only through a chain (a COND's `done`, a KEYWORD's `after`)
    genuinely has none, and then the warning is correct."""
    key = (side, area)
    if key not in _CELLS:
        b = os.path.join(ROOT, "baseline", f"side{side}", f"s{side}_record_id{area:02d}.bin")
        if not os.path.isfile(b):
            _CELLS[key] = None
        else:
            a = wl_area.load_area(open(b, "rb").read(), side, area)
            m = {}
            for i, op in enumerate(a.nibble[:a.width * a.height]):
                if 0 < op < 0x10:
                    m.setdefault((op, a.cellid[i]), []).append((i % a.width, i // a.width))
            _CELLS[key] = m
    m = _CELLS[key]
    return (m or {}).get((grp, cid)) or []


def mark_of(side, area, grp, cid):
    ir = marked_ir(side, area)
    if ir is None:
        return None, "area not found"
    rr = ((ir.get("groups") or {}).get(str(grp)) or {}).get(str(cid))
    if rr is None:
        return None, f"grp{grp}[{cid}] does not exist"
    return rr.get("ref"), None if rr.get("ref") else "record carries no mark"


def main():
    write = "--write" in sys.argv
    with open(QUESTS) as fh:
        doc = json.load(fh)

    done = kept = 0
    problems = []
    for slug, q in doc.items():
        if slug.startswith("_"):
            continue
        for i, st in enumerate(q.get("steps") or []):
            if st.get("kind") != "ref":
                kept += 1
                continue
            m = REF_RE.match(st.get("ref") or "")
            if not m:
                problems.append(f"{slug}[{i}] ref not parseable: {st.get('ref')!r}")
                kept += 1
                continue
            side, area, grp, cid = (int(x) for x in m.groups())
            mk, why = mark_of(side, area, grp, cid)
            if not mk:
                problems.append(f"{slug}[{i}] {st['ref']} — {why}")
                kept += 1
                continue
            at = {"side": side, "area": area}
            cs = cells_of(side, area, grp, cid)
            if cs:
                at["col"], at["row"] = cs[0]
                if len(cs) > 1:
                    at["also"] = [{"col": c, "row": r} for c, r in cs[1:]]
            new = {"kind": "beat", "phase": "placed",
                   "at": at,
                   "records": [mk],
                   "from": f"ref:{st['ref']}"}
            if st.get("note"):
                new["note"] = st["note"]
            for extra in ("conds",):                    # carried verbatim, 2 steps have it
                if extra in st:
                    new[extra] = st[extra]
            if write:
                q["steps"][i] = new
            done += 1

    print(f"steps migrated        {done}")
    print(f"left as they were     {kept}")
    print(f"PROBLEMS              {len(problems)}"
          + "".join(f"\n  {p}" for p in problems[:12]))
    # An unresolved ref is LEFT AS IT WAS, so writing loses nothing — it just does not gain a mark.
    # Only a ref that cannot even be parsed stops the run, because that is a broken file, not a
    # broken target. (The one known case: s4a17:grp8[0]. Area 17 has no op-6 and no op-8 cell, and
    # grp8's pointer aliases grp6's, so both name the same bytes at 2042 that no cell dispatches. The
    # note assumed a KEYWORD record where the engine uses CONDs — grp2[7..9] carry those strings.)
    if write and any("not parseable" in p for p in problems):
        print("\nrefusing to write: a ref is malformed", file=sys.stderr)
        return 1
    if write:
        shutil.copy(QUESTS, QUESTS + ".bak_v2")
        with open(QUESTS, "w") as fh:
            json.dump(doc, fh, indent=1, ensure_ascii=False, sort_keys=True)
        print(f"\nwritten; previous file kept as {os.path.basename(QUESTS)}.bak_v2")
    else:
        print("\ndry run — rerun with --write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
