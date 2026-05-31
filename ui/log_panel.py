"""CSV data logging panel."""

from __future__ import annotations

import csv
import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QFileDialog, QLineEdit,
    QHeaderView, QFrame,
)

from instruments import HantekRLC1733C, Measurement

_DARK_BG = "#1A1A2A"
_CARD_BG = "#252535"
_TEXT    = "#FFFFFF"
_DIM     = "#8888AA"


class LogPanel(QWidget):
    """Auto-logs measurements to CSV and shows a live table."""

    def __init__(self, device: HantekRLC1733C, parent=None) -> None:
        super().__init__(parent)
        self._device = device
        self._log_file: Path | None = None
        self._writer: csv.DictWriter | None = None
        self._fh = None
        self._rows: list[dict] = []
        self.setStyleSheet(f"background: {_DARK_BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ── toolbar ──
        bar = QFrame()
        bar.setStyleSheet(f"background: {_CARD_BG}; border-radius: 6px;")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(8, 6, 8, 6)
        bar_layout.setSpacing(6)

        self._lbl_file = QLabel("No file selected")
        self._lbl_file.setStyleSheet(f"color: {_DIM}; font-size: 9pt;")
        bar_layout.addWidget(self._lbl_file, 1)

        self._btn_choose = self._btn("📁  File…", "#607D8B")
        self._btn_start  = self._btn("▶  Start Log", "#4CAF50")
        self._btn_stop   = self._btn("■  Stop Log",  "#f44336")
        self._btn_clear  = self._btn("Clear",         "#455A64")
        self._btn_stop.setEnabled(False)

        bar_layout.addWidget(self._btn_choose)
        bar_layout.addWidget(self._btn_start)
        bar_layout.addWidget(self._btn_stop)
        bar_layout.addWidget(self._btn_clear)
        layout.addWidget(bar)

        # ── interval control ──
        row = QHBoxLayout()
        _lbl_interval = QLabel("Log interval (s):")
        _lbl_interval.setStyleSheet(f"color: {_DIM}; font-size: 9pt;")
        row.addWidget(_lbl_interval)
        self._interval_edit = QLineEdit("1.0")
        self._interval_edit.setFixedWidth(60)
        self._interval_edit.setStyleSheet(
            f"background: #333350; color: {_TEXT}; border: 1px solid #555; border-radius:3px; padding:2px 4px;")
        row.addWidget(self._interval_edit)
        row.addStretch()
        self._lbl_count = QLabel("0 rows")
        self._lbl_count.setStyleSheet(f"color: {_DIM}; font-size: 9pt;")
        row.addWidget(self._lbl_count)
        layout.addLayout(row)

        # ── table ──
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            ["Timestamp", "Mode", "Frequency", "Primary", "Secondary", "Phase", "OL"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.setStyleSheet(f"""
            QTableWidget {{
                background: {_CARD_BG}; color: {_TEXT}; gridline-color: #333;
                border: none; font-size: 9pt;
            }}
            QHeaderView::section {{
                background: #1E1E30; color: {_DIM}; border: 1px solid #333; padding: 4px;
            }}
            QTableWidget::item:selected {{ background: #4C97FF; }}
        """)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table)

        # wiring
        self._btn_choose.clicked.connect(self._choose_file)
        self._btn_start.clicked.connect(self._start_log)
        self._btn_stop.clicked.connect(self._stop_log)
        self._btn_clear.clicked.connect(self._clear)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._log_row)

    @staticmethod
    def _btn(label: str, color: str) -> QPushButton:
        b = QPushButton(label)
        b.setFixedHeight(28)
        b.setStyleSheet(f"""
            QPushButton {{
                background: {color}; color: white; border-radius: 4px;
                font-size: 9pt; font-weight: bold; padding: 0 10px;
            }}
            QPushButton:hover {{ background: {color}cc; }}
            QPushButton:disabled {{ background: #444; color: #666; }}
        """)
        return b

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save CSV log", str(Path.home() / "measurement_log.csv"),
            "CSV Files (*.csv)")
        if path:
            self._log_file = Path(path)
            self._lbl_file.setText(str(self._log_file))

    def _start_log(self) -> None:
        if not self._log_file:
            self._choose_file()
        if not self._log_file:
            return
        interval = float(self._interval_edit.text() or 1.0) * 1000
        self._fh = open(self._log_file, "w", newline="", encoding="utf-8")
        fieldnames = ["timestamp", "mode", "frequency", "primary", "secondary", "phase", "overload"]
        self._writer = csv.DictWriter(self._fh, fieldnames=fieldnames)
        self._writer.writeheader()
        self._timer.start(int(interval))
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)

    def _stop_log(self) -> None:
        self._timer.stop()
        if self._fh:
            self._fh.close()
            self._fh = None
        self._writer = None
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)

    def _log_row(self) -> None:
        m = self._device.measure()
        row = {
            "timestamp": f"{time.strftime('%Y-%m-%d %H:%M:%S')}.{int(time.time() * 1000) % 1000:03d}",
            "mode":      m.mode_label,
            "frequency": m.frequency.label,
            "primary":   m.primary,
            "secondary": m.secondary,
            "phase":     m.phase,
            "overload":  int(m.overload),
        }
        if self._writer:
            self._writer.writerow(row)
            self._fh.flush()
        self._rows.append(row)
        self._add_table_row(row)
        self._lbl_count.setText(f"{len(self._rows)} rows")

    def _add_table_row(self, row: dict) -> None:
        r = self._table.rowCount()
        self._table.insertRow(r)
        vals = [
            row["timestamp"],
            row["mode"],
            row["frequency"],
            f"{row['primary']:.6g}",
            f"{row['secondary']:.6g}",
            f"{row['phase']:.2f}°",
            "OL" if row["overload"] else "",
        ]
        for col, val in enumerate(vals):
            item = QTableWidgetItem(str(val))
            item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self._table.setItem(r, col, item)
        self._table.scrollToBottom()

    def _clear(self) -> None:
        self._table.setRowCount(0)
        self._rows.clear()
        self._lbl_count.setText("0 rows")

    def log_external(self, label: str, value) -> None:
        """Called by block executor to add a row."""
        if self._writer:
            m = self._device.measure()
            self._log_row()

