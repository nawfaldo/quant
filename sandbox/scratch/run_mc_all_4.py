import json
import math
import multiprocessing
import os
import pickle
import random
import statistics
import time
from datetime import datetime, timezone
from sandbox.research import exness_combined_strategies as ecs
from sandbox.research import exness_combined_montecarlo as mc

lo_str, hi_str = "2022-01-01", "2026-08-21"
lo = int(datetime.fromisoformat(lo_str).replace(tzinfo=timezone.utc).timestamp())
hi = int(datetime.fromisoformat(hi_str).replace(tzinfo=timezone.utc).timestamp())
block_days = 14
paths = 500
workers = 12

with open('sandbox/.cache/mc_books_canon_2226_2022.pkl', 'rb') as f:
    st_canon = pickle.load(f)

with open('sandbox/.cache/p99_extra_2226.pkl', 'rb') as f:
    extra = pickle.load(f)

# Base 22 sleeves: drop ethusd:pullback
base_members = [m for m in st_canon['members'] if f"{m['symbol']}:{m['family']}" != "ethusd:pullback"]
base_logs = {k: v for k, v in st_canon['logs'].items() if k != "ethusd:pullback"}

configs = [
    ("Base 22 (no pullback)", None),
    ("Option A (+ethusd:lux_body_momentum@15m)", "ethusd:lux_body_momentum@15m"),
    ("Option B (+btc:qp_momentum_top@30m)", "btc:qp_momentum_top@30m"),
    ("Option C (+gbpjpy:td_ema_smi@60m)", "gbpjpy:td_ema_smi@60m"),
]

# Ensure cache dir exists
cache_dir = "sandbox/.cache/mc_compare_tmp"
os.makedirs(cache_dir, exist_ok=True)

mc.pin_external_window((lo_str, hi_str))
blocks = mc.blocks_of(block_days)

def run_config_mc(label, cand_key):
    st = {
        'members': list(base_members),
        'logs': dict(base_logs),
        'bars_by': dict(st_canon['bars_by']),
        'ctx_by': dict(st_canon['ctx_by']),
    }
    if cand_key:
        sym = cand_key.split(':')[0]
        st['bars_by'][sym] = extra['bars_by'][sym]
        st['ctx_by'][sym] = extra['ctx_by'][sym]
        cand_mem = next((m for m in extra['members'] if f"{m['symbol']}:{m['family']}" == cand_key.split('@')[0]), {'symbol': sym, 'family': cand_key.split(':')[1], 'params': {}})
        st['members'].append(cand_mem)
        st['logs'][cand_key] = extra['logs'][cand_key]
        
    cpath = os.path.join(cache_dir, f"{label[:8]}.pkl")
    with open(cpath, 'wb') as f:
        pickle.dump(st, f, protocol=5)
        
    actual = mc.reference(st, blocks, block_days)
    
    # Run multiprocessing bootstrap
    results = []
    with multiprocessing.Pool(workers, mc._init_worker, (cpath, block_days)) as pool:
        for row in pool.imap_unordered(mc._path_boot, range(1, paths + 1), chunksize=5):
            results.append(row)
            
    rets = [r['return_pct'] for r in results]
    dds = [r['mtm_dd_pct'] for r in results]
    
    return {
        'label': label,
        'actual_ret': actual['return_pct'],
        'actual_dd': actual['mtm_dd_pct'],
        'mc_p5_ret': mc.quantile(rets, 5),
        'mc_med_ret': mc.quantile(rets, 50),
        'mc_p95_ret': mc.quantile(rets, 95),
        'mc_dd_p50': mc.quantile(dds, 50),
        'mc_dd_p95': mc.quantile(dds, 95),
        'mc_dd_p99': mc.quantile(dds, 99),
        'p_dd_gt_20': 100.0 * sum(1 for d in dds if d > 20.0) / len(dds),
        'p_dd_gt_25': 100.0 * sum(1 for d in dds if d > 25.0) / len(dds),
        'p_dd_gt_30': 100.0 * sum(1 for d in dds if d > 30.0) / len(dds),
    }

if __name__ == '__main__':
    print(f"Running 500-path Monte Carlo across 4 configurations (12 workers)...")
    summary = []
    for lbl, cand in configs:
        t0 = time.time()
        res = run_config_mc(lbl, cand)
        print(f"Done {lbl} in {time.time()-t0:.1f}s")
        summary.append(res)
        
    out_file = "sandbox/scratch/mc_compare_summary.json"
    with open(out_file, 'w') as f:
        json.dump(summary, f, indent=2)
    print("ALL DONE")
