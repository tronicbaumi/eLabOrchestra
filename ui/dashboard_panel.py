"""
Dashboard Panel – live visualization widgets driven by Blockly programs.

Widget types
────────────
  ArcGauge      – semi-circular dial gauge
  YTChart       – rolling time-series line chart
  XYChart       – X-Y scatter / line chart
  TextDisplay   – large numeric / text readout
  ButtonWidget  – push button with latching "clicked" state
  CheckboxWidget – checkbox with label
  DropdownWidget – labelled drop-down (combo box)
  KnobWidget    – rotary knob (drag / scroll wheel)
  SwitchWidget  – animated iOS-style toggle switch
  SliderWidget  – horizontal fill slider

Layout features
───────────────
  • Each card can span 1, 2 or 3 columns  (click the ◀▶ button in the header)
  • Cards can be dragged to any grid position by their title bar
  • Toolbar: column count selector, Save Layout, Load Layout, Clear All
  • Layout (order + spans + widget configs) is saved/loaded as JSON

Thread safety
─────────────
  All public methods are thread-safe; they marshal updates to the Qt main
  thread via a queued signal so the Blockly executor can call them freely
  from background threads.
"""

from __future__ import annotations

import json
import math
import time
import threading
from collections import deque
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal, QTimer, QRectF, QPointF, QPoint, QSize
from PySide6.QtGui import (
    QPainter, QColor, QPen, QFont, QBrush, QPainterPath,
    QMouseEvent, QRadialGradient, QLinearGradient, QPixmap,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea,
    QLabel, QPushButton, QCheckBox, QComboBox, QFrame, QSizePolicy,
    QToolButton, QFileDialog, QSpinBox, QApplication,
)

_DARK  = "#1A1A2A"
_CARD  = "#252535"
_EDGE  = "#333355"
_TEXT  = "#FFFFFF"
_DIM   = "#8888AA"
_DASH  = "#E64A19"
_DROP  = "#4C97FF"   # drop-target highlight colour


def _hex(c: str) -> QColor:
    return QColor(c)


# ══════════════════════════════════════════════════════════════════════════════
# Individual display / control widgets
# ══════════════════════════════════════════════════════════════════════════════

class _ArcGauge(QWidget):
    """Semi-circular arc gauge (270° sweep)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value  = 0.0
        self._min    = 0.0
        self._max    = 100.0
        self._label  = ""
        self._colour = "#4C97FF"
        self.setMinimumSize(180, 160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def configure(self, label: str = "", min_val: float = 0,
                  max_val: float = 100, colour: str = "#4C97FF") -> None:
        self._label  = label
        self._min    = float(min_val)
        self._max    = float(max_val)
        self._colour = colour
        self.update()

    def set_value(self, value: float) -> None:
        self._value = float(value)
        self.update()

    def get_config(self) -> dict:
        return {"label": self._label, "min": self._min,
                "max": self._max, "colour": self._colour}

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h  = self.width(), self.height()
        margin = 18
        r     = min(w - 2 * margin, (h - 2 * margin) * 1.15) * 0.48
        cx    = w / 2
        cy    = h * 0.54
        thick = max(8, int(r * 0.22))

        pen = QPen(_hex(_EDGE), thick, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
        p.drawArc(rect, int(225 * 16), int(-270 * 16))

        rng  = self._max - self._min
        frac = max(0.0, min(1.0, (self._value - self._min) / rng)) if rng else 0.0
        if frac > 0:
            pen.setColor(_hex(self._colour))
            p.setPen(pen)
            p.drawArc(rect, int(225 * 16), int(-270 * 16 * frac))

        p.setPen(_hex(_DIM))
        p.setFont(QFont("Arial", max(7, int(r * 0.14))))
        for ang_deg, val in [(225, self._min), (-45, self._max)]:
            rad = math.radians(ang_deg)
            tx  = cx + (r + thick) * math.cos(rad)
            ty  = cy - (r + thick) * math.sin(rad)
            p.drawText(QRectF(tx - 20, ty - 10, 40, 20), Qt.AlignCenter, f"{val:g}")

        p.setPen(_hex(_TEXT))
        p.setFont(QFont("Arial", max(10, int(r * 0.28)), QFont.Bold))
        p.drawText(QRectF(cx - r, cy - r * 0.4, 2 * r, r * 0.8),
                   Qt.AlignCenter, f"{self._value:.4g}")

        if self._label:
            p.setPen(_hex(_DIM))
            p.setFont(QFont("Arial", max(7, int(r * 0.16))))
            p.drawText(QRectF(cx - r, cy + r * 0.18, 2 * r, r * 0.4),
                       Qt.AlignCenter, self._label)
        p.end()


class _YTChart(QWidget):
    """Rolling time-series chart."""

    MAX_POINTS = 2000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data:  deque[tuple[float, float]] = deque(maxlen=self.MAX_POINTS)
        self._label  = ""
        self._colour = "#4CAF50"
        self._window = 60.0
        self._t0     = time.monotonic()
        self.setMinimumSize(220, 160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def configure(self, label: str = "", colour: str = "#4CAF50",
                  window: float = 60.0) -> None:
        self._label  = label
        self._colour = colour
        self._window = max(1.0, float(window))
        self.update()

    def add_point(self, value: float) -> None:
        self._data.append((time.monotonic() - self._t0, float(value)))
        self.update()

    def clear(self) -> None:
        self._data.clear()
        self._t0 = time.monotonic()
        self.update()

    def get_config(self) -> dict:
        return {"label": self._label, "colour": self._colour, "window": self._window}

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, _hex(_CARD))
        pad_l, pad_r, pad_t, pad_b = 46, 12, 18, 28
        cw = w - pad_l - pad_r
        ch = h - pad_t - pad_b
        if cw < 10 or ch < 10:
            return

        p.setPen(QPen(_hex(_EDGE), 1))
        p.drawLine(pad_l, pad_t, pad_l, pad_t + ch)
        p.drawLine(pad_l, pad_t + ch, pad_l + cw, pad_t + ch)

        t_now = time.monotonic() - self._t0
        t_min = t_now - self._window
        pts   = [(t, v) for t, v in self._data if t >= t_min]
        if not pts:
            self._draw_label(p, w, h); p.end(); return

        vals = [v for _, v in pts]
        y_min, y_max = min(vals), max(vals)
        if y_min == y_max: y_min -= 1; y_max += 1
        y_rng = y_max - y_min

        def sx(t): return pad_l + cw * max(0, (t - t_min) / self._window)
        def sy(v): return pad_t + ch * (1 - (v - y_min) / y_rng)

        p.setPen(QPen(_hex(_EDGE), 1, Qt.DotLine))
        p.setFont(QFont("Arial", 7))
        for i in range(5):
            yv  = y_min + y_rng * i / 4
            yp  = sy(yv)
            p.drawLine(int(pad_l), int(yp), int(pad_l + cw), int(yp))
            p.setPen(_hex(_DIM))
            p.drawText(QRectF(0, yp - 8, pad_l - 2, 16),
                       Qt.AlignRight | Qt.AlignVCenter, f"{yv:.3g}")
            p.setPen(QPen(_hex(_EDGE), 1, Qt.DotLine))

        path = QPainterPath()
        first = True
        for t, v in pts:
            x, y = sx(t), sy(v)
            if first: path.moveTo(x, y); first = False
            else:     path.lineTo(x, y)
        pen = QPen(_hex(self._colour), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen); p.drawPath(path)

        p.setPen(_hex(_DIM)); p.setFont(QFont("Arial", 7))
        for i in range(5):
            tv  = t_min + self._window * i / 4
            xp  = sx(tv)
            lbl = f"{int(tv)}s" if abs(tv) < 3600 else f"{tv/60:.1f}m"
            p.drawText(QRectF(xp - 20, pad_t + ch + 2, 40, 14), Qt.AlignCenter, lbl)

        self._draw_label(p, w, h); p.end()

    def _draw_label(self, p, w, h):
        if self._label:
            p.setPen(_hex(_DIM)); p.setFont(QFont("Arial", 8))
            p.drawText(QRectF(0, 2, w, 14), Qt.AlignCenter, self._label)


class _XYChart(QWidget):
    """X-Y scatter / line chart."""

    MAX_POINTS = 2000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data:  deque[tuple[float, float]] = deque(maxlen=self.MAX_POINTS)
        self._label  = ""
        self._colour = "#FF9800"
        self.setMinimumSize(220, 160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def configure(self, label: str = "", colour: str = "#FF9800") -> None:
        self._label = label; self._colour = colour; self.update()

    def add_point(self, x: float, y: float) -> None:
        self._data.append((float(x), float(y))); self.update()

    def clear(self) -> None:
        self._data.clear(); self.update()

    def get_config(self) -> dict:
        return {"label": self._label, "colour": self._colour}

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, _hex(_CARD))
        pad_l, pad_r, pad_t, pad_b = 46, 12, 18, 28
        cw = w - pad_l - pad_r; ch = h - pad_t - pad_b
        if cw < 10 or ch < 10: return

        p.setPen(QPen(_hex(_EDGE), 1))
        p.drawLine(pad_l, pad_t, pad_l, pad_t + ch)
        p.drawLine(pad_l, pad_t + ch, pad_l + cw, pad_t + ch)

        if not self._data:
            self._draw_label(p, w, h); p.end(); return

        xs = [x for x, _ in self._data]; ys = [y for _, y in self._data]
        x_min, x_max = min(xs), max(xs); y_min, y_max = min(ys), max(ys)
        if x_min == x_max: x_min -= 1; x_max += 1
        if y_min == y_max: y_min -= 1; y_max += 1

        def sx(x): return pad_l + cw * (x - x_min) / (x_max - x_min)
        def sy(y): return pad_t + ch * (1 - (y - y_min) / (y_max - y_min))

        p.setPen(QPen(_hex(_EDGE), 1, Qt.DotLine)); p.setFont(QFont("Arial", 7))
        for i in range(5):
            xv = x_min + (x_max - x_min) * i / 4
            p.drawLine(int(sx(xv)), pad_t, int(sx(xv)), pad_t + ch)
            yv = y_min + (y_max - y_min) * i / 4; yp = sy(yv)
            p.drawLine(pad_l, int(yp), pad_l + cw, int(yp))
            p.setPen(_hex(_DIM))
            p.drawText(QRectF(0, yp-8, pad_l-2, 16), Qt.AlignRight|Qt.AlignVCenter, f"{yv:.3g}")
            p.drawText(QRectF(sx(xv)-20, pad_t+ch+2, 40, 14), Qt.AlignCenter, f"{xv:.3g}")
            p.setPen(QPen(_hex(_EDGE), 1, Qt.DotLine))

        path = QPainterPath(); first = True
        for x, y in self._data:
            px, py = sx(x), sy(y)
            if first: path.moveTo(px, py); first = False
            else:     path.lineTo(px, py)
        p.setPen(QPen(_hex(self._colour), 2, Qt.SolidLine, Qt.RoundCap))
        p.drawPath(path)
        p.setBrush(_hex(self._colour)); p.setPen(Qt.NoPen)
        for x, y in list(self._data)[-50:]:
            p.drawEllipse(QPointF(sx(x), sy(y)), 3, 3)

        self._draw_label(p, w, h); p.end()

    def _draw_label(self, p, w, h):
        if self._label:
            p.setPen(_hex(_DIM)); p.setFont(QFont("Arial", 8))
            p.drawText(QRectF(0, 2, w, 14), Qt.AlignCenter, self._label)


class _TextDisplay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._value  = "—"
        self._label  = ""
        self._colour = "#FFFFFF"
        self.setMinimumSize(140, 80)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def configure(self, label: str = "", colour: str = "#FFFFFF") -> None:
        self._label = label; self._colour = colour; self.update()

    def set_value(self, value) -> None:
        if isinstance(value, float):
            self._value = f"{value:.3f}"
        else:
            self._value = str(value)
        self.update()

    def get_config(self) -> dict:
        return {"label": self._label, "colour": self._colour}

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, _hex(_CARD))
        top = 4
        if self._label:
            p.setPen(_hex(_DIM)); p.setFont(QFont("Arial", 8))
            p.drawText(QRectF(0, 4, w, 16), Qt.AlignCenter, self._label)
            top = 22
        p.setPen(_hex(self._colour))
        fs = max(14, min(32, int(h * 0.4)))
        p.setFont(QFont("Courier New", fs, QFont.Bold))
        p.drawText(QRectF(8, top, w - 16, h - top - 4), Qt.AlignCenter, self._value)
        p.end()


class _ButtonWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._clicked = False
        self._lock    = threading.Lock()
        self._label   = "Button"
        self._colour  = "#4C97FF"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self._btn = QPushButton("Button")
        self._btn.setFixedHeight(36)
        self._btn.clicked.connect(self._on_click)
        lay.addWidget(self._btn)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._apply_style()

    def _apply_style(self) -> None:
        c  = self._colour
        cl = QColor(c).lighter(120).name()
        cd = QColor(c).darker(120).name()
        self._btn.setStyleSheet(
            f"QPushButton {{ background:{c}; color:white; border-radius:6px;"
            f" font-weight:bold; font-size:10pt; }}"
            f"QPushButton:hover {{ background:{cl}; }}"
            f"QPushButton:pressed {{ background:{cd}; }}")

    def configure(self, label: str = "Button", colour: str = "#4C97FF") -> None:
        self._label = label; self._colour = colour
        self._btn.setText(label); self._apply_style()

    def _on_click(self) -> None:
        with self._lock: self._clicked = True

    def consume_click(self) -> bool:
        with self._lock:
            v = self._clicked; self._clicked = False; return v

    def get_config(self) -> dict:
        return {"label": self._label, "colour": self._colour}


class _CheckboxWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._label   = "Checkbox"
        self._default = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self._cb = QCheckBox("Checkbox")
        self._cb.setStyleSheet(
            f"QCheckBox {{ color:{_TEXT}; font-size:10pt; }}"
            f"QCheckBox::indicator {{ width:18px; height:18px; }}")
        lay.addWidget(self._cb)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def configure(self, label: str = "Checkbox", default: bool = False) -> None:
        self._label = label; self._default = default
        self._cb.setText(label); self._cb.setChecked(default)

    def is_checked(self) -> bool:
        return self._cb.isChecked()

    def get_config(self) -> dict:
        return {"label": self._label, "default": self._cb.isChecked()}


class _DropdownWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._label   = "Dropdown"
        self._options: list[str] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)
        self._lbl = QLabel("Dropdown")
        self._lbl.setStyleSheet(f"color:{_DIM}; font-size:8pt;")
        lay.addWidget(self._lbl)
        self._combo = QComboBox()
        self._combo.setStyleSheet(
            f"QComboBox {{ background:#333350; color:{_TEXT}; border:1px solid #555580;"
            f" border-radius:4px; padding:4px 8px; font-size:10pt; min-height:26px; }}"
            f"QComboBox::drop-down {{ border:none; width:20px; }}"
            f"QComboBox QAbstractItemView {{ background:#333350; color:{_TEXT};"
            f" selection-background-color:#4C97FF; }}")
        lay.addWidget(self._combo)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def configure(self, label: str = "Dropdown",
                  options: list[str] | None = None) -> None:
        self._label   = label
        self._options = list(options or [])
        self._lbl.setText(label)
        self._combo.blockSignals(True)
        current = self._combo.currentText()
        self._combo.clear()
        if self._options:
            self._combo.addItems(self._options)
            idx = self._combo.findText(current)
            if idx >= 0: self._combo.setCurrentIndex(idx)
        self._combo.blockSignals(False)

    def current_value(self) -> str:
        return self._combo.currentText()

    def get_config(self) -> dict:
        return {"label": self._label, "options": self._options}


class _KnobWidget(QWidget):
    """Rotary knob — 270° sweep. Drag up/down or scroll wheel. Double-click resets."""

    valueChanged = Signal(float)
    _START_DEG = 225
    _SPAN_DEG  = 270

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value   = 0.0
        self._min     = 0.0
        self._max     = 100.0
        self._step    = 1.0
        self._label   = ""
        self._colour  = "#4C97FF"
        self._lock    = threading.Lock()
        self._drag_y  = 0
        self._drag_v  = 0.0
        self.setMinimumSize(160, 170)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.SizeVerCursor)

    def configure(self, label: str = "", min_val: float = 0, max_val: float = 100,
                  step: float = 1, colour: str = "#4C97FF") -> None:
        self._label  = label
        self._min    = float(min_val); self._max = float(max_val)
        self._step   = float(step) if step > 0 else 1.0
        self._colour = colour
        self._value  = max(self._min, min(self._max, self._value))
        self.update()

    def get_value(self) -> float:
        with self._lock: return self._value

    def set_value(self, value: float) -> None:
        with self._lock:
            self._value = max(self._min, min(self._max, float(value)))
        self.update(); self.valueChanged.emit(self._value)

    def get_config(self) -> dict:
        return {"label": self._label, "min": self._min, "max": self._max,
                "step": self._step, "colour": self._colour}

    def mousePressEvent(self, ev: QMouseEvent) -> None:
        self._drag_y = ev.position().y(); self._drag_v = self._value

    def mouseMoveEvent(self, ev: QMouseEvent) -> None:
        dy    = self._drag_y - ev.position().y()
        rng   = self._max - self._min
        delta = dy / max(1, self.height()) * rng
        raw   = self._drag_v + delta
        steps = round((raw - self._min) / self._step)
        self.set_value(self._min + steps * self._step)

    def mouseDoubleClickEvent(self, ev: QMouseEvent) -> None:
        self.set_value(self._min)

    def wheelEvent(self, ev) -> None:
        self.set_value(self._value + (1 if ev.angleDelta().y() > 0 else -1) * self._step)

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h    = self.width(), self.height()
        margin  = 22
        r_outer = max(30.0, min(w / 2, (h - 30) / 2) - margin * 0.5)
        cx      = w / 2; cy = h * 0.50
        track_w = max(6, int(r_outer * 0.18))
        knob_r  = r_outer - track_w - 4

        pen = QPen(_hex(_EDGE), track_w, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        rect = QRectF(cx - r_outer, cy - r_outer, r_outer * 2, r_outer * 2)
        p.drawArc(rect, int(self._START_DEG * 16), int(-self._SPAN_DEG * 16))

        rng  = self._max - self._min
        frac = max(0.0, min(1.0, (self._value - self._min) / rng)) if rng else 0.0
        if frac > 0:
            pen.setColor(_hex(self._colour)); p.setPen(pen)
            p.drawArc(rect, int(self._START_DEG * 16), int(-self._SPAN_DEG * 16 * frac))

        grad = QRadialGradient(cx - knob_r * 0.25, cy - knob_r * 0.25, knob_r * 1.2)
        grad.setColorAt(0.0, QColor("#3A3A5A")); grad.setColorAt(1.0, QColor("#1A1A2A"))
        p.setPen(QPen(_hex(_EDGE), 1.5)); p.setBrush(QBrush(grad))
        p.drawEllipse(QRectF(cx - knob_r, cy - knob_r, knob_r * 2, knob_r * 2))

        ang_rad = math.radians(self._START_DEG - self._SPAN_DEG * frac)
        p.setPen(QPen(_hex(self._colour), max(2, int(knob_r * 0.09)), Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(cx + knob_r*0.38*math.cos(ang_rad), cy - knob_r*0.38*math.sin(ang_rad)),
                   QPointF(cx + knob_r*0.80*math.cos(ang_rad), cy - knob_r*0.80*math.sin(ang_rad)))

        p.setPen(Qt.NoPen); p.setBrush(_hex(self._colour))
        p.drawEllipse(QPointF(cx, cy), max(3.0, knob_r * 0.10), max(3.0, knob_r * 0.10))

        p.setPen(_hex(_DIM)); p.setFont(QFont("Arial", max(6, int(r_outer * 0.14))))
        for ang_deg, val in [(self._START_DEG, self._min),
                              (self._START_DEG - self._SPAN_DEG, self._max)]:
            rad = math.radians(ang_deg)
            tx  = cx + (r_outer + track_w) * math.cos(rad)
            ty  = cy - (r_outer + track_w) * math.sin(rad)
            p.drawText(QRectF(tx - 18, ty - 9, 36, 18), Qt.AlignCenter, f"{val:g}")

        p.setPen(_hex(_TEXT))
        p.setFont(QFont("Arial", max(9, int(knob_r * 0.30)), QFont.Bold))
        p.drawText(QRectF(cx - knob_r*0.8, cy - knob_r*0.3, knob_r*1.6, knob_r*0.6),
                   Qt.AlignCenter, f"{self._value:.4g}")

        if self._label:
            p.setPen(_hex(_DIM)); p.setFont(QFont("Arial", max(7, int(r_outer * 0.14))))
            p.drawText(QRectF(0, cy + r_outer * 0.62, w, 22), Qt.AlignCenter, self._label)
        p.end()


class _SwitchWidget(QWidget):
    """Animated iOS-style toggle switch."""

    toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state     = False
        self._label     = "Switch"
        self._on_label  = "ON"
        self._off_label = "OFF"
        self._on_colour = "#43A047"
        self._lock      = threading.Lock()
        self._anim_frac = 0.0
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(16)
        self._anim_timer.timeout.connect(self._anim_step)
        self.setMinimumSize(140, 80)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setCursor(Qt.PointingHandCursor)

    def configure(self, label: str = "Switch", on_label: str = "ON",
                  off_label: str = "OFF", on_colour: str = "#43A047") -> None:
        self._label = label; self._on_label = on_label
        self._off_label = off_label; self._on_colour = on_colour
        self.update()

    def is_on(self) -> bool:
        with self._lock: return self._state

    def set_state(self, state: bool) -> None:
        with self._lock: self._state = bool(state)
        self._start_anim(); self.update(); self.toggled.emit(self._state)

    def _start_anim(self) -> None:
        if not self._anim_timer.isActive(): self._anim_timer.start()

    def _anim_step(self) -> None:
        target = 1.0 if self._state else 0.0
        diff   = target - self._anim_frac
        if abs(diff) < 0.04:
            self._anim_frac = target; self._anim_timer.stop()
        else:
            self._anim_frac += diff * 0.25
        self.update()

    def mousePressEvent(self, ev: QMouseEvent) -> None:
        self.set_state(not self._state)

    def get_config(self) -> dict:
        return {"label": self._label, "on_label": self._on_label,
                "off_label": self._off_label, "on_colour": self._on_colour}

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        lbl_h = 0
        if self._label:
            p.setPen(_hex(_DIM)); p.setFont(QFont("Arial", 9))
            p.drawText(QRectF(0, 4, w, 18), Qt.AlignCenter, self._label)
            lbl_h = 20

        track_h = min(38, (h - lbl_h - 14))
        track_w = track_h * 2.0
        tx = (w - track_w) / 2; ty = lbl_h + 8; radius = track_h / 2

        off_c = QColor(_CARD).lighter(130); on_c = QColor(self._on_colour)
        track_c = QColor(
            int(off_c.red()   + (on_c.red()   - off_c.red())   * self._anim_frac),
            int(off_c.green() + (on_c.green() - off_c.green()) * self._anim_frac),
            int(off_c.blue()  + (on_c.blue()  - off_c.blue())  * self._anim_frac),
        )
        p.setPen(Qt.NoPen); p.setBrush(track_c)
        p.drawRoundedRect(QRectF(tx, ty, track_w, track_h), radius, radius)
        p.setPen(QPen(_hex(_EDGE), 1.5)); p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(tx, ty, track_w, track_h), radius, radius)

        thumb_r   = radius - 3
        thumb_x   = tx + radius + self._anim_frac * (track_w - 2 * radius)
        thumb_y   = ty + track_h / 2
        tg = QRadialGradient(thumb_x - thumb_r * 0.2, thumb_y - thumb_r * 0.3, thumb_r * 1.3)
        tg.setColorAt(0.0, QColor("#FFFFFF")); tg.setColorAt(1.0, QColor("#CCCCCC"))
        p.setPen(QPen(QColor("#888888"), 1)); p.setBrush(QBrush(tg))
        p.drawEllipse(QPointF(thumb_x, thumb_y), thumb_r, thumb_r)

        state_txt = self._on_label if self._state else self._off_label
        state_col = self._on_colour if self._state else "#666688"
        p.setPen(_hex(state_col)); p.setFont(QFont("Arial", 8, QFont.Bold))
        p.drawText(QRectF(tx, ty + track_h + 4, track_w, 16), Qt.AlignCenter, state_txt)
        p.end()


class _SliderWidget(QWidget):
    """Horizontal fill slider with step-snapping and scroll-wheel support."""

    valueChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value    = 0.0
        self._min      = 0.0
        self._max      = 100.0
        self._step     = 1.0
        self._label    = ""
        self._colour   = "#4C97FF"
        self._lock     = threading.Lock()
        self._dragging = False
        self.setMinimumSize(200, 80)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setCursor(Qt.PointingHandCursor)

    def configure(self, label: str = "", min_val: float = 0, max_val: float = 100,
                  step: float = 1, colour: str = "#4C97FF") -> None:
        self._label = label; self._min = float(min_val); self._max = float(max_val)
        self._step = float(step) if step > 0 else 1.0; self._colour = colour
        self._value = max(self._min, min(self._max, self._value)); self.update()

    def get_value(self) -> float:
        with self._lock: return self._value

    def set_value(self, value: float) -> None:
        v = max(self._min, min(self._max, float(value)))
        if self._step:
            v = self._min + round((v - self._min) / self._step) * self._step
        v = max(self._min, min(self._max, v))
        changed = False
        with self._lock:
            if v != self._value: self._value = v; changed = True
        if changed: self.update(); self.valueChanged.emit(v)

    def get_config(self) -> dict:
        return {"label": self._label, "min": self._min, "max": self._max,
                "step": self._step, "colour": self._colour}

    def _track_rect(self):
        pad_x   = 14; track_h = 8
        lbl_h   = 20 if self._label else 0
        cy      = self.height() / 2 + lbl_h / 2
        return pad_x, cy - track_h / 2, self.width() - 2 * pad_x, track_h

    def _value_to_x(self, value):
        tx, _, tw, _ = self._track_rect()
        rng = self._max - self._min
        return tx + ((value - self._min) / rng if rng else 0) * tw

    def _x_to_value(self, x):
        tx, _, tw, _ = self._track_rect()
        return self._min + max(0.0, min(1.0, (x - tx) / tw if tw else 0)) * (self._max - self._min)

    def mousePressEvent(self, ev: QMouseEvent) -> None:
        self._dragging = True; self.set_value(self._x_to_value(ev.position().x()))

    def mouseMoveEvent(self, ev: QMouseEvent) -> None:
        if self._dragging: self.set_value(self._x_to_value(ev.position().x()))

    def mouseReleaseEvent(self, ev: QMouseEvent) -> None:
        self._dragging = False

    def wheelEvent(self, ev) -> None:
        self.set_value(self._value + (1 if ev.angleDelta().y() > 0 else -1) * self._step)

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        lbl_h = 0
        if self._label:
            p.setPen(_hex(_DIM)); p.setFont(QFont("Arial", 8))
            p.drawText(QRectF(0, 4, w, 18), Qt.AlignCenter, self._label)
            lbl_h = 20

        tx, ty, tw, th = self._track_rect(); tr = th / 2

        p.setPen(Qt.NoPen); p.setBrush(_hex(_EDGE))
        p.drawRoundedRect(QRectF(tx, ty, tw, th), tr, tr)

        rng  = self._max - self._min
        frac = (self._value - self._min) / rng if rng else 0.0
        fill_w = max(0.0, frac * tw)
        if fill_w > 0:
            grad = QLinearGradient(tx, 0, tx + fill_w, 0)
            grad.setColorAt(0.0, QColor(self._colour).darker(110))
            grad.setColorAt(1.0, QColor(self._colour))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(QRectF(tx, ty, fill_w, th), tr, tr)

        thumb_x = self._value_to_x(self._value); thumb_r = th * 1.5
        tg = QRadialGradient(thumb_x - thumb_r*0.2, ty + th/2 - thumb_r*0.3, thumb_r*1.4)
        tg.setColorAt(0.0, QColor(self._colour).lighter(140))
        tg.setColorAt(1.0, QColor(self._colour))
        p.setBrush(QBrush(tg)); p.setPen(QPen(QColor(self._colour).darker(130), 1.5))
        p.drawEllipse(QPointF(thumb_x, ty + th / 2), thumb_r, thumb_r)

        p.setPen(_hex(_DIM)); p.setFont(QFont("Arial", 7))
        bot = ty + th + thumb_r + 2
        p.drawText(QRectF(tx - 10, bot, 30, 14), Qt.AlignLeft,  f"{self._min:g}")
        p.drawText(QRectF(tx + tw - 20, bot, 30, 14), Qt.AlignRight, f"{self._max:g}")

        p.setPen(_hex(_TEXT)); p.setFont(QFont("Arial", 9, QFont.Bold))
        p.drawText(QRectF(thumb_x - 28, bot, 56, 15), Qt.AlignCenter, f"{self._value:.4g}")
        p.end()


# ══════════════════════════════════════════════════════════════════════════════
# Type registry
# ══════════════════════════════════════════════════════════════════════════════

_TYPE_GAUGE    = "gauge"
_TYPE_YT       = "yt"
_TYPE_XY       = "xy"
_TYPE_TEXT     = "text"
_TYPE_BUTTON   = "button"
_TYPE_CHECKBOX = "checkbox"
_TYPE_DROPDOWN = "dropdown"
_TYPE_KNOB     = "knob"
_TYPE_SWITCH   = "switch"
_TYPE_SLIDER   = "slider"

_WIDGET_CLASSES = {
    _TYPE_GAUGE:    _ArcGauge,
    _TYPE_YT:       _YTChart,
    _TYPE_XY:       _XYChart,
    _TYPE_TEXT:     _TextDisplay,
    _TYPE_BUTTON:   _ButtonWidget,
    _TYPE_CHECKBOX: _CheckboxWidget,
    _TYPE_DROPDOWN: _DropdownWidget,
    _TYPE_KNOB:     _KnobWidget,
    _TYPE_SWITCH:   _SwitchWidget,
    _TYPE_SLIDER:   _SliderWidget,
}

_CARD_SIZES = {       # (min_w, min_h)
    _TYPE_GAUGE:    (190, 180),
    _TYPE_YT:       (280, 200),
    _TYPE_XY:       (280, 200),
    _TYPE_TEXT:     (160, 90),
    _TYPE_BUTTON:   (140, 70),
    _TYPE_CHECKBOX: (140, 60),
    _TYPE_DROPDOWN: (160, 80),
    _TYPE_KNOB:     (175, 185),
    _TYPE_SWITCH:   (155, 95),
    _TYPE_SLIDER:   (220, 90),
}


# ══════════════════════════════════════════════════════════════════════════════
# Card title bar  (drag handle + span toggle + close)
# ══════════════════════════════════════════════════════════════════════════════

class _TitleBar(QFrame):
    """
    Orange header bar for a DashCard.
    • Drag from title bar to reorder cards.
    • Click ◀▶ to cycle column span (1 → 2 → 3 → 1).
    • Click ✕ to close.
    """

    _DRAG_THRESHOLD = 8   # px Manhattan distance before drag starts

    def __init__(self, name: str, on_close,
                 on_drag_start, on_drag_move, on_drag_end,
                 on_resize, parent=None):
        super().__init__(parent)
        self._name       = name
        self._on_close   = on_close
        self._on_drag_start = on_drag_start
        self._on_drag_move  = on_drag_move
        self._on_drag_end   = on_drag_end
        self._on_resize  = on_resize
        self._press_pos: Optional[QPoint] = None
        self._dragging   = False
        self._span       = 1

        self.setFixedHeight(26)
        self.setStyleSheet(
            f"background:{_DASH}; border-top-left-radius:7px;"
            f" border-top-right-radius:7px;")
        self.setCursor(Qt.OpenHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 4, 0)
        lay.setSpacing(2)

        self._title_lbl = QLabel(name)
        self._title_lbl.setStyleSheet(
            "color:white; font-size:8pt; font-weight:bold; background:transparent;")
        lay.addWidget(self._title_lbl)
        lay.addStretch()

        self._span_btn = QToolButton()
        self._span_btn.setText("1×")
        self._span_btn.setFixedSize(26, 18)
        self._span_btn.setStyleSheet(
            "QToolButton { background:rgba(0,0,0,30); color:white; border-radius:3px;"
            " font-size:7pt; border:none; }"
            "QToolButton:hover { background:rgba(255,255,255,40); }")
        self._span_btn.clicked.connect(self._cycle_span)
        self._span_btn.setCursor(Qt.PointingHandCursor)
        lay.addWidget(self._span_btn)

        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.setFixedSize(20, 18)
        close_btn.setStyleSheet(
            "QToolButton { background:transparent; color:rgba(255,255,255,160);"
            " border:none; font-size:9pt; }"
            "QToolButton:hover { color:white; }")
        close_btn.clicked.connect(lambda: self._on_close(self._name))
        close_btn.setCursor(Qt.PointingHandCursor)
        lay.addWidget(close_btn)

    def set_span(self, span: int) -> None:
        self._span = span
        self._span_btn.setText(f"{span}×")

    def _cycle_span(self) -> None:
        new_span = (self._span % 3) + 1   # 1→2→3→1
        self._on_resize(self._name, new_span)

    # ── drag ─────────────────────────────────────────────────────────────────

    def mousePressEvent(self, ev: QMouseEvent) -> None:
        if ev.button() == Qt.LeftButton:
            self._press_pos = ev.globalPosition().toPoint()
            self._dragging  = False
            self.setCursor(Qt.ClosedHandCursor)
        ev.accept()

    def mouseMoveEvent(self, ev: QMouseEvent) -> None:
        if ev.buttons() & Qt.LeftButton and self._press_pos is not None:
            gpos = ev.globalPosition().toPoint()
            dist = (gpos - self._press_pos).manhattanLength()
            if not self._dragging and dist > self._DRAG_THRESHOLD:
                self._dragging = True
                self._on_drag_start(self._name, gpos)
            elif self._dragging:
                self._on_drag_move(gpos)
        ev.accept()

    def mouseReleaseEvent(self, ev: QMouseEvent) -> None:
        self.setCursor(Qt.OpenHandCursor)
        if self._dragging:
            self._dragging  = False
            self._press_pos = None
            self._on_drag_end(ev.globalPosition().toPoint())
        ev.accept()


# ══════════════════════════════════════════════════════════════════════════════
# Dashboard card wrapper
# ══════════════════════════════════════════════════════════════════════════════

class _DashCard(QFrame):
    """Card with coloured title bar (drag handle + span toggle + close) and content."""

    def __init__(self, name: str, widget_type: str, widget: QWidget,
                 on_close, on_drag_start, on_drag_move, on_drag_end,
                 on_resize, parent=None) -> None:
        super().__init__(parent)
        self.name        = name
        self.widget_type = widget_type
        self.widget      = widget
        self._highlight  = False

        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            f"_DashCard {{ background:{_CARD}; border:1px solid {_EDGE};"
            f" border-radius:8px; }}")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._title_bar = _TitleBar(
            name, on_close,
            on_drag_start, on_drag_move, on_drag_end, on_resize)
        root.addWidget(self._title_bar)

        content = QFrame()
        content.setStyleSheet(f"background:{_CARD};")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(4, 4, 4, 4)
        cl.addWidget(widget)
        root.addWidget(content, 1)

    def set_span(self, span: int) -> None:
        self._title_bar.set_span(span)

    def set_drop_highlight(self, on: bool) -> None:
        self._highlight = on
        border = f"2px solid {_DROP}" if on else f"1px solid {_EDGE}"
        self.setStyleSheet(
            f"QFrame {{ background:{_CARD}; border:{border}; border-radius:8px; }}")


# ══════════════════════════════════════════════════════════════════════════════
# Dashboard panel
# ══════════════════════════════════════════════════════════════════════════════

class DashboardPanel(QWidget):
    """
    Scrollable grid of named dashboard cards.

    Drag cards by their title bar to reorder.
    Click the span button (1× / 2× / 3×) to resize a card.

    All public API methods are **thread-safe**.
    """

    _queue_signal = Signal(str, str, object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cards:     dict[str, _DashCard] = {}
        self._order:     list[str] = []
        self._spans:     dict[str, int] = {}      # name → column span (1/2/3)
        self._cols       = 3
        self._drag_name: Optional[str] = None     # card being dragged
        self._drag_ghost: Optional[QLabel] = None # floating preview
        self._drop_target: Optional[str] = None   # card currently highlighted

        self._queue_signal.connect(self._dispatch, Qt.QueuedConnection)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── toolbar ───────────────────────────────────────────────────────────
        bar = QFrame()
        bar.setFixedHeight(36)
        bar.setStyleSheet(f"background:#0F0F1A; border-bottom:1px solid {_EDGE};")
        tl = QHBoxLayout(bar)
        tl.setContentsMargins(10, 2, 10, 2)
        tl.setSpacing(8)

        lbl = QLabel("Dashboard")
        lbl.setStyleSheet(f"color:{_TEXT}; font-weight:bold; font-size:9pt;")
        tl.addWidget(lbl)

        tl.addWidget(QLabel("Columns:"))
        self._col_spin = QSpinBox()
        self._col_spin.setRange(1, 6)
        self._col_spin.setValue(self._cols)
        self._col_spin.setFixedWidth(50)
        self._col_spin.setStyleSheet(
            "QSpinBox { background:#252535; color:white; border:1px solid #444;"
            " border-radius:3px; padding:1px 4px; }")
        self._col_spin.valueChanged.connect(self._on_cols_changed)
        tl.addWidget(self._col_spin)

        tl.addStretch()

        for label, slot, colour in (
            ("💾 Save Layout", self.save_layout, "#37474F"),
            ("📂 Load Layout", self.load_layout, "#37474F"),
            ("🗑 Clear All",   self.clear_all,   "#607D8B"),
        ):
            btn = QPushButton(label)
            btn.setFixedHeight(26)
            btn.setStyleSheet(
                f"QPushButton {{ background:{colour}; color:white; border-radius:4px;"
                f" font-size:8pt; padding:0 10px; }}"
                f"QPushButton:hover {{ background:{QColor(colour).lighter(120).name()}; }}")
            btn.clicked.connect(slot)
            tl.addWidget(btn)

        root.addWidget(bar)

        # ── scroll area ───────────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea {{ border:none; background:{_DARK}; }}"
            f"QScrollBar:vertical {{ background:{_CARD}; width:8px; }}"
            f"QScrollBar::handle:vertical {{ background:#444466; border-radius:4px; }}")
        self._container = QWidget()
        self._container.setStyleSheet(f"background:{_DARK};")
        self._grid = QGridLayout(self._container)
        self._grid.setContentsMargins(10, 10, 10, 10)
        self._grid.setSpacing(10)
        scroll.setWidget(self._container)
        root.addWidget(scroll, 1)

    # ── column count ──────────────────────────────────────────────────────────

    def _on_cols_changed(self, value: int) -> None:
        self._cols = value
        self._relayout()

    # ── grid management ───────────────────────────────────────────────────────

    def _relayout(self) -> None:
        while self._grid.count():
            self._grid.takeAt(0)
        col = 0; row = 0
        for name in self._order:
            card   = self._cards[name]
            span   = min(self._spans.get(name, 1), self._cols)
            if col + span > self._cols:
                col = 0; row += 1
            self._grid.addWidget(card, row, col, 1, span)
            col += span
            if col >= self._cols:
                col = 0; row += 1
        for c in range(self._cols):
            self._grid.setColumnStretch(c, 1)

    def _ensure_card(self, name: str, widget_type: str) -> _DashCard:
        if name in self._cards:
            return self._cards[name]
        wcls   = _WIDGET_CLASSES.get(widget_type, _TextDisplay)
        widget = wcls()
        mw, mh = _CARD_SIZES.get(widget_type, (160, 100))
        card = _DashCard(
            name, widget_type, widget,
            on_close       = self._on_close_card,
            on_drag_start  = self._drag_start,
            on_drag_move   = self._drag_move,
            on_drag_end    = self._drag_end,
            on_resize      = self._resize_card,
        )
        card.setMinimumSize(mw, mh)
        self._cards[name] = card
        self._spans[name] = 1
        self._order.append(name)
        self._relayout()
        return card

    def _on_close_card(self, name: str) -> None:
        if name in self._cards:
            card = self._cards.pop(name)
            self._spans.pop(name, None)
            self._order.remove(name)
            card.setParent(None)
            card.deleteLater()
            self._relayout()

    def _resize_card(self, name: str, new_span: int) -> None:
        self._spans[name] = max(1, min(self._cols, new_span))
        self._cards[name].set_span(self._spans[name])
        self._relayout()

    def clear_all(self) -> None:
        for name in list(self._order):
            self._on_close_card(name)

    # ── drag and drop ─────────────────────────────────────────────────────────

    def _drag_start(self, name: str, global_pos: QPoint) -> None:
        self._drag_name = name
        card  = self._cards[name]
        # create floating ghost
        pix   = card.grab()
        ghost = QLabel()
        ghost.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
        ghost.setAttribute(Qt.WA_TranslucentBackground, False)
        ghost.setWindowOpacity(0.72)
        ghost.setPixmap(pix.scaled(
            max(120, pix.width() // 2), max(80, pix.height() // 2),
            Qt.KeepAspectRatio, Qt.SmoothTransformation))
        ghost.resize(ghost.pixmap().size())
        ghost.move(global_pos - QPoint(ghost.width() // 2, 13))
        ghost.show()
        self._drag_ghost = ghost

    def _drag_move(self, global_pos: QPoint) -> None:
        if self._drag_ghost:
            self._drag_ghost.move(global_pos - QPoint(self._drag_ghost.width() // 2, 13))

        # find card under cursor
        target = self._card_at_global(global_pos)
        if target == self._drag_name:
            target = None
        if target != self._drop_target:
            if self._drop_target and self._drop_target in self._cards:
                self._cards[self._drop_target].set_drop_highlight(False)
            if target and target in self._cards:
                self._cards[target].set_drop_highlight(True)
            self._drop_target = target

    def _drag_end(self, global_pos: QPoint) -> None:
        # hide ghost
        if self._drag_ghost:
            self._drag_ghost.hide()
            self._drag_ghost.deleteLater()
            self._drag_ghost = None

        # clear highlight
        if self._drop_target and self._drop_target in self._cards:
            self._cards[self._drop_target].set_drop_highlight(False)

        # perform swap
        src = self._drag_name
        tgt = self._drop_target
        if src and tgt and src != tgt and src in self._order and tgt in self._order:
            i, j = self._order.index(src), self._order.index(tgt)
            self._order[i], self._order[j] = self._order[j], self._order[i]
            self._relayout()

        self._drag_name   = None
        self._drop_target = None

    def _card_at_global(self, global_pos: QPoint) -> Optional[str]:
        for name, card in self._cards.items():
            local = card.mapFromGlobal(global_pos)
            if card.rect().contains(local):
                return name
        return None

    # ── save / load layout ────────────────────────────────────────────────────

    def save_layout(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Dashboard Layout", "",
            "Dashboard Layout (*.dashboard.json);;JSON files (*.json);;All files (*)")
        if not path:
            return
        data = {
            "version": 2,
            "cols":    self._cols,
            "widgets": []
        }
        for name in self._order:
            card   = self._cards[name]
            widget = card.widget
            cfg    = widget.get_config() if hasattr(widget, "get_config") else {}
            data["widgets"].append({
                "name":    name,
                "type":    card.widget_type,
                "colspan": self._spans.get(name, 1),
                "config":  cfg,
            })
        try:
            Path(path).write_text(json.dumps(data, indent=2))
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Save Failed", str(e))

    def load_layout(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Dashboard Layout", "",
            "Dashboard Layout (*.dashboard.json);;JSON files (*.json);;All files (*)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text())
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Load Failed", str(e)); return

        self.clear_all()

        cols = data.get("cols", 3)
        self._cols = cols
        self._col_spin.setValue(cols)

        for entry in data.get("widgets", []):
            name    = entry.get("name", "widget")
            wtype   = entry.get("type", _TYPE_TEXT)
            colspan = entry.get("colspan", 1)
            cfg     = entry.get("config", {})

            card   = self._ensure_card(name, wtype)
            widget = card.widget
            self._resize_card(name, colspan)
            self._restore_widget_config(widget, wtype, cfg)

    def _restore_widget_config(self, widget, wtype: str, cfg: dict) -> None:
        """Apply a saved config dict back to a freshly-created widget."""
        try:
            if wtype == _TYPE_GAUGE:
                widget.configure(cfg.get("label",""), cfg.get("min",0),
                                 cfg.get("max",100), cfg.get("colour","#4C97FF"))
            elif wtype == _TYPE_YT:
                widget.configure(cfg.get("label",""), cfg.get("colour","#4CAF50"),
                                 cfg.get("window",60))
            elif wtype == _TYPE_XY:
                widget.configure(cfg.get("label",""), cfg.get("colour","#FF9800"))
            elif wtype == _TYPE_TEXT:
                widget.configure(cfg.get("label",""), cfg.get("colour","#FFFFFF"))
            elif wtype == _TYPE_BUTTON:
                widget.configure(cfg.get("label","Button"), cfg.get("colour","#4C97FF"))
            elif wtype == _TYPE_CHECKBOX:
                widget.configure(cfg.get("label","Checkbox"), cfg.get("default",False))
            elif wtype == _TYPE_DROPDOWN:
                widget.configure(cfg.get("label","Dropdown"), cfg.get("options",[]))
            elif wtype == _TYPE_KNOB:
                widget.configure(cfg.get("label",""), cfg.get("min",0),
                                 cfg.get("max",100), cfg.get("step",1),
                                 cfg.get("colour","#4C97FF"))
            elif wtype == _TYPE_SWITCH:
                widget.configure(cfg.get("label","Switch"), cfg.get("on_label","ON"),
                                 cfg.get("off_label","OFF"), cfg.get("on_colour","#43A047"))
            elif wtype == _TYPE_SLIDER:
                widget.configure(cfg.get("label",""), cfg.get("min",0),
                                 cfg.get("max",100), cfg.get("step",1),
                                 cfg.get("colour","#4C97FF"))
        except Exception:
            pass

    # ── thread-safe dispatch ──────────────────────────────────────────────────

    def _dispatch(self, op: str, name: str, data: dict) -> None:
        handler = getattr(self, f"_do_{op}", None)
        if handler:
            handler(name, data)

    def _emit(self, op: str, name: str, data: dict) -> None:
        self._queue_signal.emit(op, name, data)

    # ── gauge ─────────────────────────────────────────────────────────────────

    def set_gauge(self, name: str, value: float,
                  min_val: float = 0, max_val: float = 100) -> None:
        self._emit("gauge_value", name, {"value": value, "min": min_val, "max": max_val})

    def config_gauge(self, name: str, label: str = "", min_val: float = 0,
                     max_val: float = 100, colour: str = "#4C97FF") -> None:
        self._emit("gauge_config", name,
                   {"label": label, "min": min_val, "max": max_val, "colour": colour})

    def _do_gauge_value(self, name: str, d: dict) -> None:
        card = self._ensure_card(name, _TYPE_GAUGE)
        card.widget.configure(max_val=d["max"], min_val=d["min"])
        card.widget.set_value(d["value"])

    def _do_gauge_config(self, name: str, d: dict) -> None:
        self._ensure_card(name, _TYPE_GAUGE).widget.configure(
            d["label"], d["min"], d["max"], d["colour"])

    # ── Y-T chart ─────────────────────────────────────────────────────────────

    def plot_yt(self, name: str, value: float) -> None:
        self._emit("yt_point", name, {"value": value})

    def config_yt(self, name: str, label: str = "", colour: str = "#4CAF50",
                  window: float = 60.0) -> None:
        self._emit("yt_config", name, {"label": label, "colour": colour, "window": window})

    def _do_yt_point(self, name: str, d: dict) -> None:
        self._ensure_card(name, _TYPE_YT).widget.add_point(d["value"])

    def _do_yt_config(self, name: str, d: dict) -> None:
        self._ensure_card(name, _TYPE_YT).widget.configure(d["label"], d["colour"], d["window"])

    # ── X-Y chart ─────────────────────────────────────────────────────────────

    def plot_xy(self, name: str, x: float, y: float) -> None:
        self._emit("xy_point", name, {"x": x, "y": y})

    def config_xy(self, name: str, label: str = "", colour: str = "#FF9800") -> None:
        self._emit("xy_config", name, {"label": label, "colour": colour})

    def _do_xy_point(self, name: str, d: dict) -> None:
        self._ensure_card(name, _TYPE_XY).widget.add_point(d["x"], d["y"])

    def _do_xy_config(self, name: str, d: dict) -> None:
        self._ensure_card(name, _TYPE_XY).widget.configure(d["label"], d["colour"])

    # ── text display ──────────────────────────────────────────────────────────

    def show_text(self, name: str, value) -> None:
        self._emit("text_value", name, {"value": value})

    def config_text(self, name: str, label: str = "", colour: str = "#FFFFFF") -> None:
        self._emit("text_config", name, {"label": label, "colour": colour})

    def _do_text_value(self, name: str, d: dict) -> None:
        self._ensure_card(name, _TYPE_TEXT).widget.set_value(d["value"])

    def _do_text_config(self, name: str, d: dict) -> None:
        self._ensure_card(name, _TYPE_TEXT).widget.configure(d["label"], d["colour"])

    # ── clear chart ───────────────────────────────────────────────────────────

    def clear_chart(self, name: str) -> None:
        self._emit("chart_clear", name, {})

    def _do_chart_clear(self, name: str, _d: dict) -> None:
        if name in self._cards:
            w = self._cards[name].widget
            if hasattr(w, "clear"): w.clear()

    # ── button ────────────────────────────────────────────────────────────────

    def config_button(self, name: str, label: str = "Button",
                      colour: str = "#4C97FF") -> None:
        self._emit("button_config", name, {"label": label, "colour": colour})

    def get_button_clicked(self, name: str) -> bool:
        card = self._cards.get(name)
        if card and isinstance(card.widget, _ButtonWidget):
            return card.widget.consume_click()
        return False

    def _do_button_config(self, name: str, d: dict) -> None:
        self._ensure_card(name, _TYPE_BUTTON).widget.configure(d["label"], d["colour"])

    # ── checkbox ──────────────────────────────────────────────────────────────

    def config_checkbox(self, name: str, label: str = "Checkbox",
                        default: bool = False) -> None:
        self._emit("checkbox_config", name, {"label": label, "default": default})

    def get_checkbox(self, name: str) -> bool:
        card = self._cards.get(name)
        if card and isinstance(card.widget, _CheckboxWidget):
            return card.widget.is_checked()
        return False

    def _do_checkbox_config(self, name: str, d: dict) -> None:
        self._ensure_card(name, _TYPE_CHECKBOX).widget.configure(d["label"], d["default"])

    # ── dropdown ──────────────────────────────────────────────────────────────

    def config_dropdown(self, name: str, label: str = "Dropdown",
                        options: list[str] | None = None) -> None:
        self._emit("dropdown_config", name, {"label": label, "options": options or []})

    def get_dropdown(self, name: str) -> str:
        card = self._cards.get(name)
        if card and isinstance(card.widget, _DropdownWidget):
            return card.widget.current_value()
        return ""

    def _do_dropdown_config(self, name: str, d: dict) -> None:
        self._ensure_card(name, _TYPE_DROPDOWN).widget.configure(d["label"], d["options"])

    # ── knob ──────────────────────────────────────────────────────────────────

    def config_knob(self, name: str, label: str = "", min_val: float = 0,
                    max_val: float = 100, step: float = 1,
                    colour: str = "#4C97FF") -> None:
        self._emit("knob_config", name,
                   {"label": label, "min": min_val, "max": max_val,
                    "step": step, "colour": colour})

    def set_knob(self, name: str, value: float) -> None:
        self._emit("knob_set", name, {"value": value})

    def get_knob(self, name: str) -> float:
        card = self._cards.get(name)
        if card and isinstance(card.widget, _KnobWidget):
            return card.widget.get_value()
        return 0.0

    def _do_knob_config(self, name: str, d: dict) -> None:
        self._ensure_card(name, _TYPE_KNOB).widget.configure(
            d["label"], d["min"], d["max"], d["step"], d["colour"])

    def _do_knob_set(self, name: str, d: dict) -> None:
        self._ensure_card(name, _TYPE_KNOB).widget.set_value(d["value"])

    # ── switch ────────────────────────────────────────────────────────────────

    def config_switch(self, name: str, label: str = "Switch",
                      on_label: str = "ON", off_label: str = "OFF",
                      on_colour: str = "#43A047") -> None:
        self._emit("switch_config", name,
                   {"label": label, "on_label": on_label,
                    "off_label": off_label, "on_colour": on_colour})

    def set_switch(self, name: str, state: bool) -> None:
        self._emit("switch_set", name, {"state": bool(state)})

    def get_switch(self, name: str) -> bool:
        card = self._cards.get(name)
        if card and isinstance(card.widget, _SwitchWidget):
            return card.widget.is_on()
        return False

    def _do_switch_config(self, name: str, d: dict) -> None:
        self._ensure_card(name, _TYPE_SWITCH).widget.configure(
            d["label"], d["on_label"], d["off_label"], d["on_colour"])

    def _do_switch_set(self, name: str, d: dict) -> None:
        self._ensure_card(name, _TYPE_SWITCH).widget.set_state(d["state"])

    # ── slider ────────────────────────────────────────────────────────────────

    def config_slider(self, name: str, label: str = "", min_val: float = 0,
                      max_val: float = 100, step: float = 1,
                      colour: str = "#4C97FF") -> None:
        self._emit("slider_config", name,
                   {"label": label, "min": min_val, "max": max_val,
                    "step": step, "colour": colour})

    def set_slider(self, name: str, value: float) -> None:
        self._emit("slider_set", name, {"value": value})

    def get_slider(self, name: str) -> float:
        card = self._cards.get(name)
        if card and isinstance(card.widget, _SliderWidget):
            return card.widget.get_value()
        return 0.0

    def _do_slider_config(self, name: str, d: dict) -> None:
        self._ensure_card(name, _TYPE_SLIDER).widget.configure(
            d["label"], d["min"], d["max"], d["step"], d["colour"])

    def _do_slider_set(self, name: str, d: dict) -> None:
        self._ensure_card(name, _TYPE_SLIDER).widget.set_value(d["value"])
