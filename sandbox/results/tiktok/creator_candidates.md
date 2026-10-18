# Strategy-creator candidates (scoured 2026-10-03)

How: 26 TikTok video searches (backtest / quant / algo / pine script / ORB / RSI /
MACD / supertrend / ICT / seasonality / ML ...) + 3 user searches -> 1,057 accounts,
ranked by how many searches they hit and how many captions mention
backtest/win rate/rules; top 65 profiles opened for followers + bio.
TikTok search is localized to Indonesia for this login, so results skew Indonesian.
Followers as shown on 2026-10-03. Already extracted: luxalgo, quantpad,
thequantbuilder, tradingdata2.

## Extraction progress (updated 2026-10-03)

Per account: notes `<handle>_strategy_notes.md` here, module
`sandbox/research/tt_<handle>.py`, group `<handle>` in `cfd_tt_families`.
Checked with `py -m sandbox.research._tt_verify --group <handle>`. No sweeps run.

| account | status | posts read | families |
|---|---|---|---|
| @fluxchart | done | 690 on grid; 44 + 24 read (rest screened by caption) | 24 (`fx_`) |
| @quantcrawler | done | 400 on grid; 26 + 13 read (rest screened: AI/promo) | 3 (`qc_`) |
| @deltatrendtrading | done | 419 on grid; 29 + 11 read (rest: commentary/options) | 4 (`dt_`) |
| @jasonjets | done | 75 on grid; 26 + 6 read | 2 (`jj_`) |
| @jsfinancials | done | 496 on grid; 32 + 8 read | 0 (XAU cross-asset state model needs DXY/US10Y/VIX) |
| @tst_life | done | 192 on grid; 31 + 6 read (rest: quizzes/promos) | 5 (`tst_`) |
| @artybryjatma | done | 152 of 152 | 10 (`ma_`) |
| YouTube Trading Rush | done | ~100 test videos | 31 (`tr_`) |
| YouTube neurotrader | done | 23 | 7 (`nt_`) |
| Substack Quantitativo | done | ~50 posts | 6 (`qt_`) |
| Substack setup4alpha | done (free text only) | ~80 posts | 3 (`s4a_`) |
| YouTube Algovibes | done | 78 of 331 (rule videos) | 14 (`av_`) |
| YouTube Part Time Larry | done | 14 of 168 | 6 (`ptl_`) |
| YouTube tomtrades | done | 20 of 59 | 3 (`tom_`) |
| YouTube BionicNQ | done | 12 of 82 | 2 (`bnq_`) |
| YouTube Okala 8020 | done | 1 video + 10 shorts | 1 (`o80_`) |
| YouTube TradingMythbusters | done | 16 of 16 (descriptions) | 8 (`tmb_`) |
| @blissdgl_ (Jooviers Gems) | done | 74 on grid; 18 read | 2 (`jg_`) |
| @quanttactic | done | 44 on grid; 32 read | 16 (`qtt_`) |
| @mulhamtrading | done | 193 on grid; 10 read | 1 (`mul_`) |
| @moneyballaustin | done (mostly SKIP) | 278 on grid; 3 read | 1 (`mab_`) |
| @nomanskii | done | 157 on grid; 11 read | 7 (`nmk_`) |
| @tradewithpat | done | 278 on grid; 12 read | 2 (`twp_`) |
| @thecrypteller | done | 138 on grid; 10 read | 2 (`ctel_`) |
| @chartfanatics | done | 521 on grid; 10 read | 4 (`cf_`) |
| @danielholmestrades | done | 70 on grid; 8 read | 2 (`dh_`) |
| @cyforex2 | SKIP (screened) | 284 on grid, 0 read | 0 -- multi-part ICT/SMC lessons; ORB/CRT/FVG/OB already built |
| @piplabtrading | done | 288 on grid; 5 read | 1 (`plab_`) |
| @safucrypto | done | 208 on grid (mostly geopolitics, paid scripts); 6 read | 1 (`safu_`) |
| @yo.forex3 | done | 250 on grid (indicator clips, promos); 7 read | 5 (`yf_`, public indicators) |
| @algotradepro0 | done | 37 on grid; 15 read | 4 (`atp_`) |
| @rsipro.360 | done | 17 on grid; 17 read | 11 (`rp_`) |
| @stratifybacktesting | SKIP (screened) | 11 on grid; 4 read | 0 -- app promo; CCI(20) +-100 reversion and MACD 12/26/9 cross are generic, already covered |
| @tradingwithmustafa | done | 294 on grid (promos); 8 read | 1 (`mus_`) |
| @novo_vox | done | 123 on grid (CRT/ICT, promos); 5 read | 1 (`novo_`) |
| @itsjjsimon | done (via YouTube) | TikTok grid throttled; YouTube 30 titles, 1 strategy video read | 1 (`jjs_`) |
| @wintertradesnq | SKIP (screened) | 22 on grid, 0 read | 0 -- trade callouts / POV clips, no rules |
| @pyquantlab | done | 212 on grid; 8 rules (posts + pyquantlab.com articles) | 8 (`pql_`) |
| @marlon_quantlab | done | 41 on grid; 10 read (mostly negative tests) | 2 (`mql_`) |
| @kreativekodr | done | 120 on grid (options bots, AI coding); 6 read | 1 (`kk_`) |
| @renko_rutomation_lab | SKIP (screened) | 8 on grid; 3 read | 0 -- music clips + course link, no rules |
| @tradingmythbusters (TikTok) | covered | 173 on grid (teasers of the YouTube tests + pattern explainers) | 0 new -- YouTube extraction already holds the 8 `tmb_` families |
| @moonthetrain | SKIP (screened) | 2 on grid | 0 -- SuperTrend after costs (covered) |
| @algovex | screened | 18 on grid (app promo); 4 read | 0 new -- marubozu no edge (PF 0.88); re-test of mql_open_candle_ema PF 0.79 EURUSD / 1.13 S&P; overnight-drift (close->open) not testable with session flatten |
| @bot2profit | screened | 30 on grid (rule-search bot diary + viral-strategy tests); 4 read | 0 new -- its tests: 5m/EMA12 -0.22R over 19 markets, ORB -$3,388, FVG eaten by fees, oversold RSI loses to random; only survivor is a 2007 monthly timing rule (27 trades / 32 y) |
| @tradieapp | SKIP (screened) | 11 on grid | 0 -- AI strategy-builder app promo, proprietary indicators |
| @algofib (FibAlgo) | SKIP (screened) | 250 on grid | 0 -- closed-source indicator promos (Perfect Entry/Retracement Zones), no rules |
| @mr.algo.trading | screened | 134 on grid (Thai basics); 2 read | 0 new -- his day-trade = 15m opening-candle body break + 1m breaker retest, trail (ORB retest families cover it) |
| @edgealgo.arabic | SKIP (screened) | 286 on grid (Arabic) | 0 -- promotes its own closed indicator; SMC talk is generic |
| @trade.with.coach.bri | screened | 275 on grid (discipline talk, Trend Fusion indicator promo); 3 read | 0 new -- 15m ORB, 3m close outside, retest, target next S/D zone (ORB retest families cover it) |
| @jadetantrade | done | 284 on grid (Altrade promos); 3 read | 1 (`jt_`) |
| @hendy.tan | SKIP (screened) | 263 on grid (crypto/wallet education) | 0 -- indicator posts are generic beginner rules (Supertrend, RSI 30/70, RSI+MA, MA cross), already covered |
| @edwinardyanto | SKIP (screened) | 201 on grid (Pine tutorials, TA basics, yapping) | 0 -- his indicator-test series covers SMC, Ichimoku, Hull Suite, CRT, UT Bot: all already coded |
| @tradermisterius | screened | 52 on grid (manual 100x backtests); 6 read | 0 -- entry rules and results only on screen, narration names indicators (DEMA+Supertrend+QQE, HARSI+SSL+MFI, MACD 8/13/3 + session, Supertrend-MA+SSL hybrid); overlaps existing families |
| @tayyong_99 | SKIP (screened) | 209 on grid (SMC lessons, motivation); 3 read | 0 -- the "100x Stoch/RSI/MACD" series reposts Forex Sarjana (= rp_stoch_rsi_macd) |
| @matfinaps | SKIP (screened) | 227 on grid | 0 -- prop-firm statistics talk, no rules |
| @quantguild | SKIP (screened) | 244 on grid | 0 -- YouTube cuts (TJR critique, Kalman MR theory), no rules |
| @julias.algos | SKIP (screened) | 156 on grid; 2 read | 0 -- finance explainers; pairs trading described as obsolete |
| @sharpresearch | SKIP (screened) | 281 on grid | 0 -- backtesting talk; MA crosses / VWAP / S&R tested negative; FRED macro models (daily) |
| @quantphil | SKIP (screened) | 76 on grid | 0 -- sells a proprietary ML model (ApolloAlpha) |
| @falaktb | SKIP (screened) | 24 on grid | 0 -- quant concept explainers (PCA, Kalman, L3 data) |
| @michael.trades1 | SKIP (screened) | 197 on grid | 0 -- animated candlestick-pattern clips, no rules |
| @macmoney82 | done | 37 on grid; 3 read | 1 (`mac_`) |
| @sergey_algotradin | SKIP (screened) | 26 on grid | 0 -- MT5 / EA tutorials |
| @mian.algofx | SKIP (screened) | 153 on grid | 0 -- repeated closed-indicator gold clips |
| @moondevonyt | SKIP (screened) | 246 on grid | 0 -- AI-agent hype; edges use liquidation data we do not hold |
| @_cgtrades_ | SKIP (screened) | 307 on grid | 0 -- motivation vlog |
| @tradex_labs | SKIP (screened) | 261 on grid; 7 read | 0 -- sells 6 ORB variants + PIVOTS bundle, parameters paywalled; plain 15m ORB already covered |
| all TikTok accounts (A, debunk, vendors, Indonesian, B) | done 2026-10-04 | see rows above | - |
| YouTube CodeTrading | done | 34 of 350 (60 screened) | 15 (`ct_`) |
| YouTube Kevin Davey | screened LOW, not started | - | - |
| YouTube The Trading Channel, Quant Guild | screened SKIP | - | - |
| quantifiedstrategies.com (@QuantifiedStrat) | done | 314 articles scanned, 28 read | 12 (`qs_`) |
| X @quantscience_ | SKIP (screened 2026-10-04) | tool / library / PDF promos (Nautilus examples, GitHub repos) | 0 |
| X @_QuantTrader | SKIP (screened 2026-10-04) | inactive since 2020, trading quotes and links | 0 |
| automatedtradingstrategies substack | SKIP (screened 2026-10-04) | 23 recent posts, 20 paid-only; free ones are forward-test overviews with no rules | 0 |

## Screen before extracting (added 2026-10-03)

Every account gets a cheap GO/SKIP check first: titles only + 2-3 sampled
video descriptions/posts. GO only when rules (entry, stop, exit) are stated
for free. SKIP: rules behind a course/paywall/Discord, clips or recaps only,
psychology/prop-firm talk, discretionary ICT/price-action narration.

| account | verdict | why |
|---|---|---|
| YouTube TradingMythbusters | GO | every video tests a named strategy as an explicit algorithm (Elliott 5-wave, trendline break, SuperTrend+DEMA, H&S, Fib 61.8, London breakout...); rules written in the descriptions |
| YouTube CodeTrading | GO (strategy videos only) | descriptions list exact rules (HMA16/65 + RSI>52 + daily linreg; pin bar + EMA50/200, ATR stop, 2R, 50-bar exit); skip the numbered course lessons and AI/RL-bot videos |
| YouTube Kevin Davey (Algo Trading With Kevin Davey) | LOW | free part is "I tested RSI / golden cross / Keltner on 220 markets" (classic rules, mostly fails); his own strategies sit behind a sign-up page |
| YouTube The Trading Channel | SKIP | discretionary price action, every video funnels to a paid course |
| YouTube Quant Guild | SKIP | quant theory and guru reviews, no trading rules |
| quantifiedstrategies.com | GO (single-strategy articles only) | Chrome passes the bot check; single-strategy articles give full rules free, sometimes code (RSI 30>50 + RSI2<15, exit RSI2>85; turnaround Tuesday; IBS; ORB). Round-up pages say "rules at the bottom" and hide them from non-members: skip those. Mostly daily SPY/QQQ close-to-close, overlaps Quantitativo/setup4alpha |
| setup4alpha, BionicNQ, Okala 8020 | (would be SKIP) | already done before this screen: paywalled / ICT recaps / clips |

YouTube captions: fetched by loading each video in the player and turning
captions on (plain caption downloads now need a token). Helper code is kept
in the browser's localStorage (`yt_helpers`) on youtube.com.

(2026-10-04: all six zoom-collected accounts re-scrolled at normal zoom and
their full grids screened by caption; the counts above are the full grids.)

Resume order: ALL TikTok tiers DONE 2026-10-04 (A, debunk, vendors, Indonesian, B). Since 2026-10-04
background page fetches hit TikTok's firewall; load each post as a real page
in the tab (one every ~7 s) and read its embedded data.

## TikTok - A: posts testable rules / backtests (closest to quantpad / luxalgo)

| account | followers | what they post |
|---|---|---|
| @fluxchart | 93.7K | indicator vendor like LuxAlgo: ORB, "AI strategy (backtested)", 2-indicator strategies |
| @quantcrawler | 52.2K | ORB stats on ES/NQ ("1,178,668 ORB trades"), TradingView strategies |
| @deltatrendtrading | 119.3K | ML version of ORB, "minimum viable" edge tests |
| @jasonjets | 31.4K | quant tests: vol arb, TSMOM, with results |
| @jsfinancials | 12.1K | published quant researcher; backtest method, fixable strategies |
| @tst_life (Trading Strategy Testing) | 114.1K | MACD/indicator strategies tested |
| @artybryjatma | 122.4K | "strategy backtested 100 times" (Fibonacci scalping etc.) |
| @blissdgl_ | 250.5K | scalping strategies "with backtested results" |
| @quanttactic | 11.1K | RSI 6-year backtest, mean reversion backtest |
| @mulhamtrading | 30.5K | ICT liquidity / silver bullet with backtests and win rates |
| @moneyballaustin | 14.5K | ICT/SMC backtested 100 trades |
| @nomanskii | 17.8K | FVG on QQQ, 30-min ORB + 20 EMA on SPY backtests |
| @tradewithpat | 83.7K | "backtested this strategy for 15 years" |
| @thecrypteller | 125.4K | ORB daily setup, ICT setup |
| @chartfanatics | 135.3K | ORB, mean reversion strategies from guest traders |
| @danielholmestrades | 209.2K | simple breakout strategy |
| @cyforex2 | 127.9K | ORB rules (multi-part) |
| @piplabtrading | 5.2K | ORB |
| @safucrypto | 31.2K | automated ORB with ATR, indicator settings |
| @yo.forex3 | 31.5K | tests TradingView indicators (ChartPrime trendline breakouts, zero-lag MA) |
| @algotradepro0 | 2.0K | indicator-vs-indicator backtests (zero-lag MACD vs MACD) |
| @rsipro.360 | 10K | MACD / RSI + Supertrend combos |
| @stratifybacktesting | 11.6K | backtesting app; MACD momentum strategy |
| @tradingwithmustafa | 15.4K | overnight-range rules |
| @novo_vox | 37.5K | candle range theory "backtested 10,000 times" |
| @itsjjsimon | 18.5K | continuation vs mean-reversion model comparison |
| @wintertradesnq | 3.1K | NQ quantitative data |
| @tradex_labs | 9.5K | trend / mean reversion / momentum systems |
| @pyquantlab | 981 | Python strategies (KNN mean reversion on ETH, adaptive vol) |
| @marlon_quantlab | 726 | quant research; tested ICT silver bullet |
| @kreativekodr | 124 | QQQ reversal strategy backtest |
| @renko_rutomation_lab | 174 | Renko bot backtests |

### Debunk channels (like quantpad: they test other people's strategies)

| account | followers | note |
|---|---|---|
| @tradingmythbusters | 71 | tests popular strategies (also on YouTube) |
| @moonthetrain | 1 | popular strategies on real tick data (SuperTrend after costs) |
| @algovex | 98 | backtests strategies from reels/TikToks |
| @bot2profit | 148 | "testing viral strategies with my bot" (part 14+) |
| @tradieapp | 10 | RSI+MACD on SPY, 5 years |

### Indicator vendors (like luxalgo)

| account | followers | note |
|---|---|---|
| @algofib | 127.4K | FibAlgo AI indicators |
| @mr.algo.trading | 76.3K | Thai, free indicators |
| @edgealgo.arabic | 38K | Edge Algo indicator (Arabic) |
| @trade.with.coach.bri | 6.4K | Trendfusion algo, "85% backtested" |

### Indonesian (rules in Bahasa)

| account | followers | note |
|---|---|---|
| @jadetantrade | 49.2K | TradingView strategies (Hull Suite), with/without commission |
| @hendy.tan | 18.7K | RSI strategy / Supertrend strategy indicators |
| @edwinardyanto | 9.6K | algo trader, free Pine scripts |
| @tradermisterius | 3.8K | "Tukang backtesting": backtests indicators on forex/crypto/stocks |
| @tayyong_99 | 970 | backtest 100x Stochastic/RSI/MACD |

## TikTok - B: quant talk, few mechanical rules (low yield)

@matfinaps 4.7K (hedge fund PM), @quantguild 3.7K, @julias.algos 33K (pairs trading,
paper-based strategy), @sharpresearch 30.2K (econ data), @quantphil 1K, @falaktb 4.6K,
@michael.trades1 534K (Graph Insight pattern explainers), @macmoney82 91,
@sergey_algotradin 4.4K (MT5 EAs), @mian.algofx 1.4K, @moondevonyt 10.1K, @_cgtrades_ 624.

## Other platforms

YouTube: Trading Rush (352K, "tested 100 times"), neurotrader (permutation tests,
Donchian), Algovibes (codes viral strategies in Python), Part Time Larry, tomtrades
(91.9K), BionicNQ (18K), Okala 8020 (8.8K, NQ), CodeTrading, Kevin Davey,
The Trading Channel, Trading Strategy Testing (= @tst_life), Quant Guild,
TradingMythbusters.
X / Substack: @QuantifiedStrat (daily rule-based backtests, 200+ free strategies),
@quantscience_, @_QuantTrader (NQ/ES), Quantitativo (ES/NQ intraday momentum),
setup4alpha (Connors rules + RealTest code), automatedtradingstrategies.substack,
Concretum/Zarattini (already used: sandbox/research/zarattini_*).
