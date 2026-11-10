#!/usr/bin/env python3
"""
Wasteland area-data decompressor — byte-verified reimplementation of the $C800
scene-overlay codec (block2 engine). Verified against the live runtime trace
(area_change.duckdb, Desert Nomads): tile-map block (1135 B) + graphics block 4
(277 B) both decode 100% byte-identical.

Codec (per the $C800 disasm):
  - MSB-first bitstream.
  - Each "block"/invocation begins by reading a 20-byte DICTIONARY (8 bits each).
  - Per output symbol:
      bit 0  -> literal: read 8 bits = the byte
      bit 1  -> read 3-bit code:
                  0/1            -> dict[code]
                  else extend the value 1 bit at a time and match the WC8EC
                  code table over 3 length tiers (4/5/6-bit) -> dict[matched index]
                  no match (6-bit $3F) -> RLE run: read 8 bits = count ($E1),
                                          read 8 bits = value ($E4); emit value
                                          'count' times (count-1 more follow).
  - An AREA is a SEQUENCE of such blocks (the engine re-invokes $C800 per
    section: map, then graphics chunks, then sprites). Each block re-reads a
    fresh dictionary. Decode each block to its known output length, then the
    next block's dictionary follows in the stream.

WC8EC table (from $C8EC): code patterns indexed by dict slot.
"""

WC8EC = [0x00,0x01,0x04,0x05,0x06,0x07,0x08,0x09,0x0A,0x16,0x17,0x18,0x19,0x1A,0x1B,0x1C,0x1D,0x3C,0x3D,0x3E]

class BitReader:
    def __init__(self, data, pos=0):
        self.d = data; self.p = pos; self.cnt = 0; self.buf = 0
    def bit(self):
        if self.cnt == 0:
            self.buf = self.d[self.p]; self.p += 1; self.cnt = 8
        self.cnt -= 1
        return (self.buf >> self.cnt) & 1      # MSB-first
    def bits(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | self.bit()
        return v

def decode_block(br, out_len):
    """Decode one block (fresh 20-byte dict) of out_len bytes from BitReader br."""
    D = [br.bits(8) for _ in range(20)]
    out = bytearray(); run = 0; runv = 0
    while len(out) < out_len:
        if run > 0:
            out.append(runv); run -= 1; continue
        if br.bit() == 0:
            out.append(br.bits(8)); continue        # literal
        code = br.bits(3)
        if code <= 1:
            out.append(D[code]); continue           # short dict
        matched = False
        for lo, hi in ((2, 8), (9, 16), (17, 19)):  # 4/5/6-bit tiers
            code = (code << 1) | br.bit()
            for y in range(hi, lo - 1, -1):
                if code == WC8EC[y]:
                    out.append(D[y]); matched = True; break
            if matched:
                break
        if matched:
            continue
        run = br.bits(8); runv = br.bits(8)          # RLE run
        out.append(runv); run = (run - 1) & 0xFF
    return bytes(out), D

def decode_area(data, block_lengths, start=0):
    """Decode a full area = sequence of blocks. block_lengths from the engine
    (per-invocation output sizes; get them from a trace of $C826 stores, or the
    on-disk area-file block table). Returns the concatenated decompressed area."""
    br = BitReader(data, start)
    out = bytearray()
    for n in block_lengths:
        seg, _ = decode_block(br, n)
        out += seg
    return bytes(out)

if __name__ == "__main__":
    import sys
    # demo: decode_area(open(sys.argv[1],'rb').read(), [int(x) for x in sys.argv[2:]])
    print("wl_decompress: import decode_block / decode_area")
