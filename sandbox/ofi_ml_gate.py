"""The frozen Deep OFI Momentum meta-labeler, as the replica applies it.

`ofi_momentum.rs` runs every candidate through `ml_accepts` before entering, so
a replica that skips the gate is not a replica of the registered strategy -- it
is a replica of the retired unfiltered one. `validation.py` compared those two for
a while and read the difference as PnL drift.

This module holds the feature transform and the decision, with no imports from
`strategies` or `ofi_ml`, so both the replica and the training script can use
one copy. `research/ofi_ml.py` trains and exports the coefficients; nothing here fits
anything.

The transform must stay identical to `ml_features` in the Rust file. Its test is
`ml_logit_matches_the_python_export` there, plus `validation.py` end to end.
"""
import json
import math
from pathlib import Path

MODEL_PATH = Path(__file__).parent / "ofi_ml_model.json"

_MODEL = None


def model():
    """The exported model, loaded once."""
    global _MODEL
    if _MODEL is None:
        _MODEL = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    return _MODEL


def features(bar_ts, feature, z, spread, delta, side_is_long, entry_from, entry_to):
    """The 12 model inputs, read off the closed signal bar only.

    Every signed term is signed by the side actually traded, so the model sees
    "flow agreeing with my entry", not "flow that is positive".
    """
    sign = 1.0 if side_is_long else -1.0
    span = max(1, entry_to - entry_from)
    minute = (bar_ts % 86_400) // 60
    return (
        min(abs(z), 6.0),
        math.asinh(sign * delta / 100.0),
        sign * feature["top1_imbalance"],
        sign * feature["top5_imbalance"],
        sign * feature["top10_imbalance"],
        sign * (feature["microprice"] - feature["midprice"]) / 0.25,
        math.asinh(sign * feature["price_change"] / 2.0),
        sign * feature["replenishment_score"],
        math.log1p(feature["trade_count"]),
        math.log1p(feature["depth_event_count"]),
        spread,
        (minute - entry_from) / span,
    )


def probability(row):
    """Win probability for one feature tuple, from the frozen coefficients."""
    spec = model()
    logit = spec["intercept"] + sum(
        c * (x - m) / s
        for c, x, m, s in zip(spec["coefficients"], row,
                              spec["scaler_mean"], spec["scaler_scale"]))
    return 1.0 / (1.0 + math.exp(-logit))


def accepts(bar_ts, feature, z, spread, delta, side_is_long, entry_from, entry_to):
    """Whether the gate lets this candidate through."""
    row = features(bar_ts, feature, z, spread, delta, side_is_long,
                   entry_from, entry_to)
    return probability(row) >= model()["threshold"]
