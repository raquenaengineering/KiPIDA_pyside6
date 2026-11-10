"""Headless smoke tests for the PySide6 UI (QT_QPA_PLATFORM=offscreen, see conftest)."""
import unittest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from kipida.core.models import ComponentRef, PowerRail, UnifiedLoad, VoltageRegulator

app = QApplication.instance() or QApplication([])


class FakePad:
    def __init__(self, number):
        self.number = number


class FakeDiscoverer:
    nets = {
        "+5V": {"U1": [FakePad("1"), FakePad("2")], "J1": [FakePad("1")]},
        "+3V3": {"U1": [FakePad("5")], "U2": [FakePad("7"), FakePad("8")]},
    }

    def get_components_on_net(self, net):
        return self.nets.get(net, {})


class TestComponentSelector(unittest.TestCase):
    def setUp(self):
        from kipida.ui.component_selector import ComponentSelectorDialog
        self.dlg = ComponentSelectorDialog(None, "Select", "+5V", FakeDiscoverer.nets["+5V"])

    def test_load_all_pads(self):
        self.dlg.set_mode("LOAD")
        self.dlg.lst_comps.setCurrentItem(self.dlg.lst_comps.findItems("U1", Qt.MatchFlag.MatchExactly)[0])
        self.dlg.txt_val.setText("0.5")
        self.dlg.hide()  # get_selection must not depend on widget visibility
        self.assertEqual(self.dlg.get_selection(), ("U1", 0.5, ["1", "2"]))

    def test_source_subset_of_pads_via_prepopulate(self):
        self.dlg.set_mode("SOURCE")
        self.dlg.prepopulate("U1", 0.0, ["2"])
        self.assertFalse(self.dlg.chk_all_pads.isChecked())
        self.assertEqual(self.dlg.get_selection(), ("U1", 0.0, ["2"]))


class TestRegulatorDialog(unittest.TestCase):
    def test_prepopulate_round_trip(self):
        from kipida.ui.regulator_dialog import RegulatorDialog
        reg = VoltageRegulator(name="old", input_rail_name="+5V", input_ref_des="U1", input_pad_names=["2"],
                               output_rail_name="+3V3", output_ref_des="U1", output_pad_names=["5"],
                               reg_type="SWITCHING", efficiency=0.9)
        dlg = RegulatorDialog(None, "Edit", ["+5V", "+3V3"], FakeDiscoverer(),
                              input_rail=reg.input_rail_name, output_rail=reg.output_rail_name)
        dlg.prepopulate(reg)
        self.assertEqual(dlg.get_value(), {
            'name': "U1 (+3V3)", 'input_rail': "+5V", 'input_ref_des': "U1", 'input_pads': ["2"],
            'output_rail': "+3V3", 'output_ref_des': "U1", 'output_pads': ["5"],
            'type': "SWITCHING", 'efficiency': 0.9,
        })
        self.assertTrue(dlg.txt_eff.isEnabled())


class TestResultsView(unittest.TestCase):
    def _rail_result(self):
        from kipida.simulation.mesh import Mesh
        from kipida.simulation.system import RailResult
        m = Mesh()
        m.grid_origin, m.grid_step = (0.0, 0.0), 1.0
        res = {}
        for nid, (xi, yi, lid) in enumerate([(0, 0, 0), (1, 0, 0), (0, 0, 2), (1, 0, 2)]):
            m.nodes.append(nid)
            m.node_map[(xi, yi, lid)] = nid
            m.node_coords[nid] = (float(xi), float(yi), lid)
            res[nid] = 3.3 - 0.01 * nid
        return RailResult("+3V3", m, res, min(res.values()), max(res.values()))

    def test_tabs_follow_stackup_order(self):
        from kipida.ui.results_view import RailResultsWidget
        # KiCad 9+ ids: F.Cu=0, B.Cu=2 -> stackup order must win over id order
        stackup = {'copper': {0: {'name': 'F.Cu'}, 2: {'name': 'B.Cu'}}, 'layer_order': [0, 2]}
        view = RailResultsWidget(self._rail_result(), stackup, drop_pct=5.0)
        self.assertEqual([view.tabText(i) for i in range(view.count())], ["3D View", "F.Cu", "B.Cu"])

    def test_drop_pct_updates_levels_and_hover(self):
        from kipida.ui.results_view import RailResultsWidget, drop_levels
        view = RailResultsWidget(self._rail_result(), {'copper': {}, 'layer_order': []}, drop_pct=5.0)
        plot = view.layer_plots[0]
        self.assertEqual(tuple(round(v, 6) for v in plot.color_bar.levels()), tuple(round(v, 6) for v in drop_levels(3.3, 5.0)))
        view.set_drop_pct(10.0)
        lo, hi = plot.color_bar.levels()
        self.assertAlmostEqual(lo, 3.3 * 0.9, places=6)
        self.assertAlmostEqual(hi, 3.3, places=6)
        self.assertIn("3.2900 V", plot.readout_text(1.0, 0.0))
        self.assertIn("no copper", plot.readout_text(5.0, 5.0))

    def test_3d_texture_transparent_outside_copper(self):
        from kipida.ui.results_view import grid_to_rgba
        from kipida.visualization.layer_grid import mesh_to_layer_grids
        r = self._rail_result()
        r.mesh.node_map[(3, 2, 0)] = 99  # widen the grid: cells in between have no copper
        r.results[99] = 3.3
        g = mesh_to_layer_grids(r.mesh, r.results)[0]
        rgba = grid_to_rgba(g, (3.2, 3.3))
        self.assertEqual(rgba.shape, g.values.shape + (4,))
        self.assertEqual(rgba.dtype.name, "uint8")
        self.assertEqual(rgba[0, 0, 3], 255)   # copper: opaque
        self.assertEqual(rgba[2, 1, 3], 0)     # no copper: transparent

    def test_zero_drop_rail_does_not_break_levels(self):
        from kipida.ui.results_view import drop_levels
        lo, hi = drop_levels(3.3, 0.0)
        self.assertLess(lo, hi)


class TestMainWindow(unittest.TestCase):
    def test_build_and_show_rails(self):
        from kipida.ui.main_window import MainWindow
        win = MainWindow(board_adapter=None, project=None)
        try:
            self.assertIn("PySide6", win.windowTitle())
            rail = PowerRail("+5V", 5.0)
            rail.add_load(UnifiedLoad(ComponentRef("U2"), 1.5, ["1"]))
            panel = win.power_tree
            panel.rails = [rail, PowerRail("+3V3", 3.3)]
            panel._populate_rail_list(0)
            self.assertIs(panel.active_rail, rail)
            self.assertTrue(panel.detail_panel.isEnabled())
            self.assertEqual(panel.comp_list.topLevelItemCount(), 1)
            self.assertEqual(panel.comp_list.topLevelItem(0).text(2), "1.5 A")

            panel.on_voltage_change("4.8")
            self.assertEqual(rail.nominal_voltage, 4.8)

            win.log("hello from test")
            app.processEvents()
            self.assertIn("hello from test", win.log_ctrl.toPlainText())
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main()
