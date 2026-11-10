#!/usr/bin/env python3
"""wl_text5_encode.py — ENCODE side of the Wasteland 5-bit packed-text codec + full text-region
rebuilder for the editor's prose editor. Inverse of wl_text5.py (decode).

The record's text table lives at base = LE16(raw[slot+0]) - $3400 and is laid out:
    [base .. base+$3C)   char table  (5-bit idx → PETSCII; value 0 = string terminator)
    [base+$3C ..]        offset table (OT): 2-byte LE, ONE entry per GROUP-OF-4 strings.
                         OT[g] = byte offset from base+$3C to that group's bitstream.
    then                 the 5-bit packed strings, group by group (4 null-terminated / group).

ENCODING is *decode-faithful*, not bit-identical to the original: we only need
decode(encode(s)) == s. A char (PETSCII v) encodes as:
    - uppercase letter A-Z  → upshift $1E, then encode lowercase (v+$20)
    - idx in 0..29 with table[idx]==v          → the 5-bit value idx
    - idx in 30..61 with table[idx]==v          → $1F, then (idx-$1E) as 5 bits
    - string terminator                         → the 5-bit idx whose table[idx]==0
Bits pack LSB-first within each byte (matches engine reader W291D).

REBUILD via RELOCATION: the freshly built region (char table copied verbatim + new OT + new
bitstreams) is appended at the END of the blob and raw[slot+0] is repointed to it. Nothing else in
the record moves, so no other pointer/table is invalidated regardless of what followed the old
region. The old region bytes are left in place (dead).
"""
from __future__ import annotations

import wl_text5
from wl_machine import RECORD_BASE as RAM   # $3400 on the C64, $D000 on the Apple

TERM = "\x00"   # not used directly; terminator handled via table


class _BitW:
    """5-bit writer, LSB-first within each byte (inverse of wl_text5.Bits5)."""
    def __init__(self):
        self.out = bytearray()
        self.cur = 0
        self.cnt = 0

    def write5(self, v: int):
        for i in range(5):
            self.cur |= ((v >> i) & 1) << self.cnt
            self.cnt += 1
            if self.cnt == 8:
                self.out.append(self.cur)
                self.cur = 0
                self.cnt = 0

    def finish(self) -> bytes:
        if self.cnt:                       # flush the partial last byte (high bits = 0)
            self.out.append(self.cur)
            self.cur = 0
            self.cnt = 0
        return bytes(self.out)


def _reverse(table: bytes):
    """Build char→emit-plan from a per-record char table. Returns (direct, ext, term_idx).
    direct[v] = 5-bit idx (0..29); ext[v] = idx (30..61, emitted as $1F,idx-$1E)."""
    direct: dict[int, int] = {}
    ext: dict[int, int] = {}
    term_idx = None
    # idx 0..29 = direct 5-bit codes; idx 30..61 = extended ($1F, idx-$1E). The control codes $1E/$1F
    # are FIRST-READ VALUES, not table indices — table[30]/[31] are real extended char slots.
    for idx in range(min(62, len(table))):
        v = table[idx]
        if v == 0:
            if term_idx is None:
                term_idx = idx
            continue
        if idx < 0x1E:
            direct.setdefault(v, idx)
        else:
            ext.setdefault(v, idx)
    return direct, ext, term_idx


def _tokens(s: str):
    """Split a decoded string into PETSCII values. '\\n'→$0D, '<xx>'→$xx, else ord(c)."""
    vals = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\n":
            vals.append(0x0D); i += 1
        elif c == "<" and i + 3 < len(s) and s[i + 3] == ">" and all(ch in "0123456789abcdefABCDEF" for ch in s[i + 1:i + 3]):
            vals.append(int(s[i + 1:i + 3], 16)); i += 4
        else:
            vals.append(ord(c)); i += 1
    return vals


class EncodeError(ValueError):
    pass


def _char_reprs(s: str):
    """The PETSCII value(s) an encoder must be able to emit for string s. Uppercase A-Z encodes as
    upshift + lowercase, so it only needs the LOWERCASE letter in the table."""
    need = []
    for v in _tokens(s):
        need.append(v + 0x20 if 0x41 <= v <= 0x5A else v)
    return need


def _extend_table(table: bytes, strings: list[str]) -> tuple[bytearray, dict, dict, int]:
    """Copy the record's char table and add any chars the strings need into spare (value-0) slots,
    keeping exactly one 0-slot as the terminator. Returns (table, direct, ext, term_idx).
    Raises EncodeError if a char can't be placed (no free slots)."""
    tbl = bytearray(table[:0x3C])
    if len(tbl) < 0x3C:
        tbl += bytes(0x3C - len(tbl))
    direct, ext, term_idx = _reverse(tbl)
    if term_idx is None:
        raise EncodeError("char table has no terminator slot (no idx with value 0)")
    have = set(direct) | set(ext)
    # free slots = value-0 OR $7F (the build's FILLER marker, wl_edit_prose.py) except the reserved
    # terminator; prefer direct (idx<30, 1 code). Area 39 parks its spare slots as $7F, not 0, so
    # only counting 0 made a table with 12 free slots report "full".
    free = [i for i in range(len(tbl)) if tbl[i] in (0, 0x7F) and i != term_idx]
    free.sort(key=lambda i: (i >= 0x1E, i))          # direct slots first
    needed = []
    for s in strings:
        for v in _char_reprs(s):
            if v not in have and v not in needed:
                needed.append(v)
    for v in needed:
        # A char that is not a BYTE cannot go in a byte table at any table fullness — an em dash is
        # U+2014, and the slot assignment below used to raise a bare ValueError on it, which
        # `unencodable` does not catch. That turned "you typed a dash we cannot write" into a 500.
        if not 0 <= v <= 0xFF:
            raise EncodeError(f"char U+{v:04X} ({chr(v)}) is not a byte — the table cannot hold it")
        if not free:
            raise EncodeError(f"char table full — cannot add ${v:02X} "
                              f"({chr(v) if 32 <= v < 127 else '?'}); free {len(free)} slots left")
        idx = free.pop(0)
        tbl[idx] = v
        (direct if idx < 0x1E else ext)[v] = idx
        have.add(v)
    return tbl, direct, ext, term_idx


def _emit_string(s: str, direct: dict, ext: dict, term_idx: int, bw: _BitW):
    for v in _tokens(s):
        if 0x41 <= v <= 0x5A:                        # uppercase A-Z → upshift + lowercase
            bw.write5(0x1E); v += 0x20
        if v in direct:
            bw.write5(direct[v])
        elif v in ext:
            bw.write5(0x1F); bw.write5(ext[v] - 0x1E)
        else:
            raise EncodeError(f"char ${v:02X} not encodable")   # _extend_table should have placed it
    bw.write5(term_idx)


def unencodable(table: bytes, strings: list[str]) -> list[int]:
    """Return the distinct chars in `strings` that can't be encoded even after auto-extending the
    table (i.e. the table is full). Empty list = all fine. For the editor's validation."""
    try:
        _extend_table(table, strings)
        return []
    except EncodeError:
        pass
    # recompute exactly what wouldn't fit
    tbl = bytearray(table[:0x3C])
    direct, ext, term_idx = _reverse(tbl)
    have = set(direct) | set(ext)
    free = sum(1 for i in range(len(tbl)) if tbl[i] in (0, 0x7F)) - 1   # $7F = FILLER reserve
    over, impossible = [], []
    for s in strings:
        for v in _char_reprs(s):
            if v in have:
                continue
            if not 0 <= v <= 0xFF:                   # never placeable, whatever the table looks like
                if v not in impossible:
                    impossible.append(v)
            elif v not in over:
                over.append(v)
    return impossible + over[max(0, free):]


def enumerate_strings(raw: bytes, base: int, table: bytes | None = None) -> list[str]:
    """Robustly enumerate all strings in a record text table. Walks the OT group by group; stops
    when an OT entry is out of range, non-monotonic, or points into the OT itself (the OT[0]//2
    count over-reports by one on some records — see a10)."""
    if table is None:
        table = raw[base:base + 62]
    ot = base + 0x3C
    ot_size = raw[ot] | (raw[ot + 1] << 8)          # OT[0] = bytes of OT (data starts after it)
    ngroups_cap = ot_size // 2
    strings: list[str] = []
    prev = -1
    for g in range(ngroups_cap):
        e = raw[ot + g * 2] | (raw[ot + g * 2 + 1] << 8)
        if e < ot_size or e < prev or ot + e >= len(raw):
            break                                    # garbage/spare OT slot → real groups done
        prev = e
        br = wl_text5.Bits5(raw, ot + e)
        for _ in range(4):
            s = wl_text5.decode_string(raw, br, table)
            strings.append("" if s is None else s)
    return strings


def build_region(table: bytes, strings: list[str]) -> bytes:
    """Build a complete text region: char table (auto-extended for new chars) + fresh OT + packed
    strings. Groups of 4 strings; OT has one entry per group; OT[g] = offset from (region+$3C)."""
    tbl, direct, ext, term_idx = _extend_table(table, strings)
    groups = [strings[i:i + 4] for i in range(0, len(strings), 4)] or [[]]
    blobs = []
    for grp in groups:
        bw = _BitW()
        for s in grp:
            _emit_string(s, direct, ext, term_idx, bw)
        # pad the group to 4 with empty strings — the decoder always reads 4 per group, so unused
        # slots must be real (empty) terminators, else they decode trailing binary as garbage
        for _ in range(4 - len(grp)):
            _emit_string("", direct, ext, term_idx, bw)
        blobs.append(bw.finish())
    ot_size = len(groups) * 2
    ot = bytearray()
    off = ot_size                                    # first group's data begins right after the OT
    for b in blobs:
        ot += bytes([off & 0xFF, (off >> 8) & 0xFF])
        off += len(b)
    return tbl + bytes(ot) + b"".join(blobs)


def region_end(raw: bytes, base: int) -> int:
    """Byte offset just past the last string of the text region at `base` (robust group walk)."""
    table = raw[base:base + 62]
    ot = base + 0x3C
    ot_size = raw[ot] | (raw[ot + 1] << 8)
    end = ot + ot_size
    prev = -1
    for g in range(ot_size // 2):
        e = raw[ot + g * 2] | (raw[ot + g * 2 + 1] << 8)
        if e < ot_size or e < prev or ot + e >= len(raw):
            break
        prev = e
        br = wl_text5.Bits5(raw, ot + e)
        for _ in range(4):
            wl_text5.decode_string(raw, br, table)
        end = br.p
    return end


def rebuild_record_text(raw: bytes, slot: int, strings: list[str]) -> bytes:
    """Return a new record blob with `strings` as its text table. If the old region already sits at
    the blob tail (true after any prior rebuild), it is rebuilt IN PLACE at the same base (slot+0
    unchanged, no bloat). Otherwise the new region is RELOCATED to the blob end and slot+0 repointed
    — nothing else moves, so no other pointer/table is invalidated. `strings` = the full list."""
    base = (raw[slot] | (raw[slot + 1] << 8)) - RAM
    table = raw[base:base + 62]
    region = build_region(table, strings)
    out = bytearray(raw)
    # ALWAYS relocate (append at the end + repoint slot+0), NEVER the in-place `del out[base:]`.
    # The in-place path assumed the text region was the blob tail, decided via region_end() — but
    # region_end walks the packed bitstream with NO hard terminator, so if ANY data follows the text
    # (e.g. a grp11 chain block appended by write_cell_chain) it walks INTO it and returns >= len,
    # and the `del` then wipes that data too. That silently destroyed op-B rotation chains on every
    # text edit. Relocating only ever APPENDS — it can never delete a record. The old text region is
    # left as dead space; compact_record (orphaned-text-region GC) reclaims it.
    new_base_ram = len(raw) + RAM
    out[slot] = new_base_ram & 0xFF
    out[slot + 1] = (new_base_ram >> 8) & 0xFF
    out += region
    return bytes(out)

def char_budget(table: bytes, strings: list[str]) -> dict:
    """Report the char-table budget for a set of strings WITHOUT writing. For the editor's
    pre-save validation: how many free slots, which new chars are needed, and — if it won't fit —
    exactly which chars can't be placed and which existing chars are used only once (so a human can
    decide to drop one). Free slots are value-0 OR $7F (the build's FILLER marker)."""
    tbl = bytearray(table[:0x3C])
    direct, ext, term_idx = _reverse(tbl)
    have = set(direct) | set(ext)
    free = [i for i in range(len(tbl)) if tbl[i] in (0, 0x7F) and i != term_idx]
    needed = []
    for s in strings:
        for v in _char_reprs(s):
            if v not in have and v not in needed:
                needed.append(v)
    # usage count of each existing char across the given strings (for "used once -> droppable")
    from collections import Counter
    used = Counter(v for s in strings for v in _char_reprs(s))
    droppable = sorted(v for v in have if used.get(v, 0) == 0)   # in table, referenced by no string
    def show(v):
        return chr(v) if 32 <= v < 127 else f"${v:02X}"
    fits = len(needed) <= len(free)
    return {
        "free": len(free),
        "needed": [show(v) for v in needed],
        "fits": fits,
        "overflow": [show(v) for v in (needed[len(free):] if not fits else [])],
        "droppable": [show(v) for v in droppable],   # existing chars no string uses -> free to reuse
    }

