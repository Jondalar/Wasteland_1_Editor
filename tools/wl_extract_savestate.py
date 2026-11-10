#!/usr/bin/env python3
"""
Extract the Wasteland save-state party records from a .d64.

Spec (reverse-engineered + LIVE-trace verified 2026-06-06, see docs/DATA_MODEL.md
"LIVE TRACE"):
  - 8 character/save records, 256 B each, on TRACK 35.
  - record r (-> RAM $F400 + r*$100) lives in T35 PHYSICAL sector SECTORS[r]
    = [8,2,13,7,1,12,6,0]  (= W66AF table in the savegame editor; idx7->0).
  - on-disk obfuscation = positional XOR: byte[Y] ^= (Y*2)&$FF  for Y=0..254.
    byte $FF (offset 255) is NOT touched = plaintext per-block checksum.
    self-inverse (descramble == scramble). NO compression.

Outputs (2048 B each = 8x256, in $F400 order):
  <out>/<stem>_savestate_raw.bin     as-on-disk (scrambled)
  <out>/<stem>_savestate_plain.bin   descrambled (plaintext = what lands at $F400)

Usage: wl_extract_savestate.py <disk.d64> <out_dir>
"""
import sys, os

# d64 sectors per track (1-based track index)
def track_base_sector(track):
    spt = lambda t: 21 if t <= 17 else 19 if t <= 24 else 18 if t <= 30 else 17
    return sum(spt(t) for t in range(1, track))

SECTORS = [8, 2, 13, 7, 1, 12, 6, 0]   # T35 phys sector per record r (-> $F400 + r*256)
TRACK = 35


def descramble(block, key=0x00):
    # on-disk byte[Y] = plain[Y] ^ (Y*2) ^ key   (key = key_seed = record0 byte $FF = $F4FF).
    # The game-written save uses key=checksum (save_scramble_block); a key=$00 save also exists
    # (inline block3 path). Pass the real key_seed = blocks[0][255]. byte 255 untouched (= key).
    out = bytearray(block)
    for y in range(255):                # 0..254 ; byte 255 = checksum/key_seed, untouched
        out[y] ^= ((y * 2) & 0xFF) ^ key
    return bytes(out)


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    d64, outdir = sys.argv[1], sys.argv[2]
    img = open(d64, "rb").read()
    if len(img) != 174848:
        print(f"WARN: {d64} is {len(img)} B (expected 174848 for a 35-track d64)")
    base = track_base_sector(TRACK)     # = 666 for T35
    key = img[(base + SECTORS[0]) * 256 + 255]   # key_seed = record0 ($F400) byte $FF = $F4FF
    print(f"key_seed (rec0 $FF) = ${key:02X}  (was hardcoded $00 = the bug for game-written saves)")
    raw = bytearray()
    plain = bytearray()
    print(f"{'rec':>3} {'$addr':>6} {'T/S':>7}  pop?  name-ish (plain, offs 0..11)")
    for r, sec in enumerate(SECTORS):
        off = (base + sec) * 256
        blk = img[off:off + 256]
        if len(blk) != 256:
            sys.exit(f"ERROR: short read T{TRACK}/S{sec} @ {off}")
        dec = descramble(blk, key)
        raw += blk
        plain += dec
        nonempty = any(b not in (0x21, 0x00) for b in dec[:0x18])
        name = "".join(chr(c) if 32 <= c < 127 else "." for c in dec[:12])
        print(f"{r:>3} ${0xF400 + r*256:04X} T{TRACK}/S{sec:<2}  "
              f"{'YES' if nonempty else 'empty':>5}  {name}")
    os.makedirs(outdir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(d64))[0]
    rp = os.path.join(outdir, f"{stem}_savestate_raw.bin")
    pp = os.path.join(outdir, f"{stem}_savestate_plain.bin")
    open(rp, "wb").write(raw)
    open(pp, "wb").write(plain)
    print(f"\nwrote {rp}  ({len(raw)} B, scrambled / as-on-disk)")
    print(f"wrote {pp}  ({len(plain)} B, descrambled / $F400 plaintext)")


if __name__ == "__main__":
    main()
