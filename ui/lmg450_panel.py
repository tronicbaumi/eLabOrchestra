"""
LMG450 Panel – full-featured display for the ZES Zimmer LMG450 power analyser.

Tabs:
  • Overview   – live V, I, P, Q, S, PF, φ, f readouts + DC components
  • Harmonics  – bar chart (THD + orders 1-20) for voltage and current
  • Integration – Wh, Ah, elapsed time; start / stop / reset controls
  • Aggregate  – Psum, Qsum, Ssum, Wpsum, AHpsum across all channels
"""

from __future__ import annotations

import math
import threading
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QSpinBox, QGroupBox,
    QFrame, QTabWidget, QSizePolicy,
)
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QFontMetrics

_DARK   = "#1A1A2A"
_CARD   = "#252535"
_ACCENT = "#9C27B0"    # violet – LMG450 accent
_TEXT   = "#FFFFFF"
_DIM    = "#8888AA"
_GREEN  = "#4CAF50"
_RED    = "#f44336"
_YELLOW = "#FFEB3B"
_BLUE   = "#64B5F6"
_ORANGE = "#FFB74D"
_VIOLET = "#CE93D8"


def _grp(title: str, colour: str = _TEXT) -> QGroupBox:
    g = QGroupBox(title)
    g.setStyleSheet(
        f"QGroupBox {{ background:{_CARD}; border:1px solid #333355; border-radius:8px;"
        f"            margin-top:10px; color:{colour}; font-weight:bold; }}"
        f"QGroupBox::title {{ subcontrol-origin:margin; left:10px; padding:0 4px; }}"
    )
    return g


class _BigVal(QFrame):
    """Large numeric readout."""
    def __init__(self, title: str, unit: str, color: str = _TEXT):
        super().__init__()
        self.setStyleSheet(f"background:{_CARD}; border-radius:6px; padding:2px;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(1)
        t = QLabel(title)
        t.setStyleSheet(f"color:{_DIM}; font-size:8pt;")
        lay.addWidget(t)
        row = QHBoxLayout()
        self._v = QLabel("—")
        self._v.setStyleSheet(
            f"color:{color}; font-size:22pt; font-weight:bold; letter-spacing:1px;")
        row.addWidget(self._v)
        u = QLabel(unit)
        u.setStyleSheet(f"color:{_DIM}; font-size:9pt; padding-top:8px;")
        row.addWidget(u, 0, Qt.AlignBottom)
        row.addStretch()
        lay.addLayout(row)

    def set(self, v: float | None, dec: int = 4) -> None:
        self._v.setText("—" if v is None else f"{v:.{dec}f}")


# ── Harmonics bar-chart widget ────────────────────────────────────────────────

class _HarmChart(QWidget):
    """Simple bar chart showing harmonic amplitudes (orders 1-N)."""

    def __init__(self, colour: str = _BLUE, parent=None):
        super().__init__(parent)
        self._colour  = QColor(colour)
        self._data: list[float] = []
        self._title   = ""
        self.setMinimumHeight(140)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_data(self, values: list[float], title: str = "") -> None:
        self._data  = values
        self._title = title
        self.update()

    def paintEvent(self, _) -> None:
        if not self._data:
            return
        p   = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(_CARD))

        w, h  = self.width(), self.height()
        pad   = 10
        top   = 22
        bot   = h - 18
        draw_h = bot - top
        n     = len(self._data)
        mx    = max(self._data) if self._data else 1.0
        if mx == 0:
            mx = 1.0
        bar_w = max(2, (w - 2 * pad) // n - 2)

        # title
        p.setPen(QColor(_DIM))
        p.setFont(QFont("Arial", 8))
        p.drawText(pad, 14, self._title)

        for i, val in enumerate(self._data):
            bh  = max(1, int(draw_h * val / mx))
            x   = pad + i * ((w - 2 * pad) // n)
            y   = bot - bh
            col = QColor(self._colour)
            col.setAlpha(200 if i > 0 else 255)
            p.fillRect(x, y, bar_w, bh, col)

            # order label
            if n <= 20:
                p.setPen(QColor(_DIM))
                p.setFont(QFont("Arial", 7))
                p.drawText(x, bot + 12, str(i + 1))

        # y-axis max label
        p.setPen(QColor(_DIM))
        p.setFont(QFont("Arial", 7))
        p.drawText(w - 50, top + 10, f"{mx:.3g}")
        p.end()


# ── Overview tab ──────────────────────────────────────────────────────────────

class _OverviewTab(QWidget):
    def __init__(self):
        super().__init__()
        grid = QGridLayout(self)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(8)

        self.urms  = _BigVal("Voltage (Urms)",  "V",   _BLUE)
        self.irms  = _BigVal("Current (Irms)",  "A",   _GREEN)
        self.p     = _BigVal("Active Power P",  "W",   _YELLOW)
        self.q     = _BigVal("Reactive Power Q","VAr", _ORANGE)
        self.s     = _BigVal("Apparent Power S","VA",  _VIOLET)
        self.lamda = _BigVal("Power Factor λ",  "",    _BLUE)
        self.phi   = _BigVal("Phase Angle φ",   "°",   _VIOLET)
        self.fu    = _BigVal("Frequency",        "Hz",  _DIM)
        self.ubdc  = _BigVal("DC Voltage",       "V",   _ORANGE)
        self.ibdc  = _BigVal("DC Current",       "A",   _RED)

        widgets = [self.urms, self.irms, self.p, self.q, self.s,
                   self.lamda, self.phi, self.fu, self.ubdc, self.ibdc]
        for i, w in enumerate(widgets):
            grid.addWidget(w, i // 2, i % 2)


# ── Harmonics tab ─────────────────────────────────────────────────────────────

class _HarmonicsTab(QWidget):
    # carries worker-thread results back to the GUI thread (queued connection)
    _harmonics_ready = Signal(list, list, int)

    def __init__(self, device):
        super().__init__()
        self._device = device
        self._refresh_running = False
        self._harmonics_ready.connect(self._apply_harmonics)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Orders:"))
        self._spin_orders = QSpinBox()
        self._spin_orders.setRange(2, 50)
        self._spin_orders.setValue(20)
        self._spin_orders.setStyleSheet(
            f"QSpinBox {{ background:#333350; color:{_TEXT}; border:1px solid #555580;"
            f" border-radius:4px; padding:2px 6px; }}")
        ctrl.addWidget(self._spin_orders)

        self._btn_refresh = QPushButton("⟳  Refresh")
        self._btn_refresh.setFixedHeight(28)
        self._btn_refresh.setStyleSheet(
            f"QPushButton {{ background:#607D8B; color:white; border-radius:4px;"
            f" font-weight:bold; padding:0 12px; }}"
            f"QPushButton:hover {{ background:#78909C; }}")
        self._btn_refresh.clicked.connect(self.refresh)
        ctrl.addWidget(self._btn_refresh)
        ctrl.addStretch()

        # THD labels
        self._lbl_uthd = QLabel("Voltage THD: —")
        self._lbl_uthd.setStyleSheet(f"color:{_BLUE}; font-size:9pt;")
        self._lbl_ithd = QLabel("Current THD: —")
        self._lbl_ithd.setStyleSheet(f"color:{_GREEN}; font-size:9pt;")
        ctrl.addWidget(self._lbl_uthd)
        ctrl.addWidget(self._lbl_ithd)
        lay.addLayout(ctrl)

        self._chart_u = _HarmChart(_BLUE)
        self._chart_i = _HarmChart(_GREEN)

        grp_u = _grp("Voltage Harmonics", _BLUE)
        gu = QVBoxLayout(grp_u)
        gu.setContentsMargins(4, 16, 4, 4)
        gu.addWidget(self._chart_u)
        lay.addWidget(grp_u, 1)

        grp_i = _grp("Current Harmonics", _GREEN)
        gi = QVBoxLayout(grp_i)
        gi.setContentsMargins(4, 16, 4, 4)
        gi.addWidget(self._chart_i)
        lay.addWidget(grp_i, 1)

    def refresh(self) -> None:
        """Fetch harmonics on a worker thread (2·n serial queries would
        freeze the GUI for seconds) and apply the result via signal."""
        if not self._device or not self._device.connected:
            return
        if self._refresh_running:
            return
        self._refresh_running = True
        self._btn_refresh.setEnabled(False)
        self._btn_refresh.setText("⟳  Reading…")
        n = self._spin_orders.value()
        device = self._device

        def worker() -> None:
            try:
                u_harms, i_harms = device.measure_harmonics(max_order=n)
            except Exception:
                u_harms, i_harms = [], []
            self._harmonics_ready.emit(u_harms, i_harms, n)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_harmonics(self, u_harms: list, i_harms: list, n: int) -> None:
        self._refresh_running = False
        self._btn_refresh.setEnabled(True)
        self._btn_refresh.setText("⟳  Refresh")
        self._chart_u.set_data(u_harms, f"Voltage harmonics  (1–{n})")
        self._chart_i.set_data(i_harms, f"Current harmonics  (1–{n})")
        # THD from last channel measurement (cached snapshot)
        try:
            st = self._device.get_state()
            m  = st.channels[st.active_chan - 1]
            self._lbl_uthd.setText(f"Voltage THD: {m.uthd:.2f} %")
            self._lbl_ithd.setText(f"Current THD: {m.ithd:.2f} %")
        except Exception:
            pass


# ── Integration tab ───────────────────────────────────────────────────────────

class _IntegrationTab(QWidget):
    def __init__(self, device):
        super().__init__()
        self._device = device
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(10)

        # readouts
        read_grp = _grp("Integration Results", _YELLOW)
        rg = QGridLayout(read_grp)
        rg.setContentsMargins(10, 18, 10, 10)
        rg.setSpacing(8)

        self._wh    = _BigVal("Energy (Wh)",  "Wh",  _YELLOW)
        self._ah    = _BigVal("Charge (Ah)",  "Ah",  _ORANGE)
        self._itime = _BigVal("Elapsed Time", "s",   _DIM)
        rg.addWidget(self._wh,    0, 0)
        rg.addWidget(self._ah,    0, 1)
        rg.addWidget(self._itime, 0, 2)
        lay.addWidget(read_grp)

        # controls
        ctrl_grp = _grp("Controls", _GREEN)
        cg = QHBoxLayout(ctrl_grp)
        cg.setContentsMargins(14, 18, 14, 12)
        cg.setSpacing(10)

        for label, bg, hover, slot in [
            ("▶  Start",   _GREEN,  "#66BB6A", self._start),
            ("■  Stop",    _RED,    "#EF5350", self._stop),
            ("↺  Reset",   "#607D8B","#78909C", self._reset),
        ]:
            b = QPushButton(label)
            b.setFixedHeight(32)
            b.setMinimumWidth(100)
            b.setStyleSheet(
                f"QPushButton {{ background:{bg}; color:white; border-radius:4px;"
                f" font-weight:bold; font-size:10pt; }}"
                f"QPushButton:hover {{ background:{hover}; }}")
            b.clicked.connect(slot)
            cg.addWidget(b)
        cg.addStretch()
        lay.addWidget(ctrl_grp)
        lay.addStretch()

    def _start(self):
        if self._device and self._device.connected:
            self._device.integration_start()

    def _stop(self):
        if self._device and self._device.connected:
            self._device.integration_stop()

    def _reset(self):
        if self._device and self._device.connected:
            self._device.integration_reset()

    def update_values(self, wh: float, ah: float, itime: float) -> None:
        self._wh.set(wh, 4)
        self._ah.set(ah, 6)
        self._itime.set(itime, 1)


# ── Aggregate tab ─────────────────────────────────────────────────────────────

class _AggregateTab(QWidget):
    def __init__(self):
        super().__init__()
        lay = QGridLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        self.psum   = _BigVal("Total Active Power",    "W",   _YELLOW)
        self.qsum   = _BigVal("Total Reactive Power",  "VAr", _ORANGE)
        self.ssum   = _BigVal("Total Apparent Power",  "VA",  _VIOLET)
        self.wpsum  = _BigVal("Total Energy",          "Wh",  _YELLOW)
        self.ahpsum = _BigVal("Total Charge",          "Ah",  _ORANGE)

        for i, w in enumerate([self.psum, self.qsum, self.ssum, self.wpsum, self.ahpsum]):
            lay.addWidget(w, i // 2, i % 2)


# ── main panel ────────────────────────────────────────────────────────────────

class LMG450Panel(QWidget):
    """Full-featured panel for the LMG450 power analyser."""

    def __init__(self, device=None, parent=None):
        super().__init__(parent)
        self._device = device
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(500)

    def set_device(self, device) -> None:
        self._device = device
        self._harm_tab._device = device
        self._int_tab._device  = device

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── channel selector ─────────────────────────────────────────────────
        top = QHBoxLayout()
        top.addWidget(QLabel("Active channel:"))
        self._combo_ch = QComboBox()
        self._combo_ch.addItems(["Ch 1", "Ch 2", "Ch 3", "Ch 4"])
        self._combo_ch.setStyleSheet(
            f"QComboBox {{ background:#333350; color:{_TEXT}; border:1px solid #555580;"
            f" border-radius:4px; padding:2px 8px; min-height:24px; }}"
            f"QComboBox::drop-down {{ border:none; width:18px; }}"
            f"QComboBox QAbstractItemView {{ background:#333350; color:{_TEXT};"
            f" selection-background-color:{_ACCENT}; }}"
        )
        self._combo_ch.currentIndexChanged.connect(
            lambda i: self._device and self._device.connected and
                      self._device.select_channel(i + 1)
        )
        top.addWidget(self._combo_ch)

        top.addWidget(QLabel("Averaging:"))
        self._spin_avrg = QSpinBox()
        self._spin_avrg.setRange(1, 128)
        self._spin_avrg.setValue(1)
        self._spin_avrg.setStyleSheet(
            f"QSpinBox {{ background:#333350; color:{_TEXT}; border:1px solid #555580;"
            f" border-radius:4px; padding:2px 6px; min-height:24px; }}")
        self._spin_avrg.valueChanged.connect(
            lambda v: self._device and self._device.connected and
                      self._device.set_averaging(v)
        )
        top.addWidget(self._spin_avrg)
        top.addStretch()
        root.addLayout(top)

        # ── tabs ──────────────────────────────────────────────────────────────
        tabs = QTabWidget()
        tabs.setStyleSheet(
            f"QTabWidget::pane {{ border:1px solid #333355; background:{_DARK}; }}"
            f"QTabBar::tab {{ background:#252535; color:{_DIM}; padding:6px 14px;"
            f"  border-top-left-radius:4px; border-top-right-radius:4px; }}"
            f"QTabBar::tab:selected {{ background:{_ACCENT}; color:white; }}"
        )

        self._overview_tab = _OverviewTab()
        tabs.addTab(self._overview_tab, "📊  Overview")

        self._harm_tab = _HarmonicsTab(self._device)
        tabs.addTab(self._harm_tab, "〰  Harmonics")

        self._int_tab = _IntegrationTab(self._device)
        tabs.addTab(self._int_tab, "∫  Integration")

        self._agg_tab = _AggregateTab()
        tabs.addTab(self._agg_tab, "∑  Aggregate")

        root.addWidget(tabs, 1)

    # ── polling ───────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        if not self._device or not self._device.connected:
            return
        try:
            # cached snapshot — serial I/O happens on the driver poll thread
            st = self._device.get_state()
            m  = st.channels[st.active_chan - 1]
            ov = self._overview_tab
            ov.urms.set(m.urms,  4)
            ov.irms.set(m.irms,  5)
            ov.p.set(m.p,        4)
            ov.q.set(m.q,        4)
            ov.s.set(m.s,        4)
            ov.lamda.set(m.lamda, 5)
            ov.phi.set(m.phi,    4)
            ov.fu.set(m.fu,      4)
            ov.ubdc.set(m.ubdc,  5)
            ov.ibdc.set(m.ibdc,  6)

            self._int_tab.update_values(m.wh, m.ah, m.itime)

            a = st.aggregate
            ag = self._agg_tab
            ag.psum.set(a.psum,   4)
            ag.qsum.set(a.qsum,   4)
            ag.ssum.set(a.ssum,   4)
            ag.wpsum.set(a.wpsum, 4)
            ag.ahpsum.set(a.ahpsum, 6)
        except Exception:
            pass
