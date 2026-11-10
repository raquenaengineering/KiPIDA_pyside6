from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QVBoxLayout,
)


def pad_name(pad) -> str:
    """Pad number/name of a kipy pad object."""
    return str(getattr(pad, 'number', getattr(pad, 'name', '')) or '')


class ComponentSelectorDialog(QDialog):
    """Pick a component on a net (and its pads) to act as a source or load."""

    def __init__(self, parent, title, net_name, components):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.net_name = net_name
        self.components = components  # { ref_des: [pad_objs] }
        self.mode = "LOAD"

        self._init_ui()
        self.resize(400, 500)

    def _init_ui(self):
        vbox = QVBoxLayout(self)

        self.lbl_instruction = QLabel("Select Component:")
        vbox.addWidget(self.lbl_instruction)

        self.lst_comps = QListWidget()
        self.lst_comps.setSortingEnabled(True)
        self.comp_map = {}
        if isinstance(self.components, dict):
            for ref, pads in self.components.items():
                self.lst_comps.addItem(ref)
                self.comp_map[ref] = pads
        else:  # legacy: list of objects
            for c in self.components:
                ref = getattr(c, 'ref_des', str(c))
                self.lst_comps.addItem(ref)
                self.comp_map[ref] = c
        vbox.addWidget(self.lst_comps, 2)

        h_val = QHBoxLayout()
        self.lbl_val = QLabel("Value:")
        self.txt_val = QLineEdit()
        h_val.addWidget(self.lbl_val)
        h_val.addWidget(self.txt_val, 1)
        vbox.addLayout(h_val)

        vbox.addWidget(QLabel("Select Pads:"))
        self.chk_all_pads = QCheckBox("Use all connected pads")
        self.chk_all_pads.setChecked(True)
        vbox.addWidget(self.chk_all_pads)

        self.pad_list = QListWidget()
        self.pad_list.setEnabled(False)  # enabled when "use all" is unchecked
        vbox.addWidget(self.pad_list, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        vbox.addWidget(buttons)

        self.lst_comps.currentRowChanged.connect(self.on_select)
        self.lst_comps.itemDoubleClicked.connect(lambda _item: self._on_ok())
        self.chk_all_pads.toggled.connect(lambda use_all: self.pad_list.setEnabled(not use_all))

    def set_mode(self, mode):
        self.mode = mode
        if mode == "SOURCE":
            self.lbl_instruction.setText("Select Component to use as Voltage Source:")
            # Voltage is set at rail level
            self.lbl_val.hide()
            self.txt_val.hide()
        else:
            self.lbl_instruction.setText("Select Component to use as Load:")
            self.lbl_val.setText("Current (A):")
            self.txt_val.setText("1.0")
            self.lbl_val.show()
            self.txt_val.show()

    def _selected_ref(self):
        item = self.lst_comps.currentItem()
        return item.text() if item else None

    def _pad_names_of(self, ref):
        comp_data = self.comp_map.get(ref)
        pads = comp_data if isinstance(comp_data, list) else getattr(comp_data, 'pads', [])
        return [n for n in (pad_name(p) for p in pads) if n]

    def on_select(self, _row=None):
        """Refill the pad list for the selected component (all checked)."""
        self.pad_list.clear()
        ref = self._selected_ref()
        if not ref:
            return
        for name in self._pad_names_of(ref):
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.pad_list.addItem(item)

    def _on_ok(self):
        if not self._selected_ref():
            QMessageBox.warning(self, "Validation Error", "Please select a component.")
            return
        if self.mode == "LOAD":
            try:
                if float(self.txt_val.text()) < 0:
                    raise ValueError
            except ValueError:
                QMessageBox.warning(self, "Validation Error", "Current must be a number (A) >= 0.")
                return
        self.accept()

    def get_selection(self):
        """Returns (ref_des, value, [pad_names])."""
        ref = self._selected_ref()
        if not ref:
            return None, 0.0, []

        # Use mode, not widget visibility: widgets report hidden once the dialog closes
        val = 0.0
        if self.mode == "LOAD":
            try:
                val = float(self.txt_val.text())
            except ValueError:
                val = 0.0

        if self.chk_all_pads.isChecked():
            pads = self._pad_names_of(ref)
        else:
            pads = [self.pad_list.item(i).text() for i in range(self.pad_list.count())
                    if self.pad_list.item(i).checkState() == Qt.CheckState.Checked]
        return ref, val, pads

    def prepopulate(self, ref_des, value, pads):
        """Pre-set the dialog state for editing."""
        matches = self.lst_comps.findItems(ref_des, Qt.MatchFlag.MatchExactly)
        if not matches:
            return
        self.lst_comps.setCurrentItem(matches[0])  # triggers on_select -> pad list

        if self.mode == "LOAD":
            self.txt_val.setText(str(value))

        all_comp_pads = [self.pad_list.item(i).text() for i in range(self.pad_list.count())]
        if set(all_comp_pads) == set(pads):
            self.chk_all_pads.setChecked(True)
        else:
            self.chk_all_pads.setChecked(False)
            for i in range(self.pad_list.count()):
                item = self.pad_list.item(i)
                item.setCheckState(Qt.CheckState.Checked if item.text() in pads else Qt.CheckState.Unchecked)
