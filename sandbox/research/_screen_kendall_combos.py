"""Screen replacements for usdjpy:kendall specifically inside Base, Combo A, and Combo C."""
import json
import os
from itertools import combinations
from sandbox.research import _p99_lab as lab
from sandbox.research import exness_combined_strategies as ecs

CANDIDATES_POOL = [
    'gbpusd:td_ema_cross_level@30m', 'audjpy:regime_breakout', 'eurjpy:lux_key_levels_orb@120m',
    'usdjpy:roofing', 'de40:luxalgo_manipulation@120m', 'audusd:lux_sweep_ifvg@60m',
    'uk100:xma_cross', 'usoil:xma_cross', 'eurjpy:lux_ny_vwap_pullback@15m',
    'usdjpy:qp_ma_cross@60m', 'usdjpy:qp_volume_breakout@30m', 'us500:lux_rsi_50_pullback@60m',
    'btc:jump', 'ethusd:lux_body_momentum@15m', 'eurjpy:vwap', 'gbpjpy:td_ut_bot@30m'
]

def main():
    lab._arm()
    base = list(ecs.BOOK)
    base_no_k = [s for s in base if s != "usdjpy:kendall"]

    combo_a_no_k = [s for s in base + ["eurjpy:lux_ny_vwap_pullback@15m", "usoil:xma_cross"] if s != "usdjpy:kendall"]
    combo_c_no_k = [s for s in base + ["eurjpy:lux_ny_vwap_pullback@15m", "uk100:xma_cross", "usoil:xma_cross"] if s != "usdjpy:kendall"]

    # Test single additions to Combo A without kendall
    # and single additions to Combo C without kendall
    # and pair replacements for Base without kendall
    test_configs = []
    labels = []

    # 1. Base benchmarks
    test_configs.append((base, 0.13, None))
    labels.append("Canon Base (20)")
    test_configs.append((base_no_k, 0.13, None))
    labels.append("Base - kendall (19)")

    # 2. Combo A benchmarks
    test_configs.append((base + ["eurjpy:lux_ny_vwap_pullback@15m", "usoil:xma_cross"], 0.13, None))
    labels.append("Combo A (22)")
    test_configs.append((combo_a_no_k, 0.13, None))
    labels.append("Combo A - kendall (21)")

    # 3. Combo C benchmarks
    test_configs.append((base + ["eurjpy:lux_ny_vwap_pullback@15m", "uk100:xma_cross", "usoil:xma_cross"], 0.13, None))
    labels.append("Combo C (23)")
    test_configs.append((combo_c_no_k, 0.13, None))
    labels.append("Combo C - kendall (22)")

    # Candidates for Combo A (excluding what's already in Combo A)
    pool_for_a = [c for c in CANDIDATES_POOL if c not in combo_a_no_k]
    for c in pool_for_a:
        test_configs.append((combo_a_no_k + [c], 0.13, None))
        labels.append(f"Combo A - kendall + {c}")

    # Candidates for Combo C (excluding what's already in Combo C)
    pool_for_c = [c for c in CANDIDATES_POOL if c not in combo_c_no_k]
    for c in pool_for_c:
        test_configs.append((combo_c_no_k + [c], 0.13, None))
        labels.append(f"Combo C - kendall + {c}")

    # Best pairs for Base without kendall
    # Pairs from [usoil, eurjpy_vwap, uk100, audjpy, gbpusd, usdjpy_roofing, de40_lux]
    base_pair_pool = ['usoil:xma_cross', 'eurjpy:lux_ny_vwap_pullback@15m', 'uk100:xma_cross', 'audjpy:regime_breakout', 'gbpusd:td_ema_cross_level@30m', 'usdjpy:roofing', 'de40:luxalgo_manipulation@120m']
    for p in combinations(base_pair_pool, 2):
        test_configs.append((base_no_k + list(p), 0.13, None))
        labels.append(f"Base - kendall + {' + '.join(p)}")

    print(f"Evaluating {len(test_configs)} configurations on 300 paths across 2526, 2224, 2226...", flush=True)
    r26 = lab.evaluate("2526", test_configs, paths=300, realised=True)
    r24 = lab.evaluate("2224", test_configs, paths=300, realised=True)
    rfull = lab.evaluate("2226", test_configs, paths=300, realised=True)

    rows = []
    for lbl, cfg, d26, d24, df in zip(labels, test_configs, r26, r24, rfull):
        rows.append({
            "label": lbl,
            "sleeves": len(cfg[0]),
            "med26": d26["med_ret"], "real26": d26["real_ret"], "dd99_26": d26["dd99"], "rdd26": d26["real_dd"],
            "med24": d24["med_ret"], "real24": d24["real_ret"], "dd99_24": d24["dd99"], "rdd24": d24["real_dd"],
            "medfull": df["med_ret"], "realfull": df["real_ret"], "dd99_full": df["dd99"], "rddfull": df["real_dd"]
        })

    with open("sandbox/results/screen_kendall_combos.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)

    print("\n--- RESULTS SAVED ---")
    for r in rows:
        print(f"{r['label']:<48} | 25-26: med {r['med26']:>+5.0f}% p99 {r['dd99_26']:>5.2f}% | 22-24: med {r['med24']:>+5.0f}% p99 {r['dd99_24']:>5.2f}% | 22-26: med {r['medfull']:>+6.0f}% p99 {r['dd99_full']:>5.2f}%")

if __name__ == "__main__":
    main()
