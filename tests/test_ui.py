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
