import json

with open("sandbox/results/combo_2026_sleeve_breakdown.json", "r") as f:
    data = json.load(f)

lines = []
def p(s=""):
    lines.append(s)

for book_name in ["Combo A (22 sleeves)", "Combo C (23 sleeves)"]:
    p("# " + book_name + " - 2026 Sleeve Contributions\n")
    cold = data[book_name]["cold_2026"]
    comp = data[book_name]["compounded_from_2025"]
    
    p(f"**Cold 2026 ($500 start)**: Final ${cold['eq_close_26']:,.2f} | Return +{cold['pct_26']:.2f}% | PnL +${cold['total_pnl_26']:,.2f} | MTM DD {cold['mtm_dd_pct']:.2f}%")
    p(f"**Compounded 2026 (from 2025)**: Open ${comp['eq_open_26']:,.2f} -> Final ${comp['eq_close_26']:,.2f} | 2026 Gain +{comp['pct_26']:.2f}% | 2026 PnL +${comp['total_pnl_26']:,.2f}\n")

    p("### 1. Per-Sleeve 2026 Contribution\n")
    p("| Sleeve | Status | Cold PnL $ | Cold PnL % | Trades | Win Rate | Profit Factor | Compounded 2026 PnL $ | Comp PnL % |")
    p("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
    
    sorted_sleeves = sorted(cold["sleeve_stats"].keys(), key=lambda s: -cold["sleeve_stats"][s]["pnl"])
    for s in sorted_sleeves:
        c_stat = cold["sleeve_stats"][s]
        comp_stat = comp["sleeve_stats"][s]
        is_add = "**NEW ADD**" if s in ["eurjpy:lux_ny_vwap_pullback@15m", "usoil:xma_cross", "uk100:xma_cross"] else "Base"
        p(f"| `{s}` | {is_add} | **+${c_stat['pnl']:,.2f}** | {c_stat['share_of_pnl_pct']:.1f}% | {c_stat['trades']} | {c_stat['win_rate']:.1f}% | {c_stat['profit_factor']:.2f} | +${comp_stat['pnl']:,.2f} | {comp_stat['share_of_pnl_pct']:.1f}% |")

    p("\n### 2. Monthly Breakdown 2026\n")
    adds = [s for s in sorted_sleeves if s in ["eurjpy:lux_ny_vwap_pullback@15m", "usoil:xma_cross", "uk100:xma_cross"]]
    header = "| Month | Open $ | Close $ | Book Return % | Book PnL $ | Trades |"
    for a in adds:
        header += f" `{a}` PnL |"
    p(header)
    sep = "| :--- | :---: | :---: | :---: | :---: | :---: |" + " :---: |" * len(adds)
    p(sep)
    
    months = sorted(cold["monthly"].keys())
    for m in months:
        m_data = cold["monthly"][m]
        row = f"| **{m}** | ${m_data['open']:,.1f} | ${m_data['close']:,.1f} | **{m_data['pct']:+6.2f}%** | +${m_data['pnl']:,.2f} | {m_data['trades']} |"
        for a in adds:
            row += f" {m_data['by_sleeve'].get(a, 0.0):+,.2f} |"
        p(row)

    p("\n### 3. Weekly Breakdown 2026\n")
    header_w = "| Week Start | Open $ | Close $ | Week % | PnL $ | Trades |"
    for a in adds:
        header_w += f" `{a}` PnL |"
    p(header_w)
    sep_w = "| :--- | :---: | :---: | :---: | :---: | :---: |" + " :---: |" * len(adds)
    p(sep_w)

    for w in cold["weekly"]:
        row_w = f"| {w['week_start']} | ${w['open']:,.1f} | ${w['close']:,.1f} | **{w['pct']:+6.2f}%** | {w['pnl']:+,.2f} | {w['trades']} |"
        for a in adds:
            row_w += f" {w['by_sleeve'].get(a, 0.0):+,.2f} |"
        p(row_w)
    p("\n---\n")

with open("sandbox/results/breakdown_report.md", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("Wrote breakdown_report.md successfully")
