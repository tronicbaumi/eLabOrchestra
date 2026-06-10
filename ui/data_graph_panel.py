"""
Data Graph Panel — advanced plot of all logged measurement data.

Features:
  • pyqtgraph PlotWidget with native zoom (scroll-wheel / right-drag) and
    pan (left-drag); "Reset View" fits all data back into the viewport
  • Time axis with readable date/time ticks (DateAxisItem)
  • Scrollable signal-selector on the left with per-signal show/hide checkboxes
  • Colour-coded movable legend inside the plot
  • "Select All" / "Deselect All" helpers
  • Auto-refresh every second while logging is active (toggleable)
  • Export to PNG (pyqtgraph ImageExporter)
  • Export to CSV (only the currently visible signals)

The panel holds a reference to LogPanel so it can read `_rows` and
`_active_keys` directly without any extra signal wiring.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QCheckBox, QFileDialog, QSizePolicy,
    QSplitter,
)
from PySide6.QtGui import QColor

try:
    import pyqtgraph as pg
    from pyqtgraph.exporters import ImageExporter
    _PG_AVAILABLE = True
except ImportError:
    _PG_AVAILABLE = False

if TYPE_CHECKING:
    from .log_panel import LogPanel

# ── colours ───────────────────────────────────────────────────────────────────
_DARK   = "#1A1A2A"
_CARD   = "#252535"
_TEXT   = "#FFFFFF"
_DIM    = "#8888AA"
_ACCENT = "#4C97FF"

# 20-colour palette for signal curves (distinct, readable on dark background)
_PALETTE = [
    "#4C97FF", "#FF6B6B", "#4CAF50", "#FFD54F", "#CE93D8",
    "#4DD0E1", "#FF8A65", "#A5D6A7", "#F48FB1", "#80DEEA",
    "#FFCC02", "#EF9A9A", "#80CBC4", "#BCAAA4", "#E6EE9C",
    "#B39DDB", "#90CAF9", "#FFAB40", "#69F0AE", "#FF4081",
]


# ── helpers ───────────────────────────────────────────────────────────────────

def _btn(label: str, color: str, min_w: int = 80) -> QPushButton:
    b = QPushButton(label)
    b.setFixedHeight(26)
    b.setMinimumWidth(min_w)
    b.setStyleSheet(f"""
        QPushButton {{
            background:{color}; color:white; border-radius:4px;
            font-size:8pt; font-weight:bold; padding:0 8px;
        }}
        QPushButton:hover   {{ background:{color}cc; }}
        QPushButton:disabled {{ background:#444; color:#666; }}
        QPushButton:checked  {{ background:{color}; border:2px solid white; }}
    """)
    return b


def _parse_ts(ts: str) -> float:
    """Convert 'YYYY-MM-DD HH:MM:SS.mmm' to POSIX float."""
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S.%f").timestamp()
    except ValueError:
        try:
            return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            return 0.0


def _to_float(val) -> float | None:
    """Try to parse a cell value as float; return None on failure."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ── fallback widget when pyqtgraph is not installed ───────────────────────────

class _MissingPg(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lbl = QLabel(
            "pyqtgraph is not installed.\n\n"
            "Run:  pip install pyqtgraph\n\n"
            "then restart the application."
        )
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(f"color:{_DIM}; font-size:10pt;")
        lay.addWidget(lbl)
        self._timer = QTimer()   # no-op stub so main_window can call _timer.stop()


# ── main panel ────────────────────────────────────────────────────────────────

class DataGraphPanel(QWidget):
    """
    Advanced graph panel.  Pass the LogPanel instance so the graph can
    read its `_rows` list and column metadata directly.
    """

    def __init__(self, log_panel: "LogPanel", parent=None) -> None:
        super().__init__(parent)
        self._log    = log_panel
        self._last_n = 0               # row count at last refresh
        self._curves: dict[str, "pg.PlotDataItem"] = {}
        self._checks: dict[str, QCheckBox]          = {}
        self._color_map: dict[str, str]             = {}

        self.setStyleSheet(f"background:{_DARK};")

        if not _PG_AVAILABLE:
            QVBoxLayout(self).addWidget(_MissingPg(self))
            self._timer = QTimer()
            return

        self._setup_pyqtgraph()
        self._build_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._auto_refresh)
        self._timer.start(1000)

    # ── pyqtgraph global config ───────────────────────────────────────────────

    def _setup_pyqtgraph(self) -> None:
        pg.setConfigOption("background", _DARK)
        pg.setConfigOption("foreground", _TEXT)
        pg.setConfigOption("antialias",  True)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(4)
        splitter.setStyleSheet("QSplitter::handle { background:#333355; }")
        splitter.addWidget(self._build_signal_selector())
        splitter.addWidget(self._build_plot())
        splitter.setSizes([200, 800])
        root.addWidget(splitter, 1)

    def _build_toolbar(self) -> QFrame:
        bar = QFrame()
        bar.setStyleSheet(f"background:{_CARD}; border-bottom:1px solid #333355;")
        bar.setFixedHeight(36)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)

        self._btn_auto = _btn("⏵  Auto", "#546E7A", 72)
        self._btn_auto.setCheckable(True)
        self._btn_auto.setChecked(True)
        self._btn_auto.setToolTip("Toggle automatic 1-second refresh")
        lay.addWidget(self._btn_auto)

        self._btn_refresh = _btn("↻  Refresh", "#607D8B", 80)
        self._btn_refresh.clicked.connect(self.refresh)
        lay.addWidget(self._btn_refresh)

        self._btn_reset = _btn("⊡  Fit", "#455A64", 56)
        self._btn_reset.setCheckable(True)
        self._btn_reset.setChecked(True)   # before connect — plot not built yet
        self._btn_reset.toggled.connect(self._on_fit_toggled)
        self._btn_reset.setToolTip(
            "Keep the view scaled so all incoming data stays visible.\n"
            "Zooming or panning manually pauses this; click again to re-enable.")
        lay.addWidget(self._btn_reset)

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color:#333355; margin:4px 2px;")
        lay.addWidget(sep)

        btn_all = _btn("All", "#37474F", 44)
        btn_all.clicked.connect(self._select_all)
        lay.addWidget(btn_all)

        btn_none = _btn("None", "#37474F", 52)
        btn_none.clicked.connect(self._deselect_all)
        lay.addWidget(btn_none)

        lay.addStretch()

        self._lbl_rows = QLabel("0 rows")
        self._lbl_rows.setStyleSheet(f"color:{_DIM}; font-size:8pt;")
        lay.addWidget(self._lbl_rows)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.VLine)
        sep2.setStyleSheet("color:#333355; margin:4px 2px;")
        lay.addWidget(sep2)

        self._btn_save_png = _btn("🖼  PNG", "#1565C0", 72)
        self._btn_save_png.clicked.connect(self._export_png)
        lay.addWidget(self._btn_save_png)

        self._btn_save_csv = _btn("📄  CSV", "#2E7D32", 72)
        self._btn_save_csv.clicked.connect(self._export_csv)
        lay.addWidget(self._btn_save_csv)

        return bar

    def _build_signal_selector(self) -> QWidget:
        container = QWidget()
        container.setStyleSheet(f"background:{_CARD};")
        container.setMinimumWidth(160)
        container.setMaximumWidth(280)
        vlay = QVBoxLayout(container)
        vlay.setContentsMargins(6, 6, 6, 6)
        vlay.setSpacing(4)

        hdr = QLabel("Signals")
        hdr.setStyleSheet(f"color:{_DIM}; font-size:8pt; font-weight:bold;")
        vlay.addWidget(hdr)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border:none; background:transparent; }")

        self._sig_container = QWidget()
        self._sig_container.setStyleSheet("background:transparent;")
        self._sig_layout = QVBoxLayout(self._sig_container)
        self._sig_layout.setContentsMargins(0, 0, 0, 0)
        self._sig_layout.setSpacing(2)
        self._sig_layout.addStretch()

        scroll.setWidget(self._sig_container)
        vlay.addWidget(scroll, 1)

        return container

    def _build_plot(self) -> pg.PlotWidget:
        axis = pg.DateAxisItem(orientation="bottom")
        self._plot = pg.PlotWidget(axisItems={"bottom": axis})
        self._plot.setLabel("left",   "Value")
        self._plot.setLabel("bottom", "Time")
        self._plot.showGrid(x=True, y=True, alpha=0.25)
        self._plot.getPlotItem().setMenuEnabled(False)

        # viewbox: left-drag = pan, right-drag = zoom (pyqtgraph default)
        vb = self._plot.getViewBox()
        vb.setMouseMode(pg.ViewBox.RectMode)   # right-drag zooms a rectangle

        # follow incoming data: keep auto-range on until the user zooms/pans,
        # then pause following (Fit button unchecks) so we don't fight them
        vb.enableAutoRange(x=True, y=True)
        vb.sigRangeChangedManually.connect(self._on_manual_range)

        # legend — movable inside the plot
        self._legend = self._plot.addLegend(
            offset=(10, 10),
            labelTextColor=_TEXT,
            brush=pg.mkBrush(color=(30, 30, 50, 200)),
            pen=pg.mkPen(color="#333355"),
        )

        return self._plot

    # ── signal selector management ────────────────────────────────────────────

    def _ensure_signal(self, key: str, label: str) -> None:
        """Add a checkbox + curve for `key` if not already present."""
        if key in self._checks:
            return

        # assign colour
        idx = len(self._color_map) % len(_PALETTE)
        color = _PALETTE[idx]
        self._color_map[key] = color

        # checkbox row
        row = QWidget()
        row.setStyleSheet("background:transparent;")
        rlay = QHBoxLayout(row)
        rlay.setContentsMargins(2, 0, 2, 0)
        rlay.setSpacing(4)

        dot = QLabel("●")
        dot.setStyleSheet(f"color:{color}; font-size:10pt;")
        rlay.addWidget(dot)

        cb = QCheckBox(label)
        cb.setChecked(True)
        cb.setStyleSheet(
            f"QCheckBox {{ color:{_TEXT}; font-size:8pt; }}"
            f"QCheckBox::indicator {{ width:12px; height:12px; }}"
        )
        cb.stateChanged.connect(lambda _, k=key: self._on_visibility_changed(k))
        rlay.addWidget(cb, 1)

        # insert before the trailing stretch
        self._sig_layout.insertWidget(self._sig_layout.count() - 1, row)
        self._checks[key] = cb

        # curve
        pen = pg.mkPen(color=color, width=1.5)
        curve = self._plot.plot([], [], name=label, pen=pen)
        self._curves[key] = curve

    def _on_visibility_changed(self, key: str) -> None:
        visible = self._checks[key].isChecked()
        curve   = self._curves.get(key)
        if curve:
            curve.setVisible(visible)
            # update legend visibility
            item = self._legend.items
            for sample, lbl in item:
                if lbl.text == self._checks[key].text():
                    sample.setVisible(visible)
                    lbl.setVisible(visible)

    def _select_all(self) -> None:
        for cb in self._checks.values():
            cb.setChecked(True)

    def _deselect_all(self) -> None:
        for cb in self._checks.values():
            cb.setChecked(False)

    # ── data refresh ──────────────────────────────────────────────────────────

    def _auto_refresh(self) -> None:
        if self._btn_auto.isChecked():
            self.refresh()

    def refresh(self) -> None:
        """Rebuild all curves from LogPanel._rows."""
        rows = self._log._rows
        n    = len(rows)
        self._lbl_rows.setText(f"{n} rows")

        if n == 0:
            return

        # Collect all numeric field keys present in rows
        all_keys: list[str] = []
        for key in (self._log._active_keys or []):
            # check at least one row has a real numeric value
            for row in rows[-10:]:   # sample last 10 rows
                if _to_float(row.get(key)) is not None:
                    all_keys.append(key)
                    break

        # get display labels from log_panel's _FIELD_MAP
        try:
            from .log_panel import _FIELD_MAP
            def _label(k):
                fd = _FIELD_MAP.get(k)
                if fd:
                    return f"{fd.label} ({fd.unit})" if fd.unit else fd.label
                return k
        except Exception:
            def _label(k): return k

        # ensure curves exist for every key
        for key in all_keys:
            self._ensure_signal(key, _label(key))

        # build X array (unix timestamps)
        xs = np.array([_parse_ts(r["timestamp"]) for r in rows], dtype=float)

        # update each curve
        for key, curve in self._curves.items():
            ys_raw = [_to_float(r.get(key)) for r in rows]
            # replace None with NaN so pyqtgraph draws gaps
            ys = np.array([v if v is not None else np.nan for v in ys_raw],
                          dtype=float)
            curve.setData(xs, ys)

        self._last_n = n

        # while auto-fit is on, make sure the view tracks all data — pyqtgraph
        # silently disables auto-range after any manual zoom/pan
        if self._btn_reset.isChecked():
            self._plot.getViewBox().enableAutoRange(x=True, y=True)

    def _on_fit_toggled(self, checked: bool) -> None:
        if not checked:
            return   # leave the user's manual view untouched
        vb = self._plot.getViewBox()
        vb.autoRange()                       # fit everything now …
        vb.enableAutoRange(x=True, y=True)   # … and keep following new data

    def _on_manual_range(self, *_args) -> None:
        """User zoomed or panned — pause auto-fit so the view stays put."""
        if self._btn_reset.isChecked():
            self._btn_reset.blockSignals(True)
            self._btn_reset.setChecked(False)
            self._btn_reset.blockSignals(False)

    # ── export ────────────────────────────────────────────────────────────────

    def _export_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Graph as PNG",
            str(Path.home() / "graph.png"),
            "PNG Image (*.png)")
        if not path:
            return
        try:
            exporter = ImageExporter(self._plot.plotItem)
            exporter.parameters()["width"]  = 1920
            exporter.parameters()["height"] = 1080
            exporter.export(path)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Export Failed", str(exc))

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Visible Data as CSV",
            str(Path.home() / "graph_data.csv"),
            "CSV Files (*.csv)")
        if not path:
            return

        rows = self._log._rows
        if not rows:
            return

        # export only visible signals
        visible_keys = [k for k, cb in self._checks.items() if cb.isChecked()]
        fieldnames   = ["timestamp"] + visible_keys

        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=fieldnames,
                                        extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Export Failed", str(exc))
