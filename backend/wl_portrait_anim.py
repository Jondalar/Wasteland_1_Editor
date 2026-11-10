#!/usr/bin/env python3
"""
Wasteland portrait ANIMATION — byte-exact Python port of the engine's 4-slot
animated-object tick + EOR patch draw.

Companion to cart_EF/tools/wl_decode_portrait.py (the STILL decoder). That tool ports
the engine's MAIN draw path (plain STA store, $064E); this module adds the animation:
the per-frame tick that counts 4 concurrent slots down and EOR-toggles small patch
streams (blink / mouth / glint) into the running $6000 multicolour bitmap.

SPEC — reversed from cart_EF/src/ef_engine.asm:
  anim_obj_init      ($0586, asm:879)  seed header + 4 slots, draw the base still
  frame_gate_ac      ($05C5, asm:918)  per-frame tick; $B1/$B2 dividers; walk 4 slots
  anim_obj_advance   ($05F4, asm:943)  read [delay][patch-lo][patch-hi]; $FF -> restart
  anim_obj_restart   ($0643, asm:990)  re-seed the slot stream ptr, retry advance
  draw_anim_object   ($0648/$064E, asm:997) decode one patch; nop-variant = EOR store
  anim_stream_byte   ($06C4, asm:1073) auto-incrementing stream reader
  anim_set_stream_ptr($06D0, asm:1085) slot ptr = record[6+2x..] + blob base

BLOB HEADER (record, blob[0..]):
  [0]=C1 start-col  [1]=BF row-reset  [2]=C2 end-col  [3]=C0 end-row
  [4..5]=main (base) stream offset    [6..13]=4 anim-slot stream ptr offsets (2 B each,
  slot ptr == 0 => slot disabled). All 88 portraits share dims 01 10 0b 63.

ANIM-SLOT STREAM (per active slot, at its blob offset):
  a chain of 3-byte entries  [delay][patch-lo][patch-hi]  terminated by a $FF delay:
    delay        -> slot countdown $DA,x (in "walk" units; see timing below)
    patch-lo/hi  -> blob offset of the patch draw-stream to run (added to blob base)
    $FF          -> RESTART: re-seed slot ptr to its start, read the first entry again
  The slot ptr auto-advances by 3 per entry (anim_stream_byte increments the operand).

PATCH DRAW-STREAM (EOR overlay, draw_anim nop-variant $0648):
  Identical cell/RLE grammar to the base draw, but (a) the store is read-modify-write
  EOR (toggle) instead of plain overwrite, and (b) a literal $00 byte TERMINATES the
  current sub-block (the base absorbs that test via a $2C BIT opcode; the anim path
  keeps the `beq` — asm:1032-1039). Layout:
    [init-skip]                          initial RLE skip count (usually 0)
    then sub-blocks, each: [X col][Y row][mode]  then cells, walking the rectangle
      col X..C2 x row Y..C0 (row reset to BF on column step), where per cell (skip==0):
        b == $00  -> end this sub-block, go read the next [X][Y][mode] header (NOT drawn)
        b == mode -> RLE: read [skip][repeat]; re-evaluate the SAME cell with skip set
        else      -> EOR the literal b into the bitmap
      (skip>0 emits `repeat` EOR'd, skip--, no $00 test — asm:1049-1053)
      X == $FF at a header -> whole patch done (rts)
      rectangle fully covered (X==C2 && Y==C0) -> whole patch done (rts)
  Bitmap address (base $6000): addr = (Y>>3)*320 + (Y&7) + X*8   (W2387+W23A7, asm:5620).

TICK / TIMING (frame_gate_ac -> anim_tick_objects, asm:929):
    dec $B1; if != 0: return
    dec $B2; if != 0: return                        [$B2 seeds to 1, then reloads to 2]
    $B2 = 2
    for slot x in 0..3 (enable flag $5BFB,x = 1): dec $DA,x; if 0 -> anim_obj_advance(x)
  A slot's `delay` is in WALK units: the walk fires every `delay` gate-openings.

  THE UNIT BELOW IS THE HARNESS'S, NOT THE GAME'S. This module decodes with the two preview
  drivers' model (portraits/christina/{christina_anim,anim_test}.asm), which force $B1=1 on
  every pass, so the $B1 gate always falls through and the walk lands every 2nd call. Hence
  `delays` are returned in those harness TICKS: one walk == 2 ticks.

  IN THE GAME IT IS ~70x SLOWER, and it is NOT raster-locked. $B1 is NEVER WRITTEN anywhere
  in the engine (verified across the full disassembly: the only $B1 access is the `dec` here),
  so it free-runs mod 256 and one walk = 2*256 = 512 calls to frame_gate_ac. Nothing paces
  those calls by raster either — the IRQ ($2947) only scans the keyboard. They come from
  whichever loop is spinning, and only TWO of those can matter, because a portrait is on
  screen ONLY in the encounter/dialogue screen and never on the map:
    - the blocking key-wait ($0247 -> W2514), a ~231-cycle spin: one delay unit ~= 118k
      cycles ~= 120 ms ~= 6 PAL frames. This is the steady state.
    - text output, 3 ticks per printed character: the animation runs FASTER while a message
      is typing out.
  The editor uses 140 ms, eyeballed against the real Sentinel scene (main.js
  MS_PER_DELAY_UNIT). PAL vs NTSC differs only by the ~4% clock; on an accelerated CPU the
  busy-loops evaporate and it races.

COLOURS (fixed picture-window scheme, NOT per-portrait — same as wl_decode_portrait):
  00=$D021 black  01=screen-hi $2 red  10=screen-lo $6 blue  11=colour-RAM $01 white.
  Box = char cols 1..11, rows 2..12; MC pixel doubled to 2 px wide (2:1). Pepto RGB.

No side effects on import.
"""

# ---------------------------------------------------------------------------
# Base still decode  (faithful copy of wl_decode_portrait.decode, $064E plain STA)
# ---------------------------------------------------------------------------
def decode_base(blob):
    """Return an 8192-byte bytearray = the $6000..$7FFF MC bitmap for one str blob
    (the base still, before any animation frame is applied)."""
    C1, BF, C2, C0 = blob[0], blob[1], blob[2], blob[3]
    p = blob[4] | (blob[5] << 8)          # main stream offset
    bm = bytearray(8192)

    def rd():
        nonlocal p
        v = blob[p]; p += 1; return v

    skip = rd()                            # draw_anim_skip_count+1 (initial)
    repeat = 0
    X = rd()                               # start byte-col
    if X == 0xFF:
        return bm
    Y = rd()                               # start pixel-row
    mode = rd()

    while True:                            # column/row fill (single run, rts at X==C2)
        addr = (Y >> 3) * 320 + (Y & 7) + X * 8
        if skip != 0:                      # RLE run
            a = repeat
            skip = (skip - 1) & 0xFF
            bm[addr] = a
        else:
            b = rd()
            if b == mode:                  # escape -> set up RLE, re-eval SAME cell
                skip = rd()
                repeat = rd()
                continue
            bm[addr] = b                   # literal (plain overwrite)
        if Y == C0:                        # advance: down a column to C0, then next col
            if X == C2:
                return bm                  # draw_anim_done (rts)
            Y = BF
            X = (X + 1) & 0xFF
        else:
            Y = (Y + 1) & 0xFF


# ---------------------------------------------------------------------------
# Patch draw  (draw_anim nop-variant $0648: EOR store + $00 sub-block terminator)
# ---------------------------------------------------------------------------
def _draw_patch(bm, blob, p, BF, C2, C0):
    """EOR-draw one animation patch stream (blob offset p) into the running bitmap."""
    skip = blob[p]; p += 1                 # initial RLE skip count
    repeat = 0
    while True:                            # draw_anim_run_loop: one sub-block per header
        X = blob[p]; p += 1                # col
        if X == 0xFF:                      # header $FF -> whole patch done
            return
        Y = blob[p]; p += 1                # row
        mode = blob[p]; p += 1
        while True:                        # walk the rectangle for this sub-block
            addr = (Y >> 3) * 320 + (Y & 7) + X * 8
            if skip != 0:                  # RLE run: EOR `repeat`, no $00 test
                bm[addr] ^= repeat
                skip = (skip - 1) & 0xFF
            else:
                b = blob[p]; p += 1
                if b == 0x00:              # $00 -> end sub-block, read next header
                    break
                if b == mode:              # escape -> RLE, re-eval SAME cell
                    skip = blob[p]; p += 1
                    repeat = blob[p]; p += 1
                    continue
                bm[addr] ^= b              # literal EOR toggle
            if Y == C0:                    # rectangle step (down, then next column)
                if X == C2:
                    return                 # rectangle covered -> whole patch done
                Y = BF
                X = (X + 1) & 0xFF
            else:
                Y = (Y + 1) & 0xFF


# ---------------------------------------------------------------------------
# 4-slot concurrent tick
# ---------------------------------------------------------------------------
def _seed(blob):
    """Build the initial animation state (post anim_obj_init) and the base bitmap."""
    st = {
        "C1": blob[0], "BF": blob[1], "C2": blob[2], "C0": blob[3],
        "slot_ptr":   [0, 0, 0, 0],        # current (auto-advancing) slot stream ptr
        "slot_start": [0, 0, 0, 0],        # slot stream start (for restart / disable)
        "DA":         [1, 1, 1, 1],        # per-slot countdown $DA,x (init 1)
        "B2": 1,                           # divider $B2 (init 1, reloads to 2)
    }
    for x in range(4):
        off = 6 + 2 * x
        sp = blob[off] | (blob[off + 1] << 8)
        st["slot_start"][x] = sp
        st["slot_ptr"][x] = sp
    return decode_base(blob), st


def _advance(bm, blob, st, x):
    """anim_obj_advance(x): consume one slot entry; draw its patch or restart."""
    if st["slot_start"][x] == 0:           # ora ($B9),y == 0 -> slot disabled, no-op
        return
    for _ in range(9):                     # restart retry guard (real streams start w/ a delay)
        p = st["slot_ptr"][x]
        delay = blob[p]; p += 1
        if delay == 0xFF:                  # end marker -> anim_obj_restart: re-seed, retry
            st["slot_ptr"][x] = st["slot_start"][x]
            continue
        st["DA"][x] = delay                # sta $DA,x
        lo = blob[p]; p += 1               # patch-lo (+ blob base)
        hi = blob[p]; p += 1               # patch-hi (+ blob base)
        st["slot_ptr"][x] = p              # save advanced slot ptr
        _draw_patch(bm, blob, lo | (hi << 8), st["BF"], st["C2"], st["C0"])
        return


def _tick(bm, blob, st):
    """frame_gate_ac for one frame (driver has reset $B1=1): apply the $B1/$B2
    dividers and, on a walk frame, count the 4 slots down and advance any that hit 0."""
    # dec $B1 with $B1==1 -> 0 -> falls through (both drivers reset $B1 every frame)
    st["B2"] = (st["B2"] - 1) & 0xFF
    if st["B2"] != 0:                      # not a walk frame
        return
    st["B2"] = 2
    for x in range(4):                     # enable flag $5BFB,x = 1 for all 4 slots
        st["DA"][x] = (st["DA"][x] - 1) & 0xFF
        if st["DA"][x] != 0:
            continue
        _advance(bm, blob, st, x)


def anim_frames(blob, max_ticks=120):
    """Simulate the portrait animation for up to `max_ticks` frames (ticks).

    Returns {"frames": [bitmap_bytes, ...], "delays": [ticks, ...]} where frames[i]
    is an 8192-byte $6000 bitmap and delays[i] is how many ticks (game frames) it is
    held before frames[i+1] replaces it. frames[0] is the base still. Play the frames
    in order, holding each delays[i] ticks, and loop for the animation.

    A tick here is a HARNESS tick, not a PAL frame — see TICK / TIMING above. The $B2
    divider makes the slot walk every 2nd tick, so `2 ticks == one delay unit`, and a
    delay unit is what the caller has to convert to wall time (the editor holds it for
    MS_PER_DELAY_UNIT = 140 ms). Do not read these as 50 Hz frames: that claim used to
    stand here and it made the previews play the animation 1.5x too slow.
    """
    bm, st = _seed(blob)
    frames = [bytes(bm)]
    delays = []
    last = bytearray(bm)
    hold = 0
    for _ in range(max_ticks):
        _tick(bm, blob, st)
        hold += 1
        if bm != last:                     # bitmap changed -> close previous frame
            delays.append(hold)
            frames.append(bytes(bm))
            last = bytearray(bm)
            hold = 0
    delays.append(hold)                    # final frame's remaining hold
    return {"frames": frames, "delays": delays}


# ---------------------------------------------------------------------------
# Bitmap -> RGBA  (fixed picture-window colour scheme; MC pixel doubled 2:1)
# ---------------------------------------------------------------------------
BOX = (1, 12, 2, 13)                       # char cols 1..11, rows 2..12 (exclusive hi)
SCREEN_HI, SCREEN_LO, CRAM, BG = 2, 6, 1, 0   # red / blue / white / black (C64 indices)
# ONE palette for the whole editor. This module used to carry its own Pepto variant, so the
# animated preview painted red as (129,51,43) while every other portrait surface used wl_tiles'
# (104,55,43). Identical picture, different bytes — a frame saved out of the preview then came back
# through the PNG import as "not a palette colour, snapped to red". Same table, no snapping.
try:
    from wl_tiles import PEPTO                                          # noqa: F401
except ImportError:                                                     # standalone __main__ use
    PEPTO = [(0, 0, 0), (255, 255, 255), (104, 55, 43), (112, 190, 196), (129, 55, 148),
             (86, 171, 54), (53, 40, 121), (191, 206, 114), (129, 84, 29), (72, 60, 0),
             (172, 101, 92), (68, 68, 68), (108, 108, 108), (154, 239, 123),
             (126, 110, 213), (149, 149, 149)]
_MC_COLOUR = {0: BG, 1: SCREEN_HI, 2: SCREEN_LO, 3: CRAM}

# Rendered picture dimensions: 11 char cols * 4 MC px * 2 (doubled) wide.
# HEIGHT IS 84, NOT 88. The picture window is pixel rows 16..99 (wl_portrait_encode.py header:
# "C1=01 BF=10 C2=0b C0=63 ... 11 cols * 84 rows"), which is ten and a HALF char rows — char row 12
# contributes only its first 4 lines. Deriving the height from the char box gave 88 and tacked four
# rows of padding under every portrait. Measured over all 100 str blobs: pixel rows 100..103 are
# black in every base and animate in none, so nothing is lost by cutting them. What they DID cost:
# the preview was stretched 88/84 = 4.8% vertically, and a frame saved out of it came back as 88x88
# — not a whole multiple of the 44x84 grid, so the PNG import refused its own picture (Till,
# 2026-08-23). 88x84 is 2x1 of the grid and round-trips.
ANIM_W = (BOX[1] - BOX[0]) * 4 * 2         # 88
ANIM_H = 84


def bitmap_to_rgba(bitmap):
    """Render the portrait box of an 8192-byte $6000 bitmap to raw RGBA bytes
    (ANIM_W x ANIM_H = 88x88, 4 bytes/pixel, alpha 255), top-to-bottom row-major."""
    c0, c1, r0, r1 = BOX
    out = bytearray(ANIM_W * ANIM_H * 4)
    for r in range(r0, r1):
        for c in range(c0, c1):
            off = r * 320 + c * 8
            for l in range(8):
                byte = bitmap[off + l]
                y = (r - r0) * 8 + l
                for pp in range(4):
                    bits = (byte >> ((3 - pp) * 2)) & 3
                    cr, cg, cb = PEPTO[_MC_COLOUR[bits]]
                    x = ((c - c0) * 4 + pp) * 2
                    if y >= ANIM_H:                     # last char row is half a row (see ANIM_H)
                        continue
                    for dx in (0, 1):
                        i = (y * ANIM_W + x + dx) * 4
                        out[i] = cr; out[i + 1] = cg; out[i + 2] = cb; out[i + 3] = 255
    return bytes(out)


if __name__ == "__main__":
    import sys
    blob = open(sys.argv[1], "rb").read()
    anim = anim_frames(blob, int(sys.argv[2]) if len(sys.argv) > 2 else 120)
    n = len(anim["frames"])
    print(f"{n} frames, delays (ticks) = {anim['delays']}")
    print(f"total ticks = {sum(anim['delays'])}, picture = {ANIM_W}x{ANIM_H} RGBA")
