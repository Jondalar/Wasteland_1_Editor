#!/usr/bin/env python3
"""_questlog_test.py — the quest MODEL, held to the standard the record batteries hold bytes to.

The other batteries in this directory all check the same thing in the end: that a byte the editor
writes means what the editor thinks it means. The quest side has no bytes yet, and that is exactly
why it needs a battery *now* — every one of the four modules under test makes a promise that will
be impossible to un-break later:

  wl_questlog       migrates 34 quests of HANDWRITING. Notes nobody can retype, refs nobody can
                    re-derive. A migration that loses one of them loses it for good, and it would
                    lose it silently — the file still parses, the Events rail still renders, and
                    the sentence is simply gone. So the test is not "does it produce v2": it is
                    "is every key, every value, every note, every ref still there afterwards", and
                    "does a second pass change nothing" (save() migrates on every write — a
                    migration that drifted by one field per pass would rewrite 60 KB forever).

  wl_questlog qids  a qid is a SAVE-FILE ADDRESS. A cart in somebody's drawer refers to a quest by
                    number, so handing a retired number to a new quest makes an old save read as
                    progress in a story that never happened. Allocation must be unique, and a
                    retired id must stay spent.

  wl_qvalidate      the battery that reads the author's intent. Two of the three live faults in the
                    TREX build were our own emitters agreeing with our own readers, so each check
                    here is asserted on the FULL set it raises, not just the one it is named after:
                    a battery that cross-fires is one nobody reads. And the case that matters most
                    is the one that must stay SILENT — a beat with no quest is correct and normal
                    (Konzept §7.3), so questfreedom may never produce a complaint.

  wl_place          which beat carries a quest binding. `door-to-service` is the trap: the last
                    beat is the SHOP, and an op-$6 AREA_EVENT has no hand-over field at all — bind
                    there and the effect compiles into nothing. The door must come out, for all
                    eight patterns, and a new pattern must fail this test rather than slip past it.
                    Section (j) then runs the compile path itself against the baseline blobs with
                    `commit=False`: the `place` half of the Spec §8 table had the same shape of
                    hole this battery exists to close — the checks were tested by their validate
                    NAMESAKES, which are different code, and no case ever called `place()` at all.

  wl_scene_registry a routine without an entry is not callable (§2.6). If that refusal ever stops
                    refusing, an unknown name goes into a record as a mystery byte.

READ-ONLY. The real `annotations/quests.json` is read and worked on as an in-memory copy; nothing
here writes anything anywhere. Section (i) proves it rather than claiming it.

    python3 editor/backend/_questlog_test.py [-v]
"""
import copy
import hashlib
import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wl_blueprint                                              # noqa: E402
import wl_item                                                   # noqa: E402
import wl_place                                                  # noqa: E402
import wl_questlog                                               # noqa: E402
import wl_qvalidate                                              # noqa: E402
import wl_scene_registry                                         # noqa: E402

ANNOTATIONS = pathlib.Path(__file__).resolve().parents[1] / "annotations"
QUESTS = ANNOTATIONS / "quests.json"
REPO = ANNOTATIONS.parents[1]                                    # the repo root wl_item reads from

# What the shipped file holds today. Written down rather than counted from the file itself, because
# "34 quests survived" is only a claim if the 34 is independent of the thing under test.
N_QUESTS, N_STEPS = 34, 233

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)
    return cond


def _fingerprint(path):
    """What a file is, from the outside — enough to notice any write at all."""
    b = path.read_bytes()
    return hashlib.sha256(b).hexdigest(), len(b)


# ── (a) the migration loses nothing ───────────────────────────────────────────────────────────
def a_lossless(src):
    """Every key, every value, every note and every ref of the shipped file, still there.

    Field by field rather than by count: a migration that dropped one note and added one key would
    keep every total intact.
    """
    doc = wl_questlog.migrate(copy.deepcopy(src))
    q0, q1 = wl_questlog.quests(src), wl_questlog.quests(doc)

    check(len(q0) == N_QUESTS, f"the shipped file holds {len(q0)} quests, expected {N_QUESTS}")
    check(set(q0) == set(q1),
          f"migrate changed the quest set: lost {sorted(set(q0) - set(q1))}, "
          f"invented {sorted(set(q1) - set(q0))}")

    steps0 = sum(len(q.get("steps") or []) for q in q0.values())
    steps1 = sum(len(q.get("steps") or []) for q in q1.values())
    check(steps0 == N_STEPS, f"the shipped file holds {steps0} steps, expected {N_STEPS}")
    check(steps0 == steps1, f"migrate turned {steps0} steps into {steps1}")

    dropped = []
    for slug, before in q0.items():
        after = q1.get(slug) or {}
        for k, v in before.items():
            if k == "steps":
                continue                                  # the one field that legitimately changes
            if after.get(k) != v:
                dropped.append(f"{slug}.{k}: {v!r} → {after.get(k)!r}")
        for i, st in enumerate(before.get("steps") or []):
            got = (after.get("steps") or [None] * (i + 1))[i]
            if not isinstance(got, dict):
                dropped.append(f"{slug}.steps[{i}]: became {got!r}")
                continue
            for k, v in (st if isinstance(st, dict) else {"ref": st}).items():
                if got.get(k) != v:
                    dropped.append(f"{slug}.steps[{i}].{k}: {v!r} → {got.get(k)!r}")
    check(not dropped, f"migrate lost {len(dropped)} field(s), e.g. {dropped[:3]}")

    # the refs and the notes are the handwriting — count them the way a reader would
    def refs(qs):
        return sorted((s, st.get("ref")) for s, q in qs.items()
                      for st in (q.get("steps") or []) if isinstance(st, dict) and st.get("ref"))

    def notes(qs):
        return sorted((s, st.get("note")) for s, q in qs.items()
                      for st in (q.get("steps") or []) if isinstance(st, dict) and st.get("note"))
    check(refs(q0) == refs(q1), "the refs do not survive the migration")
    check(notes(q0) == notes(q1), "the step notes do not survive the migration")

    # only ADDING is allowed, and only the fields the spec names
    added_q = {k for q in q1.values() for k in q} - {k for q in q0.values() for k in q}
    added_s = ({k for q in q1.values() for st in q["steps"] for k in st}
               - {k for q in q0.values() for st in (q.get("steps") or []) for k in st})
    check(added_q <= {"qid", "axis", "kind", "home"},
          f"migrate added quest fields nobody asked for: {sorted(added_q)}")
    check(added_s <= {"kind", "phase"},
          f"migrate added step fields nobody asked for: {sorted(added_s)}")

    # the document's own keys, and anything the file holds that is neither quest nor bookkeeping
    check(doc.get("_README") == src.get("_README"), "_README did not survive the migration")
    check(wl_questlog.version(doc) == wl_questlog.VERSION,
          f"migrate produced v{wl_questlog.version(doc)}")
    odd = copy.deepcopy(src)
    odd["schema_note"] = "not a quest, not bookkeeping — and still nobody's to throw away"
    odd["_retired_qids"] = [200, 201]
    out = wl_questlog.migrate(odd)
    check(out.get("schema_note") == odd["schema_note"],
          "a non-quest top-level key was dropped — losing data is not a migration")
    check(out.get("_retired_qids") == [200, 201], "the retired ids did not survive the migration")
    check(all(not str(k).startswith("_") or k.startswith("_") for k in out),
          "unreachable")                                  # shape guard, kept for the next reader
    print(f"(a) migrate: {len(q0)} quests, {steps0} steps — every field, every ref, every note "
          f"survives; adds {sorted(added_q)} / {sorted(added_s)} and drops nothing")
    return doc


# ── (b) the migration is a fixpoint ───────────────────────────────────────────────────────────
def b_idempotent(src, once):
    twice = wl_questlog.migrate(copy.deepcopy(once))
    same = json.dumps(twice, sort_keys=True) == json.dumps(once, sort_keys=True)
    if not check(same, "a second migrate() changes the document — save() runs it on every write, "
                       "so this rewrites 60 KB forever"):
        import difflib
        a = json.dumps(once, indent=1, sort_keys=True).splitlines()
        b = json.dumps(twice, indent=1, sort_keys=True).splitlines()
        FAIL.append("  " + " / ".join(list(difflib.unified_diff(a, b, "pass1", "pass2", n=0))[2:8]))

    # …and on a document holding every step shape at once, which the shipped file does not: a bare
    # string ref, an annotated ref, a beat that still says `state`, a beat with records, and a dict
    # that is neither (`_step_kind` returns None for it ON PURPOSE — inventing a kind would be the
    # first lossy guess, so it has to survive untouched twice over).
    mixed = {"_version": 1, "_README": "keep me",
             "q": {"order": 1, "track": "side", "steps": [
                 "s1a9:grp1[21]",
                 {"ref": "s1a9:grp8[0]", "note": "what the farmers say", "conds": [1, 2]},
                 {"blueprint": "a-sign", "state": "cast", "values": {"say": "hi"}},
                 {"title": "the sack", "records": ["grp5[3]"], "at": {"side": 1, "area": 51}},
                 {"note": "neither a ref nor a beat"}]}}
    m1 = wl_questlog.migrate(copy.deepcopy(mixed))
    m2 = wl_questlog.migrate(copy.deepcopy(m1))
    check(json.dumps(m1, sort_keys=True) == json.dumps(m2, sort_keys=True),
          "migrate is not idempotent on mixed step shapes")
    st = m1["q"]["steps"]
    check(st[0] == {"kind": "ref", "ref": "s1a9:grp1[21]"},
          f"a bare string ref became {st[0]!r}")
    check(st[1].get("conds") == [1, 2], "an unknown step key (`conds`) was thrown away")
    check(st[2].get("phase") == "cast" and "state" not in st[2],
          f"`state` → `phase` did not happen: {st[2]!r}")
    check(st[3].get("kind") == "beat" and st[3].get("phase") == "placed",
          f"a beat with records is not `placed`: {st[3]!r}")
    check(st[4] == {"note": "neither a ref nor a beat"},
          f"a step that is neither shape was rewritten: {st[4]!r}")
    check(m1["q"].get("track") == "side" and m1["q"].get("kind") == "side",
          "`track` decides `kind` where a human typed one, and stays in place — "
          f"got track={m1['q'].get('track')!r} kind={m1['q'].get('kind')!r}")
    # …and a quest that says nothing about which track it is on keeps saying nothing. `order` used
    # to decide it, but all 34 shipped quests carry an `order` and the side quests hold 8, 9, 14,
    # 22 … — it is a rank across BOTH tracks, so that rule would have written `kind: "main"` into
    # the fifteen quests nobody has classified, permanently and invisibly (every view renders
    # `track`).
    unsaid = wl_questlog.migrate({"q": {"order": 3, "steps": []}})["q"]
    check("kind" not in unsaid,
          f"a quest with no `track` was given a kind out of `order`: {unsaid.get('kind')!r}")
    said = wl_questlog.migrate({"q": {"order": 3, "track": "main", "steps": []}})["q"]
    check(said.get("kind") == "main", f"`track` did not decide `kind`: {said.get('kind')!r}")

    kinds = sorted({q.get("kind") or "(unsaid)" for q in wl_questlog.quests(once).values()})
    print(f"(b) idempotent: a second pass over the shipped file changes nothing; every step shape "
          f"reaches a fixpoint · quest kinds {kinds}")


# ── (c) qids ──────────────────────────────────────────────────────────────────────────────────
def c_qids(doc):
    qs = wl_questlog.quests(doc)
    ids = [q.get("qid") for q in qs.values()]
    check(all(isinstance(i, int) and 0 <= i <= wl_questlog.QID_MAX for i in ids),
          f"a quest came out of the migration without a usable qid: {sorted(set(ids))[:5]}")
    check(len(set(ids)) == len(ids),
          f"duplicate qids after the migration: "
          f"{sorted(i for i in set(ids) if ids.count(i) > 1)}")
    check(wl_questlog.used_qids(doc) == set(ids), "used_qids disagrees with the quests it read")
    check(wl_questlog.by_qid(doc) == {q["qid"]: s for s, q in qs.items()},
          "by_qid is not the inverse of the qid field")

    # allocation: hand out sixteen in a row, ASSIGNING each — an allocator that does not see its
    # own last answer hands the same number out sixteen times
    live = copy.deepcopy(doc)
    handed = []
    for n in range(16):
        i = wl_questlog.alloc_qid(live)
        check(i not in wl_questlog.used_qids(live), f"alloc_qid handed out {i}, which is in use")
        handed.append(i)
        live[f"new-{n}"] = {"name": f"n{n}", "qid": i, "axis": [], "steps": []}
    check(len(set(handed)) == len(handed), f"alloc_qid repeated itself: {handed}")
    check(handed == sorted(handed) and handed[0] == max(ids) + 1,
          f"alloc_qid is meant to give the smallest free id first; it gave {handed[:4]}…")

    # a hole in the middle is filled before the top is extended
    holed = copy.deepcopy(doc)
    victim = next(s for s, q in wl_questlog.quests(holed).items() if q["qid"] == 7)
    del holed[victim]
    check(wl_questlog.alloc_qid(holed) == 7, "a freed number in the middle is not offered first")

    # …and THAT is the trap retire_qid closes: the delete path has two halves
    retired = wl_questlog.retire_qid(copy.deepcopy(holed), 7)
    check(wl_questlog.alloc_qid(retired) != 7,
          "a retired qid was handed straight back out — an old save would read as progress in a "
          "story that never happened")
    check(7 in wl_questlog.used_qids(retired), "a retired qid is not counted as spent")
    # `.get` rather than `[...]`: a retire_qid that quietly did nothing would raise here, and a
    # battery that dies on the fault it is testing reports nothing about the four sections after it
    twice = (wl_questlog.retire_qid(copy.deepcopy(retired), 7) or {}).get("_retired_qids") or []
    check(twice.count(7) == 1, f"retiring the same id twice lists it {twice.count(7)}×: {twice}")
    try:
        wl_questlog.retire_qid({}, 999)
        FAIL.append("retire_qid accepted 999 as a questlog address (one byte, 0..255)")
    except ValueError:
        pass

    try:
        wl_questlog.alloc_qid({"_retired_qids": list(range(wl_questlog.QID_MAX + 1))})
        FAIL.append("alloc_qid invented an id out of a full document instead of refusing")
    except ValueError:
        pass
    print(f"(c) qids: {len(ids)} unique, 0..{max(ids)}; 16 allocated in a row without a repeat; "
          f"a hole is refilled, a RETIRED id never is; a full document refuses")


# ── (d) the axis ──────────────────────────────────────────────────────────────────────────────
def d_axis():
    """`open` is position 0 and has no bit. Everything below follows from that one sentence."""
    q = {"axis": [{"id": "s1", "log": "a"}, {"id": "s2"}, {"id": "done"}, {"id": "failed"}]}
    check(wl_questlog.axis_ids(q) == ["s1", "s2", "done", "failed"],
          f"axis_ids reordered or filtered: {wl_questlog.axis_ids(q)}")
    states = [s["id"] for s in wl_questlog.axis_states(q)]
    check(states == ["open", "s1", "s2", "done", "failed"],
          f"axis_states must lead with the implicit `open`: {states}")
    check(wl_questlog.axis_states(q)[0].get("implicit") is True,
          "the leading `open` is not marked implicit")

    # a typed `open` is dropped — position 0 is not a stored bit, and letting it in gives it one
    typed = {"axis": [{"id": "open"}, {"id": "s1"}, {"id": "done"}]}
    check(wl_questlog.axis_ids(typed) == ["s1", "done"],
          f"a typed `open` reached the storage axis: {wl_questlog.axis_ids(typed)}")
    check([s["id"] for s in wl_questlog.axis_states(typed)] == ["open", "s1", "done"],
          "a typed `open` was listed twice")
    check(wl_questlog.bytes_for(typed) == wl_questlog.bytes_for({"axis": [{"id": "s1"},
                                                                         {"id": "done"}]}),
          "a typed `open` costs a bit it must not cost")

    # a bare-string axis is legal too
    check(wl_questlog.axis_ids({"axis": ["s1", "s2"]}) == ["s1", "s2"],
          "a bare-string axis entry is not read")

    check(wl_questlog.axis_index(q, "open") == 0, "open is not index 0")
    check(wl_questlog.axis_index(q, "done") == 3, "axis_index counts wrong")
    check(wl_questlog.axis_index(q, "DONE") == 3, "axis_index is case-sensitive on a state name")
    check(wl_questlog.axis_index(q, "nope") is None, "axis_index invented an index")

    # the storage bit is index-1, which is the sentence the whole model rests on
    check(all(wl_questlog.axis_index(q, s) - 1 == i for i, s in enumerate(wl_questlog.axis_ids(q))),
          "the documented bit position (axis_index - 1) does not line up with the axis order")

    sizes = [(n, wl_questlog.bytes_for({"axis": [{"id": f"s{i}"} for i in range(n)]}))
             for n in (0, 1, 7, 8, 9, 16, 17)]
    check(sizes == [(0, 0), (1, 1), (7, 1), (8, 1), (9, 2), (16, 2), (17, 3)],
          f"bytes_for is not one bit per state with `open` free: {sizes}")

    check(wl_questlog.at_least(q, "s2", "s1") and not wl_questlog.at_least(q, "s1", "s2"),
          "at_least does not order the forward states")
    check(wl_questlog.at_least(q, "open", "open") and not wl_questlog.at_least(q, "open", "s1"),
          "at_least mishandles the implicit `open`")
    check(wl_questlog.at_least(q, "done", "done"), "at_least says a state is not itself")
    # `failed` stands BESIDE the line: it is not past what it never reached, and no forward state
    # implies it. That is why the spec writes the failed gate as == and the others as ≥.
    check(not wl_questlog.at_least(q, "done", "failed"), "a finished quest counted as failed")
    check(not wl_questlog.at_least(q, "failed", "s1"), "a failed quest counted as past s1")
    check(wl_questlog.at_least(q, "failed", "failed"), "failed does not compare equal to itself")
    # …and it must not shift the forward count either
    nofail = {"axis": [{"id": "s1"}, {"id": "s2"}, {"id": "done"}]}
    check(wl_questlog.at_least(q, "done", "s2") == wl_questlog.at_least(nofail, "done", "s2"),
          "declaring `failed` changed the order of the forward states")
    for a, b in (("s9", "s1"), ("s1", "s9")):
        try:
            wl_questlog.at_least(q, a, b)
            FAIL.append(f"at_least({a}, {b}) answered instead of naming the unknown state — "
                        f"a typo in a gate is exactly what this must not swallow")
        except ValueError:
            pass

    check(wl_questlog.is_reserved("Open") and wl_questlog.is_reserved("failed")
          and not wl_questlog.is_reserved("s1"),
          "is_reserved does not match RESERVED_STATES")
    print(f"(d) axis: implicit `open` at 0 (typed `open` dropped), bit = index-1, "
          f"bytes {[n for _, n in sizes]} for {[n for n, _ in sizes]} states, "
          f"`failed` beside the line")


# ── (e) markers ───────────────────────────────────────────────────────────────────────────────
def e_markers():
    """Three spare item slots in the whole game. The point of the helper is that it says so."""
    names = {1: "Canteen", 70: "", 71: "", 72: "(unused 72)", 94: "Meson Cannon"}
    check(wl_questlog.free_item_ids(names) == [70, 71, 72],
          f"free_item_ids over an injected table: {wl_questlog.free_item_ids(names)}")
    check(wl_questlog.free_item_ids() == [70, 71, 72],
          f"the shipped item table no longer has exactly 70/71/72 spare: "
          f"{wl_questlog.free_item_ids()}")

    doc, handed = {"_version": 2}, []
    for n in range(3):
        i = wl_questlog.alloc_item_marker(doc, names)
        handed.append(i)
        doc[f"q{n}"] = {"markers": [{"kind": "item", "item": i}]}
    check(handed == [70, 71, 72], f"item markers were not handed out in order: {handed}")
    check(wl_questlog.alloc_item_marker(doc, names) is None,
          "a fourth item marker was invented — the caller has to be able to say 422 instead of "
          "recycling a live item id")
    check(wl_questlog.marker_items(doc) == {70, 71, 72},
          "marker_items does not see what was handed out")

    # …and spare is not enough. Byte 4 of the shipped descriptors is [5, 1, 0] for 70/71/72, and an
    # op-1 `have(item)` SPENDS one use per test ($14A5 skips the decrement only at zero). A marker
    # on 70 would answer five times and then not exist. So a caller that knows the counts gets only
    # the ids a gate can test forever — spec §2.3, the spend trap.
    counts = wl_item.table(REPO) or []
    real = {r["id"]: r["charges"] for r in counts}
    check([real.get(i) for i in (70, 71, 72)] == [5, 1, 0],
          f"the shipped charge counts for the three spare ids moved: "
          f"{[real.get(i) for i in (70, 71, 72)]}")
    check(wl_questlog.free_item_ids(charges=counts) == [72],
          f"free_item_ids still offers a spendable id as a marker: "
          f"{wl_questlog.free_item_ids(charges=counts)}")
    check(wl_questlog.alloc_item_marker({"_version": 2}, charges=counts) == 72,
          "alloc_item_marker did not pick the one id a marker survives on")
    check(wl_questlog.spendable_free_ids(charges=counts) == [(70, 5), (71, 1)],
          f"the refusal cannot say why 70/71 are not on offer: "
          f"{wl_questlog.spendable_free_ids(charges=counts)}")
    check(wl_questlog.free_item_ids() == [70, 71, 72],
          "without the counts the allocator has to behave as before — it cannot see them, and "
          "pretending otherwise would hide the trap instead of naming it")

    check(wl_questlog.check_word_unique("CATERPILLAR", ["THANATOS"]) is None,
          "a free word was refused")
    for bad, why in (("", "empty"), ("AB", "two letters"), ("1UP", "leading digit"),
                     ("SOME WORD", "a space the player cannot type as one word")):
        check(wl_questlog.check_word_unique(bad, []) is not None,
              f"check_word_unique accepted {bad!r} ({why})")
    # CASE IS NORMALISED, not refused — and it has to be, because that is the only reason a
    # collision is caught at all: `marker_words` upper-cases what it reads, so a marker typed in
    # lower case is the same keyword as one typed in capitals and must collide with it.
    check(wl_questlog.check_word_unique("caterpillar", []) is None,
          "a lower-case word was refused rather than normalised")
    check(wl_questlog.check_word_unique("thanatos", ["THANATOS"]) is not None,
          "a collision was missed because the word was typed in lower case")
    check(wl_questlog.marker_words({"q": {"markers": [{"kind": "word", "word": "caterpillar"}]}})
          == {"CATERPILLAR"}, "a lower-case marker is not read as the keyword it becomes")
    doc2 = {"q": {"markers": [{"kind": "word", "word": "garoof"}]}}
    check(wl_questlog.check_word_unique("GAROOF", [], doc2) is not None,
          "two quests may not declare the same marker word")
    check(wl_questlog.marker_words(doc2) == {"GAROOF"}, "marker_words does not upper-case")
    print("(e) markers: 70/71/72 and nothing else, of which only 72 is created at count 0 and may "
          "be a marker; exhaustion answers None; a word is refused for shape, for the game's own "
          "keywords and for another quest's marker")


# ── (f) wl_qvalidate — one case per check, and one that must stay silent ───────────────────────
def _at(col, row):
    return {"side": 1, "area": 51, "col": col, "row": row}


CLEAN = {
    "_version": 2,
    "_README": "not a quest",
    "trex-missing-toaster": {
        "name": "The Missing Toaster", "qid": 2, "kind": "side",
        "home": {"side": 1, "area": 51},
        "axis": [
            {"id": "asked", "log": "The TREX wants its toaster back."},
            {"id": "found", "log": "We found the toaster in the scrapyard."},
            {"id": "done", "log": "The toaster is back with the TREX."},
            {"id": "failed", "log": "The toaster is slag."},
        ],
        "steps": [
            {"kind": "beat", "phase": "placed", "id": "ask", "trigger": "talk", "at": _at(24, 25),
             "blueprint": "a-sign", "bind": {"effect": "advance", "to": "asked", "on": "say"}},
            {"kind": "beat", "phase": "placed", "id": "fetch", "trigger": "step_on",
             "at": _at(25, 26), "blueprint": "guarded-stash",
             "gate": {"cond": "quest", "quest": 2, "state": "asked"},
             "bind": {"effect": "advance", "to": "found", "on": "loot"}},
            {"kind": "beat", "phase": "placed", "id": "hand-over", "trigger": "talk",
             "at": _at(24, 25), "blueprint": "a-sign",
             "gate": {"cond": "quest", "quest": 2, "state": "found"},
             "bind": {"effect": "advance", "to": "done", "on": "say"}},
        ],
    },
}
SLUG = "trex-missing-toaster"


def _limit(q):
    q["steps"][2]["bind"]["to"] = "s9"                    # a state the axis never declared


def _fail_no_failed(q):
    q["axis"] = [a for a in q["axis"] if a["id"] != "failed"]
    q["steps"][2]["bind"] = {"effect": "fail", "on": "say"}


def _monotonic(q):
    q["steps"][2]["bind"]["to"] = "asked"                 # gate needs `found`, advance falls back


def _reachable(q):
    del q["steps"][1]["bind"]                             # nothing reaches `found`; step 2 gates on it


def _no_qid(q):
    del q["qid"]                                          # bindings with nowhere to write


def _carrier(q):
    q["steps"][0]["blueprint"] = "door-to-service"        # bind the shop AT the shop…
    q["steps"][0]["bind"]["on"] = "service"               # …the one op with no hand-over field


def _text_budget(q):
    q["axis"][0]["log"] = ("The TREX, keeper of the scrapyard, wants the toaster it lost "
                           "returned to it at once.")


def _log_gap(q):
    del q["axis"][1]["log"]                               # `found` is gated on and says nothing


def _unbound(q):
    del q["steps"][1]["at"]                               # a beat still without a cell


def _chained(q):
    q["steps"][1]["trigger"] = "chained"                  # nothing says who plants it


def _chained_ok(q):
    q["steps"][1]["trigger"] = "chained"
    q["steps"][0]["next"] = "fetch"                       # …and now something does


def _questfree_beat(q):
    # THE case. A beat that belongs to no quest is not an omission, it is the normal shape of the
    # world (Konzept §7.3): the records exist, the area is the store, and there is nothing to say.
    q["steps"].append({"kind": "beat", "phase": "placed", "id": "scenery", "at": _at(1, 1),
                       "blueprint": "a-sign", "values": {"say": "A rusted sign."}})


CASES = [
    ("clean",                 set(),                    lambda q: None),
    ("axis limit",            {"axis-limit"},           _limit),
    ("fail with no `failed`", {"axis-limit"},           _fail_no_failed),
    ("axis monotonic",        {"axis-monotonic"},       _monotonic),
    ("axis reachable",        {"axis-reachable"},       _reachable),
    ("bind without quest",    {"bind-without-quest"},   _no_qid),
    ("binding carrier",       {"binding-carrier"},      _carrier),
    ("axis text budget",      {"axis-text-budget"},     _text_budget),
    ("log gap",               {"log-gap"},              _log_gap),
    ("unbound beat",          {"unbound-beat"},         _unbound),
    ("unreachable beat",      {"unreachable-beat"},     _chained),
    ("chained with producer", set(),                    _chained_ok),
    ("questfree beat",        set(),                    _questfree_beat),
]

# Every check the module documents. A check that stops firing, or one that arrives without a case,
# both show up here — the battery has to know what it is supposed to cover.
DOCUMENTED = {"axis-limit", "axis-monotonic", "axis-reachable", "bind-without-quest",
              "binding-carrier", "axis-text-budget", "log-gap", "unbound-beat", "unreachable-beat",
              "histogram"}


def f_validate(real, verbose):
    covered = set()
    for name, expect, mutate in CASES:
        doc = copy.deepcopy(CLEAN)
        mutate(doc[SLUG])
        r = wl_qvalidate.validate_quest(doc, SLUG)
        raised = {e["check"] for e in r["errors"]} | {w["check"] for w in r["warnings"]}
        covered |= expect
        check(raised == expect,
              f"validate '{name}': raised {sorted(raised) or '-'}, expected {sorted(expect) or '-'}"
              + ("" if raised >= expect else " — the check it is named after did not fire")
              + ("" if raised <= expect else " — and a battery that cross-fires is one nobody reads"))
        # every finding has to be actionable: a sentence, and — where it carries one — a step index
        # that points into THIS quest's list. A finding whose site lies in another quest carries no
        # index at all, precisely so it can never point into the wrong list.
        steps = doc[SLUG].get("steps") or []
        for e in r["errors"] + r["warnings"]:
            check(bool(e.get("detail")),
                  f"validate '{name}': {e['check']} has no sentence a designer can act on")
            if "step" in e:
                check(isinstance(e["step"], int) and 0 <= e["step"] < len(steps),
                      f"validate '{name}': {e['check']} points at step {e['step']!r}, and this "
                      f"quest has {len(steps)}")
        info = {i["check"] for i in r["info"]}
        check("axis-bytes" in info, f"validate '{name}': the axis cost is not reported")
        if verbose:
            print(f"    {name:<22} {sorted(raised) or '-'}")
            for e in r["errors"] + r["warnings"]:
                print(f"        [{e['check']}] {e['detail']}")
    # ── the histogram · needs a POPULATION, so it gets a document of its own ─────────────────
    # "Where is this one the outlier" (Spec §8). Six sibling quests of four steps, one of twenty.
    # The check only speaks when a quest beats EVERY other on a metric and is at least twice the
    # typical one, which is also why it stays silent in all thirteen cases above: one quest is not
    # a distribution, and a warning that fires on everything is one nobody reads.
    pop = {"_version": 2}
    for n in range(6):
        pop[f"q{n}"] = {"name": f"q{n}", "qid": n,
                        "steps": [{"kind": "beat", "phase": "placed", "at": _at(1, 1),
                                   "blueprint": "a-sign"} for _ in range(4)]}
    pop["q0"]["steps"] *= 5                                # 20 steps against a median of 4
    raised = {e["check"] for e in wl_qvalidate.validate_quest(pop, "q0")["warnings"]}
    check(raised == {"histogram"},
          f"the outlier of the population raised {sorted(raised) or '-'}, expected ['histogram']")
    mid = {e["check"] for e in wl_qvalidate.validate_quest(pop, "q3")["warnings"]}
    check(not mid, f"a quest in the MIDDLE of the distribution was called an outlier: {sorted(mid)}")
    small = {"_version": 2, "a": pop["q0"], "b": pop["q1"]}
    check(not wl_qvalidate.validate_quest(small, "a")["warnings"],
          "two quests were treated as a distribution — that is an anecdote, not a histogram")
    covered |= {"histogram"}

    check(covered == DOCUMENTED,
          f"the case list does not line up with the documented checks: "
          f"missing {sorted(DOCUMENTED - covered)}, extra {sorted(covered - DOCUMENTED)}")

    # a quest that is nothing but questfree beats: no qid, no axis, no bindings — and no complaint
    bare = {"_version": 2, "scenery": {"name": "Furniture", "kind": "side", "steps": [
        {"kind": "beat", "phase": "placed", "at": _at(3, 4), "blueprint": "a-sign"},
        {"kind": "beat", "phase": "placed", "at": _at(5, 6), "blueprint": "guarded-stash"}]}}
    r = wl_qvalidate.validate_quest(bare, "scenery")
    check(not r["errors"] and not r["warnings"],
          f"a quest of questfree beats was complained about: "
          f"{[e['check'] for e in r['errors'] + r['warnings']]}")

    # the axis cost the validator reports must be the one the model charges
    for n in (0, 3, 9):
        q = {"axis": [{"id": f"s{i}"} for i in range(n)], "steps": []}
        got = wl_qvalidate.validate_quest({"q": q}, "q")["info"]
        want = wl_questlog.bytes_for(q)
        check(any(f"{want} byte" in i["detail"] for i in got),
              f"validate charges a {n}-state axis something other than {want} byte(s): "
              f"{[i['detail'] for i in got]}")

    # and the shipped file must be quiet: 34 annotated quests, nothing to report. A battery that
    # cries wolf on the existing data is a battery the designer switches off.
    noise, shape = [], []
    for slug in wl_questlog.quests(real):
        r = wl_qvalidate.validate_quest(real, slug)
        for e in r["errors"] + r["warnings"]:
            # the histogram is the one finding that is ALLOWED on the shipped file: it says nothing
            # is wrong, only that one quest is not shaped like the others (nomads-brakeman is 21
            # steps against a longest-other of 12). Everything else has to be silent.
            (shape if e["check"] == "histogram" else noise).append(f"{slug}: {e['check']}")
    check(not noise, f"the shipped quests raise {len(noise)} finding(s): {noise[:4]}")
    check(wl_qvalidate.validate_quest(real, "no-such-quest")["errors"],
          "an unknown slug got a clean bill of health")
    print(f"(f) validate: {len(CASES)} cases, one per documented check plus three that must stay "
          f"silent; {len(wl_questlog.quests(real))} shipped quests raise nothing but "
          f"{len(shape)} histogram note(s) ({', '.join(shape) or 'none'})")


# ── (g) wl_place — who carries the binding ────────────────────────────────────────────────────
# Measured over wl_blueprint.PATTERNS. `door-to-service` is the one that is not obvious and the one
# that matters: the last beat is the SHOP, and an op-$6 AREA_EVENT has no hand-over field at all, so
# the DOOR in front of it has to come out. Bind at the shop and the effect compiles into nothing.
CARRIER = {
    "guarded-stash": "loot",
    "guarded-stash-note": "after",
    "two-waves": "loot",
    "hidden-cache": "after",
    "locked-way": "door",
    "door-to-service": "door",
    "ambush": "after",
    "a-sign": "say",
}


def g_carriers():
    ids = {bp["id"] for bp in wl_blueprint.PATTERNS}
    check(set(CARRIER) == ids,
          f"the pattern set moved: this battery does not name {sorted(ids - set(CARRIER))}, "
          f"and names {sorted(set(CARRIER) - ids)} which no longer exists")

    for bp in wl_blueprint.PATTERNS:
        want = CARRIER.get(bp["id"])
        got = wl_place.default_carrier(bp["id"])
        check(got == want, f"default_carrier({bp['id']}) = {got!r}, expected {want!r}")
        check(wl_place.default_carrier(bp) == got,
              f"{bp['id']}: default_carrier disagrees with itself given the dict instead of the id")
        # the property behind the table: the LAST beat that can carry, not simply the last beat
        roles = [b["role"] for b in bp["beats"]]
        if got:
            check(wl_place.can_carry(next(b["op"] for b in bp["beats"] if b["role"] == got)),
                  f"{bp['id']}: the default carrier {got!r} cannot carry")
            after = roles[roles.index(got) + 1:]
            check(not any(wl_place.can_carry(b["op"]) for b in bp["beats"] if b["role"] in after),
                  f"{bp['id']}: {after} comes after {got!r} and could carry — the default must be "
                  f"the LAST beat that can")

    # the trap, said out loud
    d2s = next(bp for bp in wl_blueprint.PATTERNS if bp["id"] == "door-to-service")
    check(wl_place.default_carrier(d2s) == "door" and d2s["beats"][-1]["role"] == "service",
          "door-to-service must bind at the DOOR: its last beat is the shop, and an op-$6 "
          "AREA_EVENT has no hand-over field")
    check(not wl_place.can_carry(0x6) and wl_place.HANDOVER.get(0x6) is None,
          "op-$6 is listed as able to carry a binding")
    check(wl_place.CARRIERS["service"]["can_bind"] is False,
          "the `service` role claims it can carry a binding")

    # resolve_binding: a valid `on`, an invalid one, and the default
    for bp in wl_blueprint.PATTERNS:
        role, err = wl_place.resolve_binding(bp["id"])
        check(role == CARRIER[bp["id"]] and err is None,
              f"resolve_binding({bp['id']}) = ({role!r}, {err!r})")
        for b in bp["beats"]:
            role, err = wl_place.resolve_binding(bp["id"], b["role"])
            if wl_place.can_carry(b["op"]):
                check(role == b["role"] and err is None,
                      f"resolve_binding({bp['id']}, {b['role']!r}) refused a carrying role: {err}")
            else:
                check(role is None and err,
                      f"resolve_binding({bp['id']}, {b['role']!r}) accepted an op-${b['op']:X} beat "
                      f"with no hand-over field")
                check(CARRIER[bp["id"]] in (err or ""),
                      f"resolve_binding({bp['id']}, {b['role']!r}) refuses without naming the beat "
                      f"that CAN carry ({CARRIER[bp['id']]!r}): {err}")
        # a role this blueprint does not have, and one nothing has
        for junk in ("door", "loot", "not-a-role", ""):
            if junk in [b["role"] for b in bp["beats"]] or not junk:
                continue
            role, err = wl_place.resolve_binding(bp["id"], junk)
            check(role is None and err, f"resolve_binding({bp['id']}, {junk!r}) accepted it")

    # …and it never raises, whatever it is handed: the caller collects refusals, it does not catch
    for pattern in ("no-such-blueprint", "svc-shop", None, ""):
        try:
            role, err = wl_place.resolve_binding(pattern, "loot")
            check(role is None and err, f"resolve_binding({pattern!r}) answered {role!r}")
        except Exception as ex:                                     # noqa: BLE001
            FAIL.append(f"resolve_binding({pattern!r}) raised {ex!r} instead of reporting")

    tbl = {r["role"] for r in wl_place.carrier_table()}
    check(tbl == set(wl_place.CARRIERS), "carrier_table does not cover every role")
    print(f"(g) carriers: {len(CARRIER)} patterns, every default is the last beat that CAN carry — "
          f"door-to-service binds at the door, not at the shop")


# ── (h) wl_scene_registry ─────────────────────────────────────────────────────────────────────
def h_scenes():
    """A routine without an entry is not callable — the only thing standing between an unknown
    name and a mystery byte in a record."""
    errs = wl_scene_registry.check_call("trex-teleport", {"area": 51})
    check(errs, "a routine with no registry entry was accepted — §2.6 says it is not callable")
    check(any("scenes.json" in e for e in errs),
          f"the refusal does not say where to declare it: {errs}")
    check(wl_scene_registry.get("trex-teleport") is None, "get() invented an entry")

    check(not wl_scene_registry.check_call("trex-talk",
                                           {"pages": [0x40, 0x41], "label": "The TREX",
                                            "portrait": 8}),
          f"a declared call was refused: "
          f"{wl_scene_registry.check_call('trex-talk', {'pages': [0x40], 'label': 'x', 'portrait': 8})}")
    # a declared routine still has to be called correctly, or the entry buys nothing
    check(len(wl_scene_registry.check_call("trex-talk", {})) == 3,
          "a call with no parameters at all was not fully reported")
    bad = wl_scene_registry.check_call("trex-talk", {"pages": [200], "label": "The TREX",
                                                    "portrait": 8})
    check(bad and any("7 bits" in e for e in bad),
          f"a page id above the 7-bit text ceiling passed: {bad}")
    check(wl_scene_registry.check_call("trex-talk", {"pages": [1], "label": "x", "portrait": 8,
                                                    "speed": 3}),
          "a parameter the routine does not take was accepted")
    check(not wl_scene_registry.problems(),
          f"the registry itself does not hold up: {wl_scene_registry.problems()[:3]}")
    print(f"(h) scenes: {len(wl_scene_registry.names())} routines declared, the file audits clean; "
          f"an undeclared routine is refused and told where to declare itself")


# ── (j) the PLACE half of the battery ─────────────────────────────────────────────────────────
# Spec §8 has two columns under *Wo*: `validate` (case (f) above) and `place`. The place rows are
# the ones that need an area, and until now not one of them had a case — the battery tested their
# validate-stage twins (`binding-carrier`, `bind-without-quest`), which is different code under a
# different name. So this runs the REAL `wl_place.place()` against the REAL baseline blobs with
# `commit=False`, and the host records every save instead of performing one. That buys the second
# assertion as well: a refused placement must have saved NOTHING (the all-or-nothing rule, §4.1).
#
# The areas are picked for what they are:
#   s1a29   64 strings, 4 foes, combat block intact — the one where a placement must simply work
#   s2a5    0 foes and no 34-string block (its `desc+$37` points into a 48-string pool) — one of
#           the three shipped areas that cannot host a fight
#   s1a10   152 strings, i.e. past the 7-bit text id ceiling — nothing new fits
#   s3a12   exactly 9728 bytes, the whole $3400..$5A00 window, with text ids still free
#
# One row of the table has no case here and cannot have one honestly: `round-trip` re-reads every
# record it just wrote through the engine's own pointer formula, so forcing it means writing a blob
# that is already broken — the failure it guards against is a bug in the emitter, and a test that
# fakes it would only prove the fake.
BASELINE = REPO / "baseline"


class _PlaceProbe:
    """Everything `wl_place` asks a host for, answered from the baseline blobs and from memory."""

    def __init__(self, doc=None):
        self.saved, self.doc = [], copy.deepcopy(doc or {})

    def record(self, side, area):
        return (BASELINE / f"side{side}" / f"s{side}_record_id{area:02d}.bin").read_bytes()

    def ir(self, side, area):
        import wl_ir
        return wl_ir.record_to_ir(self.record(side, area), side, area)

    def save_record(self, side, area, data):
        self.saved.append(f"record s{side}a{area} {len(data)}B")
        self.blob = bytes(data)                 # kept so a test can read back what was written
        return "(memory)"

    def quests(self):
        return copy.deepcopy(self.doc)

    def save_quests(self, doc):
        self.saved.append("quests")
        self.doc = doc

    def items(self):
        return wl_item.table(REPO)


class _RefusingHost(_PlaceProbe):
    """A host whose serializer says no — the one refusal that can only come from the write side."""

    def save_record(self, side, area, data):
        raise ValueError("this record does not lay out: probe")


def _hole(key, kind):
    return {"key": key, "kind": kind, "label": key, "hint": ""}


SIGN = {"say": "A rusted sign."}
STASH = {"foe": {"id": 1}, "announce": "Something moves.", "items": [{"item": 44, "qty": 1}]}
CACHE = {"check": {"op": 1, "arg": 70}, "prompt": "A loose board.", "found": "A cache!",
         "items": [{"item": 44, "qty": 1}], "after": "We take it."}
# a marker on item 70, which the shipped table creates with FIVE uses
TOKEN_DOC = {"q": {"name": "q", "qid": 3, "steps": [],
                   "markers": [{"id": "TOY", "kind": "item", "item": 70}]}}
QUEST_DOC = {"garoof": {"name": "g", "qid": 17, "axis": [{"id": "s1"}], "steps": []}}
# two shapes wl_blueprint does not offer, precisely because they are the ones that must be refused
ZERO_EDGE = {"id": "probe-zero-edge", "name": "an encounter handing over to an encounter",
             "beats": [{"role": "fight", "op": 0x3, "link": "advance"},
                       {"role": "fight2", "op": 0x3}],
             "holes": [_hole("foe", "foe"), _hole("announce", "text"),
                       _hole("foe2", "foe"), _hole("announce2", "text")]}
NO_CARRIER = {"id": "probe-portrait", "name": "a picture on something that cannot show one",
              "beats": [{"role": "say", "op": 0x1}],
              "holes": [_hole("say", "text"), _hole("portrait", "portrait")]}

#  name                   expected refusals      side area blueprint      values         host
PLACE_CASES = [
    ("clean sign",         set(),                 1, 29, "a-sign",        SIGN,          None),
    ("clean fight",        set(),                 1, 29, "guarded-stash", STASH,         None),
    ("cell off the map",   {"cell"},              1, 29, "a-sign",        SIGN,          None),
    ("hole still empty",   {"values-missing"},    1, 29, "a-sign",        {},            None),
    ("no such foe",        {"combat-kit"},        1, 29, "guarded-stash",
     {**STASH, "foe": {"id": 99}}, None),
    ("area cannot fight",  {"combat-kit"},        2, 5, "guarded-stash",
     {**STASH, "foe": {"name": "Thing", "portrait": 0, "glyph": 2}}, None),
    ("no text id left",    {"text-budget"},       1, 10, "a-sign",        SIGN,          None),
    ("a service alone",    {"blueprint"},         1, 29, "svc-shop",      {},            None),
    ("edge the game never makes", {"edge-census"}, 1, 29, ZERO_EDGE,
     {"foe": {"id": 1}, "announce": "a", "foe2": {"id": 2}, "announce2": "b"}, None),
    ("picture, no carrier", {"carrier"},          1, 29, NO_CARRIER,
     {"say": "hi", "portrait": 8}, None),
    ("marker with a count", {"item-spend"},       1, 29, "hidden-cache",  CACHE,         TOKEN_DOC),
    # s3a12 is one of the four areas that sit EXACTLY on the $3400..$5A00 window (9728 B) and still
    # has text ids free, so it is the one place where the cap refuses and the text budget does not
    ("area is full",       {"record-cap"},        3, 12, "a-sign",        SIGN,          None),
    # (1,2) of s1a29 is the ONLY way to grp2[6]: no other square dispatches it, no chain reaches it,
    # no patch puts it back. Placing there would take the square and orphan the record — silently,
    # which is the part worth refusing over. A square that several beats SHARE over time is fine;
    # WL1 does it 1881 times through op-C patches, and those are reachable, so they do not trip this.
    ("square is the only way", {"cell-orphans"},   1, 29, "a-sign",        SIGN,          None),
]

# Where each case places. Most sit on (5,5), which is empty in every area used above.
PLACE_CELL = {"cell off the map": (99, 99), "square is the only way": (1, 2)}


def j_place(verbose):
    for name, expect, side, area, bp, values, doc in PLACE_CASES:
        host = _PlaceProbe(doc)
        pc, pr = PLACE_CELL.get(name, (5, 5))
        res = wl_place.place(host, side, area, bp, values, pc, pr, commit=False)
        got = {e["check"] for e in res.get("refused") or []}
        check(got == expect,
              f"place '{name}': refused {sorted(got) or '-'}, expected {sorted(expect) or '-'}")
        for e in res.get("refused") or []:
            check(bool(e.get("detail")), f"place '{name}': {e['check']} has no sentence")
        check(not host.saved,
              f"place '{name}': all-or-nothing broken — it saved {host.saved} anyway")
        if verbose:
            print(f"    {name:<24} {sorted(got) or '-'}")
            for e in res.get("refused") or []:
                print(f"        [{e['check']}] {e['detail'][:100]}")

    # the same gate on a consumable that is NOT a quest marker is a warning, not a refusal: taking
    # the Rope off you is a thing the shipped game does on purpose (Konzept §7)
    plain = wl_place.place(_PlaceProbe(), 1, 29, "hidden-cache", CACHE, 5, 5, commit=False)
    check("refused" not in plain and any("takes one of its" in w for w in plain["warnings"]),
          f"a gate on a plain consumable was not warned about: {plain.get('warnings')}")
    zero = wl_place.place(_PlaceProbe(TOKEN_DOC), 1, 29, "hidden-cache",
                          {**CACHE, "check": {"op": 1, "arg": 72}}, 5, 5, commit=False)
    check("refused" not in zero and not zero["warnings"],
          f"an item created at count 0 was complained about: {zero}")

    # the quest half: who may carry a binding, whether the quest exists, whether it has an address
    # — and what happens to a binding whose bytes the engine cannot read YET
    carrier = wl_place.place(_PlaceProbe(QUEST_DOC), 1, 29, "door-to-service",
                             {"service_kind": {"selector": 9}, "name": "TREX", "portrait": 8,
                              "pages": ["hi"]}, 5, 5,
                             quest={"slug": "garoof", "effect": "advance", "to": "s1",
                                    "on": "service"}, commit=False)
    check([e["check"] for e in carrier.get("refused") or []] == ["bind-carrier"],
          f"binding to the shop itself was accepted: {carrier}")
    for slug, want in (("nope", "bind-quest"), ("q", "bind-qid")):
        host = _PlaceProbe({"q": {"name": "q", "steps": []}})            # a quest with no qid
        r = wl_place.place(host, 1, 29, "a-sign", SIGN, 5, 5,
                           quest={"slug": slug, "effect": "advance", "to": "s1"}, commit=False)
        check([e["check"] for e in r.get("refused") or []] == [want],
              f"a binding to {slug!r} raised {r.get('refused')}, expected {want}")

    # A binding is AUTHORED even though its bytes cannot be emitted yet. This used to raise 501 and
    # throw the whole placement away — four fields lost because the fifth had no contract. The
    # contract exists now (QUESTLOG_SPEC §5), so the beat is placed, the binding is stored beside it,
    # and the one missing half says so. The binding names a QUEST, not an address, so nothing
    # authored has to change when the engine side lands.
    host = _PlaceProbe(QUEST_DOC)
    bound = wl_place.place(host, 1, 29, "a-sign", SIGN, 5, 5,
                           quest={"slug": "garoof", "effect": "advance", "to": "s1"}, commit=True)
    check(not (bound.get("refused") or []), f"a bound beat was refused: {bound.get('refused')}")
    check(bound.get("records"), "a bound beat placed no records")
    check(any("not compiled" in w.lower() for w in bound.get("warnings") or []),
          f"a bound beat compiled silently: {bound.get('warnings')}")
    check(host.saved, "a bound beat wrote nothing at all")
    step = ((host.doc.get("garoof") or {}).get("steps") or [{}])[-1]
    check((step.get("bind") or {}).get("effect") == "advance",
          f"the binding was not stored on the step: {step}")

    # …and the same beat WITHOUT an effect is a normal placement that notes itself in the quest
    host = _PlaceProbe(QUEST_DOC)
    ok = wl_place.place(host, 1, 29, "a-sign", SIGN, 5, 5,
                        quest={"slug": "garoof", "title": "the sign"}, commit=True)
    check("refused" not in ok and host.saved and ok["quest"] == {"slug": "garoof", "step": 0},
          f"a questfree-effect placement did not go through: {ok}")
    step = host.doc["garoof"]["steps"][0]
    check(step.get("phase") == "placed" and step.get("records") and "bind" not in step,
          f"the quest note is not the §2.4 form C shape: {step}")

    # a placement that carries a GATE keeps it. The note used to be rebuilt from scratch, which
    # dropped `gates`, `trigger` and `id` — and with them every validate check that reads them.
    host = _PlaceProbe({"garoof": {"name": "g", "qid": 17, "axis": [{"id": "s1"}], "steps": [
        {"kind": "beat", "phase": "cast", "id": "ask", "trigger": "talk", "blueprint": "a-sign",
         "gates": [{"cond": "quest", "quest": 17, "state": "s1"}], "note": "1988"}]}})
    wl_place.place(host, 1, 29, "a-sign", SIGN, 5, 5,
                   quest={"slug": "garoof", "step": 0, "title": "the sign"}, commit=True)
    kept = host.doc["garoof"]["steps"][0]
    check(kept.get("gates") and kept.get("id") == "ask" and kept.get("trigger") == "talk",
          f"placing a beat threw away what the designer typed on it: {sorted(kept)}")

    # the write side: the serializer is the last thing that can refuse, and when it does the map
    # and the tileset must still be untouched — here, nothing further is saved at all
    refuser = _RefusingHost()
    r = wl_place.place(refuser, 1, 29, "a-sign", SIGN, 5, 5, commit=True)
    check([e["check"] for e in r.get("refused") or []] == ["record-save"],
          f"a serializer refusal came back as {r.get('refused')}")

    print(f"(j) place: {len(PLACE_CASES)} area cases + the quest half — every refusal saves "
          f"nothing, a gate survives its placement, and a binding is stored with a warning "
          f"instead of throwing the placement away")


# ── (i) nothing was written ───────────────────────────────────────────────────────────────────
def i_readonly(before, before_dir):
    after = _fingerprint(QUESTS)
    check(after == before,
          f"THE BATTERY WROTE quests.json — {before[1]} bytes became {after[1]}. Restore it from "
          f"git before anything else.")
    now = sorted(p.name for p in ANNOTATIONS.iterdir())
    check(now == before_dir,
          f"the battery left files behind in annotations/: {sorted(set(now) - set(before_dir))}")
    print(f"(i) read-only: quests.json unchanged ({before[1]} bytes, sha {before[0][:12]}), "
          f"no .tmp and no .bak_v1 left behind")


def k_store(verbose):
    """The questlog STORE: which byte, which bit, and what must never move.

    The axis is authored; the allocation is not. It is written down because a save refers to a bit
    by POSITION and flash only ever writes 1->0 — so the tests that matter are about stability, not
    about arithmetic: an allocated range stays put, a grown axis moves to fresh bytes instead of
    stretching over its neighbour, and a deleted quest's bytes are never handed to the next one.
    """
    def Q(qid, n, **kw):
        return {"qid": qid, "name": f"q{qid}",
                "axis": [{"id": f"s{i}", "log": f"line {i}"} for i in range(1, n + 1)], **kw}

    # bit i holds axis[i+1]; `open` is position 0 and has NO bit
    d = {"_version": 2, "a": Q(1, 2)}
    wl_questlog.log_alloc(d, 1, wl_questlog.bytes_for(d["a"]))
    st = wl_questlog.log_layout(d)["quests"][0]["states"]
    check(st[0]["id"] == "open" and st[0]["bit"] is None, "store: `open` was given a bit")
    check([x["bit"] for x in st[1:]] == [0, 1], f"store: bits are {[x['bit'] for x in st[1:]]}")
    check(wl_questlog.bytes_for(d["a"]) == 1, "store: 2 states should cost 1 byte")
    check(wl_questlog.bytes_for(Q(2, 8)) == 1 and wl_questlog.bytes_for(Q(2, 9)) == 2,
          "store: the byte boundary is not at 8 states")

    # a second quest gets its own bytes, and the first one does not move
    d["b"] = Q(2, 9)
    wl_questlog.log_alloc(d, 2, wl_questlog.bytes_for(d["b"]))
    lay = {r["qid"]: r for r in wl_questlog.log_layout(d)["quests"]}
    check(lay[1]["byte"] == 0 and lay[1]["len"] == 1, "store: qid 1 moved")
    check(lay[2]["byte"] == 1 and lay[2]["len"] == 2, f"store: qid 2 landed at {lay[2]['byte']}")
    check(wl_questlog.log_layout(d)["bytes"] == 3, "store: total size wrong")

    # an axis that outgrows its range is REALLOCATED, and the old bytes are retired, not stretched
    d["a"] = Q(1, 12)
    got = wl_questlog.log_alloc(d, 1, wl_questlog.bytes_for(d["a"]))
    b = wl_questlog.log_block(d)
    check(got["byte"] == 3 and got["len"] == 2, f"store: grown quest went to {got}")
    check({"byte": 0, "len": 1, "qid": 1} in b["retired"], "store: the old range was not retired")
    check(not wl_questlog.log_problems(d), f"store: {wl_questlog.log_problems(d)}")

    # a deleted quest's bytes are never handed out again
    wl_questlog.log_free(d, 2)
    d.pop("b")
    d["c"] = Q(3, 3)
    got = wl_questlog.log_alloc(d, 3, wl_questlog.bytes_for(d["c"]))
    check(got["byte"] == 5, f"store: a new quest took retired bytes — landed at {got['byte']}")

    # the problems it must report
    bad = {"_version": 2, "x": Q(7, 9), "_log": {"version": 1, "bytes": 1,
                                                 "alloc": {"7": {"byte": 0, "len": 1}}}}
    kinds = {p["check"] for p in wl_questlog.log_problems(bad)}
    check("log-outgrown" in kinds, f"store: an outgrown range was not reported ({kinds})")
    noalloc = {"_version": 2, "x": Q(7, 2)}
    check("log-unallocated" in {p["check"] for p in wl_questlog.log_problems(noalloc)},
          "store: a quest with states and no bytes was not reported")
    nolog = {"_version": 2, "x": {"qid": 7, "name": "x", "axis": [{"id": "s1"}]}}
    wl_questlog.log_alloc(nolog, 7, 1)
    check("log-gap" in {p["check"] for p in wl_questlog.log_problems(nolog)},
          "store: a state without a log line was not reported")

    # the shipped file declares no axis at all, so it costs nothing and reports nothing
    shipped = wl_questlog.load(QUESTS)
    lays = wl_questlog.log_layout(shipped)
    check(lays["bytes"] == 0 and lays["lines"] == 0,
          f"store: the shipped file suddenly costs {lays['bytes']} byte(s)")
    check(not wl_questlog.log_problems(shipped),
          f"store: the shipped file reports {wl_questlog.log_problems(shipped)}")

    print(f"(k) store: `open` costs no bit, the byte boundary is at 8 states, a grown axis moves to "
          f"fresh bytes and retires the old, a deleted quest's bytes are never reused; the shipped "
          f"file declares no axis and so costs 0 bytes")


def l_service_door(verbose):
    """A placed service door must have the shape all 34 shipped ones have.

    Measured over the whole game: 34 op-A records whose tail hands to an op-6, and all 34 carry
    `b0 & $C0 == $C0` with `x = y = 0` and `target` = their own area. Bit 7 is party-relative
    (SERVICE_HANDLERS.md, handler $89A3), so X/Y are DELTAS — 0,0 means "stay put", which is what
    opening a shop does.

    Without it the door takes the pattern's defaults (x=1, y=1, target=0) as ABSOLUTE and walks the
    party into area 0. Nothing refused, nothing looked wrong; the shop simply was not there.
    """
    import wl_ir
    host = _PlaceProbe()
    res = wl_place.place(host, 1, 29, "door-to-service",
                         {"service_kind": {"selector": 1}, "name": "Test shop",
                          "portrait": 1, "greeting": 1},
                         6, 6, commit=True)
    check(not (res.get("refused") or []), f"door: refused {res.get('refused')}")
    check(getattr(host, "blob", None) is not None, "door: nothing was written")
    if not getattr(host, "blob", None):
        return

    ir = wl_ir.record_to_ir(host.blob, 1, 29)
    doors = [rr for recs in [ir["groups"].get("10") or {}] for rr in recs.values()
             if isinstance(rr.get("next"), dict) and rr["next"].get("op") == 6]
    check(len(doors) == 1, f"door: found {len(doors)} service doors, expected 1")
    if not doors:
        return
    d = doors[0]
    check(d["b0"] & 0xC0 == 0xC0,
          f"door: b0 is ${d['b0']:02X} — bit 7 (party-relative) is what makes x/y deltas")
    check(d["x"] == 0 and d["y"] == 0, f"door: x/y are {d['x']}/{d['y']}, every shipped one is 0/0")
    check(d["target"] == 29, f"door: target is {d['target']}, every shipped one is its own area")
    check(wl_ir._emit_adv_tail(d["next"]) == bytes([6, d["next"]["id"]]),
          "door: the tail is not the 2-byte [06][id] every shipped door has")

    print(f"(l) service door: b0 ${d['b0']:02X} (bit7 party-relative + bit6 confirm), x=y=0, "
          f"target = its own area, 2-byte tail — the shape all 34 shipped doors have")


def main():
    verbose = "-v" in sys.argv
    before = _fingerprint(QUESTS)
    before_dir = sorted(p.name for p in ANNOTATIONS.iterdir())

    src = wl_questlog.load(QUESTS)                       # the in-memory copy; the file is never touched
    once = a_lossless(src)
    b_idempotent(src, once)
    c_qids(once)
    d_axis()
    e_markers()
    f_validate(once, verbose)
    g_carriers()
    h_scenes()
    j_place(verbose)
    k_store(verbose)
    l_service_door(verbose)
    i_readonly(before, before_dir)

    if FAIL:
        print(f"\nFAILED ({len(FAIL)}):")
        for f in FAIL:
            print("  -", f)
        return 1
    print("\nthe handwriting survives the migration, a qid is never handed out twice, "
          "questfreedom is never a complaint, and the shop is not what carries the binding")
    return 0


if __name__ == "__main__":
    sys.exit(main())
