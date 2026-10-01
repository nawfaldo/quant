import pickle
from datetime import datetime, timezone
from sandbox.research import exness_combined_strategies as ecs
from sandbox.research import exness_combined_montecarlo as mc

# 1. Load base 2026 book
with open('sandbox/.cache/mc_books_ytd_verify_2026.pkl', 'rb') as f:
    state_26 = pickle.load(f)

# Base 22 sleeves (drop ethusd:pullback)
base_22_keys = [k for k in state_26['logs'] if k != "ethusd:pullback"]

# Load extra candidate logs
with open('sandbox/.cache/p99_extra_2526.pkl', 'rb') as f:
    extra_2526 = pickle.load(f)

with open('sandbox/.cache/mc_books_canon_2226_2022.pkl', 'rb') as f:
    state_2226 = pickle.load(f)
with open('sandbox/.cache/p99_extra_2226.pkl', 'rb') as f:
    extra_2226 = pickle.load(f)

lo_26 = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
hi_26 = int(datetime(2026, 8, 21, tzinfo=timezone.utc).timestamp())
lo_22 = int(datetime(2022, 1, 1, tzinfo=timezone.utc).timestamp())
hi_22 = int(datetime(2026, 8, 21, tzinfo=timezone.utc).timestamp())

mc.pin_external_window(("2026-01-01", "2026-08-21"))
base_state_26 = {
    'members': [m for m in state_26['members'] if f"{m['symbol']}:{m['family']}" != "ethusd:pullback"],
    'logs': {k: state_26['logs'][k] for k in base_22_keys},
    'bars_by': state_26['bars_by'],
    'ctx_by': state_26['ctx_by'],
}
b_base_26 = mc.run_book(base_state_26, lo=lo_26, hi=hi_26, initial=500.0)

mc.pin_external_window(("2022-01-01", "2026-08-21"))
base_state_2226 = {
    'members': [m for m in state_2226['members'] if f"{m['symbol']}:{m['family']}" != "ethusd:pullback"],
    'logs': {k: state_2226['logs'][k] for k in base_22_keys if k in state_2226['logs']},
    'bars_by': state_2226['bars_by'],
    'ctx_by': state_2226['ctx_by'],
}
b_base_2226 = mc.run_book(base_state_2226, lo=lo_22, hi=hi_22, initial=500.0)

candidates = sorted(k for k in extra_2226['logs'] if k not in base_22_keys and k != "ethusd:pullback")
print(f"Testing {len(candidates)} candidates...")

# Cache contexts for candidate symbols
sym_ctx = {}
def get_sym_bars(sym):
    if sym in sym_ctx:
        return sym_ctx[sym]
    if sym in base_state_26['bars_by']:
        sym_ctx[sym] = (base_state_26['bars_by'][sym], base_state_26['ctx_by'][sym])
    elif sym in extra_2226['bars_by']:
        sym_ctx[sym] = (extra_2226['bars_by'][sym], extra_2226['ctx_by'][sym])
    else:
        try:
            bars, ctx = ecs._context(sym)
            sym_ctx[sym] = (bars, {"cfg": ctx["cfg"], "symbol": ctx.get("symbol", sym)})
        except Exception:
            sym_ctx[sym] = (None, None)
    return sym_ctx[sym]

results = []
for cand in candidates:
    log_22 = extra_2226['logs'][cand]
    t_26 = [t for t in log_22 if t['entry_ts'] >= lo_26 and t['exit_ts'] < hi_26]
    t_2224 = [t for t in log_22 if t['exit_ts'] < int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())]
    
    if len(t_26) < 10 or len(t_2224) < 30:
        continue
    
    pnl_26_raw = sum(t.get('pnl', 0) for t in t_26)
    w_26 = sum(1 for t in t_26 if t.get('pnl', 0) > 0)
    wr_26 = w_26 / len(t_26)
    
    if pnl_26_raw <= 0 or wr_26 < 0.45:
        continue
        
    sym = cand.split(':')[0]
    bars, ctx = get_sym_bars(sym)
    if bars is None:
        continue
        
    cand_mem = next((m for m in extra_2226['members'] if f"{m['symbol']}:{m['family']}" == cand.split('@')[0] or (cand.endswith('@we') and f"{m['symbol']}:{m['family']}@we" == cand)), None)
    if not cand_mem:
        cand_mem = {'symbol': sym, 'family': cand.split(':')[1], 'params': {}}
    
    try:
        mc.pin_external_window(("2026-01-01", "2026-08-21"))
        test_state_26 = {
            'members': base_state_26['members'] + [cand_mem],
            'logs': {**base_state_26['logs'], cand: t_26},
            'bars_by': {**base_state_26['bars_by'], sym: bars},
            'ctx_by': {**base_state_26['ctx_by'], sym: ctx},
        }
        b_cand_26 = mc.run_book(test_state_26, lo=lo_26, hi=hi_26, initial=500.0)
        
        if b_cand_26['mtm_dd_pct'] > 14.0 or b_cand_26['return_pct'] < b_base_26['return_pct']:
            continue
            
        mc.pin_external_window(("2022-01-01", "2026-08-21"))
        test_state_2226 = {
            'members': base_state_2226['members'] + [cand_mem],
            'logs': {**base_state_2226['logs'], cand: log_22},
            'bars_by': {**base_state_2226['bars_by'], sym: bars},
            'ctx_by': {**base_state_2226['ctx_by'], sym: ctx},
        }
        b_cand_2226 = mc.run_book(test_state_2226, lo=lo_22, hi=hi_22, initial=500.0)
        
        if b_cand_2226['mtm_dd_pct'] > 21.2:
            continue
            
        cand_pnl_26 = sum(t['pnl'] for t in b_cand_26['settled'] if t['sleeve'] == cand)
        cand_trades_26 = sum(1 for t in b_cand_26['settled'] if t['sleeve'] == cand)
        
        results.append({
            'sleeve': cand,
            'ret_26': b_cand_26['return_pct'],
            'dd_26': b_cand_26['mtm_dd_pct'],
            'pnl_26': cand_pnl_26,
            'n_26': cand_trades_26,
            'ret_2226': b_cand_2226['return_pct'],
            'dd_2226': b_cand_2226['mtm_dd_pct'],
        })
    except Exception as e:
        continue

print(f"\nFound {len(results)} valid replacement candidates!")
results.sort(key=lambda r: (-r['ret_26'], r['dd_26']))

print(f"{'Sleeve':<35}{'2026 Ret%':>10}{'2026 DD%':>10}{'2026 PnL$':>10}{'22-26 Ret%':>12}{'22-26 DD%':>11}")
for r in results[:25]:
    print(f"{r['sleeve']:<35}{r['ret_26']:>9.1f}%{r['dd_26']:>9.2f}%{r['pnl_26']:>10.1f}{r['ret_2226']:>11.0f}%{r['dd_2226']:>10.2f}%")
