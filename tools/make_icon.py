"""Generate the Ki-PIDA toolbar icon: Z-shaped trace, round pads, heat-map colouring.

Geometry is defined on a 24x24 grid and rendered natively at each size, so the
small icon stays crisp. Writes resources/kipida-24.png and kipida-48.png
(referenced by plugin.json and the app window).

Usage:
    python tools/make_icon.py                     # regenerate the icons
    python tools/make_icon.py --preview out.png   # also write a preview sheet
"""
import argparse
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")  # offscreen has no font lookup
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPainterPath, QPen

app = QGuiApplication([])

# --- design (24-unit grid) --------------------------------------------------
PAD_A, PAD_B = (5.3, 5.3), (18.7, 18.7)          # start (top-left), end (bottom-right)
Z_PATH = [PAD_A, (18.7, 5.3), (5.3, 18.7), PAD_B]  # top bar, 45deg diagonal, bottom bar
TRACE_W = 4.2       # trace width
PAD_R = 3.9         # pad radius; pad + outline ends 0.4 units from the edge
HOLE_R = 1.4        # drill hole radius
OUTLINE = 1.0       # dark outline thickness
OUTLINE_COLOR = QColor(26, 26, 30, 235)

# Heat-map stops: hot (source) -> cold (far end), like the IR-drop plots
STOPS = [(0.00, "#e3262f"), (0.25, "#ff8a1c"), (0.50, "#ffd426"),
         (0.75, "#2fbf71"), (1.00, "#2470d8")]


def heat(t):
    t = min(max(t, 0.0), 1.0)
    for (t0, c0), (t1, c1) in zip(STOPS, STOPS[1:]):
        if t <= t1:
            a, b = QColor(c0), QColor(c1)
            f = (t - t0) / (t1 - t0)
            return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * f,
                                   a.greenF() + (b.greenF() - a.greenF()) * f,
                                   a.blueF() + (b.blueF() - a.blueF()) * f)
    return QColor(STOPS[-1][1])


def sample_path(points, step=0.05):
    """Points along the polyline with their normalised arc-length position."""
    seg_len = [math.dist(p, q) for p, q in zip(points, points[1:])]
    total = sum(seg_len)
    out, done = [], 0.0
    for (p, q), L in zip(zip(points, points[1:]), seg_len):
        n = max(1, int(L / step))
        for i in range(n):
            f = i / n
            out.append(((p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f), (done + L * f) / total))
        done += L
    out.append((points[-1], 1.0))
    return out


def render(size):
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 24.0, size / 24.0)

    path = QPainterPath(QPointF(*Z_PATH[0]))
    for pt in Z_PATH[1:]:
        path.lineTo(QPointF(*pt))

    def disc(center, r, color):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(*center), r, r)

    # 1. dark outline under everything
    p.setPen(QPen(OUTLINE_COLOR, TRACE_W + 2 * OUTLINE, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    disc(PAD_A, PAD_R + OUTLINE, OUTLINE_COLOR)
    disc(PAD_B, PAD_R + OUTLINE, OUTLINE_COLOR)

    # 2. heat-map trace: short round-capped segments blend into a smooth gradient
    pts = sample_path(Z_PATH)
    for (a, ta), (b, _tb) in zip(pts, pts[1:]):
        p.setPen(QPen(heat(ta), TRACE_W, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(*a), QPointF(*b))

    # 3. pads in the end colours, with a drill hole (outlined, see-through)
    disc(PAD_A, PAD_R, heat(0.0))
    disc(PAD_B, PAD_R, heat(1.0))
    for c in (PAD_A, PAD_B):
        disc(c, HOLE_R + OUTLINE * 0.6, OUTLINE_COLOR)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        disc(c, HOLE_R, Qt.GlobalColor.black)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    p.end()
    return img


def preview_sheet(icons, out_path):
    """Icons at 1x on KiCad-like light/dark toolbars, plus zoomed views."""
    W, H = 760, 420
    sheet = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    sheet.fill(QColor("#ffffff"))
    p = QPainter(sheet)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setFont(QFont("Segoe UI", 9))

    for row, (bg, fg, label) in enumerate([("#f0f0f0", "#202020", "Light toolbar"),
                                            ("#2d2d30", "#e0e0e0", "Dark toolbar")]):
        y = 20 + row * 110
        p.fillRect(QRectF(10, y, 360, 95), QColor(bg))
        p.setPen(QColor(fg))
        p.drawText(QPointF(20, y + 16), f"{label} (actual size)")
        p.drawText(QPointF(95, y + 86), "24px")
        p.drawImage(QPointF(110, y + 36), icons[24])
        p.drawText(QPointF(190, y + 86), "48px (HiDPI)")
        p.drawImage(QPointF(210, y + 26), icons[48])
        # fake neighbouring toolbar buttons for scale
        p.setPen(QPen(QColor(fg), 1.5))
        p.drawRect(QRectF(300, y + 36, 24, 24))
        p.drawLine(QPointF(330, y + 48), QPointF(354, y + 48))

    p.setPen(QColor("#202020"))
    p.drawText(QPointF(390, 34), "24px zoomed 8x (real pixels)")
    p.fillRect(QRectF(390, 42, 192, 192), QColor("#c8c8c8"))
    p.drawImage(QRectF(390, 42, 192, 192), icons[24])
    p.drawText(QPointF(600, 34), "48px zoomed 3x")
    p.fillRect(QRectF(600, 42, 144, 144), QColor("#2d2d30"))
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.drawImage(QRectF(600, 42, 144, 144), icons[48])
    p.drawText(QPointF(390, 262), "Large vector render (shape detail)")
    p.fillRect(QRectF(390, 270, 144, 144), QColor("#f0f0f0"))
    p.drawImage(QPointF(390, 270), render(144))
    p.fillRect(QRectF(550, 270, 144, 144), QColor("#2d2d30"))
    p.drawImage(QPointF(550, 270), render(144))
    p.end()
    sheet.save(out_path)




SIZES = (24, 48)
RESOURCES = Path(__file__).resolve().parent.parent / "resources"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", type=Path, default=RESOURCES, help="where to write kipida-<size>.png")
    parser.add_argument("--preview", type=Path, help="also write a preview sheet PNG to this path")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    icons = {s: render(s) for s in SIZES}
    for s, img in icons.items():
        path = args.out_dir / f"kipida-{s}.png"
        img.save(str(path))
        print("wrote", path)
    if args.preview:
        preview_sheet(icons, str(args.preview))
        print("wrote", args.preview)


if __name__ == "__main__":
    main()