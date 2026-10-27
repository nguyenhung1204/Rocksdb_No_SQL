import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bench import percentile  # noqa: E402


class BenchmarkTest(unittest.TestCase):
    def test_percentiles_use_nearest_rank_of_observed_latencies(self):
        samples = [1, 2, 3, 4, 100]

        self.assertEqual(percentile(samples, 50), 3)
        self.assertEqual(percentile(samples, 95), 100)
        self.assertEqual(percentile(samples, 99), 100)


if __name__ == "__main__":
    unittest.main()
