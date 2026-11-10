"""Mesher tests using small kipy-style (IPC API) fake board objects."""
import unittest

from shapely.geometry import Polygon

from kipida.simulation.mesh import Mesher


class FakeNet:
    def __init__(self, name):
        self.name = name


class FakeVector:
    """kipy Vector2: coordinates in nanometres."""
    def __init__(self, x, y):
        self.x, self.y = x, y


class FakeVia:
    """Through via; with no layer info the mesher spans all copper layers."""
    def __init__(self, net, x, y, width):
        self.net = FakeNet(net)
        self.position = FakeVector(x, y)
        self.width = width


class FakeBoard:
    def __init__(self):
        self.vias = []
        self.footprints = []


def _cross_layer_edges(mesh):
    """Matrix entries connecting nodes on different layers."""
    return [(u, v) for u, v in zip(mesh.G_coo_row, mesh.G_coo_col)
            if u != v and mesh.node_coords[u][2] != mesh.node_coords[v][2]]


class TestMesher(unittest.TestCase):
    def setUp(self):
        self.board = FakeBoard()
        self.mesher = Mesher(self.board)

    def test_simple_rect_mesh(self):
        # 20x10 mm rect on layer 0, 5 mm grid -> 5 x 3 nodes
        geo = {0: Polygon([(0, 0), (20, 0), (20, 10), (0, 10)])}
        stackup = {'copper': {0: {'thickness_mm': 0.035}}, 'resistivity': 1.7e-5}

        mesh = self.mesher.generate_mesh("Test", geo, stackup, grid_size_mm=5.0)

        self.assertEqual(len(mesh.nodes), 15)
        self.assertTrue(len(mesh.G_coo_data) > 0)

    def test_vertical_link_via(self):
        # Same small square on F.Cu (0) and B.Cu (31), via at the origin
        square = Polygon([(-1, -1), (1, -1), (1, 1), (-1, 1)])
        geo = {0: square, 31: square}
        stackup = {'copper': {0: {}, 31: {}}, 'resistivity': 1.7e-5, 'substrate': []}

        without_via = self.mesher.generate_mesh("Test", geo, stackup, grid_size_mm=0.5)
        self.assertEqual(_cross_layer_edges(without_via), [])

        self.board.vias.append(FakeVia("Test", 0, 0, 300_000))
        with_via = self.mesher.generate_mesh("Test", geo, stackup, grid_size_mm=0.5)
        self.assertTrue(_cross_layer_edges(with_via), "via did not connect F.Cu and B.Cu")

    def test_via_on_other_net_ignored(self):
        square = Polygon([(-1, -1), (1, -1), (1, 1), (-1, 1)])
        geo = {0: square, 31: square}
        stackup = {'copper': {0: {}, 31: {}}, 'resistivity': 1.7e-5, 'substrate': []}

        self.board.vias.append(FakeVia("OtherNet", 0, 0, 300_000))
        mesh = self.mesher.generate_mesh("Test", geo, stackup, grid_size_mm=0.5)
        self.assertEqual(_cross_layer_edges(mesh), [])


if __name__ == '__main__':
    unittest.main()
