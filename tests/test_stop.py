"""Simulation in a separate process: Stop button, close-while-running, crashes."""
import time
import unittest
from unittest import mock

import numpy as np
from PySide6.QtWidgets import QApplication, QMessageBox

from kipida.core.models import PowerRail
from tests import sim_targets

app = QApplication.instance() or QApplication([])

RAILS = [PowerRail("+5V", 5.0), PowerRail("GND", 0.0)]


def wait_for(cond, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


class WorkerHarness:
    """Runs a SimulationWorker and records its signals."""

    def __init__(self, target):
        from kipida.ui.main_window import SimulationWorker
        self.events = []
        self.worker = SimulationWorker(RAILS, 0.1, False, target=target)
        self.worker.log_message.connect(lambda m: self.events.append(("log", m)))
        self.worker.succeeded.connect(lambda r: self.events.append(("ok", r)))
        self.worker.failed.connect(lambda e, tb: self.events.append(("failed", e)))
        self.worker.cancelled.connect(lambda: self.events.append(("cancelled",)))

    def run(self, timeout=30.0):
        self.worker.start()
        assert wait_for(self.worker.isFinished, timeout), "worker did not finish"
        return self.kinds()

    def kinds(self):
        return [e[0] for e in self.events]


class TestSimulationWorker(unittest.TestCase):
    def test_result_comes_back_from_the_process(self):
        h = WorkerHarness(sim_targets.ok)
        self.assertEqual(h.run(), ["log", "ok"])
        self.assertEqual(h.events[1][1], {"rails": ["+5V", "GND"], "grid": 0.1})

    def test_stop_kills_a_long_run_immediately(self):
        h = WorkerHarness(sim_targets.slow)
        h.worker.start()
        self.assertTrue(wait_for(lambda: ("log", "started") in h.events))
        t = time.perf_counter()
        h.worker.stop()
        self.assertTrue(wait_for(h.worker.isFinished, 10))
        self.assertLess(time.perf_counter() - t, 3.0)
        self.assertEqual(h.kinds(), ["log", "cancelled"])
        self.assertFalse(h.worker._process.is_alive())

    def test_process_crash_is_reported(self):
        h = WorkerHarness(sim_targets.crash)
        kinds = h.run()
        # A hard exit can lose messages still queued in the dying process, so the
        # log line may or may not arrive; the failure must always be reported.
        self.assertEqual(kinds[-1], "failed")
        self.assertNotIn("ok", kinds)
        self.assertIn("exit code 3", str(h.events[-1][1]))

    def test_cycle_error_keeps_its_type(self):
        from kipida.simulation.system import PowerTreeCycleError
        h = WorkerHarness(sim_targets.cycle)
        self.assertEqual(h.run(), ["failed"])
        self.assertIsInstance(h.events[-1][1], PowerTreeCycleError)


class TestMainWindowStop(unittest.TestCase):
    def setUp(self):
        from kipida.ui.main_window import MainWindow
        self.win = MainWindow(board_adapter=None, project=None)
        self.win.simulation_target = sim_targets.slow
        self.win.power_tree.rails = list(RAILS)
        self.win.show()
        # Window shortcuts (R, P) only fire in the active window
        from PySide6.QtTest import QTest
        self.win.activateWindow()
        QTest.qWaitForWindowActive(self.win)

    def tearDown(self):
        if self.win.is_simulating():
            self.win._worker.stop()
            self.win._worker.wait(10000)
        self.win.close()

    def start_slow_run(self):
        self.assertFalse(self.win.btn_stop.isEnabled())
        self.win.btn_run.click()
        self.assertTrue(wait_for(lambda: "started" in self.win.log_ctrl.toPlainText()))
        self.assertTrue(self.win.btn_stop.isEnabled())
        self.assertFalse(self.win.btn_run.isEnabled())

    def test_stop_button(self):
        self.start_slow_run()
        self.win.btn_stop.click()
        self.assertTrue(wait_for(lambda: not self.win.is_simulating(), 10))
        app.processEvents()
        self.assertIn("stopped by the user", self.win.log_ctrl.toPlainText())
        self.assertTrue(self.win.lbl_timing.text().startswith("Stopped after"))
        self.assertTrue(self.win.btn_run.isEnabled())
        self.assertFalse(self.win.btn_stop.isEnabled())

    def test_close_asks_and_no_keeps_running(self):
        self.start_slow_run()
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as ask:
            self.win.close()
        ask.assert_called_once()
        self.assertTrue(self.win.isVisible())
        self.assertTrue(self.win.is_simulating())

    def test_close_asks_and_yes_stops_and_closes(self):
        self.start_slow_run()
        proc = self.win._worker._process
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.win.close()
        self.assertFalse(self.win.isVisible())
        self.assertFalse(proc.is_alive())

    def test_keys_r_and_p(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.win.tabs.setCurrentIndex(self.win.TAB_CONFIG)  # works outside the Results tab too
        target = self.win.power_tree.rail_list
        QTest.keyClick(target, Qt.Key.Key_R)
        self.assertTrue(wait_for(lambda: "started" in self.win.log_ctrl.toPlainText()))
        self.assertTrue(self.win.is_simulating())
        worker = self.win._worker
        QTest.keyClick(target, Qt.Key.Key_R)          # already running: ignored
        self.assertIs(self.win._worker, worker)
        QTest.keyClick(target, Qt.Key.Key_P)
        self.assertTrue(wait_for(lambda: not self.win.is_simulating(), 10))
        QTest.keyClick(target, Qt.Key.Key_P)          # nothing running: harmless
        app.processEvents()
        self.assertIn("stopped by the user", self.win.log_ctrl.toPlainText())

    def test_typing_in_a_text_field_does_not_run(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.win.power_tree._populate_rail_list(0)
        field = self.win.power_tree.txt_voltage
        field.setFocus()
        QTest.keyClicks(field, "rp")
        app.processEvents()
        self.assertFalse(self.win.is_simulating())
        self.assertTrue(field.text().endswith("rp"))
        # ...while the same key outside a text field does run (so the check above is meaningful)
        QTest.keyClick(self.win.power_tree.rail_list, Qt.Key.Key_R)
        self.assertTrue(wait_for(self.win.is_simulating, 5))

    def test_close_when_idle_does_not_ask(self):
        with mock.patch.object(QMessageBox, "question") as ask:
            self.win.close()
        ask.assert_not_called()
        self.assertFalse(self.win.isVisible())


class TestSlimResult(unittest.TestCase):
    def test_compact_result_gives_identical_layer_grids(self):
        from kipida.simulation.mesh import Mesh
        from kipida.simulation.process import slim_result
        from kipida.simulation.system import RailResult, SystemResult
        from kipida.visualization.layer_grid import mesh_to_layer_grids
        m = Mesh()
        m.grid_origin, m.grid_step = (1.0, 2.0), 0.5
        res = {}
        for nid, key in enumerate([(0, 0, 3), (4, 1, 3), (2, 2, 34)]):
            m.nodes.append(nid)
            m.node_map[key] = nid
            res[nid] = 5.0 - 0.01 * nid
        full = SystemResult(stackup={}, elapsed_s=1.5)
        full.rails["+5V"] = RailResult("+5V", m, res, 4.98, 5.0, {"solve": 0.1})
        slim = slim_result(full)
        r = slim.rails["+5V"]
        self.assertEqual(len(r.mesh.nodes), 3)
        self.assertEqual((r.v_min, r.v_max, r.timings, slim.elapsed_s), (4.98, 5.0, {"solve": 0.1}, 1.5))
        a, b = mesh_to_layer_grids(m, res), mesh_to_layer_grids(r.mesh, r.results)
        self.assertEqual(sorted(a), sorted(b))
        for lid in a:
            np.testing.assert_array_equal(a[lid].values, b[lid].values)
            self.assertEqual(a[lid].rect, b[lid].rect)


if __name__ == "__main__":
    unittest.main()
