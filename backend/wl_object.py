"""wl_object.py — OBJECT and BLUEPRINT: a map region as a self-contained, re-usable thing.

Alex's model, in his words:

    Objects   = Map + Tiles + Script,  and  script = OP & asset (portrait / service / grp16)
    Blueprint = 1..n objects bundled

That model is right, and this module is what it costs to make it true.

AN OBJECT IS NOT A BYTE RANGE. A shop is not "the bytes from here to there" — its record sits in a
group array, its text sits in the area's string pool, its picture sits in a tileset shared with other
areas, and its foe sits in two parallel tables somewhere else again. What holds it together is a
REFERENCE GRAPH. So an object is defined as a REACHABILITY SET: roots = the selected map cells,
edges = every reference a record carries, leaves = the assets those references name.

Two things then have to be decided, and both are decisions, not derivations:

  THE CUT      Follow every edge and you drag in half the area — a shop's dialogue reaches a keyword
               which reaches a common refusal line which twenty other cells also use. So each reached
               record is classified: OWNED when every reference to it starts inside the object,
               SHARED when anything outside also points at it. Owned records travel; shared ones
               become a REQUIREMENT the paste reports instead of silently duplicating.
               (The engine's own structural reference — grp15 is walked over [0, desc+$32) whatever
               else is true — does not count as an outside owner: that reference exists in the target
               area just the same, so counting it would make an encounter slot unownable.)

  THE REMAP    "Asset" is not one namespace. It is nine, with nine different paste rules, and getting
               one of them wrong is silent:

                   group record id   area-local     renumber into the target's free slots
                   text index        area-local     append the string (or reuse an identical one)
                   tile id           tileset-local  import the pixels, then renumber
                   foe id            area-local     append to BOTH parallel tables at once
                   grp17 NPC index   area-local     append; the nibble that names it is 4 bits wide
                   grp16 event id    area-local     NOT carried — see below
                   PATCH cell x/y    area-local     ABSOLUTE coordinates: translate by the origin
                   portrait str id   GLOBAL         never renumber; require the blob to exist
                   item id           GLOBAL         never renumber
                   area id           GLOBAL         never renumber (a door out of the object)

Both directions of that table run through ONE function, `visit()`. Harvesting a reference and
rewriting it are the same walk with a different callback, because this session's recurring bug was
two implementations of one rule drifting apart — and the copy is always the one nobody tests.

GRP16 IS NOT CARRIED. Its handlers are reached through a jump table indexed by event id, and the code
inside references records by ABSOLUTE ADDRESS — so renumbering an object's record ids on paste would
leave those references aimed at the old slots. Relocating the code is solved (wl_reloc); rebinding
hand-written addresses to renumbered records is not. An object containing an EVENT therefore travels
with a stated requirement instead of a silent half-copy.

    obj = cut(raw, side, area, cells, gfx, map_bytes, name="Shop")
    res = paste(raw2, side2, area2, obj, col, row, gfx2, map_bytes2)
    bp  = bundle([obj, obj2], name="Trading post")
    res = place(raw2, side2, area2, bp, col, row, gfx2, map_bytes2)

`_object_test.py` states the invariant: cut(paste(obj)) == obj once local ids are canonicalised.
That exercises all nine maps at once — if any of them drops, mis-orders or aliases a reference, the
second cut cannot reproduce the first.
"""
from __future__ import annotations

import copy

import wl_area
import wl_cell
import wl_grp16
import wl_ir
import wl_tiles

from wl_machine import RECORD_BASE as RAM   # $3400 on the C64, $D000 on the Apple
FORMAT = 1
CAP = 9728                       # the $3400..$5A00 record window (wl_persist.CAP)


class NoRoom(ValueError):
    """The target has no free slot of some kind left — tiles, or addressable string ids.

    Like TooBig this is a capacity fact about the TARGET, not a defect, so it is its own exception:
    the caller can say which resource ran out and offer the real remedy (compact the tileset, drop
    unused strings) instead of reporting a bug.
    """


class TooBig(ValueError):
    """The paste would push the record past the 9728-byte window.

    Not a defect: four of the 43 shipped areas already sit at the cap, so an object simply does not
    fit there. It is its own exception so a caller can offer the real remedy — compact the record,
    drop unused strings, or place the object in a different area — instead of reporting a bug.
    """


# ─────────────────────────────── the reference table ───────────────────────────────

def _s8(b):
    return b - 256 if b >= 0x80 else b


def visit(rr: dict, fn) -> dict:
    """Visit every reference in a typed IR record. `fn(kind, value) -> value`.

    THE one reference table. Kinds and their value shapes:

        "rec"      (grp, cid)      a group array slot
        "text"     index           a string in the area's pool
        "tile"     id              a tile in the area's tileset
        "foe"      id              a row of the parallel foe tables (1-based; 0 = empty slot)
        "npc"      index           a grp17 hireable (0 = none; only 4 bits are available)
        "event"    id              a grp16 jump-table entry
        "cell"     (x, y)          an ABSOLUTE map coordinate written by a CELL_PATCH entry
        "item"     id              an engine item (global)
        "area"     id              a transition target (global)

    Returns a NEW record; `rr` is never touched. A callback returning its value unchanged makes this
    a harvester, one returning a mapped value makes it a rewriter — same walk either way.
    """
    r = copy.deepcopy(rr)
    op = r.get("op")

    def adv(d):
        # 0 < op: an advance of op $0 is "this cell becomes EMPTY", not a reference into group 0 —
        # there is no group 0 (wl_ir._ref_ops rejects it the same way). Remapping it would rewrite a
        # cell-state write as if it named a record.
        if d and 0 < d.get("op", 0xFF) < 0x10:
            g, c = fn("rec", (d["op"], d["id"]))
            d["op"], d["id"] = g, c

    def txt(key):
        if isinstance(r.get(key), int):
            r[key] = (r[key] & 0x80) | (fn("text", r[key] & 0x7F) & 0x7F)

    if op == "SAY":
        # A SAY is a RUN of string ids, not one id: bytes continue until bit7 marks the last. Every
        # byte in that run is a text reference — Scott's Bar tells you the THANATOS password in the
        # fourth string of a four-string say, and the Stagecoach Inn gives up MUERTE the same way.
        # Rewriting only byte0 left the rest pointing at the SOURCE area's pool after a paste.
        r["messages"] = [fn("text", int(i) & 0x7F) & 0x7F for i in (r.get("messages") or [0])]
        adv(r.get("branch"))
    elif op == "BUMP":
        txt("msg")                                   # bit7 is a flag byte, not part of the id
        adv(r.get("next"))
    elif op == "LOOT":
        adv(r.get("advance"))
        for it in r.get("items", []):
            if "qty16" in it:                        # money: [$5E|bit7][lo][hi] — not an item id
                continue
            it["item"] = (it["item"] & 0x80) | (fn("item", it["item"] & 0x7F) & 0x7F)
    elif op == "ENCOUNTER":
        txt("msg")
        for sl in r.get("slots", []):
            if sl.get("foe"):                        # 0 = the slot is empty, not foe zero
                sl["foe"] = fn("foe", sl["foe"]) & 0x7F
        f = r.get("flags") or {}
        if f.get("npc"):
            f["npc"] = fn("npc", f["npc"]) & 0x0F
        adv(r.get("victory"))
    elif op == "OBJ":
        # the tile callback has always seen the FULL byte, so a SOLID object's reference is
        # $80|tile and falls through a tilemap keyed 0..95 untouched. Splitting it here would start
        # renumbering the 27 shipped solid objects on paste — a different bug, not a rename.
        if "tile" in r:
            v = fn("tile", (r["tile"] & 0x7F) | (0x80 if r.get("solid") else 0)) & 0xFF
            r["tile"], r["solid"] = v & 0x7F, bool(v & 0x80)
        else:
            d = bytearray(wl_ir._unhex(r.get("data", "hex:")))
            if len(d) > 1:
                d[1] = fn("tile", d[1]) & 0xFF
            r["data"] = wl_ir._hex(bytes(d))
        adv(r.get("use"))
    elif op == "HAZARD":
        txt("msg")
    elif op == "COND":
        for k in ("prompt", "success", "fail"):
            txt(k)
        adv(r.get("done"))
        adv(r.get("alt"))
        for br in r.get("branches", []):
            adv(br)
    elif op == "PATCH":
        txt("msg")
        for e in r.get("entries", []):
            # flags bit0 = PARTY-RELATIVE: dx/dy are a signed offset from wherever the party stands,
            # so they survive a move untouched. Everything else is an ABSOLUTE cell coordinate and
            # must be translated, or a pasted object rewrites whatever now sits at the SOURCE
            # coordinates. wl_cell.patch_targets is the reader that proves the semantics.
            if not (e.get("flags", 0) & 1):
                x, y = fn("cell", (_s8(e["dx"]), _s8(e["dy"])))
                e["dx"], e["dy"] = x & 0xFF, y & 0xFF
            if 0 < e.get("op", 0xFF) < 0x10:
                g, c = fn("rec", (e["op"], e["id"]))
                e["op"], e["id"] = g, c
        adv(r.get("after"))
    elif op == "TRANSITION":
        r["target"] = fn("area", r.get("target", 0)) & 0xFF
        adv(r.get("next"))          # a plain door's follow-op is the single byte $FF/$FD/$FE, and
                                    # adv() already rejects those (and op $0 = "cell goes empty")
        if r.get("next") is None and r.get("tail") is not None:
            # the pre-rename shape, still on disk in editor/blueprints. Without this arm refs_of()
            # returns no record edge, so renormalize() calls the door's own service unreachable and
            # DELETES it from the document — one save in the blueprint workbench and the shop is gone.
            tail = bytearray(wl_ir._unhex(r["tail"]))
            j = 0
            while j + 1 < len(tail):
                if 0 < tail[j] < 0x10:
                    g, c = fn("rec", (tail[j], tail[j + 1]))
                    tail[j], tail[j + 1] = g & 0xFF, c & 0xFF
                j += 2              # a plain door's tail is the single byte $FF — no pair to rewrite
            r["tail"] = wl_ir._hex(bytes(tail))
    elif op == "KEYWORD":
        txt("prompt")
        for kw in r.get("keywords", []):
            kw["str"] = fn("text", kw["str"] & 0x7F) & 0x7F   # bit7 = "last keyword", re-set on emit
            adv(kw.get("branch"))
        adv(r.get("after"))
    elif op == "EVENT":
        ev = fn("event", r.get("event", 0)) & 0xFF
        r["event"] = ev                              # `args` is read only by that area's own grp16
        b = bytearray(wl_ir._unhex(r.get("record", "hex:")))   # handler, so it holds no portable ref
        if b:                                        # legacy document: byte0 IS the event id
            b[0] = ev
            r["record"] = wl_ir._hex(bytes(b))
    # SERVICE keeps its portrait at a family-dependent offset inside `tail`, and that id is GLOBAL —
    # nothing to renumber. It is harvested as a requirement by service_portrait() below, which reads
    # the id out of the record itself. HIREABLE / RAW / DANGLE hold no references at all.
    return r


def refs_of(rr: dict) -> list:
    """[(kind, value) …] — every reference in one record, in visit order."""
    out = []

    def fn(kind, val):
        out.append((kind, val))
        return val
    visit(rr, fn)
    return out


# ─────────────────────────────── the reference graph ───────────────────────────────

def _lookup(ir, g, cid):
    """(typed record, opaque hex) for one array slot — exactly one of the two is not None."""
    rr = ir["groups"].get(str(g), {}).get(str(cid))
    if rr is not None:
        return rr, None
    return None, ir.get("opaque", {}).get(str(g), {}).get(str(cid))


def ref_graph(raw, side, area_id, ir=None):
    """{(grp, cid): {origin …}} — who references each record.

    origin = ("cell", grid index) | ("rec", grp, cid) | ("engine", what)

    This is `wl_ir._ref_ops` with the origins kept. _ref_ops answers "is this reachable", which is
    what a decode needs; an object needs "reachable FROM WHERE", because that is the whole basis of
    the owned/shared cut.

    Door tails are NOT seeded separately here (they are in _ref_ops): a door's tail is an edge of the
    op-A TRANSITION record, and `visit` walks it as one. Seeding it as a nameless engine reference
    would mark every shop and infirmary as shared and no service could ever belong to an object.
    """
    a = wl_area.load_area(raw, side, area_id)
    ir = ir or wl_ir.record_to_ir(raw, side, area_id)
    src, seeds, seen = {}, [], set()
    for i, op in enumerate(a.nibble[:a.width * a.height]):
        if 0 < op < 0x10:
            seeds.append(((op, a.cellid[i]), ("cell", i)))
    while seeds:
        key, origin = seeds.pop()
        g, cid = key
        if not (0 < g < 19):
            continue
        src.setdefault(key, set()).add(origin)
        if key in seen:
            continue
        seen.add(key)
        rr, _ = _lookup(ir, g, cid)
        if rr is None:
            continue
        for kind, val in refs_of(rr):
            if kind == "rec":
                seeds.append((tuple(val), ("rec", g, cid)))
            elif kind == "npc" and val:
                # an encounter's hire nibble IS a reference to grp17[n] — it just does not travel as
                # an [op][id] pair. Missing it left every hireable NPC unreachable, so no object ever
                # carried one and `requires` claimed an NPC it had in fact taken along.
                seeds.append(((17, val), ("rec", g, cid)))
    # ENGINE-driven references. grp15 is walked over [0, desc+$32) no matter what points at it.
    n15 = (ir.get("descriptor") or {}).get("encounter_slot_count") or 0
    for cid in range(n15):
        src.setdefault((15, cid), set()).add(("engine", "grp15-walk"))
    return src


_STRUCTURAL = {("engine", "grp15-walk")}


def _owned(key, sources, inside_cells, inside_recs) -> bool:
    """True when every reference to `key` starts inside the object."""
    for o in sources:
        if o in _STRUCTURAL:
            continue
        if o[0] == "cell" and o[1] in inside_cells:
            continue
        if o[0] == "rec" and (o[1], o[2]) in inside_recs:
            continue
        return False
    return True


# ─────────────────────────────── CUT ───────────────────────────────

def cut(raw, side, area_id, cells, gfx=None, map_bytes=None, name="object", ir=None) -> dict:
    """Cut a map region out as a self-contained OBJECT.

    `cells` = [(col, row) …]. Returns the object document (JSON-serialisable). The source area is
    NOT modified — a cut is a copy; removing the original is a separate, explicit act.
    """
    a = wl_area.load_area(raw, side, area_id)
    W, H = a.width, a.height
    ir = ir or wl_ir.record_to_ir(raw, side, area_id)
    cells = [(int(c), int(r)) for c, r in cells]
    if not cells:
        raise ValueError("an object needs at least one cell")
    for c, r in cells:
        if not (0 <= c < W and 0 <= r < H):
            raise ValueError(f"cell ({c},{r}) is outside the {W}x{H} map")
    c0, r0 = min(c for c, _ in cells), min(r for _, r in cells)
    idx = {r * W + c for c, r in cells}

    # ── reachability from the selected cells ──────────────────────────────────────────
    graph = ref_graph(raw, side, area_id, ir)
    queue = [(a.nibble[r * W + c], a.cellid[r * W + c]) for c, r in cells
             if 0 < a.nibble[r * W + c] < 0x10]
    reached = set()
    while queue:
        key = queue.pop()
        if key in reached or not (0 < key[0] < 19):
            continue
        rr, hexs = _lookup(ir, *key)
        if rr is None and hexs is None:
            continue
        reached.add(key)
        if rr is not None:
            edges = refs_of(rr)
            queue += [tuple(v) for k, v in edges if k == "rec"]
            queue += [(17, v) for k, v in edges if k == "npc" and v]   # the hire nibble is an edge

    # ── the CUT: owned travels, shared becomes a requirement ──────────────────────────
    owned = {k for k in reached if _owned(k, graph.get(k, set()), idx, reached)}
    # …but that tested against the WHOLE reached set, which still contains shared records. A record
    # reachable only THROUGH a shared one is not ours either, so shrink to a fixpoint. Without this
    # a private dialogue hanging off a shared keyword travelled while the keyword that reaches it
    # stayed behind — an object with no way in.
    while True:
        drop = {k for k in owned if not _owned(k, graph.get(k, set()), idx, owned)}
        if not drop:
            break
        owned -= drop
    shared = reached - owned

    obj = {"format": FORMAT, "name": name,
           "source": {"side": side, "area": area_id},
           "origin": {"col": c0, "row": r0},
           "size": {"w": max(c for c, _ in cells) - c0 + 1,
                    "h": max(r for _, r in cells) - r0 + 1},
           "cells": [], "records": {}, "opaque": {}, "text": {}, "tiles": {},
           "foes": {}, "npcs": {},
           "requires": {"records": [], "portraits": [], "areas": [], "items": [],
                        "grp16_events": [], "cells": []},
           "notes": []}

    tiles = wl_area.load_map_tileids(map_bytes, W, H) if map_bytes else None
    for c, r in sorted(cells, key=lambda p: (p[1], p[0])):
        i = r * W + c
        e = {"dc": c - c0, "dr": r - r0, "op": a.nibble[i], "cellid": a.cellid[i] & 0xFF}
        if tiles is not None:
            e["tile"] = tiles[i]
        obj["cells"].append(e)

    # ── harvest what the owned records name ───────────────────────────────────────────
    want = {"text": set(), "tile": set(), "foe": set(), "npc": set(), "event": set(),
            "item": set(), "area": set(), "cell": set()}
    for g, cid in sorted(owned):
        rr, hexs = _lookup(ir, g, cid)
        if rr is None:
            # OPAQUE: bytes whose format is not modelled, so no reference inside can be found — let
            # alone renumbered. Carried verbatim, and said out loud.
            obj["opaque"].setdefault(str(g), {})[str(cid)] = hexs
            obj["notes"].append(f"grp{g}[{cid}] travels as verbatim bytes — any reference inside it "
                                f"still names area {area_id}'s ids")
            continue
        if g == 17:                                  # grp17 records ARE the NPC objects; one home
            obj["npcs"][str(cid)] = copy.deepcopy(rr)
        else:
            obj["records"].setdefault(str(g), {})[str(cid)] = copy.deepcopy(rr)
        for kind, val in refs_of(rr):
            if kind == "rec":
                if tuple(val) in shared:
                    obj["requires"]["records"].append(f"grp{val[0]}[{val[1]}]")
            elif kind in want:
                want[kind].add(tuple(val) if kind == "cell" else val)

    # text — always carried by VALUE. A string is content: duplicating it costs bytes and never
    # breaks, whereas requiring the target to already hold the same INDEX would make every object
    # area-specific, which is the opposite of a blueprint.
    strings = ir.get("strings") or []
    for t in sorted(want["text"]):
        obj["text"][str(t)] = strings[t] if t < len(strings) else ""

    # tiles — the pictures under the selected cells AND any an OBJ record names
    for e in obj["cells"]:
        if "tile" in e:
            want["tile"].add(e["tile"])
    if gfx:
        for t in sorted(want["tile"]):
            if t >= wl_tiles.NUM_TILES:
                continue
            d = wl_tiles.get_tile(gfx, t)
            obj["tiles"][str(t)] = {"pixels": d["pixels"], "chars": d["chars"]}
    elif want["tile"]:
        obj["notes"].append("no tileset was supplied, so the object carries tile IDS but not their "
                            "pictures — it can only go into an area on the same tileset")

    # foes — name and 8-byte record travel TOGETHER: they are one object split across two arrays,
    # paired by POSITION, so carrying one without the other re-assigns every portrait after it.
    for fid in sorted(want["foe"]):
        f = ir["foes"][fid - 1] if 1 <= fid <= len(ir["foes"]) else None
        if f is None:
            obj["notes"].append(f"an encounter names foe {fid}, which the tables do not hold")
            continue
        obj["foes"][str(fid)] = {"name": f["name"], "record": f["record"]}
        if f.get("portrait_str"):
            obj["requires"]["portraits"].append(f["portrait_str"])

    # NPCs reached through an encounter's hire nibble but NOT owned → a requirement
    for n in sorted(want["npc"]):
        if str(n) not in obj["npcs"]:
            obj["requires"]["records"].append(f"grp17[{n}]")

    # A CELL that dispatches a record the object does not carry is an unbound reference too — and the
    # most consequential kind, because it is what the player walks into. A shared bump message is the
    # usual case: half the map says "The door is locked.", so grp11[3] belongs to nobody, the cell
    # travels with the id unchanged, and in the target area that id means something else entirely.
    for c, r in cells:
        op, cid = a.nibble[r * W + c], a.cellid[r * W + c] & 0xFF
        if 0 < op < 0x10 and (op, cid) not in owned:
            obj["requires"]["records"].append(f"grp{op}[{cid}]")

    # portraits a SERVICE shows. Read from the RECORD (service_portrait), not from wl_foe.services():
    # the two agree on all 35 shipped services, and this one also works on an object that has left
    # its area — which renormalize() needs after an edit. A STOCK service (families 0-3) is
    # deliberately not a requirement: its picture is a hardcoded immediate in the SHARED scene blob,
    # so it is present wherever that scene runs and the target has nothing to provide.
    for gs, recs in obj["records"].items():
        for rr in recs.values():
            p = service_portrait(rr)
            if p:
                obj["requires"]["portraits"].append(p)

    req = obj["requires"]
    req["areas"] = sorted({v for v in want["area"] if v and v != area_id})
    req["items"] = sorted(want["item"])
    req["grp16_events"] = sorted(want["event"])
    req["portraits"] = sorted(set(req["portraits"]))
    req["records"] = sorted(set(req["records"]))
    outside = sorted({(x, y) for x, y in want["cell"] if not (0 <= x < W and 0 <= y < H)
                      or (y * W + x) not in idx})
    req["cells"] = [[x - c0, y - r0] for x, y in outside]
    if outside:
        obj["notes"].append(f"{len(outside)} CELL_PATCH target(s) lie outside the selection. They "
                            "travel with the object, so they will rewrite whatever sits at the same "
                            "RELATIVE position in the target area")
    if want["event"]:
        obj["notes"].append(f"EVENT record(s) name grp16 handler(s) {sorted(want['event'])}. grp16 "
                            "CODE is not carried — it addresses records absolutely and cannot follow "
                            "a renumbering. The target area needs its own handler for those ids.")
    return obj


# A SERVICE record carries its portrait id at record +$13, which is `tail[0]` — but ONLY for the
# families whose header is a 4-byte field block plus an 11-char name. Families 0/1/2/3 have no such
# byte: 0 and 3 have an empty tail, 2 has a 12-char name, and 1 (the classic shop) starts its tail
# with the STOCK LIST. A stock service's picture is a hardcoded immediate in the shared scene blob
# instead (wl_foe.stock_portrait).
# Checked against wl_foe.services(), which reads it through _service_section's per-family field map:
# 35 of 35 shipped SERVICE records agree, 0 differ. This standalone version exists because an object
# has to answer "which portrait do I need" away from the area it was cut from.
_SERVICE_NO_PORTRAIT = frozenset({0, 1, 2, 3})


def service_portrait(rr):
    """The portrait str id a SERVICE record carries in its own bytes, or None."""
    if rr.get("op") != "SERVICE" or rr.get("selector") in _SERVICE_NO_PORTRAIT:
        return None
    if rr.get("portrait") is not None:                # families 5/6/7/8/9 name it; the frozenset
        return rr["portrait"]                         # above is now belt-and-braces, not the rule
    tail = wl_ir._unhex(rr.get("tail", "hex:"))       # legacy document, still the raw tail
    return tail[0] if tail else None


def renormalize(obj) -> dict:
    """Re-derive everything a hand edit can invalidate. Returns (object, what changed).

    The mini-editor lets a designer move, repaint and DELETE cells inside a blueprint. Any of those
    can leave the document incoherent: a record nothing reaches any more, a string nobody names, a
    tile picture for a tile no cell shows, a `requires` list describing the object as it was before
    the edit. Rather than forbid the edits, the document is settled after each one — same idea as
    the record serializer's layout fixpoint, one level up.

    Nothing here consults an AREA. An object is standalone by construction, so its own consistency
    has to be decidable from its own bytes.
    """
    o = copy.deepcopy(obj)
    was = {"records": sum(len(v) for v in o.get("records", {}).values())
                      + sum(len(v) for v in o.get("opaque", {}).values()),
           "npcs": len(o.get("npcs", {})), "text": len(o.get("text", {})),
           "tiles": len(o.get("tiles", {})), "foes": len(o.get("foes", {}))}

    # ── geometry: dedupe, re-origin to (0,0), re-measure ──────────────────────────────
    cells = {}
    for e in o.get("cells", []):
        cells[(int(e["dr"]), int(e["dc"]))] = e            # a later entry wins, by reading order
    if not cells:
        raise ValueError("a blueprint object needs at least one cell")
    dc0 = min(c for _, c in cells)
    dr0 = min(r for r, _ in cells)
    o["cells"] = []
    for (r, c) in sorted(cells):
        e = dict(cells[(r, c)])
        e["dc"], e["dr"] = c - dc0, r - dr0
        o["cells"].append(e)
    o["size"] = {"w": max(e["dc"] for e in o["cells"]) + 1,
                 "h": max(e["dr"] for e in o["cells"]) + 1}

    # ── reachability INSIDE the object: the cells are the only roots there are ────────
    carried = {(int(g), int(c)) for g, v in o.get("records", {}).items() for c in v}
    carried |= {(int(g), int(c)) for g, v in o.get("opaque", {}).items() for c in v}
    carried |= {(17, int(n)) for n in o.get("npcs", {})}

    def rec(key):
        g, c = key
        if g == 17:
            return o.get("npcs", {}).get(str(c))
        return o.get("records", {}).get(str(g), {}).get(str(c))

    keep, queue, needs = set(), [], set()

    def reach(k):
        """Follow it if we carry it, otherwise record it as something the paste has to bind."""
        (queue.append(k) if k in carried else needs.add(k))

    for e in o["cells"]:
        if 0 < e["op"] < 0x10:
            reach((e["op"], e["cellid"]))
    while queue:
        k = queue.pop()
        if k in keep:
            continue
        keep.add(k)
        rr = rec(k)
        if rr is None:
            continue                                  # opaque bytes: no readable reference inside
        for kind, val in refs_of(rr):
            if kind == "rec":
                reach(tuple(val))
            elif kind == "npc" and val:
                reach((17, val))

    dropped = sorted(carried - keep)
    o["records"] = {g: {c: rr for c, rr in v.items() if (int(g), int(c)) in keep}
                    for g, v in o.get("records", {}).items()}
    o["opaque"] = {g: {c: h for c, h in v.items() if (int(g), int(c)) in keep}
                   for g, v in o.get("opaque", {}).items()}
    o["npcs"] = {n: rr for n, rr in o.get("npcs", {}).items() if (17, int(n)) in keep}
    o["records"] = {g: v for g, v in o["records"].items() if v}
    o["opaque"] = {g: v for g, v in o["opaque"].items() if v}

    # ── what the survivors name ──────────────────────────────────────────────────────
    want = {"text": set(), "tile": set(), "foe": set(), "event": set(), "item": set(),
            "area": set(), "cell": set()}
    ports = set()
    for g, v in list(o["records"].items()) + [("17", o["npcs"])]:
        for cid, rr in v.items():
            for kind, val in refs_of(rr):
                if kind in want:
                    want[kind].add(tuple(val) if kind == "cell" else val)
            p = service_portrait(rr)
            if p:
                ports.add(p)
    for e in o["cells"]:
        if "tile" in e:
            want["tile"].add(e["tile"])

    o["text"] = {t: v for t, v in o.get("text", {}).items() if int(t) in want["text"]}
    o["tiles"] = {t: v for t, v in o.get("tiles", {}).items() if int(t) in want["tile"]}
    o["foes"] = {f: v for f, v in o.get("foes", {}).items() if int(f) in want["foe"]}
    for f in o["foes"].values():
        rec8 = wl_ir._unhex(f.get("record", "hex:"))
        if len(rec8) == 8 and rec8[7]:
            ports.add(rec8[7])

    src = (o.get("source") or {}).get("area")
    req = {"records": sorted({f"grp{g}[{c}]" for g, c in needs | set(dropped)}),
           "portraits": sorted(ports),
           "areas": sorted({v for v in want["area"] if v and v != src}),
           "items": sorted(want["item"]), "grp16_events": sorted(want["event"]),
           "cells": [[x - dc0, y - dr0] for x, y in sorted(want["cell"])
                     if not any(e["dc"] == x - dc0 and e["dr"] == y - dr0 for e in o["cells"])]}
    o["requires"] = req

    notes = []
    if dropped:
        notes.append(f"{len(dropped)} record(s) are no longer reached by any cell and were dropped: "
                     + ", ".join(f"grp{g}[{c}]" for g, c in dropped[:8]))
    if req["cells"]:
        notes.append(f"{len(req['cells'])} CELL_PATCH target(s) lie outside the object — they travel "
                     "with it and will rewrite whatever sits at the same relative position")
    if req["grp16_events"]:
        notes.append(f"EVENT record(s) name grp16 handler(s) {req['grp16_events']}. grp16 CODE is "
                     "not carried; the target area needs its own handler for those ids.")
    o["notes"] = notes
    now = {"records": sum(len(v) for v in o["records"].values())
                      + sum(len(v) for v in o["opaque"].values()),
           "npcs": len(o["npcs"]), "text": len(o["text"]),
           "tiles": len(o["tiles"]), "foes": len(o["foes"])}
    return o, {k: now[k] - was[k] for k in now if now[k] != was[k]}


# ─────────────────────────────── PASTE ───────────────────────────────

def _used_ids(ir, g) -> set:
    """Ids already taken in group `g` — including every group it shares an array with."""
    grps = {g}
    for _own, v in (ir.get("array_alias") or {}).items():
        if g in v:
            grps |= set(v)
    used = set()
    for x in grps:
        used |= {int(k) for k in ir["groups"].get(str(x), {})}
        used |= {int(k) for k in ir.get("opaque", {}).get(str(x), {})}
    return used


def _alloc(ir, g, taken) -> int:
    """The next free slot in group `g`. grp17 index 0 is the table header, never a record."""
    used = _used_ids(ir, g) | taken
    k = 1 if g == 17 else 0
    while k in used:
        k += 1
    return k


# A TEXT ID IS 7 BITS. SAY and BUMP keep the string id in byte0 with bit7 as a terminator/flag, and
# a KEYWORD entry marks its last keyword the same way — so those fields simply cannot name a string
# above 127. Measured over all 43 shipped areas: 5355 text references, NONE above 127, and the three
# highest land on exactly 127. Eleven areas hold a pool LONGER than 128, but no record reaches into
# the tail. So 128 is the ceiling for anything an object can carry, whatever the pool's length.
TEXT_MAX = 128


def _text_slots(ir, strings) -> list:
    """Addressable string slots the target is not using: index < 128, empty, referenced by nothing.

    They exist because build_region pads every group of 4 with empty strings, so a pool of 22 real
    strings really holds 24 addressable ids. Filling one of those is the only way to add a string to
    an area whose pool has already passed 128 — appending would put it out of reach of every op that
    could name it.
    """
    used = {0}                                       # id 0 means "no text" throughout the engine
    for recs in ir["groups"].values():
        for rr in recs.values():
            used |= {v for k, v in refs_of(rr) if k == "text"}
    return [i for i, x in enumerate(strings[:TEXT_MAX]) if not x and i not in used]


def _pic(gfx, t):
    d = wl_tiles.get_tile(gfx, t)
    return _norm((d["pixels"], d["chars"]))


def _norm(pic):
    px, ch = pic
    return (tuple(tuple(r) for r in px), tuple(tuple(sorted(c.items())) for c in ch))


def _free_tile(gfx, claimed):
    for t in range(wl_tiles.NUM_TILES):
        if t not in wl_tiles.FIXED and t not in claimed and wl_tiles.tile_empty(gfx, t):
            return t
    return None


def gid_hint(ir) -> str:
    """The tileset id from the descriptor tail, for a readable capacity message."""
    tail = wl_ir._unhex((ir.get("descriptor") or {}).get("tail", "hex:"))
    return str(tail[4]) if len(tail) > 4 else "?"    # tail starts at slot+$2C, gfx id sits at +$30


def grp16_events(ir) -> set:
    """Event ids the area's grp16 actually handles ($0000 in the jump table = unhandled)."""
    g = ir.get("grp16")
    if not g:
        return set()
    try:
        tbl = wl_grp16.jump_table(wl_ir._unhex(g["code"]), g["offset"] + RAM)
    except Exception:                                # noqa: BLE001
        return set()
    return {i for i, v in enumerate(tbl) if v}


def paste(raw, side, area_id, obj, col, row, gfx=None, map_bytes=None, ir=None) -> dict:
    """Place an OBJECT into an area. Returns {record, gfx, map, report}.

    `record` is the new blob; `gfx` / `map` are the new asset bytes when they changed, else None.
    Nothing is written to disk — the caller decides, and the record still goes through
    wl_persist.save_record, so the serializer and its engine-equivalence check have the last word.
    """
    a = wl_area.load_area(raw, side, area_id)
    W, H = a.width, a.height
    ir = copy.deepcopy(ir or wl_ir.record_to_ir(raw, side, area_id))
    report = {"records": {}, "text": {}, "tiles": {}, "foes": {}, "npcs": {}, "warnings": []}

    for e in obj["cells"]:
        c, r = col + e["dc"], row + e["dr"]
        if not (0 <= c < W and 0 <= r < H):
            raise ValueError(f"the object does not fit: cell ({c},{r}) is outside the {W}x{H} map")

    # ── 1. records: a target slot for every carried id ────────────────────────────────
    recmap, taken = {}, {}
    src = [(int(g), int(c)) for g, recs in obj.get("records", {}).items() for c in recs]
    src += [(int(g), int(c)) for g, recs in obj.get("opaque", {}).items() for c in recs]
    src += [(17, int(n)) for n in obj.get("npcs", {})]
    for g, cid in sorted(src):
        taken.setdefault(g, set())
        n = _alloc(ir, g, taken[g])
        taken[g].add(n)
        recmap[(g, cid)] = (g, n)
        report["records"][f"grp{g}[{cid}]"] = f"grp{g}[{n}]"
        if g == 17 and n > 15:
            raise ValueError(f"grp17 index {n} does not fit the 4-bit hire nibble of an encounter")

    # ── 2. text: reuse an identical string, else append ───────────────────────────────
    strings = list(ir.get("strings") or [])
    byval = {}
    for i, x in enumerate(strings[:TEXT_MAX]):       # only an ADDRESSABLE id can be reused
        byval.setdefault(x, i)
    spare = _text_slots(ir, strings)
    txtmap = {}
    for ts, x in sorted(obj.get("text", {}).items(), key=lambda kv: int(kv[0])):
        if x not in byval:
            if spare:
                i = spare.pop(0)                     # reuse an empty, unreferenced low slot
                strings[i] = x
            elif len(strings) < TEXT_MAX:
                strings.append(x)
                i = len(strings) - 1
            else:
                raise NoRoom(f"the target's string pool holds {len(strings)} strings with no free "
                             f"id below {TEXT_MAX}, and a string id is 7 bits — "
                             f"{x.strip()[:40]!r} cannot be addressed")
            byval[x] = i
        txtmap[int(ts)] = byval[x]
        report["text"][ts] = byval[x]
    ir["strings"] = strings

    # ── 3. tiles: an identical picture is reused, otherwise an empty slot is claimed ───
    tilemap, newgfx = {}, None
    if obj.get("tiles"):
        if not gfx:
            raise ValueError("the object carries tile pictures but no target tileset was supplied")
        g2 = bytes(gfx)
        have, claimed = {}, set()
        for t in range(wl_tiles.NUM_TILES):
            have.setdefault(_pic(g2, t), t)
        for ts, d in sorted(obj["tiles"].items(), key=lambda kv: int(kv[0])):
            key = _norm((d["pixels"], d["chars"]))
            match = have.get(key)
            if match is None or match in claimed:
                match = _free_tile(g2, claimed)
                if match is None:
                    raise NoRoom(f"tileset {gid_hint(ir)} has no empty tile left for the object's "
                                 f"tile {ts} — compact it or clear unused tiles first")
                g2 = wl_tiles.set_tile(g2, match, d["pixels"], d["chars"])
                have.setdefault(key, match)
            claimed.add(match)
            tilemap[int(ts)] = match
            report["tiles"][ts] = match
        newgfx = g2 if g2 != bytes(gfx) else None

    # ── 4. foes: name and record are ONE object across two parallel tables ────────────
    foemap = {}
    if obj.get("foes"):
        nb = bytearray(wl_ir._unhex(ir["foe_names_raw"]))
        fr = bytearray(wl_ir._unhex(ir["foe_records_raw"]))
        for fs, f in sorted(obj["foes"].items(), key=lambda kv: int(kv[0])):
            same = next((i + 1 for i, x in enumerate(ir["foes"])
                         if x["name"] == f["name"] and x["record"] == f["record"]), None)
            if same is not None:
                foemap[int(fs)] = same
                report["foes"][fs] = same
                continue
            n = len(ir["foes"]) + 1
            # SPLICE, never rebuild: the name table stores stem/singular/plural separated by $0A and
            # the display form is lossy, so re-encoding every entry would quietly rewrite the ones
            # already there. _names_true_end is where the READER stops — pad bytes can follow.
            end = wl_cell._names_true_end(nb, 0)
            nb[end:end] = b"\x00" + str(f["name"]).encode("ascii", "replace") + b"\n\n"
            rec8 = wl_ir._unhex(f["record"])[:8].ljust(8, b"\x00")
            at = n * 8                               # record 0 is the header, so ids are 1-based
            if len(fr) < at:
                fr += b"\x00" * (at - len(fr))
            fr[at:at] = rec8
            ir["foes"].append({"name": f["name"], "record": wl_ir._hex(rec8),
                               "portrait_str": rec8[7]})
            foemap[int(fs)] = n
            report["foes"][fs] = n
        ir["foe_names_raw"] = wl_ir._hex(bytes(nb))
        ir["foe_records_raw"] = wl_ir._hex(bytes(fr))

    # ── 5. rewrite every reference, then insert ───────────────────────────────────────
    dc = col - obj["origin"]["col"]
    dr = row - obj["origin"]["row"]
    ev_have = grp16_events(ir)

    def remap(kind, val):
        if kind == "rec":
            k = tuple(val)
            if k in recmap:
                return recmap[k]
            report["warnings"].append(f"grp{k[0]}[{k[1]}] is a REQUIRED record the object does not "
                                      "carry — it still names the source area's slot")
            return val
        if kind == "text":
            return txtmap.get(val, val)
        if kind == "tile":
            return tilemap.get(val, val)
        if kind == "foe":
            return foemap.get(val, val)
        if kind == "npc":
            return recmap.get((17, val), (17, val))[1]
        if kind == "cell":
            return (val[0] + dc, val[1] + dr)
        if kind == "event":
            if val not in ev_have:
                report["warnings"].append(f"EVENT {val} has no grp16 handler in this area — the "
                                          "jump-table entry is $0000, so nothing happens")
            return val
        return val                                   # item / area: global, never renumbered

    for gs, recs in obj.get("records", {}).items():
        for cs, rr in recs.items():
            _, n = recmap[(int(gs), int(cs))]
            ir["groups"].setdefault(gs, {})[str(n)] = visit(rr, remap)
    for gs, recs in obj.get("opaque", {}).items():
        for cs, hexs in recs.items():
            _, n = recmap[(int(gs), int(cs))]
            ir.setdefault("opaque", {}).setdefault(gs, {})[str(n)] = hexs
    for ns, rr in obj.get("npcs", {}).items():
        _, n = recmap[(17, int(ns))]
        ir["groups"].setdefault("17", {})[str(n)] = visit(rr, remap)

    # a group that was PARKED (descriptor word aimed at another section) now owns records
    for gs in list(ir.get("dead_ptrs", {})):
        if gs in ir["groups"] or gs in ir.get("opaque", {}):
            ir["dead_ptrs"].pop(gs, None)
    # grp15 is walked over [0, desc+$32): a new encounter slot only EXISTS if the count says so
    n15 = max([int(k) for k in ir["groups"].get("15", {})] or [-1]) + 1
    tail = bytearray(wl_ir._unhex(ir["descriptor"]["tail"]))
    if len(tail) > 6 and n15 > tail[6]:
        tail[6] = min(n15, 0xFF)                     # tail starts at slot+$2C, count sits at +$32
        ir["descriptor"]["tail"] = wl_ir._hex(bytes(tail))
        ir["descriptor"]["encounter_slot_count"] = tail[6]

    # ── 6. the cells themselves, and the tiles under them ─────────────────────────────
    grid_op, grid_id = list(ir["grid"]["op"]), list(ir["grid"]["cellid"])
    tiles = wl_area.load_map_tileids(map_bytes, W, H) if map_bytes else None
    for e in obj["cells"]:
        i = (row + e["dr"]) * W + (col + e["dc"])
        op, cid = e["op"], e["cellid"]
        if 0 < op < 0x10:
            if (op, cid) in recmap:
                cid = recmap[(op, cid)][1]
            else:
                report["warnings"].append(
                    f"cell +{e['dc']},+{e['dr']} dispatches grp{op}[{cid}], which the object does "
                    "not carry — it now names whatever this area holds at that id")
        grid_op[i], grid_id[i] = op & 0x0F, cid & 0xFF
        if tiles is not None and "tile" in e:
            tiles[i] = tilemap.get(e["tile"], e["tile"])
    ir["grid"]["op"], ir["grid"]["cellid"] = grid_op, grid_id

    blob = wl_ir.ir_to_record(ir)
    if len(blob) > CAP:
        raise TooBig(f"placing '{obj.get('name', 'the object')}' makes the record {len(blob)} B, "
                     f"{len(blob) - CAP} B over the {CAP} B window")
    seen = set()
    report["warnings"] = [w for w in report["warnings"] if not (w in seen or seen.add(w))]
    report["bytes"], report["delta"] = len(blob), len(blob) - len(raw)
    return {"record": blob, "gfx": newgfx,
            "map": (wl_area.save_map_tileids(map_bytes, tiles, W, H) if tiles is not None else None),
            "report": report}


# ─────────────────────────────── BLUEPRINT ───────────────────────────────

def renormalize_doc(doc) -> dict:
    """Settle a whole blueprint after an edit: every object re-derived, the bundle re-measured."""
    objs, changed = [], {}
    for o in doc.get("objects", [doc]):
        off = o.get("offset") or {"dc": 0, "dr": 0}
        n, ch = renormalize(o)
        n["offset"] = off
        for k, v in ch.items():
            changed[k] = changed.get(k, 0) + v
        objs.append(n)
    out = bundle([{**o, "origin": {"col": o.get("offset", {}).get("dc", 0),
                                   "row": o.get("offset", {}).get("dr", 0)}} for o in objs],
                 name=doc.get("name", "blueprint"), description=doc.get("description", ""))
    out["source"] = doc.get("source", out.get("source"))
    for a, b in zip(out["objects"], objs):           # bundle() re-derives offset from origin
        a["name"] = b.get("name", a.get("name"))
    return out, changed


def bundle(objects, name="blueprint", description="") -> dict:
    """1..n objects into ONE placeable blueprint.

    Each object keeps its OFFSET inside the bundle, so the geometry between them survives — that is
    the whole point of bundling rather than pasting each one by hand.
    """
    if not objects:
        raise ValueError("a blueprint needs at least one object")
    c0 = min(o["origin"]["col"] for o in objects)
    r0 = min(o["origin"]["row"] for o in objects)
    parts = []
    for o in objects:
        p = copy.deepcopy(o)
        p["offset"] = {"dc": o["origin"]["col"] - c0, "dr": o["origin"]["row"] - r0}
        parts.append(p)
    req = {}
    for o in objects:
        for k, v in (o.get("requires") or {}).items():
            for x in v:
                if x not in req.setdefault(k, []):
                    req[k].append(x)
    return {"kind": "wl-blueprint", "format": FORMAT, "name": name, "description": description,
            "size": {"w": max(o["origin"]["col"] - c0 + o["size"]["w"] for o in objects),
                     "h": max(o["origin"]["row"] - r0 + o["size"]["h"] for o in objects)},
            "objects": parts, "requires": req, "source": objects[0].get("source", {}),
            "notes": [n for o in objects for n in o.get("notes", [])]}


def place(raw, side, area_id, bp, col, row, gfx=None, map_bytes=None) -> dict:
    """Place a whole blueprint. Objects are applied in order, each against the result of the last, so
    a later object sees the ids and tiles the earlier one already claimed."""
    objs = bp["objects"] if bp.get("kind") == "wl-blueprint" else [bp]
    cur = bytes(raw)
    g = bytes(gfx) if gfx else None
    m = bytes(map_bytes) if map_bytes else None
    reports = []
    for o in objs:
        off = o.get("offset") or {"dc": 0, "dr": 0}
        res = paste(cur, side, area_id, o, col + off["dc"], row + off["dr"], g, m)
        cur = res["record"]
        g = res["gfx"] or g
        m = res["map"] or m
        reports.append({"name": o.get("name", "object"), **res["report"]})
    return {"record": cur,
            "gfx": (g if gfx is not None and g != bytes(gfx) else None),
            "map": (m if map_bytes is not None and m != bytes(map_bytes) else None),
            "report": {"objects": reports,
                       "bytes": len(cur), "delta": len(cur) - len(raw),
                       "warnings": [w for r in reports for w in r.get("warnings", [])]}}


def check(obj, gfx=None, portrait_exists=None) -> list:
    """[] when the object can be placed as-is; else what a paste will not be able to bind.

    `portrait_exists(str_id) -> bool` comes from the caller — the editor resolves str blobs across
    all four sides, and a portrait id is GLOBAL, so existence is not knowable from the record.
    """
    bad = []
    req = obj.get("requires") or {}
    src = (obj.get("source") or {}).get("area")
    for r in req.get("records", []):
        bad.append(f"{r} is referenced but not carried — after the paste it still names area "
                   f"{src}'s slot")
    if portrait_exists:
        for p in req.get("portraits", []):
            if not portrait_exists(p):
                bad.append(f"portrait str {p} does not exist — the face will not draw")
    if req.get("grp16_events"):
        bad.append(f"needs a grp16 handler for event(s) {req['grp16_events']}: that is area CODE and "
                   "is not carried")
    if obj.get("tiles") and gfx is not None:
        free = sum(1 for t in range(wl_tiles.NUM_TILES)
                   if t not in wl_tiles.FIXED and wl_tiles.tile_empty(gfx, t))
        if free < len(obj["tiles"]):
            bad.append(f"the object may need up to {len(obj['tiles'])} tiles, the target tileset has "
                       f"{free} empty")
    return bad


# ─────────────────────────── canonical form (what makes it testable) ───────────────────────────

def canon(obj) -> dict:
    """The object with every LOCAL id renumbered into order of appearance.

    Two objects are THE SAME object when their canonical forms match. That is what makes the
    cut/paste round-trip testable: the ids on either side of a paste are necessarily different, the
    STRUCTURE must not be.
    """
    o = copy.deepcopy(obj)
    c0, r0 = o["origin"]["col"], o["origin"]["row"]
    order = {"rec": {}, "text": {}, "tile": {}, "foe": {}, "npc": {}}

    def num(kind, val):
        d = order[kind]
        if val not in d:
            d[val] = len(d)
        return d[val]

    flat = {(int(g), int(c)): rr for g, recs in o.get("records", {}).items()
            for c, rr in recs.items()}
    flat.update({(17, int(n)): rr for n, rr in o.get("npcs", {}).items()})

    cells = sorted(o["cells"], key=lambda e: (e["dr"], e["dc"]))
    seq = []
    for e in cells:                                  # the cells introduce the ids, in reading order
        if 0 < e["op"] < 0x10:
            num("rec", (e["op"], e["cellid"]))
            seq.append((e["op"], e["cellid"]))
        if "tile" in e:
            num("tile", e["tile"])
    seen = set()
    while seq:
        key = seq.pop(0)
        if key in seen or key not in flat:
            continue
        seen.add(key)
        for kind, val in refs_of(flat[key]):
            if kind == "rec":
                num("rec", tuple(val))
                seq.append(tuple(val))
            elif kind in order:
                num(kind, tuple(val) if kind == "cell" else val)
    for key in sorted(flat):                         # anything unreachable still has to be stable
        num("rec", key)
    for n in sorted(o.get("npcs", {}), key=int):
        num("npc", int(n))
    for t in sorted(o.get("text", {}), key=int):
        num("text", int(t))
    for t in sorted(o.get("tiles", {}), key=int):
        num("tile", int(t))
    for f in sorted(o.get("foes", {}), key=int):
        num("foe", int(f))

    def fn(kind, val):
        if kind == "rec":
            return (val[0], num("rec", tuple(val)))  # the GROUP is part of the identity
        if kind == "cell":
            return (val[0] - c0, val[1] - r0)        # PATCH targets: relative to the object
        if kind in order:
            return num(kind, val)
        return val

    out = {"cells": [], "records": {}, "opaque": {}, "text": {}, "tiles": {},
           "foes": {}, "npcs": {}}
    for e in cells:
        n = dict(e)
        if 0 < e["op"] < 0x10 and (e["op"], e["cellid"]) in order["rec"]:
            n["cellid"] = order["rec"][(e["op"], e["cellid"])]
        if "tile" in e:
            n["tile"] = order["tile"].get(e["tile"], e["tile"])
        out["cells"].append(n)
    for (g, c), rr in sorted(flat.items()):
        home = out["npcs"] if g == 17 else out["records"]
        home[f"{g}:{order['rec'][(g, c)]}"] = _strip(visit(rr, fn))
    for gs, recs in o.get("opaque", {}).items():
        for cs, hexs in recs.items():
            out["opaque"][f"{gs}:{order['rec'].get((int(gs), int(cs)), int(cs))}"] = hexs
    for t, s in o.get("text", {}).items():
        out["text"][str(order["text"][int(t)])] = s
    for t, d in o.get("tiles", {}).items():
        out["tiles"][str(order["tile"][int(t)])] = d
    for f, d in o.get("foes", {}).items():
        out["foes"][str(order["foe"][int(f)])] = d
    return out


def _strip(rr):
    """Drop the fields a paste legitimately re-derives, so canon() compares CONTENT."""
    return {k: v for k, v in rr.items() if k not in ("payload",)}
