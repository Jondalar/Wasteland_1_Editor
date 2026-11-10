"""wl_marks.py — references by NAME instead of by table position (IR_NAMED_REFERENCES.md).

The IR stores the game the way the engine addresses it: `{"op": 4, "id": 5}` is "group 4, record 5"
and `"prompt": 67` is "string 67 of this area". Measured over the 43 shipped areas there are 8855
such pointers, and every one names a slot. That is why the IR can only replace, never insert or
delete — one record more shifts its successors and nothing pulls the pointers along.

Addresses have been free for a while: `ir_to_record` lays the blob out from scratch on every save
and moves 149 of Highpool's 155 records without breaking anything, because it rewrites the pointer
tables. This module gives the NUMBER the same freedom, by putting a name in front of it.

    mark(ir, area)      numbers → marks     one-time, on import
    resolve(ir)         marks → numbers     before every emit

`resolve` is the numbering pass the emitter gains: collect the marks, hand out numbers, resolve.
Then `ir_to_record` runs unchanged.

## What is NOT marked, and why

The 31655 cell→record links keep their numbers. A cell and its record always sit in the same
document and are always renumbered in the same pass, so that link never has to survive numbering on
its own — and at 56% occupancy the grid is not sparse, so every map representation measured LARGER
than the two arrays it would replace. The obligation that comes with it: **the numbering pass
rewrites the grid**, and `resolve` does exactly that.

## bit7 rides beside the mark, not inside it

`COND.prompt` and friends carry a flag in bit 7 — clear on 708 of 719, set on 11, and on
`KEYWORD.prompt` set on 115 of 180. It is not part of the index (the low seven bits still land
inside the string pool either way), so it is almost certainly which renderer draws the text. A
reference names the text; how it is shown is a different fact, and it gets its own field rather
than being folded into the name where it would be invisible.
"""
import re

# ── which fields carry what, per op. Read out of `wl_ir._emit_op_record`, not guessed: the emitter
# is the only place that says for certain which byte a field becomes. A wrong entry here cannot go
# unnoticed — resolve() would put back a different number and `_markgate.py` fails on the bytes.
#
#   str        one string id                      adv        one [op][id] pair
#   str_list   a run of string ids                adv_list   a list of pairs
#   entries    PATCH's [flags][dx][dy][op][id]    keywords   KEYWORD's [str][op][id]
SPEC = {
    "SAY":        {"str_list": ["messages"]},
    "BUMP":       {"str": ["msg"], "adv": ["next"]},
    "LOOT":       {"adv": ["advance"]},
    "ENCOUNTER":  {"str": ["msg"], "adv": ["victory"]},
    "OBJ":        {"str": ["msg"], "adv": ["use"]},
    "HAZARD":     {"str": ["msg"]},
    "COND":       {"str": ["prompt", "success", "fail"], "adv": ["done", "alt"],
                   "adv_list": ["branches"]},
    "PATCH":      {"str": ["msg"], "adv": ["after"], "entries": True},
    "TRANSITION": {"adv": ["next"]},
    "KEYWORD":    {"str": ["prompt"], "adv": ["cancel", "after"], "keywords": True},
    # selector families differ: 0/1/5 carry the greeting at +$05, the rest at +$03, and the
    # decoder names them apart — so both spellings are references, and exactly one record uses
    # the second one.
    "SERVICE":    {"str": ["greeting", "prompt"], "adv": ["advance"]},
    "EVENT":      {"adv": ["next"]},
}

MARK = re.compile(r"^[a-z0-9][a-z0-9/_-]*$")     # what a mark may look like


# ────────────────────────────────────────────────────────────── slugs

_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "you", "your", "at", "on",
         "for", "with", "that", "this", "from", "but", "not", "are", "was", "has", "have"}


def slug(text, want=3):
    """A readable handle for a piece of prose. Content where there is any, running number where
    there is none — the 719 CONDs and 623 doors mostly have no speaking text.

    Stop words go because "the-boy-watches-you-from" says less than "boy-watches-brush" in the same
    space. The slug is NOT re-derived when the text changes: it is a name, not a quotation."""
    words = [w for w in re.findall(r"[a-z0-9]+", str(text).lower()) if w not in _STOP]
    out = "-".join(words[:want])
    return out[:40].strip("-")


# ────────────────────────────────────────────────────────────── walking

def _classify(pair, groups):
    """'ref' | 'sentinel' | 'dangling'. The third is why this returns three things and not a bool.

    $FF/$FD/$FE are sentinels — 'no follow-op', 'swap the cell back' — and op 0 is terrain, which
    has no group. But a pair CAN name an op-and-id that simply is not there, and the shipped game
    contains seven of those. They cannot be marked (there is nothing to name) and they must not be
    filed under 'sentinel' either, or a real defect hides inside a normal-looking count."""
    if not isinstance(pair, dict):
        return "sentinel"
    op, rid = pair.get("op"), pair.get("id")
    if not isinstance(op, int) or not (1 <= op <= 15) or not isinstance(rid, int):
        return "sentinel"
    return "ref" if str(rid) in (groups.get(str(op)) or {}) else "dangling"


def _walk(rr, groups, on_str, on_adv):
    """Every reference of one record, through the callbacks. Returns nothing; the callbacks edit.

    Shared by mark() and resolve() ON PURPOSE — two walks would drift, and a field the marker
    touches but the resolver misses is a byte that never comes back."""
    spec = SPEC.get(rr.get("op")) or {}

    for k in spec.get("str", []):
        if isinstance(rr.get(k), (int, str, dict)):
            on_str(rr, k)
    for k in spec.get("str_list", []):
        lst = rr.get(k)
        if isinstance(lst, list):
            for i in range(len(lst)):
                on_str(lst, i)
    for k in spec.get("adv", []):
        on_adv(rr, k)
    for k in spec.get("adv_list", []):
        lst = rr.get(k)
        if isinstance(lst, list):
            for i in range(len(lst)):
                on_adv(lst, i)
    if spec.get("keywords"):
        for kw in (rr.get("keywords") or []):
            on_str(kw, "str")
            on_adv(kw, "branch")
    if spec.get("entries"):
        for e in (rr.get("entries") or []):
            on_adv(e, None)                       # op/id live inline, not in a sub-object


# ────────────────────────────────────────────────────────────── numbers → marks

def _record_base(rr, cid, g, strings):
    """The one naming rule for a record, used by mark() AND remark().

    A SAY borrows its first line; everything else keeps op+id, because "cond042" lies less than a
    slug taken from a prompt the record only TESTS. Two namers is how the first migration ended up
    writing `say000` where `desert-north-settlement` belonged — one function now, on purpose."""
    first = (rr.get("messages") or [None])[0] if rr.get("op") == "SAY" else None
    if isinstance(first, int) and (first & 0x7F) < len(strings):
        return slug(strings[first & 0x7F]) or f"r{int(g):02d}{int(cid):03d}"
    return f"{str(rr.get('op', 'r')).lower()}{int(cid):03d}"


def _string_base(sv, i):
    return slug(sv) or f"txt{i:03d}"



def mark(ir, area):
    """Give every record and string a mark and turn every reference into one. Returns a report.

    `area` is the prefix — marks are area-scoped because content does not move quietly, it gets
    copied (IR_NAMED_REFERENCES.md §1, the house rule wl_blueprint states outright)."""
    groups, strings = ir.get("groups") or {}, ir.get("strings") or []

    # 1) a mark for every string, from its own text. Texts live under `txt/` and records do not,
    #    which keeps a SAY from colliding with the very line it says — without that segment the
    #    record came out as "tearful-boy-watches-2", numbered against its own content.
    used, smark = {}, {}
    for i, s in enumerate(strings):
        base = _string_base(s, i)
        n = used.get(("t", base), 0)
        used[("t", base)] = n + 1
        smark[i] = f"{area}/txt/{base}" + (f"-{n + 1}" if n else "")

    # 2) a mark for every record. A SAY can borrow its first line; everything else is a number,
    #    because "cond042" lies less than a slug taken from a prompt the record only tests.
    rmark = {}
    for g, recs in groups.items():
        for cid, rr in recs.items():
            base = _record_base(rr, cid, g, strings)
            n = used.get(("r", base), 0)
            used[("r", base)] = n + 1
            rmark[(int(g), int(cid))] = f"{area}/{base}" + (f"-{n + 1}" if n else "")

    rep = {"strings": 0, "records": 0, "str_refs": 0, "rec_refs": 0,
           "sentinels": 0, "dangling": [], "out_of_pool": 0}

    def do_str(holder, key):
        v = holder[key] if isinstance(key, int) else holder.get(key)
        if not isinstance(v, int):
            return
        idx, hi = v & 0x7F, bool(v & 0x80)
        if idx >= len(strings):                   # points past the pool — leave the number alone
            rep["out_of_pool"] += 1
            return
        if isinstance(key, int):
            holder[key] = smark[idx]
        else:
            holder[key] = smark[idx]
            if hi:
                holder[f"{key}_hi"] = True        # the renderer flag, beside the name not inside it
        rep["str_refs"] += 1

    def do_adv(holder, key):
        pair = holder if key is None else (holder[key] if isinstance(key, int) else holder.get(key))
        kind = _classify(pair, groups)
        if kind == "sentinel":
            rep["sentinels"] += 1
            return
        if kind == "dangling":
            rep["dangling"].append(f'grp{pair["op"]}[{pair["id"]}]')
            return
        m = rmark[(pair["op"], pair["id"])]
        if key is None:                            # PATCH entry: op/id inline
            holder.pop("op"), holder.pop("id")
            holder["to"] = m
        elif isinstance(key, int):
            holder[key] = {"to": m}
        else:
            holder[key] = {"to": m}
        rep["rec_refs"] += 1

    for g, recs in groups.items():
        for cid, rr in recs.items():
            _walk(rr, groups, do_str, do_adv)

    # 3) the marks themselves, written last so the walk above still saw plain records
    for i, m in smark.items():
        rep["strings"] += 1
    ir["string_marks"] = [smark[i] for i in range(len(strings))]
    for g, recs in groups.items():
        for cid, rr in recs.items():
            rr["ref"] = rmark[(int(g), int(cid))]
            rr.setdefault("type", "world")         # the default that keeps unmarked 1988 data safe
            rep["records"] += 1
    ir.setdefault("meta", {})["marked"] = True
    return rep


# ────────────────────────────────────────────────────────────── marks → numbers

def resolve(ir):
    """Hand out numbers and put them back. THE numbering pass — the emitter's new first step.

    Numbers are handed out in the order the records stand in the document, which for an imported
    area is the order they already had. That is what makes the gate meaningful: nothing moves unless
    somebody moved it."""
    groups, strings = ir.get("groups") or {}, ir.get("strings") or []
    smarks = ir.get("string_marks") or []
    s_of = {m: i for i, m in enumerate(smarks)}
    r_of = {}
    for g, recs in groups.items():
        for cid, rr in recs.items():
            if rr.get("ref"):
                r_of[rr["ref"]] = (int(g), int(cid))

    missing = []

    def do_str(holder, key):
        v = holder[key] if isinstance(key, int) else holder.get(key)
        if not isinstance(v, str):
            return
        if v not in s_of:
            missing.append(v)
            return
        n = s_of[v]
        if isinstance(key, int):
            holder[key] = n
        else:
            if holder.pop(f"{key}_hi", False):
                n |= 0x80
            holder[key] = n

    def do_adv(holder, key):
        h = holder if key is None else (holder[key] if isinstance(key, int) else holder.get(key))
        if not (isinstance(h, dict) and isinstance(h.get("to"), str)):
            return
        if h["to"] not in r_of:
            missing.append(h["to"])
            return
        op, rid = r_of[h["to"]]
        if key is None:
            holder.pop("to")
            holder["op"], holder["id"] = op, rid
        elif isinstance(key, int):
            holder[key] = {"op": op, "id": rid}
        else:
            holder[key] = {"op": op, "id": rid}

    for g, recs in groups.items():
        for cid, rr in recs.items():
            _walk(rr, groups, do_str, do_adv)

    if missing:
        raise ValueError(f"{len(missing)} mark(s) without a target: {sorted(set(missing))[:5]}")
    for g, recs in groups.items():
        for cid, rr in recs.items():
            rr.pop("ref", None)
    ir.pop("string_marks", None)
    (ir.get("meta") or {}).pop("marked", None)
    return ir


# ────────────────────────────────────────────────────────────── carrying marks across a save

def carry(old_ir, new_ir):
    """Move the marks of a stored document onto a freshly decoded one, keyed by (group, id).

    THE SAVE PATH'S COMPROMISE, and it is worth stating rather than hiding. Seventeen endpoints
    hand `save_record` BYTES, so the IR on the way out is rebuilt by `record_to_ir` and knows
    nothing of the marks the stored document carried. Keying on (group, id) restores them exactly
    as long as nothing was renumbered — which is true of every byte-patching endpoint there is,
    because none of them can insert.

    Where it stops: an insert. The record that moved from grp1[15] to grp1[16] would inherit the
    mark of whoever now sits at 15. That is not a bug to fix here — it is the reason `save_ir`
    exists, and the reason a caller that renumbers must use it.
    """
    om = {}
    for g, recs in (old_ir.get("groups") or {}).items():
        for cid, rr in recs.items():
            if rr.get("ref"):
                om[(g, cid)] = (rr["ref"], rr.get("type"), rr.get("from"))
    kept = 0
    for g, recs in (new_ir.get("groups") or {}).items():
        for cid, rr in recs.items():
            got = om.get((g, cid))
            if not got:
                continue
            ref, typ, frm = got
            rr["ref"] = ref
            if typ:
                rr["type"] = typ
            if frm:
                rr["from"] = frm
            kept += 1
    # strings are positional in both documents; a text pool only ever grows or shrinks at its end
    # when the editor edits prose, and `remove_unused_strings` renumbers through the same save.
    old_s = old_ir.get("string_marks") or []
    new_n = len(new_ir.get("strings") or [])
    if old_s:
        new_ir["string_marks"] = (old_s + [None] * new_n)[:new_n]
    return kept


def is_marked(ir):
    return bool((ir.get("meta") or {}).get("marked"))


def remark(ir, area):
    """Mark whatever is not marked yet, leaving existing marks alone. Runs after `carry`, so a save
    that ADDED a record gives the newcomer a mark without disturbing its neighbours.

    Names come from `_record_base`/`_string_base` — the same rule `mark()` uses. They used to come
    from a second, sloppier copy here, which is how the first five migrated documents ended up with
    `s1a10/say000` where `s1a10/desert-north-settlement` belonged."""
    strings = ir.get("strings") or []
    groups = ir.get("groups") or {}
    smarks = list(ir.get("string_marks") or [])
    smarks = (smarks + [None] * len(strings))[:len(strings)]

    taken = {m for m in smarks if m}
    taken |= {rr["ref"] for recs in groups.values() for rr in recs.values() if rr.get("ref")}

    def claim(base):
        cand = f"{area}/{base}"
        n = 1
        while cand in taken:
            n += 1
            cand = f"{area}/{base}-{n}"
        taken.add(cand)
        return cand

    for i, sv in enumerate(strings):
        if not smarks[i]:
            smarks[i] = claim(f"txt/{_string_base(sv, i)}")
    ir["string_marks"] = smarks

    for g, recs in groups.items():
        for cid, rr in recs.items():
            if not rr.get("ref"):
                rr["ref"] = claim(_record_base(rr, cid, g, strings))
            rr.setdefault("type", "world")
    ir.setdefault("meta", {})["marked"] = True
    return ir
