"""S5 cancel-at-distance (spoof/layering) signal definition.

This module deliberately keeps the S5 definition separate from the minute-bar
strategy registry.  S5 is observed and executed on the native one-second
feature clock, while registered replicas use minute bars.

Definition fixed before inspecting S5 PnL:

* normalize ``log1p`` add/cancel volume causally by second-of-session over the
  prior 20 observations at that clock slot;
* remember the strongest same-side add in the prior five contiguous seconds;
* confirm it with a same-side cancellation;
* require both the add and cancellation to be at least ``distance_ticks`` from
  that side's contemporaneous touch;
* short a canceled bid layer and buy a canceled ask layer;
* skip a second when both sides qualify;
* enter at the next contiguous valid second and exit after 15 seconds.

Only three coarse axes are free.  The cancellation z-score, lag, holding
horizon, direction, and cost model are fixed so the experiment cannot search
its way into a story after seeing outcomes.
"""

from dataclasses import dataclass

from sandbox import execution as execution_model

NAME = "S5 Cancel At Distance"
TICK_SIZE = 0.25
NORMALIZATION_OBSERVATIONS = 20
CONFIRMATION_LAG_SECONDS = 5
FIXED_CANCEL_Z = 2.0
CACHE_Z = 1.5
ENTRY_FROM_SECOND = 585 * 60
ENTRY_TO_SECOND = 930 * 60
PRIMARY_HORIZON_SECONDS = 15
HORIZONS_SECONDS = (1, PRIMARY_HORIZON_SECONDS, 60)
RISK_STOP_POINTS = 6.0


@dataclass(frozen=True)
class Definition:
    """The small interface needed by plateau and walk-forward selectors."""

    name: str = NAME
    execution: execution_model.Execution = execution_model.Execution(
        initial=10_000.0,
        slippage=0.2,
        margin=0.25,
        step=0.01,
        point_value=1.0,
        risk=0.005,
        leverage=1.0,
        session_end_min=None,
    )

    @property
    def defaults(self):
        return {
            "layer_z": 3.0,
            "distance_ticks": 4.0,
            "cancel_ratio": 1.0,
        }

    @property
    def grid(self):
        return {
            "layer_z": [2.0, 3.0, 4.0],
            "distance_ticks": [2.0, 4.0, 6.0],
            "cancel_ratio": [0.5, 1.0, 1.5],
        }

    @staticmethod
    def valid(_params):
        return True

    def all_params(self, overrides=None):
        out = dict(self.defaults)
        out.update(overrides or {})
        return out


def qualifies(event, params):
    """Whether one side of one cancel second clears a grid cell."""
    return (
        event["layer_z"] >= params["layer_z"]
        and event["cancel_z"] >= FIXED_CANCEL_Z
        and event["layer_distance_ticks"] >= params["distance_ticks"]
        and event["cancel_distance_ticks"] >= params["distance_ticks"]
        and event["cancel_volume"]
        >= params["cancel_ratio"] * event["layer_volume"]
    )


def selected_events(events, params):
    """One unambiguous directional event per signal second."""
    by_timestamp = {}
    for event in events:
        if qualifies(event, params):
            by_timestamp.setdefault(event["signal_ts"], []).append(event)
    out = []
    for timestamp in sorted(by_timestamp):
        rows = by_timestamp[timestamp]
        sides = {row["side"] for row in rows}
        if len(sides) == 1:
            out.append(max(rows, key=lambda row: (row["cancel_z"], row["layer_z"])))
    return out
