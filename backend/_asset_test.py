"""Asset test: the write paths that touch TILESETS, PORTRAITS and the MAP grid.

`_authoring_test` and `_maintenance_test` cover records. These three classes have their own codecs
and had never been measured — which mattered, because every write path measured this session turned
out to have a bug in it, and the portrait encoder alone had three.

The invariant is different here and has to be, because the operations legitimately change bytes:

    tileset compact      packs tiles forward and REMAPS every area's map so it still shows the same
                         picture. So the test is not "bytes unchanged" but "every cell of every
                         affected area renders the same tile as before".
    tileset clear-unused blanks tiles no map references. Every tile a map DOES reference must come
                         out byte-identical, and the engine-fixed glyphs 0..9 must never be touched.
    set_tile / copy_tile round-trip: what was written decodes back, and a copied tile renders
                         identically to its source.
    portrait build_blob  the picture is what must survive, not the file: still bitmap, every
                         animation frame, and every frame delay.
    map /tiles           the tile grid round-trips and the trailing bytes (towns carry ~120 of them)
                         stay byte-identical.

    python3 editor/backend/_asset_test.py [-v]
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wl_tiles                                                 # noqa: E402
import wl_portrait_anim as anim                                 # noqa: E402
import wl_portrait_encode as enc                                # noqa: E402
from wl_area import load_area, load_map_tileids, save_map_tileids   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
def _pic(gfx, tid):
    """A tile's PICTURE — get_tile also returns its id, and two tiles are never equal on that."""
    d = wl_tiles.get_tile(gfx, tid)
    return (d["pixels"], d["chars"])



def _resolve(kind, side, num):
    for d in (f"work/side{side}", f"baseline/side{side}"):
        p = os.path.join(ROOT, d, f"s{side}_{kind}_id{num:02d}.bin")
        if os.path.isfile(p):
            return p
    return None


def _areas():
    seen = {}
    for pat in ("work/side*/s*_record_id*.bin", "baseline/side*/s*_record_id*.bin"):
        for f in sorted(glob.glob(os.path.join(ROOT, pat))):
            b = os.path.basename(f)[:-4]
            seen.setdefault(b, f)
    for b, f in sorted(seen.items()):
        yield f, int(b[1]), int(b.split("id")[1])


def _area_gfx(raw, side, area_id):
    a = load_area(raw, side, area_id)
    return a.raw[a.slot + 0x30] if a.slot + 0x30 < len(a.raw) else None


def tilesets(fails, verbose):
    """compact / clear-unused / set_tile / copy_tile."""
    # which areas use which tileset, and their tile grids
    users = {}
    for f, side, aid in _areas():
        raw = open(f, "rb").read()
        gid = _area_gfx(raw, side, aid)
        if gid is None:
            continue
        mp = _resolve("map", side, aid)
        if not mp:
            continue
        a = load_area(raw, side, aid)
        users.setdefault(gid, []).append(
            (f"s{side}a{aid}", load_map_tileids(open(mp, "rb").read(), a.width, a.height)))

    n = 0
    seen = {}                                          # work over baseline, as everywhere else
    for pat in ("baseline/side*/s*_gfx_id*.bin", "work/side*/s*_gfx_id*.bin"):
        for q in sorted(glob.glob(os.path.join(ROOT, pat))):
            seen[os.path.basename(q)] = q
    for gp in [seen[k] for k in sorted(seen)]:
        gid = int(os.path.basename(gp).split("id")[1].split(".")[0])
        gfx = open(gp, "rb").read()
        if len(gfx) < 3072 + wl_tiles.NUM_TILES * 4:
            continue
        n += 1
        tag = f"gfx{gid}"

        # ── compact: the PICTURE each map cell shows must not change ──────────────────
        order, remap = wl_tiles.compact_order(gfx)
        packed = wl_tiles.apply_order(gfx, order)
        for aname, grid in users.get(gid, []):
            for i, t in enumerate(grid):
                if t >= wl_tiles.NUM_TILES:
                    continue
                if _pic(packed, remap[t]) != _pic(gfx, t):
                    fails.append(f"{tag} compact: {aname} cell {i} showed tile {t}, after the "
                                 f"remap to {remap[t]} it renders differently")
                    break
        for t in sorted(wl_tiles.FIXED):                       # engine-fixed glyphs stay put
            if remap[t] != t:
                fails.append(f"{tag} compact: engine-fixed tile {t} moved to {remap[t]}")

        # ── clear-unused: referenced tiles must survive byte-identical ────────────────
        used = {t for _, grid in users.get(gid, []) for t in grid if t < wl_tiles.NUM_TILES}
        victims = [t for t in range(wl_tiles.NUM_TILES)
                   if t not in used and t not in wl_tiles.FIXED and not wl_tiles.tile_empty(gfx, t)]
        cleared = wl_tiles.clear_tiles(gfx, victims)
        for t in sorted(used | wl_tiles.FIXED):
            if t >= wl_tiles.NUM_TILES:
                continue
            if _pic(cleared, t) != _pic(gfx, t):
                fails.append(f"{tag} clear-unused: tile {t} is referenced by a map but changed")
                break
        for t in victims:
            if not wl_tiles.tile_empty(cleared, t):
                fails.append(f"{tag} clear-unused: tile {t} was not actually blanked")
                break

        # ── set_tile round-trip, and copy_tile renders the source ─────────────────────
        src = next((t for t in range(wl_tiles.NUM_TILES) if not wl_tiles.tile_empty(gfx, t)), None)
        if src is not None:
            d = wl_tiles.get_tile(gfx, src)
            back = wl_tiles.set_tile(gfx, 11, d["pixels"], d["chars"])
            if _pic(back, 11) != _pic(gfx, src):
                fails.append(f"{tag} set_tile: tile 11 did not decode back to what was written")
            cp = wl_tiles.copy_tile(gfx, 12, gfx, src)
            if _pic(cp, 12) != _pic(gfx, src):
                fails.append(f"{tag} copy_tile: the copy does not render like tile {src}")
        if verbose:
            print(f"{tag:<8} {len(users.get(gid, []))} areas · {len(victims)} unreferenced tiles")
    return n


def portraits(fails, verbose):
    """A rebuilt blob must show the same picture — still, every frame, every delay."""
    n = 0
    for f in sorted(glob.glob(os.path.join(ROOT, "baseline", "side*", "s*_str_id*.bin"))
                    + glob.glob(os.path.join(ROOT, "editor", "work", "side*", "s*_str_id*.bin"))):
        b = open(f, "rb").read()
        try:
            bm = anim.decode_base(b)
            new = enc.build_blob(bm, template_blob=b)
            A, B = anim.anim_frames(b), anim.anim_frames(new)
        except Exception as e:                                  # noqa: BLE001
            fails.append(f"{os.path.basename(f)}: raised {e!r}")
            continue
        n += 1
        nm = os.path.basename(f)
        if anim.decode_base(new) != bm:
            fails.append(f"{nm}: the still changed")
        elif A["frames"] != B["frames"]:
            fails.append(f"{nm}: animation frames differ ({len(A['frames'])} vs {len(B['frames'])})")
        elif A["delays"] != B["delays"]:
            fails.append(f"{nm}: frame delays differ")
        # the anim slot streams and their patch data must survive verbatim
        for i in range(4):
            sp = b[6 + 2 * i] | (b[7 + 2 * i] << 8)
            np_ = new[6 + 2 * i] | (new[7 + 2 * i] << 8)
            if bool(sp) != bool(np_):
                fails.append(f"{nm}: slot {i} enabled={bool(sp)} became {bool(np_)}")
    return n


def maps(fails, verbose):
    """The tile grid round-trips and the trailing bytes stay untouched."""
    n = 0
    for f, side, aid in _areas():
        mp = _resolve("map", side, aid)
        if not mp:
            continue
        a = load_area(open(f, "rb").read(), side, aid)
        mb = open(mp, "rb").read()
        grid = load_map_tileids(mb, a.width, a.height)
        n += 1
        out = save_map_tileids(mb, grid, a.width, a.height)
        if out != mb:
            fails.append(f"s{side}a{aid} map: an unchanged grid did not write back byte-identical")
            continue
        g2 = list(grid)
        g2[0] = (g2[0] + 1) % 96
        out = save_map_tileids(mb, g2, a.width, a.height)
        if load_map_tileids(out, a.width, a.height) != g2:
            fails.append(f"s{side}a{aid} map: the edited grid did not read back")
        tail = a.width * a.height
        if out[tail:] != mb[tail:]:
            fails.append(f"s{side}a{aid} map: {len(mb) - tail} trailing bytes were disturbed")
    return n


def main():
    verbose = "-v" in sys.argv
    fails = []
    t = tilesets(fails, verbose)
    p = portraits(fails, verbose)
    m = maps(fails, verbose)
    print(f"\n{t} tilesets · {p} portraits · {m} map grids")
    if fails:
        print(f"{len(fails)} failures:")
        for x in fails[:16]:
            print(f"  {x}")
        return 1
    print("tilesets remap without changing the picture · portraits rebuild identically · "
          "map grids round-trip with their trailing bytes intact")
    return 0


if __name__ == "__main__":
    sys.exit(main())
