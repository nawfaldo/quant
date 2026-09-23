"""A4 time-clocked VPIN proxy as an overlay on mean-reversion strategies.

The gate is deliberately small and causal:

* minute toxicity is ``abs(trade_delta) / aggressive_volume``;
* a reading at an entry uses only completed minutes before the entry;
* 1/3/5-minute rolling means test persistence without inventing a directional
  signal;
* 0.15/0.20/0.30 are coarse absolute cuts around the observed upper tail;
* absent, zero-volume, or non-contiguous history fails open.

The wrapper keeps the compiled strategy's signal and bracket parameters fixed.
Only the toxicity off-switch is searched.
"""

from sandbox import data
from sandbox.strategies.base import Strategy


LOOKBACKS = (1, 3, 5)
CUTOFFS = (0.15, 0.20, 0.30)


def minute_toxicity(feature):
    """Return the time-clocked VPIN proxy for one completed minute."""
    if feature is None:
        return None
    volume = (
        feature.get("aggressive_buy_volume", 0.0)
        + feature.get("aggressive_sell_volume", 0.0)
    )
    if volume <= 0.0:
        return None
    return abs(feature.get("trade_delta", 0.0)) / volume


def toxicity_series(bars, features, lookback):
    """Reading available at each bar open, using prior completed minutes only.

    A full contiguous window is required. Missing history is not evidence of
    low toxicity and is represented by ``None`` so the gate can fail open.
    """
    if lookback <= 0:
        raise ValueError("lookback must be positive")
    out = [None] * len(bars)
    for entry_index in range(lookback, len(bars)):
        start = entry_index - lookback
        window = bars[start:entry_index]
        if any(
            window[index + 1][data.TS] != window[index][data.TS] + 60
            for index in range(len(window) - 1)
        ):
            continue
        # The last completed minute must lead directly into the entry minute.
        if window[-1][data.TS] + 60 != bars[entry_index][data.TS]:
            continue
        readings = [
            minute_toxicity(features.get(bar[data.TS])) for bar in window
        ]
        if any(value is None for value in readings):
            continue
        out[entry_index] = sum(readings) / lookback
    return out


class ToxicityOverlay(Strategy):
    """Keep a base strategy's entries unless recent toxicity is too high."""

    defaults = {"toxicity_lookback": 3, "toxicity_cutoff": 0.20}
    grid = {
        "toxicity_lookback": list(LOOKBACKS),
        "toxicity_cutoff": list(CUTOFFS),
    }

    def __init__(self, base):
        self.base = base
        # Trial accounting intentionally stays on the underlying strategy
        # family: the overlay inherits every search already spent on its entry.
        self.name = base.name
        self.bars = base.bars
        self.symbol = base.symbol
        self.execution = base.execution

    @property
    def date_range(self):
        return self.base.date_range

    def groups(self):
        # Base parameters are frozen, so all base signal groups can be joined
        # before the two gate axes are applied.
        return {"all": sorted(self.grid)}

    def context(self):
        bars = data.load_bars(self.bars, self.symbol)
        features = data.load_l2_features(self.symbol)
        return {
            "base": self.base.context(),
            "toxicity": {
                lookback: toxicity_series(bars, features, lookback)
                for lookback in LOOKBACKS
            },
        }

    def signals(self, bars, context, group, params):
        base_params = self.base.all_params()
        signals = []
        for base_group in self.base.groups():
            signals.extend(
                self.base.signals(
                    bars, context["base"], base_group, base_params
                )
            )

        readings = context["toxicity"][params["toxicity_lookback"]]
        cutoff = params["toxicity_cutoff"]
        return [
            signal
            for signal in signals
            if readings[signal.index] is None
            or readings[signal.index] < cutoff
        ]

