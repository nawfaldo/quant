import json
import os
from sandbox.research import exness_combined_strategies as ecs
from sandbox.research import tt_book

print("Current BOOK sleeves:", len(ecs.BOOK))
with open(os.path.join(ecs.SURVIVOR_DIR, "SURVIVORS.json"), encoding="utf-8") as f:
    survivors = json.load(f)["survivors"]

pass_survivors = [s for s in survivors if s.get("null_verdict") in ("PASS", "PASS*")]
print("Passing survivors:", len(pass_survivors))

keys = set()
for s in pass_survivors:
    k = f"{s['symbol']}:{s['family']}"
    if s.get("timeframe") and str(s.get("study_wave", "")).startswith("tiktok"):
        k = f"{k}@{s['timeframe']}"
    keys.add(k)

# Add tt_book sleeves
for k in tt_book.POOL:
    keys.add(k)

non_book = [k for k in sorted(keys) if k not in ecs.BOOK and not k.startswith("jp225:")]
print("Non-book candidates:", len(non_book))
for k in non_book:
    print(" ", k)
