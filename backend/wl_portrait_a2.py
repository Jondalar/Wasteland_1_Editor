"""wl_portrait_a2.py — decode an Apple II Wasteland portrait (str blob) into RGB.

The C64 and the Apple carry the same portraits, under the same str ids. The container is not the
same, and no amount of staring at the bytes produced it: this was read out of the Apple engine's
own draw routine, disassembled off master 1. The addresses below are that engine's.

  $0AE8   the parameter setup: four header bytes -> the loop bounds
  $0BBB   the draw loop
  $0C12   the stream reader (self-modifying, auto-incrementing)
  $2943   row address = ($08AB,x) / ($096B,x) — the 192-entry hi-res line table, split into a
          low-byte and a high-byte half, which is why it never shows up as one contiguous table

HEADER (blob[0..11])
  [0] = C4  first byte column   (1)
  [1] = C5  last byte column, INCLUSIVE — the loop's `cpy $C5` falls through on equal  (12)
  [2] = C2  first screen row    (16)
  [3] = C3  row limit           (99)
  All 88 portraits carry `01 0c 10 63`. The engine derives two more:
      C6 = C2 + 2      the pass limit — exactly two passes
      C7 = C3 - C2     the row-wrap span, 83
  [4..5] LE16, one past the base stream. [6..11] three more of the same shape: the animation
  streams, not decoded here.

BASE STREAM (from blob[12])
  Same RLE as the C64's, no [skip][X][Y][mode] preamble, escape fixed at $80:
      $80  -> read [skip][repeat], emit `repeat` for the next `skip` cells
      else -> emit the byte
  A literal $FF ends the draw early ($0BD1); no portrait uses it.

THE ORDER, AND THE ONE THING THAT MATTERS
  Row is the INNER loop and steps by TWO; column is the outer one; the whole thing runs twice,
  the second pass starting one row lower. So: down a column taking every second row, next
  column, and when the even rows are done, again for the odd ones.

  And then the detail that makes or breaks the picture. On wrapping to the next column the
  engine does `txa / sbc $C7 / tax` with C7 = C3 - C2 = 83 — one LESS than the 84 rows it just
  walked. So each column starts one row below where the previous one did, and the sweep runs
  diagonally. Model it as a clean rectangle and the portrait comes out sheared, with the face
  displaced into the next column; it looks nearly right, which is the dangerous part.

  That drift is also why the cell count is 996 and not 12 x 83: columns alternate between 42 and
  41 cells. The count is not derived here, it is produced by walking the loop — see `cell_order`.

  Verified: 996 cells on all 88 blobs, the stream stopping one byte short of blob[4..5] every
  time. What that last byte is, is not established.

PIXELS: 12 bytes x 7 = 84 px wide, rows 16..98. Bit 0 leftmost, bit 7 the colour pair, two lit
neighbours read white — the tileset's rules, so the palette comes from wl_tiles_a2.

Read-only, like everything else on an Apple data root.
"""
from __future__ import annotations

import io

import wl_tiles_a2 as _a2

STREAM_START = 12
ESCAPE = 0x80
TERMINATOR = 0xFF


def bounds(blob: bytes) -> tuple[int, int, int, int]:
    """(first column, last column inclusive, first row, row limit) — the engine's C4/C5/C2/C3."""
    return blob[0], blob[1], blob[2], blob[3]


def cell_order(blob: bytes) -> list[tuple[int, int]]:
    """(row, column) for each cell, in stream order — the draw loop at $0BBB, walked.

    Written as the 6502 wrote it, drift and all, rather than as the rectangle it nearly is."""
    c4, c5, c2, c3 = bounds(blob)
    c6 = (c2 + 2) & 0xFF                     # $0AF9: adc #$02
    c7 = (c3 - c2) & 0xFF                    # $0B03: sec / sbc $C2   <- 83, not 84
    seq: list[tuple[int, int]] = []
    x, npass = c2, 0
    while True:
        y = c4
        while True:
            while True:
                seq.append((x, y))
                x = (x + 2) & 0xFF           # $0BD7: inx / inx
                if x < c3:                   # $0BD9: cpx $C3 / bcc
                    continue
                break
            x = (x - c7) & 0xFF              # $0BDD: txa / sbc $C7 / tax
            y += 1                           # $0BE1: iny
            if y <= c5:                      # $0BE2: cpy $C5 / bcc / beq
                continue
            break
        npass += 1
        x = (c2 + npass) & 0xFF              # $0BE9: lda $C2 / adc #pass
        if x < c6:                           # $0BF1: cpx $C6 / bcc
            continue
        break
    return seq


def _run(grid, blob, p, r0, c0, bounds_, stop):
    """One invocation of the draw routine at $0BAF. Returns the stream position after it.

    The engine uses this same entry for the base still and for every animation patch — there is
    no separate EOR variant as on the C64. A patch is just a stream that opens with $FF.
    """
    c4, c5, c2, c3 = bounds_
    c6 = (c2 + 2) & 0xFF
    c7 = (c3 - c2) & 0xFF
    H, W = len(grid), len(grid[0])

    def rd():
        nonlocal p
        if p >= stop:
            raise IndexError
        v = blob[p]
        p += 1
        return v

    def put(x, y, b):
        r, c = x - r0, y - c0
        if 0 <= r < H and 0 <= c < W:
            grid[r][c] = b

    skip = repeat = 0
    npass = 0
    x = c2
    try:
        while True:
            y = c4
            while True:
                while True:
                    while True:                       # one cell, retried after $80/$FF
                        if skip:
                            skip -= 1
                            put(x, y, repeat)
                            break
                        b = rd()
                        if b == ESCAPE:               # $0BCD: cmp #$80
                            skip, repeat = rd(), rd()
                            continue
                        if b == TERMINATOR:           # $0BD1: cmp #$FF -> $0C05
                            x = rd()
                            y = rd()
                            if y == 0xFF:             # $0C0D: cpy #$FF -> rts
                                return p
                            continue                  # $0C0F: bne -> carry on there
                        put(x, y, b)
                        break
                    x = (x + 2) & 0xFF
                    if x < c3:
                        continue
                    break
                x = (x - c7) & 0xFF
                y += 1
                if y <= c5:
                    continue
                break
            npass += 1
            x = (c2 + npass) & 0xFF
            if x < c6:
                continue
            return p
    except IndexError:
        return p


def decode_grid(blob: bytes) -> tuple[list[list[int]], int, int]:
    """(grid, first row, first column) for the base still. grid[row - r0][col - c0] = byte."""
    seq = cell_order(blob)
    rows = [r for r, _ in seq]
    cols = [c for _, c in seq]
    r0, c0 = min(rows), min(cols)
    grid = [[0] * (max(cols) - c0 + 1) for _ in range(max(rows) - r0 + 1)]
    _run(grid, blob, STREAM_START, r0, c0, bounds(blob), blob[4] | (blob[5] << 8))
    return grid, r0, c0


def slot_pointers(blob: bytes) -> list[int]:
    """The four animation streams — blob[4..11], 0 meaning the slot is off ($0B6D: lda/ora)."""
    return [blob[4 + 2 * i] | (blob[5 + 2 * i] << 8) for i in range(4)]


def anim_frames(blob: bytes, max_frames: int = 64):
    """Replay the 4-slot animation. Returns {"grids": [...], "delays": [...]} — one entry per
    tick that actually redrew something, starting from the base still.

    The tick at $0B4C: count a slot down, and at zero run its patch through the same draw
    routine, then read a delay byte and make that the new countdown. A delay of 0 re-seeds the
    slot from its header pointer and starts over ($0B87: jsr $0C1E), which is the loop point.
    """
    base, r0, c0 = decode_grid(blob)
    bnd = bounds(blob)
    ptr = slot_pointers(blob)
    cur = list(ptr)
    count = [1 if q else 0 for q in ptr]          # a live slot fires on the first tick
    grid = [row[:] for row in base]
    grids, delays = [], []
    restarted = [not q for q in ptr]
    for _ in range(4096):
        if len(grids) >= max_frames or all(restarted):
            break
        drew = False
        for i in range(4):
            if not ptr[i] or not count[i]:
                continue
            count[i] -= 1
            if count[i]:
                continue
            try:
                p = _run(grid, blob, cur[i], r0, c0, bnd, len(blob))
                d = blob[p]
                p += 1
            except IndexError:
                restarted[i] = True
                continue
            drew = True
            if d == 0:                            # $0B85: delay 0 -> restart this slot
                cur[i] = ptr[i]
                restarted[i] = True
                count[i] = 1
            else:
                cur[i] = p
                count[i] = d
        if drew:
            grids.append([row[:] for row in grid])
            delays.append(min((count[i] for i in range(4) if ptr[i] and count[i]), default=1))
    if not grids:
        grids, delays = [base], [1]
    return {"grids": grids, "delays": delays, "r0": r0, "c0": c0}


def pixels(blob: bytes) -> list[list[tuple[int, int, int]]]:
    """RGB rows. Same hi-res colour rules as the tileset — see wl_tiles_a2.

    The picture starts at byte column c0 (1 on every portrait), so its first pixel sits at
    screen x = c0 * 7 = 7, an ODD column. Artefact colour is a function of that absolute
    parity, so the offset has to go into the hue: leave it out and every colour flips to the
    other half of its pair — the doctor's blue curtains come out orange."""
    grid, _, c0 = decode_grid(blob)
    xoff = c0 * 7
    width = len(grid[0]) * 7
    out = []
    for row in grid:
        lit, pal = [], []
        for b in row:
            for bit in range(7):
                lit.append((b >> bit) & 1)
                pal.append((b >> 7) & 1)
        line = []
        for x in range(width):
            if not lit[x]:
                line.append(_a2.BLACK)
                continue
            left = lit[x - 1] if x else 0
            right = lit[x + 1] if x < width - 1 else 0
            line.append(_a2.WHITE if (left or right) else _a2._hue(x + xoff, pal[x]))
        out.append(line)
    return out


def png(blob: bytes, scale: int = 4) -> bytes:
    """The portrait as a PNG, `scale`x in both directions.

    Square scaling, deliberately: a hi-res pixel is taller than it is wide on real hardware, but
    the editor shows these beside C64 portraits and a stretched one reads as a different picture.
    The shape is the thing being checked here, not the monitor."""
    from PIL import Image
    grid = pixels(blob)
    h, w = len(grid), len(grid[0])
    im = Image.new("RGB", (w, h))
    for y, row in enumerate(grid):
        for x, rgb in enumerate(row):
            im.putpixel((x, y), rgb)
    im = im.resize((w * scale, h * scale), Image.NEAREST)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def rgba(grid) -> bytes:
    """One decoded grid -> RGBA bytes, for the anim route."""
    W = len(grid[0]) * 7
    buf = bytearray()
    for row in grid:
        lit, pal = [], []
        for b in row:
            for bit in range(7):
                lit.append((b >> bit) & 1)
                pal.append((b >> 7) & 1)
        for x in range(W):
            if not lit[x]:
                r, g, bl = _a2.BLACK
            else:
                left = lit[x - 1] if x else 0
                right = lit[x + 1] if x < W - 1 else 0
                r, g, bl = _a2.WHITE if (left or right) else _a2._hue(x + 7, pal[x])
            buf += bytes((r, g, bl, 255))
    return bytes(buf)
