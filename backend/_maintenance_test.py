"""Maintenance test: the operations that RESHAPE a record without authoring anything.

`_authoring_test` covers adding records. This covers the other write paths — compaction, string GC,
descriptor edits, prose rebuild — and each one gets the invariant that actually applies to it,
because a blanket "nothing may change" is wrong for half of them:

    /compact      reclaims dead space          -> every dispatchable record reads identically
    /gc-strings   RENUMBERS string refs        -> records change by design; every surviving string
                                                  must still resolve to the SAME TEXT
    /fill-tile    descriptor +$33              -> one byte, everything else identical
    /gfx          descriptor +$30              -> one byte, everything else identical
    /prose        rebuilds the text5 region    -> records unaffected (they reference by id), and the
                                                  edited string decodes back to what was written
    /undo, /history/restore                     -> a stored blob must normalise to a fixpoint whose
                                                  .json compiles back to the same .bin

On top of its own invariant every operation must also produce a blob that is a layout FIXPOINT and
whose editor views do not move on a further re-pack — the two properties that made the difference
between "it looked fine" and "it was fine" everywhere else.

    python3 editor/backend/_maintenance_test.py [-v]
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wl_cell                                                  # noqa: E402
from wl_machine import RECORD_BASE as RAM   # $3400 on the C64, $D000 on the Apple
import wl_ir                                                    # noqa: E402
import wl_persist                                               # noqa: E402
import wl_text5_encode as tenc                                  # noqa: E402
from wl_area import load_area, slot_for_area                    # noqa: E402
from _view_stability_test import views                          # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CAP = wl_persist.CAP


def _strings(raw, side, area_id):
    slot = slot_for_area(area_id)
    base = (raw[slot] | (raw[slot + 1] << 8)) - RAM
    if base < 0 or base + 0x3C >= len(raw):
        return []
    return list(tenc.enumerate_strings(raw, base, raw[base:base + 62]))


def _post(raw, side, area_id, tag, fails, expect_equivalent=True, before=None, full=None):
    """The properties EVERY save has to satisfy, whatever the operation was.

    Over the cap is NOT a failure: `save_record` raises and the endpoint answers 422, which is the
    correct outcome for a record that is genuinely full. It is recorded so the count is visible —
    two areas ship within a few bytes of the 9728 B window and have no headroom for a longer string.
    """
    try:
        blob, _ = wl_persist.normalize(raw, side, area_id)
    except Exception as e:                                      # noqa: BLE001
        fails.append(f"{tag}: normalize failed — {e}")
        return None
    if len(blob) > CAP:
        if full is not None:
            full.append(f"{tag}: {len(blob)} B — save refused, record is full")
            return None
        fails.append(f"{tag}: {len(blob)} B over the {CAP} B cap")
    if expect_equivalent and before is not None:
        for d in wl_persist.verify_equivalent(before, blob, side, area_id)[:1]:
            fails.append(f"{tag}: {d}")
    v0 = views(blob, side, area_id)
    v1 = views(wl_ir.ir_to_record(wl_ir.record_to_ir(blob, side, area_id)), side, area_id)
    for k in v0:
        if v0[k] != v1[k]:
            fails.append(f"{tag}: view '{k}' unstable across a re-pack")
            break
    return blob


def main():
    verbose = "-v" in sys.argv
    fails, refused, n = [], [], 0
    for f in sorted(glob.glob(os.path.join(ROOT, "baseline", "side*", "s*_record_id*.bin"))):
        b = os.path.basename(f)[:-4]
        side, area_id = int(b[1]), int(b.split("id")[1])
        raw = open(f, "rb").read()
        slot = load_area(raw, side, area_id).slot

        # ── /compact ──────────────────────────────────────────────────────────────────────
        try:
            new, reclaimed = wl_cell.compact_record(raw, side, area_id)
            n += 1
            _post(new, side, area_id, f"{b} compact(-{reclaimed} B)", fails, before=raw)
        except Exception as e:                                  # noqa: BLE001
            fails.append(f"{b} compact: raised {e!r}")

        # ── /gc-strings ───────────────────────────────────────────────────────────────────
        # Records change on purpose (refs are renumbered), so the invariant is SEMANTIC: every
        # string a record still points at must decode to the text it pointed at before.
        try:
            new, removed = wl_cell.remove_unused_strings(raw, side, area_id)
            n += 1
            blob = _post(new, side, area_id, f"{b} gc-strings(-{removed})", fails,
                         expect_equivalent=False)
            if blob is not None:
                a0, a1 = load_area(raw, side, area_id), load_area(blob, side, area_id)
                for i, op in enumerate(a0.nibble[:a0.width * a0.height]):
                    if op not in (0x1, 0xB, 0x9, 0x4):          # ops whose byte0 is a string id
                        continue
                    r0 = wl_cell.subptr(raw, a0.slot, op, a0.cellid[i])
                    r1 = wl_cell.subptr(blob, a1.slot, op, a1.cellid[i])
                    if r0 is None or r1 is None:
                        continue
                    t0 = wl_cell._prose(a0, raw[r0] & 0x7F)
                    t1 = wl_cell._prose(a1, blob[r1] & 0x7F)
                    if t0 != t1:
                        fails.append(f"{b} gc-strings: cell {i} op${op:X} text changed "
                                     f"{t0!r} -> {t1!r}")
                        break
        except Exception as e:                                  # noqa: BLE001
            fails.append(f"{b} gc-strings: raised {e!r}")

        # ── /fill-tile and /gfx — one descriptor byte each ────────────────────────────────
        for off, name in ((0x33, "fill-tile"), (0x30, "gfx")):
            if slot + off >= len(raw):
                continue
            buf = bytearray(raw)
            buf[slot + off] = (buf[slot + off] + 1) & 0xFF
            n += 1
            blob = _post(bytes(buf), side, area_id, f"{b} {name}", fails, before=bytes(buf))
            if blob is not None and blob[slot + off] != buf[slot + off]:
                fails.append(f"{b} {name}: descriptor +${off:02X} did not survive the save")

        # ── /prose — the text region, edited through the IR (see the endpoint) ────────────
        try:
            ir = wl_ir.record_to_ir(raw, side, area_id)
            strings = list(ir["strings"])
            if strings:
                sid = next((k for k, s in enumerate(strings) if s and k > 0), None)
                if sid is not None:
                    edited = list(strings)
                    edited[sid] = "TEST STRING"                 # plain A-Z + space: always encodable
                    ir["strings"] = edited
                    new = wl_ir.ir_to_record(ir)
                    n += 1
                    blob = _post(new, side, area_id, f"{b} prose", fails, before=raw,
                                 full=refused)
                    if blob is not None:
                        got = _strings(blob, side, area_id)
                        if len(got) <= sid or got[sid] != "TEST STRING":
                            fails.append(f"{b} prose: string {sid} decoded back as "
                                         f"{got[sid] if len(got) > sid else '<missing>'!r}")
                        for k, s in enumerate(strings):         # every OTHER string untouched
                            if k != sid and (len(got) <= k or got[k] != s):
                                fails.append(f"{b} prose: string {k} changed as a side effect")
                                break
        except Exception as e:                                  # noqa: BLE001
            fails.append(f"{b} prose: raised {e!r}")

        # ── /undo and the history restores — they hand a STORED blob back to the save path ────
        # The blob can come from an arbitrary git revision, so the property is self-consistency:
        # it must normalise to a fixpoint that the engine reads identically, and the .bin/.json
        # pair the save path writes must agree. A blob that fails is one the current model cannot
        # reproduce, and the restore has to refuse rather than put it in front of the cart build.
        try:
            n += 1
            blob = _post(raw, side, area_id, f"{b} restore", fails, before=raw)
            if blob is not None:
                ir = wl_ir.record_to_ir(blob, side, area_id)
                if wl_ir.ir_to_record(ir) != blob:
                    fails.append(f"{b} restore: the .json IR does not compile back to the .bin")
        except Exception as e:                                  # noqa: BLE001
            fails.append(f"{b} restore: raised {e!r}")

        if verbose:
            print(f"{b:<20} {'OK' if not any(x.startswith(b) for x in fails) else 'FAIL'}")

    print(f"\n{n} maintenance operations across the 43 areas")
    if refused:
        print(f"{len(refused)} correctly refused (no headroom left in the record):")
        for x in refused:
            print(f"  {x}")
    if fails:
        print(f"{len(fails)} failures:")
        for x in fails[:14]:
            print(f"  {x}")
        return 1
    print("compact · gc-strings · fill-tile · gfx · prose — all keep their invariant and re-pack clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
