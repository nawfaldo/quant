"""S2 stop-run reversal at prior-session RTH extremes.

Pre-declared experiment (written before inspecting PnL):

    Stops cluster beyond the previous regular-session high and low.  A genuine
    stop run should first trade through the level with aggression in the
    breakout direction, then close back inside while trade delta flips.  Enter
    the rejection on the next minute and fade the failed break.

Only three coarse axes are free: minimum penetration, maximum rejection delay,
and the rejection/sweep delta ratio.  The exit is fixed at a 12-point stop,
20-point target, and 60-minute time stop.  There is at most one attempt per
prior-session side per day.  Prior levels use completed 09:30--15:59 New York
bars only, so every level is known before the session that trades it.
"""

from sandbox.execution import LONG, SHORT, Execution, Signal
from sandbox.strategies.base import Strategy, register
from sandbox.data import C, D, H, L, O, TS

RTH_FROM = 570
RTH_TO = 959


def _exit_index(bars, entry_index, side, stop, target, max_minutes, session_end):
    """First possible re-entry index after this signal's resolved position."""
    entry = bars[entry_index][O]
    entry_ts = bars[entry_index][TS]
    entry_minute = (entry_ts % 86_400) // 60
    for index in range(entry_index + 1, len(bars)):
        bar = bars[index]
        if bar[TS] // 86_400 != entry_ts // 86_400:
            return index
        minute = (bar[TS] % 86_400) // 60
        if side == LONG:
            bracket = bar[L] <= entry - stop or bar[H] >= entry + target
        else:
            bracket = bar[H] >= entry + stop or bar[L] <= entry - target
        if bracket or minute >= entry_minute + max_minutes:
            return index
        nxt = bars[index + 1] if index + 1 < len(bars) else None
        if (
            minute < session_end
            and (
                nxt is None
                or nxt[TS] // 86_400 != bar[TS] // 86_400
                or (nxt[TS] % 86_400) // 60 >= session_end
            )
        ):
            return index
    return len(bars)


@register
class StopRunReversal(Strategy):
    name = "Stop Run Reversal"
    bars = "level_two"
    symbol = "nq"
    execution = Execution(session_end_min=960)
    defaults = {
        "penetration": 3.0,
        "rejection_minutes": 3,
        "delta_ratio": 0.25,
        "stop": 12.0,
        "target": 20.0,
        "time_stop": 60,
        "entry_from": RTH_FROM,
        "entry_to": 930,
        # Historical research cutoff.  Later Bookmap rows were still arriving
        # during this run and do not form complete sessions.
        "from_date": None,
        "to_date": "2026-07-23",
    }
    grid = {
        "penetration": [1.0, 3.0, 5.0],
        "rejection_minutes": [1, 3, 5],
        "delta_ratio": [0.0, 0.25, 0.5],
    }

    def context(self):
        # The optimizer calls this once before evaluating the grid.  Computing
        # the same completed-session map inside every cell would add no
        # information and needlessly repeat the full bar walk 27 times.
        from sandbox import data

        bars = data.load_bars(self.bars, self.symbol)
        return {"prior": self._prior_extremes(bars)}

    @staticmethod
    def _prior_extremes(bars):
        by_day = {}
        for bar in bars:
            minute = (bar[TS] % 86_400) // 60
            if not RTH_FROM <= minute <= RTH_TO:
                continue
            day = bar[TS] // 86_400
            row = by_day.setdefault(day, [bar[H], bar[L], 0])
            row[0] = max(row[0], bar[H])
            row[1] = min(row[1], bar[L])
            row[2] += 1

        prior = {}
        previous = None
        for day in sorted(by_day):
            # A shortened futures session is still a session.  Requiring at
            # least 180 observations excludes interrupted collector fragments.
            if previous is not None:
                prior[day] = (by_day[previous][0], by_day[previous][1])
            if by_day[day][2] >= 180:
                previous = day
        return prior

    def signals(self, bars, context, group, params):
        prior = context["prior"] if context is not None else self._prior_extremes(bars)
        start = None
        end = None
        if params["from_date"]:
            from sandbox.metrics import split_ts

            start = split_ts(params["from_date"])
        if params["to_date"]:
            from sandbox.metrics import split_ts

            end = split_ts(params["to_date"]) + 86_400

        out = []
        free_from = -1
        state_day = None
        used = {"high": False, "low": False}
        pending = {"high": None, "low": None}

        for index, bar in enumerate(bars):
            day = bar[TS] // 86_400
            minute = (bar[TS] % 86_400) // 60
            if day != state_day:
                state_day = day
                used = {"high": False, "low": False}
                pending = {"high": None, "low": None}
            if day not in prior or not RTH_FROM <= minute <= RTH_TO:
                continue
            if start is not None and bar[TS] < start:
                continue
            if end is not None and bar[TS] >= end:
                continue

            high_level, low_level = prior[day]
            penetration = params["penetration"]

            if (
                not used["high"]
                and pending["high"] is None
                and bar[H] >= high_level + penetration
                and bar[D] > 0.0
            ):
                pending["high"] = (index, bar[D])
            if (
                not used["low"]
                and pending["low"] is None
                and bar[L] <= low_level - penetration
                and bar[D] < 0.0
            ):
                pending["low"] = (index, bar[D])

            for key, level, side in (
                ("high", high_level, SHORT),
                ("low", low_level, LONG),
            ):
                sweep = pending[key]
                if sweep is None:
                    continue
                sweep_index, sweep_delta = sweep
                age = index - sweep_index
                if age > params["rejection_minutes"]:
                    pending[key] = None
                    used[key] = True
                    continue
                flipped = (
                    bar[D] <= -abs(sweep_delta) * params["delta_ratio"]
                    if key == "high"
                    else bar[D] >= abs(sweep_delta) * params["delta_ratio"]
                )
                inside = bar[C] < level if key == "high" else bar[C] > level
                if not (age >= 1 and inside and flipped):
                    continue

                pending[key] = None
                used[key] = True
                entry_index = index + 1
                if entry_index >= len(bars):
                    continue
                entry = bars[entry_index]
                entry_minute = (entry[TS] % 86_400) // 60
                if (
                    entry[TS] != bar[TS] + 60
                    or entry[TS] // 86_400 != day
                    or not params["entry_from"] <= entry_minute <= params["entry_to"]
                    or entry_index < free_from
                ):
                    continue
                out.append(
                    Signal(
                        entry_index,
                        side,
                        params["stop"],
                        params["target"],
                        params["time_stop"],
                    )
                )
                free_from = _exit_index(
                    bars,
                    entry_index,
                    side,
                    params["stop"],
                    params["target"],
                    params["time_stop"],
                    self.execution.session_end_min,
                )
        return out
