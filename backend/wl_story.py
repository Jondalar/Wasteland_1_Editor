"""wl_story.py — the fold from records to a STORY, and the gate that stands before the bytes.

Two things live here, and they are the two things the architecture doc names as missing.

1. THE FOLD.  A chain of records is not a story. Seven records carry the Missing Toaster, but a
   player experiences four things: a fight, a second fight, a find, a conversation. The other three
   — two CELL_PATCHes and an OBJ — are not events at all; they are *how one event hands over to the
   next*. Drawn as boxes they read like steps the player takes. Drawn as arrows they read like what
   they are. `fold()` does exactly that split and nothing else: BEAT records become rows, PLUMBING
   records become the edge between two rows, and the spawn coordinate rides on the edge, where it
   belongs, so it exists ONCE instead of once per coupled patch entry.

2. THE GATE.  Three things have to hold before a record may be emitted, and today all three are
   checkable but none of them is checked in one place:
     * the edge GRAMMAR — an ENCOUNTER handing over to another ENCOUNTER does not occur in the
       shipped game (0 of 592 edges), and the one time it did occur it was mine and it hung;
     * the area BUDGET — a text id is SEVEN BITS, so nothing above 127 is referenceable, and no
       amount of correct serializing rescues a 129th string;
     * the FIT — the serializer may legitimately change a record's length, and a blob that grew has
       to still land in its bank.
   The grammar is MEASURED from the shipped areas, never asserted: `edge_grammar()` counts real
   edges in real records, and `validate()` only reports what that count says.

Nothing in here writes. The fold is a view and the gate is an opinion; `wl_ir.ir_to_record` stays
the only path to bytes.
"""

import wl_cell
import wl_ir

# ---------------------------------------------------------------------------------------------
# What the player notices, and what merely wires
# ---------------------------------------------------------------------------------------------
#
# The op nibble is the record's kind (wl_cell.op_record_ir has the byte layouts). Splitting it this
# way is not a matter of taste — it follows what the engine DOES with the record:
#
#   BEAT      the engine shows something, asks something, or resolves something. The turn stops
#             here: a fight is fought, a sack is opened, a speech is paged through.
#   PLUMBING  the engine rewrites state and moves straight on. CELL_PATCH writes op-nibble+id into
#             OTHER cells (never tiles), OBJ overrides one cell's drawn tile — the only runtime tile
#             change there is — and op 0 means "this cell becomes empty", which 265 shipped loot
#             sacks use as their last act. None of the three is a thing that HAPPENS to the party.

BEAT_OPS = {
    0x1: "SAY",         0x2: "COND",     0x3: "ENCOUNTER",
    0x5: "LOOT",        0x6: "SERVICE",  0x8: "KEYWORD",
    0x9: "HAZARD",      0xA: "TRANSITION", 0xB: "BUMP",
}
PLUMB_OPS = {0x4: "OBJ", 0xC: "PATCH"}

# How an edge reads, keyed by the record it leaves. Taken from the field labels in
# `wl_cell.record_fields` so the two cannot drift; this is only the fallback wording.
EDGE_FALLBACK = "then"


def is_beat(nibble: int) -> bool:
    return int(nibble) in BEAT_OPS


def _rec(ir, grp, cid):
    return (ir.get("groups") or {}).get(str(grp), {}).get(str(cid))


def _edge_label(rr) -> str:
    """The wording of the arrow LEAVING this record — the label of its own edge field.

    Reusing `record_fields` here is the point: the arrow the reader sees and the form field the
    editor writes are the same thing, so renaming one renames the other."""
    for f in wl_cell.record_fields(rr):
        if f.get("kind") == "edge":
            return f.get("label") or EDGE_FALLBACK
    return EDGE_FALLBACK


def _effects(rr, ir) -> list:
    """What a plumbing record DOES, as structured effects the UI can render on the arrow.

    A PATCH entry is `(x, y) becomes (op, id)`; op 0 is not a reference, it is the cell going empty
    — that distinction is what `wl_ir._record_edges` used to get wrong, and it cut every chain that
    cleared a square. An OBJ is one drawn tile."""
    out = []
    if rr.get("op") == "PATCH":
        for e in rr.get("entries", []):
            tgt = _rec(ir, e["op"], e["id"]) if 0 < e["op"] < 0x10 else None
            out.append({"kind": "cell", "x": e["dx"], "y": e["dy"],
                        "to_op": e["op"], "to_id": e["id"],
                        "to_kind": (tgt or {}).get("op") if tgt else ("empty" if not e["op"] else None)})
    elif rr.get("op") == "OBJ":
        # BYTE 1, not byte 0. `draw_map_cell` renders `grp4[cellid][1] & $7F` over the map tile with
        # bit7 = solid (OP4_OBJECTS.md); byte 0 is the message. TREX's droid marker is `00 02` — a
        # message of 0 and tile $02, and reading byte 0 reported it as "tile $00".
        if "tile" in rr:
            out.append({"kind": "tile", "tile": rr["tile"] & 0x7F, "solid": bool(rr.get("solid"))})
            if rr.get("msg", 0) & 0x7F:
                out.append({"kind": "msg", "msg": rr["msg"] & 0x7F})
        else:                                        # legacy document, still the raw pair
            data = wl_ir._unhex(rr.get("data", "hex:"))
            if len(data) > 1:
                out.append({"kind": "tile", "tile": data[1] & 0x7F, "solid": bool(data[1] & 0x80)})
            if data and data[0] & 0x7F:
                out.append({"kind": "msg", "msg": data[0] & 0x7F})
    return out


def fold(chain: dict, ir: dict) -> dict:
    """One walked chain → beats, with the plumbing folded onto the arrows between them.

    `chain` is what `GET /chains` already returns: {cell, steps:[{grp,cid,op,label,fields}]}. The
    fold adds no new source of truth — it only decides which of those steps is a row and which is an
    arrow, and then answers the question the flat list could not: WHERE does the next beat stand?
    It stands wherever the patch entry on the incoming arrow put it. That is why a spawn coordinate
    needs to exist once rather than twice."""
    beats, pending = [], []
    for st in chain.get("steps", []):
        if is_beat(st["grp"]):
            beats.append({"step": st, "via": pending})
            pending = []
        else:
            pending.append(st)

    out, prev = [], None
    for i, b in enumerate(beats):
        st = b["step"]
        via, effects = [], []
        for p in b["via"]:
            rr = _rec(ir, p["grp"], p["cid"])
            via.append({"grp": p["grp"], "cid": p["cid"], "op": p.get("op")})
            if rr:
                effects += _effects(rr, ir)

        # WHERE the beat stands. Three cases, and they are the engine's three, not a heuristic:
        #   chain    the first beat sits on the square the chain was walked from;
        #   edge     a patch entry on the incoming arrow planted it somewhere else — that entry may
        #            name the beat itself, or name a PLUMBING record on the same arrow (TREX's
        #            sack patches (25,25) to the OBJ marker, and the SERVICE hangs off the OBJ, so
        #            the coordinate belongs to the hop, not to either record alone);
        #   same     no patch at all — a plain advance replaces the CELL'S OWN state, so the beat
        #            inherits the square it came from. That is the common shipped shape and the
        #            reason a fold that only looked for patch entries left most beats coordinate-less.
        cell, owner = None, None
        if i == 0:
            cell, owner = list(chain.get("cell") or []) or None, "chain"
        else:
            hop = {(v["grp"], v["cid"]) for v in via} | {(st["grp"], st["cid"])}
            for e in effects:
                if e.get("kind") == "cell" and (e.get("to_op"), e.get("to_id")) in hop:
                    cell, owner = [e["x"], e["y"]], "edge"
                    break
            if cell is None:
                cell, owner = (out[-1]["cell"] if out else None), "same"

        edge = None
        if i > 0:
            src = _rec(ir, prev["grp"], prev["cid"])
            edge = {"label": _edge_label(src) if src else EDGE_FALLBACK,
                    "via": via, "effects": effects}
        elif via:                                  # plumbing BEFORE the first beat — rare, but real
            edge = {"label": "before", "via": via, "effects": effects}

        out.append({"grp": st["grp"], "cid": st["cid"], "op": st.get("op"),
                    "label": st.get("label", ""), "fields": st.get("fields", []),
                    "cell": cell, "cell_owner": owner, "in_edge": edge})
        prev = st

    trailing = []
    for p in pending:                              # plumbing after the last beat: still an arrow
        rr = _rec(ir, p["grp"], p["cid"])
        trailing.append({"grp": p["grp"], "cid": p["cid"], "op": p.get("op"),
                         "effects": _effects(rr, ir) if rr else []})

    return {"cell": chain.get("cell"), "beats": out, "trailing": trailing,
            "records": len(chain.get("steps", [])), "shown": len(out)}


# ---------------------------------------------------------------------------------------------
# The TREE — the same fold, but following every branch instead of only the first
# ---------------------------------------------------------------------------------------------
#
# `fold()` works on a chain that was already walked as a single path, which is what the old walker
# produced: at every record it took the FIRST unvisited edge and dropped the rest. That is fine for
# the ninety per cent of the game that is linear, and wrong exactly where the interesting authoring
# happens — a KEYWORD with three keywords forks three ways, a COND goes one way on a pass and
# another on a fail. Drawn on a canvas those forks are why the canvas existed. Drawn as an outline
# they are indentation, which is cheaper to read and possible to edit.
#
# So: walk the reference graph as a TREE from a root, folding plumbing onto the edges exactly as
# before. Where the tree would revisit a record, stop and say so rather than recursing — a rejoin
# is real information ("this is also reached from over there"), and it is the one thing a canvas
# shows that a list has to state in words.

MAX_DEPTH = 24


def _named(ir, rr) -> list:
    """Named edges with the keyword ids resolved to the actual WORDS.

    `record_edges_named` cannot do this itself — it sees one record, not the area's string pool —
    and a fork labelled "on keyword #37" tells a reader nothing about which branch is which."""
    strings = ir.get("strings") or []
    out = wl_ir.record_edges_named(rr)
    for e in out:
        sid = e.get("str_id")
        if sid is not None and (sid & 0x7F) < len(strings):
            word = " ".join(str(strings[sid & 0x7F]).split())[:24]
            if word:
                e["label"] = f"on “{word}”"
        # a COND branch knows which condition it belongs to — "condition 6" names nothing, and the
        # arrow into the Engine text is the most consequential one in the quest
        c = e.get("cond")
        if c:
            e["label"] = wl_cell._cond_label(c["op"], c["arg"], c.get("difficulty", 0))
    return out


def _hop(ir, start, seen_local):
    """Follow plumbing from `start` until a BEAT is reached, collecting what the plumbing does.

    Returns (beat_ref, via, effects, forks). `forks` is non-empty when a plumbing record itself
    branches — a CELL_PATCH that plants two different states is the common case, and it is a fork
    of the story even though no beat sits on the patch itself."""
    via, effects, cur = [], [], start
    guard = 0
    while cur and guard < 8:
        guard += 1
        if is_beat(cur[0]):
            return cur, via, effects, []
        rr = _rec(ir, cur[0], cur[1])
        if not rr or cur in seen_local:
            return None, via, effects, []
        seen_local.add(cur)
        via.append({"grp": cur[0], "cid": cur[1], "op": rr.get("op")})
        effects += _effects(rr, ir)
        nxt = [e for e in _named(ir, rr) if tuple(e["to"]) not in seen_local]
        if len(nxt) > 1:
            return None, via, effects, nxt
        cur = tuple(nxt[0]["to"]) if nxt else None
    return None, via, effects, []



def _merge_edges(edges) -> list:
    """One edge per destination. Entries that lead to the same record are ways of doing the same
    thing, not different things; their labels join with "or"."""
    out, by = [], {}
    for e in edges:
        k = tuple(e["to"])
        if k in by:
            prev = by[k]
            if e.get("label") and e["label"] not in prev["_labels"]:
                prev["_labels"].append(e["label"])
                prev["label"] = " or ".join(prev["_labels"])
            continue
        d = dict(e); d["_labels"] = [e.get("label", "")] if e.get("label") else []
        by[k] = d
        out.append(d)
    return out


def _node(ir, ref, edge, cell, owner, seen, depth):
    rr = _rec(ir, ref[0], ref[1])
    if not rr:
        return None
    key = f"{ref[0]}/{ref[1]}"
    node = {"grp": ref[0], "cid": ref[1], "op": rr.get("op"),
            "fields": wl_cell.record_fields(rr), "cell": cell, "cell_owner": owner,
            "in_edge": edge, "children": [], "rejoin": False}
    if key in seen or depth >= MAX_DEPTH:
        node["rejoin"] = True                    # already told elsewhere in this tree — say it, don't repeat it
        return node
    seen.add(key)
    # Several entries can lead to the SAME place — Highpool's machine has three ways to work out
    # what it is, all printing the same sentence. Following each as its own branch drew that
    # sentence three times and called the chain twelve beats long. One child per destination, with
    # the ways to reach it joined on the arrow.
    for e in _merge_edges(_named(ir, rr)):
        tgt, via, effects, forks = _hop(ir, tuple(e["to"]), set())
        sub = {"label": e["label"], "key": e["key"], "via": via, "effects": effects}
        if tgt:
            # where the child stands: a patch entry on this hop, the coordinate the edge itself
            # names, or — failing both — the same square, because a plain advance replaces the
            # cell's own state
            hop = {(v["grp"], v["cid"]) for v in via} | {tgt}
            c, own = None, None
            for x in effects:
                if x.get("kind") == "cell" and (x.get("to_op"), x.get("to_id")) in hop:
                    c, own = [x["x"], x["y"]], "edge"
                    break
            if c is None and e.get("cell"):
                c, own = list(e["cell"]), "edge"
            if c is None:
                c, own = cell, "same"
            kid = _node(ir, tgt, sub, c, own, seen, depth + 1)
            if kid:
                node["children"].append(kid)
        elif forks:
            # plumbing that branches: each of its edges becomes its own child of this beat
            for f in forks:
                t2, v2, e2, _ = _hop(ir, tuple(f["to"]), set())
                if not t2:
                    continue
                # the label stays the one the BEAT gave this arrow ("after taking it"), not the
                # patch entry's own wording. Using the entry's produced
                #   "(8,13) becomes: (8,13) becomes FIGHT · (1,29) becomes OBJ · …"
                # — the arrow announcing the first of the effects it is already listing.
                sub2 = {"label": e["label"], "key": f["key"], "via": via + v2, "effects": effects + e2}
                c2 = list(f["cell"]) if f.get("cell") else cell
                kid = _node(ir, t2, sub2, c2, "edge" if f.get("cell") else "same", seen, depth + 1)
                if kid:
                    node["children"].append(kid)
        elif via or effects:
            node["children"].append({"grp": None, "cid": None, "op": None, "fields": [],
                                     "cell": cell, "cell_owner": "same", "in_edge": sub,
                                     "children": [], "rejoin": False, "terminal": True})
    return node


def tree(ir: dict, root_op: int, root_cid: int, cell=None) -> dict:
    """One chain as a TREE of beats, plumbing folded onto the edges. Branches become children."""
    seen = set()
    start, via, effects, forks = _hop(ir, (root_op, root_cid), set())
    if not start:
        return None
    edge = {"label": "", "key": "", "via": via, "effects": effects} if via else None
    return _node(ir, start, edge, list(cell) if cell else None, "chain", seen, 0)


def flatten(node, depth=0, out=None) -> list:
    """The tree as rows, depth-tagged — an outline is a list with indentation, nothing more.

    Indentation marks a FORK, not a step. A linear chain of four beats is four rows at the same
    level, because nothing about it branches; indenting each one would spend the whole width on the
    ninety per cent of the game that is a straight line and leave none for the ten per cent that
    actually splits. Only a node with more than one child pushes its children in."""
    out = [] if out is None else out
    if not node:
        return out
    out.append({**{k: v for k, v in node.items() if k != "children"}, "depth": depth,
                "forks": len(node.get("children", []))})
    kids = node.get("children", [])
    for k in kids:
        flatten(k, depth + (1 if len(kids) > 1 else 0), out)
    return out


def roots(ir: dict) -> list:
    """Every cell that DISPATCHES a record, as (op, cid, [col,row]).

    A root is a square the player can stand on. That is the whole test — and it used to be narrowed
    by "…and nothing else points at it", which sounds right and is not: Highpool's water purifier at
    (28,16) is a square you walk onto AND the place grp1[56]/[57] send you back to after you have
    worked out what it is. Being returned to does not stop it from being where you start; the filter
    dropped the one cell the whole quest is about, and the Events view simply did not list it.

    Records with no cell of their own are still not roots — they cannot be, since this walks the
    map grid. That was the real intent all along."""
    W = ir["meta"]["width"]
    gop, gcid = ir["grid"]["op"], ir["grid"]["cellid"]
    out = []
    for i, (op, cid) in enumerate(zip(gop, gcid)):
        if 0 < op < 0x10:
            out.append((op, cid, [i % W, i // W]))
    seen, uniq = set(), []
    for op, cid, cell in out:                    # one entry per record, not per square it sits on
        if (op, cid) in seen:
            continue
        seen.add((op, cid))
        uniq.append({"op": op, "cid": cid, "cell": cell})
    return uniq


# ---------------------------------------------------------------------------------------------
# The vocabulary — so a form asks for a THING, not for a byte
# ---------------------------------------------------------------------------------------------

def vocab(ir: dict, area=None, label=None) -> dict:
    """Everything a form needs to offer names instead of numbers, for ONE area.

    Two fields were still asking for raw bytes long after everything around them had names.
    `item 93` is Jewelry, and the game's whole item table is right there. `after taking it [12][10]`
    is the op nibble and the record index — that reads as a memory address, and the thing it points
    at is a CELL_PATCH that happens to be the most consequential half of the loot sack: it decides
    what the square becomes. Neither number is a thing a designer thinks in.

    Built per area because the record half IS per area; the item table is global but travels along
    so the frontend needs one payload rather than two."""
    recs = {}
    for g, rr_map in (ir.get("groups") or {}).items():
        entries = {}
        for cid, rr in (rr_map or {}).items():
            txt = ""
            if label:
                try:
                    txt = (label(int(g), int(cid)) or {}).get("text", "") or ""
                except Exception:                     # noqa: BLE001 — a label is a nicety, never fatal
                    txt = ""
            entries[cid] = {"op": rr.get("op"), "label": " ".join(str(txt).split())[:52]}
        if entries:
            recs[g] = entries
    return {"items": {str(k): v for k, v in wl_cell._ITEM_NAMES.items()},
            "records": recs,
            # what an edge's op nibble MEANS, in the story's words rather than the file's. 0 and
            # $FF are not records at all and are the two answers a designer picks most often.
            "edge_ops": ([{"op": 0, "name": "— the cell goes empty"}]
                         + [{"op": n, "name": f"{BEAT_OPS.get(n) or PLUMB_OPS.get(n)} · grp{n}"}
                            for n in sorted(set(BEAT_OPS) | set(PLUMB_OPS)) if str(n) in recs]
                         + [{"op": 255, "name": "— stop here"}])}


# ---------------------------------------------------------------------------------------------
# The measured grammar
# ---------------------------------------------------------------------------------------------

def area_edges(ir: dict) -> list:
    """Every record→record edge of one area as (src_kind, dst_kind).

    Uses `wl_ir._record_edges`, the same walker the chain view uses, so the grammar counts exactly
    the edges the editor can also follow — a grammar measured on a different graph than the one
    being validated would be worse than none."""
    out = []
    for g, recs in (ir.get("groups") or {}).items():
        for cid, rr in (recs or {}).items():
            src = rr.get("op")
            if not src:
                continue
            for (op, rid) in wl_ir._record_edges(rr):
                dst = _rec(ir, op, rid)
                if dst and dst.get("op"):
                    out.append((src, dst["op"]))
    return out


def edge_grammar(irs) -> dict:
    """Count every src→dst pair over a set of areas. `irs` is an iterable of decoded IRs.

    The result is the whole answer to "is this shape one the game ever ships?" — including the
    zeroes, which are the interesting half."""
    counts, kinds = {}, set()
    for ir in irs:
        for src, dst in area_edges(ir):
            counts[f"{src}>{dst}"] = counts.get(f"{src}>{dst}", 0) + 1
            kinds.update((src, dst))
    return {"counts": counts, "kinds": sorted(kinds), "total": sum(counts.values())}


# ---------------------------------------------------------------------------------------------
# The budget and the gate
# ---------------------------------------------------------------------------------------------

# A text id is seven bits wide (`wl1-text-id-7-bit`): the engine masks the id with $7F everywhere it
# resolves one, so a 129th string can be STORED but never NAMED by a record.
#
# Eleven shipped areas hold longer pools anyway — Stagecoach Inn has 192 — and that is not a defect.
# Strings past 127 are reached by POINTER rather than by id: the 34-string combat block sits at the
# end of the pool and `desc+$37..+$5B` points straight at it, which is exactly why Stagecoach Inn can
# park it at 156. So a long pool is not an error to report; the actionable fact is narrower and
# forward-looking: in such an area a NEW string cannot be given an id, so nothing new can refer to it.
TEXT_ID_MAX = 128


def budget(ir: dict, blob_len: int = None, baseline_len: int = None) -> dict:
    """What this area has already spent of what it cannot exceed.

    Step 2 of the authoring flow — picking blueprints — ALLOCATES these. Showing them only at
    serialize time is what makes an area quietly fill up while three steps are already wired."""
    strings = ir.get("strings") or []
    foes = ir.get("foes") or []
    groups = {g: len(r or {}) for g, r in (ir.get("groups") or {}).items()}
    b = {
        # `free` is the only number that decides anything: how many more strings this area can hand
        # out ids for. `past_id` is descriptive — those exist and work, by pointer.
        "strings": {"used": len(strings), "max": TEXT_ID_MAX,
                    "free": max(0, TEXT_ID_MAX - len(strings)),
                    "past_id": max(0, len(strings) - TEXT_ID_MAX)},
        "foes": {"used": len(foes)},
        "records": {"per_group": groups, "total": sum(groups.values())},
    }
    if blob_len is not None:
        b["bytes"] = {"now": blob_len, "baseline": baseline_len,
                      "delta": (blob_len - baseline_len) if baseline_len is not None else None}
    return b


def validate(ir: dict, grammar: dict = None, bud: dict = None) -> list:
    """The gate between wiring and bytes. Returns findings, most severe first; empty means go.

    Deliberately opinionated only where there is a measurement behind the opinion. An edge that the
    shipped game never uses is reported as `unseen`, not as `illegal` — the game is the authority on
    what works, but 43 areas are not a proof of impossibility, and saying so keeps the tool honest.
    """
    found = []
    counts = (grammar or {}).get("counts") or {}

    if counts:
        for src, dst in area_edges(ir):
            n = counts.get(f"{src}>{dst}", 0)
            if n == 0:
                found.append({"level": "warn", "kind": "unseen-edge",
                              "text": f"{src} → {dst} does not occur in the shipped game",
                              "detail": "not proof it cannot work — but every one so far that "
                                        "looked like this was a bug"})
    b = bud or budget(ir)
    s = b.get("strings") or {}
    if not s.get("free"):
        found.append({"level": "error", "kind": "text-ids-exhausted",
                      "text": f"no free text id — the pool already holds {s.get('used')} strings",
                      "detail": "a new string can be stored but no record could name it: the engine "
                                "masks every text id with $7F. Say it in an existing string, or "
                                "author the step in another area."})
    elif s["free"] <= 8:
        found.append({"level": "warn", "kind": "text-ids-tight",
                      "text": f"only {s['free']} text id(s) left of {TEXT_ID_MAX}"})
    if s.get("past_id"):
        found.append({"level": "info", "kind": "pointer-strings",
                      "text": f"{s['past_id']} string(s) sit past id 127",
                      "detail": "reached by pointer, not by id — the combat block at desc+$37 is "
                                "the usual reason. Shipped and working; just not nameable."})

    by = b.get("bytes") or {}
    if by.get("delta") and by["delta"] > 0:
        found.append({"level": "info", "kind": "grew",
                      "text": f"record blob is {by['delta']} byte(s) larger than baseline",
                      "detail": "fine until its bank is full — the build's place() decides"})

    # A record nothing reaches is not an error (unbound records are how a quest is drafted), but it
    # is the single most common reason a freshly authored step "does nothing" in game.
    reached = {f"{op}/{rid}" for g, recs in (ir.get("groups") or {}).items()
               for cid, rr in (recs or {}).items()
               for (op, rid) in wl_ir._record_edges(rr)}
    grid = ir.get("grid") or {}
    for op, cid in zip(grid.get("op") or [], grid.get("cellid") or []):
        if 0 < op < 0x10:
            reached.add(f"{op}/{cid}")
    orphans = [f"grp{g}[{cid}]" for g, recs in (ir.get("groups") or {}).items()
               for cid in (recs or {}) if f"{g}/{cid}" not in reached]
    if orphans:
        found.append({"level": "info", "kind": "unreached",
                      "text": f"{len(orphans)} record(s) nothing points at",
                      "detail": ", ".join(orphans[:12]) + ("…" if len(orphans) > 12 else "")})

    order = {"error": 0, "warn": 1, "info": 2}
    return sorted(found, key=lambda f: order.get(f["level"], 3))
