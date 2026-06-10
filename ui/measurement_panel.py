"""Live measurement display panel for the Hantek RLC 1733C."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QComboBox, QPushButton, QGroupBox, QGridLayout, QSizePolicy,
)

from instruments import HantekRLC1733C, Measurement, MeasureMode, TestFrequency


_DARK_BG   = "#1A1A2A"
_CARD_BG   = "#252535"
_ACCENT    = "#4C97FF"
_GREEN     = "#4CAF50"
_RED       = "#f44336"
_TEXT_MAIN = "#FFFFFF"
_TEXT_DIM  = "#8888AA"


def _style_card(widget: QWidget) -> None:
    widget.setStyleSheet(f"background: {_CARD_BG}; border-radius: 8px; padding: 6px;")


class BigValueDisplay(QFrame):
    """Large primary + secondary value display."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {_CARD_BG}; border-radius: 10px;")
        self.setMinimumHeight(120)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)

        # mode / frequency row
        top_row = QHBoxLayout()
        self._lbl_mode = QLabel("Cs")
        self._lbl_mode.setStyleSheet(f"color: {_ACCENT}; font-size: 14pt; font-weight: bold;")
        self._lbl_freq = QLabel("1 kHz")
        self._lbl_freq.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 10pt;")
        self._lbl_status = QLabel("○  Disconnected")
        self._lbl_status.setStyleSheet(f"color: #f44336; font-size: 9pt; font-weight: bold;")
        top_row.addWidget(self._lbl_mode)
        top_row.addWidget(self._lbl_freq)
        top_row.addStretch()
        top_row.addWidget(self._lbl_status)
        layout.addLayout(top_row)

        # primary value
        self._lbl_primary = QLabel("---")
        self._lbl_primary.setFont(QFont("Consolas", 32, QFont.Bold))
        self._lbl_primary.setStyleSheet(f"color: {_TEXT_MAIN};")
        self._lbl_primary.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_primary)

        # secondary value
        self._lbl_secondary = QLabel("---")
        self._lbl_secondary.setFont(QFont("Consolas", 14))
        self._lbl_secondary.setStyleSheet(f"color: {_TEXT_DIM};")
        self._lbl_secondary.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_secondary)

        # phase — only shown for Z / Theta modes
        self._lbl_phase = QLabel("Phase: ---")
        self._lbl_phase.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 9pt;")
        self._lbl_phase.setAlignment(Qt.AlignCenter)
        self._lbl_phase.setVisible(False)
        layout.addWidget(self._lbl_phase)

    def update_measurement(self, m: Measurement) -> None:
        self._lbl_mode.setText(m.mode_label)
        self._lbl_freq.setText(m.frequency.label)
        status_txt = "●  OL" if m.overload else ("●  HOLD" if m.hold else "●  LIVE")
        status_col = "#f44336" if m.overload else "#4CAF50"
        self._lbl_status.setText(status_txt)
        self._lbl_status.setStyleSheet(f"color: {status_col}; font-size: 9pt; font-weight: bold;")
        self._lbl_primary.setText(m.primary_str)
        self._lbl_secondary.setText(m.secondary_str)
        show_phase = m.mode in (MeasureMode.Z, MeasureMode.Theta)
        self._lbl_phase.setVisible(show_phase)
        if show_phase:
            self._lbl_phase.setText(
                "Phase: —" if m.phase is None else f"Phase: {m.phase:.2f}°")


class ControlPanel(QGroupBox):
    """Mode, frequency and quick-action buttons."""

    mode_changed = Signal(MeasureMode)
    freq_changed = Signal(TestFrequency)
    hold_toggled = Signal(bool)
    rel_toggled  = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__("Instrument Control", parent)
        self.setStyleSheet(f"""
            QGroupBox {{
                color: {_TEXT_MAIN}; font-weight: bold;
                border: 1px solid #444460; border-radius: 8px;
                margin-top: 10px; background: {_CARD_BG};
            }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
        """)

        grid = QGridLayout(self)
        grid.setContentsMargins(10, 16, 10, 10)
        grid.setSpacing(6)

        lbl_style = f"color: {_TEXT_DIM}; font-size: 9pt;"
        combo_style = f"""
            QComboBox {{
                background: #333350; color: {_TEXT_MAIN};
                border: 1px solid #555580; border-radius: 4px;
                padding: 3px 6px; font-size: 9pt;
            }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox QAbstractItemView {{ background: #333350; color: {_TEXT_MAIN}; }}
        """

        # Mode — default to Cs (index 2) to match driver default
        grid.addWidget(self._lbl("Mode", lbl_style), 0, 0)
        self._combo_mode = QComboBox()
        self._combo_mode.addItems([m.name for m in MeasureMode])
        self._combo_mode.setCurrentIndex(list(MeasureMode).index(MeasureMode.Cs))
        self._combo_mode.setStyleSheet(combo_style)
        self._combo_mode.currentIndexChanged.connect(self._on_mode)
        grid.addWidget(self._combo_mode, 0, 1)

        # Frequency
        grid.addWidget(self._lbl("Frequency", lbl_style), 1, 0)
        self._combo_freq = QComboBox()
        for f in TestFrequency:
            self._combo_freq.addItem(f.label)
        self._combo_freq.setCurrentIndex(2)  # 1 kHz default
        self._combo_freq.setStyleSheet(combo_style)
        self._combo_freq.currentIndexChanged.connect(self._on_freq)
        grid.addWidget(self._combo_freq, 1, 1)

        # Quick buttons
        btn_row = QHBoxLayout()
        self._btn_hold = self._toggle_btn("HOLD", "#607D8B")
        self._btn_rel  = self._toggle_btn("REL",  "#607D8B")
        btn_row.addWidget(self._btn_hold)
        btn_row.addWidget(self._btn_rel)
        grid.addLayout(btn_row, 2, 0, 1, 2)

    @staticmethod
    def _lbl(text: str, style: str) -> QLabel:
        l = QLabel(text)
        l.setStyleSheet(style)
        return l

    @staticmethod
    def _toggle_btn(label: str, color: str) -> QPushButton:
        b = QPushButton(label)
        b.setCheckable(True)
        b.setFixedHeight(28)
        b.setStyleSheet(f"""
            QPushButton {{
                background: {color}; color: white; border-radius: 4px;
                font-weight: bold; padding: 0 10px; font-size: 9pt;
            }}
            QPushButton:checked {{ background: #4C97FF; }}
        """)
        return b

    def _on_mode(self, idx: int) -> None:
        modes = list(MeasureMode)
        if 0 <= idx < len(modes):
            self.mode_changed.emit(modes[idx])

    def _on_freq(self, idx: int) -> None:
        freqs = list(TestFrequency)
        if 0 <= idx < len(freqs):
            self.freq_changed.emit(freqs[idx])

    @property
    def hold_button(self) -> QPushButton:
        return self._btn_hold

    @property
    def rel_button(self) -> QPushButton:
        return self._btn_rel


class MeasurementPanel(QWidget):
    """Full measurement panel: display + controls."""

    def __init__(self, device: HantekRLC1733C, parent=None) -> None:
        super().__init__(parent)
        self._device = device
        self.setStyleSheet(f"background: {_DARK_BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self._display = BigValueDisplay()
        layout.addWidget(self._display)

        self._controls = ControlPanel()
        layout.addWidget(self._controls)

        layout.addStretch()

        # wire controls to device
        self._controls.mode_changed.connect(device.set_mode)
        self._controls.freq_changed.connect(device.set_frequency)
        self._controls.hold_button.toggled.connect(device.set_hold)
        self._controls.rel_button.toggled.connect(device.set_rel)

        # poll timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(500)

    def _refresh(self) -> None:
        # cached snapshot — serial I/O happens on the driver poll thread
        m = self._device.last_measurement()
        self._display.update_measurement(m)
