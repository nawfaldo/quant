import pickle
from datetime import datetime, timezone
from sandbox.research import exness_combined_strategies as ecs
from sandbox.research import _mc_books as mb
from sandbox.research import exness_combined_montecarlo as mc

mb.arm()
mc.prepare = mb.prepare

windows = [
    ('2022-2026', '2022-01-01', '2026-08-21'),
    ('2022-2024', '2022-01-01', '2025-01-01'),
    ('2025-2026', '2025-01-01', '2026-08-21'),
    ('2026', '2026-01-01', '2026-08-21'),
]

state = mc.prepare()

print(f"NEW CANON BOOK ({len(ecs.BOOK)} sleeves):")
print(f"{'Window':<12}{'Return %':>12}{'Final $':>12}{'MTM dd %':>12}{'Trades':>10}")
for name, s_str, e_str in windows:
    lo = int(datetime.fromisoformat(s_str).replace(tzinfo=timezone.utc).timestamp())
    hi = int(datetime.fromisoformat(e_str).replace(tzinfo=timezone.utc).timestamp())
    mc.pin_external_window((s_str, e_str))
    b = mc.run_book(state, lo=lo, hi=hi, initial=500.0)
    print(f"{name:<12}{b['return_pct']:>11.1f}%{b['final']:>12,.2f}{b['mtm_dd_pct']:>11.2f}%{b['trades']:>10}")
