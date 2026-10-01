import pickle
from datetime import datetime, timezone
from sandbox.research import exness_combined_montecarlo as mc

windows = [
    ("2022-2026", "sandbox/.cache/mc_books_canon_2226_2022.pkl", "2022-01-01", "2026-08-21"),
    ("2022-2024", "sandbox/.cache/mc_books_canon_2224_2022.pkl", "2022-01-01", "2025-01-01"),
    ("2025-2026", "sandbox/.cache/mc_books_canon_2526.pkl", "2025-01-01", "2026-08-21"),
    ("2026", "sandbox/.cache/mc_books_canon_26_2026.pkl", "2026-01-01", "2026-08-21"),
]

print(f"{'Window':<12}{'Current Ret%':>14}{'No Pullback Ret%':>18}{'Diff Ret%':>12}{'Curr MTMdd':>12}{'No PB MTMdd':>14}{'Diff MTMdd':>12}")

for name, cache_file, start_str, end_str in windows:
    with open(cache_file, 'rb') as f:
        state = pickle.load(f)
    
    lo = int(datetime.fromisoformat(start_str).replace(tzinfo=timezone.utc).timestamp())
    hi = int(datetime.fromisoformat(end_str).replace(tzinfo=timezone.utc).timestamp())
    
    mc.pin_external_window((start_str, end_str))
    b_curr = mc.run_book(state, lo=lo, hi=hi, initial=500.0)
    
    state_no_pb = {
        'members': [m for m in state['members'] if f"{m['symbol']}:{m['family']}" != "ethusd:pullback"],
        'logs': {k: v for k, v in state['logs'].items() if k != "ethusd:pullback"},
        'bars_by': state['bars_by'],
        'ctx_by': state['ctx_by'],
    }
    b_no_pb = mc.run_book(state_no_pb, lo=lo, hi=hi, initial=500.0)
    
    d_ret = b_no_pb['return_pct'] - b_curr['return_pct']
    d_dd = b_no_pb['mtm_dd_pct'] - b_curr['mtm_dd_pct']
    
    print(f"{name:<12}{b_curr['return_pct']:>13.1f}%{b_no_pb['return_pct']:>17.1f}%{d_ret:>+11.1f}%{b_curr['mtm_dd_pct']:>11.2f}%{b_no_pb['mtm_dd_pct']:>13.2f}%{d_dd:>+11.2f}%")
