#!/usr/bin/env python3
"""
Wasteland 5-bit packed-text decoder + string-table reader.
Reverse-engineered from block2 engine ($0200) routines:
  W291D ($291D) : 5-bit bit reader, LSB-first within each byte.
  W28F4 ($28F4) : decode one char. $1E = upshift (next letter uppercase),
                  $1F = extended (read 5 more bits, +$1E -> table idx 30..61).
                  Char = table[idx]; table value 0 terminates the string.
  W28E2 ($28E2) : $31/$32 (bitstream) = record_base ($52/$53) + $3C.
  W1E0E ($1E0E) : string-table indexing. Offset table @ base+$3C, 2-byte LE
                  entries, ONE entry per GROUP OF 4 strings. For string idx:
                    group = idx>>2 ; rem = idx&3
                    grp_start = base+$3C + LE16(OT[group])
                    skip `rem` null-terminated strings, then decode.
  Table        : the 5-bit -> PETSCII map. Either embedded at the block base
                 (62 bytes) or the engine-global table at $29E7
                 (" earoitnlcmpduhk\\r fgbywv1.x9)j7...").

VALIDATED byte-exact on:
  - block3 title/intro  (table " teanoisrd..." @ file 1656): "WASTELAND!",
    "Place : EARTH", "Year : 1998", "Status: DEFCON 1", ...
  - utils char-create   (table " etnrisa..."   @ file 2441): "Name:", "Rank:",
    "Male/Female", "U.S./Russian/Mexican/Indian/Chinese", ...
  - Doctor area block T25/phys15 (embedded table " eo<0>tn<13>asirdlhyupmwc.g"):
    "Radiation poisoning", "Wasteland Herpes", "Healing costs $", ...
"""
import sys

# engine-global table at $29E7 (40 captured entries + pad to 62)
G29E7 = bytes([
    0x20,0x65,0x61,0x72,0x6f,0x69,0x74,0x6e,0x6c,0x63,0x6d,0x70,0x64,0x75,0x68,0x6b,
    0x0d,0x66,0x67,0x62,0x79,0x77,0x76,0x31,0x2e,0x78,0x39,0x29,0x6a,0x37,0x0b,0x03,
    0x02,0x7a,0x06,0x04,0x01,0x27,0x71,0x3f]) + bytes(22)


class Bits5:
    """5-bit reader, LSB-first within each byte (matches engine W291D)."""
    def __init__(self, data, pos=0):
        self.d = data; self.p = pos; self.cnt = 0; self.cur = 0

    def read5(self):
        v = 0
        for i in range(5):
            if self.cnt == 0:
                if self.p >= len(self.d):
                    raise EOFError
                self.cur = self.d[self.p]; self.p += 1; self.cnt = 8
            v |= (self.cur & 1) << i
            self.cur >>= 1; self.cnt -= 1
        return v


def decode_string(data, br, table):
    """Decode one null-terminated 5-bit string via reader `br`. None on EOF."""
    out = []; up = False
    while True:
        try:
            c = br.read5()
        except EOFError:
            return None if not out else ''.join(out)
        if c == 0x1E:           # upshift: next letter uppercase
            up = True; continue
        if c == 0x1F:           # extended char
            try:
                idx = br.read5() + 0x1E
            except EOFError:
                return ''.join(out)
        else:
            idx = c
        if idx >= len(table):
            return ''.join(out)
        v = table[idx]
        if v == 0:              # terminator
            break
        ch = chr(v) if 32 <= v < 127 else ('\n' if v == 0x0d else f'<{v:02x}>')
        if up and 0x61 <= v <= 0x7a:
            ch = chr(v - 0x20); up = False
        elif up:
            up = False
        out.append(ch)
    return ''.join(out)


def get_string(data, base, idx, table, ot_off=0x3C):
    """Fetch string #idx from the string table at `base` (offset table @ base+ot_off)."""
    group = idx >> 2; rem = idx & 3
    ot = base + ot_off
    e = data[ot + group * 2] | (data[ot + group * 2 + 1] << 8)
    br = Bits5(data, ot + e)
    for _ in range(rem):
        decode_string(data, br, table)
    return decode_string(data, br, table)


def dump_table(data, base, table, ot_off=0x3C, max_strings=None):
    """Yield (idx, text) for every string in the table. Group count from OT[0]."""
    ot = base + ot_off
    ent0 = data[ot] | (data[ot + 1] << 8)
    ngroups = ent0 // 2
    n = ngroups * 4 if max_strings is None else max_strings
    for idx in range(n):
        yield idx, get_string(data, base, idx, table, ot_off)


if __name__ == '__main__':
    # usage: wl_text5.py <file> <table-base-hex> [ot-off-hex]
    path = sys.argv[1]
    base = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0
    ot_off = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0x3C
    d = open(path, 'rb').read()
    if path.endswith('.prg'):
        d = d[2:]
    tbl = d[base:base + 62]
    for idx, s in dump_table(d, base, tbl, ot_off):
        if s and s.strip():
            print(f"[{idx:3d}] {s!r}")
