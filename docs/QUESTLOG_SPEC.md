# Das Questlog — was es kann

**Stand:** 2026-08-08, Editor 0.9.15. Modell, Anzeige und **Byte-Vertrag** stehen (§5) —
die 6502-Seite kann dagegen gebaut werden, bevor der Editor sie hat.
**Umsetzung:** [`wl_questlog.py`](../backend/wl_questlog.py) · `GET /api/quests/log` ·
Batterie-Abschnitt (k) in [`_questlog_test.py`](../backend/_questlog_test.py).
**Herkunft:** [`QUEST_DESIGNER_CONCEPT.md`](QUEST_DESIGNER_CONCEPT.md) §8.6 Option B ·
[`QUEST_DESIGNER_SPEC.md`](QUEST_DESIGNER_SPEC.md) §2.2–2.5.

WL1 hat kein Questlog. Dieses Papier beschreibt eine Fähigkeit, die eingeführt wird — als Vertrag,
gegen den der Editor heute schon autorisiert und prüft, und aus dem später ein `.bin` fällt.

---

## 1. Der Speicher

Ein durchgehender Bytebereich. Jede Quest bekommt daraus einen Bereich, der ihr fest gehört.

```
$FF   freies Byte — nichts ist passiert
$00   alle acht Bits beschrieben
```

**Es wird runtergezählt.** Flash schreibt nur 1→0, also *löscht* jeder Fortschritt ein Bit, und der
Zustand ist die **Zahl der gelöschten Bits**. Die Bits gehen der Reihe nach: erst läuft Byte 0 leer,
dann Byte 1.

```
open   $FF FF     kein Bit gelöscht — unberührte Quest und gelöschter Speicher sind dieselben Bytes
s1     $FE FF     Bit 0 von Byte 0
s2     $FC FF     Bits 0-1
s8     $00 FF     Byte 0 leer
s9     $00 FE     Bit 0 von Byte 1
```

Ein Byte trägt **9 Zustände** — acht Bits plus `open`, das keines braucht. Allgemein:
`Zustände = 8 × Bytes + 1`.

**Der Autor sieht davon nichts.** Er zählt vorwärts (`open → s1 → … → done`); die Kodierung zählt
rückwärts. Die Umkehr darf im Designer nirgends auftauchen.

### Warum das Layout geschrieben und nicht gerechnet wird

Ein Spielstand verweist auf ein Bit über seine **Position**. Würde die Zuteilung bei jedem Lesen neu
gerechnet, verschöbe sich jedes Bit, sobald irgendeine Achse wächst — und ein Spielstand in einer
Schublade läse dann den Fortschritt einer anderen Quest. Deshalb:

- die Zuteilung wird **einmal** vergeben und in `_log` festgehalten;
- ein freigewordener Bereich wird **stillgelegt**, nie neu vergeben;
- eine gewachsene Achse bekommt einen **neuen** Bereich und lässt den alten liegen.

Dieselbe Regel wie bei den `qid`s, aus demselben Grund.

---

## 2. Was man damit sagen kann

### 2.1 Eine Quest hat einen Fortschritt

```jsonc
"axis": [
  {"id": "s1",     "log": "Adam Doochin bat uns, seinen Hund zu suchen."},
  {"id": "s2",     "log": "Am Damm lief uns ein verstörter Hund zu."},
  {"id": "done",   "log": "Garoof und Adam sind wieder beisammen."},
  {"id": "failed", "log": "Wir haben Adam gesagt, dass Garoof tot ist."}
]
```

`open` steht nie drin. Reihenfolge = Fortschritt. Länge frei — der Speicher wächst in Byteschritten
mit. `done` ist der letzte Vorwärtszustand; `failed` steht **daneben**, nicht dahinter.

### 2.2 Ein Schritt liest den Zustand — das Tor

```jsonc
{"cond": "quest", "quest": 17, "state": "s2"}      // ≥ s2
{"cond": "quest", "quest": 17, "state": "failed"}  // == failed
```

`≥`, weil der Zustand monoton ist: wer auf `s3` steht, hat `s2` hinter sich. `failed` vergleicht nur
mit sich selbst — eine gescheiterte Quest ist nicht „hinter" den Zuständen, die sie nie erreicht hat
(`wl_questlog.at_least`).

Das **Nein** ist kein eigenes Vokabular: ein COND hat `done` und `alt`, und `alt` ist der Fall, in
dem das Tor nicht hält.

### 2.3 Ein Schritt schreibt den Zustand — die Wirkung

```jsonc
{"effect": "advance", "quest": 17, "to": "s1"}     // "to" weglassen = eine Stufe weiter
{"effect": "fail",    "quest": 17}
```

Die Wirkung hängt an einem **Beat**, und zwar an der Rolle, die eine Übergabe hat:

```jsonc
"bind": {"effect": "advance", "to": "s1", "on": "loot"}
```

Nicht jede Rolle kann das. `wl_place.HANDOVER` sagt gemessen, welcher Op ein Feld dafür hat —
SAY, COND, ENCOUNTER, LOOT und TRANSITION ja, **SERVICE nicht** (28 von 39 Servicetüren enden nach
6 Byte). Deshalb bindet `door-to-service` an der **Tür**, nicht am Laden.

`quest` ist immer die **`qid`**, nie der Slug: der Slug ist Autorenkomfort und darf sich ändern, die
Nummer ist die Adresse im Speicher und wird nach dem Löschen einer Quest **nie** neu vergeben.

### 2.4 Quests voneinander abhängig machen

Der Speicher ist **global** und über die `qid` adressiert. Jeder Beat jeder Quest darf den Zustand
jeder anderen lesen. Damit ist eine Abhängigkeit kein Vermerk mehr, sondern ein Mechanismus:

```jsonc
// Quest 22, erster Beat: geht erst auf, wenn Quest 17 fertig ist
"bind": {"gate": {"cond": "quest", "quest": 17, "state": "done"}, "on": "check"}
```

**Das ist der Unterschied zu heute.** `"after": ["sewers-rebuild-max"]` ist eine *Behauptung* — der
Editor sortiert danach die Stufenansicht, das Spiel weiß nichts davon. Ein Tor auf `quest 17 ≥ done`
ist dieselbe Aussage als *Sperre*: der Spieler kommt nicht durch.

Beides bleibt nebeneinander stehen und meint Verschiedenes:

| | |
|---|---|
| `after` | Erzählordnung. Anzeige, Stufen, „was zuerst gelesen werden will" |
| Tor auf fremde `qid` | Spielregel. Was die Engine durchlässt |

Damit lassen sich bauen: **Ketten** (B öffnet erst nach A), **Und-Verzweigungen** (ein Beat mit
mehreren Toren), **Alternativen** (zwei Beats, deren Tore einander ausschließen), **Sackgassen**
(`failed` als Tor für einen Ausweichzweig).

### 2.5 Einen Schritt fest verdrahten

Ein Beat ist damit vollständig beschreibbar, ohne dass ein Mensch Bytes anfasst:

```
TRIGGER   was der Spieler tut          die Op des Beats — betreten, reden, plündern, kämpfen
GATE      was wahr sein muss           Zustand der eigenen oder einer fremden Quest
EFFECT    was sich ändert              advance / fail auf einer qid
NEXT      wohin danach                 die Übergabe der Rolle (done/alt/after/advance)
```

Vier Angaben, und alle vier stehen im Beat. Der Emitter macht daraus Records; welche Rolle die
Übergabe trägt, entscheidet die Tabelle aus §2.3 und nicht der Autor.

---

## 3. Die Struktur

### 3.1 Autoriert — pro Quest, in `quests.json`

```jsonc
"needles-bloodstaff": {
  "qid": 14,
  "axis":    [{"id": "s1", "log": "…"}, {"id": "done", "log": "…"}],
  "markers": [{"id": "TOY", "kind": "item", "item": 75}]
}
```

### 3.2 Zugeteilt — einmal, dann fest

```jsonc
"_log": {
  "version": 1,
  "bytes":   3,
  "alloc":   {"14": {"byte": 0, "len": 2}},
  "retired": [{"byte": 2, "len": 1, "qid": 9}]
}
```

Die Zuteilung passiert im **Schreibpfad** (`app._quests_write`), nicht in einem Endpunkt — damit
kein Weg einen Zustand anlegen kann, ohne dass die Bytes dafür entstehen. `log_alloc` ist idempotent
solange der Bereich reicht, ein Umbenennen fasst also nichts an.

### 3.3 Abgeleitet — `GET /api/quests/log`

```jsonc
{
  "bytes": 3, "lines": 11,
  "encoding": {"erased": 255, "counts": "down",
               "note": "progress CLEARS bits; the state is the number of cleared bits"},
  "quests": [
    {"slug": "needles-bloodstaff", "qid": 14, "byte": 0, "len": 2, "needs": 2,
     "states": [
       {"id": "open", "byte": null, "bit": null, "mask": null},
       {"id": "s1",   "byte": 0, "bit": 0, "mask": 1, "reached_when": "bit == 0", "log": "…"}
     ]}
  ],
  "retired": [], "problems": []
}
```

Das ist die Anzeige im Quest-Editor **und** die Form, aus der ein `.bin` fällt.

### 3.4 Was geprüft wird

| | |
|---|---|
| `log-overlap` | zwei Quests auf demselben Byte |
| `log-retired-reused` | ein stillgelegtes Byte wurde neu vergeben — ein Spielstand hat die Bits noch |
| `log-unallocated` | eine Quest deklariert Zustände, hat aber keine Bytes |
| `log-outgrown` | die Achse ist über ihren Bereich gewachsen |
| `log-gap` | ein Zustand ohne Logzeile |
| `axis-limit` · `axis-monotonic` · `axis-reachable` | eine Wirkung über `done` hinaus, rückwärts, oder ein Zustand, den nichts erreicht |
| `axis-text-budget` | **die echte Grenze**, s. §4 |

---

## 4. Was es nicht kann

**Nur vorwärts.** Ein Zustand steigt, er fällt nie — Flash kann ein gelöschtes Bit nicht
zurücksetzen, ohne den ganzen Sektor zu löschen. Ein „Quest zurücksetzen" gibt es nicht.

**Keine Zahlen.** Der Speicher zählt Zustände, nicht Dinge. „Bring mir fünf Schädel" ist kein
Zustand, sondern fünf davon — oder ein Item mit Zählerstand, was das Spiel ohnehin besser kann. Das
Konzept nennt dafür ausdrücklich zwei Quests, deren Differenz zählt (§8.4).

**`failed` ist eine Sackgasse.** Aus jedem Zustand erreichbar, aber nichts geht danach weiter, und
kein Vorwärtszustand impliziert es.

**Die Achse begrenzt der TEXT, nicht der Speicher.** Ein Byte mehr kostet ein Byte; eine Logzeile
mehr kostet eine text5-Zeile, und davon gibt es je Ort 127 (`wl1-text-id-7-bit`). Deshalb ist
`axis-text-budget` eine Warnung und die Bytezahl nur eine Anzeige. Wer eine 40-stufige Quest
schreibt, scheitert am Text, lange bevor der Speicher eng wird.

**Kein Zustand ohne Quest.** Der Speicher ist über `qid` adressiert. Ein globales Weltflag, das zu
keiner Quest gehört, hat hier keinen Platz — dafür gibt es Zellen, Items und `grp16`.

---

## 5. Der Byte-Vertrag

Damit die 6502-Seite **dagegen** gebaut werden kann, statt dass der Editor auf sie wartet. Beide
Vokabeln aus §2 kompilieren in einen op-2-Record. Eine der beiden braucht die Engine gar nicht.

### 5.1 Der Effect — vorhanden, nichts zu bauen

Ein COND trägt bei `+$8/+$9` ein Effect-Paar `[target][value]`:

```
target & $7F   OFFSET INS MEMBER-RECORD
target bit7    "value ist literal"
```

Gemessen: **719 der 719 COND-Records** tragen das Feld, ausgeliefert benutzt mit `member_offset`
`$1D` (CON), `$15` (Geld), `$21` (XP). Ein Byte an einem Offset zu schreiben ist also nichts
Neues — es ist der Pfad, den das Spiel seit 1988 geht.

```jsonc
{"effect": "advance", "quest": 17, "to": "s2"}
  →  effect = {"target": <Cache-Offset von Quest 17>, "value": 2, "literal": true}
```

**Kein Engine-Eingriff.** Der Cache-Offset kommt aus der Slot-Zuordnung (§3.2 + der Party-Seite);
der Wert ist die Zustandszahl, nicht die Bitmaske — im RAM wird vorwärts gezählt.

### 5.2 Das Gate — eine neue Prüfart, und nur eine

Ein COND trägt eine Liste von Bedingungen `[{opb, arg}]` mit `Art = opb >> 5` und
`Schwierigkeit = opb & $1F`. Gemessen über alle 43 Orte:

```
Art  0: 570   1: 1090   2: 425   3: 28   4: 35   5: 3   6: 5        FREI: nur 7
```

**Art 7 ist die einzige freie.** Der Vertrag:

```
opb  = (7 << 5) | Zustand        Zustand 0..31, gezählt wie der Autor zählt (open = 0)
arg  = Cache-Offset der Quest     dasselbe Byte, das der Effect schreibt
```

Was der Handler tun muss, in einem Satz: **das Byte bei `arg` lesen und `>= Zustand` setzen** —
Carry gesetzt = Bedingung erfüllt, wie jede andere Art auch. Kein Wurf, keine Skalierung.

**Warum nicht Art 2 („attribute check"), obwohl sie schon einen Member-Offset liest:** weil sie nie
vergleicht. `OP2_COND_TRADE.md` hat es byte-getraced — `opb & $1F == 0` ist ein **auto-pass**,
`!= 0` führt über `scale_x5` auf `rng_mod_range`, also einen **Würfelwurf** gegen das Attribut. Ein
Tor, das würfelt, ist kein Tor. Art 2 ist eine Fertigkeitsprobe und bleibt eine.

### 5.3 Was das für die Reihenfolge heißt

```
heute      Editor autorisiert, prüft, PLATZIERT und speichert die Bindung. Was noch nicht in
           Bytes fällt, meldet er als Warnung — vier Felder wegzuwerfen, weil das fünfte noch
           keinen Leser hat, war der falsche Handel
danach     die Engine lernt Art 7 (eine Verzweigung im Bedingungs-Walk) und den Flash↔Party-
           Abgleich (grp16, §Konzept 8.6 C). Der Effect braucht nichts davon
dann       die Warnung fällt, und der vorhandene Emitter schreibt beide Vokabeln
```

Der Vertrag ist damit vollständig genug, um die 6502-Seite dagegen zu schreiben, **bevor** der
Editor sie hat. Was er offen lässt, ist unten.

---

## 6. Nicht entschieden

**Wo der Bereich im Flash liegt** und **wer ihn liest**. Das Konzept nennt drei Orte (§8.6 A/B/D)
und zwei Leser; die Arbeitsannahme ist B + C, festgelegt ist nichts. Diese Spec beschreibt
ausdrücklich nur die **Struktur** — sie legt sich auf keine Adresse und keine Aufrufkonvention fest.

**Der Emitter.** `log_layout()` liefert die Form; was daraus für Bytes werden und wie das Overlay
sie liest, ist offen. Alex baut dafür ein eigenes Overlay.

**Es gibt keine Sperre mehr.** `/api/design/place` platziert den Beat, schreibt die Records und
legt die Bindung beim Schritt ab; die eine Hälfte, die noch nicht kompiliert, kommt als Warnung
zurück. Die Bindung nennt eine **Quest**, keine Adresse — wenn die Engine-Seite steht, kompiliert
ein erneutes Platzieren sie, ohne dass am Autorierten etwas zu ändern wäre. Der ausgelieferte
Bestand deklariert ohnehin keine einzige Achse und kostet 0 Byte.
