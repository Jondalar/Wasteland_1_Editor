"""wl_tiles_a2.py — decode an Apple II Wasteland map TILESET into RGB.

The C64 and the Apple keep the SAME tileset: tile N is the same picture on both machines, in
the same order, 96 of them. What differs is everything about how a picture is stored, because
the two machines do colour in incompatible ways. wl_tiles.py cannot be parameterised into this;
it is a second format, so it is a second module.

Format (measured against the four Apple sides, read back against the C64 tileset tile by tile):

  - gfx blob = 96 tiles x 32 B = 3072 B of pure bitmap. There is NO colour table: the C64 keeps
    96 x 4 B of colour at blob offset 3072, and the Apple keeps nothing there. Whatever sits
    past 3072 in a depacked Apple blob is the codec overshooting its input budget — in every
    side it is a long RLE run of one alternating byte pair, and it renders as flat dither.
  - one tile = 14 x 16 px. 2 bytes per row, 16 rows, top to bottom.
      byte k of a row = pixels 7k..7k+6, BIT 0 LEFTMOST (the Apple's bit order, not the C64's).
      bit 7 is not a pixel: it selects the colour pair for those 7 pixels.

Colour is NTSC artefact colour, not an attribute. A lit pixel takes its hue from its column
parity and from bit 7 of the byte it sits in; two lit pixels side by side read as white:

      bit7=0   even column -> violet    odd column -> green
      bit7=1   even column -> blue      odd column -> orange
      two adjacent lit pixels -> white        unlit -> black

  Which parity is violet and which is green is a convention — the hardware phase depends on the
  monitor, and emulators disagree. This module picks the common one (even = violet/blue) and
  keeps it in one place, PARITY_EVEN_FIRST, so flipping it is one edit rather than twelve.

WHY THE TILE IS 14 PX AND WHY THAT MATTERS: artefact colour normally depends on where a thing
sits on screen, which would make a tile's colours depend on the column it is drawn in. 14 is
even, and a tile starts at x = column * 14, so the parity inside a tile is the same wherever it
is placed. An Apple tile's colours are fully determined by its own 32 bytes. 280/14 = 20 tiles
across, exactly the C64's 320/16 = 20.

Read-only on purpose: an Apple data root refuses every write (see wl_machine.py and the
_machine_readonly middleware), so there is no set_tile, no compaction and no reorder here.
"""
from __future__ import annotations

import base64

# The Apple II hi-res palette — six colours, no more: there is no palette register on this
# machine to hold anything else. These are not a guess at "what looks Apple-ish". They were
# counted out of real Apple II screenshots of this very game (the title globe, the overworld,
# a portrait), which contain exactly these six values and nothing else.
BLACK  = (0x04, 0x02, 0x04)
WHITE  = (0xFC, 0xFE, 0xFC)
GREEN  = (0x04, 0xFE, 0x04)
VIOLET = (0xFC, 0x02, 0xFC)
BLUE   = (0x04, 0x02, 0xFC)
ORANGE = (0xFC, 0x82, 0x04)

PARITY_EVEN_FIRST = True     # even column = violet (bit7=0) / blue (bit7=1)

# Artefact colour depends on the ABSOLUTE screen column, so anything that does not start at
# x = 0 has to say where it starts. A tile does: it is 14 px wide and sits at column * 14, so
# its parity is always the same and the offset is 0. A portrait does not — it starts at byte
# column 1, i.e. x = 7 — and gets this wrong the colours come out as the other half of each
# pair: blue reads as orange, violet as green. See wl_portrait_a2.

NUM_TILES = 96               # same count as the C64 — the maps address the same ids
TILE_W = 14                  # 2 bytes x 7 pixels
TILE_H = 16
TILE_BYTES = 32
BITMAP_SIZE = NUM_TILES * TILE_BYTES        # 3072; nothing of ours lives past this


def _hue(x: int, palette_bit: int) -> tuple[int, int, int]:
    even = (x % 2 == 0) if PARITY_EVEN_FIRST else (x % 2 == 1)
    if palette_bit:
        return BLUE if even else ORANGE
    return VIOLET if even else GREEN


def decode_tile(gfx: bytes, tile_id: int) -> list[list[tuple[int, int, int]]]:
    """Return a 14x16 grid of RGB tuples for one tile.

    A pixel at the tile's left or right edge is coloured as if its outside neighbour were dark.
    On a real screen that neighbour is the next tile and the pair could read as white instead;
    a tileset viewer shows one tile at a time and has no next tile, so this is the honest choice
    rather than a guess about what it will be drawn beside."""
    px = [[BLACK] * TILE_W for _ in range(TILE_H)]
    base = tile_id * TILE_BYTES
    for row in range(TILE_H):
        o = base + row * 2
        if o + 1 >= len(gfx):
            break
        lit, pal = [], []
        for byte in (gfx[o], gfx[o + 1]):
            for bit in range(7):                 # bit 0 is the LEFTMOST pixel
                lit.append((byte >> bit) & 1)
                pal.append((byte >> 7) & 1)
        for x in range(TILE_W):
            if not lit[x]:
                continue
            left = lit[x - 1] if x > 0 else 0
            right = lit[x + 1] if x < TILE_W - 1 else 0
            px[row][x] = WHITE if (left or right) else _hue(x, pal[x])
    return px


def build_atlas(gfx: bytes, cols: int = 16, **_ignored) -> dict:
    """All 96 tiles in one RGBA atlas, `cols` wide.

    `**_ignored` swallows the C64's bg/cram arguments so the two modules can stand in for each
    other at every call site. They mean nothing here: this machine has no background register
    and no colour RAM.

    The tile is 14x16, not square, so the atlas reports `tile_w`/`tile_h` beside the C64's
    square `tile`. A client that only knows `tile` still slices correctly on the C64 and would
    slice an Apple atlas wrongly, so it must read the pair — frontend/main.js blit() does."""
    rows = (NUM_TILES + cols - 1) // cols
    W, H = cols * TILE_W, rows * TILE_H
    buf = bytearray(W * H * 4)
    for tid in range(NUM_TILES):
        tile = decode_tile(gfx, tid)
        ox, oy = (tid % cols) * TILE_W, (tid // cols) * TILE_H
        for y in range(TILE_H):
            for x in range(TILE_W):
                r, g, b = tile[y][x]
                o = ((oy + y) * W + (ox + x)) * 4
                buf[o:o + 4] = bytes((r, g, b, 255))
    return {
        "w": W, "h": H, "cols": cols, "tile": TILE_W, "tile_w": TILE_W, "tile_h": TILE_H,
        "num_tiles": NUM_TILES, "machine": "apple",
        "rgba_b64": base64.b64encode(bytes(buf)).decode("ascii"),
    }


def tile_empty(gfx: bytes, tid: int) -> bool:
    return gfx[tid * TILE_BYTES:(tid + 1) * TILE_BYTES] == bytes(TILE_BYTES)
