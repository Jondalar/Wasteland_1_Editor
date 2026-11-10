"""wl_quest.py — the cross-area graph: what one place needs that another place gives.

A Flow is a chain of cells INSIDE one area. A quest is the same thing across the border, and the
game never writes one down: there is no quest table, no flag list, no journal. What exists are two
kinds of reference that happen to reach past the area they sit in, and both are derivable:

  ITEM      an item id is GLOBAL. A LOOT sack in Quartz grants item 67; a COND in Scott's Bar tests
            "do you carry item 67". Nothing links the two — the id does.

  KEYWORD   a keyword id is a TEXT index, so it is area-local and the id links nothing. The WORD
            does: THANATOS is spoken by a man in Scott's Bar and asked for in Quartz House. So the
            edge is matched on the decoded string, not on any number.

            …and the word is often not in the area's TEXT at all. CATERPILLAR — the password that
            opens the Atchison tent — appears in no area's strings anywhere in the game. It is in
            PARAGRAPH 57, and p57 is what the one cell in Scott's Bar that tests for the visa card
            prints. So an area's paragraph references count as things that area says, and without
            that the single most characteristic link in the game is invisible.

Two filters decide what is a story and what is plumbing, and they are the whole trick:

  Tools are not quests.  Ax, Grenade, TNT, Crowbar, Rope, Sledge hammer, Pick ax are "needed" in
      twenty to thirty areas — those are capability checks (dig here, breach that), not something
      somebody asked you to fetch. The cut is not a guess: counting how many areas test for each
      item leaves a hole in the histogram with nothing in it.

          1 area   25 items   |   6 areas   1 item  (Shovel)
          2 areas  12 items   |  ─────────────────── the gap
          3 areas   3 items   |   9 areas   Rope
          4 areas   2 items   |  10 areas   Proton ax
          5 areas   1 item    |  12..33     Ax · Grenade · Pick ax · Crowbar · Sledge · TNT · RPG …

      TARGET_MAX sits in that hole, and every one of the twelve items above it is in fact a weapon
      or a digging tool.

  Single letters are not passwords.  A, B, C, D, E, N, O, R, Y are yes/no and menu picks. A keyword
      needs at least MIN_WORD letters.

What comes out is a LINK — one item or one word, with every place that gives it and every place
that wants it, down to the cell so the editor can jump there. Links are the derived layer and
cannot be edited.

The authored layer sits on top, exactly like the teleport tree: `editor/annotations/quests.json`
names a quest and lists the links that belong to it. That is where "The Four Cylinders" comes from —
four separate items, one door, one story, and only a human knows that. A link nobody has grouped
stays visible as ungrouped, so the naming can never hide a fact the data holds.

Nothing here is used by the cart build. It is a reading aid.
"""
from __future__ import annotations

import re

import wl_cell
import wl_ir

TARGET_MAX = 6          # an item wanted in more areas than this is equipment (see the histogram)
SPREAD_MAX = 5          # …and one lying around in more places than this is not much of a fetch
MIN_WORD = 3            # a keyword shorter than this is a yes/no or a menu letter
_WORD = re.compile(r"\b[A-Z][A-Z0-9]{2,15}\b")
_PARA_REF = re.compile(r"paragraph\s+(\d+)", re.I)
# How the game phrases a CHALLENGE, in its own words. Branch count alone does not separate a lock
# from a conversation: the hideout door in Quartz House accepts three WRONG death-words beside the
# right one, so it has as many destinations as Bobby's chat about his dog. The prompt does separate
# them — a door asks for a word, a person asks what you want to talk about.
_CHALLENGE = re.compile(
    r"password|what.s the word|who sent you|state your business|speak the name|security code|"
    r"combination|enter .*code|gimme the|correct password|do you know today|what is your gift|"
    r"section leader|what.s the word|input |no one gets in", re.I)
_PARAGRAPHS = {}


def paragraphs() -> dict:
    """The printed book — one loader, in wl_cell, shared by every view that needs it."""
    return wl_cell.paragraphs()


def _cells_for(area, op, cid):
    """Every map cell that dispatches (op, cid) — so a link can be clicked open."""
    W = area.width
    return [[i % W, i // W] for i, o in enumerate(area.nibble[:W * area.height])
            if o == op and area.cellid[i] == cid]


def _clean(s):
    return " ".join(str(s or "").split())[:120]


def scan_area(side, aid, name, ir, area) -> dict:
    """One area's contribution to the graph. Pure read; no state."""
    S = [(" ".join(str(x).split())) for x in (ir.get("strings") or [])]

    def txt(i):
        i &= 0x7F
        return S[i] if 0 <= i < len(S) else ""

    here = {"side": side, "area": aid, "name": name}
    gives, wants, asks, says = [], [], [], {}

    for cid, rr in ir["groups"].get("5", {}).items():          # LOOT sacks hand items over
        for it in rr.get("items", []):
            if "qty16" in it:
                continue                                       # money, not an item id
            gives.append({**here, "item": it["item"] & 0x7F, "qty": it.get("qty", 1),
                          "where": f"grp5[{cid}]", "cells": _cells_for(area, 0x5, int(cid))})

    for cid, rr in ir["groups"].get("2", {}).items():          # COND, condition op 1 = HAVE ITEM
        for c in rr.get("conds", []):
            if (c["opb"] >> 5) != 1:
                continue
            wants.append({**here, "item": c["arg"], "where": f"grp2[{cid}]",
                          "prompt": _clean(txt(rr.get("prompt", 0)) or txt(rr.get("success", 0))),
                          "cells": _cells_for(area, 0x2, int(cid))})

    for cid, rr in ir["groups"].get("8", {}).items():          # KEYWORD: which words are asked for
        # A LOCK or a CONVERSATION? Count the DISTINCT places this record's words lead to. All five
        # spellings of IRWIN FINSTER go to one record — that is a lock with aliases. Bobby's five
        # words go to three different answers — that is a conversation, and CAVE there is a topic,
        # not a password. Without this, "CAVE IN!! The explosives brought down the roof!" in the
        # Mine shaft read as the source of Highpool's CAVE, and every keyword that is also an
        # ordinary English word produced a phantom cross-area edge.
        tgt = {(b.get("op"), b.get("id")) for b in
               (k.get("branch") or {} for k in rr.get("keywords", []))
               if b.get("op", 0xFF) < 0x10}
        challenge = bool(_CHALLENGE.search(txt(rr.get("prompt", 0))))
        for k in rr.get("keywords", []):
            w = txt(k["str"]).strip().strip('"').upper()
            if len(w) >= MIN_WORD and w.isalpha():
                asks.append({**here, "word": w, "where": f"grp8[{cid}]", "branches": len(tgt),
                             "challenge": challenge,
                             "prompt": _clean(txt(rr.get("prompt", 0))),
                             "cells": _cells_for(area, 0x8, int(cid))})

    # Everything this area SAYS: its own strings, plus every paragraph it sends the player to.
    # "Read paragraph 57." is this area telling you CATERPILLAR even though the word appears in no
    # area's text at all. The search for which words are in here happens in build(), against the
    # actual keyword set — a word is only a source if something, somewhere, asks for it.
    lines = list(S)
    P = paragraphs()
    for x in S:
        for n in _PARA_REF.findall(x):
            t = P.get(n)
            if t:
                lines.append(f"p{n}: {t}")
    return {"gives": gives, "wants": wants, "asks": asks, "lines": lines, "area": here}


def build(scans) -> dict:
    """The derived graph: one LINK per item id / per word, with its sources and targets.

    `scans` = the per-area dicts from scan_area. Ordering is by the caller, and stable.
    """
    gives, wants, asks = {}, {}, {}
    says = {}
    areas = {}
    for sc in scans:
        areas[(sc["area"]["side"], sc["area"]["area"])] = sc["area"]["name"]
        for g in sc["gives"]:
            gives.setdefault(g["item"], []).append(g)
        for w in sc["wants"]:
            wants.setdefault(w["item"], []).append(w)
        for a in sc["asks"]:
            asks.setdefault(a["word"], []).append(a)
    # WHERE each asked word is SAID — searched case-insensitively, because the game does not shout
    # its passwords: the bum downtown mumbles "Damn Acapulco club..." in ordinary sentence case
    # while the door upstairs asks for ACAPULCO. Restricted to words something actually asks for,
    # so this is a lookup of a few dozen terms rather than a scan for every capitalised token.
    for word in asks:
        rx = re.compile(rf"\b{re.escape(word)}\b", re.I)
        for sc in scans:
            hit = None
            for x in sc.get("lines", []):
                m = rx.search(x)
                # The match must not be all-lowercase. The game writes a password as a proper noun —
                # CATERPILLAR, THANATOS, MUERTE, KESTREL in capitals, "Damn Acapulco club" in title
                # case — while an ordinary noun stays lowercase. Without this, "Even here the cave is
                # very dark" in the Mine shaft counted as the source of Highpool's CAVE keyword, and
                # every common English word among the keywords produced a phantom edge.
                if m and not m.group(0).islower():
                    hit = x
                    break
            if hit:
                says.setdefault(word, []).append({**sc["area"], "line": _clean(hit)})

    links = []
    for iid, tgt in sorted(wants.items()):
        if iid in (0, 0xFF):
            continue                                  # not an item: 0 = none, $FF = the terminator
        tgt_areas = {t["name"] for t in tgt}
        src = gives.get(iid, [])
        src_areas = {s["name"] for s in src}
        if len(tgt_areas) > TARGET_MAX:
            continue                                  # equipment: wanted nearly everywhere
        if src_areas and tgt_areas <= src_areas and len(tgt_areas) == 1:
            continue                                  # given and wanted in the SAME single area
        links.append({"key": f"item:{iid}", "kind": "item", "id": iid,
                      "label": wl_cell._item_name(iid),
                      # an item lying in six different places is not much of a fetch. Shown, but
                      # marked, so the sharp ones sort to the top without anything being dropped.
                      "loose": len(src_areas) > SPREAD_MAX,
                      "sources": src, "targets": tgt,
                      "source_areas": sorted(src_areas), "target_areas": sorted(tgt_areas)})

    for word, tgt in sorted(asks.items()):
        tgt_areas = {t["name"] for t in tgt}
        src = [s for s in says.get(word, []) if s["name"] not in tgt_areas]
        if len(tgt_areas) < 2 and not src:
            continue                                  # asked in one place, said nowhere else
        # A link is sharp when at least one place asking for it is a LOCK — a keyword record whose
        # words all lead to the same answer. A word only ever offered inside a menu of topics is a
        # conversation subject, and a word nobody says elsewhere has no source at all.
        lock = any(t.get("challenge") or t.get("branches", 9) <= 2 for t in tgt)
        links.append({"key": f"kw:{word}", "kind": "keyword", "id": word, "label": word,
                      "loose": not src or not lock,
                      "sources": src, "targets": tgt,
                      "source_areas": sorted({s["name"] for s in src}),
                      "target_areas": sorted(tgt_areas)})
    return {"links": links, "areas": [{"side": k[0], "area": k[1], "name": v}
                                      for k, v in sorted(areas.items())]}


# ─────────────────────────── STEPS — the quest as a story ───────────────────────────
# The links above are DISCOVERY: they find connections nobody would enumerate by hand (THANATOS,
# the four cylinders). They are not the quest. A quest, as Alex describes one, is a sequence:
#
#   talk to Bobby (DOG · REX · CAVE) → he lets slip where the cave is → find the hole → tie the
#   rope → climb down → it is dark, you need a match → the rabid dog lunges → a plank over fresh
#   earth: "My dog Rex, gunned down by vigilante Rangers" → the boy stands up and attacks you
#
# Every one of those steps IS a record in the data — that whole chain is grp1[15] → grp8[1..3] →
# grp1[26] → grp2[6] → grp8[4] → grp10[1] → grp2[7] → grp3[8] → grp1[27] → grp3[10] in Highpool.
# So a step is a REFERENCE to a record, the ORDER is authored, and everything shown about a step is
# rendered from the record each time: rename a foe or rewrite a line and the quest text follows.
# Steps may cross areas freely — that is the whole difference from a Flow.
STEP_RE = re.compile(r"^s(\d)a(\d+):grp(\d+)\[(\d+)\]$")


def step_ref(side, area, grp, cid) -> str:
    return f"s{side}a{area}:grp{grp}[{cid}]"


def _step_text(rr, S) -> list:
    """The lines this record actually puts on screen, in the order the player meets them."""
    def t(i):
        i &= 0x7F
        return S[i] if 0 <= i < len(S) and S[i] else ""
    op = rr.get("op")
    out = []
    if op == "SAY":
        # the whole run, not just its first id — several of the game's most important lines are the
        # third or fourth string of one say
        out += [t(i) for i in (rr.get("messages") or [0])]
    elif op in ("BUMP", "HAZARD", "PATCH", "ENCOUNTER"):
        out.append(t(rr.get("msg", 0)))
    elif op == "COND":
        out += [t(rr.get("prompt", 0)), t(rr.get("success", 0)), t(rr.get("fail", 0))]
    elif op == "KEYWORD":
        out.append(t(rr.get("prompt", 0)))
    elif op == "SERVICE":
        out.append(str(rr.get("name", "")).strip())
    return [x for x in out if x]



def _reached_from(ir, area, grp, cid) -> list:
    """Every record that points at this one, with the edge that does it and where THAT record sits.

    Half of any chain has no cell — a victory state, a loot advance, the two branches of a check.
    Asking "where is it" of such a record has no answer; asking "how do you reach it" does."""
    import wl_ir
    hit = {}
    for g, recs in (ir.get("groups") or {}).items():
        for c, rr in (recs or {}).items():
            for e in wl_ir.record_edges_named(rr):
                if tuple(e["to"]) != (int(grp), int(cid)):
                    continue
                # ONE entry per source record. A COND with three conditions pointing here is one
                # gate, not three ways in; listing it three times says something the data does not.
                k = (int(g), int(c))
                d = hit.setdefault(k, {"grp": int(g), "cid": int(c), "op": rr.get("op"),
                                       "labels": [], "cells": _cells_for(area, int(g), int(c))})
                lab = e.get("label", "")
                cd = e.get("cond")
                if cd:                       # "condition 6" -> "have item: Engine"
                    lab = wl_cell._cond_label(cd["op"], cd["arg"], cd.get("difficulty", 0))
                if lab and lab not in d["labels"]:
                    d["labels"].append(lab)
    return list(hit.values())




def _spends(op: int, arg: int) -> bool:
    """Does passing this entry cost the party the item? From the item descriptor's charge byte."""
    if op not in (1, 5, 6, 7):
        return False
    try:
        import wl_item, wl_cell
        n = wl_item.charges(wl_cell._REPO, arg) if wl_cell._REPO else None
        return bool(n)
    except Exception:                                # noqa: BLE001
        return False


def _role_groups(acts) -> list:
    """Entries grouped by where they lead — a suggestion for splitting a gate into its roles.

    Same target = alternatives, one role. Different targets = different outcomes, and a candidate
    for two steps. Whether a group is `costly` (it spends a consumable) is the strongest hint at
    which one the player reaches second, but it is a hint: the ORDER stays a human's call."""
    import wl_cell
    if len(acts) < 2:
        return []
    g = {}
    for a in acts:
        t = a.get("to") or {}
        key = f"{t.get('op')}/{t.get('id')}"
        g.setdefault(key, {"to": t, "idx": [], "labels": [], "costly": False})
        g[key]["idx"].append(a["i"])
        g[key]["labels"].append(a["label"])
        if a.get("costs"):
            g[key]["costly"] = True
    out = list(g.values())
    if len(out) < 2:
        return []
    # These are OUTCOMES, and that is all the record says. Whether the player meets them at
    # different times is not in the bytes, and I got this wrong once by assuming it was: a gate
    # whose groups differ in cost looked like "find out, then act", so the Jail cell — disarm the
    # alarm, or use the Passkey, or blow it open — was split into three ordered steps. It is not a
    # sequence. It is three ways into one cell, and one of them happens to cost explosives.
    #
    # Highpool's purifier IS a sequence, and the reason is semantic: the text you get from the free
    # entries tells you what the costly one needs. A human reads that. The bytes carry the groups
    # and the costs; the ordering stays a judgement, so nothing here asserts one.
    for x in out:
        x["kind"] = "outcomes"
    return out


def resolve_step(ref, load, picked=None) -> dict:
    """One authored step → what the data says about it now. `load(side, area)` → (ir, area, name)."""
    m = STEP_RE.match(str(ref or ""))
    if not m:
        return {"ref": ref, "ok": False, "why": "not a step reference (s1a10:grp1[15])"}
    side, aid, grp, cid = (int(x) for x in m.groups())
    try:
        ir, area, name = load(side, aid)
    except Exception as e:                                       # noqa: BLE001
        return {"ref": ref, "ok": False, "why": f"area not readable: {e}"}
    rr = ir["groups"].get(str(grp), {}).get(str(cid))
    if rr is None:
        raw = ir.get("opaque", {}).get(str(grp), {}).get(str(cid))
        return {"ref": ref, "ok": raw is not None, "side": side, "area": aid, "area_name": name,
                "grp": grp, "cid": cid, "op": "RAW" if raw else None,
                "why": None if raw else f"grp{grp}[{cid}] does not exist in this area any more",
                "text": [], "cells": [], "next": []}
    S = [" ".join(str(x).split()) for x in (ir.get("strings") or [])]
    lines = _step_text(rr, S)
    d = {"ref": ref, "ok": True, "side": side, "area": aid, "area_name": name,
         "grp": grp, "cid": cid, "op": rr["op"], "text": lines,
         # "Read paragraph 57." resolved to what paragraph 57 SAYS — the plot lives there, so a
         # step that shows only the pointer is showing nothing
         "paragraphs": wl_cell.paragraph_refs(lines),
         "cells": _cells_for(area, grp, cid),
         # HOW you get here, when nothing on the map dispatches it. Steps 3 and 4 of The Water
         # Purifier are the two halves of a check — you read them by passing the COND at (28,16),
         # and the step showed no coordinate at all because it genuinely has none. "Nowhere" is the
         # wrong answer; "through the gate on that square" is the right one.
         "reached_from": _reached_from(ir, area, grp, cid),
         "next": [step_ref(side, aid, g, c) for k, (g, c) in
                  ((k, v) for k, v in _refs(rr) if k == "rec")]}
    if rr["op"] == "ENCOUNTER":
        # the FIRST OCCUPIED slot, not slot 0: an encounter can leave the front slots empty, and
        # taking slot 0 blindly reported "None× None" for the rabid dog that is sitting in slot 2
        sl = next((x for x in (rr.get("slots") or []) if x.get("foe")), None)
        fid = (sl or {}).get("foe")
        fo = ir["foes"][fid - 1] if fid and fid <= len(ir["foes"]) else None
        d["foe"] = {"name": (fo or {}).get("name"), "portrait": (fo or {}).get("portrait_str"),
                    "count": (sl or {}).get("count")}
    if rr["op"] == "KEYWORD":
        d["keywords"] = [S[k["str"] & 0x7F] if (k["str"] & 0x7F) < len(S) else str(k["str"])
                         for k in rr.get("keywords", [])]
    if rr["op"] == "TRANSITION":
        d["to_area"] = rr.get("target")
    if rr["op"] == "PATCH":
        d["patches"] = len(rr.get("entries") or [])
        # WHICH squares, and what they become. "rewrites 3 cells" is the shape of the thing without
        # any of its meaning: this is the ambush at the dig, three hostile groups placed around a
        # party that is standing in the middle of them. A patch is where a quest turns.
        ent = []
        for e in rr.get("entries", []):
            tgt = ir["groups"].get(str(e["op"]), {}).get(str(e["id"])) if 0 < e["op"] < 0x10 else None
            ent.append({"x": e["dx"], "y": e["dy"],
                        # bit0 = party-relative: the offset is from where the party is STANDING,
                        # which is how the Atchison tent goes quiet the moment you hand the shovel over
                        "relative": bool(e["flags"] & 0x01),
                        "op": e["op"], "id": e["id"],
                        "becomes": (tgt or {}).get("op") if tgt else ("empty" if not e["op"] else None)})
        d["rewrites"] = ent
    if rr["op"] == "COND":
        # the human-readable gate ("PERCEPTION 5 · HAVE Rope"), through the one reader the cell
        # inspector already uses — a second condition decoder is exactly the drift this project keeps
        # paying for
        # from the IR, not from a byte walk with a four-entry cap — see cond_summary_ir. This is
        # why step 2 of The Water Purifier used to read "trade record (shop wares)" instead of
        # naming the six things you can do at that machine.
        d["gate"] = wl_cell.cond_summary_ir(rr)
        # A COND record can play more than ONE ROLE, at different times in the story. Highpool's
        # purifier is the clean case: the skill entries are how you find out WHAT the machine is
        # and what it lacks; the Engine entry is what you do about it, and it only means anything
        # after you have done one of the others. The record is one record, but the quest passes
        # through it twice — so a step may name a SUBSET of its conditions, and the same record
        # appears again later with the rest. Without that the quest reads as if you could walk up
        # with an Engine you had no reason to be carrying.
        acts = [{"i": i,
                 "label": wl_cell._cond_label(c["opb"] >> 5, c["arg"], c["opb"] & 0x1F),
                 # the CHARGES byte, not the wording — "requires: Passkey · keeps it" was being
                 # read as costly because the sentence contains the word "uses"
                 "costs": _spends(c["opb"] >> 5, c["arg"]),
                 "never": wl_cell.cond_impossible(c["opb"] >> 5, c["arg"]),
                 "to": (rr.get("branches") or [{}])[i] if i < len(rr.get("branches") or []) else None}
                for i, c in enumerate(rr.get("conds") or [])]
        if picked is not None:
            keep = set(picked)
            d["actions"] = [a for a in acts if a["i"] in keep]
            d["role"] = True                       # this step is one role of the record, not all of it
            d["gate"] = " · ".join(a["label"] for a in d["actions"]) or "always"
        else:
            d["actions"] = acts
            # What the RECORD can tell us about splitting, and no more. Entries that branch to the
            # same place are alternatives — pick the lock or force it, the door opens either way.
            # Entries that branch elsewhere are a different outcome, and when one group costs a
            # consumable and the other does not, that is the shape of "find out what it needs" then
            # "come back and do it". 48 records in the game look like this.
            #
            # What the record CANNOT say is which group comes FIRST. In Highpool a human reads
            # "could be fixed with a new engine" and knows; the bytes do not. So this is offered,
            # never applied.
            d["role_groups"] = _role_groups(acts)
    return d


def _refs(rr):
    """Local import shim so this module does not depend on wl_object at import time."""
    import wl_object
    return wl_object.refs_of(rr)


def claims(authored) -> dict:
    """{(side, area): {(grp, cid): [quest id …]}} — which records the authored quests lay claim to.

    This is what makes the coverage question answerable, and the Flow view separable: a script event
    is "part of a story" exactly when one of the records it dispatches is a step of one. Not every
    event needs to be — most of a town is furniture, signs and bump messages — but knowing WHICH is
    the difference between a flow list you can read and one you scroll past.
    """
    out = {}
    for qid, q in (authored or {}).items():
        for st in (q.get("steps") or []):
            ref = st if isinstance(st, str) else st.get("ref")
            m = STEP_RE.match(str(ref or ""))
            if not m:
                continue
            side, aid, grp, cid = (int(x) for x in m.groups())
            out.setdefault((side, aid), {}).setdefault((grp, cid), [])
            if qid not in out[(side, aid)][(grp, cid)]:
                out[(side, aid)][(grp, cid)].append(qid)
    return out


def coverage(claims_map, per_area) -> dict:
    """How much of the script the questlines actually touch.

    `per_area` = {(side, area): (name, total records, total dispatchable cells)}. The number is not
    a target — most records are scenery, and a game where every sign belonged to a quest would be a
    worse game. It is here so the shape of the answer is visible: which areas are understood as
    stories and which are still just a pile of records.
    """
    rows, t_rec, t_cl, t_area = [], 0, 0, 0
    for key, (name, nrec, ncell) in sorted(per_area.items()):
        cl = claims_map.get(key, {})
        rows.append({"side": key[0], "area": key[1], "name": name, "records": nrec,
                     "claimed": len(cl), "cells": ncell,
                     "quests": sorted({q for v in cl.values() for q in v})})
        t_rec += nrec
        t_cl += len(cl)
        t_area += 1 if cl else 0
    return {"areas": rows,
            "total": {"areas": len(per_area), "areas_with_a_story": t_area,
                      "records": t_rec, "claimed": t_cl,
                      "pct": round(100.0 * t_cl / t_rec, 1) if t_rec else 0.0}}


def order(authored) -> dict:
    """Resolve the `after:` prerequisites into a running order, and say what does not resolve.

    Wasteland has no quest object and therefore no quest ORDER either — but the main line has one,
    and it is the only chain in the game where somebody sends you onward: Brygo → Charmaine →
    Needles → the sewers → Max → the Sleeper Base → Darwin → Cochise. Everything else you find.
    So a quest may name the quests it needs first, and this turns that into a sequence.

    Returns {id: depth} plus the ids whose prerequisites are missing or circular — reported rather
    than silently dropped, because a broken dependency is the interesting case.
    """
    ids = set(authored)
    depth, bad, cyc = {}, {}, set()

    def resolve(qid, seen):
        if qid in depth:
            return depth[qid]
        if qid in seen:
            cyc.add(qid)
            return 0
        need = [x for x in (authored.get(qid, {}).get("after") or [])]
        miss = [x for x in need if x not in ids]
        if miss:
            bad[qid] = miss
        d = 0
        for x in need:
            if x in ids:
                d = max(d, resolve(x, seen | {qid}) + 1)
        depth[qid] = d
        return d

    for qid in ids:
        resolve(qid, set())
    return {"depth": depth, "unknown_after": bad, "cycles": sorted(cyc)}


_MARKREF = {}


def ref_for_mark(mark, side, area, load):
    """A mark -> the positional reference the rest of this module still speaks.

    Steps used to carry `s2a34:grp2[0]`, a table position. Since the quest migration they carry a
    MARK — `s2a34/cond000` — which survives an insert where the position does not. Everything
    downstream (resolve_step, the views, the split path) still addresses records positionally, so
    this is the one place the two spellings meet: look the mark up in the area's document and hand
    back where it currently sits.

    Cached per area because a quest pane resolves dozens of steps across a handful of areas."""
    if not mark or side is None or area is None:
        return None
    key = (side, area)
    idx = _MARKREF.get(key)
    if idx is None:
        try:
            ir, _, _ = load(side, area)
        except Exception:                                        # noqa: BLE001
            _MARKREF[key] = {}
            return None
        groups = ir.get("groups") or {}
        if not any(rr.get("ref") for recs in groups.values() for rr in recs.values()):
            import copy
            import wl_marks
            ir = {"groups": copy.deepcopy(groups), "strings": list(ir.get("strings") or [])}
            wl_marks.mark(ir, f"s{side}a{area:02d}")
            groups = ir["groups"]
        idx = {rr["ref"]: f"s{side}a{area}:grp{g}[{cid}]"
               for g, recs in groups.items() for cid, rr in recs.items() if rr.get("ref")}
        _MARKREF[key] = idx
    return idx.get(mark)


def forget_marks(side=None, area=None):
    """Drop the mark index — a save may have renumbered or renamed."""
    if side is None:
        _MARKREF.clear()
    else:
        _MARKREF.pop((side, area), None)


def compose(graph, authored, load=None) -> dict:
    """The quest list: an authored STORY (ordered steps) plus the derived links it touches.

    `authored` = {quest id: {name, notes, steps: [ref | {ref, note}], links: [key …]}}.

    Two layers, and they answer different questions. `steps` is the story — what happens, in order,
    across whatever areas it takes. `links` is what the graph found: which items and words this
    quest hangs on. A link that no quest claims is returned under `ungrouped`; the naming layer is
    allowed to organise the graph, never to hide it. A quest naming a link the graph no longer
    produces, or a step whose record is gone, is reported rather than dropped — that is the
    interesting case (an edit cut the connection), and quietly forgetting it is how a document
    stops describing the thing it is about.
    """
    by_key = {x["key"]: x for x in graph["links"]}
    ordering = order(authored or {})
    claimed, quests = set(), []
    for qid, q in sorted(authored.items(), key=lambda kv: (kv[1].get("order", 999), kv[0])):
        keys = list(q.get("links") or [])
        got = [by_key[k] for k in keys if k in by_key]
        claimed.update(k for k in keys if k in by_key)
        steps = []
        for st in (q.get("steps") or []):
            ref, note = (st, "") if isinstance(st, str) else (st.get("ref"), st.get("note", ""))
            # A migrated beat names its record by MARK, not by position. Translate here so every
            # reader below stays unchanged — without this the step resolved to nothing and the pane
            # rendered all 232 of them as "not placed yet".
            if not ref and isinstance(st, dict) and st.get("records") and load:
                at = st.get("at") or {}
                ref = ref_for_mark(st["records"][0], at.get("side"), at.get("area"), load)
            # The AUTHORED fields come through untouched. Steps used to be nothing but a reference
            # into the shipped game plus a note, so resolving one was the whole job. A DESIGNED step
            # carries its own state — the words, the shape it was cast from, what has been filled in
            # — and it exists before any reference does, which is the entire point of designing top
            # down. Dropping those fields here made a designed step read as a broken reference.
            extra = {k: v for k, v in st.items() if k not in ("ref", "note")} if isinstance(st, dict) else {}
            resolved = (resolve_step(ref, load, (st.get("conds") if isinstance(st, dict) else None))
                        if (load and ref) else {"ref": ref, "ok": None})
            steps.append({**resolved, "note": note, **extra})
        areas = sorted({a for x in got for a in x["source_areas"] + x["target_areas"]}
                       | {s["area_name"] for s in steps if s.get("area_name")})
        quests.append({"id": qid, "name": q.get("name") or qid, "notes": q.get("notes", ""),
                       "links": got, "missing": [k for k in keys if k not in by_key],
                       "steps": steps, "broken": [s["ref"] for s in steps if s.get("ok") is False],
                       "areas": areas,
                       # the spine: which track this belongs to, what it needs first, and how deep
                       # that puts it in the chain
                       "track": q.get("track", "side"), "after": list(q.get("after") or []),
                       "depth": ordering["depth"].get(qid, 0),
                       "unknown_after": ordering["unknown_after"].get(qid, []),
                       "region": q.get("region", ""), "order": q.get("order", 999)})
    main = sorted([q for q in quests if q["track"] == "main"],
                  key=lambda q: (q["depth"], q["order"]))
    return {"quests": quests, "main": [q["id"] for q in main],
            "cycles": ordering["cycles"],
            "ungrouped": [x for x in graph["links"] if x["key"] not in claimed],
            "areas": graph["areas"]}
