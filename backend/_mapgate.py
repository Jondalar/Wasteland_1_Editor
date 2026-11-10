#!/usr/bin/env python3
"""_mapgate.py — the map blob as a document, and back.

    map.bin ──map_to_ir──► IR ──ir_to_map──► map.bin      must be byte-identical, 43/43

The record has had a document since P1.5; the map layer never did. It was safe anyway, because
nothing ever REGENERATED a map: `save_map_tileids` patches the first width*height bytes and leaves
the rest alone, so the trailing bytes were only ever copied. The moment a document becomes the
source, that safety-by-accident ends — hence this gate.

    ./_mapgate.py               check all 43
    ./_mapgate.py -v            plus per-area numbers
    ./_mapgate.py --strip S A   write backend/_experiments/sS_map_idAA.STRIPPED.bin (tail removed)

`--strip` is the experiment: emit a map with the drawn grid only and nothing after it. The loader
bounds the decompressor at $EE00 and the draw pass reads width*height, so by the code the tail
should be inert — this is how we find out whether the code is right.
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wl_area                                                        # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, "..", "..", "baseline")


def areas():
    for f in sorted(glob.glob(os.path.join(BASE, "side*", "s*_map_id*.bin"))):
        b = os.path.basename(f)
        side, aid = int(b[1]), int(b.split("id")[1].split(".")[0])
        rec = f.replace("_map_", "_record_")
        if os.path.isfile(rec):
            yield f, b, side, aid, rec


def dims(rec_path, side, aid):
    a = wl_area.load_area(open(rec_path, "rb").read(), side, aid)
    return a.width, a.height


def main():
    if "--strip" in sys.argv:
        i = sys.argv.index("--strip")
        return strip(int(sys.argv[i + 1]), int(sys.argv[i + 2]))
    verbose = "-v" in sys.argv
    bad, tails, short = [], {}, []

    for path, name, side, aid, rec in areas():
        raw = open(path, "rb").read()
        w, h = dims(rec, side, aid)
        n = w * h
        if len(raw) < n:
            short.append(f"{name} {len(raw)} B for a {w}x{h} grid ({n} B)")
            continue
        ir = wl_area.map_to_ir(raw, w, h)
        try:
            out = wl_area.ir_to_map(ir)
        except ValueError as e:
            bad.append((name, str(e)[:60]))
            continue
        if out != raw:
            bad.append((name, f"{len(raw)} B in, {len(out)} B out"))
        t = len(raw) - n
        tails[name] = t
        if verbose:
            print(f"  {name:24} {w:3}x{h:<3} = {n:5} gezeichnet   Datei {len(raw):5}   Rest {t:5}")

    print(f"\nareas                 {len(tails)}")
    print(f"ohne Rest             {sum(1 for t in tails.values() if t == 0)}"
          f"  (davon 64x64: die Grenze $EE00 kappt bei 4096)")
    print(f"Rest gesamt           {sum(tails.values())} B"
          f"   min {min(tails.values())}  max {max(tails.values())}")
    if short:
        print(f"KURZ (Datei < Gitter)  {len(short)}" + "".join(f"\n  {s}" for s in short))
    print(f"\nbyte-identisch: {'ALLE' if not bad else f'NEIN — {len(bad)} fehlerhaft'}")
    for n, why in bad[:10]:
        print(f"  {n:24} {why}")
    if bad or short:
        print("\nFAIL", file=sys.stderr)
        return 1
    print("\nPASS")
    return 0


def strip(side, aid):
    """Write the map without its tail, next to the original. Nothing is overwritten."""
    src = os.path.join(BASE, f"side{side}", f"s{side}_map_id{aid:02d}.bin")
    rec = src.replace("_map_", "_record_")
    raw = open(src, "rb").read()
    w, h = dims(rec, side, aid)
    ir = wl_area.map_to_ir(raw, w, h)
    tail = (len(ir["tail"]) - 4) // 2
    ir["tail"] = "hex:"
    out = wl_area.ir_to_map(ir)
    # NOT next to the original: the cart build globs baseline/side*/s*_map_id*.bin and would see
    # a second "map id10" -> "WARN: collision side1 vs side1 -> skip", silently invalidating the
    # experiment. Learned the hard way, 2026-08-08.
    out_dir = os.path.join(HERE, "_experiments")
    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, f"s{side}_map_id{aid:02d}.STRIPPED.bin")
    with open(dst, "wb") as fh:
        fh.write(out)
    with open(dst.replace(".bin", ".json"), "w") as fh:
        json.dump(ir, fh, indent=1, sort_keys=True)
    print(f"s{side}a{aid:02d}  {len(raw)} B → {len(out)} B   ({tail} B Rest entfernt, {w}x{h} bleibt)")
    print(f"  {os.path.relpath(dst, os.path.join(HERE, '..', '..'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
