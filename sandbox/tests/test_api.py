"""Public API invariants."""

import unittest

from sandbox import get_strategy, list_strategies


class PublicApiTests(unittest.TestCase):
    def test_strategy_names_are_stable_and_sorted(self):
        names = list_strategies()

        self.assertEqual(names, tuple(sorted(names)))
        self.assertIn("Hourly Delta Reversal", names)

    def test_configuration_does_not_mutate_registry(self):
        registered = get_strategy("Hourly Delta Reversal")
        original = dict(registered.defaults)

        configured = registered.configured({"sell_delta": 123})

        self.assertEqual(configured.defaults["sell_delta"], 123)
        self.assertEqual(registered.defaults, original)
        self.assertIsNot(configured.defaults, registered.defaults)


if __name__ == "__main__":
    unittest.main()
