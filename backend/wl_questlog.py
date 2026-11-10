"""wl_questlog.py — the quest DATA MODEL: version 2 of `quests.json`, and the way there from v1.

`quests.json` started as a reading aid. Thirty-four quests, each one a name, a note and an ordered
list of REFERENCES into records the 1988 authors had already written — annotation, pointing at a
game that was finished. Nothing in it could be wrong, because nothing in it was load-bearing.

The designer changes that. A quest now has to say things the file has no room for:

  qid      the questlog address. The engine will store progress per quest, and a save from a cart
           in somebody's drawer refers to the quest by NUMBER — so the number is permanent, it is
           allocated once, and it is NEVER reused when a quest is deleted. That is why deletion
           pushes the id onto `_retired_qids` instead of just dropping it.
  axis     the states this quest can be in, in the order they are reached. `open` is not in the
           list — every quest is open before anything happens, it carries no log line, and writing
           it down would only invite somebody to give it a bit. The bits are for the rest.
  markers  the item / word / cell a quest hands out and later tests. A marker is a DECLARATION, so
           the id behind it can be allocated once and checked for collisions, instead of a designer
           picking item 75 twice and finding out in the runtime.
  kind     main line or side quest, and `home` where it starts.

And one rename that had to happen before anything else: `state` used to mean the AUTHOR's progress
on a step (`sketch` → `cast`), while the notation uses `state` for the quest's own stage. Two
meanings, one word, one file. The quest stage wins; the step's field is now `phase` (spec E1).

## The three step shapes

    {"kind": "ref",  "ref": "s1a9:grp1[21]", "note": "…"}      the 1988 game, annotated
    {"kind": "beat", "phase": "sketch"|"cast", …}              designed, not yet bytes
    {"kind": "beat", "phase": "placed", "at": …, "records": …} designed, bytes exist

`kind` is written down rather than sniffed at every read, because the two shapes are about to stop
looking alike: a ref is read-only (it describes something that already exists), a beat is the thing
the designer owns.

## What this module is careful about, and why

*The 34 quests are handwriting.* Nobody can retype those notes. So `save()` copies the file to
`<path>.bak_v1` before it first writes over a v1 document, and `migrate()` only ever ADDS fields —
`track`, `links`, `conds` and every unknown key survive untouched. `wl_quest.compose()` passes
unknown step keys through as `extra`, and that only stays true if nothing here throws them away.

*Underscore or it evaporates.* `app._quests_write()` keeps exactly two classes of top-level key:
those a quest owns (dict values) and those starting with `_`. A schema marker written as
`"version": 2` would survive until the next step edit and then silently un-migrate the file. Hence
`_version` and `_retired_qids`.

*Whole-file writes are visible.* The document is 60 KB written `indent=1, sort_keys=True`; changing
those options would bury the real change under a 60 KB diff. They are not options here.

Pure functions. No FastAPI, no `app` import — app imports this, never the other way round.
"""
from __future__ import annotations

import json
import math
import os
import pathlib
import re
import shutil
import tempfile
import threading

# One writer at a time, process-wide. `save()` reads the file to keep its `_`-keys and writes it
# back, and that pair has to be indivisible — see the docstring there for what two tabs did to it.
_WRITE_LOCK = threading.RLock()

VERSION = 2                     # what `save()` writes, and what `migrate()` produces
QID_MAX = 255                   # the questlog address is one byte
QUEST_KINDS = ("main", "side")
STEP_KINDS = ("ref", "beat")
PHASES = ("sketch", "cast", "placed")

# The three states no axis may name, because the model already means something by them. `open` is
# the implicit first state of every quest; `done` is the last forward one; `failed` is a dead end
# beside the line, not a point on it.
RESERVED_STATES = ("open", "done", "failed")

MARKER_KINDS = ("item", "word", "cell")

# A marker word is a keyword the player types, so it lives under the same rules as every other
# keyword in the game: capitals, and long enough not to collide with the yes/no and menu letters
# (`wl_quest.MIN_WORD`, and the reason is written up there).
_WORD_OK = re.compile(r"^[A-Z][A-Z0-9]{2,15}$")


# ── the document ──────────────────────────────────────────────────────────────────────────────

def load(path):
    """Read `quests.json`. Accepts v1 and v2 alike and returns the document unchanged.

    Deliberately NOT migrating: reading is not editing. A caller that only wants to look at the
    claims of the Events rail should not rewrite anybody's file, and a migration that happens on
    read would fire once per process with nothing to compare against. `migrate()` is a separate,
    explicit step, and `save()` runs it.

    Raises OSError / ValueError the way `json.load` does — `app._quests_authored()` already turns
    both into the one 500 in that file.
    """
    p = pathlib.Path(path)
    doc = json.loads(p.read_text())
    if not isinstance(doc, dict):
        raise ValueError("quests.json must hold an object at the top level")
    return doc


def version(doc) -> int:
    """1 for anything that does not say otherwise — v1 is simply the file before the marker."""
    v = (doc or {}).get("_version")
    return v if isinstance(v, int) and v > 0 else 1


def quests(doc) -> dict:
    """{slug: quest} — the same filter `app._quests_authored()` applies, without the HTTP part.

    Underscore keys are the document's own bookkeeping and non-dict values are not quests; both are
    invisible to every consumer, so they are invisible here too.
    """
    return {k: v for k, v in (doc or {}).items()
            if isinstance(k, str) and not k.startswith("_") and isinstance(v, dict)}


def save(path, doc, backup=True):
    """Write v2, atomically, and never over the only copy of the handwriting.

    Three things happen in a fixed order and the order is the point:

      1. the file on disk is read, so its `_`-keys (`_README`, `_retired_qids`) survive a caller
         that only knows about quests — the same rule `app._quests_write()` follows;
      2. if what is on disk is still v1, it is copied to `<path>.bak_v1` (once — an existing backup
         is never overwritten, because the second write would back up the already-migrated file);
      3. the merged document is migrated and stamped, so a v1 document can never leave here.

    tmp + `os.replace` rather than a plain write: a process that dies mid-write leaves a truncated
    `quests.json`, and every reader of that file answers 500 afterwards — the Events rail and the
    Flow tab of every area included.

    The temp file gets a UNIQUE name and the whole read-merge-write is held under a lock, and both
    halves were paid for. FastAPI runs these routes in a threadpool, so two tabs saving at once are
    genuinely parallel; with one fixed `quests.json.tmp` they wrote into the SAME file, and after
    the first `os.replace` the loser's open handle pointed at the published document and kept
    writing into it. Measured on a copy: ~53% of concurrent saves raised, readers saw an
    unparseable quests.json — and worse, a `save()` that read the file mid-flight caught its own
    `except (OSError, ValueError)`, took `old = {}` and published a document with `_README` and
    `_retired_qids` GONE. A dropped `_retired_qids` hands a spent questlog address to the next
    quest, which is the one thing this module says must never happen.
    """
    with _WRITE_LOCK:
        return _save(path, doc, backup)


def _save(path, doc, backup):
    p = pathlib.Path(path)
    try:
        old = load(p)
    except (OSError, ValueError):
        old = {}
    if backup and old and version(old) < VERSION:
        bak = p.with_name(p.name + ".bak_v1")
        if not bak.exists():
            shutil.copy2(p, bak)
    keep = {k: v for k, v in old.items() if isinstance(k, str) and k.startswith("_")}
    out = migrate({**keep, **(doc or {})})
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(out, indent=1, sort_keys=True))
            fh.flush()
            os.fsync(fh.fileno())                  # the rename is atomic; the CONTENT has to be there
        os.replace(tmp, p)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)  # never leave a half-written sibling behind
        raise
    return out


# ── migration ─────────────────────────────────────────────────────────────────────────────────

def _step_kind(st):
    """Which of the three shapes a v1 step is — by what it CARRIES, not by what it is called.

    Returns None for a step that is neither, and that is on purpose: a dict with nothing but a note
    is not a reference and not a beat, and inventing a kind for it would be the first lossy guess.
    """
    if isinstance(st, str):
        return "ref"
    if not isinstance(st, dict):
        return None
    if st.get("kind") in STEP_KINDS:
        return st["kind"]
    if st.get("ref"):
        return "ref"
    if st.get("blueprint") or st.get("title") or st.get("values") or st.get("records"):
        return "beat"
    return None


def _migrate_step(st):
    if isinstance(st, str):
        # `wl_quest.claims()` and `compose()` both accept a bare string ref. Nothing in the file is
        # one today, but the shape is legal, and a v2 step is a dict.
        return {"kind": "ref", "ref": st}
    if not isinstance(st, dict):
        return st
    out = dict(st)
    kind = _step_kind(out)
    if kind:
        out["kind"] = kind
    if kind == "beat":
        # E1: the author's progress is `phase`; `state` now belongs to the quest axis.
        if "state" in out and "phase" not in out:
            out["phase"] = out.pop("state")
        elif "state" in out:
            out.pop("state")
        if not out.get("phase"):
            # Structural only — what the step HAS says which of the three it is. The real recompute
            # lives where the values are checked (`wl_blueprint.missing()`), and that needs the
            # catalogue; this just refuses to leave a beat without the field its shape requires.
            out["phase"] = ("placed" if (out.get("records") or out.get("at"))
                            else "cast" if out.get("blueprint") else "sketch")
    return out


def _quest_kind(q):
    """main or side — or None when the file does not say.

    Spec §3.2 originally derived this from `order`: set → main, unset → side. Measured against the
    real file that rule is not merely wrong for a few quests, its premise is false: ALL 34 carry an
    `order` (it is a rank across BOTH tracks — the side quests hold 8, 9, 14, 22 …), so `order`
    carries no main/side information at all and the rule would have stamped `kind: "main"` into the
    fifteen quests that never said which they are. `track` is what a human typed, on 19 of them
    ("main" 10 / "side" 9), and it is the same distinction under the older name.

    So: the human's word decides, and where there is none this returns None and the migration
    leaves the field OUT. An absent `kind` reads as "nobody has said yet" — `_compose_quests` shows
    `track` and falls back to `side`, exactly as every view already did — while a written-down
    "main" would be a fact this editor invented and then stored in the handwriting for good.
    `track` is left in place: `wl_quest.compose()` reads it, and this migration removes nothing.
    """
    t = q.get("track")
    if isinstance(t, str) and t.lower() in QUEST_KINDS:
        return t.lower()
    return None


def _qid_order(item):
    """The order qids are handed out in: by `order`, then by slug. Stable, so a re-run of the
    migration on a document that lost its marker allocates the same numbers again."""
    slug, q = item
    o = q.get("order")
    return (o if isinstance(o, int) else 10 ** 6, slug)


def migrate(doc) -> dict:
    """v1 → v2, lossless and idempotent. Adds fields, renames one, removes nothing.

    Idempotent matters more than it sounds: `save()` runs this on every write, so a migration that
    drifted by one field per pass would rewrite the whole 60 KB file forever.
    """
    doc = doc or {}
    out = {k: v for k, v in doc.items() if isinstance(k, str) and k.startswith("_")}
    out["_version"] = VERSION
    retired = _retired(doc)
    if retired:
        out["_retired_qids"] = retired
    elif "_retired_qids" in out:
        out.pop("_retired_qids")

    qs = quests(doc)
    used = set(retired)
    for q in qs.values():
        qid = q.get("qid")
        if isinstance(qid, int) and 0 <= qid <= QID_MAX:
            used.add(qid)

    nxt = 0
    for slug, q in sorted(qs.items(), key=_qid_order):
        n = dict(q)
        if not (isinstance(n.get("qid"), int) and 0 <= n["qid"] <= QID_MAX):
            while nxt in used:
                nxt += 1
            if nxt > QID_MAX:
                raise ValueError("all 256 questlog ids are spoken for — no id left to give "
                                 f"'{slug}'; retiring ids does not free them (see _retired_qids)")
            n["qid"] = nxt
            used.add(nxt)
        n.setdefault("axis", [])
        if not n.get("kind"):
            k = _quest_kind(n)
            if k:                                  # unset stays unset — see _quest_kind
                n["kind"] = k
        # `home` is NOT guessed. A quest's first area is derivable from its first step, but a step
        # can be annotated in any order and a wrong home reads as an authored fact.
        n["steps"] = [_migrate_step(s) for s in (n.get("steps") or [])]
        out[slug] = n

    # Everything else the file holds that is neither a quest nor bookkeeping — kept verbatim rather
    # than dropped, even though the writers upstream would drop it. Losing data is not a migration.
    for k, v in doc.items():
        if k not in out:
            out[k] = v
    return out


# ── qids ──────────────────────────────────────────────────────────────────────────────────────

def _retired(doc) -> list:
    v = (doc or {}).get("_retired_qids")
    if not isinstance(v, list):
        return []
    return sorted({x for x in v if isinstance(x, int) and 0 <= x <= QID_MAX})


def used_qids(doc) -> set:
    """Every id ever handed out — the live quests plus the retired ones.

    Both halves count. A cart in the field still carries the retired quest's bit in its save, so
    handing that number to a new quest would make an old save read as progress in a story that
    never happened.
    """
    out = set(_retired(doc))
    for q in quests(doc).values():
        qid = q.get("qid")
        if isinstance(qid, int) and 0 <= qid <= QID_MAX:
            out.add(qid)
    return out


def alloc_qid(doc) -> int:
    """The smallest free questlog address, 0..255."""
    used = used_qids(doc)
    for i in range(QID_MAX + 1):
        if i not in used:
            return i
    raise ValueError("all 256 questlog ids are spoken for; retired ids are never reused, so a new "
                     "quest needs one released by hand from _retired_qids")


def retire_qid(doc, qid) -> dict:
    """Record a deleted quest's id as spent. The delete path's other half — dropping the quest
    without this is what makes the next `alloc_qid()` hand the number straight back out."""
    if not (isinstance(qid, int) and 0 <= qid <= QID_MAX):
        raise ValueError(f"{qid!r} is not a questlog id")
    doc = doc or {}
    doc["_retired_qids"] = sorted(set(_retired(doc)) | {qid})
    return doc


def by_qid(doc) -> dict:
    """{qid: slug} — the direction the IR needs. `{"effect": "advance", "quest": 17}` carries the
    NUMBER, because the slug is author comfort and is allowed to change under a running cart."""
    return {q["qid"]: slug for slug, q in quests(doc).items()
            if isinstance(q.get("qid"), int)}


# ── the axis ──────────────────────────────────────────────────────────────────────────────────

def is_reserved(state_id) -> bool:
    """`open`, `done`, `failed` — the three the model already means something by."""
    return str(state_id or "").strip().lower() in RESERVED_STATES


def axis_ids(q) -> list:
    """The written-down states, in progress order.

    `open` is filtered out even if somebody typed it: it is position 0 by definition, it carries no
    log line, and letting it into the list would give it a storage bit it must not have.
    """
    out = []
    for s in (q or {}).get("axis") or []:
        sid = s.get("id") if isinstance(s, dict) else s
        sid = str(sid or "").strip()
        if not sid or sid.lower() == "open":
            continue
        out.append(sid)
    return out


def axis_states(q) -> list:
    """The full state list including the implicit `open` at position 0.

    Each entry is `{id, log, reserved}`; `open` additionally carries `implicit`. This is what the
    logbook renders and what `axis_index()` counts in.
    """
    src = {}
    for s in (q or {}).get("axis") or []:
        if isinstance(s, dict) and s.get("id"):
            src[str(s["id"]).strip()] = s
    out = [{"id": "open", "log": None, "reserved": True, "implicit": True}]
    for sid in axis_ids(q):
        s = src.get(sid) or {}
        out.append({"id": sid, "log": s.get("log"), "reserved": is_reserved(sid)})
    return out


def axis_index(q, state_id):
    """Where a state sits, counting `open` as 0. None when the axis does not name it.

    The STORAGE bit is one less: bit i holds `axis[i]`, i.e. `axis_index() - 1`. `open` has no bit —
    a quest nobody has touched has no bit cleared, which is what an erased store reads: $FF.
    """
    want = str(state_id or "").strip().lower()
    for i, s in enumerate(axis_states(q)):
        if s["id"].lower() == want:
            return i
    return None


def bytes_for(q) -> int:
    """How much the questlog spends on this quest: one bit per axis state, `open` free."""
    return math.ceil(len(axis_ids(q)) / 8)


def at_least(q, current, want) -> bool:
    """Is `current` at least as far along as `want` — the comparison `{"cond": "quest", …}` makes.

    `failed` is a dead end, not a point on the line: a failed quest is not "past" the states it
    never reached, and no forward state implies it. So it only ever compares equal to itself, which
    is exactly why the spec writes the failed gate as `==` and the others as `≥`.
    """
    a, b = axis_index(q, current), axis_index(q, want)
    if a is None:
        raise ValueError(f"'{current}' is not a state of this quest")
    if b is None:
        raise ValueError(f"'{want}' is not a state of this quest")
    if is_reserved(current) and str(current).lower() == "failed":
        return str(want).lower() == "failed"
    if str(want).lower() == "failed":
        return False
    # positions on the forward line only — `failed` sits beside it and must not shift the count
    fwd = [s["id"].lower() for s in axis_states(q) if s["id"].lower() != "failed"]
    return fwd.index(str(current).lower()) >= fwd.index(str(want).lower())


# ── markers ───────────────────────────────────────────────────────────────────────────────────

def markers(q) -> list:
    return [m for m in ((q or {}).get("markers") or []) if isinstance(m, dict)]


def marker_items(doc) -> set:
    """Every item id a marker in this document has already claimed."""
    out = set()
    for q in quests(doc).values():
        for m in markers(q):
            if m.get("kind") == "item" and isinstance(m.get("item"), int):
                out.add(m["item"])
    return out


def free_item_ids(item_names=None, reserved=(), charges=None) -> list:
    """The item ids that are genuinely spare, smallest first.

    Spare means: a slot the shipped table HAS and the shipped game does not use. There are three —
    70, 71, 72 ($46/$47/$48), whose names decode to an empty string in the engine's message bank
    and which appear in exactly one filler loot bag. Every other id 0..94 is a real item.

    Nothing above 94 is offered. The descriptor table is `$3108 + id*8` and `$3108 + 95*8 = $3400`,
    where the area record window starts — id 95 would read its price out of a record. Growing the
    table past that is engine work, not an allocation, so this function will not pretend otherwise.

    …and spare is not enough for a MARKER. Measured in the shipped descriptor table, byte 4 of the
    three is `[5, 1, 0]`: id 70 is created with five uses and id 71 with one, and an op-1 `have
    (item)` gate DECREMENTS at $14A5 unless the count is zero. A marker on 70 would therefore
    survive five tests and then quietly stop existing — the spend trap the spec allocates these ids
    to avoid (§2.3). So a caller that knows the counts passes them in and gets only the ids a gate
    can test forever; `charges` is a {id: count} map or a list of `wl_item` rows.

    Both injections exist so this module does not drag `wl_cell` (200 KB) or the areatrack blobs in
    at import time. Without `item_names` it falls back to `wl_cell._ITEM_NAMES`; without `charges`
    it cannot see the counts and says so by offering all three — which is the old behaviour, and
    the reason the item-spend check in `wl_place` runs on the placement as well.
    """
    if item_names is None:
        import wl_cell                             # local shim, same as wl_quest._refs
        item_names = wl_cell._ITEM_NAMES
    if isinstance(charges, list):
        charges = {r["id"]: r.get("charges") for r in charges if isinstance(r, dict) and "id" in r}
    taken = {int(x) for x in reserved if isinstance(x, int)}
    out = []
    for iid, name in sorted(item_names.items()):
        if iid in taken:
            continue
        if charges and charges.get(iid):
            continue
        n = str(name or "").strip()
        if not n or n.startswith("(unused"):
            out.append(iid)
    return out


def spendable_free_ids(item_names=None, charges=None) -> list:
    """The spare ids a marker may NOT have, and how many uses each is made with: [(id, count)].

    Only for the sentence the caller shows when it runs out — "there is no spare id left" reads as
    a mistake when 70 and 71 are sitting right there, so the refusal has to say why they are not on
    offer."""
    if charges is None:
        return []
    if isinstance(charges, list):
        charges = {r["id"]: r.get("charges") for r in charges if isinstance(r, dict) and "id" in r}
    return [(i, charges.get(i)) for i in free_item_ids(item_names) if charges.get(i)]


def alloc_item_marker(doc, item_names=None, reserved=(), charges=None):
    """A free global item id for a `{"kind": "item"}` marker — None when there is none left.

    None is a real answer here and the caller must say so out loud (the spec's 422 shape): the game
    has three spare item slots in total, and a designer who has used them cannot have a fourth
    without the item table growing. Silently reusing a live id would hand the player a marker that
    is also a Canteen.

    The counter is not decided here and cannot be — nothing in this editor writes an item
    descriptor. What this does instead is hand out only ids that ALREADY read count 0, which is the
    same guarantee from the other end (`wl_item`: byte 4 = 0 means the engine's `and #$3F / beq`
    skips the decrement). Pass `charges` and that holds; leave it out and it cannot be checked here.
    """
    taken = marker_items(doc) | {int(x) for x in reserved if isinstance(x, int)}
    free = free_item_ids(item_names, reserved=taken, charges=charges)
    return free[0] if free else None


def marker_words(doc) -> set:
    """The words markers in this document have already declared, upper-cased."""
    out = set()
    for q in quests(doc).values():
        for m in markers(q):
            if m.get("kind") == "word" and m.get("word"):
                out.add(str(m["word"]).strip().upper())
    return out


def check_word_unique(word, known_words=(), doc=None):
    """None when the word may be declared, otherwise the sentence saying why not.

    `known_words` is every keyword the game already answers to — the caller passes it in, because
    finding them means decoding all 43 areas plus the paragraph text (`wl_quest` builds exactly
    that list as the `kw:` links of its graph, `{l["id"] for l in graph["links"] if l["kind"] ==
    "keyword"}`). Doing it in here would make a pure helper cost ten seconds.

    A collision is not a crash, which is what makes it worth checking: two places answering to
    THANATOS both fire, and the second story quietly opens with the first one's password.
    """
    w = str(word or "").strip().upper()
    if not w:
        return "a marker word cannot be empty"
    if not _WORD_OK.match(w):
        return (f"'{w}' is not a usable keyword — it must be 3 to 16 capitals or digits and start "
                "with a letter; shorter words collide with the game's yes/no and menu picks")
    if w in {str(k or "").strip().upper() for k in known_words}:
        return f"'{w}' is already a keyword in the game — pick a word nothing else answers to"
    if doc is not None and w in marker_words(doc):
        return f"'{w}' is already declared as a marker by another quest"
    return None


# ── selftest ──────────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import collections
    import sys

    path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else \
        pathlib.Path(__file__).resolve().parents[1] / "annotations" / "quests.json"
    print(f"reading {path}   (read-only — this test never writes)")
    src = load(path)
    print(f"  on disk: v{version(src)}, {len(src)} top-level keys, "
          f"{len(quests(src))} quests, {path.stat().st_size} bytes")

    doc = migrate(src)
    qs = quests(doc)
    print(f"\nmigrated: v{version(doc)}, {len(qs)} quests")

    kinds = collections.Counter()
    phases = collections.Counter()
    for q in qs.values():
        for st in q.get("steps") or []:
            kinds[st.get("kind") or "(none)"] += 1
            if st.get("kind") == "beat":
                phases[st.get("phase") or "(none)"] += 1
    print("  steps by kind:  " + ", ".join(f"{k}={n}" for k, n in sorted(kinds.items())))
    if phases:
        print("  beats by phase: " + ", ".join(f"{k}={n}" for k, n in sorted(phases.items())))
    print("  quests by kind: " + ", ".join(
        f"{k}={n}" for k, n in sorted(collections.Counter(
            q.get("kind") or "(unsaid)" for q in qs.values()).items())))
    print(f"  leftover 'state' fields: "
          f"{sum(1 for q in qs.values() for s in (q.get('steps') or []) if 'state' in s)}")

    rows = sorted(((q["qid"], slug) for slug, q in qs.items()), key=lambda r: r[0])
    print(f"\nqids handed out: {len(rows)}, {rows[0][0]}..{rows[-1][0]}, "
          f"next free = {alloc_qid(doc)}")
    for qid, slug in rows:
        q = qs[slug]
        print(f"  {qid:3d}  {slug:<24} {q.get('kind') or '?':<4} order={q.get('order')}"
              f"  axis={len(axis_ids(q))} ({bytes_for(q)} B)")
    dupes = [r for r, n in collections.Counter(r[0] for r in rows).items() if n > 1]
    print(f"  duplicate qids: {dupes or 'none'}")

    again = migrate(doc)
    same = json.dumps(again, sort_keys=True) == json.dumps(doc, sort_keys=True)
    print(f"\nidempotent: second migrate() changes nothing -> {same}")
    if not same:
        a = json.dumps(doc, indent=1, sort_keys=True).splitlines()
        b = json.dumps(again, indent=1, sort_keys=True).splitlines()
        import difflib
        print("\n".join(list(difflib.unified_diff(a, b, "pass1", "pass2", n=1))[:40]))

    # the axis helpers, on an axis the file does not have yet (all 34 are still `[]`)
    demo = {"axis": [{"id": "s1"}, {"id": "s2"}, {"id": "done"}, {"id": "failed"}]}
    print(f"\naxis demo {axis_ids(demo)}  states={[s['id'] for s in axis_states(demo)]}  "
          f"bytes={bytes_for(demo)}")
    print(f"  index(open)={axis_index(demo, 'open')} index(done)={axis_index(demo, 'done')} "
          f"index(nope)={axis_index(demo, 'nope')}")
    print(f"  at_least(s2, s1)={at_least(demo, 's2', 's1')}  "
          f"at_least(s1, s2)={at_least(demo, 's1', 's2')}  "
          f"at_least(done, failed)={at_least(demo, 'done', 'failed')}  "
          f"at_least(failed, failed)={at_least(demo, 'failed', 'failed')}")
    print(f"  free item ids: {free_item_ids()}   alloc_item_marker -> {alloc_item_marker(doc)}")
    print(f"  check_word_unique('CATERPILLAR', ['THANATOS']) -> "
          f"{check_word_unique('CATERPILLAR', ['THANATOS'])}")
    print(f"  check_word_unique('THANATOS',    ['THANATOS']) -> "
          f"{check_word_unique('THANATOS', ['THANATOS'])}")


# ── THE QUESTLOG STORE ────────────────────────────────────────────────────────────────────────
# The axis says WHAT states a quest has; this says WHERE they live. Two different questions, and
# only the first one is authored.
#
# WHY THE LAYOUT IS PERSISTED AND NOT DERIVED. It would be one line to lay the quests out by qid on
# every read — and every such read would be a different answer as soon as one axis grows. A save in
# somebody's drawer refers to a bit by POSITION, and flash only ever writes 1→0, so a bit that moves
# is a saved game that lies. The allocation is therefore made once, written down beside
# `_retired_qids`, and freed ranges are retired rather than handed out again — the same rule the
# qids themselves follow, for the same reason.
#
#   "_log": {
#     "version": 1,
#     "bytes": 3,                                  total size of the store
#     "alloc":   {"14": {"byte": 0, "len": 2}},    qid -> its bytes
#     "retired": [{"byte": 2, "len": 1, "qid": 9}] freed by deletion, never reused
#   }
#
# WHAT A BIT MEANS, and the polarity is the whole point. Concept §8.6 option B: the store COUNTS
# DOWN FROM $FF. Erased flash is $FF, flash only ever writes 1->0, so progress CLEARS bits and the
# state is the NUMBER OF CLEARED BITS. Bits go in order — byte 0 empties before byte 1 — so:
#
#     open   $FF FF   nothing cleared. An untouched quest and an erased store are the same bytes
#     s1     $FE FF   bit 0 of byte 0 cleared
#     s2     $FC FF   bits 0-1 cleared
#     s9     $00 FE   byte 0 empty, bit 0 of byte 1 cleared
#
# A state is REACHED when its bit reads 0. `open` is position 0, has no bit and costs nothing —
# 8 bits carry 9 states (concept: states = 8 x bytes + 1). `done` and `failed` are ordinary states
# with ordinary bits.
#
# The author never sees this. He counts FORWARD (open -> s1 -> ... -> done); the encoding counts
# backward, and the concept says outright that the inversion must not surface in the designer.
LOG_VERSION = 1
ERASED = 0xFF                                    # a byte of the store before anything happened


def log_block(doc) -> dict:
    """The `_log` block, with its shape guaranteed. Not created on read — `log_alloc` writes."""
    b = (doc or {}).get("_log") or {}
    return {"version": b.get("version", LOG_VERSION),
            "bytes": int(b.get("bytes") or 0),
            "alloc": {str(k): dict(v) for k, v in (b.get("alloc") or {}).items()},
            "retired": [dict(x) for x in (b.get("retired") or [])]}


def log_alloc(doc, qid, need) -> dict:
    """Give quest `qid` a range of `need` bytes and return it. Idempotent while the size fits.

    First fit over the gaps between existing ranges, skipping retired ones. A quest whose axis
    outgrew its range gets a NEW range and the old one is retired — the bits do not move under a
    saved game, they are abandoned."""
    b = log_block(doc)
    need = max(1, int(need))
    cur = b["alloc"].get(str(qid))
    if cur and int(cur.get("len") or 0) >= need:
        return dict(cur)

    taken = [(int(v["byte"]), int(v["len"])) for k, v in b["alloc"].items() if k != str(qid)]
    taken += [(int(x["byte"]), int(x["len"])) for x in b["retired"]]
    if cur:
        b["retired"].append({"byte": int(cur["byte"]), "len": int(cur["len"]), "qid": int(qid)})
        taken.append((int(cur["byte"]), int(cur["len"])))
    taken.sort()

    at = 0
    for start, ln in taken:
        if at + need <= start:
            break
        at = max(at, start + ln)
    got = {"byte": at, "len": need}
    b["alloc"][str(qid)] = got
    b["bytes"] = max(b["bytes"], at + need)
    doc["_log"] = b
    return dict(got)


def log_free(doc, qid) -> dict:
    """Retire a quest's range. The bytes are NOT handed to the next quest — a cart in a drawer still
    has them set, and reusing them would read somebody else's progress as this quest's."""
    b = log_block(doc)
    cur = b["alloc"].pop(str(qid), None)
    if cur:
        b["retired"].append({"byte": int(cur["byte"]), "len": int(cur["len"]), "qid": int(qid)})
    doc["_log"] = b
    return b


def log_layout(doc) -> dict:
    """The store as the designer shows it and the emitter will walk it: every quest, its bytes, and
    every state's bit. Derived — the authored half is `axis`, the allocated half is `_log`.

    A quest with an empty axis holds no state and appears with `len: 0`; it costs nothing until it
    declares one. A quest whose axis is longer than its allocation is reported by `log_problems`
    rather than silently truncated here."""
    b = log_block(doc)
    rows = []
    for slug, q in sorted(quests(doc).items(), key=lambda kv: (kv[1].get("qid", 9999), kv[0])):
        qid = q.get("qid")
        states = axis_states(q)
        need = bytes_for(q)
        got = b["alloc"].get(str(qid)) if qid is not None else None
        base = int(got["byte"]) if got else None
        out = []
        for i, s in enumerate(states):
            if i == 0:                                   # `open` — position 0, no bit
                out.append({**s, "byte": None, "bit": None, "mask": None})
                continue
            k = i - 1
            # `mask` is the bit this state CLEARS; the state is reached when that bit reads 0.
            out.append({**s, "byte": (base + k // 8) if base is not None else None,
                        "bit": k % 8, "mask": 1 << (k % 8), "reached_when": "bit == 0"})
        rows.append({"slug": slug, "qid": qid, "name": q.get("name"),
                     "byte": base, "len": int(got["len"]) if got else 0, "needs": need,
                     "states": out})
    return {"version": b["version"], "bytes": b["bytes"], "quests": rows,
            # spelled out so an emitter cannot get the polarity wrong
            "encoding": {"erased": ERASED, "counts": "down",
                         "note": "progress CLEARS bits; the state is the number of cleared bits; "
                                 "`open` clears none, so an untouched quest reads $FF"},
            "retired": b["retired"],
            "lines": sum(1 for r in rows for s in r["states"] if s.get("log"))}


def log_problems(doc) -> list:
    """What would make the store wrong. Same spirit as `wl_scene_registry.problems`: measured, and
    each entry says which quest and what to do."""
    b = log_block(doc)
    out = []
    seen = {}
    for k, v in b["alloc"].items():
        for off in range(int(v["byte"]), int(v["byte"]) + int(v["len"])):
            if off in seen:
                out.append({"check": "log-overlap",
                            "detail": f"byte {off} is allocated to qid {seen[off]} and qid {k}"})
            seen[off] = k
    for x in b["retired"]:
        for off in range(int(x["byte"]), int(x["byte"]) + int(x["len"])):
            if off in seen:
                out.append({"check": "log-retired-reused",
                            "detail": f"byte {off} is retired (was qid {x.get('qid')}) but handed "
                                      f"to qid {seen[off]} — a saved cart still has those bits"})
    for slug, q in quests(doc).items():
        qid, need = q.get("qid"), bytes_for(q)
        if not need:
            continue
        got = b["alloc"].get(str(qid))
        if not got:
            out.append({"check": "log-unallocated",
                        "detail": f"{slug} declares {len(axis_ids(q))} state(s) but has no bytes"})
        elif int(got["len"]) < need:
            out.append({"check": "log-outgrown",
                        "detail": f"{slug} needs {need} byte(s), holds {got['len']} — reallocate"})
        for s in axis_states(q)[1:]:
            if not s.get("log"):
                out.append({"check": "log-gap",
                            "detail": f"{slug}: state {s['id']!r} has no log line"})
    return out
