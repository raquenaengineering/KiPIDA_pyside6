"""Power-tree (multi-rail) DC simulation, independent of any GUI toolkit.

Solves every rail in dependency order (leaves first) so that the current drawn
by downstream regulators is known before the rail feeding them is solved.
Previously this lived in the wx main dialog's ``on_run`` handler.
"""
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from kipida.core.models import PowerRail, VoltageRegulator
from kipida.kicad.extractor import GeometryExtractor
from kipida.kicad.outline import extract_board_outline
from kipida.simulation.mesh import Mesher, Mesh
from kipida.simulation.solver import Solver


class PowerTreeCycleError(ValueError):
    """Raised when regulators form a loop between rails."""


@dataclass
class RailResult:
    net_name: str
    mesh: Mesh
    results: Dict[int, float]  # node_id -> voltage
    v_min: float
    v_max: float
    # Seconds per phase: geometry, mesh, setup (source/load mapping), solve
    timings: Dict[str, float] = field(default_factory=dict)

    @property
    def drop(self) -> float:
        return self.v_max - self.v_min

    @property
    def elapsed_s(self) -> float:
        return sum(self.timings.values())


@dataclass
class SystemResult:
    stackup: dict
    solve_order: List[str] = field(default_factory=list)
    rails: Dict[str, RailResult] = field(default_factory=dict)  # in solve order
    elapsed_s: float = 0.0  # wall time of the whole run, incl. stackup and skipped rails
    board_outline: list = field(default_factory=list)  # Edge.Cuts polylines, (N, 2) arrays in mm


def format_timings(timings: Dict[str, float]) -> str:
    """e.g. 'geometry 0.42 s, mesh 1.30 s, setup 0.05 s, solve 0.88 s'."""
    return ", ".join(f"{phase} {secs:.2f} s" for phase, secs in timings.items())


def build_rail_dependency_graph(rails: List[PowerRail]) -> Dict[str, List[str]]:
    """rail_name -> names of the rails it is fed from (via regulators)."""
    graph = {rail.net_name: [] for rail in rails}
    for rail in rails:
        for reg in rail.child_regulators:
            if reg.output_rail_name in graph:
                graph[reg.output_rail_name].append(rail.net_name)
    return graph


def topological_sort_rails(rails: List[PowerRail]) -> List[PowerRail]:
    """Sort rails leaves first (outputs before the rails feeding them).

    Raises PowerTreeCycleError if the regulators form a cycle.
    """
    graph = build_rail_dependency_graph(rails)
    visited, rec_stack, order = set(), set(), []

    def dfs(name):
        if name in rec_stack:
            raise PowerTreeCycleError(f"Cycle detected in power rail dependencies involving '{name}'")
        if name in visited:
            return
        visited.add(name)
        rec_stack.add(name)
        for dep in graph.get(name, []):
            dfs(dep)
        rec_stack.remove(name)
        order.append(name)

    for rail in rails:
        if rail.net_name not in visited:
            dfs(rail.net_name)

    order.reverse()
    by_name = {r.net_name: r for r in rails}
    return [by_name[n] for n in order]


def regulator_input_current(reg: VoltageRegulator, output_current: float,
                            input_voltage: float, output_voltage: float) -> float:
    """Current a regulator draws from its input rail for a given output current."""
    if reg.reg_type == "SWITCHING":
        # Power conservation: P_in = P_out / efficiency
        p_out = output_current * output_voltage
        p_in = p_out / reg.efficiency if reg.efficiency > 0 else p_out
        return p_in / input_voltage if input_voltage > 0 else 0.0
    # LINEAR (and unknown types): input current equals output current
    return output_current


def _get_val(obj, attr_name, default=None):
    """Robustly get attribute value from a kipy object (property or getter)."""
    if obj is None:
        return default
    if hasattr(obj, attr_name):
        val = getattr(obj, attr_name)
        if val is not None:
            return val
    for prefix in ["get_", ""]:
        method_name = prefix + attr_name
        if hasattr(obj, method_name):
            try:
                val = getattr(obj, method_name)()
                if val is not None:
                    return val
            except Exception:
                pass
    return default


class SystemSimulator:
    def __init__(self, board, debug: bool = False, log_callback: Optional[Callable[[str], None]] = None):
        self.board = board
        self.debug = debug
        self.log_callback = log_callback

    def log(self, msg: str):
        if self.log_callback:
            self.log_callback(msg)
        else:
            print(msg)

    # -- board lookups -----------------------------------------------------

    def _get_board_items(self, attr_name):
        if hasattr(self.board, attr_name):
            return getattr(self.board, attr_name)
        method_name = f"get_{attr_name}"
        if hasattr(self.board, method_name):
            try:
                return getattr(self.board, method_name)()
            except Exception:
                pass
        return []

    def get_mesh_nodes(self, mesh: Mesh, ref_des: str, pad_names: List[str]) -> List[int]:
        """Mesh node ids under the given pads of a footprint (all pads if none given)."""
        debug = self.debug
        if debug:
            self.log(f"  [_get_mesh_nodes] Looking up {ref_des} pads={pad_names}")
        nodes = []

        found_fp = None
        for fp in self._get_board_items('footprints'):
            ref = _get_val(fp, 'reference', _get_val(fp, 'ref_des', ''))
            if not ref:
                ref_field = _get_val(fp, 'reference_field')
                if ref_field:
                    text = _get_val(ref_field, 'text')
                    if text:
                        ref = _get_val(text, 'value', '')
            if ref == ref_des:
                found_fp = fp
                break

        if not found_fp:
            if debug:
                self.log(f"  Warning: Footprint {ref_des} not found.")
            return []

        pads = _get_val(found_fp, 'pads')
        if pads is None:
            defn = _get_val(found_fp, 'definition')
            pads = _get_val(defn, 'pads', [])

        if not pad_names:
            target_pads = pads
        else:
            target_pads = [p for p in pads if _get_val(p, 'number', _get_val(p, 'name', '')) in pad_names]

        if not target_pads:
            if debug:
                self.log(f"  No pads found for {ref_des} matching {pad_names}")
            return []

        origin = mesh.grid_origin
        gs = mesh.grid_step

        for p in target_pads:
            pos = _get_val(p, 'position')
            if not pos:
                continue

            px, py = 0, 0
            if hasattr(pos, 'x') and hasattr(pos, 'y'):
                px = _get_val(pos, 'x', 0)
                py = _get_val(pos, 'y', 0)
            elif hasattr(pos, '__getitem__'):
                px, py = pos[0], pos[1]

            # Convert to mm if likely in nm (KiCad native). Heuristic: > 10000 => nm
            if abs(px) > 10000 or abs(py) > 10000:
                px /= 1e6
                py /= 1e6

            tx = int(round((px - origin[0]) / gs))
            ty = int(round((py - origin[1]) / gs))

            # Search 3x3 neighbourhood across layers
            found_any = False
            for dx in [-1, 0, 1]:
                for dy in [-1, 0, 1]:
                    for layer in range(32):
                        nid = mesh.node_map.get((tx + dx, ty + dy, layer))
                        if nid is not None:
                            nodes.append(nid)
                            found_any = True

            if not found_any and debug:
                self.log(f"  Pad {ref_des} at ({px:.2f},{py:.2f}) not on mesh.")

        return list(set(nodes))

    # -- simulation --------------------------------------------------------

    def run(self, rails: List[PowerRail], grid_size_mm: float = 0.1) -> SystemResult:
        """Solve all rails. Raises PowerTreeCycleError or extraction errors."""
        t_run = time.perf_counter()
        debug = self.debug
        grid_size_mm = max(grid_size_mm, 0.01)

        extractor = GeometryExtractor(self.board, debug=debug, log_callback=self.log)
        stackup = extractor.get_board_stackup()

        sorted_rails = topological_sort_rails(rails)
        result = SystemResult(stackup=stackup, solve_order=[r.net_name for r in sorted_rails])
        self.log(f"Rail solve order: {' -> '.join(result.solve_order)}")

        # Board outline (Edge.Cuts) for display only
        result.board_outline = extract_board_outline(self.board, log=self.log)
        self.log(f"Board outline: {len(result.board_outline)} Edge.Cuts polyline(s)")

        rail_by_name = {r.net_name: r for r in rails}
        rail_total_current = {r.net_name: 0.0 for r in rails}

        for rail in sorted_rails:
            self.log(f"Processing Rail: {rail.net_name} (Sources: {len(rail.sources)}, Loads: {len(rail.loads)})")

            # Direct loads on this rail
            rail_total_current[rail.net_name] = sum(load.total_current for load in rail.loads)
            timings = {}

            # A. Geometry & mesh
            t = time.perf_counter()
            geo = extractor.get_net_geometry(rail.net_name)
            timings["geometry"] = time.perf_counter() - t
            if not geo:
                self.log(f"  Skipping {rail.net_name}: No geometry.")
                continue

            t = time.perf_counter()
            mesher = Mesher(self.board, debug=debug, log_callback=self.log)
            mesh = mesher.generate_mesh(rail.net_name, geo, stackup, grid_size_mm=grid_size_mm)
            timings["mesh"] = time.perf_counter() - t
            if len(mesh.nodes) == 0:
                self.log(f"  Skipping {rail.net_name}: Mesh empty.")
                continue

            t = time.perf_counter()
            solver_sources, solver_loads = [], []

            # B1. Explicit sources
            for src in rail.sources:
                nodes = self.get_mesh_nodes(mesh, src.component_ref.ref_des, src.pad_names)
                if debug:
                    self.log(f"  Source {src.component_ref.ref_des} pads {src.pad_names} -> {len(nodes)} nodes")
                if not nodes:
                    self.log(f"  WARNING: Source {src.component_ref.ref_des} pads {src.pad_names} found NO mesh nodes!")
                for nid in nodes:
                    solver_sources.append({'node_id': nid, 'voltage': rail.nominal_voltage})

            # B2. Regulators whose output feeds this rail act as sources
            for other_rail in rails:
                for reg in other_rail.child_regulators:
                    if reg.output_rail_name != rail.net_name:
                        continue
                    nodes = self.get_mesh_nodes(mesh, reg.output_ref_des, reg.output_pad_names)
                    if debug:
                        self.log(f"  Regulator {reg.name} output {reg.output_ref_des} pads {reg.output_pad_names} -> {len(nodes)} nodes")
                    if not nodes:
                        self.log(f"  WARNING: Regulator {reg.name} output {reg.output_ref_des} pads {reg.output_pad_names} found NO mesh nodes!")
                    for nid in nodes:
                        solver_sources.append({'node_id': nid, 'voltage': rail.nominal_voltage})

            # B3. Explicit loads
            for load in rail.loads:
                nodes = self.get_mesh_nodes(mesh, load.component_ref.ref_des, load.pad_names)
                if not nodes:
                    continue
                i_per_node = load.total_current / len(nodes)
                for nid in nodes:
                    solver_loads.append({'node_id': nid, 'current': i_per_node})

            # B4. Downstream regulators draw current from this rail (outputs already solved)
            for reg in rail.child_regulators:
                total_output_current = rail_total_current.get(reg.output_rail_name, 0.0)
                if total_output_current == 0:
                    if debug:
                        self.log(f"  Regulator {reg.name} has no load on output rail {reg.output_rail_name}")
                    continue

                out_rail = rail_by_name.get(reg.output_rail_name)
                output_v = out_rail.nominal_voltage if out_rail else 0.0
                input_current = regulator_input_current(reg, total_output_current, rail.nominal_voltage, output_v)
                if input_current == 0:
                    continue

                rail_total_current[rail.net_name] += input_current

                nodes = self.get_mesh_nodes(mesh, reg.input_ref_des, reg.input_pad_names)
                if not nodes:
                    self.log(f"  WARNING: Regulator {reg.name} input at {reg.input_ref_des} pads {reg.input_pad_names} found NO mesh nodes!")
                    continue

                i_per_node = input_current / len(nodes)
                for nid in nodes:
                    solver_loads.append({'node_id': nid, 'current': i_per_node})

                if debug:
                    self.log(f"  Regulator {reg.name} draws {input_current:.2f}A from {rail.net_name} ({reg.reg_type})")

            timings["setup"] = time.perf_counter() - t

            # C. Solve
            if not solver_sources:
                self.log(f"  Warning: No sources for {rail.net_name}. Skipping solve.")
                continue

            t = time.perf_counter()
            solver = Solver(debug=debug, log_callback=self.log)
            results = solver.solve(mesh, solver_sources, solver_loads)
            timings["solve"] = time.perf_counter() - t

            # D. Store
            if results:
                v_vals = list(results.values())
                rail_result = RailResult(rail.net_name, mesh, results, min(v_vals), max(v_vals), timings)
                result.rails[rail.net_name] = rail_result
                self.log(f"  Solved {rail.net_name}: Drop {rail_result.drop:.4f} V "
                         f"[{len(mesh.nodes):,} nodes, {rail_result.elapsed_s:.2f} s: {format_timings(timings)}]")
            else:
                self.log(f"  Solved {rail.net_name}: No result. [{format_timings(timings)}]")

        result.elapsed_s = time.perf_counter() - t_run
        self.log(f"Simulation finished in {result.elapsed_s:.2f} s")
        return result
