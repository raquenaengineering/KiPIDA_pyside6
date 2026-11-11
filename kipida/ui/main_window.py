import multiprocessing
import queue
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

from kipida.simulation.estimate import Calibration, estimate_seconds, format_duration
from kipida.simulation.process import run_simulation
from kipida.simulation.system import PowerTreeCycleError, format_timings
from kipida.ui.power_tree_panel import PowerTreePanel
from kipida.ui.results_view import RAIL_TAB_COLOR, RailResultsWidget, highlight_active_tab
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
    """Runs the simulation in a separate process and relays its messages.

    The process can be killed at any moment with stop(), even in the middle of
    meshing or the sparse solve (a thread could not be stopped safely). This
    QThread only waits on the process's message queue, so the window stays
    responsive.
    """

    log_message = Signal(str)
    succeeded = Signal(object)          # SystemResult (slimmed, see simulation.process)
    failed = Signal(object, str)        # exception, formatted traceback
    cancelled = Signal()

    def __init__(self, rails, grid_size_mm, debug, target=None, parent=None):
        super().__init__(parent)
        self.rails = rails
        self.grid_size_mm = grid_size_mm
        self.debug = debug
        self.target = target or run_simulation  # replaceable in tests
        self._process = None
        self._stop_requested = False

    def stop(self):
        """Kill the simulation process now. Safe to call from the GUI thread."""
        self._stop_requested = True
        proc = self._process
        if proc is not None and proc.is_alive():
            proc.kill()

    def run(self):
        ctx = multiprocessing.get_context("spawn")  # never fork a Qt process
        q = ctx.Queue()
        self._process = ctx.Process(target=self.target, args=(q, self.rails, self.grid_size_mm, self.debug),
                                    name="kipida-simulation", daemon=True)
        finished = False
        try:
            self._process.start()
            if self._stop_requested:  # Stop pressed while the process was starting
                self._process.kill()
            while not finished:
                try:
                    msg = q.get(timeout=0.1)
                except queue.Empty:
                    if self._stop_requested or not self._process.is_alive():
                        # Pick up anything sent just before the process ended
                        try:
                            msg = q.get(timeout=0.5)
                        except queue.Empty:
                            break
                    else:
                        continue
                finished = self._handle(msg)
        except Exception as e:  # e.g. the process could not be started
            if not self._stop_requested:
                self.failed.emit(e, traceback.format_exc())
            finished = True
        finally:
            if self._process.is_alive():
                self._process.kill()
            self._process.join(timeout=5)
            exitcode = self._process.exitcode
            q.close()
            q.cancel_join_thread()  # do not block on unread data from a killed process

        if self._stop_requested:
            self.cancelled.emit()
        elif not finished:
            self.failed.emit(RuntimeError(f"The simulation process ended unexpectedly (exit code {exitcode}). "
                                          "This can happen when it runs out of memory, e.g. on a very fine mesh."), "")

    def _handle(self, msg):
        """Process one queue message. Returns True when the run is over."""
        kind = msg[0]
        if kind == "log":
            self.log_message.emit(msg[1])
            return False
        if self._stop_requested:  # a result arriving after Stop is discarded
            return True
        if kind == "result":
            self.succeeded.emit(msg[1])
        elif kind == "error":
            _kind, err_type, text, tb = msg
            exc = PowerTreeCycleError(text) if err_type == "cycle" else RuntimeError(f"{err_type}: {text}")
            self.failed.emit(exc, tb)
        return True


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
        self.simulation_target = None  # None = simulation.process.run_simulation (tests may replace)
        self.calibration = None        # timing model from the last completed run (see simulation.estimate)
        self._run_grid_mm = None

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
        highlight_active_tab(self.results_tabs, RAIL_TAB_COLOR)
        self.result_splitter.addWidget(self.result_text)
        self.result_splitter.addWidget(self.results_tabs)
        self.result_splitter.setSizes([100, 500])
        results_page = QWidget()
        rv = QVBoxLayout(results_page)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(self.result_splitter, 1)
        hint = QLabel("Keys:  R run   |   P stop   |   1-9 rail   |   ← / → view   |   C fit to canvas   |   F full screen (Esc to leave)")
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
        self.lbl_estimate = QLabel()
        btns.addWidget(self.lbl_estimate)
        btns.addSpacing(8)
        self.btn_run = QPushButton("Run Simulation")
        self.btn_run.setToolTip("Run the simulation (R)")
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setToolTip("Stop the running simulation immediately (P)")
        self.btn_stop.setEnabled(False)
        self.btn_close = QPushButton("Close")
        btns.addWidget(self.btn_run)
        btns.addWidget(self.btn_stop)
        btns.addWidget(self.btn_close)
        main.addLayout(btns)

        self.spin_grid_size.valueChanged.connect(self._update_estimate)
        self._update_estimate()

        self.btn_run.clicked.connect(self.on_run)
        self.btn_stop.clicked.connect(self.on_stop)
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

        # Run / stop from any tab. Text fields keep these keys while typing
        # (Qt gives a focused line edit priority over single-letter shortcuts).
        run = QShortcut(QKeySequence(Qt.Key.Key_R), self)
        run.activated.connect(lambda: self.btn_run.isEnabled() and self.on_run())
        stop = QShortcut(QKeySequence(Qt.Key.Key_P), self)
        stop.activated.connect(lambda: self.btn_stop.isEnabled() and self.on_stop())

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
        self.btn_stop.setEnabled(busy)
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

        self._run_grid_mm = self.spin_grid_size.value()
        self._worker = SimulationWorker(rails, self._run_grid_mm, self.chk_debug.isChecked(),
                                        target=self.simulation_target, parent=self)
        self._worker.log_message.connect(self.log)
        self._worker.succeeded.connect(self._on_sim_succeeded)
        self._worker.failed.connect(self._on_sim_failed)
        self._worker.cancelled.connect(self._on_sim_cancelled)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def is_simulating(self):
        return self._worker is not None and self._worker.isRunning()

    def on_stop(self):
        if self.is_simulating():
            self.log("Stopping simulation...")
            self.btn_stop.setEnabled(False)
            self._worker.stop()

    def _on_sim_cancelled(self):
        secs = self._run_clock.elapsed() / 1000.0
        self.lbl_timing.setText(f"Stopped after {secs:.2f} s")
        self.log(f"--- Simulation stopped by the user after {secs:.2f} s ---")

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

        # Re-calibrate the time estimate from this run (only completed runs count)
        if result.rails:
            self.calibration = Calibration.from_run(result, self._run_grid_mm, total_s, plot_s)
            self._update_estimate()

    def _update_estimate(self):
        """Expected run time at the current Mesh Resolution, from the last completed run."""
        if self.calibration is None:
            self.lbl_estimate.setText("Estimate: run once to calibrate")
            self.lbl_estimate.setToolTip("The run-time estimate is calibrated from a completed simulation.")
            return
        grid = self.spin_grid_size.value()
        secs = estimate_seconds(self.calibration, grid)
        self.lbl_estimate.setText(f"Estimated: {format_duration(secs)}")
        self.lbl_estimate.setToolTip(
            f"Expected time at {grid:.3f} mm, scaled from the last run "
            f"({self.calibration.grid_mm:.3f} mm, {self.calibration.wall_s:.1f} s). "
            "Assumes the same board and power tree.")

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
        if self.is_simulating():
            answer = QMessageBox.question(
                self, "Simulation running",
                "A simulation is still running.\n\nStop it and close Ki-PIDA?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            worker = self._worker
            worker.stop()        # kills the simulation process
            worker.wait(10000)   # the relay thread exits as soon as the process is gone
        sys.stdout, sys.stderr = self._orig_stdout, self._orig_stderr
        super().closeEvent(event)
