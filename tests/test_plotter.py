
import unittest

from kipida.simulation.mesh import Mesh
from kipida.visualization.plotter import Plotter

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class TestPlotter(unittest.TestCase):
    def setUp(self):
        self.plotter = Plotter(debug=True)
        self.mesh = Mesh()
        # Nodes 0,1 on layer 0; nodes 2,3 on layer 1
        self.mesh.nodes = [0, 1, 2, 3]
        self.mesh.node_coords = {
            0: (0.0, 0.0, 0),
            1: (1.0, 1.0, 0),
            2: (0.0, 0.0, 1),
            3: (1.0, 1.0, 1)
        }
        self.mesh.results = {0: 3.3, 1: 3.2, 2: 3.3, 3: 3.1}
        self.stackup = {
            'copper': {0: {}, 1: {}},
            'layer_order': [0, 1]
        }

    def test_plot_3d(self):
        png = self.plotter.plot_3d_mesh(self.mesh, self.stackup)
        self.assertIsInstance(png, bytes)
        self.assertTrue(png.startswith(PNG_SIGNATURE))

    def test_plot_2d_layer(self):
        png = self.plotter.plot_layer_2d(self.mesh, 0, self.stackup, vmin=3.0, vmax=3.5, layer_name="F.Cu (Test)")
        self.assertIsInstance(png, bytes)
        self.assertTrue(png.startswith(PNG_SIGNATURE))

    def test_plot_2d_empty_layer(self):
        self.assertIsNone(self.plotter.plot_layer_2d(self.mesh, 99, self.stackup))


if __name__ == '__main__':
    unittest.main()
