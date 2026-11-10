#!/usr/bin/env python3
"""
Wasteland boot-asset extractor — faithful replay of the resident fastloader
($FC00, ex-$C300) reading the three boot assets the patched installer (2.0) pulls
before 'jmp $7E00'.  STATIC: reads the pristine per-sector rawscan/, applies the
exact (track, start-idx, dest, end-page) ABI the 2.0 code sets up, in the verified
zone interleave + $FD0B step-down order (tools/wl_directory).

WHY THIS TOOL EXISTS — the off-by-one fix:
  The fastloader page loop (reloc_FC00 $FF2E..) writes a 256-byte block, then
      lda dest_hi  /  cmp $FD(end_page)  /  beq return  /  inc dest_hi  /  step
  i.e. it STOPS once the just-written page == end-page.  end-page is the LAST
  page written = INCLUSIVE.  pages = end_page - dest_hi + 1.
  The previous baseline assets were one page short each (end-page read as
  exclusive), which silently dropped:
      resident $C900 (key-decode table $C953 + sfx_play $C993 + intro sprite) ->
             title menu could not read S/U keys (keyboard_scan decodes via $C953)
      engine $3000-$30FF
      block3 $8D00-$8DFF

ABI (verified from 02_2.0_disasm.asm $C0F6 installer, the three jsr $FC00):
  resident: Y track=$22(34)  X start-idx=$0E(14)  $FE/$FF=$C600  $FD=$C6+$03=$C9
  engine : Y track=$22(34)  X start-idx=$0A(10)  $FE/$FF=$0200  $FD=$02+$2E=$30
  block3 : Y track=$04( 4)  X start-idx=$10(16)  $FE/$FF=$7E00  $FD=$7E+$0F=$8D
  (mode A=$01 = raw block read for all three; no codec — codec is area data only.)

Usage:
  wl_extract_boot_assets.py <rawscan_dir> <out_dir> [asset ...]
  asset in {font,engine,block3} (default: all)
Outputs <out_dir>/<asset>_<dest>.prg  (2-byte load addr + body), byte-exact.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wl_directory import idx2phys, step_down, secs  # verified interleave + $FD0B

# asset -> (track, start_idx, dest_addr, end_page)   end_page INCLUSIVE
ASSETS = {
    # $C600-$C9FF resident: font/charset + $C800 codec + $C930 loader zone-table
    # + $C953 key-decode table + $C993 sfx.  (NOT just the font -> name = "resident".)
    "resident": (34, 14, 0xC600, 0xC9),
    "engine": (34, 10, 0x0200, 0x30),
    "block3": (4,  16, 0x7E00, 0x8D),
    # Start overlay (the in-game engine): loader_entry loads T31/blk9 -> $7E00,
    # end-page = $7E + descriptor[$5A05]=$48 - 1 = $C5  (72 pages, $7E00-$C5FF),
    # then jmp $7E00.  Loaded via direct_load_exec (LUT1) at Start.
    "start_overlay": (31, 9, 0x7E00, 0xC5),
}


def replay(track, start_idx, dest, end_page, rawdir):
    """Faithful fastload: walk sectors in step-down order until the page just
    written == end_page (inclusive). Returns (body_bytes, [(t,phys),...])."""
    dest_hi = dest >> 8
    t, i = track, start_idx
    body = bytearray()
    seq = []
    page = dest_hi
    while True:
        phys = idx2phys(t, i)
        f = os.path.join(rawdir, f"t{t}s{phys}.bin")
        if not os.path.exists(f):
            sys.exit(f"ERROR: missing raw sector {f} (asset page ${page:02X}00)")
        blk = open(f, "rb").read()
        if len(blk) != 256:
            sys.exit(f"ERROR: {f} is {len(blk)} bytes, expected 256")
        body += blk
        seq.append((t, phys, i))
        if page == end_page:          # just wrote the end page -> stop (inclusive)
            break
        page += 1
        nx = step_down(t, i, 1)       # $FD0B
        if nx is None:
            sys.exit(f"ERROR: ran off start of disk before reaching end-page "
                     f"${end_page:02X} (asset partial at ${page:02X}00)")
        t, i = nx
    return bytes(body), seq


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    rawdir, outdir = sys.argv[1], sys.argv[2]
    which = sys.argv[3:] or list(ASSETS)
    os.makedirs(outdir, exist_ok=True)
    for name in which:
        if name not in ASSETS:
            sys.exit(f"ERROR: unknown asset '{name}' (have {list(ASSETS)})")
        track, sidx, dest, endp = ASSETS[name]
        body, seq = replay(track, sidx, dest, endp, rawdir)
        npages = (dest >> 8) - (dest >> 8) + len(body) // 256
        out = os.path.join(outdir, f"{name}_{dest:04X}.prg")
        with open(out, "wb") as fh:
            fh.write(bytes([dest & 0xFF, dest >> 8]))
            fh.write(body)
        chain = " ".join(f"t{t}s{p}" for t, p, _ in seq)
        print(f"{name:7s} ${dest:04X}-${dest+len(body)-1:04X}  "
              f"{len(body):5d}B ({len(body)//256}p)  sectors: {chain}")
    print(f"\nwrote {len(which)} asset(s) -> {outdir}")


if __name__ == "__main__":
    main()
