"""Fake simulation-process entry points for tests.

They run in a real child process (multiprocessing 'spawn'), so they must be
module-level functions in an importable module. Same signature and messages
as kipida.simulation.process.run_simulation.
"""
import os
import time


def ok(queue, rails, grid_size_mm, debug):
    queue.put(("log", f"fake run: {len(rails)} rails at {grid_size_mm} mm"))
    queue.put(("result", {"rails": [r.net_name for r in rails], "grid": grid_size_mm}))


def slow(queue, rails, grid_size_mm, debug):
    queue.put(("log", "started"))
    time.sleep(120)  # must be killed by Stop long before this ends
    queue.put(("result", "should never arrive"))


def crash(queue, rails, grid_size_mm, debug):
    queue.put(("log", "about to die"))
    os._exit(3)  # like being killed by the OS (e.g. out of memory)


def cycle(queue, rails, grid_size_mm, debug):
    queue.put(("error", "cycle", "Cycle detected in power rail dependencies involving 'A'", "tb"))
