"""
Power Supply Panel – live display + controls for OWON SP3103.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QDoubleSpinBox, QGroupBox, QFrame,
)

_DARK   = "#1A1A2A"
_CARD   = "#252535"
_ACCENT = "#4C97FF"
_TEXT   = "#FFFFFF"
_DIM    = "#8888AA"
_GREEN  = "#4CAF50"
_RED    = "#f44336"
_ORANGE = "#FF9800"


def _card_style(color: str) -> str:
    return (
        f"QGroupBox {{ background:{_CARD}; border:1px solid #333355; border-radius:8px;"
        f"            margin-top:10px; color:{_TEXT}; font-weight:bold; }}"
        f"QGroupBox::title {{ subcontrol-origin:margin; left:10px; padding:0 4px; color:{color}; }}"
    )


class _BigValue(QFrame):
    """Large numeric readout with unit label."""

    def __init__(self, title: str, unit: str, color: str = _TEXT, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{_CARD}; border-radius:6px;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(0)

        lbl_title = QLabel(title)
        lbl_title.setStyleSheet(f"color:{_DIM}; font-size:8pt;")
        lay.addWidget(lbl_title)

        row = QHBoxLayout()
        self._lbl_val = QLabel("—")
        self._lbl_val.setStyleSheet(
            f"color:{color}; font-size:17pt; font-weight:bold; letter-spacing:2px;")
        row.addWidget(self._lbl_val)

        lbl_unit = QLabel(unit)
        lbl_unit.setStyleSheet(f"color:{_DIM}; font-size:12pt; padding-top:12px;")
        row.addWidget(lbl_unit, 0, Qt.AlignBottom)
        row.addStretch()
        lay.addLayout(row)

    def set_value(self, v: float | None, decimals: int = 3) -> None:
        self._lbl_val.setText("—" if v is None else f"{v:.{decimals}f}")


class PowerSupplyPanel(QWidget):
    """
    Full panel for the OWON SP3103:
      • Top row: large V / I / P readout
      • Middle:  set-point controls + output toggle
      • Bottom:  status bar
    """

    def __init__(self, device=None, parent=None) -> None:
        super().__init__(parent)
        self._device = device
        self._output_on = False
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(500)

    def set_device(self, device) -> None:
        self._device = device

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # ── readout row ──────────────────────────────────────────────────────
        read_grp = QGroupBox("Measured Output")
        read_grp.setStyleSheet(_card_style("#64B5F6"))
        read_lay = QHBoxLayout(read_grp)
        read_lay.setContentsMargins(10, 18, 10, 10)
        read_lay.setSpacing(10)

        self._disp_v = _BigValue("Voltage", "V",  "#64B5F6")
        self._disp_i = _BigValue("Current", "A",  "#81C784")
        self._disp_p = _BigValue("Power",   "W",  "#FFB74D")
        read_lay.addWidget(self._disp_v)
        read_lay.addWidget(self._disp_i)
        read_lay.addWidget(self._disp_p)
        root.addWidget(read_grp)

        # ── set-point controls ───────────────────────────────────────────────
        set_grp = QGroupBox("Set Points")
        set_grp.setStyleSheet(_card_style("#FFD54F"))
        set_lay = QGridLayout(set_grp)
        set_lay.setContentsMargins(14, 18, 14, 12)
        set_lay.setSpacing(8)

        def _lbl(txt):
            l = QLabel(txt)
            l.setStyleSheet(f"color:{_DIM}; font-size:9pt;")
            return l

        def _spin(vmax: float, dec: int, suffix: str) -> QDoubleSpinBox:
            s = QDoubleSpinBox()
            s.setRange(0.0, vmax)
            s.setDecimals(dec)
            s.setSingleStep(0.1 if vmax <= 10 else 0.5)
            s.setSuffix(f"  {suffix}")
            s.setStyleSheet(
                f"QDoubleSpinBox {{ background:#333350; color:{_TEXT}; "
                f"border:1px solid #555580; border-radius:4px; "
                f"padding:4px 24px 4px 8px; font-size:10pt; min-height:28px; }}"
                f"QDoubleSpinBox::up-button {{ subcontrol-origin:border; "
                f"subcontrol-position:top right; width:22px; border-left:1px solid #555580; "
                f"border-bottom:1px solid #555580; border-top-right-radius:4px; "
                f"background:#444466; }}"
                f"QDoubleSpinBox::up-button:hover {{ background:#555588; }}"
                f"QDoubleSpinBox::up-button:pressed {{ background:#4C97FF; }}"
                f"QDoubleSpinBox::up-arrow {{ width:8px; height:8px; "
                f"border-left:4px solid transparent; border-right:4px solid transparent; "
                f"border-bottom:6px solid {_TEXT}; }}"
                f"QDoubleSpinBox::down-button {{ subcontrol-origin:border; "
                f"subcontrol-position:bottom right; width:22px; border-left:1px solid #555580; "
                f"border-top:1px solid #555580; border-bottom-right-radius:4px; "
                f"background:#444466; }}"
                f"QDoubleSpinBox::down-button:hover {{ background:#555588; }}"
                f"QDoubleSpinBox::down-button:pressed {{ background:#4C97FF; }}"
                f"QDoubleSpinBox::down-arrow {{ width:8px; height:8px; "
                f"border-left:4px solid transparent; border-right:4px solid transparent; "
                f"border-top:6px solid {_TEXT}; }}"
            )
            return s

        set_lay.addWidget(_lbl("Voltage set:"),  0, 0)
        self._spin_v = _spin(30.0, 2, "V")
        set_lay.addWidget(self._spin_v, 0, 1)

        self._btn_apply_v = self._mk_btn("Apply", "#607D8B", "#78909C")
        self._btn_apply_v.clicked.connect(self._apply_voltage)
        set_lay.addWidget(self._btn_apply_v, 0, 2)

        set_lay.addWidget(_lbl("Current limit:"), 1, 0)
        self._spin_i = _spin(3.0, 3, "A")
        set_lay.addWidget(self._spin_i, 1, 1)

        self._btn_apply_i = self._mk_btn("Apply", "#607D8B", "#78909C")
        self._btn_apply_i.clicked.connect(self._apply_current)
        set_lay.addWidget(self._btn_apply_i, 1, 2)

        root.addWidget(set_grp)

        # ── output toggle ────────────────────────────────────────────────────
        out_grp = QGroupBox("Output Control")
        out_grp.setStyleSheet(_card_style(_GREEN))
        out_lay = QHBoxLayout(out_grp)
        out_lay.setContentsMargins(14, 18, 14, 12)

        self._lbl_out_state = QLabel("○  Output OFF")
        self._lbl_out_state.setStyleSheet(f"color:{_RED}; font-weight:bold; font-size:11pt;")
        out_lay.addWidget(self._lbl_out_state)
        out_lay.addStretch()

        self._btn_output = self._mk_btn("Enable Output", _GREEN, "#66BB6A", width=140)
        self._btn_output.clicked.connect(self._toggle_output)
        out_lay.addWidget(self._btn_output)

        root.addWidget(out_grp)
        root.addStretch()

    @staticmethod
    def _mk_btn(label: str, bg: str, hover: str, width: int = 90) -> QPushButton:
        b = QPushButton(label)
        b.setFixedHeight(30)
        b.setMinimumWidth(width)
        b.setStyleSheet(
            f"QPushButton {{ background:{bg}; color:white; border-radius:4px; "
            f"font-weight:bold; padding:0 10px; font-size:9pt; }}"
            f"QPushButton:hover {{ background:{hover}; }}"
        )
        return b

    # ── slots ─────────────────────────────────────────────────────────────────

    def _apply_voltage(self) -> None:
        if self._device and self._device.connected:
            self._device.set_voltage(self._spin_v.value())

    def _apply_current(self) -> None:
        if self._device and self._device.connected:
            self._device.set_current(self._spin_i.value())

    def _toggle_output(self) -> None:
        if not self._device or not self._device.connected:
            return
        self._output_on = not self._output_on
        self._device.set_output(self._output_on)
        self._refresh_output_ui()

    def _refresh_output_ui(self) -> None:
        if self._output_on:
            self._lbl_out_state.setText("●  Output ON")
            self._lbl_out_state.setStyleSheet(
                f"color:{_GREEN}; font-weight:bold; font-size:11pt;")
            self._btn_output.setText("Disable Output")
            self._btn_output.setStyleSheet(
                f"QPushButton {{ background:{_RED}; color:white; border-radius:4px; "
                f"font-weight:bold; padding:0 10px; font-size:9pt; }}"
                f"QPushButton:hover {{ background:#EF5350; }}"
            )
        else:
            self._lbl_out_state.setText("○  Output OFF")
            self._lbl_out_state.setStyleSheet(
                f"color:{_RED}; font-weight:bold; font-size:11pt;")
            self._btn_output.setText("Enable Output")
            self._btn_output.setStyleSheet(
                f"QPushButton {{ background:{_GREEN}; color:white; border-radius:4px; "
                f"font-weight:bold; padding:0 10px; font-size:9pt; }}"
                f"QPushButton:hover {{ background:#66BB6A; }}"
            )

    def _poll(self) -> None:
        if not self._device or not self._device.connected:
            return
        try:
            # cached snapshot — serial I/O happens on the driver poll thread
            state = self._device.get_state()
            self._disp_v.set_value(state.meas_voltage, 3)
            self._disp_i.set_value(state.meas_current, 3)
            self._disp_p.set_value(state.power,        3)
            if state.output_on != self._output_on:
                self._output_on = state.output_on
                self._refresh_output_ui()
        except Exception:
            pass
