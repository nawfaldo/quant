import os
from collections import defaultdict
from datetime import datetime, timezone
from sandbox.research import exness_combined_strategies as ecs

rows = ecs.candidate_rows_exact(['ethusd:pullback'])
row = rows['ethusd:pullback']
ecs.ef.resolve('ethusd', allow_stale=True)
_r, log, bars, ctx = ecs.sleeve_trades(row, lo=None, hi=None)

by_year = defaultdict(lambda: {'trades': 0, 'wins': 0, 'losses': 0, 'gross_win': 0.0, 'gross_loss': 0.0, 'net_pnl': 0.0})
for t in log:
    y = datetime.fromtimestamp(t['exit_ts'], timezone.utc).year
    by_year[y]['trades'] += 1
    pnl = t['pnl']
    by_year[y]['net_pnl'] += pnl
    if pnl > 0:
        by_year[y]['wins'] += 1
        by_year[y]['gross_win'] += pnl
    else:
        by_year[y]['losses'] += 1
        by_year[y]['gross_loss'] += pnl

print(f"{'Year':<6}{'Trades':>8}{'Win Rate':>10}{'Net PnL':>12}{'Avg Trade':>12}{'Profit Factor':>14}")
for y in sorted(by_year):
    s = by_year[y]
    wr = s['wins'] / s['trades'] if s['trades'] else 0
    pf = s['gross_win'] / abs(s['gross_loss']) if s['gross_loss'] else float('inf')
    avg = s['net_pnl'] / s['trades'] if s['trades'] else 0
    print(f"{y:<6}{s['trades']:>8}{wr:>9.1%}{s['net_pnl']:>12.2f}{avg:>12.2f}{pf:>14.2f}")
