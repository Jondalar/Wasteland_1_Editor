"""wl_foe.py — FOE and NPC as OBJECTS, with the invariants that hold them together.

A foe is not one structure. It is a name in one table, an 8-byte record in a second table, a portrait
id inside that record, an encounter row that points at it, and — if it can be hired — a 256-byte
party-member record in a third table reached through a nibble in that encounter row. Five places, and
the only thing binding them is convention: "position k here belongs to position k there".

That is exactly why the serializer cannot help. `foe_names_raw` and `foe_records_raw` are carried
VERBATIM in the IR (the format of a name table with its own separators is not modelled), so a repack
preserves whatever is there — including two tables that have drifted out of step. Swap two entries
and every check in the project still passes, because the bytes are all still present and the record
still re-packs byte-identically. The player just meets a lizard called Dire Coyote.

So the binding has to be stated as an invariant and checked as one:

    F1  the name table and the record table are PARALLEL — record k belongs to name k-1
    F2  every foe an encounter references exists in both tables
    F3  every hireable nibble resolves to a real grp17 record
    F4  a foe with a portrait has a non-zero str id, and a foe without one is not drawn

`refs()` answers "is this foe used?" from the REFERENCE GRAPH, not from a scan of map cells. The
cell scan is what the editor showed until now, and it is wrong in 34 of 43 areas: an encounter
reached through a record edge, a door tail, or the engine-iterated grp15 table has no cell of its
own, so 100 of 279 live foes were displayed as unused template leftovers.
"""
import wl_cell
import wl_ir

from wl_machine import RECORD_BASE as RAM   # $3400 on the C64, $D000 on the Apple


def _foe_table(area):
    """(base offset, count) of the 8-byte foe-record table. Record 0 is a header, so ids are 1-based.

    The count is the NAME COUNT — a foe IS a name, and the record table is the parallel array. That
    is definitional, and the layout confirms it: in 41 of 43 areas the table ends exactly, to the
    byte, where the text region at desc+$00 begins (the adjacency the serializer preserves on emit).
    The two that differ are the blank template (no foes at all) and s3_id39, where something else
    sits between the table and the text — so adjacency is the CROSS-CHECK, not the definition.

    The old rule guessed the end from record CONTENT ("byte1 must be 0, byte7 must be non-zero") and
    was simply wrong: Base Cochise's first foe record is `00 7D 01 05 10 01 02 46`, so the scan
    stopped at zero records and the editor listed no foes at all for that area.
    """
    raw = area.raw
    base = wl_cell._le(raw, area.slot + 0x04) - RAM
    if not (0 <= base < len(raw)):
        return None, 0
    return base, len(wl_cell._foe_names(area))


def refs(area) -> dict:
    """{foe id: [(grp, cid) …]} — who references each foe, from the reference GRAPH.

    Not a scan of map cells: an encounter can be reached through a record edge, an op-A door tail or
    the engine's own grp15 walk, and those have no cell to find."""
    raw, slot = area.raw, area.slot
    out = {}
    # grp15 IS real and IS engine-walked — it is the wandering-monster free list, `+$32` slots of it
    # (wl_world). A guard added here on 2026-08-02 read `slot+$06+14*2`, which is group FOURTEEN:
    # the group pointer table is `slot+$06 + grp*2`, so 15 sits at +$24 and the guard was dropping
    # a group that exists in 39 of 43 areas. Left as a warning: the one indexing mistake this table
    # invites costs a whole group, silently, because the wrong slot is also a valid pointer.
    for grp, cid in wl_ir._ref_ops(raw, slot, area, wl_ir._door_targets(raw, slot, area)):
        if grp not in (0x3, 0xF):
            continue
        rec = wl_cell.subptr(raw, slot, grp, cid)
        if rec is None or rec + 7 >= len(raw):
            continue
        # ALL THREE SLOTS, not just the first. An encounter is [dist][dist][msg] then three
        # [foe][count] pairs, and a group can be mixed — reading only +$03 reported the foes in
        # slots two and three as though nothing spawned them.
        for k in range(3):
            fid = raw[rec + 3 + k * 2] & 0x7F
            if fid:
                out.setdefault(fid, []).append((grp, cid))
    return out


def hire_slots(area) -> dict:
    """{grp17 index: [(grp, cid) …]} — the hireable NPC each encounter row points at (byte+9 hi nibble)."""
    raw, slot = area.raw, area.slot
    out = {}
    for grp, cid in wl_ir._ref_ops(raw, slot, area, wl_ir._door_targets(raw, slot, area)):
        if grp not in (0x3, 0xF):
            continue
        rec = wl_cell.subptr(raw, slot, grp, cid)
        if rec is None or rec + 9 >= len(raw):
            continue
        n = raw[rec + 9] >> 4
        if n:
            out.setdefault(n, []).append((grp, cid))
    return out


def grp17_table(area) -> list:
    """The grp17 pointer table, sized by the highest index any reference uses (it has no length
    field). Derived from the reference graph for the same reason `refs` is."""
    g17 = wl_cell._le(area.raw, area.slot + 0x28)
    if not g17:
        return []
    base, used = g17 - RAM, hire_slots(area)
    top = max(used) if used else 0
    out = []
    for k in range(top + 1):
        o = base + k * 2
        out.append(wl_cell._le(area.raw, o) if 0 <= o + 1 < len(area.raw) else 0)
    return out


def foes(area, side: int = 0) -> list:
    """The composed FOE objects: name, portrait, glyph, who references them, hire slot."""
    raw = area.raw
    forms = wl_cell.foe_name_forms(area)
    names = [s.strip() for s, _ in forms]
    base, n = _foe_table(area)
    used, hires = refs(area), hire_slots(area)
    hire_of = {}
    for k, where in hires.items():
        for grp, cid in where:
            rec = wl_cell.subptr(raw, area.slot, grp, cid)
            if rec is not None and rec + 3 < len(raw):
                hire_of[raw[rec + 3] & 0x7F] = k
    out = []
    for fid in range(1, max(n, len(names)) + 1):
        r = base + fid * 8 if base is not None else None
        ok = r is not None and r + 7 < len(raw) and fid <= n
        out.append({
            "id": fid,
            "name": names[fid - 1] if fid - 1 < len(names) else None,
            # BOTH words, because the entry carries both and the engine picks by head-count. The
            # panel used to show — and post back — only stem+singular, which is how "Madman" read
            # as "Madma". FOE_RECORD.md / the $0A switch at ef_engine.asm $205E.
            "name_plural": forms[fid - 1][1].strip() if fid - 1 < len(forms) else None,
            "portrait_str": raw[r + 7] if ok else None,
            "glyph": raw[r + 6] if ok else None,
            "record": wl_ir._hex(raw[r:r + 8]) if ok else None,
            "used": fid in used,
            "referenced_by": [f"grp{g}[{c}]" for g, c in used.get(fid, [])],
            # the MAP CELLS that dispatch one of those encounters — so a click can land on the
            # thing that spawns this foe instead of on a portrait editor
            "cells": [[i % area.width, i // area.width]
                      for i, op in enumerate(area.nibble[:area.width * area.height])
                      if (op, area.cellid[i]) in set(used.get(fid, []))],
            "hire_slot": hire_of.get(fid),
            "side": side,
        })
    return out


def check(area) -> list:
    """[] when the foe/NPC object graph is internally consistent; else the broken invariants."""
    raw = area.raw
    names = wl_cell._foe_names(area)
    base, n = _foe_table(area)
    bad = []
    if base is None:
        return ["foe-record table pointer (desc+$04) is out of range"] if names else []
    text = wl_cell._le(raw, area.slot + 0x00) - RAM                                      # F1
    if n and base + (n + 1) * 8 > max(text, 0) and 0 <= text < len(raw) and text > base:
        bad.append(f"F1 {n} foe records from +{base} run past the text region at +{text} — the name "
                   "table and the record table are PARALLEL, so a length mismatch re-assigns every "
                   "portrait after the drift point")
    for fid, where in sorted(refs(area).items()):                                        # F2
        if fid == 0:
            continue
        if fid > n or fid > len(names):
            bad.append(f"F2 {', '.join(f'grp{g}[{c}]' for g, c in where)} references foe {fid}, "
                       f"but the tables hold {min(n, len(names))}")
    tbl = grp17_table(area)
    for k, where in sorted(hire_slots(area).items()):                                    # F3
        if k >= len(tbl) or not tbl[k] or not (0 <= tbl[k] - RAM < len(raw)):
            bad.append(f"F3 {', '.join(f'grp{g}[{c}]' for g, c in where)} offers hire slot {k}, "
                       "which resolves to no grp17 record")
    for fid in range(1, n + 1):                                                          # F4
        r = base + fid * 8
        if r + 7 < len(raw) and raw[r + 7] == 0:
            bad.append(f"F4 foe {fid} ({names[fid - 1] if fid - 1 < len(names) else '?'}) has no "
                       "portrait id")
    return bad


def stock_portrait(side: int, sel: int):
    """The portrait id a STOCK service shows, read out of its scene handler.

    A stock service (selectors $00/$01) keeps no portrait byte in its record — the id is a hardcoded
    IMMEDIATE in the shared scene blob:

        s1_scene_id00.bin +$15   A9 00 20 1C 04   lda #$00 / jsr $041C   -> str 0   Infirmary
        s1_scene_id01.bin +$1E   A9 01 20 1C 04   lda #$01 / jsr $041C   -> str 1   Store

    So it IS knowable, just not from the record — which is why the panel showed Highpool's doctor and
    shop as portrait-less tiles. `$041C` is draw_anim_object's entry (LOCATION_PICTURE.md §0 step 8);
    the first `lda #imm` feeding it is the picture. Returns None when the blob is unavailable or the
    pattern is absent, and the caller keeps the label-only tile.

    The blob is SHARED per scene id, so every area running this stock scene shows the same face.
    """
    import os
    import wl_cell
    repo = getattr(wl_cell, "_REPO", None)
    if repo is None:
        return None
    name = f"s{side}_scene_id{sel:02d}.bin"            # work copy first, then the pristine baseline
    for cand in (os.path.join(str(repo), "editor", "work", f"side{side}", name),
                 os.path.join(str(repo), "baseline", f"side{side}", name)):
        if os.path.isfile(cand):
            break
    else:
        return None
    try:
        with open(cand, "rb") as fh:
            b = fh.read()
    except OSError:
        return None
    for i in range(len(b) - 4):
        if b[i] == 0xA9 and b[i + 2] == 0x20 and b[i + 3] == 0x1C and b[i + 4] == 0x04:
            return b[i + 1]
    return None


def services(area, side: int = 0) -> list:
    """Every op-6 SERVICE record with its label and PORTRAIT — the third portrait source.

    The Data tab knew two: the foe-record table and grp17. A service carries its own portrait id
    (+$13 on the derived copies), and TREX Base is nothing BUT services — 12 of them holding the 12
    contiguous portraits str 79..90, none of which the panel could show.

    The decode is `wl_cell._service_section`, not a second reader: it already knows that the field
    offsets differ per family (store/doc/bank/revive/talk, SERVICE_HANDLERS.md), and this session's
    whole lesson is that a second walker is a bug waiting for a re-pack.
    """
    import wl_cell
    raw = area.raw
    out = []
    if wl_cell.group_is_alias(raw, area.slot, 6):
        return out                                 # filler pointer — those bytes are another group's
    for cid in range(wl_cell.group_slot_count(raw, area.slot, 6)):
        rec = wl_cell.subptr(raw, area.slot, 6, cid)
        if rec is None or rec >= len(raw) or not (raw[rec] & 0x80):
            continue                                   # bit7 clear = an EVENT (grp16), not a service
        try:
            rows = dict(wl_cell._service_section(area, rec, RAM + rec)["rows"])
        except Exception:                              # noqa: BLE001
            continue
        p = rows.get("portrait", "")
        pid = int(p.split()[-1]) if isinstance(p, str) and p.split()[-1:] and p.split()[-1].isdigit() \
            else None
        # A STOCK service (selector 0/1, the 1988 originals) has no portrait id in the record at
        # all — the image is baked into the scene blob. That is not a missing field, so the note
        # travels with the entry and the UI can say why there is no thumbnail.
        note = None
        if pid is None:                                # stock: dig the id out of the scene handler
            pid = stock_portrait(side, raw[rec] & 0x7F)
            note = str(p) + (" — read from the scene handler" if pid is not None else "")
        out.append({"cid": cid, "kind": rows.get("kind", "service"),
                    "label": rows.get("sign / label") or f"service {cid}",
                    "portrait_str": pid, "side": side, "portrait_note": note,
                    "stock": bool(note)})
    return out


def orphan_hires(area, side: int = 0) -> list:
    """grp17 member records that EXIST but no encounter offers — nothing spawns them.

    Not a defect on its own (the engine can be sent to one by grp16 code or a save-state), but the
    NPC panel lists NPCs by their marker cell, so an unreferenced record was simply invisible.
    TREX Base has exactly one.
    """
    import wl_cell
    tbl, used = grp17_table(area), set(hire_slots(area))
    top = max(len(tbl), wl_cell.group_slot_count(area.raw, area.slot, 17))
    out = []
    for n in range(1, top):
        o = wl_cell.subptr(area.raw, area.slot, 17, n)
        if o is None or n in used or not (0 <= o < len(area.raw)):
            continue
        name = bytes(area.raw[o:o + 14]).split(b"\x00")[0].decode("ascii", "replace").strip()
        if not name or not all(32 <= ord(c) < 127 for c in name):
            continue                                   # not a member record — a parked pointer
        out.append({"n": n, "name": name, "side": side})
    return out


# The five KINDs of byte 6. The names are wastelib's `MobType` (docs/REF_WASTELIB.md §3), adopted after our own 400 records
# agreed with them and against the labels that stood here before. Three of the five had been guessed
# from too small a sample, and the data says so plainly:
#
#   4 was "robot"   — but it holds Clawman, Tronodile, Cyclon, Centron, Cyborg, Hexborg: CYBORG
#   5 was "turret"  — 82 records, mostly mobile: Silicon Sniper, Steel Reaver, VTOL Auto-fire
#                     Robot, Laser Pod Blaster. Turrets are in there, they are not what it is
#   2 was "undead"  — Desert Dweller, Shambler Ghoul, Lost Soul, Pit Ghoul. Ghouls are MUTANTs
#                     in this world; "undead" was a genre guess, not a reading
#
# The tile stays measured, not borrowed: it is a CONSEQUENCE of the kind, not a field of its own.
# The engine draws $5BF5[kind] on the encounter cell (W95EC), names an unnamed foe with engine
# message kind + $52 (FOE_NAME_PRINT), and the AI reads the same byte for its approach — 1 goes
# through the melee gate, 4 and 5 take the two ranged bands. So picking a "map figure" was picking
# a behaviour. Counts below are over all 43 shipped areas.
FOE_KINDS = {
    1: {"tile": 6, "name": "animal",
        "note": "59 recs — beasts. Bunny, Rat, Coydog, Dire Coyote, Gila Monitor. Melee gate."},
    2: {"tile": 3, "name": "mutant",
        "note": "7 recs — Desert Dweller, Shambler Ghoul, Lost Soul, Pit Ghoul, Night Terror."},
    3: {"tile": 4, "name": "humanoid",
        "note": "220 recs — people. Gunman, Guard, Jerk, Cutthroat, Marksman. The common case."},
    4: {"tile": 2, "name": "cyborg",
        "note": "32 recs — flesh and machine. Clawman, Tronodile, Cyclon, Centron, Hexborg."},
    5: {"tile": 1, "name": "robot",
        "note": "82 recs — Silicon Sniper, Steel Reaver, VTOL Auto-fire Robot, Xenon Laser Cannon."},
}


def stats(rec8: bytes) -> dict:
    """Every byte of the 8-byte foe record, in the terms a designer thinks in.

    RE'd 2026-08-03 from the MAIN overlay (docs/engine/FOE_RECORD.md). The record is reached as
    `($16)` after W9E9F/W9EA4 resolve it — base = descriptor +$04, stride 8 (`$4C`) — and every
    field below has a call site:

      +0/+1  CON, 16-BIT (W9EE6 returns A=+0, X=+1). The spawn builder rolls
             `HP = (CON >> 2) + rnd(CON)` as a 16-bit pair (W9459/W9488/W948C), and the AI's morale
             check compares the group's remaining strength against CON >> 1. The old model read
             byte 0 alone, which made Night Terror (+0 = 0, +1 = 125 -> 32000) a 0 HP foe and
             Finster Leviathan a 176 HP one. Killing it pays CON experience.
      +2     RANGE — added to the kind's band and compared with the distance to the target
             ($84B5 path). Melee kinds sit at 1, turrets at 30; the two agree across all 400.
      +3     damage DICE, each a d6 ($24D6 = rnd & 7, reject >= 6, +1) — X into DICE_SUM.
      +4 lo  ARMOUR. WA7F4 subtracts it from the attacker's dice: fewer dice than armour and the
             hit does nothing at all, otherwise the roll is `2*dice - armour`.
      +4 hi  max GROUP SIZE — the wandering-encounter raiser rolls `rnd(1..hi)` into the grp15
             slot's count ($B045). The 13 records with hi = 0 are the fixed NPCs (Fat Freddy,
             Irwin John Finster, Casino Guard) that no random encounter may ever raise.
      +5 lo  ATTACK TYPE — indexes WB0EB / WB0F8 / $5B93 and ABILITY_RANGE_TIER; the same id space
             the party's weapons use ($7E), which is why `find_first_match` accepts both.
      +5 hi  damage BASE — the constant DICE_SUM starts from.
      +6     KIND (see FOE_KINDS) — map tile, default name, and melee/ranged AI in one byte.
      +7     PORTRAIT — $5B00[+7] -> $CA, the anim object the combat screen shows.
    """
    b = list(rec8) + [0] * (8 - len(rec8))
    con = b[0] | (b[1] << 8)
    dice, base, armour = b[3], b[5] >> 4, b[4] & 0x0F
    kind = FOE_KINDS.get(b[6], {})
    return {"con": con, "exp": con,
            "hp_min": (con >> 2) + 1, "hp_max": min(0xFFFF, (con >> 2) + con),
            "range": b[2], "dice": dice, "dmg_base": base,
            # each die is a d6, so the spread is fixed by the two numbers the roll uses
            "dmg_min": base + dice, "dmg_max": min(0xFF, base + 6 * dice),
            "armour": armour, "group_max": b[4] >> 4, "attack_type": b[5] & 0x0F,
            "kind": b[6], "kind_name": kind.get("name", "?"),
            "glyph_class": b[6], "portrait": b[7]}


def toughness(rec8: bytes) -> dict:
    """Back-compat alias — `stats` under the name the API and the templates list grew up with."""
    return stats(rec8)


# name -> (byte, shift, mask). Two fields share byte 4 and two share byte 5, so a nibble field must
# not clobber its neighbour — which is exactly what writing whole bytes used to do.
_PACK = {"range": (2, 0, 0xFF), "dice": (3, 0, 0xFF),
         "armour": (4, 0, 0x0F), "group_max": (4, 4, 0x0F),
         "attack_type": (5, 0, 0x0F), "dmg_base": (5, 4, 0x0F),
         "kind": (6, 0, 0xFF), "portrait": (7, 0, 0xFF)}


def pack_stats(rec8: bytes, spec: dict) -> bytes:
    """Apply the named fields of `spec` onto an 8-byte record, leaving the rest untouched.

    Only keys that are present are written, so a caller may change the armour of a foe without
    having to restate its damage. CON is the 16-bit pair; everything else is a byte or a nibble.
    """
    b = bytearray(bytes(rec8)[:8].ljust(8, b"\x00"))
    if spec.get("con") is not None:
        con = max(0, min(0xFFFF, int(spec["con"])))
        b[0], b[1] = con & 0xFF, con >> 8
    for key, (idx, shift, mask) in _PACK.items():
        if spec.get(key) is None:
            continue
        v = max(0, min(mask, int(spec[key])))
        b[idx] = (b[idx] & ~(mask << shift) & 0xFF) | (v << shift)
    return bytes(b)


def templates(repo) -> list:
    """Every DISTINCT foe record the shipped game contains, as difficulty templates.

    Combat stats are borrowed, never invented — so the editor's difficulty control is "as tough as
    <a foe that exists>", and this is the menu. Deduped by the 8 bytes; the name and area of the
    first occurrence identify it. Sorted by byte 0, which is the thing a designer is choosing.
    """
    import glob
    import os
    import wl_cell
    from wl_area import load_area
    seen = {}
    for f in sorted(glob.glob(os.path.join(str(repo), "baseline", "side*", "s*_record_id*.bin"))):
        b = os.path.basename(f)
        side, aid = int(b[1]), int(b.split("id")[1][:2])
        try:
            area = load_area(open(f, "rb").read(), side, aid)
        except Exception:                                        # noqa: BLE001
            continue
        for fo in foes(area, side):
            if not (fo.get("record") and fo.get("name")):
                continue
            rec = wl_ir._unhex(fo["record"])
            key = bytes(rec[:6])                                 # ignore glyph + portrait: those are
            if key in seen:                                      # the caller's choice, not the template's
                continue
            seen[key] = {"record": wl_ir._hex(bytes(rec)), "name": fo["name"],
                         "from": f"s{side}a{aid}", **toughness(rec)}
    return sorted(seen.values(), key=lambda t: (t["exp"], t["dice"]))
