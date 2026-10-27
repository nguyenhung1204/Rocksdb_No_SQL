import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import bench  # noqa: E402


class BenchmarkTest(unittest.TestCase):
    def test_percentiles_use_nearest_rank_of_observed_latencies(self):
        samples = [1, 2, 3, 4, 100]

        self.assertEqual(bench.percentile(samples, 50), 3)
        self.assertEqual(bench.percentile(samples, 95), 100)
        self.assertEqual(bench.percentile(samples, 99), 100)

    def test_independent_write_trials_report_spread_and_sample_count(self):
        summary = getattr(bench, "throughput_summary", None)
        self.assertTrue(callable(summary), "independent write runs need a sample-aware summary")
        self.assertEqual(summary([10, 30, 20]), {
            "min": 10, "median": 20, "max": 30, "samples": 3,
        })

    def test_query_percentile_summary_reports_observation_count(self):
        result = bench.latency_summary([1, 2, 3, 4, 100])
        self.assertEqual(result, {"samples": 5, "p50": 3, "p95": 100, "p99": 100})


if __name__ == "__main__":
    unittest.main()
