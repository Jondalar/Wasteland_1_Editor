"""Object test: OBJECT cut / paste and BLUEPRINT placement, over every shipped area.

The other batteries state "the bytes did not change" (records) or "the picture did not change"
(assets). Neither invariant fits here, because a paste changes ids in NINE namespaces on purpose:
the record lands in a different array slot, its strings get different indices, its tiles different
numbers, its foe a different row. Every one of those is a rewrite, and a rewrite that quietly drops a
reference produces bytes that look perfectly reasonable.

So the invariant is a FIXPOINT on structure, not on bytes:

    O1  cut → paste → cut  reproduces the object.  canon() renumbers every local id into order of
        appearance, so the two sides are comparable exactly when the STRUCTURE survived. This is one
        assertion but it covers all nine maps at once: drop a text ref and the second cut carries a
        different string; mis-order the tiles and the cells point at the wrong picture; forget to
        translate a CELL_PATCH coordinate and it comes back at the wrong offset.

    O2  the source area is untouched by a cut (a cut is a copy).

    O3  the pasted record still SERIALIZES — it goes through the same fixpoint the save path uses
        (wl_persist.normalize) and stays under the record cap.

    O4  a paste never disturbs what was already there: every (grp, id) the target could dispatch
        BEFORE still reads the same bytes after. Same check the save path makes.

    O5  the owned/shared cut is sound — nothing the object carries is referenced from outside it,
        and everything it names but does not carry is listed in `requires`.

    O6  blueprints: bundling two objects and placing the bundle equals placing them one after the
        other at the same coordinates.

    O8  renormalize() — what settles a blueprint after a hand edit in the mini-editor — agrees with
        the cut it is settling: same requirements, same portraits, same events, and applying it
        twice changes nothing. It has to reach those answers WITHOUT the source area, so this is
        also the check that an object is genuinely standalone.

    O7  the same fixpoint ACROSS areas — which is the whole point of a blueprint. A cross-area paste
        is the case where every namespace genuinely differs: another tileset (so the pictures have
        to be imported and renumbered), another string pool, another set of foe tables, another
        group layout. Same-area pasting can pass while cross-area is broken, because same-area finds
        most of what it needs already present.

    python3 editor/backend/_object_test.py [-v] [-a s1a51]
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wl_area                                                   # noqa: E402
import wl_cell                                                   # noqa: E402
import wl_ir                                                     # noqa: E402
import wl_object as O                                            # noqa: E402
import wl_persist                                                # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
wl_cell.configure(__import__("pathlib").Path(ROOT))


def _resolve(kind, side, num):
    for d in (f"work/side{side}", f"baseline/side{side}"):
        p = os.path.join(ROOT, d, f"s{side}_{kind}_id{num:02d}.bin")
        if os.path.isfile(p):
            return p
    return None


def _asset(kind, num):
    for side in (1, 2, 3, 4):
        p = _resolve(kind, side, num)
        if p:
            return p
    return None


def _areas():
    seen = {}
    for pat in ("work/side*/s*_record_id*.bin", "baseline/side*/s*_record_id*.bin"):
        for f in sorted(glob.glob(os.path.join(ROOT, pat))):
            b = os.path.basename(f)[:-4]
            seen.setdefault(b, f)
    for b, f in sorted(seen.items()):
        yield f"s{b[1]}a{int(b.split('id')[1])}", f, int(b[1]), int(b.split("id")[1])


# Ops worth snapping, most structure first: a door/service carries a whole shop, a keyword a whole
# conversation, a patch the cell rewrites, an encounter a foe and possibly a hireable. A plain BUMP
# message is usually shared by half the map, so cutting one exercises nothing but the shared path.
_INTERESTING = (0xA, 0x6, 0x8, 0x2, 0xC, 0x3, 0x5, 0x4, 0x1, 0xB, 0x7, 0xD, 0xE, 0x9)


def _regions(ir, a, limit=4):
    """The cell selections worth testing, richest op first.

    TWO shapes per structural cell, because they exercise different halves of the cut:
      the CELL ALONE          usually self-contained, so it can be moved to another area (O7)
      the cell plus its 3x3   drags in neighbours, which is where SHARED records show up — a bump
                              message half the map uses, an adjacent door — and therefore where the
                              owned/shared classification and `requires` get tested.
    """
    W, H = a.width, a.height
    by_op = {}
    for i, op in enumerate(a.nibble[:W * H]):
        if 0 < op < 0x10:
            by_op.setdefault(op, []).append((i % W, i // W))
    out, used, n = [], set(), 0
    for op in _INTERESTING:
        for c, r in by_op.get(op, []):
            if (c, r) in used or n >= limit:
                break
            block = [(x, y) for y in range(max(0, r - 1), min(r + 2, H))
                     for x in range(max(0, c - 1), min(c + 2, W))]
            used |= set(block)
            n += 1
            out.append([(c, r)])
            out.append(block)
    return out


def _free_spot(a, w, h):
    """Top-left of a w×h patch of cells that dispatch nothing — where a paste can land harmlessly."""
    W, H = a.width, a.height
    for r in range(H - h + 1):
        for c in range(W - w + 1):
            if all(a.nibble[(r + dy) * W + (c + dx)] == 0
                   for dy in range(h) for dx in range(w)):
                return c, r
    return None


def run(only=None, verbose=False):
    fails, n_obj, n_area, refused = [], 0, 0, []
    ctx, keep = [], []                     # one area context + one rich object each, for the O7 pass
    for tag, path, side, aid in _areas():
        if only and tag != only:
            continue
        raw = open(path, "rb").read()
        try:
            ir = wl_ir.record_to_ir(raw, side, aid)
            a = wl_area.load_area(raw, side, aid)
        except Exception as e:                                   # noqa: BLE001
            fails.append(f"{tag}: decode raised {e!r}")
            continue
        gid = a.raw[a.slot + 0x30] if a.slot + 0x30 < len(a.raw) else None
        gp = _asset("gfx", gid) if gid is not None else None
        gfx = open(gp, "rb").read() if gp else None
        if gfx is not None and len(gfx) < 3072 + 96 * 4:
            gfx = None
        mp = _resolve("map", side, aid)
        mapb = open(mp, "rb").read() if mp else None
        n_area += 1
        ctx.append({"tag": tag, "raw": raw, "side": side, "aid": aid, "a": a, "ir": ir,
                    "gfx": gfx, "map": mapb})

        for cells in _regions(ir, a):
            n_obj += 1
            what = f"{tag} @({cells[0][0]},{cells[0][1]})"
            try:
                obj = O.cut(raw, side, aid, cells, gfx, mapb, name=what, ir=ir)
            except Exception as e:                               # noqa: BLE001
                fails.append(f"{what}: cut raised {e!r}")
                continue

            # ── O2 the cut is a COPY ──────────────────────────────────────────────────
            if open(path, "rb").read() != raw:
                fails.append(f"{what}: the cut modified the source file")

            # ── O5 the cut is sound ───────────────────────────────────────────────────
            graph = O.ref_graph(raw, side, aid, ir)
            idx = {r * a.width + c for c, r in cells}
            carried = {(int(g), int(c)) for g, recs in obj["records"].items() for c in recs}
            carried |= {(int(g), int(c)) for g, recs in obj["opaque"].items() for c in recs}
            carried |= {(17, int(n)) for n in obj["npcs"]}
            for key in sorted(carried):
                for o in graph.get(key, set()):
                    if o in O._STRUCTURAL:
                        continue
                    if o[0] == "cell" and o[1] not in idx:
                        fails.append(f"{what}: carries grp{key[0]}[{key[1]}] although cell "
                                     f"{o[1] % a.width},{o[1] // a.width} outside the object "
                                     "dispatches it")
                        break
                    if o[0] == "rec" and (o[1], o[2]) not in carried:
                        fails.append(f"{what}: carries grp{key[0]}[{key[1]}] although "
                                     f"grp{o[1]}[{o[2]}] outside the object references it")
                        break
            named = set()
            for g, recs in obj["records"].items():
                for c, rr in recs.items():
                    named |= {tuple(v) for k, v in O.refs_of(rr) if k == "rec"}
            missing = {k for k in named if k not in carried}
            listed = set(obj["requires"]["records"])
            for g, c in sorted(missing):
                if f"grp{g}[{c}]" not in listed:
                    fails.append(f"{what}: references grp{g}[{c}] but does not carry it and does "
                                 "not list it in requires")

            # ── O8 renormalize agrees with the cut, and is a fixpoint ─────────────────
            try:
                n1, _ = O.renormalize(obj)
                n2, _ = O.renormalize(n1)
            except Exception as e:                               # noqa: BLE001
                fails.append(f"{what}: renormalize raised {e!r}")
                continue
            if n1 != n2:
                fails.append(f"{what}: renormalize is not a fixpoint")
            for k in ("records", "portraits", "grp16_events"):
                if sorted(set(n1["requires"][k])) != sorted(set(obj["requires"][k])):
                    fails.append(f"{what}: renormalize re-derived requires[{k}] as "
                                 f"{n1['requires'][k]} where the cut said {obj['requires'][k]}")

            # ── paste it somewhere harmless in the SAME area ──────────────────────────
            spot = _free_spot(a, obj["size"]["w"], obj["size"]["h"])
            if spot is None:
                continue
            try:
                res = O.paste(raw, side, aid, obj, spot[0], spot[1], gfx, mapb, ir=ir)
            except (O.TooBig, O.NoRoom) as e:
                # Capacity, not correctness: four of the 43 areas already sit at the 9728-byte
                # window, several tilesets have no empty tile, and eleven string pools have passed
                # the 7-bit id ceiling. Refusing is the CORRECT outcome, so it is counted — but
                # counted OUT LOUD, because a silently truncated record would look identical.
                refused.append(f"{what}: {e}")
                continue
            except Exception as e:                               # noqa: BLE001
                fails.append(f"{what}: paste raised {e!r}")
                continue
            new = res["record"]

            # ── O3 it still serializes to a fixpoint, under the cap ───────────────────
            try:
                blob, _ = wl_persist.normalize(new, side, aid)
            except Exception as e:                               # noqa: BLE001
                fails.append(f"{what}: the pasted record does not reach a layout fixpoint ({e})")
                continue
            if len(blob) > wl_persist.CAP:
                fails.append(f"{what}: the pasted record is {len(blob)} B, over the "
                             f"{wl_persist.CAP} B cap, but paste did not refuse it")
                continue

            # ── O4 nothing that was already there moved ───────────────────────────────
            bad = wl_persist.verify_equivalent(raw, blob, side, aid)
            if bad:
                fails.append(f"{what}: the paste disturbed the target — {bad[0]}"
                             + (f" (+{len(bad) - 1} more)" if len(bad) > 1 else ""))
                continue

            # ── O1 the FIXPOINT: cut it back out and compare structure ────────────────
            back_cells = [(spot[0] + e["dc"], spot[1] + e["dr"]) for e in obj["cells"]]
            try:
                obj2 = O.cut(blob, side, aid, back_cells,
                             res["gfx"] or gfx, res["map"] or mapb, name=what)
            except Exception as e:                               # noqa: BLE001
                fails.append(f"{what}: re-cut raised {e!r}")
                continue
            ca, cb = O.canon(obj), O.canon(obj2)
            if ca != cb:
                fails.append(f"{what}: cut(paste(obj)) != obj — {_diff(ca, cb)}")
                continue
            if (not keep or keep[-1][0] != tag) and not obj["requires"]["records"] \
                    and (obj["records"] or obj["foes"] or obj["npcs"]):
                keep.append((tag, obj))              # first SELF-CONTAINED rich object, for O7
            if verbose:
                print(f"  {what:<28} {len(carried)} rec · {len(obj['text'])} str · "
                      f"{len(obj['tiles'])} tiles · {len(obj['foes'])} foes"
                      + (f" · {len(res['report']['warnings'])} warn"
                         if res["report"]["warnings"] else ""))

        # ── O6 a bundle equals the objects placed one after another ───────────────────
        regs = _regions(ir, a, limit=2)
        if len(regs) == 2 and gfx is not None:
            try:
                o1 = O.cut(raw, side, aid, regs[0], gfx, mapb, name="a", ir=ir)
                o2 = O.cut(raw, side, aid, regs[1], gfx, mapb, name="b", ir=ir)
                bp = O.bundle([o1, o2], name="pair")
                w, h = bp["size"]["w"], bp["size"]["h"]
                spot = _free_spot(a, w, h)
                if spot is not None:
                    one = O.place(raw, side, aid, bp, spot[0], spot[1], gfx, mapb)
                    c0 = min(o["origin"]["col"] for o in (o1, o2))
                    r0 = min(o["origin"]["row"] for o in (o1, o2))
                    step = O.paste(raw, side, aid, o1,
                                   spot[0] + o1["origin"]["col"] - c0,
                                   spot[1] + o1["origin"]["row"] - r0, gfx, mapb)
                    step2 = O.paste(step["record"], side, aid, o2,
                                    spot[0] + o2["origin"]["col"] - c0,
                                    spot[1] + o2["origin"]["row"] - r0,
                                    step["gfx"] or gfx, step["map"] or mapb)
                    if one["record"] != step2["record"]:
                        fails.append(f"{tag}: placing a bundle differs from pasting its objects "
                                     "one after the other")
            except (O.TooBig, O.NoRoom):
                pass                                             # the area is at the cap; see above
            except Exception as e:                               # noqa: BLE001
                fails.append(f"{tag}: blueprint round raised {e!r}")

    # ── O7 the cross-area fixpoint — what a blueprint is actually for ─────────────────
    n_cross, skipped_open = 0, []
    by_tag = {c["tag"]: c for c in ctx}
    for i, (tag, obj) in enumerate(keep):
        if only:
            break
        # ONLY self-contained objects. With an unbound requirement the fixpoint is not even
        # defined: `grp11[3]` resolves to one record in the source area and a different one in the
        # target, and if that other record happens to be referenced by nothing else, the re-cut
        # legitimately OWNS it and carries a record the original did not. The paste already warns;
        # comparing structure across an unbound reference would be comparing two different objects.
        if obj["requires"]["records"]:
            skipped_open.append(f"{tag}: {len(obj['requires']['records'])} unbound reference(s)")
            continue
        dst = ctx[(ctx.index(by_tag[tag]) + 1) % len(ctx)]
        if dst["tag"] == tag or dst["gfx"] is None:
            continue
        spot = _free_spot(dst["a"], obj["size"]["w"], obj["size"]["h"])
        if spot is None:
            continue
        what = f"{tag} -> {dst['tag']}"
        try:
            res = O.paste(dst["raw"], dst["side"], dst["aid"], obj, spot[0], spot[1],
                          dst["gfx"], dst["map"], ir=dst["ir"])
        except (O.TooBig, O.NoRoom) as e:
            refused.append(f"{what}: {e}")
            continue
        except Exception as e:                                   # noqa: BLE001
            fails.append(f"{what}: cross-area paste raised {e!r}")
            continue
        n_cross += 1
        try:
            blob, _ = wl_persist.normalize(res["record"], dst["side"], dst["aid"])
        except Exception as e:                                   # noqa: BLE001
            fails.append(f"{what}: the pasted record does not reach a layout fixpoint ({e})")
            continue
        bad = wl_persist.verify_equivalent(dst["raw"], blob, dst["side"], dst["aid"])
        if bad:
            fails.append(f"{what}: the paste disturbed the target — {bad[0]}")
            continue
        back = [(spot[0] + e["dc"], spot[1] + e["dr"]) for e in obj["cells"]]
        try:
            obj2 = O.cut(blob, dst["side"], dst["aid"], back,
                         res["gfx"] or dst["gfx"], res["map"] or dst["map"])
        except Exception as e:                                   # noqa: BLE001
            fails.append(f"{what}: cross-area re-cut raised {e!r}")
            continue
        ca, cb = O.canon(obj), O.canon(obj2)
        if ca != cb:
            fails.append(f"{what}: cut(paste(obj)) != obj across areas — {_diff(ca, cb)}")
        elif verbose:
            print(f"  {what:<28} {len(res['report']['tiles'])} tiles imported · "
                  f"{len(res['report']['text'])} strings · {len(res['report']['foes'])} foes")

    print(f"\n{n_area} areas · {n_obj} objects cut and pasted · {n_cross} moved to ANOTHER area"
          + (f" ({len(skipped_open)} not self-contained, so no cross-area fixpoint)"
             if skipped_open else "")
          + (f" · {len(refused)} refused, the target area is at the {O.CAP} B cap" if refused else ""))
    if refused and verbose:
        for x in refused:
            print(f"  refused: {x}")
    if fails:
        print(f"{len(fails)} failures:")
        for x in fails[:20]:
            print(f"  {x}")
        if len(fails) > 20:
            print(f"  … +{len(fails) - 20} more")
        return 1
    print("objects survive cut → paste → cut · the target is undisturbed · requires is complete · "
          "a bundle equals its objects placed in order")
    return 0


def _diff(a, b):
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            va, vb = a.get(k), b.get(k)
            if isinstance(va, dict) and isinstance(vb, dict):
                for kk in sorted(set(va) | set(vb)):
                    if va.get(kk) != vb.get(kk):
                        return f"{k}[{kk}]: {str(va.get(kk))[:90]} vs {str(vb.get(kk))[:90]}"
            return f"{k}: {str(va)[:90]} vs {str(vb)[:90]}"
    return "?"


if __name__ == "__main__":
    only = None
    if "-a" in sys.argv:
        only = sys.argv[sys.argv.index("-a") + 1]
    sys.exit(run(only, "-v" in sys.argv))
