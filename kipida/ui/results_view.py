"""Interactive result plots (pyqtgraph): per-layer voltage maps and a 3D view."""
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QLabel, QTabWidget, QVBoxLayout, QWidget

from kipida.visualization.layer_grid import LayerGrid, mesh_to_layer_grids

pg.setConfigOptions(imageAxisOrder="col-major", antialias=False)

COLORMAP_NAME = "viridis"


def _colormap():
    return pg.colormap.get(COLORMAP_NAME)


def drop_levels(v_max: float, drop_pct: float):
    """Colour scale: from (1 - drop%) of the rail's max voltage up to the max."""
    lo, hi = v_max * (1.0 - drop_pct / 100.0), v_max
    if hi - lo < 1e-9:  # zero drop range would break the colour bar
        lo = hi - 1e-6
    return lo, hi


class LayerPlot(QWidget):
    """2D voltage map of one copper layer, with hover readout and colour bar."""

    def __init__(self, grid: LayerGrid, layer_name: str, levels, parent=None):
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

    def __init__(self, grids, layer_order, levels, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.planes = {}  # layer_id -> (GLImageItem, LayerGrid)
        try:
            import pyqtgraph.opengl as gl
            from OpenGL import GL
        except ImportError as e:  # PyOpenGL missing
            layout.addWidget(QLabel(f"3D view unavailable (OpenGL support missing: {e})"))
            return

        any_grid = next(iter(grids.values()), None)
        if any_grid is None:
            layout.addWidget(QLabel("No mesh nodes to display."))
            return
        x, y, w, h = any_grid.rect
        span = max(w, h, 1e-3)

        # Top layer highest; layers spaced by a fraction of the board size
        order = [lid for lid in layer_order if lid in grids] + sorted(set(grids) - set(layer_order))
        spacing = span * 0.15
        layer_z = {lid: (len(order) - 1 - i) * spacing for i, lid in enumerate(order)}

        self.view = gl.GLViewWidget()
        self.view.setBackgroundColor("k")
        layout.addWidget(self.view, 1)
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

        cx, cy = x + w / 2, -(y + h / 2)
        cz = (len(order) - 1) * spacing / 2
        self.view.opts["center"] = pg.Vector(cx, cy, cz)
        self.view.setCameraPosition(distance=span * 2.0, elevation=28, azimuth=-60)

    def set_levels(self, lo, hi):
        for item, g in self.planes.values():
            item.setData(grid_to_rgba(g, (lo, hi)))


class RailResultsWidget(QTabWidget):
    """Tabs for one rail: '3D View' plus one map per copper layer."""

    def __init__(self, rail_result, stackup, drop_pct, parent=None):
        super().__init__(parent)
        self.rail_result = rail_result
        copper = (stackup or {}).get("copper", {})
        layer_order = list((stackup or {}).get("layer_order", []))

        grids = mesh_to_layer_grids(rail_result.mesh, rail_result.results)
        levels = drop_levels(rail_result.v_max, drop_pct)

        self.view_3d = Rail3DView(grids, layer_order, levels)
        self.addTab(self.view_3d, "3D View")

        self.layer_plots = []
        ordered = [lid for lid in layer_order if lid in grids] + sorted(set(grids) - set(layer_order))
        for lid in ordered:
            name = copper.get(lid, {}).get("name", str(lid))
            plot = LayerPlot(grids[lid], name, levels)
            self.layer_plots.append(plot)
            self.addTab(plot, name)

    def set_drop_pct(self, drop_pct):
        lo, hi = drop_levels(self.rail_result.v_max, drop_pct)
        self.view_3d.set_levels(lo, hi)
        for plot in self.layer_plots:
            plot.set_levels(lo, hi)
