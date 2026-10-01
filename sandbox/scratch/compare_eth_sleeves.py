import pickle
from datetime import datetime, timezone
from collections import defaultdict

with open('sandbox/.cache/mc_books_ytd_verify_2026.pkl', 'rb') as f:
    state = pickle.load(f)

from sandbox.research import exness_combined_montecarlo as mc
mc.pin_external_window(('2026-01-01', '2026-09-23'))
book = mc.run_book(state, lo=int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()), hi=int(datetime(2026, 9, 23, tzinfo=timezone.utc).timestamp()), initial=500.0)

eth_sleeves = [k for k in state['logs'] if k.startswith('ethusd:')]

print(f"{'Sleeve':<28}{'Trades':>8}{'Wins':>6}{'WinRate':>9}{'PnL $':>10}{'MaxLoss':>10}{'MaxWin':>10}")
for s in eth_sleeves:
    trades = [t for t in book['settled'] if t['sleeve'] == s]
    if not trades:
        continue
    w = sum(1 for t in trades if t['pnl'] > 0)
    pnl = sum(t['pnl'] for t in trades)
    min_loss = min(t['pnl'] for t in trades)
    max_win = max(t['pnl'] for t in trades)
    print(f"{s:<28}{len(trades):>8}{w:>6}{w/len(trades):>8.1%}{pnl:>10.2f}{min_loss:>10.2f}{max_win:>10.2f}")
