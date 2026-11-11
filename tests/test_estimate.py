"""Run-time estimate, checked against real measurements.

Measured on a 4-layer board (two solved rails), full simulation in a fresh
process. Per-rail timings at 0.1 mm are the calibration; the measured wall
times at other resolutions are what the estimate must predict.
"""
import unittest

from kipida.simulation.estimate import Calibration, estimate_seconds, format_duration

CAL_01MM = Calibration(
    grid_mm=0.1,
    rails={
        "+3V3": (8_428, {"geometry": 0.14, "mesh": 0.20, "setup": 0.10, "solve": 0.16}),
        "+12V": (215_436, {"geometry": 0.22, "mesh": 0.43, "setup": 0.24, "solve": 1.00}),
    },
    plots_s=0.0,      # the measurement runs drew no plots
    overhead_s=1.81,  # process start + KiCad connection + stackup/outline/skipped rails
    wall_s=4.3,
)

MEASURED_WALL_S = {0.2: 3.2, 0.07: 5.7, 0.05: 8.6, 0.035: 15.2, 0.025: 28.1}


class TestEstimate(unittest.TestCase):
    def test_predicts_measured_runs_within_20_percent(self):
        for grid, measured in MEASURED_WALL_S.items():
            predicted = estimate_seconds(CAL_01MM, grid)
            with self.subTest(grid=grid):
                self.assertLess(abs(predicted - measured) / measured, 0.20,
                                f"{grid} mm: predicted {predicted:.1f} s, measured {measured} s")

    def test_same_resolution_gives_calibration_time(self):
        rails_and_overhead = 1.81 + 0.6 + 1.89
        self.assertAlmostEqual(estimate_seconds(CAL_01MM, 0.1), rails_and_overhead, places=6)

    def test_from_run(self):
        from kipida.simulation.mesh import Mesh
        from kipida.simulation.system import RailResult, SystemResult
        m = Mesh()
        m.nodes = list(range(1000))
        res = SystemResult(stackup={})
        res.rails["A"] = RailResult("A", m, {}, 0.0, 1.0, {"geometry": 0.2, "mesh": 0.5, "setup": 0.1, "solve": 1.2})
        cal = Calibration.from_run(res, grid_mm=0.1, wall_s=5.0, plots_s=1.0)
        self.assertEqual(cal.rails["A"][0], 1000)
        self.assertAlmostEqual(cal.overhead_s, 5.0 - 2.0 - 1.0)
        self.assertAlmostEqual(estimate_seconds(cal, 0.1), 5.0)

    def test_format(self):
        self.assertEqual(format_duration(0.4), "~1 s")
        self.assertEqual(format_duration(7.6), "~8 s")
        self.assertEqual(format_duration(28.1), "~30 s")
        self.assertEqual(format_duration(200), "~3 min")
        self.assertEqual(format_duration(5400), "~1.5 h")


if __name__ == "__main__":
    unittest.main()
