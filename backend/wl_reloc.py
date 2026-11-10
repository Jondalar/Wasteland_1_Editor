"""wl_reloc.py — moving an area's grp16 6502 to a different offset inside the record.

grp16 is the one section the serializer cannot lay out freely: every other cross-reference in a
record is an id or an index and survives a move, but grp16 is machine code whose absolute operands
and front jump table point at its own bytes. Move the blob without touching them and the handlers
read and jump into whatever now occupies the old address.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
It builds an explicit RELOCATION TABLE — the byte offsets inside the blob that hold an in-window
16-bit address — from a flow walk of the code, and adds the delta to exactly those. That is the safe
half of the problem, and it is the half a scan cannot do: the Accolade EF relocator
(GRP16_RELOCATION_SPEC §1) walks bytes guessing instruction widths from opcode tables, which works
only on a pure opcode stream. grp16 is a jump table, then code, then data tables — a scan mis-aligns
on the first of the three and silently rewrites data that looked like an operand.

An operand is not the only place an address lives. The code also ASSEMBLES addresses from two
immediates, and those have to be relocated exactly like an operand:

    lda #$FC / sta $31 / lda #$42 / sta $32                        ; ($31) = $42FC, a string in the blob
    ldy #$03 / lda ($5F),y / clc / adc #$3E / sta $13 / lda #$3E / sta $12
                                                                  ; ($12) = $3E3E + payload*256

Both are from Sleeper Lv2's cloning lab (s4_id13) — the prompt it prints, and the four 256-byte clone
slots it picks between. `_built_pointers()` reads them: an immediate bound into `$ZZ`, its partner
bound into `$ZZ+1` within a few instructions, and the assembled address landing inside the blob. The
address is right there in the bytes; it just is not an operand. The record IS self-contained, and
every one of its pointers is legible.

An immediate equal to a blob page that is NOT part of a recognised pair is the honest unknown — it
may be half of a construction written some other way — and `plan()` refuses the blob rather than
guessing. On the shipped corpus nothing hits that: all 32 blobs relocate.

And because "I recognised the idiom" is still a pattern match, the table is not trusted on that
basis. `trace_sites()` EXECUTES every handler under byte-level taint (`wl_taint6502`): each blob byte
carries its own offset, labels propagate through loads, stores, the stack and arithmetic, and an
effective address landing in the window names the bytes that built it — operand, immediate pair,
table entry, stack round-trip, it makes no difference. `verify_table()` compares that against the
static table, on the natural paths and again with every branch forced the other way. Result on the
shipped corpus: nothing execution finds is missing from the static table.

DEFAULT stays PIN (`policy: "pin"`) anyway, for a different reason: pinning costs 7 bytes across all
32 blobs, because the item-level packer already fills the hole in front of grp16. Floating buys no
space on production records. Relocation is for CODE THAT MOVES BY INTENT — a blueprint carrying grp16
into another area, or authored grp16 at a new offset — via `policy: "float"`.

And the table is then USED and checked: `cart_EF/tools/wl_reloc_test.py` runs all 66 handlers at
both bases over identical memory with the OLD span zeroed — so a missed pointer reads $00 rather
than quietly finding the original bytes — and compares every write. 66/66 identical.
"""
from wl_disasm6502 import _OPS, _SZ
from wl_grp16 import jump_table

from wl_machine import RECORD_BASE as RAM   # $3400 on the C64, $D000 on the Apple
# ops that can hold half of an address in an immediate. Comparisons are excluded on purpose: a
# `cmp #$3E` tests a value, it does not build a pointer, and including them flagged clean blobs.
_IMM_BUILD = {"lda", "ldx", "ldy", "adc", "sbc", "and", "ora", "eor"}


class Unsafe(Exception):
    """Raised when relocate() is asked to move a blob whose plan is not safe."""


def _walk(code: bytes, base: int, entries: list):
    """Flow walk from the entries. Returns (covered, sites, immediates, zp ptr bases, instr starts).

    `sites` are (offset_of_the_operand_bytes, target, mnemonic) for every 3-byte absolute
    instruction; the caller splits them into in-window (relocate) and fixed (leave alone).
    """
    n = len(code)
    seen, covered, sites, imms, zpp, starts = set(), set(), [], [], set(), []
    todo = [e for e in entries if e] or [base]
    while todo:
        pc = todo.pop()
        while base <= pc < base + n and pc not in seen:
            seen.add(pc)
            ent = _OPS.get(code[pc - base])
            if ent is None:
                break                                       # ran into data
            mn, mode = ent
            sz = _SZ[mode]
            if pc - base + sz >= n:
                break
            covered.update(range(pc - base, pc - base + 1 + sz))
            if mode == "imm" and mn in _IMM_BUILD:
                imms.append((pc, mn, code[pc - base + 1]))
            starts.append((pc, mn, mode))
            if mode in ("izy", "izx"):                       # ($zz),y — $zz/$zz+1 is a POINTER pair
                zpp.add(code[pc - base + 1])
            if mode in ("abs", "abx", "aby", "ind"):
                t = code[pc - base + 1] | (code[pc - base + 2] << 8)
                sites.append((pc - base + 1, t, mn))
            if mode == "rel":
                d = code[pc - base + 1]
                todo.append(pc + 2 + (d - 256 if d & 0x80 else d))
            if mn == "jsr":
                t = code[pc - base + 1] | (code[pc - base + 2] << 8)
                if base <= t < base + n:
                    todo.append(t)
            if mn in ("jmp", "rts", "rti"):
                if mn == "jmp" and mode == "abs":
                    t = code[pc - base + 1] | (code[pc - base + 2] << 8)
                    if base <= t < base + n:
                        todo.append(t)
                break
            pc += 1 + sz
    return covered, sites, imms, sorted(zpp), sorted(starts)


def _built_pointers(code: bytes, base: int, starts: list) -> list:
    """In-window addresses the code ASSEMBLES from two immediates: [(lo_off, hi_off, target, $ZZ)].

    The idiom is one instruction pair, and it is fully readable — the address is right there in the
    bytes, just not as an operand:

        lda #$FC / sta $31 / lda #$42 / sta $32          -> ($31) = $42FC   (a string in the blob)
        lda ($5F),y / clc / adc #$3E / sta $13 / lda #$3E / sta $12
                                                         -> ($12) = $3E3E + payload*256

    The second one is Sleeper Lv2 choosing one of its four 256-byte clone slots; the `adc` immediate
    is the BASE of the high byte, so adding a delta to it is exactly as correct as adding it to an
    operand. Only pairs whose assembled address lands INSIDE the blob are returned — a `lda #$00 /
    sta $12 / lda #$05 / sta $13` counter assembles to $0500 and is ignored by the same window test
    that protects the operand path.
    """
    n = len(code)
    pend, bind, out = None, {}, []
    for idx, (pc, mn, mode) in enumerate(starts):
        if idx and starts[idx - 1][0] + 1 + _SZ[starts[idx - 1][2]] != pc:
            pend, bind = None, {}                    # not the fall-through successor: state is stale
        if mode == "imm" and mn in ("lda", "adc", "sbc", "ora", "eor", "and"):
            pend = (pc - base + 1, code[pc - base + 1], idx)
        elif mn == "sta" and mode == "zp" and pend is not None:
            z = code[pc - base + 1]
            bind[z] = pend
            # A pointer pair is ($ZZ, $ZZ+1) for ANY $ZZ — not an even-aligned pair. Matching with
            # `z ^ 1` missed the $31/$32 construction that prints this blob's clone prompt, because
            # $31 is odd; the partner has to be looked for on BOTH sides.
            for zlo, lo, hi in ((z, pend, bind.get(z + 1)), (z - 1, bind.get(z - 1), pend)):
                if lo is None or hi is None or abs(lo[2] - hi[2]) > 6:
                    continue
                t = lo[1] | (hi[1] << 8)
                if base <= t < base + n:
                    out.append((lo[0], hi[0], t, zlo))
                    # CONSUME both halves. Leaving them bound let the NEXT construction pair its
                    # fresh low byte with this one's stale high byte: `lda #$00 / sta $31` after
                    # `lda #$42 / sta $32` assembled a phantom $4200 and claimed the $42 byte a
                    # second time, so relocating wrote that byte twice and broke the real $42FC.
                    bind.pop(zlo, None)
                    bind.pop(zlo + 1, None)
                    break
            pend = None
        elif mn in ("lda", "ldx", "ldy", "pla", "txa", "tya", "jsr"):
            pend = None                              # A/X/Y reloaded from elsewhere — binding is over
    return out


def _runs(offs) -> list:
    """[(start, length)] for a set of byte offsets."""
    out, s = [], sorted(offs)
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[j] + 1:
            j += 1
        out.append((s[i], s[j] - s[i] + 1))
        i = j + 1
    return out


def plan(code: bytes, base: int) -> dict:
    """Everything needed to decide whether — and how — this blob may move.

    `sites` is the relocation table: byte offsets holding a 16-bit LE address into the blob's own
    window. `fixed` counts the absolute operands pointing at the engine, zero page or I/O — those
    are structurally excluded by the window test, exactly the guard the Accolade relocator got right.
    """
    n = len(code)
    if not n:
        return {"table": [], "sites": [], "split": [], "fixed": 0, "unreached": [], "zp_ptrs": [],
                "risks": [], "safe": True}
    tbl = jump_table(code, base)
    covered, ops, imms, zpp, starts = _walk(code, base, tbl)
    tbl_bytes = set(range(2 * len(tbl)))

    opsites, fixed = [], 0
    for off, t, mn in ops:
        if base <= t < base + n:
            opsites.append(off)
        else:
            fixed += 1
    tblsites = [2 * i for i, v in enumerate(tbl) if v]      # the jump table relocates too — missing
    sites = sorted(set(opsites) | set(tblsites))            # it is the classic bug (SPEC §8)

    unreached = _runs(set(range(n)) - covered - tbl_bytes)
    risks = []
    if not tbl:
        risks.append("no jump table could be derived — the blob's entry points are unknown")
    # Addresses ASSEMBLED from two immediates. Those that are READ OUT (a recognised $ZZ/$ZZ+1 pair
    # assembling into the window) become relocation sites like any other. What remains — an immediate
    # equal to a page the blob occupies that is NOT part of a recognised pair — is the honest unknown:
    # it may be half of a construction written some other way, and no delta pass would see it, so the
    # blob is refused rather than guessed at.
    built_ptrs = _built_pointers(code, base, starts)
    split = [(lo, hi, t) for lo, hi, t, _ in built_ptrs]
    # a constructed pointer lives in zero page exactly like a dereferenced one — the editor and the
    # relocation harness need both, or the harness reads a correctly-moved high byte as a divergence
    zpp = sorted(set(zpp) | {z for *_, z in built_ptrs})
    claimed = {o for lo, hi, _ in split for o in (lo, hi)}
    pages = set(range(base >> 8, ((base + n - 1) >> 8) + 1))
    built = [(pc, mn, v) for pc, mn, v in imms if v in pages and (pc - base + 1) not in claimed]
    if built:
        where = ", ".join(f"${pc:04X} {mn} #${v:02X}" for pc, mn, v in built[:4])
        risks.append(f"immediate equal to a blob page ({len(built)} sites: {where}) that is not part "
                     "of a recognised pointer construction — a delta pass cannot see it")
    # A POINTER TABLE hiding in bytes the walk never classified would need relocating too, and no
    # operand names it. Test for the SHAPE of one — two or more consecutive aligned words that are
    # all null or all in-window — not for a single word that merely happens to land in range: data
    # tables hit that by coincidence (and the coincidence moves with the blob, so the verdict would
    # not even be stable). Bit masks, stat rows and text fail the shape test; a pointer table cannot.
    targets = [t - base for _, _, t in split]
    for off, ln in unreached:
        if any(off <= t < off + ln for t in targets):
            continue                                 # a constructed pointer names this run as DATA:
        run = 0                                      # runtime storage or a string, not pointers
        for o in range(off, off + ln - 1, 2):
            v = code[o] | (code[o + 1] << 8)
            run = run + 1 if (v == 0 or base <= v < base + n) else 0
            if run >= 2:
                risks.append(f"unreached bytes +{off}..+{off + ln} look like a POINTER TABLE "
                             f"(in-window words at +{o - 2}, +{o}) the walk never classified")
                break
    for a, b in zip(sites, sites[1:]):                      # two sites cannot share a byte
        if b - a < 2:
            risks.append(f"relocation sites +{a} and +{b} overlap")
    for s in opsites:                                       # nor may an operand sit in the table:
        if s < 2 * len(tbl):                                # then one of the two readings is wrong
            risks.append(f"operand +{s} lands inside the jump table")
    halves = [o for lo, hi, _ in split for o in (lo, hi)]   # one byte, one address — a byte claimed
    for o in set(halves):                                   # by two constructions means one of them
        if halves.count(o) > 1:                             # was mis-read, and relocating writes it
            risks.append(f"byte +{o} is claimed by two built addresses")   # twice
    for o in halves:
        if o in set(sites) or (o - 1) in set(sites):
            risks.append(f"byte +{o} is both an operand half and a built-address half")
    return {"table": tbl, "sites": sites, "split": split, "fixed": fixed, "unreached": unreached,
            "zp_ptrs": zpp, "risks": risks, "safe": not risks}


# Deterministic seed matrix. One fixed zero page only ever walks one path through a handler, and
# the branch conditions here are the game's own state: how many rangers are in the party, which
# payload byte the raising EVENT carries, whether a slot is occupied. These are the values that
# actually gate the code, chosen from what the handlers compare against (`cmp #$07` party-full,
# payload ids 0..3 for the four clone slots), so the union of the runs covers the branches instead
# of the same one N times.
SEEDS = [{}, {"party": 1}, {"party": 6}, {"party": 7}, {"pay": 1}, {"pay": 2}, {"pay": 3},
         {"pay": 3, "party": 6}, {"fill": 0x00}, {"fill": 0xFF}]


def image(raw: bytes, base: int, n: int, code: bytes, desc: int, seed=None):
    """The deterministic memory a grp16 handler is traced in. No randomness, no clock, no I/O.

    Record at $3400, the blob at `base`, `$60` (RTS) everywhere else so any engine call returns at
    once, and a fixed zero-page seed. Two runs of the same blob produce the same trace, always —
    which is what lets an execution trace be used as a build-time fact and not just a smoke test.
    """
    m = bytearray(b"\x60" * 0x10000)
    m[0x0000:0x0200] = b"\x00" * 0x200
    m[RAM:RAM + len(raw)] = raw
    m[base:base + n] = code
    seed = seed or {}
    if "fill" in seed:                                         # a blank vs a full member record
        m[0x2000:0x2100] = bytes([seed["fill"]]) * 0x100
    else:
        m[0x2000:0x2004] = bytes((0x11, 0x22, 0x33, 0x44))     # member record
    m[0x2100] = 0x01                                           # roster
    m[0x2200:0x2210] = bytes(range(16))                        # the raising EVENT record
    if "pay" in seed:                                          # payload +3/+4 pick a slot / target
        m[0x2203] = m[0x2204] = seed["pay"]
    party = seed.get("party", 4)
    zp = {0x5F: 0x00, 0x60: 0x22, 0x61: desc & 0xFF, 0x62: desc >> 8, 0x66: 0x00, 0x67: 0x20,
          0x68: 0x00, 0x69: 0x21, 0x57: 5, 0x58: 7, 0x07: party, 0x0A: party,
          0x04: 0x30, 0x05: 0x01, 0x06: 0x00}
    for k, v in zp.items():
        m[k] = v
    return m


def trace_sites(raw: bytes, code: bytes, base: int, desc: int, entries=None,
                explore: bool = False) -> dict:
    """THE DETERMINISTIC RELOCATION TABLE: run the code and see which bytes become an address.

    Every handler is executed with byte-level taint (wl_taint6502). When the CPU forms an effective
    address inside the blob, the two bytes that produced it are recorded — an operand, a jump-table
    word, two immediates in a zero-page pointer, a byte pulled off the stack, it makes no difference.
    Nothing is pattern-matched, so nothing can be pattern-missed.

    Returns {"words": {offset}, "split": {(lo, hi)}, "partial": {...}, "errors": [...]}, where a
    `words` entry is a normal 16-bit LE site (the halves are adjacent) and `split` is a pair that is
    not. Coverage is the limit, not correctness: an unexecuted branch contributes nothing, which is
    why `plan()` keeps its static walk and `verify_table()` compares the two.
    """
    from wl_taint6502 import Taint
    n = len(code)
    words, split, partial, errs = set(), set(), set(), []
    explored = set()                                            # sites seen only on a FORCED path
    for entry in (entries if entries is not None else jump_table(code, base)):
        if not entry:
            continue
        for seed in SEEDS:
            t = Taint(image(raw, base, n, code, desc, seed), base, n)
            try:
                t.run(entry, max_steps=400_000)
            except Exception as e:                              # noqa: BLE001
                errs.append(f"${entry:04X} {seed}: {type(e).__name__} {e}")
            for lo, hi in t.sites:
                (words.add(lo) if hi == lo + 1 else split.add((lo, hi)))
            partial |= t.partial
        if explore:
            explored |= _explore(raw, code, base, desc, entry, Taint)
    return {"words": words, "split": split, "partial": partial, "explored": explored,
            "errors": errs}


def _explore(raw, code, base, desc, entry, Taint, runs=96) -> set:
    """Re-run one handler while FORCING each branch the other way, breadth-first.

    Natural execution only ever walks the paths the seed state selects; this walks the rest. A site
    found only here is NOT added to the table — a forced path can reach a state the game never
    produces, and relocating a byte on that evidence would corrupt data. It is a CROSS-CHECK: if
    forcing every branch still forms no in-window address the static walk does not already list,
    then the static table is not merely plausible, it is complete over the whole branch graph.
    """
    n, out, seen, queue = len(code), set(), {()}, [()]
    while queue and len(seen) < runs:
        force = queue.pop(0)
        t = Taint(image(raw, base, n, code, desc), base, n)
        t.force = list(force)
        try:
            t.run(entry, max_steps=40_000)
        except Exception:                                       # noqa: BLE001
            pass                                                # a forced path may well crash
        for lo, hi in t.sites:
            out.add((lo, hi))
        for k, (_, taken) in enumerate(t.branch_log[:24]):
            nxt = tuple(list(force)[:k] + [not taken]) if k < len(force) else \
                tuple([d for _, d in t.branch_log[:k]] + [not taken])
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return out


def verify_table(pln: dict, traced: dict) -> list:
    """Cross-check the static relocation table against the traced one. [] = they agree.

    The two have complementary blind spots — the static walk sees code it can decode but has to
    recognise how an address was built; the trace sees exactly how every address was built but only
    on the paths it runs. A site in one and not the other is the interesting case, and this names it.
    """
    bad = []
    static_split = {(lo, hi) for lo, hi, _ in pln.get("split", [])}
    for o in sorted(traced["words"] - set(pln["sites"])):
        bad.append(f"+{o}: the trace formed an address here, the static table does not list it")
    for p in sorted(traced["split"] - static_split):
        bad.append(f"+{p[0]}/+{p[1]}: built address seen at runtime, missing from the static table")
    for o, half in sorted(traced["partial"]):
        bad.append(f"+{o}: only the {half} half of an in-window address is a blob byte")
    for lo, hi in sorted(traced.get("explored", ())):           # forced-path cross-check
        if (hi == lo + 1 and lo not in set(pln["sites"])) or (hi != lo + 1 and
                                                              (lo, hi) not in static_split):
            bad.append(f"+{lo}/+{hi}: a forced path forms an in-window address the static table "
                       "does not list")
    return bad


def relocate(code: bytes, base: int, new_base: int, pln=None) -> bytes:
    """The blob, rewritten to run at `new_base`. Raises Unsafe unless the plan says it may move."""
    pln = pln or plan(code, base)
    if not pln["safe"]:
        raise Unsafe("; ".join(pln["risks"]))
    if new_base == base:
        return bytes(code)
    delta, n, out = new_base - base, len(code), bytearray(code)
    for off in pln["sites"]:
        v = out[off] | (out[off + 1] << 8)
        if not (base <= v < base + n):                      # window guard, re-checked at apply time
            continue
        w = (v + delta) & 0xFFFF
        out[off], out[off + 1] = w & 0xFF, w >> 8
    for lo, hi, t in pln.get("split", []):                  # the two halves of a built address: the
        w = (t + delta) & 0xFFFF                            # carry out of the low byte lands in the
        out[lo], out[hi] = w & 0xFF, w >> 8                 # high one, exactly as the 16-bit add does
    return bytes(out)


def verify(old: bytes, new: bytes, base: int, new_base: int) -> list:
    """Cross-check a relocation by re-planning the MOVED blob. [] = it holds up.

    Three things must be true afterwards: the byte that is not an address is untouched, every
    address that pointed into the old window points into the new one, and the relocated blob plans
    to exactly the same site list — i.e. the move did not change how the code reads.
    """
    n, delta = len(old), new_base - base
    bad = []
    p0, p1 = plan(old, base), plan(new, new_base)
    if p0["sites"] != p1["sites"]:
        bad.append(f"site list changed: {len(p0['sites'])} -> {len(p1['sites'])}")
    sset = set(p0["sites"])
    half = {o for lo, hi, _ in p0.get("split", []) for o in (lo, hi)}
    for i in range(n):
        if i in sset or (i - 1) in sset or i in half:
            continue
        if old[i] != new[i]:
            bad.append(f"non-address byte +{i} changed ${old[i]:02X} -> ${new[i]:02X}")
    for lo, hi, t in p0.get("split", []):
        w = new[lo] | (new[hi] << 8)
        if w != (t + delta) & 0xFFFF:
            bad.append(f"built address ${t:04X} -> ${w:04X}, expected ${(t + delta) & 0xFFFF:04X}")
    for off in p0["sites"]:
        v = old[off] | (old[off + 1] << 8)
        w = new[off] | (new[off + 1] << 8)
        if v == 0:
            if w:
                bad.append(f"null jump-table entry +{off} became ${w:04X}")
        elif w != (v + delta) & 0xFFFF:
            bad.append(f"+{off}: ${v:04X} -> ${w:04X}, expected ${(v + delta) & 0xFFFF:04X}")
        elif not (new_base <= w < new_base + n):
            bad.append(f"+{off}: ${w:04X} left the new window")
    return bad
