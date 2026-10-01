import pickle
from datetime import datetime, timezone

with open('sandbox/.cache/mc_books_ytd_verify_2026.pkl', 'rb') as f:
    state = pickle.load(f)

bars = state['bars_by']['ethusd']
b2026 = [b for b in bars if b[0] >= 1767225600]
p_start = b2026[0][4]
p_end = b2026[-1][4]
print(f"ETH 2026 start: {p_start:.2f}, end: {p_end:.2f}, change: {(p_end/p_start - 1):+.2%}")

by_m = {}
for b in b2026:
    dt = datetime.fromtimestamp(b[0], timezone.utc)
    m = dt.strftime('%Y-%m')
    if m not in by_m:
        by_m[m] = {'open': b[1], 'high': b[2], 'low': b[3], 'close': b[4]}
    by_m[m]['high'] = max(by_m[m]['high'], b[2])
    by_m[m]['low'] = min(by_m[m]['low'], b[3])
    by_m[m]['close'] = b[4]

for m, d in sorted(by_m.items()):
    ret = (d['close'] / d['open'] - 1) * 100
    rng = (d['high'] - d['low']) / d['open'] * 100
    print(f"{m}: open={d['open']:7.2f}, high={d['high']:7.2f}, low={d['low']:7.2f}, close={d['close']:7.2f}, ret={ret:+6.2f}%, range={rng:5.1f}%")
