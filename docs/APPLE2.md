# The Apple II release as a second data source

Wasteland was written on the Apple II; the C64 version is the port. The question this document
answers is whether the editor could read the Apple disks too.

**It can, and the work is small.** The data is the same data. Text codec, compressor, directory
format, area records, item table and asset ids are shared; only the graphics are machine-specific.

Findings below came from a format comparison run against five `.dsk` images in October 2026. Each
claim names the evidence it rests on. Two of them were re-checked by hand afterwards and are
marked; the rest are the comparison's own measurements.

---

## The images

Five `.dsk`, 143,360 B each = 35 tracks × 16 sectors × 256 B, DOS 3.3 **logical** order, so
track *t* sector *s* is at `(t*16 + s) * 256`. No de-interleave needed.

`wastboot.dsk` is `wastmst1.dsk` plus boot code — tracks 3 to 33 are byte-identical.

None of the five carries a DOS 3.3 VTOC: track 17 sector 0 on the masters is game data. Custom
loader, exactly like the C64 side. The bytes are fully readable anyway.

---

## 1. The streams run *downwards*

This is the first thing to get right, and getting it wrong yields garbage.

A multi-sector asset continues at the **previous** sector, and underflows to the previous track:
the successor of T05/S00 is T04/S0F. That is the same discipline as the C64's `$FD0B` step-down
(`tools/wl_directory.py`), minus the 1541's zone table and the track-18 skip — 16 sectors per
track, uniformly.

Proof: the engine's string offset table at file offset `0x050C6` is monotonic and stops exactly at
the sector boundary `0x050FF`; the next sector holds 6502 code. A sweep of all 560 sectors found
exactly one continuation, T04/S0F, whose first words continue the sequence.

---

## 2. Same text codec

The character table lives at the same place in the same shape: the 62-byte alphabet, then the
offset table at `base + $3C`, 2-byte little-endian per group of four strings.

```
APPLE  0a 73 00 20 65 61 72 6f 69 74 6e 6c 63 6d 70 64 75 68 6b 0d 66 67 62 77 79 76 31 78 2e 39 ...
C64    0a 73 00 20 65 61 72 6f 69 74 6e 6c 63 6d 70 64 75 68 6b 0d 66 67 62 79 77 76 31 2e 78 39 ...
```

**60 of 62 bytes identical**, with two adjacent transpositions (`y`↔`w`, `.`↔`x`). The first 38 of
43 offset-table entries are byte-identical.

`backend/wl_text5.py` decodes the Apple engine pool **unchanged** — 172 strings, matching the C64.

> **A real defect, found on the way and verified by hand.** The fallback constant `G29E7` in
> `backend/wl_text5.py` starts three bytes too late: it begins at `$29E7` (`20 65 61 72 …`) where
> the table begins at `$29E4` (`0a 73 00 20 65 …`). It is missing the newline, the `s`, and — the
> part that matters — the `0x00` terminator, so nothing would decode with it.
>
> **It is unused.** Nothing in either repository references `G29E7`; every caller passes the table
> read out of the blob. Dead constant, wrong, harmless. Fix it when you touch the file.

---

## 3. Same directory

Track **34** — the last track — holds the per-kind directories: 4-byte entries
`[off][step][len][side]`, with the base track/sector in bytes 254/255. That is the C64 format, and
the sector numbers line up one-to-one with the C64's block indices on track 35:

| C64 T35 blk | Apple | kind |
|---|---|---|
| 9 | T34/S09 | str, high ids (64–127) |
| 10 | T34/S0A | str |
| 12 | T34/S0C | gfx |
| 13 | T34/S0D | map |
| 14 | T34/S0E | record (`[track][blk][len]`, raw) |
| 15 | T33/S0F | areatrack |
| 11 | T34/S0B | C64: charset · **Apple: the depacker** |

The id sets match exactly, for every kind and all four sides, checked against the original C64
directories. Example, map id 8 (Desert Nomads): both say track 21, offset 208, length 2.

---

## 4. Same compression

Track 34 sector 11 carries the depacker. Against the C64's `$C800` codec it is the same routine
relocated — page `$C8` → `$FF`, zero page +3 — and the code table is byte-identical:

```
C64    (off 236..255) 00 01 04 05 06 07 08 09 0a 16 17 18 19 1a 1b 1c 1d 3c 3d 3e
APPLE  (off 234..253) 00 01 04 05 06 07 08 09 0a 16 17 18 19 1a 1b 1c 1d 3c 3d 3e
```

Depacked with an unchanged `decode_until_eoi`: Apple map id 8 → 1135 B, C64 → 1135 B, **byte for
byte identical**. Across all sides, 22 of 29 maps are byte-identical; the rest agree to 84–95 %
(version drift — 1172 B against 1177 B and so on).

---

## 5. Same records, one constant apart

42 records shared across the four sides:

| | |
|---|---|
| map grids identical (both layers, width, height) | **41 / 42** |
| foe name tables identical | **42 / 42** |
| prose strings identical | **2472 / 2478 — 99.8 %** |
| record lengths identical | 40 / 42 |

The one grid exception is side 1 area 0, the overworld, and it differs in **one cell out of 4096**.

Where records differ, the differences start exactly at the end of the grid and land only on odd
bytes — the high bytes of little-endian pointers — with a constant bias:

```
side 1 record 43, descriptor $0600:
  +$00  APPLE $DD0D   C64 $410D    delta $6400
  +$02  APPLE $DB58   C64 $3F58    delta $6400
  …     17 of 17 descriptor words, all delta $6400
```

**The record window sits at `$D000` on the Apple and at `$3400` on the C64.** That is the entire
model difference. The records' own character tables are 62 of 62 bytes identical — only the
engine-global one carries the two transpositions.

Demonstrated: `wl_cell.py` reloaded with `0x3400` → `0xD000`, nothing else changed, run against
untouched Apple records — foe names and prose came back identical on all three records tried.

## Item table

Found at T27/S0C (read descending), 768 B. `wl_item.parse()` unchanged yields 95 descriptors,
**92 byte-identical**. The three differences are all in byte 2 — the field `wl_item.py` itself
documents as unidentified. Prices, class and charges agree.

---

## What it would take

### Extraction

| file | verdict |
|---|---|
| `tools/wl_g64decode.py` | **not needed.** A `.dsk` is already de-GCR'd, 256-byte sectors |
| `tools/wl_directory.py` | **new reader, ~20 lines.** The zone table, interleave, `C930_START` and the track-18 skip are 1541 things and fall away. The entry semantics stay |
| `tools/wl_decompress.py` | **unchanged** |
| `tools/wl_extract_side.py` | `decode_until_eoi` **unchanged**; replace `sec` / `step_down` / `build_stream` |
| `tools/wl_extract_baseline.py` | same model, same directory indices, same kinds — only the sector addressing |

The complete Apple sector reader written during the analysis is **38 lines**
(`a2extract.py`, parked outside the repo — see below).

### Backend

| module | verdict |
|---|---|
| `wl_text5.py`, `wl_area.py`, `wl_item.py` | **unchanged**, each verified against Apple data |
| `wl_cell.py`, `wl_foe.py`, `wl_grp16.py`, `app.py` | **a constant, not a model.** The literal `0x3400` (~25 places) has to become a parameter — `$D000` for Apple |
| `wl_text5_encode.py` | same, one place (`base = LE16(raw[slot+0]) - 0x3400`) |
| `wl_tiles.py`, the portrait modules | **does not carry over.** C64 multicolour bitmap against Apple hi-res is a genuinely different format model |

---

## Open

**The write path is unproven.** Only reading was demonstrated. On save the editor relocates the
rebuilt text region to the **end** of the blob and repoints `raw[slot+0]`. With a load base of
`$D000`, how far the Apple record window extends upwards decides whether appending at the end is
even legal there — and that lives in the Apple engine code, which was not disassembled.

**Graphics were deliberately out of scope.** The gfx blobs (tilesets) and str blobs
(portraits, scenes) are present and keyed under the same ids, and comparable in size — gfx
3129–3456 B against the C64's 3456 B. The str container looks structurally parallel: byte 0 `$01`,
byte 3 `$63`, then a monotonic LE16 offset table. Beyond that shape, **not verified**. The pixel
payload naturally differs (9–37 % byte agreement).

> Since settled. `backend/wl_tiles_a2.py` decodes the tileset and `backend/wl_portrait_a2.py` the
> str container, both read out of the Apple engine's own draw routine rather than guessed; the
> off-directory title and endgame pictures are in the next section.

---

## The title screen and the endgame pictures

Neither machine keeps these in a directory: the engine loads them by hardcoded track and block.
On the Apple they are found, extracted and previewed — `tools/wl_extract_title_endgame.py` holds
the table and the provenance of every number in it. In short:

| what | where | codec budget | decodes to | box | anim slots |
|---|---|---|---|---|---|
| globe | master 1 `T04/S06` | 22 | 6687 B | 252×127 | 4 |
| Cochise dome | master 4 `T03/S0F` | 15 | 4560 B | 84×83 | 1 |
| Cochise dome, 2nd animation | master 4 `T02/S0C` | 12 | 3554 B | 84×83 | 2 |
| death silhouette | master 4 `T02/S00` | 16 | 5162 B | 84×83 | 1 |
| all-dead finale | master 4 `T01/S00` | 17 | 5163 B | 252×127 | 4 |

The two domes share a byte-identical base stream and differ only in what animates over it, which
is why the Apple has five pictures where the C64 has four.

All five are anim-objects in the portrait container, so `wl_portrait_a2` renders them unchanged.
Each was matched picture-for-picture against the C64 blob it corresponds to.

The globe's parameters are read straight out of the Apple title module (kept at `T34/S08..S0F`
on the crack disk, running at `$6000`): `$61FB` sets track 4 / sector 6, `$6206` sets the codec
budget `$16`, `$61EC` the destination `$6C00` and `$61F6` the stop page `$88`. The codec's `$CE`
budget and `$CD` stop page are `$D1` / `$D0` here — the same +3 zero-page shift the depacker
carries.

**The C64's `logo` has no Apple counterpart.** That title screen draws its text with the engine's
ordinary renderer; the module loads exactly one picture.

**Still open: the engine's load table for the four endgame blobs.** Their budgets are measured —
bounded below by the object's true extent and above by where the next blob starts, which pins
three of the four to a single value — but the code that issues those loads was not found. The
resident (`$0800-$36FF` plus `$D000-$D3FF` / `$F100-$F2FF` / `$F700-$FEFF`) has exactly one place
that writes the budget, `$2CD4`, and it serves the directory-driven path at `$2CC5`. A search of
all five images for every other write to `$D1`, for every call to the depacker wrapper `$0A82`
and its hi-res-hole stub `$407C`, and for every call to the raw sector loader `$2D4C` turned up
nothing outside that path and the title module.

### Engine map, for whoever picks this up

Read off the title module's five boot loads at `$60C1`:

| from | to |
|---|---|
| master 1 `T29/S04` ↓4 | `$D000-$D3FF` (language card bank 1) |
| master 1 `T07/S09` ↓2 | `$F100-$F2FF` |
| master 1 `T07/S07` ↓47 | `$0800-$36FF` — the resident |
| master 1 `T04/S08` ↓2 | `$3E00-$3FFF`, then copied 8 bytes at a time into the hi-res page-2 **screen holes** at `$4078 + k*$80`, k = 0..63 — 512 bytes of `jmp`s, the engine's jump table, hidden in the bytes the display never fetches. Two entries per hole (`+0`, `+3`); `$407C` is the depacker |
| master 1 `T01/S0F` ↓8 | `$F700-$FEFF` |

Every stream descends, so `address = (idx(T09/S0F) - idx(t,s)) * 256 - 0x2000` for the big one:
`T07/S0F` → `$0000`, `T04/S0F` → `$3000`.

Useful entry points in the resident: `$2D4C` load *n* sectors descending into `$FF`… (`$0803` is
its public thunk), `$2DFC` load an overlay (`$0809`), `$2E4E` step down (`$0806`), `$2CC5` depack
a directory entry, `$0A82` call the depacker at `$FF00`, `$0ABF` show a str picture, `$0AE8` the
anim-object draw setup. `$0E`/`$0F` are the stream cursor (track, sector), `$D1` the codec budget,
`$D0` the stop page, `$BC`/`$BD` the blob pointer.

Overlays are chained with `ldx #track / stx $0E / ldx #sector / stx $0F / jmp $0809`; a module's
first sector begins `4C <entry> <load addr> <pages>`. There are five, all loading at `$6000`:
`T08/S09` utilities, `T28/S08` character creation, `T29/S00`, `T33/S0D` the main game module, and
`T03/S00` the one-sector death screen ("Grim Reaper / Your life has ended in The Wasteland…",
which shows str id 59). The Cochise ending most likely lives in `T33/S0D`; it has not been read.

---

## Working material

The disk images, the analysis scripts and their output are parked **outside this repository**, at
`../_wl1_apple2/` — disks, scripts, extracted. They must never be committed here;
`.gitignore` and `tools/check_clean.py` both know the Apple container extensions now.
