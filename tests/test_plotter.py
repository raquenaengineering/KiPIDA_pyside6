
import unittest
import sys
import types

from kipida.simulation.mesh import Mesh

# Plotter converts figures to wx bitmaps, and a real wx.Bitmap needs a running
# wx.App. Replace the plotter module's `wx` reference with a stub instead of
# overwriting sys.modules['wx'], so other tests still get the real wxPython.
_wx_stub = types.ModuleType('wx')
_wx_stub.Bitmap = lambda *args: "BITMAP_OBJECT"
_wx_stub.Image = lambda *args: "IMAGE_OBJECT"
_wx_stub.BITMAP_TYPE_PNG = 1

try:
    import wx  # noqa: F401
except ImportError:
    sys.modules['wx'] = _wx_stub  # wxPython not installed: let plotter import

from kipida.visualization import plotter as plotter_module

plotter_module.wx = _wx_stub
Plotter = plotter_module.Plotter

class TestPlotter(unittest.TestCase):
    def setUp(self):
        self.plotter = Plotter(debug=True)
        self.mesh = Mesh()
        # Create a simple dummy mesh
        # Nodes 0,1 on layer 0
        # Nodes 2,3 on layer 1
        self.mesh.nodes = [0, 1, 2, 3]
        self.mesh.node_coords = {
            0: (0.0, 0.0, 0),
            1: (1.0, 1.0, 0),
            2: (0.0, 0.0, 1),
            3: (1.0, 1.0, 1)
        }
        self.mesh.results = {
            0: 3.3,
            1: 3.2,
            2: 3.3,
            3: 3.1
        }
        self.stackup = {
            'copper': {0: {}, 1: {}},
            'layer_order': [0, 1]
        }

    def test_plot_3d(self):
        # Should return a bitmap (or mock string)
        bmp = self.plotter.plot_3d_mesh(self.mesh, self.stackup)
        print(f"3D Plot result: {bmp}")
        self.assertIsNotNone(bmp)
        
    def test_plot_2d_layer(self):
        # Should return a bitmap for layer 0
        bmp = self.plotter.plot_layer_2d(self.mesh, 0, self.stackup, vmin=3.0, vmax=3.5, layer_name="F.Cu (Test)")
        print(f"2D Plot Layer 0 result: {bmp}")
        self.assertIsNotNone(bmp)
        
    def test_plot_2d_empty_layer(self):
        # Layer 99 empty
        bmp = self.plotter.plot_layer_2d(self.mesh, 99, self.stackup)
        self.assertIsNone(bmp)

if __name__ == '__main__':
    unittest.main()
