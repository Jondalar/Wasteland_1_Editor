"""wl_blueprint.py — the catalogue a designer picks from, and the plan a pick turns into.

This is the top-down half. Everything else in this editor reads UP from the records — beats out of
bytes, events out of beats, quests annotated onto events — because that is the direction reverse
engineering runs in. Designing runs the other way: you think of a quest, you pick shapes, you fill
in who and what, you place it, and records fall out at the end. For that to be possible one thing
has to exist that does not exist anywhere else here: a description of a piece of content that is
NOT yet bytes, and is allowed to be incomplete.

That is a blueprint. Three families, and they are different in kind, not in size:

  MAP        a region of the map — a building, a camp — with its cells, tiles and records.
             These already exist (editor/blueprints/*.json, cut from a real area) and are placed by
             the existing blueprint endpoints. Listed here so the designer sees one catalogue.
  SERVICE    a shop, a doctor, a library, a talker. One op-6 record with a family-specific layout.
  PATTERN    a repeating shape: something guards a cache, a check hides a stash, a door leads to a
             shop. Two to four beats wired to each other.

The patterns are NOT invented. They are the shapes the shipped game actually repeats, counted over
all 43 areas with the same tree walk the Events view uses (`seen`). "Guarded stash" is 95 whole
chains and 233 adjacent pairs; if a shape is not in here it is because the game does not do it.
Same discipline as the edge grammar: measure, then offer.

A blueprint is always COPIED into the quest that uses it and filled in there. It is a form, never a
shared instance — the whole point is to shape it to the one place it is going.
"""

# ── HOLE KINDS ────────────────────────────────────────────────────────────────────────────────
# What the designer still has to say. Each kind is something the UI can render as a picker with
# pictures and names rather than a number box: a foe shows its portrait, an item its name, a check
# its skill list. `text` and `pages` become NEW strings in the area at compile time — which is
# exactly where the text-id budget bites, and why the gate runs before any of this is written.
HOLE_KINDS = ("foe", "text", "pages", "items", "check", "service_kind", "name", "portrait", "area")


def _hole(key, kind, label, hint="", **extra):
    return {"key": key, "kind": kind, "label": label, "hint": hint, **extra}


# ── SERVICES ──────────────────────────────────────────────────────────────────────────────────
# Selector → family, read off what the shipped records are CALLED. The layout families come from
# `wl_cell.op_record_ir`: 0/1/5 carry a greeting at +$05 and a class list, 9 carries page ids at
# +$14, everything else carries a greeting at +$03. The portrait is at +$13 in all of them, because
# the 11-byte name plus its terminator is what puts it there.
SERVICES = [
    {"id": "svc-shop", "name": "Shop", "selector": 1, "seen": 7,
     "about": "Sells wares. “Trading car”, “AG. store”, “Q. Emporium”.",
     "holes": [_hole("name", "name", "sign over the door", "11 characters"),
               _hole("portrait", "portrait", "who serves you"),
               _hole("greeting", "text", "what they say when you walk in")]},
    {"id": "svc-doctor", "name": "Doctor / infirmary", "selector": 0, "seen": 7,
     "about": "Heals and revives. “Infirmary”, “Old Doc Bob”, “Nuclear Aid”.",
     "holes": [_hole("name", "name", "sign over the door", "11 characters"),
               _hole("portrait", "portrait", "who treats you"),
               _hole("greeting", "text", "what they say when you walk in")]},
    {"id": "svc-library", "name": "Library / teacher", "selector": 2, "seen": 8,
     "about": "Teaches skills. “Vegas Lib.”, “New Thought”, “E Technical”.",
     "holes": [_hole("name", "name", "sign over the door", "11 characters"),
               _hole("portrait", "portrait", "who teaches"),
               _hole("greeting", "text", "what they say when you walk in")]},
    {"id": "svc-talk", "name": "Someone who talks", "selector": 9, "seen": 2,
     "about": "Pages of speech with a portrait, and nothing else. TREX and the TOASTMASTER are "
              "the only two in the game — the newest thing here, and the cheapest to author.",
     "holes": [_hole("name", "name", "what they are called", "11 characters"),
               _hole("portrait", "portrait", "their picture"),
               _hole("pages", "pages", "what they say", "one page per screen")]},
    {"id": "svc-ranger", "name": "Ranger centre", "selector": 3, "seen": 1,
     "about": "The Rangers' own hall. One in the game.",
     "holes": [_hole("name", "name", "sign over the door", "11 characters"),
               _hole("portrait", "portrait", "who greets you"),
               _hole("greeting", "text", "what they say when you walk in")]},
    # OURS, not 1988's — and the difference is three bytes. `s1_scene_id05.bin` is a copy of the
    # store scene 01 with `$1E/$1F` `A9 01` (lda #1 — portrait wired to 1) changed to `B1 5F`
    # (lda ($5F),y — portrait read from the RECORD) and its offset `$29` 13→14. Selector 1 gives
    # every shop in the game the same face; this one lets each shop carry its own, which is why
    # TREX Base has twelve services with twelve portraits.
    #
    # Same holes as `svc-shop`, so the two are interchangeable at authoring time — the only reason
    # to take selector 1 is byte-fidelity to a 1988 record.
    {"id": "svc-shop-face", "name": "Shop, with its own face", "selector": 5, "seen": 8,
     "about": "Sells wares like the shop above, but the portrait comes from the record instead of "
              "being wired into the scene. Ours: eight in TREX Base, none in the 1988 game.",
     "holes": [_hole("name", "name", "sign over the door", "11 characters"),
               _hole("portrait", "portrait", "who serves you"),
               _hole("greeting", "text", "what they say when you walk in")]},
]


# ── PATTERNS ──────────────────────────────────────────────────────────────────────────────────
# `beats` is the order the player meets them. `link` says how each beat hands over to the next:
#   advance   the cell's own state is replaced — the plain shipped hand-over
#   patch     a CELL_PATCH plants the next beat on ANOTHER square (and clears this one)
# The counts are from the shipped game, measured with the Events tree walk.
PATTERNS = [
    {"id": "guarded-stash", "name": "Something guards a cache", "seen": 95,
     "about": "The commonest shape in the game: you walk up, you fight, what is left is a sack. "
              "233 adjacent pairs, 95 chains that are exactly this and nothing more.",
     "beats": [{"role": "fight", "op": 0x3, "link": "advance"},
               {"role": "loot", "op": 0x5}],
     "holes": [_hole("foe", "foe", "who guards it"),
               _hole("announce", "text", "what you see walking up",
                     "shown before the fight starts"),
               _hole("items", "items", "what is in the sack")]},

    {"id": "guarded-stash-note", "name": "A cache with something to read", "seen": 25,
     "about": "The same, but taking it leaves a message — a note, a body, a realisation.",
     "beats": [{"role": "fight", "op": 0x3, "link": "advance"},
               {"role": "loot", "op": 0x5, "link": "advance"},
               {"role": "after", "op": 0x1}],
     "holes": [_hole("foe", "foe", "who guards it"),
               _hole("announce", "text", "what you see walking up"),
               _hole("items", "items", "what is in the sack"),
               _hole("after", "text", "what you read once you have it")]},

    {"id": "two-waves", "name": "Two waves", "seen": 25,
     "about": "Beat the first and a second one steps out, somewhere you can see. The Missing "
              "Toaster is this shape.",
     "beats": [{"role": "fight", "op": 0x3, "link": "patch"},
               {"role": "fight2", "op": 0x3, "link": "advance"},
               {"role": "loot", "op": 0x5}],
     "holes": [_hole("foe", "foe", "the first one"),
               _hole("announce", "text", "what you see walking up"),
               _hole("foe2", "foe", "what comes next"),
               _hole("announce2", "text", "what you see when it appears"),
               _hole("items", "items", "what it was standing over")]},

    {"id": "hidden-cache", "name": "Hidden — you have to spot it", "seen": 19,
     "about": "A skill or attribute check stands in front of a cache. Pass and you find it; fail "
              "and the square says nothing. Highpool's loose floorboard is this.",
     "beats": [{"role": "check", "op": 0x2, "link": "advance"},
               {"role": "loot", "op": 0x5, "link": "advance"},
               {"role": "after", "op": 0x1}],
     "holes": [_hole("check", "check", "what it takes to spot it"),
               _hole("prompt", "text", "what the square looks like at first"),
               _hole("found", "text", "what you see when you spot it"),
               _hole("items", "items", "what is hidden there"),
               _hole("after", "text", "what you think once you have it")]},

    {"id": "locked-way", "name": "A way that has to be opened", "seen": 27,
     "about": "A check in front of a door. Pass and you go through.",
     "beats": [{"role": "check", "op": 0x2, "link": "advance"},
               {"role": "door", "op": 0xA}],
     "holes": [_hole("check", "check", "what it takes to open it"),
               _hole("prompt", "text", "what you see"),
               _hole("area", "area", "where it leads")]},

    {"id": "door-to-service", "name": "A door into a shop or a talker", "seen": 31,
     "about": "The way every shop, doctor and library in the game is reached: a door record whose "
              "tail hands over to the service. Nothing is rewritten, which is why these were "
              "invisible in the old view.",
     "beats": [{"role": "door", "op": 0xA, "link": "door"},
               {"role": "service", "op": 0x6}],
     # `pages` belongs to the TALKER (selector 9) and `greeting` to everyone else — `_params` picks
     # by selector, and only one of the two is ever read. Declaring just `pages` is how a shop
     # placed through this pattern used to open with greeting 0 and say whatever string 0 of that
     # area happened to say. Both are optional; the placement path warns when the one that matters
     # is empty.
     "holes": [_hole("service_kind", "service_kind", "what kind of place"),
               _hole("name", "name", "sign over the door", "11 characters"),
               _hole("portrait", "portrait", "who is inside"),
               _hole("greeting", "text", "what they say when you walk in", optional=True),
               _hole("pages", "pages", "what they say", "one page per screen", optional=True)]},

    {"id": "ambush", "name": "A warning, then an ambush", "seen": 11,
     "about": "Something tells you, then something jumps you, then you are left with the result.",
     "beats": [{"role": "warn", "op": 0x1, "link": "advance"},
               {"role": "fight", "op": 0x3, "link": "advance"},
               {"role": "after", "op": 0x1}],
     "holes": [_hole("warn", "text", "the warning"),
               _hole("foe", "foe", "what jumps you"),
               _hole("announce", "text", "what you see when it does"),
               _hole("after", "text", "what is left afterwards")]},

    {"id": "a-sign", "name": "Something to read", "seen": 92,
     "about": "One square, one message. A sign, a smell, a body. The furniture of the world — and "
              "the cheapest thing there is to place.",
     "beats": [{"role": "say", "op": 0x1}],
     "holes": [_hole("say", "text", "what it says")]},
]


def catalogue() -> dict:
    """TWO families in one list, so the designer sees a catalogue and not three tabs.

    Buildings were the third, and they were the one that could not be compiled: `find()` — the
    lookup the one compile path uses — knows patterns and services, so a building picked as a
    beat's shape was refused as "no blueprint" at placement time. A building is a map region and it
    is placed by the blueprint endpoints; what a beat is made of is a pattern or a service (Spec
    §10 P7, and `editor/blueprints/Shop.SPLIT.md` for what a building splits INTO: its map half is
    a block, its scene half is `door-to-service` with values)."""
    return {
        "families": [
            {"key": "pattern", "label": "Patterns",
             "about": "Shapes the game repeats. Counted over all 43 areas — if it is not here, "
                      "the game does not do it.",
             "items": PATTERNS},
            {"key": "service", "label": "Services",
             "about": "A shop, a doctor, a library, someone who talks.",
             "items": SERVICES},
        ],
        "hole_kinds": list(HOLE_KINDS),
    }


def find(bp_id: str):
    for lst in (PATTERNS, SERVICES):
        for b in lst:
            if b["id"] == bp_id:
                return b
    return None


def missing(bp: dict, values: dict) -> list:
    """Which holes are still empty. A sketch is ALLOWED to be incomplete — that is the whole reason
    this layer exists — so this reports rather than refuses."""
    out = []
    for h in bp.get("holes", []):
        # An OPTIONAL hole is one of a set where only one applies — `door-to-service` declares both
        # `greeting` and `pages`, and `_params` reads whichever the chosen selector uses. Demanding
        # both would make every shop unplaceable; demanding neither is what left a shop opening on
        # string 0. So they are offered, not required, and `wl_place._checks` is what complains
        # when the one that actually matters is empty.
        if h.get("optional"):
            continue
        v = values.get(h["key"])
        if v is None or v == "" or v == [] or v == {}:
            out.append(h["key"])
    return out


def plan(bp: dict, values: dict) -> list:
    """A filled blueprint → the ordered record specs to emit, LAST FIRST.

    Last first because a record can only point at one that already has an id: the fight's victory
    has to name the sack, so the sack is written first. Each entry is
    {role, op, params, link, needs} where `needs` names the role this one must point at once that
    role has an id — the caller resolves it, because only the caller knows the ids it allocated.

    No bytes are built here and no ids are invented. This says WHAT to emit, in what order, and how
    the pieces refer to each other; `wl_cell.op_record_ir` remains the only thing that names a
    record's fields and `wl_ir._emit_op_record` the only thing that writes them.
    """
    beats = bp.get("beats", [])
    out = []
    for i, b in enumerate(beats):
        nxt = beats[i + 1]["role"] if i + 1 < len(beats) else None
        out.append({"role": b["role"], "op": b["op"], "link": b.get("link"),
                    "needs": nxt, "params": _params(bp, b, values)})
    return list(reversed(out))


def _params(bp: dict, beat: dict, v: dict) -> dict:
    """The flat params for one beat — the same shape `op_record_ir` takes.

    Text values arrive here as STRING IDS: the caller appends the designer's words to the area's
    pool first, because that is the step that can fail (a 7-bit id, and twelve areas already have
    none free) and it must fail before anything is written."""
    r, op = beat["role"], beat["op"]
    if op == 0x3:                                  # ENCOUNTER
        foe = v.get("foe2" if r == "fight2" else "foe") or {}
        return {"foe": int(foe.get("id", 1)), "count": int(foe.get("count", 1)),
                "message": int(v.get("announce2" if r == "fight2" else "announce") or 0),
                "active": True,
                # peaceful: it stands there until the player presses E. 111 shipped encounters do,
                # and it is the difference between a scene and an ambush.
                "peaceful": bool(foe.get("peaceful", r != "fight2")),
                "bit2": bool(foe.get("stationary", True))}
    if op == 0x5:                                  # LOOT
        return {"items": [{"item": int(i["item"]), "qty": int(i.get("qty", 1))}
                          for i in (v.get("items") or [])]}
    if op == 0x1:                                  # SAY
        ids = v.get(r) or v.get("say") or 0
        return {"messages": ids if isinstance(ids, list) else [int(ids)]}
    if op == 0x2:                                  # COND
        c = v.get("check") or {}
        return {"b0": 0x80, "prompt": int(v.get("prompt") or 0),
                "success": int(v.get("found") or 0), "fail": 0,
                "conds": [{"op": ((int(c.get("op", 0)) & 7) << 5) | (int(c.get("difficulty", 2)) & 0x1F),
                           "arg": int(c.get("arg", 9))}]}
    if op == 0xA:                                  # TRANSITION
        a = v.get("area") or {}
        return {"target": int(a.get("target", 0)), "x": int(a.get("x", 1)),
                "y": int(a.get("y", 1)), "message": int(v.get("prompt") or 0)}
    if op == 0x6:                                  # SERVICE
        sel = int((v.get("service_kind") or {}).get("selector", 9))
        p = {"selector": sel, "label": str(v.get("name") or "")[:11],
             "portrait": int(v.get("portrait") or 0)}
        if sel == 9:
            p["pages"] = list(v.get("pages") or [])
        else:
            p["greeting"] = int(v.get("greeting") or 0)
        return p
    return {}


def text_holes(bp: dict) -> list:
    """The holes whose value is WORDS and therefore becomes a new string in the area."""
    return [h["key"] for h in bp.get("holes", []) if h["kind"] in ("text", "pages")]
