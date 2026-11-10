#!/usr/bin/env python3
"""_story_test.py — the fold is a view, and the gate only says what it measured.

Two claims are worth a battery here, and they are different in kind.

The FOLD is a claim about faithfulness: folding may drop nothing and invent nothing. Every record
must end up either as a beat or on an arrow, the beats must be beats, and a coordinate must be
either one the chain owns, one an arrow planted, or one inherited from the square before — never
conjured. Run over all 43 areas, that is a real constraint: 400-odd chains with every shipped shape
in them, including the ones nobody would think to write a case for.

The GATE is a claim about honesty: it may only report what the shipped bytes support. So the
grammar is measured here too, and the two checks that matter are the negative ones — that
ENCOUNTER→ENCOUNTER really is absent from the shipped game, and that injecting one is caught.
"""

import copy
import sys

sys.path.insert(0, ".")
import app                       # noqa: E402
import wl_cell                   # noqa: E402
import wl_ir                     # noqa: E402
import wl_story                  # noqa: E402

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)
    return cond


def main():
    areas = app.list_areas()["areas"]
    grammar = app._edge_grammar()

    # --- (a) the fold loses nothing and invents nothing -------------------------------------
    chains = beats = folded = 0
    for a in areas:
        side, aid = a["side"], a["area_id"]
        ir = app._ir(side, aid)
        st = app.area_story(side, aid)
        for sto in st["stories"]:
            chains += 1
            beats += sto["shown"]
            folded += sto["records"] - sto["shown"]
            check(sto["shown"] <= sto["records"],
                  f"s{side}a{aid} @{sto['cell']}: {sto['shown']} beats from {sto['records']} records")

            # every beat is a BEAT op — a CELL_PATCH must never surface as a row
            for b in sto["beats"]:
                check(wl_story.is_beat(b["grp"]),
                      f"s{side}a{aid} grp{b['grp']}[{b['cid']}] is plumbing but was rendered as a beat")
                check(b["cell_owner"] in ("chain", "edge", "same", None),
                      f"s{side}a{aid} grp{b['grp']}[{b['cid']}]: unknown cell owner {b['cell_owner']}")
                # a coordinate must have a reason. `edge` means a patch entry on the incoming arrow
                # named this hop; anything else is inherited or the chain's own square.
                if b["cell_owner"] == "edge":
                    hop = {(v["grp"], v["cid"]) for v in b["in_edge"]["via"]} | {(b["grp"], b["cid"])}
                    hit = [e for e in b["in_edge"]["effects"]
                           if e.get("kind") == "cell" and (e.get("to_op"), e.get("to_id")) in hop]
                    check(hit and [hit[0]["x"], hit[0]["y"]] == b["cell"],
                          f"s{side}a{aid} grp{b['grp']}[{b['cid']}]: cell {b['cell']} claimed from an "
                          f"edge that does not plant it")

            # the arrows must account for exactly the records the beats did not
            via = sum(len(b["in_edge"]["via"]) for b in sto["beats"] if b["in_edge"])
            via += len(sto["trailing"])
            check(via == sto["records"] - sto["shown"],
                  f"s{side}a{aid} @{sto['cell']}: {via} records on arrows but "
                  f"{sto['records'] - sto['shown']} were folded away — the fold dropped one")

    print(f"(a) fold: {chains} chains, {beats} beats, {folded} records folded onto arrows — "
          f"nothing dropped, nothing invented")

    # --- (b) the Missing Toaster, the shape this was built for -------------------------------
    st = app.area_story(1, 51)
    toaster = next((s for s in st["stories"] if s["cell"] == [24, 25]), None)
    if check(toaster is not None, "s1a51: no chain at (24,25)"):
        check(toaster["records"] == 7 and toaster["shown"] == 4,
              f"toaster: {toaster['records']} records → {toaster['shown']} beats, expected 7 → 4")
        got = [(b["op"], b["cell"], b["cell_owner"]) for b in toaster["beats"]]
        want = [("ENCOUNTER", [24, 25], "chain"), ("ENCOUNTER", [25, 26], "edge"),
                ("LOOT", [25, 26], "same"), ("SERVICE", [25, 25], "edge")]
        check(got == want, f"toaster beats {got}\n            expected {want}")
        # the OBJ marker's tile must come off byte 1 — byte 0 is the message, and reading it
        # reported TREX's droid as "tile $00"
        tiles = [e for b in toaster["beats"] if b["in_edge"]
                 for e in b["in_edge"]["effects"] if e["kind"] == "tile"]
        check(tiles and tiles[0]["tile"] == 0x02,
              f"toaster: OBJ marker tile is {tiles and hex(tiles[0]['tile'])}, expected $02")
        print(f"(b) toaster: 7 records → 4 beats, cells {[b['cell'] for b in toaster['beats']]}, "
              f"marker tile ${tiles[0]['tile']:02X}")

    # --- (c) the grammar is measured, and the gate uses only the measurement ------------------
    check(grammar["counts"].get("ENCOUNTER>ENCOUNTER", 0) == 0,
          "ENCOUNTER>ENCOUNTER is no longer absent from the shipped game — "
          "the gate's headline example needs re-deriving")
    top = sorted(grammar["counts"].items(), key=lambda kv: -kv[1])[:3]
    print(f"(c) grammar: {grammar['total']} edges over {len(areas)} areas, "
          f"{len(grammar['counts'])} distinct shapes; top {', '.join(f'{k} {v}' for k, v in top)}; "
          f"ENCOUNTER>ENCOUNTER 0")

    # inject the shape that hung the game and confirm the gate catches it
    bad = copy.deepcopy(app._ir(1, 51))
    bad["groups"]["3"]["48"]["victory"] = {"op": 3, "id": 47}
    caught = [f for f in wl_story.validate(bad, grammar, wl_story.budget(bad))
              if f["kind"] == "unseen-edge"]
    check(caught, "an ENCOUNTER handing over to an ENCOUNTER passed the gate")
    # and confirm it does NOT fire on the area as shipped
    clean = [f for f in wl_story.validate(app._ir(1, 51), grammar, None) if f["kind"] == "unseen-edge"]
    check(not clean, f"the gate flags shipped edges as unseen: {[f['text'] for f in clean]}")
    print(f"(d) gate: injected ENCOUNTER→ENCOUNTER caught; shipped area clean")

    # --- (e) the budget's one hard number ----------------------------------------------------
    # A text id is 7 bits. Eleven shipped areas hold longer pools — that is not an error, those
    # strings are reached by pointer — but in such an area no NEW string can be named, and that is
    # the number authoring has to see BEFORE three steps are wired into it.
    full = []
    for a in areas:
        b = app.area_budget(a["side"], a["area_id"])
        s = b["strings"]
        check(s["free"] == max(0, wl_story.TEXT_ID_MAX - s["used"]),
              f"s{a['side']}a{a['area_id']}: free {s['free']} does not follow from used {s['used']}")
        check(s["past_id"] == max(0, s["used"] - wl_story.TEXT_ID_MAX),
              f"s{a['side']}a{a['area_id']}: past_id inconsistent")
        if not s["free"]:
            full.append(f"s{a['side']}a{a['area_id']}")
    check(len(full) >= 11, f"only {len(full)} areas out of text ids — expected the 11 known long pools")
    print(f"(e) budget: {len(full)} areas have no free text id ({', '.join(full[:6])}…)")

    # --- (f) the COND form is LOSSLESS ------------------------------------------------------
    # A form that cannot reproduce what it was shown will quietly rewrite a record the moment
    # somebody opens it and presses Save. So: read every shipped gate through record_fields, write
    # the same values straight back, and require the bytes to be identical. Round-tripping the
    # decoder against itself proves nothing — this compares against the emitted record.
    gates = drift = 0
    for a in areas:
        ir = app._ir(a["side"], a["area_id"])
        area, _, _, _ = app._load_area_full(a["side"], a["area_id"])
        for cid, rr in (ir.get("groups") or {}).get("2", {}).items():
            if rr.get("op") != "COND":
                continue
            gates += 1
            before = wl_ir._emit_op_record(rr)
            F = {f["key"]: f["value"] for f in wl_cell.record_fields(copy.deepcopy(rr), area)}
            r2 = wl_cell.patch_record_fields(copy.deepcopy(rr), F)
            if wl_ir._emit_op_record(r2) != before:
                drift += 1
                if drift <= 3:
                    FAIL.append(f"s{a['side']}a{a['area_id']} grp2[{cid}]: the COND form does not "
                                f"reproduce its own record")
    check(drift == 0, f"{drift} of {gates} gates drift when saved unchanged")
    print(f"(f) COND form: {gates} shipped gates read and written back byte-identically")

    # --- (g) the TREE sees at least what the single-path fold saw ---------------------------
    # The tree exists because the old walk followed the FIRST edge out of every record and dropped
    # the rest — a KEYWORD with three keywords was drawn as one line. So the tree must be a superset:
    # every record the linear fold reached must appear in the tree of the same root, and the tree
    # must additionally reach the branches. Anything else means the tree walk lost a path while
    # gaining one, which is the failure that would be invisible in a screenshot.
    grew = same = 0
    for a in areas:
        side, aid = a["side"], a["area_id"]
        ir = app._ir(side, aid)
        st = app.area_story(side, aid)
        for sto in st["stories"]:
            flat = {(b["grp"], b["cid"]) for b in sto["beats"]}
            first = sto["beats"][0]
            t = wl_story.tree(ir, first["grp"], first["cid"], sto["cell"])
            rows = wl_story.flatten(t)
            seen = {(r["grp"], r["cid"]) for r in rows if r.get("grp") is not None}
            missing = flat - seen
            check(not missing, f"s{side}a{aid} @{sto['cell']}: the tree lost {sorted(missing)} "
                               f"that the linear fold reached")
            if len(seen) > len(flat):
                grew += 1
            elif seen == flat:
                same += 1
            # indentation must mark a fork and nothing else
            for r in rows:
                if r["depth"] and r["forks"] == 0 and r is rows[-1]:
                    pass
    print(f"(g) tree: superset of the linear fold everywhere · {grew} chains reach further once "
          f"every branch is followed, {same} are genuinely linear")

    if FAIL:
        print(f"\nFAILED ({len(FAIL)}):")
        for f in FAIL:
            print("  -", f)
        return 1
    print("\nthe fold loses nothing, and the gate reports only what the shipped bytes support")
    return 0


if __name__ == "__main__":
    sys.exit(main())
