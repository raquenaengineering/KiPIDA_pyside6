"""Read pad/via padstack data from kipy (KiCad IPC API) objects.

kipy exposes sizes through the padstack (``item.padstack.copper_layers[0].size``,
``item.padstack.drill.diameter``) and angles as ``Angle`` objects. Older code
asked for attributes such as ``width``, ``drill_size`` or a float angle, which
kipy does not have; the lookups silently fell back to defaults. These helpers
read the real values and only fall back when the data is genuinely missing.
All sizes are returned in mm.
"""
NM_PER_MM = 1e6


def _first(obj, *names):
    """First attribute in names that exists and is not None, else None."""
    for name in names:
        try:
            val = getattr(obj, name)
        except Exception:
            continue
        if callable(val) and not hasattr(val, 'x'):
            try:
                val = val()
            except Exception:
                continue
        if val is not None:
            return val
    return None


def angle_degrees(value):
    """Degrees from a kipy Angle, a plain number, or None (-> None)."""
    if value is None:
        return None
    if hasattr(value, 'degrees'):
        return float(value.degrees)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def copper_size_mm(item):
    """(width, height) of the copper on the item's first copper layer, or None."""
    ps = _first(item, 'padstack')
    layers = _first(ps, 'copper_layers') if ps is not None else None
    size = _first(layers[0], 'size') if layers else None
    if size is None:
        size = _first(item, 'size')
    if size is None or not hasattr(size, 'x'):
        return None
    return size.x / NM_PER_MM, size.y / NM_PER_MM


def copper_diameter_mm(item, default=None):
    """Outer copper diameter of a via (or round pad); legacy 'width' as fallback."""
    size = copper_size_mm(item)
    if size and size[0] > 0:
        return size[0]
    width = _first(item, 'width', 'diameter')
    if isinstance(width, (int, float)) and width > 0:
        return width / NM_PER_MM
    return default


def drill_diameter_mm(item, default=None):
    """Drilled (plated) hole diameter of a via or through-hole pad."""
    ps = _first(item, 'padstack')
    drill = _first(ps, 'drill') if ps is not None else None
    dia = _first(drill, 'diameter') if drill is not None else None
    if dia is None:
        dia = _first(item, 'drill_size', 'drill_diameter', 'drill')
    if hasattr(dia, 'x'):  # Vector2 (x, y) - use the smaller side for slots
        d = min(dia.x, dia.y) if getattr(dia, 'y', 0) > 0 else dia.x
    elif isinstance(dia, (int, float)):
        d = dia
    else:
        return default
    return d / NM_PER_MM if d > 0 else default
