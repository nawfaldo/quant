"""The small interface a sweepable strategy must implement.

A strategy owns *signals only*. Entry bracketing, costs, sizing and the session
flatten live in `execution.py`, so adding a strategy means writing one
`signals()` and describing its parameters.
"""
from copy import copy

from sandbox import data
from sandbox.execution import Execution


class Strategy:
    #: name used on the command line and in `/api/run` payloads
    name = ""
    #: bar source from `data.BAR_SOURCES`
    bars = "level_two"
    symbol = "nq"
    #: account/cost model; override when a strategy trades a different session
    execution = Execution()
    #: the parameters compiled into the Rust file today
    defaults: dict = {}
    #: sweepable parameter -> candidate values
    grid: dict = {}

    @property
    def server_name(self):
        """Display name the Rust engine registers this strategy under.

        The Rust names gained an `NQ ` prefix on 2026-08-02 so they read like the
        BTC ones. Replica names deliberately did *not* change: the CLI selects on
        them and roughly twenty scripts under `research/` hold them as dict keys,
        including several whose results are quoted in sealed JSON. This property
        is the only place the two namespaces have to meet — override it wherever
        a replica's name differs from its Rust counterpart.
        """
        return self.name

    @property
    def date_range(self):
        """Date range the Rust engine is run over, for `validation.py`.

        Derived from the bars the replica loads, never hardcoded: a fixed range
        goes stale the moment history is extended, and then `validate` compares
        the two engines over different spans and calls the disagreement a match.
        """
        return data.bar_range(self.bars, self.symbol)

    def groups(self):
        """Independent signal groups, as `{group: [parameter, ...]}`.

        A group's signals depend only on the parameters it lists, so a sweep can
        generate each group once per combination of *its* parameters and then
        join groups for free. Strategies whose signals are entangled should keep
        the single default group.
        """
        return {"all": sorted(self.grid)}

    def valid(self, params):
        """Reject impossible corners of the grid (e.g. a target inside its stop).

        Prefer constraints that only involve one group's parameters: those prune
        the sweep before any signals are generated.
        """
        return True

    def context(self):
        """Anything loaded once and reused across every parameter combination.

        Runs before the sweep and is handed back to `signals()`. Return None when
        a strategy needs nothing beyond its bars.
        """
        return None

    def signals(self, bars, context, group, params):
        """`Signal`s for one group at one point in the parameter space.

        `params` always carries every parameter (defaults merged with the swept
        values), so a strategy may read shared settings that are not in `grid`.
        """
        raise NotImplementedError

    def all_params(self, overrides=None):
        merged = dict(self.defaults)
        merged.update(overrides or {})
        return merged

    def configured(self, overrides=None):
        """Return an independent copy with fixed parameter overrides.

        Registered strategies are shared objects. Copying before configuration
        keeps notebook cells, CLI commands, and tests from leaking state into
        one another.
        """
        configured = copy(self)
        configured.defaults = self.all_params(overrides)
        configured.grid = dict(self.grid)
        return configured


REGISTRY = {}


def register(strategy_class):
    """Class decorator: make a strategy available by name."""
    instance = strategy_class()
    REGISTRY[instance.name] = instance
    return strategy_class


def get(name):
    if name not in REGISTRY:
        raise SystemExit(f"unknown strategy {name!r}; known: {', '.join(sorted(REGISTRY))}")
    return REGISTRY[name]
