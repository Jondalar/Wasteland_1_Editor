# Das Meta-Modell

**Status:** Entwurf, 2026-08-07. Nichts davon ist gebaut.
**Zweck:** eine Persistenz, die *das Spiel* hält — nicht die Bytes eines bestimmten Spiels. Aus ihr
werden die WL1-Payloads kompiliert; dass sie kompilierbar sind, ist eine Eigenschaft des **Ziels**,
nicht des Modells.
**Grundlage:** gemessen an den 43 ausgelieferten Areas, 4885 Records, 305 912 Byte. Jede Zahl in
diesem Papier ist gezählt, nicht geschätzt.
**Weg dahin:** [`META_MODEL_PLAN.md`](META_MODEL_PLAN.md) — vier Stufen, jede mit einem
automatischen Tor.
**Zweite Quelle:** [`REF_WASTELIB.md`](REF_WASTELIB.md) — eine unabhängige Aufarbeitung derselben
Spieldaten für die DOS-Fassung. Sie bestätigt die fünf Prüfungsarten (5/5, gleiche Nummern) und
benennt den Foe-Byte, den wir nie benennen konnten.
**Verwandt:** [`QUEST_DESIGNER_CONCEPT.md`](QUEST_DESIGNER_CONCEPT.md) ·
[`QUEST_DESIGNER_SPEC.md`](QUEST_DESIGNER_SPEC.md) ·
[`../../docs/engine/`](../../docs/engine/) (wo die Begriffe herkommen).

---

## 0. Der Unterschied zum JSON-IR

Das IR ist **nah dran und doch etwas anderes**. Was es kann: Felder haben Namen statt Offsets,
Prosa ist entschlüsselter Text statt 5-Bit-Bytes, es ist diffbar und von Hand editierbar.

Was es nicht ist: eine id *ist* dort eine Tabellenposition, ein Verweis lautet `(Gruppe, id)`, und
es gilt je Area. Verschiebt sich ein Record, ändert sich jeder Verweis darauf.

> **Das IR hat die Byte-Offsets abstrahiert, nicht die Adressierung.**

Der Test, der die beiden trennt:

> Kann dasselbe Modell ein anderes Binärlayout erzeugen, ohne sich zu ändern?

Beide Schichten sind nötig, sie zeigen nur in verschiedene Richtungen:

```
Meta-Modell    was das Spiel IST            ← Autor arbeitet hier
     │  Compiler — ein Ziel ist eine Engine samt ihrer Grenzen
     ▼
   JSON-IR     was die Bytes SIND           ← Zwischenstufe, prüfbar
     │  Emitter — wl_ir.ir_to_record
     ▼
    .bin       Build-Artefakt
```

---

## 1. Die Regeln

1. **Identität statt Nummer.** Ein Ding, ein Ort, eine Person hat einen dauerhaften Namen. Nummern
   vergibt der Compiler.
2. **Keine Zielgrenzen im Modell.** „Höchstens 127 Texte", „96 Kacheln", „9728 Byte" sind
   Eigenschaften der WL1-Engine. Steht so eine Zahl im Modell, ist das Modell wieder WL1-förmig.
3. **Was nicht modellierbar ist, bleibt opak — und wird gezählt** (§4). Die Größe dieser Luke misst,
   wie meta das Modell wirklich ist.
4. **Alles Abgeleitete wird abgeleitet.** Kanten werden berechnet, nie eingegeben. Eine gespeicherte
   Kante kann von den Daten abweichen; eine berechnete nicht.
5. **Ein Compiler verweigert, statt zu kürzen.** Stilles Abschneiden ist der Fehler, den dieses
   Projekt am häufigsten bezahlt hat.

---

## 2. Die Entitäten

### 2.0 Zwei Sorten: gefunden und eingeführt

Nicht jede Entität hier steht in den Spieldaten. Manche sind **im Spiel vorhanden** und werden beim
Import gefüllt; andere haben wir **eingeführt**, weil sie zum Autorisieren fehlen. Beide gehören ins
Modell, aber sie verhalten sich verschieden — und wer das verwechselt, sucht beim Import nach etwas,
das es nie gab.

```
GEFUNDEN     ORT · FELD · DING · WESEN · TEXT · SZENE · ZWEITZUSTAND · ÜBERGANG
             DIENST · GRUPPE · BILDNIS · KACHELSATZ · SCHRIFT · FERTIGKEIT · EIGENSCHAFT
             → P3 füllt sie aus den Bytes. Ihre Probe ist die Rundreise.

EINGEFÜHRT   QUEST (Achse, Logzeilen, Beats) · MARKER · WORT als Entität
             → nichts zu importieren. Sie beginnen leer und werden geschrieben.
                Ihre Probe ist, dass sie zu etwas kompilieren, das die Engine ausführt.
```

**Der Befund dahinter ist belegt, nicht angenommen.** wastelib hat weder eine Quest- noch eine
Wort-Entität ([`REF_WASTELIB.md`](REF_WASTELIB.md) §4) — eine unabhängige Aufarbeitung derselben
Spieldaten für eine andere Plattform findet unsere Autorenschicht nicht, weil sie nicht in den
Bytes ist. Wasteland führt kein Questbuch; ein Keyword steht im Kopf des Spielers.

Das ist kein Mangel des Spiels, sondern der Grund für dieses Modell: **WL1 hat die Daten, WL2
braucht die Schicht darüber.** Ein De-Make ist deshalb nicht „WL2 in WL1s Modell pressen", sondern
WL1s Datenmodell **plus** die Autorenschicht, die WL2 verlangt — und dieselbe Schicht trägt danach
eine dritte, eigene Geschichte, ohne dass am Datenteil etwas anders wird.

### 2.1 WELT

```
id · Name · Ziele[]            welche Engines diese Welt rendern können
```

### 2.2 ORT — *place*

Ein begehbarer Raum: eine Stadt, ein Bunkergeschoss, die Oberwelt.

```
id · Name · Art            overworld | siedlung | innenraum | dungeon
Grundriss                  Raster aus FELDERN, beliebig groß
Kachelsatz →               welche Optik
Begegnungen                Rate, Nähe-Schwelle, Auftauchen-Schwelle, welche WESEN
Rückkehr                   wo die Gruppe steht, wenn sie von außen kommt
Kampfsprache →             TEXTE, die dieser Ort für Kämpfe braucht
```

*„Kampfsprache" ist eine eigene Beziehung, weil ein Ort ohne sie keinen Kampf zeigen kann — in WL1
ist das der 34-String-Block, und ihn zu vergessen war ein Live-Fehler.*

### 2.3 FELD — *spot*

Eine Position. Trägt drei Dinge, die **unabhängig** sind:

```
Optik      welche Kachel man sieht          (auch: eine ÜBERLAGERUNG, s. OBJEKT)
Physik     begehbar | blockierend | Wasser | Tür | …
Inhalt     nichts, oder genau eine SZENE
```

Dass WL1 Optik und Physik in einem Nibble faltet, ist Zielsache.

### 2.4 DING — *thing*

```
id · Name (Einzahl, Mehrzahl)
Art       18 Typen: Nahkampf · kurz/mittel/weit · Salve · Rakete · Energie · Sprengstoff
          · Munition · Rüstung · Standard · Besonderes · Questgegenstand
Wirkung                                 Waffenwerte, was es öffnet, was es heilt
Preis                                   was ein Laden dafür nimmt
Vorrat                                  unbegrenzt | zählbar (n) | einmalig
verbraucht sich                         ja | nein
```

*Die 18 Typen sind gemessen, nicht erfunden:* Byte 3 des Deskriptors ist `Typ << 3 | Flag`, 94 der
95 Items fallen in 1..18, jede Gruppe ist inhaltlich rein. Die Namen stammen aus wastelibs
`ItemType`, die Prüfung aus unseren Daten ([`REF_WASTELIB.md`](REF_WASTELIB.md) §3). Das Flag im
untersten Bit trägt genau die Sprengstoffe.

*Wo das in WL1 liegt (nachgelesen):* **nicht** im Engine-Binary, sondern im `areatrack`-Blob —
`set_item_descriptor_ptr` ($0BCD) setzt `$45/$46` als Low/High eines **Zeigers**, nicht als
Track/Sektor: `$3108 + id*8`. 768 B = 8-Byte-Kopf + **95 Items à 8 Byte**. Belegt sind Preis
(LE16), Klasse und Ladungszahl; Byte 2 und 5-7 sind teilweise offen (`wl_item.py`).

**BELEG** ist eine eigene Art: ein Ding, das nur bezeugt, dass etwas geschehen ist. Es soll nie
verbraucht, verkauft oder fallengelassen werden. In WL1 ist das ein Item mit Zählerstand 0 — die
Absicht steht dort nirgends, und genau deshalb läuft man in die Verbrauchsfalle.

### 2.5 WESEN — *creature*

```
id · Name (Einzahl, Mehrzahl)          zwei Wörter, keine Endungsregel
Werte        Zähigkeit · Angriff (Würfel) · Reichweite · Rüstung · Erfahrungswert
Gestalt      welche Glyphe im Kampf
Gesicht →    BILDNIS
Art          feindlich | friedlich | ortsfest | anheuerbar
Stoff        Tier | Mutant | Mensch | Cyborg | Roboter        ← orthogonal zur Art
Sprache      Begrüßung → TEXT · Seiten → TEXTE · hört auf → WÖRTER
Habe         DINGE, die es trägt
Können       Fertigkeiten, Eigenschaften        (für anheuerbare)
```

*„Stoff" ist gemessen, die Namen sind geborgt:* Byte 6 des Foe-Records wurde als *„KIND, nicht Tile"* gemessen,
ohne dass wir die Arten benennen konnten. wastelib nennt sie (`MobType` 1-5), und über 400
Foe-Records nimmt Byte 6 genau diese fünf Werte an — 220 Mensch, 82 Roboter, 59 Tier, 32 Cyborg,
7 Mutant ([`REF_WASTELIB.md`](REF_WASTELIB.md) §3). Im Kampf entscheidet es mit, was wirkt.
Dass Typ 4 *Cyborg* heißt, steht allerdings auf dem Wort der DOS-Fassung — die fünf Werte sind
unsere Messung, die fünf Wörter nicht (§5 dort).

Einzahl und Mehrzahl sind **zwei Wörter**. Wie WL1 daraus einen Stamm mit Umschaltstelle macht
(`Madm|a|e|n` → Madman/Madmen), ist Zielsache — und genau die Umkodierung, die einmal einen Namen
um einen Buchstaben verkürzt hat.

### 2.6 WORT — *word*

```
id · das Wort · was es aufschließt
```

Ein Wort ist **kein Zustand des Spielstands** — es steht im Kopf des Spielers. Das Modell hält es
trotzdem, weil sonst niemand sagen kann, wo es gelernt und wo es gebraucht wird.

### 2.7 TEXT — *prose*

```
id · Sprecher (optional) · der Text · Anlass
```

Keine Länge im Modell.

### 2.8 SZENE — *scene*

Was an einem Feld geschieht.

```
AUSLÖSER   drauftreten · ansprechen · dagegenlaufen · Wort sagen · Ort betreten
           · nach dem Kampf · gerufen
BEDINGUNG  trägt DING · kennt WORT · Fertigkeit(Stufe) · Eigenschaft(Stufe)
           · Gruppengröße · Geld · Queststand              — frei kombinierbar: UND · ODER · NICHT
WIRKUNG    sagen · geben · nehmen · kämpfen · schaden(Würfel) · Feld verändern
           · woandershin · handeln · Erfahrung · Geld · heilen
           · Queststand vorrücken · WORT lehren · Code rufen
FOLGE      welche SZENE danach — oder keine
SCHEITERN  eigene Wirkung, wenn die Bedingung nicht hält
```

**„dagegenlaufen"** ist ein eigener Auslöser, kein Sonderfall von „drauftreten": 336 Records im
ausgelieferten Spiel benutzen ihn (die Wand, die etwas sagt, wenn man dagegen rennt).

Bedingungen sind **frei kombinierbar**. Dass WL1 nur eine Liste kennt, die an der ersten erfüllten
abzweigt, ist Zielsache.

### 2.9 ZWEITZUSTAND — *mask*

Der **andere Anblick eines Feldes**: dieselbe Stelle, ein anderes Bild — und optional etwas zu
lesen. Eine versteckte Basis, bevor und nachdem man sie findet; eine Tür, bevor und nachdem sie
gesprengt wird.

```
id · Aussehen (Kachel) · fest ja|nein · Nachricht (optional) · Benutzung → SZENE
```

*Hieß im ersten Entwurf OBJEKT — „ein Fass, ein Schild, eine Leiche". Zu eng.* wastelib nennt es
`MASK` (*„used for hidden bases, 'exploding' tiles, smashed doors"*), und die Messung gibt ihnen
recht: op-4 ist mit **404 Treffern das häufigste Ziel eines CELL_PATCH überhaupt**, und 129 der 235
Records tragen gar keine Nachricht — reiner Optikwechsel ([`REF_WASTELIB.md`](REF_WASTELIB.md) §3).

### 2.10 ÜBERGANG — *link*

```
von ORT/FELD  →  nach ORT/FELD
Richtung      einseitig | beidseitig
sichtbar      sofort | wenn Bedingung
Beschriftung  wie er auf der Karte heißt
Nachspiel     was direkt danach geschieht (z. B. ein DIENST)
```

### 2.11 DIENST — *service*

```
id · Art (Laden | Arzt | Bibliothek | Redner | Bank) · Name über der Tür
Gesicht → BILDNIS · Begrüßung → TEXT · Seiten → TEXTE
Angebot   welche DINGE, zu welchem Preis, welche Leistungen
```

### 2.12 QUEST

Aus [`QUEST_DESIGNER_CONCEPT.md`](QUEST_DESIGNER_CONCEPT.md), hier als Entität:

```
id · Titel · Heimatort → ORT · Hauptlinie | Nebenlinie
Zustandsachse   open → s1 … sN → done, failed daneben — Länge frei
Logzeile        je Zustand, optional
Beats           SZENEN, die einen Zustand vorrücken
Marker          DINGE oder WÖRTER, die diese Quest als ihre benutzt
```

Die Achse ist im Modell **nicht monoton erzwungen**. Monotonie ist eine Eigenschaft des WL1-Ziels
(Flash schreibt nur `1→0`). Ein Ziel mit RAM-Flags könnte rückwärts — und damit fiele „Ruf, der
sinkt" aus der Liste der Unmöglichkeiten.

### 2.13 FERTIGKEIT und EIGENSCHAFT — *skill, attribute*

Beides wird von `SZENE.BEDINGUNG` und `WESEN.Können` referenziert, war aber nirgends deklariert —
und damit war WL1s Liste fest verdrahtet. Sie sind **nicht dasselbe**:

```
FERTIGKEIT    id · Name · Kosten (was eine Stufe bei der Erschaffung kostet)
EIGENSCHAFT   id · Name · wie sie ausgewürfelt wird
```

*Nachgelesen:* eine **Fertigkeit ist Daten** — `ef_char_ranger_overlay.asm` trägt bei `$8519` eine
Offset-Tabelle und ab `$8525` eine 5-Bit-gepackte Namensbank (Alphabet `" eatroc\ry l\0nhusiwpd?…"`,
dieselbe Codec-Familie wie der Prosatext), dazu bei `$8352` die **Kostentabelle** und bei `$84C7`
die Rechnung Kosten × Stufe. 35 Stück. Der Editor zeigt die Namen bis heute aus dem *Guide*, nicht
aus dem Spiel — die 5-Bit-Bank ist nie dekodiert worden.

Eine **Eigenschaft ist Struktur** — sieben feste Felder im Member-Record (`+$0E..+$14`, ST IQ LK SP
AGL DEX CHR), gewürfelt von `CR_STAT_ROLL`. Keine Tabelle, kein Name in den Daten. Im Modell ist
sie trotzdem eine Entität; dass WL1 genau sieben davon fest verbaut hat, steht in §6.1.

### 2.14 GRUPPE — *party*

```
Startaufstellung   welche WESEN, mit welcher Habe
Anheuerbare        wer unterwegs dazukommen kann
```

### 2.15 BILDNIS · KACHELSATZ · SCHRIFT · KLANG

Optik und Ton mit Identität. Ein Bildnis ist ein Gesicht, kein Blob an Adresse X. Ein Kachelsatz
ist eine Sammlung **benannter** Kacheln, keine Tabelle fester Länge.

```
BILDNIS      id · Name · Bild · Bewegung (Regungen)
KACHELSATZ   id · Name · Kacheln[] (jede mit Namen und Rolle: Boden | Wand | Wasser | Zierrat)
SCHRIFT      id · Name · Glyphen[] (jede mit ihrem Zeichen)
```

*Nachgelesen:* WL1 hat **eine** Schrift — 256 B, also 32 Glyphen, resident bei `$C800`, geladen als
T35/blk11. Sie ergänzt den ROM-Zeichensatz um Kleinbuchstaben, und sie ist in allen vier Seiten
**byte-identisch** mit dem Boot-Font (`LOADER.md`: *„boot font's `$C800` page is byte-identical to
disk idx11 charset"*). Vier Blobs, ein Inhalt.

### 2.16 OPAK — *escape hatch*

```
id · Art · die Bytes · woher · was es liest/schreibt
```

Code statt Inhalt: 6502-Routinen, vorgebaute Overlays. **§4 zählt, wie viel das ist.**

---

## 3. Deckung — jeder Record des Spiels findet ein Zuhause

Gezählt über alle 43 Areas. Links, was die Engine hat; rechts, was es im Modell wird:

| Record | n | wird zu |
|---|---:|---|
| SAY | 917 | SZENE, Wirkung *sagen* (ein Lauf von TEXTEN) |
| COND | 719 | SZENE mit BEDINGUNG, zwei Wirkungen (Erfolg/Scheitern) und einer Wirkung auf die GRUPPE |
| ENCOUNTER | 718 | SZENE, Wirkung *kämpfen* — Slots → WESEN, Flags → deren Art, victory → FOLGE |
| TRANSITION | 623 | ÜBERGANG |
| LOOT | 480 | SZENE, Wirkung *geben* |
| PATCH | 429 | SZENE, Wirkung *Feld verändern* (mehrere auf einmal) |
| BUMP | 336 | SZENE, Auslöser *dagegenlaufen* |
| OBJ | 235 | ZWEITZUSTAND (§2.9) |
| KEYWORD | 180 | SZENE, Auslöser *Wort sagen* — je Wort ein Zweig |
| EVENT | 179 | **OPAK** — ruft area-eigenen Code, Argumente nur diesem bekannt |
| SERVICE | 36 | DIENST |
| HAZARD | 18 | SZENE, Wirkung *schaden(Würfel)* |
| HIREABLE | 15 | WESEN, Art *anheuerbar* |
| **4885** | | |

Dazu, außerhalb der Recordblobs:

```
45 map        → ORT.Grundriss        107 str       → BILDNISSE
11 gfx        → KACHELSÄTZE           35 desc      → ORT
 8 areatrack  → DINGE (95 à 8 B) und DIENST.Angebot — derselbe Blob trägt beides
 4 charset    → SCHRIFT (ein Inhalt, viermal)        save → GRUPPE
89 Paragraphen → TEXTE               area_names.json → ORT.Name
17 scene · 32 .prg · grp16 → OPAK
```

Zwei liegen **nicht** in `baseline/`, sondern im Code: die **Fertigkeiten** stehen im
Charakter-Overlay (`$8519` Offsets, `$8525` 5-Bit-Namen, `$8352` Kosten), die **Eigenschaften** gar
nicht — sie sind sieben feste Felder des Member-Records. Wer sie importiert, liest Assembler, kein
Blob.

**Kein Record bleibt ohne Entität.** Das Vokabular aus §2 ist über den Bestand vollständig — was
nicht heißt, dass jedes Feld darin benannt ist (§4).

---

## 4. Die Fluchtluke, gezählt

Regel 3 verlangt eine Zahl. Sie lautet — und sie lautet **zweimal**, weil die Fluchtluke zwei
getrennte Räume hat:

```
Recordbytes gesamt                        305 912

(a) IN den Gruppen-Records, hex:-Felder     7 617   = 2,5 %     ← die Tabelle unten
(b) AUSSERHALB, ganze Blobs                21 907   = 7,2 %

    foe_names_raw     6 162 B     grp16.code        3 851 B     foe_records_raw  3 610 B
    foes[].record     3 200 B     text_table        2 580 B     descriptor.tail  2 064 B
    foe_table_header    320 B     foe_table_extra      90 B     foe_names_term      30 B

    zusammen unbenannt                     29 524   = 9,7 %
```

Die Tabelle für (a), Stand vor P1:

```
HIREABLE.rest      3 525 B    15 Records    Member-Record — Feldkarte steht in NPC_SPAWN.md
EVENT.record         929 B   179 Records    Argumente für area-eigenen Code
TRANSITION.tail      774 B   623 Records    Folge-Op-Paar
LOOT.term            480 B   480 Records    ein Abschlussbyte je Record
OBJ.data             470 B   235 Records    Kachel + Flags, 2 B
SAY.msg_bytes        447 B   141 Records    der String-Lauf
SERVICE.tail/fields/hdr 584 B               Layout-Familien
KEYWORD.hdr          360 B   180 Records    2 B — NICHT konstant $FFFF: 58 der 180 tragen dort
                                            eine echte Kante (die CANCEL-Kante, gemessen)
```

**90,3 % des Spiels sind benannt** — nicht 97,5 %. Diese Zahl stand hier, weil sie nur (a) zählte
und trotzdem durch die Gesamtbytes geteilt wurde: Teilmenge im Zähler, Ganzes im Nenner. `_hexgate.py`
hatte denselben Zuschnitt und beide bestätigten einander, was keine Bestätigung ist. Das Tor druckt
jetzt beide Räume und ihre Summe; der Fortschrittsbalken ratscht weiterhin nur über (a), weil der
eingefrorene Snapshot über (a) genommen wurde — (b) zu ratschen heißt einen neuen Snapshot nehmen,
und das ist eine Entscheidung, kein Bugfix.

Der größte Brocken in (a) war *unbenannt*, nicht *unbekannt*: `HIREABLE.rest` ist der Member-Record,
dessen Feldkarte vollständig dokumentiert ist — sie wurde nur nie verdrahtet. Echt opak ist allein
`EVENT.record`: Argumente, die nur der grp16-Code dieses Ortes deuten kann. In (b) ist der Anteil
umgekehrt: `grp16.code` sind 6502-Routinen (§2.15 verspricht sie hier zu zählen — hier sind sie),
`foes[].record` und `foe_records_raw` decken dieselben Bytes doppelt ab.

Beide Tabellen sind der Fortschrittsbalken des Modells. Sie sollen schrumpfen.

---

## 5. Was ein Ziel tun muss

```
1  Nummern vergeben    Identitäten → ids der Zielwelt
2  Kodieren            Text in das Textformat, Bilder in das Bildformat
3  Packen              Records anordnen, Zeiger setzen, Bänke füllen
4  Verweigern          was nicht passt, mit Angabe was und warum
```

Schritt 4 ist der wichtigste. Ein Ziel, das kürzt, produziert ein Spiel, das anders ist als das
Modell — und dann lügt das Modell.

---

## 6. Das Ziel WL1

### 6.1 Grenzen, die es dem Modell auferlegt

| Modell | WL1 |
|---|---|
| Ort beliebig groß | Grundriss fest je Ort; Recordblob ≤ 9728 B |
| Text beliebig lang | 5-Bit-Codec, Zeichentabelle je Record, ~60 Zeichen praktisch |
| Texte beliebig viele | **7-Bit-id → höchstens 127 je Ort referenzierbar**; 11 Orte mussten das umgehen |
| Kachelsatz beliebig | 96 Kacheln, globaler Vorrat |
| Optik ≠ Physik | dasselbe Nibble trägt beides |
| Bedingungen UND/ODER/NICHT | Liste, zweigt an der ersten erfüllten ab |
| Ding zählbar | „trägt DING" **verbraucht**, wenn der Zählerstand > 0 ist |
| Name Einzahl/Mehrzahl | ein Stamm mit Umschaltstelle |
| Wesen frei bewertbar | Erfahrung/HP/Rüstung nicht rückentwickelt — Werte werden kopiert, nicht erfunden |
| Eigenschaften frei | **genau sieben**, als feste Felder `+$0E..+$14` verbaut. Eine achte ist keine Datenfrage, sondern ein Engine-Eingriff |
| Fertigkeiten frei | 35, Namen 5-Bit-gepackt im Charakter-Overlay. Eine neue braucht Platz in Namensbank *und* Kostentabelle |
| Schrift frei | eine, 32 Glyphen, resident bei `$C800` |
| Dienst in einer Kette | **kein Übergabefeld**, nichts kann daran hängen |
| Questachse frei | monoton, weil Flash nur `1→0` schreibt |
| Szene ruft Code | nur über area-eigenes grp16 mit eigener Argumentform |

### 6.2 Wo die Engine wachsen müsste

Aus dem WL2-Delta, hier als Ziel-Lücken:

| fehlt | was es bräuchte |
|---|---|
| **Flag-/Zählerspeicher** | der Questlog. Ohne ihn sind Vorbedingung, Zähler, Ruf und Fehlschlag alle dasselbe Token-Item |
| **Ruf, der fällt** | ein Wert in beide Richtungen — mit Flash-`1→0` nicht ohne Erase |
| **Begleiter** | ein WESEN, das mitläuft, ohne einen Gruppenplatz zu belegen |
| **Gruppenidentität** | „ist Christina dabei" — heute zählt die Engine nur |
| **Menge im Inventar** | „habe ich drei davon" — heute nur Präsenz, und die verbraucht |
| **Zeit je Quest** | genau ein globales Timed-Event |
| **Welt tickt weiter** | keine Simulation abwesender Orte |
| **Bedingungen UND/ODER** | heute eine Liste mit Erst-Treffer-Abzweig |

---

## 7. Abnahme

`_ir_roundtrip_test` bildet eine **layout-unabhängige semantische Signatur**: jede Zelle mit ihrem
dekodierten Record, jede Kette mit ihren Gliedern, alles offset-invariant.

> Modell kompilieren → Signatur bilden → gegen die ausgelieferte Signatur halten.

**Nicht Byte-Identität.** Gemessen kommen 42 von 43 Areas aus dem IR kleiner zurück
(305 912 → 302 905 B, 1,0 % Slack, den die 1988er Layouts zwischen den Records ließen).
Byte-Gleichheit wäre die falsche Frage.

---

## 8. Was aus dem Bestand wo landet

```
4885 Records über 43 Orte     → §3
  227 quest-beansprucht       → Beats an Questachsen
 ~95 % Rest                   → Welt: Lore-Szenen, Möblierung
```

Die 227 sind das **Annotierte**, nicht das Vorhandene: Las Vegas, Needles, Quartz und Sewers Lv1
stehen auf null, und dort sitzen Faran Brygo, der Junk Master und die Kanalpassage. Bestand in die
Bedeutungsschicht zu heben ist zum Teil Entdeckungsarbeit.

---

## 9. Offen

1. ~~Wo die Persistenz liegt und in welchem Format.~~ **Entschieden 2026-08-08: JSON-Dateien in
   git, kein SQLite** — 1,67 MB Bestand, 0 ms je Abfrage, und eine Datenbank kostet Diffs,
   Merges und die Historie je Record. Begründung: [`IR_NAMED_REFERENCES.md`](IR_NAMED_REFERENCES.md) §7.
2. **Wie ein Ziel Nummern vergibt**, ohne dass Referenzen brechen, wenn sich etwas verschiebt.
3. **Wer die 4885 Records in Entitäten überführt** — automatisch aus dem IR, von Hand, oder beides.
   §3 sagt, dass es geht; es sagt nicht, wer es tut.
4. **Ob WL1 und WL2 eine Welt teilen** oder zwei Welten mit einem Modell sind.
5. **Die Fertigkeitsnamen sind nie dekodiert worden.** Die 35 Namen im Editor stammen aus dem
   Guide, die 5-Bit-Bank bei `$8525` liegt ungelesen da. Solange das so ist, kann das Modell
   nicht behaupten, die Fertigkeiten aus dem Spiel zu kennen.
6. **`EVENT.args`** — 179 Records mit Argumenten, die nur der zugehörige Code deutet. Solange das
   opak bleibt, ist jede Area mit grp16 nur zusammen mit ihrem Code verschiebbar.
