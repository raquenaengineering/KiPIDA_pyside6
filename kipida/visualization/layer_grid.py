"""Turn a solved mesh into per-layer 2D voltage grids (NumPy only, no GUI).

The mesher places nodes on a regular grid: node_map[(xi, yi, layer)] -> node_id
with coordinates x = origin_x + xi * step, y = origin_y + yi * step (mm, KiCad
orientation: y grows downwards). Each copper layer therefore maps directly to a
2D array, with NaN where there is no copper. Displaying that array as an image
is far cheaper than plotting every node as a point.
"""
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np


@dataclass
class LayerGrid:
    layer_id: int
    values: np.ndarray  # float32, shape (nx, ny), indexed [xi, yi]; NaN = no copper
    x0: float           # x of the centre of cell [0, 0] (mm)
    y0: float           # y of the centre of cell [0, 0] (mm)
    step: float         # cell size (mm)

    @property
    def rect(self):
        """(x, y, width, height) in mm covering all cells edge to edge."""
        nx, ny = self.values.shape
        return (self.x0 - self.step / 2, self.y0 - self.step / 2, nx * self.step, ny * self.step)

    def value_at(self, x_mm: float, y_mm: float) -> Optional[float]:
        """Voltage of the cell containing (x, y), or None outside copper."""
        xi = int(round((x_mm - self.x0) / self.step))
        yi = int(round((y_mm - self.y0) / self.step))
        nx, ny = self.values.shape
        if 0 <= xi < nx and 0 <= yi < ny:
            v = self.values[xi, yi]
            if not np.isnan(v):
                return float(v)
        return None

    @property
    def v_range(self):
        finite = self.values[~np.isnan(self.values)]
        return (float(finite.min()), float(finite.max())) if finite.size else (0.0, 0.0)


def mesh_to_layer_grids(mesh, results: Dict[int, float]) -> Dict[int, LayerGrid]:
    """{layer_id: LayerGrid} for every layer with mesh nodes. All grids share one shape.

    Accepts a full mesh (node_map dict + results dict), or the compact form sent
    back by the simulation process (mesh.node_keys (N, 3) and mesh.node_volts (N,)).
    """
    compact = getattr(mesh, "node_keys", None)
    if compact is not None:
        if len(compact) == 0:
            return {}
        keys = np.asarray(compact, dtype=np.int64)
        volts = np.asarray(mesh.node_volts, dtype=np.float32)
    else:
        if not mesh.node_map:
            return {}
        keys = np.array(list(mesh.node_map.keys()), dtype=np.int64)        # (N, 3): xi, yi, layer
        nids = np.fromiter(mesh.node_map.values(), dtype=np.int64, count=len(keys))

        by_nid = np.full(int(nids.max()) + 1, np.nan, dtype=np.float32)
        if results:
            r_ids = np.fromiter(results.keys(), dtype=np.int64, count=len(results))
            r_vals = np.fromiter(results.values(), dtype=np.float64, count=len(results))
            ok = r_ids <= nids.max()
            by_nid[r_ids[ok]] = r_vals[ok]
        volts = by_nid[nids]

    xi, yi, layers = keys[:, 0], keys[:, 1], keys[:, 2]
    shape = (int(xi.max()) + 1, int(yi.max()) + 1)
    x0, y0 = mesh.grid_origin
    grids = {}
    for lid in np.unique(layers):
        sel = layers == lid
        values = np.full(shape, np.nan, dtype=np.float32)
        values[xi[sel], yi[sel]] = volts[sel]
        grids[int(lid)] = LayerGrid(int(lid), values, float(x0), float(y0), float(mesh.grid_step))
    return grids
