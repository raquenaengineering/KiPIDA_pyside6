"""Board outline (Edge.Cuts) extraction, with kipy-style fake shapes (nm units)."""
import math
import unittest

import numpy as np

from kipida.kicad.outline import EDGE_CUTS_LAYER, arc_points, extract_board_outline, shape_to_polylines

MM = 1_000_000  # nm per mm
F_CU = 3


class V:  # kipy Vector2
    def __init__(self, x_mm, y_mm):
        self.x, self.y = x_mm * MM, y_mm * MM


class Segment:
    def __init__(self, a, b, layer=EDGE_CUTS_LAYER):
        self.start, self.end, self.layer = V(*a), V(*b), layer


class Arc:
    def __init__(self, a, m, b, layer=EDGE_CUTS_LAYER):
        self.start, self.mid, self.end, self.layer = V(*a), V(*m), V(*b), layer

    def center(self):  # real kipy arcs have this method too; must not be mistaken for a circle
        return None


class Circle:
    def __init__(self, c, r, layer=EDGE_CUTS_LAYER):
        self.center, self.radius_point, self.layer = V(*c), V(c[0] + r, c[1]), layer


class Rect:
    def __init__(self, tl, br, layer=EDGE_CUTS_LAYER):
        self.top_left, self.bottom_right, self.layer = V(*tl), V(*br), layer


class Bezier:
    def __init__(self, a, c1, c2, b, layer=EDGE_CUTS_LAYER):
        self.start, self.control1, self.control2, self.end, self.layer = V(*a), V(*c1), V(*c2), V(*b), layer


class Node:
    def __init__(self, point=None, arc=None):
        self.has_arc = arc is not None
        self.has_point = point is not None
        self.point = V(*point) if point else None
        self.arc = arc


class ArcSME:
    def __init__(self, a, m, b):
        self.start, self.mid, self.end = V(*a), V(*m), V(*b)


class PolyLine:
    def __init__(self, nodes, closed=True):
        self.nodes, self.closed = nodes, closed


class PolyWithHoles:
    def __init__(self, outline, holes=()):
        self.outline, self.holes = outline, list(holes)


class Polygon:
    def __init__(self, polys, layer=EDGE_CUTS_LAYER):
        self.polygons, self.layer = polys, layer


class FakeBoard:
    def __init__(self, shapes, fail=False):
        self._shapes, self._fail = shapes, fail

    def get_shapes(self):
        if self._fail:
            raise RuntimeError("API timeout")
        return self._shapes


def _radii(pts, center):
    return np.hypot(pts[:, 0] - center[0], pts[:, 1] - center[1])


class TestArcPoints(unittest.TestCase):
    def test_follows_mid_point_both_directions(self):
        # Same end points, mid on opposite sides -> opposite half circles
        upper = arc_points((-1, 0), (0, -1), (1, 0))
        lower = arc_points((-1, 0), (0, 1), (1, 0))
        np.testing.assert_allclose(_radii(upper, (0, 0)), 1.0, atol=1e-9)
        np.testing.assert_allclose(_radii(lower, (0, 0)), 1.0, atol=1e-9)
        self.assertTrue(np.all(upper[:, 1] <= 1e-9))
        self.assertTrue(np.all(lower[:, 1] >= -1e-9))
        self.assertEqual(tuple(upper[0]), (-1, 0))
        self.assertEqual(tuple(upper[-1]), (1, 0))

    def test_large_arc_through_mid(self):
        # 270 deg arc: start (1,0), mid (-1,0)... end (0,-1) going through the left side
        pts = arc_points((1, 0), (-1, 0), (0, -1))
        np.testing.assert_allclose(_radii(pts, (0, 0)), 1.0, atol=1e-9)
        self.assertTrue(np.any(pts[:, 1] > 0.99))  # passes through (0, 1)

    def test_collinear_falls_back_to_lines(self):
        self.assertEqual(arc_points((0, 0), (1, 0), (2, 0)).shape, (3, 2))


class TestShapes(unittest.TestCase):
    def test_segment_rect_circle_bezier(self):
        (seg,) = shape_to_polylines(Segment((0, 0), (10, 0)))
        np.testing.assert_allclose(seg, [(0, 0), (10, 0)])

        (rect,) = shape_to_polylines(Rect((0, 0), (50, 30)))
        self.assertEqual(len(rect), 5)
        np.testing.assert_allclose(rect[0], rect[-1])  # closed

        (circ,) = shape_to_polylines(Circle((5, 5), 2))
        np.testing.assert_allclose(_radii(circ, (5, 5)), 2.0, atol=1e-9)

        (bez,) = shape_to_polylines(Bezier((0, 0), (0, 10), (10, 10), (10, 0)))
        np.testing.assert_allclose(bez[0], (0, 0))
        np.testing.assert_allclose(bez[-1], (10, 0))

    def test_arc_not_mistaken_for_circle(self):
        (arc,) = shape_to_polylines(Arc((10, 0), (0, 10), (-10, 0)))
        np.testing.assert_allclose(_radii(arc, (0, 0)), 10.0, atol=1e-6)

    def test_polygon_with_arc_node_and_hole(self):
        outline = PolyLine([Node(point=(0, 0)), Node(point=(20, 0)),
                            Node(arc=ArcSME((20, 10), (25, 15), (20, 20))), Node(point=(0, 20))])
        hole = PolyLine([Node(point=(5, 5)), Node(point=(8, 5)), Node(point=(8, 8))])
        loops = shape_to_polylines(Polygon([PolyWithHoles(outline, [hole])]))
        self.assertEqual(len(loops), 2)
        self.assertGreater(len(loops[0]), 6)            # arc flattened into several points
        np.testing.assert_allclose(loops[0][0], loops[0][-1])  # closed
        self.assertAlmostEqual(loops[0][:, 0].max(), 25.0, delta=0.05)


class TestExtract(unittest.TestCase):
    def test_only_edge_cuts(self):
        board = FakeBoard([Rect((0, 0), (50, 30)), Segment((0, 0), (5, 5), layer=F_CU)])
        outline = extract_board_outline(board)
        self.assertEqual(len(outline), 1)
        self.assertEqual(outline[0].shape, (5, 2))

    def test_api_failure_is_not_fatal(self):
        logs = []
        self.assertEqual(extract_board_outline(FakeBoard([], fail=True), log=logs.append), [])
        self.assertIn("API timeout", logs[0])
        self.assertEqual(extract_board_outline(None), [])


if __name__ == "__main__":
    unittest.main()
