"""NPC / FOE / SERVICE authoring — checked as OBJECTS, not as bytes.

These are the write paths the serializer cannot protect. `foe_names_raw` and `foe_records_raw` ride
through the IR verbatim, so a repack preserves two tables that have drifted out of step just as
faithfully as two that agree, and every byte-level check still passes. A swap there is invisible
until a player meets a lizard called Dire Coyote.

So the test is not "did the bytes survive" but "does the OBJECT still hold together" — `wl_foe.check`
states the binding as four invariants, and they are true of all 43 shipped areas before anything is
edited, which is what makes them invariants rather than opinions.

Per area:
    add_npc      -> invariant holds · the NPC is findable by name with its portrait and hire slot
                    · every pre-existing dispatch still reads identically · re-packs to a fixpoint
    update_npc   -> renaming moves BOTH tables together; the portrait follows the foe id
    delete       -> clearing the marker cells leaves the object graph consistent
    update_service -> the rebuilt SERVICE record still decodes, and nothing else moves

    python3 editor/backend/_npc_service_test.py [-v]
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wl_cell                                                  # noqa: E402
from wl_machine import RECORD_BASE as RAM   # $3400 on the C64, $D000 on the Apple
import wl_foe                                                   # noqa: E402
import wl_ir                                                    # noqa: E402
import wl_persist                                               # noqa: E402
from wl_area import load_area, save_area                        # noqa: E402
from _view_stability_test import views                          # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeArea:
    """Just enough of an Area for `_foe_names` to read a table we have only just emitted: the
    reader takes its base from desc+$02, so the table goes at a known offset and desc+$02 points
    there."""

    def __init__(self, table: bytes, at: int = 64):
        raw = bytearray(at) + bytearray(table)
        raw[2], raw[3] = (at + RAM) & 0xFF, ((at + RAM) >> 8) & 0xFF
        self.raw, self.slot = bytes(raw), 0

SPEC = {"name": "TESTRANGER", "stats": [12] * 7, "maxcon": 40, "money": 500,
        "portrait_str": 54, "glyph_tile": 4, "hireable": True, "join_str": 0, "rank": "SCOUT"}


def _post(blob, side, area_id, tag, fails, before=None, full=None):
    """The properties every save must satisfy, plus the object invariant."""
    try:
        out, _ = wl_persist.normalize(blob, side, area_id)
    except Exception as e:                                      # noqa: BLE001
        fails.append(f"{tag}: normalize failed — {e}")
        return None
    if len(out) > wl_persist.CAP:
        (full if full is not None else fails).append(
            f"{tag}: {len(out)} B — refused, no headroom left in the record")
        return None
    a = load_area(out, side, area_id)
    for v in wl_foe.check(a)[:1]:
        fails.append(f"{tag}: {v}")
    if before is not None:
        for d in wl_persist.verify_equivalent(before, out, side, area_id)[:1]:
            fails.append(f"{tag}: {d}")
    v0 = views(out, side, area_id)
    v1 = views(wl_ir.ir_to_record(wl_ir.record_to_ir(out, side, area_id)), side, area_id)
    for k in v0:
        if v0[k] != v1[k]:
            fails.append(f"{tag}: view '{k}' unstable across a re-pack")
            break
    return out


def main():
    verbose = "-v" in sys.argv
    fails, refused, noedit, n = [], [], [], 0
    for f in sorted(glob.glob(os.path.join(ROOT, "baseline", "side*", "s*_record_id*.bin"))):
        b = os.path.basename(f)[:-4]
        side, area_id = int(b[1]), int(b.split("id")[1])
        raw = open(f, "rb").read()
        a0 = load_area(raw, side, area_id)
        for v in wl_foe.check(a0)[:1]:                          # the shipped data must be clean
            fails.append(f"{b} BASELINE: {v}")

        # ── add_npc ───────────────────────────────────────────────────────────────────────
        added = None
        try:
            buf, info = wl_cell.add_npc(raw, side, area_id, SPEC)
            # the endpoint places the MARKER CELL — without it the new encounter record is
            # referenced by nothing, so the object is unreachable and `hire_slot` stays None
            a_mid = load_area(buf, side, area_id)
            nib, cid = list(a_mid.nibble), list(a_mid.cellid)
            free = next((i for i, op in enumerate(nib[:a_mid.width * a_mid.height]) if op == 0), None)
            if free is None:
                raise RuntimeError("no free cell for the marker")
            nib[free], cid[free] = 0x3, info["marker_cell_id"]
            n += 1
            added = _post(save_area(a_mid, nib, cid), side, area_id, f"{b} add_npc", fails,
                          before=raw, full=refused)
            if added is not None:
                a1 = load_area(added, side, area_id)
                fo = [x for x in wl_foe.foes(a1, side) if x["name"] == SPEC["name"]]
                if not fo:
                    fails.append(f"{b} add_npc: the NPC is not in the foe table after the save")
                elif fo[0]["portrait_str"] != SPEC["portrait_str"]:
                    fails.append(f"{b} add_npc: portrait {fo[0]['portrait_str']} != "
                                 f"{SPEC['portrait_str']} — the tables drifted")
                elif fo[0]["hire_slot"] != info["n"]:
                    fails.append(f"{b} add_npc: hire slot {fo[0]['hire_slot']} != grp17 index "
                                 f"{info['n']}")
        except Exception as e:                                  # noqa: BLE001
            fails.append(f"{b} add_npc: raised {e!r}")

        # ── update_npc: rename must move BOTH tables ──────────────────────────────────────
        if added is not None:
            try:
                # the NPC just created — not "the first non-zero grp17 entry". A template NPC that
                # no encounter references has no foe NAME to rename, so renaming it correctly
                # touches only the member record.
                a1 = load_area(added, side, area_id)
                idx = info["n"] if info["n"] < len(wl_foe.grp17_table(a1)) else None
                if idx is not None:
                    buf = wl_cell.update_npc(added, side, area_id, idx,
                                             {"name": "Renamed Npc", "portrait_str": 10})
                    n += 1
                    # no equivalence demand: an update CHANGES the grp17 record by design. What must
                    # hold is the object invariant and the fixpoint.
                    up = _post(buf, side, area_id, f"{b} update_npc", fails, full=refused)
                    if up is not None:
                        a2 = load_area(up, side, area_id)
                        # the FOE table keeps the case; the grp17 member record is uppercased
                        fo = [x for x in wl_foe.foes(a2, side) if x["name"] == "Renamed Npc"]
                        if not fo:
                            fails.append(f"{b} update_npc: the renamed foe is gone from the name table")
                        elif fo[0]["portrait_str"] != 10:
                            fails.append(f"{b} update_npc: portrait did not follow the rename "
                                         f"(got {fo[0]['portrait_str']})")
            except Exception as e:                              # noqa: BLE001
                fails.append(f"{b} update_npc: raised {e!r}")

        # ── delete: clear the marker cells, the graph must stay consistent ────────────────
        if added is not None:
            try:
                a1 = load_area(added, side, area_id)
                nib, cid = list(a1.nibble), list(a1.cellid)
                cleared = 0
                for i, op in enumerate(a1.nibble):
                    if op in (0x3, 0xF):
                        rec = wl_cell.subptr(added, a1.slot, op, a1.cellid[i])
                        if rec is not None and rec + 9 < len(added) and (added[rec + 9] >> 4):
                            nib[i] = cid[i] = 0
                            cleared += 1
                if cleared:
                    n += 1
                    _post(save_area(a1, nib, cid), side, area_id, f"{b} npc_delete", fails)
            except Exception as e:                              # noqa: BLE001
                fails.append(f"{b} npc_delete: raised {e!r}")

        # ── add_foe / update_foe: does the change SURVIVE the emit? ───────────────────────
        # The model (ir["foes"]) wins over the raw blobs, so a writer that patches only the blobs
        # has its change silently dropped — update_foe did exactly that and a glyph edit vanished
        # without an error. Demanding the value BACK OUT of the re-emitted record is the only check
        # that catches it; asserting on the intermediate would have passed.
        try:
            blob, res = wl_cell.add_foe(raw, side, area_id,
                                        {"name": "Probe", "portrait_str": 37, "glyph_tile": 2})
            fid = res["foe_id"]
            n += 1
            for field, spec, want in (("glyph", {"glyph_tile": 4}, ("glyph", 3)),
                                      ("portrait", {"portrait_str": 61}, ("portrait_str", 61)),
                                      # the case the author typed SURVIVES — 398 of the game's 400
                                      # foe names are mixed, and the two that are not are ours
                                      ("name", {"name": "Old Sentry"}, ("name", "Old Sentry"))):
                out = wl_cell.update_foe(blob, side, area_id, fid, spec)
                got = next((f for f in wl_foe.foes(load_area(out, side, area_id), side)
                            if f["id"] == fid), None)
                n += 1
                if got is None:
                    fails.append(f"{b} update_foe({field}): foe {fid} is gone after the save")
                elif got.get(want[0]) != want[1]:
                    fails.append(f"{b} update_foe({field}): asked for {want[0]}={want[1]!r}, "
                                 f"the re-emitted record says {got.get(want[0])!r}")
                else:
                    blob = out
        except ValueError as ve:                         # no room / no foe table: a refusal
            noedit.append(f"{b} add_foe: {ve}")
        except Exception as e:                           # noqa: BLE001
            fails.append(f"{b} add_foe/update_foe: raised {e!r}")

        # ── update_service ────────────────────────────────────────────────────────────────
        try:
            ir = wl_ir.record_to_ir(raw, side, area_id)
            svc = [int(k) for k, v in ir["groups"].get("6", {}).items() if v.get("op") == "SERVICE"]
            for cid0 in svc:                             # every service in the area, not just one
                buf = bytearray(raw)
                try:
                    wl_cell.update_service(buf, a0, cid0, {"label": "TESTED"})
                except ValueError as ve:                 # a family whose layout is not modelled:
                    noedit.append(f"{b} grp6[{cid0}]: {ve}")     # the endpoint answers 422, which is
                    continue                                     # a refusal, not a corruption
                n += 1
                out = _post(bytes(buf), side, area_id, f"{b} update_service[{cid0}]", fails,
                            full=refused)
                if out is not None:
                    rr = wl_ir._decode_op_record(out, load_area(out, side, area_id).slot, 6, cid0)
                    if rr.get("op") != "SERVICE":
                        fails.append(f"{b} update_service: grp6[{cid0}] decodes as "
                                     f"{rr.get('op')}, not SERVICE")
        except Exception as e:                                  # noqa: BLE001
            fails.append(f"{b} update_service: raised {e!r}")

        if verbose:
            print(f"{b:<20} {'OK' if not any(x.startswith(b) for x in fails) else 'FAIL'}")

    # ── the foe-NAME TABLE's own shape ────────────────────────────────────────────────────────
    # The engine prints "1 Brat" and "3 Brats" out of ONE entry, so an entry carries both words:
    # `$00 STEM \n SINGULAR-PART \n PLURAL-PART \n TAIL`, and the table closes with `$00 $FF` (or a
    # bare `$00`, letting the next table's first byte stop the walk). The editor used to emit
    # `$00 STEM \n \n` closed by `$01`, which its OWN reader accepted and the engine did not: the
    # name walker read one foe's plural out of the next entry and, at the end, found no terminator
    # it knows and kept going. TREX Base froze on the encounter contact box — announce printed, then
    # the clock stopped. Two readers, one of them not the engine's, is the bug class.
    #
    # THE STRONG FORM of that check is a full round-trip of the TABLE: decode every shipped entry,
    # re-emit, and demand the original bytes back. It has to hold for every area, because
    # `update_foe` re-emits the WHOLE table whenever anyone saves any foe — so an entry the emitter
    # cannot reproduce is an entry the next Save destroys. That is exactly what happened to Madman:
    # `Madm|a|e|n` read back as "Madma" and was re-emitted as `Madma|\n|s`, i.e. "Madma" / "Madmas"
    # in combat.
    #
    # Which is also why entries pass through as SEGMENTS rather than as re-derived words. The game
    # spells the same rule two ways — Stagecoach Inn has `Madm|a|e|n`, the Sewers have
    # `Clawm|an|en|` — and a reader that normalises them churns bytes nobody asked it to touch.
    exact = shaped = 0
    for f in sorted(glob.glob(os.path.join(ROOT, "baseline", "side*", "s*_record_id*.bin"))):
        b = os.path.basename(f)[:-4]
        side, area_id = int(b[1]), int(b.split("id")[1])
        raw = open(f, "rb").read()
        try:
            area = load_area(raw, side, area_id)
        except Exception:                                       # noqa: BLE001
            continue
        parts = wl_cell._foe_name_parts(area)
        if not parts:
            continue
        names = wl_cell._foe_names(area)
        nb = wl_cell._le(raw, area.slot + 0x02) - RAM
        fb = wl_cell._le(raw, area.slot + 0x04) - RAM
        shipped, enc = bytes(raw[nb:fb]), wl_cell.encode_foe_names(parts)
        n += 1
        # …and the two WORDS survive the pair conversion, which is what the panel edits
        for k, p in enumerate(parts):
            sing, plur = wl_cell.name_forms(p)
            if wl_cell.name_forms(wl_cell.segs_from_forms(sing, plur)) != (sing, plur):
                fails.append(f"{b} foe names: entry {k + 1} {p} → ({sing!r}, {plur!r}) does not "
                             "re-encode to the same two words")
        if not enc.endswith(b"\x00\xff"):
            fails.append(f"{b} foe names: emitted table does not close with $00 $FF")
        for e in enc.split(b"\x00")[1:-1]:
            # 3 = the switch is armed and closed (stem/singular/plural, with or without a tail);
            # 0 = a name with no plural form at all, which never arms it. Anything else leaves the
            # filter live and eats whatever prints next.
            # …counted outside the f-string: an escape inside one is 3.12 syntax, and this suite
            # has to run on the same interpreter as the rest (3.10 is the one with fastapi)
            seps = e.count(b"\n")
            if e and seps not in (0, 3):
                fails.append(f"{b} foe names: entry {e!r} has {seps} \\n separators, "
                             "which neither closes the singular/plural switch nor avoids it")
                break
        if wl_cell._foe_names(_FakeArea(enc)) != names:
            fails.append(f"{b} foe names: the emitted table does not read back as {names[:3]}…")
        shaped += 1
        # the round-trip, for EVERY area (bar the explicit terminator, which 10 areas leave to the
        # next table's first byte)
        if enc.rstrip(b"\xff") == shipped.rstrip(b"\xff"):
            exact += 1
        else:
            fails.append(f"{b} foe names: decode → emit is not byte-exact\n"
                         f"    shipped {shipped[:60]!r}\n    emitted {enc[:60]!r}")
    print(f"{shaped} foe-name tables checked for shape, {exact} of them decode → emit byte-exact")

    print(f"\n{n} NPC / FOE / SERVICE operations across the 43 areas")
    if refused:
        print(f"{len(refused)} refused for lack of room (correct — the record is full):")
        for x in refused[:4]:
            print(f"  {x}")
    if noedit:
        from collections import Counter
        c = Counter(x.split(": ")[-1] for x in noedit)
        print(f"{len(noedit)} service records the editor cannot patch: {dict(c)}")
    if fails:
        print(f"{len(fails)} failures:")
        for x in fails[:16]:
            print(f"  {x}")
        return 1
    print("the foe/NPC object graph survives add, rename, delete and service edits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
