# Benannte Referenzen im IR

**Status:** Entwurf, 2026-08-08. Nichts davon ist gebaut.
**Die eine Änderung:** ein Verweis benennt eine **Sache** statt einer **Tabellenposition**.
**Warum zuerst:** ohne sie kann keine Entität einen Record besitzen, und keine Metadaten
überleben einen Speichervorgang. Mit ihr fällt beides von selbst.
**Verwandt:** [`META_MODEL.md`](META_MODEL.md) · [`META_MODEL_PLAN.md`](META_MODEL_PLAN.md) ·
[`QUEST_DESIGNER_SPEC.md`](QUEST_DESIGNER_SPEC.md)

---

## 0. Der Befund

Im ausgelieferten Spiel stehen **40 510 positionsgebundene Verweise** (gemessen über 43 Areas):

```
Record → Record     4 781     {"op": 4, "id": 5}
Record → String     4 074     "prompt": 67
                   -------
                    8 855     bekommen eine Marke

Zelle  → Record    31 655     bleibt eine Nummer — Begründung §2
```

Jeder benennt eine **Tabellenposition**. Die Folgen sind messbar:

- **Einfügen und Löschen gibt es nicht.** Nur Ersetzen. Ein Record mehr verschiebt alle
  Nachfolger, und nichts zieht die Zeiger nach.
- **Inhalt kann seinen Ort nicht verlassen.** „Text 67" existiert 43-mal und meint 43
  verschiedene Sätze.
- **Verweise verrotten still.** Von 233 Quest-Schritten zeigt heute schon einer ins Leere
  (`cochise-finster` → `s4a17:grp8[0]`).

Die **Adressen** sind längst frei: `ir_to_record` legt bei jedem Speichern neu aus und zieht 149
von 155 Records in Highpool um, ohne dass etwas bricht — weil es die Zeigertabellen mitschreibt.

> **Diese Änderung gibt der Nummer dieselbe Freiheit, die die Adresse schon hat.**

---

## 1. Was ein Record bekommt

```jsonc
{
  "op": "SAY",
  "ref":  "highpool/boy-watches",        // ← die Marke. Ersetzt (Gruppe, Nummer) als Name.
  "type": "world",                       // ← world | quest | random      Vorgabe: world
  "from": {"how": "manual"},             // ← woher er kam                Vorgabe: unbekannt
  "messages": ["highpool/txt-35"]        // ← Verweis als Marke, nicht als Index
}
```

Drei Zusätze, und sie sind **nicht gleich viel wert**:

### `ref` — die Marke

Erzeugt, nicht erdacht. Beim Einlesen bekommt jedes Ziel eine stabile Marke; menschliche Namen
kommen später und nur dort, wo jemand sie braucht. **Identität heißt nicht „ein guter Name",
sondern „ein Token, das die Umnummerierung überlebt".**

Umfang: 1931 Records und 1880 Strings sind Ziel mindestens eines Verweises (40 % / 41 %). Nur die
brauchen zwingend eine Marke — der Rest bekommt sie der Einheitlichkeit halber.

**Form: `<ort>/<slug>`, ortsgebunden** (entschieden). Der Namensraum spiegelt, was das Spiel
ohnehin trennt:

```
je Ort     Records · Texte · Foes          was im Area-Blob liegt
global     Items · Orte · Wörter            was die Engine global adressiert
```

Nichts erfunden — diese Trennung steht in den Bytes. Und die Ortsbindung ist keine Lüge, die auf
ihren Umzug wartet, weil **Inhalt nicht heimlich umzieht: er wird kopiert.** Das ist die Hausart,
die `wl_blueprint` wörtlich ausspricht — *„A blueprint is always COPIED into the quest that uses
it. It is a form, never a shared instance."* Blocks, Patterns, Blueprints teilen nie; Texte
verhalten sich nicht anders.

*Daraus folgt eine Funktion, die fehlt:* **Texte kopieren, wie man Patterns kopiert.** Die 148
wortgleichen Texte im Spiel — der 34-String-Kampfblock in 40 von 43 Orten — sind genau das, und
heute gibt es keinen Knopf dafür.

**Woraus die Marke entsteht: aus dem Inhalt, wo es einen gibt, sonst laufend** (entschieden).
`highpool/boy-watches` aus *„A tearful boy watches you from the nearby brush."*; `highpool/r0042`
für die 719 CONDs und 623 Übergänge, die keinen sprechenden Text haben. Die Marke wird beim Ändern
des Textes **nicht** nachgezogen — sie ist ein Name, kein Zitat.

**Und das Erzeugen ist ein EINMALIGES Skript, keine Editor-Funktion** (entschieden, Alex). Es läuft
in Python, man sieht sich die Namen an, justiert nach, lässt es wieder laufen — bis es passt. Danach
stehen die Marken in den Daten. Neue Records später bekommen eine laufende Nummer; das
Massenproblem gibt es dann nicht mehr.

### `type` — trägt Last

Wird **gelesen** und entscheidet: „Quest löschen" entfernt `type: quest`, die Abdeckungszahl zählt
darüber, die Batterie behandelt Handlung anders als Welt.

```
world    Vorgabe. Alles, was nicht ausdrücklich anders markiert ist.
quest    gehört einer Quest, wird mit ihr entfernt.
random   Erzeugnis eines Generators, ersetzbar durch einen neuen Lauf.
```

**Fehlt `type`, gilt `world`.** Andersherum wäre jedes unmarkierte 1988er Record Freiwild.

### `from` — trägt keine Last, mit einer Ausnahme

`manual` · `pattern:<id>` · `block:<id>` · `copy:<ref>` — Dokumentation. Wird geschrieben, von
nichts gelesen, und beschreibt nach einem halben Jahr, was der Editor mal getan hat.

**Die Ausnahme ist `random`.** Generator-Ausgabe ist das Einzige, was man wiederholt *ersetzen*
will: Generator neu laufen lassen, genau seine alten Records raus, neue rein. Dafür muss stehen,
welcher Lauf welches Record erzeugt hat — dort trägt Herkunft Last.

### `copy:<ref>` — geführte Beziehung, Propagation nur auf Knopfdruck

Entschieden, und es ist ein Drittes zwischen „tote Notiz" und „zieht automatisch nach": **git.** Die
Beziehung wird geführt und ist abfragbar, aber nichts geschieht von selbst.

```
Master  → show derived → 12 Kopien, 5 davon veraltet → gezielt push auf 3
Kopie   → pull → die neuere Fassung des Masters holen
```

Dafür trägt die Kopie zwei Dinge mehr als den Verweis:

```jsonc
"from": { "how": "pattern", "ref": "guarded-stash",
          "rev": 2,                 // Stand des Masters beim Kopieren
          "as":  "sha:9f3c…" }      // wie die Kopie beim Anlegen aussah
```

`rev` beantwortet *„gibt es etwas zu holen"*. `as` beantwortet *„ist an dieser Kopie seither von
Hand gearbeitet worden"* — der Fingerabdruck der Kopie zum Zeitpunkt ihrer Entstehung, also
`sha(Kopie heute) != as` heißt: jemand hat sie angepasst.

```
rev aktuell, unverändert     nichts zu tun
rev alt,     unverändert     ein Abzug, Nachziehen ist folgenlos
rev aktuell, verändert       eigene Anpassung, in Ordnung
rev alt,     verändert       beides — hier steckt Handarbeit UND der Master ist weiter
```

**`as` ist Anzeige, kein Schutz** (entschieden). Bei *show derived* steht dann „12 Kopien, 5
veraltet, davon 2 mit eigenen Änderungen" — du siehst, welche Pushes trivial sind und welche eine
Ansicht verdienen. Schützen muss es nichts, weil ein Pull nichts überschreibt:

**Ein Pull legt nebeneinander** (entschieden). Er überschreibt nicht und verweigert nicht — die
neue Fassung tritt neben die vorhandene, beide bleiben, **der Mensch entscheidet**. Wie ein
Konflikt in git, nur ohne Blockade: die Kopie darf vorübergehend zwei Stände halten.

Das ist auch der Grund, warum die Genauigkeit des Fingerabdrucks keine Rolle spielt. Als Schutz
hätte jede Ungenauigkeit Daten gekostet; als Anzeige ist eine falsche Einschätzung ärgerlich und
sonst nichts.

**„Wo überall benutzt" bleibt abgeleitet** — die Rückwärtssuche über die Notizen, nie gespeichert
(Regel 4). *Sofortiger Nutzen:* ein Tippfehler im Kampfblock ist heute 40-mal zu korrigieren, ohne
dass jemand weiß, ob er alle erwischt hat.

---

## 2. Das Raster — bleibt, wie es ist

**Entschieden: die Zellen bekommen KEINE Marken.** Ein Entwurf hier war dünne Besetzung; die
Messung hat ihn widerlegt.

```
56 320 Zellen, davon 31 655 mit Inhalt = 56 %      nichts daran ist dünn

Raster heute, zwei Arrays          0,37 MB
als Map, kurze Marken (r042)       0,52 MB
als Map, volle Marken              0,74 MB
```

Jede Map ist größer, weil eine Marke ~20 Zeichen kostet und eine Nummer zwei.

**Der inhaltliche Grund wiegt schwerer als der Größengrund.** Marken braucht ein Verweis, der die
Nummerierung **unabhängig** überleben muss — Record→Record, Record→Text, Quest→Record. Eine Zelle
und ihr Record stehen aber immer im selben Dokument und werden immer im selben Durchgang neu
nummeriert. Dieser Weg muss nie unabhängig überleben.

Damit sind es **8855 Marken statt 40 510.**

> **Die Auflage daraus: der Nummerierungsdurchgang schreibt das Raster mit.** Wer die Recordliste
> von Hand ändert, ohne durch ihn zu gehen, bricht die Zellen still. Derselbe Grund, aus dem
> `sync_from_json` heute die IR-Version prüft, bevor es ein `.json` kompiliert.

---

## 3. Was der Emitter dazubekommt

Einen **Durchgang davor**. Er macht genau das, was er für Adressen längst macht:

```
1  Marken einsammeln        alle refs des Dokuments
2  Nummern vergeben         je Gruppe fortlaufend; Strings fortlaufend
3  Auflösen                 jede Marke → (Gruppe, Nummer) bzw. Index
4  auslegen                 wl_ir.ir_to_record, unverändert
```

Schritt 4 bleibt, wie er ist — er ist über alle 43 Orte geprüft.

**Und hier wird das Verweigern echt** ([`META_MODEL.md`](META_MODEL.md) §5, Regel 5): eine Marke
ohne Ziel ist ein Fehler, keine 0. Heute schreibt der Emitter `67` wieder als `67`, ob dort etwas
steht oder nicht.

### Zielgrenzen, die erst hier auffallen

Die Nummernvergabe stößt an WL1s Grenzen, und zwar **laut**:

- **String-ids sind 7 Bit.** Mehr als 127 benannte Texte je Ort lassen sich nicht vergeben —
  11 Orte mussten das schon 1988 umgehen.
- **Gruppennummern sind 7 Bit** in derselben Weise.
- **Der Recordblob endet bei 9728 B.**

Das sind Meldungen des Ziels, keine Fehler des Dokuments ([`META_MODEL.md`](META_MODEL.md) §6.1).

---

## 4. Die Folge, die alles andere löst

Eine Marke steht **nicht** in den Bytes. Damit ist `record_to_ir` keine vollständige Quelle mehr:
aus einem Blob lässt sich die Nummer lesen, die Marke nicht.

```
heute    .bin ──record_to_ir──► IR        IR ist herstellbar, also Abbild
danach   .bin ✗                 IR        IR trägt mehr als die Bytes, also Quelle
         IR ──ir_to_record──► .bin        das Blob ist Erzeugnis
```

Das beantwortet die Frage, an der die Metadaten hingen: sie überleben, weil das IR nicht mehr
neu erzeugt wird. `normalize()` darf nicht länger `record_to_ir(raw)` rufen — es bekommt das
Dokument, nicht die Bytes.

**`record_to_ir` bleibt** und tut, was ein Dekompiler tun soll: die 1988er Dumps einlesen. Einmal.

---

## 5. Das Tor

Dasselbe Muster wie [`_hexgate.py`](../backend/_hexgate.py), derselbe Snapshot:

```
für alle 43 Orte:   IR ──benennen──► IR' ──Nummern neu vergeben──► .bin
                    muss byte-identisch zum eingefrorenen Hash sein
```

Kommen die 43 Areas byte-gleich heraus, hat die Abstraktion nichts verloren. Jede Abweichung ist
ein Verweis, der falsch aufgelöst wurde.

Zweites Tor, weil das erste es nicht sieht:

```
jede Marke hat genau ein Ziel        keine Waise, kein Duplikat
jedes Ziel ist erreichbar            oder ausdrücklich als tot markiert
```

---

## 5.1 Gebaut (2026-08-08) — und was dabei über die Persistenz herauskam

Beide Tore stehen, aber sie beweisen Verschiedenes, und die Unterscheidung ist der eigentliche
Ertrag dieser Etappe:

```
_markgate.py            leitet die Dokumente jeden Lauf neu aus Bytes ab   → beweist den CODE
_markgate.py --stored   liest, was auf Platte liegt                        → beweist die DATEIEN
```

Das zweite Tor war beim ersten Lauf **rot**, und zwar aus einem Grund, den niemand vermutet hatte:

| | |
|---|---|
| gespeicherte Dokumente | 3 für 5 Records — zwei Records hatten nie eines |
| deren `ir_version` | `None`, `2`, `2` — der Decoder steht bei `3` |
| Folge | `sync_from_json` hat sie **seit jeher verweigert** (die Versionsprüfung tut genau das) |

Das `.json` war also nie Quelle von irgendetwas. Es lag als veraltetes Nebenprodukt daneben, und
kein Tor hat je gefragt, ob es zu seinen eigenen Bytes passt — es tat es nicht, in allen fünf
Fällen. Deshalb liest [`_markmigrate.py`](../backend/_markmigrate.py) die **Bytes** und nicht das
alte Dokument: aus dem `.json` zu rekonstruieren hätte die Veraltung mitgenommen.

**Die Bytes bewegen sich dabei**, und das ist geprüft statt behauptet. Der heutige Serializer packt
Slack weg, den der ältere stehen ließ:

```
s1a00  9053 → 9018 B   (-35)      s1a10  9207 → 9119 B   (-88)
s1a43  5591 → 5583 B   (-8)       s1a51  5610 → 5610 B   (±0)
s3a39  5373 → 5354 B   (-19)                    alle: 0 Engine-Unterschiede
```

`verify_equivalent` vergleicht jedes `(grp, id)`, das die Engine überhaupt anspringen kann, und
meldet null Abweichungen. Das Spiel liest, was es vorher las. Diese Neuemission führt die Migration
auch nicht ein — sie ist ohnehin scharf und wäre beim nächsten gewöhnlichen Speichern jeder Area
passiert; hier wird sie nur sichtbar und prüfbar statt überraschend.

### Wo §4 in der Umsetzung abweicht — bewusst

§4 verlangt, `normalize()` dürfe `record_to_ir(raw)` nicht mehr rufen. Es ruft es weiter, weil
**17 Endpunkte Bytes übergeben**. Statt sie alle umzustellen trägt `wl_marks.carry()` die Marken
des gespeicherten Dokuments auf das frisch dekodierte, über `(Gruppe, Id)`.

Das ist exakt so lange richtig, wie nichts umnummeriert wird — und kein byte-patchender Endpunkt
kann das, weil keiner einfügen kann. Beim Einfügen bräche es: der Record, der von `grp1[15]` nach
`grp1[16]` rutscht, erbte die Marke seines Nachfolgers. **Deshalb muss ein Aufrufer, der
umnummeriert, das Dokument übergeben und nicht Bytes.** Die Grenze steht im Docstring von `carry`,
nicht nur hier.

### Was nicht gewandert ist

Die 40 Areas, die nur Bytes in `baseline/` haben. Ein Dokument dafür zu bauen ist leicht und würde
sich genauso verhalten — aber sie sind die Eingabe des Cart-Builds, und niemand hat sie bearbeitet.
Sie neu zu emittieren ist eine Entscheidung (P4s Tor: semantische Signatur statt Byte-Identität),
keine Sache einer Migration.

`baseline/` ist dabei kein Dump, sondern ein **erklärter** Stand — Area 0 trägt dort den
TREX-Base-Einbau. Damit `--promote` das Dokument nicht abhängt, kopiert
[`check_editor_sync.sh`](../../build/check_editor_sync.sh) das `.json` jetzt neben dem
`.bin` mit; vorher wäre genau die Drift entstanden, die oben alle fünf Dokumente veraltet hat.

---

## 6. Was die Migration mitnimmt

### 6.1 Die 233 Quest-Schritte

`quests.json` zeigt heute mit Positionen ins IR — `{"kind": "ref", "ref": "s4a17:grp8[0]"}`. Das
Skript kennt die Zuordnung alt→neu und schreibt sie auf Marken um.

**Einer zeigt schon heute ins Leere.** `cochise-finster` Schritt 2 nennt `s4a17:grp8[0]` mit der
Notiz *„Base Cochise, level 2: FINSTER · MISSION · CHAT · BASE COCHISE · BREAK."* Nachgesehen:
s4 area 17 **ist** Cochise Lv2, aber die Area hat gar keine Keyword-Gruppe, und das Wort `MISSION`
kommt im ganzen Spiel nicht vor. FINSTER-Keywords gibt es nur in Darwin Village und Mindlink. Die
Notiz beschreibt einen Record, den die ausgelieferten Daten nicht enthalten — vermutlich aus dem
Guide annotiert, nicht aus den Bytes.

**Regel:** ein Verweis ohne Ziel wird zum **Entwurfsschritt**.

```jsonc
vorher   {"kind": "ref",  "ref": "s4a17:grp8[0]", "note": "…"}
nachher  {"kind": "beat", "phase": "sketch",      "note": "…"}
```

Die Notiz ist gute Handarbeit und bleibt; der falsche Zeiger fällt weg; im Designer erscheint der
Schritt als **unfertig statt kaputt**. Das ist die Form, die
[`QUEST_DESIGNER_SPEC.md`](QUEST_DESIGNER_SPEC.md) §2.4 für „entworfen, noch keine Bytes" ohnehin
vorsieht. Das Skript listet am Ende auf, welche Schritte es so umgestellt hat.

---

## 6.2 Reihenfolge

Der Plan in [`META_MODEL_PLAN.md`](META_MODEL_PLAN.md) hatte P2 „das ganze Modell" und P3 „alle
Daten rein". **Das war die falsche Zerlegung.** Diese Stufe steht davor und macht beide erst
möglich:

```
P1   Rest reversen            ✅ durch
P1.5 benannte Referenzen      ← hier
P2   Entitäten                erst jetzt sinnvoll: eine Entität kann einen Record besitzen
P3   Daten rein
P4   Compiler
```

Warum P2 vorher keinen Sinn ergibt: eine Entität, die `grp1[21]` besitzt, besitzt eine
Tabellenposition. Beim ersten Einfügen besitzt sie etwas anderes, und niemand merkt es.

---

## 7. Entschieden (2026-08-08, mit Alex)

| | |
|---|---|
| **1 Form der Marke** | `<ort>/<slug>`, **ortsgebunden**. Inhalt zieht nicht um, er wird kopiert — Hausart wie bei Blocks, Patterns, Blueprints. Dazu fehlt eine **Kopierfunktion für Texte** (§1) |
| **2 Erzeugung** | aus dem Inhalt, wo es einen gibt, sonst laufend — und als **einmaliges Skript**, nicht als Editor-Funktion (§1) |
| **3 Tote Verweise** | werden zu **Entwurfsschritten**: `kind: "beat"`, `phase: "sketch"`, Notiz bleibt, `ref` fällt weg. Das Skript listet auf, welche es waren (§6.1) |
| **4 `copy:<ref>`** | **geführte Beziehung, git-artig** — `rev` + `as`, *show derived* / *pull* / *push* nur auf Knopfdruck, „wo überall" bleibt abgeleitet (§1) |
| **5 Das Raster** | **keine Marken.** 56 % Belegung, jede Map ist größer, und Zelle→Record muss die Nummerierung nie unabhängig überleben (§2) |

### Der Speicher: JSON, nicht SQLite

Gemessen: der ganze Bestand ist **1,67 MB JSON**, das Dekodieren aus den Bytes dauert **0,30 s**,
eine Abfrage über den geladenen Bestand **0 ms**. Eine Datenbank würde einen Index anlegen für eine
Suche, die keine Zeit braucht.

Was sie kostet, trifft den Betrieb: Autocommit bei jedem Save, `/api/history/record/…/restore`, zwei
Menschen gleichzeitig auf `nas-work`. Eine SQLite-Datei ist für git ein Binärklumpen — keine Diffs,
keine Merges, keine Historie je Record, und ein gleichzeitiger Zugriff endet in einem Konflikt, den
niemand auflösen kann.

Und der Reiz einer Datenbank — Beziehungen abfragen — ist genau der Grund, aus dem sie die Wahrheit
nicht halten darf: **eine gespeicherte Kante kann von den Daten abweichen, eine berechnete nicht**
(Regel 4). Bei 0 ms gibt es keinen Anlass, eine zu speichern.

*Falls das später kippt:* SQLite als **abgeleiteter Index**, beim Laden aus dem JSON gebaut,
jederzeit wegwerfbar. Nicht als Speicher.

---

## 8. Offen

Nichts mehr. Die beiden Punkte, die aus §1 entstanden waren — worüber `as` den Fingerabdruck bildet
und was ein Pull mit einer veränderten Kopie tut — sind am 2026-08-08 beantwortet und stehen dort.
