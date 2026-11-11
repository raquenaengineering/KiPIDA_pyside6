"""Run a simulation in a separate Python process, so it can be stopped at any time.

Python threads cannot be killed safely, and the heavy steps (meshing, the sparse
solve) cannot be interrupted from inside. Running the whole simulation in its
own process lets the UI kill it instantly, and a run that exhausts memory
cannot take the window down with it.

The child process reconnects to KiCad itself (KICAD_API_SOCKET / KICAD_API_TOKEN
are inherited from the plugin's environment) and reports back through a
multiprocessing queue with these messages:

    ("log", text)
    ("result", SystemResult)                     # meshes slimmed by slim_result()
    ("error", kind, message, traceback_text)     # kind: "cycle" or exception name

This module has no Qt dependency.
"""
import traceback

import numpy as np

from kipida.simulation.mesh import Mesh
from kipida.simulation.system import PowerTreeCycleError, RailResult, SystemResult, SystemSimulator

KICAD_TIMEOUT_MS = 60000  # large boards: geometry requests can take a while


def slim_result(result: SystemResult) -> SystemResult:
    """Copy of the result with only what the UI needs, as compact NumPy arrays.

    The full mesh holds millions of Python objects (conductance lists, dicts);
    pickling those to the UI would be slow and memory hungry. The display only
    needs each node's grid position and voltage.
    """
    slim = SystemResult(stackup=result.stackup, solve_order=list(result.solve_order),
                        elapsed_s=result.elapsed_s, board_outline=list(result.board_outline))
    for name, rail in result.rails.items():
        mesh = rail.mesh
        n = len(mesh.node_map)
        keys = np.fromiter((c for k in mesh.node_map for c in k), dtype=np.int32, count=3 * n).reshape(n, 3)
        ids = np.fromiter(mesh.node_map.values(), dtype=np.int64, count=n)
        volts = np.fromiter((rail.results.get(int(i), np.nan) for i in ids), dtype=np.float64, count=n)

        m = Mesh()
        m.grid_origin, m.grid_step = tuple(map(float, mesh.grid_origin)), float(mesh.grid_step)
        m.nodes = ids                       # len() = node count
        m.node_keys, m.node_volts = keys, volts  # used by visualization.layer_grid
        slim.rails[name] = RailResult(name, m, {}, rail.v_min, rail.v_max, dict(rail.timings))
    return slim


def run_simulation(queue, rails, grid_size_mm, debug):
    """Child-process entry point (must stay a module-level function for 'spawn')."""
    try:
        import kipy
        board = kipy.KiCad(timeout_ms=KICAD_TIMEOUT_MS).get_board()
        sim = SystemSimulator(board, debug=debug, log_callback=lambda msg: queue.put(("log", msg)))
        result = sim.run(rails, grid_size_mm=grid_size_mm)
        queue.put(("log", "Sending results to the window..."))
        queue.put(("result", slim_result(result)))
    except PowerTreeCycleError as e:
        queue.put(("error", "cycle", str(e), traceback.format_exc()))
    except BaseException as e:  # MemoryError, connection errors, ...
        queue.put(("error", type(e).__name__, str(e), traceback.format_exc()))
