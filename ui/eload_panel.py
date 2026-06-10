"""
Electronic Load Panel – live display + controls for Array 3721A.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QDoubleSpinBox, QComboBox,
    QGroupBox, QFrame,
)

from instruments.array_3721a import ELoadMode

_DARK   = "#1A1A2A"
_CARD   = "#252535"
_ACCENT = "#4C97FF"
_TEXT   = "#FFFFFF"
_DIM    = "#8888AA"
_GREEN  = "#4CAF50"
_RED    = "#f44336"
_ORANGE = "#FF9800"
_TEAL   = "#00BCD4"


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
        lbl_unit.setStyleSheet(f"color:{_DIM}; font-size:12pt; padding-top:8px;")
        row.addWidget(lbl_unit, 0, Qt.AlignBottom)
        row.addStretch()
        lay.addLayout(row)

    def set_value(self, v: float | None, decimals: int = 3) -> None:
        self._lbl_val.setText("—" if v is None else f"{v:.{decimals}f}")


# ── mode metadata ─────────────────────────────────────────────────────────────

_MODE_META = {
    ELoadMode.CCH: ("CCH – CC High",  "A",  0.0,  40.0,   0.1,  3),
    ELoadMode.CCL: ("CCL – CC Low",   "A",  0.0,   4.0,   0.01, 4),
    ELoadMode.CV:  ("CV  – Const. V", "V",  0.0,  80.0,   0.1,  3),
    ELoadMode.CRL: ("CRL – CR Low",   "Ω",  0.05, 10.0,   0.01, 3),
    ELoadMode.CRM: ("CRM – CR Med",   "Ω",  2.0,  100.0,  0.1,  3),
    ELoadMode.CRH: ("CRH – CR High",  "Ω",  20.0, 10000., 1.0,  2),
    ELoadMode.CPV: ("CPV – CP (V)",   "W",  0.0,  400.0,  1.0,  2),
    ELoadMode.CPC: ("CPC – CP (I)",   "W",  0.0,  400.0,  1.0,  2),
}


class ELoadPanel(QWidget):
    """
    Full panel for the Array 3721A electronic load:
      • Top row:   large V / I / P readout
      • Middle:    mode selector + set-level spinbox
      • Bottom:    input ON/OFF toggle
    """

    def __init__(self, device=None, parent=None) -> None:
        super().__init__(parent)
        self._device   = device
        self._input_on = False
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

        # ── measured readout ─────────────────────────────────────────────────
        read_grp = QGroupBox("Measured Input")
        read_grp.setStyleSheet(_card_style(_TEAL))
        read_lay = QHBoxLayout(read_grp)
        read_lay.setContentsMargins(10, 18, 10, 10)
        read_lay.setSpacing(10)

        self._disp_v = _BigValue("Voltage", "V", "#64B5F6")
        self._disp_i = _BigValue("Current", "A", "#81C784")
        self._disp_p = _BigValue("Power",   "W", "#FFB74D")
        read_lay.addWidget(self._disp_v)
        read_lay.addWidget(self._disp_i)
        read_lay.addWidget(self._disp_p)
        root.addWidget(read_grp)

        # ── mode + set level ─────────────────────────────────────────────────
        set_grp = QGroupBox("Mode & Set Level")
        set_grp.setStyleSheet(_card_style("#FFD54F"))
        set_lay = QGridLayout(set_grp)
        set_lay.setContentsMargins(14, 18, 14, 12)
        set_lay.setSpacing(8)

        def _lbl(txt):
            l = QLabel(txt)
            l.setStyleSheet(f"color:{_DIM}; font-size:9pt;")
            return l

        # mode combo
        set_lay.addWidget(_lbl("Mode:"), 0, 0)
        self._combo_mode = QComboBox()
        for mode in ELoadMode.ALL:
            label, *_ = _MODE_META[mode]
            self._combo_mode.addItem(label, userData=mode)
        self._combo_mode.setStyleSheet(
            f"QComboBox {{ background:#333350; color:{_TEXT}; border:1px solid #555580;"
            f" border-radius:4px; padding:4px 8px; font-size:10pt; min-height:28px; }}"
            f"QComboBox::drop-down {{ border:none; width:20px; }}"
            f"QComboBox QAbstractItemView {{ background:#333350; color:{_TEXT};"
            f" selection-background-color:#4C97FF; }}"
        )
        self._combo_mode.currentIndexChanged.connect(self._on_mode_changed)
        set_lay.addWidget(self._combo_mode, 0, 1)

        btn_mode = self._mk_btn("Set Mode", "#607D8B", "#78909C")
        btn_mode.clicked.connect(self._apply_mode)
        set_lay.addWidget(btn_mode, 0, 2)

        # set level spinbox
        set_lay.addWidget(_lbl("Set Level:"), 1, 0)
        self._spin_level = QDoubleSpinBox()
        self._spin_level.setStyleSheet(
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
        set_lay.addWidget(self._spin_level, 1, 1)

        btn_apply = self._mk_btn("Apply", "#607D8B", "#78909C")
        btn_apply.clicked.connect(self._apply_level)
        set_lay.addWidget(btn_apply, 1, 2)

        root.addWidget(set_grp)

        # ── input control ────────────────────────────────────────────────────
        inp_grp = QGroupBox("Input Control")
        inp_grp.setStyleSheet(_card_style(_GREEN))
        inp_lay = QHBoxLayout(inp_grp)
        inp_lay.setContentsMargins(14, 18, 14, 12)

        self._lbl_inp_state = QLabel("○  Input OFF")
        self._lbl_inp_state.setStyleSheet(
            f"color:{_RED}; font-weight:bold; font-size:11pt;")
        inp_lay.addWidget(self._lbl_inp_state)
        inp_lay.addStretch()

        self._btn_input = self._mk_btn("Enable Input", _GREEN, "#66BB6A", width=140)
        self._btn_input.clicked.connect(self._toggle_input)
        inp_lay.addWidget(self._btn_input)

        root.addWidget(inp_grp)
        root.addStretch()

        # initialise spinbox for default mode (CCH)
        self._update_spin_for_mode(ELoadMode.CCH)

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

    # ── internal helpers ──────────────────────────────────────────────────────

    def _update_spin_for_mode(self, mode: str) -> None:
        _, unit, vmin, vmax, step, dec = _MODE_META[mode]
        self._spin_level.blockSignals(True)
        self._spin_level.setRange(vmin, vmax)
        self._spin_level.setSingleStep(step)
        self._spin_level.setDecimals(dec)
        self._spin_level.setSuffix(f"  {unit}")
        self._spin_level.setValue(max(vmin, min(vmax, self._spin_level.value())))
        self._spin_level.blockSignals(False)

    def _current_mode(self) -> str:
        return self._combo_mode.currentData()

    # ── slots ─────────────────────────────────────────────────────────────────

    def _on_mode_changed(self, _idx: int) -> None:
        self._update_spin_for_mode(self._current_mode())

    def _apply_mode(self) -> None:
        if self._device and self._device.connected:
            self._device.set_mode(self._current_mode())

    def _apply_level(self) -> None:
        if self._device and self._device.connected:
            self._device.set_level(self._spin_level.value())

    def _toggle_input(self) -> None:
        if not self._device or not self._device.connected:
            return
        self._input_on = not self._input_on
        self._device.set_input(self._input_on)
        self._refresh_input_ui()

    def _refresh_input_ui(self) -> None:
        if self._input_on:
            self._lbl_inp_state.setText("●  Input ON")
            self._lbl_inp_state.setStyleSheet(
                f"color:{_GREEN}; font-weight:bold; font-size:11pt;")
            self._btn_input.setText("Disable Input")
            self._btn_input.setStyleSheet(
                f"QPushButton {{ background:{_RED}; color:white; border-radius:4px; "
                f"font-weight:bold; padding:0 10px; font-size:9pt; }}"
                f"QPushButton:hover {{ background:#EF5350; }}"
            )
        else:
            self._lbl_inp_state.setText("○  Input OFF")
            self._lbl_inp_state.setStyleSheet(
                f"color:{_RED}; font-weight:bold; font-size:11pt;")
            self._btn_input.setText("Enable Input")
            self._btn_input.setStyleSheet(
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
            # sync input state if device reports differently
            if state.input_on != self._input_on:
                self._input_on = state.input_on
                self._refresh_input_ui()
            # sync mode combo if it changed on device
            mode = state.mode
            for i in range(self._combo_mode.count()):
                if self._combo_mode.itemData(i) == mode:
                    if self._combo_mode.currentIndex() != i:
                        self._combo_mode.blockSignals(True)
                        self._combo_mode.setCurrentIndex(i)
                        self._combo_mode.blockSignals(False)
                        self._update_spin_for_mode(mode)
                    break
        except Exception:
            pass
