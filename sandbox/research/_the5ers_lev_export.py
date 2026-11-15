"""`results/filtered/leveraged_the5ers/LEV.json` (from `_replay_the5ers` with
T5_MODE=lev T5_SRC=results/filtered/the5ers) -> one JSON per cell + SURVIVORS.json
in `results/filtered/leveraged_the5ers/`, the format of
`results/filtered/leveraged_fundednext/` (the user, 2026-11-08: "do leverage
thing like fundednext"): `leverage` = k sized on 2024-2025 to a 10% MTM
drawdown + the three windows at k + the whole-number ladder; the index ranked by
leveraged 2026 return, with `ladder_max_k` and the ladder summary.

    py -m sandbox.research._the5ers_lev_export
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "..", "results")
SRC = os.path.join(RESULTS, "filtered", "the5ers")
DST = os.path.join(RESULTS, "filtered", "leveraged_the5ers")


def main():
    lev = json.load(open(os.path.join(DST, "LEV.json"), encoding="utf-8"))
    target, rows = lev["target_dd_pct"], lev["rows"]
    index = json.load(open(os.path.join(SRC, "SURVIVORS.json"), encoding="utf-8"))
    for name in os.listdir(DST):
        if name.endswith(".json") and name not in ("LEV.json",):
            os.remove(os.path.join(DST, name))
    out = []
    for s in index["survivors"]:
        row = rows.get(s["file"])
        if row is None:
            continue
        steps = row.get("ladder") or []
        cell = json.load(open(os.path.join(SRC, s["file"]), encoding="utf-8"))
        cell["leverage"] = {"target_dd_pct": target, "balance": lev["balance"], **row}
        with open(os.path.join(DST, s["file"]), "w", encoding="utf-8") as handle:
            json.dump(cell, handle, indent=1)
        inside = [st["k"] for st in steps if st["2024-2025"]["max_dd_pct"] <= target]
        out.append({**s, "k": row["k"],
                    "lev_2026_return_pct": row["2026"]["return_pct"],
                    "lev_2026_max_dd_pct": row["2026"]["max_dd_pct"],
                    "lev_2024_2025_return_pct": row["2024-2025"]["return_pct"],
                    "lev_2024_2025_max_dd_pct": row["2024-2025"]["max_dd_pct"],
                    "lev_2024_2026_return_pct": row["2024-2026"]["return_pct"],
                    "lev_2024_2026_max_dd_pct": row["2024-2026"]["max_dd_pct"],
                    "lev_worst_day_2026_usd": row["worst_day_2026_usd"],
                    "ladder_max_k": max(inside) if inside else 0,
                    "ladder": [{"k": st["k"], "is_dd": st["2024-2025"]["max_dd_pct"],
                                "ret_2026": st["2026"]["return_pct"],
                                "dd_2026": st["2026"]["max_dd_pct"],
                                "ret_2024_2026": st["2024-2026"]["return_pct"]} for st in steps]})
    out.sort(key=lambda s: -s["lev_2026_return_pct"])
    with open(os.path.join(DST, "SURVIVORS.json"), "w", encoding="utf-8") as handle:
        json.dump({"count": len(out), "target_dd_pct": target, "balance": lev["balance"],
                   "broker": "the5ers", "k_set_on": "2024-2025 max MTM drawdown",
                   "ranked_by": "leveraged 2026 return", "survivors": out}, handle, indent=2)
    print(f"wrote {len(out)} of {index['count']} -> {os.path.abspath(DST)}")


if __name__ == "__main__":
    main()
