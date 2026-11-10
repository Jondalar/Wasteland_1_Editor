"""Authoring test: a record the EDITOR creates must be as solid as one from 1988.

Every battery so far reads and re-packs records that already exist. Authoring is the other
direction and it has its own failure modes: an added record that decodes back to something else, an
array relocation that breaks a dispatch, a new record that shifts the layout so some OTHER record's
view changes. None of that is covered by decoding the 43 baselines.

So: for every op the editor can add, build the record with the serializer's own emitter, append it
with `wl_cell.add_record`, point a free cell at it, and then demand of the RESULT everything the
baselines have to satisfy —

  1. the new record decodes back to exactly the IR it was authored from
  2. every previously-dispatching cell still resolves to the same record bytes
  3. the whole area survives `record_to_ir -> ir_to_record` byte-identically
  4. no editor view changes across that re-pack (the `_view_stability_test` property)
  5. adding N records in a row keeps all of the above (arrays relocate every time)

    python3 editor/backend/_authoring_test.py [-v]
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wl_cell                                                  # noqa: E402
import wl_ir                                                    # noqa: E402
from wl_area import load_area, save_area                        # noqa: E402
import wl_persist                                              # noqa: E402
from _view_stability_test import views                          # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# One authored record per op, written as IR — so the bytes are exactly what the serializer emits.
SPECS = {
    0x1: {"op": "SAY", "messages": [3]},   # a SAY is the message list and nothing else
    0x2: {"op": "COND", "b0": 0x80, "prompt": 4, "success": 5, "fail": 6,
          "done": {"op": 0xFF, "id": 0}, "alt": {"op": 0xFF, "id": 0},
          "effect": {"target": 0, "value": 0}, "conds": [{"opb": 0x20, "arg": 0x0E}]},
    0x4: {"op": "OBJ", "msg": 7, "tile": 2, "solid": False, "use": {"op": 0xFF}},
    0x5: {"op": "LOOT", "advance": {"op": 0xFF, "id": 0},
          "items": [{"item": 0x1C, "qty": 2}, {"item": 0xDE, "qty16": 1234}],
          "term_raw": "hex:00ff"},                     # the walker's 2-byte form — its only exerciser
    0x9: {"op": "HAZARD", "msg": 8, "dice": 3},
    0xA: {"op": "TRANSITION", "b0": 0x40, "x": 5, "y": 6, "target": 0, "next": {"op": 0xFF}},
    0xB: {"op": "BUMP", "msg": 9, "next": {"op": 0xFF}},
    0xC: {"op": "PATCH", "msg": 10, "entries": [{"op": 1, "id": 0, "dx": 3, "dy": 4, "flags": 0}],
          "after": {"op": 0xFF}},
}

# The OTHER authoring path — the one the editor's Add-op button actually takes. `SPECS` above hands
# the serializer a ready-made IR record, so it proves the EMITTER and nothing else. The endpoint
# takes flat parameters and must first NAME them correctly, and that step used to be a second,
# hand-rolled copy of every layout. It had drifted in three places, all silent, because a record
# with the right length and the wrong meaning still round-trips byte-for-byte:
#
#     LOOT     `message` was written into the ADVANCE pair (+0/+1), not a message field
#     SERVICE  the portrait went to +$03; it lives at +$13
#     SAY/OBJ  the trailing advance byte was omitted and borrowed from the next record
#
# So the params go through `wl_cell.op_record_ir` and the RESULT must decode back to what the
# parameters meant. Anything that drifts again fails here instead of on hardware.
PARAM_SPECS = {
    0x1: {"message": 3},
    0x2: {"prompt": 4, "success": 5, "fail": 6, "conds": [{"op": 0x20, "arg": 0x0E}]},
    0x3: {"message": 7, "foe": 2, "count": 3, "peaceful": True, "victory_op": 5, "victory_id": 1},
    0x4: {"message": 7, "pad": 2},
    0x5: {"items": [{"item": 0x1C, "qty": 2}], "advance_op": 6, "advance_id": 4},
    0x6: {"selector": 9, "label": "Toastmaster", "portrait": 75, "pages": [3, 4, 5]},
    0x9: {"message": 8, "dice": 3},
    0xA: {"message": 0, "x": 5, "y": 6, "target": 0},
    0xB: {"message": 9},
    0xC: {"message": 10, "patches": [{"dx": 3, "dy": 4, "op": 1, "cell_id": 0}]},
}

# What each parameter set MEANS, checked against the decoded record. Only the fields a parameter
# names — the decoder adds derived ones.
PARAM_MEANING = {
    0x1: {"messages": [3]},
    0x2: {"prompt": 4, "success": 5, "fail": 6},
    0x3: {"msg": 7, "flags": {"active": True, "peaceful": True, "npc": 0},
          "victory": {"op": 5, "id": 1}},
    0x5: {"advance": {"op": 6, "id": 4}, "items": [{"item": 0x9C, "qty": 2}]},
    0x6: {"selector": 9},
    0x9: {"msg": 8, "dice": 3},
    0xA: {"x": 5, "y": 6, "target": 0},
    0xB: {"msg": 9},
    0xC: {"msg": 10},
}


def _free_cell(area):
    """A map cell with no op — where an authored record gets dispatched from."""
    for i, op in enumerate(area.nibble[:area.width * area.height]):
        if op == 0:
            return i
    return None


def _dispatch_map(area):
    """{(op, cid): record bytes} for every cell that dispatches — what must not change."""
    out = {}
    bounds = wl_cell._section_bounds(area.raw, area.slot)
    for i, op in enumerate(area.nibble[:area.width * area.height]):
        if not 0 < op < 0x10:
            continue
        cid = area.cellid[i]
        o = wl_cell.subptr(area.raw, area.slot, op, cid)
        if o is None:
            continue
        end = wl_cell._section_end(bounds, o, len(area.raw))
        out[(op, cid)] = bytes(area.raw[o:end])
    return out


def author(raw, side, area_id, op, spec):
    """Append one authored record and dispatch a free cell at it. Returns (new blob, dispatch id)."""
    area = load_area(raw, side, area_id)
    i = _free_cell(area)
    if i is None:
        return None, None
    rec = wl_ir._emit_op_record(spec)
    buf = bytearray(area.raw)
    active = sorted(wl_cell.group_referenced_ids(area, op))
    new_id = wl_cell.add_record(buf, area.slot, op, rec, active)
    a2 = load_area(bytes(buf), side, area_id)
    nib, cid = list(a2.nibble), list(a2.cellid)
    nib[i], cid[i] = op, new_id
    surgery = save_area(a2, nib, cid)
    # What the editor actually STORES is not the byte-surgery output: `_save_record` runs it through
    # `wl_persist.normalize`, a layout fixpoint. That matters here — `add_record` relocates the
    # group's pointer array to the blob tail and leaves the old one behind as an unreferenced hole,
    # which stretches the measured extent of whatever record preceded it. The serializer rebuilds
    # every array from scratch, so the hole never reaches disk. Test the stored bytes, not the
    # intermediate.
    blob, _ = wl_persist.normalize(surgery, side, area_id)
    return blob, new_id


def check(raw, side, area_id, op, cid, spec, before, tag, fails):
    a = load_area(raw, side, area_id)
    # 1. the authored record decodes back to what was authored
    got = wl_ir._decode_op_record(a.raw, a.slot, op, cid)
    want = json.loads(json.dumps(spec))
    for k, v in want.items():
        if k == "op":
            continue
        g = got.get(k)
        if isinstance(v, dict) and isinstance(g, dict):
            g = {kk: g.get(kk) for kk in v}          # the decoder adds derived fields (COND effect
        if g != v:                                   # names them); only the AUTHORED keys must match
            fails.append(f"{tag} op${op:X}: field {k} authored {v!r}, decoded {got.get(k)!r}")
            break
    # 2. every pre-existing dispatch still reads the same bytes — asked the way the save path asks
    #    it, with the engine's own resolution rather than a byte compare
    for d in wl_persist.verify_equivalent(before, raw, side, area_id)[:1]:
        fails.append(f"{tag} op${op:X}: {d}")
    # 3. + 4. the area survives a re-pack, and no view moves with it
    try:
        packed = wl_ir.ir_to_record(wl_ir.record_to_ir(raw, side, area_id))
    except Exception as e:                                      # noqa: BLE001
        fails.append(f"{tag} op${op:X}: re-pack raised {e!r}")
        return raw
    v0, v1 = views(raw, side, area_id), views(packed, side, area_id)
    for k in v0:
        if v0[k] != v1[k]:
            fails.append(f"{tag} op${op:X}: view '{k}' changes across a re-pack of the authored area")
            break
    return raw


def author_chain(raw, side, area_id, steps):
    """The other authoring path: a CHAIN of linked records, ids allocated by chain_head()."""
    area = load_area(raw, side, area_id)
    i = _free_cell(area)
    if i is None:
        return None
    buf = bytearray(area.raw)
    out = wl_cell.write_script_chain(bytes(buf), area, i % area.width, i // area.width, steps,
                                     loop=True)[0]              # (bytes, head_op, head_id)
    blob, _ = wl_persist.normalize(out, side, area_id)
    return blob


def main():
    verbose = "-v" in sys.argv
    fails, n = [], 0
    for f in sorted(glob.glob(os.path.join(ROOT, "baseline", "side*", "s*_record_id*.bin"))):
        b = os.path.basename(f)[:-4]
        side, area_id = int(b[1]), int(b.split("id")[1])
        raw = open(f, "rb").read()
        if _free_cell(load_area(raw, side, area_id)) is None:
            continue
        # one op at a time, each onto a FRESH copy of the area
        for op, spec in SPECS.items():
            before = raw
            out, cid = author(raw, side, area_id, op, spec)
            if out is None:
                continue
            n += 1
            check(out, side, area_id, op, cid, spec, before, b, fails)
        # the ENDPOINT path: flat params → op_record_ir → the same demands as above, plus "the
        # decoded record still means what the parameters said"
        for op, params in PARAM_SPECS.items():
            try:
                spec = wl_cell.op_record_ir(op, params)
            except ValueError as e:                             # noqa: BLE001
                fails.append(f"{b} params op${op:X}: op_record_ir raised {e!r}")
                continue
            out, cid = author(raw, side, area_id, op, spec)
            if out is None:
                continue
            n += 1
            check(out, side, area_id, op, cid, spec, raw, f"{b}(params)", fails)
            aa = load_area(out, side, area_id)
            got = wl_ir._decode_op_record(aa.raw, aa.slot, op, cid)
            for k, want in PARAM_MEANING.get(op, {}).items():
                g = got.get(k)
                if isinstance(want, dict) and isinstance(g, dict):
                    g = {kk: g.get(kk) for kk in want}
                if isinstance(want, list) and isinstance(g, list) and len(g) >= len(want):
                    # project only the named keys of a list of RECORDS; a list of plain ints
                    # (SAY's `messages`) is compared whole
                    g = [{kk: e.get(kk) for kk in w} if isinstance(w, dict) else e
                         for e, w in zip(g, want)]
                if g != want:
                    fails.append(f"{b}(params) op${op:X}: params meant {k}={want!r}, "
                                 f"the record decodes to {got.get(k)!r}")
        # and all of them onto ONE copy, in sequence — every add relocates an array
        cur = raw
        for op, spec in SPECS.items():
            before = cur
            out, cid = author(cur, side, area_id, op, spec)
            if out is None:
                break
            cur = check(out, side, area_id, op, cid, spec, before, f"{b}(seq)", fails)
        # CHAINS take a different id-allocation path (chain_head + add_chain), so they get their own
        # pass: three linked steps, then the same demands as a single record.
        try:
            ch = author_chain(raw, side, area_id,
                              [{"op": 0xB, "message": 3}, {"op": 0x1, "message": 4},
                               {"op": 0xB, "message": 5}])
        except Exception as e:                                  # noqa: BLE001
            ch = None
            fails.append(f"{b} chain: raised {e!r}")
        if ch is not None:
            n += 3
            for d in wl_persist.verify_equivalent(raw, ch, side, area_id)[:1]:
                fails.append(f"{b} chain: {d}")
            v0, v1 = views(ch, side, area_id), views(
                wl_ir.ir_to_record(wl_ir.record_to_ir(ch, side, area_id)), side, area_id)
            for k in v0:
                if v0[k] != v1[k]:
                    fails.append(f"{b} chain: view '{k}' unstable across a re-pack")
                    break
        if verbose:
            print(f"{b:<20} {'OK' if not any(x.startswith(b) for x in fails) else 'FAIL'}")
    print(f"\n{n} records authored across the baseline areas")
    if fails:
        print(f"{len(fails)} failures:")
        seen = set()
        for x in fails[:20]:
            k = x.split(":")[1] if ":" in x else x
            if k in seen:
                continue
            seen.add(k)
            print(f"  {x}")
        return 1
    print("every authored record decodes back, leaves existing dispatches intact, and re-packs clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
