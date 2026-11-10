"""Parity test: the IR-native views (wl_view) must return exactly what the byte views (wl_cell) do.

This is what makes switching the editor over to the JSON-IR safe. Both implementations decode the
same record format — one from bytes, one from the typed IR — so any disagreement is a decoder bug in
whichever is wrong, and the test says which record it is.

    python3 editor/backend/_view_parity_test.py [-v]

Runs over all 43 baseline records plus every edited work record. Exit code 1 on any difference.
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wl_cell                                                  # noqa: E402
import wl_ir                                                    # noqa: E402
import wl_view                                                  # noqa: E402
from wl_area import load_area                                   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def records():
    seen = set()
    for pat in ("baseline/side*/s*_record_id*.bin", "work/side*/s*_record_id*.bin"):
        for f in sorted(glob.glob(os.path.join(ROOT, pat))):
            b = os.path.basename(f)
            if b.startswith("._") or ".bak" in b:
                continue
            key = (b, "work" if "/work/" in f else "base")
            if key in seen:
                continue
            seen.add(key)
            yield f, int(b[1]), int(b.split("id")[1].split(".")[0])


def _cond(area, ir):
    """cond_summary for an IR COND record — matches wl_cell's byte-offset call by finding the id."""
    idx = {id(v): int(k) for k, v in ir["groups"].get("2", {}).items()}

    def f(rr):
        cid = idx.get(id(rr))
        rec = wl_cell.subptr(area.raw, area.slot, 2, cid) if cid is not None else None
        return wl_cell._cond_summary(area, rec + 0x0A) if rec is not None else "always"
    return f


def first_diff(a, b, path=""):
    if type(a) is not type(b):
        return f"{path}: {type(a).__name__} vs {type(b).__name__}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                return f"{path}.{k}: only in byte-view"
            if k not in b:
                return f"{path}.{k}: only in IR-view"
            d = first_diff(a[k], b[k], f"{path}.{k}")
            if d:
                return d
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: len {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_diff(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    return None if a == b else f"{path}: {a!r} vs {b!r}"


def main():
    verbose = "-v" in sys.argv
    tot = {"chains": 0, "transitions": 0, "mutations": 0, "targets": 0}
    fails = []
    n = 0
    for path, side, area in records():
        raw = open(path, "rb").read()
        name = f"{'work' if '/work/' in path else 'base'}:{os.path.basename(path)[:-4]}"
        try:
            a = load_area(raw, side, area)
            ir = wl_ir.record_to_ir(raw, side, area)
        except Exception as e:                                  # noqa: BLE001
            fails.append((name, "load", repr(e)))
            continue
        n += 1
        # presentation helpers stay in wl_cell; only the STRUCTURE is what differs between the two
        st = lambda op, cid: wl_cell._state_text(a, op, cid)    # noqa: E731
        lab = lambda op, cid: wl_cell._target_label(a, op, cid)  # noqa: E731
        tp = lambda c, r, op, cid: wl_cell._tile_pair(a, None, c, r, op, cid)   # noqa: E731
        pr = lambda idx: wl_cell._prose(a, idx)                 # noqa: E731

        checks = [
            ("chains", lambda: wl_cell.area_chains(a), lambda: wl_view.area_chains(ir, st)),
            ("transitions", lambda: wl_cell.area_transitions(a),
             lambda: wl_view.area_transitions(ir, pr)),
            ("mutations", lambda: wl_cell.area_mutations(a),
             lambda: wl_view.area_mutations(ir, lab, tp, _cond(a, ir), pr)),
        ]
        for key, fb, fi in checks:
            try:
                ob, oi = fb(), fi()
            except Exception as e:                              # noqa: BLE001
                fails.append((name, key, f"raise {e!r}"))
                continue
            d = first_diff(json.loads(json.dumps(ob)), json.loads(json.dumps(oi)), key)
            if d:
                fails.append((name, key, d))
            else:
                tot[key] += 1
        # patch_targets for every producing cell
        bad = 0
        for i, op in enumerate(a.nibble[:a.width * a.height]):
            if op not in wl_view._PRODUCERS:
                continue
            col, row = i % a.width, i // a.width
            ob = wl_cell.patch_targets(a, col, row, op, a.cellid[i])
            oi = wl_view.patch_targets(ir, col, row, op, a.cellid[i])
            if json.dumps(ob, sort_keys=True) != json.dumps(oi, sort_keys=True):
                bad += 1
                if len(fails) < 40:
                    fails.append((name, f"patch_targets({col},{row},op${op:X})",
                                  first_diff(ob, oi, "t") or "differs"))
        if not bad:
            tot["targets"] += 1
        if verbose:
            print(f"{name:<28} {'OK' if not any(f[0] == name for f in fails) else 'DIFF'}")

    print(f"\n{n} records · byte-view vs IR-view")
    for k, v in tot.items():
        print(f"  {k:<12} {v}/{n} identical")
    if fails:
        print(f"\n{len(fails)} differences:")
        for nm, k, d in fails[:25]:
            print(f"  {nm} {k}: {d}")
        return 1
    print("\nall views identical — the IR is a faithful replacement for the byte decode")
    return 0


if __name__ == "__main__":
    sys.exit(main())
