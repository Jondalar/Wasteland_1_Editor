#!/usr/bin/env python3
"""Minimal 6502 disassembler for the AREA Code tab (grp16 per-area event code).

Deliberately tiny: documented opcodes only, linear sweep from an entry point,
stops at RTS/RTI/JMP or on an undefined opcode (grp16 fragments are short; an
undefined byte almost always means we ran off the code into data). Output is
"minimal, readable" per the user's directive — address, bytes, mnemonic, and a
comment for the few KNOWN engine symbols (GRP16_SPEC.md).
"""

# mode -> operand byte count
_SZ = {"imp": 0, "acc": 0, "imm": 1, "zp": 1, "zpx": 1, "zpy": 1,
       "izx": 1, "izy": 1, "abs": 2, "abx": 2, "aby": 2, "ind": 2, "rel": 1}

_OPS: dict[int, tuple[str, str]] = {}


def _add(mn, pairs):
    for opc, mode in pairs:
        _OPS[opc] = (mn, mode)


_add("adc", [(0x69, "imm"), (0x65, "zp"), (0x75, "zpx"), (0x6D, "abs"), (0x7D, "abx"), (0x79, "aby"), (0x61, "izx"), (0x71, "izy")])
_add("and", [(0x29, "imm"), (0x25, "zp"), (0x35, "zpx"), (0x2D, "abs"), (0x3D, "abx"), (0x39, "aby"), (0x21, "izx"), (0x31, "izy")])
_add("asl", [(0x0A, "acc"), (0x06, "zp"), (0x16, "zpx"), (0x0E, "abs"), (0x1E, "abx")])
_add("bit", [(0x24, "zp"), (0x2C, "abs")])
_add("cmp", [(0xC9, "imm"), (0xC5, "zp"), (0xD5, "zpx"), (0xCD, "abs"), (0xDD, "abx"), (0xD9, "aby"), (0xC1, "izx"), (0xD1, "izy")])
_add("cpx", [(0xE0, "imm"), (0xE4, "zp"), (0xEC, "abs")])
_add("cpy", [(0xC0, "imm"), (0xC4, "zp"), (0xCC, "abs")])
_add("dec", [(0xC6, "zp"), (0xD6, "zpx"), (0xCE, "abs"), (0xDE, "abx")])
_add("eor", [(0x49, "imm"), (0x45, "zp"), (0x55, "zpx"), (0x4D, "abs"), (0x5D, "abx"), (0x59, "aby"), (0x41, "izx"), (0x51, "izy")])
_add("inc", [(0xE6, "zp"), (0xF6, "zpx"), (0xEE, "abs"), (0xFE, "abx")])
_add("jmp", [(0x4C, "abs"), (0x6C, "ind")])
_add("jsr", [(0x20, "abs")])
_add("lda", [(0xA9, "imm"), (0xA5, "zp"), (0xB5, "zpx"), (0xAD, "abs"), (0xBD, "abx"), (0xB9, "aby"), (0xA1, "izx"), (0xB1, "izy")])
_add("ldx", [(0xA2, "imm"), (0xA6, "zp"), (0xB6, "zpy"), (0xAE, "abs"), (0xBE, "aby")])
_add("ldy", [(0xA0, "imm"), (0xA4, "zp"), (0xB4, "zpx"), (0xAC, "abs"), (0xBC, "abx")])
_add("lsr", [(0x4A, "acc"), (0x46, "zp"), (0x56, "zpx"), (0x4E, "abs"), (0x5E, "abx")])
_add("ora", [(0x09, "imm"), (0x05, "zp"), (0x15, "zpx"), (0x0D, "abs"), (0x1D, "abx"), (0x19, "aby"), (0x01, "izx"), (0x11, "izy")])
_add("rol", [(0x2A, "acc"), (0x26, "zp"), (0x36, "zpx"), (0x2E, "abs"), (0x3E, "abx")])
_add("ror", [(0x6A, "acc"), (0x66, "zp"), (0x76, "zpx"), (0x6E, "abs"), (0x7E, "abx")])
_add("sbc", [(0xE9, "imm"), (0xE5, "zp"), (0xF5, "zpx"), (0xED, "abs"), (0xFD, "abx"), (0xF9, "aby"), (0xE1, "izx"), (0xF1, "izy")])
_add("sta", [(0x85, "zp"), (0x95, "zpx"), (0x8D, "abs"), (0x9D, "abx"), (0x99, "aby"), (0x81, "izx"), (0x91, "izy")])
_add("stx", [(0x86, "zp"), (0x96, "zpy"), (0x8E, "abs")])
_add("sty", [(0x84, "zp"), (0x94, "zpx"), (0x8C, "abs")])
for opc, mn in [(0x00, "brk"), (0x08, "php"), (0x18, "clc"), (0x28, "plp"), (0x38, "sec"),
                (0x40, "rti"), (0x48, "pha"), (0x58, "cli"), (0x60, "rts"), (0x68, "pla"),
                (0x78, "sei"), (0x88, "dey"), (0x8A, "txa"), (0x98, "tya"), (0x9A, "txs"),
                (0xA8, "tay"), (0xAA, "tax"), (0xB8, "clv"), (0xBA, "tsx"), (0xC8, "iny"),
                (0xCA, "dex"), (0xD8, "cld"), (0xE8, "inx"), (0xEA, "nop"), (0xF8, "sed")]:
    _OPS[opc] = (mn, "imp")
for opc, mn in [(0x10, "bpl"), (0x30, "bmi"), (0x50, "bvc"), (0x70, "bvs"),
                (0x90, "bcc"), (0xB0, "bcs"), (0xD0, "bne"), (0xF0, "beq")]:
    _OPS[opc] = (mn, "rel")

# the few engine symbols grp16 fragments touch (GRP16_SPEC.md)
KNOWN = {
    0xAC8C: "VIEW_REFRESH (redraw after map change)",
    0x8845: "grp16 dispatch",
}
KNOWN_ZP = {
    0x5F: "record base lo", 0x60: "record base hi",
    0x59: "party X", 0x5A: "party Y",
}


def disasm(raw: bytes, start: int, org: int, max_instr: int = 96) -> list[dict]:
    """Linear disassembly of raw[start:], addresses reported at `org`+offset.
    Stops after rts/rti/jmp, on an undefined opcode, or after max_instr.
    Returns [{addr, bytes, text, comment}]."""
    out = []
    i = start
    for _ in range(max_instr):
        if i >= len(raw):
            break
        opc = raw[i]
        if opc not in _OPS:
            out.append({"addr": org + (i - start), "bytes": f"{opc:02X}",
                        "text": f".byte ${opc:02X}", "comment": "undefined opcode — data?"})
            break
        mn, mode = _OPS[opc]
        n = _SZ[mode]
        if i + n >= len(raw) + (0 if n == 0 else 1):
            break
        bs = raw[i:i + 1 + n]
        addr = org + (i - start)
        o1 = raw[i + 1] if n >= 1 else 0
        o2 = raw[i + 2] if n >= 2 else 0
        w = o1 | (o2 << 8)
        comment = ""
        if mode == "imp":
            text = mn
        elif mode == "acc":
            text = f"{mn} a"
        elif mode == "imm":
            text = f"{mn} #${o1:02X}"
        elif mode == "zp":
            text = f"{mn} ${o1:02X}"
            comment = KNOWN_ZP.get(o1, "")
        elif mode == "zpx":
            text = f"{mn} ${o1:02X},x"
        elif mode == "zpy":
            text = f"{mn} ${o1:02X},y"
        elif mode == "izx":
            text = f"{mn} (${o1:02X},x)"
        elif mode == "izy":
            text = f"{mn} (${o1:02X}),y"
            comment = KNOWN_ZP.get(o1, "")
        elif mode == "rel":
            dst = addr + 2 + (o1 - 256 if o1 > 127 else o1)
            text = f"{mn} ${dst:04X}"
        elif mode == "ind":
            text = f"{mn} (${w:04X})"
        else:  # abs/abx/aby
            sfx = {"abs": "", "abx": ",x", "aby": ",y"}[mode]
            text = f"{mn} ${w:04X}{sfx}"
            comment = KNOWN.get(w, "")
        out.append({"addr": addr, "bytes": " ".join(f"{b:02X}" for b in bs),
                    "text": text, "comment": comment})
        i += 1 + n
        if mn in ("rts", "rti", "jmp"):
            break
    return out
