"""Main application window."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QSize, QObject, Signal
from PySide6.QtGui import QColor, QAction
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QTabWidget,
    QLabel, QPushButton, QFrame, QStatusBar, QToolBar, QFileDialog,
    QMessageBox, QInputDialog, QSplitter,
)

from instruments import HantekRLC1733C, OwonSP3103, LMG450, MagtrolDSP7000, Array3721A
from scratch import BlocklyCanvas, BlocklyExecutor, BlockProgram
from config import ConfigManager
from .measurement_panel import MeasurementPanel
from .log_panel import LogPanel
from .power_supply_panel import PowerSupplyPanel
from .lmg450_panel import LMG450Panel
from .uart_config_dialog import UartConfigDialog, SerialConfig
from .dashboard_panel import DashboardPanel
from .dsp7000_panel import DSP7000Panel
from .uart_terminal_panel import UartTerminalPanel
from .ai_panel import AIPanel
from .eload_panel import ELoadPanel

_DARK   = "#1A1A2A"
_CARD   = "#252535"
_ACCENT = "#4C97FF"
_TEXT   = "#FFFFFF"
_DIM    = "#8888AA"

_APP_STYLESHEET = f"""
QMainWindow, QWidget {{ background: {_DARK}; color: {_TEXT}; }}
QTabWidget::pane {{ border: 1px solid #333355; border-radius: 6px; background: {_DARK}; }}
QTabBar::tab {{
    background: #252535; color: {_DIM}; padding: 8px 18px;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
    font-size: 10pt;
}}
QTabBar::tab:selected {{ background: {_ACCENT}; color: white; }}
QTabBar::tab:hover {{ background: #333360; color: white; }}
QStatusBar {{ background: #0F0F1A; color: {_DIM}; font-size: 8pt; }}
QToolBar {{ background: #0F0F1A; border: none; spacing: 4px; padding: 2px 6px; }}
QMenuBar {{ background: #0F0F1A; color: {_TEXT}; }}
QMenuBar::item:selected {{ background: {_ACCENT}; }}
QMenu {{ background: #252535; color: {_TEXT}; border: 1px solid #333; }}
QMenu::item:selected {{ background: {_ACCENT}; }}
QSplitter::handle {{ background: #333355; }}
"""


# ── non-visual connection helper ──────────────────────────────────────────────

class _InstrumentConn(QObject):
    """
    Holds serial config for one instrument and handles connect / disconnect.
    Emits `status_changed(text, colour_hex)` whenever the connection state changes.
    """

    status_changed = Signal(str, str)   # (text, colour_hex) — "Connected" | "Disconnected"

    def __init__(self, device, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._device     = device
        self._serial_cfg = SerialConfig()

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def serial_cfg(self) -> SerialConfig:
        return self._serial_cfg

    def set_serial_config(self, cfg: SerialConfig) -> None:
        self._serial_cfg = cfg

    def do_connect(self) -> bool:
        ok, info = self._device.connect_with_config(self._serial_cfg)
        if ok:
            self.status_changed.emit("Connected", "#4CAF50")
        else:
            self._emit_disconnected()
            return False
        return True

    def do_disconnect(self) -> None:
        self._device.disconnect()
        self._emit_disconnected()

    def do_connect_with_warning(self, parent_widget) -> None:
        """connect and show QMessageBox on failure."""
        ok, info = self._device.connect_with_config(self._serial_cfg)
        if ok:
            self.status_changed.emit("Connected", "#4CAF50")
        else:
            self._emit_disconnected()
            QMessageBox.warning(parent_widget, "Connection Failed",
                                f"Could not connect:\n{info}")

    def _emit_disconnected(self) -> None:
        self.status_changed.emit("Disconnected", "#f44336")


# ── read-only instrument status strip ─────────────────────────────────────────

class _StatusStrip(QFrame):
    """
    A single thin bar that shows name + coloured status dot for every
    instrument.  No buttons — purely informational.
    """

    _DOT_DISCONNECTED = ("○", "#f44336")
    _DOT_OK           = ("●", "#4CAF50")
    _DOT_SIM          = ("●", "#FF9800")

    def __init__(self, instruments: list[tuple[str, _InstrumentConn]], parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        self.setStyleSheet(
            "background: #0F0F1A; border-bottom: 1px solid #222240;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(0)

        self._status_labels: dict[str, QLabel] = {}

        for i, (title, conn) in enumerate(instruments):
            if i > 0:
                sep = QFrame()
                sep.setFrameShape(QFrame.VLine)
                sep.setStyleSheet("color: #333355; margin: 4px 16px;")
                layout.addWidget(sep)

            name_lbl = QLabel(title)
            name_lbl.setStyleSheet(
                f"color: {_DIM}; font-size: 8pt; font-weight: bold;"
                f" padding-right: 6px;")
            layout.addWidget(name_lbl)

            status_lbl = QLabel("○  Disconnected")
            status_lbl.setStyleSheet("color: #f44336; font-size: 8pt;")
            layout.addWidget(status_lbl)
            self._status_labels[title] = status_lbl

            conn.status_changed.connect(
                lambda txt, col, lbl=status_lbl:
                    (lbl.setText(f"●  {txt}" if txt != "Disconnected" else "○  Disconnected"),
                     lbl.setStyleSheet(f"color: {col}; font-size: 8pt;"))
            )

        layout.addStretch()


# ── main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("eLabOrchestra")
        self.resize(1400, 860)
        self.setStyleSheet(_APP_STYLESHEET)

        # ── instruments ───────────────────────────────────────────────────────
        self._device = HantekRLC1733C()
        self._psu    = OwonSP3103()
        self._lmg    = LMG450()
        self._dsp    = MagtrolDSP7000()
        self._eload  = Array3721A()

        # ── connection helpers (hold serial config, no UI) ────────────────────
        self._lcr_conn   = _InstrumentConn(self._device, parent=self)
        self._psu_conn   = _InstrumentConn(self._psu,    parent=self)
        self._lmg_conn   = _InstrumentConn(self._lmg,    parent=self)
        self._dsp_conn   = _InstrumentConn(self._dsp,    parent=self)
        self._eload_conn = _InstrumentConn(self._eload,  parent=self)

        self._config    = ConfigManager()
        self._dashboard = DashboardPanel()
        self._executor  = BlocklyExecutor(
            device=self._device, psu=self._psu, lmg=self._lmg,
            dsp=self._dsp, dashboard=self._dashboard, eload=self._eload)

        self._build_ui()
        self._build_menu()
        self._build_toolbar()
        self._connect_signals()

        # ── restore last session ──────────────────────────────────────────────
        cfg = self._config.load("last_session")

        try:
            d = cfg.get("serial") or {}
            self._lcr_conn.set_serial_config(
                SerialConfig.from_dict(d) if d else SerialConfig())
        except Exception:
            pass

        try:
            d = cfg.get("psu_serial") or {}
            self._psu_conn.set_serial_config(
                SerialConfig.from_dict(d) if d else SerialConfig())
        except Exception:
            pass

        try:
            d = cfg.get("lmg_serial") or {}
            self._lmg_conn.set_serial_config(
                SerialConfig.from_dict(d) if d else
                SerialConfig(baudrate=57600))
        except Exception:
            pass

        try:
            d = cfg.get("dsp_serial") or {}
            self._dsp_conn.set_serial_config(
                SerialConfig.from_dict(d) if d else
                SerialConfig(baudrate=9600))
        except Exception:
            pass

        try:
            d = cfg.get("eload_serial") or {}
            self._eload_conn.set_serial_config(
                SerialConfig.from_dict(d) if d else
                SerialConfig(baudrate=9600))
        except Exception:
            pass

        # auto-connect all instruments
        self._lcr_conn.do_connect()
        self._psu_conn.do_connect()
        self._lmg_conn.do_connect()
        self._dsp_conn.do_connect()
        self._eload_conn.do_connect()

        # restore program XML
        prog_cfg = cfg.get("program", {})
        saved_xml = prog_cfg.get("_xml", "") if isinstance(prog_cfg, dict) else ""
        if saved_xml:
            self._scratch.bridge.restore_xml(saved_xml)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # slim read-only status strip
        self._status_strip = _StatusStrip([
            ("Hantek RLC 1733C",       self._lcr_conn),
            ("OWON SP3103",            self._psu_conn),
            ("ZES Zimmer LMG450",      self._lmg_conn),
            ("Magtrol DSP7000",        self._dsp_conn),
            ("Array 3721A",            self._eload_conn),
        ])
        root.addWidget(self._status_strip)

        # main splitter
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(4)

        left = QTabWidget()
        left.setMinimumWidth(380)

        self._meas_panel = MeasurementPanel(self._device)
        left.addTab(self._meas_panel, "📊  LCR Meter")

        self._psu_panel = PowerSupplyPanel(self._psu)
        left.addTab(self._psu_panel, "⚡  Power Supply")

        self._lmg_panel = LMG450Panel(self._lmg)
        left.addTab(self._lmg_panel, "⚛  LMG450")

        self._dsp_panel = DSP7000Panel(self._dsp)
        left.addTab(self._dsp_panel, "⚙  DSP7000")

        self._eload_panel = ELoadPanel(self._eload)
        left.addTab(self._eload_panel, "🔋  E-Load")

        self._log_panel = LogPanel(self._device, self._psu, self._lmg, self._dsp)
        left.addTab(self._log_panel, "📋  Data Log")

        splitter.addWidget(left)

        right_tabs = QTabWidget()
        right_tabs.setMinimumWidth(600)

        self._scratch = BlocklyCanvas()
        right_tabs.addTab(self._scratch, "🔧  Blockly")
        right_tabs.addTab(self._dashboard, "📊  Dashboard")

        self._uart_terminal = UartTerminalPanel({
            "Hantek RLC 1733C":  self._device,
            "OWON SP3103":       self._psu,
            "ZES Zimmer LMG450": self._lmg,
            "Magtrol DSP7000":   self._dsp,
            "Array 3721A":       self._eload,
        })
        right_tabs.addTab(self._uart_terminal, "🖥  UART Terminal")

        self._ai_panel = AIPanel(blockly_canvas=self._scratch)
        right_tabs.addTab(self._ai_panel, "🤖  AI Assistant")

        splitter.addWidget(right_tabs)

        splitter.setSizes([420, 980])
        root.addWidget(splitter, 1)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("Ready")

    # ── menus ─────────────────────────────────────────────────────────────────

    def _build_menu(self) -> None:
        mb = self.menuBar()

        # ── File ──────────────────────────────────────────────────────────────
        file_menu = mb.addMenu("&File")
        file_menu.addAction("New Program",        self._new_program,     "Ctrl+N")
        file_menu.addAction("Open Program…",      self._open_program,    "Ctrl+O")
        file_menu.addAction("Save Program",       self._save_program,    "Ctrl+S")
        file_menu.addAction("Save Program As…",   self._save_program_as, "Ctrl+Shift+S")
        file_menu.addSeparator()
        file_menu.addAction("Save Config Profile…", self._save_profile)
        file_menu.addAction("Load Config Profile…", self._load_profile)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close, "Alt+F4")

        # ── LCR Meter ─────────────────────────────────────────────────────────
        lcr_menu = mb.addMenu("&LCR Meter")
        lcr_menu.addAction("Serial Port Configuration…",
                           self._open_lcr_uart_config, "Ctrl+U")
        lcr_menu.addSeparator()
        lcr_menu.addAction("Connect",    self._lcr_connect,    "F7")
        lcr_menu.addAction("Disconnect", self._lcr_disconnect, "F8")

        # ── Power Supply ──────────────────────────────────────────────────────
        psu_menu = mb.addMenu("&Power Supply")
        psu_menu.addAction("Serial Port Configuration…",
                           self._open_psu_uart_config, "Ctrl+P")
        psu_menu.addSeparator()
        psu_menu.addAction("Connect",    self._psu_connect,    "F9")
        psu_menu.addAction("Disconnect", self._psu_disconnect, "F10")
        psu_menu.addSeparator()
        psu_menu.addAction("Enable Output",  lambda: self._psu_output(True))
        psu_menu.addAction("Disable Output", lambda: self._psu_output(False))

        # ── LMG450 ────────────────────────────────────────────────────────────
        lmg_menu = mb.addMenu("L&MG450")
        lmg_menu.addAction("Serial Port Configuration…",
                           self._open_lmg_uart_config, "Ctrl+L")
        lmg_menu.addSeparator()
        lmg_menu.addAction("Connect",    self._lmg_connect,    "F11")
        lmg_menu.addAction("Disconnect", self._lmg_disconnect, "F12")
        lmg_menu.addSeparator()
        lmg_menu.addAction("Start Integration",
            lambda: self._lmg.integration_start() if self._lmg.connected else None)
        lmg_menu.addAction("Stop Integration",
            lambda: self._lmg.integration_stop()  if self._lmg.connected else None)
        lmg_menu.addAction("Reset Integration",
            lambda: self._lmg.integration_reset() if self._lmg.connected else None)
        lmg_menu.addSeparator()
        ch_menu = lmg_menu.addMenu("Select Channel")
        for ch in range(1, 5):
            ch_menu.addAction(f"Channel {ch}",
                              lambda c=ch: self._lmg_select_channel(c))
        lmg_menu.addSeparator()
        lmg_menu.addAction("Refresh Harmonics", self._lmg_refresh_harmonics)

        # ── DSP7000 ───────────────────────────────────────────────────────────
        dsp_menu = mb.addMenu("&DSP7000")
        dsp_menu.addAction("Serial Port Configuration…",
                           self._open_dsp_uart_config, "Ctrl+D")
        dsp_menu.addSeparator()
        dsp_menu.addAction("Connect",    self._dsp_connect,    "F3")
        dsp_menu.addAction("Disconnect", self._dsp_disconnect, "F4")
        dsp_menu.addSeparator()
        dsp_ch_menu = dsp_menu.addMenu("Active Channel")
        for _ch in range(1, 3):
            dsp_ch_menu.addAction(f"Channel {_ch}",
                                  lambda c=_ch: self._dsp_panel._ch1._dsp.set_speed(c, 0))
        dsp_menu.addSeparator()
        dsp_menu.addAction("Reset Channel 1",
                           lambda: self._dsp.reset_channel(1) if self._dsp.connected else None)
        dsp_menu.addAction("Reset Channel 2",
                           lambda: self._dsp.reset_channel(2) if self._dsp.connected else None)
        dsp_menu.addSeparator()
        dsp_menu.addAction("Abort Ramp Ch1",
                           lambda: self._dsp.abort_ramp(1) if self._dsp.connected else None)
        dsp_menu.addAction("Abort Ramp Ch2",
                           lambda: self._dsp.abort_ramp(2) if self._dsp.connected else None)
        dsp_menu.addSeparator()
        dsp_menu.addAction("Save Config",
                           lambda: (self._dsp.save(1), self._dsp.save(2)))

        # ── E-Load ────────────────────────────────────────────────────────────
        eload_menu = mb.addMenu("&E-Load")
        eload_menu.addAction("Serial Port Configuration…",
                             self._open_eload_uart_config)
        eload_menu.addSeparator()
        eload_menu.addAction("Connect",    self._eload_connect)
        eload_menu.addAction("Disconnect", self._eload_disconnect)
        eload_menu.addSeparator()
        eload_menu.addAction("Enable Input",  lambda: self._eload_input(True))
        eload_menu.addAction("Disable Input", lambda: self._eload_input(False))

        # ── Blockly ───────────────────────────────────────────────────────────
        blockly_menu = mb.addMenu("&Blockly")
        blockly_menu.addAction("Clear Workspace",  self._new_program,      "Ctrl+Shift+N")
        blockly_menu.addSeparator()
        blockly_menu.addAction("Open Program…",    self._open_program,     "Ctrl+O")
        blockly_menu.addAction("Save Program",     self._save_program,     "Ctrl+S")
        blockly_menu.addAction("Save Program As…", self._save_program_as,  "Ctrl+Shift+S")
        blockly_menu.addSeparator()
        blockly_menu.addAction("Run Program",      self._run_program,      "F5")
        blockly_menu.addAction("Stop Program",     self._stop_program,     "F6")
        blockly_menu.addSeparator()
        blockly_menu.addAction("Zoom In",  lambda: self._blockly_zoom(+1), "Ctrl+=")
        blockly_menu.addAction("Zoom Out", lambda: self._blockly_zoom(-1), "Ctrl+-")
        blockly_menu.addAction("Fit to Screen",    self._blockly_fit,      "Ctrl+Shift+F")

        # ── Help ──────────────────────────────────────────────────────────────
        help_menu = mb.addMenu("&Help")
        help_menu.addAction("About", self._about)

    # ── toolbar ───────────────────────────────────────────────────────────────

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(tb)

        def add_btn(label: str, color: str, slot) -> QPushButton:
            b = QPushButton(label)
            b.setFixedHeight(28)
            b.setStyleSheet(
                f"QPushButton {{ background:{color}; color:white; border-radius:4px;"
                f" font-weight:bold; padding:0 12px; margin:2px; }}"
                f"QPushButton:hover {{ background:{QColor(color).lighter(120).name()}; }}"
                f"QPushButton:disabled {{ background:#444; color:#777; }}"
            )
            b.clicked.connect(slot)
            tb.addWidget(b)
            return b

        self._tb_run  = add_btn("▶  Run",  "#4CAF50", self._run_program)
        self._tb_stop = add_btn("■  Stop", "#f44336", self._stop_program)
        self._tb_stop.setEnabled(False)   # nothing running on startup
        tb.addSeparator()
        add_btn("💾  Save", "#607D8B", self._save_program)
        add_btn("📂  Open", "#607D8B", self._open_program)

    # ── signal wiring ─────────────────────────────────────────────────────────

    def _connect_signals(self) -> None:
        self._scratch.run_requested.connect(self._run_program)
        self._scratch.stop_requested.connect(self._stop_program)

        def on_show(label, value):
            msg = f"{label} = {value}"
            self._status.showMessage(msg, 4000)
            self._scratch.set_message(msg)

        def on_log(label, value):
            self._log_panel.log_external(label, value)

        self._executor.add_show_callback(on_show)
        self._executor.add_log_callback(on_log)

    # ── Blockly actions ───────────────────────────────────────────────────────

    def _set_instrument_polling(self, active: bool) -> None:
        """Pause or resume all instrument panel timers (but not the log timer)."""
        for panel in (self._meas_panel, self._psu_panel,
                      self._lmg_panel, self._dsp_panel, self._eload_panel):
            if active:
                panel._timer.start(500)
            else:
                panel._timer.stop()

    def _set_blockly_running(self, running: bool) -> None:
        self._tb_run.setEnabled(not running)
        self._tb_stop.setEnabled(running)
        self._scratch.set_running(running)

    def _run_program(self) -> None:
        self._set_instrument_polling(False)
        prog = self._scratch.get_program_dict()
        self._executor.run(prog)
        self._set_blockly_running(True)
        self._scratch.set_run_status("Running…")
        self._status.showMessage("Program running…")

    def _stop_program(self) -> None:
        self._executor.stop()
        self._set_instrument_polling(True)
        self._set_blockly_running(False)
        self._scratch.set_run_status("Idle")
        self._status.showMessage("Program stopped.")

    def _new_program(self) -> None:
        self._scratch.clear_workspace()
        self._current_file: Path | None = None

    def _open_program(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Program", str(Path.home()),
            "eLabOrchestra Program (*.elpo);;XML (*.xml);;JSON (*.json)")
        if path:
            try:
                raw = Path(path).read_text(encoding="utf-8")
                if path.endswith(".xml") or raw.strip().startswith("<"):
                    self._scratch.load_workspace_xml(raw)
                else:
                    d = json.loads(raw)
                    xml = d.get("_xml", "")
                    if xml:
                        self._scratch.load_workspace_xml(xml)
                    else:
                        self._scratch.load_program_dict(d)
                self._current_file = Path(path)
                self._status.showMessage(f"Opened {path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not open file:\n{e}")

    def _save_program(self) -> None:
        if not hasattr(self, "_current_file") or not self._current_file:
            self._save_program_as()
            return
        self._write_program(self._current_file)

    def _save_program_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Program", str(Path.home() / "program.elpo"),
            "eLabOrchestra Program (*.elpo);;JSON (*.json)")
        if path:
            self._current_file = Path(path)
            self._write_program(self._current_file)

    def _write_program(self, path: Path) -> None:
        try:
            xml = self._scratch.get_workspace_xml()
            if str(path).endswith(".xml"):
                path.write_text(xml, encoding="utf-8")
            else:
                d = {"_xml": xml, "stacks": self._scratch.program.get("stacks", [])}
                path.write_text(json.dumps(d, indent=2), encoding="utf-8")
            self._status.showMessage(f"Saved to {path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not save:\n{e}")

    def _blockly_zoom(self, direction: int) -> None:
        js = "workspace.zoomCenter(1);" if direction > 0 else "workspace.zoomCenter(-1);"
        self._scratch._view.page().runJavaScript(js)

    def _blockly_fit(self) -> None:
        self._scratch._view.page().runJavaScript("workspace.scrollCenter();")

    # ── LCR Meter actions ─────────────────────────────────────────────────────

    def _open_lcr_uart_config(self) -> None:
        dlg = UartConfigDialog(self._lcr_conn.serial_cfg, parent=self)
        if dlg.exec():
            self._lcr_conn.set_serial_config(dlg.config)
            if self._device.connected:
                self._device.disconnect()
            self._lcr_conn.do_connect_with_warning(self)
            self._status.showMessage(
                f"LCR serial port: {dlg.config.port}  {dlg.config.baudrate}")

    def _lcr_connect(self) -> None:
        if not self._device.connected:
            self._lcr_conn.do_connect_with_warning(self)

    def _lcr_disconnect(self) -> None:
        if self._device.connected:
            self._lcr_conn.do_disconnect()
            self._status.showMessage("LCR meter disconnected.")

    # ── Power Supply actions ──────────────────────────────────────────────────

    def _open_psu_uart_config(self) -> None:
        dlg = UartConfigDialog(self._psu_conn.serial_cfg, parent=self)
        if dlg.exec():
            self._psu_conn.set_serial_config(dlg.config)
            if self._psu.connected:
                self._psu.disconnect()
            self._psu_conn.do_connect_with_warning(self)
            self._status.showMessage(
                f"PSU serial port: {dlg.config.port}  {dlg.config.baudrate}")

    def _psu_connect(self) -> None:
        if not self._psu.connected:
            self._psu_conn.do_connect_with_warning(self)

    def _psu_disconnect(self) -> None:
        if self._psu.connected:
            self._psu_conn.do_disconnect()
            self._status.showMessage("Power supply disconnected.")

    def _psu_output(self, on: bool) -> None:
        if self._psu.connected:
            self._psu.set_output(on)
            self._psu_panel._output_on = on
            self._psu_panel._refresh_output_ui()
            self._status.showMessage(f"PSU output {'ON' if on else 'OFF'}")

    # ── LMG450 actions ────────────────────────────────────────────────────────

    def _open_lmg_uart_config(self) -> None:
        dlg = UartConfigDialog(self._lmg_conn.serial_cfg, parent=self)
        if dlg.exec():
            self._lmg_conn.set_serial_config(dlg.config)
            if self._lmg.connected:
                self._lmg.disconnect()
            self._lmg_conn.do_connect_with_warning(self)
            self._status.showMessage(
                f"LMG serial port: {dlg.config.port}  {dlg.config.baudrate}")

    def _lmg_connect(self) -> None:
        if not self._lmg.connected:
            self._lmg_conn.do_connect_with_warning(self)

    def _lmg_disconnect(self) -> None:
        if self._lmg.connected:
            self._lmg_conn.do_disconnect()
            self._status.showMessage("LMG450 disconnected.")

    def _lmg_select_channel(self, ch: int) -> None:
        if self._lmg.connected:
            self._lmg.select_channel(ch)
            self._lmg_panel._combo_ch.setCurrentIndex(ch - 1)
            self._status.showMessage(f"LMG450: channel {ch} selected.")

    def _lmg_refresh_harmonics(self) -> None:
        self._lmg_panel._harm_tab.refresh()

    # ── DSP7000 actions ───────────────────────────────────────────────────────

    def _open_dsp_uart_config(self) -> None:
        from .uart_config_dialog import UartConfigDialog as _UCD
        cfg = self._dsp_conn.serial_cfg or SerialConfig(baudrate=9600)
        dlg = _UCD(cfg, parent=self)
        if dlg.exec():
            self._dsp_conn.set_serial_config(dlg.config)
            self._dsp_conn.do_connect_with_warning(self)
            self._status.showMessage(
                f"DSP7000 serial port: {dlg.config.port}  {dlg.config.baudrate}")

    def _dsp_connect(self) -> None:
        self._dsp_conn.do_connect_with_warning(self)

    def _dsp_disconnect(self) -> None:
        self._dsp_conn.do_disconnect()
        self._status.showMessage("DSP7000 disconnected.")

    # ── E-Load actions ────────────────────────────────────────────────────────

    def _open_eload_uart_config(self) -> None:
        cfg = self._eload_conn.serial_cfg or SerialConfig(baudrate=9600)
        dlg = UartConfigDialog(cfg, parent=self)
        if dlg.exec():
            self._eload_conn.set_serial_config(dlg.config)
            self._eload_conn.do_connect_with_warning(self)
            self._status.showMessage(
                f"E-Load serial port: {dlg.config.port}  {dlg.config.baudrate}")

    def _eload_connect(self) -> None:
        self._eload_conn.do_connect_with_warning(self)

    def _eload_disconnect(self) -> None:
        self._eload_conn.do_disconnect()
        self._status.showMessage("Array 3721A disconnected.")

    def _eload_input(self, on: bool) -> None:
        if self._eload.connected:
            self._eload.set_input(on)
            self._eload_panel._input_on = on
            self._eload_panel._refresh_input_ui()
            self._status.showMessage(f"E-Load input {'ON' if on else 'OFF'}")

    # ── Profile save/load ─────────────────────────────────────────────────────

    def _save_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "Save Profile", "Profile name:")
        if ok and name:
            self._config.set("program",    self._scratch.get_program_dict_for_save())
            self._config.set("device",     self._device.get_config())
            self._config.set("serial",     self._lcr_conn.serial_cfg.to_dict())
            self._config.set("psu_device", self._psu.get_config())
            self._config.set("psu_serial", self._psu_conn.serial_cfg.to_dict())
            self._config.set("lmg_device", self._lmg.get_config())
            self._config.set("lmg_serial", self._lmg_conn.serial_cfg.to_dict())
            if self._dsp_conn.serial_cfg:
                self._config.set("dsp_serial", self._dsp_conn.serial_cfg.to_dict())
            self._config.set("eload_device", self._eload.get_config())
            self._config.set("eload_serial", self._eload_conn.serial_cfg.to_dict())
            self._config.save(name)
            self._status.showMessage(f"Profile '{name}' saved.")

    def _load_profile(self) -> None:
        profiles = self._config.list_profiles()
        if not profiles:
            QMessageBox.information(self, "Load Profile", "No profiles found.")
            return
        name, ok = QInputDialog.getItem(
            self, "Load Profile", "Select profile:", profiles, 0, False)
        if ok and name:
            cfg = self._config.load(name)
            if cfg.get("program"):
                self._scratch.load_program_dict(cfg["program"])
            if cfg.get("device"):
                self._device.apply_config(cfg["device"])
            if cfg.get("serial"):
                try:
                    self._lcr_conn.set_serial_config(
                        SerialConfig.from_dict(cfg["serial"]))
                except Exception:
                    pass
            if cfg.get("psu_device"):
                self._psu.apply_config(cfg["psu_device"])
            if cfg.get("psu_serial"):
                try:
                    self._psu_conn.set_serial_config(
                        SerialConfig.from_dict(cfg["psu_serial"]))
                except Exception:
                    pass
            if cfg.get("lmg_device"):
                self._lmg.apply_config(cfg["lmg_device"])
            if cfg.get("lmg_serial"):
                try:
                    self._lmg_conn.set_serial_config(
                        SerialConfig.from_dict(cfg["lmg_serial"]))
                except Exception:
                    pass
            if cfg.get("dsp_serial"):
                try:
                    self._dsp_conn.set_serial_config(
                        SerialConfig.from_dict(cfg["dsp_serial"]))
                except Exception:
                    pass
            if cfg.get("eload_device"):
                self._eload.apply_config(cfg["eload_device"])
            if cfg.get("eload_serial"):
                try:
                    self._eload_conn.set_serial_config(
                        SerialConfig.from_dict(cfg["eload_serial"]))
                except Exception:
                    pass
            self._status.showMessage(f"Profile '{name}' loaded.")

    # ── Help ──────────────────────────────────────────────────────────────────

    def _about(self) -> None:
        QMessageBox.about(self, "About eLabOrchestra",
            "<b>eLabOrchestra</b><br>"
            "Electronic instrument control &amp; automation.<br><br>"
            "Instruments: Hantek RLC 1733C · OWON SP3103 · ZES Zimmer LMG450 · Magtrol DSP7000 · Array 3721A<br>"
            "Visual programming: Google Blockly v10.4.3<br>"
            "<small>v0.3  —  eLabOrchestra project</small>")

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        self._set_instrument_polling(False)
        self._executor.stop()
        self._config.set("program",    self._scratch.get_program_dict_for_save())
        self._config.set("device",     self._device.get_config())
        self._config.set("serial",     self._lcr_conn.serial_cfg.to_dict())
        self._config.set("psu_device", self._psu.get_config())
        self._config.set("psu_serial", self._psu_conn.serial_cfg.to_dict())
        self._config.set("lmg_device",   self._lmg.get_config())
        self._config.set("lmg_serial",   self._lmg_conn.serial_cfg.to_dict())
        self._config.set("eload_device", self._eload.get_config())
        self._config.set("eload_serial", self._eload_conn.serial_cfg.to_dict())
        self._config.save("last_session")
        self._device.disconnect()
        self._psu.disconnect()
        self._lmg.disconnect()
        self._eload.disconnect()
        event.accept()
