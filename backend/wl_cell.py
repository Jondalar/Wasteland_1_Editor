"""wl_cell.py — decode what a map cell DOES (the scene-script/area record it fires), rendered for
the editor's cell inspector. Byte-proven recipe (see cart_EF/docs/engine/AREA_MODEL.md,
AREA_GROUPS.md, GRP16_CARTOGRAPHY.md; block2 area_record_subptr $09B8).

Resolution rule (ONE routine): record = WORD[ WORD[desc+$06 + grp*2] + idx*2 ], pointers are RAM
$3400-based → file offset = ram - $3400. For a stepped cell: grp = op-type nibble, idx = cell-id.
Prose (messages/prompts/labels) uses the RECORD's OWN char table at LE16(raw[slot+0])-$3400 (NOT
the $29E4 item table). Item ids → names via ITEM_TABLE.md. NPC portrait: foe table desc+$04,
8-B records, byte7 = str id, byte3 = 1-based index into desc+$02 names.
"""
from __future__ import annotations
from pathlib import Path

import wl_text5
from wl_machine import RECORD_BASE as RAM   # $3400 on the C64, $D000 on the Apple

OP_NAMES = {
    0x0: "default / terrain", 0x1: "SAY", 0x2: "COND/TRADE", 0x3: "encounter",
    0x4: "MSG (single str+$0)", 0x5: "LOOT", 0x6: "AREA_EVENT (shop/doc)", 0x7: "npc-data",
    0x8: "KEYWORD", 0x9: "sweep/XP", 0xA: "TRANSITION", 0xB: "bump-msg / npc-data",
    0xC: "CELL_PATCH", 0xD: "default", 0xE: "default", 0xF: "npc-data / encounter",
}

# ── module data loaded lazily from the repo ──
_ITEMS: dict[int, str] | None = None
_AREANAMES: dict[int, str] | None = None
_REPO: Path | None = None


def configure(repo: Path):
    global _REPO
    _REPO = repo


def _le(raw: bytes, o: int) -> int:
    return (raw[o] | (raw[o + 1] << 8)) if o + 1 < len(raw) else 0


def _f(raw: bytes, ram: int) -> int:
    """RAM $3400-relative pointer → blob file offset (None if the group is unused = 0)."""
    return ram - RAM


def gptr(raw: bytes, slot: int, grp: int):
    ram = _le(raw, slot + 0x06 + grp * 2)
    return None if ram == 0 else ram - RAM


def group_is_alias(raw: bytes, slot: int, grp: int) -> bool:
    """True when this group's pointer is FILLER — the same base another group (or a section header)
    already owns, so its "slots" are that other structure's bytes.

    Unused group slots are routinely filled with a neighbour's pointer rather than 0. Blood Temple2
    has grp6 = grp7 = grp8 = grp16, and reading grp6 there produced a "service" whose label is the
    binary of somebody else's records — which then MOVED whenever the record was re-packed, because
    it was never a service to begin with. Same rule GRP16_SPEC §5 uses for per-area code."""
    g = gptr(raw, slot, grp)
    if g is None:
        return False
    others = [gptr(raw, slot, x) for x in range(19) if x != grp]
    others += [_le(raw, slot + o) - RAM for o in (0, 2, 4)]
    return g in others


def subptr(raw: bytes, slot: int, grp: int, idx: int):
    g = gptr(raw, slot, grp)
    if g is None:
        return None
    ram = _le(raw, g + idx * 2)
    return None if ram == 0 else ram - RAM


# Item id -> name, baked in from docs/ITEM_TABLE.md (text5-decoded from the engine message
# bank at $29E4, msg id = item id + $24). Embedded rather than read from the repo: the
# container only mounts /data, so a docs/ lookup silently yielded "item $xx" forever.
# ── item + skill names: NOT shipped, loaded from the user's own disk ─────────────────
# Upstream pasted these 130 names into this file as literals. They are the 1988 game's own
# prose, so this project does not carry them: `tools/wl_gen_names.py` decodes them from the
# engine blob on YOUR side-1 image into data/names.json, and they are read from there.
# Missing file -> the editor shows "item $NN" / "skill NN" and keeps working.
_NAMES_CACHE: dict = {}


def _names() -> dict:
    if not _NAMES_CACHE:
        import json
        p = (_REPO / "names.json") if _REPO else None
        try:
            d = json.loads(p.read_text()) if p and p.is_file() else {}
        except (ValueError, OSError):
            d = {}
        _NAMES_CACHE["items"] = {int(k): v for k, v in (d.get("items") or {}).items()}
        _NAMES_CACHE["skills"] = list(d.get("skills") or [])
    return _NAMES_CACHE


class _ItemNames:
    """dict-ish view so the existing .get/.items()/`in` call sites keep working unchanged."""
    def get(self, k, default=None): return _names()["items"].get(k, default)
    def items(self): return _names()["items"].items()
    def __contains__(self, k): return k in _names()["items"]
    def __getitem__(self, k): return _names()["items"][k]
    def __len__(self): return len(_names()["items"])
    def __iter__(self): return iter(_names()["items"])


class _SkillNames:
    def __getitem__(self, i): return _names()["skills"][i]
    def __len__(self): return len(_names()["skills"])
    def __iter__(self): return iter(_names()["skills"])


_ITEM_NAMES = _ItemNames()
_SKILL_NAMES = _SkillNames()


# ── the control codes inside a string ──────────────────────────────────────────────────────────
# TRACED, not guessed — `char_out` ($1F03) dispatches control bytes through a 17-entry jump table,
# and there are TWO of them: mode A at $1F42 (the default) and mode B at $1F64 (char_out_mode_b).
# Decoding both settles the question of whether a code behaves differently in the combat log than
# in a dialogue box:
#
#   code  mode A  mode B   same
#   $07   $12B0   $12B0    yes      $08   $1F3C   $200C    DIFFERENT
#   $0A   $205E   $205E    yes      $0D   $20F9   $1FFB    DIFFERENT
#   $0B   $1FBA   $1FBA    yes      >=$10 $1F86   $1FEA    DIFFERENT (the literal-glyph path)
#   $0E   $20BE   $20BE    yes
#   $0F   $1F9D   $1F9D    yes
#
# Every code an author can type behaves IDENTICALLY in both renderers. The three that differ are
# the literal path and two codes nothing in the string pool uses.
#
# Two labels here were wrong before the trace and are corrected:
#   $0F was called "the target". $1F9D is `lda $38 / … / num_print_zero_pad` — it prints $38 as a
#       NUMBER, and $38 is the live count of the group (COMBAT_TEXT.md §3).
#   $07 was called "right-hand box" on the strength of one note. $12B0 is newline_if_needed+4,
#       which sets up a frame at column 39 row 14 and calls draw_text_box_frame — so the label was
#       right, and now it is evidence rather than recollection.
#
# Counted over all 43 areas: <0a> 811 (747 in the combat block) · <0e> 720 · <0f> 356 · <0b> 284 ·
# <07> 47 (none in combat). <7f> (12) and <06> (1) occur only in unreachable or filler strings and
# are deliberately not given a meaning.
TEXT_CODES = {
    "0a": {"name": "count switch", "arity": 2,
           "about": "one branch for a single foe, the next for several",
           "example": "fail<0a>s<0a><0a> → “fails” alone, “fail” in a group",
           "traced": "$205E sets $B3; W2063 filters on $38, the live count"},
    "0e": {"name": "second switch", "arity": 3,
           "about": "three branches — used for his / her / its",
           "example": "<0e>him<0e>her<0e>it<0e>",
           "traced": "$20BE sets $CB; W20C3 filters. The MECHANISM is traced; that the three "
                     "branches are genders is read off the strings, not off the code"},
    "0f": {"name": "how many", "arity": 0,
           "about": "prints a number — the live count of the group",
           "example": "hits <0f> → “hits 3”",
           "traced": "$1F9D: lda $38 → num_print_zero_pad"},
    "0b": {"name": "name here", "arity": 0,
           "about": "splices in the name the engine has staged — in a fight, the attacker",
           "example": "<0b> attacks",
           "traced": "$1FBA: save $31/$32, print the string at $66/$67, restore and resume"},
    "07": {"name": "right-hand box", "arity": 0,
           "about": "opens a second text box — only meaningful at the start of a greeting",
           "example": "<07>Welcome to the store",
           "traced": "$12B0 = newline_if_needed+4 → draw_text_box_frame at column 39, row 14"},
}



def explain_text(s: str) -> str:
    """A string with its control codes written out, for reading — never for saving.

    `<0b> attacks <0f>` means nothing on the page; "«attacker» attacks «target»" does. A switch
    keeps BOTH branches, because that is the point of it: showing one would be a sentence the player
    only sees half the time.

    A switch of arity N is N segments between N+1 markers — `fail<0a>s<0a><0a>` is two segments,
    "s" and empty, giving "fails" for one foe and "fail" for several."""
    import re as _re
    out = str(s)
    for code, info in TEXT_CODES.items():
        n = info["arity"]
        if n < 2:
            continue
        pat = f"<{code}>" + f"(.*?)<{code}>" * n
        out = _re.sub(pat, lambda m: "«" + " / ".join(g if g else "—" for g in m.groups()) + "»",
                      out, flags=_re.S)
    for code, info in TEXT_CODES.items():
        if info["arity"] == 0:
            out = out.replace(f"<{code}>", f"«{info['name']}»")
    return out


def _item_name(iid: int) -> str:
    return _ITEM_NAMES.get(iid, f"item ${iid:02X}")


# The 7 rolled attributes + MaxCON, by member-record offset. Sources: point-buy overlay text
# ($80C2: "1) Strength 2) IQ/Skill 3) Luck 4) Speed 5) Agility 6) Dexterity 7) Charisma 8) Maxcon")
# and CR_STAT_ROLL ($7FD8) which rolls the 7 attributes at +$0E..+$14. op-2 attribute checks
# reference these record fields. (Skill NAMES are a separate 5-bit packed table at $8519 — ids
# live as [id][value] pairs at record +$80..$BB (member_skill_lookup $1392); name decode deferred.)
_ATTR_NAMES = {0x0E: "Strength", 0x0F: "IQ", 0x10: "Luck", 0x11: "Speed",
               0x12: "Agility", 0x13: "Dexterity", 0x14: "Charisma", 0x1B: "MaxCON"}


def _attr_name(off: int) -> str:
    return _ATTR_NAMES.get(off, f"attr +${off:02X}")


# Weapon stats, sourced from the Nearly Ultimate Wasteland Guide (damage = number of dice; ammo
# type; clip = shots per load; governing skill). Surfaced as a tooltip so a designer placing a
# weapon in a loot sack sees what it is. id -> (damage, ammo, clip, skill). ammo/clip None = melee.
_WEAPON_STATS = {
    15: (2, None, None, "Knife throw"), 14: (5, None, None, "Brawling"),
    4: (3, None, None, "Knife fight"), 2: (3, None, None, "Brawling"),
    1: (3, None, None, "Brawling"), 3: (6, None, None, "Brawling"),
    5: (14, None, None, "Brawling"), 8: (5, None, None, "Demolitions"),
    6: (7, None, None, "Demolitions"), 7: (10, None, None, "Demolitions"),
    9: (4, None, None, "AT weapon"), 10: (7, None, None, "AT weapon"),
    11: (10, None, None, "AT weapon"), 12: (13, None, None, "AT weapon"),
    17: (11, "fuel", 60, "none"), 13: (4, "45", 7, "Clip pistol"),
    16: (3, "9mm", 18, "Clip pistol"), 18: (5, "7.62mm", 10, "Rifle"),
    19: (5, "7.62mm", 8, "Rifle"), 20: (200, "7.62mm", 63, "Rifle"),
    21: (4, "45", 30, "SMG"), 22: (4, "9mm", 40, "SMG"),
    23: (6, "7.62mm", 30, "Assault rifle"), 24: (6, "7.62mm", 35, "Assault rifle"),
    25: (6, "Power", 40, "Energy weapon"), 27: (8, "Power", 30, "Energy weapon"),
    28: (12, "Power", 20, "Energy weapon"), 26: (14, "Power", 20, "Energy weapon"),
    29: (19, "Power", 10, "Energy weapon"),
}


def _item_hint(iid: int) -> str:
    """Item name + weapon statblock (damage/ammo/clip/skill) when the item is a weapon."""
    name = _item_name(iid)
    w = _WEAPON_STATS.get(iid)
    if not w:
        return name
    dmg, ammo, clip, skill = w
    bits = [f"{dmg} dmg"]
    if ammo:
        bits.append(f"{ammo}×{clip}" if clip else str(ammo))
    bits.append(skill)
    return f"{name}  ({', '.join(bits)})"


def loot_entries(raw, rec: int, limit: int = 64):
    """Walk a LOOT sack's item list: (entries, terminator bytes, offset just past it).

    THE ONE walker for this list — `wl_ir`'s LOOT decode and the editor's label both call it. They
    used to have one each, and the second one did not know that MONEY takes a 16-bit amount:
    `[$5E|bit7][lo][hi]` is a THREE-byte entry. Reading it as a pair walked the rest of the list one
    byte out of phase, so a sack holding 1000 credits was shown as "Cash ×104 · Chainsaw ×255 · …",
    the tail of it read out of the NEXT record — which meant the displayed contents changed when a
    re-pack changed the neighbour. Same bug, same shape, as the unbounded op-A door-tail walk.

    Entries are {"item": byte, "qty": n} or {"item": byte, "qty16": n} for money. bit7 on the item
    byte = flagged/guaranteed; the low 7 bits are the real id.
    """
    items, j, term = [], rec + 2, bytes([0xFF])
    while j + 1 < len(raw) and len(items) < limit:
        if raw[j] == 0xFF:
            term = bytes([0xFF])
            j += 1
            break
        if raw[j] == 0x00:                           # the usual close: item id 0, then the $FF
            term = bytes([raw[j], raw[j + 1]])
            j += 2
            break
        if (raw[j] & 0x7F) == 0x5E:
            items.append({"item": raw[j],
                          "qty16": raw[j + 1] | (raw[j + 2] << 8) if j + 2 < len(raw)
                          else raw[j + 1]})
            j += 3
        else:
            items.append({"item": raw[j], "qty": raw[j + 1]})
            j += 2
    return items, term, j


def _loot_items(area, cid: int, limit: int = 12) -> list[str]:
    """The contents of a LOOT sack (grp5, cid), for the flow/mutation views — so a designer sees
    what a bag GRANTS ('Laser rifle ×2 · Power pack ×12') instead of a bare 'LOOT #N'."""
    raw = area.raw
    rec = subptr(raw, area.slot, 0x5, cid)
    if rec is None or rec + 2 >= len(raw):
        return []
    out = []
    for e in loot_entries(raw, rec)[0][:limit]:
        iid = e["item"]
        if "qty16" in e:
            out.append(f"Cash ×{e['qty16']}")
        elif iid & 0x80:
            # bit7 = flagged item (identified/guaranteed); low 7 bits = the REAL item id. VERIFIED
            # 2026-07-26: Citadel LOOT 5 = $1C/$1D/$22 → Laser rifle ×2 · Meson cannon ×1 · Power pack
            # ×12 = the guide's armoury bag exactly. (Old "random item of class" reading was wrong.)
            out.append(f"{_item_name(iid & 0x7F)} ×{e['qty'] & 0x7F}")
        else:
            out.append(f"{_item_name(iid)} ×{e['qty']}")
    return out


def _area_name(aid: int) -> str:
    global _AREANAMES
    if _AREANAMES is None:
        _AREANAMES = {}
        import json
        p = (_REPO / "assets" / "areanames" / "area_names.json") if _REPO else None
        if p and p.is_file():
            try:
                for k, v in json.loads(p.read_text()).items():
                    if k.isdigit() and isinstance(v, dict) and v.get("name"):
                        _AREANAMES[int(k)] = v["name"]
            except (ValueError, KeyError):
                pass
    return _AREANAMES.get(aid, f"area {aid}")


_PARAGRAPHS: dict = {}
_PARA_REF = __import__("re").compile(r"[Rr]ead paragraph\s+(\d+)")


def paragraphs() -> dict:
    """The printed paragraph book, {number: text}, loaded once.

    It belongs in the data model rather than beside it. "Read paragraph 57." is not a sentence — it
    is a REFERENCE, and the thing it refers to carries the plot: the visa-card errand, the vault
    numbers on three dead men's bracelets, the password that opens the Atchison tent. A view that
    shows the sentence and not the paragraph is showing the pointer instead of the value.
    """
    if not _PARAGRAPHS:
        import json
        p = (_REPO / "assets" / "paragraphs" / "wl1_paragraphs.json") if _REPO else None
        try:
            _PARAGRAPHS.update(json.loads(p.read_text())["paragraphs"])
        except Exception:                                # noqa: BLE001 — every view works without it
            _PARAGRAPHS["_missing"] = ""
    return _PARAGRAPHS


def paragraph_refs(text) -> dict:
    """{number: paragraph text} for every "Read paragraph N." in a string (or list of strings)."""
    if text is None:
        return {}
    xs = [text] if isinstance(text, str) else list(text)
    P = paragraphs()
    out = {}
    for x in xs:
        for n in _PARA_REF.findall(str(x)):
            if P.get(n):
                out[n] = " ".join(P[n].split())
    return out


def _prose(area, idx: int) -> str:
    """Decode string #idx from the record's own text5 table."""
    idx &= 0x7F
    if idx == 0:
        return ""
    try:
        tbase = _le(area.raw, area.slot + 0) - RAM
        tbl = area.raw[tbase:tbase + 62]
        s = wl_text5.get_string(area.raw, tbase, idx, tbl, ot_off=0x3C)
        return s.strip() if s else f"(str #{idx})"   # guard: get_string can return None for an out-of-range id
    except (IndexError, ValueError):
        return f"(str #{idx})"


def _hexbytes(raw: bytes, off: int, n: int) -> str:
    return " ".join(f"{b:02X}" for b in raw[off:off + n])


def _s8(b: int) -> int:
    """Interpret a byte as a signed 8-bit offset (dx/dy in CELL_PATCH wrap via adc)."""
    return b - 256 if b > 127 else b


def _is_write(b: int) -> bool:
    """A DONE/ALT/patch [op-nibble] byte names a NEW cell state only if it is a real op nibble (0..15).

    ≥$10 goes to map_cell_resolve_escape instead, and only $FF is the true no-op there ($0A00:
    `lda $64 / bmi` returns). $FD/$FE do write the cell — they swap it back from the saved $AD/$AE
    pair, which is how a service door becomes a door again — but they name no state of their own, so
    for every caller here they are still "nothing new to show"."""
    return b < 0x10


def inspect_op(area, col: int, row: int, op: int, disp: int) -> list[dict]:
    raw = area.raw
    slot = area.slot
    S: list[dict] = []
    S.append({"title": "Op-type", "kind": "kv", "rows": [
        ["op-type", f"${op:X} {OP_NAMES.get(op, '?')}"],
        ["dispatch (cell-id)", f"${disp:02X} ({disp})"],
    ]})

    # dispatch groups exist only for stepped scene ops
    if op in (0x1, 0x2, 0x4, 0x5, 0x6, 0x8, 0x9, 0xA, 0xB, 0xC):
        rec = subptr(raw, slot, op, disp)
        if rec is None:
            S.append({"title": "Record", "kind": "kv",
                      "rows": [["resolved", f"grp{op}[{disp}] = (unused / null)"]]})
            return S
        S += _decode_record(area, op, rec)

    if op == 0x6:
        S += _grp16_section(area)
    return S


# Set by app.py at import: AREATRACK_LOADER(side, track_id) -> bytes | None.
# The areatrack (LUT kind $0F, 768 B -> $3100) IS the item table: 8-byte header then 95 x 8-byte
# descriptors, desc[id] = +8 + id*8, with +$00/+$01 price, +$02 SHOP STOCK, +$03>>3 class.
AREATRACK_LOADER = None


def _areatrack_offer(side: int, track: int, classes: set[int]):
    """What a shop actually offers: stock != 0 AND class in the record's list. -> [(id, name, price)]"""
    if AREATRACK_LOADER is None:
        return None
    b = AREATRACK_LOADER(side, track)
    if not b or len(b) < 8 + 95 * 8:
        return None
    out = []
    for i in range(95):
        o = 8 + i * 8
        if b[o + 2] and (b[o + 3] >> 3) in classes:
            out.append((i, _item_name(i), b[o] | (b[o + 1] << 8)))
    return out


def _service_family(sel: int) -> str | None:
    """Which record layout a scene selector uses. 0/1 are the stock blobs, 5-9 are ours."""
    return ("store" if sel in (1, 5) else "doc" if sel in (0, 6) else
            "bank" if sel == 7 else "revive" if sel == 8 else "talk" if sel == 9 else None)


def service_name_span(sel: int) -> tuple[int, int]:
    """(offset from the record base, length) of a SERVICE's 11-char label.

    ONE rule, because there were two and they disagreed. The store/doc/bank families put four field
    bytes ahead of the name, so it sits at +$07 — and `_service_section` hard-coded that for every
    selector. Selectors 2 and 3 have no field bytes at all: Ranger Center's record is
    `83 FF FF "Ranger Ctr." 00`, name at +$03, and the panel read it four bytes late and showed
    "er Ctr.". The serializer already knew (wl_ir's `nf, nn`); the display did not.
    """
    # Selector 2 has ONE field byte, not zero: its labels came out as "\x16Vegas Lib." with the
    # control byte attached. The SPAN the emitter consumes is unchanged (3 + 1 + 11 == 3 + 0 + 12),
    # so this is a display correction that cannot move a byte.
    return (4, 11) if sel == 2 else (3, 11) if sel == 3 else (7, 11)


def service_end(raw, rec: int) -> int:
    """Where a SERVICE record really ENDS — derived from its own offset bytes, not from neighbours.

    A bank/revive record addresses $00-terminated ASCII by an offset from its own base, so the record
    must cover everything those offsets reach. `_section_end` cannot know that: it answers "up to the
    next resolved pointer", and in Area 51 a pointer resolves INTO the Sentinel's third string, which
    cut the record at 60 bytes when it is 106 and lost "He walks among us again." and the refusal
    line. THE OFFSETS ARE THE DEFINITION; the next-structure rule is only an upper bound.
    """
    if rec is None or not (0 <= rec < len(raw)) or not (raw[rec] & 0x80):
        return rec
    fam = raw[rec] & 0x7F
    if fam not in (7, 8):
        return rec
    end = rec
    # revive addresses three strings by OFFSET BYTES at +4/+5/+6; bank has one at a FIXED +$15
    # (+4 there is the prompt's text5 id, not an offset — reading it as one cut the confirmation).
    for off in ([raw[rec + k] for k in (4, 5, 6) if rec + k < len(raw)] if fam == 8 else [0x15]):
        if not off:
            continue
        e = rec + off
        while e < len(raw) and raw[e] != 0x00:
            e += 1
        end = max(end, e + 1)
    return min(end, len(raw))


def _ascii_at(raw, rec: int, off: int, end: int | None = None) -> str:
    """A $00-terminated ASCII string that a record field points at by 8-bit offset from the record
    base. That single byte is why the whole record is capped at 255 B — the 6502 does
    `lda (REC),y / clc / adc REC / ... / adc #$00`, so the offset never carries past a page."""
    if not off or rec + off >= len(raw):
        return ""
    # BOUNDED by the record. Unbounded, a string whose $00 terminator is not inside the record ran
    # into the NEXT one, so its length — and with it the "text budget" the panel shows — changed
    # whenever a re-pack put a different neighbour there (s1_id51 read 149 B free before and 206
    # after). Same failure as the op-A door tail and the loot list.
    stop = min(end if end is not None else len(raw), len(raw))
    return bytes(raw[rec + off:stop]).split(b"\x00")[0].decode("ascii", "replace")


def _pack_ascii(start: int, texts) -> tuple[list[int], bytes]:
    """Lay $00-terminated ASCII out from `start` and return (offset per string, the blob).

    Refuses anything that would push an offset past 255. The 6502 reads these with a single
    offset byte added to the record base without carrying into the high byte, so a 256th byte
    does not truncate the text — it silently makes the string point somewhere else entirely.
    """
    offs, blob = [], b""
    for t in texts:
        off = start + len(blob)
        if off > 255:
            raise ValueError(f"text does not fit: offset {off} exceeds the 255-byte record window "
                             f"(the 6502 addresses these with ONE offset byte). Shorten the text.")
        offs.append(off)
        blob += str(t).encode("ascii", "replace") + b"\x00"
    if start + len(blob) > 256:
        raise ValueError(f"texts total {start + len(blob)} B, the record window is 256 B")
    return offs, blob


def _ascii_budget(start: int, count: int, texts) -> int:
    """Bytes still free before an offset would exceed 255 and silently point at garbage."""
    used = start + sum(len(t) + 1 for t in texts)
    return max(0, 255 - used)


def _service_section(area, rec: int, ramaddr: int) -> dict:
    """Decode an op-6 SERVICE record ($80|selector). THREE families, and the field offsets differ
    (SERVICE_HANDLERS.md):

      STORE   sel 1 = stock blob · sel 5 = our patched copy
              +$03 buy-shift · +$04 sell-shift · +$05 greeting · +$06 AREATRACK id ·
              +$07..$12 label · then the CLASS list, $FF-terminated —
              at +$13 for the stock blob, at +$14 for ours (the patch moved it to free +$13
              for the portrait).  Portrait: stock = baked in the blob, ours = record +$13.
      DOC     sel 0 = stock · sel 6 = our patched copy
              +$03 greeting · +$04/+$05/+$06 the three treatment prices (0 = free) ·
              label · portrait: stock = in the blob, ours = record +$13.
      BANK    sel 7 (trex_bank.asm, ours only)
              +$03 greeting · +$04 prompt · +$05/+$06 amount · label · +$13 portrait ·
              +$15.. the confirmation as RAW $00-terminated ASCII (printed via $0274
              print_string, not the text5 path — so it is free of the char table).
    """
    raw = area.raw
    sel = raw[rec] & 0x7F
    patched = sel in (5, 6)                     # our derived copies read the portrait from +$13
    fam = _service_family(sel) or "?"
    disp = {"store": "Store", "doc": "Doctor / Infirmary", "bank": "Bank",
            "revive": "Revive (Sentinel)", "talk": "Talk / dialog"}.get(fam, f"selector ${sel:02X}")
    noff, nlen = service_name_span(sel)
    label = bytes(raw[rec + noff:rec + noff + nlen]).split(b"\x00")[0].decode("ascii", "replace").strip()
    rows = [["kind", disp + ("  (stock blob)" if sel in (0, 1) else "  (derived copy)")],
            ["selector", f"${sel:02X} → s{area.side}_scene_id{sel:02d}.bin ($CA00)"],
            ["sign / label", label or "—"]]
    if fam == "?":
        # An unknown selector has no field map, so every row below would be invented — and the write
        # path refuses it anyway ("unknown service selector"). Say that instead of offering boxes
        # that cannot be saved. Ranger Center's $03 is the one in the shipped game.
        rows.append(["layout", "not one of the known families — this selector's own $CA00 handler "
                               "reads the record, so only the label is safe to read here. Not editable."])
        return {"title": f"🛒 SERVICE — {label or '?'}  (grp6 @ ${ramaddr:04X})", "kind": "kv",
                "rows": rows, "scene_sel": sel, "readonly": True}

    if sel in (7, 8, 9) or patched:
        rows.append(["portrait", f"str {raw[rec + 0x13]}"])
    else:
        rows.append(["portrait", "baked in the scene blob (not a record field)"])

    if fam == "store":
        buy, sell, track = raw[rec + 3], raw[rec + 4], raw[rec + 6]
        rows += [["greeting", _prose(area, raw[rec + 5]) or f"str #{raw[rec+5]}"],
                 ["stock table", f"areatrack {track}  (s{area.side}_areatrack_id{track:02d}.bin)"],
                 ["buy price", "full price" if buy == 0 else f"P − (P>>{buy})"],
                 ["sell price", "full price" if sell == 0 else f"P − (P>>{sell})"]]
        cls, i = set(), rec + (0x14 if patched else 0x13)
        while i < len(raw) and raw[i] != 0xFF and len(cls) < 16:
            cls.add(raw[i]); i += 1
        rows.append(["classes", ", ".join(str(c) for c in sorted(cls)) or "—"])
        offer = _areatrack_offer(area.side, track, cls)
        if offer is None:
            rows.append(["sells", f"(areatrack {track} not available)"])
        elif not offer:
            rows.append(["sells", "nothing — no item in these classes is in stock in that table"])
        else:
            price = offer[0][2]
            flat = all(p == price for _, _, p in offer)
            rows.append([f"sells ({len(offer)})", ", ".join(n for _, n, _ in offer)])
            rows.append(["price", f"{price} each" if flat else "per item (see table)"])
    elif fam == "doc":
        p = [raw[rec + 4], raw[rec + 5], raw[rec + 6]]
        rows += [["greeting", _prose(area, raw[rec + 3]) or f"str #{raw[rec+3]}"],
                 ["treatment prices", "free (0/0/0)" if not any(p) else " / ".join(map(str, p))]]
    elif fam == "bank":
        amount = raw[rec + 5] | (raw[rec + 6] << 8)
        ascii_msg = bytes(raw[rec + 0x15:rec + 0x40]).split(b"\x00")[0].decode("ascii", "replace")
        rows += [["greeting", _prose(area, raw[rec + 3]) or f"str #{raw[rec+3]}"],
                 ["prompt", _prose(area, raw[rec + 4]) or f"str #{raw[rec+4]}"],
                 ["gives", f"{amount} per ranger"],
                 ["confirmation (ASCII)", ascii_msg or "—"]]
    elif fam == "revive":
        _e = max(service_end(raw, rec),          # the offsets define it; the next structure is only
                 _section_end(_section_bounds(raw, area.slot), rec, len(raw)))
        texts = [_ascii_at(raw, rec, raw[rec + 4], _e), _ascii_at(raw, rec, raw[rec + 5], _e),
                 _ascii_at(raw, rec, raw[rec + 6], _e)]
        rows += [["greeting", _prose(area, raw[rec + 3]) or f"str #{raw[rec+3]}"],
                 ["prompt (ASCII)", texts[0] or "—"],
                 ["revived (ASCII)", texts[1] or "—"],
                 ["refused (ASCII)", texts[2] or "—"]]
        edit = {"family": fam, "texts": texts,
                "budget": _ascii_budget(0x14, len(texts), texts)}
        rows.append(["text budget", f"{edit['budget']} B left of 255"])
    elif fam == "talk":
        # pages are now text5 STRING-IDS (0.9.64), one byte each at +$14; the text lives in the STR
        # pool and is edited in the Text tab. We show which string each page is + its resolved text.
        n = raw[rec + 4]
        ids = [raw[rec + 0x14 + i] for i in range(n)]
        pages = [_prose(area, sid) or f"str #{sid}" for sid in ids]
        rows.append(["pages", str(n)])
        for i, (sid, pg) in enumerate(zip(ids, pages)):
            rows.append([f"page {i + 1} (str {sid})", pg or "—"])
        edit = {"family": fam, "page_ids": ids, "pages": pages}

    out = {"title": f"🛒 SERVICE — {label or disp}  (grp6 @ ${ramaddr:04X})", "kind": "kv", "rows": rows}
    out["scene_sel"] = sel                     # → frontend offers the $CA00 scene-code view
    if fam in ("revive", "talk"):
        out["edit"] = {**edit, "portrait": raw[rec + 0x13], "label": label}
    return out


def _decode_record(area, op: int, rec: int) -> list[dict]:
    raw = area.raw
    out: list[dict] = []
    ramaddr = rec + RAM

    if op == 0x1:   # SAY — [msg...] bit7=last, then branch pair
        msgs, i = [], rec
        while i < len(raw):
            b = raw[i]; i += 1
            mid = b & 0x7F
            if mid:
                msgs.append(_prose(area, mid) or f"(str #{mid})")
            if b & 0x80:
                break
        out.append({"title": f"SAY  (grp1 @ ${ramaddr:04X})", "kind": "text",
                    "text": "\n".join(f"• {m}" for m in msgs) or "(no message)"})

    elif op == 0x2:   # COND/TRADE
        prompt = _prose(area, raw[rec + 1]) if rec + 1 < len(raw) else ""
        result = _prose(area, raw[rec + 2]) if rec + 2 < len(raw) else ""
        fail = _prose(area, raw[rec + 3]) if rec + 3 < len(raw) else ""
        eff_t = raw[rec + 8] if rec + 8 < len(raw) else 0
        eff_v = raw[rec + 9] if rec + 9 < len(raw) else 0
        eff = {0x1D: "damage", 0x15: "money", 0x21: "XP"}.get(eff_t & 0x7F, f"target ${eff_t & 0x7F:02X}")
        rows = [["prompt", prompt or "—"], ["on success", result or "—"], ["on fail", fail or "—"]]
        if eff_t or eff_v:
            rows.append(["effect", f"{eff} = {eff_v}" + (" (literal)" if eff_t & 0x80 else "")])
        # condition list from +$0A: [opbyte][arg] until $FF (opbyte>>5 = op, &$1F = id)
        # NOT "requires"/"gives". That labelling is the last trace of the discredited reading in
        # which op-0 handed the party an item; op-0 is a SKILL check and gives nothing. Every entry
        # is one thing the player can do on this square — the record is a menu, not a set of tests.
        conds = _decode_conditions(area, rec + 0x0A)
        for k, c in enumerate(conds):
            rows.append([f"you can {k + 1}" if len(conds) > 1 else "you can", c["label"]])
        out.append({"title": f"COND / TRADE  (grp2 @ ${ramaddr:04X})", "kind": "kv", "rows": rows})

    elif op == 0x4:   # SCENE_OP_MSG — single string at rec+$0, then SCRIPT_ADVANCE (W833D)
        out.append({"title": f"MSG  (grp4 @ ${ramaddr:04X})", "kind": "text",
                    "text": _prose(area, raw[rec]) or "(message, no string)"})

    elif op == 0x9:   # PARTY_FLAG_SWEEP ($82E2) — msg + party-wide CON damage + poison
        dice = raw[rec + 1] if rec + 1 < len(raw) else 0
        out.append({"title": f"PARTY HAZARD  (grp9 @ ${ramaddr:04X})", "kind": "kv",
                    "rows": [["message", _prose(area, raw[rec]) or "(none)"],
                             ["message str id", f"{raw[rec] & 0x7F}"],
                             ["damage (dice)", f"{dice} × RNG roll → CON, EVERY member"],
                             ["status", "sets poison bit $01 on every member (Doc 'Cure' clears it)"]]})

    elif op == 0x5:   # LOOT — R+0 msg, then the item list (loot_entries owns the walk)
        # The THIRD copy of this walk, and it had the same defect the other two had: MONEY is
        # `[$5E|bit7][lo][hi]`, a three-byte entry. Read as a pair it lost phase and turned ONE
        # money entry into two phantom items — s2_id04's 2000 credits displayed as
        # "Cash x208 · Plastic explosive x255", and the tail changed with the neighbouring record.
        msg = _prose(area, raw[rec]) if rec < len(raw) else ""
        items = []
        for e in loot_entries(raw, rec)[0]:
            iid = e["item"]
            if "qty16" in e:
                items.append(f"Cash x{e['qty16']}")
            elif iid & 0x80:                             # bit7 = flagged item, low 7 bits = real id
                items.append(f"{_item_name(iid & 0x7F)} x{e['qty'] & 0x7F}")
            else:
                items.append(f"{_item_name(iid)} x{e['qty']}")
        out.append({"title": f"LOOT  (grp5 @ ${ramaddr:04X})", "kind": "list",
                    "items": ([f"msg: {msg}"] if msg else []) + (items or ["(empty)"])})

    elif op == 0x6:   # AREA_EVENT
        b0 = raw[rec]
        if b0 & 0x80:
            out.append(_service_section(area, rec, ramaddr))
        else:
            out.append({"title": f"AREA_EVENT → grp16 code  (grp6 @ ${ramaddr:04X})", "kind": "kv",
                        "rows": [["event id", f"{b0}"], ["see", "grp16 section below"]]})

    elif op == 0x8:   # KEYWORD — prompt(+0), no-match branch [$FF$FF](+1/+2), keyword string
        prompt = _prose(area, raw[rec] & 0x7F)            # ids (+3, bit7-last), then a per-keyword
        kw_ids, i = [], rec + 3                            # BRANCH TABLE of [next-op][next-id]: typing
        while i < len(raw):                               # keyword j rewrites the cell to (op,id)[j]
            b = raw[i]; i += 1                             # (engine SCENE_OP_KEYWORD_PROMPT $88E4 →
            kw_ids.append(b & 0x7F)                        # WB105 → map_cell_resolve_escape). See
            if b & 0x80:                                  # docs/engine/OP8_KEYWORD.md.
                break
        rows = [["prompt", prompt or "—"]]
        bt = i                                            # branch table starts right after the keywords
        for j, kid in enumerate(kw_ids):
            kw = _prose(area, kid) or f"str #{kid}"
            bo = bt + j * 2
            if bo + 1 < len(raw):
                nop, nid = raw[bo], raw[bo + 1]
                tgt = "no change" if nop >= 0x10 else _target_label(area, nop, nid)
            else:
                tgt = "—"
            rows.append([f"keyword “{kw}”", f"→ {tgt}"])
        out.append({"title": f"KEYWORD  (grp8 @ ${ramaddr:04X})", "kind": "kv", "rows": rows})

    elif op == 0xA:   # TRANSITION — [b0][X][Y][tgt] then optional [06][cid] SERVICE chain
        b0 = raw[rec]; X = raw[rec + 1]; Y = raw[rec + 2]; tgt = raw[rec + 3]
        label = _prose(area, (b0 - 0x40)) if 0x40 <= b0 <= 0x7F else ""
        # SERVICE DOOR: the record chains [b0][X][Y][tgt=self][06][op6-cid] — a 2-op script
        # (transition-to-self draws the shop frame, then op-6 runs the service). Detect + follow.
        is_service = (rec + 5 < len(raw) and raw[rec + 4] == 0x06
                      and tgt not in (0xFF,) and tgt == area.area_id)
        if is_service:
            svc_cid = raw[rec + 5]
            rows = [["kind", "SERVICE DOOR (transition-to-self → op-6)"],
                    ["confirm prompt", "Enter new location? Y/N" if b0 & 0x40 else "none"],
                    ["mutates to", f"op-6 [{svc_cid}] (the service below)"]]
            out.append({"title": f"SHOP/DOC DOOR  (grp10 @ ${ramaddr:04X})", "kind": "kv", "rows": rows})
            rec6 = subptr(raw, area.slot, 0x6, svc_cid)
            if rec6 is not None and 0 <= rec6 < len(raw) and (raw[rec6] & 0x80):
                out.append(_service_section(area, rec6, rec6 + RAM))
        else:
            rows = [["message", label or "—"],
                    ["arrive", f"({X}, {Y})" + (" (party-relative)" if b0 & 0x80 else "")]]
            if raw[rec + 3] == 0xFF:
                rows.append(["target", "RETURN to caller"])
            else:
                rows.append(["target", f"area {tgt} — {_area_name(tgt)}"])
            out.append({"title": f"TRANSITION  (grp10 @ ${ramaddr:04X})", "kind": "kv", "rows": rows})

    elif op == 0xB:   # BUMP / NPC-DATA — grp11 record = [str-id][next-op-nibble][next-cell-id]
        # On bump: show the string, then map_cell_resolve_escape ($042E) rewrites THIS cell to
        # (next-op, next-id) — the same cell-writer as op-2 DONE / op-C. A self-rewriting chain
        # rotates messages (e.g. Downtown Needles (20,25): $1A→$1B→$1C→$1D→$1A = str 91→92→93→94).
        mid = raw[rec] & 0x7F
        rows = [["message", _prose(area, mid) or f"str #{mid}"],
                ["string id", f"{mid}"]]
        if rec + 1 < len(raw):
            nop = raw[rec + 1]
            # A STOP advance ($FF/$FE/$FD, anything >= $10) is ONE byte — the engine never reads an
            # id it will not use, so the record ends there and raw[rec+2] belongs to the NEXT record.
            # Printing it showed a different "id" before and after a re-pack: the neighbour's byte.
            rows.append(["on bump → rewrite cell to",
                         (f"op ${nop:X} {OP_NAMES.get(nop, '?')} id ${raw[rec + 2]:02X}"
                          + ("  (self / rotation)" if nop == 0xB else ""))
                         if nop < 0x10 and rec + 2 < len(raw) else "— no change (stop)"])
        out.append({"title": f"BUMP MESSAGE  (grp11 @ ${ramaddr:04X})", "kind": "kv", "rows": rows})
        # FOLLOW THE CHAIN: a self-rewriting op-B record rotates through several grp11 records
        # (each bump → the next id). Those next records are NOT map cells, so walk the chain here
        # and list every link — the whole rotation the player would see, in order.
        if rec + 2 < len(raw) and raw[rec + 1] == 0xB:
            crows, seen, nxt = [], {rec}, raw[rec + 2]
            crows.append(["1  (this cell)",
                          f"str {raw[rec] & 0x7F}: “{(_prose(area, raw[rec]) or '')[:32]}” → id ${raw[rec + 2]:02X}"])
            while len(crows) < 16:
                r2 = subptr(raw, area.slot, 0xB, nxt)
                if r2 is None or r2 + 2 >= len(raw):
                    break
                if r2 in seen:                          # closed the loop -> flag it, don't re-list
                    crows[-1][1] += "   ↺ loops back to start"
                    break
                seen.add(r2)
                crows.append([f"{len(crows) + 1}  id ${nxt:02X}",
                              f"str {raw[r2] & 0x7F}: “{(_prose(area, raw[r2]) or '')[:32]}” → id ${raw[r2 + 2]:02X}"])
                if raw[r2 + 1] != 0xB:
                    break
                nxt = raw[r2 + 2]
            if len(crows) > 1:
                out.append({"title": f"↻ ROTATION CHAIN  ({len(crows)} links)", "kind": "kv", "rows": crows})

    elif op == 0xC:   # CELL_PATCH — R+0 msg, then 5-B [flags][dx][dy][op-nibble][cell-id], bit7=last
        msg = _prose(area, raw[rec])
        patches, i = [], rec + 1
        while i + 4 < len(raw):
            flags, dx, dy, onib, cid = raw[i], raw[i + 1], raw[i + 2], raw[i + 3], raw[i + 4]
            base = "party" if flags & 1 else "(0,0)"
            patches.append(f"{base}+({_s8(dx):+d},{_s8(dy):+d}) → op ${onib:X} {OP_NAMES.get(onib, '?')} id ${cid:02X}"
                           + ("  [skip-refresh]" if flags & 0x40 else ""))
            if flags & 0x80:
                break
            i += 5
        out.append({"title": f"CELL_PATCH  (grp12 @ ${ramaddr:04X})", "kind": "list",
                    "items": ([f"msg: {msg}"] if msg else []) + (patches or ["(no entries)"])})

    # raw bytes for reference — bounded by the record's OWN extent. A fixed 16-byte window spilled
    # into the next structure, so the panel showed different "record bytes" for the same record
    # before and after a re-pack.
    _end = _section_end(_section_bounds(raw, area.slot), rec, len(raw)) if rec is not None else None
    out.append({"title": "Record bytes", "kind": "hex",
                "hex": _hexbytes(raw, rec, min(16, _end - rec) if _end else 16)})
    return out


# condition op → human label. opbyte>>5 = op (engine W902E), opbyte&$1F = id; arg follows.
# 0=skill check, 2=ATTRIBUTE check, 3=party-count, 4=money, else 1/5/6/7 = HAVE ITEM
# (inventory scan — engine $0344 = $124C = inventory_first_used_slot; NOT a flag check).
# Full map + the op-0/op-2 COND-vs-TRADE duality: cart_EF/docs/engine/OP2_COND_TRADE.md.
_COND_OP = {0: "skill check", 2: "attribute check", 3: "party-count ≥", 4: "money ≥"}

# The condition vocabulary as the EDITOR offers it. One list, so the prose a reader sees and the
# choices an author gets cannot drift — the same reason `record_fields` and `op_record_ir` share a
# file. `difficulty` says whether the opbyte's low 5 bits are a check difficulty (ops 0 and 2) or
# unused; `arg` names what the second byte means, which is the thing that was wrong for years:
# labelling every arg as an item id showed a Perception check as "Mangler".
COND_KINDS = [
    {"op": 0, "name": "skill check", "arg": "skill", "difficulty": True},
    {"op": 2, "name": "attribute check", "arg": "attribute", "difficulty": True},
    {"op": 3, "name": "party count ≥", "arg": "count", "difficulty": False},
    {"op": 4, "name": "money ≥", "arg": "amount", "difficulty": False},
    {"op": 1, "name": "have item", "arg": "item", "difficulty": False},
]


def cond_vocab() -> dict:
    """The names behind a condition's arg, keyed by condition op — so the UI can offer a list
    instead of asking for a number nobody knows."""
    return {"skill": {i: n for i, n in enumerate(_SKILL_NAMES) if n},
            "attribute": {off: nm for off, nm in _ATTR_NAMES.items()}}


def _cond_label(op: int, arg: int, diff: int = 0) -> str:
    """One condition in words. Same mapping as `_decode_conditions`, reachable without an area."""
    if op == 3:
        return f"party count ≥ {arg}"
    if op == 4:
        return f"money ≥ {arg}"
    if op == 0:
        nm = _SKILL_NAMES[arg] if arg < len(_SKILL_NAMES) and _SKILL_NAMES[arg] else ""
        return f"skill: {nm or f'#{arg}'}" + (f" · difficulty {diff}" if diff else "")
    if op == 2:
        return f"attribute: {_attr_name(arg)}" + (f" · difficulty {diff}" if diff else "")
    # op 1/5/6/7 REQUIRES the item and may spend it. The cond-walk scans the member's pack for `arg`
    # ($0344 -> $124C) and on a hit calls $0380 -> $14A5 before branching. $14A5 reads the slot's
    # second byte, and this is the whole point:
    #
    #     lda ($66),y / and #$3F / beq W14C4      <- zero: JUMP PAST the decrement
    #     sec / sbc #$01                          <- otherwise: one fewer
    #
    # So whether it is consumed is a property of the ITEM INSTANCE, not of the condition — a crowbar
    # sits in the starting pack at 0 and survives; matches sit at 40 and burn one. The data agrees:
    # 11 of the 54 wanted items are demanded more often than the game ever hands out (Sledge hammer
    # 47 wanted / 3 in existence), so those cannot be spent on every pass.
    #
    # The label therefore says what the RECORD says. "uses up" was the previous wording here and it
    # over-claimed — the record cannot know the count.
    nm = _item_name(arg) if arg in _ITEM_NAMES else f"#{arg}"
    # …and now the item table can answer it: byte 4 of the descriptor is the count an item is
    # created with, and $14A5 skips its decrement at zero. Keycards and crowbars are made with 0.
    n = _item_charges(arg)
    if n is None:
        return f"requires: {nm}"
    return f"requires: {nm} · " + ("keeps it" if n == 0 else
                                   ("uses it up" if n == 1 else f"one of {n} uses"))


def cond_impossible(op: int, arg: int) -> str:
    """Why an entry can never pass, or "" when it can.

    Cochise Lv3's grp2[15] is dispatched from two real cells, so the engine does walk it — and its
    entries are `item #255` (there are 95) and `party count >= 59` (a party holds at most 7).
    Showing those as ways into the area would be a lie of a different kind than the one the
    four-entry cap told. They are shown, and marked."""
    if op in (1, 5, 6, 7) and arg not in _ITEM_NAMES:
        return "no such item"
    if op == 3 and arg > 7:
        return "a party holds at most 7"
    if op == 2 and not (0x0E <= arg <= 0x1B):
        return "not an attribute offset"
    return ""


def _item_charges(item_id: int):
    try:
        import wl_item
        return wl_item.charges(_REPO, item_id) if _REPO else None
    except Exception:                                # noqa: BLE001 — a label is never worth a 500
        return None


   # $0200 blob (char table $29E4, offset table $2A20). Decoded via tools/wl_text5.py from
   # cart_EF/build/out/engine_0200.prg. Items share the same pool at msg id = item_id + $24, which
   # is why skill 9 (Perception) was mis-shown as item 9 ("Mangler", msg $2D). Engine capitalisation
   # kept as-is. See editor/docs/GAME_LOGIC.md §2b.


def _decode_conditions(area, off: int) -> list[dict]:
    """Decode the op-$2 condition list: 2-byte [opb][arg] entries from `off`, op = opb>>5, until $FF
    (SCENE_COND_WALK $8E49, op via W902E = opb>>5). CONSERVATIVE: a real COND block terminates with
    $FF within a few entries; a TRADE/shop record has a long ware list instead, which must NOT be
    decoded as conditions (that produced garbage like "Mangler · Throwing knife · Crowbar · …" —
    those were a weapon shop's wares). If it does not terminate cleanly, we say so rather than invent
    conditions. Condition op-0 IS a SKILL check (member_skill_lookup $1392, e.g. Perception to spot a
    cave entrance) and is shown with its verified name (_SKILL_NAMES). Still open: an ATTRIBUTE check
    as a condition (would scan stats $0E..$14) — not located in SCENE_COND_WALK yet."""
    raw = area.raw
    out, i = [], off
    # NOT 4. That cap was here to catch a shop's ware list, and it caught a fifth of the game's
    # gates instead: 130 COND records carry more than four conditions and every one of them is
    # real — the Sleeper bulkhead offers twelve ways in, Cochise Lv3 has 48. It is why the Engine
    # step was missing from The Water Purifier (six conditions) and why Events still printed
    # "trade record (shop wares)" at Highpool (29,28). The list is terminated by the record's own
    # $FF; the only thing worth guarding against is a walk that never finds one.
    MAX = 64
    while i + 1 < len(raw) and raw[i] != 0xFF:
        if len(out) >= MAX:                          # no terminator in sight → not a condition list
            return [{"op": None, "arg": None,
                     "label": f"no $FF terminator within {MAX} entries — not a condition list"}]
        opb, arg = raw[i], raw[i + 1]
        op = opb >> 5
        # Verified against SCENE_COND_WALK ($8E49) + the routines it calls:
        #   op 0 -> SKILL check: $0359 = member_skill_lookup ($1392) searches the member skill table
        #           ($80..$BB) for skill-id == arg. NOT an item (the old "give/buy item" was wrong —
        #           it read the skill id as an item id, so a Perception check showed as "Mangler").
        #   op 2 -> ATTRIBUTE check ($0212): arg = member-record stat OFFSET ($0E..$14 rolled
        #           attributes + $1B MaxCON), NOT an item id. VERIFIED 2026-07-26: across all areas
        #           the op-2 cond args cluster exactly on $0E-$14 (Str/IQ/Luck/Speed/Agi/Dex/Chr) —
        #           the guide's Mine-Shaft/Vegas "Luck check of all characters" ($10) confirms it.
        #           The old "item select: _item_name(arg)" was WRONG (showed Luck $10 as "VP91Z 9mm
        #           pistol", Agility $12 as "M17 carbine").
        #   op 3 -> party count == arg     op 4 -> money >= arg
        #   op 1/5/6/7 -> item possession ($0344 inventory scan) on arg
        #   COND-vs-TRADE DUALITY RESOLVED (2026-07-28, OP2_COND_TRADE.md §5.3): for op-0 AND op-2
        #   the opbyte's low-5-bits = the check DIFFICULTY (scale_x5 -> threshold/RNG roll; 0 = auto-
        #   pass given the skill/attr, 1-6 = harder). The "$0207 shop_buy_item"/"$0212 shop_item_select"
        #   labels are DUAL-USE routines — a difficulty roll here, a real purchase only in a shop record.
        # ONE wording, from _cond_label — the byte walker used to phrase the same condition
        # differently from the IR path ("requires skill #9 (Perception)" against "skill:
        # Perception"), so Events and the quest view described the same gate in two languages.
        lab = _cond_label(op, arg, opb & 0x1F)
        why = cond_impossible(op, arg)
        out.append({"op": op, "arg": arg, "never": why,
                    "label": lab + (f" (never — {why})" if why else "")})
        i += 2
    return out


def _grp16_section(area) -> list[dict]:
    """If this area has grp16 per-area code, show its event table."""
    raw = area.raw
    g16ram = _le(raw, area.slot + 0x26)
    if g16ram == 0:
        return []
    return [{"title": "grp16 (per-area code)", "kind": "kv",
             "rows": [["code table", f"${g16ram:04X}"],
                      ["reference", "cart_EF/docs/engine/GRP16_CARTOGRAPHY.md"]]}]


def name_forms(segs) -> tuple[str, str]:
    """A name entry's `\\n`-separated segments → the two words the ENGINE actually prints.

    The `$0A` switch is a THREE-STATE filter, not a suffix pair (`ef_engine.asm` $205E → W2063):
    segment 0 always prints, segment 1 only when the live count `$38` is 1, segment 2 only when it
    is not, and the third `$0A` reaches W208B, which clears the filter — so everything from segment
    3 on prints in BOTH forms.

    That tail is the whole point: `Madm|a|e|n` is Madman / Madmen, and `Gunm|a|e|n` is Gunman /
    Gunmen. Joining only stem+singular dropped it and showed "Madma" — for all eight of the game's
    -man/-men names.
    """
    segs = [str(s) for s in segs] or [""]
    stem, tail = segs[0], "".join(segs[3:])
    return (stem + (segs[1] if len(segs) > 1 else "") + tail,
            stem + (segs[2] if len(segs) > 2 else "") + tail)


def segs_from_forms(singular: str, plural: str = "") -> list[str]:
    """(singular, plural) → segments that print exactly those two words. Partial inverse of
    `name_forms`: it produces ONE valid encoding, not necessarily the shipped one.

    What the two words share at the FRONT is the stem, what they share at the BACK is the tail, and
    what is left between them is the singular/plural pair. The game is not consistent about where it
    puts that split — Stagecoach Inn stores `Madm|a|e|n` and the Sewers store `Clawm|an|en|` for the
    same -man/-men rule, and both print correctly. So this is only ever applied to an entry somebody
    actually RENAMED; every other entry is carried through verbatim (`_foe_name_parts` →
    `encode_foe_names`), which is what keeps a Save from churning bytes it was not asked to touch.
    """
    s, p = str(singular), str(plural or "")
    if not p or p == s:
        return [s]                       # 126 shipped entries have no plural form at all
    i = 0
    while i < len(s) and i < len(p) and s[i] == p[i]:
        i += 1
    j = 0                                # the shared tail may not eat back into the shared stem
    while j < len(s) - i and j < len(p) - i and s[len(s) - 1 - j] == p[len(p) - 1 - j]:
        j += 1
    tail = s[len(s) - j:] if j else ""
    mid_s, mid_p = s[i:len(s) - j] if j else s[i:], p[i:len(p) - j] if j else p[i:]
    return [s[:i], mid_s, mid_p, tail]   # a "" tail is the closing $0A — 276 shipped entries have it


def parts_to_segs(parts) -> tuple[list[str], bool]:
    """Split-on-`\\n` parts → the (segments, lf) pair `emit_foe_names` and the IR model speak.

    Same entry, two spellings: the walker's parts keep the closing `$0A` as a trailing empty part,
    the IR keeps it as a flag. One conversion, so neither side has to know about the other's."""
    parts = [str(x) for x in parts] or [""]
    return (parts[:-1], True) if len(parts) > 1 and parts[-1] == "" else (parts, False)


def _foe_name_parts(area) -> list[list[str]]:
    """Foe-name table at desc+$02: `$00`-prefixed entries, `\\n`(0a)-separated segments. 1-based.

    Returns the SEGMENTS. Everything that needs a word calls `name_forms` on them — a reader that
    joins here is the bug this split exists to prevent."""
    raw = area.raw
    base = _le(raw, area.slot + 0x02) - RAM
    out, i = [], base
    for _ in range(64):
        if i >= len(raw) or raw[i] != 0x00:
            break
        i += 1
        parts, cur = [], bytearray()
        while i < len(raw) and raw[i] not in (0x00,):
            if raw[i] == 0x0A:
                parts.append(cur.decode("ascii", "replace")); cur = bytearray()
            else:
                cur.append(raw[i])
            i += 1
        parts.append(cur.decode("ascii", "replace"))
        disp = name_forms(parts)[0].strip()
        if not disp or not all(32 <= ord(c) < 127 for c in disp):
            break
        out.append(parts)
    return out


def foe_name_forms(area) -> list[tuple[str, str]]:
    """The area's foe names as (singular, plural), 1-based — the editable pair."""
    return [name_forms(p) for p in _foe_name_parts(area)]


def _replaced_name(parts: list[list[str]], k: int, sing: str, plur: str) -> list:
    """The name table with entry k rebuilt from two words and every other entry untouched."""
    out = list(parts)
    if 0 <= k < len(out):
        out[k] = segs_from_forms(sing, plur)
    return out


def _foe_names(area) -> list[str]:
    """The area's foe names, SINGULAR, 1-based — what "1 Madman" prints."""
    return [name_forms(p)[0].strip() for p in _foe_name_parts(area)]



def cond_summary_ir(rr) -> str:
    """The gate in words, from the IR record rather than from a byte walk.

    `_decode_conditions` caps at four entries and calls anything longer a shop's ware list. That
    heuristic was wrong: 130 of the game's COND records carry more than four, and every one of them
    is a real condition list — the Sleeper Base bulkhead offers twelve ways in, Cochise Lv3 has 48
    with money and party-count checks among them, which no ware list would hold. The cap silently
    replaced all 130 with "trade record (shop wares)", which is how the Engine step vanished from
    The Water Purifier: its record has six conditions.

    The IR's list is terminated by the record's own $FF, so it needs no guess at all."""
    cs = rr.get("conds") or []
    if not cs:
        return "always"
    out = []
    for c in cs:
        op, arg = c["opb"] >> 5, c["arg"]
        why = cond_impossible(op, arg)
        out.append(_cond_label(op, arg, c["opb"] & 0x1F) + (f" (never — {why})" if why else ""))
    return " · ".join(out)


def _cond_summary(area, off: int) -> str:
    conds = _decode_conditions(area, off)
    return " · ".join(c["label"] for c in conds) if conds else "always"


def _tile_pair(area, tileids, col: int, row: int, new_op: int, new_cid: int) -> dict:
    """{tile_now, tile_after} for the cell at (col,row) if the map grid is available."""
    if tileids is None:
        return {"tile_now": None, "tile_after": None}
    i = row * area.width + col
    mt = tileids[i] if 0 <= i < len(tileids) else 0
    return {"tile_now": display_tile(area, area.nibble[i], area.cellid[i], mt),
            "tile_after": display_tile(area, new_op, new_cid, mt)}


def display_tile(area, op: int, cid: int, map_tile: int) -> int | None:
    """The tile the ENGINE actually draws for a cell — not necessarily the map tile.

    draw_map_cell overrides the map tile for some ops (docs/engine/OP4_OBJECTS.md):
    op-$4 OBJECT draws grp4[cellid] byte1 & $7F over it, op-$5 the loot sack, op-$9 the night
    form. That is why Base Cochise renders as a mountain while the map underneath already holds
    the base icon: the icon only becomes visible once a CELL_PATCH turns the op into $A.
    Returns None when the record cannot be resolved (dangling dispatch)."""
    if op == 0x5:
        return 5
    if op == 0x9:
        return 8
    if op == 0x4:
        rec = subptr(area.raw, area.slot, 0x4, cid)
        if rec is None or not (0 <= rec) or rec + 1 >= len(area.raw):
            return None
        return area.raw[rec + 1] & 0x7F
    return map_tile


_PRODUCERS = (0x1, 0x2, 0x3, 0x4, 0x5, 0xB, 0xC)   # ops whose record names a follow-up state (+$B BUMP)


def patch_targets(area, col: int, row: int, op: int, cid: int) -> list[dict]:
    """What the state (op, cid) at (col,row) REWRITES when it fires. The two producing ops:

      op-$2 COND/TRADE  rewrites ITS OWN cell — success pair at rec+4/+5, fail at rec+6/+7
      op-$C CELL_PATCH  rewrites a list of cells — 5-byte entries from rec+1,
                        [flags][dx][dy][op][cid], flags bit0 = party-relative, bit7 = last

    Format verified against a live capture (docs/engine/CELL_PATCH.md): 116 measured cell changes,
    116 reproduced, 0 unexplained. Party-relative entries are reported with cell=None because their
    target depends on where the party stood."""
    raw, slot, W, H = area.raw, area.slot, area.width, area.height
    out: list[dict] = []
    # EVERY scene op ends with WB105(k) = ldx $57/ldy $58/jmp $042E — it resolves the escape at
    # record offset k and writes it to the PARTY's cell, i.e. this one. So k is where a cell's
    # NEXT state is written down, and it differs per op:
    #   op-$5 LOOT k=0   (list starts at +2, so +0/+1 are free)   "take the loot -> next stage"
    #   op-$4 OBJECT k=2 (+0 msg, +1 tile)                        usually $FF = stays put
    #   op-$1 SAY    k = right after the bit7-terminated msg list
    #   op-$C PATCH  k = after the entry list (adc #$05 on the last index)
    # $FF/$FE/$FD go to map_cell_resolve_escape: $FF changes nothing, $FD/$FE swap the cell back from
    # the saved $AD/$AE pair — a write, but not a state this record names. See _is_write.
    adv = None
    if op == 0x5:
        rec = subptr(raw, slot, 0x5, cid)
        if rec is not None and rec + 1 < len(raw):
            adv = (rec, "take loot")
    elif op == 0x4:
        rec = subptr(raw, slot, 0x4, cid)
        if rec is not None and rec + 3 < len(raw):
            adv = (rec + 2, "use object")
    elif op == 0x3:
        # ENCOUNTER: 12-byte record, escape pair at +10/+11. The victory path leaves a LOOT SACK
        # (op-$5, which draw_map_cell renders as tile 5) carrying the loot id; picking it up is
        # what arms the next step. Alex described the sequence, the records confirm it:
        # grp3[0..10] all advance to op $5 with loot ids 0..9.
        rec = subptr(raw, slot, 0x3, cid)
        if rec is not None and rec + 11 < len(raw):
            adv = (rec + 10, "after the fight")
    elif op == 0x1:
        # A SAY has NO stored advance. `scene_op_say ($BDE9)` walks the ids to the bit7 terminator
        # and ends `tya / jmp WB105` — the cell advances by HOW MANY messages there were. The two
        # bytes after the list belong to the NEXT record, and reading them as a target made every
        # SAY in the game hand over to a record nothing points at.
        pass
    elif op == 0xB:                                      # BUMP self-rewrite: [str][next-op][next-id]
        rec = subptr(raw, slot, 0xB, cid)
        if rec is not None and rec + 2 < len(raw):
            adv = (rec + 1, "on bump")
    if adv is not None:
        o, label = adv
        if _is_write(raw[o]):
            out.append({"cell": [col, row], "op_nibble": raw[o], "cell_id": raw[o + 1],
                        "branch": label})
    if op == 0x2:
        rec = subptr(raw, slot, 0x2, cid)
        if rec is None or rec + 7 >= len(raw):
            return out
        for branch, (ob, cb) in (("success", (rec + 4, rec + 5)), ("fail", (rec + 6, rec + 7))):
            if _is_write(raw[ob]):
                out.append({"cell": [col, row], "op_nibble": raw[ob], "cell_id": raw[cb],
                            "branch": branch})
    elif op == 0xC:
        rec = subptr(raw, slot, 0xC, cid)
        if rec is None or not (0 <= rec < len(raw)):
            return out
        j = rec + 1
        while j + 4 < len(raw):
            flags, dx, dy, onib, ncid = raw[j], raw[j+1], raw[j+2], raw[j+3], raw[j+4]
            if _is_write(onib):
                if flags & 1:                      # party-relative: target unknown without a party
                    out.append({"cell": None, "op_nibble": onib, "cell_id": ncid,
                                "branch": "patch", "relative": [_s8(dx), _s8(dy)]})
                else:
                    tc, tr = _s8(dx), _s8(dy)
                    if 0 <= tc < W and 0 <= tr < H:
                        out.append({"cell": [tc, tr], "op_nibble": onib, "cell_id": ncid,
                                    "branch": "patch"})
            if flags & 0x80:
                j += 5
                if j + 1 < len(raw) and _is_write(raw[j]):
                    out.append({"cell": [col, row], "op_nibble": raw[j], "cell_id": raw[j + 1],
                                "branch": "after patch"})
                break
            j += 5
    return out


def area_chains(area, tileids=None) -> dict:
    """Follow the mutation CHAIN, not just one hop.

    A patch does not simply flip a cell to a final state: the state it writes is often itself a
    producer, which unlocks the next patch. Highpool's dried creek is six fights where taking each
    loot arms the next step — 116 cells end up changed, from records holding 1-4 entries each. One
    hop cannot show that, which is why this walks the transitive closure.

    Returns {"cells": {"col,row": [state, ...]}} with states in chain order: step 0 is what the map
    ships with, each later step carries the op/id it becomes, the tile it then DISPLAYS, and which
    cell caused it. Cycles terminate because a (cell, op, id) triple is only expanded once."""
    W, H = area.width, area.height
    chains: dict[str, list] = {}
    seen: set[tuple] = set()

    def tile_of(col, row, op, cid):
        if tileids is None:
            return None
        i = row * W + col
        return display_tile(area, op, cid, tileids[i] if 0 <= i < len(tileids) else 0)

    frontier = []
    for row in range(H):
        for col in range(W):
            i = row * W + col
            op, cid = area.nibble[i], area.cellid[i]
            if op in _PRODUCERS:
                key = (col, row, op, cid)
                seen.add(key)
                frontier.append((key, 0, f"{col},{row}"))   # 3rd = the ROOT this descends from

    while frontier:
        (col, row, op, cid), depth, root = frontier.pop(0)
        if depth > 200:                      # runaway guard only — Highpool's creek chain is
                                             # six fights deep, ~30 links, so 12 cut it in half
            continue
        for t in patch_targets(area, col, row, op, cid):
            if t["cell"] is None:            # party-relative — cannot place it statically
                continue
            tc, tr = t["cell"]
            nop, ncid = t["op_nibble"], t["cell_id"]
            key = (tc, tr, nop, ncid)
            if key in seen:
                continue
            seen.add(key)
            k = f"{tc},{tr}"
            if k not in chains:              # step 0 = the state the map ships with
                i = tr * W + tc
                op0, cid0 = area.nibble[i], area.cellid[i]
                chains[k] = [{"step": 0, "op_nibble": op0, "cell_id": cid0,
                              "tile": tile_of(tc, tr, op0, cid0),
                              "from": None, "branch": None,
                              **_state_text(area, op0, cid0)}]
            chains[k].append({"step": len(chains[k]), "op_nibble": nop, "cell_id": ncid,
                              "tile": tile_of(tc, tr, nop, ncid),
                              "from": [col, row], "from_state": [op, cid],
                              "branch": t.get("branch"), "root": root,
                              **_state_text(area, nop, ncid)})
            if nop in _PRODUCERS:
                frontier.append((key, depth + 1, root))
    return {"cells": chains}


def _target_label(area, onib: int, cid: int) -> str:
    """Label for a mutation target; when the target is a LOOT sack, inline what it grants."""
    base = f"→ op ${onib:X} {OP_NAMES.get(onib, '?')} #{cid}"
    if onib == 0x5:
        loot = _loot_items(area, cid)
        if loot:
            base += " (" + " · ".join(loot) + ")"
    return base


def area_mutations(area, tileids=None) -> list[dict]:
    """Enumerate every cell whose script REWRITES the map: op-2 (DONE/ALT in place) and op-C
    CELL_PATCH (offset target list). Feeds the Map tab's 'scripted changes' list + overlay.

    Per source cell: {source:[col,row], op, trigger, targets:[{cell:[col,row], op_nibble,
    cell_id, branch, label, tile_now, tile_after}], code?}. op-6 grp16 cells are flagged
    code-driven (not enumerable).

    tile_now/tile_after are what the target cell DISPLAYS before and after — resolved through
    display_tile, because for op-$4 that is a record tile and not the map tile. Without them the
    UI cannot show a state sequence like mountain -> base -> ruin. Pass `tileids` (the map grid)
    to get them; without it both stay None."""
    raw = area.raw
    slot = area.slot
    W, H = area.width, area.height
    out: list[dict] = []
    for row in range(H):
        for col in range(W):
            i = row * W + col
            op = area.nibble[i]
            disp = area.cellid[i]
            if op == 0x2:
                rec = subptr(raw, slot, op, disp)
                if rec is None or rec + 7 >= len(raw):
                    continue
                trigger = _cond_summary(area, rec + 0x0A)
                targets = []
                for branch, (ob, cb) in (("success", (rec + 4, rec + 5)), ("fail", (rec + 6, rec + 7))):
                    onib, cid = raw[ob], raw[cb]
                    if not _is_write(onib):        # $FF FF alt / escape = no map change
                        continue
                    targets.append({"cell": [col, row], "op_nibble": onib, "cell_id": cid,
                                    "branch": branch,
                                    "label": _target_label(area, onib, cid),
                                    **_tile_pair(area, tileids, col, row, onib, cid)})
                if targets:
                    out.append({"source": [col, row], "op": 2, "trigger": trigger, "targets": targets})
            elif op == 0xC:
                rec = subptr(raw, slot, op, disp)
                if rec is None:
                    continue
                targets, j = [], rec + 1
                while j + 4 < len(raw):
                    flags, dx, dy, onib, cid = raw[j], raw[j+1], raw[j+2], raw[j+3], raw[j+4]
                    if flags & 1:
                        tc, tr = col + _s8(dx), row + _s8(dy)
                    else:
                        tc, tr = _s8(dx), _s8(dy)
                    if 0 <= tc < W and 0 <= tr < H and _is_write(onib):
                        targets.append({"cell": [tc, tr], "op_nibble": onib, "cell_id": cid,
                                        "branch": "patch",
                                        "label": _target_label(area, onib, cid),
                                        **_tile_pair(area, tileids, tc, tr, onib, cid)})
                    if flags & 0x80:
                        break
                    j += 5
                if targets:
                    out.append({"source": [col, row], "op": 12,
                                "trigger": _prose(area, raw[rec]) or "patch", "targets": targets})
            elif op == 0x6:
                # grp16 code cells can mutate the map too, but only at runtime — flag, don't enumerate
                b0 = subptr(raw, slot, op, disp)
                if b0 is not None and not (raw[b0] & 0x80):
                    out.append({"source": [col, row], "op": 6, "trigger": f"grp16 event {raw[b0]}",
                                "targets": [], "code": True, "event": raw[b0]})
            elif op == 0xB:
                # BUMP self-rewrite: grp11 rec = [str][next-op][next-id]; on bump map_cell_resolve_escape
                # rewrites THIS cell to (next-op, next-id) -> a rotating message chain (e.g. str 91..94).
                rec = subptr(raw, slot, op, disp)
                if rec is None or rec + 2 >= len(raw):
                    continue
                onib, cid = raw[rec + 1], raw[rec + 2]
                if not _is_write(onib):
                    continue
                out.append({"source": [col, row], "op": 11,
                            "trigger": _prose(area, raw[rec]) or f"bump str #{raw[rec] & 0x7F}",
                            "targets": [{"cell": [col, row], "op_nibble": onib, "cell_id": cid,
                                         "branch": "on bump",
                                         "label": _target_label(area, onib, cid),
                                         **_tile_pair(area, tileids, col, row, onib, cid)}]})
    return out


def add_record(raw: bytearray, slot: int, grp: int, rec_bytes: bytes, active_ids: list[int]) -> int:
    """ADD a new subtable record to group `grp` and return its dispatch id. Safe append that never
    disturbs existing ACTIVE dispatches: the group's pointer ARRAY is relocated to the blob tail,
    extended by one entry (copying the pointers of active ids 0..N-1, new id = N points at the new
    record appended after). Only the group pointer (desc+$06+grp*2) is repointed — every other
    pointer/table stays byte-identical. `active_ids` = the dispatch ids of op-`grp` cells actually on
    the map (the ids that MUST keep resolving); ids beyond them are unreferenced and dropped. Verified
    on TREX (area 51). Caller must round-trip-decode the result to confirm before shipping."""
    def le(b, o): return (b[o] | (b[o + 1] << 8)) if o + 1 < len(b) else 0
    def wle(b, o, v): b[o] = v & 0xFF; b[o + 1] = (v >> 8) & 0xFF

    gpo = slot + 0x06 + grp * 2
    old_arr = le(raw, gpo) - RAM
    # THE ARRAY'S LENGTH IS ITS SLOT COUNT, not "up to the highest referenced id". Sizing it from
    # `active_ids` DELETED every record above that mark: Highpool's grp2 has 18 slots and 16 live
    # records, but only 9 ids are referenced from a cell or a record edge, so adding one op dropped
    # grp2[16] and grp2[17] — records the engine can still be sent to by grp16 code, a CELL_PATCH or
    # a save-state. Existence = has a valid array entry (the same rule `_group_ids` decodes by);
    # reachability is a different, playability question. `active_ids` now only raises the floor.
    slots = group_slot_count(raw, slot, grp)
    n = max(slots, (max(active_ids) + 1) if active_ids else 0)
    new_id = n
    rec_off = len(raw)
    raw += bytes(rec_bytes)                      # 1) append the new record at the blob tail
    arr_off = len(raw)                           # 2) append the new (extended) pointer array
    for i in range(new_id + 1):
        if i < n:
            p = le(raw, old_arr + i * 2) if 0 <= old_arr + i * 2 else 0
        elif i == new_id:
            p = rec_off + RAM
        else:
            p = 0
        raw += bytes([p & 0xFF, (p >> 8) & 0xFF])
    wle(raw, gpo, arr_off + RAM)              # 3) repoint the group to the new array
    return new_id


def group_referenced_ids(area, grp: int) -> set:
    """Every dispatch id of group `grp` that is REFERENCED anywhere — by a map cell OR by a
    follow-pointer inside another record. add_record's `active_ids` only sees CELL-referenced ids;
    for a group like grp11 (op-B) a rotation chain references records (27→28→29) that NO cell points
    at, and dropping them corrupts the chain. This is the complete set, so a chain writer can pick a
    head id strictly above every existing record and never clobber one.

    Follow-refs that target a group = a `[op-nibble==grp][cell-id]` pair: op-B next (rec+1/+2),
    op-2 DONE/ALT (rec+4/+5, +6/+7), op-C CELL_PATCH entries (op/id at +3/+4 of each 5-byte entry)."""
    raw, slot, W, H = area.raw, area.slot, area.width, area.height
    ids = set()
    for i in range(min(len(area.nibble), W * H)):        # 1) cell-referenced
        if area.nibble[i] == grp:
            ids.add(area.cellid[i])
    # 2) follow-refs from op-B / op-2 / op-C records of THIS area
    seen_cells = {(area.nibble[i], area.cellid[i]) for i in range(min(len(area.nibble), W * H))}
    def refs_from(op, cid):
        rec = subptr(raw, slot, op, cid)
        if rec is None:
            return
        if op == 0xB and rec + 2 < len(raw):
            if raw[rec + 1] == grp:
                ids.add(raw[rec + 2])
        elif op == 0x2 and rec + 7 < len(raw):
            for ob, cb in ((rec + 4, rec + 5), (rec + 6, rec + 7)):
                if raw[ob] == grp:
                    ids.add(raw[cb])
        elif op == 0xC:
            j = rec + 1
            while j + 4 < len(raw):
                if raw[j + 3] == grp:
                    ids.add(raw[j + 4])
                if raw[j] & 0x80:
                    break
                j += 5
    for op, cid in seen_cells:
        if op in (0x2, 0xB, 0xC):
            refs_from(op, cid)
    # 3) follow op-B chains transitively (a chain link points at the NEXT link's id, which itself
    #    may point further) so the whole ring is captured even if only its head is cell-referenced.
    frontier = list(ids)
    while frontier:
        cid = frontier.pop()
        rec = subptr(raw, slot, grp, cid) if grp == 0xB else None
        if rec is not None and rec + 2 < len(raw) and raw[rec + 1] == 0xB:
            nxt = raw[rec + 2]
            if nxt not in ids:
                ids.add(nxt); frontier.append(nxt)
    return ids


def group_array_extent(raw: bytes, slot: int, grp: int) -> int:
    """Highest id+1 that has an IN-RANGE pointer in group `grp`'s array. A chain writer must land
    ABOVE this, not just above referenced ids: a group's array can hold ORPHAN records (present in
    the blob, referenced by no cell/chain — e.g. template bump strings), and re-pointing one of
    those ids clobbers it. Tolerates a few $0000 gap-slots (unused ids).

    PRECISE END (was over-reading): a naive walk stops at the first out-of-range word, but a record's
    leading bytes right after the array read as an IN-RANGE word → a PHANTOM extra slot (the walk
    steps 1 into the first record). That phantom made _record_pointers list DATA as a pointer, which
    compaction's fixup would then rewrite → corruption (guard-caught, but load-bearing). The true end
    is where this array's own RECORDS begin: the array is [arr, first_record), records are strictly
    after `arr` in a before-layout (or entirely before it in write_cell_chain's append-layout, where
    NO target is > arr and the walk simply runs to EOF). So bound the extent by the smallest entry
    target that lies past the array start — a record cannot sit inside its own pointer array. grp15
    is additionally capped by its engine COUNT (desc+$32), which is 0 for the inactive/aliased case."""
    gpo = slot + 0x06 + grp * 2
    arr = ((raw[gpo] | raw[gpo + 1] << 8) - RAM) if gpo + 1 < len(raw) else -1
    if arr < 0:
        return 0
    ext, miss = 0, 0
    fwd = []                                               # in-range targets that lie AT/AFTER arr
    for i in range(256):
        o = arr + i * 2
        if o + 1 >= len(raw):
            break
        p = raw[o] | raw[o + 1] << 8
        fo = p - RAM
        if p >= RAM and 0 <= fo < len(raw) - 2:        # in-range pointer = a live array slot
            ext, miss = i + 1, 0
            if fo > arr:
                fwd.append(fo)
        elif p == 0:                                       # unused slot — tolerate a small gap
            miss += 1
            if miss > 8:
                break
        else:                                              # out-of-range = past the array end
            break
    if fwd:                                                # before-layout: array ends where records begin
        ext = min(ext, (min(fwd) - arr) // 2)
    if grp == 15:                                          # encounter-defs are engine-counted, not walked
        cnt = raw[slot + 0x32] if slot + 0x32 < len(raw) else 0
        ext = min(ext, cnt)
    return ext


def group_slot_count(raw: bytes, slot: int, grp: int, bounds=None) -> int:
    """How many SLOTS group `grp`'s array really has = its SPAN, not a terminator scan.

    The span is [array base, first known structure after it), tightened by the smallest forward
    target inside it — a record cannot sit inside its own pointer array. That is stricter AND wider
    than group_array_extent(): stricter because a word past the span is another structure's bytes
    (it invented grp5[255] / grp2[62] / grp6[23]), wider because the extent walk gives up after 8
    empty slots and s3_id12's grp10 array has a longer gap run — it reported 11 of 100 slots, so 67
    live transitions looked like nothing at all.

    group_array_extent() now has exactly ONE caller left: the FIRST pass of _section_bounds, where
    it is a deliberate bootstrap — this function needs bounds, so it cannot be the thing that builds
    them. The second pass then re-runs with this one and refines. That converges: a third pass adds
    no mark in any of the 43 areas.

    Everywhere else it was swapped out, because its short count caused three separate bugs in one
    day — add_record sized a rebuilt array from it and DELETED the records above (Highpool grp2: 18
    slots, 16 records, 9 referenced), chain_head allocated an id an existing record already owned,
    and _ref_ops missed an encounter's hire nibble so the NPC it offers vanished from the IR."""
    arr = gptr(raw, slot, grp)
    if arr is None or not (0 <= arr < len(raw)):
        return 0
    # PARKED pointer = not an array at all. The engine marks a group inactive by aiming its word at
    # another SECTION (foe-names is the usual choice, grp16's base also occurs) instead of zeroing
    # it. Walking "slots" there reads foe-name text or 6502 code as pointers, and those phantom
    # targets land INSIDE real records — which then measure 1-2 bytes long. Anything whose length is
    # derived from the layout (the verbatim SERVICE/EVENT records) would be truncated by that.
    # Only the three TAIL sections are unambiguous here: a group array never starts where the text
    # or foe tables start. grp16 is deliberately NOT in this list — when grp16's word and a group's
    # array coincide, either side can be the parked one (s4_id16: grp16 is parked on grp10's live
    # array, 135 cells dispatch through it), and that call needs the reference analysis in wl_ir.
    for fld in (0x00, 0x02, 0x04):
        v = _le(raw, slot + fld)
        if v and v - RAM == arr:
            return 0
    if grp == 15:                                        # engine-COUNTED, not walked (desc+$32)
        cnt = raw[slot + 0x32] if slot + 0x32 < len(raw) else 0
        if cnt == 0:
            return 0
    end = _section_end(bounds if bounds is not None else _section_bounds(raw, slot), arr, len(raw))
    fwd = [t - RAM for t in (_le(raw, o) for o in range(arr, max(arr, end - 1), 2))
           if RAM < t < RAM + len(raw) and t - RAM > arr]
    if fwd:
        end = min(end, min(fwd))
    n = max(0, (end - arr) // 2)
    if grp == 15:
        n = min(n, raw[slot + 0x32] if slot + 0x32 < len(raw) else 0)
    return n


def _section_bounds(raw: bytes, slot: int) -> list:
    """Sorted, deduped LIVE-section start offsets of a record blob. Each live section's true END is
    the NEXT boundary. Sources are all authoritative or descriptor-derived: the descriptor start,
    every descriptor pointer field (text / foe-names / foe-table / grp16 / grp17 / each group array),
    every group-array RECORD target (bounded extent, so no phantom targets pollute the set), and EOF.

    Gives region_end / the foe-table walk a HARD upper bound instead of terminator-scanning past the
    true end into the adjacent section. This is the fix for both hardening residuals the verify pass
    found: (1) over-reads that mis-listed data as pointers, and (2) the fail-closed compaction that
    could never reclaim a text orphan (its scanned end overlapped live data) → the blob grew per edit
    until it risked overflowing the $3400 record cap."""
    n = len(raw)
    b = {slot, n}
    def addb(off):
        if 0 <= off <= n:
            b.add(off)
    for fld in (0x00, 0x02, 0x04, 0x26, 0x28):            # text, foe-names, foe-table, grp16, grp17
        v = _le(raw, slot + fld)
        if v:
            addb(v - RAM)
    for g in range(19):
        if g == 16:                                      # grp16 = CODE, not a pointer array
            continue
        ga = gptr(raw, slot, g)
        if ga is None:
            continue
        addb(ga)
        for cid in range(group_array_extent(raw, slot, g)):
            t = _le(raw, ga + cid * 2)
            if t:
                addb(t - RAM)
    # SECOND PASS. group_array_extent gives up after 8 empty slots, so a SPARSE array (grp12 in
    # s1_id08 has entries 0, 18, 19 and 17 holes) contributes only its first target and every record
    # behind the gap stays invisible — which makes its NEIGHBOUR's section look far longer than it
    # is. group_slot_count spans the array instead, but needs bounds, so it runs on the first pass's
    # result. Marks only ever get added, never removed, so one refinement pass is enough.
    srt = sorted(b)
    for g in range(19):
        if g == 16:
            continue
        ga = gptr(raw, slot, g)
        if ga is None:
            continue
        for cid in range(group_slot_count(raw, slot, g, srt)):
            t = _le(raw, ga + cid * 2)
            if t:
                addb(t - RAM)
    return sorted(b)


def _section_end(bounds: list, off: int, n: int) -> int:
    """Smallest boundary strictly greater than `off` — the end of the section that contains `off`."""
    for x in bounds:
        if x > off:
            return x
    return n


def _foe_table_end(raw: bytes, slot: int) -> int:
    """End offset of the foe-record table — RELOCATION-INVARIANT (must NOT depend on where the text
    region currently sits, or area_digest stops being a compaction invariant). The foe-table is the
    last fixed section before the text, so its end is the FIRST char-table signature at/after ft —
    whether that is the live text or an orphan left by a prior relocation, both share the table head.
    Falls back to EOF if no text region follows (data areas without a real text pool)."""
    ft = _le(raw, slot + 0x04) - RAM
    live = _le(raw, slot) - RAM
    if not (0 <= ft <= len(raw)) or not (0 <= live < len(raw)):
        return len(raw)
    sig = bytes(raw[live:live + 16])
    if len(sig) < 16 or sig.count(0) > 12:
        return len(raw)
    j = raw.find(sig, ft)
    return j if j >= ft else len(raw)


def chain_head(area, grp: int) -> int:
    """The id a new chain's head should take: above every referenced id AND the array's SLOT COUNT.

    `group_array_extent` is the older, weaker measure — it stops after 8 empty slots, so on s3_id12
    it reported 11 of 100. Sizing a new chain's head from it hands out an id an existing record
    already owns, and `add_chain` then overwrites that record's array entry. `group_slot_count` is
    the span-derived count the decoder itself uses (see add_record).
    """
    keep = group_referenced_ids(area, grp)
    return max((max(keep) + 1) if keep else 0, group_slot_count(area.raw, area.slot, grp))


def rebuild_op_group(raw: bytes, area, grp: int = 0xB):
    """Rebuild group `grp` CLEAN and return (new_bytes, id_map, dropped). Only records reachable from
    a cell or a follow-ref survive (unreferenced = GC'd); survivors get fresh CONTIGUOUS ids in one
    fresh block with a freshly-built pointer array; every reference is remapped. Corruption-proof: the
    new array is built from scratch, so it can never inherit a prior array's garbage — the whole class
    of bug where the 3 overlapping editors left the pointer table inconsistent.

    grp==0xB only: 3-byte [str][next-op][next-id] bump/rotation records. next-id is remapped when it
    points at a survivor (chain links stay valid); a chain is emitted contiguously (head..tail) so the
    record-bytes view reads as one run. References remapped: cell cellid (op-B cells), op-2 DONE/ALT
    pairs whose op==$B, op-C patch entries whose op==$B.

    id_map = {old_id: new_id}; dropped = count of orphan records removed. The OLD records+array stay
    in the blob as dead space (reclaimed by compact); nothing points at them anymore."""
    if grp != 0xB:
        raise ValueError("rebuild_op_group supports grp $B only for now")
    slot, W, H = area.slot, area.width, area.height
    N = W * H
    nib = [area.nibble[i] for i in range(N)]
    cid = [area.cellid[i] for i in range(N)]

    # 1) ROOTS: every grp id entered from outside the group (cells + op-2 DONE/ALT + op-C patches).
    roots = []
    def add_root(x):
        if x not in roots:
            roots.append(x)
    for i in range(N):
        if nib[i] == grp:
            add_root(cid[i])
    for i in range(N):
        op, d = nib[i], cid[i]
        if op == 0x2:
            r = subptr(raw, slot, 0x2, d)
            if r is not None and r + 7 < len(raw):
                for ob, cb in ((r + 4, r + 5), (r + 6, r + 7)):
                    if raw[ob] == grp:
                        add_root(raw[cb])
        elif op == 0xC:
            r = subptr(raw, slot, 0xC, d)
            if r is not None:
                j = r + 1
                while j + 4 < len(raw):
                    if raw[j + 3] == grp:
                        add_root(raw[j + 4])
                    if raw[j] & 0x80:
                        break
                    j += 5

    # 2) REACHABLE closure, emitted so each chain is contiguous (walk each root's chain in order).
    order, seen = [], set()
    for root in roots:
        c = root
        while c not in seen:
            r = subptr(raw, slot, grp, c)
            if r is None or r + 2 >= len(raw):
                break                                   # dangling ref → that link can't survive
            seen.add(c); order.append(c)
            if raw[r + 1] != 0xB:                        # static bump → end of this chain
                break
            c = raw[r + 2]

    newid = {old: k for k, old in enumerate(order)}
    oldoff = {old: subptr(raw, slot, grp, old) for old in order}

    # 3) fresh contiguous records block, next-id remapped to survivors
    block = bytearray()
    for old in order:
        r = oldoff[old]
        s, nop, nid = raw[r], raw[r + 1], raw[r + 2]
        nid2 = newid[nid] if nid in newid else nid       # remap when it targets a survivor
        block += bytes([s, nop, nid2 & 0xFF])

    # 4) assemble: original blob + block + fresh pointer array; repoint the group
    new = bytearray(raw)
    block_off = len(new)
    new += block
    arr_off = len(new)
    for k in range(len(order)):
        p = block_off + k * 3 + RAM
        new += bytes([p & 0xFF, (p >> 8) & 0xFF])
    gpo = slot + 0x06 + grp * 2
    new[gpo] = (arr_off + RAM) & 0xFF
    new[gpo + 1] = ((arr_off + RAM) >> 8) & 0xFF

    # 5) remap references in the NEW blob
    nib_stride = W // 2
    cid_base = W * nib_stride
    for i in range(N):                                   # cell cellid layer
        if nib[i] == grp and cid[i] in newid:
            new[cid_base + i] = newid[cid[i]] & 0xFF
    for i in range(N):                                   # op-2 DONE/ALT + op-C patch cross-refs
        op, d = nib[i], cid[i]
        if op == 0x2:
            r = subptr(raw, slot, 0x2, d)
            if r is not None and r + 7 < len(new):
                for ob, cb in ((r + 4, r + 5), (r + 6, r + 7)):
                    if raw[ob] == grp and raw[cb] in newid:
                        new[cb] = newid[raw[cb]] & 0xFF
        elif op == 0xC:
            r = subptr(raw, slot, 0xC, d)
            if r is not None:
                j = r + 1
                while j + 4 < len(new):
                    if raw[j + 3] == grp and raw[j + 4] in newid:
                        new[j + 4] = newid[raw[j + 4]] & 0xFF
                    if raw[j] & 0x80:
                        break
                    j += 5

    # group_slot_count, not group_array_extent: the weak measure gives up after 8 empty slots, so on
    # a sparse array it UNDER-reports how many records were dropped (s3_id12 sees 11 of 100).
    _ext = group_slot_count(raw, slot, grp)
    dropped = _ext - len(order) if _ext > len(order) else 0
    return bytes(new), newid, max(0, dropped)


def chain_links(area, cid: int) -> dict:
    """Follow the op-B chain from record `cid` → {links:[{message}], loop:bool}. Lets the editor
    PRE-LOAD an existing chain so it can be edited/extended/reordered (not just created fresh)."""
    raw, slot = area.raw, area.slot
    links, seen, c, loop = [], set(), cid, False
    while True:
        if c in seen:
            loop = True
            break
        r = subptr(raw, slot, 0xB, c)
        if r is None or r + 2 >= len(raw):
            break
        seen.add(c)
        links.append({"message": raw[r] & 0x7F})
        if raw[r + 1] != 0xB:                            # static bump → chain ends (no loop)
            break
        c = raw[r + 2]
    return {"links": links, "loop": loop}


def _grp11_survivors(raw, area, exclude_cell=None):
    """Ordered list of grp11 record ids reachable from cells/cross-refs (chains contiguous), optionally
    EXCLUDING the chain rooted at one cell (col,row) — used when REPLACING that cell's chain."""
    slot, W, H = area.slot, area.width, area.height
    N = W * H
    nib = [area.nibble[i] for i in range(N)]
    cid = [area.cellid[i] for i in range(N)]
    ex_i = (exclude_cell[1] * W + exclude_cell[0]) if exclude_cell else -1
    roots = []
    def add_root(x):
        if x not in roots:
            roots.append(x)
    for i in range(N):
        if nib[i] == 0xB and i != ex_i:
            add_root(cid[i])
    for i in range(N):
        op, d = nib[i], cid[i]
        if op == 0x2:
            r = subptr(raw, slot, 0x2, d)
            if r is not None and r + 7 < len(raw):
                for ob, cb in ((r + 4, r + 5), (r + 6, r + 7)):
                    if raw[ob] == 0xB:
                        add_root(raw[cb])
        elif op == 0xC:
            r = subptr(raw, slot, 0xC, d)
            if r is not None:
                j = r + 1
                while j + 4 < len(raw):
                    if raw[j + 3] == 0xB:
                        add_root(raw[j + 4])
                    if raw[j] & 0x80:
                        break
                    j += 5
    order, seen = [], set()
    for root in roots:
        c = root
        while c not in seen:
            r = subptr(raw, slot, 0xB, c)
            if r is None or r + 2 >= len(raw):
                break
            seen.add(c); order.append(c)
            if raw[r + 1] != 0xB:
                break
            c = raw[r + 2]
    return order


def write_cell_chain(raw: bytes, area, col: int, row: int, links: list, loop: bool = True):
    """CREATE or EDIT the op-B chain at (col,row): rebuild grp11 CLEAN with the new chain in place.
    `links` = [{message}] (a link may carry next_op/next_id to end into another op). The target cell's
    OLD chain is dropped; every OTHER cell's records are preserved + renumbered; orphans are GC'd; the
    pointer array is built fresh. Returns (new_bytes, head, n_survivors). Corruption-proof by
    construction — nothing is appended onto a possibly-inconsistent array."""
    slot, W, H = area.slot, area.width, area.height
    N = W * H
    nib = [area.nibble[i] for i in range(N)]
    cid = [area.cellid[i] for i in range(N)]
    survivors = _grp11_survivors(raw, area, exclude_cell=(col, row))
    newid = {old: k for k, old in enumerate(survivors)}
    S = len(survivors)
    L = len(links)
    head = S                                             # the new chain follows the survivors

    # rebuilt survivor records (next-id remapped)
    block = bytearray()
    for old in survivors:
        r = subptr(raw, slot, 0xB, old)
        s, nop, nid = raw[r], raw[r + 1], raw[r + 2]
        block += bytes([s, nop, (newid[nid] if nid in newid else nid) & 0xFF])
    # new chain records at ids head..head+L-1
    for k, ln in enumerate(links):
        msg = int(ln.get("message", 0)) & 0x7F
        if k < L - 1:
            nop, nid = 0x0B, head + k + 1
        elif "next_op" in ln:
            nop, nid = int(ln["next_op"]) & 0xFF, int(ln.get("next_id", 0)) & 0xFF
        elif loop:
            nop, nid = 0x0B, head
        else:
            nop, nid = 0xFF, 0
        block += bytes([msg, nop, nid & 0xFF])

    # PACK: if the current grp11 array sits at the very tail, this rebuild fully replaces the block+
    # array that precede it → truncate that dead tail first so REPEATED edits don't grow the blob.
    # (Only when the tail is exactly [ext 3-byte records][ext-word array] — otherwise leave it.)
    reclaim_at = len(raw)
    gpo = slot + 0x06 + 0xB * 2
    cur_arr = (raw[gpo] | (raw[gpo + 1] << 8)) - RAM
    # the true SPAN: this then checks the array sits exactly at the blob tail with a contiguous
    # record block in front of it, and a short count would make that check miss (or, worse, pass on
    # a wrong block).
    ext = group_slot_count(raw, slot, 0xB)
    if 0 <= cur_arr and cur_arr + ext * 2 == len(raw) and ext > 0:
        block_start = cur_arr - ext * 3
        offs = [subptr(raw, slot, 0xB, k) for k in range(ext)]
        if block_start >= 0 and offs == [block_start + k * 3 for k in range(ext)]:
            reclaim_at = block_start                     # exact contiguous tail block → reclaim it

    new = bytearray(raw[:reclaim_at])
    block_off = len(new)
    new += block
    arr_off = len(new)
    for k in range(S + L):
        p = block_off + k * 3 + RAM
        new += bytes([p & 0xFF, (p >> 8) & 0xFF])
    new[gpo] = (arr_off + RAM) & 0xFF
    new[gpo + 1] = ((arr_off + RAM) >> 8) & 0xFF

    # remap references: cells (target → head), op-2/op-C cross-refs
    nib_stride = W // 2
    cid_base = W * nib_stride
    tgt_i = row * W + col
    for i in range(N):                                   # OTHER op-B cells → renumbered id
        if i != tgt_i and nib[i] == 0xB and cid[i] in newid:
            new[cid_base + i] = newid[cid[i]] & 0xFF
    # target cell → op-B pointing at the new chain head (works whatever its old op was)
    new[cid_base + tgt_i] = head & 0xFF
    noff = row * nib_stride + (col >> 1)
    if (col & 1) == 0:
        new[noff] = (0xB << 4) | (new[noff] & 0x0F)
    else:
        new[noff] = (new[noff] & 0xF0) | 0xB
    for i in range(N):
        op, d = nib[i], cid[i]
        if op == 0x2:
            r = subptr(raw, slot, 0x2, d)
            if r is not None and r + 7 < len(new):
                for ob, cb in ((r + 4, r + 5), (r + 6, r + 7)):
                    if raw[ob] == 0xB and raw[cb] in newid:
                        new[cb] = newid[raw[cb]] & 0xFF
        elif op == 0xC:
            r = subptr(raw, slot, 0xC, d)
            if r is not None:
                j = r + 1
                while j + 4 < len(new):
                    if raw[j + 3] == 0xB and raw[j + 4] in newid:
                        new[j + 4] = newid[raw[j + 4]] & 0xFF
                    if raw[j] & 0x80:
                        break
                    j += 5
    return bytes(new), head, S


def add_chain(raw: bytearray, slot: int, grp: int, records: list, head: int) -> int:
    """Append a CHAIN of records to group `grp` as one contiguous block; return the HEAD id (=`head`).
    `head` MUST come from chain_head() so it clears every existing record (referenced OR orphan);
    the N records get ids head..head+N-1. The new pointer array copies EVERY id 0..head-1 from the
    old array (so all existing records survive) and appends the N new offsets. The caller builds
    each record's follow-ref against `head` (link k → head+k+1, last → head for a loop or $FF for a
    stop). Round-trip-verify before shipping."""
    def le(b, o): return (b[o] | (b[o + 1] << 8)) if o + 1 < len(b) else 0
    def wle(b, o, v): b[o] = v & 0xFF; b[o + 1] = (v >> 8) & 0xFF
    gpo = slot + 0x06 + grp * 2
    old_arr = le(raw, gpo) - RAM
    offs = []
    for rb in records:                           # 1) append the N records contiguously
        offs.append(len(raw))
        raw += bytes(rb)
    arr_off = len(raw)                           # 2) new pointer array: ids 0..head-1 copied, then N new
    for i in range(head + len(records)):
        if i < head:
            p = le(raw, old_arr + i * 2) if old_arr >= 0 else 0
        else:
            p = offs[i - head] + RAM
        raw += bytes([p & 0xFF, (p >> 8) & 0xFF])
    wle(raw, gpo, arr_off + RAM)              # 3) repoint the group
    return head


# ── GENERAL script chains (heterogeneous ops) ────────────────────────────────
# Every chainable op ends its interaction by rewriting the cell to (next-op, next-id): next-op < $10
# continues the chain, >= $10 (e.g. $FF) stops it. So a chain is a cell state machine, NOT op-B-only
# — the record SHAPE + advance-SLOT differ per op (all verified against real Highpool records via
# area_chains): $B BUMP [str][nop][nid] · $1 SAY [str|$80][nop][nid] · $5 LOOT [nop][nid][item,qty..][$FF].
_CHAINABLE_OPS = (0x1, 0x5, 0xB)

def _chain_step_record(op: int, params: dict, nop: int, nid: int) -> bytes:
    """One chainable script record whose ADVANCE edge (rewrite-cell-to) is (nop, nid)."""
    m = int(params.get("message", 0)) & 0x7F
    if op == 0xB:                                        # BUMP  [str][nop][nid]        advance +1/+2
        return bytes([m, nop & 0xFF, nid & 0xFF])
    if op == 0x1:                                        # SAY — the message list, terminated. No tail:
        return bytes([m | 0x80])                         #   the engine advances by the message COUNT
    if op == 0x5:                                        # LOOT  [nop][nid][item,qty..][$FF]  advance +0/+1
        r = bytearray([nop & 0xFF, nid & 0xFF])
        for it in (params.get("items") or [])[:12]:
            r += bytes([int(it["item"]) & 0xFF, int(it.get("qty", 1)) & 0xFF])
        r.append(0xFF)
        return bytes(r)
    raise ValueError(f"op ${op:X} is not chain-authorable yet (have $1 SAY / $5 LOOT / $B BUMP)")


def write_script_chain(raw: bytes, area, col: int, row: int, steps: list, loop: bool = True):
    """CREATE a heterogeneous script chain at (col,row). `steps` = ordered [{op, message?/items?}].
    Each step becomes a record in its op's group (group index == op nibble for $1/$5/$B); consecutive
    steps are wired via next-op/next-id at each op's advance slot, so ONE interaction walks the whole
    sequence (bump→bump→LOOT→bump). Last step loops to the head (`loop`) or stops ($FF). The (col,row)
    cell is repointed at step 0. Returns (new_bytes, head_op, head_id).

    IDs are allocated ABOVE every referenced record per group (chain_head) so nothing existing is
    clobbered; records are APPENDED. Re-editing a chain orphans its old records (a group-record GC
    would reclaim them — not built yet), so CREATE is clean, repeated EDITs of one cell grow the blob."""
    from collections import defaultdict
    steps = [dict(st) for st in steps]
    for st in steps:
        if int(st["op"]) not in _CHAINABLE_OPS:
            raise ValueError(f"op ${int(st['op']):X} not chainable")
        st["_grp"] = int(st["op"]) & 0x0F
    heads, ctr = {}, defaultdict(int)                    # id per step, per group, in step order
    for st in steps:
        g = st["_grp"]
        if g not in heads:
            heads[g] = chain_head(area, g)
        st["_id"] = heads[g] + ctr[g]
        ctr[g] += 1
    def adv(k):                                          # advance edge of step k
        if k < len(steps) - 1:
            n = steps[k + 1]
        elif loop:
            n = steps[0]
        else:
            return 0xFF, 0
        return int(n["op"]) & 0xFF, n["_id"] & 0xFF
    recs = defaultdict(list)                             # grp -> [(id, record_bytes)]
    for k, st in enumerate(steps):
        nop, nid = adv(k)
        recs[st["_grp"]].append((st["_id"], _chain_step_record(int(st["op"]), st, nop, nid)))
    new = bytearray(raw)
    for g, items in recs.items():
        items.sort()
        add_chain(new, area.slot, g, [rb for _i, rb in items], heads[g])
    # repoint the (col,row) cell at step 0 (rewrite its grid nibble + cellid directly)
    W = area.width
    nib_stride = W // 2
    cid_base = nib_stride * area.height
    s0 = steps[0]
    new[cid_base + row * W + col] = s0["_id"] & 0xFF
    noff = row * nib_stride + (col >> 1)
    if (col & 1) == 0:
        new[noff] = ((int(s0["op"]) & 0xF) << 4) | (new[noff] & 0x0F)
    else:
        new[noff] = (new[noff] & 0xF0) | (int(s0["op"]) & 0xF)
    return bytes(new), int(s0["op"]) & 0xF, s0["_id"] & 0xFF


def area_transitions(area) -> list[dict]:
    """Enumerate every op-$A TRANSITION cell of this area — the raw material for the teleport
    reverse-index and the overworld→subarea tree. Each op-$A record = [b0][X][Y][tgt][$FF]:
      b0 bit7 = party-relative (X/Y is a DELTA, no absolute arrival → not a teleport target)
      b0&$7F (when $40..$7F) = the 'Entering …' label string id
      X,Y     = ABSOLUTE arrival cell inside tgt (the game's own drop point) unless party-relative
      tgt     = target area-id ($FF = RETURN to caller, 0 = overworld, ≥$80 = shared house)
    Returns {cell:[col,row], target, arrive:[X,Y], message, party_relative, is_return}."""
    raw = area.raw
    slot = area.slot
    W, H = area.width, area.height
    out: list[dict] = []
    active_idx = set()
    for row in range(H):
        for col in range(W):
            i = row * W + col
            if area.nibble[i] != 0xA:
                continue
            idx = area.cellid[i]
            active_idx.add(idx)
            rec = subptr(raw, slot, 0xA, idx)
            if rec is None or rec < 0 or rec + 3 >= len(raw):   # rec<0 = dangling template pointer (blank areas)
                continue
            b0, X, Y, tgt = raw[rec], raw[rec + 1], raw[rec + 2], raw[rec + 3]
            out.append({
                "cell": [col, row], "target": tgt, "arrive": [X, Y],
                "message": (_prose(area, b0 - 0x40) if 0x40 <= b0 <= 0x7F else "").strip(),
                "party_relative": bool(b0 & 0x80), "is_return": tgt == 0xFF, "source": "cell",
            })
    # subtable-only op-$A entries: transition records that live in grp10 but are NOT wired to an
    # active op-$A cell — either conditionally revealed (an op-2/op-C flips a cell to op-$A with that
    # id) or vestigial. They still carry a real ABSOLUTE arrival the active-cell scan alone misses —
    # e.g. Cochise Core (area 20) is reachable only this way (from Lv2 id$0C arrive 16,10 / Lv3 id$09
    # arrive 16,16). Validated by the [$FF] record terminator + a plausible target id.
    for idx in range(min(48, group_slot_count(raw, slot, 0xA))):   # SLOTS, not a fixed 48: past the
        if idx in active_idx:                                      # array's end the "pointers" are
            continue                                               # another structure's bytes
        try:
            rec = subptr(raw, slot, 0xA, idx)
        except (IndexError, ValueError):
            continue
        if rec is None or rec < 0 or rec + 4 >= len(raw) or raw[rec + 4] != 0xFF:
            continue
        b0, X, Y, tgt = raw[rec], raw[rec + 1], raw[rec + 2], raw[rec + 3]
        if not (tgt <= 51 or tgt == 0xFF):
            continue
        out.append({
            "cell": None, "target": tgt, "arrive": [X, Y],
            "message": (_prose(area, b0 - 0x40) if 0x40 <= b0 <= 0x7F else "").strip(),
            "party_relative": bool(b0 & 0x80), "is_return": tgt == 0xFF, "source": "subtable",
        })
    return out


# ── editable script fields (Slice 2) — each field is a single record byte at a fixed offset, so
#    edits are always IN PLACE (no record resize → pointer tables stay valid). off = blob file
#    offset. kind drives the frontend widget + hint. ──
def _fld(off, label, kind, val, hint=""):
    return {"off": off, "label": label, "kind": kind, "value": val, "hint": hint}


def editable_fields(area, col: int, row: int, op: int, disp: int) -> list[dict]:
    raw = area.raw
    rec = subptr(raw, area.slot, op, disp) if op in (0x1, 0x2, 0x4, 0x5, 0x6, 0x8, 0x9, 0xA, 0xB, 0xC) else None
    if rec is None:
        return []
    F: list[dict] = []

    def strf(o, lab):
        if o < len(raw):
            sid = raw[o] & 0x7F
            fld = _fld(o, lab, "strid", raw[o], f"str #{sid}")
            fld["str_id"] = sid
            fld["text"] = _prose(area, raw[o])     # decoded prose, editable in place
            F.append(fld)

    def opnib(o, lab):
        if o < len(raw):
            v = raw[o]
            F.append(_fld(o, lab, "opnibble", v,
                          OP_NAMES.get(v, "escape / no-write") if _is_write(v) else "escape / no-write"))

    if op == 0x2:      # COND/TRADE — the core logic op
        strf(rec + 1, "prompt")
        strf(rec + 2, "on success")
        strf(rec + 3, "on fail")
        opnib(rec + 4, "DONE op")
        F.append(_fld(rec + 5, "DONE cell-id", "u8", raw[rec + 5]))
        opnib(rec + 6, "ALT op")
        F.append(_fld(rec + 7, "ALT cell-id", "u8", raw[rec + 7]))
        if rec + 9 < len(raw):
            F.append(_fld(rec + 8, "effect target", "u8", raw[rec + 8],
                          {0x1D: "damage", 0x15: "money", 0x21: "XP"}.get(raw[rec + 8] & 0x7F, "")))
            F.append(_fld(rec + 9, "effect value", "u8", raw[rec + 9]))
        # conditions [opbyte][arg] until $FF. ONE wording — _cond_label — like everywhere else; this
        # block used to carry a fourth dialect of its own ("op 1" / "have item: Engine" beside the
        # quest view's "requires: Engine · uses it up"). And no 8-entry cap: the game's longest real
        # list is 48, and cutting it here hid the entries an editor is most likely to be looking for.
        i, n = rec + 0x0A, 0
        while i + 1 < len(raw) and raw[i] != 0xFF and n < 64:
            opb, arg = raw[i], raw[i + 1]
            cop, diff = opb >> 5, opb & 0x1F
            lab = _cond_label(cop, arg, diff)
            why = cond_impossible(cop, arg)
            F.append(_fld(i, f"cond {n} op", "u8", opb, lab.split(":")[0].split(" ·")[0]))
            F.append(_fld(i + 1, f"cond {n} arg",
                          "itemid" if cop in (1, 5, 6, 7) else "u8", arg,
                          lab + (f"  (never — {why})" if why else "")))
            i += 2; n += 1

    elif op == 0xA:    # TRANSITION [b0][X][Y][tgt]
        F.append(_fld(rec + 1, "arrive X", "u8", raw[rec + 1]))
        F.append(_fld(rec + 2, "arrive Y", "u8", raw[rec + 2]))
        if rec + 3 < len(raw):
            F.append(_fld(rec + 3, "target area", "areaid", raw[rec + 3],
                          "RETURN" if raw[rec + 3] == 0xFF else _area_name(raw[rec + 3])))

    elif op == 0xC:    # CELL_PATCH — per 5-byte entry
        strf(rec, "message")
        i, n = rec + 1, 0
        while i + 4 < len(raw) and n < 12:
            F.append(_fld(i, f"patch {n} flags", "u8", raw[i], "bit0=party-rel bit6=skip bit7=last"))
            F.append(_fld(i + 1, f"patch {n} dx", "s8", raw[i + 1]))
            F.append(_fld(i + 2, f"patch {n} dy", "s8", raw[i + 2]))
            opnib(i + 3, f"patch {n} op")
            F.append(_fld(i + 4, f"patch {n} cell-id", "u8", raw[i + 4]))
            if raw[i] & 0x80:
                break
            i += 5; n += 1

    elif op == 0x4:    # MSG — single string
        strf(rec, "message")

    elif op == 0x9:    # PARTY_FLAG_SWEEP — msg + damage dice
        strf(rec, "message")
        if rec + 1 < len(raw):
            F.append(_fld(rec + 1, "damage dice", "u8", raw[rec + 1], "× RNG roll → CON, every member"))

    elif op == 0xB:    # BUMP — [str][next-op][next-id] rotating chain
        strf(rec, "message")
        opnib(rec + 1, "on-bump op")
        if rec + 2 < len(raw) and raw[rec + 1] < 0x10:    # a STOP op has no id byte — see inspect_op
            F.append(_fld(rec + 2, "on-bump cell-id", "u8", raw[rec + 2]))

    elif op == 0x5:    # LOOT — item/qty pairs, MONEY is a 3-byte entry (loot_entries owns the walk)
        strf(rec, "message")
        i = rec + 2
        for n, e in enumerate(loot_entries(raw, rec)[0][:12]):
            F.append(_fld(i, f"item {n}", "itemid", e["item"], _item_hint(e["item"])))
            if "qty16" in e:                             # [$5E|bit7][lo][hi] — a 16-bit amount, and
                F.append(_fld(i + 1, f"item {n} amount", "u16", e["qty16"]))   # editing it as a
                i += 3                                   # single byte truncated it to 0..255
            else:
                F.append(_fld(i + 1, f"item {n} qty", "u8", e["qty"]))
                i += 2

    elif op in (0x1, 0x4, 0x9):   # SAY / sweep — first string
        strf(rec, "message")

    return F


def area_hireables(area, side: int) -> list[dict]:
    """Hireable NPCs (grp17) + their SPAWN CELLS — docs/engine/NPC_SPAWN.md recipe.
    Chain: op-$3/op-$F marker cell → grp3/grp15[cell-id] 12-B foe-type rec → byte+9
    bits4-7 = grp17 index (0 = none), bit1 = peaceful/hireable → grp17[n] = a full
    $100-byte party-member record. POSITION = the marker cell itself. Guards: ptr 0 /
    out-of-blob = absent (blank areas); grp17 entries validated by ASCII name."""
    raw, slot = area.raw, area.slot
    gp = [_le(raw, slot + 6 + g * 2) for g in range(19)]
    W = area.width
    reach: dict = {}
    for i, nib in enumerate(area.nibble):
        if nib in (0x3, 0xF):
            reach.setdefault((nib, area.cellid[i]), []).append([i % W, i // W])
    g17 = gp[17]
    if not (RAM <= g17 < RAM + len(raw)):
        return []
    names = _foe_names(area)
    foetab = _le(raw, slot + 0x04) - RAM
    by_n: dict = {}
    for (op, cid), cells in sorted(reach.items()):
        base = gp[3 if op == 0x3 else 15]
        if base == 0 or not (RAM <= base < RAM + len(raw)):
            continue
        po = base + cid * 2 - RAM
        if po + 1 >= len(raw):
            continue
        p = _le(raw, po)
        if not (RAM < p <= RAM + len(raw) - 12):
            continue
        rec = p - RAM
        n = raw[rec + 9] >> 4
        if n == 0:
            continue
        qo = g17 + n * 2 - RAM
        if qo + 1 >= len(raw):
            continue
        q = _le(raw, qo)
        if not (RAM < q <= RAM + len(raw) - 0x40):
            continue
        ro = q - RAM
        nb = bytes(raw[ro:ro + 14]).split(b"\x00")[0]
        if not (2 <= len(nb) <= 13 and all(32 <= c < 127 for c in nb)):
            continue                                    # alias/garbage → not a live NPC
        fid = raw[rec + 3] & 0x7F                       # first foe slot → portrait/name/glyph
        fr = foetab + fid * 8
        stri = raw[fr + 7] if (fid and 0 <= fr and fr + 7 < len(raw)) else None
        # Standing-figure tile: the engine draws $5BF5[foe-rec byte6] (ENCOUNTER_SCAN,
        # ef_main_overlay.asm:4030 "draw marker glyph $5BF5,y via $22D8"; $5BF5 is never
        # written, i.e. a fixed lookup). Table read out of live RAM in area 51, 2026-07-21:
        #     byte6  0  1  2  3  4  5  6  7  8  9
        #     tile   0  6  3  4  2  1  1  1  1  1
        # Christina (byte6=$03 -> tile 4) matches, which validates the read. It is NOT byte6+1 —
        # that old approximation only happened to fit her. Only SIX tiles are reachable at all:
        # {0,1,2,3,4,6}; tile 5 and everything from 7 up cannot be an NPC figure.
        gcls = raw[fr + 6] & 0x7F if (fid and 0 <= fr and fr + 6 < len(raw)) else None
        glyph = GLYPH_TABLE[gcls] if gcls is not None and gcls < len(GLYPH_TABLE) else None
        e = by_n.setdefault(n, {
            "grp17_index": n, "name": nb.decode("ascii"), "side": side, "cells": [],
            "portrait_str": stri, "glyph": glyph,       # foe-table byte6 = the tile the engine draws on the cell
            "foe_name": names[fid - 1] if 0 < fid <= len(names) else "",
            "stats": list(raw[ro + 0x0E:ro + 0x15]),    # ST IQ LK SP AGL DEX CHR
            "maxcon": raw[ro + 0x1B] | (raw[ro + 0x1C] << 8),
            "join_str": raw[ro + 0x30], "hire_gate": raw[ro + 0x31],
            "money": raw[ro + 0x15] | (raw[ro + 0x16] << 8) | (raw[ro + 0x17] << 16),
            "rank": bytes(raw[ro + 0x32:ro + 0x4A]).split(b"\x00")[0].decode("ascii", "replace"),
            "hireable": bool(raw[rec + 9] & 0x02),
        })
        e["cells"].extend(cells)
    return list(by_n.values())


def area_npcs(area, side: int) -> list[dict]:
    """Enumerate the area's FOES — name, portrait, and whether the area actually uses them.

    Both halves of that used to be wrong. The table END was guessed from record CONTENT ("byte1 is
    0, byte7 is non-zero"), which fails on Base Cochise's very first record (`00 7D 01 …`) and made
    the editor list no foes at all for that area; the count is the NAME count (wl_foe._foe_table).
    And "used" came from a scan of MAP CELLS, so an encounter reached through a record edge, an op-A
    door tail or the engine-iterated grp15 table counted as unused — 100 of 279 live foes across 34
    of the 43 areas, shown as inert template leftovers. It now comes from the reference GRAPH.

    Pairing is POSITIONAL: name k-1 belongs to record k (byte7 = portrait str id). byte3 is NOT the
    name index — that was the long-standing bug in wl_portrait_names.py (Gila Monitor resolved to a
    man); positional gives the lizard str54, Dire Coyote the dog str10, and reproduces the Leroy's
    trace anchor Woman -> str78. Deduped by name, first portrait wins.
    """
    import wl_foe
    out, seen = [], set()
    for fo in wl_foe.foes(area, side):
        if not fo["name"] or fo["name"] in seen or fo["portrait_str"] is None:
            continue
        seen.add(fo["name"])
        out.append({"str_id": fo["portrait_str"], "name": fo["name"], "side": side,
                    "used": fo["used"], "foe_id": fo["id"], "cells": fo.get("cells", []),
                    "referenced_by": fo["referenced_by"], "hire_slot": fo["hire_slot"]})
    return out

def text_usage(area) -> dict[int, list[dict]]:
    """String-id → every place the area's script shows that text in-game.
    Mirrors the _decode_record offsets per op format (SAY chain, COND prompt/success/
    fail, sweep/LOOT/CELL_PATCH message, KEYWORD prompt+chain, op-A label b0-$40,
    store greeting rec+5, grp17 join line +$30). Active cells only (subtable-only
    records are conditional/vestigial — out of scope here)."""
    raw, slot, W = area.raw, area.slot, area.width
    use: dict[int, list[dict]] = {}

    def add(sid, col, row, role):
        sid &= 0x7F
        if sid:
            use.setdefault(sid, []).append({"cell": [col, row], "role": role})

    seen = set()
    for i, op in enumerate(area.nibble):
        cid = area.cellid[i]
        col, row = i % W, i // W
        if op not in (0x1, 0x2, 0x4, 0x5, 0x6, 0x8, 0x9, 0xA, 0xC):
            continue
        if (op, cid) in seen:                 # same record → one reference is enough
            continue
        seen.add((op, cid))
        rec = subptr(raw, slot, op, cid)
        if rec is None or rec < 0 or rec >= len(raw):
            continue
        if op == 0x1:
            j = rec
            while j < len(raw):
                b = raw[j]; j += 1
                add(b, col, row, "SAY")
                if b & 0x80:
                    break
        elif op == 0x2:
            for off, role in ((1, "prompt"), (2, "on success"), (3, "on fail")):
                if rec + off < len(raw):
                    add(raw[rec + off], col, row, role)
        elif op in (0x4, 0x9):
            add(raw[rec], col, row, "sweep msg")
        elif op in (0x5, 0xC):
            add(raw[rec], col, row, "LOOT msg" if op == 0x5 else "PATCH msg")
        elif op == 0x6:
            b0 = raw[rec]
            if b0 & 0x80 and (b0 & 0x7F) == 1 and rec + 5 < len(raw):
                add(raw[rec + 5], col, row, "shop greeting")
        elif op == 0x8:
            add(raw[rec], col, row, "keyword prompt")
            j = rec + 3
            while j < len(raw):
                b = raw[j]; j += 1
                add(b, col, row, "keyword")
                if b & 0x80:
                    break
        elif op == 0xA:
            b0 = raw[rec]
            if 0x40 <= b0 <= 0x7F:
                add(b0 - 0x40, col, row, "transition msg")
    # op-B walls: grp11 record = [str-id][$FF] -> the bump message
    for i, op in enumerate(area.nibble):
        if op != 0xB:
            continue
        cid = area.cellid[i]
        if (0xB, cid) in seen:
            continue
        seen.add((0xB, cid))
        rec = subptr(raw, slot, 0xB, cid)
        if rec is not None and 0 <= rec < len(raw):
            add(raw[rec], i % W, i // W, "bump msg")
    return use


def area_grp16(area) -> dict:
    """The grp16 per-area code table: which event ids the script fires (op-6 records
    with byte0 bit7=0) + a minimal disassembly of each (GRP16_SPEC.md: flat LE16 ptr
    array at desc+$26, indexed by event id; entry $0000 = unused; the fragment runs
    with X = event id, ($5F/$60) = record base, RTS carry -> VIEW_REFRESH)."""
    import wl_disasm6502 as d65
    raw, slot, W = area.raw, area.slot, area.width
    g16 = _le(raw, slot + 0x26)
    if g16 == 0:
        return {"table": None, "events": []}
    tbase = g16 - RAM
    # event ids actually fired by op-6 cells (bit7=0 -> grp16)
    fired: dict[int, list[list[int]]] = {}
    seen = set()
    for i, op in enumerate(area.nibble):
        if op != 0x6:
            continue
        cid = area.cellid[i]
        rec = subptr(raw, slot, 0x6, cid)
        if rec is None or not (0 <= rec < len(raw)):
            continue
        b0 = raw[rec]
        if b0 & 0x80:
            continue
        fired.setdefault(b0, []).append([i % W, i // W])
    # The table's LENGTH is derived, not scanned: it sits immediately before its lowest handler, so
    # count = (min live entry - g16)/2 (GRP16_SPEC §2), and `wl_grp16.jump_table` is the one place
    # that knows it. This used to walk a fixed 32 entries and keep whichever ones "disassembled
    # cleanly" — a heuristic that read past the real table into the next structure and invented
    # events (s1_id09 grew a phantom event 25 whose bytes belonged to the neighbour, and it appeared
    # or vanished depending on what a re-pack put there).
    from wl_grp16 import jump_table
    import wl_ir
    lo, hi = wl_ir.grp16_extent(raw, slot)
    tbl = jump_table(raw[lo:hi], lo + RAM) if lo is not None else []
    events = []
    for ev, ptr in enumerate(tbl):
        if not ptr and ev not in fired:               # $0000 = this id has no handler
            continue
        entry = {"event": ev, "cells": fired.get(ev, []), "ptr": ptr, "listing": [],
                 "fired": ev in fired}
        if RAM < ptr < RAM + len(raw):
            entry["listing"] = d65.disasm(raw, ptr - RAM, ptr)
        events.append(entry)
    return {"table": g16, "events": events}


# ── NPC authoring (generalized from the TREX one-off surgery, 2026-07-19) ──
# Everything is RELOCATION-APPEND: tables move to the blob tail + the descriptor
# pointer is repointed — nothing else shifts, no pointer/table invalidates.

# canonical $100 party-member record template = CHRISTINA (s2 area 32, grp17[1]);
# name/stats/portrait etc. get patched per NPC, skills/inventory stay as sane defaults.
import base64 as _b64
_NPC_TEMPLATE = _b64.b64decode(
    "Q0hSSVNUSU5BAAAAAAARDxENDhENyAAAAQAFHgAYAAIDQB8ABAEAAAABBQMDAgMHMABTZW5pb3IgU3BlY2lhbGlz"
    "dAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABAwIC"
    "AwMGAgwDCwIKAxADCAIWAwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAK/8AKQAWAA8ALAAJ"
    "ABAAMAA5AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==")

# 8-byte foe record template = 'Woman' (Christina's): combat stats stay, byte6 glyph-1
# and byte7 portrait get patched.
_NPC_FOE_TEMPLATE = bytes([0x49, 0x00, 0x11, 0x06, 0x11, 0x66, 0x03, 0x4E])

# $5BF5 — the engine's NPC/foe marker-glyph table, read out of live RAM (area 51, 2026-07-21).
# The drawn map tile is GLYPH_TABLE[foe-rec byte6]; the table is fixed (never written). byte6 is
# the foe's KIND, though — the tile is only one of its three consequences (see wl_foe.FOE_KINDS),
# so `glyph_tile` is the old, narrow way in and `kind` the honest one. Both still work.
GLYPH_TABLE = [0, 6, 3, 4, 2, 1, 1, 1, 1, 1]


def _foe_stats(rec8: bytes, spec: dict) -> bytes:
    """Apply whichever named combat fields `spec` carries onto an 8-byte foe record."""
    import wl_foe
    return wl_foe.pack_stats(rec8, spec)


def glyph_class_for_tile(tile: int) -> int:
    """Lowest byte6 that makes the engine draw `tile`. Only {0,1,2,3,4,6} are reachable."""
    tile = int(tile)
    if tile not in GLYPH_TABLE:
        raise ValueError(f"tile {tile} cannot be an NPC figure — reachable tiles are "
                         f"{sorted(set(GLYPH_TABLE))}")
    return GLYPH_TABLE.index(tile)


# 12-byte foe-TYPE record template (grp3): byte3 = first-foe-id, byte9 = (n<<4)|flags.
_NPC_FOETYPE_TEMPLATE = bytes([0x3C, 0x3C, 0x2F, 0x05, 0x01, 0, 0, 0, 0, 0x13, 0, 0])


def _names_true_end(raw, nbase) -> int:
    """Walk the foe-name table exactly like _foe_names: offset just past the last valid
    entry (SPLICE TRAP: pad bytes may sit between the names and the next table — append
    there, not at the next-table boundary, or the reader stops early)."""
    i = nbase
    while i < len(raw) and raw[i] == 0x00:
        j = i + 1
        while j < len(raw) and raw[j] not in (0x00, 0x01):
            j += 1
        txt = bytes(raw[i + 1:j])
        if not txt or not all(32 <= c < 127 or c == 0x0A for c in txt):
            break
        i = j
    return i


def _npc_used_ns(raw, area) -> list[int]:
    """grp17 indices referenced by the area's op-$3/op-$F foe-type records — from the REFERENCE
    GRAPH, not from a scan of map cells.

    This sizes the grp17 table, and the table has no length field, so the measure decides which
    index a NEW npc gets. A cell scan cannot see an encounter reached through a record edge, an
    op-A door tail or the engine's own grp15 walk, so the table read short and `add_npc` handed the
    new NPC an index an existing hire reference already pointed at — in 6 of the 43 areas.
    """
    import wl_foe
    try:
        return sorted(wl_foe.hire_slots(area))
    except Exception:                                    # never let the analysis break a write path
        slot = area.slot
        ns = set()
        for i, op in enumerate(area.nibble):
            if op in (0x3, 0xF):
                rec = subptr(raw, slot, op, area.cellid[i])
                if rec is not None and 0 <= rec < len(raw) - 12 and raw[rec + 9] >> 4:
                    ns.add(raw[rec + 9] >> 4)
        return sorted(ns)


def _grp17_entries(raw, slot, area) -> list[int]:
    """The grp17 table as list of record PTRs, index = n (entry 0 = unused header).
    The table has NO length field — its true extent = max n any foe-type record uses
    (reading past that hits neighbouring data and fabricates phantom entries)."""
    g17 = _le(raw, slot + 0x28)
    if g17 == 0:
        return []
    base = g17 - RAM
    used = _npc_used_ns(raw, area)
    top = used[-1] if used else 0
    ents = []
    for k in range(top + 1):
        o = base + k * 2
        ents.append(_le(raw, o) if o + 1 < len(raw) else 0)
    return ents


def parse_foe_names(blob: bytes):
    """The foe-name table → `([{segs, lf}, …], terminator)`, or None when it is not that shape.

    An entry is `$00` then `\\n`-separated segments — stem, singular suffix, plural suffix — with
    an optional closing `\\n`. Keeping the SEGMENTS (rather than a joined display name) is what
    makes the table byte-faithful: `Bobb\\ny\\nies\\n` is Bobby/Bobbies, and a reader that joins
    stem+singular loses the plural and re-emits "Bobbys".

    Callers must verify with `emit_foe_names` — the parse is only trusted when it reconstructs the
    original exactly, and the raw bytes stay the fallback for anything irregular.
    """
    if not blob or blob[0] != 0x00:
        return None
    parts = blob.split(b"\x00")
    bodies, term = parts[1:-1], parts[-1]
    out = []
    for b in bodies:
        lf = b.endswith(b"\n")
        segs = (b[:-1] if lf else b).split(b"\n")
        out.append({"segs": [s.decode("latin-1") for s in segs], "lf": lf})
    return out, term


def emit_foe_names(entries, term: bytes = b"\xff") -> bytes:
    """Inverse of `parse_foe_names` — the ONE place that knows the grammar."""
    out = bytearray()
    for e in entries:
        out += b"\x00" + "\n".join(e["segs"]).encode("latin-1") + (b"\n" if e.get("lf") else b"")
    return bytes(out) + b"\x00" + bytes(term)


def foe_name_entries(area) -> list:
    """The area's foe names as segment lists (1-based, entry k belongs to foe k+1)."""
    raw = area.raw
    base = _le(raw, area.slot + 0x02) - RAM
    if not (0 <= base < len(raw)):
        return []
    end = _names_true_end(raw, base)
    p = parse_foe_names(bytes(raw[base:end + 2]))
    return p[0] if p else []


def encode_foe_names(names) -> bytes:
    """The foe-name table as bytes. Each item is one entry, in whichever of three spellings the
    caller has: a PARTS list (`["Madm","a","e","n"]` — carried through verbatim), a (singular,
    plural) pair, or a bare string, which means "regular plural", i.e. the same word plus an s.

    An entry is `$00 STEM \\n SINGULAR-PART \\n PLURAL-PART \\n TAIL` — the engine prints "1 Brat"
    and "3 Brats" from ONE entry, so the parts are the record, not decoration. Highpool spells it
    out: `Brat\\n\\ns\\n` and `Bobb\\ny\\nies\\n` (Bobby / Bobbies); Stagecoach Inn's `Madm\\na\\ne\\nn`
    is Madman / Madmen, the shape that needs a TAIL. Measured over the 43 baselines: 276 entries are
    3 segments with the closing `\\n`, 126 are a bare name with no plural form (e.g. "Night
    Terror"), and 8 carry the tail. `segs_from_forms` derives whichever of those three the two words
    call for.

    The table CLOSES with `$00 $FF` (29 areas) or a bare `$00` (10) — never `$01`.

    Both of those were wrong here, and the combination hangs the machine: this emitted
    `$00 STEM \\n \\n` per entry and closed with `$01`. The suffix segment was missing, so the
    engine's name walker read the plural of one foe out of the NEXT entry, and at the end of the
    table it found no terminator it knows and kept walking. In TREX Base that froze the game on the
    encounter contact box — the announce and "1 RUSTED SENTRY" printed, then the clock stopped.
    Nothing caught it because the editor's own reader (`_foe_names`) stops at `$00`/`$01` and was
    happy with what this produced: two readers, one of them not the engine's.
    """
    out = bytearray()
    for nm in names:
        if isinstance(nm, list):                        # already segments — do not re-derive them
            parts = nm
        elif isinstance(nm, tuple):
            parts = segs_from_forms(*nm)
        else:
            parts = segs_from_forms(str(nm), str(nm) + "s")
        out += b"\x00" + "\n".join(str(p) for p in parts).encode("ascii", "replace")
    return bytes(out) + b"\x00\xff"


def _encounter_ir(d: bytes) -> dict:
    """A 12-byte foe-type record as its typed IR dict (RECORD_FORMAT §4.1)."""
    return {"op": "ENCOUNTER", "dist_a": d[0], "dist_b": d[1], "msg": d[2],
            "slots": [{"foe": d[3 + k * 2] & 0x7F, "foe_roll": bool(d[3 + k * 2] & 0x80),
                       "count": d[4 + k * 2] & 0x7F, "count_roll": bool(d[4 + k * 2] & 0x80)}
                      for k in range(3)],
            "flags": {"active": bool(d[9] & 1), "peaceful": bool(d[9] & 2),
                      "bit2": bool(d[9] & 4), "bit3": bool(d[9] & 8), "npc": d[9] >> 4},
            "victory": {"op": d[10]} if d[10] >= 0x10 else {"op": d[10], "id": d[11]}}


def _ir():
    import wl_ir
    return wl_ir


def _adv_param(p: dict, op_key: str, id_key: str, default_op: int = 0xFF) -> dict:
    """A rewrite-cell advance edge from flat params. op >= $10 = a stop and carries NO id byte
    (_emit_adv_tail), which is why the id is only included when the op is a real group."""
    o = int(p.get(op_key, default_op)) & 0xFF
    return {"op": o} if o >= 0x10 else {"op": o, "id": int(p.get(id_key, 0)) & 0xFF}


def op_record_ir(op: int, p: dict) -> dict:
    """Flat editor params → the serializer's typed IR record.

    THE BYTES ARE NOT BUILT HERE. `wl_ir._emit_op_record` is the one emitter, and it is the one the
    43-area round-trip battery proves; this function only names the fields. The previous version
    hand-rolled each layout a second time and drifted from the first in three places, every one of
    them silent because a wrong-but-same-length record still round-trips:

      LOOT   emitted `[msg][$29][items][$FF]`, but +0/+1 is the ADVANCE pair (RECORD_FORMAT §4,
             and Area 51's own sacks: `01 04 9f 8a ff` = advance grp1[4]). So `message` became an
             advance OP and $29 an advance id — harmless only because op 0 clears the cell, and a
             LOOT that continues into another record was simply not expressible.
      SERVICE wrote the portrait to +$03. The portrait is at **+$13**: TREX Base's twelve services
             carry $4F..$5A there = str 79..90 = exactly side 1's portrait blobs. +$03/+$05 is the
             GREETING, and which of the two depends on the family (SERVICE_HANDLERS.md).
      SAY/OBJ dropped the trailing advance byte and relied on the neighbouring record supplying it.
    """
    if op == 0x1:                     # SAY — msg ids until bit7, then a trailing advance
        # A SAY is a RUN: the engine keeps reading ids until one has bit7 set, so `messages`
        # gives a multi-page speech out of ONE record. It carries NOTHING else — the cell advances
        # by how many pages there were, which is why Highpool can end its Bobby chain on a SAY
        # whose string is empty: one page, one step, cell quiet.
        msgs = [int(x) & 0x7F for x in (p.get("messages") or [])] or [int(p["message"]) & 0x7F]
        return {"op": "SAY", "messages": msgs}   # one page or five — the list is the whole record
    if op == 0x2:                     # COND / TRADE
        return {"op": "COND", "b0": int(p.get("b0", 0x80)) & 0xFF,
                "prompt": int(p.get("prompt", 0)) & 0x7F,
                "success": int(p.get("success", 0)) & 0x7F,
                "fail": int(p.get("fail", 0)) & 0x7F,
                "done": {"op": int(p.get("done_op", 0xFF)) & 0xFF, "id": int(p.get("done_id", 0)) & 0xFF},
                "alt": {"op": int(p.get("alt_op", 0xFF)) & 0xFF, "id": int(p.get("alt_id", 0)) & 0xFF},
                "effect": {"target": int(p.get("eff_target", 0)) & 0xFF,
                           "value": int(p.get("eff_value", 0)) & 0xFF},
                "conds": [{"opb": int(c["op"]) & 0xFF, "arg": int(c["arg"]) & 0xFF}
                          for c in (p.get("conds") or [])[:8]]}
    if op == 0x3:                     # ENCOUNTER — the 12-byte block (RECORD_FORMAT §4.1)
        slots = list(p.get("slots") or [])
        if not slots:
            slots = [{"foe": int(p.get("foe", 0)), "count": int(p.get("count", 1))}]
        if not any(int(s.get("foe", 0)) for s in slots):
            raise ValueError("an encounter needs at least one foe id in slot 1")
        slots = (slots + [{}, {}, {}])[:3]
        return {"op": "ENCOUNTER",
                # the distance gates: the row is skipped unless the party is CLOSER than these, so
                # $3C/$3C (the shipped NPC value) means "the whole map"
                "dist_a": int(p.get("dist_a", 0x3C)) & 0xFF, "dist_b": int(p.get("dist_b", 0x3C)) & 0xFF,
                "msg": int(p.get("message", 0)) & 0x7F,
                "slots": [{"foe": int(s.get("foe", 0)) & 0x7F, "foe_roll": bool(s.get("foe_roll")),
                           "count": int(s.get("count", 0)) & 0x7F,
                           "count_roll": bool(s.get("count_roll"))} for s in slots],
                # PEACEFUL (bit1) is its own thing, not a synonym for hireable: 111 shipped encounters
                # are peaceful with NPC nibble 0 — they stand there until the player presses E.
                "flags": {"active": bool(p.get("active", True)), "peaceful": bool(p.get("peaceful")),
                          "bit2": bool(p.get("bit2")), "bit3": bool(p.get("bit3")),
                          "npc": int(p.get("npc", 0)) & 0x0F},
                "victory": _adv_param(p, "victory_op", "victory_id")}
    if op == 0x4:                     # OBJ / MSG — [msg][solid|tile] then a trailing advance
        pad = int(p.get("pad", p.get("tile", 0))) & 0xFF   # `pad` stays the flat param name: it is
        return {"op": "OBJ",                               # ONE byte, tile in bits 0-6, solid in 7
                "msg": int(p.get("message", 0)) & 0x7F,
                "tile": pad & 0x7F, "solid": bool(pad & 0x80) or bool(p.get("solid")),
                "use": _adv_param(p, "next_op", "next_id")}
    if op == 0x5:                     # LOOT — [adv-op][adv-id][item,qty ..][term]
        items = []
        for it in (p.get("items") or [])[:12]:
            # bit7 on the item byte = flagged/guaranteed; every shipped sack sets it
            iid = int(it["item"]) & 0x7F
            e = {"item": iid | 0x80} if it.get("guaranteed", True) else {"item": iid}
            if (iid & 0x7F) == 0x5E:                      # money takes a 16-bit amount
                e["qty16"] = int(it.get("qty", 0)) & 0xFFFF
            else:
                e["qty"] = int(it.get("qty", 1)) & 0xFF
            items.append(e)
        if not items:
            raise ValueError("a loot sack needs at least one item")
        return {"op": "LOOT", "advance": {"op": int(p.get("advance_op", 0)) & 0xFF,
                                          "id": int(p.get("advance_id", 0)) & 0xFF},
                "items": items}                       # the $FF terminator is implied — one legal value
    if op == 0x6:
        if p.get("selector") is None:                     # plain grp16 EVENT ref, bit7 clear
            return {"op": "EVENT", "event": int(p["event"]) & 0x7F}
        sel = int(p["selector"]) & 0x7F
        # +$03..+$06 differ per family; +$07..+$12 label; +$13 PORTRAIT; +$14.. family payload.
        # The name is emitted as 11 bytes + $00, which is what puts the portrait on +$13.
        fields = bytearray(4)
        tail = bytearray([int(p.get("portrait", 0)) & 0xFF])
        greeting = int(p.get("greeting", 0)) & 0x7F
        if sel in (0, 1, 5):                              # STORE — greeting at +$05, class list after
            fields[2] = greeting
            fields[3] = int(p.get("areatrack", 0)) & 0xFF
            tail += bytes(int(c) & 0xFF for c in (p.get("classes") or p.get("items") or [])[:40])
            tail.append(0xFF)
        elif sel == 9:                                    # TALK — page count at +$04, str ids at +$14
            pages = [int(x) & 0x7F for x in (p.get("pages") or []) if int(x)]
            if not pages:
                raise ValueError("a talk service needs at least one page string id")
            fields[1] = len(pages)
            tail += bytes(pages)
        else:                                             # DOC / BANK / REVIVE — greeting at +$03
            fields[0] = greeting
            for k, v in enumerate((p.get("fields") or [])[:3]):
                fields[1 + k] = int(v) & 0xFF
        # NOT .upper(), and the shipped data is unanimous: of the 23 service names WL1 ships,
        # 23 are mixed case — "Trading car", "Q. Emporium", "Old Doc Bob", "Patch em up". (Area 51
        # is ours and does not count as evidence either way.) The same rule is already written down
        # 130 lines below for FOE names, it was simply never carried over to services. Forcing the
        # case here made every authored shop unreproducible against the ones in the game.
        #
        # The grp17 MEMBER record is the other convention and stays uppercase — JACKIE, FELICIA,
        # ACE, MAYOR PEDROS, 13 of the 14 shipped. Two tables, two conventions.
        label = str(p.get("label", ""))[:11]
        base = {"op": "SERVICE", "selector": sel, "advance": {"op": 0xFD, "id": 0xFD},
                "name": label.ljust(11)}
        legacy = dict(base, fields=_ir()._hex(bytes(fields)), tail=_ir()._hex(bytes(tail)))
        # Describe those bytes with the family model where it fits them EXACTLY, and keep the hex
        # pair where it does not. The three shapes above (store-like / talk / doc-like) predate the
        # nine measured families, so they do not all line up — sel 0 authors a class list a doc
        # record has no room for, sel 2/3/7 a tail of the wrong length. Re-shaping those is a fix,
        # not a rename, so they stay verbatim and stay visible.
        named = _ir()._service_named(base, sel, bytes(fields), bytes(tail))
        if named is not None and _ir()._emit_op_record(named) == _ir()._emit_op_record(legacy):
            return named
        return legacy
    if op == 0x8:                     # KEYWORD — [prompt][cancel pair][kw ids bit7-last][branch xK]
        kws = [int(k) & 0x7F for k in (p.get("keywords") or []) if int(k)]
        if not kws:
            raise ValueError("keyword op needs at least one keyword string id")
        br = list(p.get("branches") or [])
        return {"op": "KEYWORD", "prompt": int(p.get("prompt", 0)) & 0x7F,
                "cancel": {"op": 0xFF, "id": 0xFF},
                "keywords": [{"str": k,
                              "branch": {"op": int(br[i].get("op", 0xFF)) & 0xFF,
                                         "id": int(br[i].get("id", 0)) & 0xFF}
                              if i < len(br) else {"op": 0xFF, "id": 0}}
                             for i, k in enumerate(kws)],
                "after": {"op": 0xFF}}
    if op == 0x9:                     # PARTY_FLAG_SWEEP — [msg][damage dice]
        return {"op": "HAZARD", "msg": int(p.get("message", 0)) & 0x7F,
                "dice": int(p.get("dice", 1)) & 0xFF}
    if op == 0xA:                     # TRANSITION — [$40+msg][X][Y][tgt][term]
        return {"op": "TRANSITION", "b0": 0x40 + (int(p.get("message", 0)) & 0x3F),
                "x": int(p["x"]) & 0xFF, "y": int(p["y"]) & 0xFF,
                "target": int(p["target"]) & 0xFF, "next": {"op": 0xFF}}   # plain door: leave the cell
    if op == 0xB:                     # BUMP — [str] + trailing advance
        return {"op": "BUMP", "msg": int(p.get("message", 0)) & 0xFF,
                "next": _adv_param(p, "next_op", "next_id")}
    if op == 0xC:                     # CELL_PATCH — [msg][ [flags][dx][dy][op][cid] xN ]
        ents = p.get("patches") or []
        if not ents:
            raise ValueError("cell-patch needs at least one entry")
        return {"op": "PATCH", "msg": int(p.get("message", 0)) & 0x7F,
                "entries": [{"flags": int(e.get("flags", 0)) & 0x7F, "dx": int(e["dx"]) & 0xFF,
                             "dy": int(e["dy"]) & 0xFF, "op": int(e["op"]) & 0xFF,
                             "id": int(e["cell_id"]) & 0xFF} for e in ents[:12]],
                "after": _adv_param(p, "after_op", "after_id")}
    raise ValueError(f"add-op not supported for op ${op:X} yet")


def model_set_foe(ir, k: int, name: str = None, rec8: bytes = None, plural: str = None):
    """Keep `ir["foes"]` — the list the serializer emits the two tables FROM — in step with a write.

    k is 0-based (foe id − 1); k == len(foes) appends. Every path that changes a name or a foe
    record has to come through here, because the model wins over the raw blobs on emit: a writer
    that updated only the blobs would have its foe silently dropped."""
    foes = ir.setdefault("foes", [])
    ir.setdefault("foe_names_term", "hex:ff")
    ir.setdefault("foe_table_header", "hex:0000000000000000")
    ir.setdefault("foe_table_extra", "hex:")
    while len(foes) <= k:
        foes.append({"name": "", "record": "hex:0000000000000000", "portrait_str": 0,
                     "name_segs": {"segs": [""], "lf": False}})
    f = foes[k]
    if name is not None:
        f["name"] = name
        # The two words decide the segments (`segs_from_forms`), so an irregular plural survives a
        # rename instead of being flattened to name+"s". That flattening is what turned Madman into
        # "Madma"/"Madmas" the first time anyone pressed Save on him: the panel showed the truncated
        # display name and posted it straight back as the new name.
        segs, lf = parts_to_segs(segs_from_forms(name, str(name) + "s" if plural is None else plural))
        f["name_segs"] = {"segs": segs, "lf": lf}
    if rec8 is not None:
        f["record"] = _ir()._hex(bytes(rec8)[:8].ljust(8, b"\x00"))
        f["portrait_str"] = bytes(rec8)[7] if len(rec8) >= 8 else 0
    return f


def add_foe(raw: bytes, side: int, area_id: int, spec: dict):
    """Append a FOE — a name plus its 8-byte record — and nothing else. Returns (blob, {foe_id}).

    `add_npc` is the full five-part construction: name, foe record, a 256-byte grp17 member and an
    encounter that offers the hire slot. A foe that is only ever fought needs two of those five, and
    forcing the other three on it costs 256 bytes and invents a hireable nobody can hire. The two
    tables are PARALLEL (wl_foe F1), so they grow together here exactly as they do there — and for
    the same reason the tables are handed to the SERIALIZER rather than spliced: it places them in
    the order it guarantees (names → records → text, contiguous) and leaves no hole behind.

    spec: name, portrait_str, kind?/glyph_tile?, template? (8 raw bytes as hex — a PRESET that
    copies a shipped foe's combat bytes), and any named field of `wl_foe.pack_stats` (con, range,
    dice, dmg_base, armour, group_max, attack_type) applied on top. See docs/engine/FOE_RECORD.md.
    """
    import wl_ir
    from wl_area import load_area
    ir = wl_ir.record_to_ir(raw, side, area_id)
    area = load_area(raw, side, area_id)
    name = str(spec["name"])[:13]        # mixed case: "Dire Coyote", not "DIRE COYOTE"
    plural = str(spec["name_plural"])[:13] if spec.get("name_plural") is not None else name + "s"

    parts = _foe_name_parts(area) + [segs_from_forms(name, plural)]
    foe_id = len(parts)                                  # 1-based index into the record table
    old = wl_ir._unhex(ir["foe_records_raw"])
    tpl = spec.get("template")
    rec8 = bytearray(wl_ir._unhex(tpl) if tpl else _NPC_FOE_TEMPLATE)
    if len(rec8) != 8:
        raise ValueError(f"a foe record is 8 bytes, the template is {len(rec8)}")
    rec8 = bytearray(_foe_stats(bytes(rec8), spec))
    if spec.get("glyph_tile") is not None:
        rec8[6] = glyph_class_for_tile(spec["glyph_tile"])
    rec8[7] = int(spec["portrait_str"]) & 0xFF
    ir["foe_names_raw"] = wl_ir._hex(encode_foe_names(parts))
    ir["foe_records_raw"] = wl_ir._hex(old[:foe_id * 8].ljust(foe_id * 8, b"\x00") + bytes(rec8))
    model_set_foe(ir, foe_id - 1, name, bytes(rec8), plural=plural)
    return wl_ir.ir_to_record(ir), {"foe_id": foe_id, "name": name,
                                    "record": wl_ir._hex(bytes(rec8))}


def update_foe(raw: bytes, side: int, area_id: int, foe_id: int, spec: dict) -> bytes:
    """Patch a foe's name / portrait / glyph in the two parallel tables. Returns the new blob.

    Also the way to REWRITE a name table that was emitted in a broken shape: passing a foe's own
    name back re-emits the whole table through `encode_foe_names`."""
    import wl_ir
    from wl_area import load_area
    ir = wl_ir.record_to_ir(raw, side, area_id)
    area = load_area(raw, side, area_id)
    parts = _foe_name_parts(area)
    if not (1 <= foe_id <= len(parts)):
        raise ValueError(f"no foe {foe_id} (the table holds {len(parts)})")
    cur_s, cur_p = name_forms(parts[foe_id - 1])
    # NOT .upper(). 398 of the game's 400 foe names are mixed case — "Dire Coyote", "Wasteland
    # Warrior", "Rib Cracker" — and the only two in caps are the two this line made that way. The
    # name table is not the 5-bit prose pool; it carries the characters it is given, and combat
    # prints them as they are.
    new_s = str(spec["name"])[:13] if "name" in spec else cur_s
    # A caller that sends no plural is not saying "regular plural" — it is saying nothing. Keep the
    # one on file whenever the singular is unchanged, so saving a foe's STATS cannot rewrite his
    # name: that is how "Madman" would have become "Madma"/"Madmas" on the first Save.
    new_p = str(spec["name_plural"])[:13] if spec.get("name_plural") is not None \
        else (cur_p if new_s == cur_s else new_s + "s")
    renamed = (new_s, new_p) != (cur_s, cur_p)
    if renamed:
        parts = _replaced_name(parts, foe_id - 1, new_s, new_p)
    ir["foe_names_raw"] = wl_ir._hex(encode_foe_names(parts))
    if renamed:
        model_set_foe(ir, foe_id - 1, new_s, plural=new_p)
    tab = bytearray(wl_ir._unhex(ir["foe_records_raw"]))
    r = foe_id * 8
    if r + 7 < len(tab):
        if spec.get("template"):
            # A PRESET: a shipped record's combat bytes copied wholesale. Only bytes 0-5 come from
            # it; the kind and the portrait are the caller's own choices and survive.
            t = _ir()._unhex(spec["template"])
            if len(t) != 8:
                raise ValueError(f"a foe template is 8 bytes, this one is {len(t)}")
            tab[r:r + 6] = t[:6]
        # …and then the NAMED fields on top, so "make this one tougher" does not mean "find a
        # shipped foe that happens to be tougher". Applied after the preset by design: the preset
        # seeds, the fields correct. Nibble fields keep their neighbour (armour vs group size).
        tab[r:r + 8] = _foe_stats(bytes(tab[r:r + 8]), spec)
        if "portrait_str" in spec:
            tab[r + 7] = int(spec["portrait_str"]) & 0xFF
        if "glyph_tile" in spec:
            tab[r + 6] = glyph_class_for_tile(spec["glyph_tile"])
        ir["foe_records_raw"] = wl_ir._hex(bytes(tab))
        # …and through the MODEL, which is what the serializer emits from. Writing only the raw
        # blob here silently dropped the change: ir["foes"] still held the old record and won.
        model_set_foe(ir, foe_id - 1, rec8=bytes(tab[r:r + 8]))
    return wl_ir.ir_to_record(ir)


def add_npc(raw: bytes, side: int, area_id: int, spec: dict):
    """Create a full NPC. Returns (new blob, {n, foe_id, marker_cell_id}); the caller places the
    marker cell. spec: name, stats[7], maxcon, money, portrait_str, glyph_tile, hireable, join_str,
    rank, hire_gate.

    BUILT IN THE IR, not by moving bytes. The previous version relocated the name table and the foe
    table to the blob tail and repointed the descriptor — which broke the foe-table/text ADJACENCY
    the serializer maintains, and left both old tables sitting in the middle as holes. A hole with no
    structure behind it is indistinguishable from a record that legitimately runs long, so the
    serializer preserved it: s1_id29 stopped decoding, s3_id42 lost grp5[8], and s1_id51's hire
    reference changed meaning. Handing the serializer the TABLES lets it place them itself, in the
    order it guarantees (names -> records -> text, contiguous), and no hole is ever created.
    """
    import wl_ir
    from wl_area import load_area
    ir = wl_ir.record_to_ir(raw, side, area_id)
    area = load_area(raw, side, area_id)
    # the FOE-table name keeps the author's case (398 of 400 shipped foe names are mixed); the
    # grp17 MEMBER record below is uppercased, because 13 of the 14 shipped ones are
    name = str(spec["name"])[:13]
    plural = str(spec["name_plural"])[:13] if spec.get("name_plural") is not None else name + "s"

    # 1. the two PARALLEL tables grow together — that is the whole invariant (wl_foe.check F1)
    parts = _foe_name_parts(area) + [segs_from_forms(name, plural)]
    foe_id = len(parts)                                  # 1-based index into the record table
    old = wl_ir._unhex(ir["foe_records_raw"])
    rec8 = bytearray(_NPC_FOE_TEMPLATE)
    rec8[6] = glyph_class_for_tile(spec.get("glyph_tile", 4))
    rec8[7] = int(spec["portrait_str"]) & 0xFF
    ir["foe_names_raw"] = wl_ir._hex(encode_foe_names(parts))
    ir["foe_records_raw"] = wl_ir._hex(old[:foe_id * 8].ljust(foe_id * 8, b"\x00") + bytes(rec8))
    model_set_foe(ir, foe_id - 1, name, bytes(rec8), plural=plural)

    # 2. the $100 party-member record, as a grp17 slot above every index already in use
    import wl_foe
    used = set(wl_foe.hire_slots(area)) | {int(k) for k in ir.get("opaque", {}).get("17", {})} \
        | {int(k) for k in ir["groups"].get("17", {})}
    n = (max(used) + 1) if used else 1
    rec = bytearray(_NPC_TEMPLATE)
    rec[0:14] = name.upper().encode("ascii", "replace")[:13].ljust(14, b"\x00")
    st = list(spec.get("stats", [15] * 7))[:7]
    rec[0x0E:0x15] = bytes(max(1, min(255, int(v))) for v in st)
    rec[0x15:0x18] = int(spec.get("money", 200)).to_bytes(3, "little")
    rec[0x1B:0x1D] = int(spec.get("maxcon", 30)).to_bytes(2, "little")
    rec[0x30] = int(spec.get("join_str", 0)) & 0x7F
    rec[0x31] = int(spec.get("hire_gate", 0)) & 0xFF
    rank = str(spec.get("rank", ""))[:24].encode("ascii", "replace") + b"\x00"
    rec[0x32:0x32 + len(rank)] = rank
    ir.setdefault("opaque", {}).setdefault("17", {})[str(n)] = wl_ir._hex(rec)
    ir.get("dead_ptrs", {}).pop("17", None)              # grp17 is live now, not parked

    # 3. the encounter row that REFERENCES the foe and offers the hire slot
    ft = bytearray(_NPC_FOETYPE_TEMPLATE)
    ft[3] = foe_id
    ft[9] = (n << 4) | 0x01 | (0x02 if spec.get("hireable") else 0x00)
    cids = {int(k) for k in ir["groups"].get("3", {})} | {int(k) for k in ir.get("opaque", {}).get("3", {})}
    cid = max(max(cids) + 1 if cids else 0, group_slot_count(raw, area.slot, 3))
    ir["groups"].setdefault("3", {})[str(cid)] = _encounter_ir(bytes(ft))
    ir.get("dead_ptrs", {}).pop("3", None)               # grp3 has a record now, it is not parked
    return wl_ir.ir_to_record(ir), {"n": n, "foe_id": foe_id, "marker_cell_id": cid}

def update_npc(raw: bytes, side: int, area_id: int, n: int, spec: dict) -> bytes:
    """Patch an existing NPC (grp17 index n). Returns the new blob.

    IR-based for the same reason `add_npc` is: a rename touches the foe-NAME table, and rebuilding
    that table by moving bytes leaves the old one behind as a hole the serializer then preserves.
    Here the tables are handed to the serializer, which places them itself.
    """
    import wl_ir
    import wl_foe
    from wl_area import load_area
    ir = wl_ir.record_to_ir(raw, side, area_id)
    area = load_area(raw, side, area_id)
    slot = area.slot

    g17 = ir.get("opaque", {}).get("17", {})
    if str(n) in g17:
        rec = bytearray(wl_ir._unhex(g17[str(n)]))
    elif str(n) in ir["groups"].get("17", {}):
        rec = bytearray(wl_ir._emit_op_record(ir["groups"]["17"][str(n)]))
    else:
        raise ValueError(f"no NPC at grp17 index {n}")

    # The encounter that offers this hire slot names the foe — found through the REFERENCE GRAPH,
    # because it need not have a map cell. Missing it left a rename patching the member record only,
    # so the NPC kept its old name everywhere the player sees it.
    foe_id, enc = None, None
    for grp, cid in wl_foe.hire_slots(area).get(n, []):
        rr = ir["groups"].get(str(grp), {}).get(str(cid))
        if rr and rr.get("op") == "ENCOUNTER":
            foe_id, enc = rr["slots"][0]["foe"], rr
            break
        o = subptr(raw, slot, grp, cid)
        if o is not None and o + 3 < len(raw):
            foe_id = raw[o + 3] & 0x7F
            break
    if enc is not None and "hireable" in spec:
        enc["flags"]["peaceful"] = bool(spec["hireable"])

    if "name" in spec:
        # TWO tables, two conventions. The grp17 member record is uppercase — JACKIE, FELICIA, ACE,
        # MAYOR PEDROS, 13 of the 14 shipped ones. The FOE-name table is mixed, 398 of 400. Writing
        # one string into both is why an NPC could only ever be called by a shout.
        name = str(spec["name"])[:13]
        rec[0:14] = name.upper().encode("ascii", "replace")[:13].ljust(14, b"\x00")
        parts = _foe_name_parts(area)
        if foe_id and 0 < foe_id <= len(parts):
            cur_s, cur_p = name_forms(parts[foe_id - 1])
            plural = str(spec["name_plural"])[:13] if spec.get("name_plural") is not None \
                else (cur_p if name == cur_s else name + "s")
            if (name, plural) != (cur_s, cur_p):
                parts = _replaced_name(parts, foe_id - 1, name, plural)
                model_set_foe(ir, foe_id - 1, name, plural=plural)
            ir["foe_names_raw"] = wl_ir._hex(encode_foe_names(parts))
    if "stats" in spec:
        rec[0x0E:0x15] = bytes(max(1, min(255, int(v))) for v in list(spec["stats"])[:7])
    if "maxcon" in spec:
        rec[0x1B:0x1D] = int(spec["maxcon"]).to_bytes(2, "little")
    if "money" in spec:
        rec[0x15:0x18] = int(spec["money"]).to_bytes(3, "little")
    if "join_str" in spec:
        rec[0x30] = int(spec["join_str"]) & 0x7F
    if "hire_gate" in spec:
        rec[0x31] = int(spec["hire_gate"]) & 0xFF
    if "rank" in spec:
        rank = str(spec["rank"])[:24].encode("ascii", "replace") + b"\x00"
        rec[0x32:0x32 + len(rank)] = rank
    ir.setdefault("opaque", {}).setdefault("17", {})[str(n)] = wl_ir._hex(rec)
    ir["groups"].get("17", {}).pop(str(n), None)          # one slot, one representation

    if foe_id and ("portrait_str" in spec or "glyph_tile" in spec):
        tab = bytearray(wl_ir._unhex(ir["foe_records_raw"]))
        fr = foe_id * 8
        if fr + 7 < len(tab):
            if "portrait_str" in spec:
                tab[fr + 7] = int(spec["portrait_str"]) & 0xFF
            if "glyph_tile" in spec:
                tab[fr + 6] = glyph_class_for_tile(spec["glyph_tile"])
            ir["foe_records_raw"] = wl_ir._hex(tab)
            model_set_foe(ir, foe_id - 1, rec8=bytes(tab[fr:fr + 8]))
    return wl_ir.ir_to_record(ir)


# ── record COMPACTION (garbage-collect orphaned regions; fail-closed) ─────────
# The prose relocation-rebuild leaves the OLD text region as dead space when the
# region isn't at the blob tail (true after NPC-table appends). Repeated edits
# accumulate these → the record can exceed the crt-build cap (records load at
# $3400). This GC removes UNREFERENCED byte ranges and fixes every pointer, then
# a full round-trip digest MUST match — otherwise the compaction is rejected and
# the original is kept (so a wrong guess can never corrupt a save).

def _record_pointers(raw, area):
    """Every (location, target_ram) pointer in the record: descriptor fields + grp
    pointer-array entries (indexed by the cell-ids each op actually uses) + grp17."""
    slot = area.slot
    ptrs = []
    def add(loc):
        v = _le(raw, loc)
        if v:
            ptrs.append((loc, v))
    add(slot + 0)                    # text region base
    add(slot + 2)                    # foe names
    add(slot + 4)                    # foe table
    for g in range(19):
        add(slot + 6 + g * 2)        # grp pointer array (grp17 = slot+$28 falls here)
    # grp array entries (one per cell-id used by that op)
    for g in range(19):
        ga = gptr(raw, slot, g)
        if ga is None:
            continue
        ids = {area.cellid[i] for i, op in enumerate(area.nibble) if op == g}
        for cid in ids:
            loc = ga + cid * 2
            if 0 <= loc + 1 < len(raw):
                add(loc)
    # ── grp11 ROTATION CHAINS. A chain's links (ids 2,3,4…) are referenced by the chain's own
    # next-id, NOT by any cell — the cell-id loop above misses their array entries, so compaction
    # would relocate the link records without fixing their pointers and silently corrupt the chain
    # (this broke the TREX Radheads chain on every text edit → /prose → compact). Add every grp11
    # array entry reachable from a cell OR a chain (group_referenced_ids follows the chains).
    g11 = gptr(raw, slot, 0xB)
    if g11 is not None:
        for cid in group_referenced_ids(area, 0xB):
            loc = g11 + cid * 2
            if 0 <= loc + 1 < len(raw):
                add(loc)
    # ── SERVICE reachability. A shop/doc/bank op-6 record has NO op-6 CELL: it is reached only
    # through an op-A door's 2-op chain `[opf][X][Y][tgt] [06][op6-cid]`. The cell-id loop above
    # therefore misses grp6[cid] entirely, and compaction would relocate the record without
    # fixing the pointer (this silently broke the Area 51 bank once). Walk the chains too.
    g6 = gptr(raw, slot, 0x6)
    svc_cids = set()
    if g6 is not None:
        for i, op in enumerate(area.nibble):
            if op != 0xA:
                continue
            d = subptr(raw, slot, 0xA, area.cellid[i])
            if d is None or d + 5 >= len(raw) or raw[d + 4] != 0x06:
                continue
            cid = raw[d + 5]
            svc_cids.add(cid)
            loc = g6 + cid * 2
            if 0 <= loc + 1 < len(raw):
                add(loc)
    # ── grp16 CODE pointers. An op-6 record whose byte0 has bit7 CLEAR is an AREA_EVENT: the
    # engine dispatches to grp16[byte0], a direct code entry point inside this blob. Those
    # entries are pointers like any other and MUST move with the code (GRP16_SPEC.md §1).
    g16 = _le(raw, slot + 0x26)
    if g16 and g6 is not None:
        base16 = g16 - RAM
        for cid in svc_cids:
            rec = subptr(raw, slot, 0x6, cid)
            if rec is None or not (0 <= rec < len(raw)):
                continue
            ev = raw[rec]
            if ev & 0x80:
                continue                       # bit7 SET = $CA00 scene blob, not grp16
            loc = base16 + ev * 2
            if 0 <= loc + 1 < len(raw):
                add(loc)
    # grp17 entries (indexed by the n's the foe-type recs use)
    g17 = _le(raw, slot + 0x28)
    if g17:
        base = g17 - RAM
        for i, op in enumerate(area.nibble):
            if op in (0x3, 0xF):
                rec = subptr(raw, slot, op, area.cellid[i])
                if rec is not None and 0 <= rec + 9 < len(raw):
                    n = raw[rec + 9] >> 4
                    if n:
                        loc = base + n * 2
                        if 0 <= loc + 1 < len(raw):
                            add(loc)
    # COMPLETENESS: every VALID entry of every group's pointer array — not just the cell/chain/service
    # ids the reachability walks above cover. Records reached only by the engine (grp15 encounter defs
    # iterated by count, grp16 events fired from op-6 CELLS, special grp10 slots jeep/Sleeper/Cochise)
    # or by an op-2/op-C rewrite are otherwise missed, and a relocation would leave their array entry
    # DANGLING (42/43 records had such gaps — RECORD_FORMAT.md §6). group_array_extent walks each array
    # to its true end (tolerating $0000 gaps, stopping at the first out-of-range pointer).
    _pb = _section_bounds(raw, slot)   # once, not per group
    for g in range(19):
        if g == 16:                                      # slot+$26 = grp16 CODE base, NOT a pointer array —
            continue                                     # walking it reads 6502 operands as phantom pointers
        ga = gptr(raw, slot, g)
        if ga is None:
            continue
        for cid in range(group_slot_count(raw, slot, g, _pb)):
            loc = ga + cid * 2                           # every pointer LOCATION compaction must fix;
            if 0 <= loc + 1 < len(raw):                  # missing one makes the fail-closed check
                add(loc)                                 # reject a compaction that was fine
    seen_loc = set()                                     # dedup (walks + full scan overlap); keep first
    ptrs = [(loc, v) for (loc, v) in ptrs if not (loc in seen_loc or seen_loc.add(loc))]
    return ptrs


def area_digest(raw, side, area_id, include_strings=True):
    """Canonical fingerprint used to prove a transform changed NOTHING VISIBLE: grids, every
    stepped op's DECODED meaning (resolved text/targets), foe names, NPCs. include_strings adds
    the raw text pool (on for region-GC where it must be identical; off for string-GC where the
    pool intentionally shrinks but each op must still resolve to the same text)."""
    import hashlib
    import wl_area
    a = wl_area.load_area(raw, side, area_id)
    h = hashlib.md5()
    h.update(bytes(a.nibble)); h.update(bytes(x & 0xFF for x in a.cellid))
    # hash the DECODED meaning of each stepped op (not raw bytes — records abut pointer
    # arrays that legitimately shift during compaction; the decode is what must survive)
    for i, op in enumerate(a.nibble):
        rec = subptr(raw, a.slot, op, a.cellid[i])
        if op in (0x1, 0x2, 0x4, 0x5, 0x6, 0x8, 0x9, 0xA, 0xB, 0xC):   # +$B: op-B bump/chain
            try:
                for sec in _decode_record(a, op, rec):
                    h.update(repr(sec.get("rows") or sec.get("items") or sec.get("text") or "").encode("utf8", "replace"))
            except Exception:
                h.update(b"?")
            if op == 0x4 and rec is not None:            # OBJ: the drawn-tile byte (+1) is NOT in the decode
                h.update(bytes(raw[rec:rec + 2]))        # → digest was BLIND to a tile change (sensitivity audit)
        elif op == 0x3:                                  # ENCOUNTER: foe + count
            try:
                h.update(repr(_state_text(a, 0x3, a.cellid[i]).get("foe")).encode("utf8", "replace"))
            except Exception:
                h.update(b"?")
            if rec is not None:                          # + the 10 raw encounter-data bytes (foe/count/stats,
                h.update(bytes(raw[rec:rec + 10]))       # pre-advance) — the foe-name decode alone missed byte0
    # op-B ROTATION CHAINS as whole sequences (link records that no cell steps) + every op-B record
    for cid in group_referenced_ids(a, 0xB):
        try:
            h.update(repr(chain_links(a, cid)).encode("utf8", "replace"))
        except Exception:
            h.update(b"?")
    # CHAIN-REACHABLE states — the per-cell op loop only sees CELL-referenced records; a fight's loot
    # sack, that loot's follow-up SAY, a mid-chain PATCH, or a shop reached only through an op-A door
    # have NO cell of their own, so their CONTENT (loot items, greeting…) was BLIND (Highpool: 18/19
    # loot records). area_chains follows every op's advance transitively — fold in each reachable
    # state's DECODED meaning so a relocating serialize that alters any of them is caught.
    try:
        for _k, states in area_chains(a)["cells"].items():
            for st in states:
                h.update(f"{st['op_nibble']}:{st['cell_id']}:".encode())
                h.update(repr(st.get("text") or st.get("items") or st.get("rows") or "").encode("utf8", "replace"))
    except Exception:
        h.update(b"?")
    # foe RECORDS (stats, not just names), grp15 encounter-defs, grp16 CODE — reachable content the
    # decoded-op loop above never touches. Hash their raw bytes (relocation-invariant; grp16 stays in
    # place under compaction, so its absolute self-refs don't move).
    def _hash_range(a0, a1):
        if 0 <= a0 < a1 <= len(raw):
            h.update(raw[a0:a1])
    ft = _le(raw, a.slot + 0x04) - RAM                # foe record table (8-byte records)
    fn = _le(raw, a.slot + 0x02) - RAM
    if 0 <= ft:
        ft_end = _foe_table_end(raw, a.slot)             # relocation-INVARIANT end (text always follows the
        j = ft                                           # foe-table); the terminator rule below can over-run
        while j + 8 <= ft_end:                           # into old-text bytes that move under compaction,
            _hash_range(j, j + 8)                        # coupling the digest to moving data → compact could
            if j > ft and (raw[j + 1] != 0 or raw[j + 7] == 0):   # then never reclaim (blob grew per edit)
                break
            j += 8
            if j - ft > 8 * 64:
                break
    g15 = gptr(raw, a.slot, 15)                          # grp15 encounter-defs (12-byte, count=desc+$32)
    if g15 is not None:
        cnt = raw[a.slot + 0x32] if a.slot + 0x32 < len(raw) else 0
        r0 = subptr(raw, a.slot, 15, 0)
        if r0 is not None:
            _hash_range(r0, r0 + cnt * 12)
    g16 = _le(raw, a.slot + 0x26)                        # grp16 CODE bytes (base .. foe-names base)
    if g16:
        _hash_range(g16 - RAM, fn if 0 <= fn else len(raw))
    if include_strings:
        try:
            import wl_text5_encode as tenc
            base = (raw[a.slot] | (raw[a.slot + 1] << 8)) - RAM
            for st in tenc.enumerate_strings(raw, base, raw[base:base + 62]):
                h.update(st.encode("utf8", "replace")); h.update(b"\x1f")
        except Exception:
            pass
    h.update("|".join(_foe_names(a)).encode("utf8", "replace"))
    try:
        for x in area_hireables(a, side):
            h.update(f"{x['name']}:{x['portrait_str']}:{x['cells']}".encode())
    except Exception:
        pass
    return h.hexdigest()


def _orphaned_text_regions(raw, area):
    """Old text regions left behind by prose relocation. A text region begins with the record's
    62-byte char table; the LIVE one is at slot+0, always at the blob TAIL (rebuild_record_text
    appends). Every OTHER occurrence of that char-table signature is an orphaned region.

    An orphan's END is NOT found by walking its packed bitstream — region_end() has no hard
    terminator and both over- AND under-reads (leaving a few polluting bytes that shift into the
    foe-table hash and block every future compaction). It is found STRUCTURALLY: text is the last
    section, so relocation appends the new region exactly where the old one ended. Each orphan
    therefore runs to the NEXT text-region start (another orphan, or the live region) — a boundary
    that needs no decoding and is exact to the byte."""
    slot = area.slot
    live = (raw[slot] | (raw[slot + 1] << 8)) - RAM
    if not (0 <= live < len(raw)):
        return []
    # char-table HEAD only (not the full 62 B): build_region re-packs the table's unused/filler tail,
    # so an orphan's table and the freshly-built live table share only their head (the frequently-used
    # chars). 16 B is a strong-enough match — the packed text5 body is 5-bit indices, never ASCII, so
    # this ASCII run can't false-positive inside a bitstream.
    tbl = min(16, len(raw) - live)
    sig = bytes(raw[live:live + tbl])
    if tbl < 16 or sig.count(0) > 12:                # too-empty signature = unsafe, bail
        return []
    starts, i = [], 0                                # every char-table occurrence = a text-region start
    while True:
        j = raw.find(sig, i)
        if j < 0:
            break
        starts.append(j)
        i = j + 1
    marks = sorted(set(starts) | {len(raw)})         # + EOF sentinel
    out = []
    for j in starts:
        if j == live:
            continue
        end = min(s for s in marks if s > j)         # runs to the next text region / EOF — exact, no walk
        if slot < j < end <= len(raw) and end - j > 32:
            out.append((j, end))
    return out


def compact_record(raw, side, area_id):
    """GC orphaned regions. Returns (new_bytes, removed) — removed=0 and raw unchanged if
    nothing safe was found. Every candidate removal must leave area_digest() identical."""
    import wl_area
    raw = bytes(raw)
    want = area_digest(raw, side, area_id)
    removed_total = 0
    changed = True
    while changed:
        changed = False
        area = wl_area.load_area(raw, side, area_id)
        for gs, ge in _orphaned_text_regions(raw, area):
            size = ge - gs
            cand = bytearray(raw[:gs] + raw[ge:])
            # fixup every pointer whose target sits at/after the removed range
            area0 = wl_area.load_area(raw, side, area_id)
            for loc, val in _record_pointers(raw, area0):
                tgt = val - RAM
                nloc = loc if loc < gs else loc - size
                nval = val if tgt < ge else val - size
                if 0 <= nloc + 1 < len(cand):
                    cand[nloc] = nval & 0xFF
                    cand[nloc + 1] = (nval >> 8) & 0xFF
            try:
                if area_digest(bytes(cand), side, area_id) == want:
                    raw = bytes(cand)
                    removed_total += size
                    changed = True
                    break            # re-scan from the compacted blob
            except Exception:
                pass                 # candidate didn't even load → skip it
    return raw, removed_total


# ── remove UNUSED strings (template cruft) — renumber refs, fail-closed ───────
# Every string-reference SITE in the record, with how the id is packed:
#   'flag'  : byte = id | (byte & $80)   (SAY/COND/sweep/LOOT/KEYWORD/PATCH/service/bump/join)
#   'label' : byte = $40 + id            (op-A transition label)
def _string_ref_sites(raw, area):
    slot, W = area.slot, area.width
    sites = []                                   # (offset, kind)
    seen = set()
    def flag(o):
        if 0 <= o < len(raw):
            sites.append((o, "flag"))
    for i, op in enumerate(area.nibble):
        cid = area.cellid[i]
        if op in (0x1, 0x2, 0x4, 0x5, 0x6, 0x8, 0x9, 0xA, 0xC):
            if (op, cid) in seen:
                continue
            seen.add((op, cid))
            rec = subptr(raw, slot, op, cid)
            if rec is None or not (0 <= rec < len(raw)):
                continue
            if op == 0x1:
                j = rec
                while j < len(raw):
                    flag(j)
                    if raw[j] & 0x80:
                        break
                    j += 1
            elif op == 0x2:
                for off in (1, 2, 3):
                    flag(rec + off)
            elif op in (0x4, 0x9, 0x5, 0xC):
                flag(rec)
            elif op == 0x6:
                if raw[rec] & 0x80 and (raw[rec] & 0x7F) not in (0,) and rec + 5 < len(raw):
                    flag(rec + 5)               # shop greeting (services also use +5)
            elif op == 0x8:
                flag(rec)
                j = rec + 3
                while j < len(raw):
                    flag(j)
                    if raw[j] & 0x80:
                        break
                    j += 1
            elif op == 0xA:
                b0 = raw[rec]
                if 0x40 <= b0 <= 0x7F:
                    sites.append((rec, "label"))
    # op-B grp11 bump msgs: record = [str-id][$FF]
    for i, op in enumerate(area.nibble):
        if op == 0xB and (0xB, area.cellid[i]) not in seen:
            seen.add((0xB, area.cellid[i]))
            rec = subptr(raw, slot, 0xB, area.cellid[i])
            if rec is not None and 0 <= rec < len(raw):
                flag(rec)
    # grp17 join lines (+$30)
    g17 = _le(raw, slot + 0x28)
    if g17:
        base = g17 - RAM
        for i, op in enumerate(area.nibble):
            if op in (0x3, 0xF):
                r = subptr(raw, slot, op, area.cellid[i])
                if r is not None and 0 <= r + 9 < len(raw):
                    n = raw[r + 9] >> 4
                    if n:
                        q = _le(raw, base + n * 2) - RAM
                        if 0 <= q + 0x30 < len(raw):
                            flag(q + 0x30)
    return sites


def _site_id(raw, off, kind):
    return (raw[off] - 0x40) if kind == "label" else (raw[off] & 0x7F)


def remove_unused_strings(raw, side, area_id):
    """Drop strings no op references, renumber, fix every ref. Fail-closed: only applies
    if area_digest() stays identical. Returns (new_bytes, removed_count)."""
    import wl_area
    import wl_text5_encode as tenc
    raw = bytes(raw)
    want = area_digest(raw, side, area_id, include_strings=False)
    area = wl_area.load_area(raw, side, area_id)
    base = (raw[area.slot] | (raw[area.slot + 1] << 8)) - RAM
    strings = tenc.enumerate_strings(raw, base, raw[base:base + 62])
    sites = _string_ref_sites(raw, area)
    used = {_site_id(raw, o, k) for o, k in sites}
    used.discard(0)
    keep = [0] + sorted(i for i in used if 1 <= i < len(strings))   # id 0 reserved
    if len(keep) >= len(strings):
        return raw, 0                                   # nothing unused
    newid = {old: new for new, old in enumerate(keep)}
    # 1. rewrite reference bytes to the new ids
    out = bytearray(raw)
    for o, k in sites:
        old = _site_id(raw, o, k)
        nid = newid.get(old, 0)
        if k == "label":
            out[o] = 0x40 + (nid & 0x3F)
        else:
            out[o] = (raw[o] & 0x80) | (nid & 0x7F)
    # 2. rebuild the text region with only the kept strings (renumbered)
    new_strings = [strings[i] for i in keep]
    out = bytearray(tenc.rebuild_record_text(bytes(out), area.slot, new_strings))
    # 3. verify — reject if the decoded area changed at all
    if area_digest(bytes(out), side, area_id, include_strings=False) != want:
        return raw, 0
    removed = len(strings) - len(keep)
    # drop the now-orphaned old text region too
    out2, _ = compact_record(bytes(out), side, area_id)
    return out2, removed


def update_service(raw: bytearray, area, op6_cid: int, spec: dict) -> dict:
    """Patch an op-6 SERVICE record. Fields are whatever the family actually has (see
    _service_section for the layouts); everything not in `spec` keeps its current value.

    The record is REBUILT and APPENDED, then grp6[cid] is repointed — the class list and the
    bank's ASCII confirmation change length, so patching in place is not possible. Nothing else
    moves, and `compact_record` reclaims the old copy (it knows about op-A-chained service
    records since the pointer-walk fix).

    spec keys: portrait, label, greeting | store: buy, sell, areatrack, classes[] |
               doc: prices[3] | bank: prompt, amount, confirm(str) |
               revive: texts[3] | talk: pages[] (both are ASCII addressed by 8-bit offsets)
    """
    slot = area.slot
    rec = subptr(bytes(raw), slot, 0x6, op6_cid)
    if rec is None or rec + 0x13 > len(raw):
        raise ValueError(f"no service record at grp6[{op6_cid}]")
    old = bytes(raw[rec:rec + 0x60])
    sel = old[0] & 0x7F
    if not (old[0] & 0x80):
        raise ValueError("that op-6 record is an AREA_EVENT (grp16), not a scene service")
    patched = sel in (5, 6)
    fam = _service_family(sel)
    if fam is None:
        raise ValueError(f"unknown service selector ${sel:02X}")

    def g(key, cur):
        v = spec.get(key)
        return cur if v is None else v

    label = spec.get("label")
    if label is None:
        lab = old[7:0x13]
    else:
        lab = str(label)[:11].encode("ascii", "replace").ljust(11, b" ") + b"\x00"
    if len(lab) != 12:
        raise ValueError("label must encode to 12 bytes")

    head = bytearray(old[:7])
    if fam == "store":
        head[3] = g("buy", old[3]) & 0xFF
        head[4] = g("sell", old[4]) & 0xFF
        head[5] = g("greeting", old[5]) & 0xFF
        head[6] = g("areatrack", old[6]) & 0xFF
    elif fam == "doc":
        head[3] = g("greeting", old[3]) & 0xFF
        pr = spec.get("prices")
        if pr is not None:
            if len(pr) != 3:
                raise ValueError("prices must be three values")
            head[4], head[5], head[6] = (int(x) & 0xFF for x in pr)
    elif fam == "bank":
        head[3] = g("greeting", old[3]) & 0xFF
        head[4] = g("prompt", old[4]) & 0xFF
        amt = spec.get("amount")
        if amt is not None:
            head[5], head[6] = int(amt) & 0xFF, (int(amt) >> 8) & 0xFF
    elif fam == "revive":
        head[3] = g("greeting", old[3]) & 0xFF   # +$04/$05/$06 are offsets, filled in below
    else:                                        # talk — +$04 is the PAGE COUNT, also below
        pass

    body = bytes(head) + lab
    if fam in ("bank", "revive", "talk") or patched:
        body += bytes([g("portrait", old[0x13]) & 0xFF])
    if fam == "store":
        cls = spec.get("classes")
        if cls is None:
            i = rec + (0x14 if patched else 0x13)
            cls = []
            while i < len(raw) and raw[i] != 0xFF and len(cls) < 16:
                cls.append(raw[i]); i += 1
        if not patched:                          # stock blob: classes sit at +$13, no portrait byte
            body = bytes(head) + lab
        body += bytes(int(c) & 0xFF for c in cls) + b"\xFF"
    elif fam == "bank":
        msg = spec.get("confirm")
        if msg is None:
            msg = bytes(old[0x15:]).split(b"\x00")[0].decode("ascii", "replace")
        body += b"\x00" + str(msg).encode("ascii", "replace") + b"\x00"
    elif fam == "revive":
        # three fixed texts, each addressed by its own offset byte at +$04/+$05/+$06
        texts = spec.get("texts")
        if texts is None:
            texts = [_ascii_at(raw, rec, old[4]), _ascii_at(raw, rec, old[5]), _ascii_at(raw, rec, old[6])]
        if len(texts) != 3:
            raise ValueError("revive needs exactly three texts (prompt, revived, refused)")
        offs, blob = _pack_ascii(0x14, texts)
        head[4], head[5], head[6] = offs                     # the three offset bytes
        body = bytes(head) + body[7:] + blob                 # head changed after body was built
    elif fam == "talk":
        # pages = a list of text5 STRING-IDS (the strings live in the STR pool, edited in the Text
        # tab). spec "page_ids" is authoritative; fall back to the record's current ids.
        ids = spec.get("page_ids")
        if ids is None:
            ids = [old[0x14 + i] for i in range(old[4])]
        ids = [int(i) & 0xFF for i in ids]
        if not ids:
            raise ValueError("a talk needs at least one page")
        if len(ids) > 32:
            raise ValueError("at most 32 pages")
        head[4] = len(ids)                                   # +$04 = page count
        body = bytes(head) + body[7:] + bytes(ids)           # head changed after body was built

    off = len(raw)
    raw += body
    ram = off + RAM
    g6 = gptr(bytes(raw), slot, 0x6)
    raw[g6 + op6_cid * 2] = ram & 0xFF
    raw[g6 + op6_cid * 2 + 1] = (ram >> 8) & 0xFF
    return {"cid": op6_cid, "selector": sel, "family": fam, "bytes": len(body), "at": ram}

def _state_text(area, op: int, cid: int) -> dict:
    """What a state actually SAYS or DOES, so the Flow view reads without jumping to other tabs."""
    raw, slot = area.raw, area.slot
    info: dict = {}
    if op == 0x1:                                   # SAY: the message list
        rec = subptr(raw, slot, 0x1, cid)
        if rec is not None:
            msgs, j = [], rec
            while j < len(raw) and len(msgs) < 4:
                m = raw[j] & 0x7F
                if m:
                    msgs.append(_prose(area, m) or f"str #{m}")
                if raw[j] & 0x80:
                    break
                j += 1
            info["text"] = " / ".join(t for t in msgs if t)[:120]
    elif op == 0x3:                                 # ENCOUNTER: how many of what
        rec = subptr(raw, slot, 0x3, cid)
        if rec is not None and rec + 4 < len(raw):
            fid = raw[rec + 3] & 0x7F                # 1-based foe slot
            names = _foe_names(area)
            fname = names[fid - 1] if 0 < fid <= len(names) else f"foe {fid}"
            foetab = _le(raw, slot + 0x04) - RAM
            fr = foetab + fid * 8
            fstr = raw[fr + 7] if 0 <= fr and fr + 7 < len(raw) else None
            # count byte: bit7 is a FLAG (roaming/ambush variant), NOT part of the magnitude — mask
            # it, else outdoor encounters render absurd sizes (e.g. $83 = "131× Brat" instead of 3).
            # Verified: 146 op-3 cells have bit7 set; masked values are all sane group sizes (1-6).
            cnt = raw[rec + 4] & 0x7F
            info["text"] = f"{cnt}× {fname}"
            info["foe"] = {"name": fname, "count": cnt, "str_id": fstr}
    elif op == 0x2:
        rec = subptr(raw, slot, 0x2, cid)
        if rec is not None and rec + 0x0A < len(raw):
            info["text"] = _cond_summary(area, rec + 0x0A)[:120]
    elif op == 0xC:                                 # PATCH: how many cells, and which
        rec = subptr(raw, slot, 0xC, cid)
        if rec is not None and 0 <= rec < len(raw):
            cells, j = [], rec + 1
            while j + 4 < len(raw):
                if not (raw[j] & 1):
                    cells.append([_s8(raw[j+1]), _s8(raw[j+2])])
                if raw[j] & 0x80:
                    break
                j += 5
            info["patches"] = cells
            info["text"] = f"patches {len(cells)} cell{'' if len(cells) == 1 else 's'}"
            # for each patched cell, the tile it will DISPLAY after (for thumbnails)
            if area.width and cells:
                tgt = []
                for cx, cy in cells[:60]:
                    i = cy * area.width + cx
                    if 0 <= i < len(area.nibble):
                        tgt.append({"cell": [cx, cy]})
                info["patch_cells"] = tgt
            msg = _prose(area, raw[rec] & 0x7F) if raw[rec] else ""
            if msg:
                info["text"] = f"{msg[:80]} — {info['text']}"
    elif op == 0x5:
        loot = _loot_items(area, cid)
        info["text"] = "loot sack: " + (" · ".join(loot) if loot else "(empty)")
        if loot:
            info["loot"] = loot
    elif op == 0x6:                                 # SERVICE ($CA00) or EVENT (grp16 code)
        rec = subptr(raw, slot, 0x6, cid)
        if rec is not None and rec < len(raw) and not (raw[rec] & 0x80):
            # An EVENT is a jump into the area's OWN 6502. Until now the chain said "AREA_EVENT"
            # and stopped, which is where the world parameters went missing: 19 of these handlers
            # do nothing but retune the random-encounter setup, and they are the difference between
            # a corridor and Cochise. Say what the event DOES, in the Data panel's own words.
            import wl_world
            ev = raw[rec]
            hit = next((e for e in wl_world.raw_events(raw, slot) if e["event"] == ev), None)
            info["text"] = f"event {ev}: {hit['text']}" if hit else f"runs the area's own code (event {ev})"
            if hit:
                info["world_event"] = {"event": ev, **hit["effect"], "text": hit["text"]}
    return info


def area_flow(area, tileids=None) -> dict:
    """Group the chains into CONNECTED COMPONENTS — the "events" a designer thinks in.

    area_chains gives a state list per cell, but a Highpool fight round spans several cells: a SAY
    cell arms a KAMPF cell, whose loot sack arms the next round somewhere else. Cells that reference
    each other therefore belong to ONE event, and that is what the Flow view lists.

    Each component: {id, cells:[...], rows:[{cell, states:[...]}], out:[cell,...], summary}.
    `rows` keeps each cell's own sequence (advance edges); a state whose `from` is a different cell
    is a cross-cell edge and is what glued the component together."""
    chains = area_chains(area, tileids)["cells"]

    # Group by ROOT, not by shared cells. Union-find over shared cells merged everything: the final
    # Red Ryder patch rewrites 110 cells including the doc/trader doors, so the creek, the water
    # filter and the boy-with-the-dog all ended up in one blob. A story is what one trigger starts.
    groups: dict[str, dict] = {}
    for k, states in chains.items():
        for st in states:
            r = st.get("root")
            if not r:
                continue
            g = groups.setdefault(r, {})
            g.setdefault(k, []).append(st)

    out = []
    for root, cells in groups.items():
        rows = []
        for k in sorted(cells, key=lambda c: (int(c.split(",")[1]), int(c.split(",")[0]))):
            base = chains[k][0]
            seq = [base] + [st for st in cells[k] if st["step"] != 0]
            seq = [dict(st, **_state_text(area, st["op_nibble"], st["cell_id"])) for st in seq]
            rows.append({"cell": [int(x) for x in k.split(",")], "states": seq})
        ops = [st["op_nibble"] for r in rows for st in r["states"]]
        summary = {"fights": ops.count(0x3), "loot": ops.count(0x5),
                   "patches": ops.count(0xC), "cells": len(cells),
                   "states": sum(len(r["states"]) for r in rows)}
        title = ""
        for r in rows:
            for st in r["states"]:
                if st.get("text") and st["op_nibble"] in (0x1, 0x2):
                    title = st["text"][:60]
                    break
            if title:
                break
        out.append({"id": root, "root": [int(x) for x in root.split(",")], "title": title,
                    "cells": [[int(x) for x in c.split(",")] for c in cells],
                    "rows": rows, "summary": summary})
    out.sort(key=lambda c: (-c["summary"]["states"], c["id"]))
    return {"events": out, "width": area.width, "height": area.height}


def _service_legacy(rr) -> bool:
    """True when this SERVICE is still in the pre-family hex shape.

    `wl_ir._emit_op_record` checks `fields`/`hdr` FIRST — deliberately, so a half-populated named
    form cannot shadow a record the family model could not reproduce. The consequence for the form
    path is that writing the named key onto such a record is a silent no-op, so the form has to write
    where the emitter reads. `op_record_ir` still authors this shape for selectors 0/2/3/4/7.
    """
    return rr.get("fields") is not None or rr.get("hdr") is not None


def _service_form_bits(rr):
    """(portrait, pages) for either SERVICE shape — None portrait = this family has none."""
    if not _service_legacy(rr):
        return rr.get("portrait"), (rr.get("pages") or [])
    if int(rr.get("selector", 0)) & 0x7F in (0, 1, 2, 3):      # no portrait byte in the layout
        return None, []
    tail = _ir()._unhex(rr.get("tail", "hex:"))
    return (tail[0] if tail else 0), list(tail[1:])


def record_fields(rr, area=None) -> list:
    """The EDITABLE fields of a typed record, as a form description.

    This is the other half of `op_record_ir`: that one turns a form into a record, this one turns a
    record into a form. Keeping the two in one file is deliberate — the field list and the byte
    layout drifting apart is the bug class that cost this project a day (two emitters, two readers).

    Each field: {key, label, kind, value, hint?}. kind is text|int|bool|cell|edge|items|pages,
    which is all the UI needs to render it; the byte meaning stays here.
    """
    op = rr.get("op")
    F = []
    def edge(key, label, adv):
        F.append({"key": key, "label": label, "kind": "edge",
                  "value": {"op": (adv or {}).get("op", 0xFF), "id": (adv or {}).get("id", 0)},
                  "hint": "which state this cell becomes next · op 0 = the cell goes empty"})
    if op == "ENCOUNTER":
        f = rr.get("flags", {})
        s0 = (rr.get("slots") or [{}])[0]
        F += [{"key": "msg", "label": "announce", "kind": "text", "value": rr.get("msg", 0)},
              # `kind: foe` rather than a raw int: the number is an index into a table the editor
              # already has, so the form can offer the NAME and the face instead of asking the
              # author to remember that Highpool's 1 is a Brat.
              {"key": "foe", "label": "foe", "kind": "foe", "value": s0.get("foe", 0)},
              {"key": "count", "label": "how many", "kind": "int", "value": s0.get("count", 1)},
              {"key": "active", "label": "active", "kind": "bool", "value": bool(f.get("active"))},
              {"key": "peaceful", "label": "peaceful", "kind": "bool", "value": bool(f.get("peaceful")),
               "hint": "stands there until the player presses E; never attacks on its own"},
              {"key": "stationary", "label": "stays put", "kind": "bool", "value": bool(f.get("bit2")),
               "hint": "without this a hostile group walks toward the party"},
              {"key": "dist_a", "label": "range", "kind": "int", "value": rr.get("dist_a", 0x3C)}]
        edge("victory", "after the fight", rr.get("victory"))
    elif op == "PATCH":
        F.append({"key": "msg", "label": "message", "kind": "text", "value": rr.get("msg", 0)})
        F.append({"key": "entries", "label": "rewrites", "kind": "cells",
                  "value": [{"x": e["dx"], "y": e["dy"], "op": e["op"], "id": e["id"]}
                            for e in rr.get("entries", [])],
                  "hint": "absolute map cells — the only way a script reaches a DIFFERENT square"})
        edge("after", "this cell becomes", rr.get("after"))
    elif op == "LOOT":
        F.append({"key": "items", "label": "contains", "kind": "items",
                  "value": [{"item": i["item"] & 0x7F, "qty": i.get("qty", i.get("qty16", 1))}
                            for i in rr.get("items", [])]})
        edge("advance", "after taking it", rr.get("advance"))
    elif op == "SAY":
        F.append({"key": "messages", "label": "says", "kind": "pages",
                  "value": [int(x) & 0x7F for x in (rr.get("messages") or [0])],
                  "hint": "one page per string — the cell then advances by HOW MANY there are "
                          "(scene_op_say ends `tya / jmp WB105`), so a SAY has no target to pick"})
    elif op == "OBJ":
        # same two form keys and the same values as before the fields were named: `pad` is still
        # the whole byte, because solid+tile is what the designer types into one box
        F += [{"key": "message", "label": "message", "kind": "text",
               "value": rr.get("msg", 0) & 0x7F},
              {"key": "pad", "label": "drawn tile", "kind": "int",
               "value": (rr.get("tile", 0) & 0x7F) | (0x80 if rr.get("solid") else 0),
               "hint": "op-4 DRAWS this tile over the map for as long as the cell stays op-4"}]
        edge("use", "when stepped on", rr.get("use"))
    elif op == "BUMP":
        F.append({"key": "msg", "label": "says", "kind": "text", "value": rr.get("msg", 0)})
        edge("next", "afterwards", rr.get("next"))
    elif op == "COND":
        # The gate. Everything a designer actually decides about it lives in these fields, and none
        # of them was editable — the form opened empty and the conditions could only be read out of
        # the prose. The pass/fail branch naming is not a guess: two existing consumers already read
        # +$04/+$05 as SUCCESS and +$06/+$07 as FAIL (see the byte-field inspector), which is what
        # the IR calls `done` and `alt`.
        F += [{"key": "prompt", "label": "asks", "kind": "text", "value": rr.get("prompt", 0)},
              {"key": "success", "label": "says on pass", "kind": "text", "value": rr.get("success", 0)},
              {"key": "fail", "label": "says on fail", "kind": "text", "value": rr.get("fail", 0)},
              {"key": "conds", "label": "conditions", "kind": "conds",
               "value": [{"op": c["opb"] >> 5, "arg": c["arg"], "difficulty": c["opb"] & 0x1F,
                          "label": _cond_label(c["opb"] >> 5, c["arg"], c["opb"] & 0x1F)}
                         for c in rr.get("conds", [])],
               "options": COND_KINDS, "vocab": cond_vocab(),
               "hint": "all of them must hold · difficulty 0 = auto-pass given the skill, 1-6 harder"}]
        edge("done", "when it passes", rr.get("done"))
        edge("alt", "when it fails", rr.get("alt"))
        eff = rr.get("effect") or {}
        F += [{"key": "eff_target", "label": "effect on", "kind": "int", "value": eff.get("target", 0),
               "hint": "member-record offset · $1D CON (damage/heal) · $15 money · $21 XP · 0 = none"},
              {"key": "eff_value", "label": "effect value", "kind": "int", "value": eff.get("value", 0)}]
    elif op == "SERVICE":
        # The 11-byte name is what the story row can call this thing. Without it a service reads as
        # a blank line — eleven of TREX Base's twelve are shops and doctors with no pages at all,
        # so "0 pages" was the only thing left to say about them.
        F.append({"key": "name", "label": "name", "kind": "str", "value": str(rr.get("name", "")).strip(),
                  "hint": "11 characters, upper case — the label in the service box"})
        # a portrait id is an index into the str pool the editor already renders — same reason the
        # encounter's foe id became a picker: show the face, not the number
        port, pages = _service_form_bits(rr)
        if port is not None:                  # families 0-3 have none: their record ends at or
            F.append({"key": "portrait", "label": "portrait", "kind": "portrait",   # before +$13,
                      "value": int(port)})                                          # so the box only
        if rr.get("selector") == 9:                                                 # offered a lie
            F.append({"key": "pages", "label": "pages", "kind": "pages",
                      "value": [int(b) & 0x7F for b in pages]})
    return F


def patch_record_fields(rr, patch: dict) -> dict:
    """Apply the form values from `record_fields` back onto the typed record. Returns the record."""
    op = rr.get("op")
    def edge(key):
        if key in patch:
            v = patch[key] or {}
            o = int(v.get("op", 0xFF)) & 0xFF
            rr[key] = {"op": o} if o >= 0x10 else {"op": o, "id": int(v.get("id", 0)) & 0xFF}
    if op == "ENCOUNTER":
        for k in ("msg", "dist_a", "dist_b"):
            if k in patch:
                rr[k] = int(patch[k]) & 0xFF
        if "foe" in patch or "count" in patch:
            s = rr.setdefault("slots", [{}, {}, {}])[0]
            if "foe" in patch:
                s["foe"] = int(patch["foe"]) & 0x7F
            if "count" in patch:
                s["count"] = int(patch["count"]) & 0x7F
        f = rr.setdefault("flags", {})
        for key, bit in (("active", "active"), ("peaceful", "peaceful"), ("stationary", "bit2")):
            if key in patch:
                f[bit] = bool(patch[key])
        edge("victory")
    elif op == "PATCH":
        if "msg" in patch:
            rr["msg"] = int(patch["msg"]) & 0x7F
        if "entries" in patch:
            rr["entries"] = [{"flags": 0, "dx": int(e["x"]) & 0xFF, "dy": int(e["y"]) & 0xFF,
                              "op": int(e["op"]) & 0xFF, "id": int(e["id"]) & 0xFF}
                             for e in patch["entries"]]
        edge("after")
    elif op == "LOOT":
        if "items" in patch:
            rr["items"] = [{"item": (int(i["item"]) & 0x7F) | 0x80, "qty": int(i.get("qty", 1)) & 0xFF}
                           for i in patch["items"]]
        edge("advance")
    elif op == "SAY":
        if "messages" in patch:
            rr["messages"] = [int(x) & 0x7F for x in patch["messages"]] or [0]
        rr.pop("branch", None)                # a SAY never had one; drop it from older documents
    elif op == "OBJ":
        if "message" in patch:
            rr["msg"] = int(patch["message"]) & 0x7F
        if "pad" in patch:
            v = int(patch["pad"]) & 0xFF
            rr["tile"], rr["solid"] = v & 0x7F, bool(v & 0x80)
        rr.pop("data", None)              # older documents carried the raw pair; it would shadow
        edge("use")                       # the named fields on emit and the form save would be lost
    elif op == "BUMP":
        if "msg" in patch:
            rr["msg"] = int(patch["msg"]) & 0xFF
        edge("next")
    elif op == "COND":
        # FULL byte, not & $7F. Shipped gates carry bit7 on success/fail (Scott's Bar grp2[5] has
        # $80/$81), and masking it off rewrote 16 of the 719 shipped gates the moment the form was
        # saved unchanged. What bit7 means there is not established, so it is carried, not cleaned.
        for k in ("prompt", "success", "fail"):
            if k in patch:
                rr[k] = int(patch[k]) & 0xFF
        if "conds" in patch:
            # NO 8-entry cap. `op_record_ir` caps a NEW record at 8 because a hand-authored gate
            # that long is a mistake; an EXISTING one is whatever it is, and shipped records go up
            # to 13 — those are the TRADE half of the op-2 duality, where the list is a shop's wares
            # (op 1 = possession) rather than a check. Truncating to 8 silently dropped five wares
            # and slid the per-condition branch table underneath them.
            rr["conds"] = [{"opb": ((int(c.get("op", 0)) & 0x07) << 5) | (int(c.get("difficulty", 0)) & 0x1F),
                            "arg": int(c.get("arg", 0)) & 0xFF} for c in patch["conds"][:64]]
            # b0 bit3 means one [op][id] branch pair per condition follows the $FF terminator
            # (measured: 76/79 baselines end exactly 2N bytes later). Add or drop a condition and
            # that table has to follow, or the engine reads the NEXT record's bytes as a branch.
            if rr.get("b0", 0) & 0x08:
                br = list(rr.get("branches") or [])
                n = len(rr["conds"])
                rr["branches"] = (br + [{"op": 0xFF, "id": 0}] * n)[:n]
        eff = rr.setdefault("effect", {"target": 0, "value": 0})
        if "eff_target" in patch:
            eff["target"] = int(patch["eff_target"]) & 0xFF
        if "eff_value" in patch:
            eff["value"] = int(patch["eff_value"]) & 0xFF
        # NOT the generic edge() helper. That one drops the id when the op is >= $10, which is right
        # for an ADVANCE TAIL — there $FF terminates and no id byte follows. A COND's branches are
        # fixed two-byte pairs at +$04/+$05 and +$06/+$07, always present, so dropping the id makes
        # the record unemittable. Caught by reading every shipped gate back through the form.
        for k in ("done", "alt"):
            if k in patch:
                v = patch[k] or {}
                rr[k] = {"op": int(v.get("op", 0xFF)) & 0xFF, "id": int(v.get("id", 0)) & 0xFF}
    elif op == "SERVICE":
        if "name" in patch:
            # exactly 11 bytes, space-padded — the portrait sits at +$13 BECAUSE the name is 11 + a
            # terminator, so a shorter or longer name would move it
            # 30 of the 33 shipped signs are mixed case — "Trading car", "AG. store", "Infirmary"
            rr["name"] = str(patch["name"])[:11].ljust(11)
        # A legacy record has to be written through its `tail`, or the emitter's hex branch wins and
        # the edit disappears without a word — see _service_legacy.
        legacy = _service_legacy(rr)
        tail = bytearray(_ir()._unhex(rr.get("tail", "hex:")) or b"\x00") if legacy else None
        if "portrait" in patch and _service_form_bits(rr)[0] is not None:
            if legacy:                       # _service_form_bits is also the gate: a family with no
                tail[0] = int(patch["portrait"]) & 0xFF        # portrait byte gets no portrait write
            else:                            # (the old code put it over the first class id)
                rr["portrait"] = int(patch["portrait"]) & 0xFF
        if "pages" in patch:
            if legacy:
                tail = bytearray([tail[0]]) + bytes(int(x) & 0x7F for x in patch["pages"])
                fields = bytearray(_ir()._unhex(rr.get("fields", "hex:00000000")))
                fields[1] = len(patch["pages"])       # talk(9) keeps its page COUNT at +$04, and the
                rr["fields"] = _ir()._hex(bytes(fields))          # render loop bounds itself with it
            else:
                rr["pages"] = [int(x) & 0x7F for x in patch["pages"]]
                rr["page_count"] = len(rr["pages"])
        if legacy and ("portrait" in patch or "pages" in patch):
            rr["tail"] = _ir()._hex(bytes(tail))
    return rr
