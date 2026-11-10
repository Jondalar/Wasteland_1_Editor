// Wasteland Editor — 3 tabs (Map / Tilesets / Portraits).
// MC pixels render 2:1 (a multicolour pixel is 2 hi-res px wide). Edits save to editor/work/.

const $ = (id) => document.getElementById(id);
const OP_NAMES = {
  0x0: "default / terrain", 0x1: "SAY", 0x2: "COND/TRADE", 0x3: "encounter",
  0x4: "sweep", 0x5: "LOOT", 0x6: "AREA_EVENT (shop/doc)", 0x7: "npc-data",
  0x8: "KEYWORD", 0x9: "sweep/XP", 0xA: "TRANSITION", 0xB: "bump/npc-data",
  0xC: "CELL_PATCH", 0xD: "default", 0xE: "default", 0xF: "npc/encounter",
};
const OP_COLOR = { 0x1: "#5aa9e6", 0x2: "#e6c15a", 0x5: "#7bd88f", 0x6: "#e6635a", 0x8: "#b98ce6", 0xA: "#e6a24e", 0xC: "#5ae0d0" };
let PALETTE = [];
const status = (t) => { $("status").textContent = t; };

// ── generic atlas (base64 RGBA → offscreen canvas we blit tiles from) ──
function makeAtlas(a) {
  const bytes = Uint8ClampedArray.from(atob(a.rgba_b64), (c) => c.charCodeAt(0));
  const cv = document.createElement("canvas");
  cv.width = a.w; cv.height = a.h;
  cv.getContext("2d").putImageData(new ImageData(bytes, a.w, a.h), 0, 0);
  return { ...a, canvas: cv };
}
// A C64 tile is a square 16x16; an Apple II tile is 14x16, because 7 pixels go in a byte there.
// So the atlas reports tile_w/tile_h and `tile` stays for anything that only knew the square.
// The destination w/h are unchanged either way: both machines put 20 tiles across a screen, so
// the two shapes fill the same cell — the Apple's is simply squeezed, exactly as on the hardware.
function blit(dst, atlas, tid, dx, dy, w, h) {
  const TW = atlas.tile_w ?? atlas.tile, TH = atlas.tile_h ?? atlas.tile;
  const sx = (tid % atlas.cols) * TW, sy = ((tid / atlas.cols) | 0) * TH;
  dst.imageSmoothingEnabled = false;
  dst.drawImage(atlas.canvas, sx, sy, TW, TH, dx, dy, w, h);
}

// ═══════════════ TABS ═══════════════
// Three top tabs hold more than one section, so a tab NAME and a section id are not the same thing.
// GROUPS is that table and the ONLY place the membership is written down: which sections a top
// button owns, and which of them it is showing (`at`, the one last open).
//
//   Areas   → the map, or the flow list, depending on the mode (View / Edit / Flow)
//   Design  → the designer, or the quest overview beside it
//   World   → what the game already HAS: portraits, tilesets, the blocks and patterns you build
//             out of, the teleport table, the starting party
//
// The split follows what a screen is FOR. Design is where a quest is AUTHORED; everything else a
// screen can show is stock — and a block or a pattern is stock in exactly the sense a portrait or
// a tileset is: a catalogue of what exists, that you pick from. Quests moved the other way, out of
// World and up beside the designer, because "what am I making" is not "what does the game have".
// An earlier cut gave the catalogue its own top-level tab; that read Blocks and Patterns as a
// third kind of thing when they are the same kind as Portraits (Alex, 2026-08-06).
const GROUPS = {
  world: { at: "tilesets", panes: { tilesets: "tilesets", portraits: "portraits",
                                    teleport: "overview", gfx: "gfx",
                                    endings: "endings" } },
};
// TOP_OF says which top button lights up for a given section, so deep links (gotoArea, a portrait
// click, "open in Flow") can still name a section and the header stays right. DERIVED from GROUPS:
// a hand-written copy is what let a section show while the wrong top button was lit.
const TOP_OF = { map: "areas", flow: "areas", design: "design", quests: "quests" };
for (const [g, spec] of Object.entries(GROUPS))
  for (const sec of Object.values(spec.panes)) TOP_OF[sec] = g;

// ═══════════════ GFX (non-portrait, non-map pictures) — two panes ═══════════════
// TITLE = the WASTELAND logo (glyph grid) + the globe (anim-object). ENDINGS = the endgame scene
// frames (Cochise dome / ranger-death skeletons / all-dead finale), each also a $0586 anim-object.
// Every asset opens the SAME picture editor as portraits (openAnimEditor); the editor is mounted as a
// SIBLING of the pane's -view (so re-rendering the preview never tears it out) and driven by the box
// from the asset's own header. Edits are written to cart_EF/assets/<group>/ and the build prefers
// that source over the g64 decode. `group` on each /api/gfx asset routes it to its pane.
// An Apple II root sends the same two panes with `editable:false` — a different picture format, so
// preview only, and one asset more in ENDINGS (the Cochise dome is stored twice there, still and
// animated) and one fewer in TITLE (that screen has no logo). Nothing here is machine-specific but
// the flag; both panes read whatever /api/gfx lists.
let gfxAll = null;
const gfxLoaded = { title: false, endings: false };
const GFX_PANES = {
  title:   { list: "gfx-list",     side: "gfx-side",     view: "gfx-view",     info: "gfx-info" },
  endings: { list: "endings-list", side: "endings-side", view: "endings-view", info: "endings-info" },
};
async function gfxLoadAll() {
  if (!gfxAll) { try { gfxAll = (await (await fetch("/api/gfx")).json()).assets; } catch { gfxAll = []; } }
  return gfxAll;
}
async function gfxRenderPane(group) {
  gfxLoaded[group] = true;
  await gfxLoadAll();
  const ctx = GFX_PANES[group], items = gfxAll.filter((a) => a.group === group);
  const list = $(ctx.list); list.innerHTML = "";
  for (const a of items) {
    const t = document.createElement("div"); t.className = "pt-thumb"; t.title = `${a.label} — ${a.desc}`;
    t.innerHTML = `<img src="/api/gfx/${a.name}/preview.png?t=${Date.now()}" onerror="this.parentElement.classList.add('missing')"><span>${a.label}</span>`;
    t.addEventListener("click", () => gfxOpen(a, ctx));
    list.appendChild(t);
  }
  $(ctx.info).textContent = `${items.length} graphic${items.length === 1 ? "" : "s"}`;
}
const gfxInit = () => gfxRenderPane("title");
const endingsInit = () => gfxRenderPane("endings");

// Preview + "✎ edit" → the shared picture editor (openAnimEditor), scoped to the pane's ctx so two
// panes never fight over one id. The editor mounts as a sibling of ctx.view inside ctx.side.
function gfxOpen(a, ctx) {
  const bust = Date.now(), esc = (s) => String(s == null ? "" : s).replace(/</g, "&lt;").replace(/"/g, "&quot;");
  $("portrait-editor").hidden = true;                 // collapse any open editor when switching asset
  const box = $(ctx.view); box.classList.remove("muted");
  // `editable` is false on an Apple II root: the picture editor decodes the C64 multicolour
  // object, and the Apple keeps a hi-res artefact-colour one. Preview only, and no button to
  // press rather than a button that 409s.
  const editable = a.editable !== false;
  box.innerHTML = `
    <div class="insp-head">${esc(a.label)} ${editable ? `<button class="mini gfx-edit-btn">✎ edit</button>` : ""}
      <span class="muted small">${esc(a.kind)} · ${esc(a.desc)}</span></div>
    <img class="gfx-big gfx-preview-img" src="/api/gfx/${a.name}/preview.png?t=${bust}" alt="${esc(a.label)}">
    <div class="muted small">${editable
      ? `✎ edit opens the picture editor — paint with the 4 window colours, drop a
      PNG onto it${a.kind === "anim" ? ", or edit the animation slots" : ""}. Save writes
      <code>cart_EF/assets/${a.group}/</code> → the build packs it over the g64 decode.`
      : `Apple II data root — read only. This is a hi-res anim-object; the picture editor only
      speaks C64 multicolour.`}</div>`;
  if (editable) box.querySelector(".gfx-edit-btn").addEventListener("click", () => openAnimEditor(`/api/gfx/${a.name}`, a.label, {
    host: $(ctx.side), animCapable: a.kind === "anim", title: "Picture editor",
    onSaved: () => gfxReloadPreview(a.name, ctx),
  }));
}

// After a save, refresh the big preview + the list thumbnail from the freshly written source (scoped
// to the pane, so the other pane's stale copies are left alone).
function gfxReloadPreview(name, ctx) {
  const bust = Date.now();
  const big = $(ctx.view).querySelector(".gfx-preview-img"); if (big) big.src = `/api/gfx/${name}/preview.png?t=${bust}`;
  $(ctx.list).querySelectorAll(`img[src*="/api/gfx/${name}/preview.png"]`).forEach((img) => {
    img.src = `/api/gfx/${name}/preview.png?t=${bust}`;
  });
}
function setTab(name) {
  let real = name;
  if (name === "areas") real = mode === "flow" ? "flow" : "map";
  else if (GROUPS[name]) real = GROUPS[name].panes[GROUPS[name].at];
  // A deep link still names a SECTION ("open in Flow", a portrait click, gotoArea). Pull the
  // owning state along, or the header would say Flow while the map is on screen.
  if (real === "map" && mode === "flow") mode = "view";
  if (real === "flow" && mode !== "flow") mode = "flow";
  for (const spec of Object.values(GROUPS))
    for (const [k, v] of Object.entries(spec.panes)) if (v === real) spec.at = k;
  // SCOPED to the top strip. `.mode-switch` is also the class on #area-tabs, #subset-switch and
  // every .sub-switch strip — those are STYLING, not membership. Unscoped, this line matched
  // every one of them, and since their buttons have no data-mode it compared `undefined === mode`:
  // clicking a left-panel tab lit up all three subsets at once and dropped the top strip's
  // highlight entirely. That is the "nothing tells me what is active" break.
  document.querySelectorAll("#mode-switch button").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  // One strip is repeated in every section of its group (their layouts differ, so they stay
  // separate sections). All of them are re-asserted from GROUPS rather than from the click that
  // moved them, which is what kept the six old copies from drifting apart — one of them had lost a
  // button entirely and nobody could see it from the code that wired them.
  document.querySelectorAll(".sub-switch").forEach((s) => {
    const g = GROUPS[s.dataset.group];
    if (g) s.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.pane === g.at));
  });
  // The three strips are ONE state, so they are re-asserted from that state on every pass rather
  // than only in their own click handler — which is what let them drift apart in the first place.
  $("area-tabs").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.ap === areaTab));
  $("subset-switch").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.subset === subset));
  $("subset-switch").hidden = mode !== "edit";
  document.querySelectorAll(".tab").forEach((s) => s.classList.toggle("active", s.id === `tab-${real}`));
  const onAreas = real === "map" || real === "flow";
  $("areas-bar").hidden = !onAreas;                    // one row, shown for every view of an area
  $("areas-map-tail").hidden = !onAreas || mode === "flow";
  $("areas-flow-tail").hidden = mode !== "flow";
  const top = TOP_OF[real] || real;
  $("tabs").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.tab === top));
  if (real === "blocks") blInit();
  if (real === "patterns") kitInit();
  if (real === "tilesets" && !tsAtlas) tsInit();
  if (real === "flow") flowInit();
  if (real === "portraits" && !ptLoaded) ptInit();
  if (real === "gfx" && !gfxLoaded.title) gfxInit();
  if (real === "endings" && !gfxLoaded.endings) endingsInit();
  if (real === "overview" && !ovBuilt) overviewInit();
  if (real === "quests") qInit();
  if (real === "party") paInit();
  if (real === "blueprints") bpwInit();
  if (real === "design") dqInit();
}
// Show one pane of a group. Everything else — the strips, the top button, the lazy init — is
// setTab's job, so there is exactly one path onto a section however you got there.
function setPane(group, pane) {
  const g = GROUPS[group];
  if (!g || !g.panes[pane]) return;
  g.at = pane;
  setTab(group);
}

// The PLAY tab (embedded trx64 emulator) and the "Build & Play" button lived here in the
// cartridge project. Both are gone in the standalone editor: this repo edits data, it does
// not build or run a cartridge.
// ═══════════════════════════════════════════════════════════════════
//  MAP TAB
// ═══════════════════════════════════════════════════════════════════
const canvas = $("map"), ctx = canvas.getContext("2d");
let area = null, mapAtlas = null, hover = null, curSide = 1, curAid = 0;
const ZOOM_MIN = 4, ZOOM_MAX = 48, ZOOM_DEFAULT = 32;   // every area opens at 32
let mode = "view", brush = 0, dirty = false, painting = false, zoom = ZOOM_DEFAULT;
let mutations = [], selMut = null, incoming = {}, chains = {};   // incoming/chains keyed "col,row"
let scriptCells = new Set();   // "col,row" of cells that carry real logic (chain / mutation / event)

// engine display overrides (OP4_OBJECTS.md): op5→loot sack, op9→tile 8 (night form),
// op4→the OBJECT tile from its grp4 record (byte1 & $7F) — the "intact" look drawn over
// the (often destroyed) map tile. Fallback = the raw map tile (dangling/blank areas).
function displayTile(op, mapTile, cid) {
  if (op === 0x5) return 5;
  if (op === 0x9) return 8;
  if (op === 0x4 && area && area.op4_tiles && area.op4_tiles[cid] != null) return area.op4_tiles[cid];
  return mapTile;
}
function setDirty(d) { dirty = d; $("dirty").hidden = !d; $("save-btn").disabled = !d; }

// largest EVEN zoom that fits the map WIDTH in the pane (height scrolls). Even only —
// odd tile sizes make pixels uneven; 32 is the comfortable default.
function fitZoom() {
  if (!area) return;
  const pane = $("map-pane");
  const availW = (pane.clientWidth || window.innerWidth * 0.72) - 4;
  let z = Math.floor(availW / area.width);
  z = z - (z % 2);                                    // round down to even
  z = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, z));
  zoom = z; $("zoom-val").textContent = zoom; drawMap();
}
// Every area opens at the SAME zoom, whatever its size — a 64×64 overworld used to auto-shrink to
// 26 while a town stayed at 32, so tiles changed size as you switched areas and the eye had to
// recalibrate every time. The pane scrolls, which is the cheaper price. "fit" stays as a manual button.
function resetZoom() {
  if (!area) return;
  zoom = ZOOM_DEFAULT;
  $("zoom-val").textContent = zoom;
  drawMap();
}

function drawMap() {
  if (!area) return;
  const { width, height, tileids } = area, cell = zoom;
  canvas.width = width * cell; canvas.height = height * cell;
  ctx.imageSmoothingEnabled = false;
  const showOps = $("show-transitions").checked;
  const showHooks = $("show-hooks").checked;
  for (let row = 0; row < height; row++) for (let col = 0; col < width; col++) {
    const i = row * width + col, op = area.nibble[i];
    if (mapAtlas) blit(ctx, mapAtlas, displayTile(op, tileids[i], area.cellid[i]), col * cell, row * cell, cell, cell);
    else { ctx.fillStyle = "#101318"; ctx.fillRect(col * cell, row * cell, cell, cell); }
    if (op !== 0 && OP_COLOR[op] && showOps) {
      ctx.fillStyle = OP_COLOR[op];
      const mk = Math.max(2, (cell / 4) | 0);
      ctx.fillRect(col * cell + 1, row * cell + 1, mk, mk);
    }
    // script-hook badge: a cell that carries real logic (chain / mutation / event). A bright
    // bolt-corner in the TOP-RIGHT, distinct from the op-colour dot (top-left).
    if (showHooks && scriptCells.has(`${col},${row}`)) {
      const x = col * cell, y = row * cell, s2 = Math.max(3, (cell / 3) | 0);
      ctx.fillStyle = "#ffd23c";
      ctx.beginPath();
      ctx.moveTo(x + cell, y); ctx.lineTo(x + cell - s2, y); ctx.lineTo(x + cell, y + s2);
      ctx.closePath(); ctx.fill();
      if (cell >= 16) { ctx.fillStyle = "#191919"; ctx.font = `${Math.floor(s2*0.9)}px monospace`;
        ctx.textAlign = "right"; ctx.textBaseline = "top"; ctx.fillText("⚡", x + cell - 1, y); }
    }
  }
  // selection marquee
  if (mode === "edit" && paintTool === "select" && selRect) {
    const c0 = Math.min(selRect.a.col, selRect.b.col), r0 = Math.min(selRect.a.row, selRect.b.row);
    const w = Math.abs(selRect.a.col - selRect.b.col) + 1, h = Math.abs(selRect.a.row - selRect.b.row) + 1;
    ctx.strokeStyle = "#5aa9e6"; ctx.lineWidth = 2; ctx.strokeRect(c0 * cell + 1, r0 * cell + 1, w * cell - 2, h * cell - 2);
  }
  // stamp preview under the cursor (brush tool)
  if (mode === "edit" && paintTool === "brush" && stamp && hover) {
    ctx.globalAlpha = 0.55;
    for (let y = 0; y < stamp.h; y++) for (let x = 0; x < stamp.w; x++) if (mapAtlas) blit(ctx, mapAtlas, stamp.tiles[y * stamp.w + x], (hover.col + x) * cell, (hover.row + y) * cell, cell, cell);
    ctx.globalAlpha = 1;
  }
  if ($("show-mutations").checked) drawMutations(cell);
  if ($("show-solid").checked || activeSubset() === "physique") drawSolidOverlay(cell);
  if (showOps) drawNpcMarkers(cell);
  if (hover) { ctx.strokeStyle = mode === "edit" ? "#e6a24e" : "#fff"; ctx.lineWidth = 1; ctx.strokeRect(hover.col * cell + 0.5, hover.row * cell + 0.5, cell - 1, cell - 1); }
  drawReadout();
}

// The cursor's cell, in the control bar, always. It rides on `hover` — the same variable the white
// outline is drawn from — so the number and the marked square can never disagree, and leaving the
// map keeps the last cell instead of blanking: coordinates are what you copy into a teleport
// target or an encounter, and they have to survive the trip to the panel.
function drawReadout() {
  const el = $("cell-readout"); if (!el) return;
  el.textContent = hover ? `(${hover.col}, ${hover.row})` : "—";
}

// NPC spawn markers (grp17 hireables) — runtime overlays, NOT tiles ([[NPC_SPAWN.md]]).
// Green ring + name on the op-$3/$F marker cell the NPC stands on.
let areaNpcs = [];
function drawNpcMarkers(cell) {
  for (const n of areaNpcs) for (const c of n.cells) {
    const x = c[0] * cell, y = c[1] * cell;
    if (n.glyph != null && mapAtlas) blit(ctx, mapAtlas, n.glyph, x, y, cell, cell);   // the engine draws foe-table byte6 on the spawn cell
    ctx.strokeStyle = "#4ade80"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(x + cell / 2, y + cell / 2, cell * 0.38, 0, Math.PI * 2); ctx.stroke();
    if (cell >= 16) {
      ctx.font = `${Math.max(9, cell / 3 | 0)}px sans-serif`;
      ctx.fillStyle = "#4ade80"; ctx.textAlign = "center";
      ctx.fillText(n.name, x + cell / 2, y - 2 < 8 ? y + cell + 10 : y - 2);
      ctx.textAlign = "start";
    }
  }
}
async function loadHireables(side, id) {
  areaNpcs = [];
  try { areaNpcs = (await (await fetch(`/api/area/${side}/${id}/hireables`)).json()).npcs || []; } catch (e) {}
  const box = $("hire-list");
  if (box) {
    box.innerHTML = areaNpcs.length ? "" : `<span class="muted small">none in this area</span>`;
    for (const n of areaNpcs) {
      const t = document.createElement("div"); t.className = "npc-thumb";
      t.title = `grp17 #${n.grp17_index} · ST/IQ/LK/SP/AGL/DEX/CHR ${n.stats.join("/")} · MAXCON ${n.maxcon}` +
                (n.hireable ? "" : " · NOT flagged hireable") + " — click to edit";
      t.innerHTML = (n.portrait_str != null ? `<img src="/api/portrait/${n.side}/${n.portrait_str}" onerror="this.remove()">` : "") +
                    `<span>${n.name}<br><span class="muted">(${n.cells.map((c) => c.join(",")).join(" · ")})</span></span>`;
      t.addEventListener("click", () => { if (n.cells[0]) inspectCell(n.cells[0][0], n.cells[0][1]); npcPanel(n); drawMap(); });
      box.appendChild(t);
    }
  }
  drawMap();
}

// ── NPC editor panel (AREA Data tab) — create/edit via the add_npc/update_npc backend ──
let pickCellCb = null;   // armed by "set position": next map click lands here

function npcPanel(n) {
  // n = existing NPC object from areaNpcs, or null = create-new form
  const box = $("npc-edit");
  const v = n || { name: "", stats: [15, 15, 15, 15, 15, 15, 15], maxcon: 30, money: 200,
                   portrait_str: 79, glyph_tile: 4, hireable: false, join_str: 0, hire_gate: 0,
                   rank: "", cells: [] };
  const glyph = n ? n.glyph : v.glyph_tile;
  const statL = ["ST", "IQ", "LK", "SP", "AGL", "DEX", "CHR"];
  box.innerHTML = `<div class="insp-sec ef-box">
    <div class="insp-title">${n ? `✎ NPC — ${v.name} (grp17 #${n.grp17_index})` : "＋ new NPC"}</div>
    <table class="ef-tbl">
      <tr><td>name</td><td><input id="ne-name" maxlength="13" value="${v.name}"></td></tr>
      <tr><td>portrait</td><td><input id="ne-port" type="number" min="0" max="127" value="${v.portrait_str ?? 79}">
        <img id="ne-port-img" src="/api/portrait/1/${v.portrait_str ?? 79}" style="height:38px;vertical-align:middle;image-rendering:pixelated" onerror="this.style.opacity=.2"></td></tr>
      <tr><td>figure tile</td><td><input id="ne-glyph" type="number" min="1" max="96" value="${glyph ?? 4}"> <span class="ef-hint">tile in the AREA's tileset</span></td></tr>
      <tr><td>stats</td><td>${statL.map((l, i) => `<input class="ne-stat" data-i="${i}" type="number" min="1" max="99" value="${v.stats[i] ?? 15}" title="${l}" style="width:34px">`).join(" ")}</td></tr>
      <tr><td>maxcon</td><td><input id="ne-con" type="number" min="1" max="9999" value="${v.maxcon}"></td></tr>
      <tr><td>money</td><td><input id="ne-money" type="number" min="0" max="16000000" value="${v.money ?? 200}"></td></tr>
      <tr><td>rank</td><td><input id="ne-rank" maxlength="24" value="${v.rank ?? ""}"></td></tr>
      <tr><td>talk/join text</td><td><input id="ne-join" type="number" min="0" max="127" value="${v.join_str ?? 0}"> <span class="ef-hint" id="ne-join-txt"></span></td></tr>
      <tr><td>hireable</td><td><input id="ne-hire" type="checkbox" ${v.hireable ? "checked" : ""}> <span class="ef-hint">off = info NPC</span></td></tr>
      <tr><td>position</td><td><span id="ne-pos">${n && n.cells.length ? `(${n.cells[0].join(",")})` : "—"}</span>
        <button class="mini" id="ne-pick">${n ? "move" : "set"} — click the map</button></td></tr>
    </table>
    <div class="btns">
      <button id="ne-save">${n ? "Save NPC" : "Create NPC"}</button>
      ${n ? `<button id="ne-del" title="removes the marker cell — the figure disappears">Delete</button>` : ""}
      <button id="ne-close">Close</button>
    </div></div>`;
  let pos = n && n.cells.length ? [...n.cells[0]] : null;
  let posChanged = false;
  $("ne-port").addEventListener("input", () => { $("ne-port-img").src = `/api/portrait/1/${+$("ne-port").value}`; });
  $("ne-join").addEventListener("change", async () => {
    const d = await (await fetch(`/api/area/${curSide}/${curAid}/texts`)).json();
    const m = d.messages[+$("ne-join").value];
    $("ne-join-txt").textContent = m ? `"${m.text.trim().slice(0, 60)}"` : "(no such text — see Text tab)";
  });
  $("ne-pick").addEventListener("click", () => {
    status("click a map cell to place the NPC…");
    pickCellCb = (col, row) => { pos = [col, row]; posChanged = true; $("ne-pos").textContent = `(${col},${row})`; status(`position: (${col},${row})`); };
  });
  $("ne-close").addEventListener("click", () => { box.innerHTML = ""; pickCellCb = null; });
  if (n) {
    $("ne-del").addEventListener("click", async () => {
      if (!confirm(`Delete NPC ${v.name} (marker cell cleared)?`)) return;
      await flushLayers();
      const r = await fetch(`/api/area/${curSide}/${curAid}/npc/${n.grp17_index}`, { method: "DELETE" });
      status(r.ok ? `NPC ${v.name} removed` : `delete err ${r.status}`);
      box.innerHTML = ""; await loadArea(curSide, curAid);
    });
  }
  $("ne-save").addEventListener("click", async () => {
    const body = {
      name: $("ne-name").value.trim(),
      portrait_str: +$("ne-port").value,
      glyph_tile: +$("ne-glyph").value,
      stats: [...box.querySelectorAll(".ne-stat")].map((el) => +el.value),
      maxcon: +$("ne-con").value,
      money: +$("ne-money").value,
      rank: $("ne-rank").value,
      join_str: +$("ne-join").value,
      hireable: $("ne-hire").checked,
    };
    if (!n && !pos) { status("set a position first (click the map)"); return; }
    if (!n || posChanged) body.cell = pos;
    if (!body.name) { status("name?"); return; }
    await flushLayers();     // persist pending map/physique paint before the record rewrite reloads
    const url = n ? `/api/area/${curSide}/${curAid}/npc/${n.grp17_index}` : `/api/area/${curSide}/${curAid}/npc`;
    const r = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) { status(`NPC save err ${r.status}: ${(await r.json()).detail || ""}`); return; }
    status(n ? `NPC ${body.name} saved` : `NPC ${body.name} created`);
    box.innerHTML = ""; pickCellCb = null;
    await loadArea(curSide, curAid);
  });
}

// solidity overlay — translucent red on cells the engine blocks (op $3/$4/$B/$F). Feeds the
// Physique view + the "solid overlay" toggle. See [[wasteland-physics-op-nibble]].
function drawSolidOverlay(cell) {
  ctx.fillStyle = "#e6635a55";
  for (let row = 0; row < area.height; row++) for (let col = 0; col < area.width; col++) {
    if (SOLID_OPS.has(area.nibble[row * area.width + col])) ctx.fillRect(col * cell, row * cell, cell, cell);
  }
}

// scripted-change overlay: mark source cells; op-C draws an arrow to each target, op-2 an in-place ⟳.
function drawMutations(cell) {
  const mid = (c) => [c[0] * cell + cell / 2, c[1] * cell + cell / 2];
  for (const m of mutations) {
    const isSel = m === selMut;
    const col = m.code ? "#e6635a" : m.op === 12 ? "#5ae0d0" : "#e6c15a";
    const [sx, sy] = mid(m.source);
    ctx.lineWidth = isSel ? 3 : 1.5;
    ctx.strokeStyle = col; ctx.fillStyle = col;
    ctx.strokeRect(m.source[0] * cell + 1, m.source[1] * cell + 1, cell - 2, cell - 2);
    for (const t of m.targets || []) {
      const [tx, ty] = mid(t.cell);
      if (t.cell[0] === m.source[0] && t.cell[1] === m.source[1]) {   // in-place (op-2)
        ctx.beginPath(); ctx.arc(sx, sy, Math.max(2, cell / 4), 0.3, Math.PI * 1.7); ctx.stroke();
      } else {                                                        // arrow source→target
        arrow(sx, sy, tx, ty, cell);
        ctx.globalAlpha = 0.7; ctx.strokeRect(t.cell[0] * cell + 2, t.cell[1] * cell + 2, cell - 4, cell - 4); ctx.globalAlpha = 1;
      }
    }
  }
}
function arrow(x0, y0, x1, y1, cell) {
  ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
  const a = Math.atan2(y1 - y0, x1 - x0), h = Math.max(4, cell / 3);
  ctx.beginPath(); ctx.moveTo(x1, y1);
  ctx.lineTo(x1 - h * Math.cos(a - 0.4), y1 - h * Math.sin(a - 0.4));
  ctx.lineTo(x1 - h * Math.cos(a + 0.4), y1 - h * Math.sin(a + 0.4));
  ctx.closePath(); ctx.fill();
}

async function loadMutations(side, id) {
  mutations = []; selMut = null; incoming = {};
  try {
    const j = await (await fetch(`/api/area/${side}/${id}/mutations`)).json();
    mutations = j.mutations || []; incoming = j.incoming || {}; chains = j.chains || {};
  } catch {}
  scriptCells = new Set();
  for (const k of Object.keys(chains)) if ((chains[k] || []).length > 1) scriptCells.add(k);
  for (const m of mutations) {
    if (m.targets && m.targets.length) scriptCells.add(`${m.source[0]},${m.source[1]}`);
    for (const t of m.targets || []) if (t.cell) scriptCells.add(`${t.cell[0]},${t.cell[1]}`);
  }
  // enrich grp16 code-driven entries with the hand-written effect notes (Code tab)
  try {
    const notes = (await (await fetch(`/api/area/${side}/${id}/grp16-notes`)).json()).notes || {};
    for (const m of mutations) if (m.code && m.event != null && notes[m.event])
      m.trigger = `grp16 event ${m.event} — ${notes[m.event]}`;
  } catch {}
  renderMutList();
}
function renderMutList() {
  const box = $("mut-list"); $("mut-count").textContent = mutations.length ? mutations.length : "";
  if (!mutations.length) { box.innerHTML = `<span class="muted">no scripted map changes</span>`; box.classList.add("muted"); return; }
  box.classList.remove("muted");
  box.innerHTML = mutations.map((m, k) => {
    const tag = m.code ? "grp16 code" : m.op === 12 ? "CELL_PATCH" : "COND";
    const tgts = (m.targets || []).map((t) => `(${t.cell[0]},${t.cell[1]}) ${t.label}`).join("<br>&nbsp;&nbsp;") || (m.code ? "runtime-driven (not enumerable)" : "");
    return `<div class="mut ${m === selMut ? "sel" : ""}" data-k="${k}">
      <b>(${m.source[0]},${m.source[1]})</b> <span class="mut-tag">${tag}</span><br>
      <span class="muted">${m.trigger}</span><br>&nbsp;&nbsp;${tgts}</div>`;
  }).join("");
  box.querySelectorAll(".mut").forEach((el) => el.addEventListener("click", () => {
    selMut = mutations[+el.dataset.k]; renderMutList();
    if (!$("show-mutations").checked) { $("show-mutations").checked = true; }
    hover = { col: selMut.source[0], row: selMut.source[1] };
    inspectCell(selMut.source[0], selMut.source[1]); drawMap();
  }));
}

function renderLegend() {
  const seen = new Set(area.nibble.filter((n) => n !== 0));
  $("legend").innerHTML = [...seen].sort((a, b) => a - b).map((op) =>
    `<li><span class="sw" style="background:${OP_COLOR[op] || "#3a4152"}"></span>$${op.toString(16).toUpperCase()} ${OP_NAMES[op] || "?"}</li>`).join("") || '<li class="muted">none</li>';
}

function renderPaintPalette() {
  const pal = $("paint-palette"); pal.innerHTML = "";
  const used = new Set(area.tileids);
  for (let tid = 0; tid < (mapAtlas ? mapAtlas.num_tiles : 0); tid++) {
    const c = document.createElement("canvas"); c.width = c.height = 32; c.className = "tile";
    c.dataset.tid = tid; c.title = `tile ${tid}${used.has(tid) ? " (in use)" : ""}`;
    blit(c.getContext("2d"), mapAtlas, tid, 0, 0, 32, 32);
    if (used.has(tid)) c.classList.add("used");
    c.addEventListener("click", () => { brush = tid; $("brush-label").textContent = `tile ${tid}`; pal.querySelectorAll(".tile").forEach((e) => e.classList.toggle("sel", +e.dataset.tid === tid)); });
    pal.appendChild(c);
  }
}

function cellAt(e) {
  const r = canvas.getBoundingClientRect();
  const col = ((e.clientX - r.left) / zoom) | 0, row = ((e.clientY - r.top) / zoom) | 0;
  return (col < 0 || row < 0 || col >= area.width || row >= area.height) ? null : { col, row };
}

// ── paint tools ──
let paintTool = "brush", stamp = null, lastPaint = null, selStart = null, selRect = null;
function setTool(t) {
  paintTool = t;
  $("paint-tools").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.tool === t));
  canvas.style.cursor = t === "fill" ? "cell" : t === "select" ? "crosshair" : "cell";
}
function setTile(col, row, tid) {
  const i = row * area.width + col;
  if (col < 0 || row < 0 || col >= area.width || row >= area.height) return;
  if (area.tileids[i] !== tid) { area.tileids[i] = tid; setDirty(true); }
}
function stampAt(col, row) {
  if (stamp) { for (let y = 0; y < stamp.h; y++) for (let x = 0; x < stamp.w; x++) setTile(col + x, row + y, stamp.tiles[y * stamp.w + x]); }
  else setTile(col, row, brush);
}
function lineTo(c) {                        // Bresenham from lastPaint to c
  let { col: x0, row: y0 } = lastPaint, { col: x1, row: y1 } = c;
  const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0), sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
  let err = dx + dy;
  for (;;) { stampAt(x0, y0); if (x0 === x1 && y0 === y1) break; const e2 = 2 * err; if (e2 >= dy) { err += dy; x0 += sx; } if (e2 <= dx) { err += dx; y0 += sy; } }
}
function floodFill(col, row, target) {
  const i0 = row * area.width + col, from = area.tileids[i0];
  if (from === target) return;
  const st = [[col, row]];
  while (st.length) {
    const [x, y] = st.pop();
    if (x < 0 || y < 0 || x >= area.width || y >= area.height) continue;
    if (area.tileids[y * area.width + x] !== from) continue;
    area.tileids[y * area.width + x] = target;
    st.push([x + 1, y], [x - 1, y], [x, y + 1], [x, y - 1]);
  }
  setDirty(true);
}
function captureSelection(a, b) {
  const c0 = Math.min(a.col, b.col), r0 = Math.min(a.row, b.row), c1 = Math.max(a.col, b.col), r1 = Math.max(a.row, b.row);
  const w = c1 - c0 + 1, h = r1 - r0 + 1, tiles = [];
  for (let y = r0; y <= r1; y++) for (let x = c0; x <= c1; x++) tiles.push(area.tileids[y * area.width + x]);
  stamp = { w, h, tiles };
  // The same rectangle feeds two different things: a tile STAMP (pixels only, local, instant) and a
  // BLUEPRINT (the whole object — cells, records, texts, foes). Keeping the coordinates is what
  // lets the second one be a separate, deliberate act rather than a mode you have to choose first.
  bpSel = { c0, r0, c1, r1 };
  $("stamp-info").textContent = `stamp: ${w}×${h} (click to place · ⌫ clears)`;
  const sb = $("bp-snap");
  if (sb) { sb.disabled = false; sb.textContent = `⧉ Snap selection (${w}×${h})`; }
  setTool("brush");
}

// ═══════════════ BLUEPRINTS — a region as a re-usable object ═══════════════
// A stamp carries tiles. A blueprint carries the OBJECT: the selected cells, the records they
// dispatch, and what those records name (texts, tiles, foes, hireables). The backend decides what
// is OWNED (travels) and what is SHARED (declared as a requirement) — see wl_object.py.
let bpSel = null, bpPlacing = null;

const bpEsc = (t) => String(t ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;");

function bpParts(s) {
  const p = [];
  if (s.cells) p.push(`${s.cells} cells`);
  if (s.records) p.push(`${s.records} records`);
  if (s.text) p.push(`${s.text} texts`);
  if (s.tiles) p.push(`${s.tiles} tiles`);
  if (s.foes) p.push(`${s.foes} foes`);
  if (s.npcs) p.push(`${s.npcs} NPCs`);
  return p.join(" · ") || "nothing";
}

function bpRequires(req) {
  const r = req || {}, out = [];
  if (r.records && r.records.length) out.push(`references it does not carry: ${r.records.join(", ")}`);
  if (r.portraits && r.portraits.length) out.push(`portraits: ${r.portraits.join(", ")}`);
  if (r.grp16_events && r.grp16_events.length) out.push(`grp16 handlers for event ${r.grp16_events.join(", ")}`);
  if (r.areas && r.areas.length) out.push(`doors leading to area ${r.areas.join(", ")}`);
  return out;
}

async function bpSnap() {
  if (!bpSel) return;
  const [side, id] = $("area-select").value.split(":").map(Number);
  const name = prompt("Name this blueprint:", `area ${id} ${bpSel.c0},${bpSel.r0}`);
  if (!name) return;
  const box = $("bp-cut"); box.hidden = false; box.innerHTML = `<div class="muted small">snapping…</div>`;
  const r = await fetch("/api/blueprint/cut", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ side, area_id: id, ...bpSel, name, save: true }),
  });
  if (!r.ok) { box.innerHTML = `<div class="bp-bad">${bpEsc((await r.json()).detail || r.status)}</div>`; return; }
  const d = await r.json();
  const s = d.summary;
  let h = `<div class="bp-cut-head"><b>${bpEsc(s.name)}</b> — ${s.size.w}×${s.size.h}, ${bpParts(s)}</div>`;
  const req = bpRequires(s.requires);
  if (req.length) h += `<div class="bp-req">needs: ${req.map(bpEsc).join(" · ")}</div>`;
  (s.notes || []).forEach((n) => { h += `<div class="bp-note">${bpEsc(n)}</div>`; });
  if (d.saved) h += `<div class="muted small">saved to <code>${bpEsc(d.saved)}</code></div>`;
  box.innerHTML = h;
  await bpLoad();
}

async function bpLoad() {
  const box = $("bp-list");
  if (!box) return;
  const r = await fetch("/api/blueprints");
  if (!r.ok) { box.innerHTML = `<div class="muted small">err ${r.status}</div>`; return; }
  const { blueprints } = await r.json();
  if (!blueprints.length) { box.innerHTML = `<div class="muted small">the library is empty — select a region and snap it</div>`; return; }
  box.innerHTML = blueprints.map((b) => {
    if (b.error) return `<div class="bp-row bp-bad">${bpEsc(b.name)}: ${bpEsc(b.error)}</div>`;
    const req = bpRequires(b.requires);
    return `<div class="bp-row">
      <div class="bp-name"><b>${bpEsc(b.name)}</b> <span class="muted small">${b.size.w}×${b.size.h}</span></div>
      <div class="muted small">${bpParts(b)}${b.source && b.source.area != null ? ` · from area ${b.source.area}` : ""}</div>
      ${req.length ? `<div class="bp-req">needs: ${req.map(bpEsc).join(" · ")}</div>` : ""}
      <div class="btns">
        <button class="mini bp-place" data-n="${bpEsc(b.name)}">place…</button>
        <button class="mini bp-del" data-n="${bpEsc(b.name)}">delete</button>
      </div></div>`;
  }).join("");
  box.querySelectorAll(".bp-place").forEach((el) => el.addEventListener("click", () => bpArm(el.dataset.n)));
  box.querySelectorAll(".bp-del").forEach((el) => el.addEventListener("click", async () => {
    if (!confirm(`Delete blueprint "${el.dataset.n}"?`)) return;
    await fetch(`/api/blueprint/${encodeURIComponent(el.dataset.n)}`, { method: "DELETE" });
    await bpLoad();
  }));
}

async function bpArm(name) {
  const [side, id] = $("area-select").value.split(":").map(Number);
  const rep = $("bp-report");
  // Ask what would NOT bind BEFORE arming the click, so the answer arrives while there is still a
  // decision to make — after the placement it is only an explanation.
  const c = await fetch(`/api/blueprint/${encodeURIComponent(name)}/check`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ side, area_id: id, col: 0, row: 0 }),
  });
  if (c.ok) {
    const d = await c.json();
    rep.innerHTML = d.blocked.length
      ? `<div class="bp-req">placing here leaves open: ${d.blocked.map(bpEsc).join(" · ")}</div>`
      : `<div class="muted small">everything this blueprint needs is available in area ${id}.</div>`;
  }
  bpPlacing = name;
  status(`place "${name}": click the map for its top-left corner (Esc cancels)`);
  pickCellCb = async (col, row) => {
    bpPlacing = null;
    status(`placing "${name}" at (${col},${row})…`);
    const r = await fetch(`/api/blueprint/${encodeURIComponent(name)}/place`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ side, area_id: id, col, row }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      rep.innerHTML = `<div class="bp-bad">${bpEsc(d.detail || `error ${r.status}`)}</div>`;
      status(`could not place "${name}"`);
      return;
    }
    const rp = d.report || {};
    const moved = Object.entries(rp.records || {}).map(([a, b]) => `${a}→${b}`).join(", ");
    let h = `<div><b>placed</b> at (${col},${row}) — ${rp.bytes} B record (${rp.delta >= 0 ? "+" : ""}${rp.delta})`;
    if (d.gfx_written) h += ", tileset updated";
    if (d.map_written) h += ", map updated";
    h += `</div>`;
    if (moved) h += `<div class="muted small">records: ${bpEsc(moved)}</div>`;
    (rp.warnings || []).forEach((w) => { h += `<div class="bp-req">${bpEsc(w)}</div>`; });
    rep.innerHTML = h;
    await loadArea(side, id);
    status(`placed "${name}" at (${col},${row})`);
  };
}

// ═══════════════ PARTY — the four rangers a new game starts with ═══════════════
// Ordinary data with a known layout, so it is edited here rather than by building a blob by hand
// and pointing WL_PARTY_BLOB at it. Only the fields you touch are written; the rest of each
// 256-byte record is left alone, including everything nobody has identified yet.
let paData = null, paName = null, paSel = null, paInited = false;

function paInit() { if (!paInited) { paInited = true; paLoadList(); } }

async function paLoadList() {
  const r = await fetch("/api/parties");
  if (!r.ok) { $("pa-list").innerHTML = `<div class="bp-bad">err ${r.status}</div>`; return; }
  const d = await r.json();
  const sel = $("pa-select");
  paSel = d.selected;
  sel.innerHTML = d.parties.map((p) =>
    `<option value="${bpEsc(p.name)}"${p.plain ? "" : " disabled"}>${p.name === d.selected ? "★ " : ""}${bpEsc(p.name)}` +
    `${p.plain ? ` — ${p.members} rangers` : " — scrambled, not editable"}</option>`).join("");
  paName = paName || d.selected || d.default;
  sel.value = paName;
  await paLoad();
}

async function paLoad() {
  paName = $("pa-select").value;
  const r = await fetch(`/api/party/${encodeURIComponent(paName)}`);
  if (!r.ok) { $("pa-list").innerHTML = `<div class="bp-bad">err ${r.status}</div>`; return; }
  paData = await r.json();
  // "which party does the CART actually ship" is the question this tab exists to answer, so it is
  // the headline, and picking is one click rather than an environment variable.
  $("pa-info").innerHTML = `${paData.bytes} B · ${paData.origin}` + (paName === paSel
    ? ` · <b class="pa-ship">★ this is the party the build ships</b>`
    : ` · <button id="pa-pick" class="mini">make default</button>`);
  const pick = $("pa-pick");
  if (pick) pick.addEventListener("click", async () => {
    const r = await fetch("/api/party/selected", {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ blob: paName }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { alert(d.detail || `error ${r.status}`); return; }
    status(`the build now ships ${paName}: ${(d.names || []).join(", ")}`);
    await paLoadList();
  });
  $("pa-problems").innerHTML = (paData.problems || []).length
    ? `<div class="bp-req">${paData.problems.map(bpEsc).join("<br>")}</div>` : "";
  await paCatsLoad();
  paRender();
  paStartUI();
}

// New-game start: area from the name list + click-the-map coordinate picker (record-0
// bytes +$08/09/0A + mirror + $7F header; engine reads them via ($68)=$F400 at $9C2A).
// NOTE: an engine-side writer still overrides the position on boot (open bug) — the
// bytes are authored here so they are correct the moment that writer is fixed.
async function paStartUI() {
  let el = document.getElementById("pa-start");
  if (!el) {
    el = document.createElement("div");
    el.id = "pa-start";
    $("pa-list").after(el);
  }
  const [stR, arR] = await Promise.all([
    fetch(`/api/party/${encodeURIComponent(paName)}/start`),
    fetch("/api/areas"),
  ]);
  if (!stR.ok) { el.innerHTML = ""; return; }
  const st = await stR.json();
  const areas = (await arR.json()).areas.filter((a) => a.has_map);
  const SC = 6;
  el.innerHTML = `<div class="q-cap" style="margin-top:14px">new-game start</div>
    <div class="pa-card"><div class="pa-head">
      <label>Area <select id="pa-st-area">${areas.map((a) =>
        `<option value="${a.side}:${a.area_id}"${a.area_id === st.area ? " selected" : ""}>` +
        `${a.area_id} — ${bpEsc(a.name || "?")} (s${a.side})</option>`).join("")}</select></label>
      <label>X <input id="pa-st-x" type="number" min="0" max="63" value="${st.x}" style="width:62px"></label>
      <label>Y <input id="pa-st-y" type="number" min="0" max="63" value="${st.y}" style="width:62px"></label>
      <button id="pa-st-save" class="mini">save</button>
      <span class="muted small">click the map to set the start cell — used on NEW GAME (empty flash / Restart); Continue loads the newest flash save</span></div>
    <div style="position:relative;display:inline-block;margin-top:6px">
      <img id="pa-st-map" style="image-rendering:pixelated;cursor:crosshair;display:block;border:1px solid #333">
      <div id="pa-st-marker" style="position:absolute;width:9px;height:9px;border:2px solid #ff5252;border-radius:50%;pointer-events:none;transform:translate(-50%,-50%)"></div>
    </div></div>`;
  const cur = { area: st.area, x: st.x, y: st.y };
  const img = $("pa-st-map"), marker = $("pa-st-marker");
  const place = () => {
    marker.style.left = (cur.x + 0.5) * cellPx + "px";
    marker.style.top = (cur.y + 0.5) * cellPx + "px";
    $("pa-st-x").value = cur.x;
    $("pa-st-y").value = cur.y;
  };
  ["pa-st-x", "pa-st-y"].forEach((id) => $(id).addEventListener("change", () => {
    cur.x = Math.max(0, Math.min(63, +$("pa-st-x").value));
    cur.y = Math.max(0, Math.min(63, +$("pa-st-y").value));
    place();
  }));
  let cellPx = SC;
  const loadMap = () => {
    const [side, aid] = $("pa-st-area").value.split(":").map(Number);
    img.src = `/api/area/${side}/${aid}/render.png?scale=${SC}`;
    img.onload = () => {
      const w = img.naturalWidth;                 // Map-Breite * SC
      img.style.width = (w <= 200 ? w * 2 : w) + "px";   // 32er-Maps verdoppeln
      cellPx = parseInt(img.style.width) / (w / SC);
      place();
    };
    cur.area = aid;
  };
  $("pa-st-area").addEventListener("change", loadMap);
  img.addEventListener("click", (e) => {
    const r = img.getBoundingClientRect();
    cur.x = Math.floor((e.clientX - r.left) / cellPx);
    cur.y = Math.floor((e.clientY - r.top) / cellPx);
    place();
  });
  $("pa-st-save").addEventListener("click", async () => {
    const r = await fetch(`/api/party/${encodeURIComponent(paName)}/start`, {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify(cur),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { alert(d.detail || `error ${r.status}`); return; }
    status(`start: area ${cur.area} @ (${cur.x},${cur.y}) → ${d.saved}`);
  });
  loadMap();
}

// The seven rolled attributes. The two-letter forms are what fits above a number box, but "LK" and
// "CHR" only read as Luck and Charisma if you already know — so the full word is on the label, the
// same way the condition editor offers "Perception" rather than skill 9.
const PA_ATTRS = ["ST", "IQ", "LK", "SP", "AGL", "DEX", "CHR"];
const PA_ATTR_NAME = { ST: "Strength", IQ: "IQ", LK: "Luck", SP: "Speed",
                       AGL: "Agility", DEX: "Dexterity", CHR: "Charisma" };
const PA_NUM_NAME = { con: "Constitution — current health", maxcon: "Constitution — maximum",
                      money: "money carried", xp: "experience",
                      level: "level — the rank ladder ($24), 1 = Private",
                      ac: "armour class ($1A) — the engine caches it from the equipped armour" };

let paSlot = 1, paCats = null;

async function paCatsLoad() {
  if (paCats) return;
  const [it, sk] = await Promise.all([fetch("/api/items"), fetch("/api/skills")]);
  paCats = {
    items: (await it.json()).items.map((i) => ({ id: i.id, name: i.name })),
    skills: (await sk.json()).skills,
  };
}

function paOpt(list, sel) {
  return list.map((o) =>
    `<option value="${o.id}"${o.id === sel ? " selected" : ""}>${bpEsc(o.name)}</option>`).join("");
}

// Master-detail: the four rangers on the left, ONE ranger's editor on the right.
// Skills and inventory are LOOKUP PICKERS over the catalogs the repo already has
// (/api/items, /api/skills) — no raw ids to hand-type.
function paRender() {
  const box = $("pa-list");
  const live = paData.members.filter((m) => !m.empty && m.slot >= 1 && m.slot <= 4);
  if (!live.length) { box.innerHTML = `<div class="muted">no rangers in this blob</div>`; return; }
  if (!live.find((m) => m.slot === paSlot)) paSlot = live[0].slot;
  const m = live.find((x) => x.slot === paSlot);
  box.innerHTML = `<div class="pa-md">
    <div class="pa-master">${live.map((x) => `
      <div class="pa-mrow${x.slot === paSlot ? " on" : ""}" data-slot="${x.slot}">
        <b>${bpEsc(x.name)}</b>
        <span class="muted small">${bpEsc(x.rank)} · slot ${x.slot}</span>
        <span class="muted small">CON ${x.con}/${x.maxcon} · lvl ${x.level} · $${x.money}</span>
      </div>`).join("")}
    </div>
    <div class="pa-detail" data-slot="${m.slot}">
      <div class="pa-head">
        <input class="pa-name" value="${bpEsc(m.name)}" maxlength="14">
        <input class="pa-rank" value="${bpEsc(m.rank)}" maxlength="23" title="rank">
        <button class="mini pa-save">save</button>
      </div>
      <div class="q-cap">attributes</div>
      <div class="pa-attrs">${PA_ATTRS.map((k) =>
        `<label title="${PA_ATTR_NAME[k]}">${k}<input class="pa-at" data-k="${k}" type="number" min="0" max="255" value="${m.attrs[k]}"></label>`).join("")}</div>
      <div class="q-cap">health, money, rank</div>
      <div class="pa-nums">
        <label title="${PA_NUM_NAME.con}">CON<input class="pa-n" data-k="con" type="number" min="0" value="${m.con}"></label>
        <label title="${PA_NUM_NAME.maxcon}">max<input class="pa-n" data-k="maxcon" type="number" min="0" value="${m.maxcon}"></label>
        <label title="${PA_NUM_NAME.money}">$<input class="pa-n" data-k="money" type="number" min="0" value="${m.money}"></label>
        <label title="${PA_NUM_NAME.xp}">XP<input class="pa-n" data-k="xp" type="number" min="0" value="${m.xp}"></label>
        <label title="${PA_NUM_NAME.level}">lvl<input class="pa-n" data-k="level" type="number" min="0" max="255" value="${m.level}"></label>
        <label title="${PA_NUM_NAME.ac}">AC<input class="pa-n" data-k="ac" type="number" min="0" max="255" value="${m.ac}"></label>
      </div>
      <div class="pa-cols">
        <div><div class="q-cap">skills <button class="mini pa-add-sk">+ skill</button></div>
          <div class="pa-sk-rows">${m.skills.map((s2) => `
            <div class="pa-prow"><select class="pa-row-id">${paOpt(paCats.skills, s2.id)}</select>
              <input class="pa-row-n" type="number" min="0" max="255" value="${s2.n}" title="level">
              <button class="mini pa-del">×</button></div>`).join("")}</div></div>
        <div><div class="q-cap">inventory <button class="mini pa-add-iv">+ item</button></div>
          <div class="pa-iv-rows">${m.inventory.map((it) => `
            <div class="pa-prow"><select class="pa-row-id">${paOpt(paCats.items, it.id)}</select>
              <input class="pa-row-n" type="number" min="0" max="255" value="${it.n}" title="count/charges">
              <button class="mini pa-del">×</button></div>`).join("")}</div>
          <div class="pa-equip muted small">equipped — weapon:
            <select class="pa-eq" data-k="weapon">${paEquipOpts(m.inventory, m.weapon)}</select>
            armour: <select class="pa-eq" data-k="armour">${paEquipOpts(m.inventory, m.armour)}</select>
            <span title="equipped slots are 1-based indexes into the inventory ORDER above">ⓘ</span></div>
        </div>
      </div>
    </div></div>`;
  box.querySelectorAll(".pa-mrow").forEach((r) =>
    r.addEventListener("click", () => { paSlot = +r.dataset.slot; paRender(); }));
  const det = box.querySelector(".pa-detail");
  det.querySelector(".pa-save").addEventListener("click", () => paSave(det));
  det.querySelectorAll(".pa-del").forEach((b) =>
    b.addEventListener("click", () => b.closest(".pa-prow").remove()));
  const addRow = (wrapCls, list, defN) => {
    const div = document.createElement("div");
    div.className = "pa-prow";
    div.innerHTML = `<select class="pa-row-id">${paOpt(list, list[0].id)}</select>
      <input class="pa-row-n" type="number" min="0" max="255" value="${defN}">
      <button class="mini pa-del">×</button>`;
    div.querySelector(".pa-del").addEventListener("click", () => div.remove());
    det.querySelector(wrapCls).appendChild(div);
  };
  det.querySelector(".pa-add-sk").addEventListener("click", () => addRow(".pa-sk-rows", paCats.skills, 1));
  det.querySelector(".pa-add-iv").addEventListener("click", () => addRow(".pa-iv-rows", paCats.items, 0));
}

function paEquipOpts(inv, sel) {
  return `<option value="0"${sel === 0 ? " selected" : ""}>—</option>` + inv.map((it, i) =>
    `<option value="${i + 1}"${i + 1 === sel ? " selected" : ""}>${bpEsc(it.name)}</option>`).join("");
}

async function paSave(det) {
  const slot = +det.dataset.slot;
  const rows = (cls) => [...det.querySelectorAll(`${cls} .pa-prow`)].map((r) => ({
    id: +r.querySelector(".pa-row-id").value, n: +r.querySelector(".pa-row-n").value }));
  const body = {
    name: det.querySelector(".pa-name").value,
    rank: det.querySelector(".pa-rank").value,
    attrs: Object.fromEntries([...det.querySelectorAll(".pa-at")].map((i) => [i.dataset.k, +i.value])),
    skills: rows(".pa-sk-rows"),
    inventory: rows(".pa-iv-rows"),
  };
  det.querySelectorAll(".pa-n").forEach((i) => { body[i.dataset.k] = +i.value; });
  det.querySelectorAll(".pa-eq").forEach((i) => { body[i.dataset.k] = +i.value; });
  const r = await fetch(`/api/party/${encodeURIComponent(paName)}/member/${slot}`, {
    method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { alert(d.detail || `error ${r.status}`); return; }
  status(`${body.name}: ${d.changed} byte${d.changed === 1 ? "" : "s"} changed`);
  await paLoad();
}

// ═══════════════ QUESTS — the cross-area graph ═══════════════
// The game writes no quest down. What it has are two references that reach past the area they sit
// in — a global item id, and a keyword's WORD — and wl_quest derives every one of them. This view
// shows those links; the NAMES on top of them are authored (editor/annotations/quests.json) and
// are the only editable part. A link nobody has named still appears, under "ungrouped": the naming
// layer may organise the graph, never hide it.
let qData = null, qSel = null, qInited = false;

function qInit() { if (!qInited) { qInited = true; qLoad(); } }

// Quest ids appear in the Flow view before the Quests tab has ever been opened, so the name lookup
// pulls the list in the background rather than showing a slug.
let qNames = null;
function qName(id) {
  if (qNames === null) {
    qNames = {};
    fetch("/api/quests").then((r) => r.ok ? r.json() : null).then((d) => {
      if (!d) return;
      d.quests.forEach((q) => { qNames[q.id] = q.name; });
      if (mode === "flow") flowLoad();
    });
  }
  return bpEsc(qNames[id] || id);
}

async function qLoad(keep) {
  const r = await fetch("/api/quests");
  if (!r.ok) { $("q-list").innerHTML = `<div class="bp-bad">err ${r.status}</div>`; return; }
  qData = await r.json();
  const links = qData.quests.reduce((a, q) => a + q.links.length, 0);
  $("q-info").textContent = `${qData.quests.length} quests · ${links} links · ${qData.ungrouped.length} ungrouped`;
  qRenderList();
  if (keep || qSel) qOpen(keep || qSel);
}

function qRenderList() {
  let h = "", reg = null;
  // The MAIN LINE first, in dependency order. Wasteland has no quest object and no quest order —
  // but this one chain does, because it is the only one where somebody sends you onward: Brygo →
  // Charmaine → Needles → the sewers → Max → the Sleeper Base → Darwin → Cochise. Everything under
  // it you find rather than get told.
  const by = Object.fromEntries(qData.quests.map((q) => [q.id, q]));
  const main = (qData.main || []).map((id) => by[id]).filter(Boolean);
  if (main.length) {
    h += `<div class="q-region">Main line <span class="muted small">in order</span></div>`;
    let lvl = null;
    for (const q of main) {
      if (q.depth !== lvl) { lvl = q.depth; h += `<div class="q-stage">stage ${lvl}</div>`; }
      h += `<div class="q-row main ${q.id === qSel ? "on" : ""}" data-q="${bpEsc(q.id)}">
        <b>${bpEsc(q.name)}</b>
        <div class="muted small">${q.steps.length} steps${q.after.length
          ? ` · after ${q.after.map((x) => bpEsc((by[x] || {}).name || x)).join(", ")}` : ""}</div></div>`;
    }
    h += `<div class="q-region">Side quests</div>`;
  }
  for (const q of qData.quests) {
    if (q.track === "main") continue;
    if (q.region !== reg) { reg = q.region; h += `<div class="q-sub">${bpEsc(reg || "—")}</div>`; }
    const warn = q.missing.length ? `<span class="bp-req" title="named links the graph does not produce">⚠ ${q.missing.length}</span>` : "";
    h += `<div class="q-row ${q.id === qSel ? "on" : ""}" data-q="${bpEsc(q.id)}">
      <b>${bpEsc(q.name)}</b> ${warn}
      <div class="muted small">${q.links.length} link${q.links.length === 1 ? "" : "s"} · ${q.areas.length} areas</div></div>`;
  }
  if (qData.ungrouped.length) {
    h += `<div class="q-region">Ungrouped <span class="muted small">not part of any quest yet</span></div>`;
    h += qData.ungrouped.map((L) => `<div class="q-row q-un" data-l="${bpEsc(L.key)}">
      <b>${bpEsc(L.label)}</b> <span class="muted small">${L.kind}</span>
      <div class="muted small">${bpEsc(L.source_areas.join(", ") || "—")} → ${bpEsc(L.target_areas.join(", "))}</div></div>`).join("");
  }
  $("q-list").innerHTML = h;
  $("q-list").querySelectorAll(".q-row[data-q]").forEach((el) => el.addEventListener("click", () => qOpen(el.dataset.q)));
  $("q-list").querySelectorAll(".q-row[data-l]").forEach((el) => el.addEventListener("click", () => qLink(el.dataset.l)));
}

function qWhere(x) {
  // an area jump plus, where the record has one, the cell that carries it — a quest view you cannot
  // walk into is a report, not a tool
  const cells = (x.cells || []).slice(0, 3)
    .map((c) => `<a class="usage-link q-cell" data-side="${x.side}" data-area="${x.area}">(${c[0]},${c[1]})</a>`).join(" ");
  return `<a class="usage-link" data-side="${x.side}" data-area="${x.area}">${bpEsc(x.name)}</a>` +
    `<span class="muted small"> ${bpEsc(x.where || "")} ${cells}</span>`;
}

// A step renders from the RECORD every time, so rewriting a line or renaming a foe changes the
// quest text with it. What is authored is the ORDER and the note beside each step.
const STEP_DOT = { SAY: "#5aa9e6", KEYWORD: "#b98ce6", COND: "#e6c15a", ENCOUNTER: "#ef6a6a",
                   LOOT: "#7bd88f", PATCH: "#5ae0d0", TRANSITION: "#e6a24e", SERVICE: "#e6635a" };

function qStepHtml(st, i, n) {
  if (st.ok === false) {
    return `<div class="q-step broken"><span class="q-num">${i + 1}</span>
      <div><b>${bpEsc(st.ref)}</b><div class="bp-req">${bpEsc(st.why || "gone")}</div>
      ${st.note ? `<div class="q-note">${bpEsc(st.note)}</div>` : ""}</div>
      ${qStepTools(st, i, n)}</div>`;
  }
  // A designed beat that is not placed yet has NO record — `wl_quest.compose` resolves a step only
  // when it has a `ref`, so `ok` comes back null and there is no `text`, no `op`, no cell. It is a
  // plan, and it renders as one. Forcing it through the card below cost the whole pane: the throw
  // happened while building the template literal, so `#q-detail` was never assigned and every
  // button under it stayed unwired — one beat made the quest unopenable.
  if (st.ok == null) {
    const what = st.blueprint ? `<span class="muted small">${bpEsc(st.blueprint)}</span>` : "";
    const ph = st.phase === "cast" ? "filled in — not on a square yet"
             : st.phase === "sketch" ? "a sketch — no blueprint yet" : "not placed yet";
    return `<div class="q-step planned"><span class="q-num" style="background:#5a6472">${i + 1}</span>
      <div class="q-step-body">
        <div class="q-step-head"><b>${bpEsc(st.title || "new step")}</b> ${what}</div>
        <div class="muted small">${bpEsc(ph)} — open it in the Designer to finish it</div>
        ${st.note ? `<div class="q-note">${bpEsc(st.note)}</div>` : ""}
      </div>${qStepTools(st, i, n)}</div>`;
  }
  const facts = [];
  if (st.cells && st.cells.length) facts.push(st.cells.slice(0, 3)
    .map((c) => `<a class="usage-link" data-side="${st.side}" data-area="${st.area}" data-cell="${c[0]}:${c[1]}">(${c[0]},${c[1]})</a>`).join(" "));
  // A step with NO cell is not a step with no place — half of any chain is reached through another
  // record. Steps 3 and 4 of The Water Purifier are the two halves of a check: they showed nothing
  // at all where the honest answer is "by passing the gate at (28,16)".
  else if ((st.reached_from || []).length) facts.push("reached " + st.reached_from.slice(0, 2).map((r) => {
    const at = (r.cells || [])[0];
    // ALL the labels, not the first. Highpool's purifier can be identified by IQ, by Perception or
    // by Electronics — three real ways in, and showing one would describe a different game.
    const how = (r.labels || []).slice(0, 3).map(bpEsc).join(" · ");
    return `by the ${bpEsc(String(r.op).toLowerCase())}` + (how ? ` <b>${how}</b>` : "") +
      (at ? ` at <a class="usage-link" data-side="${st.side}" data-area="${st.area}" data-cell="${at[0]}:${at[1]}">(${at[0]},${at[1]})</a>` : "");
  }).join(" or "));
  // A COND is not one gate but a MENU: every entry is something the player can do on that square,
  // and each leads where it leads. Printing them as one run-on "needs …" hid the fact that using
  // the Engine at (28,16) is a step of the quest — the whole reason this record exists.
  if (st.actions && st.actions.length > 1) {
    const lines = st.actions.map((a) => {
      const t = a.to || {};
      const tgt = (t.op != null && t.op < 0x10) ? `<span class="muted small">→ grp${t.op}[${t.id}]</span>` : "";
      // an entry the engine walks but that can never pass is still shown — hiding it would repeat
      // the mistake the four-entry cap made — but it must not read as a way in
      const dead = a.never ? `<span class="q-never">never — ${bpEsc(a.never)}</span>` : "";
      return `<div class="q-act${a.never ? " dead" : ""}">${bpEsc(a.label)} ${tgt} ${dead}</div>`;
    }).join("");
    facts.push(`<div class="q-acts"><span class="muted small">what you can do here</span>${lines}</div>`);
  } else if (st.gate) facts.push(`needs <b>${bpEsc(st.gate)}</b>`);
  // The record groups its own entries: same target = alternatives, different target = a different
  // outcome. Which of those comes first in the story is the one thing the bytes cannot say, so the
  // split is offered and never applied on its own.
  if ((st.role_groups || []).length > 1) {
    const g = st.role_groups.map((x) =>
      `<span class="q-rg${x.costly ? " costly" : ""}">${bpEsc(x.labels.join(" / ").slice(0, 46))}</span>`).join("");
    // The record says there are N outcomes. Whether they happen at different TIMES is a judgement —
    // Highpool's machine you visit twice, the Jail cell you get into one of three ways — so the
    // split is offered and the wording does not decide for you.
    facts.push(`<div class="q-split"><span class="muted small">${st.role_groups.length} outcomes ·
      <a class="q-do-split" data-split="${i}">split</a> if the player meets them at different times
      </span>${g}</div>`);
  }
  if (st.keywords && st.keywords.length) facts.push(`ask: <b>${st.keywords.slice(0, 6).map(bpEsc).join(" · ")}</b>`);
  if (st.foe && st.foe.name) facts.push(`<b>${st.foe.count}× ${bpEsc(st.foe.name)}</b>` +
    (st.foe.portrait != null ? ` <img class="q-face" src="/api/portrait/${st.side}/${st.foe.portrait}">` : ""));
  if (st.rewrites && st.rewrites.length) {
    const NAME = { SAY: "a message", COND: "a gate", ENCOUNTER: "a fight", LOOT: "a sack",
                   SERVICE: "someone to talk to", BUMP: "a bump message", OBJ: "an object",
                   TRANSITION: "a door", empty: "nothing" };
    facts.push(st.rewrites.map((e) => {
      const where = e.relative ? `${e.x},${e.y} from the party` : `(${e.x},${e.y})`;
      return `<span class="q-rw">${bpEsc(where)} becomes <b>${bpEsc(NAME[e.becomes] || e.becomes || "?")}</b></span>`;
    }).join(" "));
  } else if (st.patches) facts.push(`rewrites ${st.patches} cell${st.patches === 1 ? "" : "s"}`);
  if (st.to_area != null) facts.push(`→ area ${st.to_area}`);
  return `<div class="q-step"><span class="q-num" style="background:${STEP_DOT[st.op] || "#5a6472"}">${i + 1}</span>
    <div class="q-step-body">
      <div class="q-step-head"><b>${bpEsc(st.op)}</b>
        <a class="usage-link" data-side="${st.side}" data-area="${st.area}">${bpEsc(st.area_name)}</a>
        <span class="muted small">grp${st.grp}[${st.cid}]</span>` +
        // The same record can appear twice, once per role it plays in the story. Saying so stops
        // it reading as a duplicate — Highpool's machine is one square you visit twice.
        (st.role ? `<span class="q-role" title="this step is one role of that record — the same record appears again for the rest">one role of this record</span>` : "") +
        `</div>
      ${st.text.map((t) => `<div class="q-said">“${bpEsc(t)}”</div>`).join("")}
      ${Object.entries(st.paragraphs || {}).map(([n, t]) =>
        `<div class="q-para"><b>¶${n}</b> ${bpEsc(t)}</div>`).join("")}
      ${facts.length ? `<div class="muted small">${facts.join("  ·  ")}</div>` : ""}
      ${st.note ? `<div class="q-note">${bpEsc(st.note)}</div>` : ""}
    </div>${qStepTools(st, i, n)}</div>`;
}

// The step tools — reorder, note, remove, split — belong to the STEP, not to the view that drew it.
// Links and Journey render the same card (qStepHtml), so they wire the same buttons here rather
// than each keeping a copy that can drift.
function qWireSteps(box, q, id) {
  // The list goes back WHOLE and merged, never rebuilt. A composed step is the authored one plus
  // what the record says right now, so the derived half is subtracted by name (DQ_DERIVED, the
  // same set the designer uses) and everything else survives — a placed beat's blueprint, values,
  // at and records, its gate, and the `conds` role-split that says which entries of one COND are
  // which role. Rebuilding a step as {ref, note} silently collapsed all of that; on The Water
  // Purifier one ↑ turned the two halves of its check back into the whole record.
  const list = () => q.steps.map((s) => {
    const out = {};
    for (const [k, v] of Object.entries(s)) if (!DQ_DERIVED.has(k)) out[k] = v;
    out.note = s.note || "";
    return out;
  });
  box.querySelectorAll("[data-split]").forEach((el) => el.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    if (!confirm("Split this gate into one step per outcome?\n\nThe records say which entries "
               + "belong together — they cannot say which one you do first, so reorder them "
               + "afterwards with ↑↓.")) return;
    const r = await fetch(`/api/quests/${encodeURIComponent(id)}/steps/${el.dataset.split}/split`,
      { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
    if (!r.ok) { status((await r.json().catch(() => ({}))).detail || "refused"); return; }
    const j = await r.json();
    status(`split into ${j.split_into} steps — put them in the order they happen`);
    qLoad(id);
  }));
  box.querySelectorAll("[data-mv]").forEach((el) => el.addEventListener("click", (ev) => {
    ev.stopPropagation();
    const i = +el.dataset.mv, d = +el.dataset.d, l = list();
    if (i + d < 0 || i + d >= l.length) return;
    [l[i], l[i + d]] = [l[i + d], l[i]];
    qSteps(id, l);
  }));
  box.querySelectorAll("[data-rm]").forEach((el) => el.addEventListener("click", (ev) => {
    ev.stopPropagation();
    const l = list();
    if (!confirm(`Remove step ${+el.dataset.rm + 1} from "${q.name}"? The record itself is untouched.`)) return;
    l.splice(+el.dataset.rm, 1); qSteps(id, l);
  }));
  box.querySelectorAll("[data-note]").forEach((el) => el.addEventListener("click", (ev) => {
    ev.stopPropagation();
    const i = +el.dataset.note, l = list();
    const t = prompt("What happens at this step?", l[i].note);
    if (t === null) return;
    l[i].note = t; qSteps(id, l);
  }));
}

function qStepTools(st, i, n) {
  return `<span class="q-step-tools">
    <button class="mini" data-mv="${i}" data-d="-1"${i === 0 ? " disabled" : ""}>↑</button>
    <button class="mini" data-mv="${i}" data-d="1"${i === n - 1 ? " disabled" : ""}>↓</button>
    <button class="mini" data-note="${i}" title="the sentence beside this step">✎</button>
    <button class="mini" data-rm="${i}">✕</button></span>`;
}

async function qSteps(id, steps) {
  const r = await fetch(`/api/quest/${encodeURIComponent(id)}`, {
    method: "PUT", headers: { "content-type": "application/json" },
    body: JSON.stringify({ steps }),
  });
  if (!r.ok) { alert((await r.json()).detail || "could not save the steps"); return; }
  await qLoad(id);
}

async function qAddStep(id) {
  const cur = $("area-select").value;
  const which = prompt("Add a step from which area?  (side:id — the picker on the Areas tab)", cur);
  if (!which) return;
  const [side, aid] = which.split(":").map(Number);
  const r = await fetch(`/api/quest-steps/${side}/${aid}`);
  if (!r.ok) { alert(`no area ${which}`); return; }
  const { steps, name } = await r.json();
  const pick = prompt(`Which record of ${name}?\n\n` +
    steps.slice(0, 60).map((s) => `grp${s.grp}[${s.cid}] ${s.op} — ${(s.text[0] || "").slice(0, 54)}`).join("\n") +
    `\n\n(type grpN[id])`, "");
  if (!pick) return;
  const q = qData.quests.find((x) => x.id === id);
  const list = (q ? q.steps : []).map((s) => ({ ref: s.ref, note: s.note || "" }));
  list.push({ ref: `s${side}a${aid}:${pick.trim()}`, note: "" });
  await qSteps(id, list);
}

function qLinkHtml(L) {
  const src = L.sources.length
    ? L.sources.map((s) => `<li>${qWhere(s)}${s.line ? `<div class="q-line">${bpEsc(s.line)}</div>` : ""}</li>`).join("")
    : `<li class="muted">nowhere — a starting item, or handed over by an NPC</li>`;
  const tgt = L.targets.map((t) => `<li>${qWhere(t)}${t.prompt ? `<div class="q-line">${bpEsc(t.prompt)}</div>` : ""}</li>`).join("");
  return `<div class="q-link ${L.loose ? "loose" : ""}" data-l="${bpEsc(L.key)}">
    <div class="q-link-head"><b>${bpEsc(L.label)}</b>
      <span class="muted small">${L.kind === "item" ? `item ${L.id}` : "keyword"}</span>
      ${L.loose ? `<span class="muted small" title="${L.kind === "item" ? "lying around in many places" : "asked in several areas but spoken nowhere else — a topic, not a password"}">· loose</span>` : ""}
      <button class="mini q-move" data-l="${bpEsc(L.key)}">move…</button></div>
    <div class="q-cols"><div><div class="q-cap">gives</div><ul>${src}</ul></div>
      <div><div class="q-cap">wants</div><ul>${tgt}</ul></div></div></div>`;
}

// ══ JOURNEY: the quest top down, in sections ══════════════════════════════════════════════════
//
// A section is a RUN of consecutive steps in the same place, not a distinct area: The Bloodstaff
// goes Church → waste pit → Church → Downtown → Blood Temple → Temple 2 → Temple → Church, and the
// Church coming back is a new section, because going back is a step. Run-length, not a set.
//
// Measured over the 34 quests: 233 steps, 18 quests in a single area, the widest crossing five.
// A distinct-area lane would collapse half the corpus into one lane and lose the return trips.
const ROMAN = (n) => ["I","II","III","IV","V","VI","VII","VIII","IX","X","XI","XII","XIII","XIV","XV",
                      "XVI","XVII","XVIII","XIX","XX"][n] || String(n + 1);
let qShape = "journey";     // what the player DOES is the question you arrive with (Alex, 2026-08-07)
const jrAreaCache = new Map();          // "side:aid" -> {area, atlas, byRec}

function jrSections(steps) {
  const out = [];
  (steps || []).forEach((st, i) => {
    const key = `${st.side}:${st.area}`;
    const last = out[out.length - 1];
    if (last && last.key === key) last.steps.push({ ...st, n: i + 1 });
    else out.push({ key, side: st.side, area: st.area,
                    name: st.area_name || `s${st.side} area ${st.area}`, steps: [{ ...st, n: i + 1 }] });
  });
  return out;
}

// One fetch per area, cached: /events rows carry the cell, the reading label AND the editable
// fields, so the map marks, the step text and the inspector all come from the same walk.
async function jrArea(side, aid) {
  const key = `${side}:${aid}`;
  if (jrAreaCache.has(key)) return jrAreaCache.get(key);
  const [ar, ts, ev, fo] = await Promise.all([
    fetch(`/api/area/${side}/${aid}`).then((r) => r.ok ? r.json() : null).catch(() => null),
    fetch(`/api/area/${side}/${aid}/tileset`).then((r) => r.ok ? r.json() : null).catch(() => null),
    fetch(`/api/area/${side}/${aid}/events`).then((r) => r.ok ? r.json() : null).catch(() => null),
    fetch(`/api/area/${side}/${aid}/foes`).then((r) => r.ok ? r.json() : null).catch(() => null),
  ]);
  const byRec = new Map();
  for (const g of (ev?.groups || [])) for (const c of g.chains) for (const r of c.rows)
    if (r.grp !== null && !byRec.has(`${r.grp}:${r.cid}`)) byRec.set(`${r.grp}:${r.cid}`, r);
  const rec = { area: ar, atlas: ts ? makeAtlas(ts) : null, byRec,
                strings: ev?.strings || null, foes: fo?.foes || null, side };
  jrAreaCache.set(key, rec);
  return rec;
}

function jrMini(cv, info, sec, selRec) {
  if (!cv || !info?.area) return;
  const A = info.area, W = A.width, H = A.height;
  const s = Math.max(3, Math.floor(150 / Math.max(W, H)));
  cv.width = W * s; cv.height = H * s;
  const g = cv.getContext("2d"); g.imageSmoothingEnabled = false;
  g.fillStyle = "#0e1116"; g.fillRect(0, 0, cv.width, cv.height);
  if (info.atlas) {
    const tiles = A.tileids || [];
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++)
      blit(g, info.atlas, displayTile(A.nibble[y * W + x], tiles[y * W + x], A.cellid[y * W + x]), x * s, y * s, s, s);
    g.fillStyle = "rgba(14,17,22,.62)"; g.fillRect(0, 0, cv.width, cv.height);
  }
  const box = (c, col, lw) => { if (!c) return; g.strokeStyle = col; g.lineWidth = lw;
                                g.strokeRect(c[0] * s + .5, c[1] * s + .5, s - 1, s - 1); };
  sec.steps.forEach((st) => box(info.byRec.get(`${st.grp}:${st.cid}`)?.cell, "#8b95a5", 1));
  if (selRec) box(info.byRec.get(selRec)?.cell, "#e6c15a", 2);
}

async function qJourney(q) {
  const box = $("q-detail");
  const secs = jrSections(q.steps);
  const total = (q.steps || []).length;
  if (!secs.length) { box.innerHTML = `<div class="muted">no steps yet — add them in Links or in Design</div>`; return; }
  box.innerHTML = `
    <div class="jr-top">
      <canvas id="jr-mini"></canvas>
      <div class="jr-top-txt">
        <div class="jr-head"><b id="jr-sec-name">…</b></div>
        <div id="jr-sec-sum" class="muted small"></div>
        <div class="muted small">${bpEsc(q.name)} · ${q.steps.length} steps · ${secs.length} sections</div>
      </div>
      <div class="jr-nav" id="jr-nav">${secs.map((sc, i) =>
        `<button class="jr-chip" data-sec="${i}" title="${bpEsc(sc.name)}">${ROMAN(i)}</button>`).join("")}</div>
    </div>
    <div id="jr-body">${secs.map((sc, i) => `
      <section class="jr-block" data-sec="${i}">
        <h3 class="jr-btitle"><span class="jr-rom">${ROMAN(i)}</span> ${bpEsc(sc.name)}
          <span class="muted small">${sc.steps.length} step${sc.steps.length === 1 ? "" : "s"}</span>
          <button class="mini jr-goto" data-side="${sc.side}" data-area="${sc.area}">↗ area</button></h3>
        <div class="jr-cards">${sc.steps.map((st, k) => `
          <div class="jr-card${k ? " linked" : ""}" data-sec="${i}" data-rec="${st.grp}:${st.cid}"
               data-side="${st.side}" data-area="${st.area}">
            ${qStepHtml(st, st.n - 1, total)}
          </div>`).join("")}</div>
      </section>
      ${i < secs.length - 1 ? `<div class="jr-travel">⇢ travels to <b>${bpEsc(secs[i + 1].name)}</b></div>` : ""}`).join("")}</div>`;

  // the sticky row follows the section you are reading
  let cur = -1;
  const show = async (i, selRec) => {
    if (i < 0 || i >= secs.length) return;
    const sc = secs[i];
    if (i !== cur) {
      cur = i;
      $("jr-sec-name").textContent = `${ROMAN(i)} · ${sc.name}`;
      const ops = [...new Set(sc.steps.map((x) => BEAT_NAME[x.op] || x.op).filter(Boolean))];
      $("jr-sec-sum").textContent = `${sc.steps.length} step${sc.steps.length === 1 ? "" : "s"}`
        + (ops.length ? ` · ${ops.join(" · ")}` : "");
      $("jr-nav").querySelectorAll(".jr-chip").forEach((b) => b.classList.toggle("on", +b.dataset.sec === i));
    }
    jrMini($("jr-mini"), await jrArea(sc.side, sc.area), sc, selRec);
  };
  const body = $("jr-body");
  wireUsageLinks(body);
  qWireSteps(body, q, q.id);
  body.addEventListener("scroll", () => {
    const top = body.getBoundingClientRect().top + 8;
    let at = 0;
    body.querySelectorAll(".jr-block").forEach((b, i) => { if (b.getBoundingClientRect().top <= top) at = i; });
    show(at);
  }, { passive: true });
  $("jr-nav").querySelectorAll(".jr-chip").forEach((b) => b.addEventListener("click", () =>
    body.querySelector(`.jr-block[data-sec="${b.dataset.sec}"]`)?.scrollIntoView({ behavior: "smooth", block: "start" })));
  body.querySelectorAll(".jr-goto").forEach((b) => b.addEventListener("click", (ev) => {
    ev.stopPropagation(); gotoArea(+b.dataset.side, +b.dataset.area);
  }));
  body.querySelectorAll(".jr-card").forEach((el) => el.addEventListener("click", async (ev) => {
    if (ev.target.closest("button, a, input, textarea, select")) return;   // tools do their own thing
    body.querySelectorAll(".jr-card.sel").forEach((x) => x.classList.remove("sel"));
    el.classList.add("sel");
    const side = +el.dataset.side, aid = +el.dataset.area;
    const info = await jrArea(side, aid);
    const row = info.byRec.get(el.dataset.rec);
    const insp = $("q-insp"); insp.hidden = false;
    if (!row) { insp.innerHTML = `<div class="muted">grp${el.dataset.rec} is not on a walked chain in ${bpEsc(secs[+el.dataset.sec].name)} — open the area to reach it.</div>`; return; }
    const [g, cid] = el.dataset.rec.split(":").map(Number);
    chainEdit(side, aid, { grp: g, cid, op: row.op, fields: row.fields }, body, insp,
              { strings: info.strings, foes: info.foes, side });
    show(+el.dataset.sec, el.dataset.rec);
  }));
  show(0);
}

function qOpen(id) {
  const q = qData.quests.find((x) => x.id === id);
  if (!q) { qSel = null; return; }
  qSel = id;
  $("q-insp").hidden = qShape !== "journey";
  if (qShape === "journey") { qRenderList(); qJourney(q); return; }
  const box = $("q-detail");
  box.innerHTML = `
    <div class="q-head"><input id="q-name" value="${bpEsc(q.name)}">
      <span class="muted small">${q.track === "main" ? `main line · stage ${q.depth} · ` : ""}${bpEsc(q.region || "")} · ${q.areas.length} areas</span>
      <button id="q-save" class="mini">save</button>
      <button id="q-del" class="mini" title="delete this quest — its links go back to ungrouped; any records it placed stay in the game">🗑 delete quest</button></div>
    ${q.after && q.after.length ? `<div class="muted small">after: ${q.after.map((x) => bpEsc(x)).join(" · ")}</div>` : ""}
    ${q.unknown_after && q.unknown_after.length ? `<div class="bp-req">names a prerequisite that does not exist: ${q.unknown_after.map(bpEsc).join(", ")}</div>` : ""}
    ${q.missing.length ? `<div class="bp-req">the graph does not produce: ${q.missing.map(bpEsc).join(", ")} — either an edit removed the connection, or it was never derivable in the first place</div>` : ""}
    <textarea id="q-notes" rows="4" placeholder="what this quest is, what it assumes, what to check…">\n${bpEsc(q.notes)}</textarea>
    <div class="q-areas muted small">${q.areas.map(bpEsc).join(" · ")}</div>
    <div class="q-cap">the story <button id="q-addstep" class="mini">＋ step</button></div>
    <div class="q-steps">${q.steps.length
      ? q.steps.map((st, i) => qStepHtml(st, i, q.steps.length)).join("")
      : `<div class="muted small">no steps yet — ＋ step adds one from any area. The links below are
         what the graph found; the steps are what actually happens.</div>`}</div>
    ${q.links.length ? `<div class="q-cap">what it hangs on</div>` : ""}
    ${q.links.map(qLinkHtml).join("")}`;
  qRenderList();
  wireUsageLinks(box);
  $("q-save").addEventListener("click", async () => {
    await fetch(`/api/quest/${encodeURIComponent(id)}`, {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ name: $("q-name").value, notes: $("q-notes").value }),
    });
    await qLoad(id);
  });
  $("q-del").addEventListener("click", async () => {
    if (!confirm(`Drop the quest "${q.name}"? Its links go back to ungrouped — the graph itself is derived and stays.`)) return;
    await fetch(`/api/quest/${encodeURIComponent(id)}`, { method: "DELETE" });
    qSel = null; $("q-detail").innerHTML = `<div class="muted">pick a quest</div>`; await qLoad();
  });
  box.querySelectorAll(".q-move").forEach((el) => el.addEventListener("click", () => qMove(el.dataset.l)));
  const add = $("q-addstep");
  if (add) add.addEventListener("click", () => qAddStep(id));
  qWireSteps(box, q, id);
}

function qLink(key) {
  const L = qData.ungrouped.find((x) => x.key === key);
  if (!L) return;
  qSel = null;
  const box = $("q-detail");
  box.innerHTML = `<div class="q-head"><b>${bpEsc(L.label)}</b>
      <span class="muted small">ungrouped ${L.kind}</span></div>${qLinkHtml(L)}`;
  wireUsageLinks(box); qRenderList();
  box.querySelectorAll(".q-move").forEach((el) => el.addEventListener("click", () => qMove(el.dataset.l)));
}

async function qMove(key) {
  const ids = qData.quests.map((q) => q.id);
  const to = prompt(`Move "${key}" into which quest?\n\n${ids.join("  ·  ")}\n\n(a new id creates a new quest; empty = leave it ungrouped)`, qSel || "");
  if (to === null) return;
  if (!to.trim()) {                                  // out of every quest = back to ungrouped
    const owner = qData.quests.find((q) => q.links.some((L) => L.key === key));
    if (owner) await fetch(`/api/quest/${encodeURIComponent(owner.id)}`, {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ links: owner.links.map((L) => L.key).filter((k) => k !== key) }),
    });
    await qLoad(); return;
  }
  const q = qData.quests.find((x) => x.id === to.trim());
  const links = q ? [...q.links.map((L) => L.key), key] : [key];
  const r = await fetch(`/api/quest/${encodeURIComponent(to.trim())}`, {
    method: "PUT", headers: { "content-type": "application/json" },
    body: JSON.stringify({ links, ...(q ? {} : { name: to.trim(), region: "" }) }),
  });
  if (!r.ok) { alert((await r.json()).detail || "could not move it"); return; }
  await qLoad(to.trim());
}

// ═══════════════ BLUEPRINTS TAB — the workbench ═══════════════
// The Map tab snaps a region out and places one back. Everything BETWEEN those two acts lives here:
// look at the block, repaint it, throw a cell out, write down what it is for.
//
// The sandbox IS the blueprint, not a scratch pad beside it. A blueprint already holds its cells
// (dc/dr/op/cellid/tile) and its tile PICTURES, so it draws itself without any target tileset and
// every edit goes straight into the document. There is nothing to "apply".
//
// Terrain is free. A cell with op > 0 carries a record, and erasing it can orphan records that only
// it reached — the backend settles that (renormalize) and reports what dropped, so the editor never
// has to guess and never loses something silently.
let bpwName = null, bpwDoc = null, bpwAtlas = null, bpwBrush = null, bpwTool = "paint",
    bpwDirty = false, bpwCells = [], bpwInited = false;

let bpwCat = null;
function bpwInit() { if (!bpwInited) { bpwInited = true; bpwList(); } }

function bpwSetDirty(d) {
  bpwDirty = d;
  $("bpw-save").disabled = !d;
  const u = $("bpw-undo"), rv = $("bpw-revert");
  if (u) u.disabled = !bpwUndo.length;
  if (rv) rv.disabled = !d;
}
// Painting a blueprint had no way back. One wrong drag over a 3x3 shop and the only remedy was the
// NAS auto-commit history, which is a real safety net but not one you can reach from here. A stack
// of whole documents is cheap at this size and covers the case that actually happens.
let bpwUndo = [];
function bpwPush() {
  if (!bpwDoc) return;
  bpwUndo.push(JSON.stringify(bpwDoc));
  if (bpwUndo.length > 40) bpwUndo.shift();
}

// The library is ALL THREE families, not just the buildings. Patterns and services existed from the
// day the designer shipped, but only inside "pick a shape for this step" — so the one screen called
// Blueprints showed one of the three kinds of blueprint there are. A catalogue you can only reach
// by already knowing what you want is not a catalogue.
async function bpwList() {
  const box = $("bpw-list");
  const [r, cat] = await Promise.all([
    fetch("/api/blueprints"),
    fetch("/api/design/catalogue").then((x) => x.ok ? x.json() : null).catch(() => null),
  ]);
  if (!r.ok) { box.innerHTML = `<div class="muted small">err ${r.status}</div>`; return; }
  const { blueprints } = await r.json();
  bpwCat = cat;
  const pat = cat?.families?.find((f) => f.key === "pattern")?.items || [];
  const svc = cat?.families?.find((f) => f.key === "service")?.items || [];
  $("bpw-info").textContent = `${pat.length} patterns · ${svc.length} services · ${blueprints.length} buildings`;

  // BUILDINGS — the map regions. Editable here; the other two are catalogue entries you instantiate
  // into a quest, which is why only these open in the workbench.
  let h = `<div class="bp-fam">Buildings <span class="muted small">a map region — cells, scripts,
      texts, tiles, foes. Snap one on the Map tab (Select → ⧉), edit it here.</span></div>`;
  h += blueprints.length
    ? blueprints.map((b) => `<div class="bp-row ${b.name === bpwName ? "on" : ""}" data-n="${bpEsc(b.name)}">
        <div class="bp-name"><b>${bpEsc(b.name)}</b> <span class="muted small">${b.size ? `${b.size.w}×${b.size.h}` : ""}</span></div>
        <div class="muted small">${b.error ? bpEsc(b.error) : bpParts(b)}</div></div>`).join("")
    : `<div class="muted small">none yet — Map tab → <b>Select</b>, drag a box, then <b>⧉ Snap selection</b>.</div>`;

  const card = (b, kind) => `<div class="bp-row bp-cat" data-bp="${bpEsc(b.id)}" data-kind="${kind}">
      <div class="bp-name"><b>${bpEsc(b.name)}</b>
        ${b.seen ? `<span class="bp-seen">${b.seen}× in the game</span>` : ""}</div>
      <div class="muted small">${bpEsc(b.about || "")}</div>
      <div class="muted small bp-holes">${(b.holes || []).map((x) => bpEsc(x.label)).join(" · ")}</div></div>`;

  h += `<div class="bp-fam">Patterns <span class="muted small">shapes the game repeats, counted over
      all 43 areas — if one is not here, the game does not do it</span></div>` + pat.map((b) => card(b, "pattern")).join("");
  h += `<div class="bp-fam">Services <span class="muted small">a shop, a doctor, a library, someone
      who talks</span></div>` + svc.map((b) => card(b, "service")).join("");
  box.innerHTML = h;
  box.querySelectorAll(".bp-row[data-n]").forEach((el) => el.addEventListener("click", () => bpwOpen(el.dataset.n)));
  box.querySelectorAll(".bp-row[data-bp]").forEach((el) => el.addEventListener("click", () => bpwShowCat(el.dataset.bp)));
}

// A pattern or a service has no map region to paint, so the workbench shows what it IS: the beats
// in order, what still has to be filled in, and the way into a quest that will use it.
function bpwShowCat(id) {
  const b = (bpwCat?.families || []).flatMap((f) => f.items).find((x) => x.id === id);
  if (!b) return;
  $("bpw-list").querySelectorAll(".bp-row").forEach((r) => r.classList.toggle("on", r.dataset.bp === id));
  // ONE assignment: #bpw-sub lives INSIDE #bpw-title, so setting the title's textContent first
  // destroys the span and the next line then reads null off a missing element.
  $("bpw-title").innerHTML = `${bpEsc(b.name)} <span class="muted small" id="bpw-sub">` +
    (b.seen ? `${b.seen}× in the shipped game` : "") + `</span>`;
  const OPN = { 1: "SAY", 2: "GATE", 3: "FIGHT", 5: "LOOT", 6: "TALK", 10: "GO", 11: "BUMP" };
  $("bpw-report").innerHTML = `<div class="bp-cat-detail">
    <p>${bpEsc(b.about || "")}</p>
    ${b.beats ? `<div class="q-cap">what the player meets</div>
      <div class="dq-beats">${b.beats.map((x, i) =>
        `<span class="dq-beat">${i + 1} ${OPN[x.op] || x.role}</span>`).join('<span class="dq-arrow">→</span>')}</div>` : ""}
    <div class="q-cap">what you still have to say</div>
    <ul class="bp-holelist">${(b.holes || []).map((x) =>
      `<li><b>${bpEsc(x.label)}</b>${x.hint ? ` <span class="muted small">${bpEsc(x.hint)}</span>` : ""}</li>`).join("")}</ul>
    <div class="muted small">A blueprint is always COPIED into the quest that uses it and shaped
      there — pick it on a step in <b>Design</b>.</div>
    <button class="mini" id="bpw-to-design">open the designer →</button></div>`;
  // Switching tabs is not handing something over. The button used to drop you on whichever quest
  // happened to be first, with the pattern you had just read left behind — you then had to
  // remember its name and find it again in a list. It carries the blueprint now.
  $("bpw-to-design").onclick = () => dqStartWith(b.id, b.name);
  // The whole workbench — brush, canvas, tiles, contents, notes — is about a map REGION, and a
  // pattern has none. Greying it out would say "you could do this here"; it is hidden instead, and
  // the description takes the width back.
  document.getElementById("tab-blueprints").classList.add("cat-mode");
  $("bpw-palette").innerHTML = "";
  $("bpw-cellinfo").textContent = "";
}

async function bpwOpen(name) {
  document.getElementById("tab-blueprints").classList.remove("cat-mode");
  const [d, at] = await Promise.all([
    fetch(`/api/blueprint/${encodeURIComponent(name)}`).then((r) => r.ok ? r.json() : null),
    fetch(`/api/blueprint/${encodeURIComponent(name)}/atlas`).then((r) => r.ok ? r.json() : null),
  ]);
  if (!d) { $("bpw-report").innerHTML = `<div class="bp-bad">could not load ${bpEsc(name)}</div>`; return; }
  bpwName = name; bpwDoc = d.blueprint; bpwAtlas = at ? makeAtlas(at) : null;
  bpwBrush = (at && at.tiles && at.tiles.length) ? at.tiles[0] : null;
  $("bpw-notes").value = bpwDoc.description || "";
  bpwSetDirty(false);
  $("bpw-report").innerHTML = "";
  bpwRender(d.summary);
  bpwList();
}

function bpwRender(summary) {
  const s = summary || {};
  $("bpw-title").textContent = bpwDoc.name;
  $("bpw-sub").textContent = `${bpwDoc.size.w}×${bpwDoc.size.h}` +
    (bpwDoc.source && bpwDoc.source.area != null ? ` · cut from area ${bpwDoc.source.area}` : "");
  bpwDraw(); bpwPalette(); bpwContents(s);
}

function bpwFlatten() {
  bpwCells = [];
  (bpwDoc.objects || [bpwDoc]).forEach((o, oi) => {
    const off = o.offset || { dc: 0, dr: 0 };
    (o.cells || []).forEach((e, ei) => bpwCells.push({ oi, ei, e, x: off.dc + e.dc, y: off.dr + e.dr }));
  });
  return bpwCells;
}

function bpwDraw() {
  const cv = $("bpw-canvas"); if (!cv || !bpwDoc) return;
  const cells = bpwFlatten();
  const W = Math.max(1, bpwDoc.size.w), H = Math.max(1, bpwDoc.size.h);
  const z = Math.max(8, Math.min(48, Math.floor(Math.min(640 / W, 640 / H))));
  cv.width = W * z; cv.height = H * z;
  const c = cv.getContext("2d"); c.imageSmoothingEnabled = false;
  c.fillStyle = "#11151c"; c.fillRect(0, 0, cv.width, cv.height);
  for (const { e, x, y } of cells) {
    if (bpwAtlas && e.tile != null) blit(c, bpwAtlas, e.tile, x * z, y * z, z, z);
    else { c.fillStyle = "#232a35"; c.fillRect(x * z, y * z, z, z); }
    if (e.op > 0 && e.op < 0x10) {                 // a script cell — anchored, not plain terrain
      c.strokeStyle = OP_COLOR[e.op] || "#8b95a5"; c.lineWidth = 2;
      c.strokeRect(x * z + 1, y * z + 1, z - 2, z - 2);
    }
  }
  c.strokeStyle = "rgba(255,255,255,.06)"; c.lineWidth = 1;
  for (let i = 0; i <= W; i++) { c.beginPath(); c.moveTo(i * z, 0); c.lineTo(i * z, H * z); c.stroke(); }
  for (let i = 0; i <= H; i++) { c.beginPath(); c.moveTo(0, i * z); c.lineTo(W * z, i * z); c.stroke(); }
  cv.dataset.z = z;
}

function bpwPalette() {
  const box = $("bpw-palette"); if (!box) return;
  const ids = [...new Set((bpwDoc.objects || [bpwDoc]).flatMap((o) => Object.keys(o.tiles || {}).map(Number)))].sort((a, b) => a - b);
  if (!ids.length) { box.innerHTML = `<span class="muted small">this blueprint carries no tile pictures</span>`; return; }
  box.innerHTML = ids.map((t) => `<canvas class="sw ${t === bpwBrush ? "on" : ""}" data-t="${t}" width="32" height="32" title="tile ${t}"></canvas>`).join("");
  box.querySelectorAll("canvas").forEach((cv) => {
    const t = +cv.dataset.t;
    if (bpwAtlas) blit(cv.getContext("2d"), bpwAtlas, t, 0, 0, 32, 32);
    cv.addEventListener("click", () => { bpwBrush = t; bpwPalette(); });
  });
}

function bpwContents(s) {
  const box = $("bpw-contents"); if (!box) return;
  const objs = bpwDoc.objects || [bpwDoc];
  const n = (k) => objs.reduce((a, o) => a + Object.keys(o[k] || {}).length, 0);
  const ops = {};
  objs.forEach((o) => Object.values(o.records || {}).forEach((v) => Object.values(v).forEach((rr) => { ops[rr.op] = (ops[rr.op] || 0) + 1; })));
  let h = `<div>${objs.reduce((a, o) => a + (o.cells || []).length, 0)} cells · ${n("text")} texts · ${n("tiles")} tiles · ${n("foes")} foes · ${n("npcs")} NPCs</div>`;
  const list = Object.entries(ops).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${v}× ${k}`).join(" · ");
  if (list) h += `<div class="muted small">records: ${bpEsc(list)}</div>`;
  const req = bpRequires((s && s.requires) || bpwDoc.requires);
  if (req.length) h += `<div class="bp-req">needs: ${req.map(bpEsc).join(" · ")}</div>`;
  ((s && s.notes) || bpwDoc.notes || []).forEach((x) => { h += `<div class="bp-note">${bpEsc(x)}</div>`; });
  box.innerHTML = h;
}

function bpwAt(ev) {
  const cv = $("bpw-canvas"), z = +cv.dataset.z || 32, r = cv.getBoundingClientRect();
  const x = Math.floor((ev.clientX - r.left) * (cv.width / r.width) / z);
  const y = Math.floor((ev.clientY - r.top) * (cv.height / r.height) / z);
  return { x, y, hit: bpwCells.find((c) => c.x === x && c.y === y) };
}

function bpwEdit(ev) {
  if (!bpwDoc) return;
  const { x, y, hit } = bpwAt(ev);
  bpwPush();                                    // snapshot before the doc changes, never after
  if (bpwTool === "erase") {
    if (!hit) return;
    if (hit.e.op > 0 && hit.e.op < 0x10 &&
        !confirm(`Cell (${x},${y}) carries a script (op $${hit.e.op.toString(16).toUpperCase()}). ` +
                 `Removing it can leave records nothing reaches — those are dropped on save. Remove it?`)) return;
    (bpwDoc.objects || [bpwDoc])[hit.oi].cells.splice(hit.ei, 1);
  } else {
    if (bpwBrush == null) return;
    if (hit) { if (hit.e.tile === bpwBrush) return; hit.e.tile = bpwBrush; }
    else {
      // painting outside the block GROWS it: a new plain-terrain cell, op 0. renormalize()
      // re-origins and re-measures on save, so negative coordinates are fine here.
      const o = (bpwDoc.objects || [bpwDoc])[0], off = o.offset || { dc: 0, dr: 0 };
      o.cells.push({ dc: x - off.dc, dr: y - off.dr, op: 0, cellid: 0, tile: bpwBrush });
    }
  }
  bpwSetDirty(true); bpwDraw();
}

async function bpwSave() {
  if (!bpwDoc || !bpwName) return;
  $("bpw-report").innerHTML = `<span class="muted">saving…</span>`;
  const r = await fetch(`/api/blueprint/${encodeURIComponent(bpwName)}`, {
    method: "PUT", headers: { "content-type": "application/json" },
    body: JSON.stringify({ blueprint: bpwDoc, description: $("bpw-notes").value }),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { $("bpw-report").innerHTML = `<div class="bp-bad">${bpEsc(d.detail || r.status)}</div>`; return; }
  bpwDoc = d.blueprint; bpwUndo = []; bpwSetDirty(false);
  const ch = Object.entries(d.changed || {}).map(([k, v]) => `${v > 0 ? "+" : ""}${v} ${k}`).join(" · ");
  $("bpw-report").innerHTML = ch
    ? `<div class="bp-req">saved — settling the document changed: ${bpEsc(ch)}</div>`
    : `<div class="muted small">saved.</div>`;
  const at = await fetch(`/api/blueprint/${encodeURIComponent(bpwName)}/atlas`).then((x) => x.ok ? x.json() : null);
  bpwAtlas = at ? makeAtlas(at) : bpwAtlas;
  bpwRender(d.summary); bpwList(); bpLoad();
}

// Floating script overlay on hover. Shows the cell's script sentence when the cursor is over a
// cell that has one; the map stays clean until then.
function scriptTip(col, row, e) {
  const tip = $("script-tip");
  if (!scriptCells.has(`${col},${row}`)) { tip.hidden = true; return; }
  const html = cellStateSection(col, row);
  if (!html) { tip.hidden = true; return; }
  tip.innerHTML = html;
  tip.querySelectorAll(".sc-tile").forEach((cv) => {
    const t = +cv.dataset.tid; cv.width = 24; cv.height = 24;
    const c = cv.getContext("2d"); c.imageSmoothingEnabled = false;
    if (mapAtlas) blit(c, mapAtlas, t, 0, 0, 24, 24);
  });
  tip.hidden = false;
  const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
  let x = e.clientX + pad, y = e.clientY + pad;
  if (x + w > window.innerWidth) x = e.clientX - w - pad;
  if (y + h > window.innerHeight) y = e.clientY - h - pad;
  tip.style.left = x + "px"; tip.style.top = y + "px";
}

canvas.addEventListener("mousemove", (e) => {
  if (!area) return; const c = cellAt(e); if (!c) return; hover = c;
  scriptTip(c.col, c.row, e);
  const a = activeSubset();
  if (a === "map") {
    if (painting && paintTool === "brush" && !e.shiftKey) { stampAt(c.col, c.row); lastPaint = c; }
    if (paintTool === "select" && selStart) selRect = { a: selStart, b: c };
  } else if (a === "physique" && physPainting) {
    physApply(c.col, c.row);
  }
  drawMap();
});
// Leaving the map falls back to the cell you actually opened, so the bar keeps saying something
// true instead of going blank the moment you reach for a field in the panel.
canvas.addEventListener("mouseleave", () => { $("script-tip").hidden = true; });
canvas.addEventListener("mousedown", (e) => {
  if (!area) return; const c = cellAt(e); if (!c) return;
  if (blkArmed && mode === "edit" && activeSubset() === "map") { blkPlace(c.col, c.row); return; }
  if (pickCellCb) { const cb = pickCellCb; pickCellCb = null; cb(c.col, c.row); drawMap(); return; }
  const a = activeSubset();
  if (a === "view" || a === "script") { inspectCell(c.col, c.row); return; }
  if (a === "physique") {
    if (physTool === "fill") { physFill(c.col, c.row); drawMap(); return; }
    physPainting = true; physApply(c.col, c.row); drawMap(); return;
  }
  // a === "map": tile paint
  if (paintTool === "fill") { floodFill(c.col, c.row, brush); drawMap(); return; }
  if (paintTool === "select") { selStart = c; selRect = { a: c, b: c }; drawMap(); return; }
  painting = true;
  if (e.shiftKey && lastPaint) lineTo(c); else stampAt(c.col, c.row);
  lastPaint = c; drawMap();
});
window.addEventListener("mouseup", () => {
  painting = false; physPainting = false;
  if (paintTool === "select" && selStart && selRect) { captureSelection(selRect.a, selRect.b); selStart = null; selRect = null; drawMap(); }
});
window.addEventListener("keydown", (e) => {
  if (mode === "edit" && (e.key === "Backspace" || e.key === "Delete") && stamp) { stamp = null; $("stamp-info").textContent = ""; e.preventDefault(); }
  if (e.key === "Escape" && bpPlacing) { bpPlacing = null; pickCellCb = null; status("placement cancelled"); }
});

// What this cell LOOKS LIKE now and what scripts turn it into. Two things are invisible without
// it: the engine draws op-$4 cells from their grp4 record, not from the map (so Base Cochise is a
// mountain while the map already holds the base icon underneath), and the patches that reveal it
// fire from cells in the far corner of the map — unfindable from here in a source-keyed list.
// The cell's SCRIPT: its chain of states as plain-language steps, with a foe portrait for fights
// and a tile thumbnail for what a step draws/changes. This is the read-only "sentence" overlay —
// the same data the Flow view uses, shown per cell.
const OP_VERB = { 0: "empty", 1: "says", 2: "condition", 3: "fight", 4: "object", 5: "loot sack",
                  6: "event", 8: "keyword", 9: "party flag", 10: "door", 11: "bump", 12: "changes cells" };
const OP_DOT = { 1: "#5aa9e6", 2: "#e6c15a", 3: "#ef6a6a", 5: "#e6c15a", 12: "#7bd88f", 10: "#e69a5a" };

function cellStateSection(col, row) {
  if (!area) return "";
  const i = row * area.width + col;
  const op = area.nibble[i], cid = area.cellid[i], mapTile = area.tileids[i];
  const ch = chains[`${col},${row}`] || [];
  const inc = incoming[`${col},${row}`] || [];
  const drawn = displayTile(op, mapTile, cid);
  if (ch.length <= 1 && !inc.length && drawn === mapTile) return "";

  const esc = (t) => String(t || "").replace(/&/g, "&amp;").replace(/</g, "&lt;");
  const tileThumb = (t) => t == null ? "" : `<canvas class="sc-tile" data-tid="${t}" title="tile #${t}"></canvas>`;
  const step = (st, idx, total) => {
    const verb = OP_VERB[st.op_nibble] || `op $${st.op_nibble.toString(16).toUpperCase()}`;
    const dot = OP_DOT[st.op_nibble] || "#5a6472";
    let thumb = "";
    if (st.foe && st.foe.str_id) thumb = `<img class="sc-foe" src="/api/portrait/${curSide}/${st.foe.str_id}" title="${esc(st.foe.name)}">`;
    else if (st.op_nibble === 0x4 || st.op_nibble === 0x0 || st.op_nibble === 0x5) thumb = tileThumb(st.tile);
    const via = st.from ? `<span class="muted small">${st.branch || "→"}</span>` : `<span class="muted small">start</span>`;
    // a state whose text is "Read paragraph 57." is showing a POINTER; show what it points at
    const para = Object.entries(st.paragraphs || {})
      .map(([n, t]) => `<div class="q-para"><b>¶${n}</b> ${esc(t)}</div>`).join("");
    const body = (st.foe ? `${st.foe.count}× <b>${esc(st.foe.name)}</b>`
      : st.text ? esc(st.text).slice(0, 80)
      : `<span class="muted">${verb}</span>`) + para;
    return `<div class="sc-step">${idx > 0 ? via : '<span class="muted small">&nbsp;</span>'}
      <span class="sc-dot" style="background:${dot}"></span>
      <span class="sc-verb">${verb}</span>${thumb}<span class="sc-body">${body}</span></div>`;
  };

  // Two destinations, because they answer different questions: Events shows this cell among the
  // area's other events, Chains is where the numbers are EDITED. The inspector is read-only, so
  // the edit link is the one that matters and comes first.
  let h = `<div class="insp-sec"><div class="insp-title">script ⚡ ` +
          `<a class="ch-link" data-c="${col},${row}">edit chain</a> · ` +
          `<a class="fl-link" data-c="${col},${row}">see in Events</a></div>`;
  if (ch.length > 1) {
    h += `<div class="sc-list">${ch.map((st, n) => step(st, n, ch.length)).join("")}</div>`;
  }
  if (drawn !== mapTile) {
    h += `<div class="sc-under muted small">drawn as ${tileThumb(drawn)} but the map holds ${tileThumb(mapTile)} underneath — do not "clean up"</div>`;
  }
  if (inc.length) {
    h += `<div class="sc-inc muted small">changed from elsewhere: ${inc.map((e) =>
      `(${e.source[0]},${e.source[1]})`).join(", ")}</div>`;
  }
  h += `</div>`;
  return h;
}


async function inspectCell(col, row) {
  const [side, id] = $("area-select").value.split(":").map(Number);
  const box = $("cell-inspect"); box.innerHTML = `<div class="muted">…</div>`;
  $("cell-panel")?.classList.remove("empty");
  const r = await fetch(`/api/area/${side}/${id}/cell/${col}/${row}`);
  if (!r.ok) { box.innerHTML = `<div class="muted">err ${r.status}</div>`; return; }
  const d = await r.json();
  let h = `<div class="insp-head">(${d.col}, ${d.row}) — op $${d.op.toString(16).toUpperCase()} ${d.op_name}</div>`;
  // Show every section. The old rule dropped them all as soon as the cell had editable fields,
  // on the theory that the field editor already says it — but it does not: an op-A service door
  // carries a TAIL naming the record it hands over to, and that tail is the only place the TREX
  // talk service is written down. The panel showed `C0 00 00 33 06 0B` and the target area, and
  // left the reader to decode `06 0B` -> grp6[11] by hand.
  for (const s of d.sections || []) {
    h += `<div class="insp-sec"><div class="insp-title">${s.title}</div>`;
    if (s.kind === "kv") h += `<table class="insp-kv">${(s.rows || []).map((rw) => `<tr><td>${rw[0]}</td><td>${rw[1]}</td></tr>`).join("")}</table>`;
    else if (s.kind === "text") h += `<div class="insp-text">${(s.text || "").replace(/</g, "&lt;")}</div>`;
    else if (s.kind === "list") h += `<ul class="insp-list">${(s.items || []).map((it) => `<li>${it}</li>`).join("")}</ul>`;
    else if (s.kind === "hex") h += `<div class="insp-hex">${s.hex || ""}</div>`;
    if (s.scene_sel != null)   // $CA00 SERVICE handler blob — offer its 6502 listing (like grp16)
      h += `<button type="button" class="mini scene-code-btn" data-sel="${s.scene_sel}" title="disassemble the $CA00 scene handler s${side}_scene_id${s.scene_sel}.bin">▸ $CA00 scene code</button><div class="scene-code" data-sel="${s.scene_sel}"></div>`;
    h += `</div>`;
  }
  h += cellStateSection(col, row);
  box.innerHTML = h;
  box.querySelectorAll(".scene-code-btn").forEach((b) => b.addEventListener("click", async () => {
    const sel = b.dataset.sel;
    const div = box.querySelector(`.scene-code[data-sel="${sel}"]`);
    if (div.dataset.open === "1") { div.innerHTML = ""; div.dataset.open = "0"; b.textContent = "▸ $CA00 scene code"; return; }
    div.innerHTML = `<div class="muted small">disassembling…</div>`;
    const rr = await fetch(`/api/scene/${side}/${sel}`);
    if (!rr.ok) { div.innerHTML = `<div class="muted small">err ${rr.status}</div>`; return; }
    const j = await rr.json();
    div.innerHTML = `<div class="muted small">${j.file} · ${j.size} B @ $${j.org.toString(16).toUpperCase()} (${j.source})</div>`
      + `<pre class="scene-listing">${(j.listing || []).map((ins) =>
        `$${ins.addr.toString(16).toUpperCase().padStart(4, "0")}  ${(ins.bytes || "").padEnd(8)} ${ins.text}${ins.comment ? "  ; " + ins.comment : ""}`
          .replace(/</g, "&lt;")).join("\n")}</pre>`;
    div.dataset.open = "1"; b.textContent = "▾ $CA00 scene code";
  }));
  // fill tile thumbnails from the map atlas
  box.querySelectorAll(".sc-tile").forEach((cv) => {
    const t = +cv.dataset.tid; cv.width = 24; cv.height = 24;
    const c = cv.getContext("2d"); c.imageSmoothingEnabled = false;
    if (mapAtlas) blit(c, mapAtlas, t, 0, 0, 24, 24);
  });
  box.querySelectorAll(".fl-link").forEach((a) => a.addEventListener("click", () => {
    const [c, r] = a.dataset.c.split(",").map(Number);
    setMode("flow"); flowLoad([c, r]);
  }));
  box.querySelectorAll(".ch-link").forEach((a) => a.addEventListener("click", () => {
    const [c, r] = a.dataset.c.split(",").map(Number);
    chainFocus(c, r);
  }));
  renderCellEdit(side, id, col, row, d.op === 0xB ? [] : (d.editable || []), d.op);
  renderAddOp(side, id, col, row, d.chain);
}

// ── ADD op to a cell (logic-ADD): build a NEW subtable record + point the cell at it.
//    Script view only. All authored types: SAY/COND/LOOT/EVENT/KEYWORD/TRANSITION/PATCH. ──
const msgIn = (id, v = 0, max = 127) =>
  `<input id="${id}" type="number" min="0" max="${max}" value="${v}" title="string id — see the AREA Text tab">`;

function renderAddOp(side, id, col, row, chainInit) {
  const box = $("cell-addop"); if (!box) return;
  const sub = activeSubset();
  // Render in VIEW too (read-only) so the panel is identical to Edit — CSS (.ro-view) disables the
  // inputs + hides the action buttons. Only skip on the Map/Physique paint sub-tabs.
  if (sub !== "script" && sub !== "view") { box.innerHTML = ""; return; }
  // Pre-load an existing op-B chain so it can be EDITED/extended/reordered (not only created).
  chainLinks = (chainInit && chainInit.links) ? chainInit.links.map((l) => ({ op: 11, message: l.message })) : [];
  chainInitLoop = chainInit ? chainInit.loop !== false : true;
  fetch(`/api/area/${side}/${id}/texts`).then((r) => r.json()).then((d) => {
    chainTexts = {}; (d.messages || []).forEach((m) => (chainTexts[m.id] = m.text || ""));
    if (+$("ao-op").value === 11) renderChainList();   // fill in the snippets once loaded
  }).catch(() => {});
  const opOpts = `
      <option value="10">$A TRANSITION (exit)</option>
      <option value="1">$1 SAY (message)</option>
      <option value="2">$2 COND/TRADE (logic)</option>
      <option value="5">$5 LOOT (container)</option>
      <option value="6">$6 EVENT (grp16 code)</option>
      <option value="12">$C CELL_PATCH (map rewrite)</option>
      <option value="4">$4 MSG (single message)</option>
      <option value="9">$9 PARTY HAZARD (poison/damage)</option>
      <option value="11">$B SCRIPT CHAIN (bump · say · loot)</option>
      <option value="8">$8 KEYWORD (ask — experimental)</option>`;
  box.innerHTML = `<div class="insp-sec ef-box"><div class="insp-title">＋ add op to this cell</div>
    <label class="tp-row">op <select id="ao-op">${opOpts}</select></label>
    <div id="ao-fields"></div>
    <div class="btns"><button id="ao-add">Add op</button>
      <button id="ao-move" class="mini" title="move THIS cell's existing op to another cell">⇢ move op</button></div>
    <p class="muted small">Creates a new record + points this cell at it (undoable). Replaces any op
      already on the cell. Message/text ids = the AREA Text tab (0 = none).</p></div>`;
  const rowsHtml = (idPrefix, cols, n) =>
    Array.from({ length: n }, (_, k) =>
      `<div class="tp-row ao-row" data-k="${k}">${cols.map((c) =>
        `<input class="${idPrefix}" data-k="${k}" data-f="${c.f}" type="number" min="${c.min}" max="${c.max}" value="${c.v}" title="${c.t}" style="width:44px">`).join(" ")}</div>`).join("");
  const fields = () => {
    const op = +$("ao-op").value;
    let h = "";
    if (op === 10) h = `<label class="tp-row">target area <input id="ao-tgt" type="number" min="0" max="255" value="0"></label>
        <label class="tp-row">arrive <input id="ao-x" class="tp-coord" type="number" min="0" max="63" value="1">,
          <input id="ao-y" class="tp-coord" type="number" min="0" max="63" value="1"></label>
        <label class="tp-row">message id ${msgIn("ao-msg", 0, 63)}</label>`;
    else if (op === 1) h = `<label class="tp-row">message id ${msgIn("ao-msg", 1)}</label>`;
    else if (op === 2) h = `<label class="tp-row">prompt ${msgIn("ao-prompt")}</label>
        <label class="tp-row">on success ${msgIn("ao-succ")}</label>
        <label class="tp-row">on fail ${msgIn("ao-fail")}</label>
        <label class="tp-row">DONE op <input id="ao-dop" type="number" min="0" max="255" value="255" title="255 = no map write"> id <input id="ao-did" type="number" min="0" max="255" value="0"></label>
        <label class="tp-row">effect tgt <input id="ao-efft" type="number" min="0" max="255" value="0" title="$1D damage · $15 money · $21 XP · 0 none"> val <input id="ao-effv" type="number" min="0" max="255" value="0"></label>
        <div class="muted small">conditions ([op][arg], 0/0 = unused; op: 0=give-item 32×2=item-sel …)</div>
        ${rowsHtml("ao-cond", [{ f: "op", min: 0, max: 255, v: 0, t: "cond opbyte" }, { f: "arg", min: 0, max: 255, v: 0, t: "arg (item id …)" }], 4)}`;
    else if (op === 5) h = `<label class="tp-row">message id ${msgIn("ao-msg")}</label>
        <div class="muted small">items ([item id][qty], 0/0 = unused; $5E = cash, id|$80 = random class)</div>
        ${rowsHtml("ao-item", [{ f: "item", min: 0, max: 255, v: 0, t: "item id (ITEM_TABLE)" }, { f: "qty", min: 0, max: 255, v: 1, t: "qty" }], 6)}`;
    else if (op === 6) h = `<label class="tp-row">kind <select id="ao-ev-kind">
          <option value="grp16">grp16 event (area code)</option>
          <option value="service">$CA00 service (shop/doc/…)</option></select></label>
        <div id="ao-ev-fields"></div>`;
    else if (op === 8) h = `<label class="tp-row">prompt ${msgIn("ao-prompt")}</label>
        <div class="muted small">keyword string ids (0 = unused) — EXPERIMENTAL: die Branch-Daten hinter der Kette sind un-RE'd</div>
        ${rowsHtml("ao-kw", [{ f: "kw", min: 0, max: 127, v: 0, t: "keyword string id" }], 4)}`;
    else if (op === 12) h = `<label class="tp-row">message id ${msgIn("ao-msg")}</label>
        <div class="muted small">patches: [flags][dx][dy][op][cell-id] — flags bit0 = party-relative; op 255 = no write</div>
        ${rowsHtml("ao-pat", [{ f: "flags", min: 0, max: 127, v: 0, t: "flags (bit0 party-rel)" }, { f: "dx", min: -128, max: 127, v: 0, t: "dx" }, { f: "dy", min: -128, max: 127, v: 0, t: "dy" }, { f: "op", min: 0, max: 255, v: 0, t: "new op-nibble" }, { f: "cell_id", min: 0, max: 255, v: 0, t: "new cell-id" }], 4)}`;
    else if (op === 4) h = `<label class="tp-row">message id ${msgIn("ao-msg", 0)}</label>`;
    else if (op === 9) h = `<label class="tp-row">message id ${msgIn("ao-msg", 0)}</label>
        <label class="tp-row">damage dice <input id="ao-dice" type="number" min="0" max="255" value="1" title="× RNG roll → CON on EVERY member, + poison"></label>`;
    else if (op === 11) h = `<label class="tp-row">add step
          <select id="ao-step-op" title="action type for the next step">
            <option value="11">$B BUMP (message)</option>
            <option value="1">$1 SAY (message)</option>
            <option value="5">$5 LOOT (item sack)</option></select>
          <button type="button" id="ao-chain-add" class="mini" title="append a step of the chosen type (nothing is written yet)">＋ add step</button></label>
        <div id="ao-chain-list" class="ao-chain"></div>
        <label class="tp-row"><input type="checkbox" id="ao-chain-loop" ${chainInitLoop ? "checked" : ""}> loop — last step → first (ring); off = stop after the last</label>
        <p class="muted small">One interaction walks the whole sequence (bump→bump→LOOT→bump). LOOT items = "itemid:qty, …" (ITEM_TABLE ids; $5E = cash).</p>`;
    $("ao-fields").innerHTML = h;
    if (op === 6) evFields();
  };
  const syncMode = () => {
    fields();
    const isChain = +$("ao-op").value === 11;
    $("ao-add").textContent = isChain ? "💾 Save chain" : "Add op";
    if (isChain) {
      renderChainList();
      const ab = $("ao-chain-add"); if (ab) ab.addEventListener("click", () => {
        const sop = +$("ao-step-op").value;
        chainLinks.push(sop === 5 ? { op: 5, items: [] } : { op: sop, message: 0 });
        renderChainList();
      });
    }
  };
  $("ao-op").addEventListener("change", syncMode);
  if (chainLinks.length) $("ao-op").value = "11";        // cell already has a chain → open it for editing
  syncMode();
  $("ao-fields").addEventListener("change", (e) => { if (e.target.id === "ao-ev-kind") evFields(); });
  $("ao-add").addEventListener("click", () => (+$("ao-op").value === 11 ? saveChain(side, id, col, row) : addOp(side, id, col, row)));
  $("ao-move").addEventListener("click", () => moveOp(side, id, col, row));
}

// ── op-B chain builder: stage N bump-message links, then write them as ONE ring/chain of records ──
let chainLinks = [];        // [{message}] staged/loaded for the current cell
let chainTexts = null;      // id -> text snippet cache (per area), best-effort for the preview
let chainInitLoop = true;   // loop toggle default = the loaded chain's loop state

const STEP_OPS = [[11, "BUMP"], [1, "SAY"], [5, "LOOT"]];   // the chainable step types the builder offers
const itemsToStr = (its) => (its || []).map((it) => `${it.item}:${it.qty}`).join(", ");
const parseItems = (s) => s.split(",").map((x) => x.trim()).filter(Boolean).map((x) => {
  const [i, q] = x.split(":"); return { item: +i || 0, qty: +(q || 1) || 1 };
});

function renderChainList() {
  const box = $("ao-chain-list"); if (!box) return;
  if (!chainLinks.length) { box.innerHTML = `<div class="muted small">no steps yet — choose a type and press ＋ add step</div>`; return; }
  const loop = $("ao-chain-loop") && $("ao-chain-loop").checked;
  const snippet = (m) => (chainTexts && chainTexts[m] != null) ? chainTexts[m].slice(0, 26) : "";
  box.innerHTML = chainLinks.map((l, k) => {
    const op = l.op || 11;
    const arrow = k < chainLinks.length - 1 ? "→" : (loop ? "↺" : "▣");
    const opSel = `<select class="ao-link-op" data-k="${k}" title="step type" style="width:64px">${
      STEP_OPS.map(([v, t]) => `<option value="${v}"${op === v ? " selected" : ""}>${t}</option>`).join("")}</select>`;
    const field = op === 5
      ? `<input class="ao-link-items" data-k="${k}" value="${itemsToStr(l.items)}" placeholder="itemid:qty, …" style="width:148px" title="LOOT sack contents (ITEM_TABLE ids)">`
      : `str <input class="ao-link-msg" data-k="${k}" type="number" min="0" max="127" value="${l.message || 0}" style="width:50px" title="string id"><span class="muted ao-link-txt">${snippet(l.message)}</span>`;
    return `<div class="ao-link"><span class="ao-ln">${k + 1}</span>
      <span class="ao-arrow muted" title="${k < chainLinks.length - 1 ? "→ next step" : (loop ? "loops to first" : "stop")}">${arrow}</span>
      ${opSel} ${field}
      <button type="button" class="mini ao-link-up" data-k="${k}" title="move up"${k === 0 ? " disabled" : ""}>↑</button>
      <button type="button" class="mini ao-link-dn" data-k="${k}" title="move down"${k === chainLinks.length - 1 ? " disabled" : ""}>↓</button>
      <button type="button" class="mini ao-link-del" data-k="${k}" title="remove">✕</button></div>`;
  }).join("");
  const move = (k, d) => { const t = chainLinks[k]; chainLinks[k] = chainLinks[k + d]; chainLinks[k + d] = t; renderChainList(); };
  box.querySelectorAll(".ao-link-op").forEach((el) => el.addEventListener("change", () => {
    const k = +el.dataset.k, v = +el.value;
    chainLinks[k] = v === 5 ? { op: 5, items: chainLinks[k].items || [] } : { op: v, message: chainLinks[k].message || 0 };
    renderChainList();
  }));
  box.querySelectorAll(".ao-link-msg").forEach((el) => el.addEventListener("input", () => {
    chainLinks[+el.dataset.k].message = +el.value;
    const t = el.parentElement.querySelector(".ao-link-txt"); if (t) t.textContent = snippet(+el.value);
  }));
  box.querySelectorAll(".ao-link-items").forEach((el) => el.addEventListener("input", () => {
    chainLinks[+el.dataset.k].items = parseItems(el.value);
  }));
  box.querySelectorAll(".ao-link-up").forEach((b) => b.addEventListener("click", () => move(+b.dataset.k, -1)));
  box.querySelectorAll(".ao-link-dn").forEach((b) => b.addEventListener("click", () => move(+b.dataset.k, 1)));
  box.querySelectorAll(".ao-link-del").forEach((b) => b.addEventListener("click", () => { chainLinks.splice(+b.dataset.k, 1); renderChainList(); }));
  const lp = $("ao-chain-loop"); if (lp && !lp._wired) { lp._wired = true; lp.addEventListener("change", renderChainList); }
}

async function saveChain(side, id, col, row) {
  if (!chainLinks.length) { status("add at least one step first"); return; }
  const loop = $("ao-chain-loop") ? $("ao-chain-loop").checked : false;
  await flushLayers();
  const steps = chainLinks.map((l) => ({ op: l.op || 11, message: l.message || 0, items: l.items || [] }));
  const pureBump = steps.every((s) => s.op === 11);
  let r;
  if (pureBump) {                                          // pure bump chain → clean grp11 rebuild (no growth)
    r = await fetch(`/api/area/${side}/${id}/add-chain`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ col, row, op: 0xB, links: steps.map((s) => ({ message: s.message })), loop }),
    });
  } else {                                                 // heterogeneous → general script-chain writer
    r = await fetch(`/api/area/${side}/${id}/add-script-chain`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ col, row, loop, steps: steps.map((s) => s.op === 5 ? { op: 5, items: s.items } : { op: s.op, message: s.message }) }),
    });
  }
  if (!r.ok) { status(`chain err ${r.status}: ${(await r.json().catch(() => ({}))).detail || ""}`); return; }
  status(`chain saved: ${steps.length} step${steps.length === 1 ? "" : "s"} (${loop ? "loop" : "stop"})`);
  chainLinks = [];
  await loadArea(side, id); inspectCell(col, row);
}

// op-6 sub-form: grp16 event id, OR a $CA00 service record (selector + portrait/label/…)
function evFields() {
  const kind = $("ao-ev-kind") ? $("ao-ev-kind").value : "grp16";
  const box = $("ao-ev-fields"); if (!box) return;
  if (kind === "grp16") {
    box.innerHTML = `<label class="tp-row">grp16 event id <input id="ao-ev" type="number" min="0" max="127" value="0"></label>
      <p class="muted small">Fires the area's own 6502 event (Code tab).</p>`;
  } else {
    box.innerHTML = `<label class="tp-row">service <select id="ao-svc">
        <option value="5">$1 category shop (05)</option>
        <option value="6">Superdoc — full heal (06)</option>
        <option value="7">Revive — Sentinel (07)</option>
        <option value="8">Bank — Centaur2 (08)</option></select></label>
      <label class="tp-row">portrait str ${msgIn("ao-svc-port", 79)}</label>
      <label class="tp-row">greeting str ${msgIn("ao-svc-greet", 0)}</label>
      <label class="tp-row">label <input id="ao-svc-label" maxlength="12" value="MELEE" style="width:110px"></label>
      <div id="ao-svc-shop"><label class="tp-row">item id range <input id="ao-svc-lo" type="number" min="0" max="255" value="0" style="width:52px"> .. <input id="ao-svc-hi" type="number" min="0" max="255" value="0" style="width:52px"></label></div>
      <p class="muted small">Builds the op-6 service record. The handler blob (s1_scene_id&lt;sel&gt;) ships via build_scenes.sh. Item range only for the shop.</p>`;
    const shopOnly = () => { $("ao-svc-shop").style.display = $("ao-svc").value === "5" ? "" : "none"; };
    $("ao-svc").addEventListener("change", shopOnly); shopOnly();
  }
}

function collectRows(cls, fields) {
  const rows = {};
  document.querySelectorAll(`input.${cls}`).forEach((el) => {
    (rows[el.dataset.k] = rows[el.dataset.k] || {})[el.dataset.f] = +el.value;
  });
  return Object.values(rows);
}

async function addOp(side, id, col, row) {
  const op = +$("ao-op").value;
  let params = {};
  if (op === 10) params = { target: +$("ao-tgt").value, x: +$("ao-x").value, y: +$("ao-y").value, message: +$("ao-msg").value };
  else if (op === 1) params = { message: +$("ao-msg").value };
  else if (op === 2) params = {
    prompt: +$("ao-prompt").value, success: +$("ao-succ").value, fail: +$("ao-fail").value,
    done_op: +$("ao-dop").value, done_id: +$("ao-did").value,
    eff_target: +$("ao-efft").value, eff_value: +$("ao-effv").value,
    conds: collectRows("ao-cond").filter((c) => c.op || c.arg),
  };
  else if (op === 5) params = { message: +$("ao-msg").value, items: collectRows("ao-item").filter((i) => i.item) };
  else if (op === 6) {
    const kind = $("ao-ev-kind") ? $("ao-ev-kind").value : "grp16";
    params = kind === "service"
      ? { selector: +$("ao-svc").value, portrait: +$("ao-svc-port").value, greeting: +$("ao-svc-greet").value,
          label: $("ao-svc-label").value, item_lo: +($("ao-svc-lo") || {}).value || 0, item_hi: +($("ao-svc-hi") || {}).value || 0 }
      : { event: +$("ao-ev").value };
  }
  else if (op === 8) params = { prompt: +$("ao-prompt").value, keywords: collectRows("ao-kw").map((k) => k.kw).filter(Boolean) };
  else if (op === 12) params = { message: +$("ao-msg").value,
    patches: collectRows("ao-pat").filter((p) => p.op || p.dx || p.dy || p.cell_id).map((p) => ({ ...p, dx: p.dx & 0xFF, dy: p.dy & 0xFF })) };
  else if (op === 4) params = { message: +$("ao-msg").value };
  else if (op === 9) params = { message: +$("ao-msg").value, dice: +$("ao-dice").value };
  // op === 11 (chain) never reaches here — it routes to saveChain(), not addOp()
  await flushLayers();
  const r = await fetch(`/api/area/${side}/${id}/add-op`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ col, row, op, params }),
  });
  if (!r.ok) { status(`add-op err ${r.status}: ${(await r.json().catch(() => ({}))).detail || ""}`); return; }
  const j = await r.json(); status(`added op $${op.toString(16).toUpperCase()} → dispatch ${j.dispatch}`);
  await loadArea(side, id); inspectCell(col, row);
}

// move THIS cell's op (nibble+cell-id) to a picked target cell — pure grid edit, record untouched
async function moveOp(side, id, col, row) {
  const i = row * area.width + col;
  if (!area.nibble[i]) { status("this cell has no op to move"); return; }
  status("click the TARGET cell for this op…");
  pickCellCb = async (tc, tr) => {
    if (dirty) await saveMap();   // moveOp posts nibble/cellid itself; only the tile layer needs flushing
    const nib = [...area.nibble], cid = [...area.cellid];
    const j = tr * area.width + tc;
    nib[j] = nib[i]; cid[j] = cid[i];
    nib[i] = 0; cid[i] = 0;
    const r = await fetch(`/api/area/${side}/${id}/nibble`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ nibble: nib, cellid: cid }) });
    if (!r.ok) { status(`move err ${r.status}`); return; }
    status(`op moved (${col},${row}) → (${tc},${tr})`);
    await loadArea(side, id); inspectCell(tc, tr);
  };
}

// ── script editing: each field is a single record byte at a fixed offset (in-place, no resize) ──
let editFields = [];
let txtOrig = {};                       // id -> text as loaded, to detect the diff

function txtDirtyEdits() {
  const out = [];
  document.querySelectorAll("#txt-list .txt-ta").forEach((ta) => {
    const id = +ta.dataset.tid;
    if (ta.value !== (txtOrig[id] ?? "")) out.push({ id, text: ta.value });
  });
  return out;
}

let txtValTimer = null;
function txtOnEdit() {
  document.querySelectorAll("#txt-list .txt-ta").forEach((ta) =>
    ta.classList.toggle("dirty", ta.value !== (txtOrig[+ta.dataset.tid] ?? "")));
  clearTimeout(txtValTimer);
  txtValTimer = setTimeout(txtValidate, 250);   // debounce the char-table check while typing
  txtRefresh();
}

function txtRefresh() {
  const n = txtDirtyEdits().length;
  $("txt-save").disabled = n === 0;
  $("txt-revert").disabled = n === 0;
  $("txt-save").textContent = n ? `Save ${n}` : "Save";
}

async function txtValidate() {
  const edits = txtDirtyEdits();
  const el = $("txt-budget");
  if (!edits.length) { el.textContent = ""; el.className = "txt-budget"; return true; }
  const r = await fetch(`/api/area/${curSide}/${curAid}/prose/validate`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ edits }) });
  if (!r.ok) { el.textContent = ""; return true; }
  const b = await r.json();
  if (b.fits) {
    const add = b.needed.length ? ` · adds ${b.needed.join(" ")}` : "";
    el.textContent = `char table: ${b.free} free${add}`;
    el.className = "txt-budget ok";
  } else {
    const drop = b.droppable.length ? ` — unused chars you could drop: ${b.droppable.join(" ")}` : "";
    el.textContent = `WON'T FIT: ${b.free} slots free, needs ${b.needed.length} new. `
      + `Can't place: ${b.overflow.join(" ")}${drop}`;
    el.className = "txt-budget over";
  }
  $("txt-save").disabled = !b.fits || !edits.length;
  return b.fits;
}

async function txtSave() {
  const edits = txtDirtyEdits();
  if (!edits.length) return;
  if (!(await txtValidate())) { status("text not saved — char table full, see the note"); return; }
  const r = await fetch(`/api/area/${curSide}/${curAid}/prose`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ edits }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { status(`text save err ${r.status}: ${j.detail || ""}`); return; }
  status(`saved ${edits.length} string(s) (Δ${j.size_delta} B) → work/`);
  loadArea(curSide, curAid);
}

function renderCellEdit(side, id, col, row, fields, op) {
  editFields = fields;
  const box = $("cell-edit");
  if (op === 0xB) {   // op-B is edited by the chain editor (above); no raw byte editor (it corrupted)
    box.innerHTML = "";
    return;
  }
  if (!fields.length) { box.innerHTML = ""; return; }
  const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  // EVERY inline <textarea> in this file opens with a literal newline before the value. The HTML
  // parser DROPS a single newline directly after the opening tag, so without one a string that
  // starts with "\n" loses it — and 2336 of the game's strings do ("\nEntering Highpool.\n").
  // Worse than cosmetic: the dirty check below is `el.value !== f.text`, so such a field counts as
  // edited the moment it is rendered, and the next Save writes it back one newline short without
  // anyone having touched it. The extra newline is eaten by the parser and the value round-trips.
  const rows = fields.map((f, k) => {
    if (f.kind === "strid") {   // str-reference: show id + $80 flag separately (raw byte = id | flag)
      const ta = `<textarea class="ef-prose" data-k="${k}" data-strid="${f.str_id}" rows="2" placeholder="(empty)">\n${esc(f.text)}</textarea>`;
      const flagged = (f.value & 0x80) !== 0;
      const zero = (f.value & 0x7F) === 0;   // id 0 = the engine's "no message" slot — never shown
      return `<tr><td>${f.label}<br><span class="ef-hint">text #${f.str_id}</span></td>
        <td>text <input type="number" data-k="${k}" data-kind="u8ref" min="0" max="127" value="${f.value & 0x7F}">
        <label class="ef-hint" title="bit7 of the raw byte (e.g. last-message in a SAY chain, party-relative on a transition)"><input type="checkbox" data-flag="${k}" ${flagged ? "checked" : ""}> $80 flag</label>${ta}
        ${zero ? `<div class="ef-warn">⚠ id 0 = no message (engine's empty slot — never shown). Use id ≥ 1.</div>` : ""}</td><td></td></tr>`;
    }
    const hint = f.hint ? `<span class="ef-hint">${esc(f.hint)}</span>` : "";
    let input;
    if (f.kind === "opnibble") {
      input = `<select data-k="${k}">${Array.from({ length: 16 }, (_, v) =>
        `<option value="${v}" ${v === f.value ? "selected" : ""}>$${v.toString(16).toUpperCase()} ${OP_NAMES[v]}</option>`).join("")
        }<option value="${f.value}" ${f.value > 15 ? "selected" : ""} ${f.value > 15 ? "" : "hidden"}>$${f.value.toString(16).toUpperCase()} escape</option></select>`;
    } else {
      // u16 = a 16-bit field stored as [lo][hi] (LOOT money). It must not be capped at 255 —
      // that is the value the record actually holds, and clamping it would silently rewrite it.
      const min = f.kind === "s8" ? -128 : 0;
      const max = f.kind === "s8" ? 127 : f.kind === "u16" ? 65535 : 255;
      const shown = f.kind === "s8" && f.value > 127 ? f.value - 256 : f.value;
      input = `<input type="number" data-k="${k}" data-kind="${f.kind}" min="${min}" max="${max}" value="${shown}">`;
    }
    return `<tr><td>${f.label}</td><td>${input}</td><td>${hint}</td></tr>`;
  }).join("");
  // Identical layout in View + Edit — CSS (.ro-view) greys the inputs + hides the button row in View.
  box.innerHTML = `<div class="insp-sec ef-box"><div class="insp-title">✎ edit script</div>
    <table class="ef-tbl">${rows}</table>
    <div class="btns"><button id="ef-save">Save script</button><button id="ef-reset">Reset</button><button id="ef-clear" title="set this cell's op → 0 (delete the script here)">Clear cell</button></div>
    <p class="muted small">Edits → <code>work/</code>. Numbers = in-place bytes; textareas edit the
      string's <b>text</b> (shared everywhere that id is used). New chars auto-extend the record's
      alphabet — full tables (~60 chars) may reject a char.</p></div>`;
  $("ef-save").addEventListener("click", () => saveScript(side, id, col, row));
  $("ef-reset").addEventListener("click", () => inspectCell(col, row));
  if ($("ef-clear")) $("ef-clear").addEventListener("click", () => clearCell(side, id, col, row));
}
// delete a cell's op/logic: op-nibble + cell-id → 0, then save the grid. The subtable record it used
// is left orphaned (harmless — nothing dispatches to it). Same effect as the Physique Erase tool.
async function clearCell(side, id, col, row) {
  if (dirty) await saveMap();   // posts nibble/cellid + reloads — flush the tile layer first
  const i = row * area.width + col;
  area.nibble[i] = 0; area.cellid[i] = 0;
  const r = await fetch(`/api/area/${side}/${id}/nibble`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ nibble: area.nibble, cellid: area.cellid }),
  });
  if (!r.ok) { status(`clear err ${r.status}`); return; }
  status(`cleared cell (${col},${row}) → op 0`);
  await loadArea(side, id); inspectCell(col, row);
}
async function saveScript(side, id, col, row) {
  await flushLayers();   // in-place script edits reload the area — keep pending paint
  // byte edits from number inputs (fixed-length, in-place); prose edits from textareas (rebuild)
  const edits = [], prose = [];
  $("cell-edit").querySelectorAll("input[data-k]").forEach((el) => {
    const f = editFields[+el.dataset.k]; let v = parseInt(el.value, 10);
    if (isNaN(v)) return;
    if (el.dataset.kind === "s8" && v < 0) v += 256;
    if (el.dataset.kind === "u8ref") {   // str ref: recombine id + the $80 flag checkbox
      const fl = $("cell-edit").querySelector(`input[data-flag="${el.dataset.k}"]`);
      v = (v & 0x7F) | (fl && fl.checked ? 0x80 : 0);
    }
    // width tells the writer how many bytes this field occupies; without it a u16 money amount
    // would be truncated to its low byte on save (1000 credits -> 232).
    edits.push(f.kind === "u16" ? { off: f.off, value: v & 0xFFFF, width: 2 }
                                : { off: f.off, value: v & 0xFF });
  });
  $("cell-edit").querySelectorAll("textarea[data-k]").forEach((el) => {
    const f = editFields[+el.dataset.k];
    if (el.value !== (f.text || "")) prose.push({ id: +el.dataset.strid, text: el.value });
  });
  // script (byte) edits first, then prose (text-region rebuild) — both read-modify-write work/record
  if (edits.length) {
    const r = await fetch(`/api/area/${side}/${id}/script`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ edits }) });
    if (!r.ok) { status(`script save err ${r.status}`); return; }
  }
  if (prose.length) {
    const r = await fetch(`/api/area/${side}/${id}/prose`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ edits: prose }) });
    if (!r.ok) { status(`prose save err ${r.status}: ${(await r.json()).detail || ""}`); return; }
    const j = await r.json(); status(`saved: ${edits.length} byte(s) + ${j.strings_changed} string(s) (Δ${j.size_delta} B)`);
  } else {
    status(`saved script (${edits.length} byte edits)`);
  }
  await loadArea(side, id); inspectCell(col, row);   // reload so decode + mutation list reflect the edit
}

let foeTemplates = null;                 // the shipped-record difficulty menu, fetched once
let areaFoes = [];
const fesc = (t) => String(t == null ? "" : t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

async function renderAreaFoes(side, id) {
  const list = $("npc-list"); list.innerHTML = `<span class="muted small">…</span>`;
  const { services, orphans } = await (await fetch(`/api/area/${side}/${id}/npcs`)).json();
  renderServicePortraits(side, id, services || []);
  renderOrphanNpcs(orphans || []);
  // the FULL table, not the deduped/filtered portrait strip: a foe you just made and have not
  // wired to an encounter yet must still be visible, or you cannot finish wiring it.
  areaFoes = (await (await fetch(`/api/area/${side}/${id}/foes`)).json()).foes || [];
  if (!areaFoes.length) { list.innerHTML = `<span class="muted small">no foes in this area</span>`; return; }
  list.innerHTML = "";
  for (const n of areaFoes) {
    const t = document.createElement("div");
    // Three states, not two. A foe no record names is usually not dead weight — foes 1..desc+$31
    // are the WANDERING POOL the engine rolls from once a turn, which is what Quartz's seven street
    // thugs are. Only what is neither referenced nor in the pool is genuinely unaccounted for.
    const roam = n.wandering || n.wandering_grown;
    t.className = "npc-thumb" + (n.used || roam ? "" : " unnamed") + (roam && !n.used ? " roaming" : "");
    const where = (n.cells || []).length
      ? `spawns at ${n.cells.map((c) => c.join(",")).join(" · ")}`
      : n.used ? `no map cell — planted by ${(n.referenced_by || []).join(", ")}`
      : n.wandering ? "no record names it because none has to: it is in this area's wandering pool "
                      + "(foes 1..desc+$31). Once a turn the engine rolls that range and plants the "
                      + "winner as an op-$0F cell."
      : n.wandering_grown ? "in the wandering pool only after a grp16 event GROWS it — see World "
                            + "> Changed in play."
      : "nothing in this area's records names it, and it is outside the wandering pool. Genuinely "
        + "unaccounted for.";
    t.title = `${n.name} · foe #${n.id} · ${n.kind_name} · portrait str ${n.portrait_str}\n` +
              `con ${n.con} (= exp) · ${n.hp_min}-${n.hp_max} HP · ${n.dmg_min}-${n.dmg_max} damage · ` +
              `armour ${n.armour} · groups of 1-${n.group_max}\n${where}\nclick = edit`;
    t.innerHTML = `<img src="/api/portrait/${n.side}/${n.portrait_str}" onerror="this.parentElement.classList.add('missing')">` +
                  `<span>${fesc(n.name)}<br><span class="muted">${n.exp} exp` +
                  `${n.used ? "" : roam ? "<br>wandering" : "<br>unnamed"}</span></span>`;
    t.addEventListener("click", () => foePanel(side, id, n));
    const b = document.createElement("button");
    b.className = "mini svc-jump"; b.textContent = "↗"; b.title = `open portrait str ${n.portrait_str}`;
    b.addEventListener("click", (ev) => { ev.stopPropagation(); setTab("portraits"); ptOpen(n.side, n.portrait_str, n.name); });
    t.appendChild(b);
    list.appendChild(t);
  }
}

// ── FOE editor (AREA Data tab, left) ─────────────────────────────────────────────────────────
// A foe is a NAME plus an 8-byte record in two parallel tables — not a map object, so there is no
// origin to place, only fields. All eight bytes are RE'd (cart_EF/docs/engine/FOE_RECORD.md), so
// the panel names them instead of offering "as tough as <a shipped foe>" and four raw guesses:
//   +0/+1 CON, 16-bit — the HP seed AND the experience the kill pays
//   +2 range · +3 damage dice (d6 each) · +4 lo armour / hi max group size
//   +5 lo attack type / hi damage base · +6 KIND · +7 portrait
// Two pairs share a byte, which is why the fields are nibbles and why writing whole bytes was
// wrong. A PRESET still exists — it seeds every field at once — but it no longer decides.
// The five kinds come from the API (wl_foe.FOE_KINDS) — /api/foe-templates has always returned
// them, this file just used to throw them away and keep its own copy. That copy went stale: it
// still said "undead/robot/turret" after the backend adopted wastelib's MobType names. One table.
let foeKinds = null;
const FOE_KINDS_FALLBACK = {                     // only if the fetch fails; the panel still renders
  1: { tile: 6, name: "animal" }, 2: { tile: 3, name: "mutant" }, 3: { tile: 4, name: "humanoid" },
  4: { tile: 2, name: "cyborg" }, 5: { tile: 1, name: "robot" },
};
const foeKind = () => foeKinds || FOE_KINDS_FALLBACK;

async function foePanel(side, id, n) {
  const box = $("foe-edit");
  if (!foeTemplates) {
    box.innerHTML = `<span class="muted small">loading presets…</span>`;
    try {
      const r = await (await fetch(`/api/foe-templates`)).json();
      foeTemplates = r.templates || [];
      foeKinds = r.kinds || null;
    } catch (e) { foeTemplates = []; }
  }
  const v = n || { id: null, name: "", name_plural: "", portrait_str: 37, record: null };
  const unhex = (h) => ((h || "").replace(/^hex:/, "").match(/../g) || []).map((x) => parseInt(x, 16));
  // An existing foe brings its own bytes; a new one starts from the gentlest shipped record rather
  // than from zeros, which are not a foe any of these fields describe.
  let cur8 = unhex(v.record);
  if (cur8.length < 8) cur8 = unhex((foeTemplates[0] || {}).record);
  if (cur8.length < 8) cur8 = [4, 0, 1, 2, 0x11, 0x01, 3, 37];
  // …and the 6-byte key the preset list is deduped by, so the menu can show WHICH shipped record
  // this foe currently matches. It used to slice(5,17) — one past "hex:" — so nothing ever matched.
  const cur6 = cur8.slice(0, 6).map((b) => b.toString(16).padStart(2, "0")).join("");
  const num = (f, val, max) => `<input id="fe-${f}" class="fe-num" type="number" min="0" max="${max}" value="${val}">`;
  box.innerHTML = `<div class="insp-sec ef-box">
    <div class="insp-title">${n ? `✎ Foe #${v.id} — ${fesc(v.name)}` : "＋ new foe"}</div>
    <table class="ef-tbl">
      <tr><td title="combat prints '1 Madman' or '3 Madmen' out of ONE table entry — the engine
        picks the word by head-count, so both live here. Leave the plural equal to the singular for
        a name that has none.">name<br>plural</td>
        <td><span class="fe-pair"><input id="fe-name" maxlength="13" value="${fesc(v.name)}">
        <input id="fe-name-plural" maxlength="13" placeholder="plural"
               value="${fesc(v.name_plural ?? "")}"></span></td></tr>
      <tr><td>portrait</td><td><span class="fe-fig">${num("port", v.portrait_str ?? 37, 127)}
        <img id="fe-port-img" src="/api/portrait/${side}/${v.portrait_str ?? 37}"
             onerror="this.style.opacity=.2"></span></td></tr>
      <tr><td title="one byte, three consequences: the map figure, the default combat name, and
        whether the AI closes to melee or holds at range">kind</td>
        <td><span class="fe-fig"><select id="fe-kind">${Object.keys(foeKind()).map((k) =>
            `<option value="${k}"${+k === cur8[6] ? " selected" : ""}>${k} · ${foeKind()[k].name}</option>`).join("")}</select>
        <canvas id="fe-fig" width="34" height="34" title="the map figure this kind draws"></canvas></span></td></tr>
      <tr><td title="copies a shipped foe's combat numbers into the fields below">preset</td>
        <td><select id="fe-tpl">
          <option value="">— copy a shipped foe's numbers —</option>
          ${foeTemplates.map((t) => `<option value="${t.record}"${t.record.replace("hex:", "").slice(0, 12) === cur6 ? " selected" : ""}>` +
              `${t.con} con · ${t.dmg_min}-${t.dmg_max} dmg — ${fesc(t.name)}</option>`).join("")}
        </select></td></tr>
      <tr><td title="16-bit. The HP seed and the experience the kill pays are the same number">con</td>
        <td>${num("con", cur8[0] | (cur8[1] << 8), 65535)}
        <span id="fe-hp" class="ef-hint"></span></td></tr>
      <tr><td title="each die is a d6">damage</td>
        <td><span class="fe-pair"><label>dice ${num("dice", cur8[3], 255)}</label>
        <label>base ${num("dmg_base", cur8[5] >> 4, 15)}</label></span>
        <div id="fe-dmg" class="ef-hint"></div></td></tr>
      <tr><td title="armour subtracts from the attacker's dice — fewer dice than this and the hit
        does nothing at all. Range: melee foes ship at 1, turrets around 30">armour<br>range</td>
        <td><span class="fe-pair"><label>arm ${num("armour", cur8[4] & 15, 15)}</label>
        <label>rng ${num("range", cur8[2], 255)}</label></span></td></tr>
      <tr><td title="attack = weapon class, sharing the id space the party's weapons use">group<br>attack</td>
        <td><span class="fe-pair"><label>grp ${num("group_max", cur8[4] >> 4, 15)}</label>
        <label>atk ${num("attack_type", cur8[5] & 15, 15)}</label></span>
        <div id="fe-grp" class="ef-hint"></div></td></tr>
    </table>
    <div class="btns"><button id="fe-save" class="mini">${n ? "Save" : "Create"}</button>
      <button id="fe-close" class="mini">Close</button></div>
    <div id="fe-msg" class="muted small"></div></div>`;
  // Every derived number the engine computes from these fields, said out loud — so nobody has to
  // recompute (con>>2)+rnd(con) or base+n×d6 in their head to know what they just typed.
  const echo = () => {
    const con = +$("fe-con").value || 0, dice = +$("fe-dice").value || 0;
    const base = +$("fe-dmg_base").value || 0, grp = +$("fe-group_max").value || 0;
    $("fe-hp").textContent = `${con} exp · ${(con >> 2) + 1}-${(con >> 2) + con} HP`;
    $("fe-dmg").textContent = `${base} + ${dice}d6 = ${base + dice}-${Math.min(255, base + 6 * dice)} damage`;
    $("fe-grp").textContent = grp ? `raises 1-${grp} per encounter`
                                  : "0 — never randomly raised";
  };
  const drawFig = () => {
    const cv = $("fe-fig"); if (!cv) return;
    const c = cv.getContext("2d"); c.clearRect(0, 0, cv.width, cv.height);
    if (mapAtlas) blit(c, mapAtlas, (foeKind()[+$("fe-kind").value] || {}).tile ?? 0, 0, 0, cv.width, cv.height);
  };
  echo(); drawFig();
  // The plural follows the name while it is still the regular one — 276 of the 410 shipped entries
  // are exactly name+"s". The moment somebody types their own (Madmen, Bobbies, Police), it stops
  // following, because an irregular plural is a fact about the word and not a default.
  $("fe-name").addEventListener("input", (e) => {
    const p = $("fe-name-plural");
    if (!p.value || p.value === (p.dataset.auto || "")) {
      p.value = e.target.value ? e.target.value + "s" : "";
      p.dataset.auto = p.value;
    }
  });
  ["con", "dice", "dmg_base", "group_max"].forEach((f) => $(`fe-${f}`).addEventListener("input", echo));
  $("fe-kind").addEventListener("change", drawFig);
  $("fe-tpl").addEventListener("change", (e) => {
    const t = unhex(e.target.value); if (t.length < 6) return;
    $("fe-con").value = t[0] | (t[1] << 8); $("fe-range").value = t[2]; $("fe-dice").value = t[3];
    $("fe-armour").value = t[4] & 15; $("fe-group_max").value = t[4] >> 4;
    $("fe-attack_type").value = t[5] & 15; $("fe-dmg_base").value = t[5] >> 4;
    echo();
  });
  $("fe-port").addEventListener("input", (e) => {
    $("fe-port-img").src = `/api/portrait/${side}/${e.target.value || 0}`; $("fe-port-img").style.opacity = 1;
  });
  $("fe-close").addEventListener("click", () => { box.innerHTML = ""; });
  $("fe-save").addEventListener("click", async () => {
    if (!areaStillOpen(side, id, "fe-msg")) return;
    const nm = $("fe-name").value.trim();
    const body = { name: nm, name_plural: $("fe-name-plural").value.trim() || nm,
                   portrait_str: +$("fe-port").value };
    ["con", "range", "dice", "dmg_base", "armour", "group_max", "attack_type", "kind"]
      .forEach((f) => { body[f] = +$(`fe-${f}`).value || 0; });
    const url = `/api/area/${side}/${id}/foe${n ? "/" + v.id : ""}${revQuery()}`;
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                                body: JSON.stringify(body) });
    if (!r.ok) { $("fe-msg").textContent = `save failed: ${(await r.text()).slice(0, 200)}`; return; }
    status(n ? `foe #${v.id} saved` : "foe created");
    box.innerHTML = "";
    await loadArea(side, id);
  });
}

// A service carries its OWN portrait id, which is neither a foe-record nor a grp17 entry — the
// third source, and the only one TREX Base has. Stock services (the 1988 originals) keep the image
// inside the scene blob instead, so they get a label tile with the reason as its tooltip.
function renderServicePortraits(side, id, svc) {
  // NOT "svc-list" — that id already belongs to the Services TAB's list. Two elements with one
  // id and $() would have returned the wrong box, silently rendering into the other tab.
  const box = $("svc-portraits"); if (!box) return;
  if (!svc.length) { box.innerHTML = `<span class="muted small">no services in this area</span>`; return; }
  box.innerHTML = "";
  for (const s of svc) {
    const t = document.createElement("div");
    t.className = "npc-thumb" + (s.portrait_str == null ? " missing" : "");
    t.title = `${s.kind} — grp6[${s.cid}]`
      + (s.portrait_str != null ? ` · str ${s.portrait_str}` : "")
      + (s.portrait_note ? `\n${s.portrait_note}` : "")
      + "\nclick = open this service";
    t.innerHTML = (s.portrait_str != null
      ? `<img src="/api/portrait/${s.side}/${s.portrait_str}" onerror="this.parentElement.classList.add('missing');this.remove()">` : "")
      + `<span>${s.label}</span>`;
    // The heading says "click to open the Services tab", so that is what a click does — the
    // portrait branch used to win and dropped you in the Portraits tab instead. The face is still
    // one click away: the ↗ badge opens it there.
    t.addEventListener("click", () => openService(s.cid));
    if (s.portrait_str != null) {
      const b = document.createElement("button");
      b.className = "mini svc-jump"; b.textContent = "↗"; b.title = `open portrait str ${s.portrait_str}`;
      b.addEventListener("click", (ev) => {
        ev.stopPropagation();
        setTab("portraits"); ptOpen(s.side, s.portrait_str, s.label);
      });
      t.appendChild(b);
    }
    box.appendChild(t);
  }
}

// Switch to the Services sub-tab and bring THIS service into view. loadServices() re-renders the
// list asynchronously, so the scroll waits for the card to exist rather than guessing a delay.
function openService(cid) {
  document.querySelector('[data-ap="services"]')?.click();
  let tries = 0;
  const find = () => {
    const el = document.querySelector(`#svc-list .svc[data-svc-cid="${cid}"]`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("svc-hit");
      setTimeout(() => el.classList.remove("svc-hit"), 1200);
    } else if (++tries < 40) setTimeout(find, 50);
  };
  find();
}

// grp17 records nothing points at. Not broken — the engine can still be sent to one by grp16 code
// or a save-state — but the NPC list is keyed by marker cell, so these had no way to show up.
function renderOrphanNpcs(orph) {
  const box = $("orph-list"), head = $("orph-head"); if (!box) return;
  if (head) head.hidden = !orph.length;
  box.innerHTML = "";
  for (const o of orph) {
    const t = document.createElement("div"); t.className = "npc-thumb missing";
    t.title = `grp17 #${o.n} — the record exists but no encounter offers it, so nothing spawns it`;
    t.innerHTML = `<span>${o.name}<br><span class="muted">grp17 #${o.n}</span></span>`;
    box.appendChild(t);
  }
}

// Clear the per-cell inspector/edit/add-op panels. MUST run on every area change: those panels
// capture the area's side/id in their save handlers, so a stale panel from area A would write into
// A while the map/dropdown show B (e.g. "RAWR! WELCOME TO TREX BASE" saved into Highpool). Flows
// that want a cell re-shown (add-op, move, cell-edit) call inspectCell() again after loadArea.
function clearCellInspect() {
  // 480px of column holding "click a cell to inspect" while the map beside it is cut off at the
  // bottom. An empty inspector gives its width back until it has something to say.
  $("cell-panel")?.classList.add("empty");
  const ins = $("cell-inspect"); if (ins) ins.innerHTML =
    `<div class="muted">click a cell to inspect it</div>`;
  const ed = $("cell-edit"); if (ed) ed.innerHTML = "";
  const ao = $("cell-addop"); if (ao) ao.innerHTML = "";
}

async function loadArea(side, id) {
  clearCellInspect();                                 // drop any stale cell panel from the previous area
  status(`loading area ${id}…`);
  const [ar, ts] = await Promise.all([
    fetch(`/api/area/${side}/${id}`).then((r) => r.ok ? r.json() : null),
    fetch(`/api/area/${side}/${id}/tileset`).then((r) => r.ok ? r.json() : null).catch(() => null),
  ]);
  if (!ar) { status(`error loading area ${id}`); return; }
  area = ar;
  if (!area.tileids || !area.tileids.length) area.tileids = new Array(area.width * area.height).fill(area.fill_tile || 0);
  mapAtlas = ts ? makeAtlas(ts) : null;
  await fillGfxPicker();
  // Size and fill only. The record blob's size vs build cap — with the over-cap warning and the
  // compact button — is the Record panel's job (renderRecPanel); it said the same thing twice, and
  // the work/work source badge is a fact about the repo, not about the map you are looking at.
  $("area-info").innerHTML = `${area.width}×${area.height}  fill $${area.fill_tile.toString(16).toUpperCase()}  ${mapAtlas ? "" : "⚠ no tileset"}`;
  status(`area ${id}: ${area.width * area.height} cells`);
  hover = null; setDirty(false);
  if (mode === "flow") flowLoad();             // Flow is a view OF this area — it follows the pick
  // physique: the area's generic wall dispatch = the most common op-$B cell-id (reuse its bump record)
  { const c = {}; let best = 0, bestN = -1;
    for (let i = 0; i < area.nibble.length; i++) if (area.nibble[i] === 0xB) { const id = area.cellid[i]; c[id] = (c[id] || 0) + 1; if (c[id] > bestN) { bestN = c[id]; best = id; } }
    wallId = best;
    const nsolid = area.nibble.filter((o) => SOLID_OPS.has(o)).length;
    if ($("phys-info")) $("phys-info").textContent = `${nsolid} solid cells · wall = op-$B id $${wallId.toString(16).toUpperCase().padStart(2, "0")}`;
  }
  setPhysDirty(false);
  curSide = side; curAid = id;
  renderRecPanel(side, id);
  drawMap(); resetZoom(); renderLegend(); renderPaintPalette(); closeAreaEditors(); renderAreaFoes(side, id); loadMutations(side, id); chainsLoad(side, id);
  loadHireables(side, id); renderTeleportPanel(id); applyLayout(); refreshRev(); renderFillTile();
  worldLoad(side, id); blkArmed = null; blkLoad(side, id);
  const at = document.querySelector("#area-tabs button.active");
  if (at && at.dataset.ap === "text") loadAreaTexts();
  if (at && at.dataset.ap === "code") loadAreaCode();
  if (at && at.dataset.ap === "combat") loadCombat();
  if (!$("map-history-wrap").hidden) mapHistoryLoad();   // keep the history panels in sync with the loaded area
  if (!$("rec-history-wrap").hidden) recHistoryLoad();
  $("map-diff-wrap").hidden = true; mapDiff = null;      // a diff is against the grid we just replaced
}

// Live-refresh the Map view after a tileset edit: if the loaded area uses that
// tileset, rebuild its atlas (pixel edits) — or fully reload it when the edit
// remapped the map's tile-ids on disk (⊟ pack).  gid = the edited gfx id.
async function syncMapAtlas(gid, remapped) {
  if (!area || area.gfx_id !== gid) return;
  if (remapped) {                                     // tileids changed on disk → reload
    const v = $("area-select").value; if (!v) return;
    const [s, i] = v.split(":").map(Number); await loadArea(s, i); return;
  }
  const ts = await (await fetch(`/api/tileset/1/${gid}`)).json();
  mapAtlas = makeAtlas(ts); drawMap();
}

let allTilesets = null;
async function fillGfxPicker() {
  const sel = $("area-gfx");
  try {
    if (!allTilesets) allTilesets = (await (await fetch("/api/tilesets")).json()).tilesets;
  } catch { allTilesets = null; }
  if (!allTilesets || !allTilesets.length) {   // fallback so the picker is never blank (Safari showed empty)
    sel.innerHTML = `<option value="${area.gfx_id}">tileset ${area.gfx_id}</option>`;
    return;
  }
  sel.innerHTML = allTilesets.map((t) => `<option value="${t.gfx_id}">tileset ${t.gfx_id}</option>`).join("");
  sel.value = String(area.gfx_id);
}

// Record overview + housekeeping (size vs build cap, string/foe counts, GC actions)
function renderRecPanel(side, id) {
  const info = $("rec-info"), panel = $("rec-panel");
  if (!info || !panel) return;
  const kb = (area.record_size / 1024).toFixed(1), over = area.record_size > area.record_cap;
  info.innerHTML = `<span class="${over ? "rec-over" : ""}">${kb} k / ${(area.record_cap / 1024).toFixed(1)} k cap</span>`;
  panel.innerHTML =
    `<div class="rec-row"><span class="muted">strings</span> <b id="rec-nstr">…</b>
       <button id="rec-gcstr" class="mini" title="remove template/unused strings — keeps only referenced text; map untouched">clean unused</button></div>
     <div class="rec-row"><span class="muted">size</span> <b>${area.record_size} B</b>
       <button id="rec-compact2" class="mini" title="reclaim orphaned dead space">compact</button></div>
     ${over ? `<div class="ef-warn">⚠ over build cap — clean/compact before building</div>` : ""}`;
  fetch(`/api/area/${side}/${id}/texts`).then((r) => r.json()).then((d) => {
    const el = $("rec-nstr"); if (el) el.textContent = d.messages.length;
  });
  $("rec-gcstr").addEventListener("click", async () => {
    if (!confirm("Remove unused/template strings from this area? (map & painting untouched; verified byte-safe)")) return;
    const r = await (await fetch(`/api/area/${side}/${id}/gc-strings`, { method: "POST" })).json();
    status(r.removed ? `removed ${r.removed} unused strings (record now ${r.size} B)` : "no unused strings found");
    await loadArea(side, id);
  });
  $("rec-compact2").addEventListener("click", async () => {
    const r = await (await fetch(`/api/area/${side}/${id}/compact`, { method: "POST" })).json();
    status(r.reclaimed ? `compacted: ${r.reclaimed} B reclaimed (now ${r.size} B)` : "nothing to reclaim");
    await loadArea(side, id);
  });
}
async function onGfxChange() {
  const [side, id] = $("area-select").value.split(":").map(Number);
  const gid = Number($("area-gfx").value);
  const r = await fetch(`/api/area/${side}/${id}/gfx`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ gfx_id: gid }) });
  const j = await r.json();
  status(`tileset → gfx ${gid}${j.gfx_available ? "" : " ⚠ blob missing"}`);
  loadArea(side, id);
}

async function saveMap() {
  const [side, id] = $("area-select").value.split(":").map(Number);
  const r = await fetch(`/api/area/${side}/${id}/tiles`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ tileids: area.tileids }) });
  if (!r.ok) { status(`save err ${r.status}`); return; }
  const j = await r.json(); setDirty(false); status(`saved ${j.saved} (${j.bytes_changed} B)`);
}

// Persist every UNSAVED in-memory layer before an op that reloads the area from disk
// (NPC/add-op/move all call loadArea, which would otherwise clobber pending paint).
// Map tiles live in area.tileids (dirty); physique nibble/cellid via savePhysics (physDirty).
async function flushLayers() {
  if (dirty) await saveMap();
  if (physDirty) await savePhysics();
}
async function revertMap() {
  const [side, id] = $("area-select").value.split(":").map(Number);
  await fetch(`/api/area/${side}/${id}/revert`, { method: "POST" });
  loadArea(side, id); status("reverted to baseline");
}
// ── BUILDING BLOCKS ───────────────────────────────────────────────────────────────────────────
// The objects a shipped map is built out of, cut along their own edges by segmenting the tile layer
// against whatever carpets the area. Offered per TILESET, because that is where a block can be
// drawn at all: of Quartz's 15 buildings, 15 render in gfx 3 and two in gfx 6 (wl_block).
//
// Armed like a stamp: pick one, then click the map. Placing resolves ROLES — a wall becomes this
// area's own op-$B id — and reports the doors as holes rather than wiring them to a record index
// that means something else here.
let blkLib = [], blkKind = "house", blkArmed = null, blkSheet = null;

async function blkLoad(side, id) {
  const box = $("blk-list"); if (!box || !area) return;
  const gfx = area.gfx_id ?? area.gfx ?? null;
  const d = await (await fetch(`/api/blocks/${gfx ?? 0}?all=1`)).json().catch(() => null);
  blkLib = (d?.blocks || []).sort((a, b) => (a.cost - b.cost) || (b.cells.length - a.cells.length));
  const ready = blkLib.filter((b) => b.cost === 0).length;
  // Areas SHARE tilesets, so importing a block edits a resource other areas draw from. It only ever
  // writes slots none of them points at — but say who is sharing, because "I edited Quartz" and
  // "I edited the tileset Quartz and Needles both use" are different sentences.
  // Not a slot count: a block brings its own tiles, and when they do not fit the answer is a new
  // tileset, not housekeeping. Say who else draws from this one, because that is what a fork undoes.
  const share = (d?.shared_with || []).length > 1
    ? ` · tileset ${gfx} is shared with ${d.shared_with.filter((n) => n !== area.name).join(", ")}` : "";
  $("blk-count").innerHTML = `${ready} ready · ${blkLib.length - ready} bring their own tiles${share}`;
  blkRender();
}

async function blkRender() {
  const box = $("blk-list"); if (!box || !area) return;
  const gfx = area.gfx_id ?? 0;
  const d = await (await fetch(`/api/blocks/${gfx}/sheet?kind=${blkKind}&all=1`)).json().catch(() => null);
  if (d) {
    const bytes = Uint8ClampedArray.from(atob(d.rgba_b64), (c) => c.charCodeAt(0));
    const cv = document.createElement("canvas"); cv.width = d.w; cv.height = d.h;
    cv.getContext("2d").putImageData(new ImageData(bytes, d.w, d.h), 0, 0);
    blkSheet = { cv, at: Object.fromEntries(d.at.map((a) => [a.id, a])) };
  }
  const list = blkLib.filter((b) => b.kind === blkKind);
  if (!list.length) { box.innerHTML = `<span class="muted small">nothing of that kind in this tileset</span>`; return; }
  // A block carries its own tiles, so a foreign one is not forbidden — it costs slots. Say the
  // price, and say when the destination has no room: every shipped tileset is FULL (0 of 96 free),
  // so importing means freeing something first.
  box.innerHTML = list.map((b) => `
    <div class="blk${b.cost ? " costs" : ""}${b.fits === false ? " blocked" : ""}" data-id="${b.id}"
         title="${b.w}×${b.h} · ${b.cells.length} cells · from ${b.seen_in.map((w) => w.name).join(", ")}${
           b.cost ? ` — brings ${b.cost} tile${b.cost === 1 ? "" : "s"} of its own`
                    + (b.fits === false ? `; this tileset has no free slot for them (Tilesets → clear unused)` : "") : ""}">
      <canvas class="blk-cv" data-id="${b.id}" width="${b.w * 12}" height="${b.h * 12}"></canvas>
      <span class="muted">${b.w}×${b.h}${b.cost ? ` +${b.cost}` : ""}</span>
    </div>`).join("");
  // drawn from the block's OWN art, not from this area's atlas — that is what self-contained means
  list.forEach((b) => {
    const cv = box.querySelector(`.blk-cv[data-id="${b.id}"]`);
    if (!cv) return;
    const g = cv.getContext("2d"); g.imageSmoothingEnabled = false;
    // from the block's OWN art, via the sheet — never through this area's atlas. Through the atlas
    // a foreign block draws whatever sits at the source indices HERE, which is how the palette came
    // to show houses made of Highpool rubble.
    const at = blkSheet?.at?.[b.id];
    if (at && blkSheet.cv) g.drawImage(blkSheet.cv, at.x, at.y, at.w, at.h, 0, 0, b.w * 12, b.h * 12);
    else { g.fillStyle = "#2f3743"; g.fillRect(0, 0, b.w * 12, b.h * 12); }
  });
  box.querySelectorAll(".blk").forEach((el) => el.addEventListener("click", () => {
    blkArmed = blkLib.find((x) => x.id === el.dataset.id) || null;
    box.querySelectorAll(".blk").forEach((x) => x.classList.toggle("on", x === el));
    $("blk-armed").innerHTML = blkArmed
      ? `armed <b>${blkArmed.kind} ${blkArmed.w}×${blkArmed.h}</b> — click the map to place it
         <button class="mini" id="blk-off">cancel</button>` : "";
    $("blk-off")?.addEventListener("click", () => {
      blkArmed = null; $("blk-armed").textContent = "";
      box.querySelectorAll(".blk").forEach((x) => x.classList.remove("on"));
    });
  }));
}

async function blkPlace(col, row) {
  if (!blkArmed || !area) return false;
  const [side, id] = $("area-select").value.split(":").map(Number);
  const post = (fork) => fetch(`/api/area/${side}/${id}/block/place`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ gfx: blkArmed.gfx, block: blkArmed.id, x: col, y: row, fork }),
  });
  let r = await post(false);
  let j = await r.json();
  if (!r.ok) { status(`could not place: ${j.detail || r.status}`); return true; }
  const holes = (j.holes || []).length;
  if (j.forked) status(`tileset ${j.forked.from} was shared with `
    + `${j.forked.was_shared_with.join(", ")} — this area now has its own, ${j.forked.to}`);
  status(`placed ${j.kind} ${blkArmed.w}×${blkArmed.h} at (${col},${row})`
       + (holes ? ` — ${holes} thing${holes === 1 ? "" : "s"} still to wire: `
                  + j.holes.map((h) => `(${h.x},${h.y}) needs ${h.needs}`).join(" · ") : ""));
  await loadArea(side, id);
  if (holes) inspectCell(j.holes[0].x, j.holes[0].y);
  return true;
}

// ── the block library at WORLD level ──────────────────────────────────────────────────────────
// What exists, drawn from each block's OWN tiles. No area, no placing: the catalogue comes before
// the tool. Every picture here is rendered from the 36 bytes the block carries, which is the whole
// claim of self-containment — through a tileset's atlas a Quartz house draws as Highpool rubble.
let blCat = null, blKind = "house", blSheet = null, blSel = null, blLib = [];
let blEdit = null, blCell = null;

async function blInit() { if (!blCat) await blLoad(); }

async function blLoad() {
  const grid = $("bl-grid"); if (!grid) return;
  grid.innerHTML = `<div class="muted">mining…</div>`;
  blCat = await (await fetch(`/api/blocks/catalogue`)).json();
  const libd = await (await fetch(`/api/block/library`)).json().catch(() => ({ blocks: [] }));
  blLib = libd.blocks || [];
  $("bl-info").textContent = `${blCat.blocks.length} mined across ${new Set(blCat.blocks.map((b) => b.gfx)).size} tilesets`
    + (blLib.length ? ` · ${blLib.length} in the library` : "");
  await blRender();
}

async function blRender() {
  const grid = $("bl-grid"); if (!grid || !blCat) return;
  // The LIBRARY first, then the mine. A forked block is the one you made — it belonged in the grid
  // from the start, and counting it in the header while never drawing it is how 66 recoloured
  // blocks came to be invisible.
  const lib = blLib.filter((b) => b.kind === blKind).map((b) => ({ ...b, lib: true,
    tiles: (b.art || []).length, seen_in: b.seen_in || [] }));
  const list = lib.concat(blCat.blocks.filter((b) => b.kind === blKind));
  if (!list.length) { grid.innerHTML = `<div class="muted">none of that kind</div>`; return; }
  grid.innerHTML = `<div class="muted small">rendering ${list.length}…</div>`;
  // one sheet per source: per tileset for the mine, one for the library — a forked block belongs
  // to no tileset any more, which is exactly what it was forked for
  const byGfx = {};
  for (const b of list) if (!b.lib) (byGfx[b.gfx] ||= []).push(b);
  const sheets = {};
  const load = async (key, url) => {
    const d = await (await fetch(url)).json().catch(() => null);
    if (!d) return;
    const bytes = Uint8ClampedArray.from(atob(d.rgba_b64), (c) => c.charCodeAt(0));
    const cv = document.createElement("canvas"); cv.width = d.w; cv.height = d.h;
    cv.getContext("2d").putImageData(new ImageData(bytes, d.w, d.h), 0, 0);
    sheets[key] = { cv, at: Object.fromEntries(d.at.map((a) => [a.id, a])) };
  };
  await Promise.all([
    ...(lib.length ? [load("lib", `/api/block/library/sheet?kind=${blKind}`)] : []),
    ...Object.keys(byGfx).map((g) => load(g, `/api/blocks/${g}/sheet?kind=${blKind}`)),
  ]);
  grid.innerHTML = list.map((b) => `
    <div class="bl-card${b.lib ? " lib" : ""}" data-id="${b.id}"
         title="${b.w}×${b.h} · ${b.tiles} tiles of its own${b.lib ? " · yours, in the library"
              : ` · tileset ${b.gfx} · from ${b.seen_in.map((w) => w.name).join(", ")}`}">
      <canvas class="bl-cv" data-id="${b.id}"></canvas>
      <div class="bl-cap">${b.lib ? `<b>${bpEsc(b.id)}</b>` : `${b.w}×${b.h}`} <span class="muted">${b.tiles}t</span></div>
    </div>`).join("");
  for (const b of list) {
    const s = sheets[b.lib ? "lib" : b.gfx], at = s?.at[b.id];
    const cv = grid.querySelector(`.bl-cv[data-id="${b.id}"]`);
    if (!cv || !at) continue;
    cv.width = at.w; cv.height = at.h;
    cv.getContext("2d").drawImage(s.cv, at.x, at.y, at.w, at.h, 0, 0, at.w, at.h);
  }
  grid.querySelectorAll(".bl-card").forEach((el) => el.addEventListener("click", () => {
    blSel = list.find((x) => x.id === el.dataset.id) || null;
    grid.querySelectorAll(".bl-card").forEach((x) => x.classList.toggle("on", x === el));
    if (blSel?.lib) blOpen(blSel.id); else blShow();
  }));
}

// ── editing a block ───────────────────────────────────────────────────────────────────────────
// A mined block is derived and re-made on every load, so it cannot hold an edit — editing forks a
// copy into the library first. Everything below then works on the block's OWN art: swapping a
// cell's tile, bringing a tile in from any tileset, painting one. None of it touches a tileset.
async function blFork(b) {
  const name = prompt("Name for the editable copy:", b.id.replace(/^g\d+-/, ""));
  if (name === null) return;
  const r = await fetch(`/api/block/fork`, { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ gfx: b.gfx, block: b.id, name }) });
  const j = await r.json();
  if (!r.ok) { status(j.detail || "could not fork"); return; }
  status(`forked → ${j.id}, editable in the library`);
  blCat = null; await blLoad(); await blOpen(j.id);
}

async function blOpen(bid, keepArt) {
  await ensurePalette();     // blArtCanvas paints THROUGH the palette; without it every chip is black
  const r = await fetch(`/api/block/${encodeURIComponent(bid)}`);
  if (!r.ok) { status("block not in the library"); return; }
  blEdit = await r.json();
  blCell = null;
  blEditRender(keepArt);
}

function blArtCanvas(cv, t, px = 6) {
  const g = cv.getContext("2d");
  cv.width = 8 * px; cv.height = 16 * px / 2;
  g.imageSmoothingEnabled = false;
  for (let y = 0; y < 16; y++) for (let x = 0; x < 8; x++) {
    const ch = (y > 7 ? 2 : 0) + (x > 3 ? 1 : 0);
    const v = t.pixels[y][x];
    g.fillStyle = PALETTE[v === 0 ? t.bg : v === 3 ? t.cram : v === 1 ? t.chars[ch].hi : t.chars[ch].lo];
    g.fillRect(x * px, y * px / 2, px, px / 2);
  }
}

function blEditRender(openArt) {
  const box = $("bl-side"); if (!box || !blEdit) return;
  const b = blEdit;
  const cellAt = (x, y) => b.cells.find((c) => c.dx === x && c.dy === y);
  // The tiles follow the FORM. One grid in the block's own footprint, each square showing the tile
  // that sits there with its own ✎ — instead of a picture up top and an unrelated wrap of chips
  // below, which made you match them by eye. Spare tiles the block carries but no square uses get
  // their own short row, because they are real and would otherwise be invisible.
  const used = new Set(b.cells.map((c) => c.art));
  const spare = (b.art_tiles || []).map((t, i) => i).filter((i) => !used.has(i));
  const grid = [];
  for (let y = 0; y < b.h; y++) for (let x = 0; x < b.w; x++) {
    const c = cellAt(x, y);
    // An empty square is background showing through, not a defect — and filling one EXTENDS the
    // block. It gets a ＋ rather than a pencil, because there is nothing to paint until it exists.
    grid.push(`<div class="ble-cell${c ? "" : " empty"}${blCell && c === blCell ? " on" : ""}"
                    data-x="${x}" data-y="${y}"
                    title="${c ? `${c.role} · tile ${c.art} · ✎ paints it, ✕ gives it back to the background`
                              : "background — ＋ makes it part of the block"}">
                 ${c ? `<canvas class="ble-cv" data-x="${x}" data-y="${y}"></canvas>
                        <button class="mini ble-paint" data-a="${c.art}" title="paint tile ${c.art}">✎</button>
                        <button class="mini ble-drop" data-x="${x}" data-y="${y}" title="remove this square from the block">✕</button>`
                      : `<button class="mini ble-fill" data-x="${x}" data-y="${y}" title="add this square to the block">＋</button>`}</div>`);
  }
  const ROLES = ["plain", "wall", "door", "service", "script"];
  box.innerHTML = `
    <h2>${bpEsc(b.id)} <span class="muted small">${b.w}×${b.h} · ${b.kind} · ${(b.art || []).length} tiles of its own</span>
      <button id="ble-use" class="mini" title="carry it to Patterns — one call then sets the block and fills its hole">→ pattern</button>
      <button id="ble-del" class="mini" title="remove this block from the library">🗑</button></h2>
    <div class="muted small">click a square to pick it · ✎ paints that tile right here</div>
    <div class="ble-grid" style="grid-template-columns:repeat(${b.w},var(--blec))">${grid.join("")}</div>
    ${blCell ? `<label class="panel-row"><span>(${blCell.dx},${blCell.dy}) is a</span>
      <select id="ble-role">${ROLES.map((r) => `<option${r === blCell.role ? " selected" : ""}>${r}</option>`).join("")}</select>
      <span class="muted small">tile ${blCell.art}</span></label>` : ""}
    <div id="ble-editor"></div>
    ${spare.length ? `<h2>Spare tiles <span class="muted small">carried, not placed — click a square, then one of these</span></h2>
      <div class="ble-arts">${spare.map((i) => `
        <div class="ble-art" data-a="${i}"><canvas class="ble-acv" data-a="${i}"></canvas>
          <button class="mini ble-paint" data-a="${i}" title="paint this tile">✎</button></div>`).join("")}</div>` : ""}
    <div class="btns"><button id="ble-add" class="mini">＋ tile from a tileset</button>
      <button id="ble-blank" class="mini">＋ blank</button></div>`;

  b.cells.forEach((c) => {
    const cv = box.querySelector(`.ble-cv[data-x="${c.dx}"][data-y="${c.dy}"]`);
    if (cv && b.art_tiles[c.art]) blArtCanvas(cv, b.art_tiles[c.art], 8);
  });
  spare.forEach((i) => {
    const cv = box.querySelector(`.ble-acv[data-a="${i}"]`);
    if (cv) blArtCanvas(cv, b.art_tiles[i], 6);
  });

  box.querySelectorAll(".ble-cell").forEach((el) => el.addEventListener("click", () => {
    blCell = cellAt(+el.dataset.x, +el.dataset.y) || null; blEditRender();
  }));
  // clicking a carried tile PUTS IT in the selected cell — the swap he asked for
  box.querySelectorAll(".ble-art").forEach((el) => el.addEventListener("click", async (ev) => {
    if (ev.target.closest(".ble-paint")) return;
    if (!blCell) { status("pick a cell of the block first"); return; }
    await fetch(`/api/block/${encodeURIComponent(b.id)}/cell`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ dx: blCell.dx, dy: blCell.dy, art: +el.dataset.a }) });
    await blOpen(b.id);
  }));
  // The EDITOR is reused, not the view: #te-body is moved into this panel and moved back on close.
  // Jumping to the Tilesets tab to paint a block's tile meant leaving the block behind — and the
  // tile list, the tileset picker and the sandbox over there have nothing to do with it.
  box.querySelectorAll(".ble-paint").forEach((el) => el.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    await ensurePalette();
    const i = +el.dataset.a, t = b.art_tiles[i];
    const body = $("te-body");
    $("ble-editor").appendChild(body);
    te = { tid: null, block: b.id, ai: i, pixels: t.pixels.map((r) => r.slice()),
           chars: t.chars.map((c) => ({ ...c })), bg: t.bg, cram: t.cram, val: 1 };
    body.hidden = false;
    $("te-id").textContent = `tile ${i} of ${b.id}`;
    teDraw(); teControls();
    body.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }));
  const cellPost = (body) => fetch(`/api/block/${encodeURIComponent(b.id)}/cell`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  box.querySelectorAll(".ble-fill").forEach((el) => el.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    const r = await cellPost({ dx: +el.dataset.x, dy: +el.dataset.y, add: true });
    if (!r.ok) { status((await r.json()).detail || "could not add"); return; }
    await blOpen(b.id);
    // it starts blank, so go straight to painting it — that is why you added it
    const cell = $("bl-side").querySelector(`.ble-cell[data-x="${el.dataset.x}"][data-y="${el.dataset.y}"] .ble-paint`);
    cell?.click();
  }));
  box.querySelectorAll(".ble-drop").forEach((el) => el.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    await cellPost({ dx: +el.dataset.x, dy: +el.dataset.y, remove: true });
    blCell = null; await blOpen(b.id);
  }));
  $("ble-role")?.addEventListener("change", async (ev) => {
    await fetch(`/api/block/${encodeURIComponent(b.id)}/cell`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ dx: blCell.dx, dy: blCell.dy, role: ev.target.value }) });
    await blOpen(b.id);
  });
  $("ble-add")?.addEventListener("click", async () => {
    const q = prompt("Bring in which tile?  gfx:tile  (it is COPIED — the block stays self-contained)", "3:40");
    if (!q) return;
    const [g, t] = q.split(":").map(Number);
    const r = await fetch(`/api/block/${encodeURIComponent(b.id)}/art?gfx=${g}&tile=${t}`, { method: "POST" });
    if (!r.ok) { status((await r.json()).detail || "no such tile"); return; }
    await blOpen(b.id);
  });
  $("ble-blank")?.addEventListener("click", async () => {
    await fetch(`/api/block/${encodeURIComponent(b.id)}/art`, { method: "POST" });
    await blOpen(b.id);
  });
  $("ble-use")?.addEventListener("click", () => kitCarry(b.id, b.id));
  $("ble-del")?.addEventListener("click", async () => {
    if (!confirm(`Remove "${b.id}" from the library? The mined original stays.`)) return;
    await fetch(`/api/block/${encodeURIComponent(b.id)}`, { method: "DELETE" });
    blEdit = null; blCat = null; await blLoad(); $("bl-side").innerHTML = `<div class="muted">pick a block</div>`;
  });
  if (openArt != null) { /* returning from the painter keeps the block open */ }
}

function blShow() {
  const box = $("bl-side"); if (!box) return;
  const b = blSel;
  if (!b) { box.innerHTML = `<div class="muted">pick a block</div>`; return; }
  const OPN = { 0x0: "walkable", 0x1: "message", 0x2: "gate", 0x3: "encounter", 0x4: "object",
                0x5: "loot", 0x6: "service", 0x8: "keyword", 0xA: "door", 0xB: "wall", 0xC: "patch" };
  box.innerHTML = `
    <h2>${b.kind} <span class="muted small">${b.w}×${b.h}</span></h2>
    <div class="muted small">${b.id}</div>
    <h2>Carries</h2>
    <div class="muted small">${b.tiles} tile${b.tiles === 1 ? "" : "s"} of its own — this block does
      not depend on tileset ${b.gfx}; placing it anywhere brings them along.</div>
    <h2>Hull <span class="muted small">what the op layer already says</span></h2>
    <ul class="bl-ops">${b.ops.map((o) => `<li><code>op $${o.toString(16).toUpperCase()}</code> ${OPN[o] || "?"}</li>`).join("")}</ul>
    <h2>Found in</h2>
    <ul class="bl-where">${b.seen_in.map((w) => `<li><a class="usage-link" data-side="${w.side}" data-area="${w.area}" data-cell="${w.at[0]}:${w.at[1]}">${bpEsc(w.name)} (${w.at.join(",")})</a></li>`).join("")}</ul>
    ${b.n > 1 ? `<div class="muted small">${b.n} copies in the shipped game</div>` : ""}
    <div class="btns"><button id="bl-fork" class="mini">✎ edit a copy</button>
      <button id="bl-use" class="mini" title="carry it to Patterns — one call then sets the block and fills its hole">→ build with a pattern</button></div>
    <div class="muted small">A mined block is derived — re-made from the areas on every load, so it
      cannot hold an edit. Editing forks it into the library, where its tiles are yours.</div>`;
  $("bl-fork")?.addEventListener("click", () => blFork(b));
  $("bl-use")?.addEventListener("click", () => kitCarry(b.id, `${b.kind} ${b.w}×${b.h}`));
  wireUsageLinks(box);
}

// ═══════════════ BAUKASTEN · PATTERNS — the short way, with no quest in it ═══════════════
//
// Weg A (concept §5): pick a shape, say who and what, put it on a square. No quest is opened and
// none is needed — a-sign appears 92 times in the shipped game, guarded-stash 95, and not one of
// them hangs on an arc. The records ARE the store; nothing is written into quests.json.
//
// The mask is the designer's, not a copy of it (dqHolesHtml / dqWireHoles / dqReadHoles), and the
// write goes through POST /api/design/place — the one compile path — with no `quest` field.
let kitCat = null, kitSel = null, kitVals = {}, kitBlock = null, kitInited = false;

function kitInit() { if (!kitInited) { kitInited = true; kitLoad(); } }

async function kitLoad() {
  if (!dqCat) dqCat = await (await fetch("/api/design/catalogue")).json();
  kitCat = dqCat;
  kitRenderList();
  kitRenderCarry();
}

function kitPatterns() {
  const fam = (kitCat?.families || []).find((f) => f.key === "pattern");
  // by how often the shipped game does it: the ones you reach for are the ones it repeats
  return (fam?.items || []).slice().sort((a, b) => (b.seen || 0) - (a.seen || 0));
}

function kitRenderList() {
  const box = $("kit-list"); if (!box) return;
  const pats = kitPatterns();
  $("kit-info").textContent = `${pats.length} patterns`;
  box.innerHTML = pats.map((b) => `<div class="kit-card${b.id === kitSel ? " on" : ""}" data-bp="${fesc(b.id)}">
      <div class="kit-n">${fesc(b.name || b.id)}${b.seen ? ` <span class="bp-seen">${b.seen}×</span>` : ""}</div>
      <div class="muted small">${fesc(b.about || "")}</div>
      <div class="muted small kit-holes">${(b.holes || []).map((h) => fesc(h.label)).join(" · ")}</div>
    </div>`).join("");
  box.querySelectorAll(".kit-card").forEach((el) => el.addEventListener("click", () => kitOpen(el.dataset.bp)));
}

// A block picked over in the Blocks pane is CARRIED here rather than placed there: /api/design/place
// sets the block and fills its hole in one call, which is the whole of Weg A. Two calls would leave
// a house standing empty whenever the second one refused.
function kitCarry(id, label) {
  kitBlock = id ? { id, label: label || id } : null;
  kitRenderCarry();
  setPane("kit", "patterns");
}

function kitRenderCarry() {
  const el = $("kit-carry"); if (!el) return;
  el.innerHTML = kitBlock
    ? `carrying block <b>${fesc(kitBlock.label)}</b> <button class="mini" id="kit-drop">put it back</button>`
    : "";
  if ($("kit-drop")) $("kit-drop").onclick = () => { kitBlock = null; kitRenderCarry(); if (kitSel) kitOpen(kitSel); };
}

async function kitOpen(id) {
  const bp = kitPatterns().find((x) => x.id === id);
  if (!bp) return;
  if (id !== kitSel) kitVals = {};
  kitSel = id;
  kitRenderList();
  const box = $("kit-work");
  box.innerHTML = `<div class="muted">reading what this area has to offer…</div>`;
  await dqPalette(dqArea || `1:${dqAreaId()}`);
  const OPN = { 1: "SAY", 2: "GATE", 3: "FIGHT", 5: "LOOT", 6: "TALK", 10: "GO", 11: "BUMP" };
  box.innerHTML = `<div class="dq-h">${fesc(bp.name || bp.id)}</div>
    <div class="dq-about">${fesc(bp.about || "")}${bp.seen ? ` <span class="muted small">— ${bp.seen}× in the game</span>` : ""}</div>
    <div class="dq-beats">${(bp.beats || []).map((b, i) =>
      `<span class="dq-beat">${i + 1} ${OPN[b.op] || b.role}</span>`).join('<span class="dq-arrow">→</span>')}</div>
    ${kitBlock ? `<div class="kit-onblock">on the block <b>${fesc(kitBlock.label)}</b>
       <span class="muted small">— the square below is the block's corner; the pattern goes into the
       hole it leaves (a door, a service square, a script square)</span></div>` : ""}
    <div id="kit-holes"></div>
    <div class="dq-place">
      <div class="dq-row">into <select id="kit-area">${(dqPal.areas || []).map((a) =>
          `<option value="${a.side}:${a.id}"${dqArea === `${a.side}:${a.id}` ? " selected" : ""}>${fesc(a.name || ("area " + a.id))}</option>`).join("")}</select>
        <span id="kit-budget" class="muted small"></span>
        x <input type="number" id="kit-col" min="0" max="63" value="20" style="width:56px">
        y <input type="number" id="kit-row" min="0" max="63" value="20" style="width:56px">
        <button class="mini dq-go" id="kit-go">Place it →</button>
        <button class="mini" id="kit-to-design">or hang it on a quest →</button></div>
      <div id="kit-msg" class="muted small"></div>
    </div>`;
  kitRenderHoles(bp);
  $("kit-area").onchange = async () => {
    kitVals = kitReadHoles(bp);                    // keep what is typed across the area change
    await dqPalette($("kit-area").value);
    kitOpen(id);
  };
  $("kit-to-design").onclick = () => dqStartWith(bp.id, bp.name || bp.id);
  $("kit-go").onclick = () => kitPlace(bp);
}

function kitRenderHoles(bp) {
  const b = dqPal.budget?.strings || {};
  $("kit-budget").innerHTML = `<span class="${b.free ? (b.free <= 8 ? "gate-warn" : "gate-ok") : "gate-err"}">${b.free} text id${b.free === 1 ? "" : "s"} free</span>`;
  $("kit-holes").innerHTML = dqHolesHtml(bp, kitVals);
  dqWireHoles($("kit-holes"), () => { kitVals = kitReadHoles(bp); kitRenderHoles(bp); });
}

function kitReadHoles(bp) { return dqReadHoles($("kit-holes"), bp); }

async function kitPlace(bp) {
  const msg = $("kit-msg");
  kitVals = kitReadHoles(bp);
  const [s, a] = $("kit-area").value.split(":").map(Number);
  msg.textContent = "writing…";
  const r = await fetch("/api/design/place", { method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ side: s, area: a, blueprint: bp.id, values: kitVals,
                           col: +$("kit-col").value, row: +$("kit-row").value,
                           ...(kitBlock ? { block: kitBlock.id } : {}) }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { msg.innerHTML = dqRefusedHtml(j, r.status); return; }
  const cell = j.cell ? `(${j.cell.col},${j.cell.row})` : "unbound";
  msg.innerHTML = `<span class="gate-ok">written at ${fesc(cell)} — ${(j.records || []).map((x) => fesc(x.role || `grp${x.grp}[${x.cid}]`)).join(", ")}
      · +${j.size_delta ?? 0} bytes</span>`
    + (j.warnings || []).map((w) => `<div class="gate-warn">${fesc(w)}</div>`).join("")
    + `<div class="muted small">No quest was touched — this beat is furniture, and the world is its
       own record of it.</div>`;
  // the palette holds this area's text budget and its foes, and the placement just changed both
  dqPal = null; await dqPalette(`${s}:${a}`);
  kitRenderHoles(bp);
  if (curSide === s && curAid === a) await loadArea(s, a);
}

// ── off-map fill tile (descriptor $33): the tile the engine draws beyond the map edge ──
function renderFillTile() {
  const inp = $("area-fill"); if (!inp || !area) return;
  inp.value = area.fill_tile ?? 0;
  drawFillPrev();
}
function drawFillPrev() {
  const c = $("area-fill-prev"); if (!c) return;
  const cx = c.getContext("2d"); cx.clearRect(0, 0, c.width, c.height);
  const t = +$("area-fill").value || 0;
  if (mapAtlas) blit(cx, mapAtlas, t, 0, 0, c.width, c.height);   // $00 (empty) draws black
}
async function saveFillTile() {
  if (!area) return;
  const [side, id] = $("area-select").value.split(":").map(Number);
  const t = Math.max(0, Math.min(95, +$("area-fill").value || 0));
  $("area-fill").value = t;
  const r = await fetch(`/api/area/${side}/${id}/fill-tile`,
    { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ tile: t }) });
  const j = await r.json();
  if (!r.ok) { status(`off-map tile failed: ${j.detail || r.status}`); return; }
  status(j.changed ? `off-map tile → $${t.toString(16).toUpperCase().padStart(2, "0")} (saved)` : "off-map tile unchanged");
  await loadArea(side, id);
}
// ── WORLD parameters (descriptor $2F/$31/$32 + the step clock) ────────────────────────────────
//
// The three encounter bytes are the reason two areas with the same tiles play differently, and
// nothing in the editor showed them. `WB015` rolls `+$2F` every turn, and on a 1 it takes a random
// foe id from `1..+$31`, allocates one of `+$32` grp15 slots and plants an op-$0F cell — so the
// "pool" is literally the head of the foe list right below this panel.
//
// The grp16 events that CHANGE these bytes are listed here too, but read-mostly: their trigger is a
// chain step, so the beat is where they belong, and this list is the index into it.
let worldData = null;

// A world byte can be set to something the engine simply cannot honour — minutes per step above
// 59 (the hour carry is ONE subtract), or a heal weight whose gcd with 16 makes it the slowest
// setting while looking like the biggest. Those are not save errors, so nothing refuses them; they
// just have to be visible at the moment of typing.
function worldWarn(warnings) {
  const box = $("world-panel"); if (!box) return;
  let el = $("world-warn");
  if (!el) {
    el = document.createElement("div"); el.id = "world-warn"; el.className = "world-warn";
    box.parentNode.insertBefore(el, box.nextSibling);
  }
  const w = warnings || [];
  el.hidden = !w.length;
  el.innerHTML = w.map((t) => `<div>⚠ ${fesc(t)}</div>`).join("");
}

async function worldLoad(side, id) {
  const box = $("world-panel"), sum = $("world-sum"), evb = $("world-events");
  if (!box) return;
  const d = await (await fetch(`/api/area/${side}/${id}/world`)).json().catch(() => null);
  worldData = d;
  if (!d || !d.fields) { sum.textContent = "—"; box.innerHTML = ""; evb.innerHTML = ""; return; }
  sum.textContent = d.summary;
  box.innerHTML = d.fields.map((f) => `
    <label class="panel-row" title="${fesc(f.hint)}"><span>${fesc(f.label)}</span>
      <input class="wf" data-k="${f.key}" type="number" min="0" max="255" step="1"
             style="width:56px" value="${f.value}"${f.readonly ? " disabled" : ""}>
      <span class="muted small wf-t" data-k="${f.key}">${fesc(f.text || "")}</span></label>`).join("");
  box.querySelectorAll(".wf").forEach((i) => i.addEventListener("change", () => worldSave(side, id)));
  worldWarn(d.warnings);
  // Live consequences while typing. NOT recomputed here: the arithmetic is modular and easy to get
  // subtly wrong (a weight of 255 heals SIXTEEN TIMES RARER than 16), so the same
  // wl_world.derive() that produced the stored text answers the preview too. A JS copy is how the
  // portrait clock ended up with two different answers in one editor.
  let wt = null;
  box.querySelectorAll(".wf").forEach((i) => i.addEventListener("input", () => {
    clearTimeout(wt);
    wt = setTimeout(async () => {
      const body = {};
      box.querySelectorAll(".wf").forEach((f) => { body[f.dataset.k] = +f.value || 0; });
      const r = await fetch("/api/world/derive", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const j = await r.json().catch(() => null);
      if (!j) return;
      for (const [k, v] of Object.entries(j.text || {})) {
        const t = box.querySelector(`.wf-t[data-k="${k}"]`);
        if (t) t.textContent = v || "";
      }
      worldWarn(j.warnings);
    }, 200);
  }));

  const evs = d.events || [];
  evb.innerHTML = !evs.length ? "" : `
    <h2 style="margin-top:10px">Changed in play <span class="muted small">${evs.length} event${evs.length === 1 ? "" : "s"} of this area's own code</span></h2>
    <div class="muted small" style="margin-bottom:4px">Each one fires from a chain step — click to
      find it. The numbers are immediates in the 6502, so they edit in place.</div>
    ${evs.map((e) => `
      <div class="wev" data-ev="${e.event}" data-raise="${(e.raised_by || [])[0] ?? ""}">
        <div class="wev-h"><b>${fesc(e.text)}</b>
          <span class="dbg"> event ${e.event}${e.raised_by.length ? ` · grp6[${e.raised_by.join("], grp6[")}]` : ""}</span></div>
        <div class="panel-row">
          ${e.effect.kind === "delta"
            ? `<span>step</span><input class="wev-f" data-k="delta" type="number" style="width:52px" value="${Math.abs(e.effect.delta)}">
               ${e.effect.cap !== null ? `<span>stops at</span><input class="wev-f" data-k="cap" type="number" min="0" max="255" style="width:52px" value="${e.effect.cap}">` : `<span class="muted small">no limit</span>`}`
            : `<span>sets it to</span><input class="wev-f" data-k="value" type="number" min="0" max="255" style="width:52px" value="${e.effect.value}">`}
        </div>
      </div>`).join("")}`;
  evb.querySelectorAll(".wev").forEach((row) => {
    const rb = row.dataset.raise;
    if (rb !== "") row.querySelector(".wev-h").addEventListener("click",
      () => chainFocusRec(6, +rb));
    row.querySelectorAll(".wev-f").forEach((i) => i.addEventListener("change", async () => {
      const body = {}; row.querySelectorAll(".wev-f").forEach((x) => { body[x.dataset.k] = +x.value; });
      const r = await fetch(`/api/area/${side}/${id}/world/event/${row.dataset.ev}`,
        { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      const j = await r.json();
      if (!r.ok) { status(`event failed: ${j.detail || r.status}`); return; }
      status(j.changed ? `event ${row.dataset.ev}: ${j.text} (saved)` : "unchanged");
      await worldLoad(side, id); chainsLoad(side, id);
    }));
  });
}

async function worldSave(side, id) {
  const fields = {};
  document.querySelectorAll("#world-panel .wf:not([disabled])")
    .forEach((i) => { fields[i.dataset.k] = Math.max(0, Math.min(255, +i.value || 0)); });
  const r = await fetch(`/api/area/${side}/${id}/world`,
    { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ fields }) });
  const j = await r.json();
  if (!r.ok) { status(`world failed: ${j.detail || r.status}`); return; }
  status((j.changed || []).length ? `world: ${j.changed.join(", ")} saved` : "world unchanged");
  await worldLoad(side, id);
}

// ── map history: same as tileset, on the MAP file (tile grid). Restore snapshots the area to ↶ undo. ──
async function mapHistoryToggle() {
  const w = $("map-history-wrap");
  w.hidden = !w.hidden;
  if (!w.hidden) await mapHistoryLoad();
}
async function mapHistoryLoad() {
  const box = $("map-history"); if (!box) return;
  const sel = $("area-select").value; if (!sel) { box.innerHTML = "<span class='muted'>load an area…</span>"; return; }
  const [side, id] = sel.split(":").map(Number);
  box.innerHTML = "<span class='muted'>loading…</span>";
  const j = await (await fetch(`/api/history/map/${side}/${id}`)).json();
  if (!j.history || !j.history.length) {
    box.innerHTML = `<span class="muted">no edit history yet — this map is still at pristine baseline. Edits auto-commit and show up here.</span>`;
    return;
  }
  box.innerHTML = j.history.map((h) =>
    `<div class="hist-row"><button class="mini hist-restore" data-rev="${h.rev}" title="restore this version">⟲</button>`
    + `<button class="mini hist-diff" data-rev="${h.rev}" title="diff this version vs now">≠</button>`
    + `<span class="hist-when">${histWhen(h.time)}</span>`
    + `<span class="hist-lbl">${histLabel(h.label)}</span>`
    + `<code class="hist-rev">${h.short}</code></div>`).join("");
  box.querySelectorAll(".hist-restore").forEach((b) =>
    b.addEventListener("click", () => mapHistoryRestore(b.dataset.rev)));
  box.querySelectorAll(".hist-diff").forEach((b) =>
    b.addEventListener("click", () => mapDiffShow(b.dataset.rev)));
}
// ── map diff: old grid vs now — changed cells outlined, A/B toggle ──
let mapDiff = null;
async function mapDiffShow(rev) {
  const [side, id] = $("area-select").value.split(":").map(Number);
  const r = await fetch(`/api/diff/map/${side}/${id}?rev=${rev}`);
  const j = await r.json();
  if (!r.ok) { status(`diff failed: ${j.detail || r.status}`); return; }
  $("map-diff-wrap").hidden = false;
  if (j.dims_changed) {
    mapDiff = null; const c = $("map-diff-canvas"); c.width = c.height = 0;
    $("map-diff-info").textContent = `vs ${j.rev} · dimensions differ (${j.byte_delta} bytes delta) — no cell view`;
    return;
  }
  mapDiff = { rev: j.rev, changed: new Set(j.changed), oldTids: j.old_tileids, w: j.width, h: j.height, showOld: true };
  $("map-diff-info").textContent = `vs ${j.rev} · ${j.changed.length} cell(s) changed`;
  mapDiffDraw();
}
function mapDiffDraw() {
  if (!mapDiff) return;
  const size = 16, { w, h } = mapDiff;
  const c = $("map-diff-canvas"); c.width = w * size; c.height = h * size;
  const cx = c.getContext("2d"); cx.imageSmoothingEnabled = false;
  $("map-diff-toggle").textContent = mapDiff.showOld ? "show: old" : "show: now";
  const tids = mapDiff.showOld ? mapDiff.oldTids : area.tileids;   // both render with the CURRENT tileset gfx
  for (let i = 0; i < w * h; i++) {
    const x = (i % w) * size, y = ((i / w) | 0) * size;
    if (mapAtlas) blit(cx, mapAtlas, tids[i] || 0, x, y, size, size);
  }
  cx.strokeStyle = "#e6635a"; cx.lineWidth = 2;
  for (const i of mapDiff.changed)
    cx.strokeRect((i % w) * size + 1, ((i / w) | 0) * size + 1, size - 2, size - 2);
}
// ── record history: descriptor/physique/script/NPCs/texts — restore any version ──
async function recHistoryToggle() {
  const w = $("rec-history-wrap");
  w.hidden = !w.hidden;
  if (!w.hidden) await recHistoryLoad();
}
async function recHistoryLoad() {
  const box = $("rec-history"); if (!box) return;
  const sel = $("area-select").value; if (!sel) { box.innerHTML = "<span class='muted'>load an area…</span>"; return; }
  const [side, id] = sel.split(":").map(Number);
  box.innerHTML = "<span class='muted'>loading…</span>";
  const j = await (await fetch(`/api/history/record/${side}/${id}`)).json();
  if (!j.history || !j.history.length) {
    box.innerHTML = `<span class="muted">no edit history yet — this record is still at pristine baseline.</span>`;
    return;
  }
  box.innerHTML = j.history.map((h) =>
    `<div class="hist-row"><button class="mini hist-restore" data-rev="${h.rev}" title="restore this version">⟲</button>`
    + `<span class="hist-when">${histWhen(h.time)}</span>`
    + `<span class="hist-lbl">${histLabel(h.label)}</span>`
    + `<code class="hist-rev">${h.short}</code></div>`).join("");
  box.querySelectorAll(".hist-restore").forEach((b) =>
    b.addEventListener("click", () => recHistoryRestore(b.dataset.rev)));
}
async function recHistoryRestore(rev) {
  const [side, id] = $("area-select").value.split(":").map(Number);
  if (!confirm(`Restore this area's RECORD (physique/script/NPCs/texts) to version ${rev.slice(0, 9)}?\nThe map grid stays as-is; the current area is pushed to ↶ undo first.`)) return;
  const r = await fetch(`/api/history/record/${side}/${id}/restore`,
    { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ rev }) });
  const j = await r.json();
  if (!r.ok) { status(`restore failed: ${j.detail || r.status}`); return; }
  status(`restored record to ${j.rev}${j.dims_changed ? " — ⚠ dimensions differ from the current map, restore the matching map version too" : ""} — ↶ undo to step back`);
  await loadArea(side, id);
  if (!$("rec-history-wrap").hidden) recHistoryLoad();
}
async function mapHistoryRestore(rev) {
  const [side, id] = $("area-select").value.split(":").map(Number);
  if (!confirm(`Restore this map's tile grid to version ${rev.slice(0, 9)}?\nThe current area is pushed to ↶ undo first (only the map changes, not the record).`)) return;
  const r = await fetch(`/api/history/map/${side}/${id}/restore`,
    { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ rev }) });
  const j = await r.json();
  if (!r.ok) { status(`restore failed: ${j.detail || r.status}`); return; }
  status(`restored map to ${j.rev} (${j.bytes} B) — ↶ undo to step back`);
  await loadArea(side, id);
  if (!$("map-history-wrap").hidden) mapHistoryLoad();   // the restore itself added a commit — refresh
}
// step-back undo: restore the area to the state before the last saved edit (map/physique/script/prose)
async function undoArea() {
  const [side, id] = $("area-select").value.split(":").map(Number);
  const r = await fetch(`/api/area/${side}/${id}/undo`, { method: "POST" });
  const j = await r.json();
  if (!j.undone) { status("nothing to undo"); return; }
  status(`undo — ${j.depth} step(s) left`); loadArea(side, id);
}
// ── mode (view/edit) + EDIT subset (map/physique/script); the left sidebar morphs per subset ──
// The three strips that make up "where am I": the top mode (View/Edit/Events), the left panel's
// tab, and — in Edit only — which layer the right panel works on. One variable each, and
// applyLayout paints all three from these, so a click can never leave two of them disagreeing.
let subset = "map", areaTab = "data";
function activeSubset() { return mode === "edit" ? subset : "view"; }
function setMode(m) {
  if (!["view", "edit", "flow"].includes(m)) return;   // a stray caller must not blank the mode
  mode = m;
  // Flow is a third way to look at the SAME area, so it swaps the section, not the sidebar — and it
  // reads the ONE area select in the shared bar rather than keeping a second one of its own.
  setTab("areas");
  if (m === "flow") flowInit();
  applyLayout();
}
function setSubset(s) {
  if (!["map", "physique", "script"].includes(s)) return;
  subset = s;
  applyLayout();                                       // applyLayout paints the strip, from state
}
// What the right column is, in each of the four states it can be in. The strip alone never said it
// — and in View there is no strip at all, so the column was read-only with nothing admitting it.
const SCOPE = {
  view:     ["Cell", "read-only", "click a cell to read its script · switch to Edit to change it"],
  script:   ["Cell", null, "click a cell to edit its script"],
  map:      ["Tiles", null, "paint the tile layer — drag to draw, Select to snap a blueprint"],
  physique: ["Walls", null, "paint the op layer — which squares block the party"],
};

function applyLayout() {
  const a = activeSubset();
  const lp = (cls, on) => { const el = document.querySelector(cls); if (el) el.hidden = !on; };
  const onCell = a === "view" || a === "script";
  lp(".lp-cell", onCell);                              // cell inspector: view + cell-edit
  lp(".lp-map", a === "map");                          // tile paint
  lp(".lp-physique", a === "physique");               // solidity paint
  // The three strips are ONE state; paint them all here so a click can never leave two disagreeing.
  $("mode-switch")?.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  $("subset-switch")?.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.subset === subset));
  $("area-tabs")?.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.ap === areaTab));
  // VIEW = the SAME editor, read-only: CSS (.ro-view) disables inputs + hides Save/undo/add buttons.
  const cp = $("cell-panel");
  if (cp) {
    cp.classList.toggle("ro-view", a === "view");
    cp.classList.toggle("cell-mode", onCell);          // only the CELL pane may collapse to 200px
    const [what, ro, how] = SCOPE[a] || SCOPE.view;
    const sc = $("cell-scope");
    if (sc) sc.innerHTML = `${mode === "edit" ? "Editing" : "Viewing"} <b>${what}</b>`
      + (ro ? ` <span class="ro">${ro}</span>` : "")
      + `<span class="how muted small">${how}</span>`;
  }
  canvas.style.cursor = (a === "map" || a === "physique") ? "cell" : "crosshair";
  drawMap();
}
// ── AREA panel (left column) tabs: Data / Teleport / Script / Text / Code ──
function setAreaTab(t) {
  if (!["data", "script", "text", "combat", "services", "code"].includes(t)) return;
  areaTab = t;
  $("area-tabs").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.ap === t));
  document.querySelectorAll("#area-panel .ap").forEach((el) => { el.hidden = !el.classList.contains(`ap-${t}`); });
  if (t === "text") loadAreaTexts();
  if (t === "code") loadAreaCode();
  if (t === "services") loadServices();
  if (t === "combat") loadCombat();
  // no "teleport" case: it lives at the foot of Data now and renders with the area
}


// ── AREA Combat tab: the fight vocabulary, read as a configuration ─────────────────────────
//
// The same 33 strings are in the Text tab, tagged — but at the END of a 152-row pool, in id order,
// interleaved with nothing. Reading them there is not reading them. Here they are the thing they
// actually are: seven weapon classes, three hit heads, the fragments, each with the words THIS
// area uses. Highpool's rocket "launches a shell", Base Cochise's "blasts a rocket off", the
// Desert Nomads' "lauches a shell" — the typo has been in the game since 1988.
const CB_CLASS = { melee: "hand to hand", pistol: "pistol", burst: "burst weapon",
                   energy: "energy weapon", clip: "full auto", thrown: "thrown charge",
                   rocket: "rocket" };

async function loadCombat() {
  if (!area) return;
  const box = $("cb-panel");
  box.innerHTML = `<span class="muted small">…</span>`;
  const d = await (await fetch(`/api/area/${curSide}/${curAid}/texts`)).json();
  const S = d.messages || [], by = Object.fromEntries(S.map((m) => [m.id, m]));
  const c = d.combat || {};
  if (!(c.verbs || []).length) {
    box.innerHTML = `<div class="muted small">This area carries no combat vocabulary. A fight here
      would print fragments of whatever happens to sit at those ids — that is what TREX Base did
      before the block was installed.</div>`;
    return;
  }
  const line = (id, label, hint) => {
    const m = by[id] || {};
    return `<div class="cb-row">
      <div class="cb-lbl">${fesc(label)}${hint ? ` <span class="muted small">${fesc(hint)}</span>` : ""}</div>
      <div class="cb-reads">${fesc(m.reads || m.text || "—")}</div>
      <textarea class="cb-ta" data-tid="${id}" rows="1">\n${fesc(m.text || "")}</textarea>
      <span class="dbg">#${id}</span></div>`;
  };
  box.innerHTML =
    `<div class="q-cap">how an attack reads — one line per weapon class</div>` +
    (c.verbs || []).map((v) => line(v.id, CB_CLASS[v.class] || v.class)).join("") +
    `<div class="q-cap">the hit</div>` +
    (c.heads || []).map((id, k) => line(id, ["lands", "for", "hard hit"][k] || `head ${k}`)).join("") +
    `<div class="q-cap">damage and misses <span class="muted small">${(c.fragments || []).length} fragments,
       assembled mid-sentence</span></div>` +
    (c.fragments || []).map((id, k) => line(id, `fragment ${k + 1}`)).join("") +
    `<div class="btns"><button id="cb-save" class="mini" disabled>Save</button>
       <span id="cb-msg" class="muted small"></span></div>`;
  const orig = {};
  box.querySelectorAll(".cb-ta").forEach((ta) => { orig[ta.dataset.tid] = ta.value; });
  const dirty = () => [...box.querySelectorAll(".cb-ta")]
    .filter((ta) => ta.value !== orig[ta.dataset.tid])
    .map((ta) => ({ id: +ta.dataset.tid, text: ta.value }));
  box.querySelectorAll(".cb-ta").forEach((ta) => ta.addEventListener("input", () => {
    $("cb-save").disabled = !dirty().length;
  }));
  $("cb-save").onclick = async () => {
    const edits = dirty();
    if (!edits.length) return;
    // the same prose endpoint the Text tab writes through — one path to the string pool
    const r = await fetch(`/api/area/${curSide}/${curAid}/prose`, { method: "POST",
      headers: { "content-type": "application/json" }, body: JSON.stringify({ edits }) });
    const j = await r.json().catch(() => ({}));
    $("cb-msg").textContent = r.ok ? `saved ${edits.length} line(s)` : (j.detail || "refused");
    if (r.ok) loadCombat();
  };
}

// ── AREA Text tab: every text of the area (message pool editable, foe names shown) ──
async function loadAreaTexts() {
  if (!area) return;
  const [side, id] = [curSide, curAid];
  const box = $("txt-list");
  box.innerHTML = "…";
  const d = await (await fetch(`/api/area/${side}/${id}/texts`)).json();
  $("txt-count").textContent = `${d.messages.length} strings`;
  const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  // Which strings are combat vocabulary, and what each is FOR. A third of every area's pool is
  // this — fragments like " for " and "<0f> " that only become a sentence mid-fight, sitting
  // unlabelled between the lines the area actually says.
  const COMBAT_ROLE = {};
  (d.combat?.verbs || []).forEach((v) => { COMBAT_ROLE[v.id] = `combat · ${v.class}`; });
  (d.combat?.heads || []).forEach((i) => { COMBAT_ROLE[i] = COMBAT_ROLE[i] || "combat · hit"; });
  (d.combat?.fragments || []).forEach((i) => { COMBAT_ROLE[i] = COMBAT_ROLE[i] || "combat · fragment"; });
  box.innerHTML = d.messages.map((m) => {
    const chips = (m.used_by || []).map((u) =>
      `<a class="txt-use" data-cell="${u.cell[0]}:${u.cell[1]}" title="cell (${u.cell[0]},${u.cell[1]})">${esc(u.role)} (${u.cell[0]},${u.cell[1]})</a>`).join(" ");
    const reserved = m.id === 0
      ? ' <span class="txt-reserved" title="Wasteland reserves string id 0 as the empty/no-message slot — a SAY/label pointing at id 0 is never printed. Put real text at id ≥ 1.">reserved — not usable as a message</span>'
      : "";
    // The RAW string is what gets edited and saved; `reads` is the same thing with its control
    // codes written out, which is the only form a person can judge. "<0b> attacks <0f>" is not a
    // sentence; "«attacker» attacks «target»" is.
    // A line break is a CHARACTER in the pool and costs a byte, but HTML collapses it — so the
    // preview showed one flowing paragraph where the string actually breaks, and a stray blank
    // line read as nothing. Marked and kept.
    const nl = (t) => esc(t).replace(/\n/g, '<span class="txt-nl">⏎</span>\n');
    const reads = m.reads && m.reads !== m.text
      ? `<div class="txt-reads">${nl(m.reads)}</div>` : "";
    const role = COMBAT_ROLE[m.id];
    const tag = role ? `<span class="txt-combat" title="part of this area's combat vocabulary — the engine reaches it through desc+$37, not by id">${esc(role)}</span>` : "";
    return `<div class="txt-row${m.id === 0 ? " txt-row-reserved" : ""}${role ? " txt-row-combat" : ""}"><div class="txt-head"><b>#${m.id}</b> ${tag} ${m.id === 0 ? reserved : (chips || '<span class="muted" title="no direct script ref — may still be pulled indirectly (encounters, engine)">no ref found</span>')}</div>
      ${reads}
      <textarea class="txt-ta" data-tid="${m.id}" rows="2">\n${esc(m.text)}</textarea></div>`;
  }).join("") || `<span class="muted">no text pool</span>`;
  // the legend, built from the SAME table the backend translated with
  $("txt-codes").innerHTML = Object.entries(d.codes || {}).map(([c, i]) =>
    `<span class="txt-code" title="${esc(i.example)}"><code>&lt;${c}&gt;</code> ${esc(i.name)}
       <span class="muted small">${esc(i.about)}</span></span>`).join("");
  $("txt-foes").innerHTML = (d.foe_names || []).map((n, i) => `<div>${i + 1}. ${esc(n)}</div>`).join("") || "—";
  // Staged editing: type freely, validate the whole pool against the char table, then Save on
  // purpose. No more blind save-on-blur (which could 422 silently and lose the edit).
  txtOrig = {}; d.messages.forEach((m) => (txtOrig[m.id] = m.text || ""));
  box.querySelectorAll(".txt-ta").forEach((ta) => ta.addEventListener("input", txtOnEdit));
  txtRefresh();
  box.querySelectorAll(".txt-use").forEach((a) => a.addEventListener("click", () => {
    const [c, r] = a.dataset.cell.split(":").map(Number);
    inspectCell(c, r);
  }));
}

async function addAreaText() {
  if (!area) return;
  const text = prompt("New string text:");
  if (text === null) return;
  const d = await (await fetch(`/api/area/${curSide}/${curAid}/texts`)).json();
  const nid = d.messages.length;
  const r = await fetch(`/api/area/${curSide}/${curAid}/prose`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ edits: [{ id: nid, text }] }) });
  if (!r.ok) { status(`add err ${r.status}: ${(await r.json()).detail || ""}`); return; }
  status(`added text #${nid} → work/`);
  loadAreaTexts();
}

// ── AREA Code tab: grp16 events, minimal readable 6502 + hand-written effect notes ──
async function loadAreaCode() {
  if (!area) return;
  const [side, id] = [curSide, curAid];
  const box = $("code-list");
  box.innerHTML = "…";
  const [d, notes] = await Promise.all([
    (await fetch(`/api/area/${side}/${id}/grp16`)).json(),
    (await fetch(`/api/area/${side}/${id}/grp16-notes`)).json(),
  ]);
  if (!d.table) { box.innerHTML = `<span class="muted">this area has no grp16 code table</span>`; return; }
  const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  box.innerHTML = `<div class="muted small">table @ $${d.table.toString(16).toUpperCase()} — X = event id, ($5F/$60) = record, RTS carry → VIEW_REFRESH</div>` +
    (d.events.map((e) => {
      const cells = e.cells.length
        ? "fired by " + e.cells.map(([c, r]) => `<a class="txt-use" data-cell="${c}:${r}">(${c},${r})</a>`).join(" ")
        : `<span class="muted">engine-fired (timer/heuristic — no op-6 cell)</span>`;
      const lst = e.listing.map((l) =>
        `<span class="muted">${l.addr.toString(16).toUpperCase().padStart(4, "0")}</span>  ${esc(l.text)}${l.comment ? `  <span class="cmt">; ${esc(l.comment)}</span>` : ""}`).join("\n");
      // machine-derived facts (wl_grp16): what the handler READS out of its EVENT record, which
      // records it resolves, which engine calls it makes. Shown above the note so it is obvious
      // which half is derived and which half is a human's reading.
      const f = (d.facts || {})[e.event];
      const chips = !f ? "" : [
        f.payload && f.payload.length
          ? `<span class="chip">reads event byte${f.payload.length > 1 ? "s" : ""} +${f.payload.join(", +")}</span>`
          : `<span class="chip muted">no payload — event id only</span>`,
        ...(f.acts_on || []).map((r) => `<span class="chip">acts on ${esc(r)}</span>`),
        ...(f.calls || []).map((c) => `<span class="chip">${esc(c)}</span>`),
        f.size ? `<span class="chip muted">${f.size} B reached</span>` : "",
      ].filter(Boolean).join(" ");
      return `<div class="code-ev"><div class="txt-head"><b>event ${e.event}</b> ${cells}</div>
        ${chips ? `<div class="code-facts">${chips}</div>` : ""}
        <pre class="code-pre">${lst || "(ptr $0000 — unused)"}</pre>
        <textarea class="txt-ta" data-ev="${e.event}" rows="2" placeholder="effect note — what this event does (map/world changes)…">\n${esc(notes.notes[e.event] || "")}</textarea></div>`;
    }).join("") || `<div class="muted">no op-6 cell fires a grp16 event here</div>`);
  box.querySelectorAll("textarea[data-ev]").forEach((ta) => ta.addEventListener("change", async () => {
    const r = await fetch(`/api/area/${side}/${id}/grp16-notes`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ event: +ta.dataset.ev, note: ta.value }) });
    status(r.ok ? `note saved (event ${ta.dataset.ev})` : `note save err ${r.status}`);
  }));
  box.querySelectorAll(".txt-use").forEach((a) => a.addEventListener("click", () => {
    const [c, r] = a.dataset.cell.split(":").map(Number);
    inspectCell(c, r);
  }));
}

// ── Physique: paint solidity onto the op-nibble layer. Wall = op-$B (bump, the engine's generic
//    wall; blocks in WAD5B), Floor = op-0. Only overwrites PHYSICS ops {0,$3,$4,$B,$F} — never a
//    script cell (op-$A/2/5/6/8/9/C), so logic is protected. See [[wasteland-physics-op-nibble]]. ──
const PHYS_OPS = new Set([0x0, 0x3, 0x4, 0xB, 0xF]);   // ops physique may repaint
const SOLID_OPS = new Set([0x3, 0x4, 0xB, 0xF]);        // ops the engine treats as solid (overlay)
let physTool = "wall", physValue = "wall", physPainting = false, physDirty = false, wallId = 0;
function setPhysTool(t) {
  physTool = t;
  if (t !== "fill") physValue = t;   // Wall/Floor/Erase set the paint value; Fill floods with it
  $("phys-tools").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.phys === t));
}
function setPhysDirty(d) { physDirty = d; $("phys-save").disabled = !d; $("dirty").hidden = !d && !dirty; }
// write one cell to a physique value. wall = op-$B (+ the area's wall id); floor = op-0 but ONLY over
// a physics op (keeps script cells); erase = op-0 FORCE, clears ANY cell incl. a script (its subtable
// record is left orphaned = harmless). This is how you DELETE a wall (Floor/Erase) or a script (Erase).
function physSet(col, row, val) {
  if (col < 0 || row < 0 || col >= area.width || row >= area.height) return;
  const i = row * area.width + col, cur = area.nibble[i];
  let op, id;
  if (val === "erase") { op = 0; id = 0; }
  else {
    if (!PHYS_OPS.has(cur)) return;                     // wall/floor protect script cells
    op = val === "wall" ? 0xB : 0; id = val === "wall" ? wallId : 0;
  }
  if (area.nibble[i] !== op || area.cellid[i] !== id) { area.nibble[i] = op; area.cellid[i] = id; setPhysDirty(true); }
}
function physApply(col, row) { physSet(col, row, physValue); }
function physFill(col, row) {                            // flood the contiguous same-op region
  const start = area.nibble[row * area.width + col];
  const st = [[col, row]];
  while (st.length) {
    const [x, y] = st.pop();
    if (x < 0 || y < 0 || x >= area.width || y >= area.height) continue;
    if (area.nibble[y * area.width + x] !== start) continue;
    physSet(x, y, physValue);
    st.push([x + 1, y], [x - 1, y], [x, y + 1], [x, y - 1]);
  }
}
async function savePhysics() {
  const [side, id] = $("area-select").value.split(":").map(Number);
  const r = await fetch(`/api/area/${side}/${id}/nibble`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ nibble: area.nibble, cellid: area.cellid }),
  });
  if (!r.ok) { status(`physics save err ${r.status}`); return; }
  const j = await r.json(); setPhysDirty(false); status(`saved physics (${j.bytes_changed} B)`);
  loadArea(side, id);
}

// ═══════════════════════════════════════════════════════════════════
//  TILESETS TAB
// ═══════════════════════════════════════════════════════════════════
let tsAtlas = null, tsGid = 0;
let te = null;   // tile editor state

async function tsInit(select) {
  const { tilesets } = await (await fetch("/api/tilesets")).json();
  $("ts-select").innerHTML = tilesets.map((t) => `<option value="${t.gfx_id}">tileset ${t.gfx_id}${t.origin === "work" ? " ✎" : ""}</option>`).join("");
  $("ts-select").onchange = () => tsLoad(Number($("ts-select").value));
  const g = select != null ? select : (tilesets[0] ? tilesets[0].gfx_id : 0);
  $("ts-select").value = g;
  await tsLoad(g);          // tsInit returned before its own load finished, so callers that waited
}                           // for it still raced tsLoad's `te = null; teShowEmpty()`
// the tile-editor panel is ALWAYS present now (fixed 420px column, sticky) so opening a tile no
// longer reflows the list. Toggle the placeholder vs the real body instead of hiding the column.
function teShowEmpty() { $("te-empty").hidden = false; $("te-body").hidden = true; }
function teShowBody() { $("te-empty").hidden = true; $("te-body").hidden = false; }
// the borrowed editor goes back where it lives, so the Tilesets tab is never left without it
function teGoHome() {
  const body = $("te-body"), home = $("te-home");
  if (body && home && body.parentElement !== home.parentElement) home.after(body);
  if (body) body.hidden = true;
}

// ── tileset sandbox: a 24×24 scratch grid (32px tiles) to try multi-tile compositions. Brush = the
// selected tile; drag paints, right-click erases. Pure client state — never saved, not the real map.
const SB_N = 24, SB_PX = 32;
let sbGrid = null, sbBrush = null, sbPainting = false;
function sbReset() { sbGrid = Array.from({ length: SB_N }, () => new Array(SB_N).fill(-1)); sbDraw(); }
function sbDraw() {
  const c = $("ts-sandbox"); if (!c) return; const cx = c.getContext("2d");
  cx.fillStyle = "#000"; cx.fillRect(0, 0, c.width, c.height);
  if (tsAtlas && sbGrid) for (let r = 0; r < SB_N; r++) for (let col = 0; col < SB_N; col++) {
    const t = sbGrid[r][col];
    if (t >= 0 && t < tsAtlas.num_tiles) blit(cx, tsAtlas, t, col * SB_PX, r * SB_PX, SB_PX, SB_PX);
  }
  cx.strokeStyle = "rgba(255,255,255,0.07)"; cx.lineWidth = 1;
  for (let i = 0; i <= SB_N; i++) {
    cx.beginPath(); cx.moveTo(i * SB_PX + 0.5, 0); cx.lineTo(i * SB_PX + 0.5, SB_N * SB_PX); cx.stroke();
    cx.beginPath(); cx.moveTo(0, i * SB_PX + 0.5); cx.lineTo(SB_N * SB_PX, i * SB_PX + 0.5); cx.stroke();
  }
}
function sbCellAt(e) {
  const r = $("ts-sandbox").getBoundingClientRect();
  return { col: Math.floor((e.clientX - r.left) / r.width * SB_N),
           row: Math.floor((e.clientY - r.top) / r.height * SB_N) };
}
function sbPaint(e, erase) {
  if (!sbGrid) return;
  const { col, row } = sbCellAt(e);
  if (col < 0 || col >= SB_N || row < 0 || row >= SB_N) return;
  const v = erase ? -1 : sbBrush;
  if (v == null || sbGrid[row][col] === v) return;
  sbGrid[row][col] = v; sbDraw();
}

async function tsLoad(gid) {
  tsGid = gid;
  const a = await (await fetch(`/api/tileset/1/${gid}`)).json();   // {side} ignored (global pool)
  tsAtlas = makeAtlas(a);
  $("ts-info").textContent = `tileset ${gid} · ${a.num_tiles} tiles`;
  $("ts-dirty").hidden = a.origin !== "work";
  $("ts-undo-btn").disabled = !a.undo_depth;
  te = null; teShowEmpty(); sbBrush = null; sbReset();   // new tileset -> reset the scratch sandbox + brush
  fetch(`/api/usage/tileset/${gid}`).then((r) => r.json()).then((u) => {
    $("ts-usage").innerHTML = u.used_by.length
      ? `<b>used by:</b> ${u.used_by.map((x) => usageLink(x.side, x.area_id, x.name || `area ${x.area_id}`)).join(" · ")}`
      : `<span class="muted">not used by any area yet</span>`;
    wireUsageLinks($("ts-usage"));
    const cur = $("ts-usage-area").value;                 // populate the "used in <area>" picker
    $("ts-usage-area").innerHTML = `<option value="">— all —</option>` +
      u.used_by.map((x) => `<option value="${x.side}:${x.area_id}">${x.name || "area " + x.area_id}</option>`).join("");
    if ([...$("ts-usage-area").options].some((o) => o.value === cur)) $("ts-usage-area").value = cur;
    applyTsUsage();
  });
  const pal = $("ts-palette"); pal.innerHTML = "";
  for (let tid = 0; tid < a.num_tiles; tid++) {
    const c = document.createElement("canvas"); c.width = c.height = 40; c.className = "tile"; c.dataset.tid = tid; c.title = `tile ${tid}`;
    blit(c.getContext("2d"), tsAtlas, tid, 0, 0, 40, 40);
    c.addEventListener("click", () => openTileEditor(tid));
    pal.appendChild(c);
  }
  if (!$("ts-history-wrap").hidden) tsHistoryLoad();   // keep the history panel in sync with the loaded gid
  $("ts-diff-wrap").hidden = true; tsDiff = null;      // a diff is against the atlas we just replaced
}
// highlight which tiles the selected area uses (used = outlined, unused = dimmed) → find removable tiles
async function applyTsUsage() {
  const pal = $("ts-palette"), v = $("ts-usage-area").value, tiles = pal.querySelectorAll(".tile");
  if (!v) { tiles.forEach((c) => c.classList.remove("used", "unused")); $("ts-usage-count").textContent = ""; return; }
  const [side, id] = v.split(":").map(Number);
  const a = await (await fetch(`/api/area/${side}/${id}`)).json();
  const used = new Set(a.tileids || []);
  let nUnused = 0;
  tiles.forEach((c) => { const t = +c.dataset.tid, u = used.has(t); c.classList.toggle("used", u); c.classList.toggle("unused", !u); if (!u) nUnused++; });
  $("ts-usage-count").textContent = `${used.size} used · ${nUnused} unused`;
}

// tile editor — MC pixels rendered 2:1 (each MC-px = 2 wide × 1 tall, scaled)
async function ensurePalette() { if (!PALETTE.length) PALETTE = (await (await fetch("/api/palette")).json()).palette; }
function teCharOf(px, py) { return (py >= 8 ? 2 : 0) + (px >= 4 ? 1 : 0); }
function teColor(v, ch) { return v === 0 ? te.bg : v === 3 ? te.cram : v === 1 ? te.chars[ch].hi : te.chars[ch].lo; }

async function openTileEditor(tid) {
  await ensurePalette();
  const t = await (await fetch(`/api/tileset/1/${tsGid}/tile/${tid}`)).json();
  te = { tid, pixels: t.pixels, chars: t.chars, bg: t.bg, cram: t.cram, val: 1 };
  teShowBody(); $("te-id").textContent = `#${tid} (gfx ${tsGid})`;
  sbBrush = tid;                       // the selected tile is the sandbox brush
  teDraw(); teControls();
  $("ts-palette").querySelectorAll(".tile").forEach((e) => e.classList.toggle("sel", +e.dataset.tid === tid));
}
function teDraw() {
  const c = $("te-canvas"), cx = c.getContext("2d");
  const uW = 44, uH = 22;                 // per-MC-pixel: 44 wide × 22 tall = 2:1
  c.width = 8 * uW; c.height = 16 * uH; cx.imageSmoothingEnabled = false;
  for (let py = 0; py < 16; py++) for (let px = 0; px < 8; px++) {
    cx.fillStyle = PALETTE[teColor(te.pixels[py][px], teCharOf(px, py))];
    cx.fillRect(px * uW, py * uH, uW, uH);
  }
  cx.strokeStyle = "#0006";
  for (let px = 0; px <= 8; px++) { cx.beginPath(); cx.moveTo(px * uW, 0); cx.lineTo(px * uW, 16 * uH); cx.stroke(); }
  for (let py = 0; py <= 16; py++) { cx.beginPath(); cx.moveTo(0, py * uH); cx.lineTo(8 * uW, py * uH); cx.stroke(); }
  cx.strokeStyle = "#e6a24e"; cx.lineWidth = 2;
  cx.strokeRect(0, 0, 4 * uW, 8 * uH); cx.strokeRect(4 * uW, 0, 4 * uW, 8 * uH);
  cx.strokeRect(0, 8 * uH, 4 * uW, 8 * uH); cx.strokeRect(4 * uW, 8 * uH, 4 * uW, 8 * uH);
}
function teControls() {
  const lab = ["%00 bg (global)", "%01 hi (per-char)", "%10 lo (per-char)", "%11 cram (global)"];
  $("te-vals").innerHTML = "<div class='te-h'>paint value</div>" + [0, 1, 2, 3].map((v) =>
    `<label class="te-val ${te.val === v ? "sel" : ""}"><input type="radio" name="tv" value="${v}" ${te.val === v ? "checked" : ""}> ${lab[v]}</label>`).join("");
  $("te-vals").querySelectorAll("input").forEach((el) => el.addEventListener("change", () => { te.val = +el.value; teControls(); }));
  const nm = ["TL", "TR", "BL", "BR"];
  const allRow = `<div class="te-char te-char-all"><span>ALL</span>${colSel("*", "hi", te.chars[0].hi)} ${colSel("*", "lo", te.chars[0].lo)}</div>`;
  $("te-chars").innerHTML = "<div class='te-h'>per-char colours (%01 / %10)</div>" + allRow + te.chars.map((ch, i) =>
    `<div class="te-char"><span>${nm[i]}</span>${colSel(i, "hi", ch.hi)} ${colSel(i, "lo", ch.lo)}</div>`).join("");
  $("te-chars").querySelectorAll("select").forEach((s) => s.addEventListener("change", () => {
    const nib = s.dataset.nib, v = +s.value;
    if (s.dataset.ch === "*") te.chars.forEach((ch) => { ch[nib] = v; });   // ALL: set every quadrant
    else te.chars[+s.dataset.ch][nib] = v;
    teDraw(); teControls();
  }));
}
function colSel(i, nib, cur) {
  return `<select class="te-col" data-ch="${i}" data-nib="${nib}" style="background:${PALETTE[cur]}">${PALETTE.map((h, ci) => `<option value="${ci}" ${ci === cur ? "selected" : ""}>${ci.toString(16).toUpperCase()}</option>`).join("")}</select>`;
}
function tePaint(e) {
  const c = $("te-canvas"), r = c.getBoundingClientRect();
  const px = ((e.clientX - r.left) / (r.width / 8)) | 0, py = ((e.clientY - r.top) / (r.height / 16)) | 0;
  if (px < 0 || py < 0 || px >= 8 || py >= 16) return;
  te.pixels[py][px] = te.val; teDraw();
}
// ONE painter, two targets. A block carries its own tiles, so painting one must write into the
// BLOCK — writing it back to a tileset would leak the edit into every area sharing that tileset,
// which is exactly what carrying the art exists to prevent.
async function teSave() {
  if (te.block) {
    const r = await fetch(`/api/block/${encodeURIComponent(te.block)}/art/${te.ai}`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ pixels: te.pixels, chars: te.chars }) });
    if (!r.ok) { status(`could not save: ${(await r.json()).detail || r.status}`); return; }
    const bid = te.block, ai = te.ai;
    status(`painted tile ${ai} of ${bid}`);
    te = null; teGoHome();
    blCat = null; await blLoad(); await blOpen(bid);
    return;
  }
  const r = await fetch(`/api/tileset/1/${tsGid}/tile/${te.tid}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ pixels: te.pixels, chars: te.chars }) });
  const j = await r.json(); status(`saved tile ${te.tid} → ${j.saved}`);
  const tid = te.tid; await tsLoad(tsGid); openTileEditor(tid); syncMapAtlas(tsGid);
}
async function teClear() {
  if (!te) return;
  te.pixels = te.pixels.map((r) => r.map(() => 0));
  te.chars = te.chars.map(() => ({ hi: 0, lo: 0 }));
  teDraw(); teControls();
  await teSave();
}
// ── copy / paste (within a set) + flip X/Y. Edits are unsaved until "Save tile". ──
let teClip = null;
function teCopy() { if (!te) return; teClip = { pixels: te.pixels.map((r) => r.slice()), chars: te.chars.map((c) => ({ ...c })) }; status(`copied tile ${te.tid} — open a slot + Paste`); }
function tePaste() { if (!te || !teClip) { status("clipboard empty (Copy a tile first)"); return; } te.pixels = teClip.pixels.map((r) => r.slice()); te.chars = teClip.chars.map((c) => ({ ...c })); teDraw(); teControls(); status("pasted — Save tile to keep"); }
function teFlipX() {          // mirror horizontally: reverse each row + swap L/R chars so colours follow
  if (!te) return;
  te.pixels = te.pixels.map((r) => r.slice().reverse());
  const c = te.chars; [c[0], c[1]] = [c[1], c[0]]; [c[2], c[3]] = [c[3], c[2]];
  teDraw(); teControls(); status("flipped ⇄ — Save tile to keep");
}
function teFlipY() {          // mirror vertically: reverse rows + swap T/B chars
  if (!te) return;
  te.pixels = te.pixels.slice().reverse();
  const c = te.chars; [c[0], c[2]] = [c[2], c[0]]; [c[1], c[3]] = [c[3], c[1]];
  teDraw(); teControls(); status("flipped ⇅ — Save tile to keep");
}
async function tsUndo() {
  const r = await fetch(`/api/tileset/1/${tsGid}/undo`, { method: "POST" });
  const j = await r.json();
  status(j.undone ? `undo (${j.depth} left)` : "nothing to undo");
  const tid = te ? te.tid : null; await tsLoad(tsGid); if (tid !== null) openTileEditor(tid); syncMapAtlas(tsGid);
}
async function tsCompact() {
  if (!confirm(`Pack tileset ${tsGid}: move empty tiles to the end (tile 0 stays fixed).\nEvery map using this tileset is remapped so it still shows the same tiles.\nNote: engine-fixed tiles (5=loot sack, 8=default) will move too. Continue?`)) return;
  const r = await fetch(`/api/tileset/1/${tsGid}/compact`, { method: "POST" });
  const j = await r.json();
  status(j.compacted ? `packed: ${j.non_empty} tiles → front; remapped ${j.maps_remapped.length} map(s): ${j.maps_remapped.join(", ")}` : `not packed (${j.reason})`);
  tsLoad(tsGid); syncMapAtlas(tsGid, true);
}
async function tsClearUnused() {
  if (!confirm(`Blank every tile that NO area using tileset ${tsGid} references in its map?\n(engine-fixed 0/5/8 kept · undoable · then use ⊟ pack to compact)`)) return;
  const r = await fetch(`/api/tileset/1/${tsGid}/clear-unused`, { method: "POST" });
  const j = await r.json();
  status(j.count ? `cleared ${j.count} unused tile(s) (${j.used} used) — now ⊟ pack to compact` : "nothing to clear — all tiles are used");
  tsLoad(tsGid); syncMapAtlas(tsGid);
}
async function tsRevert() {
  if (!confirm(`Revert tileset ${tsGid} to baseline? All edits to it are dropped.`)) return;
  const r = await fetch(`/api/tileset/1/${tsGid}/revert`, { method: "POST" });
  const j = await r.json();
  status(j.reverted ? `reverted tileset ${tsGid} to baseline` : "nothing to revert (already baseline)");
  tsLoad(tsGid); syncMapAtlas(tsGid);
}

// ── history: every editor save of this tileset is a git commit; list them + restore any version ──
function histWhen(iso) {                          // "2026-07-24T14:20:59+02:00" -> short local stamp
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString([], { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
function histLabel(msg) {                          // commit subject -> a short human tag (tileset + map ops)
  const m = msg.replace(/^editor save:\s*/, "");
  const many = (m.match(/\(\+(\d+) more\)/) || [])[1];        // coalesced burst marker
  const tag = /\/tile\/(\d+)/.test(m) ? `tile ${m.match(/\/tile\/(\d+)/)[1]}`
    : /\/tiles\b/.test(m) ? "paint tiles"
    : /\/copytiles/.test(m) ? "copy tiles"
    : /\/compact/.test(m) ? "pack / compact"
    : /\/clear-unused/.test(m) ? "clear unused"
    : /\/gc-strings/.test(m) ? "remove unused strings"
    : /\/fill-tile/.test(m) ? "off-map tile"
    : /\/nibble/.test(m) ? "paint physique"
    : /\/script/.test(m) ? "edit script"
    : /\/prose/.test(m) ? "edit text"
    : /\/npc/.test(m) ? "edit NPC"
    : /\/restore/.test(m) ? "restore version"
    : /\/revert/.test(m) ? "revert → baseline"
    : /\/new\b/.test(m) ? "new"
    : m.replace(/^(POST|PUT|DELETE)\s+\/api\//, "").replace(/\s*\(\+\d+ more\)/, "");
  return many ? `${tag} +${many}` : tag;
}
async function tsHistoryToggle() {
  const w = $("ts-history-wrap");
  w.hidden = !w.hidden;
  if (!w.hidden) await tsHistoryLoad();
}
async function tsHistoryLoad() {
  const box = $("ts-history");
  box.innerHTML = "<span class='muted'>loading…</span>";
  const j = await (await fetch(`/api/history/tileset/${tsGid}`)).json();
  if (!j.history || !j.history.length) {
    box.innerHTML = `<span class="muted">no edit history yet — tileset ${tsGid} is still at pristine baseline. Edits auto-commit and show up here.</span>`;
    return;
  }
  box.innerHTML = j.history.map((h) =>
    `<div class="hist-row"><button class="mini hist-restore" data-rev="${h.rev}" title="restore this version">⟲</button>`
    + `<button class="mini hist-diff" data-rev="${h.rev}" title="diff this version vs now">≠</button>`
    + `<span class="hist-when">${histWhen(h.time)}</span>`
    + `<span class="hist-lbl">${histLabel(h.label)}</span>`
    + `<code class="hist-rev">${h.short}</code></div>`).join("");
  box.querySelectorAll(".hist-restore").forEach((b) =>
    b.addEventListener("click", () => tsHistoryRestore(b.dataset.rev)));
  box.querySelectorAll(".hist-diff").forEach((b) =>
    b.addEventListener("click", () => tsDiffShow(b.dataset.rev)));
}
// ── tileset diff: old version vs now — changed tiles outlined, A/B toggle ──
let tsDiff = null;
async function tsDiffShow(rev) {
  const r = await fetch(`/api/diff/tileset/${tsGid}?rev=${rev}`);
  const j = await r.json();
  if (!r.ok) { status(`diff failed: ${j.detail || r.status}`); return; }
  tsDiff = { rev: j.rev, changed: new Set(j.changed), oldAtlas: makeAtlas(j.old_atlas), showOld: true };
  $("ts-diff-wrap").hidden = false;
  $("ts-diff-info").textContent = `vs ${j.rev} · ${j.changed.length} tile(s) changed`;
  tsDiffDraw();
}
function tsDiffDraw() {
  if (!tsDiff) return;
  const cols = 16, size = 40, n = 96, rows = Math.ceil(n / cols);
  const c = $("ts-diff-canvas"); c.width = cols * size; c.height = rows * size;
  const cx = c.getContext("2d"); cx.imageSmoothingEnabled = false;
  $("ts-diff-toggle").textContent = tsDiff.showOld ? "show: old" : "show: now";
  const atlas = tsDiff.showOld ? tsDiff.oldAtlas : tsAtlas;
  for (let t = 0; t < n; t++) {
    const x = (t % cols) * size, y = ((t / cols) | 0) * size;
    blit(cx, atlas, t, x, y, size, size);
  }
  cx.strokeStyle = "#e6635a"; cx.lineWidth = 2;
  for (const t of tsDiff.changed)
    cx.strokeRect((t % cols) * size + 1, ((t / cols) | 0) * size + 1, size - 2, size - 2);
}
async function tsHistoryRestore(rev) {
  if (!confirm(`Restore tileset ${tsGid} to version ${rev.slice(0, 9)}?\nThe current state is pushed to ↶ undo first.`)) return;
  const r = await fetch(`/api/history/tileset/${tsGid}/restore`,
    { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ rev }) });
  const j = await r.json();
  if (!r.ok) { status(`restore failed: ${j.detail || r.status}`); return; }
  status(`restored tileset ${tsGid} to ${j.rev} (${j.bytes} B) — ↶ undo to step back`);
  await tsLoad(tsGid); syncMapAtlas(tsGid);
  if (!$("ts-history-wrap").hidden) tsHistoryLoad();   // the restore itself added a commit — refresh
}

// ── copy-tiles overlay: pick source tiles (⌘/Shift) → click a destination start slot ──
let copyAtlas = null, copySel = [], copyDst = 0;
async function openCopyOverlay() {
  const { tilesets } = await (await fetch("/api/tilesets")).json();
  $("copy-src-select").innerHTML = tilesets.map((t) => `<option value="${t.gfx_id}">tileset ${t.gfx_id}</option>`).join("");
  $("copy-src-select").onchange = () => copyLoadSrc(Number($("copy-src-select").value));
  $("copy-overlay").hidden = false; copySel = []; copyDst = 0;
  $("copy-dst-name").textContent = `tileset ${tsGid}`;
  const first = tilesets.find((t) => t.gfx_id !== tsGid) || tilesets[0];
  $("copy-src-select").value = first.gfx_id;
  copyLoadSrc(first.gfx_id);
  copyRenderDst();
}
function copyRenderDst() {
  const g = $("copy-dst-grid"); g.innerHTML = "";   // the current (target) tileset
  for (let tid = 0; tid < tsAtlas.num_tiles; tid++) {
    const c = document.createElement("canvas"); c.width = c.height = 40; c.className = "tile"; c.dataset.tid = tid; c.title = `slot ${tid}`;
    blit(c.getContext("2d"), tsAtlas, tid, 0, 0, 40, 40);
    c.addEventListener("click", () => { copyDst = tid; $("copy-dst").textContent = tid; copyHighlightDst(); });
    g.appendChild(c);
  }
  copyHighlightDst();
}
function copyHighlightDst() {                          // highlight the slots the copy will overwrite
  const n = copySel.length || 1;
  $("copy-dst-grid").querySelectorAll(".tile").forEach((el) => {
    const t = +el.dataset.tid; el.classList.toggle("sel", t >= copyDst && t < copyDst + n);
  });
}
let copyAnchor = 0;
function renderCopySel() {
  const g = $("copy-grid");
  g.querySelectorAll(".tile").forEach((x) => x.classList.toggle("sel", copySel.includes(+x.dataset.tid)));
  $("copy-count").textContent = copySel.length;
}
async function copyLoadSrc(gid) {
  copyAtlas = { gid, atlas: makeAtlas(await (await fetch(`/api/tileset/1/${gid}`)).json()) };
  copySel = []; copyAnchor = 0; $("copy-count").textContent = 0;
  const g = $("copy-grid"); g.innerHTML = "";
  for (let tid = 0; tid < copyAtlas.atlas.num_tiles; tid++) {
    const c = document.createElement("canvas"); c.width = c.height = 40; c.className = "tile"; c.dataset.tid = tid; c.title = `tile ${tid}`;
    blit(c.getContext("2d"), copyAtlas.atlas, tid, 0, 0, 40, 40);
    c.addEventListener("click", (e) => {
      const meta = e.metaKey || e.ctrlKey;
      if (e.shiftKey) {                              // range anchor..tid
        const lo = Math.min(copyAnchor, tid), hi = Math.max(copyAnchor, tid);
        const range = []; for (let t = lo; t <= hi; t++) range.push(t);
        copySel = meta ? [...new Set([...copySel, ...range])] : range;
      } else if (meta) {                             // toggle one
        const k = copySel.indexOf(tid);
        if (k >= 0) copySel.splice(k, 1); else copySel.push(tid);
        copyAnchor = tid;
      } else {                                       // select one
        copySel = [tid]; copyAnchor = tid;
      }
      renderCopySel(); copyHighlightDst();
    });
    g.appendChild(c);
  }
}
async function copyDo() {
  if (!copySel.length) return;
  const sorted = [...copySel].sort((a, b) => a - b);
  // place source tiles into consecutive destination slots from copyDst (clamped to 0..95)
  const tiles = sorted.map((t, i) => [t, Math.min(95, copyDst + i)]);
  const r = await fetch(`/api/tileset/1/${tsGid}/copytiles`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ src_side: 1, src_gid: copyAtlas.gid, tiles }) });
  const j = await r.json(); status(`copied ${j.copied} tile(s) → slot ${copyDst}+`);
  $("copy-overlay").hidden = true; tsLoad(tsGid); syncMapAtlas(tsGid);
}

// ═══════════════════════════════════════════════════════════════════
//  PORTRAITS TAB
// ═══════════════════════════════════════════════════════════════════
let ptLoaded = false, pe = null, portraitTimer = null, ptAll = null;

async function ptInit() {
  ptLoaded = true;
  ptAll = (await (await fetch("/api/portraits")).json()).portraits;
  ptRender();
}
function ptRender() {
  const hideDups = $("pt-hide-dups").checked;
  const list = $("pt-list"); list.innerHTML = "";
  let shown = 0;
  for (const p of ptAll) {
    if (hideDups && p.dup_of) continue;
    shown++;
    const t = document.createElement("div"); t.className = "pt-thumb";   // global id pool — no disk-side in the UI
    t.title = `str ${p.str_id}` + (p.dup_of ? ` — identical to ${p.dup_of} (same pixels + animation)` : "");
    t.innerHTML = `<img src="/api/portrait/${p.side}/${p.str_id}" onerror="this.parentElement.classList.add('missing')"><span>${p.str_id}${p.dup_of ? " ≡" : ""}</span>`;
    t.addEventListener("click", () => ptOpen(p.side, p.str_id, `str ${p.str_id}`));
    list.appendChild(t);
  }
  $("pt-info").textContent = hideDups ? `${shown} unique (${ptAll.length - shown} dups hidden)` : `${shown} portraits`;
}
function ptOpen(side, strId, label) {
  $("npc-view").innerHTML = `<div class="insp-head">${label} <button class="mini" id="pe-open">✎ edit</button></div>` +
    `<canvas class="insp-portrait big" id="portrait-canvas" data-side="${side}" data-str="${strId}"></canvas>` +
    `<div class="muted small">animated (4-slot EOR)</div>`;
  playPortrait($("portrait-canvas"), side, strId);
  $("pe-open").addEventListener("click", () => openPortraitEditor(side, strId, label));
  $("portrait-editor").hidden = true; pe = null;
  fetch(`/api/usage/portrait/${strId}`).then((r) => r.json()).then((u) => {
    const el = document.createElement("div"); el.className = "usage";
    el.innerHTML = u.used_by.length
      ? `<b>used by:</b><br>${u.used_by.map((x) => `${usageLink(x.side, x.area_id, x.name)} <span class="muted">(${x.as.join(", ")})</span>`).join("<br>")}`
      : `<span class="muted">no reference FOUND — the scan reads every area's foe table, hireable
           NPCs and SERVICE records, and both picture vectors ($041C in the scene blobs, $0419 in
           the overlays). It still cannot see an id that code COMPUTES rather than writes out, so
           absent here is not the same as unused.</span>`;
    $("npc-view").appendChild(el);
    wireUsageLinks(el);
  });
}

function usageLink(side, aid, label) {
  if (aid < 0) return `<span class="muted">${label}</span>`;   // native/global note — not a clickable area
  return `<a class="usage-link" data-side="${side}" data-area="${aid}">${label}</a>`;
}
function wireUsageLinks(container) {
  container.querySelectorAll(".usage-link").forEach((a) =>
    a.addEventListener("click", () => {
      const cell = a.dataset.cell ? a.dataset.cell.split(":").map(Number) : null;
      gotoArea(Number(a.dataset.side), Number(a.dataset.area), cell);
    }));
}
// Landing on the AREA when the link named a CELL is half an answer: you arrive on a 32×32 map with
// no idea which square was meant. With a cell, it opens that square's inspector and the chain that
// runs through it — the reason for clicking a coordinate is to see what happens there.
async function gotoArea(side, aid, cell) {
  const sel = $("area-select");
  if (![...sel.options].some((o) => o.value === `${side}:${aid}`)) return;
  sel.value = `${side}:${aid}`;
  setTab("map"); setMode("view");
  await loadArea(side, aid);
  if (!cell) return;
  const [c, r] = cell;
  inspectCell(c, r);
  chainFocus(c, r);
}

async function newTilesetDialog() {
  const copy = confirm("New tileset: OK = copy an existing one · Cancel = empty (blank tiles)");
  const body = { mode: copy ? "copy" : "empty" };
  if (copy) { const g = prompt("Copy from tileset id:", "1"); if (g === null) return; body.from_gid = Number(g); }
  const r = await fetch("/api/tileset/new", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) { alert(`failed: ${r.status}`); return; }
  const j = await r.json();
  allTilesets = null;                       // refresh the area picker's cache too
  tsInit(j.gfx_id); status(`created tileset ${j.gfx_id} (${j.mode})`);
}
// ── portrait animation speed: ONE number, used by both previews ──────────────────────────
// How long the game holds ONE unit of a frame's `delay` byte. Both previews were wrong in
// different directions and disagreed with each other, so it lives here now.
//
// The engine does NOT pace this by raster. `frame_gate_ac` ($05C5) gates on `dec $B1` /
// `dec $B2`, $B2 reloads to 2, and **$B1 is never written anywhere in the game** — it is a
// free-running mod-256 counter. So one delay unit = 2 * 256 = 512 calls to frame_gate_ac, and
// what calls it is whichever loop happens to be spinning.
//
// Only two loops matter, because a portrait is on screen ONLY in the encounter/dialogue
// screen — never on the map (Alex). Those are: the blocking key-wait ($0247 -> W2514), a
// ~231-cycle spin, so 512 passes ~= 118k cycles ~= 120 ms; and text output, which ticks 3x
// per printed character, so the animation runs FASTER while a message is being typed out.
// The key-wait is the steady state and is what this constant models.
//
// 140 wins over that 120 because Alex measured it by eye against the real Sentinel scene, and
// the cycle count cannot know which loop is actually spinning. The two agree within 15%.
//
// (The harness model — "one tick = one PAL frame, so delay = 2 frames = 40 ms" — only holds for
// portraits/christina/*.asm, which force $B1=1 every pass. It is not what the game does.)
const MS_PER_DELAY_UNIT = 140;
async function playPortrait(cv, side, strId) {
  if (portraitTimer) { clearTimeout(portraitTimer); portraitTimer = null; }
  const cx = cv.getContext("2d"); cx.imageSmoothingEnabled = false;
  let anim = null;
  try { const r = await fetch(`/api/portrait/${side}/${strId}/anim`); if (r.ok) anim = await r.json(); } catch {}
  if (!anim || !anim.frames || !anim.frames.length) {
    const img = new Image(); img.onload = () => { cv.width = img.width; cv.height = img.height; cx.imageSmoothingEnabled = false; cx.drawImage(img, 0, 0); }; img.src = `/api/portrait/${side}/${strId}`; return;
  }
  // anim frames are already 2:1-corrected (w = 44 MC-px doubled = 88) — draw 1:1, CSS scales.
  cv.width = anim.w; cv.height = anim.h;
  const off = document.createElement("canvas"); off.width = anim.w; off.height = anim.h; const oc = off.getContext("2d");
  const bmps = anim.frames.map((b) => new ImageData(Uint8ClampedArray.from(atob(b), (c) => c.charCodeAt(0)), anim.w, anim.h));
  let f = 0;
  const tick = () => {
    oc.putImageData(bmps[f], 0, 0); cx.imageSmoothingEnabled = false; cx.drawImage(off, 0, 0);
    // `delays` come back in harness TICKS, and one walk = one delay unit = 2 ticks. The old
    // Math.max(60, d*20) floor was the bug: the backend expands the timeline into equal 2-tick
    // steps, so d is 2 for essentially every frame, the floor won every time, and the whole
    // animation played at a flat 210 ms per step instead of 140.
    const d = anim.delays[f] || 2; f = (f + 1) % bmps.length;
    portraitTimer = setTimeout(tick, (d / 2) * MS_PER_DELAY_UNIT);
  };
  tick();
}

function copyGrid(g) { return g.map((r) => r.slice()); }


// ── FLOW TAB — script chains as EVENTS ────────────────────────────────
// A Highpool fight round spans several cells: a SAY cell arms a KAMPF cell, whose loot sack arms
// the next round elsewhere. So the unit here is the connected component, not the cell. Each row is
// one cell's own sequence; an arrow into a different cell is what glued the component together.
const FLOW_OPS = { 0: "empty", 1: "SAY", 2: "COND", 3: "FIGHT", 4: "object", 5: "loot sack",
                   6: "event", 9: "flag", 10: "door", 11: "bump", 12: "PATCH" };
// (the old `flowData`/`flowSel` pair went with the canvas — see eventsData/eventsSel below)

async function flowInit() { await flowLoad(); }

// ═══════════════ EVENTS — the area as OUTLINES ═══════════════
//
// This replaced an SVG canvas. The canvas existed to show STRUCTURE, and it did, but it could not
// be edited, its edge labels collided with its nodes, it grew off the right of the screen, and it
// followed only the first branch out of any record — so a KEYWORD with three keywords was drawn as
// one line. An outline shows the same structure: a chain reads top to bottom, and indentation
// marks the places that actually fork. It scrolls, it takes a keyboard, and the form opens inside
// it.
let eventsData = null, eventsSel = null;

async function flowLoad(focus) {
  const [side, id] = $("area-select").value.split(":").map(Number);
  const box = $("flow-list"); box.innerHTML = "…";
  try { eventsData = await (await fetch(`/api/area/${side}/${id}/events`)).json(); }
  catch { box.innerHTML = "<div class='muted'>failed</div>"; return; }
  areaVocab = eventsData.vocab || areaVocab;
  const C = eventsData.counts || {};
  $("flow-count").textContent = `${C.chains || 0} chains · ${C.quest || 0} on a questline`;

  const all = eventsData.groups.flatMap((g) => g.chains);
  const card = (c) => {
    const first = c.rows.find((r) => r.grp !== null) || {};
    const q = (c.quests || []).map((x) => fesc(eventsData.quest_names[x] || x)).join(" · ");
    return `<div class="ev-card${c.quests.length ? " on-quest" : ""}" data-i="${all.indexOf(c)}">
      <b>(${c.root.join(",")})</b>
      <span class="muted small">${c.beats} beat${c.beats === 1 ? "" : "s"}${c.forks ? ` · ${c.forks} fork${c.forks === 1 ? "" : "s"}` : ""}</span>
      ${q ? `<div class="ev-q">${q}</div>` : ""}
      <div class="ev-t">${fesc((first.label || BEAT_NAME[first.op] || "").slice(0, 52))}</div></div>`;
  };
  box.innerHTML = eventsData.groups.filter((g) => g.chains.length).map((g, gi) =>
    `<div class="fl-sub ev-sub${g.key === "trivial" ? " folded" : ""}" data-g="${gi}">${g.key === "trivial" ? "▸ " : ""}${fesc(g.label)}
       <span class="muted small">${g.chains.length}</span></div>
     <div class="ev-grp"${g.key === "trivial" ? " hidden" : ""}>${g.chains.map(card).join("")}</div>`).join("")
    || "<div class='muted'>nothing scripted in this area</div>";

  box.querySelectorAll(".ev-sub").forEach((h) => h.addEventListener("click", () => {
    const g = h.nextElementSibling; g.hidden = !g.hidden;
    h.classList.toggle("folded", g.hidden);
    h.firstChild.textContent = (g.hidden ? "▸ " : "") + h.firstChild.textContent.replace(/^▸ /, "");
  }));
  box.querySelectorAll(".ev-card").forEach((el) =>
    el.addEventListener("click", () => flowShow(+el.dataset.i)));

  let pick = 0;
  if (focus) {
    const hit = all.findIndex((c) => c.rows.some((r) => r.cell && r.cell[0] === focus[0] && r.cell[1] === focus[1]));
    if (hit >= 0) pick = hit;
  }
  if (all.length) flowShow(pick, focus); else $("flow-detail").innerHTML = "<div class='muted'>—</div>";
}

// One chain, top to bottom. Plumbing rides the arrows; a beat that forks pushes its children in by
// one step; a chain that runs back into one already shown says so instead of looping.
// ── the chain as a FLOWCHART ──────────────────────────────────────────────────────────────────
//
// A story is a graph, and an outline is a graph flattened until the branches are gone. It reads
// fine while a chain is a straight line — which most are — and stops working exactly where it
// matters: Highpool's loot sack at (26,6) hands over to a fight AND to a grave marker, and two
// indented runs under one row do not say "these are alternatives".
//
// Layout is the classic tidy-tree, one pass: a leaf takes the next free column, a parent centres
// over its children. Nodes are real DOM (clickable, selectable, styleable like every other beat);
// only the edges are SVG, because an orthogonal connector is a path and nothing else.
const GN = { w: 208, h: 62, gx: 26, gy: 62, fh: 38, fgy: 8 };

// A FAN is a node whose children are all endings — a SAY with 29 keyword replies, a gate with a
// dozen refusals. Spread across columns it is 7000px of empty picture (Red Ryder's chain measured
// exactly that); stacked in one column it is a list, which is how it reads anyway: "any of these".
// Four or more, because two or three side by side still say "alternatives" better than a list does.
const isFan = (kids) => kids.length >= 4 && kids.every((k) => !(k.children || []).length);

function graphLayout(root) {
  const nodes = [], edges = [];
  let col = 0, fanBottom = 0;
  (function walk(n, depth, parent, y) {
    const id = nodes.length;
    const node = { id, depth, n, x: 0, y };
    nodes.push(node);
    if (parent !== null) {
      const e = n.in_edge || {};
      edges.push({ from: parent, to: id, label: e.label || "", effects: e.effects || [], via: e.via || [] });
    }
    const kids = n.children || [];
    if (!kids.length) { node.x = col * (GN.w + GN.gx); col++; }
    else if (isFan(kids)) {
      node.x = col * (GN.w + GN.gx); col++;
      kids.forEach((k, i) => {
        const kid = { id: nodes.length, depth: depth + 1, n: k, fan: true, h: GN.fh,
                      x: node.x + GN.w + GN.gx, y: y + GN.h + GN.gy + i * (GN.fh + GN.fgy) };
        nodes.push(kid);
        const e = k.in_edge || {};
        edges.push({ from: id, to: kid.id, fan: true, label: e.label || "",
                     effects: e.effects || [], via: e.via || [] });
        fanBottom = Math.max(fanBottom, kid.y + GN.fh);
      });
      col++;                                            // the stack owns a column of its own
    } else {
      const xs = kids.map((k) => walk(k, depth + 1, id, y + GN.h + GN.gy));
      node.x = (xs[0] + xs[xs.length - 1]) / 2;
    }
    return node.x;
  })(root, 0, null, 0);
  const w = Math.max(...nodes.map((n) => n.x)) + GN.w;
  const h = Math.max(fanBottom, ...nodes.map((n) => n.y + (n.h || GN.h)));
  return { nodes, edges, w, h };
}

// An orthogonal connector: down out of the parent, across at the midline, down into the child.
// Straight lines between centres cross the boxes as soon as a fork is wider than one column.
function edgePath(a, b, fan) {
  // A fan child is entered from the LEFT: one spine down the parent's centre, a stub into each row.
  if (fan) {
    const x1 = a.x + GN.w / 2, y1 = a.y + GN.h, y2 = b.y + GN.fh / 2;
    return `M${x1},${y1} L${x1},${y2} L${b.x},${y2}`;
  }
  const x1 = a.x + GN.w / 2, y1 = a.y + GN.h, x2 = b.x + GN.w / 2, y2 = b.y, my = y1 + GN.gy / 2;
  return x1 === x2 ? `M${x1},${y1} L${x2},${y2}`
                   : `M${x1},${y1} L${x1},${my} L${x2},${my} L${x2},${y2}`;
}

function graphRender(c, box) {
  const g = graphLayout(c.tree);
  const svg = g.edges.map((e) => {
    const a = g.nodes[e.from], b = g.nodes[e.to];
    const eff = (e.effects || []).map((x) => edgeEffectText(x)).join(" · ");
    const lbl = [e.label, eff].filter(Boolean).join(": ");
    // The label sits just above ITS CHILD, not at the midpoint of the horizontal run: at a fork
    // both edges share that midpoint, so the two labels landed on top of each other. Children are
    // at least one node-width apart, so anchoring on the child cannot collide.
    // a fan row already carries its own reply text; a label per stub would be 29 duplicates
    return `<path d="${edgePath(a, b, e.fan)}" class="gedge"/>` +
      (lbl && !e.fan ? `<foreignObject x="${b.x - 6}" y="${b.y - 34}" width="${GN.w + 12}" height="32">
                <div xmlns="http://www.w3.org/1999/xhtml" class="gedge-lbl" title="${fesc(lbl)}">${fesc(lbl)}</div>
              </foreignObject>` : "");
  }).join("");
  const html = g.nodes.map((nd) => {
    const n = nd.n;
    if (n.grp === null) return `<div class="gnode gend" style="left:${nd.x}px;top:${nd.y}px">ends here</div>`;
    const where = n.cell ? `(${n.cell.join(",")})` : "—";
    if (nd.fan)
      return `<div class="gnode gfan${n.rejoin ? " rejoin" : ""}" data-rec="${n.grp}:${n.cid}"
                   style="left:${nd.x}px;top:${nd.y}px" title="grp${n.grp}[${n.cid}] · ${fesc(((n.in_edge || {}).label) || "")}">
        <span class="chain-op">${BEAT_NAME[n.op] || n.op}</span>
        <span class="beat-cell">${where}</span>
        <span class="gfan-t">${fesc((n.label || "").slice(0, 70))}</span></div>`;
    return `<div class="gnode${n.rejoin ? " rejoin" : ""}" data-rec="${n.grp}:${n.cid}"
                 style="left:${nd.x}px;top:${nd.y}px" title="grp${n.grp}[${n.cid}]">
      <div class="gnode-h"><span class="chain-op">${BEAT_NAME[n.op] || n.op}</span>
        <span class="beat-cell">${where}</span>
        ${n.cell_owner === "same" ? `<span class="muted small">same square</span>` : ""}</div>
      <div class="gnode-t">${fesc((n.label || "").slice(0, 96))}</div>
      ${n.rejoin ? `<div class="ev-rejoin">↑ shown earlier</div>` : ""}</div>`;
  }).join("");
  box.innerHTML = `<div class="gwrap" style="width:${g.w}px;height:${g.h}px">
      <svg class="gsvg" width="${g.w}" height="${g.h}">${svg}</svg>${html}</div>`;
  return g;
}

let evShape = "flow";
let evGraph = null;
const treeScale = { kx: 1, ky: 1, ox: 0, oy: 0 };

// The whole chain in the header, at a glance — the same layout the big view uses, scaled to fit,
// plus a rectangle for what is on screen. Two facts a long chain hides: how big is this, and where
// am I in it. Drawn, not laid out: at this size a node is 4px and needs no text, so a canvas is
// both cheaper and sharper than 43 more DOM boxes.
function treeMini(g) {
  const cv = $("ev-tree"); if (!cv || !g) return;
  const box = cv.getBoundingClientRect();
  const cw = Math.max(120, Math.round(box.width)), ch = 168;
  const dpr = window.devicePixelRatio || 1;
  cv.width = cw * dpr; cv.height = ch * dpr;
  const x = cv.getContext("2d"); x.setTransform(dpr, 0, 0, dpr, 0, 0);
  x.clearRect(0, 0, cw, ch);
  const pad = 6;
  // Uniform scale would draw Red Ryder's chain — 4.4x taller than it is wide — as a 36px strip in a
  // 436px box: correct, and too thin to aim at. X is allowed to stretch up to 3x the fitting scale,
  // which widens a narrow tree without letting a wide one distort (a wide tree hits the width limit
  // first, so its forks keep their shape).
  const ky = (ch - pad * 2) / g.h;
  const kx = Math.min((cw - pad * 2) / g.w, ky * 3);
  treeScale.kx = kx; treeScale.ky = ky;
  treeScale.ox = (cw / kx - g.w) / 2;
  treeScale.oy = (ch / ky - g.h) / 2;
  const X = (v) => (v + treeScale.ox) * kx, Y = (v) => (v + treeScale.oy) * ky;

  x.strokeStyle = "#3d4654"; x.lineWidth = 1;
  g.edges.forEach((e) => {
    const a = g.nodes[e.from], b = g.nodes[e.to];
    x.beginPath();
    x.moveTo(X(a.x + GN.w / 2), Y(a.y + GN.h));
    x.lineTo(X(b.x + GN.w / 2), Y(b.y));
    x.stroke();
  });
  const selKey = document.querySelector("#ev-body [data-rec].sel")?.dataset.rec;
  g.nodes.forEach((n) => {
    const h = n.h || GN.h;
    const isSel = selKey && n.n.grp !== null && `${n.n.grp}:${n.n.cid}` === selKey;
    x.fillStyle = isSel ? "#e6a24e" : n.n.rejoin ? "#3d4654" : "#6f7c90";
    x.fillRect(X(n.x), Y(n.y), Math.max(3, GN.w * kx), Math.max(2, h * ky));
  });
  // what is on screen right now
  const b = $("ev-body");
  if (b) {
    x.strokeStyle = "#e6a24e"; x.lineWidth = 1.5;
    x.strokeRect(X(b.scrollLeft) + .5, Y(b.scrollTop) + .5,
                 Math.max(4, b.clientWidth * kx), Math.max(4, b.clientHeight * ky));
  }
}

function flowShow(i, focus) {
  const all = (eventsData?.groups || []).flatMap((g) => g.chains);
  const c = all[i]; if (!c) return;
  eventsSel = i;
  $("flow-list").querySelectorAll(".ev-card").forEach((el) =>
    el.classList.toggle("active", +el.dataset.i === i));

  const S = eventsData.strings || [];
  const rows = c.rows.map((r) => {
    const pad = `style="margin-left:${r.depth * 18}px"`;
    let out = "";
    if (r.in_edge && r.in_edge.label) {
      const eff = (r.in_edge.effects || []).map((x) => edgeEffectText(x)).join(" · ");
      out += `<div class="ev-edge" ${pad}><span class="edge-lbl">└▸ ${fesc(r.in_edge.label)}:</span>
        <span class="muted small">${fesc(eff || "plain hand-over")}</span>` +
        (r.in_edge.via || []).map((v) => `<span class="dbg"> grp${v.grp}[${v.cid}]</span>`).join("") + `</div>`;
    }
    if (r.grp === null) return out;                       // an arrow that ends nowhere
    const where = r.cell ? `(${r.cell.join(",")})` : "—";
    const owner = r.cell_owner === "same" ? " same square" : "";
    out += `<div class="chain-step ev-beat${r.rejoin ? " rejoin" : ""}" data-rec="${r.grp}:${r.cid}" ${pad}>
      <span class="chain-op">${BEAT_NAME[r.op] || r.op}</span>
      <span class="beat-cell">${where}</span><span class="muted small">${owner}</span>
      <span class="dbg"> grp${r.grp}[${r.cid}]</span>
      ${r.rejoin ? `<span class="ev-rejoin">↑ also reached earlier — shown once</span>` : ""}
      <div class="muted small chain-sum">${fesc((r.label || "").slice(0, 90))}</div></div>`;
    return out;
  }).join("");

  // The map belongs at the TOP, beside the heading, not under the outline. Where a chain happens is
  // the first thing you want and the last thing that changed — putting it below meant scrolling
  // past the steps to find out where they are, then scrolling back. It is one fixed row now: map
  // on the left, what and where on the right, the steps underneath.
  $("flow-detail").innerHTML =
    `<div class="ev-top">
       <canvas id="fl-mini"></canvas>
       <div class="ev-top-txt">
         <div class="fl-head">(${c.root.join(",")}) — ${c.beats} beat${c.beats === 1 ? "" : "s"}` +
    (c.forks ? ` · ${c.forks} fork${c.forks === 1 ? "" : "s"}` : "") + `</div>` +
    (c.quests.length ? `<div class="ev-top-quest">${fesc(c.quests.map((q) => eventsData.quest_names[q] || q).join(" · "))}</div>` : "") +
       `<div id="fl-note" class="muted small">click a beat — its square lights up on the map</div>
       </div>
       <!-- The whole chain at a glance, and a way back into it. A 43-beat chain is 2900px tall;
            without an overview the only way to reach the far end is to scroll and hope. -->
       <canvas id="ev-tree" title="the whole chain — click to jump there"></canvas>
     </div>
     <div id="ev-body" class="${evShape === "flow" ? "ev-flow" : "ev-outline"}"></div>`;

  const [side, id] = $("area-select").value.split(":").map(Number);
  const body = $("ev-body");
  const g = evShape === "flow" ? graphRender(c, body) : (body.innerHTML = rows, null);
  evGraph = g;
  eventsMini(c, null);
  if (g) {
    treeMini(g);
    body.addEventListener("scroll", () => treeMini(g), { passive: true });
    // Click the overview to jump: land on a node and it is SELECTED (the inspector follows), land
    // between nodes and it just pans there. Both are "take me to that part of the chain".
    $("ev-tree").addEventListener("click", (ev) => {
      const cv = $("ev-tree"), r = cv.getBoundingClientRect();
      const gx = (ev.clientX - r.left) / treeScale.kx - treeScale.ox;
      const gy = (ev.clientY - r.top) / treeScale.ky - treeScale.oy;
      const hit = g.nodes.find((n) => gx >= n.x && gx <= n.x + GN.w
                                   && gy >= n.y && gy <= n.y + (n.h || GN.h));
      body.scrollTo({ left: Math.max(0, gx - body.clientWidth / 2),
                      top: Math.max(0, gy - body.clientHeight / 2), behavior: "smooth" });
      if (hit && hit.n.grp !== null) {
        const el = body.querySelector(`[data-rec="${hit.n.grp}:${hit.n.cid}"]`);
        if (el) pick(el);
      }
    });
  }

  // ONE selection handler for both shapes: a beat is a beat, and the inspector is the same fixed
  // column either way. The form no longer opens between the rows, so what a beat hands over to
  // stays on screen while you edit it.
  function pick(el) {
    if (el.classList.contains("rejoin")) return;         // already shown above; edit it there
    const [gg, cid] = el.dataset.rec.split(":").map(Number);
    const row = c.rows.find((r) => r.grp === gg && r.cid === cid);
    if (!row) return;
    body.querySelectorAll("[data-rec].sel").forEach((x) => x.classList.remove("sel"));
    el.classList.add("sel");
    eventsMini(c, row);
    chainEdit(side, id, { grp: gg, cid, op: row.op, fields: row.fields }, body, $("ev-insp"));
  }
  body.querySelectorAll("[data-rec]").forEach((el) => el.addEventListener("click", () => pick(el)));

  if (focus) {
    eventsMini(c, c.rows.find((r) => r.cell && r.cell[0] === focus[0] && r.cell[1] === focus[1]));
    const hit = c.rows.find((r) => r.cell && r.cell[0] === focus[0] && r.cell[1] === focus[1]);
    const el = hit && body.querySelector(`[data-rec="${hit.grp}:${hit.cid}"]`);
    if (el) { el.classList.add("sel"); el.scrollIntoView({ block: "center", inline: "center" }); }
  } else {
    $("ev-insp").innerHTML = `<div class="muted">click a beat to edit it</div>`;
  }
}

// The map beside the outline. It used to be three dots on black: the chain's squares marked, and
// nothing else — no coastline, no buildings, no way to tell (26,6) from (3,28). A position needs
// something to be a position IN. So the area is drawn underneath at real tiles, dimmed, and the
// chain is picked out on top of it. Same atlas the main map uses; no second source of truth.
function eventsMini(c, sel) {
  const cv = $("fl-mini"); if (!cv) return;
  const W = area?.width || 32, H = area?.height || 32;
  const s = Math.max(4, Math.floor(240 / Math.max(W, H)));
  cv.width = W * s; cv.height = H * s;
  const g = cv.getContext("2d");
  g.imageSmoothingEnabled = false;
  g.fillStyle = "#0e1116"; g.fillRect(0, 0, cv.width, cv.height);
  if (mapAtlas && area) {
    for (let row = 0; row < H; row++) for (let col = 0; col < W; col++) {
      const i = row * W + col;
      blit(g, mapAtlas, displayTile(area.nibble[i], area.tileids[i], area.cellid[i]), col * s, row * s, s, s);
    }
    // dim the whole map so the chain reads as the foreground rather than competing with the art
    g.fillStyle = "rgba(14,17,22,.62)"; g.fillRect(0, 0, cv.width, cv.height);
  }
  const box = (x, y, col, lw) => { g.strokeStyle = col; g.lineWidth = lw;
    g.strokeRect(x * s + .5, y * s + .5, s - 1, s - 1); };
  c.rows.forEach((r) => { if (r.cell) box(r.cell[0], r.cell[1], "#8b95a5", 1); });
  if (sel) {
    (sel.in_edge?.effects || []).forEach((e) => {
      if (e.kind === "cell" && e.x < W && e.y < H) box(e.x, e.y, "#7bd88f", 2);
    });
    if (sel.cell) box(sel.cell[0], sel.cell[1], "#e6c15a", 2);
    $("fl-note").textContent = (sel.cell ? `(${sel.cell.join(",")}) · ` : "") + (sel.label || "");
  }
}

// ── portrait TRACK editor ─────────────────────────────────────────────
// Base picture on top; below it one TRACK per anim slot (they run concurrently in
// the engine). A track = a sequence of FULL states, each shown `delay` walks, looping.
// A state equal to the base = a pause. You paint whole pictures; the EOR patches are
// computed as successive diffs at save time. The last state of a track is always the base — the
// loop returns there, and that "→ base" card carries its own duration (see peEnsureClose).
// The ONE picture editor (pixel grid + up to 4 EOR animation slots + PNG drag&drop). It is
// source-agnostic: `base` is the REST base of any $0586 anim-object endpoint — a portrait
// (/api/portrait/{side}/{strId}) or a title graphic (/api/gfx/{name}). The box comes from the
// /bitmap response (w/h), so the SAME editor drives the 44×84 portraits and the 144×128 globe.
// opts: { host (DOM node to mount into), animCapable (force off for glyph assets like the logo),
//         onSaved (callback after a successful save), side/strId (portrait refresh), title }.
async function openAnimEditor(base, label, opts = {}) {
  const b = await (await fetch(`${base}/bitmap`)).json();
  let slots = [0, 1, 2, 3].map(() => ({ enabled: false, track: [] }));
  const animCapable = b.animCapable !== false && opts.animCapable !== false;
  if (animCapable) {
    try {
      const ar = await fetch(`${base}/anim-edit`);
      if (ar.ok) {
        const d = await ar.json();
        d.slots.forEach((s, i) => {
          slots[i].enabled = s.enabled;
          slots[i].track = (s.track || []).map((f) => ({ delay: f.delay, grid: copyGrid(f.pixels) }));
        });
      }
    } catch {}
  }
  pe = { base, side: opts.side, strId: opts.strId, label, pixels: b.pixels, w: b.w, h: b.h,
         palette: b.palette, val: 1, slots, animCapable, onSaved: opts.onSaved || null,
         showChanges: $("pe-show-changes").checked, playing: null,
         active: { slot: -1, frame: 0 } };   // the frame being edited: rendered BIG + pixel grid
  const box = $("portrait-editor");
  if (opts.host && box.parentNode !== opts.host) opts.host.appendChild(box);   // move to the calling tab
  box.hidden = false;
  $("pe-editor-title").textContent = opts.title || "Picture editor";
  $("pe-id").textContent = label || `str ${opts.strId}`;
  // glyph assets have no animation format — collapse the editor to a plain pixel canvas
  for (const id of ["pe-anim-play", "pe-show-changes-label"]) { const el = $(id); if (el) el.hidden = !animCapable; }
  const hint = $("pe-anim-hint"); if (hint) hint.hidden = !animCapable;
  pePal(); peFrames();
}

// Portraits: the original entry point, now a thin wrapper over the shared editor.
async function openPortraitEditor(side, strId, label) {
  return openAnimEditor(`/api/portrait/${side}/${strId}`, label || `str ${strId}`, {
    side, strId, animCapable: true, host: $("pt-side"), title: "Portrait editor",
    onSaved: () => { const live = $("portrait-canvas"); if (live) playPortrait(live, side, strId); refreshThumb(strId); },
  });
}

// Every EOR loop MUST end on the base picture. The engine restarts the patch chain at the $FF
// terminator, so a track ending on a painted state applies its patches on top of THAT state and the
// picture drifts a little further every cycle. All 43 original animations end on the base — str 01's
// slot 0 holds it for 25 walks — so the closing state is a real frame with a duration, not a detail
// of the encoder. It used to be appended at save time, which is why a base frame with delay 8 kept
// reappearing in every track after a save. Now it is in the model: shown, tunable, and removable
// only with the whole track.
function peEnsureClose() {
  if (!pe || !pe.animCapable) return;
  for (const s of pe.slots) {
    if (!s.enabled || !s.track.length) continue;
    for (const f of s.track) f.close = false;
    const last = s.track[s.track.length - 1];
    if (last.grid.every((row, y) => row.every((v, x) => v === pe.pixels[y][x]))) last.close = true;
    else s.track.push({ delay: 8, grid: copyGrid(pe.pixels), close: true });
  }
}

function peFrames() {
  peEnsureClose();
  // active frame may have been deleted / its track disabled — fall back to the base
  if (pe.active.slot >= 0 &&
      (!pe.slots[pe.active.slot].enabled || pe.active.frame >= pe.slots[pe.active.slot].track.length))
    pe.active = { slot: -1, frame: 0 };
  const wrap = $("pe-frames"); wrap.innerHTML = "";
  const mkCanvas = (parent, slot, frame) => {
    const c = document.createElement("canvas");
    c.className = "pe-fc"; c.dataset.slot = slot; c.dataset.frame = frame;
    parent.appendChild(c); return c;
  };
  // base row
  const baseRow = document.createElement("div"); baseRow.className = "pe-track";
  baseRow.innerHTML = `<div class="pe-track-label"><b>base</b><span class="muted">the still picture</span></div>`;
  const bCard = document.createElement("div"); bCard.className = "pe-frame";
  bCard.innerHTML = `<div class="pe-frame-head"><span class="muted">frame 0</span></div>`;
  mkCanvas(bCard, -1, 0); baseRow.appendChild(bCard); wrap.appendChild(baseRow);
  if (!pe.animCapable) { peDrawAll(); return; }        // glyph asset: base pixel grid only, no anim slots
  // one row per enabled slot
  pe.slots.forEach((s, i) => {
    if (!s.enabled) return;
    const row = document.createElement("div"); row.className = "pe-track";
    row.innerHTML = `<div class="pe-track-label"><b>animation ${i + 1}</b>
      <button class="mini" data-deltrack="${i}" title="remove this animation">✕ track</button></div>`;
    const strip = document.createElement("div"); strip.className = "pe-strip";
    s.track.forEach((f, j) => {
      const card = document.createElement("div"); card.className = "pe-frame" + (f.close ? " closing" : "");
      const head = f.close
        ? `<span class="muted" title="the loop has to end on the still picture — the engine repeats the patches from here, and a track that ends anywhere else smears the portrait a bit more every cycle. Set how long the base is held; ✕ track removes the animation.">→ base</span>`
        : `<span class="muted">frame ${j + 1}</span>`;
      const del = f.close ? "" : `<button class="mini" data-delframe="${i}:${j}" title="remove this frame">✕</button>`;
      card.innerHTML = `<div class="pe-frame-head">${head}
        delay <input class="pe-delay" type="number" min="1" max="254" data-slot="${i}" data-fdelay="${j}" value="${f.delay}">
        ${del}</div>`;
      mkCanvas(card, i, j); strip.appendChild(card);
    });
    const add = document.createElement("button");
    add.className = "pe-frame-add"; add.textContent = "＋ frame";
    add.title = "append a state (starts as a copy of the previous one; leave = base for a pause)";
    add.addEventListener("click", () => {
      const at = s.track.length - (s.track.length && s.track[s.track.length - 1].close ? 1 : 0);
      const prev = at ? s.track[at - 1].grid : pe.pixels;
      s.track.splice(at, 0, { delay: 8, grid: copyGrid(prev) });   // before the closing base state
      stopPreview(); peFrames();
    });
    strip.appendChild(add); row.appendChild(strip); wrap.appendChild(row);
  });
  // add-animation button (next free slot)
  if (pe.slots.some((s) => !s.enabled)) {
    const add = document.createElement("button");
    add.className = "mini pe-track-add"; add.textContent = "＋ animation";
    add.title = "new concurrent animation (max 4 — blink / mouth / glint …)";
    add.addEventListener("click", () => {
      const i = pe.slots.findIndex((s) => !s.enabled);
      pe.slots[i] = { enabled: true, track: [{ delay: 8, grid: copyGrid(pe.pixels) }] };
      stopPreview(); peFrames();
    });
    wrap.appendChild(add);
  }
  wrap.querySelectorAll("[data-fdelay]").forEach((el) => el.addEventListener("change", () => {
    pe.slots[+el.dataset.slot].track[+el.dataset.fdelay].delay = Math.min(254, Math.max(1, +el.value));
  }));
  wrap.querySelectorAll("[data-delframe]").forEach((el) => el.addEventListener("click", () => {
    const [i, j] = el.dataset.delframe.split(":").map(Number);
    pe.slots[i].track.splice(j, 1);
    // nothing but the closing base state left = the animation is gone
    if (!pe.slots[i].track.length || pe.slots[i].track.every((f) => f.close))
      pe.slots[i] = { enabled: false, track: [] };
    stopPreview(); peFrames();
  }));
  wrap.querySelectorAll("[data-deltrack]").forEach((el) => el.addEventListener("click", () => {
    pe.slots[+el.dataset.deltrack] = { enabled: false, track: [] };
    stopPreview(); peFrames();
  }));
  peDrawAll();
}

function peFrameGrid(slot, frame) { return slot < 0 ? pe.pixels : pe.slots[slot].track[frame].grid; }
function peIsActive(slot, frame) { return pe.active && pe.active.slot === slot && pe.active.frame === frame; }

function peDrawFrame(c) {
  const slot = +c.dataset.slot, frame = +c.dataset.frame, g = peFrameGrid(slot, frame);
  const act = peIsActive(slot, frame);
  const cx = c.getContext("2d"), uW = act ? 16 : 8, uH = act ? 8 : 4;   // active = 2x internal, 2:1 MC
  c.width = pe.w * uW; c.height = pe.h * uH; cx.imageSmoothingEnabled = false;
  c.closest(".pe-frame").classList.toggle("active", act);
  for (let y = 0; y < pe.h; y++) for (let x = 0; x < pe.w; x++) {
    cx.fillStyle = pe.palette[g[y][x]]; cx.fillRect(x * uW, y * uH, uW, uH);
  }
  if (act) {
    // pixel grid so you can see what you paint; heavier lines on char borders (4x8 MC px)
    cx.strokeStyle = "rgba(255,255,255,.13)"; cx.lineWidth = 1;
    for (let x = 1; x < pe.w; x++) { cx.beginPath(); cx.moveTo(x * uW + .5, 0); cx.lineTo(x * uW + .5, c.height); cx.stroke(); }
    for (let y = 1; y < pe.h; y++) { cx.beginPath(); cx.moveTo(0, y * uH + .5); cx.lineTo(c.width, y * uH + .5); cx.stroke(); }
    cx.strokeStyle = "rgba(255,255,255,.35)";
    for (let x = 4; x < pe.w; x += 4) { cx.beginPath(); cx.moveTo(x * uW + .5, 0); cx.lineTo(x * uW + .5, c.height); cx.stroke(); }
    for (let y = 8; y < pe.h; y += 8) { cx.beginPath(); cx.moveTo(0, y * uH + .5); cx.lineTo(c.width, y * uH + .5); cx.stroke(); }
  }
  if (!pe.showChanges) return;
  // animated pixels: a track frame highlights its diff vs the BASE ("what this state
  // changes"); the base card shows the union over every track state
  cx.strokeStyle = "#e6a24e"; cx.lineWidth = act ? 2 : 1;
  for (let y = 0; y < pe.h; y++) for (let x = 0; x < pe.w; x++) {
    const hot = slot >= 0
      ? g[y][x] !== pe.pixels[y][x]
      : pe.slots.some((s) => s.enabled && s.track.some((f) => f.grid[y][x] !== pe.pixels[y][x]));
    if (hot) cx.strokeRect(x * uW + .5, y * uH + .5, uW - 1, uH - 1);
  }
}
function peDrawAll() { $("pe-frames").querySelectorAll("canvas.pe-fc").forEach(peDrawFrame); }

function pePal() {
  const names = ["black %00", "red %01", "blue %10", "white %11"];
  $("pe-pal").innerHTML = pe.palette.map((h, v) => `<label class="pe-sw ${pe.val === v ? "sel" : ""}" style="background:${h}" title="${names[v]}" data-v="${v}"></label>`).join("");
  $("pe-pal").querySelectorAll(".pe-sw").forEach((el) => el.addEventListener("click", () => { pe.val = +el.dataset.v; pePal(); }));
}

function pePaint(e) {
  const c = e.target.closest("canvas.pe-fc"); if (!c || !pe) return;
  const slot = +c.dataset.slot, frame = +c.dataset.frame;
  if (!peIsActive(slot, frame)) {          // first click SELECTS (zoom big + grid) — no paint
    if (e.type === "mousedown") { pe.active = { slot, frame }; peDrawAll(); }
    return;                                 // drags crossing other canvases don't re-select
  }
  const r = c.getBoundingClientRect();
  const x = ((e.clientX - r.left) / (r.width / pe.w)) | 0, y = ((e.clientY - r.top) / (r.height / pe.h)) | 0;
  if (x < 0 || y < 0 || x >= pe.w || y >= pe.h) return;
  if (slot < 0) {
    const old = pe.pixels[y][x];
    pe.pixels[y][x] = pe.val;
    // non-animated pixels follow the base, so base edits don't leak into the EOR masks
    for (const s of pe.slots) for (const f of s.track) if (f.grid[y][x] === old) f.grid[y][x] = pe.val;
    peDrawAll();
  } else {
    pe.slots[slot].track[frame].grid[y][x] = pe.val;
    peDrawFrame(c);
    if (pe.showChanges) peDrawAll();   // union highlight on the base card changes too
  }
}

function stopPreview() { if (pe && pe.playing) { clearInterval(pe.playing); pe.playing = null; $("pe-anim-play").textContent = "▶ preview"; } }

function peAnimPlay() {
  if (pe.playing) { stopPreview(); return; }
  peEnsureClose();                       // preview what will be written, closing state included
  const tracks = pe.slots.filter((s) => s.enabled && s.track.length);
  if (!tracks.length) { status("no animation tracks"); return; }
  $("pe-anim-play").textContent = "■ stop";
  const c = $("pe-preview"), cx = c.getContext("2d"), uW = 8, uH = 4;
  c.width = pe.w * uW; c.height = pe.h * uH;
  // per-track playhead, engine-faithful: state j shows for its delay, then advance (looping;
  // the loop implicitly passes through the base = the auto-close)
  const heads = tracks.map((s) => ({ s, j: 0, left: s.track[0].delay, closing: false, base: 0 }));
  pe.playing = setInterval(() => {
    for (const h of heads) {
      if (--h.left > 0) continue;
      if (!h.closing) {
        h.j = (h.j + 1) % h.s.track.length;
        if (h.j === 0) {   // wrapped: pass through base (auto-close) before frame 0
          const lastIsBase = h.s.track[h.s.track.length - 1].grid.every((row, y) => row.every((v, x) => v === pe.pixels[y][x]));
          if (!lastIsBase) { h.closing = true; h.left = 8; continue; }
        }
      } else { h.closing = false; }
      h.left = h.s.track[h.j].delay;
    }
    const g = copyGrid(pe.pixels);
    for (const h of heads) {
      if (h.closing) continue;                       // in the base phase
      const st = h.s.track[h.j].grid;
      for (let y = 0; y < pe.h; y++) for (let x = 0; x < pe.w; x++)
        if (st[y][x] !== pe.pixels[y][x]) g[y][x] = g[y][x] ^ pe.pixels[y][x] ^ st[y][x];   // EOR-composite
    }
    cx.imageSmoothingEnabled = false;
    for (let y = 0; y < pe.h; y++) for (let x = 0; x < pe.w; x++) { cx.fillStyle = pe.palette[g[y][x]]; cx.fillRect(x * uW, y * uH, uW, uH); }
  }, MS_PER_DELAY_UNIT);   // one tick == one delay unit; was a flat 90 ms, ~35% too fast
}

// one save = base + all tracks (rebuilds the whole blob → work/ for portraits, editable source for gfx)
async function peSaveAll() {
  stopPreview();
  peEnsureClose(); peFrames();           // save exactly what the strip shows — nothing added behind it
  const url = pe.animCapable ? `${pe.base}/anim-edit` : `${pe.base}/bitmap`;
  let body;
  if (pe.animCapable) {
    const slots = pe.slots.map((s) => ({
      enabled: s.enabled && s.track.length > 0,
      track: s.track.map((f) => ({ delay: f.delay, pixels: f.grid })),
    }));
    body = { pixels: pe.pixels, slots };
  } else {
    body = { pixels: pe.pixels };                    // glyph asset: base grid only
  }
  const r = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (r.status === 503) { status("codec not ready yet"); return; }
  if (!r.ok) { status(`save err ${r.status}`); return; }
  const j = await r.json();
  const closed = (j.auto_closed || []).map((i) => i + 1);
  status(`saved ${pe.label || ("str " + pe.strId)}${j.slots != null ? ` (${j.slots} tracks)` : ""} → ${j.saved}`
         + (closed.length ? ` · a return to the base was added to animation ${closed.join(", ")}` : ""));
  if (pe.onSaved) pe.onSaved();                      // portrait viewer/thumb refresh, gfx preview refresh, …
}

// force the pt-list thumbnail(s) for a str id to refetch (server renders live from the blob)
function refreshThumb(strId) {
  document.querySelectorAll(`#pt-list img[src*="/api/portrait/"]`).forEach((img) => {
    const m = img.getAttribute("src").match(/\/api\/portrait\/(\d+)\/(\d+)/);
    if (m && +m[2] === strId) img.src = `/api/portrait/${m[1]}/${strId}?t=${Date.now()}`;
  });
}

// ═══════════════════════════════════════════════════════════════════
//  TELEPORT (per-area panel) + OVERVIEW (tree)
// ═══════════════════════════════════════════════════════════════════
let teleportData = null, ovBuilt = false;

async function ensureTeleport(force) {
  if (teleportData && !force) return teleportData;
  teleportData = await (await fetch("/api/teleport")).json();
  return teleportData;
}

const s8 = (v) => (v > 127 ? v - 256 : v);   // byte → signed (party-relative deltas)

// candidate arrivals for an area, from its incoming transitions (self-transitions excluded).
// Non-relative → the absolute arrive cell. Party-relative → the arrive bytes are a DELTA, so the
// absolute cell ≈ trigger cell + signed(dx,dy) (flagged rel, approximate — verify on the map;
// validated on Leroy's: Needles(60,28)+(-30,-16)=(30,12) ≈ the hand-set (30,13)). Deduped by coord
// (a real arrival beats a computed one), overworld first, relative ones last.
function tpCandidates(key) {
  const raw = ((teleportData.incoming || {})[key] || []).filter((e) => e.from !== +key);
  const out = [];
  for (const e of raw) {
    const cond = e.source === "subtable";   // grp10 entry not on an active cell (conditional/vestigial)
    const man = e.source === "manual";      // hand-authored grp16/keyword edge (Sleeper ladder, Mindlink)
    if (e.party_relative) {
      if (!e.cell) continue;                // subtable party-rel: no source cell → can't compute abs
      const dx = s8(e.arrive[0]), dy = s8(e.arrive[1]);
      const ax = e.cell[0] + dx, ay = e.cell[1] + dy;
      if ((dx === 0 && dy === 0) || ax < 0 || ay < 0) continue;   // degenerate / off-map
      out.push({ arrive: [ax, ay], from: e.from, message: e.message, rel: true, cond, man, cell: e.cell, delta: [dx, dy] });
    } else {
      if (e.arrive[0] === 0 && e.arrive[1] === 0) continue;
      out.push({ arrive: e.arrive, from: e.from, message: e.message, rel: false, cond, man });
    }
  }
  // rank: plain door → manual (hand-authored) → conditional/subtable → party-relative computed; overworld first within
  const tier = (c) => c.rel ? 3 : c.man ? 1 : c.cond ? 2 : 0;
  out.sort((a, b) => tier(a) - tier(b) || (b.from === 0) - (a.from === 0) || a.from - b.from);
  const seen = new Set(), uniq = [];
  for (const c of out) { const k = c.arrive.join(","); if (seen.has(k)) continue; seen.add(k); uniq.push(c); }
  return uniq;
}
function tpStatus(key) {
  const cfg = (teleportData.config || {})[key] || {};
  const cand = tpCandidates(key);
  const rawInc = ((teleportData.incoming || {})[key] || []).filter((e) => e.from !== +key);
  const distinct = new Set(cand.map((c) => c.arrive.join(","))).size;
  const coord = cfg.coord || (cand[0] ? cand[0].arrive : null);
  const name = cfg.name || (teleportData.areas[key] ? teleportData.areas[key].name : "") || "";
  const allow = cfg.allow_teleport == null ? cand.length > 0 : !!cfg.allow_teleport;
  return { cfg, cand, distinct, coord, name, allow, review: !!cfg.review,
    hasInc: cand.length > 0, hasRawInc: rawInc.length > 0,
    relOnly: cand.length > 0 && cand.every((c) => c.rel),
    plainDoor: cand.some((c) => !c.rel && !c.cond && !c.man),
    hasManual: cand.some((c) => c.man) };
}

async function renderTeleportPanel(aid) {
  await ensureTeleport();
  const box = $("tp-panel"); if (!box) return;
  const key = String(aid);
  if (!teleportData.areas[key]) await ensureTeleport(true);   // self-heal: cache may pre-date a new/edited area — refetch once
  if (!teleportData.areas[key]) { box.innerHTML = `<span class="muted">area ${aid} — no record</span>`; return; }
  const st = tpStatus(key);
  const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
  const flags = [];
  if (st.distinct > 1) flags.push(`<span class="ov-flag warn" title="multiple incoming arrivals">⚠ ${st.distinct} entries</span>`);
  if (st.relOnly) flags.push(`<span class="ov-flag rel" title="all incoming are party-relative — coords are computed from the delta, verify on the map">⟳ party-relative</span>`);
  else if (st.hasManual && !st.plainDoor) flags.push(`<span class="ov-flag man" title="reached by a hand-authored grp16/keyword edge (no plain door) — see area_names.json _manual_edges">⚙ manual</span>`);
  else if (st.cand.length && !st.plainDoor) flags.push(`<span class="ov-flag cond" title="reached only by a conditional / subtable transition (no plain door) — verify on the map">⌁ conditional</span>`);
  else if (!st.hasRawInc) flags.push(`<span class="ov-flag none" title="no incoming transition at all">○ no incoming</span>`);
  if (st.review) flags.push(`<span class="ov-flag rev" title="not yet confirmed">● review</span>`);
  const chips = st.cand.map((c) => {
    const src = c.from === 0 ? "Overworld" : (teleportData.areas[String(c.from)] ? teleportData.areas[String(c.from)].name : `area ${c.from}`);
    const sel = st.coord && c.arrive[0] === st.coord[0] && c.arrive[1] === st.coord[1] ? " sel" : "";
    const title = c.rel
      ? `party-relative from ${src} cell (${c.cell[0]},${c.cell[1]}) delta (${c.delta[0]},${c.delta[1]}) → computed (${c.arrive[0]},${c.arrive[1]}), verify on the map`
      : c.man
        ? `hand-authored grp16/keyword edge from ${src} → (${c.arrive[0]},${c.arrive[1]})${c.message ? " — " + c.message : ""}`
        : c.cond
          ? `conditional / subtable transition from ${src} → (${c.arrive[0]},${c.arrive[1]}) — not a plain door, verify on the map`
          : (c.message || "from " + src);
    const mark = c.rel ? ' <span class="tp-relmark">⟳</span>' : c.man ? ' <span class="tp-manmark">⚙</span>' : c.cond ? ' <span class="tp-condmark">⌁</span>' : "";
    return `<button class="tp-chip${sel}${c.rel ? " rel" : c.man ? " man" : c.cond ? " cond" : ""}" data-x="${c.arrive[0]}" data-y="${c.arrive[1]}" title="${esc(title)}">${esc(src)} (${c.arrive[0]},${c.arrive[1]})${mark}</button>`;
  }).join("") || `<span class="muted small">${st.hasRawInc ? "incoming is self/degenerate — set the coord by hand" : "no incoming transition — set the coord by hand"}</span>`;
  box.classList.remove("muted");
  box.innerHTML = `
    <label class="tp-allow"><input type="checkbox" id="tp-allow" ${st.allow ? "checked" : ""}> Allow teleport</label>
    <div class="tp-flags">${flags.join(" ")}</div>
    <label class="tp-row">Name <input id="tp-name" maxlength="14" value="${esc(st.name)}"></label>
    <label class="tp-row">Coordinates
      <input id="tp-x" class="tp-coord" type="number" min="0" max="255" value="${st.coord ? st.coord[0] : ""}">,
      <input id="tp-y" class="tp-coord" type="number" min="0" max="255" value="${st.coord ? st.coord[1] : ""}"></label>
    <div class="tp-cands">${chips}</div>
    <div class="btns"><button id="tp-save">Save teleport</button></div>
    <p class="muted small">Prefilled from the incoming transition. Multi-entry → click the canonical
      arrival chip. Saves to <code>area_names.json</code>.</p>`;
  box.querySelectorAll(".tp-chip").forEach((el) => el.addEventListener("click", () => {
    $("tp-x").value = el.dataset.x; $("tp-y").value = el.dataset.y;
    box.querySelectorAll(".tp-chip").forEach((c) => c.classList.remove("sel")); el.classList.add("sel");
  }));
  $("tp-save").addEventListener("click", () => saveTeleport(aid));
}

async function saveTeleport(aid) {
  const allow = $("tp-allow").checked;
  const name = $("tp-name").value.trim();
  const x = parseInt($("tp-x").value, 10), y = parseInt($("tp-y").value, 10);
  const coord = (!isNaN(x) && !isNaN(y)) ? [x, y] : null;
  const r = await fetch(`/api/teleport/config/${aid}`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ allow_teleport: allow, name, coord }),
  });
  if (!r.ok) { status(`teleport save err ${r.status}`); return; }
  const j = await r.json();
  teleportData.config[String(aid)] = { name: j.config.name, coord: j.config.coord || null,
    allow_teleport: j.config.allow_teleport, review: !!j.config.review };
  status(`teleport saved: ${j.config.name} ${coord ? "(" + coord + ")" : ""}`);
  renderTeleportPanel(aid);
  if (ovBuilt) renderOverview();
}

// ── Overview: overworld-rooted teleport tree, each node annotated with its teleport status ──
async function overviewInit() { ovBuilt = true; await ensureTeleport(true); renderOverview(); }

function renderOverview() {
  const box = $("ov-tree"); if (!box) return;
  const { areas, outgoing } = teleportData;
  const expanded = new Set();
  const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
  const rowHtml = (key, depth, isRef, viaMsg) => {
    const a = areas[key], st = tpStatus(key);
    const nm = a ? a.name : `area ${key}`;
    if (isRef) return `<div class="ov-row ref" style="--d:${depth}"><span class="ov-name" data-aid="${key}">↳ ${esc(nm)} <span class="muted">(id ${key}, above)</span></span></div>`;
    const tflag = `<span class="ov-flag ${st.allow ? "on" : "off"}" title="teleport ${st.allow ? "on" : "off"}">T</span>`;
    const co = st.coord ? `<span class="ov-co">(${st.coord[0]},${st.coord[1]})</span>` : `<span class="ov-co muted">—</span>`;
    const marks = [];
    if (st.distinct > 1) marks.push(`<span class="ov-flag warn" title="${st.distinct} incoming arrivals">⚠</span>`);
    if (st.relOnly) marks.push(`<span class="ov-flag rel" title="party-relative only — computed coord, verify">⟳</span>`);
    else if (st.hasManual && !st.plainDoor) marks.push(`<span class="ov-flag man" title="hand-authored grp16/keyword edge (no plain door)">⚙</span>`);
    else if (st.cand.length && !st.plainDoor) marks.push(`<span class="ov-flag cond" title="conditional / subtable transition only (no plain door) — verify">⌁</span>`);
    else if (!st.hasRawInc && key !== "0") marks.push(`<span class="ov-flag none" title="no incoming transition">○</span>`);
    if (st.review) marks.push(`<span class="ov-flag rev" title="review">●</span>`);
    const via = viaMsg ? `<span class="ov-via muted" title="${esc(viaMsg)}">${esc(viaMsg)}</span>` : "";
    return `<div class="ov-row" style="--d:${depth}">${tflag}<span class="ov-name" data-aid="${key}" data-side="${a ? a.side : ""}">${esc(nm)} <span class="muted">(id ${key})</span></span>${co}${marks.join("")}${via}</div>`;
  };
  const out = [];
  const kidsOf = (key) => {
    const seen = new Map();                 // target → first message (dedupe multi-cell edges)
    for (const e of (outgoing[key] || [])) {
      if (e.is_return || e.to >= 0x80 || e.to === 0 || e.to === +key) continue;
      if (!seen.has(e.to)) seen.set(e.to, e.message || "");
    }
    return [...seen.entries()];
  };
  const walk = (key, depth, viaMsg) => {
    if (expanded.has(key)) { out.push(rowHtml(key, depth, true)); return; }
    expanded.add(key);
    out.push(rowHtml(key, depth, false, viaMsg));
    for (const [to, msg] of kidsOf(key)) walk(String(to), depth + 1, msg);
  };
  walk("0", 0);
  // orphans: areas with a record but never reached from the overworld
  const orphans = Object.keys(areas).filter((k) => !expanded.has(k));
  if (orphans.length) {
    out.push(`<div class="ov-sec">Not reachable from overworld (${orphans.length})</div>`);
    for (const k of orphans.sort((a, b) => +a - +b)) out.push(rowHtml(k, 0, false));
  }
  box.classList.remove("muted");
  box.innerHTML = out.join("");
  box.querySelectorAll(".ov-name[data-side]").forEach((el) => el.addEventListener("click", () => {
    const aid = +el.dataset.aid, side = +el.dataset.side;
    if (!isNaN(side)) gotoArea(side, aid);
  }));
  const named = Object.keys(areas).length;
  const on = Object.keys(areas).filter((k) => tpStatus(k).allow).length;
  $("ov-info").textContent = `${named} areas · ${on} teleport-enabled`;
}

// ═══════════════ dialogs ═══════════════
async function newAreaDialog() {
  const s = prompt("New area — side:area (e.g. 1:50)"); if (!s) return;
  const [side, area_id] = s.split(":").map(Number);
  const mode = confirm("OK = copy from another area · Cancel = empty") ? "copy" : "empty";
  const from = prompt("Template — side:area (e.g. 4:7 Sleeper Base)", "1:10"); if (!from) return;
  const [fs, fa] = from.split(":").map(Number);
  const r = await fetch("/api/area/new", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ side, area_id, mode, from_side: fs, from_area: fa }) });
  if (!r.ok) { alert(`create failed: ${r.status} ${await r.text()}`); return; }
  await reloadAreas(`${side}:${area_id}`); status(`created area ${side}:${area_id}`);
}
async function newPortraitDialog() {
  const dst = prompt("New portrait — side:str-id (e.g. 1:90)"); if (!dst) return;
  const [side, str_id] = dst.split(":").map(Number);
  const copy = confirm("OK = copy from another portrait · Cancel = empty");
  const body = { side, str_id, mode: copy ? "copy" : "empty" };
  if (copy) { const src = prompt("Copy from — side:str-id (e.g. 2:78)", "1:56"); if (!src) return; const [fs, fst] = src.split(":").map(Number); body.from_side = fs; body.from_str = fst; }
  const r = await fetch("/api/portrait/new", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) { alert(`create failed: ${r.status} ${await r.text()}`); return; }
  ptLoaded = false; ptInit(); ptOpen(side, str_id, `str ${str_id}`); status(`created portrait ${dst}`);
}
async function reloadAreas(sel) {
  const { areas } = await (await fetch("/api/areas")).json();
  const s = $("area-select");
  s.innerHTML = areas.map((a) => { const nm = a.name ? `${a.name} — ` : ""; const tag = a.origin === "work" ? " ✎" : (a.has_map ? "" : " (no map)"); return `<option value="${a.side}:${a.area_id}">${nm}side ${a.side} area ${a.area_id}${tag}</option>`; }).join("");
  if (sel) { s.value = sel; const [side, id] = sel.split(":").map(Number); loadArea(side, id); }
}

// ═══════════════ init ═══════════════
// header git badge — WHICH backend + git state, so NAS and local dev are never confused.
async function loadGitBadge(sync) {
  const el = $("git-badge");
  const loc = location.host;                          // e.g. a LAN address vs 127.0.0.1
  el.textContent = sync ? "syncing…" : "…";
  let s = {};
  try { s = await (await fetch(sync ? "/api/git-sync" : "/api/status", { method: sync ? "POST" : "GET" })).json(); } catch { el.textContent = loc; return; }
  const where = /^127\.|^localhost/.test(loc) ? "local dev" : (s.host && s.host !== "localhost" ? s.host : loc);
  const mode = s.autocommit ? "auto-sync" : "manual";
  const bits = [];
  if (s.branch) bits.push(s.branch);
  bits.push(mode);
  if (s.clean === false) bits.push("unsaved");
  if (s.ahead) bits.push(`↑${s.ahead}`);
  if (s.behind) bits.push(`↓${s.behind}`);
  const good = s.clean !== false && !s.behind && (s.autocommit || s.ahead === 0);
  el.className = "git-badge " + (good ? "ok" : "warn");
  el.innerHTML = `<b>${where}</b> · ${bits.join(" · ")}`;
  el.title = `backend ${loc} · ${s.remote || "no remote"}\nbranch ${s.branch} · ${mode}` +
    (s.autocommit ? " (every save commits+pushes)" : " (commit by hand)") +
    `\n${s.ahead || 0} ahead / ${s.behind || 0} behind origin · ${s.clean === false ? "uncommitted changes" : "clean"}\nclick to fetch + fast-forward`;
  if (sync && s.pulled) status("git: fast-forwarded to origin");
}

// Which machine this data root came from. Only the C64 root can be written: the Apple write
// path has never been proven at its $D000 record window, so the backend answers 403 to every
// mutating call. Say that at the top of the screen rather than letting the user find out one
// refused save at a time. See docs/APPLE2.md.
async function loadMachineBadge() {
  let h;
  try { h = await (await fetch("/api/health")).json(); } catch { return; }
  if (!h || h.machine === "c64" || !h.machine) return;
  const el = $("machine-badge");
  el.className = "machine-badge";
  el.textContent = h.read_only ? `${h.machine === "apple" ? "Apple II" : h.machine} — read only` : h.machine;
  el.title = "This data root holds the Apple II release. Reading it is proven against the C64 "
           + "byte for byte; writing it is not, so every save is refused. docs/APPLE2.md";
  el.hidden = false;
}

async function init() {
  $("tabs").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
  loadMachineBadge();
  loadGitBadge();
  $("git-badge").addEventListener("click", () => loadGitBadge(true));
  setInterval(() => loadGitBadge(), 60000);           // keep ahead/behind fresh
  // map tab
  const { areas } = await (await fetch("/api/areas")).json();
  const sel = $("area-select");
  sel.innerHTML = areas.map((a) => { const nm = a.name ? `${a.name} — ` : ""; const tag = a.origin === "work" ? " ✎" : (a.has_map ? "" : " (no map)"); return `<option value="${a.side}:${a.area_id}">${nm}side ${a.side} area ${a.area_id}${tag}</option>`; }).join("");
  sel.addEventListener("change", () => { if (dirty && !confirm("Unsaved edits lost. Switch?")) return; const [side, id] = sel.value.split(":").map(Number); loadArea(side, id); });
  $("new-area-btn").addEventListener("click", newAreaDialog);
  $("area-gfx").addEventListener("change", onGfxChange);
  $("show-transitions").addEventListener("change", drawMap);
  $("show-mutations").addEventListener("change", drawMap);
  $("show-hooks").addEventListener("change", drawMap);
  $("show-transitions").addEventListener("change", drawMap);
  $("txt-save").addEventListener("click", txtSave);
  $("txt-revert").addEventListener("click", () => { loadArea(curSide, curAid); });
  $("zoom-in").addEventListener("click", () => { zoom = Math.min(ZOOM_MAX, zoom + 2); $("zoom-val").textContent = zoom; drawMap(); });
  $("zoom-out").addEventListener("click", () => { zoom = Math.max(ZOOM_MIN, zoom - 2); $("zoom-val").textContent = zoom; drawMap(); });
  $("zoom-fit").addEventListener("click", () => fitZoom());
  // Same scoping bug, worse consequence: unscoped, EVERY button in the left panel, the right panel
  // and all five World strips also called setMode(undefined). Clicking "Text" changed the top-level
  // mode out from under the user.
  document.querySelectorAll("#mode-switch button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  // Flow vs Outline is a way of DRAWING the same chain, so it does not reload anything — it
  // re-renders the selected one. Kept as a switch while the flowchart proves itself.
  $("ev-shape")?.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    evShape = b.dataset.shape;
    $("ev-shape").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x.dataset.shape === evShape));
    if (eventsSel != null) flowShow(eventsSel);
  }));
  $("subset-switch").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => setSubset(b.dataset.subset)));
  $("bl-kinds")?.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    blKind = b.dataset.bk;
    $("bl-kinds").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
    blSel = null; blShow(); blRender();
  }));
  $("blk-kinds")?.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    blkKind = b.dataset.bk;
    $("blk-kinds").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
    blkRender();
  }));
  $("area-tabs").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => setAreaTab(b.dataset.ap)));
  $("txt-add").addEventListener("click", addAreaText);
  $("npc-add").addEventListener("click", () => npcPanel(null));
  $("foe-add")?.addEventListener("click", () => foePanel(curSide, curAid, null));
  $("save-btn").addEventListener("click", saveMap);
  $("revert-btn").addEventListener("click", revertMap);
  $("map-history-btn").addEventListener("click", mapHistoryToggle);
  $("bp-snap").addEventListener("click", bpSnap);
  $("bp-refresh").addEventListener("click", bpLoad);
  bpLoad();
  document.querySelectorAll(".sub-switch").forEach((s) => s.querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => setPane(s.dataset.group, b.dataset.pane))));
  $("q-refresh").addEventListener("click", () => qLoad(qSel));
  $("q-shape")?.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    qShape = b.dataset.qshape;
    $("q-shape").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x.dataset.qshape === qShape));
    $("q-insp").hidden = qShape !== "journey";
    if (qSel) qOpen(qSel);
  }));
  $("pa-select").addEventListener("change", paLoad);
  $("pa-undo").addEventListener("click", async () => {
    const r = await fetch(`/api/party/${encodeURIComponent(paName)}/undo`, { method: "POST" });
    if (r.ok) { status("party edit undone"); await paLoad(); } else status("nothing to undo");
  });
  $("pa-revert").addEventListener("click", async () => {
    if (!confirm(`Drop the working copy of ${paName}? The pristine blob comes back.`)) return;
    await fetch(`/api/party/${encodeURIComponent(paName)}/revert`, { method: "POST" });
    await paLoad();
  });
  $("bpw-refresh").addEventListener("click", bpwList);
  $("bpw-save").addEventListener("click", bpwSave);
  $("bpw-notes").addEventListener("input", () => bpwSetDirty(true));
  $("bpw-tools").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    bpwTool = b.dataset.bt;
    $("bpw-tools").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x.dataset.bt === bpwTool));
  }));
  let bpwPaint = false;
  $("bpw-canvas").addEventListener("mousedown", (e) => { bpwPaint = true; bpwEdit(e); });
  $("bpw-canvas").addEventListener("mousemove", (e) => {
    const { x, y, hit } = bpwDoc ? bpwAt(e) : {};
    if (bpwDoc) $("bpw-cellinfo").textContent = hit
      ? `(${x},${y}) tile ${hit.e.tile ?? "—"}${hit.e.op ? ` · op $${hit.e.op.toString(16).toUpperCase()} ${OP_NAMES[hit.e.op] || ""} #${hit.e.cellid}` : ""}`
      : `(${x},${y}) empty — paint here to grow the block`;
    if (bpwPaint) bpwEdit(e);
  });
  window.addEventListener("mouseup", () => { bpwPaint = false; });
  $("bpw-undo").addEventListener("click", () => {
    if (!bpwUndo.length) return;
    bpwDoc = JSON.parse(bpwUndo.pop());
    bpwDraw(); bpwPalette(); bpwSetDirty(bpwUndo.length > 0 || bpwDirty);
    status("blueprint edit undone");
  });
  $("bpw-revert").addEventListener("click", async () => {
    if (!bpwName || !bpwDirty) return;
    if (!confirm(`Throw away the unsaved changes to "${bpwName}" and reload it from disk?`)) return;
    bpwUndo = []; await bpwOpen(bpwName); status("reloaded from disk");
  });
  $("bpw-delete").addEventListener("click", async () => {
    // Say where it goes. The library lives in git on the NAS and every save is auto-committed, so a
    // deleted blueprint IS recoverable — but only by someone who knows that, which is why it is
    // written on the button that does it.
    if (!bpwName || !confirm(`Delete blueprint "${bpwName}"?\n\nThe library is under version control ` +
        `on the NAS, so it can be recovered from history — but not from this screen.`)) return;
    await fetch(`/api/blueprint/${encodeURIComponent(bpwName)}`, { method: "DELETE" });
    bpwName = null; bpwDoc = null; $("bpw-title").textContent = "—"; $("bpw-sub").textContent = "";
    $("bpw-contents").innerHTML = ""; $("bpw-palette").innerHTML = ""; $("bpw-notes").value = "";
    const cv = $("bpw-canvas"); cv.getContext("2d").clearRect(0, 0, cv.width, cv.height);
    bpwList(); bpLoad();
  });
  $("bpw-place").addEventListener("click", () => {
    if (!bpwName) return;
    // Placing needs the map, so go there. The arm shows what would not bind before the click.
    bpArm(bpwName); setTab("map");
  });
  $("area-fill").addEventListener("change", saveFillTile);
  $("area-fill").addEventListener("input", drawFillPrev);
  $("rec-history-btn").addEventListener("click", recHistoryToggle);
  $("ts-diff-toggle").addEventListener("click", () => { if (tsDiff) { tsDiff.showOld = !tsDiff.showOld; tsDiffDraw(); } });
  $("ts-diff-close").addEventListener("click", () => { $("ts-diff-wrap").hidden = true; tsDiff = null; });
  $("map-diff-toggle").addEventListener("click", () => { if (mapDiff) { mapDiff.showOld = !mapDiff.showOld; mapDiffDraw(); } });
  $("map-diff-close").addEventListener("click", () => { $("map-diff-wrap").hidden = true; mapDiff = null; });
  $("paint-tools").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => setTool(b.dataset.tool)));
  $("phys-tools").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => setPhysTool(b.dataset.phys)));
  $("phys-save").addEventListener("click", savePhysics);
  $("phys-revert").addEventListener("click", revertMap);
  $("show-solid").addEventListener("change", drawMap);
  $("undo-btn").addEventListener("click", undoArea);
  window.addEventListener("keydown", (e) => {                 // ⌘/Ctrl+Z = undo last saved edit
    if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== "z" || e.shiftKey) return;
    if (!$("tab-map").classList.contains("active")) return;
    const t = (e.target.tagName || "");
    if (t === "INPUT" || t === "TEXTAREA" || t === "SELECT") return;
    e.preventDefault(); undoArea();
  });
  // tilesets tab
  $("te-save").addEventListener("click", teSave);
  $("te-clear").addEventListener("click", teClear);
  $("te-close").addEventListener("click", () => {
    const wasBlock = te?.block;
    te = null; teGoHome();
    if (wasBlock) blOpen(wasBlock); else teShowEmpty();
  });
  // tileset sandbox: drag = paint the selected tile, right-click = erase, clear all = reset grid
  $("ts-sandbox").addEventListener("mousedown", (e) => {
    e.preventDefault();
    if (e.button === 2) { sbPaint(e, true); return; }
    sbPainting = true; sbPaint(e, false);
  });
  $("ts-sandbox").addEventListener("mousemove", (e) => { if (sbPainting) sbPaint(e, false); });
  window.addEventListener("mouseup", () => { sbPainting = false; });
  $("ts-sandbox").addEventListener("contextmenu", (e) => { e.preventDefault(); sbPaint(e, true); });
  $("sb-clear").addEventListener("click", () => sbReset());
  $("te-copy").addEventListener("click", teCopy);
  $("te-paste").addEventListener("click", tePaste);
  $("te-flipx").addEventListener("click", teFlipX);
  $("te-flipy").addEventListener("click", teFlipY);
  $("ts-usage-area").addEventListener("change", applyTsUsage);
  $("new-tileset-btn").addEventListener("click", newTilesetDialog);
  $("ts-copy-btn").addEventListener("click", openCopyOverlay);
  $("ts-compact-btn").addEventListener("click", tsCompact);
  $("ts-clear-unused-btn").addEventListener("click", tsClearUnused);
  $("ts-undo-btn").addEventListener("click", tsUndo);
  $("ts-revert-btn").addEventListener("click", tsRevert);
  $("ts-history-btn").addEventListener("click", tsHistoryToggle);
  $("copy-cancel").addEventListener("click", () => { $("copy-overlay").hidden = true; });
  $("copy-do").addEventListener("click", copyDo);
  const tec = $("te-canvas"); let tp = false;
  tec.addEventListener("mousedown", (e) => { if (te) { tp = true; tePaint(e); } });
  tec.addEventListener("mousemove", (e) => { if (tp && te) tePaint(e); });
  // portraits tab
  $("ov-refresh").addEventListener("click", () => overviewInit());
  $("new-portrait-btn").addEventListener("click", newPortraitDialog);
  $("pt-hide-dups").addEventListener("change", () => { if (ptAll) ptRender(); });
  $("pe-save-all").addEventListener("click", peSaveAll);
  $("pe-anim-play").addEventListener("click", peAnimPlay);
  $("pe-show-changes").addEventListener("change", (e) => { if (pe) { pe.showChanges = e.target.checked; peDrawAll(); } });
// ── portrait PNG import ───────────────────────────────────────────────
// Drop a picture onto the open editor to load it into ONE state. It goes into the editor, never
// straight to disk — the editor is the preview and Save still writes it.
// WHERE it lands: the frame card you dropped it on, else the frame currently being edited. It used
// to always overwrite the base, which made uploading an animation frame by frame impossible — you
// picked frame 4, dropped, and the still picture changed instead.
// The backend does the validating; we only report what it says.
function peWhere(dst) {
  return dst.slot < 0 ? "the base picture" : `animation ${dst.slot + 1} frame ${dst.frame + 1}`;
}
// A picture that does not fit the box is NOT rejected and NOT trimmed on its own. The backend
// hands back the surplus per axis plus an ink profile (painted pixels per source row/column) and
// we let the user place the frame himself — Till paints on a square canvas, so his 88x88 is 44 MC
// pixels wide and four EMPTY rows too tall, and only he should decide what leaves the picture.
// Returns [crop_x, crop_y] or null when he cancels.
function peCropDialog(imgSrc, d, fileName) {
  return new Promise((resolve) => {
    const [w, h] = d.source, [cw, ch] = d.window, [sx, sy] = d.surplus;
    const rows = d.ink_rows || [], cols = d.ink_cols || [];
    const sum = (a, i0, i1) => { let n = 0; for (let i = i0; i < i1; i++) n += a[i] || 0; return n; };
    const lossY = (oy) => sum(rows, 0, oy) + sum(rows, oy + ch, h);
    const lossX = (ox) => sum(cols, 0, ox) + sum(cols, ox + cw, w);
    // start where the least paint is left out; ties go to the most centred offset
    const best = (n, f) => {
      let bo = 0, bl = Infinity;
      for (let o = 0; o <= n; o++) {
        const l = f(o);
        if (l < bl || (l === bl && Math.abs(o - n / 2) < Math.abs(bo - n / 2))) { bl = l; bo = o; }
      }
      return bo;
    };
    let ox = best(sx, lossX), oy = best(sy, lossY);
    const Z = Math.max(1, Math.min(8, Math.floor(Math.min(620 / w, 520 / h)) || 1));

    const back = document.createElement("div");
    back.className = "overlay pe-crop";
    back.innerHTML = `<div class="overlay-box" style="width:auto">
      <div class="overlay-head"><b>Place the ${d.w}×${d.h} frame</b>
        <span class="muted small">${fileName} — ${w}×${h}, read as ${d.block[0]}×${d.block[1]} per multicolour pixel</span>
        <span class="spacer"></span></div>
      <div class="pe-crop-stage" style="width:${w * Z}px;height:${h * Z}px">
        <img src="${imgSrc}" width="${w * Z}" height="${h * Z}">
        <div class="pe-crop-frame" tabindex="0" style="width:${cw * Z}px;height:${ch * Z}px"></div>
      </div>
      <div class="pe-crop-info small"></div>
      <div class="overlay-head"><span class="spacer"></span>
        <button class="mini" data-act="cancel">Cancel</button>
        <button data-act="ok">Import this frame</button></div></div>`;
    const frame = back.querySelector(".pe-crop-frame");
    const info = back.querySelector(".pe-crop-info");
    const place = () => {
      frame.style.left = ox * Z + "px"; frame.style.top = oy * Z + "px";
      const ly = lossY(oy), lx = lossX(ox), lost = ly + lx;
      const parts = [];
      if (sy) parts.push(`${oy} row${oy === 1 ? "" : "s"} above, ${sy - oy} below — ${ly} painted pixel${ly === 1 ? "" : "s"} left out`);
      if (sx) parts.push(`${ox} column${ox === 1 ? "" : "s"} left, ${sx - ox} right — ${lx} painted pixel${lx === 1 ? "" : "s"} left out`);
      parts.push(lost ? `⚠ ${lost} painted pixels will NOT be imported` : "✓ everything painted is inside the frame");
      info.className = "pe-crop-info small " + (lost ? "warn" : "ok");
      info.textContent = parts.join("   •   ");
    };
    place();
    // drag
    let grab = null;
    frame.addEventListener("mousedown", (e) => { grab = { x: e.clientX, y: e.clientY, ox, oy }; e.preventDefault(); frame.focus(); });
    const move = (e) => {
      if (!grab) return;
      ox = Math.max(0, Math.min(sx, grab.ox + Math.round((e.clientX - grab.x) / Z)));
      oy = Math.max(0, Math.min(sy, grab.oy + Math.round((e.clientY - grab.y) / Z)));
      place();
    };
    const up = () => { grab = null; };
    window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
    frame.addEventListener("keydown", (e) => {
      const k = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
      if (!k) return;
      e.preventDefault();
      ox = Math.max(0, Math.min(sx, ox + k[0])); oy = Math.max(0, Math.min(sy, oy + k[1])); place();
    });
    const close = (v) => {
      window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up);
      back.remove(); resolve(v);
    };
    back.addEventListener("click", (e) => {
      const a = e.target.dataset && e.target.dataset.act;
      if (a === "ok") close([ox, oy]);
      else if (a === "cancel" || e.target === back) close(null);
    });
    document.body.appendChild(back);
    frame.focus();
  });
}

async function importPortraitPNG(file, target) {
  if (!pe) return;
  const msg = $("pe-import-msg");
  const show = (text, cls) => { msg.hidden = false; msg.className = "small " + cls; msg.textContent = text; };
  if (!file.type.startsWith("image/")) return show(`${file.name} is not an image.`, "err");
  // drop target wins over the selection; fall back to the base if the frame is gone (deleted ✕)
  let dst = target || pe.active || { slot: -1, frame: 0 };
  if (dst.slot >= 0 && !(pe.slots[dst.slot] && pe.slots[dst.slot].track[dst.frame])) dst = { slot: -1, frame: 0 };
  const where = peWhere(dst);
  show(`reading ${file.name} → ${where}…`, "muted");
  const b64 = await new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(file);
  });
  const post = (crop) => fetch(`${pe.base}/import`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(crop ? { png_b64: b64, apply: false, crop_x: crop[0], crop_y: crop[1] }
                              : { png_b64: b64, apply: false }),
  });
  let r = await post(null);
  let d = await r.json().catch(() => ({}));
  if (!r.ok) return show(`Import refused — ${d.detail || r.status}`, "err");
  if (d.needs_crop) {
    show(`${file.name} is ${d.source[0]}×${d.source[1]} — place the frame…`, "muted");
    const crop = await peCropDialog(b64, d, file.name);
    if (!crop) return show(`${file.name} not imported.`, "muted");
    r = await post(crop);
    d = await r.json().catch(() => ({}));
    if (!r.ok) return show(`Import refused — ${d.detail || r.status}`, "err");
    d.warnings = (d.warnings || []).concat(
      crop[0] || crop[1] || d.surplus[0] || d.surplus[1]
        ? [`cropped to ${d.window[0]}×${d.window[1]} at ${crop[0]},${crop[1]}`] : []);
  }

  if (dst.slot < 0) {
    // RE-BASING the still picture. A track frame stores the whole picture and the blob's EOR patch
    // is its diff vs the base, so a pixel that was NOT animating must follow the new base — else the
    // entire difference between the old and new picture turns into patch data and the animation is
    // destroyed. Same rule pePaint applies to a single pixel, applied to all of them.
    const old = pe.pixels;
    pe.pixels = d.pixels;
    for (const s of pe.slots) for (const f of s.track)
      for (let y = 0; y < pe.h; y++) for (let x = 0; x < pe.w; x++)
        if (f.grid[y][x] === old[y][x]) f.grid[y][x] = d.pixels[y][x];
  } else {
    pe.slots[dst.slot].track[dst.frame].grid = d.pixels;   // one animation state, base untouched
  }
  pe.active = { slot: dst.slot, frame: dst.frame };
  peFrames();
  const parts = [`${file.name} (${d.source[0]}×${d.source[1]}, ${d.colors} colour${d.colors === 1 ? "" : "s"}) loaded into ${where} — press Save to write it.`];
  parts.push(d.mapping.map((m) => `${m.src}→${m.to}${m.exact ? "" : " (snapped)"}`).join("  "));
  if (d.warnings.length) parts.push("⚠ " + d.warnings.join("  ⚠ "));
  show(parts.join("  •  "), d.warnings.length ? "warn" : "ok");
}

(function wirePortraitImport() {
  const box = $("portrait-editor"), fi = $("pe-file");
  // the frame card under the pointer, as an import target ({slot,frame}) — null anywhere else
  const dropTarget = (e) => {
    const c = e.target.closest && e.target.closest("canvas.pe-fc");
    return c ? { slot: +c.dataset.slot, frame: +c.dataset.frame } : null;
  };
  const hover = (c) => {
    box.querySelectorAll(".pe-frame.drop").forEach((el) => el.classList.remove("drop"));
    if (c) c.closest(".pe-frame").classList.add("drop");
  };
  $("pe-import").addEventListener("click", () => fi.click());
  // button = no drop target, so it loads into the frame you are editing
  fi.addEventListener("change", () => { if (fi.files[0]) importPortraitPNG(fi.files[0]); fi.value = ""; });
  box.addEventListener("dragover", (e) => {
    e.preventDefault(); box.classList.add("dropping");
    hover(e.target.closest && e.target.closest("canvas.pe-fc"));   // show WHICH frame will take it
  });
  box.addEventListener("dragleave", (e) => { if (e.target === box) { box.classList.remove("dropping"); hover(null); } });
  box.addEventListener("drop", (e) => {
    e.preventDefault(); box.classList.remove("dropping");
    const t = dropTarget(e); hover(null);
    const f = e.dataTransfer.files[0]; if (f) importPortraitPNG(f, t);
  });
})();

  $("pe-close").addEventListener("click", () => { stopPreview(); $("portrait-editor").hidden = true; pe = null; });
  const pef = $("pe-frames"); let pp = false;
  pef.addEventListener("mousedown", (e) => { if (pe && e.target.matches("canvas.pe-fc")) { pp = true; pePaint(e); } });
  pef.addEventListener("mousemove", (e) => { if (pp && pe) pePaint(e); });
  window.addEventListener("mouseup", () => { tp = false; pp = false; });
  // default area
  // Area 0 is the Arizona overworld — the map every other area hangs off, and the one place
  // where "where am I" has an answer without context. Open there, not on some interior.
  if (areas.length) { const ow = areas.find((a) => a.side === 1 && a.area_id === 0); sel.value = ow ? "1:0" : `${areas[0].side}:${areas[0].area_id}`; const [side, id] = sel.value.split(":").map(Number); loadArea(side, id); }
}
init();


// ── AREA Services tab: shops / doctors / bank — read AND write ──────────────────────────
// A service has no op-6 cell; it hangs off an op-A door's 2-op chain, so it is enumerated from
// the doors. Field layout differs per family (store / doc / bank) — see SERVICE_HANDLERS.md.
let svcData = null;

// One ASCII box per page/text, plus the byte budget. The record window is 256 B and every string
// is reached by a single offset byte, so going over does not truncate — it points the engine at
// whatever follows. Better to show the number while typing than to fail at save.
function pageEditor(cid, field, texts, budget, labels) {
  const name = (i) => Array.isArray(labels) ? labels[i] : `${labels} ${i + 1}`;
  const boxes = (texts || []).map((t, i) =>
    `<div class="svc-page"><span>${name(i)}</span><textarea class="svc-t" data-cid="${cid}" data-f="${field}" rows="2">\n${
      String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;")}</textarea></div>`).join("");
  return `<div class="svc-pages" data-cid="${cid}" data-field="${field}" data-fixed="${Array.isArray(labels) ? 1 : 0}">${boxes}</div>
    <div class="svc-row"><span>budget</span><span class="svc-budget" data-cid="${cid}">${budget} B free</span></div>`;
}

function svcBudget(cid) {
  const wrap = document.querySelector(`#svc-list .svc-pages[data-cid="${cid}"]`);
  const out = document.querySelector(`#svc-list .svc-budget[data-cid="${cid}"]`);
  if (!wrap || !out) return;
  const vals = [...wrap.querySelectorAll("textarea")].map((t) => t.value);
  const n = wrap.dataset.fixed === "1" ? 0 : vals.length;    // talk also stores an offset per page
  const used = 0x14 + n + vals.reduce((s, v) => s + v.length + 1, 0);
  const free = 255 - used;
  out.textContent = free >= 0 ? `${free} B free` : `${-free} B TOO LONG`;
  out.className = "svc-budget" + (free < 0 ? " over" : free < 24 ? " tight" : "");
}

// Talk pages are ordered references to text5 STRINGS in the pool. Each row shows the string id and
// a preview; the TEXT itself is edited in the Text tab. Add creates a new string and appends its id;
// ▲▼ reorder; × removes the reference (the string stays in the pool). Save writes the id list.
function talkPagesEditor(cid, ed) {
  const ids = ed.page_ids || [];
  const rows = ids.map((sid, i) => {
    const txt = (ed.pages[i] || "").replace(/&/g, "&amp;").replace(/</g, "&lt;");
    return `<div class="svc-page" data-cid="${cid}" data-sid="${sid}">
      <span>page ${i + 1}</span>
      <a class="tp-str" data-sid="${sid}" title="edit text in the Text tab">str ${sid}</a>
      <span class="tp-prev muted small">${txt}</span>
      <span class="tp-ctl"><button class="tp-up mini" data-cid="${cid}" data-i="${i}">▲</button><button class="tp-dn mini" data-cid="${cid}" data-i="${i}">▼</button><button class="tp-rm mini" data-cid="${cid}" data-i="${i}">×</button></span></div>`;
  }).join("");
  return `<div class="svc-tpages" data-cid="${cid}" data-ids="${ids.join(",")}">${rows}</div>
    <div class="svc-row"><button class="svc-page-add mini" data-cid="${cid}">＋ page</button>
      <span class="muted small">a page = one pool string (edit its text in the Text tab). RETURN leaves, it wraps.</span></div>`;
}

function tpIds(cid) {
  const w = document.querySelector(`#svc-list .svc-tpages[data-cid="${cid}"]`);
  return w ? (w.dataset.ids ? w.dataset.ids.split(",").map(Number).filter((n) => !isNaN(n)) : []) : null;
}
function tpSet(cid, ids) {
  const w = document.querySelector(`#svc-list .svc-tpages[data-cid="${cid}"]`);
  if (w) w.dataset.ids = ids.join(",");
}

// Events -> the Services editor, landing on the one you were looking at. Leaving the flow view is
// part of the jump: the Services panel lives in the AREA column, which the flow view replaces.
async function svcFocus(cid) {
  setMode("view");
  setAreaTab("services");
  await loadServices();
  const el = $("svc-list")?.querySelector(`[data-svc-cid="${cid}"]`);
  if (!el) { status(`grp6[${cid}] is not in the Services list — it may be a grp16 event, not a service`); return; }
  el.scrollIntoView({ block: "center", behavior: "smooth" });
  el.classList.add("hit"); setTimeout(() => el.classList.remove("hit"), 1600);
}

async function loadServices() {
  if (!area) return;
  const box = $("svc-list");
  box.innerHTML = "…";
  const d = await (await fetch(`/api/area/${curSide}/${curAid}/services`)).json();
  svcData = d.services || [];
  $("svc-count").textContent = `${svcData.length} in this area`;
  const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const rowOf = (p, k) => { const r = (p.rows || []).find((x) => x[0] === k); return r ? r[1] : ""; };
  const num = (cid, f, v, w) => `<input class="svc-f" data-cid="${cid}" data-f="${f}" type="number" value="${v}" style="width:${w || 60}px">`;
  const txt = (cid, f, v, w) => `<input class="svc-f" data-cid="${cid}" data-f="${f}" value="${esc(v)}" style="width:${w || 180}px">`;

  box.innerHTML = svcData.map((s) => {
    if (s.grp16_event) {
      return `<div class="svc" data-svc-cid="${s.op6_cid}"><b>${s.cell ? `(${s.cell[0]},${s.cell[1]})` : "no door"}</b> — grp16 area-event #${s.byte0}
        <div class="muted small">code lives in the record; not editable here</div></div>`;
    }
    const p = s.panel, cid = s.op6_cid;
    // Talk and revive hand us STRUCTURED text (panel.edit) instead of display rows — their pages
    // are raw ASCII in the record, addressed by ONE offset byte, so the byte budget is shown.
    const ed = p.edit;
    const fam = String(rowOf(p, "kind")).toLowerCase();
    const isStore = fam.includes("store"), isBank = fam.includes("bank");
    // A STOCK service (the 1988 selectors $00/$01) has no portrait byte in its record: the id is a
    // hardcoded IMMEDIATE inside the shared scene handler — s1_scene_id00.bin does `lda #$00 / jsr
    // $041C`, s1_scene_id01.bin does `lda #$01`. Offering a spinner there was a lie twice over: the
    // save path drops the value, and every area running that stock scene would share it anyway.
    const portRow = String(rowOf(p, "portrait"));
    const portNum = (portRow.match(/\d+/) || [""])[0];
    const portField = portNum !== ""
      ? `${num(cid, "portrait", portNum)}<span class="muted small">str id</span>`
      : `<span class="muted small">${esc(portRow)} — <code>lda #&lt;id&gt; / jsr $041C</code> in
         s${curSide}_scene_id${String(p.scene_sel ?? 0).padStart(2, "0")}.bin, shared by every area using this stock scene.
         Convert it to a derived copy to give this one its own.</span>`;
    if (p.readonly) {
      return `<div class="svc" data-svc-cid="${cid}">
        <b>${s.cell ? `(${s.cell[0]},${s.cell[1]})` : "no door"}</b> ${esc(rowOf(p, "kind"))}
        <span class="muted small">op6 #${cid} · ${esc(rowOf(p, "selector"))}</span>
        <div class="svc-row"><span>label</span><b>${esc(rowOf(p, "sign / label"))}</b></div>
        <div class="muted small">${esc(rowOf(p, "layout"))}</div></div>`;
    }
    let fields = `
      <div class="svc-row"><span>label</span>${txt(cid, "label", String(rowOf(p, "sign / label")).trim(), 120)}</div>
      <div class="svc-row"><span>portrait</span>${portField}</div>`;
    if (isStore) {
      fields += `
      <div class="svc-row"><span>stock table</span>${num(cid, "areatrack", (String(rowOf(p, "stock table")).match(/\d+/) || [0])[0])}
        <span class="muted small">areatrack — holds price + what is in stock</span></div>
      <div class="svc-row"><span>classes</span>${txt(cid, "classes", String(rowOf(p, "classes")), 140)}
        <span class="muted small">comma separated</span></div>
      <div class="svc-row"><span>buy / sell shift</span>${num(cid, "buy", (String(rowOf(p, "buy price")).match(/\d+/) || [0])[0])}
        ${num(cid, "sell", (String(rowOf(p, "sell price")).match(/\d+/) || [0])[0])}
        <span class="muted small">0 = full price</span></div>`;
    } else if (isBank) {
      fields += `
      <div class="svc-row"><span>gives</span>${num(cid, "amount", (String(rowOf(p, "gives")).match(/\d+/) || [0])[0], 80)}
        <span class="muted small">per ranger</span></div>
      <div class="svc-row"><span>confirmation</span>${txt(cid, "confirm", rowOf(p, "confirmation (ASCII)"), 240)}
        <span class="muted small">plain ASCII — digits and $ allowed</span></div>`;
    } else if (ed && ed.family === "talk") {
      fields += talkPagesEditor(cid, ed);
    } else if (ed && ed.family === "revive") {
      fields += pageEditor(cid, "texts", ed.texts, ed.budget, ["prompt", "revived", "refused"]);
    } else {
      const pr = String(rowOf(p, "treatment prices"));
      fields += `<div class="svc-row"><span>prices</span>${txt(cid, "prices", pr.includes("free") ? "0,0,0" : pr.replace(/\s*\/\s*/g, ","), 120)}
        <span class="muted small">three treatments, 0 = free</span></div>`;
    }
    const sells = (p.rows || []).find((x) => String(x[0]).startsWith("sells"));
    return `<div class="svc" data-svc-cid="${cid}">
      <b>${s.cell ? `(${s.cell[0]},${s.cell[1]})` : "no door"}</b> ${esc(rowOf(p, "kind"))} <span class="muted small">op6 #${cid} · ${esc(rowOf(p, "selector"))}${s.no_door ? " · reached by a chain edge, not a door" : ""}</span>
      ${fields}
      <div class="svc-row"><span>greeting</span><span class="muted">${esc(rowOf(p, "greeting"))}</span></div>
      ${isBank ? `<div class="svc-row"><span>prompt</span><span class="muted">${esc(rowOf(p, "prompt"))}</span></div>` : ""}
      ${sells ? `<div class="svc-row"><span>sells</span><span class="muted">${esc(sells[1])}</span></div>` : ""}
      <button class="svc-save" data-cid="${cid}">Save service</button>
    </div>`;
  }).join("") || "<div class='muted'>no services in this area</div>";

  box.querySelectorAll(".svc-save").forEach((b) => b.addEventListener("click", () => saveService(+b.dataset.cid)));
  box.querySelectorAll(".svc-t").forEach((t) => t.addEventListener("input", () => svcBudget(t.dataset.cid)));
  // talk page-id management
  box.querySelectorAll(".tp-str").forEach((a) => a.addEventListener("click", () => {
    setTab("map"); setAreaTab("text");
    loadAreaTexts().then(() => {
      const ta = document.querySelector(`#txt-list .txt-ta[data-tid="${a.dataset.sid}"]`);
      if (ta) { ta.scrollIntoView({ block: "center" }); ta.focus(); }
    });
  }));
  box.querySelectorAll(".tp-rm").forEach((b) => b.addEventListener("click", () => {
    const ids = tpIds(+b.dataset.cid); if (ids.length <= 1) { status("a talk needs at least one page"); return; }
    ids.splice(+b.dataset.i, 1); tpSet(+b.dataset.cid, ids); saveService(+b.dataset.cid);
  }));
  box.querySelectorAll(".tp-up").forEach((b) => b.addEventListener("click", () => tpMove(+b.dataset.cid, +b.dataset.i, -1)));
  box.querySelectorAll(".tp-dn").forEach((b) => b.addEventListener("click", () => tpMove(+b.dataset.cid, +b.dataset.i, 1)));
  box.querySelectorAll(".svc-page-add").forEach((b) => b.addEventListener("click", () => tpAddPage(+b.dataset.cid)));
}


function tpMove(cid, i, d) {
  const ids = tpIds(cid); const j = i + d;
  if (j < 0 || j >= ids.length) return;
  [ids[i], ids[j]] = [ids[j], ids[i]]; tpSet(cid, ids); saveService(cid);
}

// A new page = a NEW pool string. Create it (with the <07> box prefix), then append its id.
async function tpAddPage(cid) {
  const text = prompt("New page text (the <07> box prefix is added for you):");
  if (text === null) return;
  const d = await (await fetch(`/api/area/${curSide}/${curAid}/texts`)).json();
  const nid = d.messages.length;
  const body = "<07>" + text;
  const r = await fetch(`/api/area/${curSide}/${curAid}/prose`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ edits: [{ id: nid, text: body }] }) });
  if (!r.ok) { status(`add page err ${r.status}: ${(await r.json()).detail || ""}`); return; }
  const ids = (tpIds(cid) || []).concat(nid); tpSet(cid, ids);
  await saveService(cid);
}

async function saveService(cid) {
  const body = {};
  const tp = tpIds(cid);
  if (tp) body.page_ids = tp;                            // talk: ordered string-id list
  const pageWrap = document.querySelector(`#svc-list .svc-pages[data-cid="${cid}"]`);
  if (pageWrap) {                                         // revive: fixed ASCII texts
    // NOT trimmed: a service greeting ends in the newlines that lay out its box ("...covered.\n\n"),
    // and trim() ate them. Emptiness is still decided on the trimmed form, so an all-whitespace page
    // still drops out of the list — only the text that survives keeps its own whitespace.
    const vals = [...pageWrap.querySelectorAll("textarea")].map((t) => t.value);
    body[pageWrap.dataset.field] = pageWrap.dataset.fixed === "1" ? vals : vals.filter((v) => v.trim() !== "");
  }
  document.querySelectorAll(`#svc-list .svc-f[data-cid="${cid}"]`).forEach((el) => {
    const f = el.dataset.f, v = el.value.trim();
    if (v === "") return;
    if (f === "classes" || f === "prices") {
      const arr = v.split(/[,\s]+/).filter(Boolean).map(Number).filter((n) => !isNaN(n));
      if (arr.length) body[f === "prices" ? "prices" : "classes"] = arr;
    } else if (f === "label" || f === "confirm") body[f] = v;
    else { const n = Number(v); if (!isNaN(n)) body[f] = n; }
  });
  const r = await saveFetch(`/api/area/${curSide}/${curAid}/service/${cid}`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (!r) return;
  const j = await r.json();
  if (!r.ok) { status(`service ${cid}: ${j.detail || "error"}`); return; }
  status(`service ${cid} saved (${j.family}, ${j.bytes} B) → ${j.saved}`);
  await loadServices();
}


// ── optimistic concurrency ──────────────────────────────────────────────────────────────
// Every save rewrites the WHOLE record, so a tab holding a stale copy would clobber newer work
// (two browsers open, or the CLI writing while the UI is up). We remember the record hash the
// view was built from and send it with each write; the backend answers 409 if it moved.
let recRev = null;

async function refreshRev() {
  try {
    const d = await (await fetch(`/api/area/${curSide}/${curAid}/rev`)).json();
    recRev = d.rev || null;
  } catch { recRev = null; }
}

function revQuery() { return recRev ? `?base_rev=${encodeURIComponent(recRev)}` : ""; }

// Wrap fetch for the area-record writes: append the rev, and turn a 409 into a clear prompt.
async function saveFetch(url, opts) {
  const r = await fetch(url + revQuery(), opts);
  if (r.status === 409) {
    const j = await r.json().catch(() => ({}));
    status(`⚠ ${j.detail || "record changed underneath"}`);
    if (confirm("Another window (or the build) changed this area since you opened it.\n\n" +
                "Your copy is stale — saving would overwrite their work.\n\nReload the area now?")) {
      await loadArea(curSide, curAid);
    }
    return null;
  }
  await refreshRev();
  return r;
}

// An open editor belongs to the area it was opened in. Switching area used to leave it standing,
// so the form still showed (and would have SAVED) another area's foe — Highpool's Brat sitting in
// TREX Base's panel, one click from overwriting foe #1 there. Two guards, because either alone
// leaves a hole: clear the panels on load, and refuse a save whose area is no longer the open one.
function closeAreaEditors() {
  for (const id of ["foe-edit", "chain-edit", "npc-edit"]) { const b = $(id); if (b) b.innerHTML = ""; }
}
function areaStillOpen(side, id, msgBox) {
  if (side === curSide && id === curAid) return true;
  const m = $(msgBox); if (m) m.textContent = "this form belongs to another area — reopen it here";
  return false;
}

// ── CHAINS: the area's script chains walked over the REFERENCE GRAPH, and editable ────────────
// The old Script list enumerated the map grid, so it could only show op-2/op-C CELLS. Half of a
// chain has no cell at all — a victory state, a loot advance, a patch target — which is why a
// six-record questline appeared as nothing. This shows the chain and lets you change the numbers
// that are only reachable through it.
let chainData = null;
let storyData = null;

// The records toggle. Debug detail (grp/cid) is a switch and not a column, because the question a
// story view answers is "what happens", and a box that carries three levels of abstraction at once
// carries none of them well. Off by default; the choice is remembered.
document.addEventListener("DOMContentLoaded", () => {
  const cb = $("chain-dbg"); if (!cb) return;
  const apply = () => document.body.classList.toggle("show-dbg", cb.checked);
  cb.checked = localStorage.getItem("wl.chainDbg") === "1";
  apply();
  cb.addEventListener("change", () => { localStorage.setItem("wl.chainDbg", cb.checked ? "1" : "0"); apply(); });
});

// The BEAT names — what a record is in the story, not what it is in the file. A player does not
// meet an "op-$3 record", they get into a fight; the grp/cid stays available behind the records
// toggle for when the question is actually about bytes.
// EVENT is its own beat, not a TALK. Both are op-6 records — bit7 of byte 0 decides — but a
// SERVICE is a person to deal with and an EVENT is a jump into the area's own 6502, which is where
// nineteen of the game's world-parameter changes live. Calling both "TALK" hid every one of them.
const BEAT_NAME = { ENCOUNTER: "FIGHT", LOOT: "LOOT", SERVICE: "TALK", EVENT: "WORLD", SAY: "SAY",
                    COND: "GATE", KEYWORD: "ASK", HAZARD: "HARM", TRANSITION: "GO", BUMP: "BUMP" };

// How an arrow's plumbing reads. A CELL_PATCH entry that points at op 0 is not a reference, it is
// the square going empty — the distinction that used to cut chains in half.
function edgeEffectText(e, recKind) {
  if (e.kind === "tile") return `draws tile $${e.tile.toString(16).toUpperCase().padStart(2, "0")}${e.solid ? " (solid)" : ""}`;
  if (e.kind === "msg") return `message ${e.msg}`;
  const to = !e.to_op ? "goes empty" : `becomes ${BEAT_NAME[e.to_kind] || e.to_kind || "op $" + e.to_op.toString(16)}`;
  return `(${e.x},${e.y}) ${to}`;
}

// The gate: grammar, budget, fit — the three things that have to hold before bytes are emitted.
// Shown above the story rather than on save, because the expensive mistake is wiring three steps
// into an area that had no text id left to begin with.
function gateRender(v) {
  const box = $("chain-gate"); if (!box) return;
  if (!v) { box.innerHTML = ""; return; }
  const s = v.budget?.strings || {}, by = v.budget?.bytes || {};
  const bits = [`<span class="${s.free ? (s.free <= 8 ? "gate-warn" : "gate-ok") : "gate-err"}">${s.free} text id${s.free === 1 ? "" : "s"} free</span>`,
                `${v.budget?.foes?.used ?? "?"} foes`,
                `${v.budget?.records?.total ?? "?"} records`];
  if (by.delta) bits.push(`<span class="${by.delta > 0 ? "gate-warn" : "gate-ok"}">${by.delta > 0 ? "+" : ""}${by.delta} B vs baseline</span>`);
  const fnd = (v.findings || []).filter((f) => f.level !== "info");
  box.innerHTML = `<div class="gate-line">${bits.join(" · ")}</div>` +
    fnd.map((f) => `<div class="gate-f gate-${f.level}"><b>${fesc(f.text)}</b>` +
                   (f.detail ? `<span class="muted small"> — ${fesc(f.detail)}</span>` : "") + `</div>`).join("");
}

async function chainsLoad(side, id) {
  const box = $("chain-list"); if (!box) return;
  box.innerHTML = `<span class="muted small">…</span>`;
  // /chains stays the edit surface (chainEdit and chainFocus both key off its steps); /story is the
  // same data folded — beats as rows, plumbing on the arrows — and /validate is the gate.
  const [chains, story, gate] = await Promise.all([
    fetch(`/api/area/${side}/${id}/chains`).then((r) => r.json()),
    fetch(`/api/area/${side}/${id}/story`).then((r) => r.json()),
    fetch(`/api/area/${side}/${id}/validate`).then((r) => r.json()).catch(() => null),
  ]);
  chainData = chains; storyData = story;
  areaVocab = chains.vocab || areaVocab;
  gateRender(gate);
  const S = story.strings || [];
  const txt = (i) => (S[i & 0x7f] || "").slice(0, 60);
  const stepOf = (b) => (chains.chains.flatMap((c) => c.steps)
                         .find((st) => st.grp === b.grp && st.cid === b.cid)) || b;
  box.innerHTML = "";
  if (!story.stories.length) { box.innerHTML = `<span class="muted small">no script chains here</span>`; return; }

  story.stories.forEach((sto, si) => {
    const d = document.createElement("div"); d.className = "chain"; d.dataset.story = si;
    const folded = sto.records - sto.shown;
    d.innerHTML = `<div class="chain-head">@ ${(sto.cell || []).join(",")} ` +
      `<span class="muted small">${sto.shown} beat${sto.shown === 1 ? "" : "s"}` +
      (folded > 0 ? ` · ${folded} on the arrows` : "") + `</span></div>`;

    sto.beats.forEach((b, k) => {
      if (b.in_edge) {                       // the arrow first — it explains where the next beat is
        const e = b.in_edge;
        const eff = (e.effects || []).map((x) => edgeEffectText(x)).join(" · ");
        const a = document.createElement("div"); a.className = "chain-edge";
        a.innerHTML = `<span class="edge-lbl">└▸ ${fesc(e.label)}:</span> ` +
          `<span class="muted small">${fesc(eff || "plain hand-over")}</span>` +
          (e.via || []).map((v) => `<span class="dbg"> grp${v.grp}[${v.cid}]</span>`).join("");
        d.appendChild(a);
      }
      const n = document.createElement("div"); n.className = "chain-step";
      n.dataset.rec = `${b.grp}:${b.cid}`;
      const pages = fieldOf(b, "pages") || [];
      const summary = b.op === "ENCOUNTER" ? txt(fieldOf(b, "msg"))
                    : b.op === "SAY"       ? txt((fieldOf(b, "messages") || [0])[0])
                    : b.op === "LOOT"      ? (fieldOf(b, "items") || []).length + " item(s)"
                    // a service is named by its label; "0 pages" was noise on the eleven TREX
                    // services that are shops and doctors rather than talkers
                    : b.op === "SERVICE"   ? (fieldOf(b, "label") || "").trim() ||
                                             (pages.length ? `${pages.length} pages` : "")
                    : b.op === "BUMP"      ? txt(fieldOf(b, "msg")) : "";
      // A beat with no square of its own is normal — it inherits the one it came from, or a patch
      // planted it. Saying WHICH is the difference between a coordinate you may move and one you
      // may not: only an `edge` coordinate belongs to this hop.
      const where = b.cell ? `(${b.cell.join(",")})` : "—";
      const owner = b.cell_owner === "edge" ? "" : b.cell_owner === "same" ? " same square" : "";
      n.innerHTML = `<span class="beat-n">${k + 1}</span>` +
        `<span class="chain-op">${BEAT_NAME[b.op] || b.op}</span> ` +
        `<span class="beat-cell">${where}</span><span class="muted small">${owner}</span>` +
        `<span class="dbg"> grp${b.grp}[${b.cid}]</span>` +
        `<div class="muted small chain-sum">${fesc(b.label || summary)}</div>`;
      n.addEventListener("click", () => chainEdit(side, id, stepOf(b)));
      d.appendChild(n);
    });
    box.appendChild(d);
  });
}
// From the cell inspector into the CHAIN that runs through this cell. A cell can appear as the
// chain's root or as a later step (a patch target has no cell of its own but writes one), so both
// are matched; the step is opened straight away, because the reason to come here is to change it.
// Jump to the chain step that RAISES a record — the world panel lists an event by what it does,
// and "which step does that" is the next question every time. By record, not by cell: an event
// hangs off a gate and usually has no square of its own.
// A miss is normal and must SAY so. The Script chain walks one path, so only one outcome of a COND
// gate is on it — Cochise Core raises six world events and three of them hang off the branch the
// walk did not take. The Events outline follows every branch, so that is where the message points.
function chainFocusRec(grp, cid) {
  document.querySelector('[data-ap="script"]')?.click();
  const open = (tries = 0) => {
    const n = $("chain-list")?.querySelector(`[data-rec="${grp}:${cid}"]`);
    if (!n) {
      if (tries < 20) { setTimeout(() => open(tries + 1), 100); return; }
      status(`grp${grp}[${cid}] is not on a walked chain — it hangs off a branch the Script view `
             + `does not follow. Events shows every branch.`);
      return;
    }
    n.scrollIntoView({ block: "center", behavior: "smooth" });
    n.classList.add("hit"); setTimeout(() => n.classList.remove("hit"), 1600);
  };
  open();
}

function chainFocus(col, row) {
  document.querySelector('[data-ap="script"]')?.click();
  const open = () => {
    if (!chainData) return false;
    for (const ch of chainData.chains) {
      const rootHit = ch.cell[0] === col && ch.cell[1] === row;
      const stepHit = ch.steps.findIndex((st) => (st.fields || []).some((f) =>
        f.kind === "cells" && (f.value || []).some((e) => e.x === col && e.y === row)));
      if (!rootHit && stepHit < 0) continue;
      const box = $("chain-list");
      // Match the rendered box by its ROOT CELL, not by list index: the story view drops chains
      // that fold to nothing, so the two lists no longer line up and an index would highlight a
      // neighbouring chain — the kind of near-miss that is worse than no highlight at all.
      const si = (storyData?.stories || []).findIndex((s) => s.cell?.[0] === ch.cell[0] && s.cell?.[1] === ch.cell[1]);
      const el = si >= 0 ? box?.querySelector(`.chain[data-story="${si}"]`) : null;
      el?.scrollIntoView({ block: "nearest" });
      el?.classList.add("chain-hit");
      setTimeout(() => el?.classList.remove("chain-hit"), 1500);
      chainEdit(curSide, curAid, ch.steps[rootHit ? 0 : stepHit]);
      return true;
    }
    status(`(${col},${row}) is not part of a script chain`);
    return true;
  };
  if (!open()) chainsLoad(curSide, curAid).then(open);
}

function fieldOf(st, key) { const f = (st.fields || []).find((x) => x.key === key); return f && f.value; }

// ── the vocabulary a form speaks in ────────────────────────────────────────────────────────
// One area's names for the two things that were still bytes on screen: what a loot sack contains,
// and what a record hands over to. The tables come from the backend with the chain payload
// (wl_story.vocab) — a copy over here would be a second item table to keep in step, which is the
// class of duplication that put a Perception check on screen as the item "Mangler".
let areaVocab = { items: {}, records: {}, edge_ops: [] };

function itemSelect(cur) {
  const it = areaVocab.items || {};
  const ids = Object.keys(it).map(Number).sort((a, b) => a - b);
  if (!ids.length) return `<input type="number" class="ii" min="0" max="127" value="${cur}" style="width:60px">`;
  const known = ids.includes(+cur);
  return `<select class="ii">` +
    (known ? "" : `<option value="${cur}" selected>item ${cur} — unknown</option>`) +
    ids.map((i) => `<option value="${i}"${i === +cur ? " selected" : ""}>${fesc(it[i])}</option>`).join("") +
    `</select>`;
}

function edgeOpSelect(cur) {
  const ops = areaVocab.edge_ops || [];
  if (!ops.length) return `<input type="number" class="eo" min="0" max="255" value="${cur}" style="width:56px">`;
  const known = ops.some((o) => o.op === +cur);
  return `<select class="eo">` +
    (known ? "" : `<option value="${cur}" selected>op ${cur}</option>`) +
    ops.map((o) => `<option value="${o.op}"${o.op === +cur ? " selected" : ""}>${fesc(o.name)}</option>`).join("") +
    `</select>`;
}

// The id list is the RECORDS THAT EXIST in that group, each with what it is and what it says — so
// picking a target is picking a thing you can read, not guessing an index.
function edgeIdSelect(op, cur) {
  const recs = (areaVocab.records || {})[String(op)];
  if (+op === 0 || +op >= 0x10) return `<span class="ei-none muted small"></span>`;
  if (!recs) return `<input type="number" class="ei" min="0" max="255" value="${cur}" style="width:56px">`;
  const ids = Object.keys(recs).map(Number).sort((a, b) => a - b);
  const known = ids.includes(+cur);
  return `<select class="ei">` +
    (known ? "" : `<option value="${cur}" selected>[${cur}] — no such record</option>`) +
    ids.map((i) => {
      const r = recs[i];
      return `<option value="${i}"${i === +cur ? " selected" : ""}>[${i}] ${fesc(r.label || r.op || "")}</option>`;
    }).join("") + `</select>`;
}

// One condition row. A condition's ARG means a different thing per kind — a skill id, a stat
// offset, an item, a number of party members — so the kind is a select and, where the arg has a
// vocabulary, the arg is one too. The vocabulary comes FROM THE BACKEND (`cond_vocab`), not from a
// copy over here: a second skill table would drift, and mislabelling the arg is exactly the bug
// that read a Perception check as the item "Mangler" for years.
function chainEditCondRows(f) {
  const opts = f.options || [], vocab = f.vocab || {};
  const sel = (name, cur) => {
    const v = vocab[name]; if (!v) return null;
    return Object.entries(v).map(([id, nm]) =>
      `<option value="${id}"${+id === +cur ? " selected" : ""}>${fesc(nm)} (${id})</option>`).join("");
  };
  return (f.value || []).map((c) => {
    const kind = opts.find((o) => o.op === c.op) || opts[opts.length - 1] || { arg: "", difficulty: false };
    const argSel = sel(kind.arg, c.arg);
    return `<div class="cellrow condrow">
      <select class="ck">${opts.map((o) => `<option value="${o.op}"${o.op === c.op ? " selected" : ""}>${fesc(o.name)}</option>`).join("")}</select>
      ${argSel ? `<select class="ca">${argSel}</select>`
               : `<input type="number" class="ca" min="0" max="255" value="${c.arg}" style="width:64px">`}
      ${kind.difficulty ? `<span class="ef-hint">difficulty <input type="number" class="cd" min="0" max="31" value="${c.difficulty}" style="width:48px"></span>` : ""}
      <button class="mini cx-del" title="remove this condition">✕</button></div>`;
  }).join("");
}

// The form opens UNDER THE BEAT that was clicked. It used to render into #chain-edit, which sits
// after the whole list — with fifty chains in an area that is thousands of pixels below the click,
// so the editor opened correctly and was simply never seen. A story view whose editor lives
// somewhere else is two components pretending to be one.
// `container` is the list the beat lives in — the Script panel by default, the Events outline when
// it calls. One function for both, because a form that behaves differently depending on which view
// opened it is two forms.
// `ctx` = {strings, foes} FOR THE RECORD'S OWN AREA. Without it 0.8.107 resolved every id against
// whatever area happened to be loaded in the Areas tab: the Journey view showed a Church/Police
// gate captioned with Highpool's "Jackie went into the cave yesterday". An id is only meaningful
// inside its area, so the resolver takes the area with it — and when the caller cannot supply one
// and the loaded area is a DIFFERENT area, the number stays a number instead of lying.
function chainEdit(side, id, st, container, targetBox, ctx) {
  const list = container || $("chain-list");
  const row = targetBox ? null : list?.querySelector(`.chain-step[data-rec="${st.grp}:${st.cid}"]`);
  list?.querySelectorAll(".chain-inline").forEach((el) => el.remove());   // one open form at a time
  list?.querySelectorAll(".chain-step.open").forEach((el) => el.classList.remove("open"));
  let box = targetBox || $("chain-edit");
  if (row) {
    box = document.createElement("div"); box.className = "chain-inline";
    row.classList.add("open");
    row.after(box);
    if ($("chain-edit")) $("chain-edit").innerHTML = "";
  } else if (!box) {
    box = document.createElement("div"); box.className = "chain-inline";
    list?.appendChild(box);
  }
  // Ids resolve in the form, not just in the reading views. "announce 15" and "foe 1" are the two
  // places the inspector still spoke in indices — and an index into a table the editor is already
  // holding is a number the author has to remember for no reason.
  const own = side === curSide && id === curAid;          // the globals describe THE LOADED area
  const strs = ctx?.strings
            || (own && ((typeof chainData !== "undefined" && chainData?.strings)
                     || (typeof eventsData !== "undefined" && eventsData?.strings)))
            || null;
  const foes = ctx?.foes || (own ? areaFoes : null);
  const strText = (i) => strs ? (strs[(+i || 0) & 0x7f] || "").slice(0, 120)
                              : `string ${+i || 0} — open s${side} area ${id} to read it`;
  const foeOf = (fid) => (foes || []).find((x) => x.id === +fid);
  const foeSide = ctx?.side ?? side;

  const rows = st.fields.map((f) => {
    const k = f.key;
    if (f.kind === "text")
      return `<tr><td>${fesc(f.label)}</td><td>
              <input type="number" data-k="${k}" data-kind="int" min="0" max="127" value="${f.value}" style="width:72px" class="ef-strid">
              <div class="ef-prev" data-for="${k}">${fesc(strText(f.value)) || "<i>empty</i>"}</div>
              <span class="ef-hint">${fesc(f.hint || "")}</span></td></tr>`;
    if (f.kind === "foe" && !foes)
      return `<tr><td>${fesc(f.label)}</td><td><input type="number" data-k="${k}" data-kind="int"
              min="0" max="127" value="${f.value}" style="width:72px">
              <span class="ef-hint">index into s${side} area ${id}'s foe table</span></td></tr>`;
    if (f.kind === "foe") {
      const cur = foeOf(f.value);
      return `<tr><td>${fesc(f.label)}</td><td><div class="ef-foe">
              <img class="ef-foe-img" src="${cur ? `/api/portrait/${cur.side}/${cur.portrait_str}` : ""}"
                   alt="" onerror="this.style.visibility='hidden'">
              <select data-k="${k}" data-kind="int" class="ef-foe-sel">
                ${(areaFoes || []).map((x) => `<option value="${x.id}"${x.id === +f.value ? " selected" : ""}>${fesc(x.name)} (${x.id}) · ${x.exp} exp</option>`).join("")}
                ${cur ? "" : `<option value="${f.value}" selected>foe ${f.value} — not in this area's table</option>`}
              </select></div>
              <span class="ef-hint">${fesc(f.hint || "")}</span></td></tr>`;
    }
    if (f.kind === "portrait")
      return `<tr><td>${fesc(f.label)}</td><td><div class="ef-foe">
              <img class="ef-foe-img ef-pt-img" src="/api/portrait/${foeSide}/${f.value}" alt=""
                   onerror="this.style.visibility='hidden'">
              <input type="number" data-k="${k}" data-kind="int" min="0" max="255" value="${f.value}"
                     style="width:72px" class="ef-ptid"></div>
              <span class="ef-hint">${fesc(f.hint || "")}</span></td></tr>`;
    if (f.kind === "bool")
      return `<tr><td>${fesc(f.label)}</td><td><input type="checkbox" data-k="${k}" data-kind="bool"${f.value ? " checked" : ""}>
              <span class="ef-hint">${fesc(f.hint || "")}</span></td></tr>`;
    // An edge used to be two number boxes: "[12] [10]". That is the op nibble and the record index
    // — an address, not a thought. It points at a CELL_PATCH that decides what the square becomes,
    // which is the consequential half of a loot sack, and nothing on screen said so.
    if (f.kind === "edge")
      return `<tr><td>${fesc(f.label)}</td><td><span data-k="${k}" data-kind="edge">
              ${edgeOpSelect(f.value.op)}${edgeIdSelect(f.value.op, f.value.id)}</span>
              <span class="ef-hint">${fesc(f.hint || "")}</span></td></tr>`;
    if (f.kind === "cells")
      return `<tr><td>${fesc(f.label)}</td><td><div data-k="${k}" data-kind="cells">` +
             f.value.map((e) => `<div class="cellrow">→ x <input type="number" class="cx" min="0" max="63" value="${e.x}" style="width:52px">
                y <input type="number" class="cy" min="0" max="63" value="${e.y}" style="width:52px">
                becomes op <input type="number" class="co" min="0" max="15" value="${e.op}" style="width:44px">
                [<input type="number" class="ci" min="0" max="255" value="${e.id}" style="width:52px">]</div>`).join("") +
             `</div><span class="ef-hint">${fesc(f.hint || "")}</span></td></tr>`;
    if (f.kind === "items")
      return `<tr><td>${fesc(f.label)}</td><td><div data-k="${k}" data-kind="items">` +
             f.value.map((e) => `<div class="cellrow">${itemSelect(e.item)}
                × <input type="number" class="iq" min="1" max="255" value="${e.qty}" style="width:52px"></div>`).join("") + `</div></td></tr>`;
    if (f.kind === "pages")
      return `<tr><td>${fesc(f.label)}</td><td><input data-k="${k}" data-kind="pages" value="${f.value.join(", ")}" style="width:220px">
              <span class="ef-hint">string ids, comma separated</span></td></tr>`;
    if (f.kind === "str")
      return `<tr><td>${fesc(f.label)}</td><td><input data-k="${k}" data-kind="str" value="${fesc(f.value)}" maxlength="11" style="width:130px">
              <span class="ef-hint">${fesc(f.hint || "")}</span></td></tr>`;
    if (f.kind === "conds")
      return `<tr><td>${fesc(f.label)}</td><td><div data-k="${k}" data-kind="conds">` +
             (chainEditCondRows(f) || `<div class="muted small">none — the gate always passes</div>`) +
             `</div><button class="mini cond-add" data-for="${k}">＋ condition</button>
             <span class="ef-hint">${fesc(f.hint || "")}</span></td></tr>`;
    return `<tr><td>${fesc(f.label)}</td><td><input type="number" data-k="${k}" data-kind="int" min="0" max="255" value="${f.value}" style="width:72px">
            <span class="ef-hint">${fesc(f.hint || "")}</span></td></tr>`;
  }).join("");
  // A SERVICE is more than these fields: wares, prices, the per-family layout, the pages as real
  // text. That editor exists on the left and is the one that knows the family rules — so this form
  // offers the four things it can safely write, and a door to the one that can write the rest.
  const jump = st.op === "SERVICE"
    ? `<button id="ce-svc" class="mini" title="open this service in the Area panel's Services editor — wares, prices, pages">↗ Services editor</button>`
    : "";
  box.innerHTML = `<div class="insp-sec ef-box"><div class="insp-title">✎ ${st.op} — grp${st.grp}[${st.cid}]</div>
    <table class="ef-tbl">${rows}</table>
    <div class="btns"><button id="ce-save" class="mini">Save</button><button id="ce-close" class="mini">Close</button>${jump}</div>
    <div id="ce-msg" class="muted small"></div></div>`;
  $("ce-svc")?.addEventListener("click", () => svcFocus(st.cid));
  // Conditions are a list you add to and remove from, and the KIND decides what the arg means —
  // so changing the kind has to re-render its row, otherwise you would be editing a skill id in a
  // control that is offering attributes.
  // Changing an edge's TARGET KIND changes which records it could point at, so the id list has to
  // be rebuilt — otherwise you would be picking a grp5 index out of a list of grp12 records.
  // live: typing a string id shows the string, picking a foe swaps the face
  box.querySelectorAll(".ef-strid").forEach((inp) => inp.addEventListener("input", () => {
    const pv = box.querySelector(`.ef-prev[data-for="${inp.dataset.k}"]`);
    if (pv) pv.innerHTML = fesc(strText(inp.value)) || "<i>empty</i>";
  }));
  box.querySelectorAll(".ef-ptid").forEach((inp) => inp.addEventListener("input", () => {
    const img = inp.parentElement.querySelector(".ef-pt-img");
    if (img) { img.style.visibility = ""; img.src = `/api/portrait/${foeSide}/${inp.value}`; }
  }));
  box.querySelectorAll(".ef-foe-sel").forEach((selEl) => selEl.addEventListener("change", () => {
    const fo = foeOf(selEl.value), img = selEl.parentElement.querySelector(".ef-foe-img");
    if (img) { img.style.visibility = ""; img.src = fo ? `/api/portrait/${fo.side}/${fo.portrait_str}` : ""; }
  }));
  box.querySelectorAll('[data-kind="edge"]').forEach((sp) => {
    sp.querySelector(".eo")?.addEventListener("change", (ev) => {
      const cur = +(sp.querySelector(".ei")?.value || 0);
      sp.querySelector(".ei, .ei-none")?.remove();
      sp.insertAdjacentHTML("beforeend", edgeIdSelect(+ev.target.value, cur));
    });
  });
  const cf = st.fields.find((f) => f.kind === "conds");
  if (cf) {
    const wrap = box.querySelector('[data-kind="conds"]');
    const redraw = (vals) => {
      const tmp = chainEditCondRows({ ...cf, value: vals });
      wrap.innerHTML = tmp || `<div class="muted small">none — the gate always passes</div>`;
      wire();
    };
    const read = () => [...wrap.querySelectorAll(".condrow")].map((r) => ({
      op: +r.querySelector(".ck").value, arg: +r.querySelector(".ca").value,
      difficulty: +(r.querySelector(".cd")?.value || 0) }));
    const wire = () => {
      wrap.querySelectorAll(".ck").forEach((s) => s.addEventListener("change", () => redraw(read())));
      wrap.querySelectorAll(".cx-del").forEach((b, i) => b.addEventListener("click", (ev) => {
        ev.preventDefault(); const v = read(); v.splice(i, 1); redraw(v); }));
    };
    wire();
    box.querySelector(".cond-add")?.addEventListener("click", (ev) => {
      ev.preventDefault(); redraw([...read(), { op: 0, arg: 9, difficulty: 0 }]);
    });
  }
  $("ce-close").addEventListener("click", () => {
    row?.classList.remove("open");
    if (box.classList.contains("chain-inline")) box.remove(); else box.innerHTML = "";
  });
  if (!targetBox) box.scrollIntoView({ block: "nearest", behavior: "smooth" });
  $("ce-save").addEventListener("click", async () => {
    if (!areaStillOpen(side, id, "ce-msg")) return;
    const out = {};
    for (const el of box.querySelectorAll("[data-k]")) {
      const k = el.dataset.k, kind = el.dataset.kind;
      if (kind === "bool") out[k] = el.checked;
      else if (kind === "int") out[k] = +el.value;
      else if (kind === "str") out[k] = el.value;
      else if (kind === "pages") out[k] = el.value.split(",").map((x) => +x.trim()).filter((x) => !isNaN(x));
      else if (kind === "edge")
        out[k] = { op: +el.querySelector(".eo").value, id: +(el.querySelector(".ei")?.value || 0) };
      else if (kind === "edge-op") out[k] = { ...(out[k] || {}), op: +el.value };
      else if (kind === "edge-id") out[k] = { ...(out[k] || {}), id: +el.value };
      else if (kind === "conds")
        out[k] = [...el.querySelectorAll(".condrow")].map((r) => ({
          op: +r.querySelector(".ck").value, arg: +r.querySelector(".ca").value,
          difficulty: +(r.querySelector(".cd")?.value || 0) }));
      else if (kind === "cells")
        out[k] = [...el.querySelectorAll(".cellrow")].map((r) => ({
          x: +r.querySelector(".cx").value, y: +r.querySelector(".cy").value,
          op: +r.querySelector(".co").value, id: +r.querySelector(".ci").value }));
      else if (kind === "items")
        out[k] = [...el.querySelectorAll(".cellrow")].map((r) => ({
          item: +r.querySelector(".ii").value, qty: +r.querySelector(".iq").value }));
    }
    const r = await fetch(`/api/area/${side}/${id}/record/${st.grp}/${st.cid}${revQuery()}`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fields: out }) });
    if (!r.ok) { $("ce-msg").textContent = `save failed: ${(await r.text()).slice(0, 220)}`; return; }
    status(`grp${st.grp}[${st.cid}] saved`);
    box.innerHTML = "";
    await loadArea(side, id);
    await chainsLoad(side, id);
  });
}

// ═══════════════ DESIGN — a quest, top down ═══════════════
//
// The one view here that does not read up from the bytes. A step starts as words, gets a shape and
// a cast, and only becomes records when it is placed. Nothing on this screen is an id: a foe is a
// face and a name, an item is a name, a check is a skill you recognise. The numbers exist — they
// are just not the designer's problem.
let dqCat = null, dqQuests = null, dqSlug = null, dqIdx = null, dqPal = null, dqArea = null;

const dqPortrait = (id) => `<img class="dq-face" src="/api/portrait/1/${id}" alt="" loading="lazy">`;

async function dqInit() {
  if (!dqCat) dqCat = await (await fetch("/api/design/catalogue")).json();
  await dqLoadQuests();
  $("dq-new").onclick = dqNewQuest;
  $("dq-del").onclick = dqDeleteQuest;
}

async function dqLoadQuests() {
  dqQuests = await (await fetch("/api/quests")).json();
  const qs = (dqQuests.quests || []).slice().sort((a, b) => (a.name || "").localeCompare(b.name || ""));
  dqSlug = dqSlug || qs[0]?.id || null;
  dqRenderQuestList();
  dqRenderAxis();
  dqRenderMarkers();
  dqRenderSteps();
  dqRenderChecks();
}

// Everything that changes when the quest under the cursor changes. One function, because five call
// sites each remembering which four panels to repaint is how the axis came to disagree with the
// beat list beside it.
function dqShowQuest() {
  dqRenderQuestList(); dqRenderAxis(); dqRenderMarkers();
  dqRenderSteps(); dqRenderChecks(); dqRenderWork();
}

// The quest overview, beside the designer and grouped exactly as World -> Quests groups it: the
// main line by stage, then side quests by region. Designing without it meant choosing out of a
// dropdown with no sight of what else exists, what it depends on, or where it happens.
function dqRenderQuestList() {
  const box = $("dq-quests"); if (!box || !dqQuests) return;
  const by = Object.fromEntries((dqQuests.quests || []).map((q) => [q.id, q]));
  const row = (q, cls) => `<div class="q-row ${cls} ${q.id === dqSlug ? "on" : ""}" data-q="${fesc(q.id)}">
      <b>${fesc(q.name)}</b>${dqDesigned(q) ? ` <span class="dq-badge">designed</span>` : ""}
      <div class="muted small">${(q.steps || []).length} step${(q.steps || []).length === 1 ? "" : "s"}` +
      (q.areas?.length ? ` · ${fesc(q.areas.slice(0, 3).join(", "))}${q.areas.length > 3 ? "…" : ""}` : "") +
      `</div></div>`;
  let h = "", reg = null;
  const main = (dqQuests.main || []).map((id) => by[id]).filter(Boolean);
  if (main.length) {
    h += `<div class="q-region">Main line <span class="muted small">in order</span></div>`;
    let lvl = null;
    for (const q of main) {
      if (q.depth !== lvl) { lvl = q.depth; h += `<div class="q-stage">stage ${lvl}</div>`; }
      h += row(q, "main");
    }
    h += `<div class="q-region">Side quests</div>`;
  }
  for (const q of (dqQuests.quests || [])) {
    if (q.track === "main") continue;
    if (q.region !== reg) { reg = q.region; h += `<div class="q-sub">${fesc(reg || "—")}</div>`; }
    h += row(q, "");
  }
  box.innerHTML = h || `<div class="muted">no quests yet</div>`;
  box.querySelectorAll(".q-row[data-q]").forEach((el) => el.addEventListener("click", () => {
    dqSlug = el.dataset.q; dqIdx = null;
    dqShowQuest();
  }));
}

// ── the STATE AXIS ────────────────────────────────────────────────────────────────────────────
// The backbone of a quest, so it stands in the main column and not at the edge: how many stages
// there are, what they are called, and what the logbook says at each. The beats fall out of this.
//
// `open` is position 0 of EVERY quest. It is never declared, never carries a log line and costs no
// storage — a fresh save is all zeroes precisely because untouched means open — so it is shown,
// greyed, and cannot be edited or removed. `done` is the last forward state; `failed` stands beside
// the line rather than on it. The backend refuses anything that breaks those three rules, and the
// refusal is printed as written rather than re-worded here.
const AXIS_FIXED = { done: "the last state the quest moves forward to", failed: "beside the line, not on it" };

function dqRenderAxis() {
  const box = $("dq-axis"); if (!box) return;
  const q = dqQuest();
  if (!q) { box.innerHTML = ""; return; }
  const ax = q.axis || [];
  // eight states to the byte, `open` free (concept §8.6): ceil((n-1)/8) with n = declared + open
  const bytes = Math.ceil(ax.length / 8);
  const has = (id) => ax.some((s) => String(s.id).toLowerCase() === id);
  box.innerHTML = `<div class="dq-cap">States
      <span class="muted small">${ax.length ? `${ax.length + 1} incl. open · ${bytes} byte${bytes === 1 ? "" : "s"}`
                                            : "none yet — a quest without an axis is allowed"}</span></div>
    <div class="ax-row ax-open"><span class="ax-fixed">open</span>
      <span class="muted small">untouched — no log line, no storage</span></div>` +
    ax.map((s, i) => `<div class="ax-row" data-i="${i}">
        ${AXIS_FIXED[s.id] ? `<span class="ax-fixed" title="${fesc(AXIS_FIXED[s.id])}">${fesc(s.id)}</span>`
                           : `<input class="ax-id" data-i="${i}" value="${fesc(s.id)}"
                                     title="the name a gate and an advance call this stage by">`}
        <span class="ax-tools">
          <button class="mini" data-axmv="${i}" data-d="-1"${i === 0 ? " disabled" : ""}>↑</button>
          <button class="mini" data-axmv="${i}" data-d="1"${i === ax.length - 1 ? " disabled" : ""}>↓</button>
          <button class="mini" data-axrm="${i}" title="drop this stage">✕</button></span>
        <input class="ax-log" data-i="${i}" value="${fesc(s.log || "")}"
               placeholder="what the logbook says once this is reached">
      </div>`).join("") +
    `<div class="btns"><button class="mini" id="ax-add">＋ stage</button>` +
    (has("done") ? "" : `<button class="mini" id="ax-done">＋ done</button>`) +
    (has("failed") ? "" : `<button class="mini" id="ax-failed">＋ failed</button>`) +
    `</div><div id="ax-msg" class="muted small"></div>`;

  const read = () => [...box.querySelectorAll(".ax-row[data-i]")].map((r) => ({
    ...(ax[+r.dataset.i] || {}),
    id: (r.querySelector(".ax-id")?.value ?? r.querySelector(".ax-fixed")?.textContent ?? "").trim(),
    log: r.querySelector(".ax-log").value }));
  box.querySelectorAll(".ax-id, .ax-log").forEach((el) =>
    el.addEventListener("change", () => dqSaveAxis(read())));
  box.querySelectorAll("[data-axmv]").forEach((el) => el.addEventListener("click", () => {
    const i = +el.dataset.axmv, d = +el.dataset.d, l = read();
    if (i + d < 0 || i + d >= l.length) return;
    [l[i], l[i + d]] = [l[i + d], l[i]];
    dqSaveAxis(l);
  }));
  box.querySelectorAll("[data-axrm]").forEach((el) => el.addEventListener("click", () => {
    const l = read(), gone = l[+el.dataset.axrm];
    // A stage that a gate or an advance names is not a line in a list — deleting it breaks
    // whatever pointed at it, and `validate` will say so rather than this dialog guessing.
    if (!confirm(`Drop the stage “${gone.id}”? Anything that gates on it or advances to it will `
               + `be reported as pointing nowhere.`)) return;
    l.splice(+el.dataset.axrm, 1); dqSaveAxis(l);
  }));
  const add = (id) => { const l = read(); l.push({ id, log: "" }); dqSaveAxis(l); };
  $("ax-add").onclick = () => {
    // s1, s2, s3 … the shape the spec's example uses, and the next free one rather than the count:
    // deleting s2 out of four stages must not offer s4 a second time.
    let n = 1; while (has(`s${n}`)) n++;
    add(`s${n}`);
  };
  if ($("ax-done")) $("ax-done").onclick = () => add("done");
  if ($("ax-failed")) $("ax-failed").onclick = () => add("failed");
}

async function dqSaveAxis(axis) {
  const q = dqQuest(); if (!q) return;
  const r = await fetch(`/api/quest/${encodeURIComponent(q.id)}`, { method: "PUT",
    headers: { "content-type": "application/json" }, body: JSON.stringify({ axis }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { $("ax-msg").innerHTML = `<span class="gate-err">${fesc(j.detail || `error ${r.status}`)}</span>`; return; }
  dqQuests = j;
  dqRenderAxis(); dqRenderChecks();
  // the mask's state lists come off the axis, so they are re-drawn — but only they. Rebuilding the
  // whole work pane here would throw away whatever is typed into a hole and not yet saved.
  const st = dqQuest()?.steps?.[dqIdx];
  if (st && $("dq-bind")) dqRenderBind(st);
  status(`${q.name}: ${axis.length} stage${axis.length === 1 ? "" : "s"}`);
}

// ── MARKERS ───────────────────────────────────────────────────────────────────────────────────
// What the quest points at by name when the questlog itself is not the natural place: an item the
// player carries, a word they can say, a square they can stand on. The editor hands out the item id
// (there are exactly three spare in the whole game) and checks a word against every keyword the
// game already answers to — both refusals come from the backend and are printed as written.
const MARKER_KINDS = ["item", "word", "cell"];

function dqRenderMarkers() {
  const box = $("dq-markers"); if (!box) return;
  const q = dqQuest();
  if (!q) { box.innerHTML = ""; return; }
  const mk = q.markers || [];
  const val = (m) => {
    if (m.kind === "word") return `<input class="mk-word" value="${fesc(m.word || "")}" placeholder="one word, said nowhere else">`;
    if (m.kind === "cell") { const a = m.at || {};
      return `<span class="mk-at">s<input class="mk-s" type="number" value="${a.side ?? 1}">
        a<input class="mk-a" type="number" value="${a.area ?? ""}">
        (<input class="mk-c" type="number" value="${a.col ?? ""}">,<input class="mk-r" type="number" value="${a.row ?? ""}">)</span>`; }
    return `<input class="mk-item" type="number" min="0" max="255" value="${m.item ?? ""}"
                   placeholder="auto" title="leave it empty and a free item id is handed out">`;
  };
  box.innerHTML = `<div class="dq-cap">Markers <span class="muted small">${mk.length || "none"}</span></div>` +
    mk.map((m, i) => `<div class="mk-row" data-i="${i}">
        <input class="mk-id" value="${fesc(m.id || "")}" placeholder="TOY">
        <select class="mk-kind">${MARKER_KINDS.map((k) =>
          `<option${k === m.kind ? " selected" : ""}>${k}</option>`).join("")}</select>
        <button class="mini" data-mkrm="${i}">✕</button>
        <div class="mk-val">${val(m)}</div></div>`).join("") +
    `<div class="btns"><button class="mini" id="mk-add">＋ marker</button></div>
     <div id="mk-msg" class="muted small"></div>`;

  const read = () => [...box.querySelectorAll(".mk-row")].map((r) => {
    const kind = r.querySelector(".mk-kind").value;
    const m = { id: r.querySelector(".mk-id").value.trim(), kind };
    if (kind === "word") m.word = r.querySelector(".mk-word").value.trim();
    else if (kind === "cell") m.at = { side: +r.querySelector(".mk-s").value, area: +r.querySelector(".mk-a").value,
                                       col: +r.querySelector(".mk-c").value, row: +r.querySelector(".mk-r").value };
    else if (r.querySelector(".mk-item").value !== "") m.item = +r.querySelector(".mk-item").value;
    return m;
  });
  box.querySelectorAll("input, select").forEach((el) =>
    el.addEventListener("change", () => dqSaveMarkers(read())));
  box.querySelectorAll("[data-mkrm]").forEach((el) => el.addEventListener("click", () => {
    const l = read(); l.splice(+el.dataset.mkrm, 1); dqSaveMarkers(l);
  }));
  $("mk-add").onclick = () => dqSaveMarkers([...read(), { id: `M${mk.length + 1}`, kind: "item" }]);
}

async function dqSaveMarkers(markers) {
  const q = dqQuest(); if (!q) return;
  const r = await fetch(`/api/quest/${encodeURIComponent(q.id)}`, { method: "PUT",
    headers: { "content-type": "application/json" }, body: JSON.stringify({ markers }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { $("mk-msg").innerHTML = `<span class="gate-err">${fesc(j.detail || `error ${r.status}`)}</span>`; return; }
  dqQuests = j;
  dqRenderMarkers(); dqRenderChecks();
}

// ── THE BATTERY ───────────────────────────────────────────────────────────────────────────────
// /api/quest/{slug}/validate, under the beat list it is about. Three levels and they mean different
// things: an ERROR is a quest that cannot work (an advance to a stage nobody declared), a WARNING is
// one that works and probably is not what you meant, INFO is arithmetic. Questfreedom is never
// remarked on — a beat with no quest is correct, and the battery does not mention it.
let dqCheckFor = null;

async function dqRenderChecks() {
  const box = $("dq-checks"); if (!box) return;
  const q = dqQuest();
  if (!q) { box.innerHTML = ""; return; }
  const want = q.id;
  dqCheckFor = want;
  box.innerHTML = `<div class="dq-cap">Checks <span class="muted small">reading…</span></div>`;
  const r = await fetch(`/api/quest/${encodeURIComponent(want)}/validate`);
  if (dqCheckFor !== want) return;                 // the quest moved on while this was in flight
  if (!r.ok) {
    box.innerHTML = `<div class="dq-cap">Checks</div>
      <div class="muted small">not checkable yet — this quest is not in the store</div>`;
    return;
  }
  const v = await r.json();
  const line = (e, cls) => `<div class="ck ck-${cls}"${e.step != null ? ` data-step="${e.step}"` : ""}>
      <span class="ck-k">${fesc(e.check)}</span> ${fesc(e.detail)}</div>`;
  const n = (v.errors || []).length + (v.warnings || []).length;
  box.innerHTML = `<div class="dq-cap">Checks <span class="muted small">${
      n ? `${(v.errors || []).length} error${(v.errors || []).length === 1 ? "" : "s"} · ${(v.warnings || []).length} warning${(v.warnings || []).length === 1 ? "" : "s"}`
        : "nothing to report"}</span></div>` +
    (v.errors || []).map((e) => line(e, "err")).join("") +
    (v.warnings || []).map((e) => line(e, "warn")).join("") +
    (v.info || []).map((e) => line(e, "info")).join("");
  box.querySelectorAll(".ck[data-step]").forEach((el) => el.addEventListener("click", () => {
    dqIdx = +el.dataset.step; dqRenderSteps(); dqRenderWork();
  }));
}

// A quest that has at least one step this editor authored rather than found.
function dqDesigned(q) { return (q.steps || []).some((s) => s.blueprint); }

async function dqNewQuest() {
  const name = prompt("What is the quest called?");
  if (!name) return;
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 48);
  // the existing single-quest endpoint (/api/quest/<id>), not a new one — a second create path
  // would be a second place for a quest to come into being
  const r = await fetch(`/api/quest/${slug}`, { method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name, links: [], steps: [] }) });
  if (!r.ok) { status(`could not create: ${await r.text()}`); return; }
  dqSlug = slug; dqIdx = null;
  await dqLoadQuests(); dqRenderWork();
}


// Deleting a quest drops the PLAN, never the game. A step that was already placed left real records
// behind, and those stay exactly where they are — removing them is a separate act on the area,
// because a record nothing knows is unreferenced is how a chain gets a hole in it. So the button
// says which of the two it is about to do.
async function dqDeleteQuest() {
  const q = dqQuest(); if (!q) return;
  const placed = (q.steps || []).filter((s) => s.ref && s.placed).length;
  const msg = placed
    ? `Delete the plan for “${q.name}”?\n\n${placed} step(s) are already written into the game. ` +
      `Those records STAY — this only removes the quest and its plan. Unplacing them is a separate job.`
    : `Delete “${q.name}”? Nothing of it was written into the game yet, so this removes all of it.`;
  if (!confirm(msg)) return;
  const r = await fetch(`/api/quest/${encodeURIComponent(q.id)}`, { method: "DELETE" });
  if (!r.ok) { status(`could not delete: ${await r.text()}`); return; }
  dqSlug = null; dqIdx = null;
  await dqLoadQuests(); dqRenderWork();
  status(placed ? `plan deleted — ${placed} placed step(s) are still in the game` : "quest deleted");
}


// Take a blueprint from the library INTO a quest: pick the quest, add a step already cast from it,
// and open that step. The whole point of a catalogue is that choosing from it does something.
async function dqStartWith(bpId, bpName) {
  setPane("design", "designer");
  if (!dqCat) dqCat = await (await fetch("/api/design/catalogue")).json();
  if (!dqQuests) await dqLoadQuests();
  const qs = (dqQuests.quests || []);
  const names = qs.map((q, i) => `${i + 1} ${q.name}`).join("\n");
  const pick = prompt(`Add "${bpName}" as a step to which quest?\n\n${names}\n\n` +
                      `Type a number, or a NEW name to start a quest.`, "");
  if (pick === null) return;
  let slug = null;
  const n = parseInt(pick, 10);
  if (String(n) === pick.trim() && qs[n - 1]) {
    slug = qs[n - 1].id;
  } else if (pick.trim()) {
    slug = pick.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 48);
    const r = await fetch(`/api/quest/${slug}`, { method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name: pick.trim(), links: [], steps: [] }) });
    if (!r.ok) { status(`could not create the quest: ${await r.text()}`); return; }
  } else { return; }
  const r2 = await fetch(`/api/quests/${slug}/steps`, { method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ title: bpName, blueprint: bpId }) });
  if (!r2.ok) { status(`could not add the step: ${await r2.text()}`); return; }
  dqSlug = slug; dqQuests = null;
  await dqLoadQuests();
  dqIdx = (dqQuest()?.steps || []).length - 1;
  dqShowQuest();
  status(`"${bpName}" added to ${dqQuest()?.name} — fill it in and place it`);
}

function dqQuest() { return (dqQuests?.quests || []).find((q) => q.id === dqSlug); }

// The left rail: the quest as a list of steps, each showing HOW FAR ALONG it is. Three states, and
// they are the whole model — words, cast, placed.
function dqRenderSteps() {
  const box = $("dq-steps"); const q = dqQuest();
  if (!q) { box.innerHTML = `<div class="muted">no quests yet</div>`; return; }
  const steps = q.steps || [];
  box.innerHTML = `<div class="dq-qname">${fesc(q.name || dqSlug)}</div>` +
    (steps.length ? steps.map((st, i) => {
      // FOUND is not PLACED. A step that carries a reference but no blueprint came out of the
      // shipped game — somebody annotated it onto records that have been there since 1988. Calling
      // that "placed" claims this editor wrote it, which is the one thing the badge exists to say.
      const state = st.ref ? (st.blueprint ? "placed" : "found")
                           : (st.blueprint ? "cast" : "sketch");
      const bp = dqFindBp(st.blueprint);
      const where = st.placed?.cell ? `(${st.placed.cell.join(",")})` : "";
      const WHAT = { found: "from the original game", placed: "written by this editor",
                     cast: "ready to place", sketch: "words only" };
      // The face is the fastest way to recognise a step you already cast. A portrait id is not.
      const faces = dqFacesOf(st);
      // What this beat does to the axis, on the beat — the two halves of a quest are the states and
      // the beats that move between them, and a rail that shows only one of them shows half a quest.
      const gate = (st.gates || []).find((g) => g && g.state);
      const tags = (gate ? `<span class="dq-state dq-gate-tag">≥ ${fesc(gate.state)}</span>` : "")
        + (st.bind ? `<span class="dq-state dq-bind-tag">${st.bind.effect === "fail" ? "fail"
                        : `→ ${fesc(st.bind.to || "next")}`}</span>` : "");
      return `<div class="dq-step ${state}${i === dqIdx ? " active" : ""}" data-i="${i}">
        <span class="dq-n">${i + 1}</span>
        ${faces.map((f) => `<img class="dq-face dq-face-sm" src="/api/portrait/1/${f}" alt="" loading="lazy">`).join("")}
        <span class="dq-title">${fesc(st.title || st.note || "—")}</span>
        <div class="dq-meta">${fesc(bp?.name || WHAT[state])}
          <span class="dq-state dq-${state}">${state}</span> ${tags} ${where}</div></div>`;
    }).join("") : `<div class="muted small">no steps yet</div>`) +
    `<div class="btns"><button class="mini" id="dq-add">＋ step</button></div>`;
  box.querySelectorAll(".dq-step").forEach((el) =>
    el.addEventListener("click", () => { dqIdx = +el.dataset.i; dqRenderSteps(); dqRenderWork(); }));
  $("dq-add").onclick = async () => {
    const title = prompt("What happens in this step? (plain words)");
    if (!title) return;
    await fetch(`/api/quests/${dqSlug}/steps`, { method: "POST",
      headers: { "content-type": "application/json" }, body: JSON.stringify({ title }) });
    await dqLoadQuests(); dqIdx = (dqQuest().steps || []).length - 1; dqRenderSteps(); dqRenderWork();
  };
}


// Every portrait a step has been given, in the order its holes are declared — a foe's face, a
// shopkeeper's, a talker's. Used both in the step list and beside the hole that chose it.
function dqFacesOf(st) {
  const out = [];
  for (const v of Object.values(st.values || {})) {
    if (typeof v === "number" && v > 0) continue;              // could be anything; only take the two
    if (v && typeof v === "object" && v.portrait) out.push(v.portrait);
  }
  const bp = dqFindBp(st.blueprint);
  for (const h of (bp?.holes || [])) {
    if (h.kind === "portrait" && st.values?.[h.key]) out.push(st.values[h.key]);
  }
  return [...new Set(out)].slice(0, 3);
}

function dqFindBp(id) {
  for (const f of (dqCat?.families || [])) { const h = f.items.find((x) => x.id === id); if (h) return h; }
  return null;
}

// The work surface: pick a shape, fill it in, place it. In that order, because that is the order
// the decisions actually happen in.
async function dqRenderWork() {
  const box = $("dq-work"), q = dqQuest();
  if (!q || dqIdx == null || !q.steps?.[dqIdx]) { box.innerHTML = `<div class="muted">pick or add a step</div>`; return; }
  const st = q.steps[dqIdx];
  if (st.ref && !st.blueprint) {
    box.innerHTML = `<div class="dq-h">${fesc(st.title || "step")}</div>
      <div class="muted">This step is already in the game — <code>${fesc(st.ref)}</code>.
      Steps that came out of the shipped areas are edited in Events, not designed here.</div>`;
    return;
  }
  const bp = dqFindBp(st.blueprint);
  if (!bp) { box.innerHTML = dqCatalogueHtml(st); dqWireCatalogue(); return; }
  // The blueprint name is the heading when the step has not been renamed — printing both read as a
  // stutter ("Something guards a cache / Something guards a cache").
  const heading = (st.title && st.title !== bp.name) ? `${st.title} <span class="muted small">${fesc(bp.name)}</span>`
                                                     : fesc(bp.name);
  box.innerHTML = `<div class="dq-h">${heading}
      <button class="mini" id="dq-rename" title="give this step your own name">rename</button>
      <button class="mini" id="dq-rebp">change shape</button></div>
    <div class="dq-about">${fesc(bp.about || "")}${bp.seen ? ` <span class="muted small">— ${bp.seen}× in the game</span>` : ""}</div>
    <div class="dq-beats">${(bp.beats || []).map((b, i) =>
      `<span class="dq-beat">${i + 1} ${BEAT_NAME[({1:"SAY",2:"COND",3:"ENCOUNTER",5:"LOOT",6:"SERVICE",10:"TRANSITION"})[b.op]] || b.role}</span>`).join('<span class="dq-arrow">→</span>')}</div>
    <div id="dq-holes"></div>
    <div id="dq-bind"></div>
    <div class="dq-place" id="dq-place"></div>`;
  $("dq-rebp").onclick = () => { st.blueprint = null; box.innerHTML = dqCatalogueHtml(st); dqWireCatalogue(); };
  $("dq-rename").onclick = async () => {
    const t = prompt("What is this step called?", st.title || bp.name);
    if (t === null) return;
    await fetch(`/api/quests/${dqSlug}/steps/${dqIdx}`, { method: "PATCH",
      headers: { "content-type": "application/json" }, body: JSON.stringify({ title: t }) });
    st.title = t; await dqLoadQuests(); dqRenderSteps(); dqRenderWork();
  };
  // The palette is a big fetch (every area, every portrait, the budget) and took three seconds the
  // first time, during which the surface was simply blank. Say so.
  $("dq-holes").innerHTML = `<div class="muted small">reading what this area has to offer…</div>`;
  await dqRenderHoles(bp, st);
}

function dqCatalogueHtml(st) {
  return `<div class="dq-h">${fesc(st.title || "step")} <span class="muted small">pick a shape</span></div>` +
    (dqCat.families || []).map((f) => `<div class="dq-fam"><b>${fesc(f.label)}</b>
        <span class="muted small">${fesc(f.about)}</span></div>
      <div class="dq-cards">${f.items.map((b) => `<div class="dq-card" data-bp="${fesc(b.id)}">
        <div class="dq-card-n">${fesc(b.name || b.id)}</div>
        <div class="dq-card-a">${fesc(b.about || "")}</div>
        ${b.seen ? `<div class="dq-seen">${b.seen}× in the game</div>` : ""}</div>`).join("")}</div>`).join("");
}

function dqWireCatalogue() {
  $("dq-work").querySelectorAll(".dq-card").forEach((el) => el.addEventListener("click", async () => {
    await fetch(`/api/quests/${dqSlug}/steps/${dqIdx}`, { method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ blueprint: el.dataset.bp }) });
    await dqLoadQuests(); dqRenderSteps(); dqRenderWork();
  }));
}

// Which area we are designing INTO decides what the pickers can offer — its foes, how much text
// room is left. So the area comes first, and the budget is on screen the whole time rather than
// arriving as a refusal at the end.
async function dqRenderHoles(bp, st) {
  const holes = $("dq-holes"); const v = st.values || {};
  // A step that is already placed lives where it lives. Otherwise the CHOICE stands: `into …` used
  // to write dqArea and then have this line recompute the key from the Areas tab and overwrite it,
  // so picking a destination looked like it worked and the placement went somewhere else.
  await dqPalette(st.placed ? `${st.placed.side}:${st.placed.area_id}` : (dqArea || `1:${dqAreaId()}`));
  const b = dqPal.budget?.strings || {};
  $("dq-budget").innerHTML = `<span class="${b.free ? (b.free <= 8 ? "gate-warn" : "gate-ok") : "gate-err"}">${b.free} text id${b.free === 1 ? "" : "s"} free</span> here`;
  $("dq-where").innerHTML = `into <select id="dq-area">${(dqPal.areas || []).map((a) =>
    `<option value="${a.side}:${a.id}" ${dqArea === `${a.side}:${a.id}` ? "selected" : ""}>${fesc(a.name || ("area " + a.id))}</option>`).join("")}</select>`;
  $("dq-area").onchange = async () => { dqArea = $("dq-area").value; dqPal = null; await dqRenderHoles(bp, st); };

  // What is chosen NOW, shown beside the question — a face for a portrait or a foe, the name for a
  // service or an item. Without it the only feedback for "who guards it" was a highlighted square
  // in a grid of a hundred thumbnails.
  const chosen = (h) => {
    const val = v[h.key];
    if (h.kind === "portrait" && val) return `<img class="dq-face dq-face-lg" src="/api/portrait/1/${val}" alt="">`;
    if (h.kind === "foe" && val && (val.portrait || val.id)) {
      const pid = val.portrait || (dqPal.foes || []).find((f) => f.id === val.id)?.portrait;
      const nm = val.name || (dqPal.foes || []).find((f) => f.id === val.id)?.name || "";
      return (pid ? `<img class="dq-face dq-face-lg" src="/api/portrait/1/${pid}" alt="">` : "") +
             (nm ? `<span class="dq-chosen">${fesc(nm)}</span>` : "");
    }
    if (h.kind === "service_kind" && val)
      return `<span class="dq-chosen">${fesc((dqPal.services || []).find((x) => x.selector === val.selector)?.name || "")}</span>`;
    return "";
  };
  holes.innerHTML = dqHolesHtml(bp, v, chosen);
  dqWireHoles(holes, async () => { await dqSave(bp, st); await dqRenderHoles(bp, st); });
  dqRenderBind(st);
  dqRenderPlace(bp, st);
}

function dqAreaId() { const s = $("area-select")?.value; return s ? +s.split(":")[1] : 51; }

// ONE palette per area, shared by the designer and the Baukasten — it is a big fetch (every area,
// every portrait, the text budget) and both screens ask the same question of it.
async function dqPalette(key) {
  if (dqPal && dqArea === key) return dqPal;
  dqArea = key;
  const [s, a] = key.split(":");
  dqPal = await (await fetch(`/api/design/palette/${s}/${a}`)).json();
  return dqPal;
}

// The mask itself, without the designer around it: label, what is chosen now, the control. The
// Baukasten fills the same holes for a beat that belongs to no quest, so it renders the same thing
// rather than a second form that would drift from this one hole kind at a time.
function dqHolesHtml(bp, v, chosen) {
  return (bp.holes || []).map((h) => `<div class="dq-hole" data-h="${h.key}">
      <div class="dq-hl">${fesc(h.label)}${h.hint ? ` <span class="muted small">${fesc(h.hint)}</span>` : ""}
        <span class="dq-now">${chosen ? chosen(h) : ""}</span></div>
      <div class="dq-hv">${dqHoleControl(h, v[h.key])}</div></div>`).join("");
}

function dqHoleControl(h, val) {
  if (h.kind === "text")
    return `<input class="dq-in" data-k="${h.key}" value="${fesc(val && typeof val === "string" ? val : "")}" placeholder="write it as the player will read it">`;
  if (h.kind === "pages")
    return `<textarea class="dq-in dq-ta" data-k="${h.key}" rows="4" placeholder="one page per line">\n${fesc(Array.isArray(val) ? val.join("\n") : "")}</textarea>`;
  if (h.kind === "name")
    return `<input class="dq-in" data-k="${h.key}" maxlength="11" style="width:150px" value="${fesc(val || "")}" placeholder="max 11 chars">`;
  if (h.kind === "items") {
    const rows = (Array.isArray(val) && val.length ? val : [{ item: 93, qty: 1 }]);
    return `<div data-k="${h.key}" class="dq-items">` + rows.map((r) => dqItemRow(r)).join("") +
      `</div><button class="mini dq-item-add">＋ another thing</button>`;
  }
  if (h.kind === "foe") {
    const f = val || {};
    return `<div data-k="${h.key}" class="dq-foe">
      <select class="dq-foe-pick">
        <option value="">— someone new —</option>
        ${(dqPal.foes || []).map((x) => `<option value="${x.id}"${+f.id === x.id ? " selected" : ""}>${fesc(x.name)}${x.toughness?.hp_max ? ` · ${x.toughness.hp_min}-${x.toughness.hp_max} HP` : ""}</option>`).join("")}
      </select>
      <span class="dq-newfoe"${f.id ? " hidden" : ""}>
        <input class="dq-fn" placeholder="what it is called" maxlength="14" value="${fesc(f.name || "")}">
        ${dqFaceGrid(f.portrait)}
      </span>
      <label class="dq-chk"><input type="checkbox" class="dq-peaceful"${f.peaceful !== false ? " checked" : ""}> waits until you press E</label>
    </div>`;
  }
  if (h.kind === "check") {
    const c = val || { op: 0, arg: 9, difficulty: 2 };
    const sk = dqPal.skills?.skill || {}, at = dqPal.skills?.attribute || {};
    return `<div data-k="${h.key}" class="dq-check">
      <select class="dq-ck"><option value="0"${c.op === 0 ? " selected" : ""}>a skill</option>
        <option value="2"${c.op === 2 ? " selected" : ""}>an attribute</option></select>
      <select class="dq-ca">${Object.entries(c.op === 2 ? at : sk).map(([i, n]) =>
        `<option value="${i}"${+i === +c.arg ? " selected" : ""}>${fesc(n)}</option>`).join("")}</select>
      <span class="muted small">how hard</span>
      <select class="dq-cd">${[0, 1, 2, 3, 4, 5, 6].map((d) =>
        `<option value="${d}"${d === +c.difficulty ? " selected" : ""}>${["anyone with it", "easy", "fair", "tricky", "hard", "very hard", "brutal"][d]}</option>`).join("")}</select>
    </div>`;
  }
  if (h.kind === "portrait") return dqFaceGrid(val);
  if (h.kind === "service_kind")
    return `<select class="dq-in" data-k="${h.key}">${(dqPal.services || []).map((s) =>
      `<option value="${s.selector}"${(val?.selector ?? 9) === s.selector ? " selected" : ""}>${fesc(s.name)} — ${fesc(s.about.slice(0, 60))}</option>`).join("")}</select>`;
  if (h.kind === "area")
    return `<div data-k="${h.key}" class="dq-areahole">
      <select class="dq-at">${(dqPal.areas || []).map((a) =>
        `<option value="${a.id}"${(val?.target ?? -1) === a.id ? " selected" : ""}>${fesc(a.name || ("area " + a.id))}</option>`).join("")}</select>
      <span class="muted small">arrive at</span> x <input type="number" class="dq-ax" min="0" max="63" value="${val?.x ?? 1}" style="width:56px">
      y <input type="number" class="dq-ay" min="0" max="63" value="${val?.y ?? 1}" style="width:56px"></div>`;
  return `<input class="dq-in" data-k="${h.key}" value="${fesc(val ?? "")}">`;
}

function dqItemRow(r) {
  // "keeps it" / "N uses" comes from the item descriptor's charges byte — the thing that decides
  // whether a door eats the key. Worth knowing while you are choosing, not afterwards.
  const tag = (it) => it.charges === undefined ? ""
    : it.charges === 0 ? " — kept" : it.charges === 1 ? " — used up" : ` — ${it.charges} uses`;
  return `<div class="dq-item"><select class="dq-ii">${(dqPal.items || []).map((it) =>
    `<option value="${it.id}"${it.id === +r.item ? " selected" : ""}>${fesc(it.name + tag(it))}</option>`).join("")}</select>
    × <input type="number" class="dq-iq" min="1" max="9999" value="${r.qty || 1}" style="width:64px"></div>`;
}

// Faces, not numbers. Every portrait the game has, as thumbnails — the one thing that makes a foe
// feel like a foe while you are choosing it.
function dqFaceGrid(cur) {
  const ps = (dqPal.portraits || []).filter((p) => p.has_png).slice(0, 96);
  return `<div class="dq-faces">` + ps.map((p) =>
    `<img class="dq-face${+cur === p.str_id ? " sel" : ""}" data-p="${p.str_id}"
       src="/api/portrait/${p.side}/${p.str_id}" title="portrait ${p.str_id}" loading="lazy">`).join("") + `</div>`;
}

// `redraw` is what a skill/attribute switch has to do afterwards: the second list can only be
// filled once the first one is decided. The designer saves the step and re-reads it, the Baukasten
// has nothing to save into — so the caller says, and neither has to know about the other.
function dqWireHoles(box, redraw) {
  // The preview beside the question has to answer the click that caused it. Rendering it only when
  // the panel is built meant picking a face changed a border somewhere in a grid of a hundred
  // thumbnails and nothing else — the one place you were looking stayed empty.
  const echo = (holeEl) => {
    const now = holeEl.querySelector(".dq-now"); if (!now) return;
    const face = holeEl.querySelector(".dq-face.sel");
    const nameEl = holeEl.querySelector(".dq-fn");
    const sel = holeEl.querySelector(".dq-foe-pick");
    const known = sel?.value ? (dqPal.foes || []).find((f) => +f.id === +sel.value) : null;
    const pid = known ? known.portrait : (face ? +face.dataset.p : null);
    const nm = known ? known.name : (nameEl?.value || "");
    now.innerHTML = (pid ? `<img class="dq-face dq-face-lg" src="/api/portrait/1/${pid}" alt="">` : "") +
                    (nm ? `<span class="dq-chosen">${fesc(nm)}</span>` : "");
  };
  box.querySelectorAll(".dq-faces").forEach((g) => g.querySelectorAll(".dq-face").forEach((im) =>
    im.addEventListener("click", () => {
      g.querySelectorAll(".dq-face").forEach((o) => o.classList.remove("sel"));
      im.classList.add("sel");
      echo(g.closest(".dq-hole"));
    })));
  box.querySelectorAll(".dq-fn").forEach((i) => i.addEventListener("input", () => echo(i.closest(".dq-hole"))));
  box.querySelectorAll(".dq-foe-pick").forEach((s) => s.addEventListener("change", () => {
    s.parentElement.querySelector(".dq-newfoe").hidden = !!s.value;
    echo(s.closest(".dq-hole"));
  }));
  box.querySelectorAll(".dq-ck").forEach((s) => s.addEventListener("change", () => redraw()));
  box.querySelectorAll(".dq-item-add").forEach((b) => b.addEventListener("click", (ev) => {
    ev.preventDefault();
    b.previousElementSibling.insertAdjacentHTML("beforeend", dqItemRow({ item: 93, qty: 1 }));
  }));
}

function dqReadHoles(box, bp) {
  const out = {};
  for (const h of (bp.holes || [])) {
    const el = box.querySelector(`[data-h="${h.key}"] .dq-hv`);
    if (!el) continue;
    if (h.kind === "text" || h.kind === "name") out[h.key] = el.querySelector(".dq-in")?.value || "";
    else if (h.kind === "pages")
      out[h.key] = (el.querySelector(".dq-ta")?.value || "").split("\n").map((x) => x.trim()).filter(Boolean);
    else if (h.kind === "items")
      out[h.key] = [...el.querySelectorAll(".dq-item")].map((r) => ({
        item: +r.querySelector(".dq-ii").value, qty: +r.querySelector(".dq-iq").value }));
    else if (h.kind === "foe") {
      const id = el.querySelector(".dq-foe-pick").value;
      const face = el.querySelector(".dq-face.sel");
      out[h.key] = id ? { id: +id, peaceful: el.querySelector(".dq-peaceful").checked, count: 1 }
                      : { name: (el.querySelector(".dq-fn")?.value || "").toUpperCase(),
                          portrait: face ? +face.dataset.p : 0, glyph: 2,
                          peaceful: el.querySelector(".dq-peaceful").checked, count: 1 };
    } else if (h.kind === "check")
      out[h.key] = { op: +el.querySelector(".dq-ck").value, arg: +el.querySelector(".dq-ca").value,
                     difficulty: +el.querySelector(".dq-cd").value };
    else if (h.kind === "portrait") {
      const f = el.querySelector(".dq-face.sel"); out[h.key] = f ? +f.dataset.p : null;
    } else if (h.kind === "service_kind") out[h.key] = { selector: +el.querySelector(".dq-in").value };
    else if (h.kind === "area") out[h.key] = { target: +el.querySelector(".dq-at").value,
      x: +el.querySelector(".dq-ax").value, y: +el.querySelector(".dq-ay").value };
  }
  return out;
}

// ── GATE and EFFECT ───────────────────────────────────────────────────────────────────────────
// What the beat needs before it fires, and what it moves. BOTH MAY BE EMPTY, and empty is never
// drawn as a gap: a-sign appears 92 times in the shipped game and guarded-stash 95, none of them
// attached to a quest. Most of a world is furniture, and furniture with a quest field nagging at it
// is bureaucracy, not a game (concept §7.6).
//
// A gate goes on the step as `gates` — a LIST, and not `gate`, because the composed view already
// uses `gate` for the readable summary of a COND record's conditions. Two meanings on one key is
// exactly the `state`/`phase` collision this model was cleaned up to remove. wl_qvalidate reads
// `gate`, `gates` and `need` alike.
const DQ_EFFECTS = { advance: "moves the quest on", fail: "ends it as failed" };

function dqRenderBind(st) {
  const box = $("dq-bind"); if (!box) return;
  const q = dqQuest();
  const gate = (st.gates || []).find((g) => g && g.state) || null;
  const bind = st.bind || null;
  const others = (dqQuests?.quests || []).filter((x) => x.qid != null);
  const states = (x) => ["open", ...(x?.axis || []).map((s) => s.id)];
  const gq = gate ? (others.find((x) => x.qid === gate.quest) || q) : null;
  const bp = dqFindBp(st.blueprint);
  const roles = [...new Set((bp?.beats || []).map((b) => b.role).filter(Boolean))];
  box.innerHTML = `<div class="dq-cap">Quest <span class="muted small">a beat needs neither — most
      of the game is furniture, and furniture is not a defect</span></div>
    <div class="dq-bind">
      <label><span>Gate</span>
        <select id="dq-gate-q"><option value="">— none —</option>${others.map((x) =>
          `<option value="${x.qid}"${gq && x.qid === gq.qid ? " selected" : ""}>${fesc(x.name)}</option>`).join("")}</select>
        <span class="muted small">≥</span>
        <select id="dq-gate-s"${gate ? "" : " disabled"}>${states(gq).map((s) =>
          `<option${gate && s === gate.state ? " selected" : ""}>${fesc(s)}</option>`).join("")}</select>
      </label>
      <label><span>Effect</span>
        <select id="dq-eff"><option value="">— nothing —</option>${Object.entries(DQ_EFFECTS).map(([k, t]) =>
          `<option value="${k}"${bind?.effect === k ? " selected" : ""}>${k} — ${t}</option>`).join("")}</select>
        <span class="muted small" id="dq-eff-tolab"${bind?.effect === "advance" ? "" : " hidden"}>to</span>
        <select id="dq-eff-to"${bind?.effect === "advance" ? "" : " hidden"}>
          <option value="">— one stage on —</option>${(q?.axis || []).map((s) =>
            `<option${bind?.to === s.id ? " selected" : ""}>${fesc(s.id)}</option>`).join("")}</select>
        <span class="muted small" id="dq-eff-onlab"${bind ? "" : " hidden"}>carried by</span>
        <select id="dq-eff-on"${bind ? "" : " hidden"}>
          <option value="">— the last beat that can —</option>${roles.map((r) =>
            `<option value="${r}"${bind?.on === r ? " selected" : ""}${r === "service" ? " disabled" : ""}>${fesc(r)}${
              r === "service" ? " (a shop has no hand-over — bind the door)" : ""}</option>`).join("")}</select>
      </label>
      ${bind ? `<div class="muted small">Authored and checked. It does not compile yet: the engine
        has no questlog read/write path (spec §2.5), so placing this beat is refused with 501 until
        it does — everything else about the beat places normally.</div>` : ""}
    </div>`;
  const q0 = $("dq-gate-q"), s0 = $("dq-gate-s"), e0 = $("dq-eff");
  q0.onchange = () => {
    if (!q0.value) { dqSetStep(dqIdx, { gates: null }); return; }
    const t = others.find((x) => String(x.qid) === q0.value);
    // the first DECLARED stage, not `open`: every quest is open before anything happens, so a gate
    // on it is a gate that is always true — which reads like a condition and is not one
    const st = states(t);
    dqSetStep(dqIdx, { gates: [{ cond: "quest", quest: +q0.value, state: st[1] || st[0] }] });
  };
  s0.onchange = () => dqSetStep(dqIdx, { gates: [{ cond: "quest", quest: +q0.value, state: s0.value }] });
  const eff = () => {
    if (!e0.value) return dqSetStep(dqIdx, { bind: null });
    const b = { effect: e0.value };
    if (e0.value === "advance" && $("dq-eff-to").value) b.to = $("dq-eff-to").value;
    if ($("dq-eff-on").value) b.on = $("dq-eff-on").value;
    dqSetStep(dqIdx, { bind: b });
  };
  e0.onchange = eff;
  $("dq-eff-to").onchange = eff;
  $("dq-eff-on").onchange = eff;
}

// The composed step is the authored one PLUS what the record says right now — its op, its lines,
// the foe's face. Writing that back would freeze derived facts into quests.json where they would
// rot, so the derived half is subtracted BY NAME and everything else is kept, including fields this
// version of the editor knows nothing about.
const DQ_DERIVED = new Set(["ok", "why", "side", "area", "area_name", "grp", "cid", "op", "text",
  "paragraphs", "cells", "reached_from", "next", "foe", "keywords", "to_area", "patches",
  "rewrites", "gate", "actions", "role", "role_groups"]);

// A step's gate and its effect are fields of the STEP, and the only route that writes a step
// verbatim is PUT /api/quest/{id} with the whole list — the step endpoints take title, blueprint,
// values and note and would drop anything else. So the list goes back whole, merged rather than
// rebuilt: a rebuilt step is how an authored beat loses its blueprint.
async function dqSetStep(i, patch) {
  const q = dqQuest(); if (!q || !q.steps?.[i]) return;
  const steps = q.steps.map((s) => {
    const out = {};
    for (const [k, v] of Object.entries(s)) if (!DQ_DERIVED.has(k)) out[k] = v;
    return out;
  });
  for (const [k, v] of Object.entries(patch)) { if (v === null) delete steps[i][k]; else steps[i][k] = v; }
  const r = await fetch(`/api/quest/${encodeURIComponent(q.id)}`, { method: "PUT",
    headers: { "content-type": "application/json" }, body: JSON.stringify({ steps }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { status(`could not save the step: ${j.detail || r.status}`); return; }
  dqQuests = j;
  // NOT dqRenderWork(): that rebuilds the hole mask out of the store and would throw away whatever
  // is typed into it but not yet saved. Only the two panels this actually changed are redrawn.
  dqRenderSteps(); dqRenderChecks();
  const now = dqQuest()?.steps?.[i];
  if (now) dqRenderBind(now);
}

async function dqSave(bp, st) {
  const values = dqReadHoles($("dq-holes"), bp);
  const r = await fetch(`/api/quests/${dqSlug}/steps/${dqIdx}`, { method: "PATCH",
    headers: { "content-type": "application/json" }, body: JSON.stringify({ values }) });
  const j = await r.json();
  st.values = { ...(st.values || {}), ...values };
  return j.missing || [];
}

// Placing is the moment bytes first exist, so it says so, and it says what it made.
function dqRenderPlace(bp, st) {
  const box = $("dq-place");
  const done = !!st.ref && st.placed;
  box.innerHTML = done
    ? `<div class="dq-done">This is in the game. <b>${fesc(st.placed.cell ? `(${st.placed.cell.join(",")})` : "")}</b>
         ${(st.placed.records || []).map((r) => `<span class="dq-rec">${fesc(r.role)}</span>`).join(" ")}
         <button class="mini" id="dq-goto">show me</button></div>`
    : `<div class="dq-row"><button class="mini" id="dq-keep">Save the plan</button>
         <span class="muted small">or put it on a square:</span>
         x <input type="number" id="dq-col" min="0" max="63" value="20" style="width:56px">
         y <input type="number" id="dq-row" min="0" max="63" value="20" style="width:56px">
         <button class="mini dq-go" id="dq-place-go">Place it →</button></div>
       <div id="dq-msg" class="muted small"></div>`;
  if (done) {
    $("dq-goto").onclick = () => {
      const [s, a] = [st.placed.side, st.placed.area_id];
      $("area-select").value = `${s}:${a}`; $("area-select").dispatchEvent(new Event("change"));
      setMode("flow"); setTab("areas");
    };
    return;
  }
  $("dq-keep").onclick = async () => {
    const miss = await dqSave(bp, st);
    $("dq-msg").textContent = miss.length ? `still empty: ${miss.join(", ")}` : "saved — nothing written to the game yet";
    await dqLoadQuests(); dqRenderSteps();
    await dqRenderHoles(bp, dqQuest().steps[dqIdx]);   // show it back the way it was stored
  };
  $("dq-place-go").onclick = async () => {
    const miss = await dqSave(bp, st);
    if (miss.length) { $("dq-msg").innerHTML = `<span class="gate-err">still empty: ${miss.join(", ")}</span>`; return; }
    const [s, a] = (dqArea || "1:51").split(":");
    $("dq-msg").textContent = "writing…";
    // Through /api/design/place, the ONE compile path (spec §4.1, E2) — the same call the Baukasten
    // makes, with a `quest` on it. `step` names the step being filled in, so the placement replaces
    // it instead of appending a second one beside it, and the beat's own effect goes along: an
    // authored `advance` that the placement quietly dropped would be a lie the file agreed with.
    const r = await fetch(`/api/design/place`, { method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ side: +s, area: +a, blueprint: st.blueprint, values: st.values || {},
                             col: +$("dq-col").value, row: +$("dq-row").value,
                             quest: { slug: dqSlug, step: dqIdx, title: st.title, note: st.note,
                                      ...(st.bind || {}) } }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { $("dq-msg").innerHTML = dqRefusedHtml(j, r.status); return; }
    $("dq-msg").innerHTML = `<span class="gate-ok">written — ${(j.records || []).map((x) => fesc(x.role)).join(", ")} · +${j.size_delta} bytes</span>`
      + (j.warnings || []).map((w) => `<div class="gate-warn">${fesc(w)}</div>`).join("");
    await dqLoadQuests(); dqRenderSteps(); dqRenderChecks(); dqRenderWork();
  };
}

// A refusal is a LIST, because fixing one sentence and being told about the next one 422 at a time
// is how a designer gives up. `/api/design/place` answers the spec's own body — `{"refused": […]}`
// at the top level — because a raised HTTPException would have been wrapped in `detail` and a
// client reading §4.1 literally would find nothing. The wrapped shape is still read here: the
// older shells raise, and the per-quest shell answers one sentence as `detail`.
function dqRefusedHtml(j, code) {
  const d = j?.detail;
  const list = Array.isArray(d?.refused) ? d.refused : Array.isArray(j?.refused) ? j.refused : null;
  if (list) return list.map((e) =>
    `<div class="gate-err"><b>${fesc(e.check)}</b> — ${fesc(e.detail)}</div>`).join("");
  return `<span class="gate-err">${fesc(typeof d === "string" ? d : `refused (${code})`)}</span>`;
}
