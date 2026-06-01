"""
BlocklyCanvas – PySide6 widget that embeds Google Blockly via QWebEngineView.

The page loads Blockly from CDN, registers custom LCR-meter blocks, and
communicates with Python through a QWebChannel (bridge object).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QUrl, QTimer
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QFrame, QLabel,
)
from PySide6.QtGui import QColor

from .blockly_bridge import BlocklyBridge

_ACCENT  = "#4C97FF"
_DARK    = "#0F0F1A"
_CARD    = "#252535"
_DIM     = "#8888AA"
_TEXT    = "#FFFFFF"

_HTML_PATH = Path(__file__).parent / "blockly_page.html"


class _ConsolePage(QWebEnginePage):
    """Forwards console.log/warn/error from JS to Python stdout for debugging."""
    def javaScriptConsoleMessage(self, level, msg, line, src):
        tag = {0: "LOG", 1: "WARN", 2: "ERR", 3: "INFO"}.get(level, "?")
        print(f"[Blockly {tag}] {msg}  (line {line})")


class BlocklyCanvas(QWidget):
    """
    Full Blockly editor embedded in a PySide6 widget.

    Signals:
        program_changed   – emitted whenever the workspace changes
        run_requested     – run button clicked
        stop_requested    – stop button clicked
    """

    program_changed = Signal()
    run_requested   = Signal()
    stop_requested  = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._bridge = BlocklyBridge(self)
        self._ready  = False   # True once JS has finished loading

        self._build_ui()
        self._setup_channel()
        self._load_page()

        self._bridge.add_change_callback(self._on_workspace_change)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # toolbar
        toolbar = self._build_toolbar()
        root.addWidget(toolbar)

        # web view
        self._view = QWebEngineView()
        self._view.setPage(_ConsolePage(self._view))
        settings = self._view.page().settings()
        settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        root.addWidget(self._view, 1)

    def _build_toolbar(self) -> QFrame:
        bar = QFrame()
        bar.setFixedHeight(40)
        bar.setStyleSheet(f"background: {_DARK}; border-bottom: 1px solid #333355;")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        def btn(label: str, bg: str, hover: str) -> QPushButton:
            b = QPushButton(label)
            b.setFixedHeight(28)
            b.setStyleSheet(f"""
                QPushButton {{
                    background: {bg}; color: white; border-radius: 4px;
                    font-weight: bold; padding: 0 14px; font-size: 9pt;
                }}
                QPushButton:hover   {{ background: {hover}; }}
                QPushButton:disabled {{ background: #444; color: #777; }}
            """)
            return b

        self._btn_run   = btn("▶  Run",    "#4CAF50", "#66BB6A")
        self._btn_stop  = btn("■  Stop",   "#f44336", "#EF5350")
        self._btn_clear = btn("Clear",     "#607D8B", "#78909C")

        self._btn_run.clicked.connect(self.run_requested)
        self._btn_stop.clicked.connect(self.stop_requested)

        # Initial state: nothing is running yet
        self._btn_stop.setEnabled(False)
        self._btn_clear.clicked.connect(self.clear_workspace)

        layout.addWidget(self._btn_run)
        layout.addWidget(self._btn_stop)
        layout.addStretch()

        # loading indicator
        self._lbl_loading = QLabel("Loading Blockly…")
        self._lbl_loading.setStyleSheet(f"color: {_DIM}; font-size: 9pt;")
        layout.addWidget(self._lbl_loading)

        layout.addWidget(self._btn_clear)
        return bar

    # ── WebChannel / page setup ───────────────────────────────────────────────

    def _setup_channel(self) -> None:
        self._channel = QWebChannel(self)
        self._channel.registerObject("bridge", self._bridge)
        self._view.page().setWebChannel(self._channel)

    def _load_page(self) -> None:
        url = QUrl.fromLocalFile(str(_HTML_PATH))
        self._view.load(url)
        self._view.loadFinished.connect(self._on_load_finished)

    def _on_load_finished(self, ok: bool) -> None:
        if ok:
            self._ready = True
            self._lbl_loading.setVisible(False)
        else:
            self._lbl_loading.setText("⚠  Failed to load Blockly")
            self._lbl_loading.setStyleSheet("color: #f44336; font-size: 9pt;")

    def _on_workspace_change(self, xml: str, json_str: str) -> None:
        self.program_changed.emit()

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def run_button(self) -> QPushButton:
        return self._btn_run

    @property
    def stop_button(self) -> QPushButton:
        return self._btn_stop

    @property
    def bridge(self) -> BlocklyBridge:
        return self._bridge

    @property
    def program(self) -> dict:
        return self._bridge.program

    def set_running(self, running: bool) -> None:
        """Grey out Run while executing; grey out Stop while idle."""
        self._btn_run.setEnabled(not running)
        self._btn_stop.setEnabled(running)

    def set_run_status(self, msg: str) -> None:
        self._bridge.set_status(msg)

    def set_message(self, msg: str) -> None:
        self._bridge.set_message(msg)

    def clear_workspace(self) -> None:
        self._view.page().runJavaScript("clearWorkspace();")
        self.program_changed.emit()

    def get_program_dict(self) -> dict:
        return self._bridge.program

    def get_workspace_xml(self) -> str:
        return self._bridge.save_xml()

    def load_workspace_xml(self, xml: str) -> None:
        """Restore a workspace from saved XML."""
        self._bridge.restore_xml(xml)
        if self._ready:
            safe = xml.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
            self._view.page().runJavaScript(f"loadXml(`{safe}`);")

    def load_program_dict(self, d: dict) -> None:
        """Compatibility shim – accepts the old {stacks:[…]} format but ignores it
        (Blockly state is stored as XML). Use load_workspace_xml for real restoration."""
        xml = d.get("_xml", "")
        if xml:
            self.load_workspace_xml(xml)

    def get_program_dict_for_save(self) -> dict:
        """Returns a dict suitable for JSON serialisation including the raw XML."""
        return {"_xml": self.get_workspace_xml(), "stacks": self.program.get("stacks", [])}
