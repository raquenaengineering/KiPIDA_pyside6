import sys
import time
import traceback

from PySide6.QtCore import QElapsedTimer, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDoubleSpinBox, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QTabWidget,
    QVBoxLayout, QWidget,
)

from kipida.simulation.system import PowerTreeCycleError, SystemSimulator, format_timings
from kipida.ui.power_tree_panel import PowerTreePanel
from kipida.version import get_full_version, get_window_title
from kipida.visualization.plotter import Plotter


class _StreamToLog:
    """File-like object forwarding print()/stderr output to the log tab."""

    def __init__(self, log_func):
        self.log_func = log_func

    def write(self, msg):
        if msg.strip():
            self.log_func(msg.strip())

    def flush(self):
        pass


class SimulationWorker(QThread):
    """Runs SystemSimulator off the GUI thread so the window stays responsive."""

    log_message = Signal(str)
    succeeded = Signal(object)          # SystemResult
    failed = Signal(object, str)        # exception, formatted traceback

    def __init__(self, board, rails, grid_size_mm, debug, parent=None):
        super().__init__(parent)
        self.board = board
        self.rails = rails
        self.grid_size_mm = grid_size_mm
        self.debug = debug

    def run(self):
        try:
            sim = SystemSimulator(self.board, debug=self.debug, log_callback=self.log_message.emit)
            self.succeeded.emit(sim.run(self.rails, grid_size_mm=self.grid_size_mm))
        except Exception as e:  # reported to the GUI thread
            self.failed.emit(e, traceback.format_exc())


class MainWindow(QMainWindow):
    TAB_CONFIG, TAB_RESULTS, TAB_LOG = 0, 1, 2

    # Thread-safe logging: any thread emits, the GUI thread appends
    _log_signal = Signal(str)

    def __init__(self, board_adapter, project=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(get_window_title())
        self.resize(1000, 700)
        self.setMinimumSize(800, 500)

        self.board = board_adapter
        self.project = project
        self.system_result = None
        self._worker = None

        # Live elapsed-time display while a simulation runs
        self._run_clock = QElapsedTimer()
        self._run_ticker = QTimer(self)
        self._run_ticker.setInterval(100)
        self._run_ticker.timeout.connect(self._update_elapsed_display)

        self._log_signal.connect(self._append_log, Qt.ConnectionType.QueuedConnection)
        self._init_ui()

        # Redirect stdout/stderr to the log tab (restored on close)
        self._orig_stdout, self._orig_stderr = sys.stdout, sys.stderr
        sys.stdout = _StreamToLog(self.log)
        sys.stderr = _StreamToLog(self.log)

        self.log(f"Ki-PIDA {get_full_version()} UI Initialized.")
        if not self.board:
            self.log("ERROR: No board object connected. Plugin will not function properly.")
        else:
            self.log(f"Board object connected: {type(self.board)}")
        if self.project:
            self.log(f"Project: {self.project.name} at {self.project.path}")
        else:
            self.log("WARNING: No project object available.")

        # Scan the board once the window is up
        QTimer.singleShot(0, self.power_tree.auto_scan)

    # -- layout -------------------------------------------------------------

    def _init_ui(self):
        central = QWidget()
        main = QVBoxLayout(central)
        self.setCentralWidget(central)

        self.tabs = QTabWidget()
        main.addWidget(self.tabs, 1)

        # Tab 1: power tree + global settings
        tab_config = QWidget()
        cv = QVBoxLayout(tab_config)
        self.power_tree = PowerTreePanel(self.board, project=self.project, log_callback=self.log)
        cv.addWidget(self.power_tree, 1)

        sett = QHBoxLayout()
        self.spin_grid_size = QDoubleSpinBox()
        self.spin_grid_size.setDecimals(3)
        self.spin_grid_size.setRange(0.01, 10.0)
        self.spin_grid_size.setSingleStep(0.05)
        self.spin_grid_size.setValue(0.1)
        sett.addWidget(QLabel("Mesh Resolution (mm):"))
        sett.addWidget(self.spin_grid_size)
        sett.addSpacing(20)

        self.spin_drop_pct = QDoubleSpinBox()
        self.spin_drop_pct.setDecimals(1)
        self.spin_drop_pct.setRange(0.0, 100.0)
        self.spin_drop_pct.setValue(5.0)
        sett.addWidget(QLabel("Max Drop %:"))
        sett.addWidget(self.spin_drop_pct)
        sett.addSpacing(20)

        self.chk_debug = QCheckBox("Enable Debug Log")
        sett.addWidget(self.chk_debug)
        sett.addStretch(1)
        cv.addLayout(sett)
        self.tabs.addTab(tab_config, "Power Tree && Config")

        # Tab 2: results (stats text above, per-rail plot tabs below)
        self.result_splitter = QSplitter(Qt.Orientation.Vertical)
        self.result_text = QPlainTextEdit()
        self.result_text.setReadOnly(True)
        self.results_tabs = QTabWidget()
        self.result_splitter.addWidget(self.result_text)
        self.result_splitter.addWidget(self.results_tabs)
        self.result_splitter.setSizes([100, 500])
        self.tabs.addTab(self.result_splitter, "Results")

        # Tab 3: log
        self.log_ctrl = QPlainTextEdit()
        self.log_ctrl.setReadOnly(True)
        self.tabs.addTab(self.log_ctrl, "Log")

        # Bottom buttons
        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_run = QPushButton("Run Simulation")
        self.btn_close = QPushButton("Close")
        btns.addWidget(self.btn_run)
        btns.addWidget(self.btn_close)
        main.addLayout(btns)

        self.btn_run.clicked.connect(self.on_run)
        self.btn_close.clicked.connect(self.close)

        self.lbl_timing = QLabel("")
        self.statusBar().addPermanentWidget(self.lbl_timing)

    # -- logging ------------------------------------------------------------

    def log(self, msg):
        """Safe to call from any thread."""
        self._log_signal.emit(str(msg))

    def _append_log(self, msg):
        self.log_ctrl.appendPlainText(msg)

    # -- simulation ---------------------------------------------------------

    def _set_busy(self, busy):
        self.btn_run.setEnabled(not busy)
        self.power_tree.setEnabled(not busy)  # rails must not change mid-solve
        if busy:
            self._run_clock.start()
            self._run_ticker.start()
            self._update_elapsed_display()
        else:
            self._run_ticker.stop()
            self.btn_run.setText("Run Simulation")

    def _update_elapsed_display(self):
        secs = self._run_clock.elapsed() / 1000.0
        self.btn_run.setText(f"Running... {secs:.1f} s")
        self.lbl_timing.setText(f"Simulating: {secs:.1f} s")

    def on_run(self):
        rails = self.power_tree.rails
        if not rails:
            QMessageBox.information(self, "Ki-PIDA", "No power rails defined.")
            return

        self.tabs.setCurrentIndex(self.TAB_LOG)
        self.log(f"--- Starting System Simulation ({len(rails)} rails) ---")
        self._set_busy(True)

        self._worker = SimulationWorker(self.board, rails, self.spin_grid_size.value(), self.chk_debug.isChecked(), self)
        self._worker.log_message.connect(self.log)
        self._worker.succeeded.connect(self._on_sim_succeeded)
        self._worker.failed.connect(self._on_sim_failed)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_sim_succeeded(self, result):
        self.system_result = result
        self._run_ticker.stop()
        self.lbl_timing.setText(f"Simulation {result.elapsed_s:.2f} s | rendering plots...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        t = time.perf_counter()
        try:
            self._update_results_ui()
        finally:
            QApplication.restoreOverrideCursor()
        plot_s = time.perf_counter() - t
        total_s = self._run_clock.elapsed() / 1000.0
        summary = f"Simulation {result.elapsed_s:.2f} s | plots {plot_s:.2f} s | total {total_s:.2f} s"
        self.lbl_timing.setText(summary)
        self.log(summary)

    def _on_sim_failed(self, exc, tb):
        self.lbl_timing.setText(f"Failed after {self._run_clock.elapsed() / 1000.0:.2f} s")
        if isinstance(exc, PowerTreeCycleError):
            self.log(f"ERROR: {exc}")
            QMessageBox.critical(self, "Power Rail Cycle Detected", str(exc))
        else:
            self.log(f"System Solve Error: {exc}")
            self.log(tb)

    def _on_worker_finished(self):
        self._set_busy(False)
        self._worker.deleteLater()
        self._worker = None

    # -- results ------------------------------------------------------------

    def _plot_page(self, png_bytes):
        pix = QPixmap()
        pix.loadFromData(png_bytes, "PNG")
        label = QLabel()
        label.setPixmap(pix)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        area = QScrollArea()
        area.setWidget(label)
        area.setWidgetResizable(True)
        return area

    def _clear_results_tabs(self):
        while self.results_tabs.count():
            page = self.results_tabs.widget(0)
            self.results_tabs.removeTab(0)
            page.deleteLater()

    def _update_results_ui(self):
        result = self.system_result
        txt = "System Simulation Results:\n==========================\n"
        txt += f"Simulation time: {result.elapsed_s:.2f} s\n\n"
        for net, r in result.rails.items():
            txt += f"Rail: {net}\n"
            txt += f"  Range: {r.v_min:.4f} - {r.v_max:.4f} V\n"
            txt += f"  Drop:  {r.drop:.4f} V\n"
            txt += f"  Time:  {r.elapsed_s:.2f} s ({format_timings(r.timings)}, {len(r.mesh.nodes):,} nodes)\n\n"
        self.result_text.setPlainText(txt)

        self._clear_results_tabs()
        if not result.rails:
            return

        stackup = result.stackup
        drop_pct = self.spin_drop_pct.value()
        plotter = Plotter(debug=self.chk_debug.isChecked())
        self.log("Rendering plots...")

        for rail_name, r in result.rails.items():
            rail_tabs = QTabWidget()
            mesh = r.mesh
            mesh.results = r.results  # plotter reads results from the mesh
            # Colour scale: from (1 - drop%) of nominal up to the max voltage
            plot_vmin = r.v_max * (1.0 - drop_pct / 100.0)

            png_3d = plotter.plot_3d_mesh(mesh, stackup, vmin=plot_vmin, vmax=r.v_max)
            if png_3d:
                rail_tabs.addTab(self._plot_page(png_3d), "3D View")

            for lid in sorted({n[2] for n in mesh.node_coords.values()}):
                l_name = str(lid)
                if stackup and 'copper' in stackup and lid in stackup['copper']:
                    l_name = stackup['copper'][lid].get('name', str(lid))
                png_2d = plotter.plot_layer_2d(mesh, lid, stackup, vmin=plot_vmin, vmax=r.v_max, layer_name=l_name)
                if png_2d:
                    rail_tabs.addTab(self._plot_page(png_2d), l_name)

            self.results_tabs.addTab(rail_tabs, rail_name)

        self.tabs.setCurrentIndex(self.TAB_RESULTS)

    # -- lifecycle ----------------------------------------------------------

    def closeEvent(self, event):
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "Ki-PIDA", "A simulation is still running. Please wait for it to finish.")
            event.ignore()
            return
        sys.stdout, sys.stderr = self._orig_stdout, self._orig_stderr
        super().closeEvent(event)
