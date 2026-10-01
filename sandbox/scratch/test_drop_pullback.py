import pickle
from datetime import datetime, timezone
from sandbox.research import exness_combined_strategies as ecs
from sandbox.research import exness_combined_montecarlo as mc

with open('sandbox/.cache/mc_books_ytd_verify_2026.pkl', 'rb') as f:
    state = pickle.load(f)

# 2026 test
lo_2026 = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
hi_2026 = int(datetime(2026, 9, 23, tzinfo=timezone.utc).timestamp())

mc.pin_external_window(('2026-01-01', '2026-09-23'))

b_curr = mc.run_book(state, lo=lo_2026, hi=hi_2026, initial=500.0)

# Filter out ethusd:pullback
state_no_pb = {
    'members': [m for m in state['members'] if f"{m['symbol']}:{m['family']}" != "ethusd:pullback"],
    'logs': {k: v for k, v in state['logs'].items() if k != "ethusd:pullback"},
    'bars_by': state['bars_by'],
    'ctx_by': state['ctx_by'],
}

b_no_pb = mc.run_book(state_no_pb, lo=lo_2026, hi=hi_2026, initial=500.0)

print(f"{'Metric':<25}{'Current (23 sleeves)':>22}{'Drop ethusd:pullback (22)':>28}")
print(f"{'2026 Return %':<25}{b_curr['return_pct']:>21.2f}%{b_no_pb['return_pct']:>27.2f}%")
print(f"{'2026 Final $':<25}{b_curr['final']:>22,.2f}{b_no_pb['final']:>28,.2f}")
print(f"{'2026 MTM dd %':<25}{b_curr['mtm_dd_pct']:>21.2f}%{b_no_pb['mtm_dd_pct']:>27.2f}%")
print(f"{'2026 Closed dd %':<25}{b_curr['max_dd_pct']:>21.2f}%{b_no_pb['max_dd_pct']:>27.2f}%")
print(f"{'2026 Trades':<25}{b_curr['trades']:>22}{b_no_pb['trades']:>28}")
