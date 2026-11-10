#!/usr/bin/env python3
"""
Wasteland portrait ANIMATION EDITOR backend — decode the 4-slot EOR anim-object
into an editable toggle model, and build a full str blob back from that model.

Companion to (and pure consumer of, no monkey-patching):
  * wl_portrait_anim.py    — decode_base(blob)->bytearray(8192)  ($6000 MC bitmap,
                             $064E plain STA; wl_portrait_anim.py:69-107),
                             _draw_patch(bm,blob,p,BF,C2,C0)  (EOR patch draw, the
                             nop-variant $0648; wl_portrait_anim.py:113-143),
                             anim_frames(blob) (4-slot tick; wl_portrait_anim.py:199-222),
                             BOX / ANIM_W / ANIM_H (wl_portrait_anim.py:228-238).
  * wl_portrait_encode.py  — encode_base(bitmap, dims) (all-literal MAIN stream;
                             wl_portrait_encode.py:97-126), STR_DIMS
                             (wl_portrait_encode.py:72).

THE ANIM-OBJECT FORMAT (reversed; see wl_portrait_anim.py:20-46)
  header[6..13] = 4 anim-slot stream pointers (2-byte blob offsets; $0000 = disabled).
  a slot stream = chain of 3-byte frames [delay][patch-lo][patch-hi] ending in a $FF
  delay (RESTART). Each patch-lo/hi = blob offset of a PATCH cell-stream drawn with EOR
  (XOR toggle) into the running $6000 bitmap. One patch applied twice = back to base, so
  a slot that walks the SAME patch on two frames is a 2-state TOGGLE (base<->alt) — the
  common blink / mouth animation.

BOX PARAMETERISATION (2026-08-13)
  Every box-window operation lives on `AnimEditor(dims)`, so this same tooling drives NOT
  only the 88 portraits (box 01 10 0b 63 = STR_DIMS) but ANY $0586 anim-object with its
  own box — e.g. the title GLOBE (box 01 08 24 87 = 144x128, all 4 anim slots live, the
  satellite + atom-bomb title animation). The module-level `decode_anim_edit` /
  `build_anim_blob` / `bitmap_to_pixels` / … are byte-exact aliases bound to a default
  STR_DIMS instance (`STR`), so the portrait endpoints + the self-test are unchanged.

EDITOR MODEL (v1 = per-slot 2-state toggle)
  A slot = { enabled, delay, alt } where alt is the ALTERNATE state of the box (a GRID_W x
  GRID_H grid of 2-bit values, same axes as app.py `_bitmap_pixels`: box byte-cols C1..C2
  -> grid x, box pixel-rows BF..C0 -> grid y). The EOR patch that drives the toggle is
  patch = base XOR alt : only cells that differ carry a nonzero byte; equal cells
  contribute 0 and are simply omitted from the stream (a 0-EOR is a no-op, and a literal
  $00 in the patch grammar TERMINATES a sub-block, so unchanged cells are never written).

COORDINATES
  box-grid (x, y): x in 0..GRID_W-1, y in 0..GRID_H-1.  col = C1 + x//4, Y = BF + y,
  pixel p = x%4.  A pixel's 2-bit value sits in bits (6-2*p) of the cell byte at
  addr = (Y>>3)*320 + (Y&7) + col*8   (matches app.py:409 / wl_portrait_anim.py:89).

PATCH-STREAM GRAMMAR WE EMIT (all-literal, sparse; the EOR/$00 semantics are
wl_portrait_anim.py:113-143)
    [00]                         initial RLE skip count = 0
    per toggled cell, grouped into vertical runs within a column:
      [X col][Y row][mode]       sub-block header (mode = escape byte, chosen absent
                                 from this patch's values so the run is pure literals)
      then one EOR byte per cell down the run (col X, rows Y, Y+1, ... contiguous)
      [00]                       end this sub-block -> decoder reads the next header
    [FF]                         header X==$FF -> whole patch done
  Unchanged cells are SKIPPED by ending the sub-block ([00]) and opening a fresh
  sub-block header at the next run's (X,Y) — never by emitting a 0 byte (that is the
  terminator). A no-op / empty patch (no differing cells) is just [00][FF].
  Only if a patch used all 255 nonzero byte values would `mode` collide with a literal;
  then it falls back to the length-1 RLE escape [mode][01][value] (as wl_portrait_encode
  does for the base — wl_portrait_encode.py:113/121-123). Never happens for real data.

No side effects on import.
"""

import wl_portrait_anim as _anim
import wl_portrait_encode as _enc


# ---------------------------------------------------------------------------
# Box-independent primitives (pure; shared by every AnimEditor)
# ---------------------------------------------------------------------------
def _addr(col, Y):
    return (Y >> 3) * 320 + (Y & 7) + col * 8


def _set_ptr(buf, off, value):
    buf[off] = value & 0xFF
    buf[off + 1] = (value >> 8) & 0xFF


def _clamp_delay(d):
    """Slot delay byte: 1..254 (0 would wrap to 255 walks; $FF is the restart marker)."""
    d = int(d) & 0xFF
    if d == 0:
        return 1
    if d == 0xFF:
        return 0xFE
    return d


def _slot_raw_frames(blob, start, guard=64):
    """Walk one slot stream [delay][lo][hi]... to the $FF restart; return
    [{'delay':d, 'pptr':off}, ...] for one cycle (frames before the restart)."""
    out = []
    p = start
    for _ in range(guard):
        delay = blob[p]
        if delay == 0xFF:
            break
        out.append({"delay": delay, "pptr": blob[p + 1] | (blob[p + 2] << 8)})
        p += 3
    return out


def _encode_patch(mask):
    """Encode {(col,Y): eor_byte} into an EOR patch cell-stream that
    wl_portrait_anim._draw_patch reproduces EXACTLY (only these cells toggled).

    Emits one sub-block per contiguous vertical run in a column (see module docstring).
    _draw_patch(zero_bitmap, this) == mask, for every mask (verified in the self-test).
    """
    out = bytearray()
    out.append(0x00)                     # initial RLE skip count
    if not mask:
        out.append(0xFF)                 # empty / no-op patch
        return bytes(out)

    vals = set(mask.values())            # all nonzero (zero cells never enter the mask)
    # `mode` must not collide with any literal; prefer a nonzero value absent from the
    # data so the run is pure literals. (0x00 is the sub-block terminator, checked before
    # the mode test in _draw_patch, so it is never a valid literal anyway.)
    mode = next((v for v in range(1, 256) if v not in vals), None)
    escape = mode is None
    if escape:
        mode = 0x01                      # degenerate: patch used all 255 nonzero values

    for col in sorted({c for (c, _Y) in mask}):
        ys = sorted(Y for (c, Y) in mask if c == col)
        run = [ys[0]]
        for y in ys[1:]:
            if y == run[-1] + 1:
                run.append(y)
            else:
                _emit_run(out, col, run, mask, mode, escape)
                run = [y]
        _emit_run(out, col, run, mask, mode, escape)
    out.append(0xFF)                     # header X==$FF -> whole patch done
    return bytes(out)


def _emit_run(out, col, run, mask, mode, escape):
    out.append(col)                      # X
    out.append(run[0])                   # Y start
    out.append(mode)                     # escape byte
    for Y in run:
        v = mask[(col, Y)]
        if escape and v == mode:
            out += bytes((mode, 0x01, v))  # length-1 RLE escape -> re-eval same cell
        else:
            out.append(v)                # literal EOR byte
    out.append(0x00)                     # end this sub-block


# ---------------------------------------------------------------------------
# Box-parameterised editor.  One instance per box (STR_DIMS for portraits, the
# globe's 01 08 24 87 for the title anim-object, ...).
# ---------------------------------------------------------------------------
class AnimEditor:
    def __init__(self, dims=_enc.STR_DIMS):
        self.dims = tuple(dims)
        self.C1, self.BF, self.C2, self.C0 = self.dims
        self.GRID_W = (self.C2 - self.C1 + 1) * 4
        self.GRID_H = (self.C0 - self.BF + 1)

    # -- bitmap <-> box-grid  (byte-identical to app.py _bitmap_pixels / _pixels_bitmap) --
    def bitmap_to_pixels(self, bm):
        """8192-byte $6000 bitmap -> GRID_H x GRID_W grid of 2-bit values."""
        rows = []
        for Y in range(self.BF, self.C0 + 1):
            row = []
            for col in range(self.C1, self.C2 + 1):
                b = bm[_addr(col, Y)]
                for p in range(4):
                    row.append((b >> (6 - 2 * p)) & 0b11)
            rows.append(row)
        return rows

    def pixels_to_bitmap(self, px):
        """GRID_H x GRID_W grid of 2-bit values -> 8192-byte $6000 bitmap."""
        bm = bytearray(8192)
        for by, Y in enumerate(range(self.BF, self.C0 + 1)):
            for ci, col in enumerate(range(self.C1, self.C2 + 1)):
                b = 0
                for p in range(4):
                    b |= (px[by][ci * 4 + p] & 0b11) << (6 - 2 * p)
                bm[_addr(col, Y)] = b
        return bm

    def _is_grid(self, g):
        """True if g looks like a GRID_H x GRID_W pixel grid."""
        return (isinstance(g, (list, tuple)) and len(g) == self.GRID_H
                and isinstance(g[0], (list, tuple)) and len(g[0]) == self.GRID_W)

    def _as_bitmap(self, base):
        """Accept an 8192-byte bitmap OR a GRID_H x GRID_W grid; return a bytearray(8192)."""
        if isinstance(base, (bytes, bytearray)):
            if len(base) != 8192:
                raise ValueError(f"bitmap must be 8192 bytes, got {len(base)}")
            return bytearray(base)
        return self.pixels_to_bitmap(base)

    # -- masks: {(col, Y): eor_byte} over box cells a patch toggles --
    def _patch_mask(self, blob, pptr):
        """Run one patch (EOR) on a zero bitmap; return {(col,Y): eor_byte} for the box
        cells it toggles. Zero base -> the result IS the patch's raw EOR effect."""
        bm = bytearray(8192)
        _anim._draw_patch(bm, blob, pptr, blob[1], blob[2], blob[3])
        mask = {}
        for Y in range(self.BF, self.C0 + 1):
            for col in range(self.C1, self.C2 + 1):
                v = bm[_addr(col, Y)]
                if v:
                    mask[(col, Y)] = v
        return mask

    def _diff_mask(self, base_bm, alt_bm):
        """{(col,Y): base^alt} over box cells where the two bitmaps differ."""
        mask = {}
        for Y in range(self.BF, self.C0 + 1):
            for col in range(self.C1, self.C2 + 1):
                a = _addr(col, Y)
                d = base_bm[a] ^ alt_bm[a]
                if d:
                    mask[(col, Y)] = d
        return mask

    def _mask_positions(self, mask):
        """Box-grid [[x,y],...] of every pixel whose 2-bit field is nonzero in `mask`
        (walk order: row-major, matching the editor grid)."""
        out = []
        for Y in range(self.BF, self.C0 + 1):
            y = Y - self.BF
            for col in range(self.C1, self.C2 + 1):
                v = mask.get((col, Y), 0)
                if not v:
                    continue
                x0 = (col - self.C1) * 4
                for p in range(4):
                    if (v >> (6 - 2 * p)) & 0b11:
                        out.append([x0 + p, y])
        return out

    def _mask_sparse(self, mask):
        """Sparse [[x,y,bits],...] of the patch's per-pixel EOR delta (bits = 2-bit value)."""
        out = []
        for Y in range(self.BF, self.C0 + 1):
            y = Y - self.BF
            for col in range(self.C1, self.C2 + 1):
                v = mask.get((col, Y), 0)
                if not v:
                    continue
                x0 = (col - self.C1) * 4
                for p in range(4):
                    bits = (v >> (6 - 2 * p)) & 0b11
                    if bits:
                        out.append([x0 + p, y, bits])
        return out

    def _sparse_delta_to_mask(self, sparse):
        """Sparse [[x,y,bits],...] where `bits` is the EOR DELTA -> {(col,Y): eor_byte}."""
        mask = {}
        for x, y, bits in sparse:
            col = self.C1 + x // 4
            Y = self.BF + y
            p = x % 4
            b = mask.get((col, Y), 0) | ((bits & 0b11) << (6 - 2 * p))
            if b:
                mask[(col, Y)] = b
            else:
                mask.pop((col, Y), None)
        return mask

    def _sparse_alt_to_mask(self, sparse, base_bm):
        """Sparse [[x,y,altval],...] where `altval` is the ALTERNATE pixel value (0..3)
        -> {(col,Y): eor_byte}, eor = base_pixel XOR altval (cells that don't change drop)."""
        mask = {}
        for x, y, altval in sparse:
            col = self.C1 + x // 4
            Y = self.BF + y
            p = x % 4
            base_bits = (base_bm[_addr(col, Y)] >> (6 - 2 * p)) & 0b11
            eor = base_bits ^ (altval & 0b11)
            if not eor:
                continue
            mask[(col, Y)] = mask.get((col, Y), 0) | (eor << (6 - 2 * p))
        return mask

    # -- DECODE existing animation -> editable toggle model --
    def decode_anim_edit(self, blob):
        """Decode a str/anim-object blob's 4-slot animation into the editable toggle model.

        Returns {"slots": [slot0, slot1, slot2, slot3]} where each slot is:
          {
            "enabled": bool,               # header pointer != $0000
            "delay":   int,                # first frame's delay (v1: the toggle interval)
            "toggle":  [[x,y], ...],       # box-grid pixels this slot flips vs the base
            "alt":     [[...GRID_W...], ...GRID_H], # alt grid = base with the toggle flipped
            "frames":  [ {"delay":d, "patch":[[x,y,bits],...]}, ... ],  # exact per-frame EOR
            "track":   [ {"delay":d, "pixels":grid}, ... ],  # running STATE per frame
          }
        v1 note: `delay`/`toggle`/`alt` collapse a multi-frame slot to a 2-state toggle
        (first delay, unioned pixels, base XOR the first non-empty patch). `frames`/`track`
        keep the full detail so nothing is lost for a faithful round-trip.
        """
        base = _anim.decode_base(blob)
        slots = []
        for x in range(4):
            off = 6 + 2 * x
            sp = blob[off] | (blob[off + 1] << 8)
            if sp == 0:
                slots.append({"enabled": False, "delay": 0, "toggle": [],
                              "alt": self.bitmap_to_pixels(base), "frames": [], "track": []})
                continue
            raw = _slot_raw_frames(blob, sp)
            frames = []
            union = {}
            first_nonempty = None
            cur = bytearray(base)                            # running picture for the track view
            track = []
            for fr in raw:
                m = self._patch_mask(blob, fr["pptr"])
                frames.append({"delay": fr["delay"], "patch": self._mask_sparse(m)})
                for (col, Y), v in m.items():
                    cur[_addr(col, Y)] ^= v
                # track entry = the STATE shown for `delay` walks (after this patch applied)
                track.append({"delay": fr["delay"], "pixels": self.bitmap_to_pixels(bytes(cur))})
                for k, v in m.items():
                    union[k] = union.get(k, 0) | v           # union of toggled positions
                if m and first_nonempty is None:
                    first_nonempty = m
            alt_bm = bytearray(base)
            if first_nonempty:
                for (col, Y), v in first_nonempty.items():   # v1 alt = base XOR first patch
                    alt_bm[_addr(col, Y)] ^= v
            slots.append({
                "enabled": True,
                "delay": raw[0]["delay"] if raw else 0,
                "toggle": self._mask_positions(union),
                "alt": self.bitmap_to_pixels(alt_bm),
                "frames": frames,
                "track": track,
            })
        return {"slots": slots}

    # -- BUILD a full str blob from base + toggle model --
    def _normalize_slot(self, s, base_bm, mark=None):
        """A slot spec -> None (disabled) or a list of (delay, mask) frames.

        `mark`, if given, is a list the auto-close appends to, so the caller can TELL the
        user that a frame was added which was not in the track it sent (the editor now ends
        every track on the base itself, so this only fires for API clients).

        Accepted forms:
          None / {"enabled": False}                              -> disabled
          {"enabled":True, "track":[{"delay":d,"pixels":grid},..]} -> STATE sequence: each
                                                frame is the full picture shown for `delay`
                                                walks; masks = successive diffs (frame vs
                                                previous, first vs base). If the last state
                                                != base, a closing return-to-base patch is
                                                auto-appended (loop must end on base or the
                                                picture drifts every cycle).
          {"enabled":True, "delay":d, "alt":[[..GRID_W..]xGRID_H]} -> 2-state toggle (grid alt)
          {"enabled":True, "delay":d, "toggle":[[x,y,altval]..]} -> 2-state toggle (sparse alt)
          {"enabled":True, "frames":[{"delay":d,"patch":[[x,y,bits]..]}, ...]} -> exact frames
        """
        if s is None or s.get("enabled", True) is False:
            return None

        if s.get("track"):
            frames = []
            prev = bytes(base_bm)
            for f in s["track"]:
                bm = self.pixels_to_bitmap(f["pixels"]) if self._is_grid(f.get("pixels")) else prev
                frames.append((int(f.get("delay", 1)), self._diff_mask(prev, bm)))
                prev = bytes(bm)
            if prev != bytes(base_bm):
                frames.append((8, self._diff_mask(prev, bytes(base_bm))))   # auto-close the loop
                if mark is not None:
                    mark.append(len(frames) - 1)
            if not any(m for _, m in frames):
                return None                  # every state == base -> nothing animates
            return frames

        if s.get("frames"):
            frames = [(int(f.get("delay", 1)),
                       self._sparse_delta_to_mask(f.get("patch") or []))
                      for f in s["frames"]]
            return frames or None

        # v1 2-state toggle
        delay = int(s.get("delay", 1))
        alt = s.get("alt")
        if self._is_grid(alt):
            mask = self._diff_mask(base_bm, self.pixels_to_bitmap(alt))
        elif s.get("toggle"):
            mask = self._sparse_alt_to_mask(s["toggle"], base_bm)
        elif alt:                            # sparse alt list [[x,y,altval],...]
            mask = self._sparse_alt_to_mask(alt, base_bm)
        else:
            mask = {}
        if not mask:
            return None                      # nothing differs -> leave slot disabled
        return [(delay, mask), (delay, mask)]  # symmetric toggle: alt held `delay`, base `delay`

    def build_anim_blob(self, base, slots, template_blob=None, report=None):
        """Build a full str/anim-object blob: base still = `base`, animation = `slots`.

        base   : an 8192-byte $6000 bitmap OR a GRID_H x GRID_W pixel grid.
        slots  : list of up to 4 slot specs (see _normalize_slot). Fewer than 4 / None /
                 {"enabled":False} entries disable the remaining slots ($0000 pointer).
        template_blob : optional; unused for geometry (the box is this editor's dims). The
                 base still AND the animation are rebuilt wholesale from the arguments.
        report : optional dict; receives {"auto_closed": [slot, ...]} listing every slot whose
                 track did not end on the base and therefore had a return-to-base frame
                 appended. Nothing is ever added silently.

        Guarantees:
          decode_base(build_anim_blob(bm, slots)) == bm.
          anim_frames(build_anim_blob(bm, slots)) toggles exactly the intended pixels.

        Layout: [14-byte header][main base stream][patch streams...][slot streams...].
        Identical patch streams are de-duplicated.
        """
        base_bm = self._as_bitmap(base)

        blob = bytearray(14)
        blob[0], blob[1], blob[2], blob[3] = self.C1, self.BF, self.C2, self.C0
        _set_ptr(blob, 4, len(blob))                     # main stream at offset 14
        blob += _enc.encode_base(bytes(base_bm), dims=self.dims)

        norm = []
        for i, spec in enumerate((list(slots or []) + [None] * 4)[:4]):
            mark = []
            norm.append(self._normalize_slot(spec, base_bm, mark))
            if mark and report is not None:
                report.setdefault("auto_closed", []).append(i)

        # Pass 1: append (de-duplicated) patch streams, remember their offsets.
        patch_off = {}
        plans = []
        for frames in norm:
            if frames is None:
                plans.append(None)
                continue
            plan = []
            for delay, mask in frames:
                pb = _encode_patch(mask)
                if pb not in patch_off:
                    patch_off[pb] = len(blob)
                    blob += pb
                plan.append((_clamp_delay(delay), patch_off[pb]))
            plans.append(plan)

        # Pass 2: append slot streams, wire the header pointers.
        for x, plan in enumerate(plans):
            if plan is None:
                _set_ptr(blob, 6 + 2 * x, 0x0000)
                continue
            _set_ptr(blob, 6 + 2 * x, len(blob))
            for delay, poff in plan:
                blob.append(delay)
                blob.append(poff & 0xFF)
                blob.append((poff >> 8) & 0xFF)
            blob.append(0xFF)                             # restart terminator
        return bytes(blob)


# ---------------------------------------------------------------------------
# Default STR_DIMS instance + byte-exact module-level aliases (portrait endpoints
# + the self-test keep the pre-2026-08-13 API unchanged).
# ---------------------------------------------------------------------------
STR = AnimEditor(_enc.STR_DIMS)
C1, BF, C2, C0 = STR.C1, STR.BF, STR.C2, STR.C0
GRID_W, GRID_H = STR.GRID_W, STR.GRID_H

bitmap_to_pixels = STR.bitmap_to_pixels
pixels_to_bitmap = STR.pixels_to_bitmap
_is_grid = STR._is_grid
_as_bitmap = STR._as_bitmap
_patch_mask = STR._patch_mask
_diff_mask = STR._diff_mask
_mask_positions = STR._mask_positions
_mask_sparse = STR._mask_sparse
_sparse_delta_to_mask = STR._sparse_delta_to_mask
_sparse_alt_to_mask = STR._sparse_alt_to_mask
decode_anim_edit = STR.decode_anim_edit
_normalize_slot = STR._normalize_slot
build_anim_blob = STR.build_anim_blob


# ---------------------------------------------------------------------------
# Self-test  (runs only when executed directly; no import-time side effects)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    CHRISTINA = os.path.join(REPO, "portraits", "christina", "s2_str_id78.bin")

    def anim_pixel_set(blob, max_ticks=120):
        """Set of box-grid (x,y) pixels that ever differ from the base across the anim."""
        seq = _anim.anim_frames(blob, max_ticks)
        base_px = bitmap_to_pixels(seq["frames"][0])
        moved = set()
        for f in seq["frames"][1:]:
            px = bitmap_to_pixels(f)
            for y in range(GRID_H):
                for x in range(GRID_W):
                    if px[y][x] != base_px[y][x]:
                        moved.add((x, y))
        return moved, len(seq["frames"]), seq["delays"]

    print("=== _encode_patch fuzz (decode(encode(mask)) == mask) ===")
    import random
    rnd = random.Random(1)
    fails = 0
    for t in range(400):
        # random sparse mask over the box
        mask = {}
        for _ in range(rnd.randint(0, 40)):
            col = rnd.randint(C1, C2)
            Y = rnd.randint(BF, C0)
            v = rnd.randint(1, 255)
            mask[(col, Y)] = v
        pb = _encode_patch(mask)
        # decode it via a tiny 1-frame blob
        tb = bytearray(14)
        tb[0], tb[1], tb[2], tb[3] = C1, BF, C2, C0
        poff = len(tb)
        tb += pb
        got = _patch_mask(bytes(tb), poff)
        if got != mask:
            fails += 1
            if fails <= 3:
                print("  MISMATCH", sorted(mask.items())[:4], "->", sorted(got.items())[:4])
    print(f"  {400 - fails}/400 masks round-tripped, {fails} fail")

    print("\n=== synthetic: 1 slot toggling a 3x3 eye region every 30 walks ===")
    blank = bytearray(8192)                       # blank base (all box pixels = 0)
    alt = [[0] * GRID_W for _ in range(GRID_H)]
    eye = [(x, y) for y in (30, 31, 32) for x in (20, 21, 22)]   # 9 box pixels
    for x, y in eye:
        alt[y][x] = 1                             # flip them to colour 1 (red)
    synth = build_anim_blob(blank, [{"enabled": True, "delay": 30, "alt": alt}])
    assert _anim.decode_base(synth) == blank, "base not preserved"
    moved, nframes, delays = anim_pixel_set(synth, max_ticks=250)   # >4 toggles
    print(f"  decode_base preserved: OK")
    print(f"  moved pixels = {len(moved)} (want 9): {'OK' if moved == set(eye) else 'FAIL '+str(sorted(moved))}")
    print(f"  distinct frames = {nframes} (base<->alt), delays(ticks) = {delays}")
    # steady-state alt/base holds (drop the seed-immediate first toggle + truncated tail)
    steady = delays[1:-1]
    print(f"  steady holds (ticks) = {steady}  -> {'OK 60 ticks == 30 walks' if steady and all(g == 60 for g in steady) else 'UNEXPECTED'}")

    if os.path.exists(CHRISTINA):
        print("\n=== christina round-trip ===")
        cb = open(CHRISTINA, "rb").read()
        base = _anim.decode_base(cb)
        dec = decode_anim_edit(cb)
        for i, s in enumerate(dec["slots"]):
            print(f"  slot {i}: enabled={s['enabled']} delay={s['delay']} "
                  f"toggle_px={len(s['toggle'])} frames={len(s['frames'])}")

        orig_moved, orig_n, orig_delays = anim_pixel_set(cb)

        # (a) EXACT rebuild from the per-frame model -> must match frame count + pixels.
        exact = build_anim_blob(base, [{"enabled": s["enabled"], "frames": s["frames"]}
                                       for s in dec["slots"]])
        assert _anim.decode_base(exact) == base, "exact: base not preserved"
        ex_moved, ex_n, ex_delays = anim_pixel_set(exact)
        print(f"  [exact frames]  orig: {orig_n} frames, {len(orig_moved)} moved px | "
              f"rebuilt: {ex_n} frames, {len(ex_moved)} moved px")
        print(f"                  frame-count match: {ex_n == orig_n}   "
              f"delays match: {ex_delays == orig_delays}   "
              f"pixel-set match: {ex_moved == orig_moved}")

        # (b) v1 rebuild from {enabled,delay,alt} -> pixel SET must match (frame count
        #     differs: v1 collapses the asymmetric multi-frame slots to symmetric toggles).
        v1 = build_anim_blob(base, [{"enabled": s["enabled"], "delay": max(1, s["delay"]),
                                     "alt": s["alt"]} for s in dec["slots"]])
        assert _anim.decode_base(v1) == base, "v1: base not preserved"
        v1_moved, v1_n, _ = anim_pixel_set(v1)
        print(f"  [v1 toggle]     rebuilt: {v1_n} frames, {len(v1_moved)} moved px | "
              f"pixel-set match: {v1_moved == orig_moved}")

        # (c) GLOBE (title anim-object, box 01 08 24 87) — box parameterisation smoke test.
        GLOBE = os.path.join(REPO, "assets", "title", "loadB_intro_text.bin")
        if os.path.exists(GLOBE):
            print("\n=== globe (box-parameterised AnimEditor) ===")
            gb = open(GLOBE, "rb").read()
            gdims = (gb[0], gb[1], gb[2], gb[3])
            ged = AnimEditor(gdims)
            gbase = _anim.decode_base(gb)
            gdec = ged.decode_anim_edit(gb)
            for i, s in enumerate(gdec["slots"]):
                print(f"  slot {i}: enabled={s['enabled']} delay={s['delay']} "
                      f"toggle_px={len(s['toggle'])} frames={len(s['frames'])}")
            gre = ged.build_anim_blob(gbase, [{"enabled": s["enabled"], "frames": s["frames"]}
                                              for s in gdec["slots"]])
            assert _anim.decode_base(gre) == gbase, "globe: base not preserved"
            g_orig = _anim.anim_frames(gb, 120)
            g_re = _anim.anim_frames(gre, 120)
            print(f"  base preserved: OK   frames orig={len(g_orig['frames'])} "
                  f"rebuilt={len(g_re['frames'])}   delays match: {g_orig['delays']==g_re['delays']}")
    else:
        print("\n(christina blob not found; skipped round-trip)")
