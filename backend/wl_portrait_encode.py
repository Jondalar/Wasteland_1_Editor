#!/usr/bin/env python3
"""
Wasteland portrait (str anim-object) ENCODER — byte-exact inverse of the base
still decoder.

Companion / inverse of:
  * editor/backend/wl_portrait_anim.py  — decode_base(blob)->bytearray(8192)
    (the engine's MAIN draw path, $064E plain STA; verified 0-diff vs 6502).
    Cell walk + RLE/escape grammar: wl_portrait_anim.py lines 69-107.
  * cart_EF/tools/wl_decode_portrait.py — the same still decoder + the header /
    stream grammar in its module docstring (lines 9-23) and decode() (lines 27-65).

WHAT THIS DOES
  Given an 8192-byte $6000..$7FFF multicolour bitmap (as produced by decode_base),
  emit the MAIN cell-list stream that decode_base reproduces EXACTLY, and assemble a
  full str blob around it (shared header dims, correct pointers, optional animation
  copied verbatim from a template blob). So the editor can save an edited base
  portrait back to a blob the engine renders identically.

THE MAIN STREAM (what decode_base reads at blob offset header[4..5])
  Header order consumed by the decoder (wl_portrait_anim.py:81-86):
      [skip][X][Y][mode]
  then, per cell, walking the box column-major:
      cols X..C2, rows (Y first col, then BF)..C0     (wl_portrait_anim.py:101-107)
      addr = (Y>>3)*320 + (Y&7) + X*8                 (wl_portrait_anim.py:89)
    per cell (skip==0, wl_portrait_anim.py:90-100):
      b = read()
      if b == mode: skip=read(); repeat=read(); RE-EVAL same cell (RLE run)
      else        : bm[addr] = b            (plain literal overwrite)
    per cell (skip>0): bm[addr] = repeat; skip--     (RLE run, no read)
    terminate when X==C2 and Y==C0 (wl_portrait_anim.py:101-103).

  All 88 portraits share dims  C1=01 BF=10 C2=0b C0=63, i.e. byte-cols 1..11,
  pixel-rows 16..99 => exactly 924 distinct cells (11 cols * 84 rows).

GRAMMAR WE EMIT (all-literal; the task explicitly blesses this)
      [00]        initial skip count = 0  (first cell reads a byte)
      [C1]        X start = 1
      [BF]        Y start = 16  (== box top; first col walks BF..C0 like the rest)
      [mode]      escape byte (see below)
      then 924 cell bytes, one per cell in decode order:
        value != mode  ->  emit value                (1-byte literal)
        value == mode  ->  emit [mode][01][value]     (1-run RLE escape)
  The RLE-escape path exists only to resolve the mode collision: a literal byte
  equal to `mode` cannot be written directly (the decoder would treat it as an RLE
  escape), so it is written as a length-1 RLE run instead, which decode_base
  re-evaluates into exactly that one cell (wl_portrait_anim.py:96-99 -> 90-93).

  MODE COLLISION RESOLUTION
    We pick `mode` = the smallest byte value NOT present among the 924 cell values.
    Then no literal ever equals mode and the stream is pure literals (no escapes).
    Only if a bitmap somehow uses all 256 byte values in the box do we fall back to
    mode=0x00 and emit the length-1 RLE escape for every cell whose value is 0x00.
    Either way decode_base reproduces the input bitmap byte-exact.

BUILD_BLOB LAYOUT
  * template_blob given (the normal editor path — preserve the animation):
      copy the whole template verbatim (header dims, the 4 anim-slot ptrs at [6..13],
      the slot streams and every patch stream keep their original offsets, so all
      animation pointers stay valid), then APPEND the freshly-encoded main stream at
      the end of the blob and repoint header[4..5] to it. The template's old main
      stream bytes are left in place as dead weight (harmless; nothing points to them
      once header[4..5] is moved).
  * no template: [14-byte header][main stream]; header[4..5]=14, all 4 slots $0000
    (animation disabled).

No side effects on import.
"""

# Shared header dims for all 88 portraits: C1 start-col, BF row-reset, C2 end-col,
# C0 end-row  (wl_decode_portrait.py:9-12, "01 10 0b 63").
STR_DIMS = (0x01, 0x10, 0x0b, 0x63)


def _cell_addresses(dims=STR_DIMS):
    """The $6000-bitmap byte addresses decode_base visits, in order.

    Reproduces the decoder's column-major box walk (wl_portrait_anim.py:88-107):
    start at X=C1, Y=BF and step Y down to C0, then next column resetting Y to BF,
    stopping after (X==C2, Y==C0). addr = (Y>>3)*320 + (Y&7) + X*8.
    """
    C1, BF, C2, C0 = dims
    addrs = []
    X, Y = C1, BF
    while True:
        addrs.append((Y >> 3) * 320 + (Y & 7) + X * 8)
        if Y == C0:
            if X == C2:
                break
            Y = BF
            X = (X + 1) & 0xFF
        else:
            Y = (Y + 1) & 0xFF
    return addrs


def encode_base(bitmap, dims=STR_DIMS):
    """Encode an 8192-byte $6000 MC bitmap into a MAIN cell-list stream.

    decode_base(blob-with-this-stream) == bitmap for every byte the decoder writes
    (the 924 box cells); bytes outside the box are left 0 by the decoder, exactly as
    they are 0 in any bitmap produced by decode_base. Returns raw stream bytes to be
    placed at blob offset header[4..5].
    """
    if len(bitmap) != 8192:
        raise ValueError(f"bitmap must be 8192 bytes, got {len(bitmap)}")
    C1, BF, C2, C0 = dims
    addrs = _cell_addresses(dims)
    vals = [bitmap[a] for a in addrs]

    used = set(vals)
    # Prefer a mode value absent from the data -> zero escapes, pure literals.
    mode = next((v for v in range(256) if v not in used), 0x00)

    out = bytearray()
    out.append(0x00)   # initial skip count
    out.append(C1)     # X start
    out.append(BF)     # Y start (== box top; first column walks BF..C0 like the rest)
    out.append(mode)   # escape byte

    # RUN-LENGTH, which this used to skip entirely: it wrote one literal per box cell, so a
    # re-encode of an UNCHANGED portrait grew from 942 to 1870 bytes — 924 literals where the
    # 1988 data uses runs. Six portraits would have cost ~5.5 KB of flash for no new pixels.
    # A run costs 3 bytes (escape, count, value) and the decoder re-evaluates the same cell after
    # the escape (wl_portrait_anim.decode_base), so it pays from 4 equal cells upward; at 3 it is
    # a wash and below that it loses. Count is one byte, so runs are cut at 255.
    i, n = 0, len(vals)
    while i < n:
        v = vals[i]
        run = 1
        while i + run < n and vals[i + run] == v and run < 255:
            run += 1
        if run >= 4 or v == mode:      # `mode` can never be a literal — it would read as an escape
            out += bytes((mode, run, v))
            i += run
        else:
            out += bytes([v]) * run
            i += run
    return bytes(out)


def _set_ptr(buf, off, value):
    buf[off] = value & 0xFF
    buf[off + 1] = (value >> 8) & 0xFF


def build_blob(bitmap, anim_slots=None, template_blob=None, dims=STR_DIMS):
    """Assemble a full str blob around an encoded main stream.

    decode_base(build_blob(bitmap)) == bitmap.

    template_blob : if given, the resulting blob preserves that template's animation
        verbatim (its 4 anim-slot pointers, slot streams and patch streams keep their
        original offsets). The new main stream is appended at the end and header[4..5]
        is repointed to it.
    anim_slots : optional list/tuple of 4 slot-pointer offsets to write into header
        [6..13], overriding whatever is there (template's, or the default $0000).
        $0000 disables a slot. (Pointers only — the patch data they reference must
        already live in the blob, i.e. this is normally used together with a template.)
    """
    main_stream = encode_base(bitmap, dims)
    C1, BF, C2, C0 = dims

    if template_blob is not None:
        has_anim = any(template_blob[6 + 2 * i] | (template_blob[7 + 2 * i] << 8) for i in range(4))
        if not has_anim and anim_slots is None:
            # Nothing in the template is worth keeping — no slots, no patch streams — so the append
            # dance is pointless, and taking it left the old main stream behind as dead bytes:
            # 928 x 10 non-animated portraits = 9280 wasted, exactly the remainder that was still
            # there after the animated ones were fixed.
            header = bytearray(14)
            header[0], header[1], header[2], header[3] = C1, BF, C2, C0
            _set_ptr(header, 4, 14)
            return bytes(header) + main_stream
        if has_anim and anim_slots is None:
            # Rebuild instead of patch. Appending the new main stream and repointing the header left
            # the OLD one in the file with nothing pointing at it — 928 dead bytes on TREX. Every
            # pointer in an animated blob is enumerable (the slot streams are 3-byte entries whose
            # patch-lo/hi IS a blob offset), so the whole thing can be emitted fresh. Verified on all
            # 90 animated portraits: same frames, same delays, same still, 176-384 bytes smaller.
            return rebuild_animated(template_blob, bitmap, dims)
        blob = bytearray(template_blob)
        blob[0], blob[1], blob[2], blob[3] = C1, BF, C2, C0   # normalize shared dims
        main_off = len(blob)
        blob += main_stream
        _set_ptr(blob, 4, main_off)                            # repoint main stream
        if anim_slots is not None:
            for i in range(4):
                _set_ptr(blob, 6 + 2 * i, anim_slots[i])
        return bytes(blob)

    # No template: minimal blob = [14-byte header][main stream].
    header = bytearray(14)
    header[0], header[1], header[2], header[3] = C1, BF, C2, C0
    _set_ptr(header, 4, 14)                                     # main stream follows
    slots = anim_slots if anim_slots is not None else (0, 0, 0, 0)
    for i in range(4):
        _set_ptr(header, 6 + 2 * i, slots[i])
    return bytes(header) + main_stream


def blank_bitmap():
    """An all-background ($6000) blank bitmap: every box cell = 0x00 (00-bits =
    background colour). decode_base only writes the box cells, so an all-zero 8192-byte
    buffer already IS the blank still."""
    return bytearray(8192)


# ---------------------------------------------------------------------------
# Self-test (runs only when executed directly; no import-time side effects)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import glob
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from wl_portrait_anim import decode_base

    samples = ["portraits/christina/s2_str_id78.bin"] + sorted(
        glob.glob("baseline/side*/s*_str_id*.bin")
    )
    ok = 0
    fail = 0
    for path in samples[:12]:
        if not os.path.exists(path):
            continue
        orig = open(path, "rb").read()
        target = decode_base(orig)                 # reference bitmap
        stream = encode_base(target)
        rebuilt = build_blob(target, template_blob=orig)
        got = decode_base(rebuilt)
        same = (got == target)
        orig_main = None
        try:
            om = orig[4] | (orig[5] << 8)
            orig_main = min((orig[6 + 2 * i] | (orig[7 + 2 * i] << 8))
                            for i in range(4)
                            if (orig[6 + 2 * i] | (orig[7 + 2 * i] << 8)) > om) - om
        except ValueError:
            orig_main = None
        tag = "OK " if same else "FAIL"
        print(f"{tag} {os.path.basename(path):22s} "
              f"enc_main={len(stream):4d}B orig_main~={orig_main} "
              f"diff={sum(a != b for a, b in zip(got, target))}")
        ok += same
        fail += not same
    print(f"\n{ok} ok, {fail} fail")


def _patch_end(blob, p, BF, C2, C0):
    """Byte just past the EOR patch draw-stream at `p` — the same walk as
    wl_portrait_anim._draw_patch, counting bytes instead of drawing them."""
    skip = blob[p]; p += 1
    while True:
        X = blob[p]; p += 1
        if X == 0xFF:
            return p
        Y = blob[p]; p += 1
        mode = blob[p]; p += 1
        while True:
            if skip:
                skip = (skip - 1) & 0xFF
            else:
                b = blob[p]; p += 1
                if b == 0x00:
                    break
                if b == mode:
                    skip = blob[p]; p += 1
                    p += 1                                 # repeat value
                    continue
            if Y == C0:
                if X == C2:
                    return p
                Y = BF
                X = (X + 1) & 0xFF
            else:
                Y = (Y + 1) & 0xFF


def rebuild_animated(template_blob, bitmap, dims=STR_DIMS):
    """Replace the STILL of an animated portrait and rebuild the blob with no dead bytes.

    `build_blob(template_blob=...)` keeps the template verbatim and appends the new main stream,
    which leaves the OLD one in the file with nothing pointing at it — 928 orphaned bytes on TREX.
    That was avoidable all along: the animation format is fully documented in wl_portrait_anim's
    header, reversed from the engine. A slot stream is 3-byte entries `[delay][patch-lo][patch-hi]`
    ended by a $FF delay, and patch-lo/hi is a BLOB OFFSET — so every pointer in the file is
    enumerable, and the blob can be emitted fresh instead of patched.

    Layout written: [14-byte header][slot streams][patch streams][new main stream], every offset
    recomputed. Patch streams shared by two slots are emitted once.
    """
    C1, BF, C2, C0 = dims
    t = template_blob
    slots = [t[6 + 2 * i] | (t[7 + 2 * i] << 8) for i in range(4)]

    entries = {}                                           # slot index -> [(delay, patch_off), ...]
    patches = {}                                           # old patch offset -> bytes
    for i, sp in enumerate(slots):
        if not sp:
            continue
        ent, p = [], sp
        while True:
            d = t[p]
            if d == 0xFF:
                break
            po = t[p + 1] | (t[p + 2] << 8)
            ent.append((d, po))
            if po not in patches:
                patches[po] = bytes(t[po:_patch_end(t, po, BF, C2, C0)])
            p += 3
        entries[i] = ent

    out = bytearray(14)
    out[0], out[1], out[2], out[3] = C1, BF, C2, C0
    slot_at, patch_at = {}, {}
    for i, ent in entries.items():                         # slot streams first
        slot_at[i] = len(out)
        out += bytes(3 * len(ent) + 1)                     # reserved; filled once patches are placed
    for po, data in patches.items():                       # then the patch streams
        patch_at[po] = len(out)
        out += data
    for i, ent in entries.items():                         # now the slot streams can be written
        p = slot_at[i]
        for d, po in ent:
            out[p] = d
            out[p + 1] = patch_at[po] & 0xFF
            out[p + 2] = (patch_at[po] >> 8) & 0xFF
            p += 3
        out[p] = 0xFF
    main_off = len(out)
    out += encode_base(bitmap, dims)
    _set_ptr(out, 4, main_off)
    for i in range(4):
        _set_ptr(out, 6 + 2 * i, slot_at.get(i, 0))
    return bytes(out)
