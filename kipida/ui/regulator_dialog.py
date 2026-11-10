from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QVBoxLayout,
)

from kipida.core.models import generate_regulator_name
from kipida.ui.component_selector import pad_name


def _checked_texts(lst: QListWidget):
    return [lst.item(i).text() for i in range(lst.count())
            if lst.item(i).checkState() == Qt.CheckState.Checked]


def _set_check_items(lst: QListWidget, names, checked=()):
    lst.clear()
    for name in names:
        item = QListWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if name in checked else Qt.CheckState.Unchecked)
        lst.addItem(item)


class RegulatorDialog(QDialog):
    """Define a regulator: input rail/component/pads -> output rail/component/pads."""

    def __init__(self, parent, title, available_rails, discoverer, input_rail=None, output_rail=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(500, 600)

        self.available_rails = sorted(available_rails)
        self.discoverer = discoverer
        self.input_rail = input_rail
        self.output_rail = output_rail

        self.input_comps = {}   # ref -> [pads]
        self.output_comps = {}  # ref -> [pads]

        self._init_ui()

        if self.input_rail:
            self._on_input_rail_change()
        if self.output_rail:
            self._on_output_rail_change()

    def _side_group(self, title, rail_label, comp_label, pads_label):
        box = QGroupBox(title)
        form = QFormLayout(box)
        cmb_rail = QComboBox()
        cmb_rail.addItems(self.available_rails)
        cmb_rail.setCurrentIndex(-1)
        cmb_comp = QComboBox()
        lst_pads = QListWidget()
        lst_pads.setMinimumHeight(80)
        form.addRow(rail_label, cmb_rail)
        form.addRow(comp_label, cmb_comp)
        form.addRow(pads_label, lst_pads)
        return box, cmb_rail, cmb_comp, lst_pads

    def _init_ui(self):
        vbox = QVBoxLayout(self)

        h_name = QHBoxLayout()
        h_name.addWidget(QLabel("Regulator Name:"))
        self.txt_name = QLineEdit()
        self.txt_name.setReadOnly(True)
        h_name.addWidget(self.txt_name, 1)
        vbox.addLayout(h_name)

        box_in, self.cmb_input_rail, self.cmb_input_comp, self.lst_input_pads = self._side_group(
            "Input Side", "Input Rail:", "Input Component:", "Input Pads:")
        box_out, self.cmb_output_rail, self.cmb_output_comp, self.lst_output_pads = self._side_group(
            "Output Side", "Output Rail:", "Output Component:", "Output Pads:")
        vbox.addWidget(box_in)
        vbox.addWidget(box_out)

        if self.input_rail:
            self.cmb_input_rail.setCurrentText(self.input_rail)
        if self.output_rail:
            self.cmb_output_rail.setCurrentText(self.output_rail)

        box_p = QGroupBox("Parameters")
        form_p = QFormLayout(box_p)
        self.cmb_type = QComboBox()
        self.cmb_type.addItems(["LINEAR", "SWITCHING"])
        self.txt_eff = QLineEdit("0.85")
        self.txt_eff.setEnabled(False)
        form_p.addRow("Type:", self.cmb_type)
        form_p.addRow("Efficiency (0.0-1.0):", self.txt_eff)
        vbox.addWidget(box_p)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        vbox.addWidget(buttons)

        # `activated` fires on user selection only (like wx EVT_COMBOBOX);
        # programmatic changes call the handlers explicitly.
        self.cmb_input_rail.activated.connect(lambda _i: self._on_input_rail_change())
        self.cmb_input_comp.activated.connect(lambda _i: self._on_input_comp_change())
        self.cmb_output_rail.activated.connect(lambda _i: self._on_output_rail_change())
        self.cmb_output_comp.activated.connect(lambda _i: self._on_output_comp_change())
        self.cmb_type.currentTextChanged.connect(self._on_type_change)

    # -- population ---------------------------------------------------------

    def _on_input_rail_change(self):
        self.input_comps = self.discoverer.get_components_on_net(self.cmb_input_rail.currentText())
        refs = sorted(self.input_comps.keys())
        self.cmb_input_comp.clear()
        self.cmb_input_comp.addItems(refs)
        if refs:
            self.cmb_input_comp.setCurrentIndex(0)
            self._on_input_comp_change()
        else:
            self.lst_input_pads.clear()
            self._update_auto_name()

    def _on_input_comp_change(self):
        pads = self.input_comps.get(self.cmb_input_comp.currentText(), [])
        _set_check_items(self.lst_input_pads, [pad_name(p) or str(p) for p in pads])
        self._update_auto_name()

    def _on_output_rail_change(self):
        self.output_comps = self.discoverer.get_components_on_net(self.cmb_output_rail.currentText())
        refs = sorted(self.output_comps.keys())
        self.cmb_output_comp.clear()
        self.cmb_output_comp.addItems(refs)
        if refs:
            self.cmb_output_comp.setCurrentIndex(0)
            self._on_output_comp_change()
        else:
            self.lst_output_pads.clear()
            self._update_auto_name()

    def _on_output_comp_change(self):
        pads = self.output_comps.get(self.cmb_output_comp.currentText(), [])
        _set_check_items(self.lst_output_pads, [pad_name(p) or str(p) for p in pads])
        self._update_auto_name()

    def _on_type_change(self, reg_type):
        self.txt_eff.setEnabled(reg_type == "SWITCHING")

    def _update_auto_name(self):
        self.txt_name.setText(generate_regulator_name(
            self.cmb_input_comp.currentText(),
            self.cmb_output_comp.currentText(),
            self.cmb_output_rail.currentText()))

    def prepopulate(self, regulator):
        """Fill the dialog from an existing VoltageRegulator."""
        self.cmb_input_rail.setCurrentText(regulator.input_rail_name)
        self.cmb_output_rail.setCurrentText(regulator.output_rail_name)
        self._on_input_rail_change()
        self._on_output_rail_change()

        self.cmb_input_comp.setCurrentText(regulator.input_ref_des)
        self.cmb_output_comp.setCurrentText(regulator.output_ref_des)
        self._on_input_comp_change()
        self._on_output_comp_change()

        for lst, wanted in ((self.lst_input_pads, regulator.input_pad_names),
                            (self.lst_output_pads, regulator.output_pad_names)):
            for i in range(lst.count()):
                if lst.item(i).text() in wanted:
                    lst.item(i).setCheckState(Qt.CheckState.Checked)

        self.cmb_type.setCurrentText(regulator.reg_type)
        self.txt_eff.setText(str(regulator.efficiency))
        self._on_type_change(regulator.reg_type)
        # Name is read-only and auto-generated from the selection (as in the wx version)

    # -- result -------------------------------------------------------------

    def _error(self, msg):
        QMessageBox.critical(self, "Error", msg)

    def _on_ok(self):
        if not self.txt_name.text().strip():
            return self._error("Name is required.")

        inp = self.cmb_input_rail.currentText()
        out = self.cmb_output_rail.currentText()
        if not inp or not out:
            return self._error("Select both Input and Output rails.")
        if inp == out:
            return self._error("Input and Output rails cannot be the same.")
        if not self.cmb_input_comp.currentText() or not _checked_texts(self.lst_input_pads):
            return self._error("Select Input Component and at least one Pad.")
        if not self.cmb_output_comp.currentText() or not _checked_texts(self.lst_output_pads):
            return self._error("Select Output Component and at least one Pad.")

        if self.cmb_type.currentText() == "SWITCHING":
            try:
                eff = float(self.txt_eff.text())
                if not (0.0 < eff <= 1.0):
                    raise ValueError
            except ValueError:
                return self._error("Efficiency must be between 0.0 and 1.0")

        self.accept()

    def get_value(self):
        switching = self.cmb_type.currentText() == "SWITCHING"
        return {
            'name': self.txt_name.text().strip(),
            'input_rail': self.cmb_input_rail.currentText(),
            'input_ref_des': self.cmb_input_comp.currentText(),
            'input_pads': _checked_texts(self.lst_input_pads),
            'output_rail': self.cmb_output_rail.currentText(),
            'output_ref_des': self.cmb_output_comp.currentText(),
            'output_pads': _checked_texts(self.lst_output_pads),
            'type': self.cmb_type.currentText(),
            'efficiency': float(self.txt_eff.text()) if switching else 1.0,
        }
