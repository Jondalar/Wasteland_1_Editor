"""wl_tiles.py — decode a Wasteland map TILESET (gfx blob) into RGB, byte-exact.

Format (reverse-engineered, see the decode in cart_EF/docs — draw_map_cell $1374 → W22EA $22EA):
  - gfx blob = 3456 B, loaded verbatim to $D000.
      $D000-$DBFF  = 3072 B = 96 tiles × 32 B   BITMAP   (blob offset id*32)
      $DC00-$DD7F  =  384 B = 96 tiles × 4 B    COLOR    (blob offset 3072 + id*4)
  - one map tile = 16×16 px = 2×2 chars, MULTICOLOR bitmap.
      char order in the 32 bytes: 0-7 TL, 8-15 TR, 16-23 BL, 24-31 BR.
      within a char: byte k = pixel row k (top→bottom).
      within a byte: 4 MC pixels, MSB pair = leftmost ([7:6]=px0 … [1:0]=px3); each MC px = 2 hi-res cols.
  - 2-bit MC pixel → color source:
      %00 → background   ($D021; area 0 = black $00)
      %01 → color-byte HIGH nibble
      %10 → color-byte LOW  nibble
      %11 → colour RAM   (global $01 = white)
  - color byte for a char = blob[3072 + id*4 + cell].
"""
from __future__ import annotations
import base64

# C64 Pepto palette, index 0..15 → RGB.
PEPTO = [
    (0x00, 0x00, 0x00), (0xFF, 0xFF, 0xFF), (0x68, 0x37, 0x2B), (0x70, 0xA4, 0xB2),
    (0x6F, 0x3D, 0x86), (0x58, 0x8D, 0x43), (0x35, 0x28, 0x79), (0xB8, 0xC7, 0x6F),
    (0x6F, 0x4F, 0x25), (0x43, 0x39, 0x00), (0x9A, 0x67, 0x59), (0x44, 0x44, 0x44),
    (0x6C, 0x6C, 0x6C), (0x9A, 0xD2, 0x84), (0x6C, 0x5E, 0xB5), (0x95, 0x95, 0x95),
]

BITMAP_SIZE = 3072          # 96 × 32
COLOR_BASE = 3072           # $DC00 - $D000
NUM_TILES = 96
TILE_PX = 16                # 16×16 (each MC pixel doubled horizontally)


def decode_tile(gfx: bytes, tile_id: int, bg: int = 0, cram: int = 1) -> list[list[tuple[int, int, int]]]:
    """Return a 16×16 grid of RGB tuples for one tile."""
    px = [[(0, 0, 0)] * TILE_PX for _ in range(TILE_PX)]
    bmp = tile_id * 32
    col = COLOR_BASE + tile_id * 4
    for cell in range(4):                       # 0=TL 1=TR 2=BL 3=BR
        cx = (cell & 1) * 8                      # screen x of this char (0 or 8)
        cy = (cell >> 1) * 8                     # screen y of this char (0 or 8)
        cbyte = gfx[col + cell] if col + cell < len(gfx) else 0
        pal = {0: bg, 1: cbyte >> 4, 2: cbyte & 0x0F, 3: cram}
        for row in range(8):
            b = gfx[bmp + cell * 8 + row] if bmp + cell * 8 + row < len(gfx) else 0
            for p in range(4):                   # p0 = leftmost (MSB pair)
                bits = (b >> (6 - 2 * p)) & 0b11
                rgb = PEPTO[pal[bits] & 0x0F]
                x = cx + p * 2                   # double the MC pixel to 2 columns
                y = cy + row
                px[y][x] = rgb
                px[y][x + 1] = rgb
    return px


def get_tile(gfx: bytes, tile_id: int) -> dict:
    """Decode one tile for the pixel editor. MCM bitmap model: a tile = 2×2 chars (TL,TR,BL,BR),
    each char 8 rows × 4 MC-pixels (2 bits). Per-pixel 2-bit value → colour:
      %00 → global background ($D021)   %01 → this char's colour-byte HIGH nibble
      %11 → global colour-RAM ($D800)   %10 → this char's colour-byte LOW nibble
    Returns pixels[16 rows][8 cols] of 2-bit values + per-char {hi,lo} nibbles (0..15).
    """
    bmp = tile_id * 32
    col = COLOR_BASE + tile_id * 4
    chars = []
    for cell in range(4):
        cbyte = gfx[col + cell] if col + cell < len(gfx) else 0
        chars.append({"hi": cbyte >> 4, "lo": cbyte & 0x0F})
    pixels = [[0] * 8 for _ in range(16)]
    for cell in range(4):
        cx = (cell & 1) * 4          # MC-pixel column base (0 or 4)
        cy = (cell >> 1) * 8         # row base (0 or 8)
        for r in range(8):
            b = gfx[bmp + cell * 8 + r] if bmp + cell * 8 + r < len(gfx) else 0
            for p in range(4):       # p0 = leftmost (MSB pair)
                pixels[cy + r][cx + p] = (b >> (6 - 2 * p)) & 0b11
    return {"tile": tile_id, "pixels": pixels, "chars": chars}


def set_tile(gfx: bytes, tile_id: int, pixels: list[list[int]], chars: list[dict]) -> bytes:
    """Encode an edited tile back into the gfx blob (bitmap 32 B + colour 4 B). Byte-exact inverse
    of get_tile. pixels[16][8] 2-bit values; chars = 4×{hi,lo} nibbles."""
    out = bytearray(gfx)
    bmp = tile_id * 32
    col = COLOR_BASE + tile_id * 4
    for cell in range(4):
        h = chars[cell]["hi"] & 0x0F
        l = chars[cell]["lo"] & 0x0F
        if col + cell < len(out):
            out[col + cell] = (h << 4) | l
        cx = (cell & 1) * 4
        cy = (cell >> 1) * 8
        for r in range(8):
            b = 0
            for p in range(4):
                b |= (pixels[cy + r][cx + p] & 0b11) << (6 - 2 * p)
            if bmp + cell * 8 + r < len(out):
                out[bmp + cell * 8 + r] = b
    return bytes(out)


def copy_tile(dst_gfx: bytes, dst_id: int, src_gfx: bytes, src_id: int) -> bytes:
    """Copy one tile (bitmap 32 B + colour 4 B) from src tileset into dst at dst_id."""
    out = bytearray(dst_gfx)
    sb, db = src_id * 32, dst_id * 32
    sc, dc = COLOR_BASE + src_id * 4, COLOR_BASE + dst_id * 4
    out[db:db + 32] = src_gfx[sb:sb + 32]
    out[dc:dc + 4] = src_gfx[sc:sc + 4]
    return bytes(out)


def build_atlas(gfx: bytes, bg: int = 0, cram: int = 1, cols: int = 16) -> dict:
    """Render all 96 tiles into one RGBA atlas (cols wide). Returns {w,h,cols,tile,rgba_b64}."""
    rows = (NUM_TILES + cols - 1) // cols
    W, H = cols * TILE_PX, rows * TILE_PX
    buf = bytearray(W * H * 4)
    for tid in range(NUM_TILES):
        tile = decode_tile(gfx, tid, bg, cram)
        ox, oy = (tid % cols) * TILE_PX, (tid // cols) * TILE_PX
        for y in range(TILE_PX):
            for x in range(TILE_PX):
                r, g, b = tile[y][x]
                o = ((oy + y) * W + (ox + x)) * 4
                buf[o:o + 4] = bytes((r, g, b, 255))
    return {
        "w": W, "h": H, "cols": cols, "tile": TILE_PX, "num_tiles": NUM_TILES,
        "rgba_b64": base64.b64encode(bytes(buf)).decode("ascii"),
    }


FIXED = frozenset(range(10))
"""Engine-fixed figure glyphs — the engine draws these by hardcoded id (player=4, loot sack=5,
op-9 hazard=8, monsters), so they must never move or be cleared."""


def tile_empty(gfx: bytes, tid: int) -> bool:
    return (gfx[tid * 32:tid * 32 + 32] == bytes(32)
            and gfx[3072 + tid * 4:3072 + tid * 4 + 4] == bytes(4))


def compact_order(gfx: bytes):
    """(new_order, remap) for packing non-empty tiles forward. remap[old_id] = new_id.

    Extracted from the endpoint so the battery can exercise the REAL transform instead of a copy of
    it — the lesson from a day of finding two implementations of the same walk that had drifted.
    """
    n = NUM_TILES
    fixed = sorted(FIXED)
    non = [t for t in range(len(fixed), n) if not tile_empty(gfx, t)]
    emp = [t for t in range(len(fixed), n) if tile_empty(gfx, t)]
    new_order = fixed + non + emp
    remap = [0] * n
    for new_pos, old in enumerate(new_order):
        remap[old] = new_pos
    return new_order, remap


def apply_order(gfx: bytes, new_order) -> bytes:
    """Rewrite a gfx blob so position k holds what tile new_order[k] held."""
    out = bytearray(gfx)
    for new_pos, old in enumerate(new_order):
        out[new_pos * 32:new_pos * 32 + 32] = gfx[old * 32:old * 32 + 32]
        out[3072 + new_pos * 4:3072 + new_pos * 4 + 4] = gfx[3072 + old * 4:3072 + old * 4 + 4]
    return bytes(out)


def clear_tiles(gfx: bytes, tids) -> bytes:
    """Blank the given tiles (bitmap + colour), leaving everything else byte-identical."""
    out = bytearray(gfx)
    for t in tids:
        if t in FIXED:
            continue
        out[t * 32:t * 32 + 32] = bytes(32)
        out[3072 + t * 4:3072 + t * 4 + 4] = bytes(4)
    return bytes(out)
