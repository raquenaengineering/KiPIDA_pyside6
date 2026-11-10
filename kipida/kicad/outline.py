"""Board outline (Edge.Cuts) extraction from the KiCad IPC API.

Returns the outline as a list of polylines, each an (N, 2) float array in mm
(KiCad coordinates, y down). Curves (arcs, circles, beziers, arc segments in
polygons) are flattened into short straight segments; this is only used as a
visual reference, not for the electrical model.
"""
import math
from typing import Callable, List, Optional

import numpy as np

try:
    from kipy.proto.board.board_types_pb2 import BoardLayer
    EDGE_CUTS_LAYER = BoardLayer.BL_Edge_Cuts
except Exception:  # kipy unavailable (tests) – value from the KiCad 9/10 protobuf API
    EDGE_CUTS_LAYER = 47

NM_PER_MM = 1e6
_CIRCLE_SEGMENTS = 72
_BEZIER_SEGMENTS = 24


def _xy(v):
    """kipy Vector2 (nm) -> (x, y) mm."""
    return (v.x / NM_PER_MM, v.y / NM_PER_MM)


def arc_points(p0, pm, p1, max_step_deg: float = 5.0) -> np.ndarray:
    """Points along the circular arc from p0 through pm to p1 (all (x, y) tuples).

    Falls back to the straight polyline p0-pm-p1 if the points are collinear.
    """
    (x1, y1), (x2, y2), (x3, y3) = p0, pm, p1
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12:
        return np.array([p0, pm, p1], dtype=float)
    ux = ((x1**2 + y1**2) * (y2 - y3) + (x2**2 + y2**2) * (y3 - y1) + (x3**2 + y3**2) * (y1 - y2)) / d
    uy = ((x1**2 + y1**2) * (x3 - x2) + (x2**2 + y2**2) * (x1 - x3) + (x3**2 + y3**2) * (x2 - x1)) / d
    r = math.hypot(x1 - ux, y1 - uy)

    a0 = math.atan2(y1 - uy, x1 - ux)
    am = math.atan2(y2 - uy, x2 - ux)
    a1 = math.atan2(y3 - uy, x3 - ux)
    # Sweep counter-clockwise from a0 to a1; if the mid point is not on that
    # sweep, the arc goes clockwise instead.
    ccw = (a1 - a0) % (2 * math.pi)
    mid = (am - a0) % (2 * math.pi)
    sweep = ccw if mid <= ccw else ccw - 2 * math.pi

    n = max(2, int(math.ceil(abs(math.degrees(sweep)) / max_step_deg)))
    angles = a0 + sweep * np.linspace(0.0, 1.0, n + 1)
    pts = np.column_stack([ux + r * np.cos(angles), uy + r * np.sin(angles)])
    pts[0], pts[-1] = p0, p1  # exact end points so pieces join up
    return pts


def _bezier_points(p0, c1, c2, p1) -> np.ndarray:
    t = np.linspace(0.0, 1.0, _BEZIER_SEGMENTS + 1)[:, None]
    p0, c1, c2, p1 = (np.asarray(p, dtype=float) for p in (p0, c1, c2, p1))
    return (1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * c1 + 3 * (1 - t) * t ** 2 * c2 + t ** 3 * p1


def _polyline_points(polyline) -> np.ndarray:
    """kipy PolyLine (points and arc nodes) -> closed (N, 2) array."""
    parts = []
    for node in polyline.nodes:
        if getattr(node, "has_arc", False):
            arc = node.arc
            parts.append(arc_points(_xy(arc.start), _xy(arc.mid), _xy(arc.end)))
        else:
            parts.append(np.array([_xy(node.point)], dtype=float))
    if not parts:
        return np.empty((0, 2))
    pts = np.concatenate(parts)
    if getattr(polyline, "closed", True) and len(pts) > 1:
        pts = np.vstack([pts, pts[:1]])
    return pts


def shape_to_polylines(shape) -> List[np.ndarray]:
    """One kipy board shape -> list of (N, 2) mm polylines (empty if unsupported)."""
    if hasattr(shape, "top_left") and hasattr(shape, "bottom_right"):          # rectangle
        (x0, y0), (x1, y1) = _xy(shape.top_left), _xy(shape.bottom_right)
        return [np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)], dtype=float)]
    if hasattr(shape, "radius_point") and hasattr(shape, "center"):            # circle
        (cx, cy), (rx, ry) = _xy(shape.center), _xy(shape.radius_point)
        r = math.hypot(rx - cx, ry - cy)
        a = np.linspace(0.0, 2 * math.pi, _CIRCLE_SEGMENTS + 1)
        return [np.column_stack([cx + r * np.cos(a), cy + r * np.sin(a)])]
    if hasattr(shape, "polygons"):                                             # polygon(s) with holes
        out = []
        for poly in shape.polygons:
            out.append(_polyline_points(poly.outline))
            out.extend(_polyline_points(h) for h in poly.holes)
        return [p for p in out if len(p) > 1]
    if hasattr(shape, "control1") and hasattr(shape, "control2"):              # cubic bezier
        return [_bezier_points(_xy(shape.start), _xy(shape.control1), _xy(shape.control2), _xy(shape.end))]
    if hasattr(shape, "mid"):                                                  # arc
        return [arc_points(_xy(shape.start), _xy(shape.mid), _xy(shape.end))]
    if hasattr(shape, "start") and hasattr(shape, "end"):                      # segment
        return [np.array([_xy(shape.start), _xy(shape.end)], dtype=float)]
    return []


def extract_board_outline(board, log: Optional[Callable[[str], None]] = None) -> List[np.ndarray]:
    """All Edge.Cuts shapes of the board as polylines in mm. Never raises."""
    if board is None or not hasattr(board, "get_shapes"):
        return []
    try:
        shapes = board.get_shapes()
    except Exception as e:
        if log:
            log(f"  Board outline unavailable: {e}")
        return []

    polylines = []
    for shape in shapes:
        if getattr(shape, "layer", None) != EDGE_CUTS_LAYER:
            continue
        try:
            polylines.extend(shape_to_polylines(shape))
        except Exception as e:  # one odd shape must not hide the rest
            if log:
                log(f"  Skipping Edge.Cuts shape {type(shape).__name__}: {e}")
    return polylines
