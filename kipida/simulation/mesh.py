
import sys
import math
from itertools import repeat

from kipida.kicad.padstack import copper_diameter_mm, copper_size_mm, drill_diameter_mm

# Use explicit check if needed, but we assume kipy objects are passed
def to_mm(val):
    return val / 1e6

try:
    import numpy as np
    import shapely
    from shapely.geometry import Point, box
except ImportError:
    np = shapely = None
    Point = box = None


def copper_mask(geometry, xv, yv):
    """Boolean grid (same shape as xv/yv): True where a grid point lies on copper.

    One vectorised Shapely test for the whole layer. Holes (clearances, antipads,
    thermal-relief gaps) are correctly excluded. The previous matplotlib
    path test treated holes as copper and re-scanned the whole grid for every
    separate copper piece (minutes on large nets). The tiny buffer keeps points
    lying exactly on a copper edge.
    """
    region = geometry.buffer(1e-5)
    shapely.prepare(region)
    return shapely.contains_xy(region, xv, yv)

class Mesh:
    """Resistive mesh: nodes on a regular grid per layer, plus the conductance
    matrix in COO form.

    The matrix entries are stored as NumPy array blocks (one block per batch of
    edges) instead of Python lists of numbers: a large net has tens of millions
    of entries, and as individual Python objects they cost gigabytes and most of
    the meshing time. G_coo_row / G_coo_col / G_coo_data return the entries as
    arrays, in the same order the edges were added.
    """

    def __init__(self):
        self.nodes = [] # List of node_ids (integers)
        self.node_coords = {} # { node_id: (x_mm, y_mm, layer_id) }
        self.edges = [] # List of (node_a, node_b, conductance_G) - Legacy support
        self.node_map = {} # { (x_idx, y_idx, layer_id): node_id }
        self.grid_origin = (0, 0)
        self.grid_step = 0

        # Sparse matrix entries, as blocks of arrays
        self._rows, self._cols, self._data = [], [], []
        self.G_final_csr = None # To be filled by solver or mesher if configured

    def add_edges(self, u, v, g):
        """Add conductance g between node arrays u[i] and v[i] (g: scalar or array).

        Stamps G[u,u] += g, G[v,v] += g, G[u,v] -= g, G[v,u] -= g for every pair.
        """
        u = np.asarray(u, dtype=np.int64).ravel()
        v = np.asarray(v, dtype=np.int64).ravel()
        if u.size == 0:
            return
        g = np.broadcast_to(np.asarray(g, dtype=np.float64), u.shape)
        self._rows.append(np.concatenate([u, v, u, v]))
        self._cols.append(np.concatenate([u, v, v, u]))
        self._data.append(np.concatenate([g, g, -g, -g]))

    def add_edge_direct(self, u, v, g):
        """Add a single edge (vias, through-hole pads)."""
        self.add_edges([u], [v], g)

    @staticmethod
    def _joined(blocks, dtype):
        return np.concatenate(blocks) if blocks else np.empty(0, dtype=dtype)

    @property
    def G_coo_row(self):
        return self._joined(self._rows, np.int64)

    @property
    def G_coo_col(self):
        return self._joined(self._cols, np.int64)

    @property
    def G_coo_data(self):
        return self._joined(self._data, np.float64)

class Mesher:
    def __init__(self, board, debug=False, log_callback=None):
        self.board = board
        self.debug = debug
        self.log_callback = log_callback
        if np is None or shapely is None:
            raise ImportError("NumPy and Shapely are required for Meshing.")
    
    def _get_val(self, obj, attr_name, default=None):
        """Robustly get attribute value from object (property or getter)."""
        if obj is None: return default
        if hasattr(obj, attr_name):
            val = getattr(obj, attr_name)
            if val is not None: return val
        for prefix in ["get_", ""]:
            method_name = prefix + attr_name
            if hasattr(obj, method_name):
                try:
                    val = getattr(obj, method_name)()
                    if val is not None: return val
                except: pass
        return default

    def _get_board_items(self, attr_name):
        """Robustly get items from board (property or getter)."""
        if hasattr(self.board, attr_name):
            return getattr(self.board, attr_name)
        method_name = f"get_{attr_name}"
        if hasattr(self.board, method_name):
            try: return getattr(self.board, method_name)()
            except: pass
        return []

    def _log(self, msg):
        """Helper to log debug messages."""
        if self.debug and self.log_callback:
            self.log_callback(f"[MESH] {msg}")

    def generate_mesh(self, net_name, geometry_by_layer, stackup, grid_size_mm=0.5):
        """
        Generates a resistive mesh from the geometry using vectorized operations.
        """
        mesh = Mesh()
        mesh.grid_step = grid_size_mm
        
        # 1. Calculate Bounding Box
        min_x, min_y, max_x, max_y = float('inf'), float('inf'), float('-inf'), float('-inf')
        
        has_geometry = False
        valid_layers = []
        for lid, poly in geometry_by_layer.items():
            if poly.is_empty: continue
            has_geometry = True
            valid_layers.append(lid)
            b = poly.bounds
            min_x = min(min_x, b[0])
            min_y = min(min_y, b[1])
            max_x = max(max_x, b[2])
            max_y = max(max_y, b[3])
            
        if not has_geometry:
            return mesh

        # Pad bounds slightly
        pad = grid_size_mm
        min_x -= pad
        min_y -= pad
        max_x += pad
        max_y += pad
        
        mesh.grid_origin = (min_x, min_y)
        
        # 2. Create Grid Coordinates
        width_mm = max_x - min_x
        height_mm = max_y - min_y
        
        nx = int(math.ceil(width_mm / grid_size_mm))
        ny = int(math.ceil(height_mm / grid_size_mm))
        
        x_coords = np.linspace(min_x, min_x + (nx * grid_size_mm), nx + 1)
        y_coords = np.linspace(min_y, min_y + (ny * grid_size_mm), ny + 1)
        
        xv, yv = np.meshgrid(x_coords, y_coords) # shape (ny+1, nx+1)

        if self.debug:
            self._log(f"Grid setup: {nx+1}x{ny+1} points, bounds ({min_x:.1f},{min_y:.1f}) to ({max_x:.1f},{max_y:.1f})")

        # 3. Vectorized Rasterization
        # We will build a 3D boolean mask: presence[layer_idx, y_idx, x_idx]
        # But layer IDs are sparse (e.g. 0, 1, 31). So we map them.
        
        sorted_layers = sorted(valid_layers)
        layer_map = { lid: idx for idx, lid in enumerate(sorted_layers) }
        
        # node_indices: [layer, y, x] -> node_id (or -1 if empty)
        node_grid = np.full((len(sorted_layers), ny + 1, nx + 1), -1, dtype=int)
        
        node_counter = 0
        
        for lid in sorted_layers:
            poly = geometry_by_layer[lid]
            if poly.is_empty: continue
            
            mask_2d = copper_mask(poly, xv, yv)
            
            # Assign Node IDs
            count_on_layer = np.count_nonzero(mask_2d)
            if count_on_layer > 0:
                # Get indices where mask is true
                y_idxs, x_idxs = np.nonzero(mask_2d)
                
                # Generate new IDs
                new_ids = np.arange(node_counter, node_counter + count_on_layer)
                node_grid[layer_map[lid], y_idxs, x_idxs] = new_ids
                
                # Node tables, built in bulk (plain Python ints/floats, no per-node loop)
                ids = new_ids.tolist()
                xs, ys = x_idxs.tolist(), y_idxs.tolist()
                layer = int(lid)
                mesh.nodes.extend(ids)
                mesh.node_map.update(zip(zip(xs, ys, repeat(layer)), ids))
                mesh.node_coords.update(zip(ids, zip((min_x + x_idxs * grid_size_mm).tolist(),
                                                     (min_y + y_idxs * grid_size_mm).tolist(),
                                                     repeat(layer))))

                node_counter += count_on_layer
                
                # 4. Generate Lateral Edges (Vectorized)
                # Physical props
                copper_info = stackup.get('copper', {}).get(lid, {})
                thick = copper_info.get('thickness_mm', 0.035)
                rho = stackup.get('resistivity', 1.7e-5)
                g_lat_val = thick / rho
                
                # Horizontal Neighbors (x, y) <-> (x+1, y)
                # Check where node and right-neighbor both exist
                
                # mask_2d is boolean. node_grid has IDs.
                # Valid H edges: mask[:, :-1] & mask[:, 1:]
                
                # Right neighbors
                right_mask = mask_2d[:, :-1] & mask_2d[:, 1:]
                if np.any(right_mask):
                    y_r, x_r = np.nonzero(right_mask)
                    # Nodes at (y,x)
                    u_ids = node_grid[layer_map[lid], y_r, x_r]
                    # Nodes at (y, x+1)
                    v_ids = node_grid[layer_map[lid], y_r, x_r + 1]
                    mesh.add_edges(u_ids, v_ids, g_lat_val)
                
                # Vertical (Top) Neighbors (x, y) <-> (x, y+1)
                top_mask = mask_2d[:-1, :] & mask_2d[1:, :]
                if np.any(top_mask):
                    y_t, x_t = np.nonzero(top_mask)
                    # Nodes at (y, x)
                    u_ids = node_grid[layer_map[lid], y_t, x_t]
                    # Nodes at (y+1, x)
                    v_ids = node_grid[layer_map[lid], y_t + 1, x_t]
                    mesh.add_edges(u_ids, v_ids, g_lat_val)

            if self.debug:
                self._log(f"  Layer {lid} vectorized mesh: {count_on_layer} nodes.")

        # 5. Vertical Connections (Vias & PTH)
        if self.log_callback:
            self.log_callback("Adding vertical interconnects...")
        
        # Helper to check if item matches net
        def match_net(item, name):
            net = self._get_val(item, 'net')
            n_name = self._get_val(net, 'name', "")
            return n_name == name

        # Get vias using proper API
        vias = self._get_board_items('vias')
        for via in vias:
            if match_net(via, net_name):
                self._add_vertical_link(mesh, via, stackup)
                    
        footprints = self._get_board_items('footprints')
        for fp in footprints:
            pads = self._get_val(fp, 'pads')
            if pads is None:
                defn = self._get_val(fp, 'definition')
                pads = self._get_val(defn, 'pads', [])
                
            for pad in pads:
                if match_net(pad, net_name):
                    # Check if PTH using numeric pad_type value
                    # pad_type: 0=SMD, 1=PTH, 2=CONN, 3=NPTH
                    p_type_val = self._get_val(pad, 'pad_type', None)
                    p_type_str = str(self._get_val(pad, 'type', ''))
                    
                    is_pth = (p_type_val == 1) or ('THROUGH' in p_type_str and 'NON' not in p_type_str)
                    
                    if is_pth:
                            pos = self._get_val(pad, 'position')

                            # Get layers from padstack
                            layers = self._get_val(pad, 'layers')
                            if not layers:
                                ps = self._get_val(pad, 'padstack')
                                if ps:
                                    layers = self._get_val(ps, 'layers')

                            # Barrel resistance from the drill; node search over the pad copper
                            size = copper_size_mm(pad)
                            pad_mm = min(size) if size else None
                            drill_mm = drill_diameter_mm(pad, default=pad_mm)
                            self._add_vertical_stack(mesh, pos,
                                                    layers=layers,
                                                    diameter=drill_mm or 0.0,
                                                    stackup=stackup,
                                                    search_diameter=max(pad_mm or 0.0, drill_mm or 0.0))

        return mesh

    def _calculate_vertical_g(self, layer_a, layer_b, stackup, diameter_mm):
        """Conductance of a plated barrel between two layers. diameter_mm: drill diameter."""
        if layer_a == layer_b: return 1.0e9
        plating_thick = 0.025
        # Copper ring area: pi/4 * (d^2 - (d - 2t)^2) = pi * (d*t - t^2)
        area = math.pi * (diameter_mm * plating_thick - plating_thick**2)
        if area <= 0: return 1000.0 
        rho = stackup.get('resistivity', 1.68e-5)
        h = 0
        l_min, l_max = min(layer_a, layer_b), max(layer_a, layer_b)
        for sub in stackup.get('substrate', []):
            sb = sub['between']
            if sb[0] is not None and sb[1] is not None:
                if min(sb) >= l_min and max(sb) <= l_max:
                    h += sub['thickness_mm']
        if h <= 0: h = 0.5 
        return area / (rho * h)

    def _get_best_node_in_radius(self, mesh, x_mm, y_mm, layer, radius_mm):
        """Find a node for the via/pad on the given layer."""
        ix_center = int(round((x_mm - mesh.grid_origin[0]) / mesh.grid_step))
        iy_center = int(round((y_mm - mesh.grid_origin[1]) / mesh.grid_step))
        
        # 1. Try exact center first
        nid = mesh.node_map.get((ix_center, iy_center, layer))
        if nid is not None:
            return nid
            
        # 2. Try immediate 3x3 neighborhood (radius 1)
        # This is essential for small vias on coarse grids.
        for dx in [-1, 0, 1]:
            for dy in [-1, 0, 1]:
                if dx == 0 and dy == 0: continue
                nid = mesh.node_map.get((ix_center + dx, iy_center + dy, layer))
                if nid is not None:
                    # Optional: Could check distance here, but first-found is usually fine
                    # for discrete grid nodes. 
                    return nid
                    
        # 3. For larger pads, try a wider search if needed
        full_search_radius = int(np.ceil(radius_mm / mesh.grid_step))
        if full_search_radius > 1:
            for r in range(2, full_search_radius + 1):
                for dx in range(-r, r + 1):
                    for dy in range(-r, r + 1):
                        if abs(dx) < r and abs(dy) < r: continue
                        nid = mesh.node_map.get((ix_center + dx, iy_center + dy, layer))
                        if nid is not None:
                            return nid
        return None

    def _add_vertical_link(self, mesh, via, stackup):
        """Adds vertical connectivity for a via."""
        layers = self._get_val(via, 'layers')
        if not layers:
            ps = self._get_val(via, 'padstack')
            if ps:
                layers = self._get_val(ps, 'layers')
        
        if not layers:
            lp = self._get_val(via, 'layer_pair')
            if lp:
                s_lid, e_lid = min(lp), max(lp)
                all_cu = sorted(stackup['copper'].keys())
                layers = [l for l in all_cu if s_lid <= l <= e_lid]
            else:
                # Default to all copper layers
                layers = list(stackup['copper'].keys())

        pos = getattr(via, 'start', None)
        if not pos: 
             pos = getattr(via, 'position', None)
             
        if not pos: return

        x_mm, y_mm = to_mm(pos.x), to_mm(pos.y)
        # Search for mesh nodes over the via pad; barrel resistance from the drill
        pad_mm = copper_diameter_mm(via, default=0.6)
        drill_mm = drill_diameter_mm(via, default=pad_mm)
        radius_mm = pad_mm / 2.0
        
        nodes_in_stack = []
        # Sort layers to ensure vertical sequence
        sorted_via_layers = sorted(list(layers))
        for lid in sorted_via_layers:
            nid = self._get_best_node_in_radius(mesh, x_mm, y_mm, lid, radius_mm)
            if nid is not None:
                nodes_in_stack.append(nid)
        
        if self.debug and len(nodes_in_stack) < 2:
            self._log(f"      [VIA] Failed to connect layers @ ({x_mm:.2f}, {y_mm:.2f}): found nodes on layers {[mesh.node_coords[n][2] for n in nodes_in_stack]}")

        for i in range(len(nodes_in_stack) - 1):
            nid_a = nodes_in_stack[i]
            nid_b = nodes_in_stack[i+1]
            la = mesh.node_coords[nid_a][2]
            lb = mesh.node_coords[nid_b][2]
            g_via = self._calculate_vertical_g(la, lb, stackup, drill_mm)
            mesh.add_edge_direct(nid_a, nid_b, g_via)

    def _add_vertical_stack(self, mesh, pos, layers, diameter, stackup, search_diameter=None):
        """Connect a through-hole pad across layers. diameter: drill (barrel) size."""
        if layers is None or len(layers) == 0:
            layers = sorted(stackup['copper'].keys())
        else:
             layers = sorted(list(layers))

        x_mm, y_mm = to_mm(pos.x), to_mm(pos.y)
        radius_mm = (search_diameter or diameter) / 2.0
        
        nodes_in_stack = []
        for layer in layers:
            nid = self._get_best_node_in_radius(mesh, x_mm, y_mm, layer, radius_mm)
            if nid is not None:
                nodes_in_stack.append(nid)
                
        for i in range(len(nodes_in_stack) - 1):
            nid_a = nodes_in_stack[i]
            nid_b = nodes_in_stack[i+1]
            la = mesh.node_coords[nid_a][2]
            lb = mesh.node_coords[nid_b][2]
            g_via = self._calculate_vertical_g(la, lb, stackup, diameter)
            mesh.add_edge_direct(nid_a, nid_b, g_via)


