"""wl_grp16.py — static analysis of an area's grp16 handlers, for the editor's Code tab.

Every handler is entered with `$5F/$60` pointing at the raising grp6 record, so the `lda ($5F),y`
offsets it reads ARE that EVENT record's payload field map, and the `lda #<id> / ldy #<grp> /
jsr $0428` pairs name the records it acts on. Both are facts about the code, derived on every load —
they sit next to the hand-written note in the UI rather than replacing it, so the machine states
what the handler touches and a human states what it MEANS.

Validated over all 43 areas: for 62 of 64 handlers the derived payload length matches the raising
record's measured extent exactly (the other two have no EVENT record — dead code). See
`cart_EF/tools/wl_grp16_events.py` for the batch report and GRP16_SPEC.md §3.2.
"""
from wl_disasm6502 import _OPS, _SZ

from wl_machine import RECORD_BASE as RAM, ZP_DESC   # $3400/$D000, and the descriptor ZP slot
# the complete engine API grp16 uses — GRP16_SPEC.md §3.1
API = {0x0428: "area_record_subptr", 0x042B: "map_cell_read_xy", 0x0335: "frame_state_core",
       0x0338: "member_record_ptr_from_a", 0x0344: "inventory_first_used_slot",
       0x029F: "member_ptr2_from_a", 0x02BA: "member_alive_check", 0x044C: "screen_flash_fx",
       0x0317: "text_render_window", 0x026F: "print_string_mode_b", 0x0323: "char_out_vector",
       0x02F6: "num_print_zero_pad", 0x032C: "num_setup_24bit", 0x0329: "num_emit_digits",
       0x02EA: "window_frame_draw", 0x04FC: "bank_ram_in", 0x04FF: "bank_ram_out"}
# zero-page the calling convention hands the handler (GRP16_SPEC §3). C64 addresses: only the
# descriptor slot ($61 -> Apple $64, see wl_machine.ZP_DESC) has been measured on the Apple, so
# on that machine these names are a guess and the disassembly simply leaves them unannotated.
ZP = {0x5F: "event record", 0x60: "event record+1", 0x61: "area descriptor", 0x62: "descriptor+1",
      0x66: "member record", 0x67: "member record+1", 0x68: "roster", 0x69: "roster+1",
      0x57: "party X", 0x58: "party Y", 0x07: "party count", 0x0A: "party count2",
      0x04: "clock", 0x05: "clock+1", 0x06: "clock+2", 0x12: "self", 0x13: "self+1"}


def jump_table(code: bytes, base: int) -> list:
    """The blob's front dispatch table: one LE address per event id, `$0000` = event not handled.

    THE NULL ENTRY IS THE POINT. The obvious scan — "take words while they point into the blob" —
    stops dead at the first `$0000`, and everything behind it becomes invisible: it read s1_id49 as
    one event instead of four and s4_id22 (whose table STARTS with two nulls) as none at all, hiding
    both handlers from the Code tab, from the coverage census and from the relocator.

    So the length is not guessed from where the scan gives up, it is DERIVED: the first handler sits
    immediately behind the table, hence `count = (min(nonzero entry) - base) / 2`. That is
    self-checking — a wrong count would put an entry past the first handler or leave a word that is
    neither null nor in-window — and it agrees with the walk on all 32 blobs.
    """
    n = len(code)
    words, i = [], 0
    while i + 1 < n:
        v = code[i] | (code[i + 1] << 8)
        if v == 0 or base <= v < base + n:
            words.append(v)
            i += 2
        else:
            break
    nz = [v for v in words if v]
    if not nz:
        return []
    cnt = (min(nz) - base) // 2
    if not (1 <= cnt <= len(words)) or 2 * cnt > n:
        return []
    return words[:cnt]


def trace(code: bytes, base: int, entry: int):
    """Follow one handler. Returns (payload Ys, api calls, zp touched, size, resolved records).

    `resolved` is the interesting one: the call is `lda #<id> / ldy #<grp> / jsr $0428`, so the two
    immediates in flight at the call site name the RECORD the event acts on — grp5[9], grp12[0], …
    That turns "this handler calls subptr" into "this handler patches the loot sack at grp5[9]"."""
    n = len(code)
    # `covered` counts every BYTE of every instruction, `seen` only their addresses — reporting
    # len(seen) as the handler size undercounted it by the operand bytes (a 3-byte instruction
    # counted as 1), which made 65% of grp16 look unread when it is plain code.
    ys, calls, zps, seen, covered, todo = set(), [], set(), set(), set(), [(entry, False)]
    resolved = []
    lastldy = lastlda = None
    while todo:
        pc, repointed = todo.pop()
        while base <= pc < base + n and pc not in seen:
            seen.add(pc)
            op = code[pc - base]
            ent = _OPS.get(op)
            if ent is None:
                break
            mn, mode = ent
            sz = _SZ[mode]
            if pc - base + sz >= n:
                break
            covered.update(range(pc - base, pc - base + 1 + sz))
            arg = code[pc - base + 1] if sz else None
            if mn == "ldy" and mode == "imm":
                lastldy = arg
            elif mn == "ldy" and mode != "imm":
                lastldy = None
            if mn == "lda" and mode == "imm":
                lastlda = arg
            elif mn == "lda" and mode != "imm":
                lastlda = None
            # ($5F),y is the EVENT record ONLY until area_record_subptr runs: $0428 REPOINTS
            # $5F/$60 at whatever record it resolved, so later reads through it belong to that
            # record instead. Without this the tool credited an ENCOUNTER's +9 flags byte to the
            # event payload and "found" five records too short for their own handler.
            if mode == "izy" and arg == 0x5F and lastldy is not None and not repointed:
                ys.add(lastldy)
            if mode in ("zp", "zpx", "zpy", "izx", "izy") and arg in ZP:
                zps.add(arg)
            if mode in ("abs", "abx", "aby"):
                t = code[pc - base + 1] | (code[pc - base + 2] << 8)
                if mn == "jsr" and t in API:
                    calls.append(API[t])
                elif mn == "jsr" and not (base <= t < base + n):
                    calls.append(f"${t:04X}")
            if mode == "rel":
                d = code[pc - base + 1]
                todo.append((pc + 2 + (d - 256 if d & 0x80 else d), repointed))
            if mn == "jsr":
                t = code[pc - base + 1] | (code[pc - base + 2] << 8)
                if t in (0x0428, 0x042B):
                    if t == 0x0428 and lastldy is not None and lastlda is not None:
                        resolved.append((lastldy, lastlda))
                    repointed = True
                if base <= t < base + n:
                    todo.append((t, repointed))
            if mn in ("jmp", "rts", "rti"):
                if mn == "jmp" and mode == "abs":
                    t = code[pc - base + 1] | (code[pc - base + 2] << 8)
                    if base <= t < base + n:
                        todo.append((t, repointed))
                break
            pc += 1 + sz
    return ys, calls, zps, len(covered), resolved




# ── the world-parameter effect: what a handler does to the area descriptor ────────────────────────
# Traced 2026-08-02. 21 of the 66 live handlers do nothing but retune three descriptor bytes — the
# random-encounter setup — and they do it in two shapes, both of which put every number that matters
# in an IMMEDIATE at a fixed position. That is what makes them editable without codegen: the delta,
# the cap and the constant can be rewritten in place, same length, no relocation.
#
#     ldy #$2F / lda ($61),y / clc adc #$02 / cmp #$64 / sta ($61),y      read-modify-write
#     lda #$14 / ldy #$2F / sta ($61),y                                   set a constant
#
# Guards vary (`beq` bail-out when the value is already 0, a `bit` skip that lets two events share
# one tail — Blood Temple's alarm on/off pair is exactly that), so the matcher walks instructions
# and ignores what it does not need rather than matching a byte pattern.
DESC_FIELDS = {0x2F: "rate", 0x31: "pool", 0x32: "slots"}
_FIELD_TEXT = {"rate": "encounter chance", "pool": "wandering foe types",
               "slots": "simultaneous wandering groups"}


def effect(code: bytes, base: int, entry: int) -> dict | None:
    """What this handler does to the descriptor — or None when it does something else.

    Returns {field, kind: 'delta'|'set', delta|value, cap, at: {name: RAM address of the immediate}}.
    `at` is the edit surface: one byte each, so a write is a single poke into the grp16 blob.
    """
    n, pc = len(code), entry
    field = read = delta = cap = value = None
    at, sign = {}, 1
    for _ in range(24):
        if not (base <= pc < base + n):
            return None
        o = pc - base
        ent = _OPS.get(code[o])
        if ent is None:
            return None
        mn, mode = ent
        sz = _SZ[mode]
        if o + sz >= n:
            return None
        arg = code[o + 1] if sz else None
        if mn == "ldy" and mode == "imm":
            field = DESC_FIELDS.get(arg)
            at["field"] = pc + 1
        elif mn == "lda" and mode == "izy" and arg == ZP_DESC:
            read = True
        elif mn == "lda" and mode == "imm":
            value, at["value"] = arg, pc + 1
        elif mn == "sec":
            sign = -1
        elif mn == "clc":
            sign = 1
        elif mn in ("adc", "sbc") and mode == "imm":
            delta = arg * (-1 if mn == "sbc" else sign)
            at["delta"] = pc + 1
        elif mn == "cmp" and mode == "imm":
            cap, at["cap"] = arg, pc + 1
        elif mn == "sta" and mode == "izy" and arg == ZP_DESC:
            if field is None:
                return None
            if read and delta is not None:
                return {"field": field, "kind": "delta", "delta": delta, "cap": cap, "at": at}
            if not read and value is not None:
                return {"field": field, "kind": "set", "value": value, "cap": None, "at": at}
            return None
        elif mn in ("rts", "rti") or (mn == "jmp" and mode == "ind"):
            return None
        elif mn == "jmp" and mode == "abs":
            t = code[o + 1] | (code[o + 2] << 8)
            if not (base <= t < base + n):
                return None
            pc = t
            continue
        elif mn == "bit":                       # the two-events-share-a-tail skip: step over it
            pass
        pc += 1 + sz
    return None


def effect_text(eff: dict) -> str:
    """The effect in the words the Data panel uses for the same three bytes."""
    what = _FIELD_TEXT.get(eff["field"], eff["field"])
    if eff["kind"] == "set":
        v = eff["value"]
        if eff["field"] == "rate":
            return f"{what} := " + ("off" if v == 0 else f"1 in {v}")
        return f"{what} := {v}"
    d = eff["delta"]
    # a bigger denominator is a RARER encounter — say the direction, not the arithmetic
    arrow = ("rarer" if d > 0 else "more often") if eff["field"] == "rate" else \
            ("more" if d > 0 else "fewer")
    s = f"{what} {d:+d} ({arrow})"
    return s + (f", stops at {eff['cap']}" if eff.get("cap") is not None else "")


def effects(ir) -> dict:
    """{event id: {effect, text}} for the handlers that retune the world. {} when none do."""
    if not ir.get("grp16"):
        return {}
    import wl_ir
    code = wl_ir._unhex(ir["grp16"]["code"])
    base = ir["grp16"]["offset"] + RAM
    out = {}
    for ev, entry in enumerate(jump_table(code, base)):
        if not entry:
            continue
        e = effect(code, base, entry)
        if e:
            out[ev] = {"effect": e, "text": effect_text(e)}
    return out


def patch_effect(code: bytearray, base: int, eff: dict, changes: dict) -> bytearray:
    """Rewrite an effect's immediates in place. Same length, so nothing relocates.

    Only the numbers the shape exposes — `delta`, `cap`, `value`. The field byte ($2F/$31/$32) is
    deliberately NOT writable here: switching which parameter a handler tunes changes what the event
    IS, and that belongs in the record's own form, not in a number box."""
    at = eff.get("at") or {}
    for k in ("delta", "cap", "value"):
        if k not in changes or changes[k] is None or k not in at:
            continue
        v = int(changes[k])
        if k == "delta":                        # the sign lives in the opcode, the byte is magnitude
            v = abs(v)
        if not (0 <= v <= 255):
            raise ValueError(f"{k} must be 0..255")
        code[at[k] - base] = v
    return code


def analyze(ir) -> dict:
    """{event id: {payload, acts_on, calls, zp, size}} for one area's IR. {} when it has no grp16."""
    if not ir.get("grp16"):
        return {}
    import wl_ir
    code = wl_ir._unhex(ir["grp16"]["code"])
    base = ir["grp16"]["offset"] + RAM
    out = {}
    for ev, entry in enumerate(jump_table(code, base)):
        if not entry:                               # $0000 = this event id has no handler
            continue
        ys, calls, zps, size, resolved = trace(code, base, entry)
        out[ev] = {"entry": entry, "size": size, "payload": sorted(ys),
                   "acts_on": [f"grp{g}[{i_}]" for g, i_ in dict.fromkeys(resolved)],
                   "calls": list(dict.fromkeys(calls)),
                   "zp": [ZP[z] for z in sorted(zps)]}
    return out
