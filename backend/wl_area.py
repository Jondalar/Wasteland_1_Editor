"""wl_area.py — load a Wasteland area RECORD blob into a structured form the editor can render.

Byte layout is the reversed, byte-exact model (see cart_EF/docs/engine/AREA_MODEL.md and
cart_EF/tools/wl_mine_overworld_coords.py — the addressing here mirrors that proven tool):

A record blob loads at RAM $3400 (file offset 0). It holds TWO flat map layers, both indexed by
the party cell (col, row):

  TYPE-NIBBLE layer  (op-type per cell, 4-bit, 2 cells/byte)
      base   = 0
      stride = W//2 bytes/row
      addr   = row*(W//2) + (col>>1)
      nibble = HIGH nibble if col EVEN, LOW nibble if col ODD

  CELL-ID layer      (1 byte/cell = per-op dispatch index)
      base   = W*(W//2)
      stride = W bytes/row
      addr   = base + row*W + col

The descriptor sits at `slot` = file offset of the header: $1800 for areas 0/12/22/26
(L1[id]==$40), else $0600. width = descriptor[slot + $2C]; the overworld is square (H=W), towns
are H=W too for the extracted blobs (grid fills exactly [0, slot)).

This module is READ/serialise only for now; the editor's save path will round-trip these bytes.
"""
from __future__ import annotations
from dataclasses import dataclass

# Areas whose descriptor lives at the $1800 slot (L1[id] == $40). Everything else = $0600.
SLOT_1800_AREAS = {0, 12, 22, 26}
DESC_WIDTH_OFF = 0x2C   # descriptor[+$2C] = map width


def slot_for_area(area_id: int) -> int:
    return 0x1800 if area_id in SLOT_1800_AREAS else 0x0600


@dataclass
class Area:
    side: int
    area_id: int
    width: int
    height: int
    slot: int
    nibble: list[int]   # width*height, op-type per cell (0..15)
    cellid: list[int]   # width*height, dispatch index per cell (0..255)
    raw: bytes          # the full record blob (for byte-exact save later)

    def to_json(self) -> dict:
        return {
            "side": self.side,
            "area_id": self.area_id,
            "width": self.width,
            "height": self.height,
            "slot": self.slot,
            "nibble": self.nibble,
            "cellid": self.cellid,
        }


def load_area(raw: bytes, side: int, area_id: int) -> Area:
    slot = slot_for_area(area_id)
    if slot + DESC_WIDTH_OFF >= len(raw):
        raise ValueError(f"record too short ({len(raw)} B) for slot ${slot:04X}")
    width = raw[slot + DESC_WIDTH_OFF]
    if width == 0:
        raise ValueError("width byte is 0")
    nib_stride = width // 2
    cid_base = width * nib_stride
    # grid must fit inside [0, slot)
    height = slot // (nib_stride + width)   # (W//2 + W) bytes per row across both layers
    if height <= 0:
        raise ValueError(f"computed height {height} <= 0 (width {width}, slot ${slot:04X})")

    nibble: list[int] = []
    cellid: list[int] = []
    for row in range(height):
        for col in range(width):
            noff = row * nib_stride + (col >> 1)
            b = raw[noff] if noff < len(raw) else 0
            nibble.append((b >> 4) if (col & 1) == 0 else (b & 0x0F))
            coff = cid_base + row * width + col
            cellid.append(raw[coff] if coff < len(raw) else 0)
    return Area(side, area_id, width, height, slot, nibble, cellid, raw)


def load_map_tileids(map_bytes: bytes, width: int, height: int) -> list[int]:
    """The visible per-cell TILE id lives in the map blob (s1_map_id*.bin → RAM $DE00), NOT in the
    record. The grid is the first width*height bytes (all ≤95 = valid tile ids); any trailing bytes
    (towns carry ~120 extra) are separate data, left untouched on save.
    """
    n = width * height
    return list(map_bytes[:n]) + [0] * max(0, n - len(map_bytes))


def save_map_tileids(map_bytes: bytes, tileids: list[int], width: int, height: int) -> bytes:
    """Patch the tile-id grid back into the map blob; trailing bytes stay byte-identical."""
    n = width * height
    if len(tileids) != n:
        raise ValueError(f"tileids length {len(tileids)} != {n}")
    out = bytearray(map_bytes)
    for i in range(min(n, len(out))):
        out[i] = tileids[i] & 0xFF
    return bytes(out)


MAP_IR_VERSION = 1


def map_to_ir(map_bytes: bytes, width: int, height: int) -> dict:
    """The map blob as a document: the drawn grid by name, everything after it verbatim.

    WHAT THE TAIL IS, as far as the code says. `load_map` (ef_engine.asm:6292) sets $CC/$CD to
    $00/$EE — an END BOUND, not a length: the decompressor fills $DE00 and may run to $EE00
    (4096 B). Where it actually stops is the compressed stream's own terminator. So area 0
    (64x64 = 4096) has NO tail because it hit the bound, while a 32x32 town stops wherever its
    stream ends: 1063, 1144, 1683 B against 1024 drawn cells.

    Nothing reads those bytes. The only reader of $DE00 is the draw pass — `map_row_base_ptr
    $0B06` builds $DE00 + row*width, `draw_map_cell $0B53` plots from it — and both are bounded
    by width and height. What the tail MEANT in the original stream is not answered by the loader;
    it is carried verbatim rather than guessed at, which is why it is one opaque field.
    """
    n = width * height
    return {
        "meta": {"width": width, "height": height, "map_version": MAP_IR_VERSION},
        "tiles": list(map_bytes[:n]),
        "tail": "hex:" + map_bytes[n:].hex(),
    }


def ir_to_map(ir: dict) -> bytes:
    """The document back to bytes. Byte-identical to what `map_to_ir` was given, unless the tail
    was deliberately emptied — which is a experiment, not a normal edit (see _mapgate.py)."""
    m = ir["meta"]
    n = m["width"] * m["height"]
    tiles = ir["tiles"]
    if len(tiles) != n:
        raise ValueError(f"{len(tiles)} tiles for a {m['width']}x{m['height']} map (want {n})")
    if any(not isinstance(t, int) or not 0 <= t <= 255 for t in tiles):
        raise ValueError("a tile id is not a byte")
    tail = ir.get("tail") or "hex:"
    if not tail.startswith("hex:"):
        raise ValueError("tail must be a hex: string")
    return bytes(tiles) + bytes.fromhex(tail[4:])


def save_area(area: "Area", nibble: list[int], cellid: list[int]) -> bytes:
    """Patch the two grid layers back into the record blob. Everything OUTSIDE the two grid
    layers (descriptor, subtables, prose, scripts) stays byte-identical — only the cells we
    edited change. Returns the new blob bytes.
    """
    w, h, slot = area.width, area.height, area.slot
    if len(nibble) != w * h or len(cellid) != w * h:
        raise ValueError(f"layer length mismatch (expected {w*h})")
    nib_stride = w // 2
    cid_base = w * nib_stride
    out = bytearray(area.raw)
    for row in range(h):
        for col in range(w):
            i = row * w + col
            # nibble layer: pack 2 cells/byte (col even = HIGH nibble)
            noff = row * nib_stride + (col >> 1)
            nv = nibble[i] & 0x0F
            if (col & 1) == 0:
                out[noff] = (nv << 4) | (out[noff] & 0x0F)
            else:
                out[noff] = (out[noff] & 0xF0) | nv
            # cell-id layer: 1 byte/cell
            out[cid_base + row * w + col] = cellid[i] & 0xFF
    if len(out) != len(area.raw):
        raise ValueError("length drift")
    return bytes(out)
