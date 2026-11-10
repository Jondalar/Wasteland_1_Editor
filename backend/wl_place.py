"""wl_place.py — a blueprint, a square, and the bytes that fall out of both.

`wl_blueprint` says WHAT a piece of content is while it is still a form with holes in it. This is
the other half: it fills the holes in, writes the records, binds the square, and — if the caller
named a quest — notes down what it just made. It is the ONE compile path (spec E2): placing needs
no quest, and a quest binding is an optional attribute of the call rather than its precondition.
A questfree beat becomes records and nothing else; the world is its own store.

Two rules shape everything below.

ALL OR NOTHING. Every check that can refuse runs before the first byte is committed. A placement
that fell over halfway would leave an area with an encounter pointing at a message that does not
exist, and nothing above would show that it is broken — it would just play wrong. So the whole
placement is built in memory and the host is asked to write only once everything holds. A failure
in the middle is a bug, not a state.

ONE EMITTER. Records are named by `wl_cell.op_record_ir` and written by `wl_ir._emit_op_record`,
exactly as everywhere else in this editor. Nothing here assembles a byte itself; the three silent
data bugs this project paid for all came from code that did.

The module is deliberately FastAPI-free: it raises nothing an HTTP layer has to know about and
returns plain dicts. What it cannot know — where a blob lives and where it is written — comes in as
a `host`, duck-typed:

    host.record(side, area) -> bytes              the area's record blob (work copy or baseline)
    host.save_record(side, area, data) -> str     THE save path; may raise ValueError to refuse
  optional, and only touched when the feature is used:
    host.ir(side, area) -> dict                   the cached JSON-IR (else it is decoded here)
    host.map(side, area) -> bytes                 the map blob                            [block]
    host.save_map(side, area, data) -> str                                                [block]
    host.gfx(gid) -> bytes                        the destination tileset                 [block]
    host.gfx_free_slots(gid) -> set               the slots NOTHING points at             [block]
    host.save_gfx(gid, data) -> str                                                       [block]
    host.block(bid) -> dict | None                a block by id, library or mined         [block]
    host.quests() -> dict                         the authored quests, mutable            [quest]
    host.save_quests(doc) -> None                                                         [quest]
    host.undo(side, area) -> None                 an undo point, taken just before the commit
"""

import dataclasses

import wl_area
from wl_machine import RECORD_BASE as RAM   # $3400 on the C64, $D000 on the Apple
import wl_blueprint
import wl_cell
import wl_ir
import wl_persist
import wl_questlog
import wl_story


# ── WHAT CAN CARRY A QUEST BINDING ────────────────────────────────────────────────────────────
# `advance Q` / `fail Q` becomes a record of its own, CHAINED to the beat that carries it. So a
# beat can carry one only if its op has somewhere to put the hand-over. That — and only that — is
# the new information here; which roles exist and which op each one is stays in `wl_blueprint.
# PATTERNS`, read out of `beats[].role` / `beats[].op` below rather than written down twice.
#
# Measured, spec §5.1. op-6 is in the table with None ON PURPOSE: a service record has no hand-over
# field at all (§5.2 — 28 of 39 service doors end after 6 bytes), and saying so beats saying
# nothing, because "not listed" would read as "not looked at".
HANDOVER = {
    0x1: "advance at the end of the string run",
    0x2: "done/alt at +4..+7, plus a branch per condition",
    0x3: "victory pair at +10/+11",
    0x5: "advance pair at +0/+1",
    0x6: None,
    0xA: "follow pair in the tail",
}

# Edges the shipped game never makes, measured with `wl_object.refs_of` over all 43 areas:
# ENCOUNTER → ENCOUNTER is 0×. The second wave always goes through a CELL_PATCH, which is exactly
# why `two-waves` is shaped the way it is.
ZERO_EDGES = {(0x3, 0x3)}


def _carriers() -> dict:
    """role -> {op, handover, can_bind}, derived from the patterns so it cannot drift from them."""
    out = {}
    for bp in wl_blueprint.PATTERNS:
        for b in bp.get("beats") or []:
            out.setdefault(b["role"], {"op": b["op"], "handover": HANDOVER.get(b["op"]),
                                       "can_bind": HANDOVER.get(b["op"]) is not None})
    return out


CARRIERS = _carriers()


def carrier_table() -> list:
    """Spec §5.1 as rows, for the beat mask: which role can be told to carry a quest binding."""
    return [{"role": r, **v, "op_name": wl_cell.OP_NAMES.get(v["op"], f"op ${v['op']:X}")}
            for r, v in sorted(CARRIERS.items(), key=lambda kv: (kv[1]["op"], kv[0]))]


def can_carry(op: int) -> bool:
    return HANDOVER.get(op) is not None


def default_carrier(pattern):
    """The LAST beat that can carry a binding — not simply the last beat.

    For `door-to-service` that is the DOOR: the op-A runs before the service, carries a follow pair
    already, and costs nothing. The service itself has no hand-over field (§5.2)."""
    bp = _bp(pattern)
    for b in reversed((bp or {}).get("beats") or []):
        if can_carry(b["op"]):
            return b["role"]
    return None


def resolve_binding(pattern, on=None):
    """(role, error) — which beat carries the quest binding. `on` overrides the default.

    Never raises: the caller collects this together with the other refusals so a designer sees all
    of them at once instead of one 422 per attempt."""
    bp = _bp(pattern)
    beats = (bp or {}).get("beats") or []
    if not beats:
        return None, "this blueprint has no beats, so there is nothing that could carry a binding"
    roles = [b["role"] for b in beats]
    if on is None:
        role = default_carrier(bp)
        if role is None:
            return None, (f"no role can carry the binding — {', '.join(roles)} are all ops without "
                          f"a hand-over field (§5.1)")
        return role, None
    if on not in roles:
        return None, f"this blueprint has no {on!r} beat — it has {', '.join(roles)}"
    beat = next(b for b in beats if b["role"] == on)
    if not can_carry(beat["op"]):
        alt = default_carrier(bp)
        name = wl_cell.OP_NAMES.get(beat["op"], f"op ${beat['op']:X}")
        return None, (f"{on} is an op-${beat['op']:X} {name} record and has no hand-over field, so "
                      f"nothing can be chained to it" +
                      (f" — bind to {alt} instead" if alt else ""))
    return on, None


# ── REFUSALS ──────────────────────────────────────────────────────────────────────────────────
class Refused(Exception):
    """Nothing was written. `entries` is the body of the 422 in spec §4.1: [{check, detail}]."""

    def __init__(self, entries, status: int = 422):
        self.entries = list(entries)
        self.status = status
        super().__init__("; ".join(f"{e['check']}: {e['detail']}" for e in self.entries))


def _e(check: str, detail: str) -> dict:
    return {"check": check, "detail": detail}


def _one(check: str, detail: str, status: int = 422) -> Refused:
    return Refused([_e(check, detail)], status)


def _bp(pattern):
    """A blueprint id or the blueprint itself — the callers have both."""
    return wl_blueprint.find(pattern) if isinstance(pattern, str) else pattern


def _need(host, *names):
    """A host that cannot do what the request asks for is a refusal, not an AttributeError."""
    lack = [n for n in names if not hasattr(host, n)]
    if lack:
        raise _one("host", f"this caller cannot do that here — it offers no {', '.join(lack)}")


# ── HOLES → IDS ───────────────────────────────────────────────────────────────────────────────
def _new_texts(bp: dict, values: dict):
    """The designer's WORDS in the order they become string ids, plus how to put the ids back:
    (key, False) for a single string, (key, n) for a run of pages."""
    texts, keys = [], []
    for h in bp.get("holes") or []:
        val = values.get(h["key"])
        if h["kind"] == "text" and isinstance(val, str) and val.strip():
            keys.append((h["key"], False))
            texts.append(val.strip())
        elif h["kind"] == "pages" and isinstance(val, list) and any(str(x).strip() for x in val):
            pages = [str(x).strip() for x in val if str(x).strip()]
            keys.append((h["key"], len(pages)))
            texts += pages
    return texts, keys


def _append_strings(raw: bytes, side: int, area_id: int, texts: list):
    """Append new strings to the area's pool → (new blob, [ids]).

    This is the step that FAILS, and it has to fail before anything else is written: a text id is
    seven bits, and twelve shipped areas already have none free. Refusing here costs the designer a
    sentence; refusing halfway leaves an area pointing at a message that does not exist."""
    if not texts:
        return raw, []
    import wl_text5_encode as tenc
    ir = wl_ir.record_to_ir(raw, side, area_id)
    strings = list(ir["strings"])
    slot = wl_area.slot_for_area(area_id)
    base = (raw[slot] | (raw[slot + 1] << 8)) - RAM
    ids = []
    for t in texts:
        if len(strings) > 0x7F:
            raise _one("text-budget",
                       f"this area has no free text id — its pool already holds {len(strings)} "
                       f"strings, and a record can only name 0..127. Say it in an existing "
                       f"message, or put this step elsewhere.")
        ids.append(len(strings))
        strings.append(str(t))
    bad = tenc.unencodable(raw[base:base + 62], strings)
    if bad:
        # name the character, not its code — the commonest offender is an em dash or a curly quote
        # a word processor put there, and "—" is a fixable sentence while "$2014" is not
        chars = ", ".join(f"“{chr(b)}”" if 32 <= b < 0x11000 else f"${b:02X}" for b in bad)
        raise _one("text-encoding", f"this area cannot write these characters: {chars}. "
                                    f"Use plain -, ' and \" instead.")
    ir["strings"] = strings
    return wl_ir.ir_to_record(ir), ids


def _normalise(bp: dict, values: dict) -> dict:
    """The shapes `wl_blueprint._params` reads, from the shapes a caller sends.

    A foe arrives as `{"id": 12, …}` from the mask but as a bare `12` from anything typed by hand
    (the spec's own example does it), and `_params` calls `.get` on it. One normalisation here
    beats an AttributeError two layers down."""
    v = dict(values)
    for h in bp.get("holes") or []:
        val = v.get(h["key"])
        if h["kind"] == "foe" and isinstance(val, (int, str)) and str(val).strip():
            v[h["key"]] = {"id": int(val)}
        elif h["kind"] == "area" and isinstance(val, (int, str)) and str(val).strip():
            v[h["key"]] = {"target": int(val)}
        elif h["kind"] == "service_kind" and isinstance(val, (int, str)) and str(val).strip():
            v[h["key"]] = {"selector": int(val)}
    return v


def _resolve_holes(raw: bytes, side: int, area_id: int, bp: dict, values: dict):
    """The designer's words and picks → the ids a record needs. Returns (blob, values).

    The order is load-bearing: strings first (they are what can fail), then foes (they grow two
    parallel tables), then — outside this function — the records."""
    v = dict(values)
    texts, keys = _new_texts(bp, v)
    raw, ids = _append_strings(raw, side, area_id, texts)
    at = 0
    for key, many in keys:
        if many is False:
            v[key] = ids[at]
            at += 1
        else:
            v[key] = ids[at:at + many]
            at += many
    for h in bp.get("holes") or []:                # a foe that does not exist yet
        f = v.get(h["key"])
        if h["kind"] != "foe" or not isinstance(f, dict) or f.get("id"):
            continue
        try:
            raw, res = wl_cell.add_foe(bytes(raw), side, area_id,
                                       {"name": f["name"], "portrait_str": int(f.get("portrait", 0)),
                                        "glyph_tile": int(f.get("glyph", 2)),
                                        **({"template": f["template"]} if f.get("template") else {})})
        except ValueError as ex:
            raise _one("foe", str(ex))
        f["id"] = res.get("foe_id") or res.get("id")
    return raw, v


# ── PLAN → RECORDS ────────────────────────────────────────────────────────────────────────────
def _emit_plan(raw: bytes, side: int, area_id: int, bp: dict, values: dict, col, row):
    """Emit a filled blueprint as real records. Returns (blob, made) — `made` in PLAY order.

    Written LAST FIRST, because a record can only point at one that already has an id: the fight's
    victory has to name the sack, so the sack is written first. `link` says how the hand-over is
    made — `advance` replaces the cell's own state (the plain shipped shape), `patch` plants the
    next beat on ANOTHER square through a CELL_PATCH, `door` is an op-A transition's tail."""
    steps = wl_blueprint.plan(bp, values)
    area = wl_area.load_area(raw, side, area_id)
    made, by_role = [], {}

    def emit(op, params, ir_patch=None):
        """One record into the blob, through the one emitter. `ir_patch` reaches the few fields the
        flat params cannot name — an op-A door's TAIL is the only one so far, and it is the whole
        mechanism behind every shop in the game."""
        nonlocal area
        try:
            ir_rec = wl_cell.op_record_ir(op, params)
            if ir_patch:
                ir_rec.update(ir_patch)
            rec = wl_ir._emit_op_record(ir_rec)
        except (ValueError, KeyError) as ex:
            raise _one("record", f"could not build the ${op:X} record: {ex}")
        buf = bytearray(area.raw)
        act = [area.cellid[i] for i in range(len(area.nibble)) if area.nibble[i] == op]
        rid = wl_cell.add_record(buf, area.slot, op, rec, act)
        # the blob GREW and the pointer array moved with it, so the Area is stale from here on
        area = wl_area.load_area(bytes(buf), side, area_id)
        return rid

    for st in steps:
        p, ir_patch = dict(st["params"]), None
        nxt = by_role.get(st["needs"]) if st["needs"] else None
        link = st.get("link")
        if nxt and link == "patch":
            # the second wave has to stand somewhere the player can SEE, so it cannot simply replace
            # this square: clear this one, plant the next one a square north. Both entries belong to
            # ONE hop, which is why the coordinate exists once instead of twice. The patch goes in
            # FIRST so this record can point at it — nothing is ever rewritten afterwards.
            dx, dy = (col if col is not None else 0), (row if row is not None else 0)
            pid = emit(0xC, {"message": 0, "patches": [
                {"dx": dx, "dy": dy, "op": 0, "cell_id": 0},
                {"dx": dx, "dy": max(0, dy - 1), "op": nxt["op"], "cell_id": nxt["id"]}]})
            made.append({"role": st["role"] + " → moves the square", "op": 0xC, "id": pid})
            nxt = {"op": 0xC, "id": pid}
            link = "advance"
        if nxt and link == "advance":
            key = {0x3: ("victory_op", "victory_id"), 0x5: ("advance_op", "advance_id"),
                   0x1: ("next_op", "next_id"), 0x2: ("done_op", "done_id"),
                   0x4: ("next_op", "next_id"), 0xB: ("next_op", "next_id")}.get(st["op"])
            if key:
                p[key[0]], p[key[1]] = nxt["op"], nxt["id"]
            else:
                # every op that can hand over has a field for it; one that does not would drop the
                # link silently, and a beat pointing nowhere is exactly the failure that looks like
                # the editor working
                raise _one("link", f"an op-${st['op']:X} {st['role']} beat has no advance field to "
                                   f"hand over with (§5.1)")
        elif nxt and link == "door":
            # an op-A transition reaches a service through its TAIL, not through an advance — that
            # is why the twelve TREX services were invisible to a walker that only followed advances
            #
            # AND IT IS NOT AN ORDINARY DOOR. Measured over all four sides: 34 service doors, and
            # all 34 carry b0 & $C0 == $C0 with x = y = 0 and target = their own area. Bit 7 means
            # party-relative (SERVICE_HANDLERS.md, handler $89A3), so X/Y are DELTAS and 0,0 is
            # "stay put" — which is what a shop door does: it opens the shop, it does not move you.
            #
            # `op_record_ir` writes b0 = $40 + message, bit 7 clear, because that is right for the
            # other 477 transitions (112 of the 589 set it; it is a real per-record choice out
            # there). Left alone, this door would take the pattern's defaults x=1, y=1, target=0
            # as ABSOLUTE and walk the party into area 0 instead of into the shop.
            ir_patch = {"next": {"op": nxt["op"], "id": nxt["id"]},
                        "b0": 0xC0 | (int(p.get("message", 0)) & 0x3F),
                        "x": 0, "y": 0, "target": area_id}
        new_id = emit(st["op"], p, ir_patch)
        by_role[st["role"]] = {"op": st["op"], "id": new_id}
        made.append({"role": st["role"], "op": st["op"], "id": new_id})
    return bytes(area.raw), list(reversed(made))   # written last = the first beat the player meets


# ── BLOCK ─────────────────────────────────────────────────────────────────────────────────────
def _stamp_block(host, area, side: int, area_id: int, spec, col, row):
    """Set the block first: its tiles, its physics, its holes. Returns everything the commit needs.

    Nothing is written here either. A block carries the 36 bytes of every tile it uses, so a
    foreign tileset is a question rather than a refusal — reuse what the destination already has,
    write the rest into slots NOTHING points at, and refuse when there are none (forking the
    tileset is a deliberate, destructive decision and stays with /api/block/place)."""
    import wl_block
    _need(host, "block", "gfx", "gfx_free_slots", "map", "save_map", "save_gfx")
    bid = spec if isinstance(spec, str) else spec.get("id")
    blk = host.block(bid)
    if blk is None:
        raise _one("block", f"no block {bid!r}")
    x = (spec.get("x") if isinstance(spec, dict) else None)
    y = (spec.get("y") if isinstance(spec, dict) else None)
    own_cell = x is None and y is None      # the block eats col/row → the pattern goes in a hole
    if own_cell:
        x, y = col, row
    if x is None or y is None:
        raise _one("block", "a block needs a square to sit on — give col/row, or block.x/block.y")

    gid = area.raw[area.slot + 0x30] if area.slot + 0x30 < len(area.raw) else 0
    dst = host.gfx(gid)
    if not dst:
        raise _one("block", f"no gfx blob for tileset {gid}")
    free = host.gfx_free_slots(gid)
    r = wl_block.resolve(blk, dst, free)
    if r["short"]:
        raise _one("block-tiles", f"tileset {gid} is {r['short']} slots short for this block — "
                                  f"give this area a tileset of its own first (block place, fork)")
    map_raw = host.map(side, area_id)
    if not map_raw:
        raise _one("block", "no map blob to draw this block into")
    tiles = wl_area.load_map_tileids(map_raw, area.width, area.height)
    res = wl_block.place(area, tiles, blk, x, y, r["map"])
    if "error" in res:                            # place() reports, it does not raise
        raise _one("block", res["error"])

    nib, cid = list(area.nibble), list(area.cellid)
    for o in res["ops"]:
        nib[o["y"] * area.width + o["x"]] = o["op"]
        cid[o["y"] * area.width + o["x"]] = o["id"]
    gfx_out = dst
    for w in r["writes"]:
        gfx_out = wl_block.put_art(gfx_out, w["tile"], w["art"])
    return {"record": wl_area.save_area(area, nib, cid),
            "map": wl_area.save_map_tileids(map_raw, res["tiles"], area.width, area.height),
            "gfx": (gid, gfx_out) if r["writes"] else None,
            "holes": res["holes"], "at": [x, y], "id": blk["id"], "own_cell": own_cell,
            "tiles_added": [w["tile"] for w in r["writes"]]}


# ── COMBAT KIT ────────────────────────────────────────────────────────────────────────────────
# The three things an area needs before a fight can be READ, spec §8 — every one of them reversed
# in `cart_EF/docs/engine/COMBAT_TEXT.md`, and every one of them paid for: TREX Base was built
# without any of it and its first fight printed `and ugRUSTED SENTRYss` / `hits 1 RUSTED SENTRYshnhw`.
#
#   1  the 34-string COMBAT VOCABULARY at the tail of the area's own pool (7 weapon verbs, 3 hit
#      heads, 24 damage fragments)
#   2  the DISPATCH TABLE at `desc+$37..+$5B` — 37 ids, entry 0 is the block base, the first ten run
#      consecutively, the rest repeat ids where a fragment is shared. It is NOT reachable as an
#      ordinary string id: several areas place the block above 127 (s2a3 at 156) and a text ref is
#      seven bits, which is exactly why the table exists
#   3  a NAME for the foe that is fighting
#
# Measured over all 43 shipped areas: 40 carry the block, and the 3 that do not (s2a5, s3a11,
# s4a13) have zero foes and zero encounter cells — no shipped area can host a fight without it. So
# this refuses nothing the game itself does.
COMBAT_BLOCK = 34               # strings in the vocabulary block
COMBAT_TABLE = 37               # entries in desc+$37..+$5B


def _combat_msgs(ir: dict) -> list:
    """`combat_msgs` as a list of ids — the IR carries it as a list, older caches as hex."""
    cm = ir.get("combat_msgs")
    if isinstance(cm, str):
        return list(wl_ir._unhex(cm))
    return list(cm or [])


def _combat_kit(ir: dict) -> list:
    """What is missing before this area can host a fight, as sentences. Empty = it is equipped."""
    cm, strings = _combat_msgs(ir), (ir.get("strings") or [])
    if len(cm) < COMBAT_TABLE:
        return [f"this area has no combat message table — `desc+$37` holds {len(cm)} of the "
                f"{COMBAT_TABLE} ids the engine indexes into, so a fight would print whatever "
                f"happens to sit there"]
    out, base = [], cm[0]
    if not base:
        out.append("`desc+$37` is 0, so the combat message table points at string 0 — the engine "
                   "would read the area's first message as the attack verb")
    elif base + COMBAT_BLOCK > len(strings):
        out.append(f"the {COMBAT_BLOCK}-string combat block is not in this pool: `desc+$37` says it "
                   f"starts at {base} and the pool holds {len(strings)} strings. Append the block "
                   f"first (Combat text, the Texts tab), or put the fight in an area that has one")
    over = sorted({v for v in cm if v >= len(strings)})
    if over and base + COMBAT_BLOCK <= len(strings):
        out.append(f"the combat message table points past the end of the pool ({len(strings)} "
                   f"strings): {', '.join(str(v) for v in over[:6])}"
                   f"{'…' if len(over) > 6 else ''}. Anything that renumbers strings has to "
                   f"renumber `desc+$37..+$5B` with them")
    return out


def _hole_cell(holes: list, op: int):
    """Which of the block's holes this pattern goes into: the one that wants what it is.

    A block lays every door, service and script square down as op 0 and says so (`wl_block.place`),
    because a half-wired op-$A dispatches whatever record happens to sit at that index."""
    want = {0xA: "door", 0x6: "service"}.get(op, "script")
    for role in (want, "script", "door", "service"):
        for h in holes:
            if h["role"] == role:
                return h["x"], h["y"]
    return None, None


# ── THE ITEM-SPEND TRAP ───────────────────────────────────────────────────────────────────────
# A condition with op 1 (and 5/6/7, the same walk) does not TEST for an item, it SPENDS it: the
# cond-walk finds `arg` in a member's pack and calls $14A5, which reads the slot's count and
#
#     lda ($66),y / and #$3F / beq W14C4      <- zero: JUMP PAST the decrement
#     sec / sbc #$01                          <- otherwise: one fewer
#
# So whether the check costs you the item is a property of the ITEM, not of the check — and the
# item table says which: byte 4 of `$3108 + id*8` is the count an item is created with. That makes
# a gate on a consumable a legitimate design (Konzept §7: "wer sicher wegnehmen will, gibt dem Item
# Zählerstand 1"), and a gate on a quest TOKEN with a count a bug that plays for a while and then
# stops — which is the trap Spec §8 refuses.
ITEM_CONDS = (1, 5, 6, 7)


def _spend_trap(host, holes: list, values: dict):
    """(refusals, warnings) for every `have(item)` this placement would author.

    A declared quest MARKER with a count is refused: the marker is the whole point of the quest and
    it would be eaten by its own gate. Any other consumable is a warning, because taking the item
    is a thing the shipped game does on purpose (the Rope, the Matches, the Shovel)."""
    if not hasattr(host, "items"):
        return [], []
    tested = []
    for h in holes:
        c = values.get(h["key"])
        if h["kind"] != "check" or not isinstance(c, dict) or c.get("arg") is None:
            continue
        if (int(c.get("op") or 0) & 7) in ITEM_CONDS:
            tested.append(int(c["arg"]))
    if not tested:
        return [], []
    counts = {r["id"]: r.get("charges") for r in (host.items() or []) if isinstance(r, dict)}
    tokens = wl_questlog.marker_items(host.quests() or {}) if hasattr(host, "quests") else set()
    bad, warn = [], []
    for iid in tested:
        n = counts.get(iid)
        if not n:                                  # 0 or unknown — unknown says nothing, loudly
            continue
        name = wl_cell._item_name(iid) if iid in wl_cell._ITEM_NAMES else f"#{iid}"
        uses = f"{n} use{'' if n == 1 else 's'}"
        if iid in tokens:
            bad.append(_e("item-spend",
                          f"{name} is a quest marker and is created with {uses}, so testing for it "
                          f"spends one — after {n} test{'' if n == 1 else 's'} the marker is gone. "
                          f"A token has to be an item the table creates at count 0."))
        else:
            warn.append(f"testing for {name} takes one of its {uses} — that is how the game takes "
                        f"a Rope off you. An item created at count 0 is kept.")
    return bad, warn


# ── CHECKS ────────────────────────────────────────────────────────────────────────────────────
def _checks(host, bp: dict, values: dict, area, ir: dict, col, row, quest):
    """Everything that can refuse, collected — spec §8, the `place` rows. Raises `Refused` with ALL
    the reasons at once, because fixing them one 422 at a time is how a designer gives up.

    Returns (bind_role, warnings)."""
    bad, warn = [], []
    beats = bp.get("beats") or []
    holes = bp.get("holes") or []

    if col is not None and row is not None and not (0 <= col < area.width and 0 <= row < area.height):
        bad.append(_e("cell", f"({col},{row}) is outside this {area.width}x{area.height} map"))
    elif col is not None and row is not None:
        # A square holds ONE [op-nibble][cell-id] pair AT A TIME — not for the whole game. 1881 op-C
        # CELL_PATCH entries across 40 areas rewrite squares as the story runs, which is exactly how
        # WL1 gives one square several beats: the shovel scene at s1a08 patches its own cell the
        # moment the shovel changes hands.
        #
        # So occupancy is not the question. The question is whether the record being displaced is
        # still REACHED afterwards — by another square, by a follow-pointer (op-B next, op-2
        # done/alt), or by a patch entry that puts it back. `group_referenced_ids` answers exactly
        # that, so the test is: blank this one square and ask again. If the answer is no, the
        # placement silently orphans a record, and silent is the part worth refusing over.
        i = row * area.width + col
        op0, id0 = area.nibble[i], area.cellid[i]
        if 0 < op0 < 0x10:
            probe = dataclasses.replace(area, nibble=list(area.nibble), cellid=list(area.cellid))
            probe.nibble[i] = 0
            probe.cellid[i] = 0
            if id0 not in wl_cell.group_referenced_ids(probe, op0):
                rr = ((ir.get("groups") or {}).get(str(op0)) or {}).get(str(id0)) or {}
                who = f" ({rr['ref']})" if rr.get("ref") else ""
                what = f"{rr['op']} " if rr.get("op") else ""
                bad.append(_e("cell-orphans",
                              f"({col},{row}) is the ONLY way to the {what}record grp{op0}[{id0}]"
                              f"{who}, and placing here would take it. Nothing else reaches it — no "
                              f"other square, no chain, no patch. Two beats on one square are fine, "
                              f"but they have to be joined: hand over from the record already here, "
                              f"or add a patch that switches the square between them."))

    bind_role = None
    if quest:
        bind_role, err = resolve_binding(bp, quest.get("on"))
        if err:
            bad.append(_e("bind-carrier", err))
    if quest and quest.get("effect"):
        # a binding is written as a QUESTLOG ADDRESS, never as a slug — the slug is author comfort
        q = (host.quests() or {}).get(quest.get("slug")) if hasattr(host, "quests") else None
        if q is None:
            bad.append(_e("bind-quest", f"there is no quest {quest.get('slug')!r} to bind to"))
        elif q.get("qid") is None:
            bad.append(_e("bind-qid", f"quest {quest.get('slug')!r} has no qid yet, and a binding "
                                      f"is written as a questlog address"))

    # A shop, a doctor and a library GREET; only a talker (selector 9) speaks in pages. The shape
    # that reaches them — `door-to-service` — declares a `pages` hole and no `greeting` one, so a
    # store placed through it takes `greeting 0` and says whatever string 0 in that area happens to
    # say. Nothing refuses, because the value is reachable (`values.greeting`) and adding a required
    # hole would break the talker this shape places today; but silence about it is how the wrong
    # sentence ends up in somebody's shop. Spec §5.5 of `blueprints/Shop.SPLIT.md`.
    if any(b["op"] == 0x6 for b in beats):
        sel = int((values.get("service_kind") or {}).get("selector", 9))
        if sel != 9 and not values.get("greeting"):
            warn.append(f"a selector-{sel} service greets with a message and this shape has no "
                        f"greeting hole yet — it will open with string 0 of that area. Pass "
                        f"`values.greeting` (a string id), or place a talker instead")

    # carrying capacity: a portrait only means anything on an ENCOUNTER or a SERVICE record
    if any(h["kind"] == "portrait" for h in holes) and not any(b["op"] in (0x3, 0x6) for b in beats):
        bad.append(_e("carrier", "this shape asks for a portrait but has no beat that can show one "
                                 "— only an ENCOUNTER and a SERVICE carry a picture"))

    for i, b in enumerate(beats[:-1]):
        nxt = beats[i + 1]
        if b.get("link") == "advance" and (b["op"], nxt["op"]) in ZERO_EDGES:
            bad.append(_e("edge-census", f"{b['role']} → {nxt['role']} is an op-${b['op']:X} handing"
                                         f" over to an op-${nxt['op']:X}, which the shipped game "
                                         f"does 0× in 43 areas"))
        if b.get("link") == "patch" and (row or 0) == 0:
            warn.append("the second wave would be planted on the square it was cleared from — "
                        "a CELL_PATCH puts it one row NORTH, and there is no row north of 0")

    foes = ir.get("foes") or []
    for h in holes:
        f = values.get(h["key"])
        if h["kind"] != "foe" or not isinstance(f, dict):
            continue
        if f.get("id"):
            fid = int(f["id"])
            if not 1 <= fid <= len(foes):
                bad.append(_e("combat-kit", f"there is no foe {fid} in this area — it has "
                                            f"{len(foes)}, and the foe table is what gives a fight "
                                            f"its name and its stats"))
            elif not str(foes[fid - 1].get("name") or "").strip():
                # a nameless foe is not a cosmetic fault: the announcement goes out and the machine
                # sits there, because the name table is what the combat line splices in (§4)
                bad.append(_e("combat-kit", f"foe {fid} in this area has no name — a fight against "
                                            f"it has nothing to splice into its combat lines"))
        elif not f.get("name"):
            bad.append(_e("combat-kit", "a new foe needs a name"))

    # …and the area's own combat equipment, once per placement rather than once per foe hole
    if any(b["op"] == 0x3 for b in beats):
        for line in _combat_kit(ir):
            bad.append(_e("combat-kit", line))

    refuse, tell = _spend_trap(host, holes, values)
    bad += refuse
    warn += tell

    texts, _ = _new_texts(bp, values)
    free = wl_story.TEXT_ID_MAX - len(ir.get("strings") or [])
    if len(texts) > free:
        bad.append(_e("text-budget", f"this needs {len(texts)} new string(s) and the area has "
                                     f"{max(0, free)} free — a text id is seven bits"))
    elif texts and free - len(texts) < 8:
        warn.append(f"{free - len(texts)} text ids left in this area after this")

    if bad:
        raise Refused(bad)
    return bind_role, warn


# ── THE QUEST NOTE ────────────────────────────────────────────────────────────────────────────
def _note_quest(host, quest: dict, bp: dict, values: dict, side: int, area_id: int, col, row,
                made: list, bind_role):
    """Step 6 — write down what was placed, in the quest that asked for it (spec §2.4, form C).

    Only quest-BOUND beats are noted. A questfree beat is complete without an entry anywhere: the
    records are in the area, and the area is the store (E2)."""
    doc = host.quests()
    slug = quest.get("slug")
    q = doc.get(slug)
    if q is None:
        raise _one("quest", f"there is no quest {slug!r}")
    step = {"kind": "beat", "phase": "placed",
            "blueprint": bp.get("id"), "values": values,
            "at": ({"side": side, "area": area_id, "col": col, "row": row}
                   if col is not None and row is not None else {"side": side, "area": area_id}),
            "records": [f"grp{m['op']}[{m['id']}]" for m in made]}
    if quest.get("title"):
        step["title"] = quest["title"]
    if quest.get("note"):
        step["note"] = quest["note"]
    if quest.get("effect"):
        step["bind"] = {"effect": quest["effect"], "to": quest.get("to"), "on": bind_role}
    steps = q.setdefault("steps", [])
    idx = quest.get("step")
    if isinstance(idx, int) and 0 <= idx < len(steps):
        # MERGE, never replace. Placing an existing step re-derives what the placement knows —
        # phase, blueprint, values, at, records — and knows nothing about everything else the step
        # carries: the `gates` the designer typed on it, a `conds` role-split, a 1988 `note`, an
        # `id` that another step's `next` points at. Rebuilding the dict threw all of that away
        # silently, and the checks that read it (wl_qvalidate `_gates_of`, `trigger`, `_step_name`)
        # simply stopped firing on exactly the beats that were finished.
        old = steps[idx] if isinstance(steps[idx], dict) else {}
        step = {**old, **step}
        # …except what the OLD placement derived and this one supersedes. A `ref`/`placed` left
        # over from an earlier square would go on naming a record these bytes just replaced;
        # `_quest_step_alias` writes both again from what was actually made.
        step.pop("ref", None)
        step.pop("placed", None)
        steps[idx] = step
    else:
        idx = len(steps)
        steps.append(step)
    return doc, {"slug": slug, "step": idx}


# ── THE ONE COMPILE PATH ──────────────────────────────────────────────────────────────────────
def place(host, side: int, area_id: int, blueprint, values: dict = None, col=None, row=None,
          block=None, quest: dict = None, commit: bool = True) -> dict:
    """Compile a blueprint onto a square. THIS is where bytes first exist.

    The order is spec §7 and is not negotiable: resolve, check, block, records, cell, quest,
    warnings. Steps 3–6 are the writing half and only run once 1–2 hold.

    Returns the §4.1 body on success, or `{"refused": [{check, detail}], "status": 422}` — no
    exception, because the HTTP status is app.py's business and this module is also called from
    tests and, later, from the notation parser."""
    try:
        return _place(host, side, area_id, blueprint, dict(values or {}), col, row, block,
                      quest, commit)
    except Refused as ref:
        return {"refused": ref.entries, "status": ref.status}


def _place(host, side, area_id, blueprint, values, col, row, block, quest, commit):
    # ── 1 · resolve ───────────────────────────────────────────────────────────────────────────
    bp = _bp(blueprint)
    if bp is None:
        raise _one("blueprint", f"no blueprint {blueprint!r} — patterns and services come from "
                                f"wl_blueprint, a building comes from /api/blueprints")
    if not bp.get("beats"):
        # SERVICES carry holes but no beats, so `plan()` would return [] and the placement would
        # produce nothing at all. A shop is not placed on its own — it is reached through a door.
        raise _one("blueprint", f"{bp.get('id')} is a service, and a service is only ever reached "
                                f"through a door — place `door-to-service` and pick the kind of "
                                f"place in its service_kind hole")
    values = _normalise(bp, values)
    gaps = wl_blueprint.missing(bp, values)
    if gaps:
        labels = {h["key"]: h.get("label") or h["key"] for h in bp.get("holes") or []}
        raise Refused([_e("values-missing", f"{k} — {labels.get(k, k)}") for k in gaps])

    raw = raw0 = host.record(side, area_id)
    if not raw:
        raise _one("area", f"no record blob for side {side} area {area_id}")
    try:
        area = wl_area.load_area(raw, side, area_id)
    except ValueError as ex:
        raise _one("area", str(ex))
    ir = host.ir(side, area_id) if hasattr(host, "ir") else wl_ir.record_to_ir(raw, side, area_id)
    if quest and quest.get("slug"):
        _need(host, "quests", "save_quests")

    # ── 2 · everything that can refuse ────────────────────────────────────────────────────────
    bind_role, warnings = _checks(host, bp, values, area, ir, col, row, quest)

    # ── 3 · the block, if one was named ───────────────────────────────────────────────────────
    stamped, map_out, gfx_out = None, None, None
    if block:
        stamped = _stamp_block(host, area, side, area_id, block, col, row)
        raw, map_out, gfx_out = stamped["record"], stamped["map"], stamped["gfx"]
        area = wl_area.load_area(raw, side, area_id)
        if stamped["own_cell"]:
            # the block took col/row as its own corner, so the pattern goes into the hole the block
            # left for it — that is what "then the pattern in its hole" means (§4.1)
            col, row = _hole_cell(stamped["holes"], bp["beats"][0]["op"])
            if col is None:
                warnings.append(f"{stamped['id']} left no door, service or script square, so this "
                                f"went in unbound — nothing on the map dispatches it yet")

    # ── 4 · the records ──────────────────────────────────────────────────────────────────────
    # what the quest note keeps: the picks as the designer made them, words and all, so the step
    # stays readable and re-placeable. `_resolve_holes` works on a copy and turns them into ids.
    words = values
    raw, values = _resolve_holes(raw, side, area_id, bp, values)
    out, made = _emit_plan(raw, side, area_id, bp, values, col, row)

    # ── 5 · the square ───────────────────────────────────────────────────────────────────────
    area = wl_area.load_area(out, side, area_id)
    nib, cid = list(area.nibble), list(area.cellid)
    head = made[0]
    cell = None
    if col is not None and row is not None:
        if not (0 <= col < area.width and 0 <= row < area.height):
            raise _one("cell", "that square is outside the map")
        nib[row * area.width + col] = head["op"]
        cid[row * area.width + col] = head["id"]
        cell = {"col": col, "row": row, "op": head["op"], "id": head["id"]}
    else:
        warnings.append("placed unbound — the records exist and have ids, but no square dispatches "
                        "them yet")
    out = wl_area.save_area(area, nib, cid)

    # every record must resolve through the engine's own formula before any of this is written
    chk = wl_area.load_area(out, side, area_id)
    for m in made:
        if wl_cell.subptr(chk.raw, chk.slot, m["op"], m["id"]) is None:
            raise _one("round-trip", f"the {m['role']} record did not round-trip; nothing saved")
    if len(out) > wl_persist.CAP:
        raise _one("record-cap", f"this area would be {len(out)} bytes and the window is "
                                 f"{wl_persist.CAP}")

    # ── 6 · the quest note ───────────────────────────────────────────────────────────────────
    qdoc, qref = None, None
    if quest and quest.get("slug"):
        # `host.quests()` hands out a fresh document every call, so this mutates nothing until
        # `save_quests` is asked for it — which is after the record has been written, or never
        qdoc, qref = _note_quest(host, quest, bp, words, side, area_id, col, row, made, bind_role)
    if quest and quest.get("effect"):
        # This used to raise 501 and throw the WHOLE placement away. That was right while the byte
        # contract was undefined — a binding would have compiled into a record nothing could read.
        # It is wrong now: QUESTLOG_SPEC §5 defines both verbs down to the byte, the step model
        # stores a binding perfectly well, and refusing the records because ONE field cannot be
        # emitted yet loses the other four that can.
        #
        # So the beat is placed, the binding is written into the quest beside it, and the one thing
        # that is still missing says so instead of failing. When the engine learns condition kind 7
        # and the flash<->party sync, re-placing the beat compiles the binding; nothing authored has
        # to be touched, because the binding names a QUEST, not an address.
        warnings.append("the quest binding is authored but NOT compiled — the engine has no "
                        "questlog read/write path yet (QUESTLOG_SPEC §5 defines the bytes it will "
                        "need). The records are placed and the binding is stored; place the beat "
                        "again once the engine side lands and it will be emitted.")

    # ── 7 · commit and report ────────────────────────────────────────────────────────────────
    saved = []
    if commit:
        if hasattr(host, "undo"):
            host.undo(side, area_id)
        try:
            # the record goes FIRST: it is the only write that can still refuse (the serializer
            # rebuilds the layout and verifies every dispatchable record), so map and tileset are
            # untouched when it does
            saved.append(host.save_record(side, area_id, out))
        except ValueError as ex:
            raise _one("record-save", str(ex))
        if map_out is not None:
            saved.append(host.save_map(side, area_id, map_out))
        if gfx_out is not None:
            saved.append(host.save_gfx(gfx_out[0], gfx_out[1]))
        if qdoc is not None:
            host.save_quests(qdoc)

    res = {"records": [{"grp": m["op"], "cid": m["id"], "role": m["role"]} for m in made],
           "cell": cell, "warnings": warnings, "saved": saved,
           "size_delta": len(out) - len(raw0)}
    if qref:
        res["quest"] = qref
    if stamped:
        res["block"] = {"id": stamped["id"], "at": stamped["at"], "holes": stamped["holes"],
                        "tiles_added": stamped["tiles_added"]}
    if not commit:
        res["dry_run"] = True
    return res
