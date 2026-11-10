"""Throwaway: round-trip battery for the JSON-IR serializer (step 2). See wl_ir.ir_to_record."""
import sys, json, glob, os
sys.path.insert(0, os.path.dirname(__file__))
import wl_ir, wl_cell
from wl_machine import RECORD_BASE as RAM   # $3400 on the C64, $D000 on the Apple
from wl_area import load_area

RECS = []
for f in sorted(glob.glob(os.path.join(os.path.dirname(__file__), "../../baseline/side*/s*_record_id*.bin"))):
    b = os.path.basename(f)
    RECS.append((f, int(b[1]), int(b.split("id")[1].split(".")[0])))


def decode_sig(raw, side, area):
    """LAYOUT-INDEPENDENT semantic signature. Uses the engine-faithful DECODERS (which resolve records
    by pointer + honour each op's real length) rather than a fixed raw-byte window that would over-read
    short records into their neighbour. Everything here is offset-invariant, so a correct re-pack must
    reproduce it exactly."""
    a = load_area(raw, side, area); slot = a.slot
    d = {"nibble": list(a.nibble), "cellid": list(a.cellid)}
    # each cell's DECODED record (typed IR = the meaning, no over-read)
    cells = []
    for i, op in enumerate(a.nibble):
        try:
            cells.append((op, a.cellid[i], repr(wl_ir._decode_op_record(raw, slot, op, a.cellid[i]))))
        except Exception:
            cells.append((op, a.cellid[i], "?"))
    d["cells"] = cells
    try:
        d["chains"] = repr(sorted((cid, repr(wl_cell.chain_links(a, cid))) for cid in wl_cell.group_referenced_ids(a, 0xB)))
    except Exception:
        d["chains"] = "?"
    d["foenames"] = "|".join(wl_cell._foe_names(a))
    for nm, fn in (("mut", wl_cell.area_mutations), ("tr", wl_cell.area_transitions)):
        try:
            d[nm] = repr(fn(a))
        except Exception:
            d[nm] = "?"
    try:
        import wl_text5_encode as tenc
        base = (raw[slot] | raw[slot + 1] << 8) - RAM
        d["strings"] = list(tenc.enumerate_strings(raw, base, raw[base:base + 62]))
    except Exception:
        d["strings"] = "?"
    return d


def first_diff(a, b, path=""):
    if type(a) != type(b):
        return f"{path}: type {type(a).__name__} vs {type(b).__name__}"
    if isinstance(a, dict):
        for k in set(a) | set(b):
            if k not in a:
                return f"{path}.{k}: missing in ir0"
            if k not in b:
                return f"{path}.{k}: missing in ir1"
            d = first_diff(a[k], b[k], f"{path}.{k}")
            if d:
                return d
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: len {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_diff(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    return None if a == b else f"{path}: {a!r} vs {b!r}"


pa = psig = pdig = 0
g16_overflow = []
fails_a = []
fails_sig = []
for f, side, area in RECS:
    base = open(f, "rb").read()
    try:
        ir0 = wl_ir.record_to_ir(base, side, area)
        blob = wl_ir.ir_to_record(ir0)
        ir1 = wl_ir.record_to_ir(blob, side, area)
    except Exception as e:
        msg = str(e)
        if "overflow" in msg.lower():
            g16_overflow.append((os.path.basename(f), msg))
        else:
            fails_a.append((os.path.basename(f), f"raise: {msg}"))
        continue
    da = first_diff(ir0, ir1, "ir")
    if da is None:
        pa += 1
    else:
        fails_a.append((os.path.basename(f), da))
    try:
        sig_ok = decode_sig(blob, side, area) == decode_sig(base, side, area)
        dig_ok = wl_cell.area_digest(blob, side, area) == wl_cell.area_digest(base, side, area)
    except Exception as e:
        fails_sig.append((os.path.basename(f), f"raise: {e}"))
        continue
    psig += sig_ok
    pdig += dig_ok
    if not sig_ok:
        s0, s1 = decode_sig(base, side, area), decode_sig(blob, side, area)
        why = next((k for k in s0 if s0.get(k) != s1.get(k)), "?")
        if why == "cells":                          # pinpoint the first differing cell's op
            cd = next((c0 for c0, c1 in zip(s0["cells"], s1["cells"]) if c0 != c1), None)
            why = f"cell op${cd[0]:X}" if cd else "cells"
        fails_sig.append((os.path.basename(f), why))

# ── THE FOE MODEL: the two parallel tables and the combat pointer table are BUILT, not copied ──
# `ir["foes"]` carries a foe as one object — its name SEGMENTS (stem / singular / plural) and its
# 8-byte record — and the serializer emits both tables from that list. Byte-faithfulness is the
# whole point, so it is the acceptance test: re-emit every baseline and demand the foe-name table,
# the foe-record table and desc+$37..+$5B come back identical. A joined display name would fail
# here on Highpool alone (Bobb/y/ies -> "Bobby" -> re-emits "Bobbys").
tab_ok, tab_bad, modelled = 0, [], 0
for f, side, area in RECS:
    raw = open(f, "rb").read()
    try:
        ir = wl_ir.record_to_ir(raw, side, area)
        back = wl_ir.ir_to_record(ir)
    except Exception as e:                                          # noqa: BLE001
        tab_bad.append((os.path.basename(f), f"raise: {e}")); continue
    modelled += bool(ir.get("foes") and all(x.get("name_segs") for x in ir["foes"]))

    def tables(rr, sd, ar):
        a = load_area(rr, sd, ar)
        nb = wl_cell._le(rr, a.slot + 0x02) - RAM
        tb = wl_cell._le(rr, a.slot + 0x04) - RAM
        n = len(wl_cell._foe_names(a))
        return (bytes(rr[nb:wl_cell._names_true_end(rr, nb) + 2]),
                bytes(rr[tb:tb + 8 * (n + 1)]),
                bytes(rr[a.slot + 0x37:a.slot + 0x5C]))
    o, g = tables(raw, side, area), tables(back, side, area)
    if o == g:
        tab_ok += 1
    else:
        tab_bad.append((os.path.basename(f),
                        ",".join(k for k, (x, y) in zip(("names", "records", "combat_msgs"),
                                                        zip(o, g)) if x != y)))

N = len(RECS)
print(f"=== {N} records ===")
print(f"(c) foe tables + desc+$37 re-emitted byte-exact : {tab_ok}/{N}"
      f"   ({modelled} areas emit from the FOE MODEL, the rest keep raw blobs)")
for nm, why in tab_bad[:6]:
    print(f"      {nm}: {why}")
print(f"(a) IR round-trip ir1==ir0     : {pa}/{N}")
print(f"(b) DECODE-SIG equivalence     : {psig}/{N}   <- the semantic oracle")
print(f"    raw area_digest match      : {pdig}/{N}   (layout-sensitive; expected low on a re-pack)")
print(f"grp16-overflow bucket          : {len(g16_overflow)}  {[a for a,_ in g16_overflow][:8]}")
from collections import Counter
print("\n--- (a) IR-round-trip failure modes (first-diff path prefix) ---")
c = Counter(d.split(":")[0].split("[")[0] for _, d in fails_a)
for k, n in c.most_common(8):
    ex = next(d for nm, d in fails_a if d.startswith(k))
    print(f"  {n:3}x {k}   e.g. {ex[:90]}")
print("\n--- (b) DECODE-SIG failure modes ---")
c = Counter(w for _, w in fails_sig)
for k, n in c.most_common(8):
    print(f"  {n:3}x {k}   e.g. {next(nm for nm,w in fails_sig if w==k)}")
