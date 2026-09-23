"""Cumulative trial accounting, for an honest search haircut.

The deflated statistic asks "how good would the best cell look if none of them
worked?", and the answer depends on how many cells you looked at. Counting only
the current run's grid answers a question nobody asked: by the time a strategy
reaches a walk-forward it has usually been through several sweeps, a handful of
one-axis scans and a few context variants, and *every one of those* was a draw
from the same noise.

The size of the correction is not cosmetic. A run that evaluates 36 cells and
reports a deflated Sharpe just below zero can land near -0.6 once it is charged
against the several hundred trials the strategy had already consumed. Same
result, different verdict, and only the second one is true.

So the count lives in a checked-in file and every run increments it. It is
append-only on purpose -- a counter you can reset is a counter that will be
reset the first time it says something unwelcome.
"""
import json
import os

PATH = os.path.join(os.path.dirname(__file__), "trials.json")


def _load():
    if not os.path.exists(PATH):
        return {}
    with open(PATH) as f:
        return json.load(f)


def total(strategy_name):
    """Trials charged to `strategy_name` so far."""
    return _load().get(strategy_name, {}).get("trials", 0)


def record(strategy_name, cells, what):
    """Charge `cells` trials to `strategy_name` and return the new total."""
    state = _load()
    entry = state.setdefault(strategy_name, {"trials": 0, "log": []})
    entry["trials"] += cells
    entry["log"].append({"what": what, "cells": cells, "running": entry["trials"]})
    with open(PATH, "w") as f:
        json.dump(state, f, indent=1)
    return entry["trials"]
