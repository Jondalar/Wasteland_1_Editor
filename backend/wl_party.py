"""wl_party.py — the save-state party: the four rangers a new game starts with.

The cart ships a party blob as the new-game state (`WL_PARTY` / `WL_PARTY_BLOB` in ef_build_menu.sh),
and until now the only way to change it was to build a different one by hand and point an environment
variable at it. It is ordinary data with a known layout, so it belongs in the editor next to
everything else that is.

    8 records of 256 bytes = the $F400..$FBFF window
      record 0     header/control, no name
      records 1-4  the four party members
      records 5-7  empty

One member record — the same 256-byte sheet `wl_ir._hireable_named` decodes (the hire flow copies a
grp17 record into this window verbatim), so the two field maps must agree byte for byte:

      $00  14 B   name, ASCII, $00-padded
      $0E   7 B   attributes ST IQ LK SP AGL DEX CHR          (cheat: 50 each)
      $15   3 B   money, little-endian                        (cheat: 555555)
      $1A   1 B   ARMOUR CLASS, not level                     (cheat: 99)
      $1B   2 B   maximum CON                                 (cheat: 9999)
      $1D   2 B   current CON, signed — see wasteland-health-model
      $1F   1 B   equipped weapon (index into the inventory)
      $20   1 B   skill points                                (cheat: 99)
      $21   3 B   experience, little-endian
      $24   1 B   LEVEL
      $25   1 B   equipped armour (index into the inventory)
      $29   1 B   status, 9 states
      $32  24 B   rank name, ASCII
      $80  58 B   SKILLS — 29 [id][level] pairs, id 0 ends the list
      $BA   2 B   the party-membership token (skill slot 29, above the id $24 the menus filter on)
      $BD  60 B   INVENTORY — [item id][qty] pairs, id 0 ends the list

$1A/$24 were the wrong way round here until the engine was asked instead of the docs: equip_slot_set
writes $1A together with the armour slot $25 and equip_slot_clear zeroes it with $25, while
ef_charpoint_overlay reads ($66),$24, bumps it and writes it back on level-up — and character
CREATION stores $01 at $24 next to the rank string "Private". The corpus agrees: over the 15 shipped
hireables $24 is strictly monotone in XP and matches the rank string, $1A is 0 wherever no armour is
worn. So the box that said "level" was showing armour class, and typing into it overwrote the AC.

The two lists are the interesting part and they are the same shape: a pair array with an implicit
end. Everything else is a scalar in a fixed place.
"""
from __future__ import annotations

import wl_cell

REC = 256
RECORDS = 8
MEMBERS = (1, 2, 3, 4)                      # 0 is the header, 5-7 are spare slots

NAME, ATTRS, MONEY, AC = 0x00, 0x0E, 0x15, 0x1A
MAXCON, CON, WEAPON, SKILLPTS, XP, LEVEL, ARMOUR, STATUS = 0x1B, 0x1D, 0x1F, 0x20, 0x21, 0x24, 0x25, 0x29
RANK, SKILLS, INV = 0x32, 0x80, 0xBD
# the skill array is 29 slots; slot 29 ($BA/$BB) is the party-membership token and $BC belongs to
# nobody yet. Ending the pair walk at $BD ran both of them through the list — the read side showed a
# phantom 31st skill and the write side zeroed the token on every save.
SKILLS_END, INV_END = 0xBA, 0xF9
ATTR_NAMES = ("ST", "IQ", "LK", "SP", "AGL", "DEX", "CHR")


def _le(b, o, n):
    return int.from_bytes(b[o:o + n], "little")


def _ple(v, n):
    return int(v).to_bytes(n, "little")


def _pairs(rec, start, end):
    """[id][value] until a zero id — the shape both the skill and the item list use."""
    out = []
    for o in range(start, end, 2):
        if not rec[o]:
            break
        out.append({"id": rec[o], "n": rec[o + 1], "at": o})
    return out


def _put_pairs(rec, start, end, items):
    room = (end - start) // 2
    if len(items) > room:
        raise ValueError(f"{len(items)} entries do not fit in {room} slots")
    for k in range(room):
        o = start + k * 2
        rec[o], rec[o + 1] = ((items[k]["id"] & 0xFF, items[k]["n"] & 0xFF) if k < len(items)
                              else (0, 0))


def _txt(b):
    return bytes(b).split(b"\x00")[0].decode("latin-1").strip()


def member(blob: bytes, n: int) -> dict:
    """One member record, decoded. `empty` when the slot carries no name."""
    r = blob[n * REC:(n + 1) * REC]
    if len(r) < REC:
        return {"slot": n, "empty": True}
    name = _txt(r[NAME:NAME + 14])
    if not name:
        return {"slot": n, "empty": True}
    sk = [{**p, "name": wl_cell._SKILL_NAMES[p["id"]]
           if p["id"] < len(wl_cell._SKILL_NAMES) else f"skill #{p['id']}"}
          for p in _pairs(r, SKILLS, SKILLS_END)]
    inv = [{**p, "name": wl_cell._item_name(p["id"] & 0x7F)} for p in _pairs(r, INV, INV_END)]
    return {
        "slot": n, "empty": False, "name": name,
        "attrs": {k: r[ATTRS + i] for i, k in enumerate(ATTR_NAMES)},
        "money": _le(r, MONEY, 3), "level": r[LEVEL], "ac": r[AC],
        "maxcon": _le(r, MAXCON, 2), "con": _le(r, CON, 2),
        "xp": _le(r, XP, 3), "status": r[STATUS], "rank": _txt(r[RANK:RANK + 24]),
        # the equipped slots are INDEXES into the inventory, not item ids: 0 = nothing equipped
        "weapon": r[WEAPON], "armour": r[ARMOUR],
        "weapon_name": inv[r[WEAPON] - 1]["name"] if 0 < r[WEAPON] <= len(inv) else None,
        "armour_name": inv[r[ARMOUR] - 1]["name"] if 0 < r[ARMOUR] <= len(inv) else None,
        "skills": sk, "inventory": inv,
    }


def is_plain(blob: bytes) -> bool:
    """True when this is an unscrambled party blob we can edit.

    Two of the shipped blobs are not: the RAW save-state is still in the engine's scrambled form,
    and the crack-names blob uses a different character encoding for the title screen. Both decode
    to eight "members" with unprintable names, which is the tell — a real party has four readable
    ones in slots 1-4 and nothing in 5-7.
    """
    if len(blob) != REC * RECORDS:
        return False
    ok = 0
    for n in MEMBERS:
        nm = _txt(blob[n * REC:n * REC + 14])
        if nm and all(32 <= ord(c) < 127 for c in nm):
            ok += 1
    empty_tail = all(not _txt(blob[n * REC:n * REC + 14]) for n in (5, 6, 7))
    return ok >= 1 and empty_tail


def load(blob: bytes) -> dict:
    if len(blob) != REC * RECORDS:
        raise ValueError(f"a party blob is {REC * RECORDS} bytes, this one is {len(blob)}")
    return {"bytes": len(blob), "members": [member(blob, n) for n in range(RECORDS)]}


def save_member(blob: bytes, n: int, spec: dict) -> bytes:
    """Write one member back. Only the fields present in `spec` are touched — everything else in
    the 256-byte record stays byte-identical, including the parts nobody has identified yet."""
    if n not in MEMBERS:
        raise ValueError(f"slot {n} is not a party member (1-4)")
    out = bytearray(blob)
    r = bytearray(out[n * REC:(n + 1) * REC])

    if "name" in spec:
        nm = str(spec["name"])[:14].encode("latin-1", "replace")
        r[NAME:NAME + 14] = nm + b"\x00" * (14 - len(nm))
    for i, k in enumerate(ATTR_NAMES):
        if "attrs" in spec and k in spec["attrs"]:
            r[ATTRS + i] = max(0, min(255, int(spec["attrs"][k])))
    for key, off, ln in (("money", MONEY, 3), ("xp", XP, 3), ("maxcon", MAXCON, 2), ("con", CON, 2)):
        if key in spec:
            r[off:off + ln] = _ple(max(0, min(256 ** ln - 1, int(spec[key]))), ln)
    for key, off in (("level", LEVEL), ("ac", AC), ("status", STATUS),
                     ("weapon", WEAPON), ("armour", ARMOUR)):
        if key in spec:
            r[off] = max(0, min(255, int(spec[key])))
    if "rank" in spec:
        rk = str(spec["rank"])[:23].encode("latin-1", "replace")
        r[RANK:RANK + 24] = rk + b"\x00" * (24 - len(rk))
    if "skills" in spec:
        _put_pairs(r, SKILLS, SKILLS_END, spec["skills"])
    if "inventory" in spec:
        _put_pairs(r, INV, INV_END, spec["inventory"])

    out[n * REC:(n + 1) * REC] = r
    if len(out) != len(blob):
        raise ValueError("length drift")
    return bytes(out)


def check(blob: bytes) -> list:
    """[] when the blob is a party the engine can load; else what is wrong with it."""
    bad = []
    if len(blob) != REC * RECORDS:
        return [f"{len(blob)} bytes, expected {REC * RECORDS}"]
    live = [n for n in MEMBERS if not member(blob, n)["empty"]]
    if not live:
        bad.append("no named member in slots 1-4 — a new game would start with an empty party")
    for n in live:
        m = member(blob, n)
        if m["con"] > m["maxcon"]:
            bad.append(f"{m['name']}: CON {m['con']} above the maximum {m['maxcon']}")
        if m["weapon"] and m["weapon"] > len(m["inventory"]):
            bad.append(f"{m['name']}: equipped weapon slot {m['weapon']} but only "
                       f"{len(m['inventory'])} items — the engine would read past the list")
        if m["armour"] and m["armour"] > len(m["inventory"]):
            bad.append(f"{m['name']}: equipped armour slot {m['armour']} but only "
                       f"{len(m['inventory'])} items")
    for n in (5, 6, 7):
        if not member(blob, n)["empty"]:
            bad.append(f"slot {n} carries a name — the engine only starts the first four")
    return bad
