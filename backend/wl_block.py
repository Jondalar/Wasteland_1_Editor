"""wl_block.py — BUILDING BLOCKS: the objects the shipped maps are actually built out of.

A block is a PLACE: a house, a hut, a compound wall, a stand of cacti. Tiles, the op layer that
makes it solid or enterable, and the holes that need filling — the door's destination, the sign's
text. Not a quest shape (that is `wl_blueprint`), and not a saved rectangle (that is the map
blueprint): a block is one object, cut along its own edges.

## How they are found, and the two miners that failed first

1. **Exact tile stamps around doors.** 363 distinct 5x3 windows over 387 op-A cells; five repeat
   four or more times, and all five inside a single area. The 1988 artists never copy-pasted.
2. **Most frequent free windows.** Ranks ground texture and wall edges — the things that carpet a
   map — and buries the one house at the bottom. Frequency is the wrong question: a wall run repeats
   hundreds of times and is not an object, a house repeats six times and is one.
3. **Connected components over non-ground tiles.** This works, and it is what runs below. Whatever
   carpets the area is background; anything drawn ON it that fits in a small box is an object. It
   finds the purple Quartz houses with their door notch, the cactus stands, the compound corners.

## The block is bound to its tileset — measured, not assumed

Quartz and Vegas look like the same town set, and their tilesets share 55 of 96 tiles at identical
indices. But of Quartz's 15 building-sized objects, **15 render in gfx 3, 2 in gfx 6, and 0 in gfx
1, 2 or 8** — as a bounding box and as a mask, the same answer. The shared 55 are walls, cacti and
ground furniture; every town redrew its houses. So the library is per gfx id, and placing a block
into a foreign tileset is not a thing that can be fudged — it needs a tile-role mapping, which is a
separate piece of work.

## The hull comes free

Every mined building carries its ops, and the op signature says what the thing IS without anyone
labelling it:

    op $0A present  ->  a HOUSE — the door needs somewhere to go
    op $06 present  ->  a SERVICE building — a shop, a doctor
    only $0B / $02  ->  a WALL or compound — solid, nothing to enter
    only $00        ->  DECORATION — cacti, rubble, a tree

## SELF-CONTAINED: a block carries its own art

A tile id indexes a tileset and means nothing outside it — that is why the first version of this
was locked to one gfx id. So a block does not store tile ids at all: it stores the 36 bytes each
tile IS (32 bitmap + 4 colour) and its cells point at that list. The block is then complete on its
own, and placing it anywhere becomes a question with an answer instead of a refusal:

    for every distinct tile the block carries
        the destination already has that exact art  ->  reuse that slot
        it does not, and a slot is free             ->  write the art there
        it does not, and nothing is free            ->  say how many slots short

Which dissolves the tileset question entirely. Quartz's houses could not be drawn in Vegas because
Vegas lacks those tiles — now they arrive with them.

## Placing resolves roles, it does not copy ids

A cell-id is an index into the SOURCE area's record arrays and means nothing in the destination. So
a block stores a ROLE per cell and placement resolves it there: a wall becomes the destination's own
op-$B wall id (its most common one — the same rule the physique painter uses), a plain cell becomes
op 0, and a door is reported as a HOLE rather than written half-wired. Geometry lands complete; what
needs a record says so.
"""
import collections

MAXW = MAXH = 6                 # a block is an object, not a district
MIN_CELLS = 2
GROUND_SHARE = 0.03             # a tile that carpets 3% of the map is background, not an object


def _ground(tiles, w, h):
    freq = collections.Counter(tiles)
    return {t for t, c in freq.items() if c >= w * h * GROUND_SHARE}


def _kind(ops: set) -> str:
    if 0x6 in ops:
        return "service"
    if 0xA in ops:
        return "house"
    if ops & {0xB, 0x2, 0x3, 0xF}:
        return "wall"
    if ops <= {0x0}:
        return "decor"
    return "other"


def _role(op: int) -> str:
    return ("door" if op == 0xA else "service" if op == 0x6 else
            "wall" if op in (0xB, 0x3, 0xF) else "script" if op else "plain")


def tile_art(gfx: bytes, tid: int) -> str:
    """The 36 bytes a tile IS — 32 bitmap + 4 colour — as hex. The whole self-containment."""
    b = bytes(gfx[tid * 32:tid * 32 + 32]) + bytes(gfx[3072 + tid * 4:3072 + tid * 4 + 4])
    return b.hex()


def put_art(gfx: bytes, tid: int, art: str) -> bytes:
    """Inverse of tile_art — drop a carried tile into a slot."""
    b = bytes.fromhex(art)
    out = bytearray(gfx)
    out[tid * 32:tid * 32 + 32] = b[:32]
    out[3072 + tid * 4:3072 + tid * 4 + 4] = b[32:36]
    return bytes(out)


def resolve(block: dict, dst_gfx: bytes, free_slots=None) -> dict:
    """Where each of the block's tiles lands in a destination tileset.

    Reuse before write: an identical tile already there is the same picture, and spending a slot on
    a duplicate is how a 96-slot tileset runs out for no reason.

    `free_slots` must be the slots NOTHING POINTS AT, not the slots that happen to be blank. Areas
    share tilesets — gfx 2 carpets eleven of them — and a deliberately blank tile can still be
    referenced by a map, where it draws as black. Writing into it would put a wall into every area
    that pointed there. The caller computes it from the union of those areas' tile grids; falling
    back to "blank bytes" here would be the unsafe reading, so the fallback is NO free slots."""
    have = {tile_art(dst_gfx, t): t for t in range(96)}
    free = sorted(free_slots) if free_slots is not None else []
    out, writes, short = [], [], 0
    for art in block.get("art", []):
        if art in have:
            out.append(have[art])
        elif free:
            t = free.pop(0)
            out.append(t)
            writes.append({"tile": t, "art": art})
            have[art] = t
        else:
            out.append(None)
            short += 1
    return {"map": out, "writes": writes, "short": short}


def _empty(gfx: bytes, tid: int) -> bool:
    return (bytes(gfx[tid * 32:tid * 32 + 32]) == bytes(32)
            and bytes(gfx[3072 + tid * 4:3072 + tid * 4 + 4]) == bytes(4))


def render(block: dict, scale: int = 1) -> dict:
    """Draw a block from ITS OWN art. {w, h, rgba} — no tileset involved.

    This is what self-contained has to mean visually too. Drawing a block through the destination's
    atlas (which is what the palette did at first) shows the tiles that happen to sit at the source
    indices THERE — a Quartz house rendered as Highpool rubble. The block carries the 36 bytes of
    every tile it uses; the picture comes from those.
    """
    import wl_tiles
    T = wl_tiles.TILE_PX
    W, H = block["w"] * T, block["h"] * T
    px = [[(0, 0, 0, 0)] * W for _ in range(H)]
    art = block.get("art") or []
    for c in block["cells"]:
        ai = c.get("art")
        if ai is None or ai >= len(art):
            continue
        one = put_art(bytes(3076), 0, art[ai])          # a one-tile blob, so decode_tile can read it
        tile = wl_tiles.decode_tile(one, 0)
        for ty in range(T):
            for tx in range(T):
                r, g, b = tile[ty][tx]
                px[c["dy"] * T + ty][c["dx"] * T + tx] = (r, g, b, 255)
    if scale > 1:
        px = [[row[x // scale] for x in range(W * scale)] for row in px for _ in range(scale)]
        W, H = W * scale, H * scale
    raw = bytearray()
    for row in px:
        for r, g, b, a in row:
            raw += bytes((r, g, b, a))
    return {"w": W, "h": H, "rgba": bytes(raw)}


def sheet(blocks: list) -> dict:
    """One RGBA strip holding every block's own picture, plus where each one sits in it.

    A sheet rather than an image per block: a catalogue of 661 would otherwise be 661 requests, and
    the point of looking at them all at once is to look at them all at once."""
    import wl_tiles
    T = wl_tiles.TILE_PX
    rows, x, y, rowh, maxw = [], 0, 0, 0, 1024
    place_at = []
    for b in blocks:
        w, h = b["w"] * T, b["h"] * T
        if x and x + w > maxw:
            x, y, rowh = 0, y + rowh, 0
        place_at.append({"id": b["id"], "x": x, "y": y, "w": w, "h": h})
        x += w
        rowh = max(rowh, h)
    W, H = maxw, y + rowh
    buf = bytearray(W * H * 4)
    for b, at in zip(blocks, place_at):
        im = render(b)
        for ry in range(im["h"]):
            src = ry * im["w"] * 4
            dst = ((at["y"] + ry) * W + at["x"]) * 4
            buf[dst:dst + im["w"] * 4] = im["rgba"][src:src + im["w"] * 4]
    return {"w": W, "h": H, "rgba": bytes(buf), "at": place_at}


# ── the LIBRARY: mined blocks are derived, edited blocks are kept ─────────────────────────────
#
# The catalogue is re-mined from the areas on every load, so it cannot be edited — an edit would be
# gone on the next pass. Editing therefore FORKS: a copy lands in editor/blocks/ and lives there.
# Which is the same rule blueprints already follow, for the same reason: the thing you shape belongs
# to what you are shaping it for, not to the place it was cut from.
import json
import pathlib


def _libdir(repo) -> pathlib.Path:
    d = pathlib.Path(repo) / "editor" / "blocks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def lib_list(repo) -> list:
    out = []
    for f in sorted(_libdir(repo).glob("*.json")):
        try:
            out.append(json.loads(f.read_text()))
        except Exception:                                  # noqa: BLE001
            continue
    return out


def lib_get(repo, bid: str):
    f = _libdir(repo) / f"{bid}.json"
    return json.loads(f.read_text()) if f.is_file() else None


def lib_save(repo, block: dict) -> dict:
    (_libdir(repo) / f"{block['id']}.json").write_text(json.dumps(block, indent=1))
    return block


def lib_delete(repo, bid: str) -> bool:
    f = _libdir(repo) / f"{bid}.json"
    if not f.is_file():
        return False
    f.unlink()
    return True


def lib_fork(repo, block: dict, name: str | None = None) -> dict:
    """Copy a mined block into the library under a free id. Self-contained already, so this is a
    plain copy — nothing to resolve, nothing left pointing at the area it came from."""
    base = (name or block["id"]).strip().replace("/", "-") or "block"
    d = _libdir(repo)
    bid, n = base, 1
    while (d / f"{bid}.json").is_file():
        n += 1
        bid = f"{base}-{n}"
    out = {**block, "id": bid, "origin": block.get("id"), "library": True}
    out.pop("map", None)
    out.pop("cost", None)
    out.pop("fits", None)
    return lib_save(repo, out)


def art_tile(art: str) -> dict:
    """One carried tile as the pixel editor's model — the SAME model the tileset editor uses, so
    there is one painter in this project and not two."""
    import wl_tiles
    # bg/cram travel with it: they are the FIXED window scheme this game draws with (black
    # background, white colour-RAM), and without them the preview had to guess.
    return {**wl_tiles.get_tile(put_art(bytes(3076), 0, art), 0), "bg": 0, "cram": 1}


def art_from_tile(pixels, chars) -> str:
    import wl_tiles
    return tile_art(wl_tiles.set_tile(bytes(3076), 0, pixels, chars), 0)


def mine(area, tiles, gfx: bytes = b"", min_cells: int = MIN_CELLS) -> list:
    """Every bounded object of one area, cut along its own edges (not to a rectangle)."""
    W, H = area.width, area.height
    if not tiles or len(tiles) < W * H:
        return []
    ground = _ground(tiles[:W * H], W, H)
    seen = [[False] * W for _ in range(H)]
    out = []
    for y in range(H):
        for x in range(W):
            if seen[y][x] or tiles[y * W + x] in ground:
                continue
            stack, comp = [(x, y)], []
            seen[y][x] = True
            while stack:
                cx, cy = stack.pop()
                comp.append((cx, cy))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < W and 0 <= ny < H and not seen[ny][nx] and tiles[ny * W + nx] not in ground:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            xs = [c[0] for c in comp]
            ys = [c[1] for c in comp]
            x0, y0 = min(xs), min(ys)
            bw, bh = max(xs) - x0 + 1, max(ys) - y0 + 1
            if not (min_cells <= len(comp) and bw <= MAXW and bh <= MAXH):
                continue
            cells = []
            ops = set()
            art, art_ix = [], {}
            for cx, cy in sorted(comp, key=lambda c: (c[1], c[0])):
                op = area.nibble[cy * W + cx]
                ops.add(op)
                t = tiles[cy * W + cx]
                if gfx:                                   # carry the picture, not the index
                    a = tile_art(gfx, t)
                    if a not in art_ix:
                        art_ix[a] = len(art)
                        art.append(a)
                    ai = art_ix[a]
                else:
                    ai = None
                cells.append({"dx": cx - x0, "dy": cy - y0, "tile": t, "art": ai,
                              "op": op, "role": _role(op)})
            out.append({"w": bw, "h": bh, "cells": cells, "at": [x0, y0], "art": art,
                        "kind": _kind(ops), "ops": sorted(ops)})
    return out


def _key(b) -> tuple:
    """Two objects are the same block when they draw the same picture with the same ops."""
    art = b.get("art") or []
    return (b["w"], b["h"],
            tuple((c["dx"], c["dy"], art[c["art"]] if c.get("art") is not None else c["tile"], c["op"])
                  for c in b["cells"]))


def catalogue(repo) -> dict:
    """{gfx id: [block, …]} over every area, deduped, with where each one came from.

    Per gfx id and not one flat list, because a block cannot be drawn outside its own tileset —
    see the module docstring for the measurement that settles it."""
    import app                                            # the loader lives there; do not duplicate it
    lib = collections.defaultdict(dict)
    for a in app.list_areas()["areas"]:
        side, aid = a["side"], a["area_id"]
        try:
            area, tiles, _, _ = app._load_area_full(side, aid)
        except Exception:                                 # noqa: BLE001 — one bad area must not kill the mine
            continue
        gfx = area.raw[area.slot + 0x30] if area.slot + 0x30 < len(area.raw) else 0
        gp, _, _ = app._resolve_asset("gfx", gfx)
        art_src = gp.read_bytes() if gp else b""
        for b in mine(area, tiles, art_src):
            k = _key(b)
            e = lib[gfx].setdefault(k, {**b, "n": 0, "seen_in": [], "gfx": gfx})
            e["n"] += 1
            where = {"side": side, "area": aid, "name": a["name"], "at": b["at"]}
            if len(e["seen_in"]) < 8:
                e["seen_in"].append(where)
    out = {}
    for gfx, blocks in lib.items():
        rows = sorted(blocks.values(), key=lambda b: (-len(b["cells"]), -b["n"]))
        for i, b in enumerate(rows):
            b.pop("at", None)
            b["id"] = f"g{gfx}-{b['kind']}-{i:03d}"
        out[gfx] = rows
    return out


def wall_id(area) -> int:
    """The area's generic wall dispatch = its most common op-$B cell id.

    Same rule the physique painter uses, and the reason placing is a role resolution: the source
    area's wall id indexes the SOURCE's records and would dispatch something else here."""
    c = collections.Counter()
    for i, op in enumerate(area.nibble[:area.width * area.height]):
        if op == 0xB:
            c[area.cellid[i]] += 1
    return c.most_common(1)[0][0] if c else 0


def place(area, tiles: list, block: dict, x: int, y: int, tilemap: list | None = None) -> dict:
    """Write a block at (x,y). Returns the new tile grid, the op/id writes, and the HOLES.

    Roles resolve against THIS area. A door is deliberately left as op 0 with its coordinate
    reported: a half-wired op-$A dispatches whatever record happens to sit at that index, which is
    the one failure mode that looks like the editor working."""
    W, H = area.width, area.height
    tiles = list(tiles)
    wid = wall_id(area)
    ops, holes = [], []
    for c in block["cells"]:
        cx, cy = x + c["dx"], y + c["dy"]
        if not (0 <= cx < W and 0 <= cy < H):
            return {"error": f"the block does not fit at ({x},{y}) — it needs {block['w']}x{block['h']}"}
        t = c["tile"]
        if tilemap is not None and c.get("art") is not None:
            t = tilemap[c["art"]]
            if t is None:
                return {"error": "the destination tileset has no room for this block's tiles"}
        tiles[cy * W + cx] = t
        if c["role"] == "wall":
            ops.append({"x": cx, "y": cy, "op": 0xB, "id": wid})
        elif c["role"] in ("door", "service", "script"):
            ops.append({"x": cx, "y": cy, "op": 0x0, "id": 0})
            holes.append({"x": cx, "y": cy, "role": c["role"],
                          "needs": "a door target" if c["role"] == "door"
                                   else "a service record" if c["role"] == "service" else "a script"})
        else:
            ops.append({"x": cx, "y": cy, "op": 0x0, "id": 0})
    return {"tiles": tiles, "ops": ops, "holes": holes}
