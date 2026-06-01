"""UART / serial message terminal panel."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt, QMetaObject, Q_ARG, Slot
from PySide6.QtGui import QTextCharFormat, QColor, QTextCursor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QTextEdit, QPushButton, QLabel, QCheckBox, QFrame,
)

_DARK    = "#1A1A2A"
_CARD    = "#151520"
_ACCENT  = "#4C97FF"
_TEXT    = "#FFFFFF"
_DIM     = "#8888AA"

_TX_COLOR  = "#4C97FF"   # blue  – outgoing
_RX_COLOR  = "#4CAF50"   # green – incoming
_TS_COLOR  = "#555570"   # dim grey – timestamp
_ERR_COLOR = "#f44336"

_PANEL_STYLE = f"""
QWidget {{ background: {_DARK}; color: {_TEXT}; }}
QTabWidget::pane {{ border: 1px solid #333355; border-radius: 4px; background: {_DARK}; }}
QTabBar::tab {{
    background: #252535; color: {_DIM}; padding: 5px 14px;
    border-top-left-radius: 4px; border-top-right-radius: 4px; font-size: 9pt;
}}
QTabBar::tab:selected {{ background: {_ACCENT}; color: white; }}
QTabBar::tab:hover {{ background: #333360; color: white; }}
QPushButton {{
    background: #333350; color: {_TEXT}; border: 1px solid #555580;
    border-radius: 3px; padding: 3px 10px; font-size: 8pt;
}}
QPushButton:hover {{ background: #444470; }}
QCheckBox {{ color: {_DIM}; font-size: 8pt; }}
"""

_TERM_STYLE = f"""
QTextEdit {{
    background: {_CARD};
    color: {_TEXT};
    border: none;
    border-radius: 4px;
    font-family: Consolas, 'Courier New', monospace;
    font-size: 9pt;
    selection-background-color: #333360;
}}
"""


class _TermWidget(QWidget):
    """Single instrument terminal pane."""

    MAX_LINES = 2000

    def __init__(self, instrument_name: str, parent=None) -> None:
        super().__init__(parent)
        self._name = instrument_name
        self._line_count = 0
        self.setStyleSheet(f"background: {_DARK};")

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # toolbar
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self._lbl_count = QLabel("0 messages")
        self._lbl_count.setStyleSheet(f"color: {_DIM}; font-size: 8pt;")
        bar.addWidget(self._lbl_count)

        bar.addStretch()

        self._chk_scroll = QCheckBox("Auto-scroll")
        self._chk_scroll.setChecked(True)
        bar.addWidget(self._chk_scroll)

        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(self._clear)
        bar.addWidget(btn_clear)

        root.addLayout(bar)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #222240; margin: 0;")
        root.addWidget(sep)

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setStyleSheet(_TERM_STYLE)
        self._text.setUndoRedoEnabled(False)
        root.addWidget(self._text, 1)

    def append_message(self, direction: str, message: str) -> None:
        """Thread-safe: marshals the append to the Qt main thread."""
        QMetaObject.invokeMethod(
            self, "_append_impl",
            Qt.QueuedConnection,
            Q_ARG(str, direction),
            Q_ARG(str, message),
        )

    def _clear(self) -> None:
        self._text.clear()
        self._line_count = 0
        self._lbl_count.setText("0 messages")

    @Slot(str, str)
    def _append_impl(self, direction: str, message: str) -> None:
        # trim oldest lines when cap is reached
        if self._line_count >= self.MAX_LINES:
            cursor = self._text.textCursor()
            cursor.movePosition(QTextCursor.Start)
            cursor.movePosition(QTextCursor.Down, QTextCursor.KeepAnchor, 200)
            cursor.removeSelectedText()
            self._line_count -= 200

        cursor = self._text.textCursor()
        cursor.movePosition(QTextCursor.End)

        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        # timestamp
        fmt_ts = QTextCharFormat()
        fmt_ts.setForeground(QColor(_TS_COLOR))
        cursor.setCharFormat(fmt_ts)
        cursor.insertText(ts + "  ")

        # direction badge
        fmt_dir = QTextCharFormat()
        fmt_dir.setFontWeight(700)
        if direction == "TX":
            fmt_dir.setForeground(QColor(_TX_COLOR))
        elif direction == "RX":
            fmt_dir.setForeground(QColor(_RX_COLOR))
        else:
            fmt_dir.setForeground(QColor(_ERR_COLOR))
        cursor.setCharFormat(fmt_dir)
        cursor.insertText(f"{direction:<3}")

        # message body
        fmt_msg = QTextCharFormat()
        fmt_msg.setForeground(QColor(_TEXT))
        cursor.setCharFormat(fmt_msg)
        cursor.insertText("  " + message + "\n")

        self._line_count += 1
        self._lbl_count.setText(f"{self._line_count} messages")

        if self._chk_scroll.isChecked():
            self._text.setTextCursor(cursor)
            self._text.ensureCursorVisible()


class UartTerminalPanel(QWidget):
    """
    Multi-instrument UART / serial message terminal.

    Each entry in `instruments` must be a driver that exposes
    `set_log_callback(cb)` where cb(direction, message) is called per
    TX/RX event.  The panel wires itself up automatically.
    """

    def __init__(self, instruments: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet(_PANEL_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._tabs = QTabWidget()
        self._terms: dict[str, _TermWidget] = {}

        _short = {
            "Hantek RLC 1733C":   "LCR",
            "OWON SP3103":        "PSU",
            "ZES Zimmer LMG450":  "LMG450",
            "Magtrol DSP7000":    "DSP7000",
        }

        for name, device in instruments.items():
            term = _TermWidget(name)
            self._terms[name] = term
            self._tabs.addTab(term, _short.get(name, name))

            if hasattr(device, "set_log_callback"):
                def _make_cb(t: _TermWidget):
                    def cb(direction: str, message: str) -> None:
                        t.append_message(direction, message)
                    return cb
                device.set_log_callback(_make_cb(term))

        root.addWidget(self._tabs)
