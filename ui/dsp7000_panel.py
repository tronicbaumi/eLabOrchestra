"""
Magtrol DSP7000 Dynamometer Controller panel.

Layout (two columns, channel 1 left / channel 2 right):
  ┌────────────────────────────────────────────────────────┐
  │  Channel selector  │  Polling interval                 │
  ├──────────┬──────────┬──────────┬──────────┬────────────┤
  │  SPEED   │  TORQUE  │  POWER   │  DIR      │  STATUS   │  (big readouts)
  ├──────────────────────────────────────────────────────  ┤
  │ [Speed Control tab] [Torque Control tab] [Ramp tab]    │
  │                     [Alarms tab]                       │
  └────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

from PySide6.QtCore    import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QDoubleSpinBox, QSpinBox,
    QTabWidget, QFrame, QGroupBox, QComboBox, QCheckBox,
)

_DARK   = "#1A1A2A"
_CARD   = "#252535"
_ACCENT = "#E65100"    # Magtrol orange
_GREEN  = "#43A047"
_RED    = "#E53935"
_TEXT   = "#FFFFFF"
_DIM    = "#8888AA"


# ── Shared big-value readout ───────────────────────────────────────────────────

class _BigVal(QFrame):
    def __init__(self, label: str, unit: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(f"QFrame {{ background: {_CARD}; border-radius: 6px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(2)

        self._lbl = QLabel(label)
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setStyleSheet(f"color:{_DIM}; font-size:8pt;")
        lay.addWidget(self._lbl)

        self._val = QLabel("—")
        self._val.setAlignment(Qt.AlignCenter)
        self._val.setStyleSheet(f"color:{_TEXT}; font-size:16pt; font-weight:bold;")
        lay.addWidget(self._val)

        if unit:
            u = QLabel(unit)
            u.setAlignment(Qt.AlignCenter)
            u.setStyleSheet(f"color:{_DIM}; font-size:8pt;")
            lay.addWidget(u)

    def set_value(self, value, fmt: str = "{:.2f}") -> None:
        if value is None:
            self._val.setText("—")
        else:
            self._val.setText(fmt.format(value))

    def set_text(self, text: str, colour: str = _TEXT) -> None:
        self._val.setText(text)
        self._val.setStyleSheet(f"color:{colour}; font-size:16pt; font-weight:bold;")


# ── Channel readout strip ──────────────────────────────────────────────────────

class _ChannelStrip(QWidget):
    def __init__(self, ch: int, parent=None) -> None:
        super().__init__(parent)
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        title = QLabel(f"Channel {ch}")
        title.setStyleSheet(f"color:{_ACCENT}; font-size:10pt; font-weight:bold;")
        lay.addWidget(title, 0, 0, 1, 4)

        self._speed  = _BigVal("SPEED",  "rpm")
        self._torque = _BigVal("TORQUE", "N·m")
        self._power  = _BigVal("POWER",  "W")
        self._dir    = _BigVal("DIR",    "")

        lay.addWidget(self._speed,  1, 0)
        lay.addWidget(self._torque, 1, 1)
        lay.addWidget(self._power,  1, 2)
        lay.addWidget(self._dir,    1, 3)

    def update(self, cd) -> None:
        self._speed.set_value(cd.speed,  "{:.1f}")
        self._torque.set_value(cd.torque, "{:.3f}")
        self._power.set_value(cd.power,  "{:.2f}")
        col = _GREEN if cd.direction == "R" else (_RED if cd.direction == "A" else "#FFAB19")
        label = {"R": "CW ▶", "L": "◀ CCW", "A": "⚠ ALARM"}.get(cd.direction, cd.direction)
        self._dir.set_text(label, col)


# ── Speed control tab ──────────────────────────────────────────────────────────

class _SpeedTab(QWidget):
    def __init__(self, dsp, ch: int, parent=None) -> None:
        super().__init__(parent)
        self._dsp = dsp
        self._ch  = ch
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)

        row = QHBoxLayout()
        row.addWidget(QLabel("Set speed (rpm):"))
        self._spin = QDoubleSpinBox()
        self._spin.setRange(0, 199999)
        self._spin.setDecimals(1)
        self._spin.setValue(1500.0)
        self._spin.setStyleSheet("QDoubleSpinBox { background:#252535; color:white; }")
        row.addWidget(self._spin)
        lay.addLayout(row)

        btn_row = QHBoxLayout()
        apply = QPushButton("Apply")
        apply.setStyleSheet(f"QPushButton {{ background:{_GREEN}; color:white; border-radius:4px; padding:4px 12px; }}")
        apply.clicked.connect(self._apply)
        btn_row.addWidget(apply)

        reset = QPushButton("Free Run")
        reset.setStyleSheet(f"QPushButton {{ background:{_RED}; color:white; border-radius:4px; padding:4px 12px; }}")
        reset.clicked.connect(self._reset)
        btn_row.addWidget(reset)
        lay.addLayout(btn_row)

        # PID
        grp = QGroupBox("Speed PID")
        grp.setStyleSheet("QGroupBox { color:#CCC; border:1px solid #333355; margin-top:6px; }")
        g = QHBoxLayout(grp)
        for name, attr in (("P", "_sp"), ("I", "_si"), ("D", "_sd")):
            g.addWidget(QLabel(name + ":"))
            sb = QSpinBox(); sb.setRange(0, 99); sb.setValue(50 if name == "P" else (10 if name == "I" else 0))
            sb.setStyleSheet("QSpinBox { background:#252535; color:white; }")
            setattr(self, attr, sb)
            g.addWidget(sb)
        pid_apply = QPushButton("Set PID")
        pid_apply.setStyleSheet(f"QPushButton {{ background:{_ACCENT}; color:white; border-radius:4px; padding:2px 8px; }}")
        pid_apply.clicked.connect(self._apply_pid)
        g.addWidget(pid_apply)
        lay.addWidget(grp)

        lay.addStretch()

    def _apply(self) -> None:
        try:
            self._dsp.set_speed(self._ch, self._spin.value())
        except Exception:
            pass

    def _reset(self) -> None:
        try:
            self._dsp.reset_speed(self._ch)
        except Exception:
            pass

    def _apply_pid(self) -> None:
        try:
            self._dsp.set_speed_pid(self._ch, self._sp.value(), self._si.value(), self._sd.value())
        except Exception:
            pass


# ── Torque control tab ─────────────────────────────────────────────────────────

class _TorqueTab(QWidget):
    def __init__(self, dsp, ch: int, parent=None) -> None:
        super().__init__(parent)
        self._dsp = dsp
        self._ch  = ch
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)

        # Torque setpoint
        row = QHBoxLayout()
        row.addWidget(QLabel("Set torque (N·m):"))
        self._torque_spin = QDoubleSpinBox()
        self._torque_spin.setRange(0, 9999)
        self._torque_spin.setDecimals(3)
        self._torque_spin.setValue(0.0)
        self._torque_spin.setStyleSheet("QDoubleSpinBox { background:#252535; color:white; }")
        row.addWidget(self._torque_spin)
        lay.addLayout(row)

        btn_row = QHBoxLayout()
        apply = QPushButton("Apply Torque")
        apply.setStyleSheet(f"QPushButton {{ background:{_GREEN}; color:white; border-radius:4px; padding:4px 12px; }}")
        apply.clicked.connect(self._apply)
        btn_row.addWidget(apply)
        reset = QPushButton("Reset")
        reset.setStyleSheet(f"QPushButton {{ background:{_RED}; color:white; border-radius:4px; padding:4px 12px; }}")
        reset.clicked.connect(self._reset)
        btn_row.addWidget(reset)
        lay.addLayout(btn_row)

        # Current output
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Brake current (%):"))
        self._cur_spin = QDoubleSpinBox()
        self._cur_spin.setRange(0, 99.99)
        self._cur_spin.setDecimals(2)
        self._cur_spin.setValue(0.0)
        self._cur_spin.setStyleSheet("QDoubleSpinBox { background:#252535; color:white; }")
        row2.addWidget(self._cur_spin)
        cur_apply = QPushButton("Set Current")
        cur_apply.setStyleSheet(f"QPushButton {{ background:{_ACCENT}; color:white; border-radius:4px; padding:4px 8px; }}")
        cur_apply.clicked.connect(self._apply_cur)
        row2.addWidget(cur_apply)
        lay.addLayout(row2)

        # PID
        grp = QGroupBox("Torque PID")
        grp.setStyleSheet("QGroupBox { color:#CCC; border:1px solid #333355; margin-top:6px; }")
        g = QHBoxLayout(grp)
        for name, attr in (("P", "_tp"), ("I", "_ti"), ("D", "_td")):
            g.addWidget(QLabel(name + ":"))
            sb = QSpinBox(); sb.setRange(0, 99); sb.setValue(50 if name == "P" else (10 if name == "I" else 0))
            sb.setStyleSheet("QSpinBox { background:#252535; color:white; }")
            setattr(self, attr, sb)
            g.addWidget(sb)
        pid_apply = QPushButton("Set PID")
        pid_apply.setStyleSheet(f"QPushButton {{ background:{_ACCENT}; color:white; border-radius:4px; padding:2px 8px; }}")
        pid_apply.clicked.connect(self._apply_pid)
        g.addWidget(pid_apply)
        lay.addWidget(grp)

        lay.addStretch()

    def _apply(self) -> None:
        try:
            self._dsp.set_torque(self._ch, self._torque_spin.value())
        except Exception:
            pass

    def _reset(self) -> None:
        try:
            self._dsp.reset_torque(self._ch)
        except Exception:
            pass

    def _apply_cur(self) -> None:
        try:
            self._dsp.set_current(self._ch, self._cur_spin.value())
        except Exception:
            pass

    def _apply_pid(self) -> None:
        try:
            self._dsp.set_torque_pid(self._ch, self._tp.value(), self._ti.value(), self._td.value())
        except Exception:
            pass


# ── Ramp tab ───────────────────────────────────────────────────────────────────

class _RampTab(QWidget):
    def __init__(self, dsp, ch: int, parent=None) -> None:
        super().__init__(parent)
        self._dsp = dsp
        self._ch  = ch
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)

        row = QHBoxLayout()
        row.addWidget(QLabel("Profile:"))
        self._mode = QComboBox()
        self._mode.addItems(["Linear", "Cosine"])
        self._mode.setStyleSheet("QComboBox { background:#252535; color:white; }")
        row.addWidget(self._mode)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Rate (rpm/s) or time (s):"))
        self._rate = QDoubleSpinBox()
        self._rate.setRange(0.1, 9999)
        self._rate.setDecimals(1)
        self._rate.setValue(100.0)
        self._rate.setStyleSheet("QDoubleSpinBox { background:#252535; color:white; }")
        row2.addWidget(self._rate)
        lay.addLayout(row2)

        btn_row = QHBoxLayout()
        for label, slot, colour in (
            ("Ramp Up",   self._up,    _GREEN),
            ("Ramp Down", self._down,  "#FF8C1A"),
            ("Abort",     self._abort, _RED),
        ):
            b = QPushButton(label)
            b.setStyleSheet(f"QPushButton {{ background:{colour}; color:white; border-radius:4px; padding:4px 10px; }}")
            b.clicked.connect(slot)
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

        # Reset channel
        lay.addWidget(_hsep())
        reset = QPushButton("Reset Channel (Free Run, Brake Off)")
        reset.setStyleSheet(f"QPushButton {{ background:{_RED}; color:white; border-radius:4px; padding:4px 12px; }}")
        reset.clicked.connect(self._reset)
        lay.addWidget(reset)

        lay.addStretch()

    def _up(self) -> None:
        try:
            self._dsp.ramp_up(self._ch, self._mode.currentIndex() == 0, self._rate.value())
        except Exception:
            pass

    def _down(self) -> None:
        try:
            self._dsp.ramp_down(self._ch, self._mode.currentIndex() == 0, self._rate.value())
        except Exception:
            pass

    def _abort(self) -> None:
        try:
            self._dsp.abort_ramp(self._ch)
        except Exception:
            pass

    def _reset(self) -> None:
        try:
            self._dsp.reset_channel(self._ch)
        except Exception:
            pass


# ── Alarms tab ─────────────────────────────────────────────────────────────────

class _AlarmsTab(QWidget):
    def __init__(self, dsp, ch: int, parent=None) -> None:
        super().__init__(parent)
        self._dsp = dsp
        self._ch  = ch
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)

        grp = QGroupBox("Alarm Setpoints")
        grp.setStyleSheet("QGroupBox { color:#CCC; border:1px solid #333355; margin-top:6px; }")
        g = QGridLayout(grp)

        self._spins: dict[str, QDoubleSpinBox] = {}
        for row_idx, (label, attr, unit, mn, mx, dec, default) in enumerate([
            ("Speed alarm (rpm)",    "speed",  "rpm", 0, 199999, 1, 5000),
            ("Torque alarm (N·m)",   "torque", "N·m", 0, 9999,   3, 100),
            ("Power alarm (kW)",     "power",  "kW",  0, 99999,  2, 10),
        ]):
            g.addWidget(QLabel(label), row_idx, 0)
            sb = QDoubleSpinBox()
            sb.setRange(mn, mx)
            sb.setDecimals(dec)
            sb.setValue(default)
            sb.setStyleSheet("QDoubleSpinBox { background:#252535; color:white; }")
            self._spins[attr] = sb
            g.addWidget(sb, row_idx, 1)

        apply = QPushButton("Apply Alarms")
        apply.setStyleSheet(f"QPushButton {{ background:{_ACCENT}; color:white; border-radius:4px; padding:4px 12px; }}")
        apply.clicked.connect(self._apply)
        g.addWidget(apply, 3, 0, 1, 2)
        lay.addWidget(grp)

        row = QHBoxLayout()
        self._enable_chk = QCheckBox("Alarms enabled")
        self._enable_chk.setChecked(True)
        self._enable_chk.setStyleSheet(f"color:{_TEXT};")
        self._enable_chk.toggled.connect(self._toggle)
        row.addWidget(self._enable_chk)
        lay.addLayout(row)

        # Tare / freeze PID
        lay.addWidget(_hsep())
        misc = QHBoxLayout()
        tare_on  = QPushButton("Tare ON")
        tare_off = QPushButton("Tare OFF")
        frz_on   = QPushButton("Freeze PID")
        frz_off  = QPushButton("Unfreeze PID")
        for btn, slot in (
            (tare_on,  self._tare_on),
            (tare_off, self._tare_off),
            (frz_on,   self._frz_on),
            (frz_off,  self._frz_off),
        ):
            btn.setStyleSheet(f"QPushButton {{ background:#252535; color:{_TEXT}; border-radius:4px; padding:3px 8px; border:1px solid #555; }}")
            btn.clicked.connect(slot)
            misc.addWidget(btn)
        lay.addLayout(misc)

        lay.addStretch()

    def _apply(self) -> None:
        try:
            self._dsp.set_speed_alarm(self._ch, self._spins["speed"].value())
            self._dsp.set_torque_alarm(self._ch, self._spins["torque"].value())
            self._dsp.set_power_alarm(self._ch, self._spins["power"].value())
        except Exception:
            pass

    def _toggle(self, checked: bool) -> None:
        try:
            self._dsp.set_alarms(self._ch, checked)
        except Exception:
            pass

    def _tare_on(self)  -> None:
        try: self._dsp.tare(self._ch, True)
        except Exception: pass

    def _tare_off(self) -> None:
        try: self._dsp.tare(self._ch, False)
        except Exception: pass

    def _frz_on(self)   -> None:
        try: self._dsp.freeze_pid(self._ch, True)
        except Exception: pass

    def _frz_off(self)  -> None:
        try: self._dsp.freeze_pid(self._ch, False)
        except Exception: pass


# ── Helper ─────────────────────────────────────────────────────────────────────

def _hsep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("color:#333355;")
    return f


# ── Single-channel panel ───────────────────────────────────────────────────────

class _ChannelPanel(QWidget):
    def __init__(self, dsp, ch: int, parent=None) -> None:
        super().__init__(parent)
        self._dsp = dsp
        self._ch  = ch
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self._strip = _ChannelStrip(ch)
        lay.addWidget(self._strip)

        tabs = QTabWidget()
        tabs.addTab(_SpeedTab(dsp, ch),   "Speed")
        tabs.addTab(_TorqueTab(dsp, ch),  "Torque")
        tabs.addTab(_RampTab(dsp, ch),    "Ramp")
        tabs.addTab(_AlarmsTab(dsp, ch),  "Alarms")
        lay.addWidget(tabs, 1)

    def refresh(self) -> None:
        try:
            # cached snapshot — serial I/O happens on the driver poll thread
            cd = self._dsp.get_state().ch(self._ch)
            self._strip.update(cd)
        except Exception:
            pass


# ── Public panel ───────────────────────────────────────────────────────────────

class DSP7000Panel(QWidget):
    """Main panel for the Magtrol DSP7000 controller."""

    def __init__(self, dsp, parent=None) -> None:
        super().__init__(parent)
        self._dsp = dsp

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        # header row
        hdr = QHBoxLayout()
        title = QLabel("Magtrol DSP7000  •  Dynamometer Controller")
        title.setStyleSheet(f"color:{_ACCENT}; font-size:11pt; font-weight:bold;")
        hdr.addWidget(title)
        hdr.addStretch()

        save_btn = QPushButton("💾 Save Config")
        save_btn.setStyleSheet(f"QPushButton {{ background:#252535; color:{_TEXT}; border-radius:4px; padding:3px 10px; border:1px solid #555; }}")
        save_btn.clicked.connect(self._save)
        hdr.addWidget(save_btn)
        lay.addLayout(hdr)

        # two channel panels side by side
        ch_row = QHBoxLayout()
        self._ch1 = _ChannelPanel(dsp, 1)
        self._ch2 = _ChannelPanel(dsp, 2)
        ch_row.addWidget(self._ch1)
        ch_row.addWidget(self._ch2)
        lay.addLayout(ch_row, 1)

        # polling timer — 500 ms
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(500)

    def _poll(self) -> None:
        self._ch1.refresh()
        self._ch2.refresh()

    def _save(self) -> None:
        try:
            self._dsp.save(1)
            self._dsp.save(2)
        except Exception:
            pass
