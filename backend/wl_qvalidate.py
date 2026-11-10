"""wl_qvalidate.py — the battery: what a quest says about itself, checked without touching a byte.

Two of the three live faults in the TREX Base build were our own emitters, invisible because our
reader accepted what our writer produced. A round trip that agrees with itself proves nothing. So
the checks that matter are the ones that read the AUTHOR'S intent — the state axis, the gates, the
bindings — and ask whether it can possibly play out. That is what runs here.

This half of the battery (Spec §8, column *Wo* = `validate`) needs no area and writes nothing:

    ERRORS      axis-limit          an `advance` to a state the quest never declared, or past `done`
                axis-monotonic      an `advance` backwards — rule 5, a quest state only ever rises
                axis-reachable      a state a gate points at that no `advance` ever reaches
                bind-without-quest  an `advance`/`fail` with no quest id to write into
                binding-carrier     `on` names a role whose op has no hand-over field (Spec §5.1)

    WARNINGS    axis-text-budget    the log lines against the text5 supply — the real limit of a
                                    long axis, not the state store
                log-gap             a state the quest USES with no log line
                unbound-beat        a beat with no cell yet
                unreachable-beat    a `chained` beat nothing in this quest produces
                histogram           where this quest is off the end of the file's own distribution

    INFO        axis-bytes          how many bytes the axis costs: ceil(len(axis)/8)

The other half — edge census, carrying capacity, combat kit, record cap, item-spend trap — needs
the destination area's bytes and lives in `wl_place._checks`, next to the placement it refuses.

**Quest freedom is never a complaint.** A beat without a quest is CORRECT and the normal case
(Konzept §7.3, §7.6). The only thing said about a missing quest is when an `advance`/`fail` has
nowhere to write — that is a broken effect, not a missing quest.

Everything reports; nothing refuses. The caller decides what an error means: `/api/design/place`
turns errors into a 422 before the first byte, `GET /api/quest/{slug}/validate` just shows them.
"""
from __future__ import annotations

import wl_blueprint

# wl_questlog owns the state axis (Spec §2.2) and wl_place the carrier table (Spec §5.1). This
# module DEFERS to both — a second copy of either table is exactly the kind of quiet drift the
# battery exists to catch. The coupling is soft only so that the file stays runnable on its own:
# every borrowed answer has a fallback that reproduces what the spec prints, and nothing here needs
# either module to do its job, because the axis is a list in the quest document either way.
try:
    import wl_questlog
except Exception:                                                    # pragma: no cover - optional
    wl_questlog = None
try:
    import wl_place
except Exception:                                                    # pragma: no cover - optional
    wl_place = None


# ── THE AXIS ──────────────────────────────────────────────────────────────────────────────────
# `open` is implicit: the first state of EVERY quest, never in `axis`, never in the log book. The
# author counts forward, and `failed` stands BESIDE the axis rather than at its end — reachable
# from every state, ordered against none.
OPEN, DONE, FAILED = "open", "done", "failed"

# One text5 line, the figure the concept prices an axis with ("42 Stufen heißen 42 Logzeilen à ~60
# Zeichen text5", Konzept §1). AXIS_TEXT_BUDGET is the whole axis against that: nine states is the
# one-byte axis the concept calls the normal case. Both are SOFT — no questlog string pool has been
# measured yet — so the caller may pass its own total.
LOG_LINE_CHARS = 60
AXIS_TEXT_BUDGET = 9 * LOG_LINE_CHARS

# The roles whose op has a hand-over field, Spec §5.1: LOOT the advance pair at +0/+1, ENCOUNTER
# the victory pair at +10/+11, COND done/alt at +4..+7, SAY the advance at the end of the string
# run, TRANSITION the follow pair in the tail. `service` (grp6 AREA_EVENT) has none — which is why
# a shop is bound at its DOOR and not at the shop.
_CARRIERS = ("loot", "fight", "fight2", "check", "say", "after", "warn", "door")


def carriers() -> set:
    """Which beat roles can carry a quest binding — `wl_place.CARRIERS` where it exists."""
    c = getattr(wl_place, "CARRIERS", None) if wl_place else None
    if isinstance(c, dict):
        # role -> {op, handover, can_bind}: a role IS in the table and still cannot carry, which is
        # the whole point of listing op-6 with a `handover` of None rather than leaving it out.
        return {k for k, v in c.items()
                if (v.get("can_bind", True) if isinstance(v, dict) else v not in (None, False))}
    if isinstance(c, (list, tuple, set, frozenset)):
        return set(c)
    return set(_CARRIERS)


def roles() -> set:
    """Every role a pattern beat has — read off the patterns, so a new pattern extends this by
    existing rather than by being listed twice."""
    c = getattr(wl_place, "CARRIERS", None) if wl_place else None
    if isinstance(c, dict):
        return set(c)
    out = set()
    for bp in wl_blueprint.PATTERNS:
        for b in bp.get("beats", []):
            out.add(b["role"])
    return out | set(_CARRIERS) | {"service"}


def axis(quest: dict) -> list:
    """The state axis as `[{id, log}, …]`, `open` first and always present.

    `wl_questlog.axis_states` is the source: it drops an `open` somebody typed into the axis by
    hand (position 0 is not a stored bit) and it fills in the log lines. The fallback does the same
    thing by hand for a file read without that module. `failed` keeps its declared place in the
    order but is not a forward state — see `forward`.
    """
    fn = getattr(wl_questlog, "axis_states", None) if wl_questlog else None
    if callable(fn):
        return [{"id": str(s["id"]), "log": s.get("log") or ""} for s in fn(quest)]
    out = [{"id": OPEN, "log": ""}]
    for a in quest.get("axis") or []:
        sid = str((a if isinstance(a, str) else a.get("id")) or "").strip()
        if not sid or sid.lower() == OPEN or sid in [o["id"] for o in out]:
            continue
        out.append({"id": sid, "log": ("" if isinstance(a, str) else (a.get("log") or ""))})
    return out


def forward(all_states: list) -> list:
    """The progress order: everything except `failed`, which is a dead end beside the axis."""
    return [s for s in all_states if s != FAILED]


def beyond_done(fwd: list) -> set:
    """States declared AFTER `done`. `done` is the last forward state by definition, so anything
    behind it can never be advanced to."""
    return set(fwd[fwd.index(DONE) + 1:]) if DONE in fwd else set()


def axis_bytes(quest: dict) -> int:
    """What the axis costs in the questlog: eight states per byte, `open` free."""
    fn = getattr(wl_questlog, "bytes_for", None) if wl_questlog else None
    return fn(quest) if callable(fn) else (len(axis(quest)) + 6) // 8


def _quests(doc: dict) -> dict:
    """{slug: quest} — underscore keys are the document's own bookkeeping, not quests."""
    fn = getattr(wl_questlog, "quests", None) if wl_questlog else None
    if callable(fn):
        return fn(doc)
    return {k: v for k, v in (doc or {}).items()
            if isinstance(k, str) and not k.startswith("_") and isinstance(v, dict)}


# ── READING A STEP ────────────────────────────────────────────────────────────────────────────
# The step model is Spec §2.4 and it pins `bind` but not `gate` — the gate is only drawn in the UI
# (§9.2) and in the IR (§2.5). So the reader is tolerant about WHERE a gate is written down and
# strict about WHAT it looks like. OFFEN: pin the field name when the beat mask lands.

def _entry(check: str, detail: str, step=None) -> dict:
    e = {"check": check, "detail": detail}
    if step is not None:
        e["step"] = step
    return e


def _step_name(st: dict, i: int) -> str:
    return str(st.get("id") or st.get("title") or f"step {i}")


def _gates_of(st: dict) -> list:
    """Every quest GATE this step carries, in the shapes the spec and the UI can produce."""
    out = []
    for key in ("gate", "gates", "need"):
        v = st.get(key)
        for g in (v if isinstance(v, (list, tuple)) else [v]):
            if isinstance(g, dict) and (g.get("cond") in (None, "quest")) and "state" in g:
                out.append(g)
    return out


def _binds_of(st: dict) -> list:
    """Every quest EFFECT this step carries. `bind` is the spec's field (§2.4); `binds`/`effects`
    are taken too because a beat may hold more than one effect (Konzept §3) and only advance/fail
    are ours."""
    out = []
    for key in ("bind", "binds", "effects"):
        v = st.get(key)
        for b in (v if isinstance(v, (list, tuple)) else [v]):
            if isinstance(b, dict) and b.get("effect") in ("advance", "fail"):
                out.append(b)
    return out


def _qref(doc: dict, ref):
    """A quest reference — a qid or (author comfort) a slug — resolved to a qid, or None."""
    if ref is None or ref == "":
        return None
    if isinstance(ref, str) and not ref.lstrip("-").isdigit():
        q = doc.get(ref)
        return q.get("qid") if isinstance(q, dict) else None
    return int(ref)


def _is_self(doc: dict, slug: str, quest: dict, ref) -> bool:
    """Does this reference name the quest under test? Unstated is NOT self — containment decides
    that, and only for effects."""
    if ref is None or ref == "":
        return False
    if isinstance(ref, str) and not ref.lstrip("-").isdigit():
        return ref == slug
    qid = quest.get("qid")
    return qid is not None and int(ref) == int(qid)


def sites(doc: dict, slug: str, quest: dict) -> list:
    """Every step in the DOCUMENT that gates on or advances this quest.

    Document-wide, not quest-wide, because a quest whose gates are other quests' states is the HELP
    archetype (Konzept §8.5) and it must not read as a dead axis. A gate is matched by quest id; an
    EFFECT without an explicit quest belongs to the quest it is written in, because a beat belongs
    to at most one quest (rule 6).
    """
    out = []
    for oslug, oq in _quests(doc).items():
        own = oslug == slug
        for i, st in enumerate(oq.get("steps") or []):
            if not isinstance(st, dict):
                continue
            gates = [str(g["state"]) for g in _gates_of(st)
                     if (own and g.get("quest") in (None, ""))
                     or _is_self(doc, slug, quest, g.get("quest"))]
            binds = []
            for b in _binds_of(st):
                ref = b.get("quest")
                if ref in (None, ""):
                    if not own:
                        continue                      # somebody else's binding, not ours to judge
                    qid = oq.get("qid")
                elif _is_self(doc, slug, quest, ref):
                    qid = _qref(doc, ref)
                else:
                    continue
                binds.append({"effect": b.get("effect"), "to": (str(b["to"]) if b.get("to") else None),
                              "on": b.get("on"), "quest": qid})
            if gates or binds:
                out.append({"slug": oslug, "step": i, "name": _step_name(st, i), "raw": st,
                            "blueprint": st.get("blueprint"), "gates": gates, "binds": binds})
    return out


# ── THE CHECKS ────────────────────────────────────────────────────────────────────────────────

def validate_quest(doc: dict, slug: str, text_budget=None) -> dict:
    """The battery over ONE quest. `doc` is the whole quests document because half the checks are
    cross-quest; `slug` picks the quest under test. Returns
    `{"errors": [...], "warnings": [...], "info": [...]}`, each entry `{check, detail, step?}`.

    Nothing is written and nothing is refused here. What an entry MEANS is the caller's call.
    """
    quest = doc.get(slug) if isinstance(doc, dict) else None
    if not isinstance(quest, dict):
        # A caller holding just the one quest may hand it straight over; anything else is a bad
        # slug and deserves to be said, not answered with a clean bill of health.
        if isinstance(doc, dict) and ("axis" in doc or "steps" in doc):
            quest, doc = doc, {slug: doc}
        else:
            return {"errors": [_entry("unknown-quest", f"no quest {slug!r} in this document")],
                    "warnings": [], "info": []}

    errors, warnings, info = [], [], []
    ax = axis(quest)                                  # `open` at 0, then the axis in progress order
    all_states = [s["id"] for s in ax]
    fwd = forward(all_states)
    order = {s: i for i, s in enumerate(fwd)}
    declared = set(all_states)
    past_done = beyond_done(fwd)
    logs = {s["id"]: s["log"] for s in ax[1:]}        # `open` never carries one
    here = sites(doc, slug, quest)
    carry = carriers()
    known_roles = roles()

    def where(s):
        """A step reference the UI can jump to. Only the quest's OWN steps get the bare index —
        anything else says whose it is, so `step` never points into the wrong list."""
        return s["step"] if s["slug"] == slug else None

    def label(s):
        return s["name"] if s["slug"] == slug else f"{s['slug']}:{s['name']}"

    # ── ERROR · axis limit ───────────────────────────────────────────────────────────────────
    # An advance can only name a state the quest declared, and never one behind `done` — `done` is
    # the last forward state by definition. `fail` names the reserved `failed`, so a quest without
    # it has an effect that lands nowhere.
    for s in here:
        for b in s["binds"]:
            if b["effect"] == "fail":
                if FAILED not in declared:
                    errors.append(_entry("axis-limit",
                                         f"{label(s)}: fail, but the axis declares no '{FAILED}' state",
                                         where(s)))
                continue
            if not b["to"]:
                continue                              # a bare advance moves one step; nothing to name
            if b["to"] not in declared:
                errors.append(_entry("axis-limit",
                                     f"{label(s)}: advance to '{b['to']}', which the axis does not declare",
                                     where(s)))
            elif b["to"] in past_done:
                errors.append(_entry("axis-limit",
                                     f"{label(s)}: advance to '{b['to']}', which sits behind '{DONE}' — "
                                     f"'{DONE}' is the last forward state",
                                     where(s)))

    # ── ERROR · monotonicity ─────────────────────────────────────────────────────────────────
    # Rule 5: a quest state only ever rises. A beat that gates on `>= s2` and advances to `s1` runs
    # the quest backwards; advancing to the state the gate already requires is no progress at all
    # and the beat can fire forever.
    for s in here:
        gated = [order[g] for g in s["gates"] if g in order]
        if not gated:
            continue
        base = max(gated)
        for b in s["binds"]:
            if b["effect"] != "advance" or not b["to"] or b["to"] not in order:
                continue
            if order[b["to"]] < base:
                errors.append(_entry("axis-monotonic",
                                     f"{label(s)}: gate needs '{fwd[base]}' but the advance goes back "
                                     f"to '{b['to']}' — a quest state never falls",
                                     where(s)))
            elif order[b["to"]] == base:
                errors.append(_entry("axis-monotonic",
                                     f"{label(s)}: gate needs '{b['to']}' and the advance sets "
                                     f"'{b['to']}' — no progress, the beat never stops firing",
                                     where(s)))

    # ── ERROR · axis reachability ────────────────────────────────────────────────────────────
    # Every state a gate points at has to be arrived at by SOME advance, or the quest is dead from
    # there on. `open` is where every quest starts, so it is always reached.
    reached = {OPEN}
    for s in here:
        gated = [order[g] for g in s["gates"] if g in order]
        base = max(gated) if gated else 0
        for b in s["binds"]:
            if b["effect"] == "fail":
                reached.add(FAILED)
            elif b["to"]:
                reached.add(b["to"])
            elif base + 1 < len(fwd):
                reached.add(fwd[base + 1])            # bare advance: one step on from where it stands
    seen = set()
    for s in here:
        for g in s["gates"]:
            if g == OPEN or g in reached or g in seen:
                continue
            seen.add(g)
            why = "the axis does not declare it" if g not in declared else "no advance ever reaches it"
            errors.append(_entry("axis-reachable",
                                 f"gate on '{g}' ({label(s)}), but {why} — the quest is dead from there",
                                 where(s)))

    # ── ERROR · binding without a quest id ───────────────────────────────────────────────────
    # The ONLY thing said about a missing quest. A beat with no quest is correct and normal; an
    # advance with no quest is an effect with nowhere to write.
    for s in here:
        for b in s["binds"]:
            if b["quest"] is None:
                errors.append(_entry("bind-without-quest",
                                     f"{label(s)}: {b['effect']} on a quest with no qid — "
                                     f"give the quest an id before binding to it",
                                     where(s)))

    # ── ERROR · binding carrier ──────────────────────────────────────────────────────────────
    # `advance`/`fail` becomes its own record chained to the carrying beat, so the beat's op needs a
    # hand-over field (Spec §5.1). One reason per binding — the first that applies is the one to
    # act on.
    resolve = getattr(wl_place, "resolve_binding", None) if wl_place else None
    for s in here:
        # Only a PATTERN or a SERVICE has beats to reason about. A map blueprint is a rectangle cut
        # out of a real area and `find` does not know it — asking wl_place about one would answer
        # "no beats", which is true and useless. Say nothing rather than something wrong.
        bp = wl_blueprint.find(s["blueprint"]) if s["blueprint"] else None
        bp_roles = [b["role"] for b in bp.get("beats", [])] if bp else None
        for b in s["binds"]:
            on = b["on"]
            if bp is not None and callable(resolve):
                _, why = resolve(bp, on)          # one table, one rule — wl_place owns both
                if why:
                    errors.append(_entry("binding-carrier", f"{label(s)}: {why}", where(s)))
            elif on and on not in known_roles:
                errors.append(_entry("binding-carrier",
                                     f"{label(s)}: '{on}' is not a beat role", where(s)))
            elif on and on not in carry:
                errors.append(_entry("binding-carrier",
                                     f"{label(s)}: role '{on}' has no hand-over field — it cannot "
                                     f"carry the binding (Spec §5.1)",
                                     where(s)))
            elif on and bp_roles is not None and on not in bp_roles:
                errors.append(_entry("binding-carrier",
                                     f"{label(s)}: '{s['blueprint']}' has no '{on}' beat "
                                     f"({', '.join(bp_roles) or 'no beats at all'})",
                                     where(s)))
            elif bp_roles is not None and not any(r in carry for r in bp_roles):
                errors.append(_entry("binding-carrier",
                                     f"{label(s)}: no role of '{s['blueprint']}' can carry the binding",
                                     where(s)))

    # ── WARNING · the axis text budget ───────────────────────────────────────────────────────
    # The real limit of a long axis is the text, not the state store: the states cost a byte per
    # eight, the log lines cost text5 records.
    budget = AXIS_TEXT_BUDGET if text_budget is None else int(text_budget)
    total = sum(len(v) for v in logs.values())
    if total > budget:
        warnings.append(_entry("axis-text-budget",
                               f"the axis writes {len(logs)} log lines, {total} characters against "
                               f"a budget of {budget}"))
    for sid, txt in logs.items():
        if len(txt) > LOG_LINE_CHARS:
            warnings.append(_entry("axis-text-budget",
                                   f"'{sid}': the log line is {len(txt)} characters, one text5 line "
                                   f"holds about {LOG_LINE_CHARS}"))

    # ── WARNING · log gaps ───────────────────────────────────────────────────────────────────
    # A state the quest USES but never writes down. `open` is silent by design — it means untouched,
    # it never appears in the log book, and it is NEVER remarked on.
    used, first_use = [], {}
    for s in here:
        for g in s["gates"]:
            if g not in used:
                used.append(g)
                first_use[g] = s
        for b in s["binds"]:
            t = FAILED if b["effect"] == "fail" else b["to"]
            if t and t not in used:
                used.append(t)
                first_use[t] = s
    for st in used:
        if st == OPEN or st not in declared:
            continue                                  # undeclared is the axis-limit error's business
        if not logs.get(st):
            warnings.append(_entry("log-gap",
                                   f"'{st}' is used but has no log line — the log book will keep "
                                   f"showing the previous one",
                                   where(first_use[st])))

    # ── WARNING · unbound beats ──────────────────────────────────────────────────────────────
    # A beat exists without a cell (rule 2, the "Hülse"). That is a legitimate stage, not an error —
    # but the list of them is what tells the author what is left to place.
    #
    # A beat that already OWNS RECORDS is not on that list, whatever its cell says. A cell is one
    # way to reach a record; a chain is another — a COND's `done`, a KEYWORD's `after`, a SERVICE's
    # `advance`. Of the 232 beats migrated out of the old annotations, 88 sit on a cell and 144 are
    # chain-reached, and every one of those 144 exists in the shipped game. Warning about them
    # would tell the author to place what is already placed.
    for i, st in enumerate(quest.get("steps") or []):
        if not isinstance(st, dict) or st.get("kind") != "beat":
            continue
        if st.get("records"):
            continue
        at = st.get("at")
        if not (isinstance(at, dict) and at.get("col") is not None and at.get("row") is not None):
            warnings.append(_entry("unbound-beat", f"{_step_name(st, i)}: no cell yet", i))

    # ── WARNING · unreachable beats ──────────────────────────────────────────────────────────
    # A `chained` beat is never walked onto — something else has to plant it. A pattern chains its
    # OWN beats internally, so a chained STEP is produced by another STEP, and if nothing in the
    # quest says which, the record is either reached from outside (transition, patch, keyword) or it
    # is dead. Say so; do not guess.
    produced = set()
    for st in quest.get("steps") or []:
        if not isinstance(st, dict):
            continue
        for key in ("next", "produces", "chains_to"):
            v = st.get(key)
            for x in (v if isinstance(v, (list, tuple)) else [v]):
                if isinstance(x, str):
                    produced.add(x)
    for i, st in enumerate(quest.get("steps") or []):
        if not isinstance(st, dict) or st.get("trigger") != "chained":
            continue
        name = _step_name(st, i)
        if name in produced or st.get("reached_by"):
            continue
        warnings.append(_entry("unreachable-beat",
                               f"{name}: chained, but nothing in this quest produces it — name who "
                               f"reaches it (an advance, a patch, a transition, a keyword) or it is "
                               f"a dead record",
                               i))

    # ── WARNING · histogram outliers ─────────────────────────────────────────────────────────
    warnings += histogram_outliers(doc, slug)

    # ── INFO · what the axis costs ───────────────────────────────────────────────────────────
    n, nb = len(ax) - 1, axis_bytes(quest)
    info.append(_entry("axis-bytes",
                       f"{n + 1} state{'' if n == 0 else 's'} (open + {n}) → "
                       f"{nb} byte{'' if nb == 1 else 's'} in the questlog"))

    return {"errors": errors, "warnings": warnings, "info": info}


# ── THE HISTOGRAM ─────────────────────────────────────────────────────────────────────────────
# "Where is this one the outlier" (Spec §8, Konzept §6) — the question that twice found in minutes
# what thinking did not. The population here is the 34 quests in the document itself: what this
# module can see is what the quests SAY, and a quest that is off the end of that distribution is
# either doing something nobody else does or has been edited into a shape nobody meant.
#
# Deliberately quiet. A metric only speaks when the quest beats EVERY other quest on it *and* is at
# least twice the typical one — an outlier that half the file also is would be noise, and a warning
# nobody reads is worse than none. Small populations say nothing at all.
_HIST_MIN_POP = 4                                  # below this a "distribution" is an anecdote
_HIST_FACTOR = 2                                   # …and it has to be twice the median, not 1 more


def _median(xs: list) -> float:
    s = sorted(xs)
    n = len(s)
    return 0.0 if not n else float(s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)


def _metrics(q: dict) -> dict:
    """What one quest can be counted in without reading a single byte of the game."""
    steps = [s for s in (q.get("steps") or []) if isinstance(s, dict)]
    return {"steps": len(steps),
            "states": len(axis(q)) - 1,
            "markers": len([m for m in (q.get("markers") or []) if isinstance(m, dict)]),
            "records": max([len(s.get("records") or []) for s in steps] or [0])}


_HIST_WORDS = {"steps": ("steps", "the longest other quest has"),
               "states": ("axis states", "the longest other axis has"),
               "markers": ("markers", "the most any other quest declares"),
               "records": ("records on one beat", "the most on any other quest's beat")}


def histogram_outliers(doc: dict, slug: str) -> list:
    """Warnings where this quest is off the end of the document's own distribution."""
    quest = _quests(doc).get(slug) or (doc if isinstance(doc, dict) and "steps" in doc else None)
    if not isinstance(quest, dict):
        return []
    others = [_metrics(q) for s, q in _quests(doc).items() if s != slug]
    if len(others) < _HIST_MIN_POP:
        return []
    mine, out = _metrics(quest), []
    for key, (what, than) in _HIST_WORDS.items():
        vals = [m[key] for m in others]
        top, mid = max(vals), _median(vals)
        if mine[key] > top and mine[key] >= _HIST_FACTOR * max(mid, 1):
            out.append(_entry("histogram",
                              f"{mine[key]} {what} — more than any of the other {len(others)} "
                              f"quests in this file ({than} {top}, the middle one {mid:g}). Not "
                              f"wrong, but nothing else here is shaped like this"))
    return out


# ── PLACE-STAGE CHECKS — the ones that need an AREA ────────────────────────────────────────────
# Edge census, carrying capacity, combat kit (the 34-string block, `desc+$37`, the foe-name table),
# record cap and the item-spend trap all need the blob the beat is going into. None of that is
# knowable from a quest document, which is why Spec §8 runs them in `/api/design/place`.
#
# They live in `wl_place._checks`, next to the placement they refuse, and they are listed here only
# so that the two halves of Spec §8 can be found from one another. There used to be an empty
# PLACE_CHECKS list and an `area_checks()` runner here for them to dock into; nothing ever
# registered and nothing ever called it, and a docking point with no ships is a place to look for a
# check that is not there.


# ── SELF-TEST ─────────────────────────────────────────────────────────────────────────────────
# One clean quest and one mutation per check, each mutation firing exactly ONE check. The point is
# not coverage for its own sake: a battery that cross-fires is a battery nobody reads, so every
# case asserts the full set of checks it raises, not just the one it is named after.

if __name__ == "__main__":
    import copy

    def _at(col, row):
        return {"side": 1, "area": 10, "col": col, "row": row}

    CLEAN = {
        "_version": 2,
        "garoof": {
            "name": "Help a man find his dog", "qid": 17, "kind": "side",
            "home": {"side": 1, "area": 10},
            "axis": [
                {"id": "s1", "log": "Adam asked us to find his lost dog."},
                {"id": "s2", "log": "We found a stressed dog near the dam."},
                {"id": "done", "log": "Garoof and Adam have been reunited."},
            ],
            "steps": [
                {"kind": "beat", "phase": "placed", "id": "ask", "trigger": "talk",
                 "at": _at(12, 7), "blueprint": "a-sign",
                 "bind": {"effect": "advance", "to": "s1", "on": "say"}},
                {"kind": "beat", "phase": "placed", "id": "find", "trigger": "step_on",
                 "at": _at(31, 19), "blueprint": "guarded-stash",
                 "gate": {"cond": "quest", "quest": 17, "state": "s1"},
                 "bind": {"effect": "advance", "to": "s2", "on": "loot"}},
                {"kind": "beat", "phase": "placed", "id": "back", "trigger": "talk",
                 "at": _at(12, 7), "blueprint": "a-sign",
                 "gate": {"cond": "quest", "quest": 17, "state": "s2"},
                 "bind": {"effect": "advance", "to": "done", "on": "say"}},
            ],
        },
    }

    def case(name, expect, mutate):
        doc = copy.deepcopy(CLEAN)
        mutate(doc["garoof"])
        return name, expect, doc

    def _axis_limit(q):
        q["steps"][2]["bind"]["to"] = "s9"            # a state the axis never declared

    def _monotonic(q):
        q["steps"][2]["bind"]["to"] = "s1"            # gate needs s2, advance falls back to s1

    def _reachable(q):
        del q["steps"][1]["bind"]                     # nothing reaches s2 any more, but back gates on it

    def _no_qid(q):
        del q["qid"]                                  # three bindings with nowhere to write

    def _carrier(q):
        q["steps"][0]["blueprint"] = "door-to-service"   # bind the shop AT the shop…
        q["steps"][0]["bind"]["on"] = "service"          # …the one op with no hand-over field

    def _text(q):
        q["axis"][0]["log"] = ("Adam Doochin asked us to find his lost dog and gave us her "
                               "favourite chew toy to help.")

    def _log_gap(q):
        del q["axis"][1]["log"]                       # s2 is gated on and advanced to, and says nothing

    def _unbound(q):
        del q["steps"][1]["at"]                       # a beat still without a cell

    def _chained(q):
        q["steps"][1]["trigger"] = "chained"          # nothing says who plants it

    def _chained_ok(q):
        q["steps"][1]["trigger"] = "chained"
        q["steps"][0]["next"] = "find"                # …and now something does

    CASES = [
        ("clean", set(), lambda q: None),
        ("axis limit", {"axis-limit"}, _axis_limit),
        ("monotonicity", {"axis-monotonic"}, _monotonic),
        ("axis reachable", {"axis-reachable"}, _reachable),
        ("bind without qid", {"bind-without-quest"}, _no_qid),
        ("binding carrier", {"binding-carrier"}, _carrier),
        ("axis text budget", {"axis-text-budget"}, _text),
        ("log gap", {"log-gap"}, _log_gap),
        ("unbound beat", {"unbound-beat"}, _unbound),
        ("unreachable beat", {"unreachable-beat"}, _chained),
        ("chained with producer", set(), _chained_ok),
    ]

    bad = 0
    for name, expect, mutate in CASES:
        _, _, doc = case(name, expect, mutate)
        r = validate_quest(doc, "garoof")
        raised = {e["check"] for e in r["errors"]} | {w["check"] for w in r["warnings"]}
        ok = raised == expect
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {name:<22} "
              f"raised={sorted(raised) or '-'}  expected={sorted(expect) or '-'}")
        for e in r["errors"]:
            print(f"       ERROR   [{e['check']}] {e['detail']}")
        for w in r["warnings"]:
            print(f"       WARN    [{w['check']}] {w['detail']}")
        for n in r["info"]:
            print(f"       info    [{n['check']}] {n['detail']}")

    print(f"\n{len(CASES) - bad}/{len(CASES)} cases as expected")
    raise SystemExit(1 if bad else 0)
