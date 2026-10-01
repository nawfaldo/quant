import pickle
from datetime import datetime, timezone
with open('sandbox/.cache/mc_books_ytd_verify_2026.pkl', 'rb') as f:
    state = pickle.load(f)

from sandbox.research import exness_combined_montecarlo as mc
mc.pin_external_window(('2026-01-01', '2026-09-23'))
book = mc.run_book(state, lo=int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()), hi=int(datetime(2026, 9, 23, tzinfo=timezone.utc).timestamp()), initial=500.0)

settled = [t for t in book['settled'] if t['sleeve'] == 'ethusd:pullback']
print(f"ethusd:pullback in book: {len(settled)} trades, total PnL: ${sum(t['pnl'] for t in settled):+.2f}")

wins = [t for t in settled if t['pnl'] > 0]
losses = [t for t in settled if t['pnl'] < 0]
print(f"Wins: {len(wins)}, Losses: {len(losses)}, Win rate: {len(wins)/len(settled):.1%}")
print(f"Gross win: +${sum(t['pnl'] for t in wins):.2f}, Gross loss: -${abs(sum(t['pnl'] for t in losses)):.2f}")

# Look at monthly breakdown of portfolio PnL vs quantity
by_month = {}
for t in settled:
    m = datetime.fromtimestamp(t['exit_ts'], timezone.utc).strftime("%Y-%m")
    if m not in by_month:
        by_month[m] = {'n': 0, 'w': 0, 'pnl': 0.0, 'qtys': []}
    by_month[m]['n'] += 1
    if t['pnl'] > 0:
        by_month[m]['w'] += 1
    by_month[m]['pnl'] += t['pnl']
    by_month[m]['qtys'].append(t.get('quantity', 0))

print("\nMonthly Portfolio Contribution:")
for m, d in sorted(by_month.items()):
    avg_qty = sum(d['qtys']) / len(d['qtys']) if d['qtys'] else 0
    print(f"{m}: trades={d['n']:2d}, wins={d['w']:2d}, win_rate={d['w']/d['n']:.1%}, pnl=${d['pnl']:+6.2f}, avg_qty={avg_qty:.3f}")

# Compare with usdjpy:kendall in 2026 to see why user asks "why is it become like usdjpy:kendall?"
