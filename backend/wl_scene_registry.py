"""wl_scene_registry.py — which built routines a beat may CALL, and with what.

`call scene X` is the one effect the designer does NOT generate (concept §2.6): every other effect
becomes records the editor writes itself, this one hands control to 6502 somebody wrote by hand — a
$CA00 service blob or an area's own grp16 handler. That is the most dangerous entry in the whole
vocabulary and, without a declaration, the one the battery is blind to: it cannot check the
parameters, and it cannot say which state the routine touches.

So the rule from §2.6, and this module is where it lives: **a routine without a registry entry is
not callable.** `check_call` says so out loud rather than letting an unknown name through as a
mystery byte in a record.

The registry itself is `editor/annotations/scenes.json`, hand-maintained next to the routine it
describes — same discipline as `grp16_effects.json`. There is no generator, on purpose: the file is
where a human states what the code MEANS, and a scan of the bytes cannot state that.

WHAT IS DECLARED, AND WHAT IS NOT. Only the values the CALLER chooses are parameters. The stock
store's portrait is baked into the blob as an immediate operand, so it is not a parameter of the
call — it is a fact about the blob, and it lives in the entry's `note`. Everything in the file is
traceable through its `evidence` list to a source in this repo.

Read: `load()` · `get(name)` · `names()`. Check: `check_call(name, params)` returns a list of
error strings, empty when the call is good; `problems()` audits the file itself, which is the other
half — a hand-edited registry that lies is worse than none.
"""
import json
import os
import pathlib

# The file sits with the other editor annotations, not under cart_EF: it is editor data, the cart
# build never reads it. WL_REPO is honoured because app.py already resolves the repo that way.
_HERE = pathlib.Path(__file__).resolve().parent
_REPO = pathlib.Path(os.environ.get("WL_REPO", _HERE.parent.parent)).resolve()
FILE = _REPO / "editor" / "annotations" / "scenes.json"

KINDS = ("scene", "grp16")
PARAM_KINDS = ("int", "str_id", "text", "list")

# A text reference is 7 bits. Measured over all 43 shipped areas: 5355 references, none above 127
# (RECORD_FORMAT.md, [[wl1-text-id-7-bit]]). An entry may still narrow it, never widen it.
STR_ID_MAX = 127

_cache = None
_cache_key = None


def _fresh(path):
    """(size, mtime) of the file — reload when a hand edit lands, without re-reading every call."""
    try:
        st = path.stat()
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def load(force: bool = False) -> dict:
    """The registry: {name: entry}. Meta keys (`_version`, `_about`, …) are not entries.

    A missing file is an EMPTY registry, not a crash — that state is legible ("nothing is callable
    yet") and every caller already has to handle the no-entry case anyway.
    """
    global _cache, _cache_key
    key = _fresh(FILE)
    if force or _cache is None or key != _cache_key:
        raw = json.loads(FILE.read_text()) if FILE.is_file() else {}
        _cache = {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, dict)}
        _cache_key = key
    return _cache


def names() -> list:
    return sorted(load())


def get(name: str):
    """The entry, or None. None IS the answer 'not callable' — see check_call."""
    return load().get(name)


def params_of(name: str) -> list:
    e = get(name)
    return list(e.get("params", [])) if e else []


def _num(v) -> bool:
    # bool is an int subclass in Python and True would sail through every range check as 1. A record
    # byte is never a flag object; refuse it here rather than emit $01 for `true`.
    return isinstance(v, int) and not isinstance(v, bool)


def _bounds(decl: dict) -> tuple:
    lo, hi = decl.get("min"), decl.get("max")
    if decl.get("kind") == "str_id" and hi is None:
        hi = STR_ID_MAX                      # the 7-bit ceiling holds even when an entry omits it
    if decl.get("kind") == "str_id" and lo is None:
        lo = 0
    return lo, hi


def _check_value(decl: dict, value, where: str) -> list:
    """One value against one declaration. `where` names it in the message ('page[2]', 'amount')."""
    kind = decl.get("kind")
    lo, hi = _bounds(decl)
    if kind in ("int", "str_id"):
        if not _num(value):
            return [f"{where}: expected a whole number ({kind}), got {type(value).__name__}"]
        out = []
        if lo is not None and value < lo:
            out.append(f"{where}: {value} is below the minimum {lo}")
        if hi is not None and value > hi:
            out.append(f"{where}: {value} is above the maximum {hi}"
                       + (" — a text id is 7 bits" if kind == "str_id" and hi == STR_ID_MAX else ""))
        return out
    if kind == "text":
        if not isinstance(value, str):
            return [f"{where}: expected text, got {type(value).__name__}"]
        out = []
        # The record stores these as plain $00-terminated bytes; anything outside ASCII does not
        # survive the trip and would be silently mangled into the record.
        try:
            value.encode("ascii")
        except UnicodeEncodeError as exc:
            out.append(f"{where}: {exc.object[exc.start:exc.end]!r} is not ASCII and cannot go into "
                       f"the record")
        if lo is not None and len(value) < lo:
            out.append(f"{where}: {len(value)} characters, at least {lo} required")
        if hi is not None and len(value) > hi:
            out.append(f"{where}: {len(value)} characters, at most {hi} fit")
        return out
    if kind == "list":
        if not isinstance(value, (list, tuple)):
            return [f"{where}: expected a list, got {type(value).__name__}"]
        out = []
        if lo is not None and len(value) < lo:
            out.append(f"{where}: {len(value)} entries, at least {lo} required")
        if hi is not None and len(value) > hi:
            out.append(f"{where}: {len(value)} entries, at most {hi} fit")
        item = decl.get("item")
        if item:
            for i, v in enumerate(value):
                out += _check_value(item, v, f"{where}[{i}]")
        return out
    return [f"{where}: the registry declares kind {kind!r}, which this editor does not know "
            f"(known: {', '.join(PARAM_KINDS)})"]


def check_call(name: str, params=None) -> list:
    """Errors for `call scene <name>` with `params`. Empty list = the call is fine.

    Every message is a sentence a designer can act on; the caller (place / validate) shows them
    verbatim.
    """
    entry = get(name)
    if entry is None:
        known = ", ".join(names()) or "the registry is empty"
        return [f"scene {name!r} has no entry in editor/annotations/scenes.json — a routine without "
                f"a registry entry is not callable (concept §2.6). Declared: {known}"]
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return [f"scene {name!r}: parameters must be given by name, got {type(params).__name__}"]

    decls = {d["key"]: d for d in entry.get("params", []) if isinstance(d, dict) and "key" in d}
    errors = []
    for key in params:
        if key not in decls:
            errors.append(f"scene {name!r}: {key!r} is not a parameter of this routine"
                          + (f" (it takes {', '.join(decls)})" if decls
                             else " (it takes no parameters)"))
    for key, decl in decls.items():
        if key not in params:
            if not decl.get("optional"):
                errors.append(f"scene {name!r}: {key!r} is missing"
                              + (f" — {decl['note']}" if decl.get("note") else ""))
            continue
        errors += [f"scene {name!r}: {msg}" for msg in _check_value(decl, params[key], key)]
    return errors


def problems() -> list:
    """Audit the FILE. A hand-maintained registry can be wrong in ways no call will reveal — a
    kind nobody implements, a range that excludes every value, a `list` with no element rule."""
    out = []
    for name, e in load().items():
        for field in ("name", "kind", "entry", "source", "params", "reads", "writes", "area_bound"):
            if field not in e:
                out.append(f"{name}: no {field!r}")
        if e.get("kind") not in KINDS:
            out.append(f"{name}: kind {e.get('kind')!r} is not one of {KINDS}")
        if e.get("kind") == "grp16":
            # grp16 lives INSIDE one area's record and is indexed by that area's own table — it
            # cannot be reached from anywhere else (GRP16_SPEC §1). And the event id IS the call:
            # it is the byte the raising op-6 EVENT record carries.
            if not e.get("area_bound"):
                out.append(f"{name}: grp16 handlers are always area-bound, but area_bound is false")
            if not isinstance(e.get("event"), int):
                out.append(f"{name}: grp16 entry without an `event` id — nothing can raise it")
        if e.get("kind") == "scene":
            # A scene is selected by the record's byte0 ($80|selector) and only exists on the sides
            # whose baseline carries the blob; without both, a call cannot even be emitted.
            if not isinstance(e.get("selector"), int):
                out.append(f"{name}: scene entry without a `selector`")
            if not e.get("sides"):
                out.append(f"{name}: scene entry without `sides` — which sides carry the blob?")
        if e.get("area_bound") and not e.get("area"):
            out.append(f"{name}: area_bound but no `area` says which one")
        for d in e.get("params", []):
            key = d.get("key", "?")
            if d.get("kind") not in PARAM_KINDS:
                out.append(f"{name}.{key}: kind {d.get('kind')!r} is not one of {PARAM_KINDS}")
            lo, hi = _bounds(d)
            if lo is not None and hi is not None and lo > hi:
                out.append(f"{name}.{key}: min {lo} is above max {hi} — nothing can pass")
            if d.get("kind") == "list" and not d.get("item"):
                out.append(f"{name}.{key}: a list without an `item` rule cannot check its entries")
    return out


if __name__ == "__main__":
    reg = load()
    print(f"{FILE}\n{len(reg)} routines: {', '.join(names())}\n")
    bad = problems()
    print("file audit:", "clean" if not bad else "\n  " + "\n  ".join(bad))

    # One good call and two bad ones — the two failure modes §2.6 names: no entry at all, and a
    # parameter the declaration refuses.
    cases = [
        ("valid — the talk handler as it is actually built",
         "trex-talk", {"pages": [0x40, 0x41, 0x42], "label": "The TREX", "portrait": 8}),
        ("invalid — a routine nobody declared",
         "trex-teleport", {"area": 51}),
        ("invalid — page id above the 7-bit text ceiling, and a label past the 11-char sign",
         "trex-talk", {"pages": [0x40, 200], "label": "Toastmaster of the Wasteland", "portrait": 8}),
    ]
    for title, name, params in cases:
        errs = check_call(name, params)
        print(f"\n{title}\n  call scene {name} {params}")
        print("  -> ok" if not errs else "".join(f"\n  -> {e}" for e in errs))

    ok = (not bad and not check_call(*cases[0][1:]) and check_call(*cases[1][1:])
          and len(check_call(*cases[2][1:])) == 2)
    print("\nself-test:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)
