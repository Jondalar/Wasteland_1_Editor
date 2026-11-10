#!/usr/bin/env python3
"""_item_table_test.py — the item descriptor table, checked against things it cannot have been fitted to.

The table was found by following `set_item_descriptor_ptr` to `$3108 + id*8`, and the claim that
byte 4 is "how many uses an item is made with" is the one that matters: it decides whether a door
eats your keycard. Three independent checks, none of which the decode could have been tuned to.
"""
import sys

sys.path.insert(0, ".")
import app                       # noqa: E402
import wl_cell                   # noqa: E402
import wl_item                   # noqa: E402

FAIL = []


def check(c, m):
    if not c:
        FAIL.append(m)


def main():
    t = wl_item.table(app.REPO)
    check(len(t) == 95, f"expected 95 descriptors, got {len(t)}")
    print(f"(a) table: {len(t)} descriptors at $3108 + id*8, "
          f"{sum(1 for r in t if r['consumable'])} consumable / {sum(1 for r in t if not r['consumable'])} kept")

    # --- (b) byte 4 IS the count the game creates an item with -------------------------------
    # The shipped starting party was written by the 1988 game, not by this decode. If byte 4 is the
    # charge count, every item in that pack must carry exactly it.
    import wl_party                                                     # noqa: PLC0415
    src = next((p for p in app.list_parties()["parties"] if "blank" in p["name"] and "plain" in p["name"]), None)
    check(src is not None, "no blank party blob to check against")
    if src:
        r, _ = app._party_path(src["name"])
        d = wl_party.load(r.read_bytes())
        n = bad = 0
        for m in d["members"]:
            for it in (m.get("inventory") or []):
                if it["id"] >= len(t):
                    continue
                n += 1
                if t[it["id"]]["charges"] != it["n"]:
                    bad += 1
                    FAIL.append(f"{it['name']}: descriptor says {t[it['id']]['charges']} charges, "
                                f"the shipped pack carries {it['n']}")
        check(bad == 0, f"{bad} of {n} shipped inventory entries disagree with byte 4")
        print(f"(b) shipped party: {n} inventory entries, {n - bad} match byte 4 exactly")

    # --- (c) the game's own arithmetic --------------------------------------------------------
    # An item required in more places than the game ever hands out cannot be consumed on every use.
    # Those items must have 0 charges — and this is a fact about the RECORDS, nothing to do with
    # the table it is testing.
    from collections import Counter                                     # noqa: PLC0415
    want, give = Counter(), Counter()
    for a in app.list_areas()["areas"]:
        ir = app._ir(a["side"], a["area_id"])
        for rr in (ir.get("groups") or {}).get("2", {}).values():
            if rr.get("op") != "COND":
                continue
            cs = rr.get("conds") or []
            if len(cs) > 12:                       # a long list is shop wares, not conditions
                continue
            for c in cs:
                if (c["opb"] >> 5) in (1, 5, 6, 7):
                    want[c["arg"]] += 1
        for rr in (ir.get("groups") or {}).get("5", {}).values():
            for it in rr.get("items", []):
                if "qty16" not in it:
                    give[it["item"] & 0x7F] += it.get("qty", 1)
    short = [i for i in want if want[i] > give[i] and i < len(t)]
    kept = [i for i in short if t[i]["charges"] == 0]
    # Rope (1 charge, 24 places, 9 ropes) and the Flamethrower are genuinely consumable — you cannot
    # use every rope spot, which is design. So the bar is "almost all", stated rather than hidden.
    check(len(kept) >= len(short) - 2,
          f"only {len(kept)} of {len(short)} over-demanded items have 0 charges")
    print(f"(c) over-demanded items: {len(short)}, of which {len(kept)} carry 0 charges "
          f"({', '.join(wl_cell._item_name(i) for i in short if t[i]['charges'])} are real consumables)")

    # --- (d) keycards are kept, the Engine is not ---------------------------------------------
    for iid, want_consumed in ((46, True), (83, False), (84, False), (85, False), (86, False), (87, False)):
        got = t[iid]["consumable"]
        check(got == want_consumed,
              f"{wl_cell._item_name(iid)}: consumable={got}, expected {want_consumed}")
    print("(d) Engine is consumed; Secpass 1/3/7/A/B are kept — the question this table was found for")

    if FAIL:
        print(f"\nFAILED ({len(FAIL)}):")
        for f in FAIL[:8]:
            print("  -", f)
        return 1
    print("\nbyte 4 is the charge count, and it decides what a door costs you")
    return 0


if __name__ == "__main__":
    sys.exit(main())
