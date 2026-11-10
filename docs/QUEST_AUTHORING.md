# Authoring a questline — what the engine actually permits

Written 2026-07-31, straight after building **The Missing Toaster** end to end: designed in the
editor, written through the endpoints, built, and played through in the headless runtime until it
worked. Everything here was paid for. Where a claim came from a measurement, the measurement is
quoted; where it came from a mistake, the mistake is named.

This is the input to the open question — **the right abstraction and notation for a quest** — not
an answer to it. The answer has to combine four things into one authorable object:

    geography (area : cell)  ×  op  ×  parameters  ×  assets      →  persisted as records

What follows is what each of those four is allowed to be.

---

## 1. A quest is a chain of CELL STATES, and the engine owns the grammar

A quest step is not a free-standing thing. It is **a state a map cell can be in**, and finishing it
rewrites that cell into the next state. So a questline is a path through a state machine whose
transitions the engine defines — not a list the designer composes freely.

Which op may follow which is **measurable**: walk `wl_object.refs_of` over all 43 areas and count.

| edge | shipped precedents |
|---|---|
| ENCOUNTER → LOOT | 264 |
| ENCOUNTER → SAY | 22 |
| SAY → ENCOUNTER | 18 |
| LOOT → SAY | 70 |
| LOOT → SERVICE | 58 |
| LOOT → PATCH | 34 |
| ENCOUNTER → PATCH | 6 |
| **ENCOUNTER → ENCOUNTER** | **0** |

**The lesson cost a build.** The first version made the second droid the first one's victory state,
because +10/+11 is documented as "the state the cell becomes after the fight" and that read
correctly. It is 1 of 592 shipped encounters — and the 1 was ours. No 1988 encounter ever chains to
another encounter.

> **For the editor:** every edge the wizard offers should carry its precedent count. Zero is not
> "forbidden", it is "you are the first — be sure". That one number would have saved two rounds.

## 2. What an op can CARRY decides the design, not the other way round

|  | portrait | multi-page | can rewrite the cell |
|---|---|---|---|
| ENCOUNTER (op-3) | ✓ foe record byte 7 | – | ✓ victory +10/+11 |
| SERVICE (op-6) | ✓ record +$13 | ✓ talk(9) pages | **✗ no advance at all** |
| SAY (op-1) | ✗ | ✓ (a run of ids, bit7 ends it) | ✓ trailing branch |
| LOOT (op-5) | ✗ | – | ✓ advance +0/+1 |
| COND (op-2) | ✗ | 3 strings | ✓ DONE / ALT |
| CELL_PATCH (op-C) | ✗ | – | ✓ **and other cells too** |

Two consequences fall straight out, and both were discovered the expensive way:

* **"Speech with a portrait, then it disappears" is not buildable.** Only a SERVICE carries a
  portrait through a dialog, and a SERVICE has no advance, so it can never clear its own cell. The
  wizard has to say this while the quest is being designed, not let it be found on hardware.
* **A cell is silenced by becoming a SAY with an EMPTY string.** That is Highpool's own idiom
  (`grp5[9] → grp1[0]`), and it is the only clean "this is over now".

## 3. Every state needs a TRIGGER, and the trigger is part of the design

Victory and advance rewrite **the cell you are standing on**. The player still has to step on it
for anything to happen. Only a **CELL_PATCH reaches a different cell** — and a hostile encounter
planted on a different cell is picked up by ENCOUNTER_SCAN in frame housekeeping, so it fires by
itself.

That difference is exactly what a player feels. Alex's report was *"vom 1. Gegner zum 2. muss ich in
das Droid Tile reinlaufen — ich dachte der 2. Encounter triggert einfach"*, and the fix was to stop
chaining on one square and do what Highpool does:

    grp3[8] ENCOUNTER → grp5[8] LOOT → grp12[10] PATCH → plants grp3[10] Bobby AT (8,13)

> **For the editor:** each step should show how it is entered — **step on it · press E · fires by
> itself**. It is the one property you cannot see from the record and cannot guess from the op.

## 4. Flags are semantics with names, not a hex byte

The encounter's byte +9 carries three switches, and each one bit me separately:

| bit | meaning | consequence |
|---|---|---|
| 0 | active | 585 of 592 shipped records set it |
| 1 | **peaceful** | short-circuits the whole combat AI path (carry out of `W9EF6`); the group stands there until the player presses **E** |
| 2 | **stationary** | skips the approach block (`W8418: and #$04` → `W84D9`); without it a hostile group walks toward the party |

111 shipped encounters are peaceful with NPC nibble 0 — a peaceful group needs **no** grp17 member.
As a hex byte in the editor these are invisible; as three named checkboxes they are a design
decision.

## 5. Combat stats are BORROWED, never invented

The Exp/HP/AC bytes of the 8-byte foe record are not reversed (NUWG_RECONCILIATION §D). The honest
move is to copy a shipped foe and say which one — and to copy from the right end of the game. The
first build gave a starting-party area a Sleeper-Base Gundroid and a Base Cochise Steel Reaver.

What IS known, and worth surfacing as one control instead of a byte:

    byte 0 = the experience award AND the HP seed
             HP = (byte0 >> 2) + rnd(byte0)          $A0 -> 40..200, avg ~120, 160 exp
    byte 3 = damage dice
    byte 6 = glyph class -> GLYPH_TABLE[$5BF5]; matching it to the terrain tile hides the figure
    byte 7 = portrait str id

> **For the editor:** a foe-template picker over the 398 shipped records, sorted by toughness,
> labelled *"as tough as a Gundroid"*. Not eight hex fields.

## 6. An area cannot host a fight until it is EQUIPPED

The most expensive finding of the session. TREX Base was built without combat, and its first fight
printed garbage and then froze. Three separate things were missing, all engine machinery rather
than content — full write-up in
[`../../docs/engine/COMBAT_TEXT.md`](../../docs/engine/COMBAT_TEXT.md):

1. the **34-string combat vocabulary block** at the tail of the area's own text pool
2. **`desc+$37..+$5B`**, the 37-entry table that points at it (`desc+$37` = the block base in 39 of
   39 areas)
3. a **foe-name table in the right grammar** — `$00 STEM $0A SINGULAR $0A PLURAL $0A`, closed
   `$00 $FF`. A wrong shape here does not misprint a name, it hangs the machine.

> **For the editor:** this is a checkable precondition. *"This area cannot host combat yet —
> install the block?"*

## 7. Text has ceilings that must bite at author time

| limit | value | where it was learned |
|---|---|---|
| text id | **7 bits** — nothing above 127 is referenceable | [[wl1-text-id-7-bit]]; 11 areas hold longer pools |
| encounter announce | median 45, longest 135 chars | measured over 691 encounters |
| talk page | 104–121 chars is what TREX's own proven pages use | matched deliberately |
| char table | 60 bytes, per area | `prose/validate` already enforces this — the others should join it |

---

## What the session taught about the EDITOR itself

**Two of the three live failures were our emitters, not the design.** The foe-name grammar lived in
the emitter instead of the model, and record bytes were built a second time in the HTTP layer
instead of by the serializer. Both were invisible, because our own reader happily accepted what our
own writer produced.

> **Rule, now wired into the batteries:** assert the emitter against the **shipped bytes**, never
> against our own decoder. `_ir_roundtrip_test` check (c) re-emits all 43 baselines and demands the
> foe-name table, the foe-record table and `desc+$37..+$5B` come back byte-identical.

**The method that worked twice, where reasoning failed:** histogram the 43 shipped areas and find
the field where the new record is the outlier. `victory op 3` — 1 of 592. The name table — 0 of 39.
Both found in minutes, no runtime. An editor could do this by itself: *"this record differs from
all others in field X"*.

**Prebuilts are a trap.** `trex_talk.asm`, the `$CA00` talk handler, is not assembled by
`ef_build_menu.sh`; `src/scenes/build_scenes.sh` has to run first. Same class as the
xtra/teleport/paragraph/manual overlays ([[wasteland-prebuilt-overlay-subbuilds]]). A Build button
that does not run the sub-builds ships stale bytes and says nothing.

**When something hangs, ask whether the in-game clock still runs.** Stopped = a loop, so look at
data; running = waiting for input, so look at the key path. It costs one question and one
screenshot, and it split the search space in half twice.

---

## The open question

None of the above is a notation. What we still need is the shape in which a quest can be WRITTEN —
one object that binds

* **geography** — which area, which cell, and for a CELL_PATCH which *other* cell
* **op** — constrained by the edge grammar of §1 and the carrying capacity of §2
* **parameters** — flags as names (§4), difficulty as one borrowed template (§5), text within its
  ceilings (§7)
* **assets** — portrait, foe record, strings, and the area preconditions of §6

…and out of which the records can be **generated**, rather than hand-assembled and then verified.
Today the editor can author every one of those pieces individually; what it cannot do is hold the
whole chain as one thing and tell you, while you design it, that the engine will not walk it.

## A gate is a menu (2026-08-02)

The single biggest correction to how this editor reads the game.

An op-$2 record's condition list is **not** a set of tests the engine runs at the party. It is the
list of things the player can DO on that square, and the command bar picks the entry. Four routines
walk the same bytes at `($5F),$0A`:

| routine | reached from |
|---|---|
| `W8E49` SCENE_COND_WALK | stepping onto the cell |
| `W8FB9` | `USE` → Skill |
| `W8FEF` | `USE` → Item |
| `W8F84` | `USE` → Attribute |

`CMD_USE ($8A77)` → `MEMBER_ACTION_DISPATCH` → `SELECT_SKILL_ITEM_ATTR ($8AD5)` offers exactly
**S**kill / **I**tem / **A**ttribute, and the item pick yields `2*id + $BB` — the inventory slot
offset. The same three kinds the list encodes.

So when you author a gate you are writing a **menu of verbs**, and each entry may lead somewhere
different. Highpool's purifier offers three ways to work out what the machine is and one to fix it.

### What it costs

An item entry spends the item only if the item HAS charges. `$14A5` masks the slot count with
`#$3F` and branches past the decrement at zero — so a crowbar, a keycard and every clip survive,
while an Engine, a rope and a match burn one. The count comes from the item descriptor at
`$3108 + id*8`, byte 4. See `docs/engine/OP2_COND_TRADE.md` §6-§9.

Practical consequence for authoring: **you cannot make a key single-use by writing the condition
differently.** Consumability belongs to the item, and it is global.

### One record, two roles

A gate can play more than one part in a story. Highpool's is the clean case: the skill entries are
how you learn what the machine needs; the Engine entry is what you come back and do. A quest step
may therefore name a SUBSET of a record's conditions (`conds: [0,1,2]`), and the same record appears
again later with the rest.

What the records CAN say: which entries lead to the same place (alternatives) and which lead
elsewhere (different outcomes), and whether a group costs a consumable.

What they CANNOT say: which role the player meets first. The Jail cell's three outcomes also differ
in cost, and it is not a sequence — it is three ways into one cell. The split is offered, never
applied.

## A patch is where a quest turns

`CELL_PATCH` is the only thing that reaches a DIFFERENT square, and it is where most quests actually
move. The Brakeman's Errand:

    grp5[12] shovel  -> grp12[0]   0,1 FROM THE PARTY -> a bump message
                                   flags $41 = party-relative: the tent goes quiet wherever you
                                   are standing in it
    grp5[4]  cache   -> grp12[18]  (4,15) (6,15) (4,17) -> three hostile groups
                                   "It's an ambush! Rail thieves burst out of hiding…"

Both are readable straight from the records, and both belong in the quest — a quest that stops at
the loot sack stops one step before the thing the loot was for.
