#!/usr/bin/env python3
"""wl_title_assets.py — the two title-screen assets, out to PNG and back in.

WHAT THEY ARE, because both file names lie. `ef_title.asm` calls one load "intro IMAGE" and the
other "intro TEXT"; the extractor calls the second one "anim". None of those is right, and "intro"
here means the 1988 TITLE sequence, not the TREX boot intro this project added.

  loadA_intro_image.bin    512 B   THE WASTELAND LOGO. 64 custom glyphs of 8 bytes, laid out by
                                   `intro_screencode_layout` ($83AD) as screen codes $20..$5F,
                                   four per row over sixteen rows -> 32 x 128 px, the letters
                                   running down the edge of the screen
  loadB_intro_text.bin    6400 B   THE GLOBE. A str ANIM-OBJECT, byte-for-byte the same format as
                                   the 88 portraits — same header, same `draw_anim_object $064E`,
                                   same `anim_obj_init $0586`. Header `01 08 24 87` where a
                                   portrait carries `01 10 0b 63`: 36 byte-columns by 135 pixel
                                   rows instead of 11 by 99

There is no title TEXT anywhere in this. The thing printed through `api_print_string` is the glyph
grid above — a run of 64 sequential screen codes, not prose.

    ./wl_title_assets.py export <build-out-dir> <png-dir>
    ./wl_title_assets.py import <build-out-dir> <png-dir>     writes the .bin files back
    ./wl_title_assets.py check  <build-out-dir>               export -> import -> byte-identical?

The globe rides on the portrait tooling unchanged — `wl_portrait_encode` takes `dims` as a
parameter everywhere and only DEFAULTS to the portrait box. The logo is its own eight lines because
64 glyphs of 8 bytes is not an anim-object at all.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "editor", "backend"))
import wl_portrait_encode as enc                                      # noqa: E402

LOGO = "loadA_intro_image.bin"
GLOBE = "loadB_intro_text.bin"
LOGO_COLS, LOGO_ROWS = 4, 16                     # from intro_screencode_layout: $20..$5F, 4 per row

# The colour window, same fixed scheme the portraits use (WASTELAND_PORTRAIT_FORMAT): background
# black, screen $26 = red/blue, colour RAM uniform white. No per-picture colour data exists.
PEPTO = [(0, 0, 0), (255, 255, 255), (104, 55, 43), (112, 164, 178), (111, 61, 134), (88, 141, 67),
         (53, 40, 121), (184, 199, 111), (111, 79, 37), (67, 57, 0), (154, 103, 89), (68, 68, 68),
         (108, 108, 108), (154, 210, 132), (108, 94, 181), (149, 149, 149)]
PAIR = {0: 0, 1: 2, 2: 6, 3: 1}                  # bit-pair -> C64 colour  (BG, screen hi/lo, CRAM)
UNPAIR = {v: k for k, v in PAIR.items()}


def dims_of(blob):
    """C1, BF, C2, C0 — every anim-object carries its own box in its first four bytes."""
    return blob[0], blob[1], blob[2], blob[3]


# ── the logo: 64 glyphs, no header, no streams ────────────────────────────────────────────────

def logo_to_png(blob, path, scale=8):
    from PIL import Image
    img = Image.new("RGB", (LOGO_COLS * 8, LOGO_ROWS * 8))
    px = img.load()
    for g in range(LOGO_COLS * LOGO_ROWS):
        gx, gy = (g % LOGO_COLS) * 8, (g // LOGO_COLS) * 8
        for row in range(8):
            byte = blob[g * 8 + row]
            for pp in range(4):
                c = PEPTO[PAIR[(byte >> ((3 - pp) * 2)) & 3]]
                px[gx + pp * 2, gy + row] = c
                px[gx + pp * 2 + 1, gy + row] = c
    img.resize((img.width * scale, img.height * scale), Image.NEAREST).save(path)


def png_to_logo(path):
    from PIL import Image
    img = Image.open(path).convert("RGB")
    w, h = LOGO_COLS * 8, LOGO_ROWS * 8
    if img.size != (w, h):
        img = img.resize((w, h), Image.NEAREST)          # an artist may have worked scaled up
    px = img.load()
    out = bytearray(512)
    for g in range(LOGO_COLS * LOGO_ROWS):
        gx, gy = (g % LOGO_COLS) * 8, (g // LOGO_COLS) * 8
        for row in range(8):
            byte = 0
            for pp in range(4):
                col = _nearest(px[gx + pp * 2, gy + row])
                byte |= (UNPAIR.get(col, 0) & 3) << ((3 - pp) * 2)
            out[g * 8 + row] = byte
    return bytes(out)


def _nearest(rgb):
    """The palette index closest to a pixel — an artist's PNG will not be exactly Pepto."""
    best, bd = 0, None
    for i in (0, 1, 2, 6):                                # only the four the window can show
        d = sum((a - b) ** 2 for a, b in zip(rgb, PEPTO[i]))
        if bd is None or d < bd:
            best, bd = i, d
    return best


# ── the globe: an anim-object, through the portrait tooling ───────────────────────────────────

def globe_to_png(blob, path, scale=5):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import wl_decode_portrait as dec
    bm = dec.decode(blob)
    dec.render_png(bm, path, scale=scale, box=dec.box_of(blob))
    return bm


def png_to_globe(path, template):
    """A PNG back into the anim-object, keeping its animation slots.

    `rebuild_animated` emits the blob fresh with every offset recomputed, so the slot streams and
    their patch streams survive and nothing is orphaned. It needs a full 8192-byte $6000 bitmap;
    only the cells inside the box are read."""
    from PIL import Image
    C1, BF, C2, C0 = dims_of(template)
    img = Image.open(path).convert("RGB")
    w, h = (C2 - C1 + 1) * 8, (C0 // 8 - BF // 8 + 1) * 8
    if img.size != (w, h):
        img = img.resize((w, h), Image.NEAREST)
    px = img.load()
    bm = bytearray(8192)
    for cr in range(BF // 8, C0 // 8 + 1):
        for cc in range(C1, C2 + 1):
            off = cr * 320 + cc * 8
            for line in range(8):
                byte = 0
                for pp in range(4):
                    x, y = ((cc - C1) * 4 + pp) * 2, (cr - BF // 8) * 8 + line
                    byte |= (UNPAIR.get(_nearest(px[x, y]), 0) & 3) << ((3 - pp) * 2)
                bm[off + line] = byte
    return enc.rebuild_animated(template, bytes(bm), dims=(C1, BF, C2, C0))


# ── CLI ───────────────────────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    cmd, out_dir = sys.argv[1], sys.argv[2]
    png_dir = sys.argv[3] if len(sys.argv) > 3 else out_dir
    logo_bin, globe_bin = os.path.join(out_dir, LOGO), os.path.join(out_dir, GLOBE)
    logo_png = os.path.join(png_dir, "title_logo.png")
    globe_png = os.path.join(png_dir, "title_globe.png")

    if cmd in ("export", "check"):
        os.makedirs(png_dir, exist_ok=True)
        logo = open(logo_bin, "rb").read()
        globe = open(globe_bin, "rb").read()
        logo_to_png(logo, logo_png)
        globe_to_png(globe, globe_png)
        print(f"  {LOGO:26} {len(logo):5} B  ->  {logo_png}   "
              f"{LOGO_COLS * 8}x{LOGO_ROWS * 8} px, 64 glyphs")
        C1, BF, C2, C0 = dims_of(globe)
        print(f"  {GLOBE:26} {len(globe):5} B  ->  {globe_png}   "
              f"box {C1}..{C2} cols, rows {BF}..{C0}")

    if cmd == "import":
        logo = png_to_logo(logo_png)
        with open(logo_bin, "wb") as fh:
            fh.write(logo)
        template = open(globe_bin, "rb").read()
        globe = png_to_globe(globe_png, template)
        with open(globe_bin, "wb") as fh:
            fh.write(globe)
        print(f"  wrote {LOGO} ({len(logo)} B) and {GLOBE} ({len(globe)} B)")

    if cmd == "check":
        logo0 = open(logo_bin, "rb").read()
        globe0 = open(globe_bin, "rb").read()
        logo1 = png_to_logo(logo_png)
        globe1 = png_to_globe(globe_png, globe0)
        ok_l = logo1 == logo0
        # The globe is re-EMITTED, not patched: streams are laid out fresh, so the file may be a
        # different length while describing the same picture. The honest test is the PICTURE.
        import wl_decode_portrait as dec
        ok_g = dec.decode(globe1) == dec.decode(globe0)
        print(f"  logo   round trip byte-identical: {ok_l}")
        print(f"  globe  round trip same picture:   {ok_g}   "
              f"({len(globe0)} B -> {len(globe1)} B)")
        return 0 if (ok_l and ok_g) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
