
import unittest


class TestPluginStructure(unittest.TestCase):
    def test_imports(self):
        """test that the main modules can be imported"""
        try:
            from kipida.core import models, config_manager
            from kipida.kicad import discovery, extractor
            from kipida.simulation import mesh, solver
        except ImportError as e:
            self.fail(f"Failed to import Core Modules: {e}")

    def test_ui_import(self):
        """test that the UI modules can be imported (requires wx)"""
        try:
            import wx
            import wx.dataview
        except ImportError:
            self.skipTest("wxPython not available")

        try:
            from kipida.ui import main_dialog
            from kipida import app
        except ImportError as e:
            self.fail(f"Failed to import UI Modules: {e}")

if __name__ == '__main__':
    unittest.main()
