#!/usr/bin/env python3
"""
Map Wasteland portraits (str ids) -> the foe/NPC names that use them.

Derived from the engine, not guessed (see ef_main_overlay W9EA4 / ef_engine
string_index_to_ptr $1B05, and trace live_mrd8cspq):

    W9EA4:  lda ($61),y  y=4/5  -> $45/$46   ; $61/$62 = area DESCRIPTOR base
            ldx #$08 / stx $4C               ; record stride = 8
            jsr string_index_to_ptr          ; $16/$17 = base + index*8
            jsr W9EDE  (ldy #$07 / lda ($16),y)   ; A = PORTRAIT (str) id

  => descriptor +$04  = WORD ptr to the FOE TABLE (8-byte records)
     descriptor +$02  = WORD ptr to the FOE NAME table
     foe record byte7 = portrait / str id
     name↔portrait is POSITIONAL: the name table and the record table are PARALLEL
     arrays of equal length, so the k-th name pairs with the k-th record's byte7.
     (byte3 is NOT the name index — that was the old bug; it mislabeled shared portraits.)

Foe-name table: entries separated by $00; inside an entry the name ends at the
first $0A (the $0A-separated tails are the singular/plural suffixes). $FF ends it.

Verified anchor: side 2, area 32 (Leroy's, at Needles), descriptor slot $0600,
foe table $41F3, index 6 -> byte7 = $4E = str 78 -> name "Woman". That is the
portrait the game shows for the woman-with-assault-rifle encounter. (Her talkable
NPC name, "CHRISTINA", lives in a different table and is not the foe name.)
"""
import glob, os, sys
from collections import defaultdict

ROOT = os.environ.get('C64RE_PROJECT_DIR') or os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

# Deliberately NOT backend/wl_machine's RECORD_BASE. This script reads the parent project's
# own baseline layout under C64RE_PROJECT_DIR, not an editor data root, so it must not follow
# WL_DATA to an Apple root and then subtract $D000 from C64 pointers. C64 only.
RAM = 0x3400

def strids(side):
    return {int(f.split('_str_id')[1].split('.')[0])
            for f in glob.glob(f'{ROOT}/baseline/side{side}/s{side}_str_id*.bin')}

def foe_names(seg):
    """Entry = base $0A sing-infix $0A plural-infix $0A common-suffix.
    singular = base + sing-infix + common-suffix, plural = base + plural-infix + suffix:
      "Gunm|a|e|n"   -> Gunman  / Gunmen
      "Bobb|y|ies|"  -> Bobby   / Bobbies
      "Jerk||s|"     -> Jerk    / Jerks
    """
    out = []
    for chunk in seg.split(b'\x00'):
        if not chunk:
            continue
        p = chunk.split(b'\x0a')
        nm = p[0] + (p[1] if len(p) > 1 else b'') + (p[3] if len(p) > 3 else b'')
        if nm and nm[0] != 0xFF:
            out.append(nm.decode('latin1'))
    return out

def area_foes(path, side):
    """-> (slot, [(index, pic, name)]) using descriptor +$02 / +$04."""
    b = open(path, 'rb').read()
    S = strids(side)
    for slot in (0x0600, 0x1800):
        if slot + 8 > len(b):
            continue
        nm = (b[slot + 2] | b[slot + 3] << 8) - RAM
        fo = (b[slot + 4] | b[slot + 5] << 8) - RAM
        if not (0 <= nm < fo < len(b) - 8):
            continue
        names = foe_names(b[nm:fo])
        recs = []
        k = 1
        # POSITIONAL pairing: the name table and the record table are PARALLEL arrays of equal
        # length — the k-th name pairs with the k-th record's portrait (byte7). byte3 is NOT the
        # name index (the old bug: byte3 mislabeled shared portraits, e.g. Gila Monitor->a man).
        # Verified in-game (Nomads: Gila Monitor->str54 lizard, Dire Coyote->str10 dog) and the
        # Leroy's anchor (record 6 -> byte7 str78 -> name #6 = "Woman").
        # SKIP (not break) records whose byte7 isn't a known portrait id / byte1!=0: a single
        # unlinkable record early in a long table (side4 Cochise) must not truncate the rest. k stays
        # aligned to names (positional pairing) and is bounded by len(names), so no garbage is added.
        while fo + k * 8 + 8 <= len(b) and k <= len(names):
            r = b[fo + k * 8: fo + k * 8 + 8]
            if r[7] in S and r[1] == 0:
                recs.append((k, r[7], names[k - 1]))
            k += 1
        if recs:
            return slot, recs
    return None, []

def main():
    pic = defaultdict(lambda: {'names': set(), 'areas': set()})
    blobs = 0
    for f in sorted(glob.glob(f'{ROOT}/baseline/side*/s*_record_id*.bin')):
        side = int(f.split('/side')[1][0])
        area = int(f.split('_record_id')[1].split('.')[0])
        slot, recs = area_foes(f, side)
        if not recs:
            continue
        blobs += 1
        for _, p, nm in recs:
            pic[(side, p)]['areas'].add(area)
            if nm:
                pic[(side, p)]['names'].add(nm)
    return pic, blobs

if __name__ == '__main__':
    pic, blobs = main()
    print(f'{blobs} area records with a foe table; {len(pic)} (side,str) pairs')
    for k in sorted(pic):
        d = pic[k]
        print(f"  side{k[0]} str{k[1]:<3} names={sorted(d['names'])} areas={sorted(d['areas'])}")
