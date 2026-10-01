import os
from datetime import datetime, timezone
from sandbox.research import exness_combined_strategies as ecs
from sandbox.research import _mc_books as mb
from sandbox.research import exness_combined_montecarlo as mc

os.environ["MC_START"] = "2022-01-01"
mb.arm()
mc.prepare = mb.prepare
state = mc.prepare()

windows = [
    ('2022-2026', '2022-01-01', '2026-08-21'),
    ('2022-2024', '2022-01-01', '2025-01-01'),
]
for name, s_str, e_str in windows:
    lo = int(datetime.fromisoformat(s_str).replace(tzinfo=timezone.utc).timestamp())
    hi = int(datetime.fromisoformat(e_str).replace(tzinfo=timezone.utc).timestamp())
    mc.pin_external_window((s_str, e_str))
    b = mc.run_book(state, lo=lo, hi=hi, initial=500.0)
    print(f"{name:<12}{b['return_pct']:>11.1f}%{b['final']:>12,.2f}{b['mtm_dd_pct']:>11.2f}%{b['trades']:>10}")
