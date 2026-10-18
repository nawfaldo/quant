# Trading Mythbusters (YouTube @TradingMythbusters) — strategy notes (read 2026-10-03)

16 videos (2025-26). Each one codes a popular idea and backtests it on 100
Binance futures, 100 US stocks, 30 CME futures and 50 FX pairs, 1m to 1d.
Rules read from the video descriptions (written out in full). Module
`tt_tradingmythbusters.py`, group `tradingmythbusters`, 8 families (`tmb_`).

| family | video | rule | his result |
|---|---|---|---|
| tmb_elliott_24_break | BxnioVonZFo | 5-wave impulse on pivots, then a close through the wave 2-4 line -> fade the impulse; stop at wave 5; TP 61.8% of 0->5; no entry if price ran too far from wave 5 | "myth or edge" |
| tmb_doji_confirm | 9GVt-psZlEc | doji (small body / range) then two candles the same way -> enter that way at next open; fixed stop | raw: no edge; HTF better; optimised crypto Sharpe > 1 |
| tmb_london_body_break | K18iaIlkA_w (Jooviers Gems) | London range; first candle whose body closes fully outside -> breakout; stop at candle's other end; 2R; one a day; no late entries | "FAILS in real data" |
| tmb_supertrend_dema | qf2moN0UhlI | SuperTrend flips and close on the right side of DEMA -> enter; exit on flip back or Fibonacci Bollinger band (1.618) | 10M candles tested |
| tmb_adx_dmi_obv | f30GA8PJFjk | ADX strong + DI cross + OBV above its average -> enter; exit when DI or OBV turns | — |
| tmb_vwap_reject | 59W3wlo33Is | downtrend, high tags VWAP from below and closes back under, bearish -> short (mirror long) | — |
| tmb_ha_stoch_doji | q_dOVESpYLI | Heikin Ashi doji with Stochastic oversold/overbought, next HA candle turns -> reversal | — |
| tmb_rsi_bb_mid | j2ESnjhT2no | RSI oversold and close below lower BB -> long, exit at BB midline (mirror short) | — |

## Not built (already covered by other creators' families)

MA crossover (dfNiF6fexxs), MACD (5SrMagIkGvE), RSI divergence (2XZveitb41w),
diagonal trendline break (IRMIUqIZjHs), head and shoulders (X6lTDdxbJuI),
order blocks (AXNcZSjJXQY), Fibonacci 61.8% (c9uu1J8J1hw). The OpenClaw
video (wbgKhRhoLhc) is an AI-agent workflow, not a rule.

Parameters he leaves unstated (doji ratio, ADX level, OBV average, stops) are
grid axes. The London family counts its cutoff from the first bar after the
window, so cash-session markets (bars from 09:30) still trade it.
