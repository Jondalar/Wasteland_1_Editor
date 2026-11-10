"""wl_persist.py — the editor's SAVE path: every write of an area record goes through the serializer.

Before: an endpoint patched bytes in place (insert a record, repoint an array, compact) and wrote the
result. That is where the whole fragility class lived — over-reads, orphan growth, phantom pointers,
extent guessing — because the layout was never rebuilt, only nudged.

Now: whatever bytes an endpoint produces are decoded to the JSON-IR and RE-SERIALIZED, so the layout
is built from scratch on every save and the blob is a FIXPOINT. Two things are written:

    work/sideN/sN_record_idAA.bin    the canonical bytes (what the cart build reads)
    work/sideN/sN_record_idAA.json   the same content as JSON-IR (diffable, the future source)

A save is REFUSED if any record the engine can dispatch would change its bytes. That check is the
same one the engine-level E2E makes (`cart_EF/tools/wl_e2e_engine_resolve.py`), minus the 6502: the
resolution formula `WORD[WORD[desc+$06+grp*2] + id*2]` is `wl_cell.subptr`, verified against the
shipped `area_record_subptr` over 4407 pairs.
"""
import json
import os

import wl_cell
import wl_ir
import wl_marks
from wl_area import load_area

from wl_machine import RECORD_BASE as RAM   # $3400 on the C64, $D000 on the Apple
CAP = 9728                                              # $3400..$5A00 record window


def normalize(raw: bytes, side: int, area: int, rounds: int = 4):
    """(blob, ir) — the record re-serialized to a layout FIXPOINT.

    Iterates because the records kept verbatim (EVENT: a payload only the area's own grp16 code
    knows how to read) are sized by the distance to the next structure, so a first pass can carry
    slack the original layout left behind; the second pass sees a tight neighbour and drops it."""
    ir = wl_ir.record_to_ir(raw, side, area)
    blob = wl_ir.ir_to_record(ir)
    for _ in range(rounds - 1):
        ir2 = wl_ir.record_to_ir(blob, side, area)
        b2 = wl_ir.ir_to_record(ir2)
        if b2 == blob:
            return blob, ir2
        ir, blob = ir2, b2
    raise RuntimeError(f"s{side} area {area}: layout did not converge in {rounds} rounds")


def _pairs(raw, side, area):
    """Every (grp, id) the engine can be asked to resolve — cells, the record graph, grp15/grp17."""
    a = load_area(raw, side, area)
    slot = a.slot
    out = {(op, a.cellid[i]) for i, op in enumerate(a.nibble[:a.width * a.height]) if 0 < op < 0x10}
    out |= set(wl_ir._ref_ops(raw, slot, a, wl_ir._door_targets(raw, slot, a)))
    # A reference only counts if the SLOT exists: an id past the array's span (grp5[255], grp2[62])
    # or in a parked group (grp17 while its pointer sits on foe-names) names no record — the word
    # "there" is another structure's bytes. Same rule the decoder uses to decide what to emit.
    bounds = wl_cell._section_bounds(raw, slot)
    n = {g: wl_cell.group_slot_count(raw, slot, g, bounds) for g in range(19) if g != 16}
    return sorted(p for p in out
                  if p[1] < n.get(p[0], 0) and wl_cell.subptr(raw, slot, p[0], p[1]) is not None)


def _marks(raw, slot):
    """Structure starts: every array entry that resolves + every descriptor pointer + EOF."""
    m = {len(raw), slot}
    for fld in range(0x00, 0x2C, 2):
        v = wl_cell._le(raw, slot + fld) - RAM
        if 0 <= v <= len(raw):
            m.add(v)
    bounds = wl_cell._section_bounds(raw, slot)
    for g in range(19):
        if g == 16 or wl_cell.gptr(raw, slot, g) is None:
            continue
        for cid in range(wl_cell.group_slot_count(raw, slot, g, bounds)):
            o = wl_cell.subptr(raw, slot, g, cid)
            if o is not None and 0 <= o < len(raw):
                m.add(o)
    return sorted(m)


def verify_equivalent(old: bytes, new: bytes, side: int, area: int) -> list:
    """[] when the engine reads identical bytes for every dispatchable (grp, id); else the diffs.

    Compares over the shorter of the two records' extents, where an extent is "up to the next
    structure" — derived from resolved pointers only, so no byte-format model takes part."""
    slot = load_area(old, side, area).slot
    mo, mn = _marks(old, slot), _marks(new, slot)

    def end(marks, p, n):
        for x in marks:
            if x > p:
                return x
        return n
    bad = []
    for g, cid in _pairs(old, side, area):
        p0 = wl_cell.subptr(old, slot, g, cid)
        p1 = wl_cell.subptr(new, slot, g, cid)
        if p1 is None or not (0 <= p1 < len(new)):
            bad.append(f"grp{g}[{cid}] no longer resolves")
            continue
        L = min(end(mo, p0, len(old)) - p0, end(mn, p1, len(new)) - p1)
        if old[p0:p0 + L] != new[p1:p1 + L]:
            bad.append(f"grp{g}[{cid}] bytes differ ({L} B compared)")
    return bad


def save_record(work_bin, raw: bytes, side: int, area: int, verify: bool = True) -> dict:
    """Serialize + write sN_record_idAA.bin and .json. Raises ValueError if the save is not
    engine-equivalent to `raw`, so a broken edit can never reach disk."""
    blob, ir = normalize(raw, side, area)
    if verify:
        bad = verify_equivalent(raw, blob, side, area)
        if bad:
            raise ValueError("serializer save refused — the engine would read different bytes: "
                             + "; ".join(bad[:6]) + (f" (+{len(bad) - 6} more)" if len(bad) > 6 else ""))
    if len(blob) > CAP:
        raise ValueError(f"record is {len(blob)} B, over the {CAP} B cap")
    work_bin = str(work_bin)
    os.makedirs(os.path.dirname(work_bin), exist_ok=True)

    # The document that goes to disk is the MARKED one. Every reference in it names its target
    # instead of pointing at a table slot, so the .json survives a renumbering and the .bin is the
    # product of resolving it (IR_NAMED_REFERENCES.md §4). Marks already on disk are carried over
    # first — regenerating them here would rename a record on every unrelated save.
    prev = work_bin[:-4] + ".json"
    if os.path.isfile(prev):
        try:
            with open(prev) as fh:
                old = json.load(fh)
            if wl_marks.is_marked(old):
                wl_marks.carry(old, ir)
        except (ValueError, OSError):
            pass                                        # unreadable = no marks to carry, not fatal
    wl_marks.remark(ir, f"s{side}a{area:02d}")

    with open(work_bin, "wb") as fh:
        fh.write(blob)
    with open(work_bin[:-4] + ".json", "w") as fh:
        json.dump(ir, fh, indent=1, sort_keys=True)
    return {"bytes": len(blob), "delta": len(blob) - len(raw), "over_cap": False,
            "records": sum(len(v) for v in ir["groups"].values()),
            "verbatim": sum(len(v) for v in ir.get("opaque", {}).values())}


def sync_from_json(work_bin, side: int, area: int) -> bool:
    """If a hand-edited .json is newer than its .bin, RECOMPILE the .bin from it. Returns True when
    it did.

    This is what makes the JSON the source of truth rather than a by-product: edit the IR in a text
    editor, and the next load compiles it. The .bin stays as the artefact the cart build consumes."""
    b = str(work_bin)
    j = b[:-4] + ".json"
    if not os.path.isfile(j):
        return False
    if os.path.isfile(b) and os.path.getmtime(j) <= os.path.getmtime(b):
        return False
    with open(j) as fh:
        ir = json.load(fh)
    # A document from an OLDER decoder must never be compiled over the bytes: its shape is not what
    # the current emitter expects, so the .bin would come back subtly different from what the .json
    # was describing. The .bin is authoritative; the next save rewrites the .json.
    v = (ir.get("meta") or {}).get("ir_version")
    if v != wl_ir.IR_VERSION:
        raise ValueError(f"{os.path.basename(j)} was written by IR version {v}, this decoder is "
                         f"{wl_ir.IR_VERSION} — not compiling it; edit the area and save to refresh")
    # Marks are resolved to slot numbers HERE and nowhere else: this is the one place a stored
    # document becomes bytes, so it is the one place that has to know that `"next": "highpool/well"`
    # and `"next": {"op": 4, "id": 12}` mean the same thing.
    blob = wl_ir.ir_to_record(wl_marks.resolve(ir) if wl_marks.is_marked(ir) else ir)
    if len(blob) > CAP:
        raise ValueError(f"{os.path.basename(j)} compiles to {len(blob)} B, over the {CAP} B cap")
    with open(b, "wb") as fh:
        fh.write(blob)
    return True
