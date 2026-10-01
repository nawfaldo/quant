import pickle
from datetime import datetime, timezone
from sandbox.research import exness_combined_montecarlo as mc

# Compare top 4 candidates across all 4 windows
windows = [
    ("2022-2026", "sandbox/.cache/mc_books_canon_2226_2022.pkl", "2022-01-01", "2026-08-21"),
    ("2022-2024", "sandbox/.cache/mc_books_canon_2224_2022.pkl", "2022-01-01", "2025-01-01"),
    ("2025-2026", "sandbox/.cache/mc_books_canon_2526.pkl", "2025-01-01", "2026-08-21"),
    ("2026", "sandbox/.cache/mc_books_canon_26_2026.pkl", "2026-01-01", "2026-08-21"),
]

top_cands = [
    "btc:qp_momentum_top@30m",
    "ethusd:lux_body_momentum@15m",
    "ethusd:qp_momentum_top@30m",
    "gbpjpy:td_ema_smi@60m"
]

with open('sandbox/.cache/p99_extra_2226.pkl', 'rb') as f:
    extra = pickle.load(f)

print(f"{'Candidate':<30}{'Window':<11}{'Return %':>12}{'Final $':>11}{'MTM dd %':>11}{'Cand PnL $':>12}")

for cand in top_cands:
    log_all = extra['logs'][cand]
    sym = cand.split(':')[0]
    bars = extra['bars_by'][sym]
    ctx = extra['ctx_by'][sym]
    cand_mem = next((m for m in extra['members'] if f"{m['symbol']}:{m['family']}" == cand.split('@')[0]), {'symbol': sym, 'family': cand.split(':')[1], 'params': {}})
    
    for wname, cfile, s_str, e_str in windows:
        with open(cfile, 'rb') as f:
            st = pickle.load(f)
        lo = int(datetime.fromisoformat(s_str).replace(tzinfo=timezone.utc).timestamp())
        hi = int(datetime.fromisoformat(e_str).replace(tzinfo=timezone.utc).timestamp())
        
        # filter base 22 (no ethusd:pullback)
        t_cand = [t for t in log_all if t['entry_ts'] >= lo and t['exit_ts'] < hi]
        
        base_st = {
            'members': [m for m in st['members'] if f"{m['symbol']}:{m['family']}" != "ethusd:pullback"] + [cand_mem],
            'logs': {k: v for k, v in st['logs'].items() if k != "ethusd:pullback"},
            'bars_by': {**st['bars_by'], sym: bars},
            'ctx_by': {**st['ctx_by'], sym: ctx},
        }
        base_st['logs'][cand] = t_cand
        
        mc.pin_external_window((s_str, e_str))
        b = mc.run_book(base_st, lo=lo, hi=hi, initial=500.0)
        
        pnl = sum(t['pnl'] for t in b['settled'] if t['sleeve'] == cand)
        print(f"{cand:<30}{wname:<11}{b['return_pct']:>11.1f}%{b['final']:>11,.0f}{b['mtm_dd_pct']:>10.2f}%{pnl:>12.2f}")
    print("-" * 88)
