"""wl_taint6502.py — a 6502 that remembers WHERE each byte came from, so addresses find themselves.

The problem this solves: which bytes of a grp16 blob hold an address into that same blob? A static
pass can read the obvious ones out of absolute operands, and with some work it can recognise the
idiom that builds an address from two immediates. But recognising idioms is guessing — the next blob
writes its pointer some other way (through a table, an EOR, a stack round-trip) and the guess is
silently wrong.

So do not recognise anything. EXECUTE the code and watch.

Every byte of the blob is labelled with its own offset. Labels propagate through loads, stores,
transfers, the stack and arithmetic, exactly as the values do. Whenever the CPU forms an effective
address that lands INSIDE the blob, the two bytes that produced that address are — by observation,
not by pattern — a relocation site. It does not matter how they got there.

    lda $3E6C,x            operand bytes are labelled -> site, the ordinary case
    lda #$FC / sta $31     the immediates carry their own offsets; when ($31),y is later
    lda #$42 / sta $32     dereferenced the label pair comes straight back out
    clc / adc #$3E         A was untainted, so the sum carries the immediate's label

What it costs: coverage. A path never executed is never observed, so this is the LOWER bound and the
static table is the upper one — `wl_reloc.plan()` runs both and reports any disagreement. Together
they are a much stronger statement than either alone: the trace proves a site is real, the static
walk proves none was missed on an unexecuted branch.

The interpreter is written generically off the shared opcode table (`wl_disasm6502._OPS`) rather
than as a hand-listed switch, and `_selftest()` cross-checks it against `wl_6502mini` — same writes,
same registers, on all 66 shipped handlers.
"""
from wl_disasm6502 import _OPS, _SZ


def selftest(raw, code, base, desc, entries, CPU) -> list:
    """Run every handler on BOTH interpreters over one image and diff. [] = this CPU is trustworthy.

    A second 6502 written from scratch is only useful if it is right, and "it produced a plausible
    trace" is not evidence. `wl_6502mini` is the independently-written one (and is itself checked
    against the c64-re sandbox), so the two disagreeing means one of them is wrong — which is exactly
    what a taint plane sitting on top of a subtly wrong CPU would look like.
    """
    import wl_reloc
    bad = []
    for entry in entries:
        if not entry:
            continue
        a = CPU(wl_reloc.image(raw, base, len(code), code, desc))
        wr = []
        orig = a._wr
        a._wr = lambda ad, v, o=orig: (wr.append((ad & 0xFFFF, v & 0xFF)), o(ad, v))[1]
        try:
            a.run(entry, max_steps=200_000)
            aerr = None
        except Exception as e:                                  # noqa: BLE001
            aerr = type(e).__name__
        b = Taint(wl_reloc.image(raw, base, len(code), code, desc), base, len(code))
        try:
            b.run(entry, max_steps=200_000, api=None)           # no hook: match the other CPU exactly
            berr = None
        except Exception as e:                                  # noqa: BLE001
            berr = type(e).__name__
        if aerr != berr:
            bad.append(f"${entry:04X}: exit {aerr} vs {berr}")
            continue
        if aerr == "NotImplementedError":                       # mini stops early on an opcode it
            k = len(wr)                                         # lacks; compare the common prefix
            if b.writes[:k] != wr:
                bad.append(f"${entry:04X}: writes differ before the mini CPU gave up")
            continue
        if wr != b.writes:
            for i, (p, q) in enumerate(zip(wr, b.writes)):
                if p != q:
                    bad.append(f"${entry:04X}: write #{i} ${p[0]:04X}=${p[1]:02X} vs "
                               f"${q[0]:04X}=${q[1]:02X}")
                    break
            else:
                bad.append(f"${entry:04X}: write count {len(wr)} vs {len(b.writes)}")
        elif (a.a, a.x, a.y, a.c, a.z) != (b.a, b.x, b.y, b.c, b.z):
            bad.append(f"${entry:04X}: regs {a.a:02X},{a.x:02X},{a.y:02X} vs "
                       f"{b.a:02X},{b.x:02X},{b.y:02X}")
        elif a.steps != b.steps:
            bad.append(f"${entry:04X}: steps {a.steps} vs {b.steps}")
    return bad

_RMW = {"asl", "lsr", "rol", "ror", "inc", "dec"}
_LOADS = {"lda": "a", "ldx": "x", "ldy": "y"}
_STORES = {"sta": "a", "stx": "x", "sty": "y"}
# label-preserving one-byte moves
_TRANSFER = {"tax": ("a", "x"), "tay": ("a", "y"), "txa": ("x", "a"), "tya": ("y", "a"),
             "tsx": (None, "x"), "txs": ("x", None)}


class Taint:
    """Flat 64K + a parallel label plane. `sites` fills in with (lo_offset, hi_offset) pairs."""

    def __init__(self, mem, base: int, n: int):
        self.m = mem if isinstance(mem, bytearray) else bytearray(mem)
        self.lbl = [0] * 0x10000                      # 0 = untainted, else blob offset + 1
        for i in range(n):
            self.lbl[base + i] = i + 1
        self.base, self.n = base, n
        self.a = self.x = self.y = 0
        self.la = self.lx = self.ly = 0                # register labels
        self.sp = 0xFD
        self.c = self.z = self.v = self.nf = 0
        self.pc = 0
        self.steps = 0
        self.force = []                               # forced branch decisions, by branch INDEX
        self.branch_log = []                          # (pc, taken) in execution order
        self.sites = set()                            # (lo_off, hi_off) — both halves known
        self.partial = set()                          # (off, "lo"|"hi") — only one half labelled
        self.writes = []

    # ── memory ──────────────────────────────────────────────────────────────
    def _rd(self, a):
        return self.m[a & 0xFFFF]

    def _wr(self, a, v, lab=0):
        a &= 0xFFFF
        self.writes.append((a, v & 0xFF))
        self.m[a] = v & 0xFF
        self.lbl[a] = lab

    def _w16(self, a):
        return self._rd(a) | (self._rd(a + 1) << 8)

    def _claim(self, addr, lo_at, hi_at):
        """An effective address was formed from the bytes at lo_at/hi_at. Record it if it aims here."""
        if not (self.base <= addr < self.base + self.n):
            return
        lo, hi = self.lbl[lo_at], self.lbl[hi_at]
        if lo and hi:
            self.sites.add((lo - 1, hi - 1))
        elif lo:
            self.partial.add((lo - 1, "lo"))
        elif hi:
            self.partial.add((hi - 1, "hi"))

    # ── addressing ──────────────────────────────────────────────────────────
    def _ea(self, mode):
        """(effective address, label of the operand byte). Claims a site as a side effect."""
        pc = self.pc
        if mode == "imm":
            self.pc += 1
            return pc, self.lbl[pc]
        if mode in ("zp", "zpx", "zpy"):
            z = self._rd(pc)
            self.pc += 1
            if mode == "zpx":
                z = (z + self.x) & 0xFF
            elif mode == "zpy":
                z = (z + self.y) & 0xFF
            return z, self.lbl[z]
        if mode in ("abs", "abx", "aby"):
            b = self._w16(pc)
            self.pc += 2
            self._claim(b, pc, pc + 1)                # the OPERAND bytes formed it
            a = b + (self.x if mode == "abx" else self.y if mode == "aby" else 0)
            return a & 0xFFFF, self.lbl[a & 0xFFFF]
        if mode in ("izx", "izy"):
            z = self._rd(pc)
            self.pc += 1
            if mode == "izx":
                z = (z + self.x) & 0xFF
                b = self._rd(z) | (self._rd((z + 1) & 0xFF) << 8)
                self._claim(b, z, (z + 1) & 0xFF)
                a = b
            else:
                b = self._rd(z) | (self._rd((z + 1) & 0xFF) << 8)
                self._claim(b, z, (z + 1) & 0xFF)     # the ZP POINTER bytes formed it — this is the
                a = b + self.y                        # case an operand scan can never see
            return a & 0xFFFF, self.lbl[a & 0xFFFF]
        raise NotImplementedError(f"addressing mode {mode}")

    # ── flags / helpers ─────────────────────────────────────────────────────
    def _nz(self, v):
        v &= 0xFF
        self.z = int(v == 0)
        self.nf = (v >> 7) & 1
        return v

    def _push(self, v, lab=0):
        self._wr(0x100 + self.sp, v, lab)
        self.sp = (self.sp - 1) & 0xFF

    def _pull(self):
        self.sp = (self.sp + 1) & 0xFF
        a = 0x100 + self.sp
        return self._rd(a), self.lbl[a]

    def _p(self):
        return 0x30 | self.c | (self.z << 1) | (self.v << 6) | (self.nf << 7)

    def _setp(self, v):
        self.c, self.z, self.v, self.nf = v & 1, (v >> 1) & 1, (v >> 6) & 1, (v >> 7) & 1

    def _adc(self, v):
        t = self.a + v + self.c
        self.v = int(((self.a ^ t) & (v ^ t) & 0x80) != 0)
        self.c = int(t > 0xFF)
        self.a = self._nz(t)

    def _cmp(self, r, v):
        self.c = int(r >= v)
        self._nz((r - v) & 0x1FF)

    # ── run ─────────────────────────────────────────────────────────────────
    def run(self, pc: int, max_steps: int = 2_000_000, api=(0x0200, 0x0800)):
        """`api` = the engine vector page. A call into it returns immediately with A=0 rather than
        with whatever A held: the engine routines there hand back a status or a keypress, and a bare
        RTS stub leaves the caller's own value, which spins s4_id13's `jsr $02EA / cmp #$00 / bne`
        input-wait loop forever and costs the trace everything behind it."""
        SENT = 0xFFFE
        self._push((SENT - 1) >> 8)
        self._push((SENT - 1) & 0xFF)
        self.pc = pc & 0xFFFF
        self.steps = 0
        while self.steps < max_steps:
            self.steps += 1
            op = self._rd(self.pc)
            ent = _OPS.get(op)
            if ent is None:
                raise NotImplementedError(f"opcode ${op:02X} at ${self.pc:04X}")
            mn, mode = ent
            self.pc = (self.pc + 1) & 0xFFFF

            if mode == "rel":
                d = self._rd(self.pc)
                self.pc += 1
                take = {"bcc": not self.c, "bcs": self.c, "beq": self.z, "bne": not self.z,
                        "bmi": self.nf, "bpl": not self.nf, "bvs": self.v, "bvc": not self.v}[mn]
                i = len(self.branch_log)
                if i < len(self.force):               # path exploration: this branch is FORCED
                    take = self.force[i]
                self.branch_log.append(((self.pc - 2) & 0xFFFF, bool(take)))
                if take:
                    self.pc = (self.pc + (d - 256 if d & 0x80 else d)) & 0xFFFF
                continue
            if mn == "jmp":
                if mode == "abs":
                    t = self._w16(self.pc)
                    self._claim(t, self.pc, self.pc + 1)
                    self.pc = t
                else:                                  # jmp (abs) — the POINTED-AT word is the site
                    p = self._w16(self.pc)
                    self._claim(self._w16(p), p, p + 1)
                    self.pc = self._w16(p)
                continue
            if mn == "jsr":
                t = self._w16(self.pc)
                self._claim(t, self.pc, self.pc + 1)
                if api and api[0] <= t < api[1]:
                    self.pc += 2
                    self.a, self.la = 0, 0
                    self._nz(0)
                    continue
                r = (self.pc + 1) & 0xFFFF
                self._push(r >> 8)
                self._push(r & 0xFF)
                self.pc = t
                continue
            if mn == "rts":
                lo, llo = self._pull()
                hi, lhi = self._pull()
                if (hi << 8 | lo) == 0xFFFD:
                    return self.steps
                t = ((hi << 8 | lo) + 1) & 0xFFFF
                if llo and lhi:                        # the push-an-address-and-RTS trick
                    self._claim(t, 0, 0)
                self.pc = t
                continue
            if mn == "rti":
                p, _ = self._pull()
                self._setp(p)
                lo, _ = self._pull()
                hi, _ = self._pull()
                self.pc = (hi << 8 | lo) & 0xFFFF
                continue

            if mode in ("imp", "acc"):
                self._implied(mn)
                continue

            ea, lab = self._ea(mode)
            if mn in _LOADS:
                v = self._rd(ea)
                setattr(self, _LOADS[mn], self._nz(v))
                setattr(self, "l" + _LOADS[mn], lab)
            elif mn in _STORES:
                r = _STORES[mn]
                self._wr(ea, getattr(self, r), getattr(self, "l" + r))
            elif mn in _RMW:
                v = self._rd(ea)
                v = self._shift(mn, v)
                self._wr(ea, v, lab)                   # a shifted/incremented byte keeps its origin
            elif mn == "adc":
                self._adc(self._rd(ea))
                self.la = self.la or lab               # untainted A + tainted operand -> the operand
            elif mn == "sbc":
                self._adc(self._rd(ea) ^ 0xFF)
                self.la = self.la or lab
            elif mn in ("and", "ora", "eor"):
                v = self._rd(ea)
                self.a = self._nz({"and": self.a & v, "ora": self.a | v, "eor": self.a ^ v}[mn])
                self.la = self.la or lab
            elif mn == "cmp":
                self._cmp(self.a, self._rd(ea))
            elif mn == "cpx":
                self._cmp(self.x, self._rd(ea))
            elif mn == "cpy":
                self._cmp(self.y, self._rd(ea))
            elif mn == "bit":
                v = self._rd(ea)
                self.z = int((self.a & v) == 0)
                self.nf, self.v = (v >> 7) & 1, (v >> 6) & 1
            else:
                raise NotImplementedError(f"{mn} {mode} at ${self.pc:04X}")
        raise RuntimeError(f"max_steps at ${self.pc:04X}")

    def _shift(self, mn, v):
        if mn == "asl":
            self.c = (v >> 7) & 1
            return self._nz(v << 1)
        if mn == "lsr":
            self.c = v & 1
            return self._nz(v >> 1)
        if mn == "rol":
            c = self.c
            self.c = (v >> 7) & 1
            return self._nz((v << 1) | c)
        if mn == "ror":
            c = self.c
            self.c = v & 1
            return self._nz((v >> 1) | (c << 7))
        return self._nz(v + (1 if mn == "inc" else -1))

    def _implied(self, mn):
        if mn in _TRANSFER:
            src, dst = _TRANSFER[mn]
            if mn == "txs":
                self.sp = self.x
                return
            v = self.sp if src is None else getattr(self, src)
            lab = 0 if src is None else getattr(self, "l" + src)
            setattr(self, dst, self._nz(v))
            setattr(self, "l" + dst, lab)
        elif mn in _RMW:                               # accumulator form
            self.a = self._shift(mn, self.a)
        elif mn == "pha":
            self._push(self.a, self.la)
        elif mn == "pla":
            self.a, self.la = self._pull()
            self._nz(self.a)
        elif mn == "php":
            self._push(self._p())
        elif mn == "plp":
            v, _ = self._pull()
            self._setp(v)
        elif mn == "inx":
            self.x = self._nz(self.x + 1)
        elif mn == "dex":
            self.x = self._nz(self.x - 1)
        elif mn == "iny":
            self.y = self._nz(self.y + 1)
        elif mn == "dey":
            self.y = self._nz(self.y - 1)
        elif mn == "clc":
            self.c = 0
        elif mn == "sec":
            self.c = 1
        elif mn == "clv":
            self.v = 0
        elif mn in ("cli", "sei", "cld", "sed", "nop"):
            pass
        else:
            raise NotImplementedError(f"implied {mn}")
