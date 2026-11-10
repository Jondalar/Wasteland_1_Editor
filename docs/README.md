# Editor-Papiere — wo was steht

Stand 2026-08-08, Editor 0.9.13. Zehn Papiere, ~184 KB. Diese Seite sagt nur, welches man wann
aufschlägt.

## Wenn du eine Quest bauen willst

| | |
|---|---|
| [`QUEST_DESIGNER_CONCEPT.md`](QUEST_DESIGNER_CONCEPT.md) | **Zuerst.** Was eine Quest aus Sicht des Autors ist: Trigger, Gate, Effect, Marker, die sechs Engine-Zwänge, der Arbeitsablauf. 46 KB, aber §1–§3 reichen zum Anfangen |
| [`QUEST_DESIGNER_SPEC.md`](QUEST_DESIGNER_SPEC.md) | Das Datenmodell und die API. Die drei Schritt-Formen (§2.4), wer eine Bindung tragen kann (§5.1), was eine Platzierung verweigert (§8) |
| [`QUESTLOG_SPEC.md`](QUESTLOG_SPEC.md) | Der Zustandsspeicher: was er kann, was nicht, wie er im Byte aussieht. **Die Engine-Seite existiert noch nicht** — autorisieren und prüfen ja, emittieren nein |
| [`QUEST_AUTHORING.md`](QUEST_AUTHORING.md) | Vorarbeit: die Zwänge, an denen ein Entwurf scheitert, bevor er Bytes wird |

## Wenn du wissen willst, wie die Daten aussehen

| | |
|---|---|
| [`GAME_LOGIC.md`](GAME_LOGIC.md) | Wie das Spiel eine Zelle in einen Record auflöst und was daraus folgt |
| [`IR_NAMED_REFERENCES.md`](IR_NAMED_REFERENCES.md) | **Marken.** Warum ein Verweis einen Namen trägt statt einer Tabellenposition, und was das für den Speicherpfad heißt. §5.1 = wie es gebaut wurde |
| [`META_MODEL.md`](META_MODEL.md) | Die 16 Entitäten, aus denen eine Welt besteht — gefunden gegen eingeführt, und die gezählten Fluchtluken |
| [`META_MODEL_PLAN.md`](META_MODEL_PLAN.md) | Der Weg dahin, mit einem automatischen Tor je Etappe |
| [`REF_WASTELIB.md`](REF_WASTELIB.md) | Die zweite Quelle (DOS-Fassung). §5 trennt, was wir gemessen haben von dem, was wir ihr glauben |
| [`PLAY_MACHINE_CONTROLS.md`](PLAY_MACHINE_CONTROLS.md) | Der Live-Tab und der trx64-Sidecar |

## Was NICHT hier steht

Die Engine selbst. Record-Format, Szenen-Script, Foe-Records, Services, Speichern —
[`docs/`](../../docs/README.md), nach Rollen sortiert. Der Editor beschreibt, wie
man Inhalt *macht*; jene Papiere beschreiben, wie die Maschine ihn *liest*.

## Zwei Stellen, an denen die Papiere heute noch nicht die Wirklichkeit sind

1. **Der Questlog hat keine Engine-Seite.** `/api/design/place` weist einen Beat mit `bind` mit
   `501 quest binding not wired` ab. Alles andere im Designer ist davon unabhängig.
2. **Die Szenen-Registry prüft, aber niemand ruft sie.** Es gibt keinen Autorenweg, der einen
   `call scene` erzeugt — `wl_scene_registry.check_call` wartet auf ihn.

Beides steht an seiner Stelle noch einmal, mit Begründung.
