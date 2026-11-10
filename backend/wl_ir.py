"""wl_ir.py — the JSON-IR (step 1 of the serializer: SERIALIZER_SPEC.md).

`record_to_ir(raw, side, area)` decodes an area RECORD blob into the canonical JSON-IR document
described in SERIALIZER_SPEC.md §3 — semantic fields, id/index-based cross-refs, never raw offsets.
This is the DECODE half; the SERIALIZE half (IR → bytes) is step 2. The IR must be LOSSLESS enough
that a future serializer reproduces a decode-EQUIVALENT record, so any op whose byte format is not
fully modelled falls back to {"op":"RAW","raw":"hex:…"} (the spec's opaque-raw rule) rather than
dropping information.

Cross-refs: string refs are indices into `strings`; record refs are (op-nibble, dispatch-id) pairs;
foe refs are 1-based indices. The serializer is the ONLY thing that ever computes byte offsets.
"""
import wl_cell
import wl_area
import wl_reloc
import wl_text5_encode as tenc

from wl_machine import RECORD_BASE as RAM   # $3400 on the C64, $D000 on the Apple

# The IR document is written to disk next to every record (.json beside the .bin) and read back in
# preference to decoding the bytes, because it is ~15x cheaper. That is only sound while the two
# agree — and a decoder change silently breaks that: Highpool's stored document still held the
# PRE-TYPED encounter shape ("data": hex) from before op-3 got real fields, so every view reading
# the .json saw `msg: null, slots: null` while the .bin decoded fine. Bump this whenever the emitted
# SHAPE changes, and a stored document from an older decoder is re-decoded instead of believed.
IR_VERSION = 4        # 4: SAY carries its trailing advance (the cell pair WB105 reads)
                      #    and every trailing advance is sized by the LAYOUT, not by its value.
                      # 3: the blind-copy hex fields were named — SAY.messages, OBJ.msg/tile/solid,
                      #    TRANSITION.next, LOOT's implied terminator, KEYWORD.cancel, the SERVICE
                      #    families, the HIREABLE sheet, EVENT.next/args. Same bytes, new shape.
_OP_NAME = {0x1: "SAY", 0x2: "COND", 0x3: "ENCOUNTER", 0x4: "OBJ", 0x5: "LOOT", 0x6: "EVENT",
            0x8: "KEYWORD", 0x9: "HAZARD", 0xA: "TRANSITION", 0xB: "BUMP", 0xC: "PATCH"}


def _hex(b) -> str:
    return "hex:" + bytes(b).hex()


def _adv(raw, off):
    """Read a rewrite-cell advance edge [next-op][next-id] at `off`, BYTE-FAITHFULLY. op >= $10 = a
    static stop (the engine ignores the id, but the record keeps whatever byte was there — preserve it
    so the round-trip is exact). Returns {"op":nib,"id":id} or None only if the slot is out of range."""
    if off is None or not (0 <= off + 1 < len(raw)):
        return None
    return {"op": raw[off], "id": raw[off + 1]}


def _adv_tail(raw, off, stop=None):
    """A TRAILING advance — the last field of a record. Unlike the fixed-position ones (COND
    done/alt at +4/+6, LOOT advance at +0), a trailing advance can be ONE byte: the engine reads
    the id only for a real link, so a stop ($FF/>=$10) needs no second byte and the record may
    simply end there. Emitting a padded 2-byte stop made every stop-record 1 byte too long —
    invisible to a byte-compare (the neighbour supplied the same byte) but caught by the walk.

    MAY be one byte, not IS: `stop` (the record's end, from _section_end) is what decides. The
    shipped data holds both forms — s1 a10 grp8[4] ends `ff ff` and a value rule shortened it to
    `ff`, while 480 SAYs really do store a single $FF. Where the layout is known it is the
    authority; the value rule is only the fallback for callers that have no extent."""
    if off is None or not (0 <= off < len(raw)):
        return None
    if stop is not None:
        if off >= stop:
            return None                              # the record ends before the advance: none stored
        if off + 1 >= stop:
            return {"op": raw[off]}
        return {"op": raw[off], "id": raw[off + 1]}
    op = raw[off]
    if op >= 0x10:
        return {"op": op}                            # stop — no id byte follows
    return {"op": op, "id": raw[off + 1] if off + 1 < len(raw) else 0}


def _msg_end(raw, rec):
    """Offset just past a bit7-terminated message that starts at `rec` (SAY/PATCH-tail walkers)."""
    j = rec
    while j < len(raw) and not (raw[j] & 0x80):
        j += 1
    return j + 1


_EVENT_READS_CACHE = []                              # [(raw, slot, {event: [offsets]})] — MRU 4


def _event_reads(raw, slot, event):
    """Which payload offsets the area's grp16 handler for `event` reads. [] when unknown.

    Derived once per blob from the handler code (wl_grp16.analyze), then reused: an area has one
    grp16 and up to eight events, so decoding the record no longer has to guess at its shape."""
    for ent in _EVENT_READS_CACHE:
        if ent[0] is raw and ent[1] == slot:
            return ent[2].get(event, [])
    try:
        import wl_grp16
        g16 = wl_cell.gptr(raw, slot, 16)
        if g16 is None:
            m = {}
        else:
            lo, hi = grp16_extent(raw, slot)
            m = {ev: f["payload"] for ev, f in wl_grp16.analyze(
                {"grp16": {"offset": lo, "code": _hex(raw[lo:hi])}}).items()}
    except Exception:
        m = {}
    _EVENT_READS_CACHE.insert(0, (raw, slot, m))
    del _EVENT_READS_CACHE[4:]
    return m.get(event, [])


_BOUNDS_CACHE = []                                   # [(raw_object, slot, bounds)] — MRU, 4 deep


def _bounds(raw, slot):
    """_section_bounds, memoised per blob+slot (the decoders call it per record).

    Keyed by the OBJECT (kept alive by this list), never by id(): CPython recycles the id of a freed
    bytes object, so a loop over blobs handed record N+1 the bounds of record N — silently wrong
    SERVICE/EVENT extents."""
    for ent in _BOUNDS_CACHE:
        if ent[0] is raw and ent[1] == slot:
            return ent[2]
    b = wl_cell._section_bounds(raw, slot)
    _BOUNDS_CACHE.insert(0, (raw, slot, b))
    del _BOUNDS_CACHE[4:]
    return b


_HIRE_UNKNOWN = (0x2A, 0x2E, 0x2F, 0xBC)         # position known, MEANING not — see _hireable_named


def _pair_run(d, base, n):
    """A [id][value] slot array: (contiguous run from slot 0, {slot: entry} for stragglers behind it).

    Both member lists end at the first id $00 — but a slot BEHIND that zero still occupies bytes, so
    a decoder that only kept the run would drop them silently. None occur in the shipped records
    (the skills' slot 29 is the one exception and it has its own name), and the split keeps the
    codec total rather than merely lucky."""
    run = 0
    while run < n and d[base + 2 * run]:
        run += 1
    head = [{"id": d[base + 2 * k], "n": d[base + 2 * k + 1]} for k in range(run)]
    tail = {str(k): {"id": d[base + 2 * k], "n": d[base + 2 * k + 1]}
            for k in range(run, n) if d[base + 2 * k]}
    return head, tail


def _hireable_named(d) -> dict:
    """The 256-byte party-member sheet, field by field. `d` is exactly $100 bytes.

    Offsets confirmed against the engine's own reads/writes through the member pointer $66/$67,
    not against the docs — the docs disagree with the bytes in two places and lose:
      $1A is ARMOUR CLASS, not level. equip_slot_set writes it together with the armour slot $25
           and equip_slot_clear zeroes it when $25 is cleared; the char sheet prints it as a number.
      $24 is LEVEL. Over the 15 shipped hireables it is strictly monotone in XP (200->1 … 50000->10),
           and ef_charpoint_overlay reads ($66),$24, computes the next threshold and writes it back.
      wl_party.py decodes the same 256-byte sheet and now agrees; the two field maps are one map.
    Four bytes keep no name at all ($2A, $2E, $2F, $BC): nothing in the disassembled tree reads them
    and they are constant or near-constant in the corpus. They stay a hex string ON PURPOSE, so the
    gate keeps counting them until somebody identifies them.
    """
    le = lambda o, n: int.from_bytes(d[o:o + n], "little")
    skills, sk_extra = _pair_run(d, 0x80, 29)        # 29, not 30: slot 29 is `token`, see below
    inv, inv_extra = _pair_run(d, 0xBD, 30)
    rank = d[0x32:0x80]
    return {
        "op": "HIREABLE",
        "name": d[:14].rstrip(b"\x00").decode("latin-1"),
        "attrs": list(d[0x0E:0x15]),                 # ST IQ LK SP AGL DEX CHR
        "money": le(0x15, 3), "sex": d[0x18], "nationality": d[0x19], "ac": d[0x1A],
        "maxcon": le(0x1B, 2), "con": le(0x1D, 2),   # CON is 16-bit and signed (bit7 of +$1E = down)
        "weapon": d[0x1F],                           # INDEX into the inventory list, not an item id
        "skill_points": d[0x20], "xp": le(0x21, 3), "level": d[0x24],
        "armour": d[0x25],                           # ALSO an index, same rule: equip_slot_set feeds
        # both slot bytes through W14F8 (`asl / clc / adc #$BB`, so 1 = inventory slot 0) and stores
        # the byte back at +$1F or +$25. It is why TREX, Dr. Mike and Vax all carry `armour: 1` with
        # AC 5/1/14 — one index, three inventories. Read as an item id it equips the wrong thing.
        # a 16-BIT pair, not two bytes: the post-damage path stores $027A/$027B into +$26/+$27 back
        # to back. "overkill" is the documented name; the 16-bit SHAPE is the measured part.
        "overkill": le(0x26, 2),
        "conditions": d[0x28],                       # ailment bitfield — ORed, tested and cleared
        "status": d[0x29],                           # by the doctor's Cure path
        # the hire flow moves these three: joining decrements +$2B/+$2C/+$2D X times (WA4A4),
        # refusing increments each by one (WA4E0). WHAT they count is not established.
        "hire_counters": list(d[0x2B:0x2E]),
        "join_line": d[0x30],                        # str id printed on joining (WA47A)
        "hire_gate": d[0x31],                        # 0 = joins unconditionally; else a reaction roll
        "rank": rank[:rank.find(b"\x00") if b"\x00" in rank else len(rank)].decode("latin-1"),
        "skills": skills, "skills_extra": sk_extra,
        # slot 29 of the skill array carries [id 36..48][$FF] in 10 of the 15 hireables — above the
        # `cmp #$24 / bcs` the char sheet and the name menu filter on, so it is INVISIBLE but
        # findable by member_skill_lookup. op-2 conditions test exactly these ids ("I see you've
        # rescued Mayor Pedros" checks 36, and Pedros carries 36), so it marks WHO is in the party.
        "token": {"id": d[0xBA], "n": d[0xBB]} if d[0xBA] else None,
        "inventory": inv, "inventory_extra": inv_extra,
        "unknown": _hex(bytes(d[o] for o in _HIRE_UNKNOWN)),
    }


def _ascii_z(b, i) -> str:
    """The $00-terminated latin-1 string starting at `b[i]` (the bank/revive payload form)."""
    k = b.find(b"\x00", i)
    return b[i:k if k >= 0 else len(b)].decode("latin-1")


def _service_named(base, fam, f, t):
    """SERVICE `fields`+`tail` → per-family named fields, or None when the family has no model.

    The field map was read out of the shipped scene blobs, not the doc: every `ldy #n / lda ($5F),y`
    in all 17 s*_scene_id*.bin was collected and matched against what the surrounding code does with
    the byte. The families split three ways at +$03 (nf = service_name_span(fam)[0] - 3) and again
    in the tail, which begins right after the 11-char label and its $00 — at +$13 for the nf=4
    families, which is exactly why "the portrait is at +$13" is true for 5/6/7/8/9 and meaningless
    for 0/1/2/3 (those records end at or before it).
    """
    d = dict(base)
    if fam in (0, 6):                                # DOC / INFIRMARY — prices the handler charges
        if len(f) < 4:                               # +$04 per CON point healed, +$05 per exam,
            return None                              # +$06 per disease cured (blob strings 11/14/20)
        d.update(greeting=f[0], heal_price=f[1], exam_price=f[2], cure_price=f[3])
        if fam == 6:                                 # the doc blob patched to read its portrait from
            if len(t) != 1:                          # the RECORD ($CA15 `A9 00` -> `B1 5F`) — one
                return None                          # byte of tail, no terminator
            d["portrait"] = t[0]
    elif fam in (1, 5):                              # STORE — +$03/+$04 are the two price shifts
        if len(f) < 4 or not t:                      # ($74/$73), +$06 picks the areatrack stock
            return None                              # table the shop loads (jsr $03BC)
        d.update(buy_shift=f[0], sell_shift=f[1], greeting=f[2], areatrack=f[3])
        cls = t[1:] if fam == 5 else t               # fam 5 = the store blob patched to read a
        if fam == 5:                                 # per-record portrait, which pushes the class
            d["portrait"] = t[0]                     # list one byte later ($CA29 `#$13` -> `#$14`)
        if not cls or cls[-1] != 0xFF:
            return None
        d["classes"] = list(cls[:-1])
    elif fam == 2:                                   # LIBRARY / SKILL TEACHER — one field, then an
        if len(f) < 1 or not t or t[-1] != 0xFF:     # $FF-terminated id list at +$10, each entry
            return None                              # gated against member record +$0F ($CB5D)
        d.update(greeting=f[0], skills=list(t[:-1]))
    elif fam == 3:                                   # RANGER CENTER — reads nothing but the label
        if f or t:
            return None
    elif fam == 7:                                   # BANK — +$05/+$06 = a 16-bit LE amount handed
        if len(f) < 4 or len(t) < 3 or t[-1] != 0:   # to the money-add ($1C/$1D, jsr $1366). The
            return None                              # confirmation text is read at a FIXED +$15, so
        d.update(greeting=f[0], prompt=f[1], amount=f[2] | (f[3] << 8),   # the byte at +$14 exists
                 portrait=t[0], pad=t[1], confirm=_ascii_z(t, 2))         # only to put it there
    elif fam == 8:                                   # REVIVE — +$04/+$05/+$06 are OFFSETS from the
        if len(f) < 4 or not t:                      # RECORD base to three $00-terminated strings
            return None                              # (prompt / on success / on refusal, $CA9C
        offs = list(f[1:4])                          # `lda ($5F),y / clc / adc $5F`). 0 = no string.
        d.update(greeting=f[0], portrait=t[0], str_offsets=offs,
                 texts=[_ascii_z(t, o - 0x13) if o else "" for o in offs])
    elif fam == 9:                                   # TALK — +$04 is the PAGE COUNT the render loop
        if len(f) < 4 or not t:                      # bounds itself with (`ldy #$04 / cmp ($5F),y`)
            return None
        d.update(spare_03=f[0], page_count=f[1], spare_05=f[2], spare_06=f[3],
                 portrait=t[0], pages=list(t[1:]))
    else:
        return None                                  # unmodelled family — keep the hex triple
    return d


def _decode_op_record(raw, slot, op, cid):
    """One group record → a typed IR dict, or a RAW opaque fallback. Advance slots verified this
    session (OP2_COND_TRADE / OP8_KEYWORD / write_script_chain)."""
    rec = wl_cell.subptr(raw, slot, op, cid)
    if rec is None or not (0 <= rec < len(raw)):
        return {"op": "DANGLE"}
    r = raw
    # Where this record ENDS — the next live structure. Every trailing advance is read against it,
    # so its length comes from the layout instead of from a rule about its value.
    stop = wl_cell._section_end(_bounds(r, slot), rec, len(r))
    if op == 0x1:                                   # SAY — a message list AND a trailing advance
        # The message run is one byte per string id, bit7 on the LAST; the terminator is POSITIONAL
        # and re-derived on emit ($80 is an ordinary record — one message, string id 0).
        #
        # The run is NOT the whole record. `scene_op_say ($BDE9)` ends
        #     tya            ; A = the message COUNT
        #     jmp WB105      ; -> ldx $57 / ldy $58 / jmp $042E map_cell_resolve_escape
        # and $5F still points at THIS record, so map_cell_resolve_escape does `tay / lda ($5F),y`
        # — it reads the byte at record+count, i.e. the byte right after the terminator. That is
        # the ordinary trailing advance pair every other op carries: [nibble][id] written into both
        # map layers at the party cell (AREA_MODEL.md "Branch = cell mutation"), with the same
        # escapes _adv_tail already knows — a bit7 nibble writes nothing ($0A3D `lda $64 / bmi`),
        # so it stores ONE byte and the id is never read.
        #
        # Modelling the run as the whole record dropped that byte on every emit: 480 of the 916
        # shipped SAYs stop with $FF, the other 318 carry a real (op, id) — and the pair that was
        # dropped is the one that opens the door / changes the terrain on the next step. Highpool.
        # The extent guard is what the earlier "there IS no branch" reading was missing: when the
        # next structure starts at the terminator the record genuinely has no pair, and reading two
        # bytes there read the NEIGHBOUR (that is where "a branch to op 255" came from).
        end = _msg_end(r, rec)
        d = {"op": "SAY", "messages": [b & 0x7F for b in r[rec:end]]}
        d["next"] = _adv_tail(r, end, stop)
        return d
    if op == 0xB:                                   # BUMP  [str][next-op][next-id]  (byte0 bit7 = a
        return {"op": "BUMP", "msg": r[rec], "next": _adv_tail(r, rec + 1, stop)}  # flag byte kept faithful
    if op == 0x5:                                   # LOOT [next-op][next-id][item,qty ..][$FF]
        # The terminator has exactly ONE legal value and therefore no longer needs storing:
        # LOOT_RESOLVE_AND_PICKUP ($9194) exits only on `cmp #$FF`; its `beq` on $00 is the
        # CONTINUE path, and it has to be — taking an item writes $00 over its id IN the record
        # ($92C7), so a $00-terminated list would truncate itself at the first pickup. All 480
        # shipped sacks leave the walker through the $FF branch. `term_raw` keeps the walker's
        # never-observed 2-byte form expressible instead of silently rewriting it.
        items, term, _ = wl_cell.loot_entries(r, rec)   # ONE walker, shared with the editor's label
        d = {"op": "LOOT", "advance": _adv(r, rec), "items": items}
        if term != b"\xff":
            d["term_raw"] = _hex(term)
        return d
    if op in (0x3, 0xF):                            # ENCOUNTER — 10 typed bytes + victory advance
        # grp15 (op $F) uses the SAME 12-byte struct: it is the area's encounter SLOT table, walked
        # by the engine over [0, desc+$32) rather than by a cell. Shipped almost empty — of 124
        # records only +2 (the type/message id) and +9 bit0 carry data — because ENCOUNTER_SPAWN_BUILD
        # WRITES the rolled foe/count back into the record at runtime ($9779/$9789 sta ($77),y).
        # Field map read out of the consumers in ef_main_overlay (not guessed):
        #   +0/+1  two DISTANCE gates. ENCOUNTER_SCAN calls $04CF -> $08F5, which computes
        #          |Y-$58|*9 + |X-$57| (party at $57/$58) and only proceeds while that distance is
        #          strictly GREATER than each byte ($95D0/$960E/$97B0: bcc+beq skip).
        #   +2     announce string id -> jsr $0317 text_render_window ($9445).
        #   +3/+4, +5/+6, +7/+8  THREE foe slots. The engine indexes them through the tables
        #          WBAE9 = $03,$05,$07 (foe type) and WBAE6 = $04,$06,$08 (count); a count of 0 ends
        #          the slot ($9459 beq). bit7 on EITHER byte means "roll it": $9420 masks $7F, calls
        #          the RNG ($0242 -> $24C4) and writes the result BACK into the record.
        #   +9     flags + NPC index: $9E5D tests bit0, $9EF6 bit1, WABC0 returns the HI NIBBLE as a
        #          grp17 index, and WABD6 sets bit0 / clears bit1 in place (a peaceful group turning
        #          hostile). In the shipped data bit0 is set on 585 of 592 records.
        d = r[rec:rec + 10]
        if len(d) < 10:
            return {"op": "RAW", "raw": _hex(d)}
        slots = []
        for k in range(3):
            f, c = d[3 + k * 2], d[4 + k * 2]
            slots.append({"foe": f & 0x7F, "foe_roll": bool(f & 0x80),
                          "count": c & 0x7F, "count_roll": bool(c & 0x80)})
        return {"op": "ENCOUNTER", "dist_a": d[0], "dist_b": d[1], "msg": d[2],
                "slots": slots,
                "flags": {"active": bool(d[9] & 1), "peaceful": bool(d[9] & 2),
                          "bit2": bool(d[9] & 4), "bit3": bool(d[9] & 8), "npc": d[9] >> 4},
                "victory": _adv_tail(r, rec + 10, stop)}
    if op == 0x4:                                   # OBJ  [msg][solid|tile][next-op][next-id]
        # byte0 is a string id read UNMASKED — WBE1E is `lda ($5F),y / beq rts / jmp $031D`, so $00
        # is "say nothing", not string 0, and there is no flag bit in it (bit7 clear in all 235
        # shipped records). byte1 has TWO consumers reading the SAME byte: dmc_type4_object draws
        # `and #$7F` (so tile $00 is a real tile, not "none"), W9171 branches on bit7 (`bpl` =
        # passable). Tile values span 0..95 = exactly wl_tiles.NUM_TILES, none out of range.
        d = r[rec:rec + 2]
        if len(d) < 2:                              # pointer runs off the blob — keep it verbatim,
            return {"op": "OBJ", "data": _hex(d),   # a named pair would emit 2 bytes where the
                    "use": _adv_tail(r, rec + 2, stop)}  # record only ever had 1
        return {"op": "OBJ", "msg": d[0], "tile": d[1] & 0x7F, "solid": bool(d[1] & 0x80),
                "use": _adv_tail(r, rec + 2, stop)}
    if op == 0x11:                                  # HIREABLE NPC — a full 256-byte party-member
        # record, identical in layout to a member at $F400+n*$100. grp17 is reached by an
        # ENCOUNTER's +9 hi-nibble, never by a cell. Field map in _hireable_named.
        d = bytes(r[rec:rec + 0x100])
        if len(d) < 0x15:
            return {"op": "RAW", "raw": _hex(d)}
        legacy = {"op": "HIREABLE", "name": d[:14].rstrip(b"\x00").decode("latin-1"),
                  "attrs": list(d[0x0E:0x15]), "rest": _hex(d[0x15:])}
        if len(d) != 0x100:                          # a slot at the very end of the blob: the named
            return legacy                            # form is a fixed 256 B and would grow it
        named = _hireable_named(d)
        # VERIFY-OR-FALL-BACK, same discipline as SERVICE. It is not theoretical: the descrambled
        # party save carries a rank field with no $00 terminator at all, and the named form cannot
        # reproduce that. Such a record keeps `rest` instead of losing bytes.
        try:
            ok = _emit_op_record(named) == d
        except Exception:
            ok = False
        return named if ok else legacy
    if op == 0x9:                                   # HAZARD  [msg][dice][adv]
        # $82E2 SWEEP/XP: +0 message, +1 the value applied to every member, then the SAME trailing
        # advance every other op carries (AREA_MODEL.md op 9: "+2/+3 branch-pair"). Modelling only
        # the first two bytes dropped it, exactly as the SAY model did.
        return {"op": "HAZARD", "msg": r[rec], "dice": r[rec + 1] if rec + 1 < len(r) else 0,
                "next": _adv_tail(r, rec + 2, stop)}
    if op == 0x2:                                   # COND/TRADE  (RECORD_FORMAT + OP2_COND_TRADE.md);
        if rec + 9 >= len(r):                        # byte-faithful: b0 = $80|flags, prompt/succ/fail
            return {"op": "RAW", "raw": _hex(r[rec:rec + 12])}   # kept full (bit7 may be set)
        conds, j = [], rec + 0x0A
        while j + 1 < len(r) and r[j] != 0xFF and len(conds) < 64:
            conds.append({"opb": r[j], "arg": r[j + 1]})   # opb: cop=opb>>5, difficulty=opb&$1F (display)
            j += 2
        j += 1                                            # past the $FF
        # b0 bit3 = PER-CONDITION BRANCHES: one [op][id] pair per cond follows the $FF terminator.
        # Measured over all 43 baselines: bit3 clear -> the record ends at the $FF (628/628); bit3
        # set -> it ends 2N bytes later (76/79 exact, 3 within the usual overlap slack). Stopping at
        # the $FF cut 12 bytes off Highpool's grp2[15] — the engine then read the NEXT record's
        # bytes as its branch table. Found by the engine-resolve E2E, not by any round-trip.
        d = {"op": "COND", "b0": r[rec], "prompt": r[rec + 1], "success": r[rec + 2],
             "fail": r[rec + 3], "done": {"op": r[rec + 4], "id": r[rec + 5]},
             "alt": {"op": r[rec + 6], "id": r[rec + 7]},
             # effect = a write into the MEMBER record: target = the offset, bit7 = "literal".
             # The three offsets that occur, all identified from their consumers:
             #   $1D  CON, the health byte (114 records) — $9D/$1D with $81..$85 = damage/heal
             #   $15  GOLD, a 3-byte little-endian field ($15/$16/$17): member_gold_add_24bit /
             #        member_gold_saturate_ff clamp it to $FF FF FF, and W157E pools it between two
             #        members. The cheat parties carry 555555 there, fresh ones 0.
             #   $21  EXPERIENCE, also 3 bytes ($21/$22/$23), read through API vector $038C.
             #        COMBAT_INIT ($B1A3) backs the triple up per member into $F487+, and the
             #        post-combat pass loads it again and SUBTRACTS the backup — the difference is
             #        what the fight earned. Zero in every fresh party, 48-59 in a played save.
             "effect": {"target": r[rec + 8], "value": r[rec + 9],
                        "member_offset": r[rec + 8] & 0x7F, "literal": bool(r[rec + 8] & 0x80),
                        "amount": r[rec + 9] & 0x7F, "amount_literal": bool(r[rec + 9] & 0x80),
                        "field": {0x1D: "con", 0x15: "gold", 0x21: "experience"}.get(
                            r[rec + 8] & 0x7F)},
             "conds": conds}
        if r[rec] & 0x08:
            d["branches"] = [_adv(r, j + k * 2) for k in range(len(conds))]
        return d
    if op == 0xC:                                   # PATCH  [msg][ [flags][dx][dy][op][id] xN ][adv]
        # BOUNDED by the record's own extent, not only by the bit7 flag. 427 of the 444 shipped
        # patch records do end on a bit7 entry; the other 17 do not, and an unbounded walk ran
        # straight through their neighbours — s2 a1's grp12[0] is a 12-byte record and the walk
        # took 351 bytes, inventing 68 entries out of the records behind it and re-emitting them
        # as its own. Same discipline as the COND branch table above: the layout decides the end.
        ents, j = [], rec + 1
        lim = min(len(r), stop)
        while j + 4 < lim:
            flags = r[j]
            ents.append({"flags": flags & 0x7F, "dx": r[j + 1], "dy": r[j + 2],
                         "op": r[j + 3], "id": r[j + 4]})
            j += 5
            if flags & 0x80:
                break
            if len(ents) >= 128:
                break
        return {"op": "PATCH", "msg": r[rec], "entries": ents, "after": _adv_tail(r, j, stop)}
    if op == 0xA:                                   # TRANSITION  [b0][X][Y][target] + the CELL-REWRITE
        # ARG at +4. Not a transition-specific structure: the op-A handler ($89B0) does `lda #$04 /
        # jsr WB105`, i.e. it hands offset 4 to map_cell_resolve_escape ($042E), the same resolver
        # BUMP/COND/PATCH feed. So the byte means what it means everywhere else — which is exactly
        # _adv_tail: $FD/$FE swap the cell back from the saved $AD/$AE pair (68 records), $FF leaves
        # the cell alone (404), anything < $10 rewrites the door cell to that record (151, and a
        # SERVICE door's [$06][cid] is the ONLY reference a shop/doc has — emitting a hard $FF here
        # cut the service off: the door printed "Entering the infirmary." and then did nothing).
        # EXACTLY ONE pair: looping while the byte stayed < $10 walked into the next records
        # (s2_id27[2] grew a 14-byte "tail", eating cids 3/4/5) and made the decode depend on the
        # NEIGHBOUR's first byte.
        b0 = r[rec]
        return {"op": "TRANSITION", "b0": b0,
                "label": (b0 - 0x40) if 0x40 <= b0 <= 0x7F else None,
                "x": r[rec + 1] if rec + 1 < len(r) else 0,
                "y": r[rec + 2] if rec + 2 < len(r) else 0,
                "target": r[rec + 3] if rec + 3 < len(r) else 0,
                "next": _adv_tail(r, rec + 4, stop)}
    if op == 0x8:                                   # KEYWORD  (OP8_KEYWORD.md)
        kw_ids, j = [], rec + 3
        while j < len(r):
            kw_ids.append(r[j] & 0x7F)
            if r[j] & 0x80:
                break
            j += 1
        bt = j + 1
        kws = []
        for k, kid in enumerate(kw_ids):
            kws.append({"str": kid, "branch": _adv(r, bt + k * 2)})
        # …and ONE trailing advance after the branch table, exactly like the other chainable ops
        # (1 byte when it is a stop, 2 for a real link). Measured: 180 of 297 KEYWORD records end
        # exactly there, 0 end at the branch table. Omitting it truncated 4 records whose next
        # record then overwrote the engine's continuation — caught by the engine-resolve E2E.
        # `after` is ALSO the NO-MATCH slot, and that is arithmetic, not a guess: a word that matches
        # nothing exhausts the compare loop with the counter at K, so $88E4 hands WB105
        # A = 2K + (3 + K) = 3 + 3K — the byte one past the K-entry branch table, exactly here.
        # 41 of its 90 live edges point at a SAY, which is the "that's not it" line.
        # +$01/+$02 is the CANCEL edge, and only that: `lda #$01 / jmp WB105` (W899F) is reached from
        # three places, all BEFORE the loop — RETURN $8D, ESC $9B, and an empty input line. WB105
        # resolves the pair at RECORD OFFSET 1 ($042E takes A as a byte index). 58 of 180 records
        # carry a real edge there — 22 point back at the KEYWORD itself ("ask again"), 20 at a SAY.
        # OP8_KEYWORD.md calls +$01/+$02 the no-match branch and claims every op-8 is $FF $FF; both
        # are contradicted by the handler and by its own worked example (s1 id49).
        # (a record so short that +$01/+$02 fall off the blob does not occur — 180/180 are in
        #  range — but pad with the sentinel rather than raise: $FF FF is the no-op the engine reads)
        return {"op": "KEYWORD", "prompt": r[rec],
                "cancel": {"op": r[rec + 1] if rec + 1 < len(r) else 0xFF,
                           "id": r[rec + 2] if rec + 2 < len(r) else 0xFF},
                "keywords": kws, "after": _adv_tail(r, bt + len(kws) * 2, stop)}
    if op == 0x6:                                   # EVENT (grp16 ref) or SERVICE ($CA00), byte0 bit7
        if r[rec] & 0x80:
            # SERVICE (shop / doc / bank / revive / talk / …) — INTRINSIC length, no layout input.
            #   [$80|fam][hdr 2][fields][name][$00][tail]
            # fields = 4 (0 for fam 2 and 3) · name = 11 chars (12 for fam 2) · tail by family:
            #   0,3 none · 1,2,5 a list up to and including $FF · 6 one byte ·
            #   9 one byte + (count at +4) str-ids · 7,8 one byte + ASCII up to and including $00
            # Verified against all 35 SERVICE records: 29 match their layout span exactly; the other
            # 6 are in the hand-built Area 51, where the model matches the record and the LAYOUT is
            # loose (orphaned service blobs nothing points at). A hard-coded $14 was wrong for every
            # family. An unknown family falls back to verbatim rather than guessing.
            fam = r[rec] & 0x7F
            noff, nn = wl_cell.service_name_span(fam)      # ONE rule, shared with the panel
            nf = noff - 3
            p = rec + 3 + nf + nn + 1
            j = None
            if fam in (0, 3):
                j = p
            elif fam in (1, 2, 5):
                j = p
                while j < len(r) and r[j] != 0xFF:
                    j += 1
                j += 1
            elif fam == 6:
                j = p + 1
            elif fam == 9 and rec + 4 < len(r):
                j = p + 1 + r[rec + 4]
            elif fam == 7:
                # bank: ONE $00-terminated ASCII confirmation at a FIXED +$15. The old walk stopped
                # at the first $00 after the label — which is the byte right before the text — so the
                # record was emitted 21 bytes long instead of 47 and "A thousand dollars added."
                # was dropped. Same rule, same place, as revive: wl_cell.service_end.
                j = max(p + 1, wl_cell.service_end(r, rec))
            elif fam == 8:
                # revive: THREE $00-terminated ASCII strings, each addressed by its own offset byte.
                # Walking to the FIRST $00 ended the record after string one, so it was emitted
                # shorter than its own offsets reach and strings two and three read the NEIGHBOUR —
                # Area 51's Sentinel lost "He walks among us again." and the refusal line on every
                # re-pack, and Area 51 is one of the two records that ship through the serializer.
                # ONE rule, in wl_cell.service_end, shared with the panel that displays them.
                j = max(p + 1, wl_cell.service_end(r, rec))
            if j is None or not (p <= j <= len(r)):
                end = wl_cell._section_end(_bounds(r, slot), rec, len(r))
                return {"op": "SERVICE", "selector": fam, "record": _hex(r[rec:end])}
            # +$01/+$02 is the same cell-advance pair KEYWORD carries, and here unconditionally:
            # the op-6 wrapper ($8839) ends `lda #$01 / jmp WB105` whichever arm ran. 33 of 36 carry
            # the $FD FD SWAP-BACK — not a no-op: $0A38 jsr's map_cell_escape_FE, which loads the
            # saved $AD/$AE pair into $64/$65 and falls into the walk that WRITES the cell back (the
            # `bmi` skip cannot fire, $AD is a 4-bit nibble), and only the returned carry differs
            # from $FE. That write is what turns the door cell back into the op-A door once the
            # $CA00 blob exits. $FF is the true no-op, and exactly one record uses it (s1 a0).
            # s4 a15 "Chopper Sim" and s4 a21 "Blackmarket" carry $0A/$02 and $0A/$0A — real
            # transitions, both to their OWN area, i.e. the self-reload idiom.
            base = {"op": "SERVICE", "selector": fam,
                    "advance": {"op": r[rec + 1] if rec + 1 < len(r) else 0xFD,
                                "id": r[rec + 2] if rec + 2 < len(r) else 0xFD},
                    "name": bytes(r[rec + 3 + nf:p - 1]).decode("latin-1")}
            legacy = dict(base, fields=_hex(r[rec + 3:rec + 3 + nf]), tail=_hex(r[p:j]))
            named = _service_named(base, fam, bytes(r[rec + 3:rec + 3 + nf]), bytes(r[p:j]))
            if named is None:
                return legacy
            # VERIFY-OR-FALL-BACK: a family model that cannot reproduce its own bytes must not be
            # allowed to emit. Anything the map below does not cover degrades to the hex triple
            # instead of moving a byte.
            try:
                ok = _emit_op_record(named) == bytes(r[rec:j])
            except Exception:
                ok = False
            return named if ok else legacy
        # EVENT (bit7 clear). byte0 is the event id; the payload after it is read by the AREA'S
        # OWN grp16 handler, so there is no global field map — but there is a per-area one, and it
        # is derivable: at handler entry $5F/$60 IS this record, so every lda ($5F),y in the handler
        # names one payload byte (wl_grp16.analyze). `payload` lists those bytes by offset so the
        # editor can show and change them; `record` stays as the byte-exact truth the emitter uses.
        # An op-6 record is THREE things. +$00 is the event id (the grp16 jump-table index). +$01/+$02
        # is the SAME cell-advance pair a SERVICE carries — the wrapper ends `lda #$01 / jmp WB105`
        # whichever arm ran — and seven of the 66 handlers prove it by WRITING there: they pick an
        # alternative [op][id] out of the args and stuff it into +$01/+$02 so the dispatcher installs
        # THAT one (s1 a0's day/night gate chooses +$3/+$4 before 6:00 and +$5/+$6 after 18:00).
        # +$03.. is per-area 6502 arguments — re-deriving the handler read map with Y tracked
        # properly puts every read at +$03 or above and contiguous, so there is no global format
        # and `args` stays honest hex.
        end = wl_cell._section_end(_bounds(r, slot), rec, len(r))
        n = end - rec
        d = {"op": "EVENT", "event": r[rec]}
        # ONE byte when the record stops there: $FD/$FE consume a single byte, and for a bit7 value
        # the resolver reads a second byte it then discards (`lda $64 / bmi`). Eight records
        # legitimately store only the escape — the ABSENT "id" key is what encodes that.
        d["next"] = {"op": r[rec + 1], "id": r[rec + 2]} if n >= 3 else \
                    {"op": r[rec + 1]} if n == 2 else None
        d["args"] = _hex(r[rec + 3:end]) if n > 3 else "hex:"
        reads = _event_reads(r, slot, r[rec])
        if reads:
            d["payload"] = {str(y): r[rec + y] for y in reads if rec + y < end}
        return d
    # not modelled yet → opaque (round-trips verbatim; promote to typed later)
    return {"op": "RAW", "note": _OP_NAME.get(op, f"${op:X}"), "raw": _hex(r[rec:rec + 8])}


def _door_targets(raw, slot, area, bounds=None):
    """(op, id) pairs reached from an op-A door's TAIL — `[b0][X][Y][tgt] [op][id]`. A shop/doc/bank
    op-6 SERVICE record has NO op-6 cell and is not in any chain: the door tail is its ONLY reference.
    Missing this dropped Highpool's Doctor + Shop (grp6[0]/[1]) from the re-pack as "orphans" — the
    door then printed "Entering the infirmary." and did nothing.

    The walk is bounded by the transition record's own EXTENT. Unbounded it ran past the record into
    whatever the layout happened to put next, and since a re-pack puts something else there, the
    same area decoded to different references before and after: s2_id26's doors invented a reference
    to grp5[93], which woke the parked grp5 and re-typed four of grp6's EVENT records as opaque grp5.
    A decode that depends on a record's NEIGHBOUR is not a decode.
    """
    out = set()
    seen = set()
    bounds = bounds if bounds is not None else wl_cell._section_bounds(raw, slot)
    for i, op in enumerate(area.nibble):
        if op != 0xA or area.cellid[i] in seen:
            continue
        seen.add(area.cellid[i])
        rec = wl_cell.subptr(raw, slot, 0xA, area.cellid[i])
        if rec is None:
            continue
        end = min(wl_cell._section_end(bounds, rec, len(raw)), len(raw))
        j = rec + 4
        while j + 1 < end:                          # walk the tail's follow-op pairs
            b = raw[j]
            if b == 0xFF or b >= 0x10:
                break
            out.add((b, raw[j + 1]))
            j += 2
    return out


def _group_ids(area, grp, bounds):
    """EVERY id of group `grp` that has a valid ARRAY ENTRY.

    The array entry IS the reference: it holds the ONLY copy of the record's address. A cell
    (op-nibble, cell-id) and a record edge (op, id) do not point at a record — they name an
    array SLOT, which the descriptor then resolves. So "does this record exist?" is answered by
    the array alone; "does the player reach it today?" is a different, PLAYABILITY question.

    This decoder used to answer the second one and drop whatever it could not reach. That is
    wrong twice over: the engine can activate any record later (CELL_PATCH, journal/save-state,
    quest progress), and grp16 code can jump at an absolute address the walk cannot enumerate.
    It is also what silently deleted Highpool's shop + infirmary. Reachability is now reported
    (wl_walk) but never used to drop anything.

    The extent is the array's SPAN, not a terminator scan: [arr, first structure after arr), further
    tightened by the smallest forward target inside it (a record cannot sit inside its own array).
    Every slot in that span with a valid pointer is a record; everything outside it is not, however
    loudly some byte pair "references" it.

    Both directions matter and both were wrong before:
      · wl_cell.group_array_extent stops after 8 consecutive empty slots — s3_id12's grp10 array has
        a longer gap run, so it reported 11 of 100 ids and 67 live transitions looked like nothing.
      · taking every [op][id] pair found anywhere as a reference invented grp5[255] and grp2[62],
        whose "array words" are another structure's bytes; one of them inflates the emitted array to
        512 B, which then no longer fits in front of a pinned grp16.

    NOTE: this answers "which SLOTS exist", not "what TYPE is slot k" — for a SHARED array the
    latter is decided by `_ref_ops` (which op references the slot), never by the group whose
    pointer happens to alias the base.
    """
    raw, slot = area.raw, area.slot
    n = wl_cell.group_slot_count(raw, slot, grp, bounds)   # parked pointers + grp15's count: there
    ids = []
    for cid in range(n):
        off = wl_cell.subptr(raw, slot, grp, cid)     # subptr only rejects $0000 — a word BELOW $3400
        if off is not None and 0 <= off < len(raw):   # is just as dead, and sizing a "record" from a
            ids.append(cid)                           # negative offset produced a 12 KB opaque blob
    return ids


def _record_edges(rr) -> list:
    """(op, id) slots this typed IR record points at — every advance / branch / patch / door edge.

    op 0 is NOT an edge. It means "this cell becomes empty" — the commonest ending in the game
    (265 of the shipped loot sacks advance to it, and a CELL_PATCH clears a square with it). Reading
    it as a reference made the chain walker follow (0, 0), find no record there and stop, so any
    chain that cleared a cell on its way was cut off at that step and the Flow view showed nothing
    beyond it. Same rule as `adv()` and `wl_object.refs_of`: a record reference is 0 < op < $10."""
    out = []

    def add(adv):
        if adv and 0 < adv.get("op", 0xFF) < 0x10 and adv.get("id") is not None:
            out.append((adv["op"], adv["id"]))
    op = rr.get("op")
    if op in ("BUMP", "SAY", "HAZARD"):
        add(rr.get("next"))
    elif op == "LOOT":
        add(rr.get("advance"))
    elif op == "ENCOUNTER":
        add(rr.get("victory"))
    elif op == "OBJ":
        add(rr.get("use"))
    elif op == "PATCH":
        for e in rr.get("entries", []):
            if 0 < e.get("op", 0xFF) < 0x10:
                out.append((e["op"], e["id"]))
        add(rr.get("after"))
    elif op == "COND":
        for k in ("done", "alt"):
            add(rr.get(k))
        for br in rr.get("branches", []):             # b0 bit3: one branch per condition
            add(br)
    elif op == "KEYWORD":
        for kw in rr.get("keywords", []):
            add(kw.get("branch"))
        add(rr.get("after"))
    elif op == "TRANSITION":                     # the door's follow-op — a SERVICE door's only ref
        add(rr.get("next"))
    return out


def record_edges_named(rr) -> list:
    """The same edges as `_record_edges`, but each carrying WHICH slot it came from and how it reads.

    `_record_edges` returns a flat (op, id) list, which is all a "does anything point here" walker
    needs. An outline needs more: a KEYWORD with three keywords forks three ways, and drawing those
    three as one line — or, worse, following only the first — is exactly what the old single-path
    walk did. So this returns [{key, label, to:(op,id)}] and keeps the two in step by mirroring the
    same op-by-op structure. Same rule throughout: a record reference is 0 < op < $10, op 0 means
    the cell goes empty and is not an edge."""
    out = []

    def add(key, label, adv):
        if adv and 0 < adv.get("op", 0xFF) < 0x10:
            out.append({"key": key, "label": label, "to": (adv["op"], adv["id"])})
    op = rr.get("op")
    if op == "BUMP":
        add("next", "afterwards", rr.get("next"))
    elif op == "LOOT":
        add("advance", "after taking it", rr.get("advance"))
    elif op == "ENCOUNTER":
        add("victory", "after the fight", rr.get("victory"))
    elif op == "OBJ":
        add("use", "when stepped on", rr.get("use"))
    elif op == "PATCH":
        for i, e in enumerate(rr.get("entries", [])):
            if 0 < e.get("op", 0xFF) < 0x10:
                out.append({"key": f"entries[{i}]", "label": f"({e['dx']},{e['dy']}) becomes",
                            "to": (e["op"], e["id"]), "cell": [e["dx"], e["dy"]]})
        add("after", "this cell becomes", rr.get("after"))
    elif op == "COND":
        add("done", "when it passes", rr.get("done"))
        add("alt", "when it fails", rr.get("alt"))
        for i, br in enumerate(rr.get("branches", [])):
            # b0 bit3: branches[i] belongs to conds[i]. "condition 6" names nothing — the caller
            # resolves `cond` into what the condition actually TESTS, because only it has the item
            # and skill tables.
            add(f"branches[{i}]", f"condition {i + 1}", br)
            if out and out[-1]["key"] == f"branches[{i}]":
                c = (rr.get("conds") or [{}])[i] if i < len(rr.get("conds") or []) else {}
                out[-1]["cond"] = {"op": (c.get("opb", 0) >> 5), "arg": c.get("arg", 0),
                                   "difficulty": c.get("opb", 0) & 0x1F}
    elif op == "KEYWORD":
        for i, kw in enumerate(rr.get("keywords", [])):
            # the keyword is a STRING ID here; whoever has the pool resolves it to the word
            add(f"keywords[{i}]", f"on keyword #{kw.get('str')}", kw.get("branch"))
            if out and out[-1]["key"] == f"keywords[{i}]":
                out[-1]["str_id"] = kw.get("str")
        add("after", "otherwise", rr.get("after"))
    elif op == "TRANSITION":
        add("next", "through the door", rr.get("next"))
    return out


def _ref_ops(raw, slot, area, door_targets=(), bounds=None) -> dict:
    """(grp, cid) -> True for every array slot something DEMONSTRABLY references with that op.

    Why this exists: group arrays are routinely SHARED — grp4 aliases grp3, grp5/grp8 share grp6's,
    grp9/grp10 share in 18 of 43 records, grp12 can alias grp15's. ONE array, entries of DIFFERENT
    types. A shared slot's type is decided by the op that REFERENCES it; the previous rule ("decode
    it as every sharing group and keep whichever reading is longest") invented 2.3 KB of CELL_PATCH
    out of eight 12-byte encounter defs in s2_id01 alone.

    Hard sources only:
      · a MAP CELL (op-nibble = group, cell-id = slot)
      · an op-A DOOR TAIL (the only reference a shop/doc SERVICE record has)
      · an edge out of a record that is itself reachable from those two (transitive)
      · ENGINE-driven: grp15 is iterated [0, desc+$32); grp17 is indexed by an encounter's byte+9
        hi-nibble (0 = "no NPC" — the real hireable records start at id 1)
    A record-edge from an UNREFERENCED record is deliberately NOT a source: those decode out of
    whatever bytes the alias happens to cover, so they would re-introduce the invention.
    """
    refs = {}
    seeds = [(op, area.cellid[i]) for i, op in enumerate(area.nibble) if 0 < op < 0x10]
    seeds += [(g, c) for g, c in door_targets]
    seen = set()
    while seeds:
        g, cid = seeds.pop()
        if (g, cid) in seen or not (0 < g < 19):
            continue
        seen.add((g, cid))
        refs[(g, cid)] = True
        if wl_cell.subptr(raw, slot, g, cid) is None:
            continue
        try:
            seeds += _record_edges(_decode_op_record(raw, slot, g, cid))
        except Exception:
            pass
    if slot + 0x32 < len(raw):                       # grp15: the engine walks the whole count
        for cid in range(raw[slot + 0x32]):
            refs[(15, cid)] = True
    for g in (3, 15):                                # grp17: encounter byte+9 hi-nibble = NPC id
        # group_slot_count, not group_array_extent: the weak measure stops after 8 empty slots, so
        # an encounter at a high id never contributed its hire nibble and the NPC it offers was
        # dropped from the IR as unreferenced. Third bug from that function today.
        for cid in range(wl_cell.group_slot_count(raw, slot, g, bounds)):
            off = wl_cell.subptr(raw, slot, g, cid)
            if off is None or not (0 <= off + 9 < len(raw)):
                continue
            hi = raw[off + 9] >> 4
            if hi:
                refs[(17, hi)] = True
    return refs


def grp16_extent(raw, slot) -> tuple:
    """(base, end) of the grp16 section — determined, NOT guessed.

    The old assumption "grp16 runs from desc+$26 to the foe-names base" is WRONG: the op-groups
    live on BOTH sides of grp16, so that span swallows their arrays and records (Highpool: 984 of
    988 bytes were foreign — grp8/9/10/11/12; its real grp16 is a 2-byte jump table plus a 2-byte
    `sec/rts` stub). grp16 ENDS at the first foreign structure after it: any other group's array,
    any other group's record, or the foe-names/foe-table/text base.
    """
    g16 = wl_cell.gptr(raw, slot, 16)
    if g16 is None or not (0 <= g16 < len(raw)):
        return None, None
    cands = []
    # the STRONG slot count: a short one yields fewer candidate boundaries, which makes grp16
    # measure LONGER than it is and swallow the structure behind it — the dangerous direction.
    _gb = wl_cell._section_bounds(raw, slot)
    for g in range(19):
        if g == 16:
            continue
        base = wl_cell.gptr(raw, slot, g)
        if base is None or not (0 <= base < len(raw)):
            continue
        if base > g16:
            cands.append(base)
        for cid in range(wl_cell.group_slot_count(raw, slot, g, _gb)):   # that group's RECORDS too
            off = wl_cell.subptr(raw, slot, g, cid)
            if off is not None and off > g16:
                cands.append(off)
    for fld in (0x00, 0x02, 0x04):                                    # text / foe-names / foe-table
        v = wl_cell._le(raw, slot + fld) - RAM
        if 0 <= v < len(raw) and v > g16:
            cands.append(v)
    return g16, (min(cands) if cands else len(raw))


_PTR_SYMS = (("text", 0x00), ("foe_names", 0x02), ("foe_table", 0x04), ("grp16", 0x26))


def _dead_ptr_sym(raw, slot, ptr, groups, opaque, g16_live=True) -> dict:
    """Describe an INACTIVE group's descriptor pointer symbolically: {"base": sym, "delta": n}.

    The engine marks a group inactive by aiming its pointer at another section (foe-names is the
    usual choice; grp16's code body also occurs) instead of zeroing it. The serializer moves those
    sections, so the raw word cannot be kept — the symbol can. "none" = it pointed at nothing the
    layout re-creates; the field is then zeroed, which is equally dead.
    """
    for g in range(19):                                 # a LIVE group's array first — that is what a
        if g == 16 or (str(g) not in groups and str(g) not in opaque):
            continue                                    # parked pointer usually aims at
        b = wl_cell.gptr(raw, slot, g)
        if b is not None and b == ptr:
            return {"base": f"grp{g}", "delta": 0}
    for sym, fld in _PTR_SYMS:                          # then an exact hit on a section base
        if sym == "grp16" and not g16_live:
            continue
        v = wl_cell._le(raw, slot + fld) - RAM
        if v == ptr:
            return {"base": sym, "delta": 0}
    if g16_live:                                        # or somewhere INSIDE the grp16 code body
        g16, g16end = grp16_extent(raw, slot)
        if g16 is not None and g16 <= ptr < g16end:
            return {"base": "grp16", "delta": ptr - g16}
    return {"base": "none", "delta": 0}


def record_to_ir(raw, side, area_id) -> dict:
    """Decode an area record blob → the canonical JSON-IR document (SERIALIZER_SPEC.md §3)."""
    a = wl_area.load_area(raw, side, area_id)
    slot, W, H = a.slot, a.width, a.height

    ir = {"meta": {"side": side, "area": area_id, "width": W, "height": H,
                   "ir_version": IR_VERSION},
          "grid": {"op": list(a.nibble), "cellid": [c & 0xFF for c in a.cellid]}}

    # descriptor — the section-base POINTERS ([slot+$00 .. slot+$2C): text/foe/19 groups) are OMITTED,
    # the serializer computes them. The NON-pointer part [slot+$2C .. slot+$5C) (width, grp15 count,
    # fill tile, opaque tail) is kept verbatim. (Sections begin exactly at slot+$5C.)
    ir["descriptor"] = {
        "off_map_fill_tile": raw[slot + 0x33] if slot + 0x33 < len(raw) else None,
        "encounter_slot_count": raw[slot + 0x32] if slot + 0x32 < len(raw) else None,
        "tail": _hex(raw[slot + 0x2C:slot + 0x5C]),
    }

    # groups (0..18, minus 16 = grp16 CODE which is its own section) → typed records by active id.
    bounds = wl_cell._section_bounds(raw, slot)
    door_tgts = _door_targets(raw, slot, a, bounds)  # op-A door tails → SERVICE records (no own cell)
    refs = _ref_ops(raw, slot, a, door_tgts, bounds)

    # ARRAY ALIASES: several groups' descriptor pointers aim at ONE array base (grp9/grp10 share in
    # 18 of 43 records; 16 further combinations occur). Emitting a separate array per group breaks
    # that and inflates the record (Highpool: +114 B). Record who shares with whom so the serializer
    # writes ONE array and points every sharer's descriptor field at it.
    from collections import defaultdict as _dd
    _by = _dd(list)
    for g in range(19):
        p_ = wl_cell.gptr(raw, slot, g)
        if p_ is not None and 0 <= p_ < len(raw):
            _by[p_].append(g)
    shared_with = {g: sorted(v) for v in _by.values() if len(v) > 1 for g in v}

    # Per array SLOT, decide ONE type and emit it ONCE (see _ref_ops):
    #   · exclusive array          → the owning group's op (unchanged; 43/43 byte-identical)
    #   · shared, exactly one ref  → that referencing op
    #   · shared, 0 or >1 refs     → OPAQUE: the original bytes verbatim, so both readers see
    #                                exactly what the engine saw. Never re-interpreted, never
    #                                duplicated per sharer.
    # grp17 is the one group whose slots are NOT self-evident: its pointer is aliased onto the
    # foe-names / grp16 base whenever the area has no hireable NPC, and reading pointers out of
    # THAT invented a 2.8 KB "record" (s4_id20). It emits encounter-referenced ids only.
    # LIVE vs INACTIVE. A group is live if something references one of its slots — or if its array
    # is its own (not shared, not a section base), in which case unreferenced records are still real
    # (dropping those deleted Highpool's shop). A group with NO reference whose pointer merely
    # COINCIDES with a live structure is inactive: those "records" are another group's array, or
    # grp16's code, read as records. That is where the phantom 2.8 KB grp17 record came from, and
    # it is also why a re-decode kept discovering new groups.
    sect = {wl_cell._le(raw, slot + f) - RAM for f in (0x00, 0x02, 0x04)}
    ref_groups = {g for g, _ in refs}
    # grp16's word can itself be a PARKED pointer aimed at another group's array. Its only entry
    # point is an op-6 EVENT record (byte0 bit7 clear → event id → the jump table), so an area with
    # no live EVENT has no way into grp16: on a base collision the other group owns the bytes.
    # Reading them as code instead deleted two real KEYWORD records in s2_id28.
    g16b = wl_cell.gptr(raw, slot, 16)
    has_event = False
    for (g_, c_) in refs:
        if g_ != 6 or wl_cell.subptr(raw, slot, 6, c_) is None:
            continue
        try:
            has_event = has_event or _decode_op_record(raw, slot, 6, c_).get("op") == "EVENT"
        except Exception:
            pass
    collide = [g for g in range(19) if g != 16 and wl_cell.gptr(raw, slot, g) == g16b]
    g16_live = g16b is not None and (has_event or not collide)
    code_lo, code_hi = grp16_extent(raw, slot) if g16_live else (None, None)
    live_group = {}
    for g in range(19):
        p_ = wl_cell.gptr(raw, slot, g)
        if g == 16 or p_ is None:
            continue
        parked = (p_ in sect                                     # aimed at a tail section
                  or (code_lo is not None and code_lo <= p_ < code_hi)   # or into grp16's code
                  or any(x != g and x in ref_groups for x in shared_with.get(g, [])))
        # grp17 is the exception: its "reference" is an encounter's byte+9 hi-nibble, which is only
        # an NPC index when a real hireable array exists — with the pointer parked on foe-names that
        # same nibble means something else, and trusting it read NPC records out of the text (+6 KB).
        live_group[g] = (g in ref_groups and g != 17) or not parked

    groups, opaque = {}, {}
    for g in range(19):
        if g == 16 or wl_cell.gptr(raw, slot, g) is None or not live_group.get(g):
            continue
        sset = [x for x in shared_with.get(g, []) if live_group.get(x)]
        sset = sset if len(sset) > 1 else None       # a dead sharer never claims a slot
        for cid in _group_ids(a, g, bounds):
            if wl_cell.subptr(raw, slot, g, cid) is None:
                continue
            if g == 17 and not refs.get((17, cid)):
                continue
            if sset:
                claim = [x for x in sset if refs.get((x, cid))]
                if claim != [g]:                     # not uniquely ours → owner keeps it verbatim
                    if len(claim) == 1:
                        continue                     # the one true owner emits it below
                    own = str(min(sset))
                    off = wl_cell.subptr(raw, slot, min(sset), cid)
                    if off is not None:
                        end = wl_cell._section_end(bounds, off, len(raw))
                        opaque.setdefault(own, {})[str(cid)] = _hex(raw[off:end])
                    continue
            off = wl_cell.subptr(raw, slot, g, cid)
            if g in _MODELLED_GROUPS:
                # NOTE: 43 of 4730 records emit LONGER than their layout span — a terminator scan that
                # runs into the neighbour, i.e. an extent rule still to be RE'd (mostly LOOT/COND/PATCH,
                # plus SERVICE's hard-coded $14). Emitting the full walked length keeps those records
                # BEHAVING as they did (the engine read that continuation too, and it is byte-identical
                # here); the duplicate tail costs 310 bytes across all 43 areas. Truncating them to the
                # span instead saves those bytes but changes what 3 live cells decode to — not worth it.
                groups.setdefault(str(g), {})[str(cid)] = _decode_op_record(raw, slot, g, cid)
            else:
                off = wl_cell.subptr(raw, slot, g, cid)
                end = wl_cell._section_end(bounds, off, len(raw))
                opaque.setdefault(str(g), {})[str(cid)] = _hex(raw[off:end])
    ir["groups"] = groups
    ir["opaque"] = opaque
    # Only sets with 2+ LIVE groups are a real shared array. A group with no slots left keeps its
    # pointer via dead_ptrs (usually foe-names) — listing those here would invent an "alias" between
    # every inactive group and make the IR unstable across a re-decode.
    live = {int(k) for k in list(groups) + list(opaque)}
    ir["array_alias"] = {str(min(v)): sorted(v) for v in _by.values()
                         if len(set(v) & live) > 1}
    # DEAD POINTERS: a group with no slots at all still has a descriptor word in the original (aimed
    # at another section — that IS how the engine marks it inactive). Keep it symbolically so the
    # serializer can restore it instead of writing $0000.
    dead = {}
    for g in range(19):
        p_ = wl_cell.gptr(raw, slot, g)
        if p_ is None or str(g) in groups or str(g) in opaque or (g == 16 and g16_live):
            continue
        sym = _dead_ptr_sym(raw, slot, p_, groups, opaque, g16_live)
        if sym["base"] != "none":                    # "none" = nothing the new layout re-creates;
            dead[str(g)] = sym                       # the field is zeroed, which is equally dead
    ir["dead_ptrs"] = dead

    # foes: 1-based parallel arrays — names (desc+$02) + the 8-byte stat records (desc+$04) + portrait.
    # The DISPLAY names are lossy (stem+singular only), so the round-trippable truth is the raw bytes
    # of both tables (kept opaque below); foes[] stays for readability.
    names = wl_cell._foe_names(a)
    ft = wl_cell._le(raw, slot + 0x04) - RAM
    fn = wl_cell._le(raw, slot + 0x02) - RAM
    foes = []
    for k, nm in enumerate(names):
        ro = ft + (k + 1) * 8                        # foe table is 1-based (record 0 = terminator)
        rec = raw[ro:ro + 8] if 0 <= ro and ro + 8 <= len(raw) else b""
        foes.append({"name": nm, "record": _hex(rec),
                     "portrait_str": rec[7] if len(rec) == 8 else None})
    ir["foes"] = foes
    # The three tail sections run foe-names → foe-table → text, so each ENDS at the next one's
    # base. An area with no foes points all three at the same offset: those sections are EMPTY.
    # Sizing them with _section_end() instead swallowed the whole text region — twice (once as
    # names, once as records), which is what blew id13 up by +6.3 KB on re-pack.
    txt = wl_cell._le(raw, slot) - RAM               # text-region base = the foe-table's end
    # …but only when the three really do run back-to-back. s1_id51 stores the foe TABLE (2200) far
    # in front of the names (2669) with group arrays and records in between: taking "up to text"
    # there swallowed 2859 B of other people's structures and re-emitted them (+708 B). So bound by
    # the next structure as well and take whichever comes first.
    fn_end = min(ft if (0 <= ft and ft >= fn) else len(raw), wl_cell._section_end(bounds, fn, len(raw)))
    ft_end = min(txt if (0 <= txt and txt >= ft) else len(raw), wl_cell._section_end(bounds, ft, len(raw)))
    ir["foe_names_raw"] = _hex(raw[fn:fn_end]) if 0 <= fn else "hex:"
    ir["foe_records_raw"] = _hex(raw[ft:ft_end]) if 0 <= ft else "hex:"

    # ── THE FOE LIST ──────────────────────────────────────────────────────────────────────────
    # A foe is a NAME and an 8-byte RECORD in two parallel tables, and the only thing binding them
    # is "position k here belongs to position k there". Carrying the tables as two opaque blobs let
    # them drift — and worse, it put the name-table GRAMMAR in the emitter instead of in the model,
    # which is how `$00 STEM \n \n` closed by `$01` got written and hung the engine (COMBAT_TEXT.md
    # §4). Here they become ONE list, so a foe is an object with a name and stats, and the two
    # tables are things the serializer BUILDS rather than things it copies.
    #
    # The name keeps its SEGMENTS (stem / singular suffix / plural suffix), because that is what
    # byte-faithful means: "Bobb"+"y"+"ies" is Bobby/Bobbies, and a joined display name re-emits
    # "Bobbys". Strictly fail-closed — the list is only emitted when it reconstructs BOTH tables
    # byte-for-byte, otherwise the raw blobs stay the source and nothing is lost.
    nb, rb = _unhex(ir["foe_names_raw"]), _unhex(ir["foe_records_raw"])
    parsed = wl_cell.parse_foe_names(nb) if nb else None
    if parsed and wl_cell.emit_foe_names(*parsed) == nb and len(parsed[0]) == len(ir["foes"]):
        entries, term = parsed
        need = (len(entries) + 1) * 8
        if len(rb) >= need:
            for k, f in enumerate(ir["foes"]):
                f["name_segs"] = entries[k]                # stem / singular suffix / plural suffix
            ir["foe_table_header"] = _hex(rb[:8])          # record 0 is a header, not a foe
            ir["foe_table_extra"] = _hex(rb[need:])        # bytes past the last foe, if any
            ir["foe_names_term"] = _hex(term)

    # ── THE COMBAT MESSAGE TABLE (desc+$37..+$5B) ─────────────────────────────────────────────
    # 37 string ids into this area's own pool: 7 weapon verbs, then the damage fragments, with ids
    # REPEATED where a fragment is shared. Lifted out of the opaque descriptor tail because it is a
    # table of TEXT REFERENCES — carried verbatim it would survive any renumbering unchanged and
    # silently point every combat line somewhere else. Note these are NOT 7-bit refs: several areas
    # place the block above 127 (s2a3 at 156). COMBAT_TEXT.md §2.
    ir["combat_msgs"] = list(raw[slot + 0x37:slot + 0x5C]) if slot + 0x5C <= len(raw) else []

    # strings (index-keyed pool) + the 60-byte char table (needed to re-encode; build_region extends it)
    base = wl_cell._le(raw, slot) - RAM
    ir["text_table"] = _hex(raw[base:base + 0x3C]) if 0 <= base else "hex:"
    try:
        ir["strings"] = list(tenc.enumerate_strings(raw, base, raw[base:base + 62]))
    except Exception:
        ir["strings"] = []

    # grp16: opaque code, PINNED by default at its original offset — its absolute operands and jump
    # table point at its own bytes, so staying put is the only fixup-free option (and it costs 7 B
    # across all 32 blobs, because the item packer fills the hole in front of it almost exactly).
    # `reloc` is the escape hatch, computed once and PERSISTED so a human can review the relocation
    # table instead of trusting a re-derivation: set policy "float" and the serializer lays grp16 out
    # with everything else and rewrites those sites. See wl_reloc.py.
    g16 = wl_cell._le(raw, slot + 0x26) if g16_live else 0
    if g16:
        o0, o1 = g16 - RAM, wl_cell._section_end(bounds, g16 - RAM, len(raw))
        code = raw[o0:o1] if 0 <= o0 < o1 <= len(raw) else b""
        p = wl_reloc.plan(code, o0 + RAM)
        ir["grp16"] = {"policy": "pin", "offset": o0, "code": _hex(code),
                       "reloc": {"safe": p["safe"], "sites": p["sites"], "risks": p["risks"]}}
    else:
        ir["grp16"] = None

    return ir


# ─────────────────────────────── SERIALIZE (IR → bytes) ───────────────────────────────
# The inverse of record_to_ir. Deterministic layout (SERIALIZER_SPEC.md §6): grids, descriptor,
# grp16 PINNED at its offset (self-modifying code), then every other section laid out fresh AFTER
# grp16 with its pointer computed — op-groups + pointer-groups get rebuilt arrays; foe tables + text
# are emitted so foe-records sits IMMEDIATELY before text (area_digest's _foe_table_end depends on it).
_MODELLED_GROUPS = (1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 15, 17)  # op-nibble == group index
                                                             # (15 = the encounter slot table)


def _unhex(h) -> bytes:
    if not h or h == "hex:":
        return b""
    return bytes.fromhex(h[4:] if h.startswith("hex:") else h)


def _emit_adv(adv) -> bytes:
    return bytes([adv["op"] & 0xFF, adv["id"] & 0xFF]) if adv else bytes([0xFF, 0x00])


def _emit_adv_tail(adv) -> bytes:
    """Trailing advance: 1 byte for a stop, 2 for a real chain link (see _adv_tail)."""
    if not adv:
        return bytes([0xFF])
    if "id" not in adv:
        return bytes([adv["op"] & 0xFF])
    return bytes([adv["op"] & 0xFF, adv["id"] & 0xFF])


def _emit_op_record(rr) -> bytes:
    """Inverse of _decode_op_record: a typed IR record → its exact byte layout."""
    op = rr["op"]
    if op == "SAY":
        # the list, terminated, THEN the trailing advance. Mask EVERY element first (a caller may
        # still carry bit7 from a raw byte), then set it on the last and nowhere else. An empty list
        # falls back to [0] = b"\x80", the same single byte the old `msg` fallback wrote — never b"".
        ids = [int(i) & 0x7F for i in (rr.get("messages") or [0])]
        b = bytes(ids[:-1] + [ids[-1] | 0x80])
        # A document that predates the pair, or a record the editor authored without one, gets the
        # $FF stop rather than nothing: leaving the byte out makes the engine read whatever record
        # follows and write it into the map. _emit_adv_tail's None -> $FF is exactly that stop.
        nx = rr.get("next")
        if nx and "id" not in nx:
            return b + bytes([nx["op"] & 0xFF])
        return b + _emit_adv_tail(nx)
    if op == "BUMP":
        return bytes([rr["msg"] & 0xFF]) + _emit_adv_tail(rr.get("next"))
    if op == "LOOT":
        b = bytearray(_emit_adv(rr.get("advance")))
        for it in rr.get("items", []):
            if "qty16" in it:
                b += bytes([it["item"] & 0xFF, it["qty16"] & 0xFF, (it["qty16"] >> 8) & 0xFF])
            else:
                b += bytes([it["item"] & 0xFF, it["qty"] & 0xFF])
        b += _unhex(rr["term_raw"]) if rr.get("term_raw") else bytes([0xFF])   # the only terminator
        return bytes(b)
    if op == "ENCOUNTER":
        if rr.get("data"):                            # pre-typed IR (older document)
            return _unhex(rr["data"]) + _emit_adv_tail(rr.get("victory"))
        b = bytearray([rr["dist_a"] & 0xFF, rr["dist_b"] & 0xFF, rr["msg"] & 0xFF])
        for sl in rr["slots"]:
            b.append((sl["foe"] & 0x7F) | (0x80 if sl["foe_roll"] else 0))
            b.append((sl["count"] & 0x7F) | (0x80 if sl["count_roll"] else 0))
        f = rr["flags"]
        b.append((f["npc"] << 4) | (1 if f["active"] else 0) | (2 if f["peaceful"] else 0)
                 | (4 if f["bit2"] else 0) | (8 if f["bit3"] else 0))
        return bytes(b) + _emit_adv_tail(rr.get("victory"))
    if op == "OBJ":
        if rr.get("data"):                            # pre-typed IR (older document / truncated rec)
            return _unhex(rr["data"]) + _emit_adv_tail(rr.get("use"))
        return bytes([rr.get("msg", 0) & 0xFF,        # unmasked read deserves an unmasked write
                      (rr.get("tile", 0) & 0x7F) | (0x80 if rr.get("solid") else 0)]) \
            + _emit_adv_tail(rr.get("use"))
    if op == "HAZARD":
        b = bytes([rr["msg"] & 0xFF, rr.get("dice", 0) & 0xFF])
        nx = rr.get("next")
        if nx and "id" not in nx:
            return b + bytes([nx["op"] & 0xFF])
        return b + _emit_adv_tail(nx)
    if op == "COND":
        d, al = rr["done"], rr["alt"]
        b = bytearray([rr["b0"] & 0xFF, rr["prompt"] & 0xFF, rr["success"] & 0xFF, rr["fail"] & 0xFF,
                       d["op"] & 0xFF, d["id"] & 0xFF, al["op"] & 0xFF, al["id"] & 0xFF,
                       rr["effect"]["target"] & 0xFF, rr["effect"]["value"] & 0xFF])
        for c in rr.get("conds", []):
            b += bytes([c["opb"] & 0xFF, c["arg"] & 0xFF])
        b.append(0xFF)
        for br in rr.get("branches", []):             # b0 bit3: one [op][id] per cond, after the $FF
            b += _emit_adv(br)
        return bytes(b)
    if op == "PATCH":
        b = bytearray([rr["msg"] & 0xFF])
        ents = rr.get("entries", [])
        for k, e in enumerate(ents):
            fl = (e["flags"] & 0x7F) | (0x80 if k == len(ents) - 1 else 0)
            b += bytes([fl, e["dx"] & 0xFF, e["dy"] & 0xFF, e["op"] & 0xFF, e["id"] & 0xFF])
        return bytes(b) + _emit_adv_tail(rr.get("after"))
    if op == "TRANSITION":                            # +4 = the cell-rewrite arg $042E resolves:
        head = bytes([rr["b0"] & 0xFF, rr["x"] & 0xFF,     # $FD/$FE swap the cell back, $FF leaves
                      rr["y"] & 0xFF, rr["target"] & 0xFF])  # it, < $10 rewrites it to [op][id]
        if rr.get("next") is None and rr.get("tail") is not None:
            # A STORED document written before the rename — a blueprint on disk, an older area .json.
            # Every other renamed op kept an arm for that shape (OBJ.data, EVENT.record, SERVICE
            # fields/hdr, HIREABLE.rest); TRANSITION lost it, and _emit_adv_tail(None) writes a hard
            # $FF. The shipped Shop blueprint carries `tail: 0602` — the door's ONLY reference to its
            # shop — so the paste emitted a door that printed its line and did nothing.
            return head + _unhex(rr["tail"])
        return head + _emit_adv_tail(rr.get("next"))
    if op == "KEYWORD":
        kws = rr.get("keywords", [])
        # `or {...}`: bare _emit_adv(None) writes FF 00, and the no-op this slot wants is FF FF
        b = bytearray([rr["prompt"] & 0xFF]) + _emit_adv(rr.get("cancel") or {"op": 0xFF, "id": 0xFF})
        for k, kw in enumerate(kws):
            b.append((kw["str"] & 0x7F) | (0x80 if k == len(kws) - 1 else 0))
        for kw in kws:
            b += _emit_adv(kw.get("branch"))
        return bytes(b) + _emit_adv_tail(rr.get("after"))
    if op == "HIREABLE":
        if rr.get("rest") is not None:                # pre-typed IR (older document / short slot)
            nm = rr["name"].encode("latin-1")[:14]
            return nm + b"\x00" * (14 - len(nm)) + bytes(rr["attrs"][:7]) + _unhex(rr["rest"])
        # Write into a zero-filled 256-byte sheet: every pad byte in the record (the name tail, the
        # rest of the 78-byte rank field, the empty list slots, +$F9..+$FF) is $00 in all 15 shipped
        # hireables, so the buffer IS the padding rule. Note the deliberate difference from the
        # legacy path above, which emitted `attrs` UNPADDED — a 6-element list there shortened the
        # record and shifted everything after it; here a missing attribute is simply 0.
        b = bytearray(0x100)
        nm = rr["name"].encode("latin-1")[:14]
        b[0:len(nm)] = nm
        b[0x0E:0x15] = bytes(x & 0xFF for x in (list(rr["attrs"]) + [0] * 7)[:7])
        b[0x15:0x18] = (rr["money"] & 0xFFFFFF).to_bytes(3, "little")
        b[0x18], b[0x19], b[0x1A] = rr["sex"] & 0xFF, rr["nationality"] & 0xFF, rr["ac"] & 0xFF
        b[0x1B:0x1D] = (rr["maxcon"] & 0xFFFF).to_bytes(2, "little")
        b[0x1D:0x1F] = (rr["con"] & 0xFFFF).to_bytes(2, "little")
        b[0x1F], b[0x20] = rr["weapon"] & 0xFF, rr["skill_points"] & 0xFF
        b[0x21:0x24] = (rr["xp"] & 0xFFFFFF).to_bytes(3, "little")
        b[0x24], b[0x25] = rr["level"] & 0xFF, rr["armour"] & 0xFF
        b[0x26:0x28] = (rr["overkill"] & 0xFFFF).to_bytes(2, "little")
        b[0x28], b[0x29] = rr["conditions"] & 0xFF, rr["status"] & 0xFF
        b[0x2B:0x2E] = bytes(x & 0xFF for x in (list(rr["hire_counters"]) + [0, 0, 0])[:3])
        b[0x30], b[0x31] = rr["join_line"] & 0xFF, rr["hire_gate"] & 0xFF
        rk = rr["rank"].encode("latin-1")[:0x4D]      # 78 B field, and the $00 terminator must fit
        b[0x32:0x32 + len(rk)] = rk
        for base, key, extra, n in ((0x80, "skills", "skills_extra", 29),
                                    (0xBD, "inventory", "inventory_extra", 30)):
            for k, e in enumerate(rr.get(key) or []):
                if k >= n:
                    break
                b[base + 2 * k], b[base + 2 * k + 1] = e["id"] & 0xFF, e["n"] & 0xFF
            for k, e in (rr.get(extra) or {}).items():
                b[base + 2 * int(k)], b[base + 2 * int(k) + 1] = e["id"] & 0xFF, e["n"] & 0xFF
        if rr.get("token"):
            b[0xBA], b[0xBB] = rr["token"]["id"] & 0xFF, rr["token"]["n"] & 0xFF
        u = _unhex(rr.get("unknown") or "")
        for i, o in enumerate(_HIRE_UNKNOWN):
            if i < len(u):
                b[o] = u[i]
        return bytes(b)
    if op == "SERVICE":
        # $FD FD is the shipped swap-back default (33 of 36) and what op_record_ir authors — it
        # rewrites the door cell from the saved $AD/$AE pair, which is how a service door becomes a
        # door again. The old code emitted _unhex of a missing key = ZERO bytes, which shifted
        # greeting, label and portrait two places left; a named pair with a default cannot do that.
        adv = _emit_adv(rr.get("advance") or {"op": 0xFD, "id": 0xFD})
        if rr.get("record"):                          # unknown family -> verbatim
            return _unhex(rr["record"])
        if rr.get("fields") is not None or rr.get("hdr") is not None:
            # the pre-family shape: an older document, or a record the family model could not
            # reproduce and the decoder deliberately left as hex. Checked FIRST so it cannot be
            # shadowed by a half-populated named form.
            return (bytes([0x80 | (rr["selector"] & 0x7F)])
                    + (_unhex(rr["hdr"]) if rr.get("hdr") is not None else adv)
                    + _unhex(rr.get("fields") or "")
                    + rr["name"].encode("latin-1") + b"\x00" + _unhex(rr.get("tail") or ""))
        sel = rr["selector"] & 0x7F
        rev_t, rev_offs = None, None
        if sel == 8:
            # REVIVE's +$04..+$06 are OFFSETS INTO THIS RECORD, so they are a function of the texts
            # and have to be derived from them — the same rule the SAY terminator follows. Carrying
            # the stored ones let a hand edit lengthen a string while the offsets stayed put, and the
            # engine then started reading in the middle of the previous one. The tail is therefore
            # built FIRST and the offset bytes fall out of it.
            rev_t, rev_offs = bytearray([rr["portrait"]]), []
            for off, s in zip(rr["str_offsets"], rr["texts"]):
                if not off:
                    rev_offs.append(0)                # 0 = "no string" — $CA9C reads the offset
                    continue                          # relative to the record, so 0 is unreachable
                rev_offs.append(0x13 + len(rev_t))
                rev_t += s.encode("latin-1") + b"\x00"
            if any(o > 0xFF for o in rev_offs):
                raise ValueError("a revive record's strings do not fit under a one-byte offset")
        if sel in (0, 6):
            f = [rr["greeting"], rr["heal_price"], rr["exam_price"], rr["cure_price"]]
        elif sel in (1, 5):
            f = [rr["buy_shift"], rr["sell_shift"], rr["greeting"], rr["areatrack"]]
        elif sel == 2:
            f = [rr["greeting"]]
        elif sel == 3:
            f = []
        elif sel == 7:
            f = [rr["greeting"], rr["prompt"], rr["amount"] & 0xFF, (rr["amount"] >> 8) & 0xFF]
        elif sel == 8:
            f = [rr["greeting"], *rev_offs]
        elif sel == 9:
            f = [rr["spare_03"], rr["page_count"], rr["spare_05"], rr["spare_06"]]
        else:
            raise ValueError(f"cannot emit SERVICE selector {sel}")
        t = bytearray()
        if sel in (0, 3):
            pass                                      # these two families have no tail at all
        elif sel == 1:
            t += bytes(rr["classes"]) + b"\xFF"
        elif sel == 5:
            t += bytes([rr["portrait"]]) + bytes(rr["classes"]) + b"\xFF"
        elif sel == 2:
            t += bytes(rr["skills"]) + b"\xFF"
        elif sel == 6:
            t += bytes([rr["portrait"]])              # exactly one byte — no terminator follows
        elif sel == 9:
            t += bytes([rr["portrait"]]) + bytes(rr["pages"])
        elif sel == 7:
            t += bytes([rr["portrait"], rr["pad"]]) + rr["confirm"].encode("latin-1") + b"\x00"
        elif sel == 8:
            t += rev_t                                # built above — the tail base IS record +$13
        return (bytes([0x80 | sel]) + adv + bytes(x & 0xFF for x in f)
                + rr["name"].encode("latin-1") + b"\x00" + bytes(t))
    if op == "EVENT":
        if rr.get("record"):                          # legacy v2 document, kept verbatim
            return _unhex(rr["record"])
        b = bytes([rr["event"] & 0xFF])
        # NOT _emit_adv_tail: its None -> $FF fallback would invent a byte on a 1-byte record, and
        # its ">= $10 drops the id" rule would drop the second $FF of the `ff ff` pairs that two
        # handlers overwrite as a unit. The presence of "id" is the only length signal.
        nx = rr.get("next")
        if nx:
            b += bytes([nx["op"] & 0xFF])
            if "id" in nx:
                b += bytes([nx["id"] & 0xFF])
        return b + _unhex(rr.get("args") or "")       # empty args really is empty — no fallback
    if op == "RAW":
        return _unhex(rr["raw"])
    if op == "DANGLE":
        return bytes([0xFF])                      # dead dispatch — a harmless 1-byte placeholder
    raise ValueError(f"cannot emit op {op!r}")


def ir_to_record(ir) -> bytes:
    """Serialize a JSON-IR document → an area-record .bin blob (loads at $3400). Inverse of
    record_to_ir; validated by decode(serialize(x))==x + decode-equivalence."""
    area = ir["meta"]["area"]
    W, H = ir["meta"]["width"], ir["meta"]["height"]
    slot = wl_area.slot_for_area(area)
    nib_stride = W // 2
    cid_base = W * nib_stride

    out = bytearray(slot)                          # grids region [0, slot)
    op, cid = ir["grid"]["op"], ir["grid"]["cellid"]
    for row in range(H):
        for col in range(W):
            i = row * W + col
            noff = row * nib_stride + (col >> 1)
            nv = op[i] & 0x0F
            out[noff] = (nv << 4) | (out[noff] & 0x0F) if (col & 1) == 0 else (out[noff] & 0xF0) | nv
            out[cid_base + row * W + col] = cid[i] & 0xFF

    tail = bytearray(_unhex(ir["descriptor"]["tail"]))   # [slot+$2C, slot+$5C) — the NON-pointer part
    cm = ir.get("combat_msgs")                     # desc+$37..+$5B = tail[$0B..$2F): the combat table,
    if cm:                                         # typed rather than opaque so renumbering can see it
        for k, v in enumerate(cm[:0x25]):
            if 0x0B + k < len(tail):
                tail[0x0B + k] = v & 0xFF
    _ensure(out, slot + 0x2C + len(tail))          # bytes; pointers [slot, slot+$2C) stay 0 until patched
    out[slot + 0x2C:slot + 0x2C + len(tail)] = tail

    def wptr(field_off, file_off):                 # descriptor pointer field ← RAM offset
        v = (file_off + RAM) & 0xFFFF
        out[slot + field_off] = v & 0xFF
        out[slot + field_off + 1] = (v >> 8) & 0xFF

    cursor = slot + 0x5C                            # sections begin right after the 92-byte descriptor

    def place(data: bytes) -> int:
        nonlocal cursor
        _ensure(out, cursor + len(data))
        out[cursor:cursor + len(data)] = data
        off = cursor
        cursor += len(data)
        return off

    # ── PACKING ────────────────────────────────────────────────────────────────────────────
    # grp16 is PINNED at its offset (self-modifying code with absolute self-refs), which leaves a
    # fixed hole [descriptor end, grp16). Everything else is free to go anywhere, because a group
    # ARRAY holds absolute pointers — a record does NOT have to follow its own array. So the unit of
    # packing is the individual ITEM (one array, or one record), not a whole group block: with block
    # granularity one 500-byte group that did not fit left the whole hole empty and pushed the record
    # over the 9728-byte cap.
    #
    # The chosen subset is laid out to END exactly at grp16, so the leftover slack sits directly
    # AFTER the descriptor, where nothing measures it. Slack must never sit after a record: a
    # record's end is "the next known structure", so trailing slack makes the last record re-decode
    # longer than it is and every re-pack grows it again.
    alias = {int(k): v for k, v in ir.get("array_alias", {}).items()}
    owner_of = {g: own for own, grps in alias.items() for g in grps}

    blocks = []                                    # ONE block per group — a group can hold both typed
    for g in sorted({int(k) for k in ir["groups"]} |    # records and verbatim ones (an unresolved
                    {int(k) for k in ir.get("opaque", {})}):   # extent rule), and emitting them as two
        rm = {}                                    # blocks wrote the descriptor pointer twice and lost
        if g in _MODELLED_GROUPS:                  # whichever half went first
            rm = {int(k): _emit_op_record(v) for k, v in ir["groups"].get(str(g), {}).items()
                  if v.get("op") != "DANGLE"}
        rm.update({int(k): _unhex(v) for k, v in ir.get("opaque", {}).get(str(g), {}).items()})
        if rm:
            blocks.append((g, rm))

    # collapse aliased groups onto their owner: ONE array, one record block, and every sharer's
    # descriptor pointer aimed at it afterwards (see array_alias in record_to_ir). Every slot was
    # typed exactly once by _ref_ops, so the merge is a plain union — no reading is "chosen" here.
    if owner_of:
        merged, order = {}, []
        for g, rm in blocks:
            own = owner_of.get(g, g)
            if own not in merged:
                merged[own] = {}
                order.append(own)
            for cid, by in rm.items():
                if cid in merged[own] and merged[own][cid] != by:
                    raise ValueError(f"alias slot grp{own}[{cid}] typed twice ({len(merged[own][cid])}"
                                     f" vs {len(by)} B) — _ref_ops must give each slot ONE owner")
                merged[own][cid] = by
        blocks = [(own, merged[own]) for own in order]

    code = _unhex(ir["grp16"]["code"]) if ir.get("grp16") else b""
    g16 = ir["grp16"]["offset"] if ir.get("grp16") else None
    # policy "float" gives up the pin: grp16 becomes an ordinary item in the pack and its absolute
    # self-references are rewritten to wherever it lands (wl_reloc). Opt-in per record — the default
    # stays "pin" because relocation cannot see an address the code builds from immediates, and the
    # pin costs nothing. wl_reloc.relocate() refuses outright when the plan is not safe.
    float16 = bool(code) and ir["grp16"].get("policy") == "float"
    g16_src = g16

    # An array is GLUED to its first record. The array's own extent is read back as "base .. first
    # structure after it", so an array followed by another array reads that one's pointer words as
    # extra slots — phantom records that then cut a real record's span short on the next decode.
    # One owned record behind it pins the extent at exactly n.
    items = []                                     # (size, kind, key, payload)
    for g, rm in blocks:
        first = min(rm)
        items.append((2 * (max(rm) + 1) + len(rm[first]), "arraypair", (g, first), rm[first]))
        for cid in sorted(rm):
            if cid != first:
                items.append((len(rm[cid]), "rec", (g, cid), rm[cid]))

    if float16:
        items.append((len(code), "g16", None, code))
        g16 = None                                 # no pinned hole: everything packs contiguously

    hole = (g16 - cursor) if (g16 is not None and g16 > cursor) else 0
    chosen, room = [], hole
    for it in sorted(items, key=lambda x: -x[0]):  # first-fit-decreasing into the pinned hole
        if it[0] <= room:
            chosen.append(it)
            room -= it[0]
    rest = [it for it in items if it not in chosen]

    off_of = {}                                    # item key -> file offset

    def lay(seq, at):
        nonlocal g16
        for sz, kind, key, payload in seq:
            _ensure(out, at + sz)
            if kind == "g16":                      # floating grp16: relocate it to where it landed
                out[at:at + sz] = wl_reloc.relocate(payload, g16_src + RAM, at + RAM)
                g16 = at
                wptr(0x26, at)
            elif kind == "rec":
                out[at:at + sz] = payload
                off_of[("rec", key)] = at
            else:                                  # arraypair: [array n*2][its first record]
                g_, cid_ = key
                off_of[("array", g_)] = at
                out[at + sz - len(payload):at + sz] = payload
                off_of[("rec", (g_, cid_))] = at + sz - len(payload)
            at += sz
        return at

    lay(chosen, cursor + room)                     # ends exactly at grp16; slack right after the desc
    if g16 is not None and not float16:
        _ensure(out, g16 + len(code))
        out[g16:g16 + len(code)] = code
        wptr(0x26, g16)
        cursor = max(cursor + hole, g16 + len(code))
    cursor = lay(rest, cursor)

    for g, rm in blocks:                           # arrays last: now every record offset is known
        base = off_of[("array", g)]
        for i in range(max(rm) + 1):
            v = (off_of[("rec", (g, i))] + RAM) if i in rm else 0
            out[base + i * 2] = v & 0xFF
            out[base + i * 2 + 1] = (v >> 8) & 0xFF
        wptr(0x06 + g * 2, base)
    if g16 is None:
        out[slot + 0x26] = 0
        out[slot + 0x27] = 0

    _own_records = {g for g, _ in blocks}
    for own, grps in alias.items():                      # sharers reuse the owner's array pointer
        v = wl_cell._le(out, slot + 0x06 + own * 2)
        if v:
            for g in grps:
                # ...unless the sharer has records of its OWN now. Aiming it back at the owner's
                # array made every id resolve into the other group's bytes: adding an encounter to
                # an area whose grp3 shared grp6's array produced a phantom grp3[70] out of grp6.
                if g != own and g != 16 and g not in _own_records:
                    out[slot + 0x06 + g * 2] = v & 0xFF
                    out[slot + 0x06 + g * 2 + 1] = (v >> 8) & 0xFF

    # The two foe tables are BUILT from the foe list when there is one (the grammar lives in
    # wl_cell.emit_foe_names, nowhere else); the raw blobs are the fallback for a table that did
    # not parse. Both stay parallel by construction — they are emitted from the same list.
    foes = ir.get("foes") or []
    if foes and all(f.get("name_segs") for f in foes) and "foe_names_term" in ir:
        nblob = wl_cell.emit_foe_names([f["name_segs"] for f in foes],
                                       _unhex(ir["foe_names_term"]))
        rblob = _unhex(ir.get("foe_table_header", "hex:")) \
            + b"".join(_unhex(f["record"])[:8].ljust(8, b"\x00") for f in foes) \
            + _unhex(ir.get("foe_table_extra", "hex:"))
    else:                                           # a table that did not parse, or a foe added by
        nblob, rblob = _unhex(ir["foe_names_raw"]), _unhex(ir["foe_records_raw"])   # a raw-blob path
    fnoff = place(nblob)
    wptr(0x02, fnoff)
    ftoff = place(rblob)                            # foe-records IMMEDIATELY before text (_foe_table_end)
    wptr(0x04, ftoff)
    text = tenc.build_region(_unhex(ir["text_table"])[:0x3C], ir["strings"])
    txtoff = place(text)
    wptr(0x00, txtoff)

    # INACTIVE groups: restore the symbolic pointer the original parked in their descriptor word
    # (usually foe-names, sometimes into grp16's code). Zeroing it would be equally dead but loses
    # a fact; keeping it means _section_bounds sees the same shape on re-decode.
    sym = {"text": txtoff, "foe_names": fnoff, "foe_table": ftoff,
           "grp16": g16 if g16 is not None else 0}
    live_groups = {g for g, _ in blocks} | {own for own in alias}
    for gk, d in ir.get("dead_ptrs", {}).items():
        g = int(gk)
        if g in live_groups:
            # The group has RECORDS now, so its descriptor word is a real array pointer. Restoring
            # the parked one on top of it aimed the group back at whatever it used to alias, and the
            # records vanished on the next decode — which is what happened the moment add_npc gave
            # an encounter to an area whose grp3 had been parked.
            continue
        b = d.get("base")
        if b in sym:
            wptr(0x06 + g * 2, sym[b] + d.get("delta", 0))
        elif b and b.startswith("grp"):
            v = wl_cell._le(out, slot + 0x06 + int(b[3:]) * 2)
            if v:
                wptr(0x06 + g * 2, v - RAM + d.get("delta", 0))

    return bytes(out)


def _ensure(buf: bytearray, n: int):
    if len(buf) < n:
        buf.extend(b"\x00" * (n - len(buf)))
