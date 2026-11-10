
import importlib.util
import pathlib
import unittest


class TestPluginStructure(unittest.TestCase):
    def test_imports(self):
        """test that the main modules can be imported"""
        try:
            from kipida.core import models, config_manager
            from kipida.kicad import discovery, extractor
            from kipida.simulation import mesh, solver, system
            from kipida.visualization import plotter
        except ImportError as e:
            self.fail(f"Failed to import Core Modules: {e}")

    def test_ui_import(self):
        """test that the UI modules can be imported (requires PySide6)"""
        try:
            from kipida.ui import main_window, power_tree_panel, component_selector, regulator_dialog
            from kipida import app
        except ImportError as e:
            self.fail(f"Failed to import UI Modules: {e}")

    def test_no_wx_left(self):
        """The plugin must not depend on wxPython (KiCad 11 ships none)."""
        root = pathlib.Path(__file__).resolve().parent.parent
        offenders = [str(p.relative_to(root)) for p in (root / "kipida").rglob("*.py")
                     if "import wx" in p.read_text(encoding="utf-8")]
        self.assertEqual(offenders, [])


if __name__ == '__main__':
    unittest.main()
