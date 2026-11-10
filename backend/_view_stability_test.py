"""Stability test: an editor view must not change when the record is RE-PACKED.

A re-pack keeps every record's own bytes identical and moves it. So any view whose output changes
across a re-pack is not reading the record — it is reading whatever happens to sit AFTER the record,
and the answer it gives depends on a layout the serializer is free to change. That is a decoder bug
every time, and it is invisible to `_view_parity_test` (which compares two decoders on the SAME
bytes) and to the repack byte-compare (which compares the record, not what a walker ran into).

Three of these have been found this way already: `_door_targets` walking an op-A tail into the next
record, `_loot_items` losing phase on a 3-byte money entry and reading the neighbour's items, and
the op-A subtable scan inventing doors past the array's end.

    python3 editor/backend/_view_stability_test.py [-v]

Exit 1 if any view differs. `wl_ir.ir_to_record` supplies the re-pack.
"""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wl_cell                                                  # noqa: E402
import wl_foe                                                   # noqa: E402
import wl_view                                                  # noqa: E402
import wl_ir                                                    # noqa: E402
from wl_area import load_area                                   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pathlib                                                  # noqa: E402
wl_cell.configure(pathlib.Path(ROOT))     # the service view resolves scene blobs through this
_PRODUCERS = (0x1, 0x2, 0x3, 0x4, 0x5, 0x6, 0x8, 0x9, 0xA, 0xB, 0xC)
_RAISED = []      # every exception any view threw — a view that throws is broken, not "stable"


# A re-pack MOVES the record, so a view that reports where a field lives must report a different
# number — that is the view working, not failing. Those keys are dropped before comparing; every
# other key describes the record's CONTENT and must survive the move untouched.
_MOVES = ("off", "offset", "addr", "rec", "base", "ptr", "file_off",
          "table",            # area_grp16's "table" is the blob's RAM address, not content
          "hex")              # the "Record bytes" panel shows the record's EXTENT, and for the 15
                              # records whose list walks past the next structure the serializer
                              # emits that full walked length, so the extent genuinely grows. The
                              # panel is right on both sides; only its length differs.


# A panel title carries the record's RAM address ("BUMP MESSAGE (grp11 @ $40F5)"). That number is
# supposed to change when the record moves, and it lives inside a STRING, where dropping a key
# cannot reach it.
_ADDR = re.compile(r"\$[0-9A-F]{4}\b")


def _strip(x):
    if isinstance(x, str):
        return _ADDR.sub("$ADDR", x)
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items() if k not in _MOVES}
    if isinstance(x, list):
        return [_strip(v) for v in x]
    return x


def _j(x):
    return json.dumps(_strip(x), sort_keys=True, default=repr)


def views(raw, side, area_id) -> dict:
    """Every read-only view the editor renders for one area, as comparable JSON."""
    a = load_area(raw, side, area_id)
    out = {}
    for name, fn in (("chains", lambda: wl_cell.area_chains(a)),
                     ("mutations", lambda: wl_cell.area_mutations(a)),
                     ("transitions", lambda: wl_cell.area_transitions(a)),
                     ("flow", lambda: wl_cell.area_flow(a)),
                     ("grp16", lambda: wl_cell.area_grp16(a)),
                     ("text_usage", lambda: wl_cell.text_usage(a)),
                     ("hireables", lambda: wl_cell.area_hireables(a, side)),
                     ("npcs", lambda: wl_cell.area_npcs(a, side)),
                     # the Data tab's other two portrait sources. Added the moment they were built
                     # this time: the 500-error came from shipping a view with no battery behind it.
                     ("services", lambda: wl_foe.services(a, side)),
                     ("orphan_npcs", lambda: wl_foe.orphan_hires(a, side)),
                     ("foe_objects", lambda: wl_foe.foes(a, side)),
                     ("foe_invariant", lambda: wl_foe.check(a)),
                     ("handovers", lambda: wl_view.record_chains(
                         wl_ir.record_to_ir(raw, side, area_id), a))):
        try:
            out[name] = _j(fn())
        except Exception as e:                                  # noqa: BLE001
            # A raise is a FAILURE, not a value. Recording it as a comparable string let a view that
            # threw on BOTH sides compare equal and pass — which is exactly how a NameError in
            # inspect_op's hex panel reached the NAS: every cell 500'd and the battery was green.
            out[name] = f"raise {type(e).__name__}: {e}"
            _RAISED.append(f"{name}: {type(e).__name__}: {e}")
    per = {"inspect": [], "editable": [], "targets": [], "tile": [], "links": []}
    seen = set()
    for i, op in enumerate(a.nibble[:a.width * a.height]):
        if op not in _PRODUCERS:
            continue
        col, row, cid = i % a.width, i // a.width, a.cellid[i]
        if (op, cid) in seen:
            continue
        seen.add((op, cid))
        disp = a.cellid[i]
        for key, fn in (("inspect", lambda: wl_cell.inspect_op(a, col, row, op, disp)),
                        ("editable", lambda: wl_cell.editable_fields(a, col, row, op, disp)),
                        ("targets", lambda: wl_cell.patch_targets(a, col, row, op, cid)),
                        ("tile", lambda: wl_cell.display_tile(a, op, cid, 0))):
            try:
                per[key].append((op, cid, _j(fn())))
            except Exception as e:                              # noqa: BLE001
                per[key].append((op, cid, f"raise {type(e).__name__}: {e}"))
                _RAISED.append(f"{key} op${op:X}[{cid}]: {type(e).__name__}: {e}")
        if op == 0xB:
            try:
                per["links"].append((cid, _j(wl_cell.chain_links(a, cid))))
            except Exception as e:                              # noqa: BLE001
                per["links"].append((cid, f"raise {type(e).__name__}: {e}"))
                _RAISED.append(f"links[{cid}]: {type(e).__name__}: {e}")
    out.update({k: _j(v) for k, v in per.items()})
    return out


def main():
    verbose = "-v" in sys.argv
    n = 0
    fails = []
    counts = {}
    for f in sorted(glob.glob(os.path.join(ROOT, "baseline", "side*", "s*_record_id*.bin"))):
        b = os.path.basename(f)[:-4]
        side, area_id = int(b[1]), int(b.split("id")[1])
        raw = open(f, "rb").read()
        try:
            packed = wl_ir.ir_to_record(wl_ir.record_to_ir(raw, side, area_id))
        except Exception as e:                                  # noqa: BLE001
            fails.append((b, "repack", repr(e)))
            continue
        n += 1
        _RAISED.clear()
        v0, v1 = views(raw, side, area_id), views(packed, side, area_id)
        for r in dict.fromkeys(_RAISED):
            fails.append((b, "raised", r))
            counts["raised"] = counts.get("raised", 0) + 1
        bad = [k for k in v0 if v0[k] != v1[k]]
        for k in bad:
            counts[k] = counts.get(k, 0) + 1
            s0, s1 = v0[k], v1[k]
            i = next((j for j in range(min(len(s0), len(s1))) if s0[j] != s1[j]), min(len(s0), len(s1)))
            fails.append((b, k, f"@{i}  …{s0[max(0, i - 55):i + 55]}\n{'':>26}…{s1[max(0, i - 55):i + 55]}"))
        if verbose:
            print(f"{b:<20} {'OK' if not bad else 'UNSTABLE: ' + ','.join(bad)}")
    print(f"\n{n} records · every editor view, original vs re-packed")
    if not fails:
        print("all views identical — no view depends on what follows a record")
        return 0
    print(f"{len(fails)} unstable views: {counts}")
    for b, k, d in fails[:12]:
        print(f"  {b} {k}: {d}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
