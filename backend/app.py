"""Wasteland Editor — FastAPI backend.

Serves the editor UI (static) and a JSON API over the byte-exact area/tile codecs. Runs the same
whether launched locally (dev) or inside the Apple Container / Docker image.

Data root = the Wasteland repo checkout, from $WL_REPO (default: repo root when run from
editor/backend/). In the container it is the mounted /data volume. Edits go to WORK (editor/work),
NEVER into baseline/ (pristine).

Two files back one area:
  - record blob  s{side}_record_id{id}.bin  → op-type nibble + descriptor (width, fill tile)
  - map blob     s{side}_map_id{id}.bin      → the visible per-cell TILE ids (paintable)
The tileset pixels come from the gfx blob s{side}_gfx_id{gid}.bin (decoded by wl_tiles).
"""
from __future__ import annotations
import base64
import json
import os
import re
import hashlib
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from wl_area import load_area, save_area, load_map_tileids, save_map_tileids
from wl_machine import RECORD_BASE as RAM, DATA, MACHINE   # $3400 on the C64, $D000 on the Apple
import wl_tiles
import wl_tiles_a2                     # the Apple II tileset: a second format, not a constant
import wl_portrait_a2                  # and its portraits: two fields, not one raster
import wl_cell
import wl_grp16
import wl_ir
import wl_item
import wl_object
import wl_party
import wl_persist
import wl_quest
import wl_blueprint
import wl_foe
import wl_story
import wl_view
import wl_place
import wl_questlog
import wl_qvalidate
import wl_scene_registry
from wl_cell import inspect_op, OP_NAMES as _OP_NAMES

HERE = Path(__file__).resolve().parent
# ── where the data lives ─────────────────────────────────────────────────────────────
# NOTHING in this repository is game data. Everything the editor reads is generated on the
# user's own machine from their own disk images by `python3 bootstrap.py` (see README), and
# all of it lands under ONE root so it can be git-ignored as a whole and deleted in one go.
#
#   data/baseline/sideN/   the extracted, depacked assets — the read-only reference
#   data/work/sideN/       the editable copies the editor writes
#   data/assets/           names, paragraphs, teleport table, title graphics
#   data/names.json        item + skill names, decoded from the user's own engine blob
#
# WL_DATA moves the whole root (a Docker volume, a second game version, a scratch copy).
# wl_machine resolves it, because the root also carries which machine it came from and the two
# must not be able to disagree.
REPO = DATA                                  # legacy alias: paths below are DATA-relative
WORK = Path(os.environ.get("WL_WORK", DATA / "work")).resolve()
ASSETS = Path(os.environ.get("WL_ASSETS", DATA / "assets")).resolve()
def _atlas(raw: bytes, **kw) -> dict:
    """Render a REAL tileset blob, in whichever format this data root's machine stores it.

    Only for bytes that came off a gfx asset. The blueprint art preview builds a SYNTHETIC C64
    blob (bitmap + colour table) and renders it with wl_tiles directly — putting that through
    here would hand C64 colour bytes to the Apple decoder."""
    return (wl_tiles_a2 if MACHINE == "apple" else wl_tiles).build_atlas(raw, **kw)


RECORD_CAP = 9728
_FLOW_CACHE: dict = {}          # (side, area, record-rev) -> flow; rev changes on every save          # largest original record; records load at $3400, bigger risks the build
FRONTEND = HERE.parent / "frontend"

# VERSION: major.minor.PATCH — bump the patch on every bug fix and every small change
# (Alex, 2026-07-30). This line is the ONLY place it is written: `index()` substitutes it into
# index.html's `__WL_VERSION__`, which is the header badge AND the `?v=` on main.js/style.css.
# It used to be duplicated in three places in the HTML, and 0.8.136 shipped with all three still
# saying 0.8.135 — so the browser kept its cached main.js and the fix was invisible.
app = FastAPI(title="Wasteland Editor", version="0.9.32")
wl_cell.configure(REPO)


# ── auto-commit: every successful editor save → a git commit (+push) on WL_GIT_BRANCH ──
# WL_GIT_AUTOCOMMIT=1 turns it on. A single background worker serializes commits and
# coalesces bursts (a save that fans out to several writes = one commit). Push uses the
# mounted deploy key (WL_GIT_KEY); commits are made even if push fails (offline-safe).
import queue as _queue
import subprocess as _sub
import threading as _thr

_GIT_ON = os.environ.get("WL_GIT_AUTOCOMMIT", "") == "1"
_GIT_BRANCH = os.environ.get("WL_GIT_BRANCH", "nas-work")
_GIT_KEY = os.environ.get("WL_GIT_KEY", "")
_GIT_Q: "_queue.Queue[str]" = _queue.Queue()


def _git(*args, check=False):
    env = dict(os.environ)
    if _GIT_KEY:
        env["GIT_SSH_COMMAND"] = f"ssh -i {_GIT_KEY} -o StrictHostKeyChecking=accept-new"
    # container runs as root, /data is owned by the NAS user -> declare it safe
    return _sub.run(["git", "-c", f"safe.directory={REPO}", "-C", str(REPO), *args],
                    env=env, capture_output=True, text=True, check=check)


def _git_worker():
    while True:
        msg = _GIT_Q.get()
        # coalesce everything queued right now into one commit
        n = 1
        try:
            while True:
                _GIT_Q.get_nowait(); n += 1
        except _queue.Empty:
            pass
        try:
            _git("add", "-A")
            st = _git("status", "--porcelain")
            if not st.stdout.strip():
                continue                      # nothing staged
            label = msg if n == 1 else f"{msg} (+{n-1} more)"
            _git("-c", "user.name=wl-editor", "-c", "user.email=wl-editor@nas",
                 "commit", "-m", f"editor save: {label}")
            _git("push", "origin", _GIT_BRANCH)   # best-effort; commit already made
        except Exception:
            pass                              # never let git break a save


if _GIT_ON:
    _thr.Thread(target=_git_worker, daemon=True).start()


def _git_state(fetch: bool = False) -> dict:
    """Where am I + how does my git stand vs origin — for the header badge, so NAS and
    local dev are never confused again."""
    import socket
    st = {"host": os.environ.get("WL_HOST_LABEL") or socket.gethostname(),
          "autocommit": _GIT_ON, "branch": None, "remote": None,
          "ahead": None, "behind": None, "clean": None, "error": None}
    try:
        st["branch"] = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or None
        st["remote"] = (_git("remote", "get-url", "origin").stdout.strip() or None)
        if fetch:
            _git("fetch", "origin", st["branch"] or "")
        st["clean"] = not _git("status", "--porcelain").stdout.strip()
        up = f"origin/{st['branch']}"
        a = _git("rev-list", "--count", f"{up}..HEAD").stdout.strip()
        b = _git("rev-list", "--count", f"HEAD..{up}").stdout.strip()
        st["ahead"] = int(a) if a.isdigit() else None
        st["behind"] = int(b) if b.isdigit() else None
    except Exception as e:
        st["error"] = str(e)
    return st


# ── per-asset history: every editor save is already a git commit (see _git_worker); these
# surface that history for ONE work file and restore any earlier version of it. Generic core —
# reused by the tileset endpoints below and (later) by maps/records; all keyed by a repo-rel path.
def _git_file_log(rel: str, limit: int = 40) -> list[dict]:
    """Commits that touched this work file, newest first: [{rev, short, time(ISO), label}]."""
    r = _git("log", f"--max-count={limit}", "--format=%H%x1f%cI%x1f%s", "--", rel)
    out = []
    for line in r.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            h, t, s = parts
            out.append({"rev": h, "short": h[:9], "time": t, "label": s})
    return out


def _git_show_bytes(rev: str, rel: str) -> bytes | None:
    """Raw bytes of a work file at a past revision (git show <rev>:<rel>). None if absent there."""
    env = dict(os.environ)
    if _GIT_KEY:
        env["GIT_SSH_COMMAND"] = f"ssh -i {_GIT_KEY} -o StrictHostKeyChecking=accept-new"
    r = _sub.run(["git", "-c", f"safe.directory={REPO}", "-C", str(REPO), "show", f"{rev}:{rel}"],
                 env=env, capture_output=True)      # NO text= : binary .bin content
    return r.stdout if r.returncode == 0 else None


@app.get("/api/status")
def api_status() -> dict:
    return _git_state(fetch=False)


@app.post("/api/git-sync")
def api_git_sync() -> dict:
    """Fetch origin and report ahead/behind. If the tree is CLEAN and we're behind,
    fast-forward pull; never touch a dirty tree (would risk unsaved edits)."""
    st = _git_state(fetch=True)
    if _GIT_ON and st.get("clean") and st.get("behind"):
        pull = _git("merge", "--ff-only", f"origin/{st['branch']}")
        st["pulled"] = pull.returncode == 0
        st = _git_state(fetch=False)
        st["pulled"] = True
    return st


@app.middleware("http")
async def _autocommit(request, call_next):
    resp = await call_next(request)
    # PATCH belongs in this list: the designer's most frequent write — filling a blueprint's holes
    # (`PATCH /api/quests/{slug}/steps/{idx}`) — is one, and without it a session that ends on a
    # hole-fill leaves that work unversioned until the next POST/PUT sweeps it up.
    if _GIT_ON and request.method in ("POST", "PATCH", "DELETE", "PUT") \
            and request.url.path.startswith("/api/") and resp.status_code < 400 \
            and "/undo" not in request.url.path:
        _GIT_Q.put(f"{request.method} {request.url.path}")
    return resp


@app.middleware("http")
async def _no_cache(request, call_next):
    """Dev tool — never let the browser cache the UI/API (Safari was serving a stale main.js)."""
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


# ── access control (NAS/remote deployment) — two roles ────────────────
# WL_AUTH_EDIT="till:pass"  full access (comma-separate for several users)
# WL_AUTH_VIEW="trex:pass"  read-only: GET/HEAD only, every mutating call -> 403
# Either set -> HTTP Basic auth is on. LAN/loopback clients skip auth (= edit; Alex at
# home) unless WL_AUTH_FORCE=1 — set that behind a reverse proxy (its LAN IP would
# otherwise open the door for the world). Nothing set -> everything open (local dev).
_AUTH_EDIT = [c for c in os.environ.get("WL_AUTH_EDIT", os.environ.get("WL_AUTH", "")).split(",") if c]
_AUTH_VIEW = [c for c in os.environ.get("WL_AUTH_VIEW", "").split(",") if c]
_AUTH_FORCE = os.environ.get("WL_AUTH_FORCE", "") == "1"


def _is_lan(host: str) -> bool:
    import ipaddress
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def _basic_role(request) -> str | None:
    """'edit' | 'view' from the Basic header, None = bad/missing credentials."""
    import base64 as b64
    import secrets as sec
    hdr = request.headers.get("authorization", "")
    if not hdr.startswith("Basic "):
        return None
    try:
        cred = b64.b64decode(hdr[6:]).decode()
    except Exception:
        return None
    for c in _AUTH_EDIT:
        if sec.compare_digest(cred, c):
            return "edit"
    for c in _AUTH_VIEW:
        if sec.compare_digest(cred, c):
            return "view"
    return None


@app.middleware("http")
async def _basic_auth(request, call_next):
    if (_AUTH_EDIT or _AUTH_VIEW) and (_AUTH_FORCE or not _is_lan(request.client.host if request.client else "")):
        from fastapi.responses import Response
        role = _basic_role(request)
        if role is None:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="wl-editor"'})
        if role == "view" and request.method not in ("GET", "HEAD", "OPTIONS"):
            return Response('{"detail":"view-only account — editing not allowed"}',
                            status_code=403, media_type="application/json")
    return await call_next(request)


# ── the Apple II release is read-only ────────────────────────────────────────────────
# Reading it is proven: all 42 shared area records decode to the same grids and the same
# prose as the C64's, once the record window moves to $D000. WRITING it is not. A save
# rebuilds the record and relocates the text region to the END of the blob, and whether a
# blob may grow there when it loads at $D000 is a fact about the Apple engine — which
# nobody has disassembled. The serializer's own check cannot catch it: it proves the
# record decodes back identically, not that the machine can still load it.
#
# So the editor shows everything and changes nothing. This is a deliberate stop, not a
# missing feature; the way to lift it is to read the Apple engine, not to delete this.
# See docs/APPLE2.md, 'Open'.
@app.middleware("http")
async def _machine_readonly(request, call_next):
    if MACHINE != "c64" and request.method not in ("GET", "HEAD", "OPTIONS"):
        from fastapi.responses import Response
        return Response(
            '{"detail":"this is an Apple II data root — read-only. The write path has only '
            'ever been proven at the C64\'s $3400 record window; at the Apple\'s $D000 it is '
            'unverified. See docs/APPLE2.md."}',
            status_code=403, media_type="application/json")
    return await call_next(request)

# ── Play / Build: REMOVED in the standalone editor ───────────────────────────────────
# The upstream tree carries ~460 lines here: a WebSocket reverse-proxy to the trx64
# emulator sidecar, the /api/play/* machine controls, and a build worker that shells out
# to the cartridge build. All three belong to the cart project, not to the editor, and
# they are the only place the editor reached into it. Nothing else referenced them.


_REC_RE = re.compile(r"^s(\d)_record_id(\d+)\.bin$")


def _baseline_dir(side: int) -> Path:
    return REPO / "baseline" / f"side{side}"


_NAMES: dict[int, str] | None = None


def _area_name(area_id: int) -> str:
    """area-id → display name from cart_EF/assets/areanames/area_names.json (keyed by area id)."""
    global _NAMES
    if _NAMES is None:
        _NAMES = {}
        import json
        f = ASSETS / "areanames" / "area_names.json"
        if f.is_file():
            try:
                for k, v in json.loads(f.read_text()).items():
                    if k.isdigit() and isinstance(v, dict) and v.get("name"):
                        _NAMES[int(k)] = v["name"]
            except (ValueError, KeyError):
                pass
    return _NAMES.get(area_id, "")


def _resolve(side: int, kind: str, num: int) -> tuple[Path, str]:
    """Prefer an edited working copy of s{side}_{kind}_id{num}.bin; fall back to pristine baseline.

    For records the JSON-IR is the source of truth: a .json that is NEWER than its .bin gets
    compiled first, so hand-editing the IR in a text editor is a supported way to work."""
    name = f"s{side}_{kind}_id{num:02d}.bin"
    wf = WORK / f"side{side}" / name
    if kind == "record":
        try:
            wl_persist.sync_from_json(wf, side, num)
        except Exception as ex:                       # a broken hand-edit must not kill the load
            print(f"[wl_persist] {name}: {ex}")
    if wf.is_file():
        return wf, "work"
    return _baseline_dir(side) / name, "baseline"


def _load_areatrack(side: int, track: int):
    """Feed wl_cell the shop stock/price table so the service panel can resolve what a shop sells."""
    try:
        pth, _ = _resolve(side, "areatrack", track)
        return pth.read_bytes() if pth.is_file() else None
    except Exception:
        return None


wl_cell.AREATRACK_LOADER = _load_areatrack


def _work_path(side: int, kind: str, num: int) -> Path:
    return WORK / f"side{side}" / f"s{side}_{kind}_id{num:02d}.bin"


def _cond_summary_ir(area, ir):
    """cond_summary for an IR COND record — the condition LABELS still come from wl_cell."""
    idx = {id(v): int(k) for k, v in ir["groups"].get("2", {}).items()}

    def f(rr):
        cid = idx.get(id(rr))
        rec = wl_cell.subptr(area.raw, area.slot, 2, cid) if cid is not None else None
        return wl_cell._cond_summary(area, rec + 0x0A) if rec is not None else "always"
    return f


_IR_CACHE: dict = {}


def _ir(side: int, area_id: int) -> dict:
    """The area's JSON-IR — read from the .json when the editor already wrote one, else decoded.

    Cached per (path, mtime, size): the derived views (chains, mutations, transitions) all run off
    this, so decoding once per revision instead of once per view is where the win is. Loading the
    stored JSON is ~15x cheaper again than decoding the bytes (0.29 ms vs 4.5 ms on Highpool)."""
    pth, _ = _resolve(side, "record", area_id)
    js = Path(str(pth)[:-4] + ".json")
    src = js if js.is_file() else pth
    try:
        st = src.stat()
        key = (str(src), st.st_mtime_ns, st.st_size)
    except OSError:
        key = (str(src), 0, 0)
    hit = _IR_CACHE.get(key)
    if hit is None:
        hit = json.loads(js.read_text()) if src is js else None
        # …unless that document predates the current decoder. Then the BYTES are the truth: a stale
        # shape is worse than a slow decode, because it is wrong quietly (Highpool's encounters read
        # as `msg: null` for every view that took the .json).
        if hit is not None and (hit.get("meta") or {}).get("ir_version") != wl_ir.IR_VERSION:
            print(f"[ir] s{side}a{area_id}: stored IR is version "
                  f"{(hit.get('meta') or {}).get('ir_version')}, decoder is {wl_ir.IR_VERSION} — re-decoding")
            hit = None
        if hit is None:
            hit = wl_ir.record_to_ir(pth.read_bytes(), side, area_id)
        if len(_IR_CACHE) > 64:
            _IR_CACHE.clear()
        _IR_CACHE[key] = hit
    return hit


def _save_record(side: int, area_id: int, data) -> Path:
    """THE record save path — every write goes through the serializer (wl_persist.save_record).

    Whatever bytes an endpoint produced are decoded to the JSON-IR and re-serialized, so the layout
    is rebuilt from scratch and the result is a fixpoint: the byte-surgery failure modes (over-read,
    orphan growth, phantom pointers, extent guessing) cannot survive a save. The save is REFUSED if
    any record the engine can dispatch would change its bytes. Writes the .bin the cart build reads
    plus the .json IR next to it."""
    wf = _work_path(side, "record", area_id)
    try:
        wl_persist.save_record(wf, bytes(data), side, area_id)
    except ValueError as ex:
        raise HTTPException(422, str(ex))
    # A save can renumber, so the mark -> position index the quest pane resolves its steps through
    # is stale the moment it returns. Dropping it here is the whole point of marks: the NAME stays,
    # the place it maps to is looked up again.
    wl_quest.forget_marks(side, area_id)
    return wf


# gfx tilesets and str portraits are a GLOBAL asset pool keyed by id — the disk side an asset
# happens to live on is meaningless for an EF build. Resolve them by id across all sides
# (work overrides baseline). Returns (path, source, home_side) or (None, None, None).
def _resolve_asset(kind: str, num: int):
    for side in (1, 2, 3, 4):
        wf = _work_path(side, kind, num)
        if wf.is_file():
            return wf, "work", side
    for side in (1, 2, 3, 4):
        bf = _baseline_dir(side) / f"s{side}_{kind}_id{num:02d}.bin"
        if bf.is_file():
            return bf, "baseline", side
    return None, None, None


def _gfx(gid: int):
    return _resolve_asset("gfx", gid)


def _str(sid: int):
    return _resolve_asset("str", sid)


def _portrait_png(sid: int):
    for side in (1, 2, 3, 4):
        p = REPO / "portraits" / "all" / f"s{side}_id{sid:02d}.png"
        if p.is_file():
            return p
    return None


class TileEdit(BaseModel):
    tileids: list[int]


class TilePixels(BaseModel):
    pixels: list[list[int]]
    chars: list[dict]


class CopyTile(BaseModel):
    src_side: int
    src_area: int
    src_tid: int
    dst_tid: int


class NewArea(BaseModel):
    side: int
    area_id: int
    mode: str            # "empty" | "copy"
    from_side: int | None = None
    from_area: int | None = None


def _area_gfx_id(side: int, area_id: int) -> int:
    rec_path, _ = _resolve(side, "record", area_id)
    area = load_area(rec_path.read_bytes(), side, area_id)
    return area.raw[area.slot + 0x30] if area.slot + 0x30 < len(area.raw) else 0


@app.get("/api/health")
def health() -> dict:
    # `machine` is a property of the data root, not of the server — see backend/wl_machine.py.
    # The frontend reads it here to say so on screen, because an editor that silently refuses
    # every save is worse than one that tells you why up front.
    return {"ok": True, "repo": str(REPO), "repo_exists": REPO.exists(),
            "machine": MACHINE, "record_base": RAM, "read_only": MACHINE != "c64"}


@app.get("/api/areas")
def list_areas() -> dict:
    seen = set()
    areas = []
    for side in (1, 2, 3, 4):
        for d, origin in ((_baseline_dir(side), "baseline"), (WORK / f"side{side}", "work")):
            if not d.is_dir():
                continue
            for f in sorted(d.glob("s*_record_id*.bin")):
                m = _REC_RE.match(f.name)
                if not m:
                    continue
                aid = int(m.group(2))
                key = (side, aid)
                if key in seen:
                    continue
                seen.add(key)
                has_map = (_resolve(side, "map", aid)[0]).is_file()
                areas.append({"side": side, "area_id": aid, "name": _area_name(aid),
                              "has_map": has_map, "origin": origin})
    areas.sort(key=lambda a: (a["side"], a["area_id"]))
    return {"areas": areas}


@app.post("/api/area/new")
def new_area(n: NewArea) -> dict:
    """Create a new area in the working tree (never baseline). copy = duplicate another area's
    record+map(+str); empty = copy a template then blank the map grid + op layer."""
    tgt_rec = _work_path(n.side, "record", n.area_id)
    if tgt_rec.is_file() or (_baseline_dir(n.side) / tgt_rec.name).is_file():
        raise HTTPException(409, f"side {n.side} area {n.area_id} already exists")
    src_side = n.from_side if n.from_side is not None else n.side
    src_area = n.from_area if n.from_area is not None else 10   # default template
    src_rec, _ = _resolve(src_side, "record", src_area)
    src_map, _ = _resolve(src_side, "map", src_area)
    if not src_rec.is_file():
        raise HTTPException(404, f"template side {src_side} area {src_area} not found")
    rec = bytearray(src_rec.read_bytes())
    area = load_area(bytes(rec), src_side, src_area)
    mapb = bytearray(src_map.read_bytes()) if src_map.is_file() else bytearray()
    if n.mode == "empty":
        # blank the two grid layers: op-nibbles → 0, tile grid → fill tile
        fill = rec[area.slot + 0x33] if area.slot + 0x33 < len(rec) else 0
        nib_stride = area.width // 2
        for o in range(nib_stride * area.height):
            rec[o] = 0
        for o in range(min(area.width * area.height, len(mapb))):
            mapb[o] = fill
    tgt_rec.parent.mkdir(parents=True, exist_ok=True)
    tgt_rec.write_bytes(bytes(rec))
    if mapb:
        _work_path(n.side, "map", n.area_id).write_bytes(bytes(mapb))

    # No asset copying needed: gfx tilesets + str portraits resolve GLOBALLY by id (the disk side
    # is meaningless for EF), so the new area's +$30 gfx and its foe str ids are found wherever
    # they live. Use the "use tileset" picker on the Map tab to change the tileset.
    return {"created": {"side": n.side, "area_id": n.area_id, "mode": n.mode,
                        "from": [src_side, src_area]}}


def _load_area_full(side: int, area_id: int):
    rec_path, rec_src = _resolve(side, "record", area_id)
    if not rec_path.is_file():
        raise HTTPException(404, f"no record blob for side {side} area {area_id}")
    try:
        area = load_area(rec_path.read_bytes(), side, area_id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    map_path, map_src = _resolve(side, "map", area_id)
    tileids = []
    if map_path.is_file():
        tileids = load_map_tileids(map_path.read_bytes(), area.width, area.height)
    return area, tileids, rec_src, map_src


@app.get("/api/area/{side}/{area_id}")
def get_area(side: int, area_id: int) -> dict:
    area, tileids, rec_src, map_src = _load_area_full(side, area_id)
    j = area.to_json()
    j["tileids"] = tileids
    j["record_source"] = rec_src
    j["map_source"] = map_src
    j["fill_tile"] = area.raw[area.slot + 0x33] if area.slot + 0x33 < len(area.raw) else 0
    j["gfx_id"] = area.raw[area.slot + 0x30] if area.slot + 0x30 < len(area.raw) else 0
    # op-4 OBJECT display tiles (docs/engine/OP4_OBJECTS.md): draw_map_cell renders
    # grp4[cellid] byte1 & $7F over the map tile (bit7 = solid). {cellid: tile}.
    op4 = {}
    for cid in set(area.cellid[i] for i, n in enumerate(area.nibble) if n == 0x4):
        rec = wl_cell.subptr(area.raw, area.slot, 0x4, cid)
        if rec is not None and 0 <= rec and rec + 1 < len(area.raw):
            op4[cid] = area.raw[rec + 1] & 0x7F
    j["op4_tiles"] = op4
    j["record_size"] = len(area.raw)          # record blob size + the build cap (UI warns if over)
    j["record_cap"] = RECORD_CAP
    return j


class CompactResult(BaseModel):
    pass


@app.post("/api/area/{side}/{area_id}/gc-strings")
def gc_strings(side: int, area_id: int, base_rev: str | None = None) -> dict:
    """Remove template/unused strings (renumber refs), fail-closed. Keeps only strings an op
    actually references (+ id 0). Map/tiles untouched — only the record's text pool."""
    _guard_rev(side, area_id, base_rev)
    rec_path, _ = _resolve(side, "record", area_id)
    if not rec_path.is_file():
        raise HTTPException(404, "no record")
    raw = rec_path.read_bytes()
    new, removed = wl_cell.remove_unused_strings(raw, side, area_id)
    if removed:
        _push_area_undo(side, area_id)
        wf = _save_record(side, area_id, new)
    return {"removed": removed, "size": len(new), "cap": RECORD_CAP, "over_cap": len(new) > RECORD_CAP}


@app.post("/api/area/{side}/{area_id}/compact")
def compact_area(side: int, area_id: int, base_rev: str | None = None) -> dict:
    """Garbage-collect orphaned dead regions from the record (fail-closed: only applies a
    change that decodes byte-identical). Brings a bloated record back under the build cap."""
    _guard_rev(side, area_id, base_rev)
    rec_path, _ = _resolve(side, "record", area_id)
    if not rec_path.is_file():
        raise HTTPException(404, "no record")
    raw = rec_path.read_bytes()
    new, reclaimed = wl_cell.compact_record(raw, side, area_id)
    if reclaimed:
        _push_area_undo(side, area_id)
        wf = _save_record(side, area_id, new)
    return {"reclaimed": reclaimed, "size": len(new), "cap": RECORD_CAP,
            "over_cap": len(new) > RECORD_CAP, "saved": _rel(_work_path(side, "record", area_id)) if reclaimed else None}


class FillTile(BaseModel):
    tile: int


@app.post("/api/area/{side}/{area_id}/fill-tile")
def set_fill_tile(side: int, area_id: int, body: FillTile) -> dict:
    """Set the area's OFF-MAP fill tile = descriptor byte $33 — the tile the engine draws for every
    cell beyond the map edge (walk off the top/side, Y goes negative). It's a tile INDEX into this
    area's own tileset ($00 = the empty/black tile). Per-area (each descriptor has its own), so two
    areas sharing a tileset can pick different border tiles. Stored exactly where the engine reads
    it: draw_map_cell does `ldy #$33 / lda ($61),y` on an out-of-bounds cell."""
    if not (0 <= body.tile < wl_tiles.NUM_TILES):
        raise HTTPException(422, f"tile must be 0..{wl_tiles.NUM_TILES - 1}")
    rec_path, _ = _resolve(side, "record", area_id)
    if not rec_path.is_file():
        raise HTTPException(404, "no record")
    raw = bytearray(rec_path.read_bytes())
    area = load_area(bytes(raw), side, area_id)
    off = area.slot + 0x33
    if off >= len(raw):
        raise HTTPException(422, "record too short for descriptor $33")
    if raw[off] == body.tile:
        return {"changed": False, "fill_tile": raw[off]}
    _push_area_undo(side, area_id)
    raw[off] = body.tile
    wf = _save_record(side, area_id, raw)
    return {"changed": True, "fill_tile": body.tile, "saved": _rel(wf)}


class WorldPatch(BaseModel):
    fields: dict = {}


class EventPatch(BaseModel):
    delta: int | None = None
    cap: int | None = None
    value: int | None = None


class WorldDerive(BaseModel):
    enc_rate: int = 0
    enc_types: int = 0
    enc_slots: int = 0
    step_frac: int = 0
    step_min: int = 0
    clock_weight: int = 0


@app.post("/api/world/derive")
def world_derive(spec: WorldDerive) -> dict:
    """What a set of world bytes MEANS in play, without writing anything.

    The panel needs this while someone is still typing, and the stored record needs it on load.
    Both go through wl_world.derive() so the two readings cannot drift — the alternative was a
    copy of the arithmetic in JS, which is how the portrait-animation clock ended up with two
    different answers in one editor."""
    import wl_world
    return wl_world.derive(spec.enc_rate, spec.enc_types, spec.enc_slots,
                           spec.step_frac, spec.step_min, spec.clock_weight)


@app.get("/api/area/{side}/{area_id}/world")
def get_world(side: int, area_id: int) -> dict:
    """The area's WORLD PARAMETERS — how the place behaves when nobody is stepping on anything.

    Eight descriptor bytes no cell points at, three of which are the random-encounter setup:
    `+$2F` the odds, `+$31` how many foe types wander, `+$32` how many groups may be out at once.
    `WB015` rolls them every turn and plants an op-$0F marker for the winner — which is why the
    shipped maps hold 366 op-$03 cells and not one op-$0F, and why 59 of the foes no encounter
    record names are simply the wandering pool.

    `events` are the grp16 handlers that change these same bytes mid-game. They are listed here AND
    on their beat in the chain, because the start value is a property of the area and the change is
    a consequence of a step."""
    import wl_world
    area, _, _, _ = _load_area_full(side, area_id)
    w = wl_world.read(area.raw, area.slot)
    evs = wl_world.raw_events(area.raw, area.slot)
    by_id = {}
    for cid in range(wl_cell.group_slot_count(area.raw, area.slot, 6)):
        rec = wl_cell.subptr(area.raw, area.slot, 6, cid)
        if rec is not None and rec < len(area.raw) and not (area.raw[rec] & 0x80):
            by_id.setdefault(area.raw[rec], []).append(cid)
    for e in evs:
        e["raised_by"] = by_id.get(e["event"], [])
    return {**w, "events": evs, "side": side, "area_id": area_id}


@app.post("/api/area/{side}/{area_id}/world")
def set_world(side: int, area_id: int, body: WorldPatch) -> dict:
    """Write world parameters. Single-byte pokes at fixed descriptor offsets: nothing here can
    change length, so no pointer moves. The save then takes the usual `_save_record` path, whose
    re-emit guard refuses the write if any dispatchable record's bytes change — which is exactly
    the check that proves a descriptor poke was inert."""
    import wl_world
    rec_path, _ = _resolve(side, "record", area_id)
    if not rec_path.is_file():
        raise HTTPException(404, "no record")
    raw = bytearray(rec_path.read_bytes())
    area = load_area(bytes(raw), side, area_id)
    try:
        hit = wl_world.poke(raw, area.slot, body.fields or {})
    except ValueError as ex:
        raise HTTPException(422, str(ex))
    if not hit:
        return {"changed": [], **wl_world.read(bytes(raw), area.slot)}
    _push_area_undo(side, area_id)
    wf = _save_record(side, area_id, raw)
    return {"changed": hit, "saved": _rel(wf), **wl_world.read(bytes(raw), area.slot)}


@app.post("/api/area/{side}/{area_id}/world/event/{event}")
def set_world_event(side: int, area_id: int, event: int, body: EventPatch) -> dict:
    """Retune one grp16 event's numbers — the step it changes by, the cap it stops at, or the value
    it sets. Those are immediates at fixed addresses inside the area's own 6502, so the write is a
    poke of the same length; which PARAMETER the handler tunes is deliberately not editable here,
    because changing that changes what the event is."""
    import wl_world
    rec_path, _ = _resolve(side, "record", area_id)
    if not rec_path.is_file():
        raise HTTPException(404, "no record")
    raw = bytearray(rec_path.read_bytes())
    area = load_area(bytes(raw), side, area_id)
    before = bytes(raw)
    try:
        res = wl_world.poke_event(raw, area.slot, event, body.dict())
    except ValueError as ex:
        raise HTTPException(422, str(ex))
    if bytes(raw) == before:
        return {"changed": False, **res}
    _push_area_undo(side, area_id)
    wf = _save_record(side, area_id, raw)
    return {"changed": True, "saved": _rel(wf), **res}


@app.get("/api/area/{side}/{area_id}/tileset")
def get_tileset(side: int, area_id: int) -> dict:
    """Decode the area's OWN gfx blob into an RGBA tile atlas. The gfx id is the descriptor's
    slot+$30 FK — the engine's ground truth (verified correct for all areas; AREA_BINDING.csv is
    wrong for a few). bg=$D021 (black), colour-RAM=white = the fixed scheme."""
    rec_path, _ = _resolve(side, "record", area_id)
    if not rec_path.is_file():
        raise HTTPException(404, f"no record for side {side} area {area_id}")
    area = load_area(rec_path.read_bytes(), side, area_id)
    gid = area.raw[area.slot + 0x30] if area.slot + 0x30 < len(area.raw) else 0
    gfx_path, _, _ = _gfx(gid)
    if gfx_path is None:
        raise HTTPException(404, f"no gfx blob id {gid} for side {side}")
    atlas = _atlas(gfx_path.read_bytes(), bg=0, cram=1)
    atlas["gfx_id"] = gid
    return atlas


_GFX_RE = re.compile(r"^s(\d)_gfx_id(\d+)\.bin$")
_STR_RE = re.compile(r"^s(\d)_str_id(\d+)\.bin$")


@app.get("/api/tilesets")
def list_tilesets() -> dict:
    """Every gfx tileset — a GLOBAL pool keyed by id (the disk side is irrelevant for EF).
    Includes new ones authored in work/. Deduped by id."""
    ids = {}
    for side in (1, 2, 3, 4):
        for d, origin in ((WORK / f"side{side}", "work"), (_baseline_dir(side), "baseline")):
            if not d.is_dir():
                continue
            for f in sorted(d.glob("s*_gfx_id*.bin")):
                m = _GFX_RE.match(f.name)
                if m:
                    gid = int(m.group(2))
                    if gid not in ids:
                        ids[gid] = origin
    return {"tilesets": [{"gfx_id": g, "origin": ids[g]} for g in sorted(ids)]}


class NewTileset(BaseModel):
    mode: str                    # "empty" | "copy"
    from_gid: int | None = None


# per-gid undo stack of prior gfx-blob bytes (in-memory, this session). Pushed before each
# mutating write (tile save / copy tiles); popped by /undo.
_GFX_UNDO: dict[int, list[bytes]] = {}


def _push_gfx_undo(gid: int):
    p, _, _ = _gfx(gid)
    if p is not None:
        _GFX_UNDO.setdefault(gid, []).append(p.read_bytes())
        del _GFX_UNDO[gid][:-30]     # cap depth


@app.post("/api/tileset/new")
def new_tileset(n: NewTileset) -> dict:
    """Create a new gfx tileset with the next free id (9+), in work/. empty = 96 blank tiles;
    copy = duplicate an existing tileset."""
    used = {t["gfx_id"] for t in list_tilesets()["tilesets"]}
    gid = next(g for g in range(256) if g not in used)
    if n.mode == "copy":
        src, _, _ = _gfx(n.from_gid if n.from_gid is not None else 0)
        if src is None:
            raise HTTPException(404, "source tileset not found")
        data = src.read_bytes()
    else:
        data = bytes(3456)       # 96 tiles × 32 B bitmap + 96 × 4 B colour, all zero = blank
    wf = _work_path(1, "gfx", gid)   # stored on side 1 by convention; id is what matters
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_bytes(data)
    return {"gfx_id": gid, "mode": n.mode}


# NOTE: {side} in these tileset routes is ignored — gfx is a GLOBAL pool keyed by id (EF: the
# disk side an asset lives on is meaningless). Resolution + work-save use the id's home side.
@app.get("/api/tileset/{side}/{gid}")
def tileset_atlas(side: int, gid: int) -> dict:
    gfx_path, source, home = _gfx(gid)
    if gfx_path is None:
        raise HTTPException(404, f"no gfx blob id {gid}")
    atlas = _atlas(gfx_path.read_bytes(), bg=0, cram=1)
    atlas["gfx_id"] = gid
    atlas["side"] = home
    atlas["origin"] = source
    atlas["undo_depth"] = len(_GFX_UNDO.get(gid, []))
    return atlas


@app.get("/api/tileset/{side}/{gid}/tile/{tid}")
def tileset_tile(side: int, gid: int, tid: int) -> dict:
    gfx_path, _, home = _gfx(gid)
    if gfx_path is None:
        raise HTTPException(404, f"no gfx blob id {gid}")
    t = wl_tiles.get_tile(gfx_path.read_bytes(), tid)
    t.update({"gfx_id": gid, "side": home, "bg": 0, "cram": 1})
    return t


@app.post("/api/tileset/{side}/{gid}/tile/{tid}")
def tileset_tile_save(side: int, gid: int, tid: int, edit: TilePixels) -> dict:
    gfx_path, _, home = _gfx(gid)
    if gfx_path is None:
        raise HTTPException(404, f"no gfx blob id {gid}")
    _push_gfx_undo(gid)
    out = wl_tiles.set_tile(gfx_path.read_bytes(), tid, edit.pixels, edit.chars)
    wf = _work_path(home, "gfx", gid)
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_bytes(out)
    return {"saved": _rel(wf), "gfx_id": gid, "tile": tid}


class CopyTiles(BaseModel):
    src_side: int
    src_gid: int
    tiles: list[list[int]]     # [[src_tid, dst_tid], ...]


@app.post("/api/tileset/{side}/{gid}/copytiles")
def tileset_copytiles(side: int, gid: int, c: CopyTiles) -> dict:
    dst_path, _, home = _gfx(gid)
    src_path, _, _ = _gfx(c.src_gid)
    if dst_path is None or src_path is None:
        raise HTTPException(404, "gfx blob missing")
    _push_gfx_undo(gid)
    src = src_path.read_bytes()
    out = dst_path.read_bytes()
    for src_tid, dst_tid in c.tiles:
        out = wl_tiles.copy_tile(out, dst_tid, src, src_tid)
    wf = _work_path(home, "gfx", gid)
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_bytes(out)
    return {"saved": _rel(wf), "copied": len(c.tiles)}


def _tile_empty(gfx: bytes, tid: int) -> bool:
    return wl_tiles.tile_empty(gfx, tid)              # one implementation, in wl_tiles


@app.post("/api/tileset/{side}/{gid}/compact")
def tileset_compact(side: int, gid: int) -> dict:
    """Pack non-empty tiles toward the front, empty tiles to the end. Tile 0 stays fixed (an empty
    tile 0 stays empty). Reordering changes tile ids, so every area using this tileset gets its map
    REMAPPED so it still shows the same tiles. Undo covers the gfx + all remapped maps."""
    gfx_path, _, home = _gfx(gid)
    if gfx_path is None:
        raise HTTPException(404, f"no gfx blob id {gid}")
    gfx = gfx_path.read_bytes()
    n = wl_tiles.NUM_TILES
    # tiles 0-9 are the ENGINE-FIXED figure glyphs (player=4, loot=5, op9=8, monsters) — the
    # engine draws them by fixed id, so they must stay put and never move/pack. Compact 10+ only.
    new_order, remap = wl_tiles.compact_order(gfx)   # the real transform lives in wl_tiles
    if remap == list(range(n)):
        return {"compacted": False, "reason": "already packed"}

    _push_gfx_undo(gid)
    out = bytearray(wl_tiles.apply_order(gfx, new_order))
    wf = _work_path(home, "gfx", gid)
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_bytes(bytes(out))

    # remap every area whose tileset is this gid
    remapped = []
    for a in list_areas()["areas"]:
        try:
            if _area_gfx_id(a["side"], a["area_id"]) != gid:
                continue
            area, tileids, _, _ = _load_area_full(a["side"], a["area_id"])
            if not tileids:
                continue
            mp_path, _ = _resolve(a["side"], "map", a["area_id"])
            new_tids = [remap[t] if t < n else t for t in tileids]
            out_map = save_map_tileids(mp_path.read_bytes(), new_tids, area.width, area.height)
            mwf = _work_path(a["side"], "map", a["area_id"])
            mwf.parent.mkdir(parents=True, exist_ok=True)
            mwf.write_bytes(out_map)
            remapped.append(f"{a['name'] or a['area_id']}")
        except Exception:
            pass
    return {"compacted": True, "non_empty": len(non), "empty": len(emp), "maps_remapped": remapped}


@app.post("/api/tileset/{side}/{gid}/clear-unused")
def tileset_clear_unused(side: int, gid: int) -> dict:
    """Blank every tile that NO area using this tileset references in its map (union of all their
    tile-id grids). Skips engine-fixed tiles (0 default, 5 loot-sack, 8 default) and already-empty
    slots. Safe: nothing on any map points at a cleared tile. Undoable (gfx undo). Follow with ⊟ pack
    to compact the freed slots."""
    gfx_path, _, home = _gfx(gid)
    if gfx_path is None:
        raise HTTPException(404, f"no gfx blob id {gid}")
    used: set[int] = set()
    for a in list_areas()["areas"]:
        try:
            if _area_gfx_id(a["side"], a["area_id"]) != gid:
                continue
            _, tileids, _, _ = _load_area_full(a["side"], a["area_id"])
            used.update(tileids)
        except Exception:
            pass
    KEEP = set(range(10))   # engine-fixed figure glyphs (player=4, loot=5, op9=8, monsters) — never clear
    gfx = bytearray(gfx_path.read_bytes())
    n = wl_tiles.NUM_TILES
    cleared = [tid for tid in range(n)
               if tid not in used and tid not in KEEP and not _tile_empty(gfx, tid)]
    if not cleared:
        return {"cleared": [], "count": 0, "used": len(used)}
    _push_gfx_undo(gid)
    for tid in cleared:
        gfx[tid * 32:tid * 32 + 32] = bytes(32)
        gfx[3072 + tid * 4:3072 + tid * 4 + 4] = bytes(4)
    wf = _work_path(home or 1, "gfx", gid)
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_bytes(bytes(gfx))
    return {"cleared": cleared, "count": len(cleared), "used": len(used)}


@app.post("/api/tileset/{side}/{gid}/revert")
def tileset_revert(side: int, gid: int) -> dict:
    """Drop the gfx work copy → back to the pristine baseline tileset (also clears its undo)."""
    reverted = False
    for s in (1, 2, 3, 4):
        wf = _work_path(s, "gfx", gid)
        if wf.is_file():
            wf.unlink()
            reverted = True
    _GFX_UNDO.pop(gid, None)
    baseline = any((_baseline_dir(s) / f"s{s}_gfx_id{gid:02d}.bin").is_file() for s in (1, 2, 3, 4))
    return {"reverted": reverted, "has_baseline": baseline}


@app.post("/api/tileset/{side}/{gid}/undo")
def tileset_undo(side: int, gid: int) -> dict:
    """Restore the gfx blob to the state before the last tile-save / copy (in-session stack)."""
    stack = _GFX_UNDO.get(gid)
    if not stack:
        return {"undone": False, "depth": 0}
    prev = stack.pop()
    _, _, home = _gfx(gid)
    wf = _work_path(home or 1, "gfx", gid)
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_bytes(prev)
    return {"undone": True, "depth": len(stack)}


class RevBody(BaseModel):
    rev: str


@app.get("/api/history/tileset/{gid}")
def tileset_history(gid: int) -> dict:
    """Save history of this tileset's work file — newest first. Each commit is one editor save;
    the label says which tile/op. Empty = still at pristine baseline (no edits committed yet)."""
    gfx_path, source, home = _gfx(gid)
    if gfx_path is None:
        raise HTTPException(404, f"no gfx blob id {gid}")
    rel = _rel(_work_path(home or 1, "gfx", gid))
    return {"gid": gid, "path": rel, "origin": source, "history": _git_file_log(rel)}


@app.post("/api/history/tileset/{gid}/restore")
def tileset_history_restore(gid: int, body: RevBody) -> dict:
    """Restore this tileset to an earlier committed version (not just baseline). Current bytes are
    pushed to the in-session undo stack first, so ↶ undo steps back out of a restore."""
    gfx_path, _, home = _gfx(gid)
    if gfx_path is None:
        raise HTTPException(404, f"no gfx blob id {gid}")
    wf = _work_path(home or 1, "gfx", gid)
    data = _git_show_bytes(body.rev, _rel(wf))
    if data is None:                                  # safety-net: file may have lived on another side
        for s in (1, 2, 3, 4):
            data = _git_show_bytes(body.rev, _rel(_work_path(s, "gfx", gid)))
            if data is not None:
                wf = _work_path(s, "gfx", gid)
                break
    if data is None:
        raise HTTPException(404, f"rev {body.rev[:9]} has no version of tileset {gid}")
    _push_gfx_undo(gid)
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_bytes(data)
    return {"restored": True, "rev": body.rev[:9], "bytes": len(data)}


@app.get("/api/usage/tileset/{gid}")
def tileset_usage(gid: int) -> dict:
    """Which areas use this tileset (descriptor +$30 == gid)."""
    out = []
    for a in list_areas()["areas"]:
        try:
            if _area_gfx_id(a["side"], a["area_id"]) == gid:
                out.append({"side": a["side"], "area_id": a["area_id"], "name": a["name"]})
        except Exception:
            pass
    return {"used_by": out}


_CODE_PORTRAITS: dict = {}


def _code_portraits() -> dict:
    """{str id: [where …]} for every portrait a piece of CODE draws, not a record.

    `lda #imm / jsr $041C` is draw_anim_object with a literal picture id (LOCATION_PICTURE.md §0).
    Three kinds of code do it and none of them is an area record, so a scan of foes/NPCs/services
    reports those portraits as unused — which is what str 3 and str 59 looked like until this
    existed, although portraits/README.md has named both all along (the ranger sheet and the death
    screen). Scene blobs beyond the two stock ones were missing the same way (str 2).

    Only ids that HAVE a blob are reported: `20 1C 04` also occurs as data — in s2/s4_scene_id01 the
    bytes are the tail of a `jmp $A9F5`, which read as "str 133", an id no blob exists for. Requiring
    the blob is the cheap, honest filter; a genuine draw of a missing portrait would be a different
    bug and would show up as a blank picture, not as a wrong name here.
    """
    files = []
    # Upstream also scanned the cartridge build's overlay .prg files here. There is no build in
    # this repo, so only the extracted scene blobs are searched; a portrait drawn from overlay
    # code alone will simply not be listed as used.
    for pat in ("baseline/side*/s*_scene_id*.bin", "work/side*/s*_scene_id*.bin"):
        files += sorted(REPO.glob(pat))
    key = tuple((str(f), f.stat().st_mtime_ns) for f in files if f.is_file())
    if _CODE_PORTRAITS.get("key") == key:
        return _CODE_PORTRAITS["hits"]
    hits: dict = {}
    for f in files:
        try:
            d = f.read_bytes()
        except OSError:
            continue
        for i in range(len(d) - 4):
            if d[i] == 0xA9 and d[i + 2] == 0x20 and d[i + 4] == 0x04 and d[i + 3] in (0x1C, 0x19):
                if _str(d[i + 1])[0] is not None:
                    hits.setdefault(d[i + 1], set()).add(f.name)
    hits = {k: sorted(v) for k, v in hits.items()}
    _CODE_PORTRAITS.update(key=key, hits=hits)
    return hits


@app.get("/api/usage/portrait/{str_id}")
def portrait_usage(str_id: int) -> dict:
    """Everywhere this portrait (str id) is used: area FOES (foe table), hireable NPCs
    (grp17 names — so str 78 reads 'Christina', not just the foe name 'Woman'), $CA00 SERVICE
    records (op-6 special, portrait at rec+$13 — Doctor/Shop/TREX crew), and CODE that draws a
    literal id (scene blobs, the ranger sheet, the death screen — see _code_portraits)."""
    out = []
    NATIVE = {0: "Doctor (infirmary face)", 1: "Shopkeeper (store face)",
              3: "character / ranger sheet", 59: "death screen (Grim Reaper)",
              8: "radio / level-up screen", 11: "radio / level-up screen"}
    for a in list_areas()["areas"]:
        try:
            area, _, _, _ = _load_area_full(a["side"], a["area_id"])
            roles = []
            for f in wl_cell.area_npcs(area, a["side"]):
                if f["str_id"] == str_id:
                    roles.append(f["name"])                     # foe
            try:
                for npc in wl_cell.area_hireables(area, a["side"]):
                    if npc.get("portrait_str") == str_id and npc["name"] not in roles:
                        roles.append(f"{npc['name']} (NPC)")     # grp17 hireable/greeter
            except Exception:
                pass
            # $CA00 SERVICE records — through wl_foe, which knows two things this scan got wrong:
            # the portrait sits at rec+$13 (rec+$03 is the greeting id), and a service reached
            # through an op-A door has NO op-6 cell of its own, which is every one of TREX Base's
            # twelve. str 79 therefore read "not used by any foe" while the TREX talk service uses it.
            import wl_foe
            for svc in wl_foe.services(area, a["side"]):
                if svc["portrait_str"] == str_id:
                    lbl = svc["label"] or f"grp6[{svc['cid']}]"
                    kind = svc["kind"].split("(")[0].strip()
                    roles.append(f"{kind} — {lbl}" + (" (stock scene)" if svc.get("stock") else ""))
            if roles:
                out.append({"side": a["side"], "area_id": a["area_id"], "name": a["name"], "as": roles})
        except Exception:
            pass
    # HAND-CONFIRMED sightings beat the scan. The scan can only see foes, grp17, SERVICE records and
    # literal `lda #id / jsr $041C`; the main overlay draws faces some other way — it contains no
    # $041C at all — so its screens are invisible here. Till found two "unused" portraits in game.
    try:
        seen = json.loads((REPO / "editor" / "annotations" / "portrait_usage.json").read_text())
    except (ValueError, OSError):
        seen = {}
    hand = seen.get(str(str_id))
    if isinstance(hand, dict):
        out.insert(0, {"side": 0, "area_id": -1, "name": hand.get("where", "seen in game"),
                       "as": [f"confirmed by hand — {hand.get('source', '')}".strip(" —")]})

    code = _code_portraits().get(str_id)
    if code:
        out.insert(0, {"side": 0, "area_id": -1,
                       "name": NATIVE.get(str_id, "drawn from code"),
                       "as": [f"code: {', '.join(code[:4])}"]})
    elif str_id in NATIVE:
        out.insert(0, {"side": 0, "area_id": -1, "name": NATIVE[str_id], "as": ["native handler"]})
    return {"used_by": out}


_SIG_CACHE: dict = {}   # path -> (mtime_ns, size, sig)


def _render_sig(f) -> str:
    """Render-identity signature: md5 over the bytes the portrait decoder actually READS
    (base still + anim entry chains + patch streams). Same-id blobs across sides often
    differ only in trailing disk slack past the last stream byte — those display
    pixel-identically, so they hash equal here. Falls back to a raw-bytes md5 if the
    decoder chokes on a blob."""
    import hashlib
    st = f.stat()
    key = str(f)
    hit = _SIG_CACHE.get(key)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    raw = f.read_bytes()
    try:
        import wl_portrait_anim as anim

        class _Rec:
            def __init__(self, b):
                self.b, self.touched = b, set()

            def __getitem__(self, i):
                self.touched.add(i)
                return self.b[i]

            def __len__(self):
                return len(self.b)

        r = _Rec(raw)
        anim.decode_base(r)
        anim.anim_frames(r, max_ticks=2000)   # every slot chain restarts well within this
        sig = hashlib.md5(bytes(raw[i] for i in sorted(r.touched))).hexdigest()
    except Exception:
        sig = hashlib.md5(raw).hexdigest()
    _SIG_CACHE[key] = (st.st_mtime_ns, st.st_size, sig)
    return sig


@app.get("/api/portraits")
def list_portraits() -> dict:
    """All str portrait blobs across sides (id is the str id; file s{side}_str_id{id}.bin)."""
    out = []
    for side in (1, 2, 3, 4):
        d = _baseline_dir(side)
        if not d.is_dir():
            continue
        for f in sorted(d.glob("s*_str_id*.bin")):
            m = _STR_RE.match(f.name)
            if m:
                sid = int(m.group(2))
                has_png = (REPO / "portraits" / "all" / f"s{side}_id{sid:02d}.png").is_file()
                out.append({"side": int(m.group(1)), "str_id": sid, "has_png": has_png,
                            "_sig": _render_sig(f)})
    # sort by str id (same local id across sides = similar faces side by side), then side;
    # render-identical blobs (same pixels+animation, tail junk ignored) point at the first copy
    out.sort(key=lambda p: (p["str_id"], p["side"]))
    first: dict = {}
    for p in out:
        h = p.pop("_sig")
        if h in first:
            p["dup_of"] = first[h]          # "s<side>:<id>" of the identical first copy
        else:
            first[h] = f"s{p['side']}:{p['str_id']}"
    return {"portraits": out}


@app.get("/api/palette")
def palette() -> dict:
    """The C64 Pepto palette (index 0..15 → RGB hex) for the tile/portrait editors."""
    return {"palette": ["#%02x%02x%02x" % rgb for rgb in wl_tiles.PEPTO]}




# per-area UNDO — a stack of pre-edit (record, map) blob snapshots (in-memory, this session). Every
# mutating area write (tiles / nibble / script / prose / gfx) pushes the CURRENT resolved bytes first;
# /undo pops and restores them to work/. Covers all edit kinds uniformly (they all write record/map).
_AREA_UNDO: dict[tuple, list] = {}


def _push_area_undo(side: int, area_id: int):
    rp, _ = _resolve(side, "record", area_id)
    mp, _ = _resolve(side, "map", area_id)
    rec = rp.read_bytes() if rp.is_file() else None
    mapb = mp.read_bytes() if mp.is_file() else None
    if rec is None and mapb is None:
        return
    st = _AREA_UNDO.setdefault((side, area_id), [])
    st.append((rec, mapb))
    del st[:-40]        # cap depth


@app.post("/api/area/{side}/{area_id}/undo")
def area_undo(side: int, area_id: int) -> dict:
    """Restore the area (record + map) to the state before the last edit (step-back undo)."""
    st = _AREA_UNDO.get((side, area_id))
    if not st:
        return {"undone": False, "depth": 0}
    rec, mapb = st.pop()
    if rec is not None:
        # Through the SAVE path, not write_bytes: it keeps the .json IR in lockstep with the .bin.
        # A bare write left the previous state's .json next to the restored .bin, and the .json is
        # the documented source of truth — hand-editing it afterwards would silently compile the
        # state the undo just removed.
        _save_record(side, area_id, rec)
    if mapb is not None:
        wf = _work_path(side, "map", area_id); wf.parent.mkdir(parents=True, exist_ok=True); wf.write_bytes(mapb)
    return {"undone": True, "depth": len(st)}


@app.post("/api/area/{side}/{area_id}/tiles")
def save_tiles(side: int, area_id: int, edit: TileEdit) -> dict:
    """Save an edited tile grid to the map blob's working copy (never touches baseline)."""
    _push_area_undo(side, area_id)
    area, _, _, _ = _load_area_full(side, area_id)
    map_path, _ = _resolve(side, "map", area_id)
    if not map_path.is_file():
        raise HTTPException(404, f"no map blob for side {side} area {area_id}")
    try:
        out = save_map_tileids(map_path.read_bytes(), edit.tileids, area.width, area.height)
    except ValueError as e:
        raise HTTPException(422, str(e))
    orig = map_path.read_bytes()
    wf = _work_path(side, "map", area_id)
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_bytes(out)
    changed = sum(1 for a, b in zip(orig, out) if a != b)
    return {"saved": _rel(wf), "bytes_changed": changed}


@app.get("/api/history/map/{side}/{area_id}")
def map_history(side: int, area_id: int) -> dict:
    """Save history of this area's MAP file (the tile grid) — newest first. One commit per save;
    the label says which op. Empty = still pristine baseline. (Record/physique history is separate.)"""
    rel = _rel(_work_path(side, "map", area_id))
    _, origin = _resolve(side, "map", area_id)
    return {"side": side, "area_id": area_id, "path": rel, "origin": origin, "history": _git_file_log(rel)}


@app.post("/api/history/map/{side}/{area_id}/restore")
def map_history_restore(side: int, area_id: int, body: RevBody) -> dict:
    """Restore the tile grid to an earlier committed version (not just baseline). Snapshots the
    current area (record + map) to the step-back undo first, so ↶ undo reverses the restore.
    Only the MAP file changes — the record (physique/script/NPCs) is left as-is."""
    wf = _work_path(side, "map", area_id)
    data = _git_show_bytes(body.rev, _rel(wf))
    if data is None:
        raise HTTPException(404, f"rev {body.rev[:9]} has no version of this map")
    _push_area_undo(side, area_id)
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_bytes(data)
    return {"restored": True, "rev": body.rev[:9], "bytes": len(data)}


@app.get("/api/history/record/{side}/{area_id}")
def record_history(side: int, area_id: int) -> dict:
    """Save history of this area's RECORD file (descriptor/physique/script/NPCs/texts) —
    newest first. Empty = still pristine baseline. (The map grid has its own history.)"""
    rel = _rel(_work_path(side, "record", area_id))
    _, origin = _resolve(side, "record", area_id)
    return {"side": side, "area_id": area_id, "path": rel, "origin": origin, "history": _git_file_log(rel)}


@app.post("/api/history/record/{side}/{area_id}/restore")
def record_history_restore(side: int, area_id: int, body: RevBody) -> dict:
    """Restore the RECORD to an earlier committed version. Snapshots the area (record + map)
    to the step-back undo first. Only the record changes — the map grid stays as-is; if the
    restored record's dimensions differ from the current one, dims_changed is flagged (then
    restore the matching map version too)."""
    wf = _work_path(side, "record", area_id)
    data = _git_show_bytes(body.rev, _rel(wf))
    if data is None:
        raise HTTPException(404, f"rev {body.rev[:9]} has no version of this record")
    dims_changed = False
    try:
        cur_path, _ = _resolve(side, "record", area_id)
        cur = load_area(cur_path.read_bytes(), side, area_id)
        old = load_area(data, side, area_id)
        dims_changed = (cur.width, cur.height) != (old.width, old.height)
    except Exception:
        pass
    _push_area_undo(side, area_id)
    # Same reason as /undo — and here the bytes come from an arbitrary git revision, so they may
    # predate any decoder fix. Routing them through the serializer re-validates them and refuses a
    # blob the current model cannot reproduce, instead of putting it in front of the cart build.
    wf = _save_record(side, area_id, data)
    return {"restored": True, "rev": body.rev[:9], "bytes": len(data), "dims_changed": dims_changed}


# ── visual diff: compare the CURRENT work state against any committed version ──
def _tile_bytes(gfx: bytes, tid: int) -> bytes:
    return gfx[tid * 32:tid * 32 + 32] + gfx[3072 + tid * 4:3072 + tid * 4 + 4]


@app.get("/api/diff/tileset/{gid}")
def tileset_diff(gid: int, rev: str) -> dict:
    """Diff the current tileset against its state at rev: which tile ids changed
    (bitmap or colour bytes), plus the OLD blob rendered as an atlas for display."""
    gfx_path, _, home = _gfx(gid)
    if gfx_path is None:
        raise HTTPException(404, f"no gfx blob id {gid}")
    old = _git_show_bytes(rev, _rel(_work_path(home or 1, "gfx", gid)))
    if old is None:
        for s in (1, 2, 3, 4):                       # file may have lived on another side
            old = _git_show_bytes(rev, _rel(_work_path(s, "gfx", gid)))
            if old is not None:
                break
    if old is None:
        raise HTTPException(404, f"rev {rev[:9]} has no version of tileset {gid}")
    cur = gfx_path.read_bytes()
    n = wl_tiles.NUM_TILES
    changed = [t for t in range(n) if _tile_bytes(old, t) != _tile_bytes(cur, t)]
    atlas = _atlas(old, bg=0, cram=1)
    return {"gid": gid, "rev": rev[:9], "changed": changed, "old_atlas": atlas}


@app.get("/api/diff/map/{side}/{area_id}")
def map_diff(side: int, area_id: int, rev: str) -> dict:
    """Diff the current map grid against its state at rev: which cell indices changed,
    plus the OLD tile-id grid for display. dims_changed if the old bytes don't fit the
    current width/height (then only a byte-level delta is reported)."""
    old = _git_show_bytes(rev, _rel(_work_path(side, "map", area_id)))
    if old is None:
        raise HTTPException(404, f"rev {rev[:9]} has no version of this map")
    area, tileids, _, _ = _load_area_full(side, area_id)
    try:
        old_tids = load_map_tileids(old, area.width, area.height)
    except Exception:
        cur_path, _ = _resolve(side, "map", area_id)
        cur = cur_path.read_bytes()
        delta = sum(1 for a, b in zip(old, cur) if a != b) + abs(len(old) - len(cur))
        return {"rev": rev[:9], "dims_changed": True, "byte_delta": delta}
    if not tileids:
        tileids = [0] * (area.width * area.height)
    changed = [i for i in range(min(len(old_tids), len(tileids))) if old_tids[i] != tileids[i]]
    return {"rev": rev[:9], "dims_changed": False, "width": area.width, "height": area.height,
            "changed": changed, "old_tileids": old_tids}


class NibbleEdit(BaseModel):
    nibble: list[int]   # width*height op-type per cell
    cellid: list[int]   # width*height dispatch id per cell


@app.post("/api/area/{side}/{area_id}/nibble")
def save_nibble(side: int, area_id: int, e: NibbleEdit) -> dict:
    """Save the op-nibble + cell-id grids back into the record blob (work copy) — the PHYSICS/logic
    layer. Physique paint writes op-$B (wall) / op-0 (floor) here; save_area round-trips the two grid
    layers byte-exact and leaves the descriptor/subtables/prose untouched. See
    cart_EF/docs/engine — passability is the op-nibble ([[wasteland-physics-op-nibble]])."""
    rec_path, _ = _resolve(side, "record", area_id)
    if not rec_path.is_file():
        raise HTTPException(404, f"no record blob for side {side} area {area_id}")
    _push_area_undo(side, area_id)
    area = load_area(rec_path.read_bytes(), side, area_id)
    try:
        out = save_area(area, e.nibble, e.cellid)
    except ValueError as ex:
        raise HTTPException(422, str(ex))
    orig = rec_path.read_bytes()
    wf = _save_record(side, area_id, out)
    changed = sum(1 for a, b in zip(orig, out) if a != b)
    return {"saved": _rel(wf), "bytes_changed": changed}


class NpcSpec(BaseModel):
    """Create/update an NPC. For update every field is optional (only sent ones patch)."""
    name: str | None = None
    name_plural: str | None = None       # the foe-table entry carries both words (FOE_RECORD.md)
    stats: list[int] | None = None       # ST IQ LK SP AGL DEX CHR
    maxcon: int | None = None
    money: int | None = None
    portrait_str: int | None = None
    glyph_tile: int | None = None        # the standing-figure tile in the AREA's tileset
    hireable: bool | None = None
    join_str: int | None = None          # talk/join line (id in the area's text pool)
    hire_gate: int | None = None
    rank: str | None = None
    cell: list[int] | None = None        # marker cell (create: required; update: move)


def _record_rev(side: int, area_id: int) -> str:
    """Short content hash of the area record as the client currently sees it. Used for optimistic
    concurrency: every write sends the rev it started from."""
    pth, _ = _resolve(side, "record", area_id)
    if not pth.is_file():
        return ""
    return hashlib.sha1(pth.read_bytes()).hexdigest()[:12]


def _guard_rev(side: int, area_id: int, base_rev: str | None) -> None:
    """409 if the record moved under the caller. EVERY editor save rewrites the WHOLE record, so a
    tab holding a stale copy would silently clobber newer work (two browsers open, or the CLI and
    the UI at once). Omitting base_rev keeps old clients working — it just gives up the check."""
    if not base_rev:
        return
    cur = _record_rev(side, area_id)
    if cur and cur != base_rev:
        raise HTTPException(409, f"record changed underneath (now {cur}, you had {base_rev}) — "
                                 f"reload the area before saving, your copy is stale")


@app.get("/api/area/{side}/{area_id}/rev")
def area_rev(side: int, area_id: int) -> dict:
    return {"rev": _record_rev(side, area_id)}


def _npc_record_rw(side: int, area_id: int):
    rec_path, _ = _resolve(side, "record", area_id)
    if not rec_path.is_file():
        raise HTTPException(404, f"no record blob for side {side} area {area_id}")
    _push_area_undo(side, area_id)
    raw = bytearray(rec_path.read_bytes())
    area = load_area(bytes(raw), side, area_id)
    return raw, area


def _npc_write(side: int, area_id: int, raw: bytes) -> str:
    return _rel(_save_record(side, area_id, raw))


class FoeSpec(BaseModel):
    """A foe that is only ever fought: a name and an 8-byte record. No grp17 member, no encounter —
    the encounter is a separate op-$3 record, because one foe can be spawned by several."""
    name: str | None = None           # required on create; on update only what is sent is patched
    name_plural: str | None = None    # the OTHER word the entry carries — "3 Madmen", not "3 Madmans"
    portrait_str: int | None = None
    glyph_tile: int | None = None     # standing-figure tile — the OLD way in; `kind` decides it
    template: str | None = None       # "hex:" 8 bytes — a PRESET: copy a shipped foe's combat bytes
    # …and the named combat fields, applied ON TOP of the preset (wl_foe.stats / FOE_RECORD.md).
    # Only what is sent is written, and the four nibble fields keep their byte-mate.
    con: int | None = None            # 16-bit: HP seed AND the experience the kill pays
    range: int | None = None
    dice: int | None = None           # d6 each
    dmg_base: int | None = None       # nibble — damage starts here
    armour: int | None = None         # nibble — subtracts from the attacker's dice
    group_max: int | None = None      # nibble — random encounters raise 1..group_max
    attack_type: int | None = None    # nibble
    kind: int | None = None           # 1 animal · 2 undead · 3 human · 4 robot · 5 turret


class RecordPatch(BaseModel):
    fields: dict                      # {key: value} from GET /chains' field descriptions


@app.get("/api/area/{side}/{area_id}/chains")
def area_record_chains(side: int, area_id: int) -> dict:
    """Every script chain of the area, walked over the REFERENCE GRAPH, with each step's editable
    fields.

    The Script view enumerated the map GRID and could therefore only ever show op-2 and op-C cells.
    Half of any chain has no cell at all — an encounter's victory state, a loot sack's advance, a
    patch's target are all reached through record edges — so a six-record questline showed up as
    nothing. This walks the edges instead, from every dispatching cell, and hands back the fields so
    the same view can EDIT what it shows."""
    ir = _ir(side, area_id)
    area, _, _, _ = _load_area_full(side, area_id)
    strings = [" ".join(str(x).split()) for x in (ir.get("strings") or [])]
    out = []
    for ch in wl_view.record_chains(ir, area, lambda op, cid: wl_cell._state_text(area, op, cid)):
        steps = []
        for st in ch["steps"]:
            g, c = st["op_nibble"], st["cell_id"]
            rr = (ir["groups"].get(str(g)) or {}).get(str(c))
            if not rr:
                continue
            steps.append({"grp": g, "cid": c, "op": rr.get("op"),
                          "label": st.get("label") or st.get("text") or "",
                          "fields": wl_cell.record_fields(rr, area)})
        if steps:
            out.append({"cell": ch["cell"], "steps": steps})
    return {"chains": out, "strings": strings,
            "vocab": wl_story.vocab(ir, area, lambda o, c: wl_cell._state_text(area, o, c))}


_GRAMMAR_CACHE: dict = {}


def _edge_grammar() -> dict:
    """The measured src→dst edge counts over every area, cached on the same key as the quest graph.

    Measured, not declared. The one time this project asserted a shape from intuition — an
    ENCOUNTER handing over to another ENCOUNTER — the game hung, and the histogram would have said
    1-of-592 in seconds. Rebuilding decodes all 43 areas, so it is keyed on their record revisions:
    any save invalidates it, nothing else does."""
    areas = list_areas()["areas"]
    key = tuple((a["side"], a["area_id"], _record_rev(a["side"], a["area_id"])) for a in areas)
    if _GRAMMAR_CACHE.get("key") == key:
        return _GRAMMAR_CACHE["grammar"]
    irs = []
    for a in areas:
        try:
            irs.append(_ir(a["side"], a["area_id"]))
        except Exception as ex:                       # noqa: BLE001 — one bad area must not kill it
            print(f"[grammar] s{a['side']}a{a['area_id']}: {ex}")
    g = wl_story.edge_grammar(irs)
    _GRAMMAR_CACHE.update(key=key, grammar=g)
    return g


@app.get("/api/grammar")
def edge_grammar() -> dict:
    """What hands over to what in the shipped game, and how often. The zeroes are the point."""
    return _edge_grammar()


@app.get("/api/area/{side}/{area_id}/story")
def area_story(side: int, area_id: int) -> dict:
    """The area's chains folded into STORIES: beats as rows, plumbing on the arrows.

    Same data as `/chains`, one decision applied — CELL_PATCH, OBJ and op-0 are not steps the player
    takes, they are how one step reaches the next. Seven records of the Missing Toaster become the
    four things a player actually experiences, and the spawn coordinate lands on the arrow that
    plants it instead of being duplicated across two coupled patch entries."""
    ch = area_record_chains(side, area_id)
    ir = _ir(side, area_id)
    stories = [wl_story.fold(c, ir) for c in ch["chains"]]
    return {"stories": [s for s in stories if s["beats"]], "strings": ch["strings"]}


@app.get("/api/area/{side}/{area_id}/events")
def area_events(side: int, area_id: int) -> dict:
    """The whole area as OUTLINES — every dispatching square, its chain walked as a tree.

    This replaces what the Events canvas was for. The canvas existed to show structure; an outline
    shows the same structure, indents only where something actually forks, and — unlike a canvas —
    can be edited in place. Two things it does that the old view could not: it follows EVERY branch
    (a KEYWORD with three keywords forked three ways and the old single-path walk took the first
    and dropped the rest), and it says where a chain rejoins one already shown instead of drawing a
    line back into the picture.

    Grouped the way the old left rail grouped, because that separation was right: an area is mostly
    furniture — signs, bump messages, forty cells saying the door is locked — and mixing the four
    chains that carry a story into that list is what made it unreadable."""
    ir = _ir(side, area_id)
    strings = [" ".join(str(x).split()) for x in (ir.get("strings") or [])]
    area, _, _, _ = _load_area_full(side, area_id)
    claims = _quest_claims().get((side, area_id), {})
    # quests.json calls it `name`; asking for `title` fell through to the SLUG, so the Events
    # rail showed "highpool-bobby-rex" where the quest is called "Bobby and his Dog Rex" —
    # the same class of leak as showing item 93 instead of Jewelry.
    names = {k: (v.get("name") or v.get("title") or k) for k, v in _quests_authored().items()}

    out = []
    for r in wl_story.roots(ir):
        t = wl_story.tree(ir, r["op"], r["cid"], r["cell"])
        if not t:
            continue
        rows = wl_story.flatten(t)
        for row in rows:                              # the label a reader sees, from the same
            if row.get("grp") is None:                # source the Script panel uses
                continue
            row["label"] = (wl_cell._state_text(area, row["grp"], row["cid"]) or {}).get("text", "")
        # The NESTED tree as well as the flat rows. An outline is a list with indentation; a
        # flowchart needs to know which children belong to which parent, and re-deriving that from
        # depth numbers is guesswork the moment two siblings have different sub-depths. Same tree,
        # same labels — one walk labels both.
        def _lab(n):
            if n.get("grp") is not None:
                n["label"] = (wl_cell._state_text(area, n["grp"], n["cid"]) or {}).get("text", "")
            for k in n.get("children", []):
                _lab(k)
            return n
        _lab(t)
        qs = sorted({q for row in rows if row.get("grp") is not None
                     for q in claims.get((row["grp"], row["cid"]), [])})
        out.append({"root": r["cell"], "op": r["op"], "cid": r["cid"], "rows": rows, "tree": t,
                    "beats": sum(1 for x in rows if x.get("grp") is not None),
                    "forks": sum(1 for x in rows if x.get("forks", 0) > 1),
                    "quests": qs})

    on_quest = [c for c in out if c["quests"]]
    rest = [c for c in out if not c["quests"]]
    # A single-beat chain is a sign or a locked door: real, but not a story. Kept, folded away.
    trivial = [c for c in rest if c["beats"] < 2]
    rest = [c for c in rest if c["beats"] >= 2]
    return {"groups": [
                {"key": "quest", "label": "on a questline", "chains": on_quest},
                {"key": "loose", "label": "not part of any story yet", "chains": rest},
                {"key": "trivial", "label": "single beats — signs, locked doors", "chains": trivial}],
            "strings": strings, "quest_names": names,
            "vocab": wl_story.vocab(ir, area, lambda o, c: wl_cell._state_text(area, o, c)),
            "counts": {"chains": len(out), "quest": len(on_quest),
                       "loose": len(rest), "trivial": len(trivial)}}


def _blob_lengths(side: int, area_id: int) -> tuple[int | None, int | None]:
    now = base = None
    pth, _ = _resolve(side, "record", area_id)
    if pth.is_file():
        now = len(pth.read_bytes())
    bp = _baseline_dir(side) / f"s{side}_record_id{area_id:02d}.bin"
    if bp.is_file():
        base = len(bp.read_bytes())
    return now, base


@app.get("/api/area/{side}/{area_id}/budget")
def area_budget(side: int, area_id: int) -> dict:
    """What the area has already spent of what it cannot exceed.

    Authoring ALLOCATES: every blueprint copied in, every page of speech, every foe takes a slot out
    of a fixed pool. The hard ceiling is the text id — seven bits, so 128 — and hitting it is not a
    serializer problem that can be fixed downstream: string 129 simply cannot be named."""
    now, base = _blob_lengths(side, area_id)
    return wl_story.budget(_ir(side, area_id), now, base)


@app.get("/api/area/{side}/{area_id}/validate")
def area_validate(side: int, area_id: int) -> dict:
    """The gate between wiring and bytes: grammar, budget, fit — in one answer.

    All three were checkable before; none of them was checked together, which is how a chain can be
    correct record-by-record and still be a shape the engine has never run."""
    ir = _ir(side, area_id)
    now, base = _blob_lengths(side, area_id)
    bud = wl_story.budget(ir, now, base)
    findings = wl_story.validate(ir, _edge_grammar(), bud)
    return {"findings": findings, "budget": bud,
            "ok": not any(f["level"] == "error" for f in findings)}


@app.post("/api/area/{side}/{area_id}/record/{grp}/{cid}")
def record_patch(side: int, area_id: int, grp: int, cid: int, p: RecordPatch,
                 base_rev: str | None = None) -> dict:
    """Edit a record's FIELDS — not its bytes.

    Everything went through /script byte offsets until now, which is unreadable and unreviewable:
    a victory target, an encounter flag, a patch's destination cell were all `{"off": 2059,
    "value": 12}`. Here the typed record is patched in the IR and the SERIALIZER re-emits it, so a
    record may change length and every array and pointer is rebuilt around it."""
    _guard_rev(side, area_id, base_rev)
    rec_path, _ = _resolve(side, "record", area_id)
    if not rec_path.is_file():
        raise HTTPException(404, "no record")
    raw = rec_path.read_bytes()
    ir = wl_ir.record_to_ir(raw, side, area_id)
    rr = (ir["groups"].get(str(grp)) or {}).get(str(cid))
    if rr is None:
        raise HTTPException(404, f"no typed record grp{grp}[{cid}] (it may be carried opaque)")
    try:
        ir["groups"][str(grp)][str(cid)] = wl_cell.patch_record_fields(dict(rr), p.fields)
        out = wl_ir.ir_to_record(ir)
    except Exception as ex:                                   # noqa: BLE001
        raise HTTPException(422, f"could not re-emit grp{grp}[{cid}]: {ex}")
    # it must decode back to what was asked for — the same demand the authoring battery makes
    chk = load_area(out, side, area_id)
    if wl_cell.subptr(out, chk.slot, grp, cid) is None:
        raise HTTPException(422, "the edited record no longer resolves; not saved")
    _push_area_undo(side, area_id)
    wf = _save_record(side, area_id, out)
    return {"saved": _rel(wf), "grp": grp, "cid": cid,
            "size_delta": len(out) - len(raw)}


@app.get("/api/foe-templates")
def foe_templates() -> dict:
    """Every DISTINCT foe record the shipped game contains, as PRESETS.

    They used to be the only way to set difficulty, because the combat bytes were not reversed. They
    are now a starting point rather than a cage — the fields are editable (FOE_RECORD.md) — and a
    preset is still the fastest way to say "as tough as a Gundroid". 337 records, deduped on bytes
    0-5 (the kind and the portrait are the author's own choice), sorted by CON."""
    import wl_foe
    return {"templates": wl_foe.templates(REPO), "kinds": wl_foe.FOE_KINDS}


@app.get("/api/area/{side}/{area_id}/foes")
def area_foes(side: int, area_id: int) -> dict:
    """The area's foe table, complete — for the FOE EDITOR.

    Deliberately NOT `/npcs`: that one dedupes by name and the panel filters it to the referenced
    ones, which is right for a portrait strip and wrong for an editor. A foe you just created and
    have not wired to an encounter yet must still be visible and editable."""
    import wl_foe
    import wl_world
    area, _, _, _ = _load_area_full(side, area_id)
    out = []
    entries = wl_cell.foe_name_entries(area)
    # Foes 1..desc+$31 are the WANDERING POOL: WB015 rolls an id in that range once a turn and
    # plants it as an op-$0F cell (RANDOM_ENCOUNTERS.md). They are named by no record BY DESIGN, so
    # calling them unused was wrong for 59 of the 90 — most of Quartz's street thugs among them.
    # grp16 can grow the range past the start value, so the cap is reported too.
    types = area.raw[area.slot + 0x31] if area.slot + 0x31 < len(area.raw) else 0
    grown = max([types] + [e["effect"].get("cap", 0) - 1 for e in wl_world.raw_events(area.raw, area.slot)
                           if e["effect"]["field"] == "pool" and e["effect"].get("cap")])
    for fo in wl_foe.foes(area, side):
        rec = wl_ir._unhex(fo["record"]) if fo.get("record") else b""
        e = entries[fo["id"] - 1] if fo["id"] - 1 < len(entries) else None
        out.append({**fo, **(wl_foe.toughness(rec) if len(rec) == 8 else {}),
                    "wandering": fo["id"] <= types,
                    "wandering_grown": types < fo["id"] <= grown,
                    "name_segs": e["segs"] if e else None,
                    "map_tile": wl_cell.GLYPH_TABLE[rec[6]] if len(rec) == 8 and rec[6] < len(wl_cell.GLYPH_TABLE) else None})
    return {"foes": out, "wandering_types": types, "wandering_max": grown,
            "glyph_tiles": sorted(set(wl_cell.GLYPH_TABLE))}


@app.post("/api/area/{side}/{area_id}/foe")
def foe_create(side: int, area_id: int, spec: FoeSpec, base_rev: str | None = None) -> dict:
    """ADD a foe (name + 8-byte record) to the area's two parallel tables. Returns its foe id, which
    an op-$3 ENCOUNTER's slot then references."""
    if spec.name is None or spec.portrait_str is None:
        raise HTTPException(422, "name and portrait_str are required to create a foe")
    _guard_rev(side, area_id, base_rev)
    raw, area = _npc_record_rw(side, area_id)
    try:
        blob, res = wl_cell.add_foe(bytes(raw), side, area_id,
                                    {k: v for k, v in spec.model_dump().items() if v is not None})
    except ValueError as ex:
        raise HTTPException(422, str(ex))
    return {"saved": _npc_write(side, area_id, blob), **res}


@app.post("/api/area/{side}/{area_id}/foe/{foe_id}")
def foe_update(side: int, area_id: int, foe_id: int, spec: FoeSpec,
               base_rev: str | None = None) -> dict:
    """Patch a foe's name / portrait / glyph. Re-emits the whole name table, so it is also the
    repair path for a table written in a broken shape."""
    _guard_rev(side, area_id, base_rev)
    raw, area = _npc_record_rw(side, area_id)
    d = {k: v for k, v in spec.model_dump().items() if v is not None}
    try:
        blob = wl_cell.update_foe(bytes(raw), side, area_id, foe_id, d)
    except ValueError as ex:
        raise HTTPException(422, str(ex))
    return {"saved": _npc_write(side, area_id, blob), "foe_id": foe_id}


@app.post("/api/area/{side}/{area_id}/npc")
def npc_create(side: int, area_id: int, spec: NpcSpec) -> dict:
    """Create a full NPC (name+foe rec+grp17 record+marker) — NPC_SPAWN.md, relocation-append."""
    if spec.name is None or spec.portrait_str is None or not spec.cell:
        raise HTTPException(422, "name, portrait_str and cell are required")
    raw, area = _npc_record_rw(side, area_id)
    d = {k: v for k, v in spec.model_dump().items() if v is not None}
    blob, res = wl_cell.add_npc(bytes(raw), side, area_id, d)
    area2 = load_area(blob, side, area_id)
    nib, cid = list(area2.nibble), list(area2.cellid)
    i = int(spec.cell[1]) * area2.width + int(spec.cell[0])
    nib[i] = 0x3
    cid[i] = res["marker_cell_id"]
    out = save_area(area2, nib, cid)
    return {"saved": _npc_write(side, area_id, out), **res}


@app.post("/api/area/{side}/{area_id}/npc/{n}")
def npc_update(side: int, area_id: int, n: int, spec: NpcSpec, base_rev: str | None = None) -> dict:
    """Patch an NPC in place; `cell` moves its marker (old marker cells cleared)."""
    _guard_rev(side, area_id, base_rev)
    raw, area = _npc_record_rw(side, area_id)
    d = {k: v for k, v in spec.model_dump().items() if v is not None}
    cell = d.pop("cell", None)
    try:
        out = wl_cell.update_npc(bytes(raw), side, area_id, n, d)
    except ValueError as ex:
        raise HTTPException(422, str(ex))
    if cell:
        area2 = load_area(out, side, area_id)
        nib, cid = list(area2.nibble), list(area2.cellid)
        moved_id = None
        for i, op in enumerate(area2.nibble):     # clear this NPC's old markers
            if op in (0x3, 0xF):
                rec = wl_cell.subptr(out, area2.slot, op, area2.cellid[i])
                if rec is not None and 0 <= rec < len(out) - 12 and (out[rec + 9] >> 4) == n:
                    moved_id = area2.cellid[i]
                    nib[i] = 0
                    cid[i] = 0
        if moved_id is None:
            raise HTTPException(422, f"NPC {n} has no marker cell to move")
        i = int(cell[1]) * area2.width + int(cell[0])
        nib[i] = 0x3
        cid[i] = moved_id
        out = save_area(area2, nib, cid)
    return {"saved": _npc_write(side, area_id, out), "n": n}


@app.delete("/api/area/{side}/{area_id}/npc/{n}")
def npc_delete(side: int, area_id: int, n: int) -> dict:
    """Remove an NPC's marker cells (records stay orphaned — harmless, same as Clear cell)."""
    raw, area = _npc_record_rw(side, area_id)
    out = bytes(raw)
    nib, cid = list(area.nibble), list(area.cellid)
    cleared = 0
    for i, op in enumerate(area.nibble):
        if op in (0x3, 0xF):
            rec = wl_cell.subptr(out, area.slot, op, area.cellid[i])
            if rec is not None and 0 <= rec < len(out) - 12 and (out[rec + 9] >> 4) == n:
                nib[i] = 0
                cid[i] = 0
                cleared += 1
    out = save_area(area, nib, cid)
    return {"saved": _npc_write(side, area_id, out), "cleared_cells": cleared}


class AddOp(BaseModel):
    # col/row are OPTIONAL: omit them to author a record with NO cell of its own — a "shell" that
    # something else reaches through a record edge (an ENCOUNTER's victory advance, a LOOT's
    # advance, a PATCH entry). Half of any quest is records the map never dispatches directly.
    col: int | None = None
    row: int | None = None
    op: int                       # 0x1 SAY | 0xA TRANSITION (more to come)
    params: dict                  # op-specific: transition {target,x,y,message?}; say {message}


def _build_op_record(op: int, p: dict) -> bytes:
    """A NEW record's bytes — named as IR by `wl_cell.op_record_ir`, emitted by the serializer."""
    try:
        return wl_ir._emit_op_record(wl_cell.op_record_ir(op, p))
    except ValueError as ex:
        raise HTTPException(422, str(ex))



@app.post("/api/area/{side}/{area_id}/add-op")
def add_op(side: int, area_id: int, a: AddOp, base_rev: str | None = None) -> dict:
    """ADD a new script op: build a fresh subtable record, append it safely (wl_cell.add_record —
    relocates+extends the group's pointer array, never disturbs active dispatches), and — when a
    cell is given — set that cell's op-nibble + cell-id to it. Round-trip-verified before writing;
    422 if the new record doesn't decode back. This is the logic-ADD path (custom areas, TREX).

    Omitting col/row leaves the record UNBOUND: it exists and has an id, and something else reaches
    it through a record edge. That is not an exotic case — an encounter's victory state, a loot
    sack's advance and every CELL_PATCH target are records the map never dispatches."""
    _guard_rev(side, area_id, base_rev)
    rec_path, _ = _resolve(side, "record", area_id)
    if not rec_path.is_file():
        raise HTTPException(404, "no record")
    if a.op == 0xB:
        raise HTTPException(422, "op-$B goes through /add-chain (chain-safe id allocation), not /add-op")
    if a.op not in (0x1, 0x2, 0x3, 0x4, 0x5, 0x6, 0x8, 0x9, 0xA, 0xC):
        raise HTTPException(422, f"add-op supports $1/$2/$3/$4/$5/$6/$8/$9/$A/$C; got ${a.op:X}")
    area = load_area(rec_path.read_bytes(), side, area_id)
    bind = a.col is not None and a.row is not None
    if bind and not (0 <= a.col < area.width and 0 <= a.row < area.height):
        raise HTTPException(422, "cell out of bounds")
    rec_bytes = _build_op_record(a.op, a.params)
    active = [area.cellid[i] for i in range(len(area.nibble)) if area.nibble[i] == a.op]
    raw = bytearray(area.raw)
    new_id = wl_cell.add_record(raw, area.slot, a.op, rec_bytes, active)
    area2 = load_area(bytes(raw), side, area_id)          # grids intact, tail grown
    nib, cid = list(area2.nibble), list(area2.cellid)
    if bind:
        i = a.row * area2.width + a.col
        nib[i], cid[i] = a.op, new_id
    out = save_area(area2, nib, cid)
    # round-trip verify: the new (op,new_id) must resolve to a real record
    chk = load_area(out, side, area_id)
    if wl_cell.subptr(chk.raw, chk.slot, a.op, new_id) is None:
        raise HTTPException(422, "record did not round-trip; not saved")
    _push_area_undo(side, area_id)
    wf = _save_record(side, area_id, out)
    return {"saved": _rel(wf), "op": a.op, "dispatch": new_id,
            "cell": [a.col, a.row] if bind else None,
            "size_delta": len(out) - len(area.raw)}


class AddChain(BaseModel):
    col: int
    row: int
    op: int = 0xB                 # the chain's op (op-B BUMP for now; the follow-op per link)
    links: list                   # ordered [{message:int, next_op?:int, next_id?:int}] — see below
    loop: bool = True             # last link → head (ring) vs. STOP ($FF)

@app.post("/api/area/{side}/{area_id}/add-chain")
def add_chain_ep(side: int, area_id: int, a: AddChain, base_rev: str | None = None) -> dict:
    """ADD a CHAIN of linked op-B records and point the cell at the HEAD. Each link = [str][$B][next];
    the writer allocates ids above EVERY referenced record (wl_cell.group_referenced_ids), so existing
    rotation chains are never clobbered. The last link loops back to the head (`loop`) or stops ($FF).
    A single static bump is just links=[{message}] with loop=false. A link may override its follow to
    a non-$B op via next_op/next_id (chain into LOOT/TRANSITION/… as the last step)."""
    _guard_rev(side, area_id, base_rev)
    rec_path, _ = _resolve(side, "record", area_id)
    if not rec_path.is_file():
        raise HTTPException(404, "no record")
    if a.op != 0xB:
        raise HTTPException(422, "add-chain currently supports op-$B chains only")
    if not a.links or len(a.links) > 64:
        raise HTTPException(422, "links must be 1..64")
    area = load_area(rec_path.read_bytes(), side, area_id)
    if not (0 <= a.col < area.width and 0 <= a.row < area.height):
        raise HTTPException(422, "cell out of bounds")
    N = len(a.links)
    # CLEAN group rebuild with the new chain in place: corruption-proof (fresh pointer array, never
    # appended onto a possibly-inconsistent one), replaces THIS cell's old chain, GCs orphans, keeps
    # every other cell's records byte-for-byte (semantically). Handles create AND edit.
    out, head, S = wl_cell.write_cell_chain(area.raw, area, a.col, a.row, a.links, a.loop)
    # round-trip verify: every new link resolves AND the cell points at the head
    chk = load_area(out, side, area_id)
    for j in range(N):
        if wl_cell.subptr(chk.raw, chk.slot, 0xB, head + j) is None:
            raise HTTPException(422, "chain did not round-trip; not saved")
    i = a.row * chk.width + a.col
    if chk.nibble[i] != 0xB or chk.cellid[i] != head:
        raise HTTPException(422, "cell did not bind to chain head; not saved")
    _push_area_undo(side, area_id)
    wf = _save_record(side, area_id, out)
    return {"saved": _rel(wf), "op": 0xB, "head": head, "links": N, "loop": a.loop,
            "survivors": S, "cell": [a.col, a.row], "size_delta": len(out) - len(area.raw)}


class ScriptStep(BaseModel):
    op: int                           # $1 SAY · $5 LOOT · $B BUMP (the chainable ops so far)
    message: int = 0                  # SAY/BUMP: string id (Text tab; 0 = none)
    items: list | None = None         # LOOT: [{item:int, qty:int}, ...]

class AddScriptChain(BaseModel):
    col: int
    row: int
    steps: list[ScriptStep]           # ordered heterogeneous steps — bump→bump→LOOT→bump etc.
    loop: bool = False                # last step → head (ring) vs. STOP ($FF)

@app.post("/api/area/{side}/{area_id}/add-script-chain")
def add_script_chain_ep(side: int, area_id: int, a: AddScriptChain, base_rev: str | None = None) -> dict:
    """ADD a HETEROGENEOUS script chain (bump→bump→LOOT→bump …) and point the cell at the head. Each
    step becomes a record in its op's group, wired to the next via that op's advance slot; the engine
    walks the whole sequence from one interaction (a general cell state machine, not op-B-only —
    verified against Highpool's fight→loot→next-round chains). Round-trip-verified before writing."""
    _guard_rev(side, area_id, base_rev)
    rec_path, _ = _resolve(side, "record", area_id)
    if not rec_path.is_file():
        raise HTTPException(404, "no record")
    if not a.steps or len(a.steps) > 32:
        raise HTTPException(422, "steps must be 1..32")
    for st in a.steps:
        if st.op not in wl_cell._CHAINABLE_OPS:
            raise HTTPException(422, f"op ${st.op:X} not chainable (have $1 SAY / $5 LOOT / $B BUMP)")
    area = load_area(rec_path.read_bytes(), side, area_id)
    if not (0 <= a.col < area.width and 0 <= a.row < area.height):
        raise HTTPException(422, "cell out of bounds")
    steps = [{"op": s.op, "message": s.message, "items": s.items} for s in a.steps]
    try:
        out, hop, hid = wl_cell.write_script_chain(area.raw, area, a.col, a.row, steps, a.loop)
    except ValueError as e:
        raise HTTPException(422, str(e))
    # round-trip verify: cell binds to the head AND the whole chain walks back to `len(steps)` states
    chk = load_area(out, side, area_id)
    i = a.row * chk.width + a.col
    if chk.nibble[i] != hop or chk.cellid[i] != hid:
        raise HTTPException(422, "cell did not bind to chain head; not saved")
    walked = wl_cell.area_chains(chk)["cells"].get(f"{a.col},{a.row}")
    if not walked or len(walked) < len(steps):
        raise HTTPException(422, f"chain did not round-trip ({len(walked or [])}/{len(steps)} states); not saved")
    _push_area_undo(side, area_id)
    wf = _save_record(side, area_id, out)
    return {"saved": _rel(wf), "head_op": hop, "head_id": hid, "steps": len(a.steps),
            "loop": a.loop, "cell": [a.col, a.row], "size_delta": len(out) - len(area.raw)}


class PortraitNew(BaseModel):
    side: int
    str_id: int
    mode: str                     # "empty" | "copy"
    from_side: int | None = None
    from_str: int | None = None


class PortraitPixels(BaseModel):
    pixels: list[list[int]]       # 2-bit values, portrait box grid


# Portrait fixed colour scheme (memory: NOT per-portrait) — %00 black, %01 red $2, %10 blue $6,
# %11 colour-RAM white $1. These map straight to Pepto indices for the editor palette.
PORTRAIT_PAL = [0, 2, 6, 1]


@app.get("/api/portrait/{side}/{str_id}/bitmap")
def get_portrait_bitmap(side: int, str_id: int) -> dict:
    """Decode a portrait to a 2-bit pixel grid (the box) for the pixel editor."""
    try:
        import wl_portrait_anim as anim
    except ImportError:
        raise HTTPException(503, "portrait decoder unavailable")
    blob_path, _, _ = _str(str_id)
    if blob_path is None:
        raise HTTPException(404, f"no str blob s{side}_str_id{str_id:02d}")
    bm = anim.decode_base(blob_path.read_bytes())
    pixels = _bitmap_pixels(bm)
    return {"w": len(pixels[0]), "h": len(pixels), "pixels": pixels,
            "palette": ["#%02x%02x%02x" % wl_tiles.PEPTO[i] for i in PORTRAIT_PAL]}


def _bitmap_pixels(bm) -> list[list[int]]:
    """$6000 MC bitmap → 2-bit grid for the portrait box (byte cols 1..11, pixel rows 16..99)."""
    C1, BF, C2, C0 = 1, 16, 11, 99
    rows = []
    for y in range(BF, C0 + 1):
        row = []
        for col in range(C1, C2 + 1):
            addr = (y >> 3) * 320 + (y & 7) + col * 8
            b = bm[addr] if addr < len(bm) else 0
            for p in range(4):
                row.append((b >> (6 - 2 * p)) & 0b11)
        rows.append(row)
    return rows


@app.post("/api/portrait/new")
def portrait_new(n: PortraitNew) -> dict:
    """Create a portrait str blob in work/: copy = duplicate another; empty = blank bitmap."""
    tgt = _work_path(n.side, "str", n.str_id)
    if n.mode == "copy":
        src, _, _ = _str(n.from_str if n.from_str is not None else n.str_id)
        if src is None:
            raise HTTPException(404, "source portrait not found")
        tgt.parent.mkdir(parents=True, exist_ok=True)
        tgt.write_bytes(src.read_bytes())
        return {"created": _rel(tgt), "mode": "copy"}
    # empty
    try:
        import wl_portrait_encode as enc
    except ImportError:
        raise HTTPException(503, "portrait encoder unavailable yet")
    tgt.parent.mkdir(parents=True, exist_ok=True)
    tgt.write_bytes(enc.build_blob(enc.blank_bitmap()))
    return {"created": _rel(tgt), "mode": "empty"}


@app.post("/api/portrait/{side}/{str_id}/bitmap")
def save_portrait_bitmap(side: int, str_id: int, edit: PortraitPixels) -> dict:
    """Re-encode an edited portrait bitmap into the str blob (work copy)."""
    try:
        import wl_portrait_encode as enc
        import wl_portrait_anim as anim
    except ImportError:
        raise HTTPException(503, "portrait encoder unavailable yet")
    src_path, _, home = _str(str_id)
    template = src_path.read_bytes() if src_path is not None else None
    bm = _pixels_bitmap(edit.pixels)
    blob = enc.build_blob(bm, template_blob=template)
    wf = _work_path(home or side, "str", str_id)
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_bytes(blob)
    return {"saved": _rel(wf), "str_id": str_id}


class TrackFrame(BaseModel):
    delay: int                   # walks this state is shown for
    pixels: list[list[int]]      # 44×84 box grid (2-bit) — the FULL picture of this state


class SlotEdit(BaseModel):
    enabled: bool
    delay: int = 8
    alt: list[list[int]] | None = None    # legacy 2-state toggle
    track: list[TrackFrame] | None = None  # state sequence (preferred; wins over alt)


class AnimEdit(BaseModel):
    pixels: list[list[int]]      # base box grid
    slots: list[SlotEdit]


class PortraitImport(BaseModel):
    png_b64: str
    apply: bool = False          # False = validate + preview only
    crop_x: int | None = None    # where the user placed the WxH frame; None = probe, import nothing
    crop_y: int | None = None


def _png_to_grid_validated(png_b64: str, W: int, H: int,
                           crop_x: int | None = None, crop_y: int | None = None) -> dict:
    """Decode a PNG to a WxH grid of 2-bit palette indices, the way portraits/globe/logo all need it.
    Validates FIRST and reports precisely — a silently wrong picture costs more than a rejected import.

    Two things make this stricter than a normal image import:
      * geometry — the picture is WxH MULTICOLOUR pixels, drawn on the PC with a block per MC pixel.
        The block size comes from the source: bx = w//W, by = h//H, so the window we read is
        (W*bx) x (H*by). Whatever is left over is SURPLUS and is never resampled away — see below.
      * colour — these anim-objects carry NO colour data of their own. The window scheme is FIXED
        (PORTRAIT_PAL = black / red / blue / white), so the image may use at most those FOUR colours.
        More than four is refused outright; colours that are not exact palette entries are snapped to
        the nearest and reported, and two source colours collapsing onto one entry is called out.

    SURPLUS = A CROP THE USER PLACES, NOT A DECISION WE MAKE.
    Till paints on a square canvas because that is what a PC paint program gives him — his 88x88
    T-Rex is 44 MC pixels wide (every column pair identical, measured) and four rows too tall, and
    those four rows are empty. Squeezing 88 rows into 84 deleted rows 21/43/65/87 out of the middle
    of his dither (0.9.26 and earlier); refusing outright just blocked him (0.9.27). Now: if the
    source is bigger than the window, we return `needs_crop` with the surplus per axis and an INK
    PROFILE per row and column, the editor shows a draggable frame over the picture, and the import
    runs again with the offset HE chose. Nothing is ever cropped without that offset.

    Returns {ok,w,h,source,block,window,surplus,crop,colors,mapping,warnings,pixels,applied:False},
    or ok=False + needs_crop + ink_rows/ink_cols and no pixels. The caller does the encode."""
    import io, base64
    from PIL import Image
    try:
        raw = base64.b64decode(png_b64.split(",")[-1])
        im = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as ex:
        raise HTTPException(422, f"not a readable image: {ex}")

    warn = []
    if im.width < W or im.height < H:
        raise HTTPException(422, f"image is {im.width}x{im.height}, too small — this picture is "
                                 f"{W}x{H} multicolour pixels (our own export writes {W*8}x{H*4})")
    # Block size straight from the source: how many image pixels the author drew per MC pixel.
    # 44x84 -> 1x1 (native grid), 88x84 -> 2x1 (square pixels), 352x336 -> 8x4 (our own export).
    bx, by = im.width // W, im.height // H
    cw, ch = W * bx, H * by
    sx, sy = im.width - cw, im.height - ch
    # Two block shapes are right: the NATIVE grid (bx == by) and SQUARE pixels (bx == 2*by, the true
    # multicolour 2:1). Anything else reads the picture stretched, which is worth saying out loud.
    if not (bx == by or bx == 2 * by):
        warn.append(f"one multicolour pixel would be read as a {bx}x{by} block, which is neither the "
                    f"native grid ({W}x{H}) nor square pixels (~2:1, e.g. {W*8}x{H*4}) — the picture "
                    f"will come out stretched")

    ox = 0 if crop_x is None else int(crop_x)
    oy = 0 if crop_y is None else int(crop_y)
    if not (0 <= ox <= sx and 0 <= oy <= sy):
        raise HTTPException(422, f"crop offset {ox},{oy} is outside 0..{sx},0..{sy} for a "
                                 f"{cw}x{ch} window in a {im.width}x{im.height} image")

    # No offset chosen yet and the picture does not fit exactly -> hand the editor everything it
    # needs to let the user place the frame, and import NOTHING.
    probing = (sx or sy) and crop_x is None and crop_y is None
    # Colour census over the WHOLE image, so a fifth colour hiding in the surplus band is caught
    # before the user spends time positioning a frame. The grid itself reads the placed window.
    seen: dict = {}
    for y in range(im.height):
        for x in range(im.width):
            c = im.getpixel((x, y))
            seen[c] = seen.get(c, 0) + 1
    grid = [[im.getpixel((ox + x * bx, oy + y * by)) for x in range(W)] for y in range(H)]

    pal = [wl_tiles.PEPTO[i] for i in PORTRAIT_PAL]
    names = ["black", "red", "blue", "white"]
    if len(seen) > 4:
        top = sorted(seen.items(), key=lambda kv: -kv[1])[:8]
        raise HTTPException(422,
            f"image uses {len(seen)} colours, the format allows 4 "
            f"(black / red / blue / white — fixed, the picture stores no colour of its own). "
            f"Most common: " + ", ".join("#%02x%02x%02x (%d px)" % (c[0], c[1], c[2], n) for c, n in top))

    def nearest(c):
        return min(range(4), key=lambda i: sum((c[k] - pal[i][k]) ** 2 for k in range(3)))

    mapping, used = [], {}
    for c, n in sorted(seen.items(), key=lambda kv: -kv[1]):
        i = nearest(c)
        d = sum((c[k] - pal[i][k]) ** 2 for k in range(3)) ** 0.5
        mapping.append({"src": "#%02x%02x%02x" % c, "to": names[i], "px": n,
                        "exact": d < 1.0, "distance": round(d, 1)})
        used.setdefault(i, []).append("#%02x%02x%02x" % c)
    for i, srcs in used.items():
        if len(srcs) > 1:
            warn.append(f"{', '.join(srcs)} all become {names[i]} — those differences are lost")
    for m in mapping:
        if not m["exact"]:
            warn.append(f"{m['src']} is not a palette colour, snapped to {m['to']}")

    out = {"ok": True, "w": W, "h": H, "source": [im.width, im.height],
           "block": [bx, by], "window": [cw, ch], "surplus": [sx, sy], "crop": [ox, oy],
           "colors": len(seen), "mapping": mapping, "warnings": warn, "applied": False}
    if probing:
        # INK PROFILE — per source row / column, how many pixels are NOT background. The editor
        # needs this to tell the user, live while he drags the frame, whether the band he is about
        # to leave outside is empty (Till's four spare rows) or actually painted.
        idx = {c: nearest(c) for c in seen}
        rows = [0] * im.height
        cols = [0] * im.width
        for y in range(im.height):
            for x in range(im.width):
                if idx[im.getpixel((x, y))]:
                    rows[y] += 1
                    cols[x] += 1
        out.update(ok=False, needs_crop=True, ink_rows=rows, ink_cols=cols,
                   message=(f"{im.width}x{im.height} holds {im.width // bx}x{im.height // by} "
                            f"multicolour pixels, the picture is {W}x{H} — place the {W}x{H} frame "
                            f"and import again; nothing is cropped until you do"))
        return out
    out["pixels"] = [[nearest(c) for c in row] for row in grid]
    return out


@app.post("/api/portrait/{side}/{str_id}/import")
def import_portrait_png(side: int, str_id: int, req: PortraitImport) -> dict:
    """Import a PNG into a portrait (44x84 MC box). Validate-first; apply=True writes the work blob."""
    out = _png_to_grid_validated(req.png_b64, 44, 84, req.crop_x, req.crop_y)
    if req.apply and out.get("ok"):
        import wl_portrait_encode as enc
        src_path, _, home = _str(str_id)
        blob = enc.build_blob(_pixels_bitmap(out["pixels"]),
                              template_blob=src_path.read_bytes() if src_path else None)
        wf = _work_path(home or side, "str", str_id)
        wf.parent.mkdir(parents=True, exist_ok=True)
        wf.write_bytes(blob)
        out["applied"] = True
        out["saved"] = _rel(wf)
    return out


@app.get("/api/portrait/{side}/{str_id}/anim-edit")
def get_anim_edit(side: int, str_id: int) -> dict:
    """Decode the portrait's animation into the editable toggle model (per-slot toggled pixels)."""
    try:
        import wl_portrait_animedit as ae
    except ImportError:
        raise HTTPException(503, "anim editor codec not ready yet")
    blob_path, _, _ = _str(str_id)
    if blob_path is None:
        raise HTTPException(404, "no str blob")
    return ae.decode_anim_edit(blob_path.read_bytes())


@app.post("/api/portrait/{side}/{str_id}/anim-edit")
def save_anim_edit(side: int, str_id: int, edit: AnimEdit) -> dict:
    """Build a str blob with the given base + custom 4-slot animation, save to work/."""
    try:
        import wl_portrait_animedit as ae
    except ImportError:
        raise HTTPException(503, "anim editor codec not ready yet")
    base_bm = _pixels_bitmap(edit.pixels)
    slots = [{"enabled": s.enabled, "delay": s.delay, "alt": s.alt,
              "track": [{"delay": f.delay, "pixels": f.pixels} for f in s.track] if s.track else None}
             for s in edit.slots]
    report: dict = {}
    blob = ae.build_anim_blob(base_bm, slots, report=report)
    _, _, home = _str(str_id)
    wf = _work_path(home or side, "str", str_id)
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_bytes(blob)
    return {"saved": _rel(wf), "str_id": str_id, "slots": sum(1 for s in edit.slots if s.enabled),
            "auto_closed": report.get("auto_closed", [])}


def _pixels_bitmap(pixels: list[list[int]]):
    """2-bit box grid → $6000 MC bitmap (inverse of _bitmap_pixels)."""
    import wl_portrait_anim as anim
    bm = bytearray(8192)
    C1, BF, C2, C0 = 1, 16, 11, 99
    for ry, y in enumerate(range(BF, C0 + 1)):
        for cidx, col in enumerate(range(C1, C2 + 1)):
            addr = (y >> 3) * 320 + (y & 7) + col * 8
            b = 0
            for p in range(4):
                v = pixels[ry][cidx * 4 + p] & 0b11
                b |= v << (6 - 2 * p)
            if addr < len(bm):
                bm[addr] = b
    return bytes(bm)


@app.get("/api/area/{side}/{area_id}/npcs")
def area_npcs(side: int, area_id: int) -> dict:
    """Every portrait the area USES, from all three sources — the panel showed only the first.

    `npcs`     the foe-record table (encounter foes)
    `services` op-6 SERVICE records, which carry their own portrait id. TREX Base is nothing but
               services, so its 12 portraits (str 79..90) were invisible.
    `orphans`  grp17 member records nothing references — no marker cell, so the NPC list, which is
               keyed by marker, could not show them either.
    """
    import wl_foe
    area, _, _, _ = _load_area_full(side, area_id)
    return {"npcs": wl_cell.area_npcs(area, side),
            "services": wl_foe.services(area, side),
            "orphans": wl_foe.orphan_hires(area, side)}


@app.get("/api/area/{side}/{area_id}/hireables")
def area_hireables(side: int, area_id: int) -> dict:
    """Hireable NPCs (grp17) with name/stats/portrait + their SPAWN CELLS (op-$3/$F markers).
    docs/engine/NPC_SPAWN.md. Runtime overlays — NOT part of the tile map."""
    area, _, _, _ = _load_area_full(side, area_id)
    try:
        return {"npcs": wl_cell.area_hireables(area, side)}
    except Exception:
        return {"npcs": []}


@app.get("/api/area/{side}/{area_id}/texts")
def area_texts(side: int, area_id: int) -> dict:
    """Every area-scoped text that lands in the game UI: the text5 message pool (with
    used-by references from the script), the foe-name table, and the grp17 hire lines."""
    import wl_text5_encode as tenc
    area, _, _, _ = _load_area_full(side, area_id)
    base = (area.raw[area.slot] | (area.raw[area.slot + 1] << 8)) - RAM
    messages: list[dict] = []
    if 0 <= base and base + 0x3C < len(area.raw):
        strs = tenc.enumerate_strings(area.raw, base, area.raw[base:base + 62])
        usage = wl_cell.text_usage(area)
        try:
            hires = wl_cell.area_hireables(area, side)
        except Exception:
            hires = []
        for h in hires:
            sid = h.get("join_str", 0) & 0x7F
            if sid:
                usage.setdefault(sid, []).append({"cell": (h.get("cells") or [[0, 0]])[0],
                                                  "role": f"join line — {h['name']}"})
        # `reads` = the same string with its control codes written out. The raw form is what gets
        # edited and saved; this is what a person can judge. `<0b> attacks <0f>` is unreadable,
        # "«attacker» attacks «target»" is a sentence.
        messages = [{"id": i, "text": s, "used_by": usage.get(i, []),
                     "reads": wl_cell.explain_text(s)} for i, s in enumerate(strs)]
    try:
        foe_names = wl_cell._foe_names(area)
    except Exception:
        foe_names = []
    # which of these strings are the combat vocabulary, and what each of the seven verbs is FOR
    ir = _ir(side, area_id)
    cm = ir.get("combat_msgs")
    block = list(wl_ir._unhex(cm)) if isinstance(cm, str) else list(cm or [])
    WEAPON = ["melee", "pistol", "burst", "energy", "clip", "thrown", "rocket"]
    combat = {"ids": block,
              "verbs": [{"class": WEAPON[k], "id": block[k]} for k in range(min(7, len(block)))],
              "heads": block[7:10], "fragments": block[10:]}
    return {"messages": messages, "foe_names": foe_names,
            "codes": wl_cell.TEXT_CODES, "combat": combat}


class ServiceSpec(BaseModel):
    """Every field optional — omitted ones keep their current value."""
    portrait: int | None = None
    label: str | None = None
    greeting: int | None = None
    buy: int | None = None          # store: buy-price shift
    sell: int | None = None         # store: sell-price shift
    areatrack: int | None = None    # store: which item/stock table to load
    classes: list[int] | None = None
    prices: list[int] | None = None # doc: the three treatment prices
    prompt: int | None = None       # bank: prompt string id
    amount: int | None = None       # bank: money per ranger
    confirm: str | None = None      # bank: confirmation, raw ASCII
    texts: list[str] | None = None  # revive: prompt / revived / refused, raw ASCII
    pages: list[str] | None = None  # (legacy) talk ASCII pages — superseded by page_ids
    page_ids: list[int] | None = None  # talk: ordered text5 string-ids, one per page


@app.get("/api/area/{side}/{area_id}/services")
def services_list(side: int, area_id: int) -> dict:
    """Every service reachable in this area: the op-A door cell, its op-6 record and the decoded
    panel. Services have NO op-6 cell of their own — they hang off the door's 2-op chain."""
    rec_path, _ = _resolve(side, "record", area_id)
    if not rec_path.is_file():
        raise HTTPException(404, "no record")
    raw = rec_path.read_bytes()
    area = load_area(raw, side, area_id)
    out = []
    for i, op in enumerate(area.nibble):
        if op != 0xA:
            continue
        d = wl_cell.subptr(raw, area.slot, 0xA, area.cellid[i])
        if d is None or d + 6 > len(raw) or raw[d + 4] != 0x06:
            continue
        cid = raw[d + 5]
        r = wl_cell.subptr(raw, area.slot, 0x6, cid)
        if r is None or r >= len(raw):
            continue
        entry = {"cell": [i % area.width, i // area.width], "door_cid": area.cellid[i],
                 "op6_cid": cid, "byte0": raw[r], "grp16_event": not (raw[r] & 0x80)}
        if raw[r] & 0x80:
            entry["panel"] = wl_cell._service_section(area, r, r + RAM)
        out.append(entry)
    # A door is the COMMON way to a service, not the only one. TREX Base's TOASTMASTER (grp6[12])
    # hangs off a chain edge, so the door walk never saw it — and the Events view could offer a jump
    # to an editor that did not list the thing being jumped to. Anything the reference graph reaches
    # is real; it just has no door cell to name it by.
    seen = {e["op6_cid"] for e in out}
    alias6 = wl_cell.group_is_alias(raw, area.slot, 6)
    for grp, cid in wl_ir._ref_ops(raw, area.slot, area, wl_ir._door_targets(raw, area.slot, area)):
        if grp != 0x6 or cid in seen or alias6:
            continue
        r = wl_cell.subptr(raw, area.slot, 0x6, cid)
        if r is None or r >= len(raw) or not (raw[r] & 0x80):
            continue          # bit7 clear = a grp16 EVENT; it belongs to Code and World, not here
        seen.add(cid)
        out.append({"cell": None, "door_cid": None, "op6_cid": cid, "byte0": raw[r],
                    "grp16_event": False, "no_door": True,
                    "panel": wl_cell._service_section(area, r, r + RAM)})
    return {"side": side, "area": area_id, "services": out}


@app.post("/api/area/{side}/{area_id}/service/{cid}")
def service_update(side: int, area_id: int, cid: int, spec: ServiceSpec, base_rev: str | None = None) -> dict:
    """Patch one service record. The record is rebuilt + relocated and grp6[cid] repointed,
    because the class list / ASCII confirmation change length."""
    _guard_rev(side, area_id, base_rev)
    raw, area = _npc_record_rw(side, area_id)
    d = {k: v for k, v in spec.model_dump().items() if v is not None}
    if not d:
        raise HTTPException(422, "nothing to change")
    try:
        res = wl_cell.update_service(raw, area, cid, d)
    except ValueError as ex:
        raise HTTPException(422, str(ex))
    return {"saved": _npc_write(side, area_id, bytes(raw)), **res}


@app.get("/api/area/{side}/{area_id}/grp16")
def area_grp16(side: int, area_id: int) -> dict:
    """grp16 per-area event code: fired event ids + a minimal 6502 listing each, PLUS the static
    facts wl_grp16 derives from that listing — which EVENT bytes the handler reads, which records it
    resolves, which engine calls it makes. Those sit next to the hand-written note in the UI: the
    machine says what the code touches, the note says what it means."""
    area, _, _, _ = _load_area_full(side, area_id)
    try:
        out = wl_cell.area_grp16(area)
    except Exception:
        return {"table": None, "events": []}
    try:
        ir = _ir(side, area_id)
        out["facts"] = {str(k): v for k, v in wl_grp16.analyze(ir).items()}
        # may this blob be MOVED? Relevant the moment a blueprint carries grp16 into another area:
        # the code is position-dependent, and some of it cannot be relocated at all (wl_reloc).
        out["reloc"] = (ir.get("grp16") or {}).get("reloc")
    except Exception as ex:                          # never let the analysis break the listing
        out["facts"] = {}
        print(f"[wl_grp16] area {side}:{area_id}: {ex}")
    return out


@app.get("/api/scene/{side}/{sel}")
def scene_code(side: int, sel: int) -> dict:
    """The $CA00 SERVICE-handler blob for a selector (s{side}_scene_id{sel}.bin), disassembled —
    so the editor can SHOW the scene code like the grp16 snippets (a shop/doc/… handler is 6502,
    not a record field). Loaded at its residence $CA00."""
    import wl_disasm6502 as d65
    pth, src = _resolve(side, "scene", sel)
    if not pth.is_file():
        raise HTTPException(404, f"no scene blob s{side}_scene_id{sel:02d}.bin")
    blob = pth.read_bytes()
    ORG = 0xCA00
    return {"side": side, "sel": sel, "file": _rel(pth), "source": src, "org": ORG,
            "size": len(blob), "listing": d65.disasm(blob, 0, ORG, max_instr=160)}


def _grp16_notes_file():
    p = REPO / "editor" / "annotations" / "grp16_effects.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class Grp16Note(BaseModel):
    event: int
    note: str


@app.get("/api/area/{side}/{area_id}/grp16-notes")
def get_grp16_notes(side: int, area_id: int) -> dict:
    """Hand-written effect annotations for grp16 events (what the code DOES to the map/
    world — feeds the 'script changes' overlay for code-driven events)."""
    f = _grp16_notes_file()
    allnotes = json.loads(f.read_text()) if f.is_file() else {}
    return {"notes": allnotes.get(f"{side}:{area_id}", {})}


@app.post("/api/area/{side}/{area_id}/grp16-notes")
def save_grp16_note(side: int, area_id: int, n: Grp16Note) -> dict:
    f = _grp16_notes_file()
    allnotes = json.loads(f.read_text()) if f.is_file() else {}
    key = f"{side}:{area_id}"
    allnotes.setdefault(key, {})
    if n.note.strip():
        allnotes[key][str(n.event)] = n.note.strip()
    else:
        allnotes[key].pop(str(n.event), None)     # empty note = delete
    f.write_text(json.dumps(allnotes, indent=2, ensure_ascii=False) + "\n")
    return {"saved": _rel(f), "events": len(allnotes[key])}


def _render_portrait_png(blob: bytes) -> bytes:
    """Decode a str blob's base still → a 352×336 PNG (fixed portrait palette). Live render
    so an EDITED portrait's thumbnail is always current (no stale portraits/all PNG).

    352x336 = 8x horizontally, 4x vertically — a WHOLE multiple of the 44x84 grid, which is also
    the true multicolour 2:1 pixel aspect. This used to be 352x352, i.e. 4.19x vertically: not a
    whole scale, so rows were duplicated unevenly and an export->edit->import round trip came back
    with a few hundred pixels shifted (measured: 469 of 3696 on str 79). At 8x4 it is exact."""
    import io
    from PIL import Image
    import wl_portrait_anim as anim
    if MACHINE == "apple":
        # A different format, not a different constant: the Apple keeps the picture in two
        # row-interleaved fields of hi-res bytes. wl_portrait_a2 says why and how.
        return wl_portrait_a2.png(blob)
    grid = _bitmap_pixels(anim.decode_base(blob))          # 84×44, 2-bit
    pal = [wl_tiles.PEPTO[i] for i in PORTRAIT_PAL]
    im = Image.new("RGB", (44, 84))
    for y, row in enumerate(grid):
        for x, v in enumerate(row):
            im.putpixel((x, y), pal[v])
    im = im.resize((44 * 8, 84 * 4), Image.NEAREST)        # 8×4 per MC pixel — exact, 2:1
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


@app.get("/api/portrait/{side}/{str_id}")
def get_portrait(side: int, str_id: int):
    """Portrait thumbnail — RENDERED LIVE from the current blob (work over baseline), so edits
    show at once. Falls back to the static portraits/all PNG only if the blob won't decode."""
    from fastapi.responses import Response
    blob_path, _, _ = _str(str_id)
    if blob_path is not None:
        try:
            png = _render_portrait_png(blob_path.read_bytes())
            # ETag on the file mtime+size so the browser refetches after a save
            st = blob_path.stat()
            return Response(png, media_type="image/png",
                            headers={"ETag": f'"{st.st_mtime_ns}-{st.st_size}"'})
        except Exception:
            pass
    p = _portrait_png(str_id)
    if p is None:
        raise HTTPException(404, f"no portrait for str {str_id}")
    return FileResponse(p, media_type="image/png")


@app.get("/api/portrait/{side}/{str_id}/anim")
def get_portrait_anim(side: int, str_id: int) -> dict:
    """Decode a portrait's animation (4-slot EOR anim-object) into RGBA frames + per-frame delays.
    Source = the str blob s{side}_str_id{strid}.bin (RAM $CA00 anim-object)."""
    try:
        import wl_portrait_anim as anim
    except ImportError:
        raise HTTPException(503, "portrait animator not available yet")
    blob_path, _, _ = _str(str_id)
    if blob_path is None:
        raise HTTPException(404, f"no str blob s{side}_str_id{str_id:02d}")
    blob = blob_path.read_bytes()
    if MACHINE == "apple":
        # Same four slots as the C64 — the count of live ones matches per portrait on 87 of 88.
        # The Apple runs them through its ordinary draw routine rather than a separate EOR pass;
        # wl_portrait_a2 says how.
        seq = wl_portrait_a2.anim_frames(blob)
        grids = seq["grids"]
        return {"w": len(grids[0][0]) * 7, "h": len(grids[0]),
                "frames": [base64.b64encode(wl_portrait_a2.rgba(g)).decode("ascii") for g in grids],
                "delays": seq["delays"]}
    seq = anim.anim_frames(blob)
    frames_b64 = [base64.b64encode(anim.bitmap_to_rgba(f)).decode("ascii") for f in seq["frames"]]
    return {"w": anim.ANIM_W, "h": anim.ANIM_H, "frames": frames_b64, "delays": seq["delays"]}


@app.get("/api/area/{side}/{area_id}/cell/{col}/{row}")
def inspect_cell(side: int, area_id: int, col: int, row: int) -> dict:
    """Decode what fires at a map cell: op-type, dispatch, and (as decoders land) the op record,
    script/logic, loot, NPC + portrait, grp16 code reference."""
    area, tileids, _, _ = _load_area_full(side, area_id)
    if not (0 <= col < area.width and 0 <= row < area.height):
        raise HTTPException(422, "cell out of bounds")
    i = row * area.width + col
    op = area.nibble[i]
    disp = area.cellid[i]
    tile = tileids[i] if i < len(tileids) else None
    out = {
        "col": col, "row": row, "op": op, "op_name": _OP_NAMES.get(op, "?"),
        "dispatch": disp, "tile": tile,
        "sections": inspect_op(area, col, row, op, disp),   # per-op decode (wl_cell)
        "editable": wl_cell.editable_fields(area, col, row, op, disp),
    }
    if op == 0xB:                                           # op-B: hand the editor the chain to edit
        out["chain"] = wl_cell.chain_links(area, disp)
    return out


@app.get("/api/area/{side}/{area_id}/mutations")
def area_mutations(side: int, area_id: int) -> dict:
    """Every cell whose script rewrites the map (op-2 DONE/ALT, op-C CELL_PATCH) — the 'scripted
    changes' list + map overlay."""
    area, tileids, _, _ = _load_area_full(side, area_id)
    ir = _ir(side, area_id)
    tp = lambda c, r, op, cid: wl_cell._tile_pair(area, tileids, c, r, op, cid)   # noqa: E731
    muts = wl_view.area_mutations(ir, lambda op, cid: wl_cell._target_label(area, op, cid), tp,
                                  _cond_summary_ir(area, ir),
                                  lambda idx: wl_cell._prose(area, idx))
    # Reverse index: a cell needs to know what changes IT, not only what it changes. Base Cochise
    # (2,3) is the target of three patches fired from the far corner of the map - unfindable from
    # the cell itself in a source-keyed list.
    incoming: dict[str, list] = {}
    for m in muts:
        for t in m.get("targets") or []:
            key = f"{t['cell'][0]},{t['cell'][1]}"
            incoming.setdefault(key, []).append(
                {"source": m["source"], "op": m["op"], "trigger": m.get("trigger"),
                 "branch": t.get("branch"), "op_nibble": t["op_nibble"], "cell_id": t["cell_id"],
                 "tile_now": t.get("tile_now"), "tile_after": t.get("tile_after"),
                 "label": t.get("label")})
    chains = wl_view.area_chains(ir, lambda op, cid: wl_cell._state_text(area, op, cid),
                                 lambda c, r, op, cid: wl_cell.display_tile(
                                     area, op, cid,
                                     tileids[r * area.width + c] if tileids else 0))["cells"]
    return {"mutations": muts, "incoming": incoming, "chains": chains}


@app.get("/api/area/{side}/{area_id}/flow")
def area_flow(side: int, area_id: int) -> dict:
    """Script chains grouped into events — the Flow tab. Cached: rebuilding walks every record."""
    key = (side, area_id, _record_rev(side, area_id))
    hit = _FLOW_CACHE.get(key)
    if hit is None:
        area, tileids, _, _ = _load_area_full(side, area_id)
        hit = wl_cell.area_flow(area, tileids)
        # area_flow only knows chains that REWRITE THE MAP. A door handing over to a service
        # rewrites nothing, so TREX Base listed one event while having twelve services. These are
        # the record-to-record chains (wl_view.record_chains).
        try:
            hit["handovers"] = wl_view.record_chains(
                _ir(side, area_id), area, lambda op, cid: wl_cell._state_text(area, op, cid))
        except Exception as ex:                       # noqa: BLE001
            hit["handovers"] = []
            print(f"[record_chains] {side}:{area_id}: {ex}")
        _FLOW_CACHE.clear()                 # keyed by record revision: a save invalidates it
        _FLOW_CACHE[key] = hit
    # Which of these events belong to a QUESTLINE. Tagged outside the cache because the quests file
    # changes independently of the record: an event is part of a story exactly when one of the
    # records it dispatches is a step of one, which is what lets the view separate the two.
    cl = _quest_claims().get((side, area_id), {})
    for ev in hit.get("events", []):
        for r in ev.get("rows", []):
            for st in r.get("states", []):
                pr = wl_cell.paragraph_refs(st.get("text"))
                if pr:
                    st["paragraphs"] = pr
        qs = sorted({q for r in ev.get("rows", []) for st in r.get("states", [])
                     for q in cl.get((st.get("op_nibble"), st.get("cell_id")), [])})
        ev["quests"] = qs
    hit["quest_records"] = len(cl)
    return hit


# ═══════════════ TELEPORT ═══════════════
# The teleport table (docs/features/LOCATION_TABLE.md) drops the party INSIDE an area at an
# arrival cell. The cell + name are AUTHORED here: the reverse-index prefills the coord from the
# incoming op-$A transition(s) that target the area, and the editor lets a human confirm/override
# the coord, rename it, and toggle whether the area is teleportable. Overrides live in
# area_names.json (allow_teleport + coord + name); wl_build_teleport_table.py honours them.

_NAMES_JSON = ASSETS / "areanames" / "area_names.json"
_TP_CACHE: dict | None = None


def _load_names_json() -> dict:
    import json
    if _NAMES_JSON.is_file():
        try:
            return json.loads(_NAMES_JSON.read_text())
        except ValueError:
            pass
    return {}


def _build_teleport() -> dict:
    """Scan every area's op-$A transitions → reverse-index (incoming per target) + forward edges
    (per source, for the tree) + per-area teleport config from area_names.json. Cached; the cache
    is cleared whenever a config is saved."""
    global _TP_CACHE
    if _TP_CACHE is not None:
        return _TP_CACHE
    names = _load_names_json()
    areas: dict[str, dict] = {}
    incoming: dict[str, list] = {}
    outgoing: dict[str, list] = {}
    for a in list_areas()["areas"]:
        aid, side = a["area_id"], a["side"]
        key = str(aid)
        try:
            area, _, _, _ = _load_area_full(side, aid)
            trans = wl_view.area_transitions(_ir(side, aid),
                                             lambda idx: wl_cell._prose(area, idx))
        except Exception:
            continue
        # dedupe by area-id: prefer the record that actually has a map + transitions
        if key in areas and not (a["has_map"] and trans):
            continue
        areas[key] = {"side": side, "name": a["name"] or f"area {aid}", "has_map": a["has_map"]}
        outgoing[key] = []
        for t in trans:
            outgoing[key].append({"to": t["target"], "arrive": t["arrive"],
                                  "message": t["message"], "party_relative": t["party_relative"],
                                  "is_return": t["is_return"], "source": t.get("source", "cell")})
            if t["is_return"] or t["target"] >= 0x80:
                continue
            incoming.setdefault(str(t["target"]), []).append({
                "from": aid, "from_side": side, "from_name": a["name"] or f"area {aid}",
                "cell": t["cell"], "arrive": t["arrive"], "message": t["message"],
                "party_relative": t["party_relative"], "source": t.get("source", "cell"),
            })
    # dedupe each target's incoming by (source, arrive, party-relative) — many areas repeat the same
    # "leaving X" arrival across dozens of cells; the distinct arrival cells are what matters.
    for tgt, lst in incoming.items():
        seen, uniq = set(), []
        for e in lst:
            k = (e["from"], e["arrive"][0], e["arrive"][1], e["party_relative"])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(e)
        incoming[tgt] = uniq
    # overlay hand-authored "manual" edges: transitions the op-$A scan CANNOT see because the
    # destination is chosen by grp16 code / a keyword menu, not encoded in any record — the Sleeper
    # ladder (op-8 KEYWORD → lvl 1/2/3) and the Mindlink (area-22 head-COND → grp16 load). Stored in
    # area_names.json "_manual_edges": [{from, to, arrive:[x,y], note}]. They wire the tree (so these
    # areas leave the orphan list) and give teleport a real arrival coord.
    for e in (names.get("_manual_edges") or []):
        try:
            fr, to = int(e["from"]), int(e["to"])
            ax, ay = int(e["arrive"][0]) & 0xFF, int(e["arrive"][1]) & 0xFF
        except (KeyError, ValueError, TypeError, IndexError):
            continue
        note, fk = str(e.get("note", "")), str(int(e["from"]))
        outgoing.setdefault(fk, []).append({"to": to, "arrive": [ax, ay], "message": note,
                                            "party_relative": False, "is_return": False, "source": "manual"})
        incoming.setdefault(str(to), []).append({
            "from": fr, "from_side": areas[fk]["side"] if fk in areas else 0,
            "from_name": areas[fk]["name"] if fk in areas else f"area {fr}",
            "cell": None, "arrive": [ax, ay], "message": note,
            "party_relative": False, "source": "manual"})
    config = {}
    for k, v in names.items():
        if not (isinstance(k, str) and k.isdigit() and isinstance(v, dict)):
            continue
        config[k] = {"name": v.get("name", ""), "coord": v.get("coord"),
                     "allow_teleport": v.get("allow_teleport"), "review": bool(v.get("review"))}
    _TP_CACHE = {"areas": areas, "incoming": incoming, "outgoing": outgoing, "config": config}
    return _TP_CACHE


# ── the RESOLVED teleport table = the 2-level tree the game menu packs. Level-0 = the overworld's
# direct exits (area-0 outgoing) that are teleport-enabled; every deeper enabled area is Level-1
# under its Level-0 ancestor (DFS over the outgoing edges, INCLUDING the hand-authored _manual_edges
# that wire grp16/keyword descents the op-$A scan can't see). This is the SAME tree the Overview tab
# draws — written to teleport_table.json so the build PACKS the editor's structure instead of
# re-deriving it: the old build heuristic collapsed the grouping when area 0's transition array was
# relocated (the TREX-slot edit), flattening every subarea to Level-0 and overflowing the menu. ──
def _s8(v):
    return v - 256 if v > 127 else v


def _tp_candidates(tp, key):
    raw = [e for e in tp["incoming"].get(key, []) if e["from"] != int(key)]
    out = []
    for e in raw:
        cond, man = e.get("source") == "subtable", e.get("source") == "manual"
        if e["party_relative"]:
            if not e.get("cell"):
                continue
            dx, dy = _s8(e["arrive"][0]), _s8(e["arrive"][1])
            ax, ay = e["cell"][0] + dx, e["cell"][1] + dy
            if (dx == 0 and dy == 0) or ax < 0 or ay < 0:
                continue
            out.append({"arrive": [ax, ay], "from": e["from"], "rel": True, "cond": cond, "man": man})
        else:
            if e["arrive"][0] == 0 and e["arrive"][1] == 0:
                continue
            out.append({"arrive": e["arrive"], "from": e["from"], "rel": False, "cond": cond, "man": man})
    tier = lambda c: 3 if c["rel"] else 1 if c["man"] else 2 if c["cond"] else 0
    out.sort(key=lambda c: (tier(c), 0 if c["from"] == 0 else 1, c["from"]))
    seen, uniq = set(), []
    for c in out:
        k = (c["arrive"][0], c["arrive"][1])
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    return uniq


def _tp_status(tp, key):
    cfg = tp["config"].get(key, {})
    cand = _tp_candidates(tp, key)
    coord = cfg.get("coord") or (cand[0]["arrive"] if cand else None)
    name = cfg.get("name") or tp["areas"].get(key, {}).get("name", "")
    allow = (len(cand) > 0) if cfg.get("allow_teleport") is None else bool(cfg.get("allow_teleport"))
    return {"allow": allow, "coord": coord, "name": name}


def _teleport_rows(tp):
    """The resolved rows [{region, area, drop_area, x, y, name}], sorted by region (head first).
    A Level-0 head has region==area; a subarea's region is its Level-0 ancestor. Mirrors the
    Overview tab's DFS from area 0 over `outgoing` (incl. _manual_edges)."""
    outgoing = tp["outgoing"]

    def kids(key):
        seen = []
        for e in outgoing.get(key, []):
            to = e["to"]
            if e["is_return"] or to >= 0x80 or to == 0 or to == int(key):
                continue
            if to not in seen:
                seen.append(to)
        return seen

    expanded, region, order = {"0"}, {}, []
    for c in kids("0"):                       # each Level-0 exit claims its whole subtree
        stack = [str(c)]
        while stack:
            n = stack.pop(0)
            if n in expanded:
                continue
            expanded.add(n)
            region[n] = c
            order.append(n)
            for k in kids(n):
                if str(k) not in expanded:
                    stack.append(str(k))
    l0 = kids("0")
    rows = []

    def add(key, reg):
        st = _tp_status(tp, key)
        if not st["allow"]:
            return
        x, y = (st["coord"] or [0, 0])[:2]
        rows.append({"region": reg, "area": int(key), "drop_area": int(key),
                     "x": x & 0xFF, "y": y & 0xFF, "name": (st["name"] or f"Area {key}")[:16]})

    add("0", 0)                               # Ranger Center = the desert overworld head
    for c in l0:
        add(str(c), c)                        # Level-0 heads (overworld exits)
    for n in order:
        if int(n) not in l0:
            add(n, region[n])                 # Level-1, grouped under its ancestor
    rows.sort(key=lambda r: (r["region"], r["area"] != r["region"], r["area"]))
    return rows


_TP_TABLE = ASSETS / "areanames" / "teleport_table.json"


def _write_teleport_table():
    """Recompute + persist the resolved teleport table so the build packs the editor's structure."""
    rows = _teleport_rows(_build_teleport())
    _TP_TABLE.write_text(json.dumps(rows, indent=2) + "\n")
    return rows


@app.get("/api/teleport")
def teleport_index() -> dict:
    """Everything the teleport authoring UI needs: per-area name/config, the incoming-transition
    reverse-index (coord prefill + multi-entry detection), and forward edges for the tree."""
    return _build_teleport()


@app.post("/api/teleport/export")
def teleport_export() -> dict:
    """Write teleport_table.json (the resolved 2-level rows the build packs)."""
    rows = _write_teleport_table()
    return {"saved": _rel(_TP_TABLE), "rows": len(rows),
            "level0": sum(1 for r in rows if r["region"] == r["area"])}


# ═══════════════ GFX (title + endgame pictures) ═══════════════
# The non-portrait, non-map pictures, in two groups:
#   * title   — the WASTELAND logo (64-glyph grid) + the globe (str anim-object = portrait format).
#   * endings — the endgame scene frames (Cochise dome / ranger-death skeletons / all-dead finale),
#               each a $0586 anim-object too (verified: 44x84 / 44x84 / 144x128, live anim slots).
# All are depacked/decoded from the g64 at build time; to make them EDITABLE they are persisted to
# data/assets/<group>/ and the editor prefers that source over the g64 decode. Every anim-object
# rides the portrait tooling (wl_portrait_anim / _encode / _animedit) with the box from its own
# header; the logo is a glyph grid. PNG round-trip codec = tools/wl_title_assets.py.
# See [[wasteland-title-cinematics]].
_GFX_ASSETS_DIR = ASSETS
_GFX_BUILD_DIR = None            # no cartridge build in this repo — source assets only
_GFX_ASSETS_C64 = {
    "logo":  {"file": "loadA_intro_image.bin", "kind": "glyph", "group": "title", "srcdir": "title",
              "fallback": None,
              "label": "WASTELAND logo", "desc": "64-glyph 4x16 grid, fixed 4-colour window"},
    "globe": {"file": "loadB_intro_text.bin", "kind": "anim", "group": "title", "srcdir": "title",
              "fallback": None,
              "label": "Globe (Erdball)", "desc": "str anim-object (portrait format), box 36x135"},
    "dome":  {"file": "endgame_t3b2.bin", "kind": "anim", "group": "endings", "srcdir": "endings",
              "fallback": None,
              "label": "Cochise dome", "desc": "endgame scene · 44x84 anim-object, 2 slots"},
    "skeletons": {"file": "endgame_t2b12.bin", "kind": "anim", "group": "endings", "srcdir": "endings",
              "fallback": None,
              "label": "Ranger-death skeletons", "desc": "endgame scene · 44x84 anim-object"},
    "finale": {"file": "endgame_t1b20.bin", "kind": "anim", "group": "endings", "srcdir": "endings",
              "fallback": None,
              "label": "All-dead finale", "desc": "endgame scene · 144x128 anim-object, 4 slots"},
}

# The Apple II keeps the same pictures, and four of the five keys above carry over unchanged so the
# frontend's two panes need no machine of their own. Two things do NOT carry over:
#
#   * `logo` is absent. The Apple title screen has no logo asset — its text is drawn with the
#     engine's ordinary renderer, and the title module loads exactly one picture, the globe. An
#     empty tile that can never fill would be a lie, so the key is simply not here.
#   * a fifth entry, `dome_anim`. The Apple stores the Cochise dome twice, with byte-identical
#     base streams and different animations over them — one live slot on one copy, two on the
#     other. The C64 has one blob where the Apple has two, so this is an extra key rather than a
#     renamed one.
#
# The container is the Apple portrait anim-object (backend/wl_portrait_a2.py), not the C64's $0586
# object: same four header numbers, different ORDER ([col0][col1][row0][row1] against the C64's
# [col0][row0][col1][row1]), 7 hi-res pixels per byte column instead of 4 multicolour ones, and
# artefact colour instead of a palette. That is why the renderer is switched, not parameterised.
# Provenance for every blob is in tools/wl_extract_title_endgame.py.
_GFX_ASSETS_A2 = {
    "globe": {"file": "a2_title_globe.bin", "kind": "anim", "group": "title", "srcdir": "title",
              "fallback": None,
              "label": "Globe (Erdball)", "desc": "hi-res anim-object, 252x127 px, 4 slots · "
                                                  "master 1 T04/S06, codec budget 22"},
    "dome":  {"file": "a2_end_dome.bin", "kind": "anim", "group": "endings", "srcdir": "endings",
              "fallback": None,
              "label": "Cochise dome", "desc": "endgame scene · 84x83 px, 1 slot · master 4 "
                                               "T03/S0F, codec budget 15"},
    "dome_anim": {"file": "a2_end_dome_anim.bin", "kind": "anim", "group": "endings",
              "srcdir": "endings", "fallback": None,
              "label": "Cochise dome (2nd animation)",
              "desc": "same still, 2 slots · master 4 T02/S0C, codec budget 12"},
    "skeletons": {"file": "a2_end_skeletons.bin", "kind": "anim", "group": "endings",
              "srcdir": "endings", "fallback": None,
              "label": "Death silhouette", "desc": "endgame scene · 84x83 px, 1 slot · master 4 "
                                                   "T02/S00, codec budget 16"},
    "finale": {"file": "a2_end_finale.bin", "kind": "anim", "group": "endings", "srcdir": "endings",
              "fallback": None,
              "label": "All-dead finale", "desc": "endgame scene · 252x127 px, 4 slots · "
                                                  "master 4 T01/S00, codec budget 17"},
}
_GFX_ASSETS = _GFX_ASSETS_A2 if MACHINE == "apple" else _GFX_ASSETS_C64


def _gfx_source(name):
    """(path, origin) for a gfx asset — the editable source (cart_EF/assets/<group>/) first, else the
    build decode so a fresh checkout still previews. None if neither exists."""
    a = _GFX_ASSETS.get(name)
    if not a:
        return None, None
    src = _GFX_ASSETS_DIR / a["srcdir"] / a["file"]
    if src.is_file():
        return src, "source"
    # Upstream had a second place to look: the cartridge build's decode output. There is no
    # build here — bootstrap.py writes the decoded assets straight into data/assets/, so the
    # editable source IS the only source.
    if not a.get("fallback"):
        return None, None
    out = a["fallback"] / a["file"]
    return (out, "build-out") if out.is_file() else (None, None)


def _title_mod():
    import sys
    tp = str(HERE.parent / "tools")
    if tp not in sys.path:
        sys.path.insert(0, tp)
    import wl_title_assets
    return wl_title_assets


def _gfx_png_bytes(name, blob):
    """Render a gfx asset blob to PNG bytes via the wl_title_assets codec (temp file)."""
    import os, tempfile
    if MACHINE == "apple":
        # Same reason as _render_portrait_png: a different format, not a different constant.
        # Every Apple gfx asset is an anim-object in the portrait container, so one call does all
        # of them — there is no Apple glyph asset.
        return wl_portrait_a2.png(blob, scale=3)
    ta = _title_mod()
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        (ta.logo_to_png if name == "logo" else ta.globe_to_png)(blob, tmp)
        return open(tmp, "rb").read()
    finally:
        os.unlink(tmp)


@app.get("/api/gfx")
def gfx_list() -> dict:
    """The editable graphics (title: logo + globe; endings: dome/skeletons/finale), with the live
    source origin. `group` lets the frontend split them into the Title and Endings tabs."""
    out = []
    for name, a in _GFX_ASSETS.items():
        p, origin = _gfx_source(name)
        out.append({"name": name, "label": a["label"], "kind": a["kind"], "group": a["group"],
                    "desc": a["desc"], "present": p is not None, "origin": origin,
                    # An Apple root previews but does not edit: the pixel editor and the anim
                    # editor are both written against the C64 multicolour object. Saying so here
                    # lets the pane drop its ✎ button rather than open an editor that 409s.
                    "editable": MACHINE == "c64"})
    return {"assets": out}


@app.get("/api/gfx/{name}/preview.png")
def gfx_preview(name: str):
    p, _ = _gfx_source(name)
    if p is None:
        raise HTTPException(404, f"no gfx asset '{name}'")
    return Response(content=_gfx_png_bytes(name, p.read_bytes()), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


class GfxImport(BaseModel):
    png_b64: str
    apply: bool = False          # False = validate + preview only (same contract as portraits)
    crop_x: int | None = None    # same crop contract as the portrait import
    crop_y: int | None = None


def _gfx_c64_only():
    """The two editor GETs decode with the C64 multicolour model — refuse them on an Apple root.

    They would not raise on Apple bytes, they would answer with a plausible-looking 44x84 grid of
    nonsense, which is worse. The write halves are already refused by _machine_readonly; this is
    the read half of the same stop."""
    if MACHINE != "c64":
        raise HTTPException(409, "the picture editor decodes the C64 multicolour anim-object; an "
                                 "Apple II root stores hi-res artefact-colour objects instead "
                                 "(backend/wl_portrait_a2.py). Preview only.")


def _gfx_box_wh(name, blob):
    """The (W, H) pixel box for a gfx asset: the globe reads it from its header, the logo is the
    fixed glyph grid."""
    if MACHINE == "apple":
        # The same four numbers in the other order — [first col][last col][first row][row limit] —
        # and 7 hi-res pixels to a byte column. wl_portrait_a2 has the draw loop this comes from.
        c4, c5, c2, c3 = wl_portrait_a2.bounds(blob)
        return (c5 - c4 + 1) * 7, (c3 - c2)
    if _GFX_ASSETS[name]["kind"] == "anim":
        C1, BF, C2, C0 = blob[0], blob[1], blob[2], blob[3]
        return (C2 - C1 + 1) * 4, (C0 - BF + 1)
    ta = _title_mod()
    return ta.LOGO_COLS * 4, ta.LOGO_ROWS * 8


@app.post("/api/gfx/{name}/import")
def gfx_import(name: str, req: GfxImport) -> dict:
    """Import a PNG into a title asset (globe/logo), same validate-first contract as the portrait
    editor. apply=True re-encodes the base grid and writes the editable source (data/assets/title/);
    for the globe the animation slots are preserved via the template. The build then packs the source
    instead of depacking the g64."""
    a = _GFX_ASSETS.get(name)
    if not a:
        raise HTTPException(404, f"no gfx asset '{name}'")
    p, _ = _gfx_source(name)
    if p is None:
        raise HTTPException(404, f"no gfx asset '{name}'")
    template = p.read_bytes()
    W, H = _gfx_box_wh(name, template)
    out = _png_to_grid_validated(req.png_b64, W, H, req.crop_x, req.crop_y)
    if req.apply and out.get("ok"):
        try:
            blob = _gfx_grid_to_blob(name, out["pixels"], template)
        except Exception as ex:
            raise HTTPException(422, f"encode failed: {ex}")
        dst = _gfx_write_source(name, blob)
        out["applied"] = True
        out["saved"] = _rel(dst)
        out["bytes"] = len(blob)
    return out


# ── in-browser pixel editor: both assets → a 2-bit grid + the FIXED 4-colour window palette (same
# scheme as portraits: 0=black 1=red 2=blue 3=white). The globe rides the portrait bitmap path with
# its OWN box (from its header); the logo is a 64-glyph grid (4 MC-px per glyph column). ──
def _gfx_pixels_grid(name, blob):
    ta = _title_mod()
    if name == "logo":
        LC, LR = ta.LOGO_COLS, ta.LOGO_ROWS          # 4 x 16 glyphs
        w, h = LC * 4, LR * 8                          # 16 x 128 MC pixels
        grid = [[0] * w for _ in range(h)]
        for g in range(LC * LR):
            gx, gy = (g % LC) * 4, (g // LC) * 8
            for row in range(8):
                byte = blob[g * 8 + row]
                for p in range(4):
                    grid[gy + row][gx + p] = (byte >> ((3 - p) * 2)) & 3
        return grid
    import wl_portrait_anim as anim
    bm = anim.decode_base(blob)
    C1, BF, C2, C0 = blob[0], blob[1], blob[2], blob[3]
    rows = []
    for y in range(BF, C0 + 1):
        r = []
        for col in range(C1, C2 + 1):
            addr = (y >> 3) * 320 + (y & 7) + col * 8
            b = bm[addr] if addr < len(bm) else 0
            for p in range(4):
                r.append((b >> (6 - 2 * p)) & 3)
        rows.append(r)
    return rows


def _gfx_grid_to_blob(name, grid, template):
    ta = _title_mod()
    if name == "logo":
        LC, LR = ta.LOGO_COLS, ta.LOGO_ROWS
        out = bytearray(512)
        for g in range(LC * LR):
            gx, gy = (g % LC) * 4, (g // LC) * 8
            for row in range(8):
                byte = 0
                for p in range(4):
                    byte |= (grid[gy + row][gx + p] & 3) << ((3 - p) * 2)
                out[g * 8 + row] = byte
        return bytes(out)
    import wl_portrait_encode as enc
    C1, BF, C2, C0 = template[0], template[1], template[2], template[3]
    bm = bytearray(8192)
    for ry, y in enumerate(range(BF, C0 + 1)):
        for cc, col in enumerate(range(C1, C2 + 1)):
            addr = (y >> 3) * 320 + (y & 7) + col * 8
            byte = 0
            for p in range(4):
                byte |= (grid[ry][cc * 4 + p] & 3) << (6 - 2 * p)
            if addr < len(bm):
                bm[addr] = byte
    return enc.rebuild_animated(template, bytes(bm), dims=(C1, BF, C2, C0))


class GfxPixels(BaseModel):
    pixels: list[list[int]]


# ── The gfx assets ride the SAME editor as portraits (openPortraitEditor / pe.base): the globe is a
# $0586 anim-object so it gets the full pixel + 4-slot-animation editor (its box comes from its own
# header, driven by wl_portrait_animedit.AnimEditor(dims) — the satellite/atom-bomb title animation);
# the logo is a glyph grid, so animCapable=false → the editor shows a plain pixel canvas. Contract is
# byte-identical to /api/portrait/{side}/{str_id}/{bitmap,anim-edit,import}. See [[wasteland-title-cinematics]]. ──
def _gfx_globe_dims(blob):
    return (blob[0], blob[1], blob[2], blob[3])


def _gfx_write_source(name, blob):
    a = _GFX_ASSETS[name]
    d = _GFX_ASSETS_DIR / a["srcdir"]
    d.mkdir(parents=True, exist_ok=True)
    dst = d / a["file"]
    dst.write_bytes(blob)
    return dst


@app.get("/api/gfx/{name}/bitmap")
def gfx_bitmap(name: str) -> dict:
    """The asset as a 2-bit pixel grid + the fixed 4-colour palette, for the shared pixel editor.
    animCapable = the asset is a $0586 anim-object (globe) vs a plain glyph grid (logo)."""
    a = _GFX_ASSETS.get(name)
    if not a:
        raise HTTPException(404, f"no gfx asset '{name}'")
    _gfx_c64_only()
    p, _ = _gfx_source(name)
    if p is None:
        raise HTTPException(404, f"no gfx asset '{name}'")
    grid = _gfx_pixels_grid(name, p.read_bytes())
    return {"w": len(grid[0]), "h": len(grid), "pixels": grid,
            "animCapable": a["kind"] == "anim",
            "palette": ["#%02x%02x%02x" % wl_tiles.PEPTO[i] for i in PORTRAIT_PAL]}


@app.post("/api/gfx/{name}/bitmap")
def gfx_set_bitmap(name: str, edit: GfxPixels) -> dict:
    """Encode an edited base pixel grid back into the asset → write the editable source.
    For the globe this keeps its animation (rebuild_animated preserves the template's slots)."""
    a = _GFX_ASSETS.get(name)
    if not a:
        raise HTTPException(404, f"no gfx asset '{name}'")
    p, _ = _gfx_source(name)
    if p is None:
        raise HTTPException(404, f"no gfx asset '{name}'")
    try:
        blob = _gfx_grid_to_blob(name, edit.pixels, p.read_bytes())
    except Exception as ex:
        raise HTTPException(422, f"encode failed: {ex}")
    dst = _gfx_write_source(name, blob)
    return {"saved": _rel(dst), "bytes": len(blob)}


@app.get("/api/gfx/{name}/anim-edit")
def gfx_anim_edit(name: str) -> dict:
    """Decode a gfx anim-object's 4-slot animation into the editable toggle model (globe only;
    a glyph asset returns four disabled slots so the shared editor degrades to a pixel canvas)."""
    a = _GFX_ASSETS.get(name)
    if not a:
        raise HTTPException(404, f"no gfx asset '{name}'")
    _gfx_c64_only()
    p, _ = _gfx_source(name)
    if p is None:
        raise HTTPException(404, f"no gfx asset '{name}'")
    if a["kind"] != "anim":
        blank = _gfx_pixels_grid(name, p.read_bytes())
        return {"slots": [{"enabled": False, "delay": 0, "toggle": [], "alt": blank,
                           "frames": [], "track": []} for _ in range(4)]}
    import wl_portrait_animedit as ae
    blob = p.read_bytes()
    return ae.AnimEditor(_gfx_globe_dims(blob)).decode_anim_edit(blob)


@app.post("/api/gfx/{name}/anim-edit")
def gfx_save_anim_edit(name: str, edit: AnimEdit) -> dict:
    """Build the gfx anim-object from base + custom 4-slot animation → write the editable source.
    A glyph asset ignores the slots and just re-encodes the base grid."""
    a = _GFX_ASSETS.get(name)
    if not a:
        raise HTTPException(404, f"no gfx asset '{name}'")
    p, _ = _gfx_source(name)
    if p is None:
        raise HTTPException(404, f"no gfx asset '{name}'")
    if a["kind"] != "anim":                       # glyph: base-only re-encode
        blob = _gfx_grid_to_blob(name, edit.pixels, p.read_bytes())
        return {"saved": _rel(_gfx_write_source(name, blob)), "bytes": len(blob), "slots": 0}
    import wl_portrait_animedit as ae
    dims = _gfx_globe_dims(p.read_bytes())
    slots = [{"enabled": s.enabled, "delay": s.delay, "alt": s.alt,
              "track": [{"delay": f.delay, "pixels": f.pixels} for f in s.track] if s.track else None}
             for s in edit.slots]
    report: dict = {}
    blob = ae.AnimEditor(dims).build_anim_blob(edit.pixels, slots, report=report)
    dst = _gfx_write_source(name, blob)
    return {"saved": _rel(dst), "bytes": len(blob), "slots": sum(1 for s in edit.slots if s.enabled),
            "auto_closed": report.get("auto_closed", [])}


class TeleportConfig(BaseModel):
    allow_teleport: bool
    name: str
    coord: list[int] | None = None      # [X, Y] confirmed arrival, or null to leave unset


@app.post("/api/teleport/config/{area_id}")
def teleport_config_save(area_id: int, c: TeleportConfig) -> dict:
    """Persist an area's teleport override into area_names.json (name + allow_teleport + coord),
    clearing its review flag. Preserves every other key/entry. Invalidates the caches."""
    import json
    global _TP_CACHE, _NAMES
    data = _load_names_json()
    key = str(area_id)
    entry = data.get(key) if isinstance(data.get(key), dict) else {}
    entry["name"] = c.name[:14]
    entry["allow_teleport"] = bool(c.allow_teleport)
    if c.coord and len(c.coord) == 2:
        entry["coord"] = [c.coord[0] & 0xFF, c.coord[1] & 0xFF]
    entry["review"] = False
    data[key] = entry
    _NAMES_JSON.write_text(json.dumps(data, indent=2) + "\n")
    _TP_CACHE = None            # rebuild index next GET
    _NAMES = None               # app.py name cache
    wl_cell._AREANAMES = None   # wl_cell name cache
    try:
        _write_teleport_table()   # keep the build-facing table in step with the curation
    except Exception:
        pass                      # never let table-regen break the config save
    return {"saved": _rel(_NAMES_JSON), "area_id": area_id, "config": entry}


class ScriptEdit(BaseModel):
    edits: list[dict]   # [{off:int, value:int}] — single-byte, in-place patches into the record blob


@app.post("/api/area/{side}/{area_id}/script")
def save_script(side: int, area_id: int, s: ScriptEdit) -> dict:
    """Patch script bytes IN PLACE (never resize → pointer tables stay valid). Each edit is one
    byte at a fixed record offset (from /cell → editable). Reads the CURRENT resolved record so
    edits accumulate with gfx/other edits, writes to work/."""
    rec_path, _ = _resolve(side, "record", area_id)
    if not rec_path.is_file():
        raise HTTPException(404, "no record")
    _push_area_undo(side, area_id)
    rec = bytearray(rec_path.read_bytes())
    changed = 0
    for e in s.edits:
        # `width` (default 1) lets a field span two bytes, stored LE — LOOT's money amount is
        # [$5E|bit7][lo][hi], and writing only the low byte turned 1000 credits into 232.
        off, w = int(e["off"]), int(e.get("width", 1))
        val = int(e["value"]) & (0xFFFF if w == 2 else 0xFF)
        if w not in (1, 2):
            raise HTTPException(422, f"unsupported field width {w}")
        if not (0 <= off and off + w <= len(rec)):
            raise HTTPException(422, f"offset {off} out of range")
        for k in range(w):
            b = (val >> (8 * k)) & 0xFF
            if rec[off + k] != b:
                rec[off + k] = b
                changed += 1
    wf = _save_record(side, area_id, rec)
    return {"saved": _rel(wf), "bytes_changed": changed}


class ProseEdit(BaseModel):
    edits: list[dict]   # [{id:int, text:str}] — replace string #id's decoded text


@app.get("/api/area/{side}/{area_id}/strings")
def list_strings(side: int, area_id: int) -> dict:
    """All decoded prose strings of the record's own text5 table, by id (for the prose editor)."""
    import wl_text5_encode as tenc
    area, _, _, _ = _load_area_full(side, area_id)
    base = (area.raw[area.slot] | (area.raw[area.slot + 1] << 8)) - RAM
    if base < 0 or base + 0x3C >= len(area.raw):
        return {"strings": []}
    strs = tenc.enumerate_strings(area.raw, base, area.raw[base:base + 62])
    return {"strings": [{"id": i, "text": s} for i, s in enumerate(strs)]}


class ProseCheck(BaseModel):
    edits: list[dict]   # [{id:int, text:str}] — same shape as a save, but nothing is written


@app.post("/api/area/{side}/{area_id}/prose/validate")
def validate_prose(side: int, area_id: int, p: ProseCheck) -> dict:
    """Dry-run the whole text pool against the char table and report the budget — for the Text tab's
    Save button, so the user sees before writing whether it fits and can decide if it does not."""
    import wl_text5_encode as tenc
    area, _, _, _ = _load_area_full(side, area_id)
    base = (area.raw[area.slot] | (area.raw[area.slot + 1] << 8)) - RAM
    if base < 0 or base + 0x3C >= len(area.raw):
        return {"ok": True, "free": 0, "needed": [], "fits": True, "overflow": [], "droppable": [],
                "note": "no text region"}
    strings = tenc.enumerate_strings(area.raw, base, area.raw[base:base + 62])
    for e in p.edits:
        sid, text = int(e["id"]), str(e["text"])
        if 0 <= sid < len(strings):
            strings[sid] = text
        elif sid == len(strings):
            strings.append(text)
    b = tenc.char_budget(area.raw[base:base + 62], strings)
    b["ok"] = b["fits"]
    return b


@app.post("/api/area/{side}/{area_id}/prose")
def save_prose(side: int, area_id: int, p: ProseEdit, base_rev: str | None = None) -> dict:
    """Edit prose string CONTENT: rebuild the record's text5 region (auto-extend the char table for
    new chars, re-pack all strings) and write it back. The region is rebuilt in place if it sits at
    the blob tail, else relocated to the end with raw[slot+0] repointed — nothing else moves, so
    every other pointer/table stays valid. Reads the CURRENT resolved record so it composes with
    script/gfx edits. 422 if an edit uses a char the (already-full) char table can't fit."""
    _guard_rev(side, area_id, base_rev)
    import wl_text5_encode as tenc
    from wl_area import slot_for_area
    rec_path, _ = _resolve(side, "record", area_id)
    if not rec_path.is_file():
        raise HTTPException(404, "no record")
    _push_area_undo(side, area_id)
    raw = rec_path.read_bytes()
    slot = slot_for_area(area_id)
    base = (raw[slot] | (raw[slot + 1] << 8)) - RAM
    if base < 0 or base + 0x3C >= len(raw):
        raise HTTPException(422, "no text region")
    # Edit the STRINGS in the IR, not the bytes. `tenc.rebuild_record_text` relocates the text
    # region to the tail and orphans the old one; compaction is fail-closed and on Quartz it could
    # not prove the reclaim safe, so 1556 dead bytes stayed in the record and every prose edit added
    # another region's worth. Handing the serializer the string LIST instead rebuilds the region
    # from scratch with every pointer recomputed — no orphan exists to reclaim. Quartz: 10522 B via
    # byte surgery, 9021 B via the IR (32 B BELOW where it started).
    ir = wl_ir.record_to_ir(raw, side, area_id)
    strings = list(ir["strings"])
    changed = 0
    for e in p.edits:
        sid, text = int(e["id"]), str(e["text"])
        if sid == len(strings) and sid <= 0x7F:      # append = NEW string (SAY etc. can ref it)
            strings.append(text)
            changed += 1
        elif not (0 <= sid < len(strings)):
            raise HTTPException(422, f"string id {sid} out of range (0..{len(strings)-1}, or exactly {len(strings)} to append)")
        elif strings[sid] != text:
            strings[sid] = text
            changed += 1
    bad = tenc.unencodable(raw[base:base + 62], strings)
    if bad:
        chars = ", ".join(f"${b:02X}({chr(b)})" if 32 <= b < 127 else f"${b:02X}" for b in bad)
        raise HTTPException(422, f"char table full — these chars can't be added: {chars}")
    ir["strings"] = strings
    new = wl_ir.ir_to_record(ir)
    wf = _save_record(side, area_id, new)
    return {"saved": _rel(wf), "strings_changed": changed, "size_delta": len(new) - len(raw),
            "grew": len(new) - len(raw), "reclaimed": 0, "size": len(new), "cap": RECORD_CAP}


# ═══════════════ THE DESIGNER — top-down, from a quest to records ═══════════════
#
# Everything above this line reads UP from the bytes. This reads DOWN. The difference is not a view:
# a designed step exists BEFORE any record does, is allowed to be incomplete, and only becomes bytes
# when it is placed. That is the layer the whole editor was missing, because reverse engineering can
# only ever produce the other direction.
#
# Three states a step can be in, and the whole designer is just moving between them:
#   sketch   words. "A broken droid guards something in TREX Base." No record, no cell, no id.
#   cast     a blueprint chosen and its holes filled — who, what, which words. Still no bytes.
#   placed   compiled onto a square. Now it has grp/cid, and every view above can see it.


class _PlaceHost:
    """What wl_place needs from this app: where a blob lives, and where it is written.

    Deliberately thin. Not one method here decides anything — the rules live in wl_place, which is
    FastAPI-free so the same placement can be made from a test and, later, from the notation
    parser. The one line worth reading twice is `save_record`: it calls `wl_persist.save_record`
    directly instead of `_save_record`, because a refusal has to come back as a ValueError. If the
    serializer's "no, that record would change" arrived as an HTTPException raised from inside
    wl_place, the all-or-nothing report would be a stack trace instead of one more refusal line
    next to the others."""

    def record(self, side, area):
        p, _ = _resolve(side, "record", area)
        return p.read_bytes() if p.is_file() else b""

    def ir(self, side, area):
        return _ir(side, area)

    def map(self, side, area):
        p, _ = _resolve(side, "map", area)
        return p.read_bytes() if p.is_file() else b""

    def gfx(self, gid):
        p, _, _ = _gfx(gid)
        return p.read_bytes() if p else None

    def gfx_free_slots(self, gid):
        return _gfx_free_slots(gid)[0]

    def block(self, bid):
        import wl_block
        b = wl_block.lib_get(REPO, bid)
        return b or next((x for bs in _block_catalogue().values() for x in bs if x["id"] == bid), None)

    def undo(self, side, area):
        _push_area_undo(side, area)

    def save_record(self, side, area, data):
        wf = _work_path(side, "record", area)
        wl_persist.save_record(wf, bytes(data), side, area)     # ValueError propagates ON PURPOSE
        return _rel(wf)

    def save_map(self, side, area, data):
        wf = _work_path(side, "map", area)
        wf.parent.mkdir(parents=True, exist_ok=True)
        wf.write_bytes(bytes(data))
        return _rel(wf)

    def save_gfx(self, gid, data):
        # the undo point belongs HERE, not to the area: `host.undo()` snapshots the record and the
        # map, and a block that brings new tiles also writes a tileset up to eleven areas draw
        # from. Both sibling routes (/api/block/place, /api/blueprint/place) push it before their
        # write; without it undo restored the square and left the tiles, i.e. the stack described
        # an edit that had not happened.
        _push_gfx_undo(gid)
        _, _, home = _gfx(gid)
        wf = _work_path(home or 1, "gfx", gid)                  # stored per id, side is convention
        wf.parent.mkdir(parents=True, exist_ok=True)
        wf.write_bytes(bytes(data))
        return _rel(wf)

    def quests(self):
        # MIGRATED, the same in-memory migration `_compose_quests` answers /api/quests from. Read
        # straight off disk the file is still v1 and no quest carries a `qid`, so every binding was
        # refused with "has no qid yet" while the designer was looking at the number the list had
        # just shown it — two endpoints disagreeing about one document, and the 501 the spec
        # mandates for a binding (§2.5) was unreachable. migrate() is idempotent and save() runs it
        # again on the way out.
        d = _quests_authored()
        try:
            return wl_questlog.quests(wl_questlog.migrate(d))
        except ValueError:                        # all 256 ids spoken for — answer what is on disk
            return d

    def save_quests(self, doc):
        _quests_write(doc)

    def items(self):
        # the item descriptor table — byte 4 is the charge count, and that is what decides whether
        # a `have(item)` gate SPENDS the token it tests for (spec §8, the item-spend trap)
        return wl_item.table(REPO) or []


def _quest_step_alias(res: dict, side: int, area_id: int, col, row):
    """Give a placed step its v1 `ref` back, and return the step. A bridge, not a shape.

    A v2 step says where its bytes are with `records: ["grp3[7]"]` (spec §2.4, form C). Four
    readers still ask for the old `s1a9:grp1[21]` string and go QUIET when it is missing instead of
    complaining: the Events rail, the Flow tab, /api/quests/coverage and the designer's own step
    list all run off `wl_quest.claims()`, which parses that grammar and simply skips a step it does
    not recognise. So a step placed after this change would stop claiming the record it had just
    written, and nothing anywhere would say so.

    Both forms are therefore written. The day `claims()` learns to read `records`, this helper is
    what gets deleted — it costs a second write of quests.json per placement and buys nothing else."""
    q = res.get("quest")
    if not q or not res.get("records"):
        return None
    head = res["records"][0]                      # written last, met first — the beat on the square
    d = _quests_authored()
    steps = (d.get(q["slug"]) or {}).get("steps") or []
    if not (0 <= q["step"] < len(steps)):
        return None
    steps[q["step"]].update(
        ref=f"s{side}a{area_id}:grp{head['grp']}[{head['cid']}]",
        placed={"side": side, "area_id": area_id,
                "cell": [col, row] if col is not None and row is not None else None,
                "records": [{"role": r["role"], "op": r["grp"], "id": r["cid"]}
                            for r in res["records"]]})
    _quests_write(d)
    return steps[q["step"]]


class DesignPlace(BaseModel):
    side: int
    area: int
    blueprint: str
    values: dict | None = None
    col: int | None = None                    # col/row omitted = a hülse: records, no square
    row: int | None = None
    block: dict | str | None = None           # "la-house-001" or {"id", "x", "y"}
    quest: dict | None = None                 # {"slug", "effect"?, "to"?, "on"?} — OPTIONAL
    base_rev: str | None = None               # the record hash the caller last read, §4.1


@app.post("/api/design/place")
def design_place(p: DesignPlace):
    """THE compile path — the one place in this editor where a designed beat becomes bytes.

    A shell on purpose (spec E2/§4.1). Placing needs no quest: a quest binding is an optional field
    of the call, not its precondition, and a questfree beat becomes records and nothing else
    because the world is its own store. Everything that can refuse runs inside wl_place before the
    first byte is committed; what happens here is turning one refusal dict into one HTTP status.

    422 carries `{"refused": [{check, detail}]}` — a LIST, because a designer who fixes one
    sentence and is then told about the next one twice over stops reading them. A quest BINDING is
    not among them: it is authored, stored on the step and reported as a warning, because refusing
    four fields that compile over one that does not yet was never the right trade.

    The refusal is RETURNED, not raised: FastAPI wraps every HTTPException detail in `detail`, so
    `raise HTTPException(422, {"refused": …})` puts the list at `detail.refused` and a client
    written to §4.1 reads `body.refused` as undefined. A JSONResponse carries the documented body.

    `base_rev` gets the same 409 guard every other area-writing route has. A placement rewrites the
    whole record, so two tabs on one area would otherwise overwrite each other completely."""
    _guard_rev(p.side, p.area, p.base_rev)
    res = wl_place.place(_PlaceHost(), p.side, p.area, p.blueprint, p.values or {},
                         p.col, p.row, p.block, p.quest)
    if "refused" in res:
        return JSONResponse(status_code=res.get("status", 422),
                            content={"refused": res["refused"]})
    _quest_step_alias(res, p.side, p.area, p.col, p.row)
    return res



@app.get("/api/items")
def item_table() -> dict:
    """The 95-entry item descriptor table — price, class, and how many uses an item is made with.

    It lives at $3108 (the areatrack blob past its 8-byte header) with 8 bytes per item, and byte 4
    is what settles whether an op-1 condition costs you the item: $14A5 skips its decrement when the
    count is zero, which is why the Sleeper Base keeps your keycards and Highpool eats the Engine.
    Charges and class are the same in every areatrack; price is per shop."""
    t = wl_item.table(REPO)
    if not t:
        raise HTTPException(404, "no areatrack blob available to read the item table from")
    return {"items": [{**r, "name": wl_cell._item_name(r["id"])} for r in t],
            "consumable": sum(1 for r in t if r["consumable"]),
            "kept": sum(1 for r in t if not r["consumable"]),
            "source": "$3108 + id*8, 8 bytes per item (areatrack + $08)"}


class SplitStep(BaseModel):
    order: list | None = None        # the group indices, in the order they happen; None = as offered


@app.post("/api/quests/{slug}/steps/{idx}/split")
def design_split_step(slug: str, idx: int, sp: SplitStep) -> dict:
    """Split a gate into one step per ROLE, using the groups the record itself defines.

    What the record can say: which entries lead to the same place (alternatives — pick the lock or
    force it, the door opens either way) and which lead elsewhere (a different outcome), and whether
    a group costs a consumable. 48 records in the game have that shape.

    What it cannot say is which role comes FIRST. In Highpool a person reads "could be fixed with a
    new engine" and knows; the bytes do not. So the caller supplies the order, and the default is
    simply the order the entries appear in."""
    d = _quests_authored()
    q = d.get(slug)
    # `0 <= idx` as well as `idx < len`, the same bound the place route uses: FastAPI parses "-1"
    # happily and a negative index reaches the LAST step. Here it was worse than acting on the
    # wrong step — `q["steps"][-1:0] = made` is an empty slice, so the two halves were INSERTED in
    # front of the step instead of replacing it and the same ref ended up claimed three times.
    if q is None or not (0 <= idx < len(q.get("steps", []))):
        raise HTTPException(404, "no such step")
    st = q["steps"][idx]
    ref = st.get("ref") if isinstance(st, dict) else st
    resolved = wl_quest.resolve_step(ref, _quest_load)
    groups = resolved.get("role_groups") or []
    if len(groups) < 2:
        raise HTTPException(422, "this record's entries all lead to the same place — "
                                 "they are alternatives, not roles, and splitting them would "
                                 "claim an order the data does not have")

    order = sp.order if sp.order else list(range(len(groups)))
    if sorted(order) != list(range(len(groups))):
        raise HTTPException(422, f"order must be a permutation of 0..{len(groups) - 1}")
    made = []
    for k in order:
        g = groups[k]
        made.append({"ref": ref, "conds": g["idx"],
                     "note": (st.get("note") if isinstance(st, dict) and k == order[0] else "") or ""})
    q["steps"][idx:idx + 1] = made
    _quests_write(d)
    return {"split_into": len(made), "steps": made}

@app.get("/api/design/catalogue")
def design_catalogue() -> dict:
    """What a designer can pick from: patterns and services — two families, Spec §10 P7.

    The map blueprints used to be a third family here, and picking one led nowhere: this catalogue
    feeds the step's *shape* picker, and a shape is compiled by `wl_place`, which resolves it
    through `wl_blueprint.find()` — patterns and services only. A building chosen here therefore
    came back as "no blueprint 'Shop'" at the one moment it mattered. Buildings are cut and placed
    by their own endpoints (`/api/blueprints`, `/api/blueprint/place`) and the Blueprints workbench
    still lists them from there; what left is the offer to use one as a beat.

    A building is also the shape the split undoes (`editor/blueprints/Shop.SPLIT.md`): its map half
    belongs in the block library and its scene half is `door-to-service` with values."""
    return wl_blueprint.catalogue()


@app.get("/api/design/palette/{side}/{area_id}")
def design_palette(side: int, area_id: int) -> dict:
    """What THIS area already offers a designer, with pictures: its foes, the items, the portraits
    that exist, and how much room is left. Names and faces, never ids."""
    ir = _ir(side, area_id)
    area, _, _, _ = _load_area_full(side, area_id)
    foes = []
    for k, f in enumerate(ir.get("foes") or []):
        rec = wl_ir._unhex(f.get("record", "hex:")) if isinstance(f.get("record"), str) else b""
        t = wl_foe.toughness(rec) if len(rec) >= 8 else {}
        foes.append({"id": k + 1, "name": f.get("name", ""), "portrait": f.get("portrait_str"),
                     "toughness": t})
    now, base = _blob_lengths(side, area_id)
    return {"foes": foes,
            # the item descriptor decides whether using it costs you the item, so the designer
            # sees it while choosing rather than finding out in play
            "items": [{"id": i, "name": n, **({"charges": t[i]["charges"],
                                               "class": t[i]["class"],
                                               "consumable": t[i]["consumable"]} if i < len(t) else {})}
                      for i, n in sorted(wl_cell._ITEM_NAMES.items())
                      for t in [wl_item.table(REPO)]],
            "portraits": list_portraits().get("portraits", []),
            "services": wl_blueprint.SERVICES,
            "areas": [{"side": a["side"], "id": a["area_id"], "name": a["name"]}
                      for a in list_areas()["areas"]],
            "budget": wl_story.budget(ir, now, base),
            "skills": wl_cell.cond_vocab()}


class DesignStep(BaseModel):
    title: str | None = None
    blueprint: str | None = None
    values: dict | None = None
    note: str | None = None


@app.post("/api/quests/{slug}/steps")
def design_add_step(slug: str, s: DesignStep) -> dict:
    """Add a step to a quest that does not exist in the game yet. Words are enough."""
    d = _quests_authored()
    q = d.get(slug)
    if q is None:
        raise HTTPException(404, f"no quest {slug!r}")
    # `phase`, not `state` (spec E1): `state` now names the QUEST's stage on its axis, and one word
    # for the author's progress and the story's progress in the same file is how a gate ends up
    # reading a sketch. `kind` says which of the three step shapes this is without anyone guessing
    # from what it happens to carry.
    step = {"kind": "beat", "title": s.title or "new step", "phase": "sketch"}
    if s.blueprint:
        step.update(blueprint=s.blueprint, values=s.values or {}, phase="cast")
    if s.note:
        step["note"] = s.note
    q.setdefault("steps", []).append(step)
    _quests_write(d)
    return {"added": len(q["steps"]) - 1, "step": step}


@app.patch("/api/quests/{slug}/steps/{idx}")
def design_edit_step(slug: str, idx: int, s: DesignStep) -> dict:
    """Fill in a sketch: pick a blueprint, fill its holes. Still writes no bytes."""
    d = _quests_authored()
    q = d.get(slug)
    if q is None or not (0 <= idx < len(q.get("steps", []))):
        raise HTTPException(404, "no such step")
    st = q["steps"][idx]
    if s.title is not None:
        st["title"] = s.title
    if s.note is not None:
        st["note"] = s.note
    if s.blueprint is not None:
        st["blueprint"] = s.blueprint
    if s.values is not None:
        st["values"] = {**(st.get("values") or {}), **s.values}
    bp = wl_blueprint.find(st.get("blueprint") or "")
    if bp and not st.get("ref"):
        st["phase"] = "cast" if not wl_blueprint.missing(bp, st.get("values") or {}) else "sketch"
    _quests_write(d)
    return {"step": st, "missing": wl_blueprint.missing(bp, st.get("values") or {}) if bp else []}


@app.delete("/api/quests/{slug}/steps/{idx}")
def design_del_step(slug: str, idx: int, expect: str | None = None) -> dict:
    """Drop a step. Only the plan — a step that was already placed keeps its records; unbinding
    those is a separate, deliberate act, because deleting a record nothing knows is unreferenced
    is how a chain gets a hole in it.

    `expect` is an optional guard: the step's ref or title as the caller last saw it. An index into
    a list shifts the moment anything before it is removed, and I proved that the hard way —
    deleting index 0 of a quest whose test step was at 11 threw away a real 1988 step and left the
    test one standing. Pass what you think is there and a mismatch refuses instead of deleting."""
    d = _quests_authored()
    q = d.get(slug)
    if q is None or not (0 <= idx < len(q.get("steps", []))):
        raise HTTPException(404, "no such step")
    st = q["steps"][idx]
    if expect is not None and expect not in (st.get("ref"), st.get("title")):
        raise HTTPException(409, f"step {idx} is {st.get('ref') or st.get('title')!r}, "
                                 f"not {expect!r} — the list moved under you; re-read it first")
    gone = q["steps"].pop(idx)
    _quests_write(d)
    return {"removed": gone}


class PlaceStep(BaseModel):
    side: int
    area_id: int
    col: int | None = None
    row: int | None = None


@app.post("/api/quests/{slug}/steps/{idx}/place")
def design_place_step(slug: str, idx: int, p: PlaceStep) -> dict:
    """Compile a cast step onto a square — the per-quest door into the one compile path.

    A shell over POST /api/design/place (spec §4.2): the quest comes from the URL instead of the
    body, and `quest.step` names an EXISTING step, so the placement replaces that step rather than
    appending a second one beside it. It stays until the designer UI calls /api/design/place
    itself; what it still adds is the OLD response shape and the old one-sentence error, because
    the step rail reads `saved`/`step` and prints `detail` as a line of text.

    The index guard is `0 <= idx` and not just `idx < len`: FastAPI parses "-1" happily, and a
    negative index used to reach the LAST step. Through wl_place that would be worse than before —
    `quest.step` out of range means APPEND, so placing at -1 would have quietly grown the quest a
    duplicate step instead of filling in the one that was asked for."""
    d = _quests_authored()
    q = d.get(slug)
    steps = (q or {}).get("steps") or []
    if q is None or not (0 <= idx < len(steps)):
        raise HTTPException(404, "no such step")
    st = steps[idx]
    # the step's own `bind` goes WITH it. Without that spread the authored `advance` was dropped on
    # the way through (wl_place writes `bind` only when it is told about an effect) and, because no
    # effect arrived, the §2.5 501 never fired either — the one placement the spec forbids came back
    # as a plain success with the binding quietly gone. Same spread the designer sends.
    res = wl_place.place(_PlaceHost(), p.side, p.area_id, st.get("blueprint") or "",
                         st.get("values") or {}, p.col, p.row,
                         quest={"slug": slug, "step": idx, "title": st.get("title"),
                                "note": st.get("note"), **(st.get("bind") or {})})
    if "refused" in res:
        raise HTTPException(res.get("status", 422),
                            "; ".join(f"{e['check']}: {e['detail']}" for e in res["refused"]))
    placed = _quest_step_alias(res, p.side, p.area_id, p.col, p.row) or st
    return {"saved": (res.get("saved") or [None])[0], "step": placed,
            "records": [{"role": r["role"], "op": r["grp"], "id": r["cid"]} for r in res["records"]],
            "warnings": res.get("warnings") or [], "size_delta": res["size_delta"]}


class SetGfx(BaseModel):
    gfx_id: int


@app.post("/api/area/{side}/{area_id}/gfx")
def set_area_gfx(side: int, area_id: int, s: SetGfx) -> dict:
    """Set the area's tileset (descriptor +$30 gfx-id) and save the record to work/. gfx resolves
    globally by id (any side), so no copy is needed — the picker offers every existing tileset."""
    rec_path, _ = _resolve(side, "record", area_id)
    if not rec_path.is_file():
        raise HTTPException(404, "no record")
    _push_area_undo(side, area_id)
    area = load_area(rec_path.read_bytes(), side, area_id)
    if area.slot + 0x30 >= len(area.raw):
        raise HTTPException(422, "record too short")
    rec = bytearray(area.raw)
    rec[area.slot + 0x30] = s.gfx_id & 0xFF
    wf = _save_record(side, area_id, rec)
    # no copy: gfx resolves globally by id (any side)
    found = _gfx(s.gfx_id)[0] is not None
    return {"gfx_id": s.gfx_id, "gfx_available": found}


@app.post("/api/area/{side}/{area_id}/revert")
def revert_area(side: int, area_id: int) -> dict:
    """Delete the working copies (record + map) → next load returns pristine baseline."""
    reverted = []
    for kind in ("record", "map"):
        wf = _work_path(side, kind, area_id)
        if wf.is_file():
            wf.unlink()
            reverted.append(kind)
    return {"reverted": reverted}


def _rel(p: Path) -> str:
    return str(p.relative_to(REPO)) if p.is_relative_to(REPO) else str(p)


# ── QUESTS — the cross-area graph ─────────────────────────────────────────────────────────
# A Flow is a chain of cells inside one area. A quest is the same thing across the border, and the
# game writes none of it down — wl_quest derives it from the two references that reach past an area
# (a global item id, a keyword's WORD) and editor/annotations/quests.json supplies the names.
# Editor-only. The cart build never reads any of this.
QUESTS_FILE = REPO / "editor" / "annotations" / "quests.json"
_QUEST_CACHE: dict = {}


class QuestEdit(BaseModel):
    name: str | None = None
    notes: str | None = None
    links: list[str] | None = None
    steps: list[dict] | None = None            # [{ref, note}] — the story, in order
    track: str | None = None                   # "main" | "side"
    after: list[str] | None = None             # quest ids that have to happen first
    region: str | None = None
    order: int | None = None
    # ── the questlog model (spec §2.1) ──
    axis: list | None = None                   # [{id, log}] — the stages, in progress order
    markers: list | None = None                # [{id, kind, item|word|at}]
    kind: str | None = None                    # "main" | "side" — the v2 name for `track`
    home: dict | None = None                   # {side, area}; {} clears it
    # `qid` is NOT here on purpose: it is handed out once by the migration and never edited, because
    # a save in the field carries the number and re-pointing it would rewrite somebody's history.


def _quest_load(side: int, area_id: int):
    """(ir, area, name) for one area — what resolve_step renders a step from."""
    a, _, _, _ = _load_area_full(side, area_id)
    return _ir(side, area_id), a, _area_name(area_id) or f"area {area_id}"


def _quest_graph() -> dict:
    """The derived graph, cached on the revisions of every record it read.

    Building it decodes all 43 areas (~10 s), so the key is the full set of record revisions: any
    save anywhere invalidates it, and nothing else does."""
    areas = list_areas()["areas"]
    key = tuple((a["side"], a["area_id"], _record_rev(a["side"], a["area_id"])) for a in areas)
    if _QUEST_CACHE.get("key") == key:
        return _QUEST_CACHE["graph"]
    scans = []
    for a in areas:
        try:
            area, _, _, _ = _load_area_full(a["side"], a["area_id"])
            scans.append(wl_quest.scan_area(a["side"], a["area_id"], a["name"] or f"area {a['area_id']}",
                                            _ir(a["side"], a["area_id"]), area))
        except Exception as ex:                       # noqa: BLE001 — one bad area must not kill the map
            print(f"[quest] s{a['side']}a{a['area_id']}: {ex}")
    g = wl_quest.build(scans)
    _QUEST_CACHE.update(key=key, graph=g)
    return g


def _quests_authored() -> dict:
    """The authored quests — `_`-prefixed bookkeeping and non-dict values are not quests.

    Through wl_questlog, which accepts v1 and v2 alike and deliberately does NOT migrate: reading
    is not editing, and the Events rail of an area must not rewrite this file on a GET. The
    migration happens on the first WRITE (below), which is also where the .bak_v1 copy is made."""
    if not QUESTS_FILE.is_file():
        return {}
    try:
        return wl_questlog.quests(wl_questlog.load(QUESTS_FILE))
    except (ValueError, OSError) as e:
        raise HTTPException(500, f"quests.json is not readable: {e}")


def _quests_write(d: dict, retire=None):
    """Write the authored quests back — migrated to v2, and atomically.

    wl_questlog.save keeps whatever `_`-keys are on disk, stamps `_version`, copies a still-v1 file
    to .bak_v1 once (34 quests of handwriting), and replaces through tmp + os.replace. The atomic
    part is not decoration: a half-written quests.json makes every reader of it answer 500,
    including the Events rail and the Flow tab of every area in the game.

    `retire` is a questlog id that leaves circulation with this write (§2.1). It is UNIONED with
    what is on disk rather than assigned: `d` holds quests only, so writing the list from the
    caller's view alone would drop every id retired before it."""
    QUESTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    doc = dict(d)
    try:
        on_disk = wl_questlog.load(QUESTS_FILE) if QUESTS_FILE.is_file() else {}
    except (OSError, ValueError):
        on_disk = {}
    if retire is not None:
        doc["_retired_qids"] = on_disk.get("_retired_qids") or []
        wl_questlog.retire_qid(doc, retire)

    # THE QUESTLOG STORE follows the axes on every write, and it happens HERE rather than in the
    # PUT so that no path can declare a state without the bytes for it. `log_alloc` is idempotent
    # while the range still fits, so an ordinary rename touches nothing; a grown axis gets a new
    # range and the old one is retired, because a bit that moves is a saved cart that lies.
    doc["_log"] = on_disk.get("_log") or {}
    if retire is not None:
        wl_questlog.log_free(doc, retire)
    for q in wl_questlog.quests(doc).values():
        need = wl_questlog.bytes_for(q)
        if need and q.get("qid") is not None:
            wl_questlog.log_alloc(doc, q["qid"], need)
    if not doc["_log"].get("alloc"):
        doc.pop("_log")                      # nothing declares a state yet — do not write an empty block

    wl_questlog.save(QUESTS_FILE, doc)
    # Only the CLAIMS half of the cache. Its key is (mtime_ns, size) and os.replace does move both,
    # but a file that comes back the same size in the same nanosecond would serve a stale rail —
    # and dropping the other half would throw away the ten-second graph for nothing.
    _QUEST_CACHE.pop("claims_key", None)


def _compose_quests(d: dict) -> dict:
    """The composed quest list plus the questlog fields the designer needs on top.

    `wl_quest.compose()` is the DERIVED view — it knows about links and records and nothing about a
    questlog — so `qid`, `axis`, `markers`, `kind` and `home` are put back on here. Additively: every
    field the frontend already reads keeps its place and its meaning, including `track`.

    The values come from a migration run IN MEMORY rather than from disk. Until something writes,
    the file is still v1 and no quest carries a qid; answering `null` would show a designer a quest
    that cannot be bound to, for no reason other than that nobody has saved yet. `migrate()` is
    deterministic and idempotent, so the numbers shown here are the numbers the first save writes
    down."""
    out = wl_quest.compose(_quest_graph(), d, _quest_load)
    try:
        v2 = wl_questlog.quests(wl_questlog.migrate(d))
    except ValueError:                            # all 256 ids spoken for — show what is on disk
        v2 = d
    for q in out["quests"]:
        src = v2.get(q["id"]) or {}
        q["qid"] = src.get("qid")
        q["axis"] = src.get("axis") or []
        q["markers"] = src.get("markers") or []
        q["kind"] = src.get("kind") or q.get("track") or "side"
        q["home"] = src.get("home")
    return out


@app.get("/api/quests/log")
def quests_log() -> dict:
    """THE QUESTLOG — what the designer shows and what an overlay will one day render.

    Two halves, and only the first is authored. `axis` on each quest says what states it has and
    what line the logbook prints for each; `_log` says which byte and bit each of those states
    occupies. The second half is allocated once and written down, because a save refers to a bit by
    position and flash only ever writes 1→0 — a bit that moves is a saved cart that lies.

    WL1 has none of this. The store does not exist in the shipped game, so the two IR verbs that
    read and write it (`{"cond": "quest"}` / `{"effect": "advance"}`) are AUTHORED and stored but
    not yet compiled — QUESTLOG_SPEC §5 defines the bytes they will become (condition kind 7 for
    the gate, the existing effect pair for the write). This endpoint shows the store being
    authored, validates it, and is the shape a `.bin` gets emitted from.

    `problems` is the same idea as the placement refusals: measured, and each one says what to do.
    """
    doc = dict(_quests_authored())
    try:
        on_disk = wl_questlog.load(QUESTS_FILE) if QUESTS_FILE.is_file() else {}
    except (OSError, ValueError):
        on_disk = {}
    doc["_log"] = on_disk.get("_log") or {}
    out = wl_questlog.log_layout(doc)
    out["problems"] = wl_questlog.log_problems(doc)
    return out


@app.get("/api/quests")
def list_quests() -> dict:
    """The quest list: the authored story (steps, in order) plus the derived links it touches,
    plus the questlog model — `qid`, `axis`, `markers` (spec §4.2)."""
    return _compose_quests(_quests_authored())


def _quest_claims() -> dict:
    """{(side, area): {(grp, cid): [quest …]}} — cached on the quests file's own revision."""
    try:
        st = QUESTS_FILE.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = (0, 0)
    if _QUEST_CACHE.get("claims_key") != key:
        _QUEST_CACHE["claims_key"] = key
        _QUEST_CACHE["claims"] = wl_quest.claims(_quests_authored())
    return _QUEST_CACHE["claims"]


# ── THE DEFAULT PARTY ─────────────────────────────────────────────────────────────────────
# The four rangers a new game starts with. The cart picks the blob through WL_PARTY /
# WL_PARTY_BLOB at build time; the editor edits it in place. Working copies go to
# editor/work/save/ so baseline/save stays pristine, exactly like every other asset.
PARTY_DIR = REPO / "baseline" / "save"
PARTY_WORK = WORK / "save"
PARTY_DEFAULT = "vanilla_named_s1_savestate_plain.bin"
# Which blob the CART BUILD ships as the new-game party. The build reads this same file, so picking
# here picks for real. It lives in editor/annotations/ rather than under cart_EF/assets because it is
# editor STATE, and because that is one of the three paths nas_sync.sh pull carries back from the
# NAS — pick a default on the NAS editor and the build on the Mac has to hear about it.
PARTY_SEL = REPO / "editor" / "annotations" / "party_selected.json"


class PartyPick(BaseModel):
    blob: str
    note: str | None = None


def _party_selected() -> str:
    try:
        return json.loads(PARTY_SEL.read_text()).get("blob") or PARTY_DEFAULT
    except (ValueError, OSError):
        return PARTY_DEFAULT


@app.put("/api/party/selected")
def pick_party(p: PartyPick) -> dict:
    """Choose the party the build ships. Refuses a blob that is not a loadable party — shipping a
    scrambled or malformed one would hand a new game an empty or corrupt roster."""
    r, _ = _party_path(p.blob)
    if not r.is_file():
        raise HTTPException(404, f"no party blob {p.blob!r}")
    blob = r.read_bytes()
    if not wl_party.is_plain(blob):
        raise HTTPException(422, f"{p.blob} is not a plain party blob — it is scrambled or uses "
                                 "the title-screen encoding")
    bad = wl_party.check(blob)
    if bad:
        raise HTTPException(422, "; ".join(bad[:3]))
    try:
        doc = json.loads(PARTY_SEL.read_text())
    except (ValueError, OSError):
        doc = {}
    names = [m["name"] for m in wl_party.load(blob)["members"] if not m["empty"]]
    doc["blob"] = p.blob
    doc["note"] = p.note if p.note is not None else ", ".join(names)
    PARTY_SEL.parent.mkdir(parents=True, exist_ok=True)
    PARTY_SEL.write_text(json.dumps(doc, indent=1))
    return {"selected": p.blob, "names": names}
_PARTY_UNDO: dict = {}


class MemberEdit(BaseModel):
    name: str | None = None
    attrs: dict | None = None
    money: int | None = None
    xp: int | None = None
    level: int | None = None                 # $24 — the rank ladder. $1A is the ARMOUR CLASS below,
    ac: int | None = None                    # and the two were transposed until the engine settled it
    maxcon: int | None = None
    con: int | None = None
    status: int | None = None
    rank: str | None = None
    weapon: int | None = None
    armour: int | None = None
    skills: list[dict] | None = None
    inventory: list[dict] | None = None


def _party_path(name: str):
    """(read path, write path). A work copy wins; the baseline is never written."""
    if not re.match(r"^[A-Za-z0-9_.-]+\.bin$", name or ""):
        raise HTTPException(422, "bad party blob name")
    w = PARTY_WORK / name
    return (w if w.is_file() else PARTY_DIR / name), w


@app.get("/api/parties")
def list_parties() -> dict:
    """Every party blob on disk — the shipped ones and any work copies."""
    out = []
    for d, origin in ((PARTY_DIR, "baseline"), (PARTY_WORK, "work")):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*savestate*.bin")):
            try:
                blob = f.read_bytes()
                plain = wl_party.is_plain(blob)
                p = wl_party.load(blob)
                live = [m for m in p["members"] if not m["empty"]]
                out.append({"name": f.name, "origin": origin, "plain": plain,
                            "members": len(live) if plain else 0,
                            "names": [m["name"] for m in live] if plain else []})
            except (ValueError, OSError) as e:
                out.append({"name": f.name, "origin": origin, "error": str(e)})
    seen, uniq = set(), []
    for x in out:                       # a work copy shadows the baseline of the same name
        if x["name"] in seen:
            continue
        seen.add(x["name"])
        uniq.append(x)
    return {"default": PARTY_DEFAULT, "selected": _party_selected(), "parties": uniq}


@app.get("/api/party/{name}")
def get_party(name: str) -> dict:
    r, w = _party_path(name)
    if not r.is_file():
        raise HTTPException(404, f"no party blob {name!r}")
    blob = r.read_bytes()
    return {"name": name, "origin": "work" if r == w else "baseline",
            **wl_party.load(blob), "problems": wl_party.check(blob)}


@app.put("/api/party/{name}/member/{slot}")
def save_party_member(name: str, slot: int, m: MemberEdit) -> dict:
    """Edit one ranger. Only the fields sent are touched; the rest of the 256-byte record — including
    everything nobody has identified yet — stays byte-identical."""
    r, w = _party_path(name)
    if not r.is_file():
        raise HTTPException(404, f"no party blob {name!r}")
    blob = r.read_bytes()
    try:
        out = wl_party.save_member(blob, slot, m.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(422, str(e))
    bad = wl_party.check(out)
    _PARTY_UNDO.setdefault(name, []).append(blob)
    del _PARTY_UNDO[name][:-20]
    w.parent.mkdir(parents=True, exist_ok=True)
    w.write_bytes(out)
    return {"saved": _rel(w), "changed": sum(1 for a, b in zip(blob, out) if a != b),
            "problems": bad, **wl_party.load(out)}


@app.get("/api/skills")
def list_skills() -> dict:
    """Skill catalog id -> name (wl_cell._SKILL_NAMES) — for lookup pickers."""
    return {"skills": [{"id": i, "name": n} for i, n in enumerate(wl_cell._SKILL_NAMES)
                       if n and i > 0]}


class StartEdit(BaseModel):
    area: int
    x: int
    y: int


@app.get("/api/party/{name}/start")
def get_party_start(name: str) -> dict:
    """New-game start: record-0 bytes +$08/+$09 = party x/y, +$0A = area, +$0B/+$0C =
    overworld mirror, +$7F = current-area header. Engine reads them via ($68)=$F400
    at $9C2A (traced 2026-08-10)."""
    r, _ = _party_path(name)
    if not r.is_file():
        raise HTTPException(404, f"no party blob {name!r}")
    b = r.read_bytes()
    return {"x": b[0x08], "y": b[0x09], "area": b[0x0A],
            "mirror": [b[0x0B], b[0x0C]], "header_area": b[0x7F]}


@app.put("/api/party/{name}/start")
def save_party_start(name: str, st: StartEdit) -> dict:
    r, w = _party_path(name)
    if not r.is_file():
        raise HTTPException(404, f"no party blob {name!r}")
    blob = r.read_bytes()
    out = bytearray(blob)
    out[0x08], out[0x09], out[0x0A] = st.x & 0xFF, st.y & 0xFF, st.area & 0xFF
    out[0x0B], out[0x0C] = st.x & 0xFF, st.y & 0xFF
    # Boot-Pfad (W8A26-Flow): Party-Zelle = $F478/$F479 + Viewport-Offset (9,4) —
    # gemessen: vanilla (46,58)+(9,4)=(55,62); Test (26,30)->(35,34). Der Picker
    # schreibt daher die ECKE = Zelle minus (9,4), geclampt.
    out[0x78] = max(0, st.x - 9) & 0xFF
    out[0x79] = max(0, st.y - 4) & 0xFF
    out[0x7F] = st.area & 0xFF
    _PARTY_UNDO.setdefault(name, []).append(blob)
    del _PARTY_UNDO[name][:-20]
    w.parent.mkdir(parents=True, exist_ok=True)
    w.write_bytes(bytes(out))
    return {"saved": _rel(w), "x": st.x, "y": st.y, "area": st.area}


@app.get("/api/area/{side}/{area_id}/render.png")
def area_render_png(side: int, area_id: int, scale: int = 4):
    """Server-rendered map thumbnail (tile atlas, no op glyphs) — for pick-a-cell UIs."""
    from fastapi.responses import Response
    import wl_tiles as _wt, base64 as _b64
    from PIL import Image as _Img
    import io
    area, tids, _, _ = _load_area_full(side, area_id)
    gid = _area_gfx_id(side, area_id)
    gfx_path, _, _ = _gfx(gid)
    atlas = _atlas(gfx_path.read_bytes(), bg=0, cram=1)
    cols = atlas["cols"]
    TW, TH = atlas.get("tile_w", atlas["tile"]), atlas.get("tile_h", atlas["tile"])
    sheet = _Img.frombytes("RGBA", (atlas["w"], atlas["h"]), _b64.b64decode(atlas["rgba_b64"]))
    if not tids:
        tids = [area.raw[area.slot + 0x33]] * (area.width * area.height)
    img = _Img.new("RGBA", (area.width * TW, area.height * TH), (0, 0, 0, 255))
    for row in range(area.height):
        for col in range(area.width):
            tid = tids[row * area.width + col]
            sx, sy = (tid % cols) * TW, (tid // cols) * TH
            img.paste(sheet.crop((sx, sy, sx + TW, sy + TH)), (col * TW, row * TH))
    px = max(1, min(scale, 8))
    img = img.resize((area.width * px, area.height * px), _Img.NEAREST)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png",
                    headers={"X-Map-W": str(area.width), "X-Map-H": str(area.height)})


@app.post("/api/party/{name}/undo")
def undo_party(name: str) -> dict:
    st = _PARTY_UNDO.get(name)
    if not st:
        raise HTTPException(404, "nothing to undo")
    blob = st.pop()
    _, w = _party_path(name)
    w.parent.mkdir(parents=True, exist_ok=True)
    w.write_bytes(blob)
    return {"undone": True, **wl_party.load(blob)}


@app.post("/api/party/{name}/revert")
def revert_party(name: str) -> dict:
    """Drop the working copy — the next read returns the pristine blob."""
    _, w = _party_path(name)
    if w.is_file():
        w.unlink()
        return {"reverted": True}
    return {"reverted": False}


@app.get("/api/paragraphs")
def list_paragraphs() -> dict:
    """The printed paragraph book. Part of the data model, not a footnote: the records say "Read
    paragraph 57." and paragraph 57 is where the plot is."""
    P = wl_cell.paragraphs()
    return {"count": len([k for k in P if k.isdigit()]),
            "paragraphs": {k: " ".join(v.split()) for k, v in P.items() if k.isdigit()}}


@app.get("/api/paragraph/{n}")
def one_paragraph(n: int) -> dict:
    t = wl_cell.paragraphs().get(str(n))
    if t is None:
        raise HTTPException(404, f"no paragraph {n}")
    return {"n": n, "text": " ".join(t.split())}


@app.get("/api/quests/coverage")
def quests_coverage() -> dict:
    """How much of the script hangs on a questline, per area.

    Deliberately not a score to maximise: most records are signs, furniture and bump messages, and
    a game where every one of them belonged to a quest would be a worse game. The point is to see
    which areas are understood as stories and which are still an undifferentiated pile."""
    per = {}
    for a in list_areas()["areas"]:
        try:
            ir = _ir(a["side"], a["area_id"])
            area, _, _, _ = _load_area_full(a["side"], a["area_id"])
        except Exception:                                 # noqa: BLE001
            continue
        n = sum(len(v) for v in ir["groups"].values()) + sum(len(v) for v in ir.get("opaque", {}).values())
        cells = sum(1 for op in area.nibble[:area.width * area.height] if 0 < op < 0x10)
        per[(a["side"], a["area_id"])] = (a["name"] or f"area {a['area_id']}", n, cells)
    return wl_quest.coverage(_quest_claims(), per)


@app.get("/api/quest-steps/{side}/{area_id}")
def quest_step_candidates(side: int, area_id: int) -> dict:
    """Every record of one area, rendered as a step — the picker for "add a step to this quest".

    Sorted the way the story tends to run: the things that speak first, then what they lead to."""
    ir, area, name = _quest_load(side, area_id)
    out = []
    for gs, recs in ir["groups"].items():
        for cs in recs:
            st = wl_quest.resolve_step(wl_quest.step_ref(side, area_id, int(gs), int(cs)), _quest_load)
            if st.get("ok"):
                out.append(st)
    out.sort(key=lambda s: (not s["text"], s["grp"], s["cid"]))
    return {"side": side, "area_id": area_id, "name": name, "steps": out}


def _check_axis(axis) -> list:
    """The stages of one quest, cleaned, or a 422 saying which rule the list broke.

    Three rules and none of them is taste. `open` is position 0 of every quest by definition and is
    never written down — letting it into the list would give it a storage bit, and a fresh save is
    untouched precisely because untouched means open — the store reads $FF and no bit is cleared.
    An id appears once, because two stages with
    one name are one bit meaning two things. And nothing stands BEHIND `done`, which is the last
    forward state; `failed` may, because it sits beside the line rather than on it."""
    if not isinstance(axis, list):
        raise HTTPException(422, "an axis is a list of stages, in the order they happen")
    out, seen, done_at = [], set(), None
    for i, s in enumerate(axis):
        if isinstance(s, str):
            s = {"id": s}
        if not isinstance(s, dict):
            raise HTTPException(422, f"stage {i} is neither an id nor {{id, log}}")
        sid = str(s.get("id") or "").strip()
        if not sid or " " in sid:
            raise HTTPException(422, f"stage {i} needs an id with no spaces in it — "
                                     f"`s1`, `hat-toy`, anything a person can read in a gate")
        if sid.lower() == "open":
            raise HTTPException(422, "`open` is the first state of every quest already — it carries "
                                     "no log line and costs no storage, so it is never declared")
        if sid.lower() in seen:
            raise HTTPException(422, f"'{sid}' is declared twice; one stage, one name, one bit")
        seen.add(sid.lower())
        if done_at is not None and sid.lower() != "failed":
            raise HTTPException(422, f"'{sid}' stands behind `done`, and `done` is the last state "
                                     f"the quest moves forward to. Only `failed` may follow it.")
        if sid.lower() == "done":
            done_at = i
        log = s.get("log")
        if log is not None and not isinstance(log, str):
            raise HTTPException(422, f"the log line of '{sid}' has to be text")
        out.append({**s, "id": sid})
    return out


def _check_markers(mk, qid: str, d: dict) -> list:
    """The things a quest points at by name: an item, a keyword, a square (spec §2.3).

    An item marker without an id gets one HERE, because there are exactly three spare item slots in
    the whole game (70/71/72 — the shipped table has them and the shipped game does not use them)
    and handing out a live id would give the player a marker that is also a Canteen. A word is
    checked against every keyword the game already answers to: two places answering to THANATOS
    both fire, and the second story quietly opens with the first one's password."""
    if not isinstance(mk, list):
        raise HTTPException(422, "markers are a list of {id, kind, …}")
    others = {k: v for k, v in d.items() if k != qid}
    taken = wl_questlog.marker_items(others)
    words = None
    out = []
    for i, m in enumerate(mk):
        if not isinstance(m, dict) or not str(m.get("id") or "").strip():
            raise HTTPException(422, f"marker {i} needs an id — the name the quest calls it by")
        kind = str(m.get("kind") or "").strip().lower()
        if kind not in wl_questlog.MARKER_KINDS:
            raise HTTPException(422, f"marker {m.get('id')!r} is a {kind or 'nameless'} marker; "
                                     f"it has to be one of {', '.join(wl_questlog.MARKER_KINDS)}")
        m = {**m, "kind": kind, "id": str(m["id"]).strip()}
        if kind == "item":
            if m.get("item") is None:
                # the shipped CHARGE COUNTS go in with the request: a marker is tested with an op-1
                # `have(item)`, and $14A5 spends one use per test unless the count is zero. Two of
                # the three spare ids are not zero (70 → 5 uses, 71 → 1), so a marker on them would
                # survive a handful of tests and then be gone (spec §2.3, the spend trap).
                counts = wl_item.table(REPO) or None
                got = wl_questlog.alloc_item_marker({**others, qid: {"markers": out}},
                                                    reserved=tuple(taken), charges=counts)
                if got is None:
                    spend = wl_questlog.spendable_free_ids(charges=counts)
                    why = ("; ".join(f"{i} is created with {c} use{'' if c == 1 else 's'} and a "
                                     f"have() test would spend one" for i, c in spend)
                           if spend else "they are taken")
                    raise HTTPException(422, "there is no spare item id left for a marker — the "
                                             f"game ships three (70, 71, 72) and {why}. Growing "
                                             "the item table is engine work, so this marker needs "
                                             "to be a word or a square instead.")
                m["item"] = got
            if not isinstance(m["item"], int):
                raise HTTPException(422, f"marker {m['id']!r} names item {m['item']!r}, not a number")
            taken.add(m["item"])
        elif kind == "word":
            if words is None:
                # every word the shipped game answers to; the graph is built for the response below
                # anyway, so asking it here is free where it would otherwise cost ten seconds
                words = {x["id"] for x in _quest_graph()["links"] if x["kind"] == "keyword"}
            bad = wl_questlog.check_word_unique(m.get("word"), words, others)
            if bad:
                raise HTTPException(422, bad)
            m["word"] = str(m["word"]).strip().upper()
        else:                                        # cell
            at = m.get("at")
            if not isinstance(at, dict) or not all(isinstance(at.get(k), int)
                                                   for k in ("side", "area", "col", "row")):
                raise HTTPException(422, f"marker {m['id']!r} points at a square, so it needs "
                                         f"at: {{side, area, col, row}}")
        out.append(m)
    return out


@app.put("/api/quest/{qid}")
def save_quest(qid: str, q: QuestEdit) -> dict:
    """Rename a quest, edit its notes, move links in and out of it — and set its questlog model.

    A link may sit in exactly one quest: moving it here takes it out of wherever it was, because two
    quests both claiming the Bloodstaff is not a statement about the game, it is a lost edit.

    `axis`, `markers`, `kind` and `home` are the v2 half (spec §4.2). Everything they can refuse is
    refused before the first field is assigned, so a rejected axis leaves the quest exactly as it
    was rather than half-renamed. `qid` is not settable: the migration hands it out once."""
    if not re.match(r"^[a-z0-9][a-z0-9-]{0,47}$", qid or ""):
        raise HTTPException(422, "a quest id is lowercase letters, digits and dashes")
    d = _quests_authored()
    axis = _check_axis(q.axis) if q.axis is not None else None
    markers = _check_markers(q.markers, qid, d) if q.markers is not None else None
    kind = None
    if q.kind is not None:
        kind = str(q.kind).strip().lower()
        if kind not in wl_questlog.QUEST_KINDS:
            raise HTTPException(422, f"a quest is {' or '.join(wl_questlog.QUEST_KINDS)}, "
                                     f"not {q.kind!r}")
    home = None
    if q.home is not None:
        if q.home and not all(isinstance(q.home.get(k), int) for k in ("side", "area")):
            raise HTTPException(422, "home is where the quest starts: {side, area}, or {} to clear it")
        home = dict(q.home) if q.home else None

    cur = dict(d.get(qid) or {"name": qid, "notes": "", "links": []})
    for f in ("name", "notes", "region", "order", "links", "steps", "track", "after"):
        v = getattr(q, f)
        if v is not None:
            cur[f] = v
    if axis is not None:
        cur["axis"] = axis
    if markers is not None:
        cur["markers"] = markers
    if kind is not None:
        cur["kind"] = kind
        if q.track is None:
            # `track` is the older name for the same distinction and is what compose() sorts the
            # main line by. Letting the two disagree would show a quest as main in one view and
            # side in the next, so setting one sets the other unless the caller said otherwise.
            cur["track"] = kind
    if q.home is not None:
        cur["home"] = home
        if home is None:
            cur.pop("home")
    if q.links is not None:
        for other, ov in d.items():
            if other != qid:
                ov["links"] = [k for k in (ov.get("links") or []) if k not in q.links]
    d[qid] = cur
    _quests_write(d)
    return _compose_quests(_quests_authored())


@app.delete("/api/quest/{qid}")
def delete_quest(qid: str) -> dict:
    """Drop the NAME. The links go back to ungrouped — the graph is derived and cannot be deleted.

    The quest's questlog id goes with it, onto `_retired_qids`, and is never handed out again
    (§2.1): a cart in somebody's drawer still carries that bit, and giving the number to the next
    quest would make an old save read as progress in a story that never happened.

    The migration runs first for the same reason. While the file is still v1 nobody carries a
    number, and dropping a quest out of a v1 document would make the next save re-hand every number
    after it — measured: deleting one quest shifted 26. Migrating pins everybody's, then exactly
    one leaves."""
    d = _quests_authored()
    if qid not in d:
        raise HTTPException(404, f"no quest {qid!r}")
    try:
        d = wl_questlog.quests(wl_questlog.migrate(d))
    except ValueError:                            # all 256 spoken for — delete still has to work
        pass
    gone = d.pop(qid, None) or {}
    _quests_write(d, retire=gone.get("qid"))
    return _compose_quests(d)


@app.get("/api/quest/{slug}/validate")
def quest_validate(slug: str) -> dict:
    """The battery over one quest (spec §8) — read-only, and it refuses nothing.

    `{"errors": [...], "warnings": [...], "info": [...]}`, each entry `{check, detail, step?}`.
    Half the checks are cross-quest (a gate may point at another quest's stage), which is why
    wl_qvalidate gets the whole document and picks the one under test out of it."""
    d = _quests_authored()
    if slug not in d:
        raise HTTPException(404, f"no quest {slug!r}")
    return wl_qvalidate.validate_quest(d, slug)


@app.get("/api/scenes")
def scene_registry() -> dict:
    """The scene registry (spec §6): the $CA00 handlers and grp16 events a record may call, and
    which parameters each one reads out of that record.

    Passed through unchanged — it is a hand-maintained annotation file whose entries were each
    derived from the built sources, and an endpoint that reshaped it would only be a second place
    to be wrong. What is NOT in it is as deliberate as what is: a handler whose record layout has
    not been reversed has no entry, because half a parameter list is worse than none."""
    return wl_scene_registry.load()


# ── OBJECTS and BLUEPRINTS ────────────────────────────────────────────────────────────────
# A blueprint is a map region turned into a re-usable thing: its cells, the records they dispatch,
# and the assets those records name (strings, tiles, foes, hireables). wl_object does the work; this
# is the library and the two verbs — SNAP a region out of an area, PLACE it into another.
#
# The library lives in the repo (editor/blueprints/*.json), not in work/: a blueprint is authored
# content meant to be shared, so it belongs in git next to the areas it was cut from.
BLUEPRINTS = Path(os.environ.get("WL_BLUEPRINTS", REPO / "editor" / "blueprints")).resolve()
_BP_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")


def _bp_path(name: str) -> Path:
    if not _BP_NAME.match(name or ""):
        raise HTTPException(422, "a blueprint name is 1-64 chars of letters, digits, space _ . -")
    return BLUEPRINTS / (name.replace(" ", "_") + ".json")


def _bp_ctx(side: int, area_id: int):
    """(record bytes, gfx bytes|None, map bytes|None, gfx id) for one area."""
    rp, _ = _resolve(side, "record", area_id)
    if not rp.is_file():
        raise HTTPException(404, f"no record blob for side {side} area {area_id}")
    raw = rp.read_bytes()
    area = load_area(raw, side, area_id)
    gid = area.raw[area.slot + 0x30] if area.slot + 0x30 < len(area.raw) else None
    gpath = _gfx(gid)[0] if gid is not None else None
    gfx = gpath.read_bytes() if gpath else None
    if gfx is not None and len(gfx) < wl_tiles.COLOR_BASE + wl_tiles.NUM_TILES * 4:
        gfx = None
    mp, _ = _resolve(side, "map", area_id)
    return raw, gfx, (mp.read_bytes() if mp.is_file() else None), gid


def _portrait_exists(sid: int) -> bool:
    return _str(sid)[0] is not None


def _bp_summary(doc: dict) -> dict:
    """What a blueprint holds, without shipping the whole document to a list view."""
    objs = doc.get("objects", [doc])
    n = lambda k: sum(len(o.get(k) or {}) for o in objs)                       # noqa: E731
    return {"name": doc.get("name"), "description": doc.get("description", ""),
            "size": doc.get("size"), "source": doc.get("source"),
            "objects": len(objs), "cells": sum(len(o.get("cells") or []) for o in objs),
            "records": n("records") + n("opaque"), "text": n("text"), "tiles": n("tiles"),
            "foes": n("foes"), "npcs": n("npcs"),
            "requires": doc.get("requires") or {}, "notes": doc.get("notes") or []}


class BPCut(BaseModel):
    side: int
    area_id: int
    c0: int
    r0: int
    c1: int
    r1: int
    name: str = "object"
    description: str = ""
    save: bool = False


class BPPlace(BaseModel):
    side: int
    area_id: int
    col: int
    row: int


# ── BUILDING BLOCKS ───────────────────────────────────────────────────────────────────────────
_BLOCK_CACHE: dict = {}


class BlockPlace(BaseModel):
    gfx: int
    block: str
    x: int
    y: int
    fork: bool = False        # give this area a tileset of its own before writing into it


def _gfx_free_slots(gid: int) -> tuple[set, list]:
    """(slots nothing points at, the areas that share this tileset).

    "Free" is NOT "the bytes are zero". Areas SHARE tilesets — gfx 2 carpets eleven of them — and a
    slot that is blank on purpose can still be referenced by a map, where it draws as black. Writing
    a house wall into it would put that wall into every area that pointed there. So free means
    UNREFERENCED, computed from the union of the tile grids of every area using this tileset — the
    same union `clear-unused` clears against — minus the engine-fixed glyphs it also protects."""
    used: set[int] = set()
    users = []
    for a in list_areas()["areas"]:
        try:
            if _area_gfx_id(a["side"], a["area_id"]) != gid:
                continue
            users.append({"side": a["side"], "area_id": a["area_id"], "name": a["name"]})
            _, tileids, _, _ = _load_area_full(a["side"], a["area_id"])
            used.update(tileids)
        except Exception:                                  # noqa: BLE001
            pass
    free = {t for t in range(wl_tiles.NUM_TILES) if t not in used and t >= 10}
    return free, users


def _block_catalogue() -> dict:
    """Mined once per record revision — the same cache key the edge grammar uses."""
    import wl_block
    areas = list_areas()["areas"]
    key = tuple((a["side"], a["area_id"], _record_rev(a["side"], a["area_id"])) for a in areas)
    if _BLOCK_CACHE.get("key") != key:
        _BLOCK_CACHE.update(key=key, cat=wl_block.catalogue(REPO))
    return _BLOCK_CACHE["cat"]


@app.get("/api/blocks")
def blocks_all() -> dict:
    """The building-block library, per TILESET.

    A block is one bounded object cut out of a shipped map — a house with its door, a compound wall,
    a stand of cacti — found by segmenting the tile layer against whatever carpets the area. It
    carries its op layer, so the op signature classifies it without anyone labelling it: op-$A means
    a house, op-$06 a service building, only walls means a compound, only op-0 means decoration.

    Keyed by gfx id because that is where it can be drawn. Measured, not assumed: of Quartz's 15
    building-sized objects, 15 render in gfx 3, two in gfx 6 and none anywhere else — every town
    redrew its houses even though the tilesets share 55 of 96 tiles. See wl_block's docstring."""
    cat = _block_catalogue()
    return {"gfx": {str(g): v for g, v in cat.items()},
            "counts": {str(g): len(v) for g, v in cat.items()}}


# Declared BEFORE /api/blocks/{gfx}: FastAPI matches routes in order, and "catalogue"
# read as a gfx id gave a 422 on the one call the World view makes.
@app.get("/api/blocks/catalogue")
def blocks_catalogue(kind: str | None = None) -> dict:
    """The whole library, every tileset — the World-level view. No area, no placing: what exists."""
    cat = _block_catalogue()
    out = []
    for g, blocks in sorted(cat.items()):
        for b in blocks:
            if kind and b["kind"] != kind:
                continue
            out.append({k: b[k] for k in ("id", "w", "h", "kind", "n", "ops", "seen_in", "gfx")}
                       | {"tiles": len(b.get("art") or [])})
    return {"blocks": out, "kinds": sorted({b["kind"] for g in cat.values() for b in g})}


@app.get("/api/blocks/{gfx}")
def blocks_for(gfx: int, all: bool = False) -> dict:
    """What the map editor offers for an area drawing from tileset `gfx`.

    Its OWN blocks first — those need no new tiles at all — then, with `all`, every other block with
    the price of bringing it: how many tile slots it would take, and whether there is room. A block
    carries its art, so a foreign one is not forbidden, it just costs slots."""
    import wl_block
    cat = _block_catalogue()
    gfx_path0, _, _ = _gfx(gfx)
    dst0 = gfx_path0.read_bytes() if gfx_path0 else b""
    free0, users0 = _gfx_free_slots(gfx)
    # The LIBRARY belongs in this list. A mined block is re-derived on every start and always
    # carries the id `g{gfx}-{kind}-{i:03d}`, so a forked or hand-authored one is not in the
    # catalogue by construction — and this list is what the map's block stamp offers. Without it
    # the one handling that needs no pattern (Konzept §7.3: "Block setzen") was impossible for
    # exactly the blocks a designer had made. The library copy WINS on an id clash: it is the
    # edited version of that block, and stamping the mined original instead is a silent wrong
    # answer.
    lib = wl_block.lib_list(REPO)
    lib_ids = {b["id"] for b in lib}
    own = [b for b in lib if b.get("gfx") == gfx] + \
          [b for b in cat.get(gfx, []) if b["id"] not in lib_ids]
    out = []
    for b in own:
        r0 = wl_block.resolve(b, dst0, free0) if dst0 else {"map": [], "short": 0, "writes": []}
        out.append(dict(b, cost=0, fits=True, map=r0["map"]))
    if all and dst0:
        rest = [b for b in lib if b.get("gfx") != gfx] + \
               [b for g, blocks in cat.items() if g != gfx
                for b in blocks if b["id"] not in lib_ids]
        for b in rest:
            r = wl_block.resolve(b, dst0, free0)
            # cost = tiles this tileset does not have. `writes` alone is not it: on a FULL
            # tileset nothing can be written, so writes is 0 while 13 tiles are missing.
            out.append(dict(b, cost=len(r["writes"]) + r["short"],
                            fits=not r["short"], short=r["short"], map=r["map"]))
    return {"gfx": gfx, "blocks": out, "free_slots": len(free0),
            "shared_with": [u["name"] for u in users0]}


class BlockFork(BaseModel):
    gfx: int
    block: str
    name: str | None = None


class BlockArt(BaseModel):
    pixels: list
    chars: list


class BlockCell(BaseModel):
    dx: int
    dy: int
    art: int | None = None       # which carried tile this cell draws
    role: str | None = None      # wall | door | service | script | plain
    add: bool = False            # a square inside the box that the block does not cover yet
    remove: bool = False         # give it back to the background


@app.get("/api/block/library")
def block_library() -> dict:
    """The blocks that have been forked for editing. Mined blocks are derived and re-made on every
    load, so they cannot hold an edit; these are the ones that can."""
    import wl_block
    return {"blocks": wl_block.lib_list(REPO)}


@app.post("/api/block/fork")
def block_fork(body: BlockFork) -> dict:
    """Copy a mined block into the library so it can be edited. Same rule blueprints follow: what
    you shape belongs to what you are shaping it for."""
    import wl_block
    blocks = _block_catalogue().get(body.gfx, [])
    blk = next((b for b in blocks if b["id"] == body.block), None)
    if blk is None:
        raise HTTPException(404, f"no block {body.block} in tileset {body.gfx}")
    return wl_block.lib_fork(REPO, blk, body.name)


@app.get("/api/block/{bid}")
def block_get(bid: str) -> dict:
    """A library block plus every carried tile decoded for the painter."""
    import wl_block
    b = wl_block.lib_get(REPO, bid)
    if b is None:
        raise HTTPException(404, f"no block {bid} in the library")
    return {**b, "art_tiles": [wl_block.art_tile(a) for a in (b.get("art") or [])]}


@app.post("/api/block/{bid}/art/{ai}")
def block_art_save(bid: str, ai: int, body: BlockArt) -> dict:
    """Paint one of the block's OWN tiles. Nothing outside the block changes — that is the whole
    point of carrying the art: editing here cannot leak into an area that shares a tileset."""
    import wl_block
    b = wl_block.lib_get(REPO, bid)
    if b is None:
        raise HTTPException(404, f"no block {bid}")
    art = list(b.get("art") or [])
    if not (0 <= ai < len(art)):
        raise HTTPException(422, f"this block carries {len(art)} tiles; there is no #{ai}")
    art[ai] = wl_block.art_from_tile(body.pixels, body.chars)
    b["art"] = art
    return wl_block.lib_save(REPO, b)


@app.post("/api/block/{bid}/art")
def block_art_add(bid: str, gfx: int | None = None, tile: int | None = None) -> dict:
    """Bring another tile INTO the block — from any tileset, or blank. It is copied, so the block
    stays self-contained and the source tileset is not referenced afterwards."""
    import wl_block
    b = wl_block.lib_get(REPO, bid)
    if b is None:
        raise HTTPException(404, f"no block {bid}")
    if gfx is not None and tile is not None:
        gp, _, _ = _gfx(gfx)
        if gp is None:
            raise HTTPException(404, f"no tileset {gfx}")
        art = wl_block.tile_art(gp.read_bytes(), tile)
    else:
        art = "00" * 36
    b["art"] = list(b.get("art") or []) + [art]
    wl_block.lib_save(REPO, b)
    return {"art": len(b["art"]) - 1, "block": b}


@app.post("/api/block/{bid}/cell")
def block_cell(bid: str, body: BlockCell) -> dict:
    """Point one cell at a different carried tile, or change what its op layer means."""
    import wl_block
    b = wl_block.lib_get(REPO, bid)
    if b is None:
        raise HTTPException(404, f"no block {bid}")
    c = next((c for c in b["cells"] if c["dx"] == body.dx and c["dy"] == body.dy), None)
    if body.remove:
        if c is None:
            raise HTTPException(404, f"nothing at ({body.dx},{body.dy}) to remove")
        b["cells"] = [x for x in b["cells"] if x is not c]
        b["ops"] = sorted({x["op"] for x in b["cells"]})
        b["kind"] = wl_block._kind(set(b["ops"]))
        return wl_block.lib_save(REPO, b)
    if c is None:
        # A block is cut along its own edges, so its box has holes — and a hole is not a mistake,
        # it is background showing through. Filling one is EXTENDING the block, which needs a cell
        # to exist before it can carry a tile or a role.
        if not body.add:
            raise HTTPException(404, f"this block has no cell at ({body.dx},{body.dy})")
        if not (0 <= body.dx < b["w"] and 0 <= body.dy < b["h"]):
            raise HTTPException(422, f"({body.dx},{body.dy}) is outside this block's {b['w']}x{b['h']} box")
        art = list(b.get("art") or [])
        ai = body.art
        if ai is None:                                  # a new square starts blank, then you paint it
            blank = "00" * 36
            if blank not in art:
                art.append(blank)
                b["art"] = art
            ai = art.index(blank)
        c = {"dx": body.dx, "dy": body.dy, "tile": 0, "art": ai, "op": 0x0, "role": "plain"}
        b["cells"].append(c)
        b["cells"].sort(key=lambda x: (x["dy"], x["dx"]))
    if body.art is not None:
        if not (0 <= body.art < len(b.get("art") or [])):
            raise HTTPException(422, f"this block carries {len(b.get('art') or [])} tiles")
        c["art"] = body.art
    if body.role is not None:
        if body.role not in ("wall", "door", "service", "script", "plain"):
            raise HTTPException(422, f"unknown role {body.role}")
        c["role"] = body.role
        c["op"] = {"wall": 0xB, "door": 0xA, "service": 0x6, "script": 0x1, "plain": 0x0}[body.role]
        b["ops"] = sorted({x["op"] for x in b["cells"]})
        b["kind"] = wl_block._kind(set(b["ops"]))
    return wl_block.lib_save(REPO, b)


@app.delete("/api/block/{bid}")
def block_delete(bid: str) -> dict:
    import wl_block
    if not wl_block.lib_delete(REPO, bid):
        raise HTTPException(404, f"no block {bid}")
    return {"deleted": bid}


@app.get("/api/block/library/sheet")
def block_library_sheet(kind: str | None = None) -> dict:
    """The LIBRARY drawn from each block's own art — same shape as the mined sheet.

    The library needs its own: the mined sheet is built per tileset, and a forked block belongs to
    no tileset any more. That is the point of it."""
    import base64
    import wl_block
    blocks = wl_block.lib_list(REPO)
    if kind:
        blocks = [b for b in blocks if b["kind"] == kind]
    sh = wl_block.sheet(blocks)
    return {"w": sh["w"], "h": sh["h"], "at": sh["at"],
            "rgba_b64": base64.b64encode(sh["rgba"]).decode()}


@app.get("/api/blocks/{gfx}/sheet")
def blocks_sheet(gfx: int, kind: str | None = None, all: bool = False) -> dict:
    """Every block drawn from ITS OWN art, in one strip, with where each sits.

    Not through any tileset: a block carries the 36 bytes of every tile it uses, and rendering it
    through the destination's atlas showed whatever happened to sit at the source indices there —
    a Quartz house as Highpool rubble. One sheet rather than one request per block, because looking
    at a catalogue means looking at all of it."""
    import base64
    import wl_block
    blocks = blocks_for(gfx, all=all)["blocks"]
    if kind:
        blocks = [b for b in blocks if b["kind"] == kind]
    sh = wl_block.sheet(blocks)
    return {"w": sh["w"], "h": sh["h"], "at": sh["at"],
            "rgba_b64": base64.b64encode(sh["rgba"]).decode()}


@app.post("/api/area/{side}/{area_id}/block/place")
def block_place(side: int, area_id: int, body: BlockPlace) -> dict:
    """Stamp a block into this area: tiles, and the op layer with its ROLES resolved here.

    A cell-id indexes the SOURCE area's record arrays, so copying one would dispatch whatever
    happens to sit at that index — the failure that looks like the editor working. A wall therefore
    becomes THIS area's own op-$B wall id, everything else lands as op 0, and a door comes back as a
    HOLE with its coordinate so the cell editor can give it somewhere to go."""
    import wl_block
    # The LIBRARY first, then the mined catalogue — the same order `_PlaceHost.block()` uses, and
    # the same order `blocks_for()` lists them in. The mined catalogue can never hold a library id
    # (`wl_block.catalogue()` re-stamps every mined block as `g{gfx}-{kind}-{i:03d}`), so without
    # this lookup the one route that sets a block WITHOUT a pattern answered 404 for exactly the
    # blocks a designer had made — and Konzept §7.3 asks for all three handlings singly. Where an
    # id exists in both, the library copy is the EDITED one and stamping the original instead
    # would be a silent wrong answer.
    blk = wl_block.lib_get(REPO, body.block)
    if blk is None:
        blocks = _block_catalogue().get(body.gfx, [])
        blk = next((b for b in blocks if b["id"] == body.block), None)
    if blk is None:
        raise HTTPException(404, f"no block {body.block} in tileset {body.gfx} or in the library")
    area, tiles, _, _ = _load_area_full(side, area_id)
    own = area.raw[area.slot + 0x30] if area.slot + 0x30 < len(area.raw) else 0
    # A block carries its own art, so a foreign tileset is a question, not a refusal: reuse the
    # tiles the destination already has, write the ones it lacks into free slots, and say how many
    # slots short when there are none. That is what self-contained buys.
    gfx_path, _, home = _gfx(own)
    if gfx_path is None:
        raise HTTPException(404, f"no gfx blob for tileset {own}")
    dst = gfx_path.read_bytes()
    free, users = _gfx_free_slots(own)
    r = wl_block.resolve(blk, dst, free)
    # A block brings its own tiles, so "no room" is not an error — it is a fork. New worlds get new
    # tilesets anyway, and forking also removes the shared-tileset hazard: you stop editing a
    # resource eleven areas draw from and give THIS area one of its own. Counting free slots was
    # bookkeeping about a limit that should not bind.
    forked = None
    if r["short"] or (body.fork and len(users) > 1):
        srcgid = own
        used_ids = {t["gfx_id"] for t in list_tilesets()["tilesets"]}
        own = next(g for g in range(256) if g not in used_ids)
        wfg = _work_path(1, "gfx", own)
        wfg.parent.mkdir(parents=True, exist_ok=True)
        wfg.write_bytes(dst)
        # a fork has exactly one user, so every slot THIS area does not reference is free
        own_used = set(tiles)
        free = {t for t in range(wl_tiles.NUM_TILES) if t >= 10 and t not in own_used}
        r = wl_block.resolve(blk, dst, free)
        if r["short"]:
            raise HTTPException(422, f"even a fresh copy is {r['short']} tile slots short — this "
                                     f"block needs {len(blk['art'])} tiles and the area already "
                                     f"uses {len(own_used)}")
        forked = {"from": srcgid, "to": own, "was_shared_with": [u["name"] for u in users]}
    res = wl_block.place(area, tiles, blk, body.x, body.y, r["map"])
    if "error" in res:
        raise HTTPException(422, res["error"])
    _push_area_undo(side, area_id)
    if r["writes"]:
        # Only ever slots nothing references — so nothing already on a map changes appearance, and
        # after a fork this tileset has exactly one user anyway.
        if not forked:
            _push_gfx_undo(own)
        for w in r["writes"]:
            dst = wl_block.put_art(dst, w["tile"], w["art"])
        wg = _work_path(1 if forked else home, "gfx", own)
        wg.parent.mkdir(parents=True, exist_ok=True)
        wg.write_bytes(dst)
    map_path, _ = _resolve(side, "map", area_id)
    if not map_path.is_file():
        raise HTTPException(404, "no map blob")
    wf_map = _work_path(side, "map", area_id)
    wf_map.parent.mkdir(parents=True, exist_ok=True)
    wf_map.write_bytes(save_map_tileids(map_path.read_bytes(), res["tiles"], area.width, area.height))
    nib, cid = list(area.nibble), list(area.cellid)
    for o in res["ops"]:
        i = o["y"] * area.width + o["x"]
        nib[i], cid[i] = o["op"], o["id"]
    rec_out = bytearray(save_area(area, nib, cid))
    if forked:                                    # point this area at its own copy (descriptor +$30)
        rec_out[area.slot + 0x30] = own & 0xFF
    wf_rec = _save_record(side, area_id, bytes(rec_out))
    return {"placed": blk["id"], "at": [body.x, body.y], "kind": blk["kind"],
            "cells": len(blk["cells"]), "holes": res["holes"],
            "tiles_added": [w["tile"] for w in r["writes"]], "tileset": own, "forked": forked,
            "saved": [_rel(wf_map), _rel(wf_rec)]}


@app.get("/api/blueprints")
def list_blueprints() -> dict:
    """The library, newest first."""
    out = []
    if BLUEPRINTS.is_dir():
        for f in sorted(BLUEPRINTS.glob("*.json"), key=lambda p: -p.stat().st_mtime):
            try:
                out.append({**_bp_summary(json.loads(f.read_text())), "file": _rel(f)})
            except (ValueError, OSError) as e:
                out.append({"name": f.stem, "error": str(e), "file": _rel(f)})
    return {"blueprints": out}


@app.post("/api/blueprint/cut")
def blueprint_cut(b: BPCut) -> dict:
    """SNAP a rectangle out of an area as a blueprint.

    Returns the full document plus a summary of what came along and what did NOT — an object that
    references a record it does not own is not broken, but the paste can only bind what travelled,
    so `requires` is the part worth reading before saving it to the library.
    """
    raw, gfx, mapb, _ = _bp_ctx(b.side, b.area_id)
    cells = [(c, r) for r in range(min(b.r0, b.r1), max(b.r0, b.r1) + 1)
             for c in range(min(b.c0, b.c1), max(b.c0, b.c1) + 1)]
    try:
        obj = wl_object.cut(raw, b.side, b.area_id, cells, gfx, mapb, name=b.name)
    except ValueError as e:
        raise HTTPException(422, str(e))
    doc = wl_object.bundle([obj], name=b.name, description=b.description)
    saved = None
    if b.save:
        BLUEPRINTS.mkdir(parents=True, exist_ok=True)
        p = _bp_path(b.name)
        p.write_text(json.dumps(doc, indent=1, sort_keys=True))
        saved = _rel(p)
    return {"blueprint": doc, "summary": _bp_summary(doc), "saved": saved,
            "blocked": wl_object.check(obj, gfx, _portrait_exists)}


@app.get("/api/blueprint/{name}")
def get_blueprint(name: str) -> dict:
    p = _bp_path(name)
    if not p.is_file():
        raise HTTPException(404, f"no blueprint {name!r}")
    doc = json.loads(p.read_text())
    return {"blueprint": doc, "summary": _bp_summary(doc)}


@app.delete("/api/blueprint/{name}")
def delete_blueprint(name: str) -> dict:
    p = _bp_path(name)
    if not p.is_file():
        raise HTTPException(404, f"no blueprint {name!r}")
    p.unlink()
    return {"deleted": name}


class BPSave(BaseModel):
    blueprint: dict
    description: str | None = None


@app.put("/api/blueprint/{name}")
def save_blueprint(name: str, b: BPSave) -> dict:
    """Save an EDITED blueprint.

    The mini-editor can move, repaint and delete cells, and any of those can leave the document
    incoherent — a record nothing reaches, a string nobody names, a `requires` list describing the
    object as it was before. So the document is SETTLED on the way in (wl_object.renormalize_doc)
    rather than trusted, and what that settling changed comes back in the response so the panel can
    say "3 records dropped" instead of quietly losing them.
    """
    p = _bp_path(name)
    doc = dict(b.blueprint)
    doc["name"] = name
    if b.description is not None:
        doc["description"] = b.description
    try:
        doc, changed = wl_object.renormalize_doc(doc)
    except ValueError as e:
        raise HTTPException(422, str(e))
    BLUEPRINTS.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=1, sort_keys=True))
    return {"saved": _rel(p), "changed": changed, "summary": _bp_summary(doc),
            "blueprint": doc}


@app.get("/api/blueprint/{name}/atlas")
def blueprint_atlas(name: str) -> dict:
    """The blueprint's OWN tiles rendered as an atlas, indexed by their own ids.

    A blueprint carries its tile pictures, so it draws itself — no target tileset, no gfx id. That
    is what makes the mini-editor honest: you see the block as it IS, not as it would look after
    landing in some area's palette.
    """
    p = _bp_path(name)
    if not p.is_file():
        raise HTTPException(404, f"no blueprint {name!r}")
    doc = json.loads(p.read_text())
    gfx = bytes(wl_tiles.COLOR_BASE + wl_tiles.NUM_TILES * 4)
    have = []
    for o in doc.get("objects", [doc]):
        for tid, d in (o.get("tiles") or {}).items():
            t = int(tid)
            if 0 <= t < wl_tiles.NUM_TILES:
                gfx = wl_tiles.set_tile(gfx, t, d["pixels"], d["chars"])
                have.append(t)
    return {**wl_tiles.build_atlas(gfx), "tiles": sorted(set(have))}


@app.post("/api/blueprint/{name}/check")
def check_blueprint(name: str, b: BPPlace) -> dict:
    """What a placement into this area would NOT be able to bind — asked before anything is written."""
    p = _bp_path(name)
    if not p.is_file():
        raise HTTPException(404, f"no blueprint {name!r}")
    doc = json.loads(p.read_text())
    _, gfx, _, gid = _bp_ctx(b.side, b.area_id)
    bad = []
    for o in doc.get("objects", [doc]):
        bad += wl_object.check(o, gfx, _portrait_exists)
    return {"blocked": sorted(set(bad)), "gfx_id": gid, "summary": _bp_summary(doc)}


@app.post("/api/blueprint/{name}/place")
def place_blueprint(name: str, b: BPPlace) -> dict:
    """PLACE a blueprint at (col, row).

    The record goes out through _save_record like every other write, so the serializer rebuilds the
    layout and REFUSES the save if any record the engine can already dispatch would change its bytes.
    The tileset and the map grid are written only when the placement actually changed them.
    """
    p = _bp_path(name)
    if not p.is_file():
        raise HTTPException(404, f"no blueprint {name!r}")
    doc = json.loads(p.read_text())
    raw, gfx, mapb, gid = _bp_ctx(b.side, b.area_id)
    try:
        res = wl_object.place(raw, b.side, b.area_id, doc, b.col, b.row, gfx, mapb)
    except (wl_object.TooBig, wl_object.NoRoom) as e:
        raise HTTPException(409, str(e))             # capacity, not a bad request — nothing written
    except ValueError as e:
        raise HTTPException(422, str(e))
    _push_area_undo(b.side, b.area_id)
    if res["gfx"] is not None and gid is not None:
        _push_gfx_undo(gid)
        home = _gfx(gid)[2] or 1
        wf = _work_path(home, "gfx", gid)
        wf.parent.mkdir(parents=True, exist_ok=True)
        wf.write_bytes(res["gfx"])
    if res["map"] is not None:
        wf = _work_path(b.side, "map", b.area_id)
        wf.parent.mkdir(parents=True, exist_ok=True)
        wf.write_bytes(res["map"])
    wf = _save_record(b.side, b.area_id, res["record"])
    return {"placed": name, "at": [b.col, b.row], "saved": _rel(wf),
            "gfx_written": res["gfx"] is not None, "map_written": res["map"] is not None,
            "report": res["report"]}


# ── static UI (mounted last so /api/* wins) ──
if FRONTEND.is_dir():
    @app.get("/")
    def index() -> HTMLResponse:
        """index.html with the version stamped in — badge and cache-buster from one source.

        Served no-store so the `?v=` is always the current one; the assets it names stay
        cacheable, which is the whole point of putting the version in their query string."""
        html = (FRONTEND / "index.html").read_text().replace("__WL_VERSION__", app.version)
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    app.mount("/", StaticFiles(directory=FRONTEND), name="frontend")
