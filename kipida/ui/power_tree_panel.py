from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QMessageBox, QPushButton, QSplitter, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from kipida.core.config_manager import load_config, save_config
from kipida.core.models import ComponentRef, PowerRail, UnifiedLoad, UnifiedSource, VoltageRegulator
from kipida.kicad.discovery import NetDiscoverer
from kipida.ui.component_selector import ComponentSelectorDialog, pad_name
from kipida.ui.regulator_dialog import RegulatorDialog


class NetSelectionDialog(QDialog):
    def __init__(self, parent, nets):
        super().__init__(parent)
        self.setWindowTitle("Select Net")
        self.resize(300, 400)

        layout = QVBoxLayout(self)
        self.lb = QListWidget()
        self.lb.addItems(sorted(nets))
        self.lb.itemDoubleClicked.connect(lambda _item: self.accept())
        layout.addWidget(self.lb)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_selected_net(self):
        item = self.lb.currentItem()
        return item.text() if item else None


def _rail_label(rail: PowerRail) -> str:
    return f"{rail.net_name} ({rail.nominal_voltage}V)" if rail.nominal_voltage else rail.net_name


class PowerTreePanel(QWidget):
    """Rail list (left) and the selected rail's sources/loads/regulators (right)."""

    def __init__(self, board, project=None, log_callback=None, parent=None):
        super().__init__(parent)
        self.board = board
        self.project = project
        self.log_callback = log_callback
        self.discoverer = NetDiscoverer(board, log_callback=log_callback)

        self.rails = []  # List[PowerRail]
        self.active_rail = None

        self._init_ui()

    def log(self, msg):
        if self.log_callback:
            self.log_callback(msg)
        else:
            print(msg)

    # -- layout -------------------------------------------------------------

    def _init_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main.addWidget(splitter)

        # Left: rail list
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("Detected Power Rails"))
        self.rail_list = QListWidget()
        lv.addWidget(self.rail_list, 1)
        self.btn_add_rail = QPushButton("Add Manual Net")
        lv.addWidget(self.btn_add_rail)
        h_cfg = QHBoxLayout()
        self.btn_save_config = QPushButton("Save Config")
        self.btn_load_config = QPushButton("Load Config")
        h_cfg.addWidget(self.btn_save_config)
        h_cfg.addWidget(self.btn_load_config)
        lv.addLayout(h_cfg)
        splitter.addWidget(left)

        # Right: details
        self.detail_panel = QWidget()
        dv = QVBoxLayout(self.detail_panel)
        dv.setContentsMargins(0, 0, 0, 0)

        props = QGroupBox("Rail Properties")
        form = QFormLayout(props)
        self.txt_voltage = QLineEdit()
        form.addRow("Nominal Voltage (V):", self.txt_voltage)
        dv.addWidget(props)

        self.comp_list = QTreeWidget()
        self.comp_list.setHeaderLabels(["Role", "Ref", "Value", "Pads"])
        self.comp_list.setRootIsDecorated(False)
        self.comp_list.setUniformRowHeights(True)
        for col, width in enumerate((160, 120, 80, 100)):
            self.comp_list.setColumnWidth(col, width)
        dv.addWidget(self.comp_list, 1)

        h_act = QHBoxLayout()
        self.btn_add_src = QPushButton("+ Source")
        self.btn_add_load = QPushButton("+ Load")
        self.btn_add_reg = QPushButton("+ Regulator")
        self.btn_del_comp = QPushButton("- Remove")
        for b in (self.btn_add_src, self.btn_add_load, self.btn_add_reg, self.btn_del_comp):
            h_act.addWidget(b)
        h_act.addStretch(1)
        dv.addLayout(h_act)

        self.detail_panel.setEnabled(False)  # until a rail is selected
        splitter.addWidget(self.detail_panel)
        splitter.setSizes([200, 600])

        self.btn_add_rail.clicked.connect(self.on_add_rail)
        self.btn_save_config.clicked.connect(self.on_save_config)
        self.btn_load_config.clicked.connect(self.on_load_config)
        self.rail_list.currentRowChanged.connect(self.on_rail_select)
        self.txt_voltage.textEdited.connect(self.on_voltage_change)  # user edits only

        self.btn_add_src.clicked.connect(lambda: self.on_add_component("SOURCE"))
        self.btn_add_load.clicked.connect(lambda: self.on_add_component("LOAD"))
        self.btn_add_reg.clicked.connect(self.on_add_regulator)
        self.btn_del_comp.clicked.connect(self.on_del_component)
        self.comp_list.itemActivated.connect(lambda _item, _col: self.on_edit_component())

    def _populate_rail_list(self, select_row=0):
        with QSignalBlocker(self.rail_list):
            self.rail_list.clear()
            self.rail_list.addItems([_rail_label(r) for r in self.rails])
            if self.rails:
                self.rail_list.setCurrentRow(select_row)
        self.on_rail_select(self.rail_list.currentRow())

    # -- rails --------------------------------------------------------------

    def auto_scan(self):
        """Load the project config if present, otherwise scan the board."""
        config_path = self._get_config_path()
        if config_path and config_path.exists():
            try:
                self.rails = load_config(str(config_path))
                self.log(f"Loaded configuration from {config_path.name} ({len(self.rails)} rails)")
            except Exception as e:
                self.log(f"Failed to load config: {e}. Running auto-scan instead.")
                self.rails = self.discoverer.discover_power_nets()
                self.log(f"Scan complete. Discovered {len(self.rails)} power rail candidates.")
        else:
            self.rails = self.discoverer.discover_power_nets()
            self.log(f"Scan complete. Discovered {len(self.rails)} power rail candidates.")
        self._populate_rail_list(0)

    def on_add_rail(self):
        existing = {r.net_name for r in self.rails}
        all_nets = self.discoverer.get_all_net_names()
        self.log(f"Found {len(all_nets)} total nets on board.")
        candidates = [n for n in all_nets if n and n not in existing]

        dlg = NetSelectionDialog(self, candidates)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            net_name = dlg.get_selected_net()
            if net_name:
                rail = PowerRail(net_name=net_name)
                v = self.discoverer._estimate_voltage(net_name)
                if v:
                    rail.nominal_voltage = v
                self.rails.append(rail)
                self._populate_rail_list(len(self.rails) - 1)
        dlg.deleteLater()

    def on_rail_select(self, idx):
        if idx is None or idx < 0 or idx >= len(self.rails):
            self.active_rail = None
            self.detail_panel.setEnabled(False)
            self.comp_list.clear()
            return
        self.active_rail = self.rails[idx]
        self.detail_panel.setEnabled(True)
        v = self.active_rail.nominal_voltage
        self.txt_voltage.setText(str(v) if v else "0.0")
        self.refresh_comp_list()

    def on_voltage_change(self, text):
        if not self.active_rail:
            return
        try:
            self.active_rail.nominal_voltage = float(text)
        except ValueError:
            pass

    # -- component list -----------------------------------------------------

    def _incoming_regulators(self):
        """(source_rail, regulator) pairs whose output feeds the active rail."""
        return [(r, reg) for r in self.rails if r is not self.active_rail
                for reg in r.child_regulators if reg.output_rail_name == self.active_rail.net_name]

    def refresh_comp_list(self):
        self.comp_list.clear()
        if not self.active_rail:
            return
        rows = []
        for s in self.active_rail.sources:
            rows.append(("SOURCE", s.component_ref.ref_des, "---", str(len(s.pad_names))))
        for l in self.active_rail.loads:
            rows.append(("LOAD", l.component_ref.ref_des, f"{l.total_current} A", str(len(l.pad_names))))
        for r in self.active_rail.child_regulators:
            rows.append((f"REG -> {r.output_rail_name}", r.name, "---", r.reg_type))
        for src_rail, reg in self._incoming_regulators():
            rows.append((f"REG FROM {src_rail.net_name}", reg.name, "SOURCE", reg.reg_type))
        self.comp_list.addTopLevelItems([QTreeWidgetItem(list(row)) for row in rows])

    def _selected_row(self):
        items = self.comp_list.selectedItems()
        return self.comp_list.indexOfTopLevelItem(items[0]) if items else -1

    def _available_components(self, skip_source=None, skip_load=None):
        """Components on the active net with at least one pad not already used.

        skip_source/skip_load: index of the entry being edited (its pads count as free).
        Returns (all_components, available_components); all is {} if the net has none.
        """
        all_comps = self.discoverer.get_components_on_net(self.active_rail.net_name)
        used = set()
        for i, src in enumerate(self.active_rail.sources):
            if i != skip_source:
                used.update(f"{src.component_ref.ref_des}-{p}" for p in src.pad_names)
        for i, load in enumerate(self.active_rail.loads):
            if i != skip_load:
                used.update(f"{load.component_ref.ref_des}-{p}" for p in load.pad_names)
        available = {ref: pads for ref, pads in all_comps.items()
                     if any(f"{ref}-{pad_name(p)}" not in used for p in pads)}
        return all_comps, available

    def on_add_component(self, mode):
        if not self.active_rail:
            return
        all_comps, available = self._available_components()
        if not all_comps:
            QMessageBox.information(self, "Ki-PIDA", "No components found on this net.")
            return
        if not available:
            QMessageBox.information(self, "Ki-PIDA", "All components on this net have been assigned.")
            return

        dlg = ComponentSelectorDialog(self, "Select Component", self.active_rail.net_name, available)
        dlg.set_mode(mode)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            ref_des, val, pads = dlg.get_selection()
            ref = ComponentRef(ref_des)
            if mode == "SOURCE":
                self.active_rail.add_source(UnifiedSource(ref, pads))
            else:
                self.active_rail.add_load(UnifiedLoad(ref, val, pads))
            self.refresh_comp_list()
        dlg.deleteLater()

    def on_edit_component(self):
        sel = self._selected_row()
        if sel == -1 or not self.active_rail:
            return
        n_src = len(self.active_rail.sources)
        n_load = len(self.active_rail.loads)
        n_reg = len(self.active_rail.child_regulators)

        if sel < n_src:
            self._edit_source(sel)
        elif sel < n_src + n_load:
            self._edit_load(sel - n_src)
        elif sel < n_src + n_load + n_reg:
            reg = self.active_rail.child_regulators[sel - n_src - n_load]
            self._edit_regulator(reg, self.active_rail)
        else:
            incoming = self._incoming_regulators()
            idx = sel - n_src - n_load - n_reg
            if idx >= len(incoming):
                QMessageBox.warning(self, "Ki-PIDA", "Could not locate regulator.")
                return
            src_rail, reg = incoming[idx]
            self._edit_regulator(reg, src_rail)

    def _edit_source(self, idx):
        comp_obj = self.active_rail.sources[idx]
        all_comps, available = self._available_components(skip_source=idx)
        if not all_comps:
            QMessageBox.information(self, "Ki-PIDA", "No components found on this net.")
            return
        dlg = ComponentSelectorDialog(self, "Edit Source", self.active_rail.net_name, available)
        dlg.set_mode("SOURCE")
        dlg.prepopulate(comp_obj.component_ref.ref_des, 0.0, comp_obj.pad_names)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            ref_des, _val, pads = dlg.get_selection()
            comp_obj.component_ref.ref_des = ref_des
            comp_obj.pad_names = pads
            self.refresh_comp_list()
        dlg.deleteLater()

    def _edit_load(self, idx):
        comp_obj = self.active_rail.loads[idx]
        all_comps, available = self._available_components(skip_load=idx)
        if not all_comps:
            QMessageBox.information(self, "Ki-PIDA", "No components found on this net.")
            return
        dlg = ComponentSelectorDialog(self, "Edit Load", self.active_rail.net_name, available)
        dlg.set_mode("LOAD")
        dlg.prepopulate(comp_obj.component_ref.ref_des, comp_obj.total_current, comp_obj.pad_names)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            ref_des, val, pads = dlg.get_selection()
            comp_obj.component_ref.ref_des = ref_des
            comp_obj.pad_names = pads
            comp_obj.total_current = val
            self.refresh_comp_list()
        dlg.deleteLater()

    def _find_rail(self, name):
        return next((r for r in self.rails if r.net_name == name), None)

    def _edit_regulator(self, reg, owner_rail):
        """Edit a regulator owned by owner_rail (its input rail)."""
        dlg = RegulatorDialog(self, "Edit Regulator", [r.net_name for r in self.rails], self.discoverer,
                              input_rail=reg.input_rail_name, output_rail=reg.output_rail_name)
        dlg.prepopulate(reg)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_value()
            reg.name = data['name']
            reg.input_rail_name = data['input_rail']
            reg.input_ref_des = data['input_ref_des']
            reg.input_pad_names = data['input_pads']
            reg.output_rail_name = data['output_rail']
            reg.output_ref_des = data['output_ref_des']
            reg.output_pad_names = data['output_pads']
            reg.reg_type = data['type']
            reg.efficiency = data['efficiency']

            # Regulators are stored on their input rail: move it if that changed
            if data['input_rail'] != owner_rail.net_name:
                owner_rail.child_regulators.remove(reg)
                target = self._find_rail(data['input_rail'])
                if target:
                    target.add_child_regulator(reg)
                    self.log(f"Regulator moved to {target.net_name}")
            self.refresh_comp_list()
        dlg.deleteLater()

    def on_add_regulator(self):
        if not self.active_rail:
            return
        dlg = RegulatorDialog(self, "Add Regulator", [r.net_name for r in self.rails], self.discoverer,
                              input_rail=self.active_rail.net_name)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_value()
            reg = VoltageRegulator(
                name=data['name'],
                input_rail_name=data['input_rail'],
                input_ref_des=data['input_ref_des'],
                input_pad_names=data['input_pads'],
                output_rail_name=data['output_rail'],
                output_ref_des=data['output_ref_des'],
                output_pad_names=data['output_pads'],
                reg_type=data['type'],
                efficiency=data['efficiency'],
            )
            # The user may have changed the input rail in the dialog
            target = self._find_rail(data['input_rail'])
            if target:
                target.add_child_regulator(reg)
                self.refresh_comp_list()
                if target is not self.active_rail:
                    self.log(f"Regulator added to {target.net_name}")
            else:
                QMessageBox.critical(self, "Ki-PIDA", "Error locating input rail.")
        dlg.deleteLater()

    def on_del_component(self):
        sel = self._selected_row()
        if sel == -1 or not self.active_rail:
            return
        n_src = len(self.active_rail.sources)
        n_load = len(self.active_rail.loads)
        n_reg = len(self.active_rail.child_regulators)
        if sel < n_src:
            self.active_rail.sources.pop(sel)
        elif sel < n_src + n_load:
            self.active_rail.loads.pop(sel - n_src)
        elif sel < n_src + n_load + n_reg:
            self.active_rail.child_regulators.pop(sel - n_src - n_load)
        self.refresh_comp_list()

    # -- config persistence -------------------------------------------------

    def _get_config_path(self):
        """<project dir>/<project name>.kipida.json, or None if unknown."""
        try:
            if self.project and hasattr(self.project, 'path') and hasattr(self.project, 'name'):
                return Path(self.project.path) / f"{self.project.name}.kipida.json"

            # Fallback: derive from the board file name
            if hasattr(self.board, 'filename'):
                project_file = self.board.filename
            elif hasattr(self.board, 'get_filename'):
                project_file = self.board.get_filename()
            else:
                self.log("ERROR: Cannot determine project path - no project object and board has no filename attribute.")
                return None
            if not project_file:
                return None
            project_path = Path(project_file)
            return project_path.parent / f"{project_path.stem}.kipida.json"
        except Exception as e:
            self.log(f"Error getting config path: {e}")
            return None

    def on_save_config(self):
        config_path = self._get_config_path()
        if not config_path:
            QMessageBox.critical(self, "Error", "Unable to determine project path. Cannot save config.")
            return
        try:
            save_config(self.rails, str(config_path))
            self.log(f"Configuration saved to {config_path.name}")
            QMessageBox.information(self, "Success", f"Configuration saved successfully to:\n{config_path}")
        except Exception as e:
            self.log(f"Failed to save config: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save configuration:\n{e}")

    def on_load_config(self):
        config_path = self._get_config_path()
        if not config_path:
            QMessageBox.critical(self, "Error", "Unable to determine project path. Cannot load config.")
            return
        if not config_path.exists():
            QMessageBox.critical(self, "Error", f"Config file not found:\n{config_path}")
            return
        try:
            self.rails = load_config(str(config_path))
            self.log(f"Loaded configuration from {config_path.name} ({len(self.rails)} rails)")
            self._populate_rail_list(0)
            QMessageBox.information(self, "Success", f"Configuration loaded successfully:\n{len(self.rails)} rails")
        except Exception as e:
            self.log(f"Failed to load config: {e}")
            QMessageBox.critical(self, "Error", f"Failed to load configuration:\n{e}")
