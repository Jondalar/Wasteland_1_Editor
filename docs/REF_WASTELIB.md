# wastelib — dieselben Daten, anderes Ziel

**Was:** <https://kayahr.github.io/wastelib/> · Quelle <https://github.com/kayahr/wastelib>
TypeScript-Bibliothek von Klaus Reimer, MIT, die die **PC/DOS-Fassung** von Wasteland liest.
**Warum hier:** eine unabhängige Aufarbeitung derselben Spieldaten für eine andere Plattform.
Wo sie und wir dasselbe finden, ist das eine Bestätigung aus zweiter Quelle. Wo wir abweichen,
lohnt ein zweiter Blick.

Für [`META_MODEL.md`](META_MODEL.md) ist es mehr als eine Fundstelle: es ist der Beleg, dass
*„ein Spiel, mehrere Ziele"* keine Behauptung ist. Dieselben Areas, dieselben Aktionsklassen,
dieselben Prüfungen — in zwei Binärformaten, die nichts gemeinsam haben.

---

## 1. Was sie liest (DOS)

| Datei | Inhalt |
|---|---|
| `GAME1` / `GAME2` | **Kartendaten**, Spielstände, Ladenlisten |
| `ALLHTDS1/2` | Kachelsätze |
| `ALLPICS1/2` | Portrait-Animationen |
| `IC0_9.WLF`, `MASKS.WLF` | Sprites |
| `COLORF.FNT` | Farbzeichensatz |
| `CURS` | Mauszeiger |
| `TITLE.PIC` | Titelbild |
| `END.CPA` | Endanimation |
| `WL.EXE` | Strings und Offsets |
| `paragraphs.bin` | die Paragraphen (aus dem Buch, per OCR) |

Kodierungen: **Huffman** und **vertikales XOR** (`src/main/io/huffman.ts`, `vxor.ts`, `xor.ts`).

Der C64 hat davon nichts: unsere Daten liegen in Spuren/Sektoren, die Prosa in einem 5-Bit-Codec,
die Bilder als Multicolour-Bitmap-Objekte. **Gleicher Inhalt, komplett andere Hülle.**

---

## 2. Die Aktionsklassen — ihr Vokabular gegen unseres

`src/main/game/ActionClass.ts`, sie verweisen auf `wasteland.gamepedia.com/Map_Tile_Action_Classes`:

| Nr | wastelib (DOS) | wir (C64, `wl_cell.OP_NAMES` / `wl_ir._OP_NAME`) | |
|---:|---|---|---|
| 0 | `NONE` | default / terrain | ✓ |
| 1 | `PRINT` | SAY | ✓ gleiche Sache, anderer Name |
| 2 | `CHECK` | COND | ✓ |
| 3 | `FIXED_ENCOUNTER` | ENCOUNTER | ✓ — ihr Name sagt mehr: *fixed*, im Gegensatz zu 15 |
| 4 | `MASK` | OBJ | **Namensunterschied mit Inhalt**, s. u. |
| 5 | `LOOT` | LOOT | ✓ |
| 6 | `SPECIAL` | AREA_EVENT / SERVICE + EVENT | ✓ — beide Fassungen falten Laden **und** Sonderaktion in eine Klasse |
| 7 | `UNKNOWN$07` | npc-data | **beide unsicher** |
| 8 | `DIALOG` | KEYWORD | ✓ ihr Name ist besser: es ist der Antwortdialog, nicht nur ein Schlüsselwort |
| 9 | `RAD` | HAZARD | ✓ — **Radioaktivität**; unser `msg + dice` ist der Strahlungsschaden |
| 10 | `TRANSITION` | TRANSITION | ✓ |
| 11 | `BLOCKED` | BUMP | ✓ „impassable square" — unser Auslöser *dagegenlaufen* ist genau das |
| 12 | `ALTER` | CELL_PATCH | ✓ |
| 13, 14 | `UNKNOWN` | default | ✓ beide leer |
| 15 | `RANDOM_ENCOUNTER` | npc-data / encounter (grp15) | ✓ Kategorie bestätigt — **wir kennen den Mechanismus genauer**, s. §3 |

### Zwei Stellen, an denen ihr Name mehr sagt

**4 — `MASK`.** Wir nennen es OBJ und beschreiben es als „etwas, das auf einem Feld liegt". Ihre
Beschreibung ist enger und interessanter:

> *„Used to mask other tiles with alternate tile graphic. Used to 'swap' tiles without using the
> ALTER action class. Used for hidden bases, 'exploding' tiles, smashed doors, etc…"*

Also nicht Zierrat, sondern ein **Zweitzustand einer Kachel ohne Records zu verändern** — eine
versteckte Basis, eine gesprengte Tür. Das erklärt, warum 129 von 235 unserer OBJ-Records gar keine
Nachricht tragen (gemessen in P1): sie sind Optikwechsel, keine Objekte.

**15 — `RANDOM_ENCOUNTER`.** Bei uns heißt grp15 „npc-data / encounter", der Zweifel steht im
Namen. Ihre Kategorie stimmt — und **unsere eigene Doku ist genauer als ihre**:
[`RANDOM_ENCOUNTERS.md`](../../docs/engine/RANDOM_ENCOUNTERS.md) sagt, grp15 ist *„not a
list of encounters: it is the **free list**"*, aus dem umherziehende Gruppen einen Platz bekommen,
und `desc+$32` ist seine Größe. Der Zweifel im Namen ist also nur nicht nachgetragen worden.

---

## 3. Nachgeprüft, eins nach dem anderen

### `CheckType` — fünf von fünf, gleiche Nummern

```
wastelib CheckType         wl_cell.COND_KINDS
0 SKILL                    0 skill check
1 ITEM                     1 have item
2 ATTRIBUTE                2 attribute check
3 MEMBERS                  3 party count ≥
4 MONEY                    4 money ≥
```

Zwei unabhängige Reverse-Engineerings, dieselben fünf Arten in derselben Reihenfolge. Unser
`opb >> 5` aus `SCENE_COND_WALK $8E49` ist damit aus zweiter Quelle bestätigt.

### `MobType` — die einzige Stelle, an der sie uns korrigiert haben

*Korrektur (2026-08-08): hier stand, Byte 6 sei „der Foe-Byte, den wir nie benennen konnten". Das
war falsch — `wl_foe.FOE_KINDS` hat alle fünf benannt, seit das Foe-Panel existiert. Der Befund ist
ein anderer und ein besserer: **drei dieser Namen waren falsch geraten**, und wastelib hat recht.*

`src/main/game/MobType.ts`:

```
1 ANIMAL · 2 MUTANT · 3 HUMANOID · 4 CYBORG · 5 ROBOT
```

**Gegen unsere Daten gehalten** — 400 Foe-Records über alle 43 Areas, Byte 6 nimmt genau diese
fünf Werte an und keinen anderen:

```
3 HUMANOID  220      5 ROBOT   82      1 ANIMAL  59
4 CYBORG     32      2 MUTANT   7
```

**Wer in den Slots steht, entscheidet den Streit** — und zwar gegen unsere alten Namen:

| Slot | hieß bei uns | Bewohner (gemessen) | Urteil |
|---:|---|---|---|
| 1 | animal | Bunny, Rat, Coydog, Dire Coyote, Gila Monitor | gleich |
| 2 | **undead** | Desert Dweller, Shambler Ghoul, Lost Soul, Pit Ghoul | Ghouls sind hier **Mutanten**, „undead" war Genre-Raten |
| 3 | human | Gunman, Guard, Jerk, Cutthroat, Marksman | gleich |
| 4 | **robot** | Clawman, Tronodile, Cyclon, Centron, Cyborg, Hexborg | Fleisch **und** Maschine = **CYBORG** |
| 5 | **turret** | Silicon Sniper, Steel Reaver, VTOL Auto-fire Robot, Xenon Laser Cannon | 82 Records, überwiegend beweglich = **ROBOT** |

Übernommen (`wl_foe.FOE_KINDS`, `FOE_RECORD.md`, Foe-Panel). Das Tile bleibt gemessen statt
geliehen — es ist eine *Folge* des Kinds, kein eigenes Feld. Nebenbei fiel dabei die zweite,
hartcodierte Kind-Tabelle in `main.js` weg: `/api/foe-templates` liefert `kinds` seit jeher mit, der
Fetch hat sie nur weggeworfen — genau deshalb stand dort noch „undead/robot/turret".

Damit ist `WESEN.Art` im Meta-Modell nicht mehr nur *feindlich/friedlich/ortsfest/anheuerbar*,
sondern hat eine zweite, orthogonale Achse: **woraus das Wesen besteht**. Das ist im Spiel eine
Kampfeigenschaft (was gegen einen Roboter wirkt, wirkt nicht gegen ein Tier).

### `ItemType` — das „Lochmuster", das keins war

`wl_item.py` hält Byte 3 des Item-Deskriptors als *class* und sagt im Kommentar, warum es dabei
blieb:

> *„Named for what the group IS, **not for a guess at the nibble's encoding** — 0 through 9 with a
> hole pattern that is not a plain class index."*

`ItemType.ts` löst das auf. **Byte 3 = `ItemType << 3 | Flag`** — die Löcher sind die Typen, für
die WL1 kein Item hat. Über die 95 Items der `areatrack`-Tabelle:

```
 1 MELEE_WEAPON    Fists · Ax · Club · Chainsaw · Knife · Proton ax
 2 SHORT_RANGE     M1911A1 45 pistol · Spear · Throwing knife · VP91Z 9mm
 3 MEDIUM_RANGE    M17 carbine
 4 LONG_RANGE      M19 rifle · Red Ryder
 6 MEDIUM_AUTO     Mac 17 SMG · Uzi SMG Mark 27
 7 LONG_AUTO       AK 97 · M1989A1 Nato assault rifle
 8 MED_ROCKET      Sabot rocket                       ← Flag 1
 9 LONG_ROCKET     LAW rocket · RPG-7                 ← Flag 1
10 SHORT_ENERGY    Laser pistol
11 MED_ENERGY      Laser carbine
12 LONG_ENERGY     Laser rifle · Meson cannon
13 EXPLOSIVE       Plastic explosive · TNT · Mangler   ← Flag 1
14 AMMO            45 clip · 7.62mm clip · 9mm clip · Howitzer shell · Power pack
15 ARMOR           Bullet proof shirt · Kevlar vest · Leather jacket
16 STANDARD        Canteen · Crowbar · Engine · Gas mask · Geiger counter
17 SPECIAL         Fruit · Jewelry
18 QUEST           Antitoxin · Finster's head · Blackstar key · Bloodstaff
```

**94 von 95 Items fallen in 1..18**, und jede Gruppe ist inhaltlich einheitlich. Der eine Ausreißer
ist `$FB` (Typ 31, Flag 3) = Geld, ein Sondermarker.

Zwei Nebenbefunde:

- **Typ 5 (`SHORT_RANGE_AUTO`) kommt in WL1 nicht vor** — das ist eines der „Löcher".
- **Das Flag im untersten Bit trägt genau die Sprengstoffe**: Typ 8, 9 und 13, also beide Raketen
  und der Explosivstoff. Nichts sonst hat es gesetzt. Was es *tut*, ist damit nicht bewiesen —
  aber die Auswahl ist zu sauber, um Zufall zu sein.

*Zur Zählung:* Slot in der Tabelle **ist** die Item-id. Mit `id+1` sieht jede Gruppe aus, als hätte
sie einen Ausreißer am Ende (Mac 17 SMG bei den Einzelschusswaffen, 45 clip bei den Energiewaffen);
mit `id` ist jede Gruppe rein. Das ist die schärfste Bestätigung der Tabellenbasis `$3108 + id*8`,
die wir haben.

### `MASK` (op 4) — bestätigt, und zwar deutlich

Ihre Beschreibung sagt „Zweitzustand einer Kachel". Der Test dafür: **worauf zielt CELL_PATCH?**
Über alle 43 Areas, ~1900 Patch-Einträge:

```
op-$4  OBJ / MASK        404   ← häufigstes Ziel überhaupt
op-$1  SAY               351
op-$2  COND              288
op-$0  terrain           213
op-$3  ENCOUNTER         185
…
```

**Op-4 ist das, worin ein Patch eine Zelle verwandelt.** Versteckte Basis, gesprengte Tür,
explodierende Kachel — genau ihre Beschreibung. Dazu passt die andere Messung: 129 der 235
op-4-Records tragen **gar keine Nachricht**, sind also reiner Optikwechsel; 106 tragen Nachricht
*und* Kachel.

`OBJEKT` im Meta-Modell (§2.9) ist damit zu eng benannt. Es ist kein Fass auf dem Boden, es ist der
**zweite Zustand eines Feldes**.

### op 7 und op 15 sind nie eine Zelle

Gemessen über 56 320 Zellen: **op-$7 kommt 0-mal vor, op-$F kommt 0-mal vor.** Beide werden nur
*angezeigt*, nie betreten — was zu wastelibs Formulierung passt („Pointer to … header"). Bei op-15
sagt unsere eigene Doku warum: es ist die Freiliste, nicht die Karte.

### `FIXED_ENCOUNTER` gegen `RANDOM_ENCOUNTER` — die Daten trennen sie sauber

```
grp3   594 Records   dist_a/dist_b echt:  (30,15) · (40,40) · (120,120) …   0/0 kommt NIE vor
grp15  124 Records   dist_a/dist_b = 0/0  in ALLEN 124
```

Eine feste Begegnung hat eine Nähe-Schwelle, weil sie an einem Ort liegt und den Spieler kommen
sieht. Ein Freilisten-Platz hat keine, weil er zur Laufzeit gefüllt wird. Die beiden Gruppen sind
strukturell verschieden, genau wie ihre Namen es behaupten.

### `Gender` und `Nationality` — schon richtig

`0 = männlich / 1 = weiblich` und `0 US · 1 RU · 2 MEX · 3 IND · 4 CHI` stehen wortgleich in
[`NPC_SPAWN.md`](../../docs/engine/NPC_SPAWN.md) (+$18, +$19). Keine neue Erkenntnis, aber
eine unabhängige Bestätigung von zwei Bytes, die wir aus der Zeichenerschaffung abgeleitet hatten.

---

## 4. Ihr Entitätsvokabular gegen §2 des Meta-Modells

`src/main/game/` — die Dateinamen sind ihre Entitäten:

| wastelib | [`META_MODEL.md`](META_MODEL.md) §2 |
|---|---|
| `GameMap`, `MapInfo` | ORT |
| `MapTile` | FELD |
| `Action`, `Actions`, `ActionClass`, `actions/*` | SZENE |
| `Check`, `CheckType` | SZENE.BEDINGUNG |
| `Item`, `ItemType` | DING |
| `Mob`, `MobType` | WESEN (feindlich) |
| `Character`, `SkillLevel`, `Gender`, `Nationality` | WESEN (anheuerbar) · FERTIGKEIT |
| `ShopItem`, `ShopItemList` | DIENST.Angebot |
| `PartyGroup`, `Savegame` | GRUPPE |
| `Font` | SCHRIFT |
| `Tilesets` | KACHELSATZ |
| `Portraits` | BILDNIS |
| `readParagraphs` | TEXT |

**Deckungsgleich, bis auf drei Stellen** — und alle drei sagen etwas:

- Sie haben **kein** QUEST. Richtig so: das Spiel schreibt keine Quest auf
  ([`QUEST_DESIGNER_CONCEPT.md`](QUEST_DESIGNER_CONCEPT.md) §2.4). Unsere Questebene ist eine
  Autorenschicht, keine Spieldatenschicht — ein unabhängiger Leser derselben Daten findet sie nicht,
  weil sie nicht da ist.
- Sie haben **kein** WORT. Aus demselben Grund: ein Keyword steht im Kopf des Spielers.
  `DIALOG` (op 8) ist bei ihnen eine Aktion, kein Vokabular.
- Sie haben `Gender` und `Nationality` als eigene Aufzählungen. Bei uns sind das zwei Bytes im
  Member-Record (+$18, +$19) — inhaltlich identisch, s. §3.

---

## 5. Grenzen dieser Quelle

**Es ist eine andere Engine.** Die Hülle ist nachweislich verschieden — Huffman und vertikales XOR
gegen unseren 5-Bit-Codec und BB2, `GAME1/GAME2` gegen vier Diskettenseiten, Strings aus `WL.EXE`
gegen Prosa im Record. Wo die Hülle so weit auseinanderliegt, kann die Logik es auch. wastelib
belegt für unsere Bytes deshalb **nichts**. Sie liefert Hypothesen und Wörter.

Danach ist jeder Fund oben zu sortieren — **was steht auf unseren Messungen, was auf ihrem Wort:**

| Fund | trägt sich selbst | von dort geborgt |
|---|---|---|
| **MASK** (op 4) | **alles.** 404 CELL_PATCH-Ziele, 129/235 ohne Nachricht — beides an C64-Bytes gemessen | nur die *Frage*, ob es ein Zweitzustand ist |
| **fixed vs. random** | **alles.** 594 mit echter Distanz gegen 124 mit 0/0 | nur die Vermutung, dass die zwei Gruppen sich unterscheiden |
| **op 7 / op 15 nie eine Zelle** | **alles.** 0 von 56 320 | nichts |
| **`ItemType << 3`** | **fast alles.** Die Zerlegung und die Reinheit der Gruppen sind gemessen; dass *Ax, Chainsaw, Knife* eine Gruppe bilden, ist wahr, wie immer man sie nennt | die 18 **Namen** |
| **`CheckType`** | unsere fünf Arten stammen aus `SCENE_COND_WALK $8E49`, unabhängig | die Übereinstimmung ist ein *Indiz für gemeinsames Design*, kein Beweis für eine der beiden Seiten |
| **`MobType`** | die **Struktur**: 400 Records, Byte 6 ∈ {1,2,3,4,5}, nichts sonst | die **Namen**. Hier trauen wir ihnen — hieße Typ 4 im C64 etwas anderes als *Cyborg*, wären die Bytes identisch und wir merkten es nicht |
| `Gender`, `Nationality` | stand schon in `NPC_SPAWN.md` | nichts |

**Die eine Stelle, an der wir auf Treu und Glauben stehen, ist die Benennung von `MobType`.** Alles
andere hält auch dann, wenn die DOS-Engine an dieser Stelle anders war.
- **Kein Byte-Layout in der Doku.** Die API-Seiten nennen Klassen und Felder, nicht Offsets. Wer
  Layouts braucht, liest den Quelltext (`src/main/game/*.ts`) — der ist MIT und lesbar.
- **Sie kennt op 7 auch nicht.** An der Stelle sind wir gleich weit.

---

## 6. Wo man nachschaut

```
src/main/game/ActionClass.ts     die 16 Aktionsklassen mit Beschreibung
src/main/game/CheckType.ts       die fünf Prüfungsarten
src/main/game/MobType.ts         die fünf Wesensarten
src/main/game/actions/           BlockedAction · CheckAction · EncounterAction
                                 MaskAction · PrintAction · TransitionAction
src/main/game/MapInfo.ts         der Area-Deskriptor
src/main/game/Character.ts       der Member-Record (unser HIREABLE)
src/main/io/                     huffman · vxor · xor · BinaryReader
```

Dazu die Wiki-Seite, auf die sie sich beruft: `wasteland.gamepedia.com/Map_Tile_Action_Classes`.
