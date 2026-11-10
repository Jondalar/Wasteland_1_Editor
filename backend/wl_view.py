"""wl_view.py — the editor's derived views, computed from the JSON-IR instead of from bytes.

Why this exists: until now the editor decoded the record format TWICE — `wl_cell` for everything the
UI shows, `wl_ir` for everything it saves. Two implementations of the same byte layout, free to drift
apart, and one of them (the display side) had no test that could catch it. Every view in here reads
the typed IR fields (`next`, `branch`, `advance`, `victory`, `use`, `done`/`alt`, `entries`,
`after`), so a record's structure is decoded in exactly one place.

Each function has a byte-based twin in `wl_cell`; `_view_parity_test.py` asserts they return the
same thing for all 43 baselines plus the edited work records. That equivalence is what makes the
switch safe — and while both exist, either can be checked against the other.
"""

_PRODUCERS = (0x1, 0x2, 0x3, 0x4, 0x5, 0xB, 0xC)   # ops whose record names a follow-up state


def _s8(v: int) -> int:
    return v - 256 if v >= 128 else v


def _adv_write(adv):
    """A DONE/ALT/advance edge writes the map only when its op byte is a real nibble (0..15).
    >= $10 (esp. $80/$FD/$FE/$FF) is the engine's no-change escape."""
    if not adv:
        return None
    op = adv.get("op", 0xFF)
    return (op, adv.get("id", 0)) if op < 0x10 else None


def _rec(ir, op, cid):
    return ir["groups"].get(str(op), {}).get(str(cid))


def patch_targets(ir, col: int, row: int, op: int, cid: int) -> list:
    """What the state (op, cid) at (col,row) REWRITES when it fires — from the IR.

    Every scene op ends by resolving ONE escape and writing it to the party's cell, i.e. this one;
    in the IR that escape is the op's typed advance field. op-2 additionally rewrites its own cell
    through DONE/ALT, and op-C carries an explicit target list. Party-relative op-C entries keep
    cell=None because their target depends on where the party stood."""
    rr = _rec(ir, op, cid)
    if not rr:
        return []
    out = []
    kind = rr.get("op")
    self_adv = {"LOOT": ("advance", "take loot"), "OBJ": ("use", "use object"),
                "ENCOUNTER": ("victory", "after the fight"), "SAY": ("branch", "after message"),
                "BUMP": ("next", "on bump")}.get(kind)
    if self_adv:
        w = _adv_write(rr.get(self_adv[0]))
        if w:
            out.append({"cell": [col, row], "op_nibble": w[0], "cell_id": w[1],
                        "branch": self_adv[1]})
    elif kind == "COND":
        for branch in ("done", "alt"):
            w = _adv_write(rr.get(branch))
            if w:
                out.append({"cell": [col, row], "op_nibble": w[0], "cell_id": w[1],
                            "branch": "success" if branch == "done" else "fail"})
    elif kind == "PATCH":
        for e in rr.get("entries", []):
            if e.get("op", 0xFF) >= 0x10:
                continue
            if e["flags"] & 1:                       # party-relative -> not placeable statically
                out.append({"cell": None, "op_nibble": e["op"], "cell_id": e["id"],
                            "branch": "patch", "relative": [_s8(e["dx"]), _s8(e["dy"])]})
            else:
                tc, tr = _s8(e["dx"]), _s8(e["dy"])
                if 0 <= tc < ir["meta"]["width"] and 0 <= tr < ir["meta"]["height"]:
                    out.append({"cell": [tc, tr], "op_nibble": e["op"], "cell_id": e["id"],
                                "branch": "patch"})
        w = _adv_write(rr.get("after"))
        if w:
            out.append({"cell": [col, row], "op_nibble": w[0], "cell_id": w[1],
                        "branch": "after patch"})
    return out


def area_chains(ir, state_text=None, tile_of=None) -> dict:
    """Follow the mutation CHAIN transitively — the IR twin of wl_cell.area_chains.

    A patch does not just flip a cell to a final state: the state it writes is often itself a
    producer, which unlocks the next patch (Highpool's dried creek is six fights where taking each
    loot arms the next step). Cycles terminate because a (cell, op, id) triple expands once.

    `state_text(op, cid) -> dict` and `tile_of(col, row, op, cid)` are injected so this stays pure
    structure; the presentation helpers still live in wl_cell.
    """
    W = ir["meta"]["width"]
    grid_op, grid_cid = ir["grid"]["op"], ir["grid"]["cellid"]
    txt = state_text or (lambda op, cid: {})
    tile = tile_of or (lambda c, r, op, cid: None)
    chains, seen, frontier = {}, set(), []
    for row in range(ir["meta"]["height"]):
        for col in range(W):
            i = row * W + col
            op, cid = grid_op[i], grid_cid[i]
            if op in _PRODUCERS:
                seen.add((col, row, op, cid))
                frontier.append(((col, row, op, cid), 0, f"{col},{row}"))
    while frontier:
        (col, row, op, cid), depth, root = frontier.pop(0)
        if depth > 200:                              # runaway guard only
            continue
        for t in patch_targets(ir, col, row, op, cid):
            if t["cell"] is None:
                continue
            tc, tr = t["cell"]
            nop, ncid = t["op_nibble"], t["cell_id"]
            key = (tc, tr, nop, ncid)
            if key in seen:
                continue
            seen.add(key)
            k = f"{tc},{tr}"
            if k not in chains:                      # step 0 = the state the map ships with
                i = tr * W + tc
                op0, cid0 = grid_op[i], grid_cid[i]
                chains[k] = [{"step": 0, "op_nibble": op0, "cell_id": cid0,
                              "tile": tile(tc, tr, op0, cid0),
                              "from": None, "branch": None, **txt(op0, cid0)}]
            chains[k].append({"step": len(chains[k]), "op_nibble": nop, "cell_id": ncid,
                              "tile": tile(tc, tr, nop, ncid),
                              "from": [col, row], "from_state": [op, cid],
                              "branch": t.get("branch"), "root": root, **txt(nop, ncid)})
            if nop in _PRODUCERS:
                frontier.append((key, depth + 1, root))
    return {"cells": chains}


def area_transitions(ir, prose=None) -> list:
    """Every op-$A TRANSITION of this area — the IR twin of wl_cell.area_transitions.

    Two sources, exactly as the engine sees them: transitions wired to a live op-$A CELL, and
    SUBTABLE-only entries — grp10 slots no cell currently points at, which an op-2/op-C can reveal
    later (Cochise Core is reachable only that way). A slot only counts if it EXISTS: past the
    array's end the "pointers" are another structure's bytes, which is what a fixed 48-slot scan
    used to read as phantom doors.
    """
    W = ir["meta"]["width"]
    grid_op, grid_cid = ir["grid"]["op"], ir["grid"]["cellid"]
    pr = prose or (lambda idx: "")
    out, active = [], set()

    def entry(rr, cell):
        b0 = rr["b0"]
        return {"cell": cell, "target": rr["target"], "arrive": [rr["x"], rr["y"]],
                "message": (pr(b0 - 0x40) if 0x40 <= b0 <= 0x7F else "").strip(),
                "party_relative": bool(b0 & 0x80), "is_return": rr["target"] == 0xFF,
                "source": "cell" if cell else "subtable"}
    for row in range(ir["meta"]["height"]):
        for col in range(W):
            i = row * W + col
            if grid_op[i] != 0xA:
                continue
            active.add(grid_cid[i])
            rr = _rec(ir, 0xA, grid_cid[i])
            if rr and rr.get("op") == "TRANSITION":
                out.append(entry(rr, [col, row]))
    for cid_s, rr in sorted(ir["groups"].get("10", {}).items(), key=lambda kv: int(kv[0])):
        cid = int(cid_s)
        if cid in active or cid >= 48 or rr.get("op") != "TRANSITION":
            continue
        if (rr.get("next") or {}).get("op") != 0xFF:   # $FF at +4 = a plain door, no follow-op
            continue                                  # (the old string test also matched "06ff")
        if not (rr["target"] <= 51 or rr["target"] == 0xFF):
            continue
        out.append(entry(rr, None))
    return out


def area_mutations(ir, target_label=None, tile_pair=None, cond_summary=None, prose=None) -> list:
    """Every cell whose script REWRITES the map — the IR twin of wl_cell.area_mutations.

    Note this is NOT patch_targets with labels bolted on: the Map tab wants a per-SOURCE view, and
    the two disagree deliberately.
      · op-C party-relative entries resolve HERE (col+dx, row+dy) because a source cell is known,
        while patch_targets reports them as unplaceable — it is asked about a state, not a cell.
      · op-C's trailing advance ("after patch") is a state transition, not a map mutation, so it
        does not belong in this list.
      · op-6 EVENT cells are flagged code-driven: only the area's own grp16 6502 knows what they do.
      · op-B BUMP self-rewrites are included — a rotating message chain changes the cell too.

    `target_label`, `tile_pair`, `cond_summary` and `prose` are injected presentation helpers; the
    structure below is all IR.
    """
    W, H = ir["meta"]["width"], ir["meta"]["height"]
    grid_op, grid_cid = ir["grid"]["op"], ir["grid"]["cellid"]
    lab = target_label or (lambda op, cid: "")
    tp = tile_pair or (lambda c, r, op, cid: {"tile_now": None, "tile_after": None})
    csum = cond_summary or (lambda rr: "always")
    pr = prose or (lambda idx: "")
    out = []

    def tgt(col, row, onib, cid, branch):
        return {"cell": [col, row], "op_nibble": onib, "cell_id": cid, "branch": branch,
                "label": lab(onib, cid), **tp(col, row, onib, cid)}

    for row in range(H):
        for col in range(W):
            i = row * W + col
            op, cid = grid_op[i], grid_cid[i]
            rr = _rec(ir, op, cid)
            if op == 0x2:
                if not rr or rr.get("op") != "COND":
                    continue
                targets = []
                for branch, key in (("success", "done"), ("fail", "alt")):
                    w = _adv_write(rr.get(key))
                    if w:
                        targets.append(tgt(col, row, w[0], w[1], branch))
                if targets:
                    out.append({"source": [col, row], "op": 2, "trigger": csum(rr),
                                "targets": targets})
            elif op == 0xC:
                if not rr or rr.get("op") != "PATCH":
                    continue
                targets = []
                for e in rr.get("entries", []):
                    if e["flags"] & 1:
                        tc, tr = col + _s8(e["dx"]), row + _s8(e["dy"])
                    else:
                        tc, tr = _s8(e["dx"]), _s8(e["dy"])
                    if 0 <= tc < W and 0 <= tr < H and e.get("op", 0xFF) < 0x10:
                        targets.append(tgt(tc, tr, e["op"], e["id"], "patch"))
                if targets:
                    out.append({"source": [col, row], "op": 12,
                                "trigger": pr(rr["msg"]) or "patch", "targets": targets})
            elif op == 0x6:
                if rr and rr.get("op") == "EVENT":
                    out.append({"source": [col, row], "op": 6,
                                "trigger": f"grp16 event {rr['event']}", "targets": [],
                                "code": True, "event": rr["event"]})
            elif op == 0xB:
                if not rr or rr.get("op") != "BUMP":
                    continue
                w = _adv_write(rr.get("next"))
                if not w:
                    continue
                out.append({"source": [col, row], "op": 11,
                            "trigger": pr(rr["msg"]) or f"bump str #{rr['msg'] & 0x7F}",
                            "targets": [tgt(col, row, w[0], w[1], "on bump")]})
    return out


def record_chains(ir, area, state_text=None) -> list:
    """Chains that hand over RECORD TO RECORD, which the map-rewrite view cannot see.

    `area_chains` follows what a script writes back into the MAP, so a sequence that never touches
    the map is invisible to it — and those are the ones a designer asks about most. TREX Base is the
    clean example: its door record is `C0 00 00 33 06 0B`, and the `06 0B` tail hands over to
    grp6[11], the TREX talk service. Nothing is rewritten, so the Flow view listed one event for an
    area with twelve services.

    So follow the other edge kind: a record's own advance/branch/door tail (`wl_ir._record_edges`,
    plus the op-A tail, which is not an advance and lives only in the transition's trailing bytes).
    Returns one entry per cell whose chain is longer than the cell itself.
    """
    import wl_ir
    W, H = ir["meta"]["width"], ir["meta"]["height"]
    grid_op, grid_cid = ir["grid"]["op"], ir["grid"]["cellid"]
    txt = state_text or (lambda op, cid: {})
    out = []
    for row in range(H):
        for col in range(W):
            i = row * W + col
            op, cid = grid_op[i], grid_cid[i]
            if not 0 < op < 0x10:
                continue
            seq, seen, cur = [], set(), (op, cid)
            while cur and cur not in seen and len(seq) < 32:
                seen.add(cur)
                rr = _rec(ir, cur[0], cur[1])
                if not rr:
                    break
                seq.append({"op_nibble": cur[0], "cell_id": cur[1], "kind": rr.get("op"),
                            **txt(cur[0], cur[1])})
                nxt = wl_ir._record_edges(rr)
                # the op-A door tail: [b0][X][Y][tgt][op][id] — not an advance field, so
                # _record_edges cannot see it, and it is the only reference a service door has
                if rr.get("op") == "TRANSITION":
                    n = rr.get("next") or {}
                    if 0 < n.get("op", 0xFF) < 0x10:
                        nxt = [(n["op"], n["id"])] + list(nxt)
                cur = next((e for e in nxt if e not in seen), None)
            if len(seq) > 1:
                out.append({"cell": [col, row], "steps": seq,
                            "kind": "hand-over", "length": len(seq)})
    return out
