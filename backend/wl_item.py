"""wl_item.py — the item descriptor table: 8 bytes per item, and what they mean.

Traced 2026-08-02, from a question that could not be answered any other way: why does the Sleeper
Base keep your keycards while Highpool's purifier eats the Engine? The two conditions are byte for
byte identical (`opb $20`, op 1, low5 0), the loot sacks hand both out at quantity 1, and the
difference is real — Secpass 3 is required in seven places and exists twice.

So it is the item. And the engine says where:

    set_item_descriptor_ptr ($0BCD):  $45 := $08 · $46 := $31 · $4C := $08 → string_index_to_ptr
    string_index_to_ptr ($1AFE):      ptr = ($46$45) + index * $4C

which is **$3108 + id * 8**. Not, as the disassembly comment had it, "track-8 sector $14" — that
reading took `$45=8` and `$46=$31` for a track and a sector when they are the low and high byte of
a pointer. $3100 is the AREATRACK load address (LOADER.md kind $0F), so the table is the areatrack
blob past an 8-byte header: 768 bytes = 8 + 95 × 8, and $3108 + 95*8 = $3400, exactly where the
area record window begins.

## The eight bytes

| byte | meaning | evidence |
|---|---|---|
| 0-1 | **price**, 16-bit little-endian | Engine 500, Secpass 1/3/7 1500, Secpass A 3000, Secpass B 7000, Crowbar 15, Match 10 |
| 2 | $00 / $FF (78 / 16 items), one $11 | not identified |
| 3 | **class** — see CLASS below | groups perfectly: melee, pistols, rifles, SMGs, energy, thrown, ammo, armour, tools, quest items |
| 4 | **charges** — the count the item is created with | verified against the shipped party: 10 items, 10 exact matches, including Match 40, pistol 7, Rope 1, Crowbar 0 |
| 5-7 | not identified | mostly zero; the weapons carry values here |

## Why byte 4 is the consumable flag

`$14A5`, reached from the condition walk's item branch, decrements the slot's count — but only
after `and #$3F / beq`, which jumps past the subtraction when the count is **zero**. An item created
with 0 charges therefore survives every use, and one created with N burns one each time.

43 of the 95 items have 0 charges, and the list reads exactly as it should: every melee weapon,
every clip, all the armour, Canteen, Crowbar, Gas mask, and every keycard. Cross-checked against
the game's own arithmetic — of the eleven items demanded in more places than the game ever hands
out, ten have 0 charges. The two that do not are Rope (1 charge, 24 places, 9 ropes) and the
Flamethrower (60), and those are genuinely consumable: you cannot use every rope spot, which is
design rather than contradiction.

Charges are GLOBAL — identical in all four areatracks for 94 of 95 items (the exception is `Cash`,
the money pseudo-item). Prices are per areatrack: side 1's third shop sells everything at 1.
"""

import pathlib

REC = 8                       # bytes per descriptor
BASE = 8                      # the areatrack's own header, ahead of item 0
COUNT = 95                    # $3108 + 95*8 = $3400, the area record window

# byte 3 = TYPE << 3 | flag. The "hole pattern" that stopped the first reading is just the types
# WL1 has no item for (5 = short-range auto): shifting right by three yields 1..18 for 94 of the 95,
# and every group comes out homogeneous — Fists/Ax/Club/Chainsaw for 1, LAW rocket/RPG-7 for 9,
# 45 clip/7.62mm/9mm/Howitzer shell for 14. The names are wastelib's ItemType, an independent
# reverse engineering of the DOS build (editor/docs/REF_WASTELIB.md §3); the measurement is ours.
#
# The low three bits carry a flag, and it is set on exactly the explosives — types 8, 9 and 13, both
# rockets and the explosive, nothing else. What it DOES is unproven, so it is carried, not named.
# $FB is the one value outside the scheme (type 31, flag 3): money, a marker rather than an item.
#
# CLASS keeps the group names this project measured its way to, because they say what the group is
# in this game's words; TYPE is the encoding underneath.
TYPE = {1: "melee", 2: "short range", 3: "medium range", 4: "long range", 5: "short range auto",
        6: "medium range auto", 7: "long range auto", 8: "medium rocket", 9: "long rocket",
        10: "short energy", 11: "medium energy", 12: "long energy", 13: "explosive", 14: "ammo",
        15: "armour", 16: "standard", 17: "special", 18: "quest"}
CLASS = {
    0x08: "melee", 0x10: "pistol", 0x18: "carbine", 0x20: "rifle", 0x30: "SMG",
    0x38: "assault rifle", 0x41: "rocket", 0x49: "rocket", 0x50: "energy pistol",
    0x58: "energy carbine", 0x60: "energy heavy", 0x69: "thrown", 0x70: "ammunition",
    0x78: "armour", 0x80: "tool", 0x88: "valuable", 0x90: "quest item", 0xFB: "money",
}

_CACHE: dict | None = None


def parse(blob: bytes) -> list:
    """One areatrack blob → 95 descriptors."""
    out = []
    for i in range(COUNT):
        o = BASE + i * REC
        r = blob[o:o + REC]
        if len(r) < REC:
            break
        out.append({"id": i, "price": r[0] | (r[1] << 8), "b2": r[2],
                    "class_id": r[3], "class": CLASS.get(r[3], f"${r[3]:02X}"),
                    "type": r[3] >> 3, "type_name": TYPE.get(r[3] >> 3),
                    "type_flag": r[3] & 7,
                    "charges": r[4], "tail": [r[5], r[6], r[7]],
                    # 0 charges = $14A5's `and #$3F / beq` jumps past the decrement
                    "consumable": r[4] != 0})
    return out


def table(repo) -> list:
    """The item table, from whichever areatrack is available.

    Charges and class are the same in every areatrack, so any one of them answers "is this
    consumed"; only the price is per shop, and a caller that needs a price should read the
    areatrack the shop actually loads."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    root = pathlib.Path(repo)
    for pat in ("editor/work/**/*areatrack*.bin", "baseline/**/*areatrack*.bin",
                "work/**/*areatrack*.bin"):
        for f in sorted(root.glob(pat)):
            try:
                blob = f.read_bytes()
            except OSError:
                continue
            if len(blob) >= BASE + COUNT * REC:
                _CACHE = parse(blob)
                return _CACHE
    _CACHE = []
    return _CACHE


def charges(repo, item_id: int):
    """How many uses an item is created with — None when the table is not available."""
    t = table(repo)
    return t[item_id]["charges"] if 0 <= item_id < len(t) else None
