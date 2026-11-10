"""Which machine a data root came from, and the one number that depends on it.

Wasteland shipped on the Apple II first and was ported to the C64. The data is the same data
— text codec, compressor, directory, area records, item table, asset ids all shared — and a
format comparison across four sides found exactly one model difference (docs/APPLE2.md §5):

    the record window sits at $3400 on the C64 and at $D000 on the Apple.

Every pointer inside an area record is an absolute address in that window, so every conversion
between a record offset and the pointer that names it goes through this constant. It used to be
the literal 0x3400 in 76 places.

One machine per data root, and so one per server run. The root says which it is: bootstrap.py
writes a MACHINE file next to baseline/, and WL_DATA picks the root. That way the number cannot
be got wrong by starting the server with the wrong flag — it travels with the data it describes.
A root without the marker is a C64 root, which is what every root written before this existed is.

    python3 bootstrap.py --disks  …/g64s            -> data/      MACHINE: c64
    python3 bootstrap.py --apple --disks …/dsks      -> data_a2/   MACHINE: apple
    WL_DATA=data_a2 bash run.sh

WL_MACHINE overrides the marker, for a root assembled by hand.
"""
from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent

# WL_DATA moves the whole root (a Docker volume, a second game version, a scratch copy).
DATA = Path(os.environ.get("WL_DATA", HERE.parent / "data")).resolve()

RECORD_BASES = {"c64": 0x3400, "apple": 0xD000}


def _read_machine() -> str:
    m = os.environ.get("WL_MACHINE")
    if not m:
        try:
            m = (DATA / "MACHINE").read_text().strip()
        except OSError:
            m = "c64"                      # no marker: a C64 root, like every root before this
    m = m.lower()
    if m not in RECORD_BASES:
        raise SystemExit(f"unknown machine {m!r} — expected one of {sorted(RECORD_BASES)}.\n"
                         f"  (from WL_MACHINE, or {DATA / 'MACHINE'})")
    return m


MACHINE = _read_machine()
RECORD_BASE = RECORD_BASES[MACHINE]

# The second machine constant, and one docs/APPLE2.md's comparison did not have: the zero-page
# slot holding the pointer to the area descriptor, which every grp16 event handler reads and
# writes through (`lda (zp),y` / `sta (zp),y`). The comparison noticed the Apple depacker sits
# three zero-page bytes higher than the C64's; the same +3 holds here.
#
#   C64    s1 area 43 handler $3E2A:  a0 2f  b1 61  38 e9 02 …
#   APPLE  s1 area 43 handler $DA2A:  a0 2f  b1 64  38 e9 02 …
#
# Measured, not inferred from the depacker: with $64 the Apple's handlers decode to exactly the
# same effects as the C64's on every shared record that has any.
ZP_DESC = {"c64": 0x61, "apple": 0x64}[MACHINE]

# True where the editor's byte-level model has only ever been proven on the C64.
IS_C64 = MACHINE == "c64"
