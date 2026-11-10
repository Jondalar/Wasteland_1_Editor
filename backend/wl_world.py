"""wl_world.py — the area's WORLD PARAMETERS: the bytes that decide how the place behaves.

Everything the editor knew about an area until now was addressed by a cell: a square, a record, a
chain. But an area also has physics, and it lives in eight descriptor bytes that no cell points at.
Three of them are the random-encounter setup — the reason a walk through Quartz feels nothing like a
walk through the desert — and they were invisible.

## What the engine does with them (traced 2026-08-02, `WB015` at ef_main_overlay.asm:8195)

Every turn — the frame loop's clock housekeeping and each movement tail:

    ldy #$2F / lda ($61),y / beq done      ; +$2F = 0 -> this area has NO random encounters
    jsr $24C4 / cmp #$01 / bne done        ; roll 1..N, only a 1 fires -> chance is 1 in N
    <find the first grp15 slot whose three foe fields are all zero; there are +$32 of them>
    ldy #$31 / lda ($61),y / jsr $24C4     ; +$31 = how many wandering TYPES -> roll = the foe id
    <foe record +4 = max group size, rolled the same way -> the count>
    sta ($5F),3 / sta ($5F),4              ; write foe + count into that free slot
    lda #$0F / sta $64 ... jmp $0A3D       ; and PLANT an op-$0F cell on the map

From there it is an ordinary encounter: `ENCOUNTER_SCAN ($94CC)` finds the op-$0F marker, gates it
on line of sight, and walks the group at the party. Which is why the shipped map data contains 366
op-$03 cells and **not one** op-$0F: op-$0F exists only at runtime. grp15 is not a list of
encounters, it is the FREE LIST those wanderers are allocated from — and `+$32` equals the grp15
slot count in every area that has any.

So `1..+$31` of the area's foe table is its wandering pool, and that is what 59 of the 90 foes no
encounter record names actually are.

## The clock fields, and the trap in them (traced 2026-08-23, `WAF32`)

`WAF32` runs on every step: `$0C += +$34`, `$0D += +$35` (one `sbc #$3C` hour carry, hours wrap at
24), then `$D2 += +$36` and the same weight into the 24-bit world clock `$04/$05/$06`. The party
heal/status pass (`WB795`: status nibble +$28, CON +$1D/$1E, MAXCON +$26/$27 halved back in) is
reached through one gate:

    lda $D2 / and #$0F / bne out / jmp WB795

`$D2` is 8 bits, so **the heal period is `16 / gcd(weight, 16)` steps** and the dial is modular,
not linear. Turning it up to 255 makes healing SIXTEEN TIMES RARER than the desert's 16 — which is
exactly what happened when Till maxed TREX Base to `step_min 255 / weight 255` to "heal faster" and
nobody healed. The shipped values are coherent: desert 16 (every step), towns 2 (every 8th),
dungeons 1 (every 16th).

## The pool is not fixed

grp16 events retune these same three bytes at runtime (`wl_grp16.effect`), and the caps prove the
intent: Mine shaft grows its pool to 16, Cochise Lv4 to 10, Cochise Core to 11, and Cochise Lv4 may
end up with 20 wandering groups at once. That is the escalation the deep levels are built on. Every
one of those events hangs off a COND gate or a chain step — so the START value belongs here, and the
CHANGE belongs on the beat that causes it. Both, not one.

## The neighbours

`+$2C`, `+$33`, `+$34/$35`, `+$36` are not encounter fields, but they are the same kind of fact —
one byte, area-wide, no cell — and the panel that hid them is the panel that made the encounter
setup invisible. (`+$30` looks like a progression tier because it rises through the game, but it is
the area's **gfx id** — the tileset FK the Data panel already offers as a dropdown.)

Writes are single-byte pokes at fixed offsets — one descriptor byte, or one immediate inside the
area's own 6502. Nothing here can change length, so no pointer moves and no record relocates. The
result still goes through the project's one save path (`_save_record` → `wl_persist.save_record`),
which re-emits the whole blob and REFUSES the save if any record the engine can dispatch would
change its bytes; that guard is what proves a poke was semantically inert.
"""
import wl_grp16
from wl_machine import RECORD_BASE as RAM   # $3400 on the C64, $D000 on the Apple

# offset → (key, label, hint, kind). Order is the panel's order.
FIELDS = [
    (0x2F, "enc_rate", "encounter chance",
     "rolled every turn; only a 1 fires, so the number is the ODDS: 30 = 1 in 30 = often, "
     "100 = 1 in 100 = rare. 0 switches random encounters off entirely.", "odds"),
    (0x31, "enc_types", "wandering foe types",
     "the roll is 1..N over this area's foe table, so foes 1..N are the wandering pool. "
     "grp16 events may grow it past the start value.", "int"),
    (0x32, "enc_slots", "wandering groups at once",
     "how many grp15 slots the engine may allocate wanderers into. Full = no new encounter "
     "until one is resolved.", "int"),
    (0x34, "step_frac", "minutes per step (fraction)",
     "added to the clock every step, in 1/256 minutes. With the field below: desert = 4 min a "
     "step, a town street = half a minute.", "int"),
    (0x35, "step_min", "minutes per step (whole)",
     "whole minutes added per step. KEEP IT UNDER 60: the engine carries the hour with a single "
     "`cmp #$3C / sbc #$3C`, so exactly one hour can roll over per step. Above 59 the minute byte "
     "never normalises and the clock drifts into nonsense.", "int"),
    (0x36, "clock_weight", "world-clock weight",
     "NOT a speed dial — bigger is not faster. It is added to an 8-bit counter and the party "
     "heals whenever that counter is a multiple of 16, so the period is 16/gcd(weight,16) steps: "
     "16 (or any multiple) = heal EVERY step, 8 = every 2nd, 4 = every 4th, 2 = every 8th, and "
     "1 or 255 = every 16th, the slowest there is. It also sets how fast the 24-bit world clock "
     "(timed events) runs. Desert 16, towns 2, dungeons 1.", "int"),
]
# NOT here: `+$33` the off-map tile. The Data panel already owns it, with a tile preview and its own
# endpoint — a second control writing the same byte is how two readers of one field drift apart.
READONLY = [
    (0x2C, "map_size", "map size", "square; the engine copies it into $55/$56 on area entry."),
]
KEY_OFF = {k: o for o, k, *_ in FIELDS}


def _odds(v: int) -> str:
    return "off — no random encounters here" if v == 0 else f"1 in {v} per turn"


def _dur(minutes: float) -> str:
    """In-game minutes as something a designer can feel."""
    if minutes <= 0:
        return "no time at all"
    if minutes < 60:
        return f"{minutes:g} min"
    h, m = divmod(int(round(minutes)), 60)
    if h < 24:
        return f"{h} h" + (f" {m:02d}" if m else "")
    d, h = divmod(h, 24)
    return f"{d} d" + (f" {h} h" if h else "")


def derive(rate: int, types: int, slots: int, step_frac: int, step_min: int, weight: int) -> dict:
    """What these six bytes MEAN in play — one implementation, used for the stored record and for
    the live preview while someone types, so the two can never drift apart.

    Everything here is read out of the engine: `WB015` for the roll, `WAF32` for the clock and the
    `$D2 & $0F` heal gate. See the module docstring and the annotations in ef_main_overlay.asm."""
    from math import gcd
    mins = step_min + step_frac / 256.0
    out, warn = {}, []

    # ── encounters — only a roll of exactly 1 fires, so the byte IS the odds
    if rate == 0:
        out["enc_rate"] = "off — no random encounters here"
    else:
        out["enc_rate"] = (f"1 in {rate} per step → about every {rate} steps"
                           + (f" = {_dur(rate * mins)} of game time" if mins else ""))
    out["enc_types"] = ("nothing wanders here" if types == 0 else
                        f"foes 1..{types} of this area's table wander")
    out["enc_slots"] = ("no slots — nothing can spawn" if slots == 0 else
                        f"{slots} wandering group{'s' if slots != 1 else ''} alive at once; when "
                        f"all {slots} are taken the roll is spent and NOTHING spawns, silently")

    # ── the clock
    out["step_frac"] = out["step_min"] = f"{mins:g} min of game time per step"
    if step_min >= 60:
        warn.append(f"minutes per step is {step_min}: the engine carries the hour with a single "
                    f"subtract of 60, so above 59 the minute byte never normalises and the clock "
                    f"drifts into nonsense. Keep it under 60.")

    # ── the heal gate: $D2 += weight (8-bit), the party heals when $D2 & $0F == 0
    if weight == 0:
        out["clock_weight"] = ("0 — the counter never moves: the party heals EVERY step or NEVER, "
                               "depending on where the counter happened to stand. World clock "
                               "frozen, so timed events never fire here.")
        warn.append("world-clock weight 0 freezes both the heal counter and the world clock.")
    else:
        period = 16 // gcd(weight, 16)
        when = ("every step" if period == 1 else f"every {period} steps")
        extra = f" = {_dur(period * mins)}" if mins else ""
        slowest = " — the SLOWEST setting there is" if period == 16 else ""
        out["clock_weight"] = (f"party heals {when}{extra}{slowest} · world clock +{weight} per "
                               f"step ({weight}x a dungeon's 1, the desert runs 16)")
        if weight > 16 and period == 16:
            warn.append(f"weight {weight} looks like a big number but heals only every 16 steps — "
                        f"the counter is 8-bit and the gate is 'multiple of 16', so what counts is "
                        f"gcd({weight},16)=1. Use 16 for every step, 8 for every 2nd, 2 for every "
                        f"8th.")
    return {"text": out, "warnings": warn}


def read(raw: bytes, slot: int) -> dict:
    """The world parameters as a form, plus the sentence they add up to."""
    def b(o):
        return raw[slot + o] if slot + o < len(raw) else 0
    dv = derive(b(0x2F), b(0x31), b(0x32), b(0x34), b(0x35), b(0x36))
    fields = [{"key": k, "off": o, "label": lab, "hint": h, "kind": kind, "value": b(o),
               "text": dv["text"].get(k)}
              for o, k, lab, h, kind in FIELDS]
    fields += [{"key": k, "off": o, "label": lab, "hint": h, "kind": "int", "value": b(o),
                "readonly": True, "text": None} for o, k, lab, h in READONLY]
    rate, types, slots = b(0x2F), b(0x31), b(0x32)
    mins = b(0x35) + b(0x34) / 256.0
    summary = (f"no random encounters · {mins:g} min a step"
               if rate == 0 else
               f"{_odds(rate)} · pool = foes 1..{types} · up to {slots} groups at once · "
               f"{mins:g} min a step")
    return {"fields": fields, "summary": summary, "warnings": dv["warnings"],
            "rate": rate, "types": types, "slots": slots, "minutes_per_step": round(mins, 4)}


def _grp16(raw: bytes, slot: int):
    """(code, base) of the area's grp16 blob, or (None, None) when it has none / is alias filler.

    Same guard as GRP16_SPEC §5: an unused group pointer is routinely filled with ANOTHER group's
    base, and disassembling that other group's DATA as code invents handlers."""
    import wl_cell
    g = wl_cell.gptr(raw, slot, 16)
    if g is None or not (0 <= g < len(raw)):
        return None, None
    others = [wl_cell.gptr(raw, slot, x) for x in range(19) if x != 16]
    others += [wl_cell._le(raw, slot + o) - RAM for o in (0, 2, 4)]
    if g in others:
        return None, None
    return raw[g:], g + RAM


def raw_events(raw: bytes, slot: int) -> list:
    """The world-parameter events, read straight from the record blob (no IR)."""
    code, base = _grp16(raw, slot)
    if code is None:
        return []
    out = []
    for ev, entry in enumerate(wl_grp16.jump_table(code, base)):
        if not entry:
            continue
        e = wl_grp16.effect(code, base, entry)
        if e:
            out.append({"event": ev, "entry": entry, "text": wl_grp16.effect_text(e), "effect": e})
    return out


def poke(raw: bytearray, slot: int, changes: dict) -> list:
    """Apply world-parameter changes in place. Returns the keys that actually changed."""
    hit = []
    for k, v in changes.items():
        if v is None or k not in KEY_OFF:
            continue
        v = int(v)
        if not (0 <= v <= 255):
            raise ValueError(f"{k} must be 0..255")
        o = slot + KEY_OFF[k]
        if o >= len(raw):
            raise ValueError(f"record too short for descriptor ${KEY_OFF[k]:02X}")
        if raw[o] != v:
            raw[o] = v
            hit.append(k)
    return hit


def poke_event(raw: bytearray, slot: int, event: int, changes: dict) -> dict:
    """Rewrite one event's delta / cap / constant in place. Length never changes, nothing relocates.

    Returns the effect as it reads AFTER the write, decoded from the patched bytes rather than from
    what was asked for — so a value the shape cannot actually hold shows up here, not in the game."""
    code, base = _grp16(bytes(raw), slot)
    if code is None:
        raise ValueError("this area has no grp16 code of its own")
    tbl = wl_grp16.jump_table(code, base)
    if not (0 <= event < len(tbl)) or not tbl[event]:
        raise ValueError(f"no handler for event {event}")
    eff = wl_grp16.effect(code, base, tbl[event])
    if not eff:
        raise ValueError(f"event {event} does not set a world parameter — it has no number form")
    for k in ("delta", "cap", "value"):
        if changes.get(k) is None or k not in (eff.get("at") or {}):
            continue
        v = abs(int(changes[k])) if k == "delta" else int(changes[k])
        if not (0 <= v <= 255):
            raise ValueError(f"{k} must be 0..255")
        raw[eff["at"][k] - RAM] = v
    code2, _ = _grp16(bytes(raw), slot)
    e2 = wl_grp16.effect(code2, base, tbl[event])
    return {"effect": e2, "text": wl_grp16.effect_text(e2) if e2 else None}


def events(ir: dict) -> list:
    """The grp16 events that change these same parameters, with the record that raises each.

    An event id is only half an answer — the player never triggers "event 2", they step on a gate.
    `raised_by` is the grp6 record carrying that id, which is what the chain view shows."""
    eff = wl_grp16.effects(ir)
    if not eff:
        return []
    by_id = {}
    for cid, rr in ((ir.get("groups") or {}).get("6") or {}).items():
        if (rr or {}).get("op") == "EVENT":
            by_id.setdefault(rr.get("event"), []).append(int(cid))
    return [{"event": ev, "text": v["text"], "effect": v["effect"],
             "raised_by": sorted(by_id.get(ev, []))}
            for ev, v in sorted(eff.items())]
