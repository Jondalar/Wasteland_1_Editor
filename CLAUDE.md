# CLAUDE.md — WL1 Editor

Guidance for Claude Code working in this repository.

## What this is

A data editor for Wasteland (Interplay / EA, 1988, C64): maps, area scripts, items, foes, texts,
portraits. FastAPI backend, dependency-free JS frontend, local browser app. Carved out of a
larger cartridge-porting project so it could be shared publicly; the cartridge build, the
emulator integration and the NAS deployment all stayed behind and are **not** coming back here.

## The one hard rule

**No game data in this repository. Not one byte.**

No disk images, no maps, no graphics, no text — and no item or skill names either, because those
are the game's prose. Everything the editor shows is decoded on the user's machine from disk
images they already own, by `bootstrap.py`.

This is not a preference. It is the reason the repo can exist publicly at all, and a single
`git add -A` is all it takes to break it permanently.

```bash
python3 tools/check_clean.py            # before any commit
python3 tools/check_clean.py --staged   # as a pre-commit hook
```

It fails on game-data extensions anywhere in the tree, on anything under `data/`, and on a source
file that inlines the name tables again. The screenshots under `docs/img/` are the single
deliberate exception — documentation of the tool, and labelled as such in `docs/GUIDE.md`.

## Getting data to work with

```bash
python3 bootstrap.py --disks /path/to/g64s     # -> data/, git-ignored
bash run.sh                                     # http://127.0.0.1:8080
```

Without `data/` the server still starts and serves an empty UI — every game-data read is lazy and
fails soft. That is by design: a stranger's first run must not crash.

## Layout

```
backend/    FastAPI app + codecs, one flat namespace, no packages
  app.py    ~146 routes
  wl_*.py   one module per format
frontend/   index.html + main.js + style.css. No framework, no build step, no node_modules
tools/      extraction and decoding, each runnable standalone
data/       generated, git-ignored
docs/       GUIDE.md (the tour), APPLE2.md, format notes
```

`WL_DATA` moves the data root; `WL_WORK` and `WL_ASSETS` override the sub-roots. Nothing in the
code may hardcode a path outside `DATA`.

## How to work here

**Measure, do not assert.** This codebase is reverse-engineered, and every format claim in it is
supposed to trace to bytes. When you change a codec, prove it against real data: re-extract and
compare, decode and diff, round-trip and check the length. "It should work" is not a result.

**Verify the artifact, not the tool.** Running a generator and reading its output proves the
generator ran. Read the file the editor would actually load.

**The serializer is the safe path.** Area records are decoded to a JSON intermediate and rebuilt
from scratch, so every pointer is recomputed; `wl_persist.save_record` refuses a save if any
record the engine can reach would come back different. Never patch record bytes in place.

**Trailing fields are sized by the layout, not by their value.** This cost real money once: the
SAY record's trailing advance pair was modelled first as "always two bytes" (which ate the
neighbour's bytes) and then as "never any" (which dropped it, and broke a town in the shipped
cartridge for two weeks). `_adv_tail(raw, off, stop)` takes the record's extent. Keep it that way.

**A `<textarea>` is not a faithful container.** The HTML parser drops one newline directly after
the opening tag, and 2336 of the game's strings start with `\n`. Every inline textarea in
`main.js` opens with an extra newline for that reason; do not "clean it up".

## Known defects

`backend/wl_text5.py`'s fallback constant `G29E7` starts three bytes too late — it is missing the
newline, the `s` and the `0x00` terminator. It is **unused**; every caller passes the table read
out of the blob. Fix it when you touch the file.

## Not done

- No synthetic test fixtures. The nine `backend/_*_test.py` files all need a populated `data/`,
  and `_ir_roundtrip_test.py` globs at module scope, so on an empty tree it passes on nothing.
  A stranger's first test run currently lies to them.
- No CI.
- Parts of `docs/` are German and were written for the parent project.
- `/favicon.ico` 404s. Cosmetic, the only console error.

## Next task: Apple II support

Wasteland was written on the Apple II; the C64 release is the port, and **the data is the same
data**. A format comparison against the Apple disks found the text codec, the compressor, the
directory format, the area records, the item table and the asset ids all shared. Only graphics
are machine-specific.

Read **`docs/APPLE2.md`** first — it has the evidence, the byte offsets and the file-level list
of what changes. The short version:

- The Apple extraction path is *simpler*: no GCR decoder, no zone tables, no interleave. A 38-line
  sector reader replaces `wl_g64decode.py` + the 1541 half of `wl_directory.py`.
- `wl_decompress.py`, `wl_text5.py`, `wl_area.py`, `wl_item.py` work unchanged.
- The record window is at `$D000` instead of `$3400`. The literal `0x3400` (~25 places) has to
  become a parameter. That is the only model difference found.
- Two things are open and must be treated as open: the **write path** (only reading was proven;
  appending the rebuilt text region at the end of a blob may not be legal at a `$D000` base — the
  answer is in the Apple engine code, which nobody has disassembled), and the **graphics**
  (Apple hi-res and its palette against C64 multicolour is a genuinely different format model,
  and was deliberately left out of the comparison).

Working material — the five `.dsk` images, the analysis scripts, and what they extracted — is
parked at `../_wl1_apple2/`, outside this repository. It stays outside.

The goal is one editor that reads both machines, not a fork.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **WL1_Editor** (3202 symbols, 11301 relationships, 284 execution flows).

> Index stale? Run `node .gitnexus/run.cjs analyze --index-only` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? Bootstrap with `npx`, `bunx`, or `pnpm dlx` — e.g. `bunx gitnexus@latest analyze` (npm 11 npx crash; #1939).

## Always Do

- **MUST run impact before editing.** Use `impact({target: "symbolName", direction: "upstream"})` or `node .gitnexus/run.cjs impact "symbolName" --direction upstream --repo .`; report callers, processes, and risk. Never substitute grep for graph analysis.
- **MUST analyze graph changes before committing.** Use `detect_changes({scope: "all"})` (MCP) or `node .gitnexus/run.cjs detect-changes --scope all --repo .` (CLI fallback). `partial: true` or `truncated: true` is not a clean check — a zero means unseen, not unaffected; re-run it. For regression review: `detect_changes({scope: "compare", base_ref: "main"})` or `node .gitnexus/run.cjs detect-changes --scope compare --base-ref "main" --repo .`.
- MUST warn on HIGH/CRITICAL `risk` pre-edit; never use `riskSharedAxes` to waive a HIGH/CRITICAL `risk` warning. Compare File/symbol: MCP File omits axes; Graph-RAG expands File.
- **MUST treat `risk: UNKNOWN` as unresolved, not as low.** An empty caller set is not evidence the symbol is unused — it can also mean the callers are not resolvable by the index (plain-object property access, dynamic dispatch, cross-language calls). `impact` pairs `UNKNOWN` with a `riskNote` saying so. Confirm with a text search before treating the symbol as safe to change or delete; do not proceed on the strength of a zero.
- **MUST use `query({search_query: "concept"})` for concepts/flows, `context({name: "symbolName"})` for a named symbol, or `impact` for blast radius, on read-only callers, dependencies, imports, or execution flow.** Graph first; text search only for empty/`UNKNOWN`/literals.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method before MCP/CLI impact analysis.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis, and never read `UNKNOWN` as an all-clear — it means the walk could not answer, which is the one verdict that requires confirming by other means.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit before MCP/CLI graph change analysis.

## Resources

| Resource | Use for |
| --- | --- |
| `gitnexus://repo/WL1_Editor/context` | Codebase overview, check index freshness |
| `gitnexus://repo/WL1_Editor/clusters` | All functional areas |
| `gitnexus://repo/WL1_Editor/processes` | All execution flows |
| `gitnexus://repo/WL1_Editor/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
| --- | --- |
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
