#!/usr/bin/env python3
"""
Wasteland portrait (str anim-object) -> C64 multicolour bitmap decoder.

Faithful Python port of the engine's draw_anim_object MAIN-draw path ($064E entry,
plain store) as read from ef_engine.asm ($064E-$06C3) + anim_obj_init ($0586). Decodes
a str blob's main cell/RLE stream into the $6000 MC bitmap exactly like the game.

Blob header (record, at blob[0]): [0]=C1 start-col, [1]=BF row-reset, [2]=C2 end-col,
[3]=C0 end-row, [4..5]=main stream offset, [6..13]=4 anim-slot stream ptrs. All 88
portraits share dims 01 10 0b 63 => box = byte-cols 1..11, pixel-rows 16..99
(= char cols 1-11, char rows 2-12).

Stream decode ($064E, plain STA): read skip_count; read X(start col); if $FF done;
read Y(start row), mode. Fill col X..C2, each col rows (Ystart|BF)..C0:
  if skip_count>0: emit repeat_val, skip_count-- (RLE run)
  else b=read(); if b==mode: skip_count=read(); repeat_val=read(); re-eval same cell
                 else emit b (literal)
Store is plain overwrite into the $6000-based bitmap; addr(col,row)=(row//8)*320+(row%8)+col*8.

Colours are FIXED (not per-portrait): MC bitmap mode, bank1, bitmap $6000, matrix $5C00.
  00=$D021 black, 01=screen-hi $2 red, 10=screen-lo $6 blue, 11=colour-RAM $01 white.
The picture window screen matrix is a uniform $26 fill in the box; colour RAM uniform $01.
"""
import sys

def decode(blob):
    """Return an 8192-byte bytearray = the $6000..$7FFF MC bitmap for one str blob."""
    C1, BF, C2, C0 = blob[0], blob[1], blob[2], blob[3]
    p = blob[4] | (blob[5] << 8)          # main stream offset
    bm = bytearray(8192)

    def rd():
        nonlocal p
        v = blob[p]; p += 1; return v

    skip = rd()                            # draw_anim_skip_count+1 (initial)
    repeat = 0
    X = rd()                               # start byte-col
    if X == 0xFF:
        return bm
    Y = rd()                               # start pixel-row
    mode = rd()

    while True:                            # column/row fill (single run, rts at X==C2)
        addr = (Y >> 3) * 320 + (Y & 7) + X * 8
        if skip != 0:                      # RLE run
            a = repeat
            skip = (skip - 1) & 0xFF
            bm[addr] = a
        else:
            b = rd()
            if b == mode:                  # escape -> set up RLE, re-eval SAME cell
                skip = rd()
                repeat = rd()
                continue                   # jmp draw_anim_skip_count (addr unchanged)
            bm[addr] = b                   # literal
        # advance: down a column to C0, then next column resetting row to BF
        if Y == C0:
            if X == C2:
                return bm                  # draw_anim_done (rts)
            Y = BF
            X = (X + 1) & 0xFF
        else:
            Y = (Y + 1) & 0xFF


# fixed picture-window colour scheme (verified in-game: colour RAM uniform $F1=$01)
BOX = (1, 12, 2, 13)          # char cols 1..11, rows 2..12 (exclusive hi)


def box_of(blob):
    """The blob's OWN box, from its header — not the portrait constant.

    All 88 portraits share `01 10 0b 63`, so BOX above was right for every one of them and the
    constant went unnoticed. It is wrong for anything else: the title-screen animation
    (`loadB_intro_text.bin`, 6400 B) is the SAME anim-object format with `01 08 24 87` — 36 byte-
    columns by 135 pixel-rows — and rendering it through BOX shows its top-left corner.

    header: [0]=C1 start-col  [1]=BF row-reset  [2]=C2 end-col  [3]=C0 end-row
    """
    c1, bf, c2, c0 = blob[0], blob[1], blob[2], blob[3]
    return (c1, c2 + 1, bf // 8, c0 // 8 + 1)
SCREEN_HI, SCREEN_LO, CRAM, BG = 2, 6, 1, 0     # red / blue / white / black
PEPTO = [(0,0,0),(255,255,255),(129,51,43),(112,190,196),(129,55,148),(86,171,54),
         (64,49,141),(191,206,114),(129,84,29),(72,60,0),(172,101,92),(68,68,68),
         (108,108,108),(154,239,123),(126,110,213),(149,149,149)]

def render_png(bm, path, scale=5, box=None):
    from PIL import Image
    c0, c1, r0, r1 = box or BOX
    NW, NH = (c1 - c0) * 4 * 2, (r1 - r0) * 8      # MC pixel = 2 C64px wide (2:1)
    img = Image.new('RGB', (NW, NH)); px = img.load()
    for r in range(r0, r1):
        for c in range(c0, c1):
            off = r * 320 + c * 8
            for l in range(8):
                byte = bm[off + l]
                for pp in range(4):
                    bits = (byte >> ((3 - pp) * 2)) & 3
                    col = {0: BG, 1: SCREEN_HI, 2: SCREEN_LO, 3: CRAM}[bits]
                    x = ((c - c0) * 4 + pp) * 2; y = (r - r0) * 8 + l
                    px[x, y] = PEPTO[col]; px[x + 1, y] = PEPTO[col]
    img.resize((NW * scale, NH * scale), Image.NEAREST).save(path)

def crop_box(bm):
    """121-cell (11x11) box as 968 raw bitmap bytes, row-major cell order."""
    c0, c1, r0, r1 = BOX
    out = bytearray()
    for r in range(r0, r1):
        for c in range(c0, c1):
            off = r * 320 + c * 8
            out += bm[off:off + 8]
    return bytes(out)

if __name__ == '__main__':
    blob = open(sys.argv[1], 'rb').read()
    bm = decode(blob)
    if len(sys.argv) > 2:
        render_png(bm, sys.argv[2], box=box_of(blob))
    sys.stdout.buffer.write(bm)
