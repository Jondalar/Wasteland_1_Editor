# Game logic — how a Wasteland area scripts itself

The editor is meant to be the knowledge base for the game logic, so this is the map of how areas
**think**: how a cell decides what to do, how conditions and flags gate events, and how one area
changes another. It is grounded in `src/ef_engine.asm` and
`src/overlays/ef_main_overlay.asm` (addresses given so it can be re-checked, not trusted).

Read alongside: [`../../docs/engine/CELL_PATCH.md`](../../docs/engine/CELL_PATCH.md)
(op-$C, verified), [`../../docs/engine/OP4_OBJECTS.md`](../../docs/engine/OP4_OBJECTS.md)
(the tile-override rule), [`../../docs/engine/SERVICE_HANDLERS.md`](../../docs/engine/SERVICE_HANDLERS.md)
(shops/doctors/bank).

## 1. Every cell is (op-nibble, dispatch-id)

Two parallel grids in the area record decide a cell's behaviour (see CELL_PATCH.md §1 for the exact
addresses):

- **op-nibble** — WHAT kind of thing the cell is (0..15).
- **dispatch-id** — WHICH record of that kind (`grp<op>[dispatch-id]`).

The op-nibble indexes `SCENE_OP_LUT` (`$ACF3` in the overlay). The ones that carry logic:

| op | name | handler | what it does |
|---|---|---|---|
| `$1` | SAY | `$BDE9` | print message(s) from grp1 |
| `$2` | COND / TRADE | `$8E14` | **evaluate conditions, then branch/mutate** — the gate (§2) |
| `$3` | ENCOUNTER | — (LUT no-op `$AE0D`) | fight; the victory path leaves a loot sack (CELL_PATCH.md §6) |
| `$4` | OBJECT | draw override | shows `grp4[id]` byte1 as its tile over the map (OP4_OBJECTS.md) |
| `$5` | LOOT | `$9194` | the loot sack (draws tile 5); taking it advances the script |
| `$6` | AREA_EVENT | `$8839` | **run grp16 code, or an area special handler** (§3) |
| `$8` | KEYWORD | `$88E3` | type-a-word prompt (passwords, keyword talk) |
| `$9` | PARTY_FLAG_SWEEP | `$82E0` | **set a per-member flag bit** (§4) |
| `$A` | TRANSITION | `$89A3` | enter another area (or a service door) |
| `$C` | CELL_PATCH | `$8878` | rewrite cells (CELL_PATCH.md) |

## 2. Conditions gate everything — op-$2 COND/TRADE

`SCENE_COND_WALK` (`$8E47`) walks 2-byte `[op][arg]` condition entries from the record at `+$0A`
until `$FF`. This is the game's IF. Entry ops:

The entry ops are corrected + tabulated in §2b (op-0 is a SKILL check, not a give — that was a bug).
Short version: money / party-count / SKILL / item-select / item-possession. Pass → the cell's
success branch fires (which usually MUTATES the cell: op-$2's DONE/ALT pair, CELL_PATCH.md §2);
fail → the fail branch. Quest state is carried as special ITEMS the party keeps (a token, a keyword,
a part) or as a required SKILL.

The Map tab already surfaces these as `source → target` mutations with the trigger text
("have item: Match", "give/buy item: Plastic explosive").

## 2b. Condition kinds — item / skill / attribute (Alex's taxonomy)

Verified from the routines `SCENE_COND_WALK` calls (op = opb>>5):

| op | kind | routine | how |
|---|---|---|---|
| 0 | **SKILL** | `$0359` = `member_skill_lookup` ($1392) | scans the member SKILL table (record `$80..$BB`, id/value pairs) for skill-id == arg. NOT an item — reading the skill id as an item id was the "Perception -> Mangler" bug. |
| 2 | **ATTRIBUTE** | `$0212` | arg = member-record stat OFFSET, checks a rolled attribute ($0E–$14) or MaxCON ($1B). NOT an item select — see §2c. |
| 1/5/6/7 | item possession | `$0344` inventory scan (`$BD..$F8`) | on arg |
| 3 | party count | `cmp $07` | count >= arg |
| 4 | money | `char_money_compare $03D4` | >= arg |

Verified against Highpool: `grp2[4] = 02 09` -> op 0, arg $09 -> **requires skill #9**, and the
Highpool cave needs **Perception**, i.e. skill 9 = Perception. `grp2[8]` -> skill #2 + item select.

**Skill NAME table — LOCATED + decoded byte-exact.** Skill and item names share ONE text5 string
pool in the resident engine blob loaded to `$0200` (char table `$29E4`, offset table `$2A20`, 43
groups). The mapping is by **message id**: **skill-id N -> msg id N** (no bias), **item-id N -> msg
id N + `$24`**. That collision is the whole "Perception -> Mangler" bug: skill 9 was read as item 9,
and item 9's name is msg `$2D` = "Mangler". Decoded with `tools/wl_text5.py` from
`build/out/engine_0200.prg`. Full id->name list (engine capitalisation):

| id | skill | id | skill | id | skill |
|----|-------|----|-------|----|-------|
| 1 | Brawling | 13 | Acrobat | 25 | Medic |
| 2 | Climb | 14 | Gamble | 26 | Safecrack |
| 3 | Clip pistol | 15 | Picklock | 27 | Cryptology |
| 4 | Knife fight | 16 | Silent move | 28 | Metallurgy |
| 5 | Pugilism | 17 | Combat shooting | 29 | Helicopter pilot |
| 6 | Rifle | 18 | Confidence | 30 | Electronics |
| 7 | Swim | 19 | Sleight of hand | 31 | Toaster repair |
| 8 | Knife throw | 20 | Demolitions | 32 | Doctor |
| **9** | **Perception** | 21 | Forgery | 33 | Clone tech |
| 10 | Assault rifle | 22 | Alarm disarm | 34 | Energy weapon |
| 11 | AT weapon | 23 | Bureaucracy | 35 | Cyborg tech |
| 12 | SMG | 24 | Bomb disarm | | |

The editor's `_SKILL_NAMES` (`editor/backend/wl_cell.py`) now carries this verified table.

**Attribute NAMES are in the engine at the characters** (Alex): `stat_label_template` (`$1152`) holds
`ST= IQ= LK= SP= AGL= DEX= CHR= SKP=` and `render_member_paperdoll` prints them for member record
offsets **`$0E..$14`** (the 7 attributes STR/IQ/LK/SP/AGL/DEX/CHR). Skills live next to them in the
record at **`$80..$BB`** (id/value, ids 1..$23).

## 2c. Attribute check LOCATED (cond-op 2) — resolved 2026-07-26

The condition kind that was "STILL OPEN" is **cond-op 2**. Its arg is a member-record stat OFFSET,
not an item id. Across all four sides the op-2 condition args cluster exactly on `$0E–$14`
(Str/IQ/Luck/Speed/Agility/Dexterity/Charisma) + `$1B` MaxCON — the rolled-attribute block, not the
item table. Ground-truth: the Nearly Ultimate Wasteland Guide's Mine-Shaft / Las-Vegas "Luck check
of all characters" is arg `$10` = Luck; Highpool (4,20) is Luck + Agility + Acrobat (a physical-
prowess gate) — which the old decoder mis-showed as "item select: VP91Z 9mm pistol · M17 carbine".
`wl_cell._decode_conditions` now labels op-2 as `attribute check: <name>` via `_attr_name`
($0E–$14, $1B). See [NUWG_RECONCILIATION.md](../../docs/engine/NUWG_RECONCILIATION.md) §A.

## 2d. LOOT records (op-5) — decoder corrections 2026-07-26

- terminator = **$00 OR $FF** (a `$00 $FF` pair used to read as "Fists ×255" then over-run).
- a loot item byte with **bit7 set = a flagged item; its low 7 bits are the REAL item id** (not a
  "random item of class N"). Verified: Citadel loot `$1C/$1D/$22` = Laser rifle×2 / Meson cannon×1 /
  Power pack×12 = the guide's armoury bag exactly. The flow/mutation views now inline bag contents,
  and weapon items carry a damage/ammo/clip/skill tooltip (`_WEAPON_STATS`).

## 3. Events — op-$6 AREA_EVENT and area special handlers

`AREA_EVENT_DISPATCH` (`$8845`), reached from an op-$6 cell:

- **event id, bit7 clear** → `area_record_subptr(Y=$10)` resolves `grp16[id]`, then `WAC69` runs it
  as a script. grp16 = the area's own 6502 code (per area, stored in the record).
- **event id, bit7 set** → `area_change_back $03BF` + `jsr $CA00` = the area's DECOMPRESSED special
  handler (the `$CA00` scene blob — same slot the shop/doc/TREX handlers use).
- A **TIMED_EVENT_CHECK** (top of `$82xx`) can dispatch `A=$84` = the bit7 path, handler `$04`,
  after `load_descriptor_blob $03B9` — i.e. an event that fires on a game-clock condition, not a
  step. (This is the Cochise reactor-timer family; see `wasteland-cochise-endgame`.)

## 4. Party flags — op-$9 PARTY_FLAG_SWEEP

`$82E2`: for each party member it ORs a bit-mask (`W8335[0] = $01`) into the member's flag byte at
**`member+$28`**. So besides inventory items, there is a second flag channel: per-member bits at
+$28. A member with status `$29` is skipped.

## 5. One area changing another — the cross-area flow (the real gap)

CELL_PATCH and the condition/event machinery are all **area-local**: op-$C writes cells in the
CURRENTLY LOADED area, op-$2 reads the party that is present. So how does completing a quest in one
area change a DIFFERENT area? Through the two shared channels above:

- **inventory items** (a token you keep), and
- **party flag bits** (member+$28),

which travel with the party. Area B checks them on entry and mutates itself.

### Worked example — the hidden bases (Sleeper Base, Base Cochise)

This is the case that exposed the gap. In the overworld (Area 0):

- **(2,3)** and **(59,7)** are op-$4 OBJECT cells that render as MOUNTAINS (`grp4[id]` tile), hiding
  the bases. They only become enterable once flipped to op-$A TRANSITION.
- **(0,63)** and **(1,63)** are op-$C CELL_PATCH cells whose records hold exactly that flip:
  (0,63) → patch (2,3)=Cochise + (59,7)=Sleeper, (1,63) → patch (2,3)=Cochise.
- Those two cells are **UNREACHABLE**: an isolated 2-cell pocket in the corner, walled off, their
  only walkable neighbour is each other. You can never step on them. They are **code hooks** — the
  patch is invoked programmatically, not by walking.
- **`grp16[0]` @ `$4FCD`** is a dedicated reveal executor: it reads two cell params (`($5F),3/4`),
  turns them into grid addresses (`$505A`/`$5042`), and self-modifies a copy loop (`$5032`) that
  writes the reveal into the record grids. `$505A` is called from nowhere else — it exists only for
  this.
- The reveal PERSISTS because it lands as ~20 record deltas in Area 0's record, captured by the
  world journal (`wasteland-overworld-reveal-mechanism`).

The **Max quest** (Area 25, Sewers Lv2) is the trigger source: a repair station where you install
the android's parts (strings: "head/torso/arms/legs of an android", "The servomotor slides into
place", "The fusion cell screws into place easily", "Repairs to this unit are complete and
successful"). Completing it sets the shared state (an item token and/or a party flag) that Area 0
then reads to fire the reveal hooks.

**Still open (honest):** the EXACT byte — which item/flag Max sets and where Area 0 gates on it — is
not yet nailed. grp16[0] is engine-dispatched (no `JSR $4FCD` in the record), so the static call
chain runs into the overworld dispatch and needs either more grp16 disassembly or a runtime trace of
the reveal firing (make a `.c64re` dump just before Max completes; observe the writers of the base
cells + the flag bytes).

### What the editor Flow view needs

The Flow view is area-local today (`source → targets` + chains within one area). It cannot show that
a cell here is driven from another area. The general fix is NOT a per-quest handler but **shared
state as first-class cross-area edges**:

- index, across ALL area records: who SETS item/flag X (op-$2 give, op-$9 sweep) and who CHECKS it
  (op-$2 possession, grp16), so a node can show "revealed by the Max quest in Area 25".
- surface code-only hooks like (0,63)/(1,63) that no cell reaches, so their effect is visible even
  though you can't walk there.
- the same shape covers the Sewers Lv2 exit that only opens after Max — a cell that looks inert but
  is a scripted target.

The global record index we already planned is the data source for this.

## Related memories
[[wasteland-area0-transition-slots]] · [[wasteland-cell-patch-format]] ·
[[wasteland-overworld-reveal-mechanism]] · [[wasteland-physics-op-nibble]] ·
[[wasteland-cochise-endgame]] · [[wasteland-scene-script-format]]
