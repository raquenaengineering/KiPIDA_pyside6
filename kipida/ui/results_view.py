"""Interactive result plots (pyqtgraph): per-layer voltage maps and a 3D view."""
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, QSignalBlocker
from PySide6.QtWidgets import QHBoxLayout, QLabel, QTabWidget, QVBoxLayout, QWidget

from kipida.visualization.layer_grid import LayerGrid, mesh_to_layer_grids

pg.setConfigOptions(imageAxisOrder="col-major", antialias=False)

COLORMAP_NAME = "viridis"
OUTLINE_COLOR = (230, 230, 230)  # board outline: light grey, readable over viridis and black


def _colormap():
    return pg.colormap.get(COLORMAP_NAME)


def outline_xy(outline):
    """Join polylines into x, y arrays separated by NaN (one plot item, gaps between pieces)."""
    if not outline:
        return np.empty(0), np.empty(0)
    nan = np.array([[np.nan, np.nan]])
    pts = np.concatenate([np.vstack([p, nan]) for p in outline])
    return pts[:, 0], pts[:, 1]


def outline_segments_3d(outline, z):
    """(2*S, 3) vertex pairs for GL 'lines' mode, y flipped as in the 3D view."""
    pairs = [np.stack([p[:-1], p[1:]], axis=1).reshape(-1, 2) for p in outline if len(p) > 1]
    if not pairs:
        return np.empty((0, 3), np.float32)
    xy = np.concatenate(pairs)
    return np.column_stack([xy[:, 0], -xy[:, 1], np.full(len(xy), z)]).astype(np.float32)


def _bounds(grids, outline):
    """(xmin, ymin, xmax, ymax) in mm covering the copper and the board outline."""
    boxes = []
    for g in grids.values():
        x, y, w, h = g.rect
        boxes.append((x, y, x + w, y + h))
    for p in outline or []:
        if len(p):
            boxes.append((p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max()))
    b = np.array(boxes)
    return b[:, 0].min(), b[:, 1].min(), b[:, 2].max(), b[:, 3].max()


def drop_levels(v_max: float, drop_pct: float):
    """Colour scale: from (1 - drop%) of the rail's max voltage up to the max."""
    lo, hi = v_max * (1.0 - drop_pct / 100.0), v_max
    if hi - lo < 1e-9:  # zero drop range would break the colour bar
        lo = hi - 1e-6
    return lo, hi


class LayerPlot(QWidget):
    """2D voltage map of one copper layer, with hover readout and colour bar."""

    def __init__(self, grid: LayerGrid, layer_name: str, levels, outline=None, parent=None):
        super().__init__(parent)
        self.grid = grid
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.readout = QLabel("Hover over the copper to read the voltage")
        layout.addWidget(self.readout)

        self.plot = pg.PlotWidget()
        layout.addWidget(self.plot, 1)
        pi = self.plot.getPlotItem()
        pi.setTitle(f"Layer: {layer_name}")
        pi.setLabel("bottom", "X", units="mm")
        pi.setLabel("left", "Y", units="mm")
        pi.setAspectLocked(True)
        pi.invertY(True)  # KiCad orientation: y grows downwards
        pi.showGrid(x=True, y=True, alpha=0.2)

        self.image = pg.ImageItem(grid.values)
        self.image.setRect(QRectF(*grid.rect))
        self.image.setColorMap(_colormap())
        pi.addItem(self.image)

        self.color_bar = pg.ColorBarItem(values=levels, colorMap=_colormap(), label="Voltage (V)",
                                         interactive=True)
        self.color_bar.setImageItem(self.image, insert_in=pi)

        # Board outline on top of the map, as a position reference
        self.outline_item = None
        if outline:
            x, y = outline_xy(outline)
            self.outline_item = pg.PlotDataItem(x, y, connect="finite",
                                                pen=pg.mkPen(OUTLINE_COLOR, width=1.5))
            self.outline_item.setZValue(10)
            pi.addItem(self.outline_item)
            pi.autoRange()  # frame the whole board, not just this rail's copper

        self.plot.scene().sigMouseMoved.connect(self._on_mouse_moved)

    def set_levels(self, lo, hi):
        self.color_bar.setLevels(values=(lo, hi))

    def readout_text(self, x_mm, y_mm):
        v = self.grid.value_at(x_mm, y_mm)
        where = f"X {x_mm:8.2f} mm   Y {y_mm:8.2f} mm"
        return f"{where}   |   {v:.4f} V" if v is not None else f"{where}   |   no copper"

    def _on_mouse_moved(self, scene_pos):
        vb = self.plot.getPlotItem().vb
        if vb.sceneBoundingRect().contains(scene_pos):
            p = vb.mapSceneToView(scene_pos)
            self.readout.setText(self.readout_text(p.x(), p.y()))


def grid_to_rgba(grid: LayerGrid, levels) -> np.ndarray:
    """(nx, ny, 4) uint8 colour image of a layer; cells without copper are transparent."""
    lo, hi = levels
    norm = np.clip((grid.values - lo) / (hi - lo), 0.0, 1.0)
    rgba = _colormap().map(np.nan_to_num(norm, nan=0.0).ravel(), mode="byte").reshape(grid.values.shape + (4,))
    rgba[..., 3] = np.where(np.isnan(grid.values), 0, 255)
    return np.ascontiguousarray(rgba, dtype=np.uint8)


class Rail3DView(QWidget):
    """All copper layers of a rail stacked in 3D (OpenGL); rotate with the mouse.

    Each layer is one textured plane (the same image as its 2D map), which is
    solid at any zoom and far lighter than drawing every node as a point.
    """

    def __init__(self, grids, layer_order, levels, outline=None, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.planes = {}  # layer_id -> (GLImageItem, LayerGrid)
        self.outline_items = []
        self.color_bar = None
        try:
            import pyqtgraph.opengl as gl
            from OpenGL import GL
        except ImportError as e:  # PyOpenGL missing
            layout.addWidget(QLabel(f"3D view unavailable (OpenGL support missing: {e})"))
            return

        if not grids:
            layout.addWidget(QLabel("No mesh nodes to display."))
            return
        xmin, ymin, xmax, ymax = _bounds(grids, outline)
        span = max(xmax - xmin, ymax - ymin, 1e-3)

        # Top layer highest; layers spaced by a fraction of the board size
        order = [lid for lid in layer_order if lid in grids] + sorted(set(grids) - set(layer_order))
        spacing = span * 0.15
        layer_z = {lid: (len(order) - 1 - i) * spacing for i, lid in enumerate(order)}

        self.view = gl.GLViewWidget()
        self.view.setBackgroundColor("k")

        # Colour legend beside the 3D view (a GL scene has no axes of its own).
        # Interactive like the 2D maps: dragging its handles re-colours the planes.
        legend = pg.GraphicsLayoutWidget()
        legend.setFixedWidth(110)
        self.color_bar = pg.ColorBarItem(values=levels, colorMap=_colormap(), label="Voltage (V)",
                                         interactive=True)
        legend.addItem(self.color_bar)
        self.color_bar.sigLevelsChanged.connect(lambda bar: self._recolor(bar.levels()))

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.view, 1)
        row.addWidget(legend)
        layout.addLayout(row, 1)
        layout.addWidget(QLabel("Drag to rotate, scroll to zoom. Top layer is highest."))

        # Alpha-blended, no depth test: planes are added bottom-up so upper
        # layers draw over lower ones, and copper-free cells stay see-through.
        gl_options = {GL.GL_DEPTH_TEST: False, GL.GL_BLEND: True,
                      'glBlendFunc': (GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)}
        for lid in reversed(order):
            g = grids[lid]
            item = gl.GLImageItem(grid_to_rgba(g, levels), glOptions=gl_options)
            # Texture pixel (i, j) -> mm; y flipped so the view matches KiCad's top view
            gx, gy, _gw, _gh = g.rect
            item.scale(g.step, -g.step, 1)
            item.translate(gx, -gy, layer_z[lid])
            self.view.addItem(item)
            self.planes[lid] = (item, g)

            # Board outline at this layer's height, drawn right after its plane
            seg = outline_segments_3d(outline or [], layer_z[lid])
            if len(seg):
                lines = gl.GLLinePlotItem(pos=seg, mode="lines", color=tuple(c / 255 for c in OUTLINE_COLOR) + (1.0,),
                                          width=1.5, antialias=True, glOptions=gl_options)
                self.view.addItem(lines)
                self.outline_items.append(lines)

        cx, cy = (xmin + xmax) / 2, -(ymin + ymax) / 2
        cz = (len(order) - 1) * spacing / 2
        self.view.opts["center"] = pg.Vector(cx, cy, cz)
        self.view.setCameraPosition(distance=span * 2.0, elevation=28, azimuth=-60)

    def _recolor(self, levels):
        for item, g in self.planes.values():
            item.setData(grid_to_rgba(g, levels))

    def set_levels(self, lo, hi):
        if self.color_bar is None:
            return
        with QSignalBlocker(self.color_bar):  # recolour once, not again via the signal
            self.color_bar.setLevels(values=(lo, hi))
        self._recolor((lo, hi))


class RailResultsWidget(QTabWidget):
    """Tabs for one rail: '3D View' plus one map per copper layer."""

    def __init__(self, rail_result, stackup, drop_pct, board_outline=None, parent=None):
        super().__init__(parent)
        self.rail_result = rail_result
        copper = (stackup or {}).get("copper", {})
        layer_order = list((stackup or {}).get("layer_order", []))

        grids = mesh_to_layer_grids(rail_result.mesh, rail_result.results)
        levels = drop_levels(rail_result.v_max, drop_pct)

        self.view_3d = Rail3DView(grids, layer_order, levels, outline=board_outline)
        self.addTab(self.view_3d, "3D View")

        self.layer_plots = []
        ordered = [lid for lid in layer_order if lid in grids] + sorted(set(grids) - set(layer_order))
        for lid in ordered:
            name = copper.get(lid, {}).get("name", str(lid))
            plot = LayerPlot(grids[lid], name, levels, outline=board_outline)
            self.layer_plots.append(plot)
            self.addTab(plot, name)

    def set_drop_pct(self, drop_pct):
        lo, hi = drop_levels(self.rail_result.v_max, drop_pct)
        self.view_3d.set_levels(lo, hi)
        for plot in self.layer_plots:
            plot.set_levels(lo, hi)
