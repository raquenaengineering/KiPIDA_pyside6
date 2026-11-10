import unittest

import numpy as np

from kipida.simulation.mesh import Mesh
from kipida.visualization.layer_grid import mesh_to_layer_grids


def _mesh():
    """2 layers on a 0.5 mm grid, origin (10, 20). Layer 0: 3 nodes, layer 31: 1 node."""
    m = Mesh()
    m.grid_origin, m.grid_step = (10.0, 20.0), 0.5
    for nid, (xi, yi, lid) in enumerate([(0, 0, 0), (1, 0, 0), (2, 1, 0), (2, 1, 31)]):
        m.nodes.append(nid)
        m.node_map[(xi, yi, lid)] = nid
        m.node_coords[nid] = (10.0 + xi * 0.5, 20.0 + yi * 0.5, lid)
    return m


RESULTS = {0: 3.30, 1: 3.28, 2: 3.25, 3: 3.24}


class TestLayerGrid(unittest.TestCase):
    def test_grids_per_layer_share_shape(self):
        grids = mesh_to_layer_grids(_mesh(), RESULTS)
        self.assertEqual(sorted(grids), [0, 31])
        self.assertEqual(grids[0].values.shape, (3, 2))
        self.assertEqual(grids[31].values.shape, (3, 2))

    def test_values_placed_and_gaps_are_nan(self):
        g = mesh_to_layer_grids(_mesh(), RESULTS)[0]
        self.assertAlmostEqual(g.values[0, 0], 3.30, places=5)
        self.assertAlmostEqual(g.values[2, 1], 3.25, places=5)
        self.assertTrue(np.isnan(g.values[0, 1]))  # no copper there
        self.assertEqual(int(np.count_nonzero(~np.isnan(g.values))), 3)

    def test_value_at_uses_mesh_coordinates(self):
        g = mesh_to_layer_grids(_mesh(), RESULTS)[0]
        self.assertAlmostEqual(g.value_at(10.5, 20.0), 3.28, places=5)
        self.assertAlmostEqual(g.value_at(10.6, 20.1), 3.28, places=5)   # nearest cell
        self.assertIsNone(g.value_at(10.0, 20.5))                         # gap
        self.assertIsNone(g.value_at(-50, -50))                           # outside

    def test_rect_covers_cells_edge_to_edge(self):
        g = mesh_to_layer_grids(_mesh(), RESULTS)[0]
        self.assertEqual(g.rect, (9.75, 19.75, 1.5, 1.0))
        self.assertEqual(g.v_range, (np.float32(3.25).item(), np.float32(3.30).item()))

    def test_empty(self):
        self.assertEqual(mesh_to_layer_grids(Mesh(), {}), {})


if __name__ == "__main__":
    unittest.main()
