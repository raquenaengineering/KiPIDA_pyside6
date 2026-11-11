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

    def test_board_outline_drawn_on_all_views(self):
        import numpy as np
        from kipida.ui.results_view import RailResultsWidget
        outline = [np.array([(-5, -5), (20, -5), (20, 10), (-5, 10), (-5, -5)], float)]
        stackup = {'copper': {0: {'name': 'F.Cu'}, 2: {'name': 'B.Cu'}}, 'layer_order': [0, 2]}
        view = RailResultsWidget(self._rail_result(), stackup, drop_pct=5.0, board_outline=outline)
        for plot in view.layer_plots:
            self.assertIsNotNone(plot.outline_item)
            x, y = plot.outline_item.getData()
            self.assertEqual(len(x), 6)  # 5 points + NaN separator
            # The view is framed on the whole board, not only the small copper area
            (x0, x1), (y0, y1) = plot.plot.getPlotItem().vb.viewRange()
            self.assertLessEqual(x0, -5)
            self.assertGreaterEqual(x1, 20)
        if view.view_3d.planes:  # 3D needs PyOpenGL
            self.assertEqual(len(view.view_3d.outline_items), 2)  # one per layer

    def test_3d_view_has_linked_color_legend(self):
        import numpy as np
        from kipida.ui.results_view import RailResultsWidget
        view = RailResultsWidget(self._rail_result(), {}, drop_pct=5.0)
        v3d = view.view_3d
        if not v3d.planes:
            self.skipTest("3D view needs PyOpenGL")
        self.assertIsNotNone(v3d.color_bar)

        # Max Drop % moves the 3D legend like the 2D ones
        view.set_drop_pct(10.0)
        lo, hi = v3d.color_bar.levels()
        self.assertAlmostEqual(lo, 3.3 * 0.9, places=6)

        # Dragging the legend handles (levels change + signal) re-colours the planes
        item, _g = next(iter(v3d.planes.values()))
        before = np.array(item.data, copy=True)
        v3d.color_bar.setLevels(values=(3.29, 3.30))
        v3d.color_bar.sigLevelsChanged.emit(v3d.color_bar)
        self.assertFalse(np.array_equal(before, item.data))

    def test_no_outline_is_fine(self):
        from kipida.ui.results_view import RailResultsWidget
        view = RailResultsWidget(self._rail_result(), {}, drop_pct=5.0, board_outline=[])
        self.assertIsNone(view.layer_plots[0].outline_item)

    def test_ground_rail_uses_its_actual_range(self):
        from kipida.ui.results_view import drop_levels
        # GND: max 0 V, min -1.1 mV. "5 % of 0 V" is no range at all; use the data range.
        self.assertEqual(drop_levels(0.0, 5.0, v_min=-0.0011), (-0.0011, 0.0))
        # Normal rails keep the Max Drop % behaviour
        lo, hi = drop_levels(24.0, 0.1, v_min=23.99)
        self.assertAlmostEqual(lo, 23.976, places=9)
        self.assertEqual(hi, 24.0)

    def test_zero_drop_rail_does_not_break_levels(self):
        from kipida.ui.results_view import drop_levels
        lo, hi = drop_levels(3.3, 0.0)
        self.assertLess(lo, hi)


def _two_rail_result():
    """SystemResult with rails +5V and +3V3 on F.Cu/B.Cu, plus a board outline."""
    import numpy as np
    from kipida.simulation.mesh import Mesh
    from kipida.simulation.system import RailResult, SystemResult
    result = SystemResult(stackup={'copper': {0: {'name': 'F.Cu'}, 2: {'name': 'B.Cu'}}, 'layer_order': [0, 2]})
    for name, v in (("+5V", 5.0), ("+3V3", 3.3)):
        m = Mesh()
        m.grid_origin, m.grid_step = (10.0, 10.0), 1.0
        res = {}
        for nid, (xi, yi, lid) in enumerate([(0, 0, 0), (5, 3, 0), (0, 0, 2), (5, 3, 2)]):
            m.nodes.append(nid)
            m.node_map[(xi, yi, lid)] = nid
            m.node_coords[nid] = (10.0 + xi, 10.0 + yi, lid)
            res[nid] = v - 0.01 * nid
        result.rails[name] = RailResult(name, m, res, min(res.values()), max(res.values()))
    result.board_outline = [np.array([(0, 0), (40, 0), (40, 25), (0, 25), (0, 0)], float)]
    return result


class TestShortcuts(unittest.TestCase):
    def setUp(self):
        from PySide6.QtTest import QTest
        from kipida.ui.main_window import MainWindow
        self.QTest = QTest
        self.win = MainWindow(board_adapter=None, project=None)
        self.win.system_result = _two_rail_result()
        self.win._update_results_ui()  # switches to the Results tab
        self.win.show()
        self.win.activateWindow()
        QTest.qWaitForWindowExposed(self.win)

    def tearDown(self):
        self.win.close()

    def press(self, key):
        self.QTest.keyClick(self.win.results_tabs, key)
        app.processEvents()

    def rail_and_view(self):
        rail = self.win._current_rail_view()
        return self.win.results_tabs.currentIndex(), rail.tabText(rail.currentIndex())

    def test_tabs_numbered(self):
        tabs = self.win.results_tabs
        self.assertEqual([tabs.tabText(i) for i in range(tabs.count())], ["1: +5V", "2: +3V3"])

    def test_arrows_step_views_and_numbers_pick_rail(self):
        self.assertEqual(self.rail_and_view(), (0, "3D View"))
        self.press(Qt.Key.Key_Left)                    # already the first view: ignored
        self.assertEqual(self.rail_and_view(), (0, "3D View"))
        self.press(Qt.Key.Key_Right)
        self.assertEqual(self.rail_and_view(), (0, "F.Cu"))
        self.press(Qt.Key.Key_2)                       # same view kept on the new rail
        self.assertEqual(self.rail_and_view(), (1, "F.Cu"))
        self.press(Qt.Key.Key_Right)
        self.assertEqual(self.rail_and_view(), (1, "B.Cu"))
        self.press(Qt.Key.Key_Right)                   # last view: ignored, no wrap-around
        self.assertEqual(self.rail_and_view(), (1, "B.Cu"))
        self.press(Qt.Key.Key_Left)
        self.assertEqual(self.rail_and_view(), (1, "F.Cu"))
        self.press(Qt.Key.Key_9)                       # no rail 9: nothing happens
        self.assertEqual(self.rail_and_view(), (1, "F.Cu"))

    def test_fit_2d_shows_whole_board(self):
        self.press(Qt.Key.Key_Right)                   # F.Cu map
        vb = self.win._current_rail_view().currentWidget().plot.getPlotItem().vb
        vb.setRange(xRange=(18, 19), yRange=(11, 12), padding=0)   # zoomed far in
        self.press(Qt.Key.Key_C)
        (x0, x1), (y0, y1) = vb.viewRange()
        # Whole 40 x 25 mm board visible ...
        self.assertTrue(x0 <= 0 and x1 >= 40 and y0 <= 0 and y1 >= 25, vb.viewRange())
        # ... and as large as possible: with the aspect ratio locked, the limiting
        # axis is tight (only the 2 % padding on each side)
        tight_x = (x1 - x0) <= 40 * 1.05
        tight_y = (y1 - y0) <= 25 * 1.05
        self.assertTrue(tight_x or tight_y, vb.viewRange())

    def test_full_screen_toggle_and_escape(self):
        self.press(Qt.Key.Key_F)
        self.assertTrue(self.win.isFullScreen())
        self.press(Qt.Key.Key_F)
        self.assertFalse(self.win.isFullScreen())
        self.press(Qt.Key.Key_F)
        self.press(Qt.Key.Key_Escape)
        self.assertFalse(self.win.isFullScreen())

    def test_keys_inactive_outside_results_tab(self):
        self.win.tabs.setCurrentIndex(self.win.TAB_CONFIG)
        app.processEvents()
        self.press(Qt.Key.Key_2)
        self.press(Qt.Key.Key_F)
        self.assertEqual(self.win.results_tabs.currentIndex(), 0)
        self.assertFalse(self.win.isFullScreen())


class TestFit3D(unittest.TestCase):
    def test_board_fills_view_and_stays_inside(self):
        from PySide6.QtGui import QVector4D
        from kipida.ui.results_view import RailResultsWidget
        result = _two_rail_result()
        view = RailResultsWidget(result.rails["+5V"], result.stackup, 5.0, board_outline=result.board_outline)
        v3d = view.view_3d
        if v3d.color_bar is None:
            self.skipTest("3D view needs PyOpenGL")
        view.resize(800, 500)
        view.show()
        app.processEvents()
        v3d.view.setCameraPosition(distance=1e4)       # far away: board tiny
        v3d.fit_view()
        w, h = v3d.view.width(), v3d.view.height()
        mvp = v3d.view.projectionMatrix((0, 0, w, h), (0, 0, w, h)) * v3d.view.viewMatrix()
        (x0, x1), (y0, y1), (z0, z1) = v3d._bbox
        ndc = []
        for x in (x0, x1):
            for y in (y0, y1):
                for z in (z0, z1):
                    p = mvp.map(QVector4D(x, y, z, 1.0))
                    ndc.append(max(abs(p.x() / p.w()), abs(p.y() / p.w())))
        self.assertLessEqual(max(ndc), 1.0)            # everything visible
        self.assertGreater(max(ndc), 0.9)              # and as large as possible
        view.close()


def _layered_result():
    """F.Cu (id 0) copper only at cell (0,0); B.Cu (id 2) at (0,0) and (1,0). 1 mm grid."""
    from kipida.simulation.mesh import Mesh
    from kipida.simulation.system import RailResult
    m = Mesh()
    m.grid_origin, m.grid_step = (0.0, 0.0), 1.0
    volts = {(0, 0, 0): 3.30, (0, 0, 2): 3.28, (1, 0, 2): 3.25}
    res = {}
    for nid, ((xi, yi, lid), v) in enumerate(volts.items()):
        m.nodes.append(nid)
        m.node_map[(xi, yi, lid)] = nid
        m.node_coords[nid] = (float(xi), float(yi), lid)
        res[nid] = v
    return RailResult("+3V3", m, res, min(res.values()), max(res.values()))


STACKUP_2L = {'copper': {0: {'name': 'F.Cu'}, 2: {'name': 'B.Cu'}}, 'layer_order': [0, 2]}


class TestCursorReadout(unittest.TestCase):
    def test_2d_label_follows_cursor_over_copper_only(self):
        from PySide6.QtCore import QPointF
        from kipida.ui.results_view import RailResultsWidget
        view = RailResultsWidget(_layered_result(), STACKUP_2L, 5.0)
        view.resize(700, 500)
        view.show()
        view.setCurrentIndex(2)  # B.Cu map
        app.processEvents()
        plot = view.layer_plots[1]
        vb = plot.plot.getPlotItem().vb
        plot._on_mouse_moved(vb.mapViewToScene(QPointF(1.0, 0.0)))
        self.assertTrue(plot.cursor_label.isVisible())
        self.assertEqual(plot.cursor_label.textItem.toPlainText(), "3.2500 V")
        pos = plot.cursor_label.pos()
        self.assertAlmostEqual(pos.x(), 1.0, places=9)
        self.assertAlmostEqual(pos.y(), 0.0, places=9)
        plot._on_mouse_moved(vb.mapViewToScene(QPointF(1.0, 5.0)))  # no copper there
        self.assertFalse(plot.cursor_label.isVisible())
        view.close()

    def test_3d_pick_returns_visible_layer(self):
        from PySide6.QtGui import QVector4D
        from kipida.ui.results_view import RailResultsWidget
        view = RailResultsWidget(_layered_result(), STACKUP_2L, 5.0)
        v3d = view.view_3d
        if v3d.color_bar is None:
            self.skipTest("3D view needs PyOpenGL")
        view.resize(700, 500)
        view.show()
        app.processEvents()
        v3d.view.setCameraPosition(elevation=90, azimuth=-90)  # looking straight down
        v3d.fit_view()

        def to_pixel(x_mm, y_mm, lid):
            w, h = v3d.view.width(), v3d.view.height()
            mvp = v3d.view.projectionMatrix((0, 0, w, h), (0, 0, w, h)) * v3d.view.viewMatrix()
            p = mvp.map(QVector4D(x_mm, -y_mm, v3d._layer_z[lid], 1.0))
            return (p.x() / p.w() + 1) * w / 2, (1 - p.y() / p.w()) * h / 2

        # Cell (0,0): both layers have copper -> the top one (F.Cu) is what you see
        lid, v, x, y = v3d.pick(*to_pixel(0.0, 0.0, 0))
        self.assertEqual((lid, round(v, 4)), (0, 3.30))
        self.assertAlmostEqual(x, 0.0, delta=0.5)
        # Cell (1,0): F.Cu has no copper there -> the ray reaches B.Cu
        lid, v, _x, _y = v3d.pick(*to_pixel(1.0, 0.0, 2))
        self.assertEqual((lid, round(v, 4)), (2, 3.25))
        # Off the board: nothing
        self.assertIsNone(v3d.pick(*to_pixel(30.0, 30.0, 0)))
        view.close()


class TestEstimateLabel(unittest.TestCase):
    def test_label_before_and_after_calibration(self):
        from kipida.simulation.estimate import Calibration
        from kipida.ui.main_window import MainWindow
        win = MainWindow(board_adapter=None, project=None)
        try:
            self.assertIn("run once to calibrate", win.lbl_estimate.text())
            win.calibration = Calibration(grid_mm=0.1, rails={"A": (200_000, {"geometry": 0.2, "mesh": 0.4,
                                                                               "setup": 0.2, "solve": 1.0})},
                                          plots_s=1.0, overhead_s=2.0, wall_s=4.8)
            win.spin_grid_size.setValue(0.1)
            win._update_estimate()
            self.assertEqual(win.lbl_estimate.text(), "Estimated: ~5 s")
            # 16x the nodes: 2.0 overhead + 11.5 plots + 0.4 fixed + 4.9 mesh + 14.2 solve = 33.0 s
            win.spin_grid_size.setValue(0.025)   # label follows the spin box
            self.assertEqual(win.lbl_estimate.text(), "Estimated: ~35 s")
        finally:
            win.close()


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
