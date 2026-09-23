"""Why a fold's train window rejects every cell. Diagnostic, not a selector."""
from dataclasses import replace

from sandbox import data
from sandbox import metrics
from sandbox import search
from sandbox import strategies
from sandbox import walkforward as wf

strategy = strategies.get("Hourly Delta Reversal (tied)")
ex = replace(strategy.execution, initial=wf.INITIAL)
bars = data.load_bars(strategy.bars, strategy.symbol)
context = strategy.context()
cells = wf.cell_fills(strategy, bars, context, ex, progress=False)

for index, (_, train_hi, _test_lo, _test_hi) in enumerate(wf.fold_windows(), 1):
    stats = {k: wf.window_stats(f, ex, None, train_hi, wf.INITIAL)[0]
             for k, f in cells.items()}
    floor = wf.MIN_TRADES_PER_MONTH * wf.months_between(None, train_hi)
    counts = {"trades": 0, "pnl": 0, "streak": 0, "top_share": 0, "plateau_width": 0}
    survivors = []
    for key, stat in stats.items():
        if stat["trades"] < floor:
            counts["trades"] += 1
            continue
        if stat["pnl"] <= 0:
            counts["pnl"] += 1
            continue
        if stat["max_loss_streak"] > wf.MAX_LOSS_STREAK:
            counts["streak"] += 1
            continue
        if stat["top_month_share"] > wf.MAX_TOP_MONTH_SHARE:
            counts["top_share"] += 1
            continue
        near = [n for n in search.neighbours(strategy, key) if n in stats]
        if not near or any(stats[n]["pnl"] <= 0 for n in near):
            counts["plateau_width"] += 1
            continue
        survivors.append((key, stat))
    print(f"fold {index} train .. {metrics.month_key(train_hi)} "
          f"(floor {floor:.0f} trades): rejected {counts}, survivors {len(survivors)}")
    best = sorted(stats.items(), key=lambda kv: kv[1]["msharpe"], reverse=True)[:5]
    for key, stat in best:
        near = [n for n in search.neighbours(strategy, key) if n in stats]
        losers = sum(1 for n in near if stats[n]["pnl"] <= 0)
        print(f"    {dict(key)}  pnl {stat['pnl']:>7.1f} tr {stat['trades']:>3} "
              f"mSh {stat['msharpe']:>5.2f} streak {stat['max_loss_streak']} "
              f"top {stat['top_month_share']:.2f} losing neighbours {losers}/{len(near)}")
