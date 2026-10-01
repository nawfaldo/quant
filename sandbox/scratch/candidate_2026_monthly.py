import pickle
from datetime import datetime, timezone
from collections import defaultdict
from sandbox.research import exness_combined_montecarlo as mc

with open('sandbox/.cache/p99_extra_2226.pkl', 'rb') as f:
    extra = pickle.load(f)

with open('sandbox/.cache/mc_books_canon_26_2026.pkl', 'rb') as f:
    st_26 = pickle.load(f)

# Base 22 sleeves (drop ethusd:pullback)
base_22_st = {
    'members': [m for m in st_26['members'] if f"{m['symbol']}:{m['family']}" != "ethusd:pullback"],
    'logs': {k: v for k, v in st_26['logs'].items() if k != "ethusd:pullback"},
    'bars_by': st_26['bars_by'],
    'ctx_by': st_26['ctx_by'],
}

cands = [
    ("Option A", "ethusd:lux_body_momentum@15m"),
    ("Option B", "btc:qp_momentum_top@30m"),
    ("Option C", "gbpjpy:td_ema_smi@60m")
]

lo_26 = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
hi_26 = int(datetime(2026, 8, 21, tzinfo=timezone.utc).timestamp())

mc.pin_external_window(("2026-01-01", "2026-08-21"))

print(f"{'Candidate':<32}{'01':>8}{'02':>8}{'03':>8}{'04':>8}{'05':>8}{'06':>8}{'07':>8}{'08':>8}{'Total $':>10}{'Trades':>7}")

for label, cand in cands:
    sym = cand.split(':')[0]
    bars = extra['bars_by'][sym]
    ctx = extra['ctx_by'][sym]
    cand_mem = next((m for m in extra['members'] if f"{m['symbol']}:{m['family']}" == cand.split('@')[0]), {'symbol': sym, 'family': cand.split(':')[1], 'params': {}})
    
    t_cand = [t for t in extra['logs'][cand] if t['entry_ts'] >= lo_26 and t['exit_ts'] < hi_26]
    
    test_st = {
        'members': base_22_st['members'] + [cand_mem],
        'logs': {**base_22_st['logs'], cand: t_cand},
        'bars_by': {**base_22_st['bars_by'], sym: bars},
        'ctx_by': {**base_22_st['ctx_by'], sym: ctx},
    }
    
    b = mc.run_book(test_st, lo=lo_26, hi=hi_26, initial=500.0)
    
    by_month = defaultdict(float)
    cand_trades = [t for t in b['settled'] if t['sleeve'] == cand]
    for t in cand_trades:
        m = datetime.fromtimestamp(t['exit_ts'], timezone.utc).strftime("%m")
        by_month[m] += t['pnl']
        
    months_str = "".join(f"{by_month.get(f'{i:02d}', 0.0):>+8.1f}" for i in range(1, 9))
    tot = sum(by_month.values())
    print(f"{cand:<32}" + months_str + f"{tot:>+10.2f}{len(cand_trades):>7}")
