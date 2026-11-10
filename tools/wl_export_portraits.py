#!/usr/bin/env python3
"""
Export ALL 88 Wasteland portraits (str anim-objects) as C64 multicolour graphics.

Per portrait -> portraits/all/<sN_idII>.{kla,png}:
  .kla  standard Koala multicolour bitmap: $6000 load addr + 8000 bitmap + 1000 screen
        + 1000 colour + 1 background. Drop-in for Koala-aware C64 tools / demos.
  .png  2:1-correct preview render.
Plus a contact sheet portraits/all/_contact.png and an index portraits/all/INDEX.md.

Colours are the game's fixed picture-window scheme (verified in-game, see
wl_decode_portrait): MC bitmap bank1, bitmap $6000, matrix $5C00; box screen $26
(01=red/10=blue), colour RAM $01 (white), background $00 (black).
Decode is byte-exact vs the engine (validated 0-diff vs sandbox on 7 portraits).
"""
import glob, os, sys
import wl_decode_portrait as W
from PIL import Image

ROOT = os.environ.get('C64RE_PROJECT_DIR') or os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
OUT = os.path.join(ROOT, 'portraits', 'all')
os.makedirs(OUT, exist_ok=True)

NAMES = {(2, 78): 'Christina'}   # known character names (extend as identified)

def screen_matrix():
    """1000-byte video matrix: $26 inside the portrait box, $6F elsewhere (init fill)."""
    c0, c1, r0, r1 = W.BOX
    scr = bytearray([0x6F]) * 1000
    for r in range(r0, r1):
        for c in range(c0, c1):
            scr[r * 40 + c] = 0x26
    return bytes(scr)

SCREEN = screen_matrix()
COLOR = bytes([0x01]) * 1000
BG = 0

def thumb(bm, cell=4):
    c0, c1, r0, r1 = W.BOX
    NW, NH = (c1 - c0) * 4 * 2, (r1 - r0) * 8
    img = Image.new('RGB', (NW, NH)); px = img.load()
    for r in range(r0, r1):
        for c in range(c0, c1):
            off = r * 320 + c * 8
            for l in range(8):
                byte = bm[off + l]
                for pp in range(4):
                    bits = (byte >> ((3 - pp) * 2)) & 3
                    col = {0: W.BG, 1: W.SCREEN_HI, 2: W.SCREEN_LO, 3: W.CRAM}[bits]
                    x = ((c - c0) * 4 + pp) * 2; y = (r - r0) * 8 + l
                    px[x, y] = W.PEPTO[col]; px[x + 1, y] = W.PEPTO[col]
    return img.resize((NW * cell, NH * cell), Image.NEAREST), (NW, NH)

def main():
    # WORK OVER BASELINE, the same resolution the editor uses. Reading only baseline is why the
    # gallery went stale: it is generated from the pristine extraction while the live data is the
    # edited copy, so every portrait Till repainted kept showing its 1988 version here.
    blobs = {}
    for d in ('baseline', 'editor/work'):                # later wins
        for f in sorted(glob.glob(f'{ROOT}/{d}/side*/s*_str_id*.bin')):
            blobs[os.path.basename(f)] = f
    blobs = [blobs[k] for k in sorted(blobs)]
    rows = []
    thumbs = []
    for f in blobs:
        base = os.path.basename(f)                      # s2_str_id78.bin
        side = int(base[1]); iid = int(base.split('id')[1].split('.')[0])
        name = NAMES.get((side, iid), '')
        stem = f's{side}_id{iid:02d}'
        blob = open(f, 'rb').read()
        bm = W.decode(blob)
        # .kla
        kla = bytes([0x00, 0x60]) + bytes(bm[:8000]) + SCREEN + COLOR + bytes([BG])
        open(f'{OUT}/{stem}.kla', 'wb').write(kla)
        # .png
        img, native = thumb(bm)
        img.save(f'{OUT}/{stem}.png')
        thumbs.append((stem, name, img))
        rows.append((stem, name, len(blob)))
    # contact sheet
    cols = 8
    tw, th = thumbs[0][2].size
    pad, label = 8, 14
    cw, ch = tw + pad, th + pad + label
    n = len(thumbs); r = (n + cols - 1) // cols
    sheet = Image.new('RGB', (cols * cw, r * ch), (20, 20, 20))
    from PIL import ImageDraw
    dr = ImageDraw.Draw(sheet)
    for i, (stem, name, img) in enumerate(thumbs):
        x = (i % cols) * cw + pad // 2; y = (i // cols) * ch + pad // 2
        sheet.paste(img, (x, y))
        dr.text((x, y + th + 1), f'{stem} {name}'[:22], fill=(200, 200, 200))
    sheet.save(f'{OUT}/_contact.png')
    # index
    with open(f'{OUT}/INDEX.md', 'w') as fp:
        fp.write('# Wasteland portraits — all 88 (str anim-objects)\n\n')
        fp.write('C64 multicolour graphics ripped from Wasteland (EA/Interplay 1988). '
                 'Byte-exact vs the engine decoder. Colours fixed: bg black, screen $26 '
                 '(red/blue), colour RAM $01 (white). `.kla` = standard Koala MC bitmap.\n\n')
        fp.write('| file | side | str id | name | blob bytes |\n|---|---|---|---|---|\n')
        for stem, name, sz in rows:
            s = stem[1]; i = stem.split('id')[1]
            fp.write(f'| {stem}.kla / .png | {s} | {int(i)} | {name} | {sz} |\n')
    print(f'exported {len(blobs)} portraits -> {OUT}')
    print('contact sheet: _contact.png   index: INDEX.md')

if __name__ == '__main__':
    main()
