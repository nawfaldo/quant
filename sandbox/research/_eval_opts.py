from sandbox.research import _p99_lab as lab
from sandbox.research import exness_combined_strategies as ecs

def main():
    base = list(ecs.BOOK)
    base_no_k = [s for s in base if s != "usdjpy:kendall"]

    # Opt 1: Combo C replacing kendall with btc:jump (23 sleeves)
    opt1 = base_no_k + ["eurjpy:lux_ny_vwap_pullback@15m", "uk100:xma_cross", "usoil:xma_cross", "btc:jump"]

    # Opt 2: Combo C keeping kendall + btc:jump (24 sleeves)
    opt2 = base + ["eurjpy:lux_ny_vwap_pullback@15m", "uk100:xma_cross", "usoil:xma_cross", "btc:jump"]

    configs = [(opt1, 0.13, None), (opt2, 0.13, None)]
    r26 = lab.evaluate("2526", configs, paths=300, realised=True)
    r24 = lab.evaluate("2224", configs, paths=300, realised=True)
    rfull = lab.evaluate("2226", configs, paths=300, realised=True)

    names = [
        "Opt 1: Combo C (23 sleeves - kendall replaced by btc:jump)",
        "Opt 2: Combo C (24 sleeves - keeping kendall + btc:jump)"
    ]
    for n, d26, d24, df in zip(names, r26, r24, rfull):
        print(f"\n{n}:")
        print(f"  25-26: med {d26['med_ret']:>+5.0f}% | real {d26['real_ret']:>+6.1f}% | p99 {d26['dd99']:>5.2f}% | rDD {d26['real_dd']:>5.2f}%")
        print(f"  22-24: med {d24['med_ret']:>+5.0f}% | real {d24['real_ret']:>+6.1f}% | p99 {d24['dd99']:>5.2f}% | rDD {d24['real_dd']:>5.2f}%")
        print(f"  22-26: med {df['med_ret']:>+6.0f}% | real {df['real_ret']:>+6.1f}% | p99 {df['dd99']:>5.2f}% | rDD {df['real_dd']:>5.2f}%")

if __name__ == "__main__":
    main()
