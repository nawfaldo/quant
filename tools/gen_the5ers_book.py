"""Regenerates the The5ers book's Rust (the5ers/nov-08-2026) from
`sandbox/results/the5ers_live_book.json` and its parity fixtures
(`sandbox/research/_the5ers_parity.py`). Copy of the FundedNext generator."""
import glob
import json
import os
import re

ROOT = r"C:\Users\JawirGaming66\quant"
CFD = os.path.join(ROOT, "live_trade/src/strategies/cfd")
BOOKDIR = os.path.join(ROOT, "live_trade/src/strategies/the5ers/nov-08-2026")
FIX = os.path.join(BOOKDIR, "fixtures")
seal = json.load(open(os.path.join(ROOT, "sandbox/results/the5ers_live_book.json")))
members = seal["members"]


def num(x):
    return repr(float(x))


def b(v):
    return "true" if v else "false"


WORDS = {"qtt": "QTT", "tmb": "TMB", "ha": "HA", "nt": "NT", "cf": "CF", "qp": "QP", "tr": "TR",
         "dt": "DT", "rsi": "RSI", "av": "AV", "htf": "HTF", "xma": "XMA", "ema": "EMA", "ma": "MA",
         "obv": "OBV", "cci": "CCI", "rvol": "RVOL", "macd": "MACD", "ptl": "PTL", "orb": "ORB",
         "bnq": "BNQ", "ifvg": "IFVG", "twp": "TWP", "ny": "NY", "rvi": "RVI", "pdr": "PDR"}


def camel(s):
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[-_@]", s))


def exit_of(mode):
    for pre, fmt in (("rr_", "Exit::RewardMultiple({})"), ("trail_", "Exit::Trail({})")):
        if mode.startswith(pre):
            return fmt.format(num(mode[len(pre):]))
    if mode.startswith("time_"):
        return f"Exit::Bars({int(mode[5:])})"
    if mode.startswith("days_"):
        return f"Exit::Days({int(mode[5:])})"
    return {"signal": "Exit::Signal", "level": "Exit::Level"}[mode]


def atr(stop):
    assert stop.startswith("atr_"), stop
    return num(stop[4:])


def gate_of(g, k):
    """`(Gate, gate_period)`: the slow RSI is `p["rsi"][1]`, OBV's channel `p["obv"][0]`."""
    return {"rsi": ("Gate::Rsi", max(3, 14 * k)), "vwap": ("Gate::Vwap", 0),
            "supertrend": ("Gate::Supertrend", 0), "obv": ("Gate::Obv", 10 * k),
            "macd": ("Gate::Macd", k), "rvol": ("Gate::Rvol", 0),
            "adx": ("Gate::Adx", max(6, 14 * k))}[g]


def pol(p):
    return {"confirm": "Polarity::Confirm", "oppose": "Polarity::Oppose"}[p]


def family_of(fam, p, fx, k):
    if fam == "confluence" and p["pool"] == "trend":
        return (f"Family::ConfluenceTrend {{ votes: {p['votes']}, rsi_period: {max(3, 14 * k)}, "
                f"macd: ({12 * k}, {26 * k}, {9 * k}), obv_channel: {10 * k}, span: {5 * k} }}")
    if fam == "confluence":
        assert p["pool"] == "exhaustion"
        return (f"Family::Confluence {{ votes: {p['votes']}, zscore_period: {2 * k}, "
                f"rsi_period: {max(2, 2 * k)} }}")
    if fam == "gated_swing":
        gate, gp = gate_of(p["gate"], k)
        donchian = f"Some({20 * k})" if p["trigger"] == "donchian" else "None"
        fast, slow = (0, 0) if p["trigger"] == "donchian" else (k, 10 * k)
        return (f"Family::GatedSwing {{ donchian: {donchian}, fast: {fast}, slow: {slow}, gate: {gate}, "
                f"polarity: {pol(p['polarity'])}, gate_period: {gp} }}")
    if fam == "rvol":
        return f"Family::Rvol {{ rvol: {num(p['rvol'])}, threshold_atr: {num(p['threshold_atr'])} }}"
    if fam == "key_reversal":
        return f"Family::KeyReversal {{ channel: {p['channel']} }}"
    if fam == "structure":
        return (f"Family::Structure {{ wing: {p['wing']}, "
                f"continuation: {b(p['mode'] == 'continuation')} }}")
    if fam == "swing_break":
        return f"Family::SwingBreak {{ wing: {p['wing']} }}"
    if fam == "ptl_orb_candle":
        return f"Family::PtlOrbCandle {{ or_min: {p['or_min']} }}"
    if fam == "day_of_week":
        return f"Family::DayOfWeek {{ weekday: {p['weekday']}, long: {b(p['side'] == 'long')} }}"
    if fam == "aroon":
        return f"Family::Aroon {{ period: {p['period']}, min_strength: {num(p['min_strength'])} }}"
    if fam == "regime_breakout":
        return (f"Family::RegimeBreakout {{ fraction: {num(p['fraction'])}, "
                f"response: {num(p['response'])} }}")
    if fam == "value_area":
        return (f"Family::ValueArea {{ buffer_atr: {num(p['buffer_atr'])}, "
                f"poc: {b(p['level'] == 'poc')} }}")
    if fam == "gated_fade":
        trig = {"vwap_dev": "FadeTrigger::VwapDev", "zscore": f"FadeTrigger::Zscore({2 * k})",
                "keltner": f"FadeTrigger::Keltner({20 * k})"}[p["trigger"]]
        gate, gp = gate_of(p["gate"], k)
        return (f"Family::GatedFade {{ trigger: {trig}, gate: {gate}, "
                f"polarity: {pol(p['polarity'])}, gate_period: {gp} }}")
    if fam == "two_stage":
        coil = {"squeeze": "Coil::Squeeze", "nr": "Coil::Nr"}[p["coil"]]
        return (f"Family::TwoStage {{ coil: {coil}, window: {p['window']}, compress: {5 * k}, "
                f"width_period: {k} }}")
    if fam == "gated_donchian":
        gate, gp = gate_of(p["gate"], k)
        return (f"Family::GatedDonchian {{ channel: {p['channel']}, gate: {gate}, "
                f"polarity: {pol(p['polarity'])}, gate_period: {gp} }}")
    if fam == "gated_orb":
        gate, gp = gate_of(p["gate"], k)
        return (f"Family::GatedOrb {{ gate: {gate}, polarity: {pol(p['polarity'])}, "
                f"gate_period: {gp} }}")
    if fam == "quantile_break":
        return (f"Family::QuantileBreak {{ channel: {p['channel']}, share: {num(p['share'])}, "
                f"buffer_atr: {num(p['buffer_atr'])} }}")
    if fam == "av_first_hour_fib":
        return f"Family::AvFirstHourFib {{ entry: {num(p['entry'])}, both: {b(p['side'] == 'both')} }}"
    if fam == "trap":
        level = {"pdr": "BreakLevel::Pdr", "donchian": "BreakLevel::Donchian",
                 "swing": "BreakLevel::Swing"}[p["level"]]
        return f"Family::Trap {{ window: {p['window']}, channel: {4 * k}, level: {level} }}"
    if fam == "obv_divergence":
        return f"Family::ObvDivergence {{ channel: {p['channel']} }}"
    if fam == "xma_cross":
        return (f"Family::XmaCross {{ fast: {p['fast']}, slow: {p['slow']}, "
                f"kind: MaKind::{p['kind'].capitalize()} }}")
    if fam == "vol_of_vol":
        return (f"Family::VolOfVol {{ period: {20 * k}, dispersion: {num(p['dispersion'])}, "
                f"unsteady: {b(p['state'] == 'unsteady')}, lookback: {k} }}")
    if fam == "skew":
        return (f"Family::Skew {{ period: {20 * k}, positive: {b(p['mode'] == 'positive')}, "
                f"threshold: {num(p['threshold'])}, long: {b(p['side'] == 'long')} }}")
    if fam == "kendall":
        return f"Family::Kendall {{ period: {p['period']}, threshold: {num(p['threshold'])} }}"
    if fam == "kalman":
        mode = {"slope": "KalmanMode::Slope", "residual": "KalmanMode::Residual"}[p["mode"]]
        return f"Family::Kalman {{ mode: {mode}, threshold: {num(p['threshold'])} }}"
    if fam == "adaptive_pullback":
        ref = {"ema_20d": 20 * k, "ema_50d": 50 * k}[p["reference"]]
        return (f"Family::AdaptivePullback {{ channel: {p['channel']}, depth: {num(p['depth'])}, "
                f"reference: {ref}, period: {p['period']}, response: {num(p['response'])} }}")
    if fam == "efficiency":
        return (f"Family::Efficiency {{ period: {p['period']}, min_er: {num(p['min_er'])}, "
                f"regime: ErRegime::{p['regime'].capitalize()} }}")
    if fam == "qtt_rsi_ema_retest":
        return f"Family::QttRsiEmaRetest {{ retest: {b(p['retest'])} }}"
    if fam == "cci":
        return f"Family::Cci {{ period: {p['period']}, threshold: {num(p['threshold'])} }}"
    if fam == "tr_oscillator_rebound":
        assert p["osc"] == "willr"
        return f"Family::TrOscillatorRebound {{ ema: {p['ema']} }}"
    if fam == "cusum":
        return f"Family::Cusum {{ multiple: {num(p['multiple'])} }}"
    if fam == "level_confluence":
        pair = {"pivot_pdr": "PivotPdr", "pivot_vwap": "PivotVwap", "pdr_vwap": "PdrVwap"}[p["pair"]]
        return f"Family::LevelConfluence {{ pair: LevelPair::{pair}, tolerance: {num(p['tolerance'])} }}"
    if fam == "linreg_trend":
        return (f"Family::LinregTrend {{ period: {p['period']}, slope: {num(p['slope'])}, "
                f"min_fit: {num(p['min_fit'])} }}")
    if fam == "break_retest":
        level = {"pdr": "BreakLevel::Pdr", "donchian": "BreakLevel::Donchian"}[p["level"]]
        retest = {"level": "Retest::Level", "vwap": "Retest::Vwap"}[p["retest"]]
        return (f"Family::BreakRetest {{ window: {p['window']}, level: {level}, retest: {retest}, "
                f"channel: {4 * k} }}")
    if fam == "estimator":
        return (f"Family::Estimator {{ period: {p['period']}, parkinson: {b(p['estimator'] == 'parkinson')}, "
                f"ratio: {num(p['ratio'])}, whipping: {b(p['state'] == 'whipping')}, lookback: {k} }}")
    if fam == "macd_hist":
        f_, s_, g_ = p["macd_set"]
        return (f"Family::MacdHist {{ fast: {f_}, slow: {s_}, signal: {g_}, "
                f"zero: {b(p['level'] == 'zero')} }}")
    if fam == "failed_break":
        return f"Family::FailedBreak {{ buffer_atr: {num(p['buffer_atr'])} }}"
    if fam == "momentum":
        return (f"Family::Momentum {{ signal_minute: {p['signal_minute']}, lookback: {p['lookback']}, "
                f"threshold_atr: {num(p['threshold_atr'])} }}")
    if fam == "half_life":
        return (f"Family::HalfLife {{ period: {p['period']}, multiple: {num(p['multiple'])}, "
                f"threshold_z: {num(p['threshold_z'])} }}")
    if fam == "volatility_breakout":
        return f"Family::VolatilityBreakout {{ fraction: {num(p['fraction'])} }}"
    if fam == "tr_rvi":
        return f"Family::TrRvi {{ below: {b(p['zone'] == 'below')}, ema: {p['ema']} }}"
    if fam == "twp_asia_london_ny":
        return f"Family::TwpAsiaLondonNy {{ tight: {num(p['tight'])}, win: {p['win']} }}"
    if fam == "bnq_ifvg_model":
        assert p["pda"] == "off"
        return f"Family::BnqIfvgModel {{ k: {p['k']}, disp: {num(p['disp'])} }}"
    if fam == "roofing":
        return (f"Family::Roofing {{ high_period: {p['band'][0]}, low_period: {p['band'][1]}, "
                f"amplitude_atr: {num(p['amplitude_atr'])} }}")
    if fam == "luxalgo_manipulation":
        engulf = {"body": "TtEngulf::Body", "range": "TtEngulf::Range"}[p["engulf"]]
        stop = ({"swing": "TtStop::Swing", "candle": "TtStop::Candle", "sweep": "TtStop::Sweep"}[p["stop"]]
                if not p["stop"].startswith("atr_") else f"TtStop::Atr({atr(p['stop'])})")
        return (f"Family::LuxalgoManipulation {{ prior_bars: {p['prior_bars']}, ema: {p['ema']}, "
                f"engulf: {engulf}, stop: {stop}, fade: {b(p.get('direction') == 'fade')} }}")
    if fam == "stochastic":
        return (f"Family::Stochastic {{ period: {p['period']}, smooth: {max(2, k // 2)}, "
                f"threshold: {num(p['threshold'])}, sum_seeds: &[] }}")
    if fam == "fracdiff":
        return (f"Family::Fracdiff {{ order: {num(p['order'])}, threshold_z: {num(p['threshold_z'])}, "
                f"window: {20 * k} }}")
    if fam == "overnight":
        return f"Family::Overnight {{ buffer_atr: {num(p['buffer_atr'])} }}"
    if fam == "amihud":
        return (f"Family::Amihud {{ short: {max(20, 5 * k)}, long: {max(120, 20 * k)}, "
                f"ratio: {num(p['ratio'])}, illiquid: {b(p['state'] == 'illiquid')}, lookback: {k} }}")
    if fam == "floor_pivot":
        level = {"pivot": 0, "first": 1, "second": 2}[p["level"]]
        return f"Family::FloorPivot {{ buffer_atr: {num(p['buffer_atr'])}, level: {level} }}"
    if fam == "qp_rsi_extreme":
        return (f"Family::QpRsiExtreme {{ level: {num(p['level'])}, both: {b(p['side'] == 'both')}, "
                f"stop_atr: {atr(p['stop'])} }}")
    raise SystemExit(f"no mapping for {fam}")


# ---------------------------------------------------------------------------
# contracts: one per market, frozen from the fixtures' spec
# ---------------------------------------------------------------------------
old_contracts = open(os.path.join(BOOKDIR, "contracts.rs"), encoding="utf-8").read()
old_session = {}
for mo in re.finditer(r"pub\(crate\) const T5_([A-Z0-9]+): Self = Self \{\s*session: \((\d+), (\d+)\),\s*shift_hours: (\d+),", old_contracts):
    old_session[mo.group(1).lower()] = (int(mo.group(2)), int(mo.group(3)), int(mo.group(4)))

fixtures = {}
for m in members:
    fam, tf = m["family"].split("@")
    stem = f"{m['symbol']}_{fam}_{tf}"
    fixtures[stem] = json.load(open(os.path.join(FIX, f"parity_{stem}.json")))

markets = {}
for stem, fx in fixtures.items():
    sym = fx["symbol"]
    have = markets.get(sym)
    if have is None or (fx["window"] == "rth" and have["window"] != "rth"):
        markets[sym] = fx

const_src = []
for sym in sorted(markets):
    fx = markets[sym]
    spec = fx["spec"]
    if fx["window"] == "rth":
        session, shift = tuple(fx["session"]), fx["shift_hours"]
    elif sym in old_session:
        session, shift = old_session[sym][:2], old_session[sym][2]
    else:
        session, shift = tuple(fx["rth"]), 0
    rule = fx["commission"]
    commission = ("Commission::None" if not rule else
                  f"Commission::UsdPerLot({num(rule[1])})" if rule[0] == "usd_per_lot" else
                  f"Commission::NotionalPct({num(rule[1])})")
    point = spec["point"] or 0.0
    fin = lambda s: abs(min(0.0, s or 0.0)) * point if spec["swap_mode"] == 1 else 0.0
    per = max(2, (session[1] - session[0]) // 30 + 1) if session[1] > session[0] else 2
    const_src.append(f"""    /// `{spec['broker']}` ({spec['asset_class']}), its cash session as the base clock.
    pub(crate) const T5_{sym.upper()}: Self = Self {{
        session: ({session[0]}, {session[1]}),
        shift_hours: {shift},
        per_session: {per},
        calendar: {num(spec['calendar'])},
        multiplier: {repr(spec['multiplier'])},
        contract_size: {num(spec['contract_size'])},
        fx_to_usd: {repr(spec['fx_to_usd'])},
        volume_min: {num(spec['volume_min'])},
        volume_step: {num(spec['volume_step'])},
        volume_max: {num(spec['volume_max'])},
        spread_bp: {repr(spec['spread_bp'])},
        financing_long: {repr(fin(spec['swap_long']))},
        financing_short: {repr(fin(spec['swap_short']))},
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: {commission},
    }};""")
head = old_contracts[:old_contracts.index("impl Instrument {")]
source = ("\n/// The5ers' name for a market (lower case, its `the5ers_<broker>_1m` table)\n"
          "/// and its point, the unit the M1 `spread` column counts in.\n"
          "pub(crate) fn broker_source(market: &str) -> Option<(&'static str, f64)> {\n"
          "    Some(match market {\n"
          + "".join(f"        \"{sym}\" => (\"{markets[sym]['spec']['broker'].lower()}\", {float(markets[sym]['spec']['point'])!r}),\n"
                    for sym in sorted(markets))
          + "        _ => return None,\n    })\n}\n")
open(os.path.join(BOOKDIR, "contracts.rs"), "w", newline="\n", encoding="utf-8").write(
    head + "impl Instrument {\n" + "\n".join(const_src) + "\n}\n" + source)

# ---------------------------------------------------------------------------
# sleeves
# ---------------------------------------------------------------------------
for old in glob.glob(os.path.join(BOOKDIR, "sleeves", "*.rs")):
    os.remove(old)
sleeves = []
for m in members:
    key = f"{m['symbol']}:{m['family']}"
    fam, tf = m["family"].split("@")
    sym = m["symbol"]
    stem = f"{sym}_{fam}_{tf}"
    fx = fixtures[stem]
    assert fx["sleeve"] == key, (fx["sleeve"], key)
    p = fx["params"]
    window = fx["window"]
    barlb = fx["bar_lookbacks"]
    tt = fx["protocol"].get("FN_TT") == "1"
    k = 1 if barlb else fx["per_session"]
    module = re.sub(r"[-@]", "_", stem)
    variant = "T5" + camel(stem)
    words = " ".join(WORDS.get(w, w.capitalize()) for w in fam.split("_"))
    label = {"rth": "", "off": " Off-hours"}.get(window, f" {window}")
    disp = (f"{sym.upper()} {words} {tf}" + " T5")
    if window == "off":
        const = f"Instrument::T5_{sym.upper()}.broker_day()"
    elif window == "rth":
        const = f"Instrument::T5_{sym.upper()}"
        assert tuple(fx["session"]) == tuple(markets[sym]["session"]) or markets[sym]["window"] != "rth", stem
    else:
        const = f"Instrument::T5_{sym.upper()}.window({fx['session'][0]}, {fx['session'][1]})"
    lem = (f"{p['last_entry_minute']}," if "last_entry_minute" in p
           else f"{fx['session'][1]}, // none in Python: the session close")
    direction = {"fade": "Fade", "follow": "Follow", "breakout": "Follow", None: "Follow"}[
        p.get("direction") if fam != "confluence" else p["mode"]]
    if fam == "luxalgo_manipulation":
        direction = "Follow"
    trend = {"none": "None", "ema_20d": "Ema20d", "ema_50d": "Ema50d"}[p["trend"]]
    vol = {"none": "Any", "calm": "Calm"}[p["vol_mode"]]
    family = family_of(fam, p, fx, k)
    uses = [w for w in ("BreakLevel", "Coil", "ErRegime", "FadeTrigger", "Gate", "KalmanMode",
                        "LevelPair", "MaKind", "Polarity", "Retest", "TtEngulf", "TtStop")
            if w + "::" in family]
    kind = "a TikTok-creator cell (`cfd_tt_families`)" if tt else "a `cfd_families` cell"
    where = {"rth": "", "off": ", run on the broker day outside the cash session"}.get(
        window, f", run in the New York window `{window}`")
    off_hours = f"Some(({fx['rth'][0]}, {fx['rth'][1]}))" if window == "off" else "None"
    scale = num(m["k"])
    src = f'''//! `{key}`, {kind} of the The5ers book
//! (`the5ers_combined_strategies`, {seal["id"]}){where}.
//! Generated from `fixtures/parity_{stem}.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{{Direction, Exit, Family, Params, Trend, VolMode{"".join(", " + u for u in uses)}}};
use crate::strategies::cfd::{{Book, EngineKind, SleeveSpec, Timeframe}};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {{
    display: "{disp}",
    id: "t5_{stem}",
    code: "T5_{stem.upper()}",
    python_key: "{key}",
    market: "{sym}",
    book: Book::The5ers,
    contract: {const},
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: {scale},
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: {b(barlb)},
    off_hours: {off_hours},
    clock_window: {b(window not in ("rth", "off"))},
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {{
        bar_minutes: {fx["bar"]},
        per_session: {fx["per_session"]},
        vol_target: {repr(fx["vol_target"])},
    }}),
    engine: EngineKind::Family(Params {{
        family: {family},
        direction: Direction::{direction},
        exit: {exit_of(p["exit_mode"])},
        last_entry_minute: {lem}
        stop_day: {num(p["stop_day"])},
        trend: Trend::{trend},
        vol_mode: VolMode::{vol},
    }}),
}};
'''
    open(os.path.join(BOOKDIR, "sleeves", module + ".rs"), "w", newline="\n").write(src)
    sleeves.append((variant, module, stem))

open(os.path.join(BOOKDIR, "sleeves", "mod.rs"), "w", newline="\n").write(
    "//! One file per sleeve of the The5ers book, each generated from its parity\n"
    "//! fixture's sealed parameters. Same shape as the Exness book's `sleeves/`.\n\n"
    + "\n".join(f"pub(crate) mod {m};" for _, m, _ in sorted(sleeves, key=lambda s: s[1])) + "\n")

# Sleeve enum: the The5ers block, after the FundedNext one (inserted the first time).
p = os.path.join(CFD, "mod.rs")
t = open(p, encoding="utf-8").read()
block = (f"    /// THE THE5ERS BOOK (`the5ers/nov-08-2026`): the {len(sleeves)} members of\n"
         f"    /// `the5ers_live_book.json` ({seal['id']}), in its order.\n"
         + "".join(f"    {v},\n" for v, _, _ in sleeves))
t, n = re.subn(r"    /// THE THE5ERS BOOK[^\n]*\n(?:    ///[^\n]*\n)*(?:    T5[A-Za-z0-9]+,\n)+", lambda mo: block, t)
if n == 0:
    t, n = re.subn(r"(    /// THE FUNDEDNEXT BOOK[^\n]*\n(?:    ///[^\n]*\n)*(?:    Fn[A-Za-z0-9]+,\n)+)", lambda mo: mo.group(1) + block, t)
assert n == 1
open(p, "w", encoding="utf-8", newline="\n").write(t)

# spec arms
p = os.path.join(CFD, "spec.rs")
t = open(p, encoding="utf-8").read()
arms = "".join(f"        S::{v} => &t5_book::{m}::SPEC,\n" for v, m, _ in sleeves)
t, n = re.subn(r"(?:        S::T5[A-Za-z0-9]+ => &t5_book::[a-z0-9_]+::SPEC,\n)+", lambda mo: arms, t)
if n == 0:
    t, n = re.subn(r"((?:        S::Fn[A-Za-z0-9]+ => &fn_book::[a-z0-9_]+::SPEC,\n)+)", lambda mo: mo.group(1) + arms, t)
    t = t.replace("    use crate::strategies::fundednext::oct_21_2026::sleeves as fn_book;\n",
                  "    use crate::strategies::fundednext::oct_21_2026::sleeves as fn_book;\n"
                  "    use crate::strategies::the5ers::nov_08_2026::sleeves as t5_book;\n")
assert n == 1
open(p, "w", encoding="utf-8", newline="\n").write(t)

# book list
p = os.path.join(BOOKDIR, "mod.rs")
t = open(p, encoding="utf-8").read()
t, n = re.subn(r"pub const BOOK: \[Sleeve; \d+\] = \[\n(?:    Sleeve::[A-Za-z0-9]+,\n)+\];",
               f"pub const BOOK: [Sleeve; {len(sleeves)}] = [\n"
               + "".join(f"    Sleeve::{v},\n" for v, _, _ in sleeves) + "];", t)
assert n == 1
open(p, "w", encoding="utf-8", newline="\n").write(t)

# parity list: the fixtures are read from disk at test time (295 MB).
p = os.path.join(BOOKDIR, "parity.rs")
t = open(p, encoding="utf-8").read()
t, n = re.subn(r"const ALL: \[\(Sleeve, &str\); \d+\] = \[\n(?:    \(\n        Sleeve::[A-Za-z0-9]+,\n        [^\n]+\n    \),\n)+\];",
               f"const ALL: [(Sleeve, &str); {len(sleeves)}] = [\n"
               + "".join(f"    (\n        Sleeve::{v},\n        \"parity_{s}.json\",\n    ),\n"
                         for v, _, s in sleeves) + "];", t)
assert n == 1, "parity ALL"
open(p, "w", encoding="utf-8", newline="\n").write(t)

print(len(sleeves), "sleeves,", len(markets), "markets:", ", ".join(sorted(markets)))
