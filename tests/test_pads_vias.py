"""Pad / via geometry and padstack reading with kipy-shaped fakes.

Regression tests for bugs found on a real KiCad 10 board:
- pad angles are kipy Angle objects; float(Angle) fails and rotation was lost
  (a 90 deg rotated 1.25 x 2.65 mm inductor pad came out 1.25 wide instead of 2.65,
  leaving the +5V track unconnected and a floating copper island at 0 V);
- kipy circle pads are shape code 1 (code 0 means 'unknown');
- vias have no 'width' and pads no 'drill_size' in kipy: sizes live in the padstack,
  so via/PTH barrel resistance used made-up defaults.
"""
import math
import unittest

from shapely.geometry import box

from kipida.kicad.extractor import GeometryExtractor, TRACK_SAFETY_BUFFER_MM
from kipida.kicad.padstack import angle_degrees, copper_diameter_mm, drill_diameter_mm
from kipida.simulation.mesh import Mesher
from kipida.simulation.solver import Solver

MM = 1_000_000
F_CU, B_CU = 3, 34  # KiCad 9/10 protobuf layer ids


class Angle:  # kipy.geometry.Angle: float(Angle) raises TypeError
    def __init__(self, deg):
        self._deg = deg

    @property
    def degrees(self):
        return self._deg

    def __float__(self):
        raise TypeError("float() argument must be a string or a real number, not 'Angle'")


class V:
    def __init__(self, x_mm, y_mm):
        self.x, self.y = int(round(x_mm * MM)), int(round(y_mm * MM))


class Net:
    def __init__(self, name):
        self.name = name


class CopperLayer:
    def __init__(self, w, h, shape):
        self.size, self.shape = V(w, h), shape


class Drill:
    def __init__(self, d):
        self.diameter = V(d, d)


class Padstack:
    def __init__(self, w, h, shape=5, angle=0.0, layers=(F_CU,), drill=None):
        self.copper_layers = [CopperLayer(w, h, shape)]
        self.angle = Angle(angle)
        self.layers = list(layers)
        self.drill = Drill(drill) if drill else None


class Pad:
    def __init__(self, net, x, y, w, h, shape=5, angle=0.0, layers=(F_CU,), drill=None, number="1", pad_type=2):
        self.net, self.position, self.number, self.pad_type = Net(net), V(x, y), number, pad_type
        self.padstack = Padstack(w, h, shape, angle, layers, drill)


class Footprint:
    def __init__(self, pads):
        class Definition:
            pass
        self.definition = Definition()
        self.definition.pads = pads


class Via:
    def __init__(self, net, x, y, pad=0.5, drill=0.3, layers=(F_CU, B_CU)):
        self.net, self.position = Net(net), V(x, y)
        self.padstack = Padstack(pad, pad, shape=1, layers=layers, drill=drill)


class Board:
    def __init__(self, footprints=(), vias=()):
        self.footprints, self.vias, self.tracks, self.zones = list(footprints), list(vias), [], []


STACKUP = {
    'copper': {F_CU: {'name': 'F.Cu', 'thickness_mm': 0.035}, B_CU: {'name': 'B.Cu', 'thickness_mm': 0.035}},
    'layer_order': [F_CU, B_CU],
    'substrate': [{'thickness_mm': 1.5, 'between': [F_CU, B_CU]}],
    'resistivity': 1.68e-5,
}


def _extractor(board):
    ex = GeometryExtractor(board)
    ex._stackup_cache = STACKUP
    return ex


class TestPadstackHelpers(unittest.TestCase):
    def test_angle_from_kipy_angle_number_and_none(self):
        self.assertEqual(angle_degrees(Angle(90)), 90.0)
        self.assertEqual(angle_degrees(45), 45.0)
        self.assertIsNone(angle_degrees(None))

    def test_via_sizes_from_padstack(self):
        via = Via("+5V", 0, 0, pad=0.5, drill=0.3)
        self.assertAlmostEqual(copper_diameter_mm(via), 0.5)
        self.assertAlmostEqual(drill_diameter_mm(via), 0.3)

    def test_missing_data_uses_default(self):
        class Bare:
            pass
        self.assertEqual(copper_diameter_mm(Bare(), default=0.6), 0.6)
        self.assertIsNone(drill_diameter_mm(Bare()))


class TestPadShapes(unittest.TestCase):
    """Approximate (fallback) pad outlines: board without get_pad_shapes_as_polygons."""

    def _pad_geom(self, pad):
        geo = _extractor(Board([Footprint([pad])])).get_net_geometry("+5V")
        return geo[F_CU]

    def test_rotated_rectangular_pad(self):
        # L1 pad 2 on the real board: 1.25 x 2.65 mm, rotated 90 deg -> 2.65 wide, 1.25 tall
        g = self._pad_geom(Pad("+5V", 37.35, 49.918, 1.25, 2.65, shape=5, angle=90))
        x0, y0, x1, y1 = g.bounds
        self.assertAlmostEqual(x1 - x0, 2.65, places=6)
        self.assertAlmostEqual(y1 - y0, 1.25, places=6)

    def test_45_degree_pad_area_preserved(self):
        g = self._pad_geom(Pad("+5V", 0, 0, 1.0, 3.0, shape=2, angle=45))
        self.assertAlmostEqual(g.area, 3.0, places=6)
        x0, _y0, x1, _y1 = g.bounds
        self.assertAlmostEqual(x1 - x0, (1.0 + 3.0) / math.sqrt(2), places=6)

    def test_circle_is_kipy_code_1(self):
        g = self._pad_geom(Pad("+5V", 0, 0, 2.0, 2.0, shape=1))
        self.assertAlmostEqual(g.area, math.pi, delta=0.02)  # circle, not a 4 mm^2 square

    def test_oval_is_a_stadium(self):
        g = self._pad_geom(Pad("+5V", 0, 0, 3.0, 1.0, shape=3))
        self.assertAlmostEqual(g.area, 2.0 * 1.0 + math.pi * 0.25, delta=0.02)


class TestExactPadShapes(unittest.TestCase):
    def test_kicad_polygons_preferred_and_fallback_on_error(self):
        from kipida.kicad import extractor as ex_mod

        class PolyLine:
            def __init__(self, pts):
                class Node:
                    def __init__(self, x, y):
                        self.has_arc, self.point = False, V(x, y)
                self.nodes, self.closed = [Node(*p) for p in pts], True

        class PolyWithHoles:
            def __init__(self, pts):
                self.outline, self.holes = PolyLine(pts), []

        pad = Pad("+5V", 0, 0, 1.0, 1.0, shape=2)
        exact = PolyWithHoles([(-2, -1), (2, -1), (2, 1), (-2, 1)])  # what "KiCad" says: 4 x 2 mm

        board = Board([Footprint([pad])])
        calls = []
        board.get_pad_shapes_as_polygons = lambda pads, layer: (calls.append(layer), [exact] * len(pads))[1]
        g = _extractor(board).get_net_geometry("+5V")[F_CU]
        self.assertEqual(calls, [F_CU])
        self.assertAlmostEqual(g.area, 8.0, places=6)

        def broken(pads, layer):
            raise RuntimeError("API not available")
        board.get_pad_shapes_as_polygons = broken
        g = _extractor(board).get_net_geometry("+5V")[F_CU]
        self.assertAlmostEqual(g.area, 1.0, places=6)  # fallback: 1 x 1 mm from the padstack


class TestVias(unittest.TestCase):
    def test_via_copper_uses_padstack_diameter(self):
        geo = _extractor(Board(vias=[Via("+5V", 0, 0, pad=0.5)])).get_net_geometry("+5V")
        r = 0.25 + TRACK_SAFETY_BUFFER_MM
        self.assertAlmostEqual(geo[F_CU].area, math.pi * r * r, delta=0.01)

    def test_via_barrel_resistance_uses_drill(self):
        square = box(-1, -1, 1, 1)
        board = Board(vias=[Via("+5V", 0, 0, pad=0.5, drill=0.3)])
        mesh = Mesher(board).generate_mesh("+5V", {F_CU: square, B_CU: square}, STACKUP, grid_size_mm=0.5)
        vertical = [g for u, v, g in zip(mesh.G_coo_row, mesh.G_coo_col, mesh.G_coo_data)
                    if u != v and mesh.node_coords[u][2] != mesh.node_coords[v][2]]
        self.assertTrue(vertical)
        t, d = 0.025, 0.3
        expected = math.pi * (d * t - t * t) / (1.68e-5 * 1.5)  # plated barrel of the 0.3 mm drill
        self.assertAlmostEqual(-vertical[0], expected, delta=expected * 1e-6)

    def test_pth_barrel_resistance_uses_drill(self):
        square = box(-2, -2, 2, 2)
        pth = Pad("+5V", 0, 0, 1.7, 1.7, shape=1, layers=(F_CU, B_CU), drill=0.95, pad_type=1)
        mesh = Mesher(Board([Footprint([pth])])).generate_mesh("+5V", {F_CU: square, B_CU: square}, STACKUP, grid_size_mm=0.5)
        vertical = [g for u, v, g in zip(mesh.G_coo_row, mesh.G_coo_col, mesh.G_coo_data)
                    if u != v and mesh.node_coords[u][2] != mesh.node_coords[v][2]]
        t, d = 0.025, 0.95
        expected = math.pi * (d * t - t * t) / (1.68e-5 * 1.5)
        self.assertAlmostEqual(-vertical[0], expected, delta=expected * 1e-6)  # not the old fixed 1000 S


class TestIslandDiagnostic(unittest.TestCase):
    def test_source_pad_interior_is_not_an_island(self):
        """A node surrounded only by source nodes used to be reported as a separate island."""
        class M:
            pass
        mesh = M()
        mesh.nodes = list(range(5))
        mesh.edges = [(i, i + 1, 1.0) for i in range(4)]  # chain 0-1-2-3-4
        logs = []
        solver = Solver(log_callback=logs.append)
        result = solver.solve(mesh, [{'node_id': n, 'voltage': 5.0} for n in (0, 1, 2)],
                              [{'node_id': 4, 'current': 1.0}])
        self.assertFalse([l for l in logs if "island" in l.lower()], logs)
        self.assertAlmostEqual(result[4], 3.0, places=6)

    def test_real_island_without_source_is_reported(self):
        class M:
            pass
        mesh = M()
        mesh.nodes = list(range(4))
        mesh.edges = [(0, 1, 1.0), (2, 3, 1.0)]  # two separate pieces
        logs = []
        Solver(log_callback=logs.append).solve(mesh, [{'node_id': 0, 'voltage': 5.0}], [])
        self.assertTrue(any("has no voltage source" in l for l in logs), logs)


if __name__ == "__main__":
    unittest.main()
