"""Tests for the multi-rail power-tree solve (kipida.simulation.system)."""
import unittest
from unittest import mock

from kipida.core.models import ComponentRef, PowerRail, UnifiedLoad, UnifiedSource, VoltageRegulator
from kipida.simulation import system
from kipida.simulation.mesh import Mesh


def _reg(inp, out, reg_type="LINEAR", eff=0.85, name="U1"):
    return VoltageRegulator(name=name, input_rail_name=inp, input_ref_des="U1", input_pad_names=["IN"],
                            output_rail_name=out, output_ref_des="U1", output_pad_names=["OUT"],
                            reg_type=reg_type, efficiency=eff)


class TestTopology(unittest.TestCase):
    def test_leaves_first(self):
        r12, r5, r33 = PowerRail("12V", 12.0), PowerRail("5V", 5.0), PowerRail("3V3", 3.3)
        r12.add_child_regulator(_reg("12V", "5V"))
        r5.add_child_regulator(_reg("5V", "3V3"))
        order = [r.net_name for r in system.topological_sort_rails([r12, r5, r33])]
        self.assertEqual(order, ["3V3", "5V", "12V"])

    def test_cycle_detected(self):
        a, b = PowerRail("A", 5.0), PowerRail("B", 3.3)
        a.add_child_regulator(_reg("A", "B"))
        b.add_child_regulator(_reg("B", "A"))
        with self.assertRaises(system.PowerTreeCycleError):
            system.topological_sort_rails([a, b])

    def test_regulator_input_current(self):
        self.assertEqual(system.regulator_input_current(_reg("A", "B", "LINEAR"), 1.0, 10.0, 5.0), 1.0)
        self.assertAlmostEqual(system.regulator_input_current(_reg("A", "B", "SWITCHING", 0.8), 1.0, 10.0, 5.0), 0.625)
        self.assertEqual(system.regulator_input_current(_reg("A", "B", "SWITCHING", 0.8), 1.0, 0.0, 5.0), 0.0)


class _FakeExtractor:
    def __init__(self, *args, **kwargs):
        pass

    def get_board_stackup(self):
        return {'copper': {0: {'name': 'F.Cu'}}, 'resistivity': 1.7e-5}

    def get_net_geometry(self, net_name):
        return {0: "non-empty geometry"}


class _FakeMesher:
    def __init__(self, *args, **kwargs):
        pass

    def generate_mesh(self, net_name, geo, stackup, grid_size_mm):
        m = Mesh()
        m.nodes = [0, 1]  # node 0: source pads, node 1: load/regulator-input pads
        return m


class TestSystemSimulator(unittest.TestCase):
    """Run the orchestration with fake geometry/mesh to check current propagation."""

    def _run(self, reg_type, eff=0.85):
        r10 = PowerRail("10V", 10.0)
        r10.add_source(UnifiedSource(ComponentRef("J1"), ["1"]))
        r5 = PowerRail("5V", 5.0)
        r5.add_load(UnifiedLoad(ComponentRef("U2"), total_current=1.0, pad_names=["1"]))
        r10.add_child_regulator(_reg("10V", "5V", reg_type, eff))

        loads_by_rail = {}

        class _RecordingSolver:
            def __init__(self, *args, **kwargs):
                pass

            def solve(self, mesh, sources, loads):
                loads_by_rail[current_rail[0]] = sum(l['current'] for l in loads)
                return {0: 10.0, 1: 9.9}

        current_rail = [None]
        sim = system.SystemSimulator(board=None, log_callback=lambda m: None)

        def fake_nodes(mesh, ref_des, pad_names):
            return [0] if ref_des == "J1" or pad_names == ["OUT"] else [1]

        orig_log = sim.log

        def tracking_log(msg):
            if msg.startswith("Processing Rail: "):
                current_rail[0] = msg.split()[2]
            orig_log(msg)

        sim.log = tracking_log
        with mock.patch.object(system, "GeometryExtractor", _FakeExtractor), \
             mock.patch.object(system, "Mesher", _FakeMesher), \
             mock.patch.object(system, "Solver", _RecordingSolver), \
             mock.patch.object(sim, "get_mesh_nodes", side_effect=fake_nodes):
            result = sim.run([r10, r5], grid_size_mm=0.1)
        return result, loads_by_rail

    def test_linear_regulator_load_applied_once(self):
        result, loads = self._run("LINEAR")
        self.assertEqual(result.solve_order, ["5V", "10V"])
        self.assertAlmostEqual(loads["5V"], 1.0)
        # 1 A out of a linear regulator draws 1 A from the input rail (not 2 A)
        self.assertAlmostEqual(loads["10V"], 1.0)
        self.assertEqual(set(result.rails), {"5V", "10V"})
        self.assertAlmostEqual(result.rails["10V"].drop, 0.1)

    def test_timings_recorded(self):
        result, _loads = self._run("LINEAR")
        for rail in result.rails.values():
            self.assertEqual(list(rail.timings), ["geometry", "mesh", "setup", "solve"])
            self.assertTrue(all(t >= 0 for t in rail.timings.values()))
        self.assertGreaterEqual(result.elapsed_s, sum(r.elapsed_s for r in result.rails.values()))
        self.assertIn("solve", system.format_timings(result.rails["5V"].timings))

    def test_switching_regulator_load_applied_once(self):
        _result, loads = self._run("SWITCHING", eff=0.8)
        # 5 V * 1 A / 0.8 / 10 V = 0.625 A
        self.assertAlmostEqual(loads["10V"], 0.625)


if __name__ == "__main__":
    unittest.main()
