import sys
import time
import traceback

from PySide6.QtCore import QElapsedTimer, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDoubleSpinBox, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTabWidget,
    QVBoxLayout, QWidget,
)

from kipida.simulation.system import PowerTreeCycleError, SystemSimulator, format_timings
from kipida.ui.power_tree_panel import PowerTreePanel
from kipida.ui.results_view import RailResultsWidget
from kipida.version import get_full_version, get_window_title


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
        self.rail_views = []  # RailResultsWidget per solved rail
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
        self.spin_drop_pct.setToolTip("Colour scale range below each rail's maximum voltage. Updates the plots live.")
        self.spin_drop_pct.valueChanged.connect(self._on_drop_pct_changed)
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
        results_page = QWidget()
        rv = QVBoxLayout(results_page)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(self.result_splitter, 1)
        hint = QLabel("Keys:  1-9 rail   |   ← / → view   |   C fit to canvas   |   F full screen (Esc to leave)")
        hint.setEnabled(False)  # greyed-out hint text
        rv.addWidget(hint)
        self.tabs.addTab(results_page, "Results")

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

        self._init_shortcuts()

    # -- keyboard shortcuts -------------------------------------------------

    def _init_shortcuts(self):
        """Results navigation keys. Only active on the Results tab, so they never
        steal typing or arrow keys from the power-tree editor."""
        self._results_shortcuts = []

        def add(key, slot):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(slot)
            self._results_shortcuts.append(sc)

        add(Qt.Key.Key_C, self.fit_current_view)
        add(Qt.Key.Key_F, self.toggle_full_screen)
        add(Qt.Key.Key_Left, lambda: self.step_view(-1))
        add(Qt.Key.Key_Right, lambda: self.step_view(+1))
        for n in range(1, 10):
            add(Qt.Key.Key_0 + n, lambda n=n: self.select_rail(n - 1))

        # Esc leaves full screen from anywhere
        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        esc.activated.connect(lambda: self.isFullScreen() and self.showNormal())

        self.tabs.currentChanged.connect(self._update_shortcuts_enabled)
        self._update_shortcuts_enabled(self.tabs.currentIndex())

    def _update_shortcuts_enabled(self, tab_index):
        on_results = tab_index == self.TAB_RESULTS
        for sc in self._results_shortcuts:
            sc.setEnabled(on_results)

    def _current_rail_view(self):
        view = self.results_tabs.currentWidget()
        return view if isinstance(view, RailResultsWidget) else None

    def fit_current_view(self):
        view = self._current_rail_view()
        if view:
            view.fit_current_view()

    def step_view(self, delta):
        view = self._current_rail_view()
        if view:
            view.step_view(delta)

    def select_rail(self, index):
        """Show rail number index+1, keeping the same view (e.g. F.Cu) if it has one."""
        if not 0 <= index < self.results_tabs.count():
            return
        old = self._current_rail_view()
        view_name = old.tabText(old.currentIndex()) if old else None
        self.results_tabs.setCurrentIndex(index)
        new = self._current_rail_view()
        if new and view_name:
            for i in range(new.count()):
                if new.tabText(i) == view_name:
                    new.setCurrentIndex(i)
                    break

    def toggle_full_screen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

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

    def _clear_results_tabs(self):
        self.rail_views = []
        while self.results_tabs.count():
            page = self.results_tabs.widget(0)
            self.results_tabs.removeTab(0)
            page.deleteLater()

    def _on_drop_pct_changed(self, drop_pct):
        """Re-colour existing plots live; no re-render or re-run needed."""
        for view in self.rail_views:
            view.set_drop_pct(drop_pct)

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

        self.log("Building plots...")
        drop_pct = self.spin_drop_pct.value()
        for i, (rail_name, r) in enumerate(result.rails.items()):
            view = RailResultsWidget(r, result.stackup, drop_pct, board_outline=result.board_outline)
            self.rail_views.append(view)
            # Number the first 9 rails to match the 1-9 keys
            self.results_tabs.addTab(view, f"{i + 1}: {rail_name}" if i < 9 else rail_name)

        self.tabs.setCurrentIndex(self.TAB_RESULTS)

    # -- lifecycle ----------------------------------------------------------

    def closeEvent(self, event):
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "Ki-PIDA", "A simulation is still running. Please wait for it to finish.")
            event.ignore()
            return
        sys.stdout, sys.stderr = self._orig_stdout, self._orig_stderr
        super().closeEvent(event)
