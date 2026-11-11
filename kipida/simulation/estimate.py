"""Estimate how long a simulation will take at another mesh resolution.

Calibrated from the last completed run. Measured on a real 4-layer board
(0.4 mm .. 0.025 mm, up to 3.4 M nodes per rail):

* mesh nodes scale with 1 / step^2 (they cover the copper area);
* meshing and the sparse solve grow ~linearly with the node count, on top of a
  small fixed cost per rail (~0.1 s each);
* geometry extraction and source/load setup do not depend on the step;
* starting the simulation process, connecting to KiCad and drawing the plots
  add a fixed part plus a part proportional to the nodes.

Assumes the same board and power tree as the calibration run.
"""
from dataclasses import dataclass, field
from typing import Dict, Tuple

# Fixed (resolution independent) part of the scalable phases, per rail, in seconds
FIXED_S = {"mesh": 0.10, "solve": 0.12}
PLOTS_FIXED_S = 0.3


@dataclass
class Calibration:
    grid_mm: float
    rails: Dict[str, Tuple[int, Dict[str, float]]] = field(default_factory=dict)  # name -> (nodes, timings)
    plots_s: float = 0.0
    overhead_s: float = 0.0   # process start, KiCad connection, stackup/outline, skipped rails
    wall_s: float = 0.0       # what the calibration run took in total

    @classmethod
    def from_run(cls, result, grid_mm: float, wall_s: float, plots_s: float) -> "Calibration":
        """From a SystemResult, the mesh step it used, and the run's wall and plot times."""
        rails = {name: (len(r.mesh.nodes), dict(r.timings)) for name, r in result.rails.items()}
        rail_s = sum(sum(t.values()) for _n, t in rails.values())
        overhead = max(0.0, wall_s - rail_s - plots_s)
        return cls(grid_mm=grid_mm, rails=rails, plots_s=plots_s, overhead_s=overhead, wall_s=wall_s)


def _scale(t: float, fixed: float, k: float) -> float:
    """Fixed part stays, the rest scales with the node-count ratio k."""
    c0 = min(t, fixed)
    return c0 + (t - c0) * k


def estimate_seconds(cal: Calibration, grid_mm: float) -> float:
    """Expected total time (seconds) of a run at grid_mm, from calibration cal."""
    k = (cal.grid_mm / grid_mm) ** 2  # node-count ratio
    total = cal.overhead_s + _scale(cal.plots_s, PLOTS_FIXED_S, k)
    for _nodes, t in cal.rails.values():
        for phase, secs in t.items():
            total += _scale(secs, FIXED_S[phase], k) if phase in FIXED_S else secs
    return total


def format_duration(seconds: float) -> str:
    """'~8 s', '~45 s', '~3 min', '~1.2 h'."""
    if seconds < 10:
        return f"~{seconds:.0f} s" if seconds >= 1 else "~1 s"
    if seconds < 90:
        return f"~{round(seconds / 5) * 5:.0f} s"
    if seconds < 3600:
        return f"~{seconds / 60:.0f} min"
    return f"~{seconds / 3600:.1f} h"
