"""
Visual Scratch-like block canvas using PySide6 QGraphicsView.

Left panel: palette of available blocks (by category).
Right panel: infinite canvas where blocks can be dragged and snapped.
"""

from __future__ import annotations

import json
import math
from typing import Optional

from PySide6.QtCore import (
    Qt, QPointF, QRectF, QSizeF, Signal, QObject, QMimeData, QByteArray,
)
from PySide6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QFontMetrics,
    QPainterPath, QDrag, QPixmap,
)
from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsItem, QGraphicsObject,
    QWidget, QHBoxLayout, QVBoxLayout, QScrollArea, QLabel, QPushButton,
    QFrame, QSizePolicy, QComboBox, QLineEdit, QApplication, QToolBar,
    QGraphicsProxyWidget,
)

from .blocks import (
    BlockDef, BlockInstance, BlockProgram, BlockType, BlockCategory,
    BLOCK_LIBRARY, BLOCK_BY_ID, CATEGORY_COLORS, FieldDef,
)

_BLOCK_W = 220
_BLOCK_H = 44
_CORNER  = 8
_NOTCH_W = 20
_NOTCH_H = 8
_SHADOW  = 3
_FONT    = QFont("Segoe UI", 9, QFont.Bold)
_FONT_SM = QFont("Segoe UI", 8)


def _hex(color: str) -> QColor:
    return QColor(color)


def _darker(color: str, factor: int = 130) -> QColor:
    return QColor(color).darker(factor)


# ── Block item ───────────────────────────────────────────────────────────────

class BlockItem(QGraphicsObject):
    moved = Signal()
    dropped_on = Signal(object)  # other BlockItem

    def __init__(self, instance: BlockInstance, scene_canvas: "ScratchCanvas") -> None:
        super().__init__()
        self.instance = instance
        self.canvas   = scene_canvas
        self._drag_start: Optional[QPointF] = None
        self._field_widgets: list[QGraphicsProxyWidget] = []

        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setPos(instance.x, instance.y)

        self._build_field_widgets()

    @property
    def defn(self) -> BlockDef:
        return self.instance.definition

    def _total_height(self) -> float:
        h = _BLOCK_H
        if self.defn.fields:
            h += 24
        if self.defn.type == BlockType.CONTROL:
            h += 40  # C-body
        return h

    def boundingRect(self) -> QRectF:
        w = _BLOCK_W
        h = self._total_height()
        return QRectF(-2, -2, w + 4 + _SHADOW, h + 4 + _SHADOW)

    def _shape_path(self) -> QPainterPath:
        w = _BLOCK_W
        h = self._total_height()
        r = _CORNER
        p = QPainterPath()

        is_hat      = self.defn.type == BlockType.HAT
        is_reporter = self.defn.type == BlockType.REPORTER
        is_control  = self.defn.type == BlockType.CONTROL
        has_top     = self.defn.type in (BlockType.COMMAND, BlockType.CONTROL)
        has_bottom  = self.defn.type in (BlockType.COMMAND, BlockType.CONTROL, BlockType.HAT)

        if is_reporter:
            # pill shape
            p.addRoundedRect(0, 0, w, h, h / 2, h / 2)
            return p

        if is_hat:
            # Rounded top arc
            p.moveTo(r, 0)
            p.arcTo(0, 0, r * 2, r * 2, 90, 90)
            p.lineTo(0, h - r)
            p.arcTo(0, h - r * 2, r * 2, r * 2, 180, 90)
            # bottom notch
            p.lineTo(_NOTCH_W, h)
            p.lineTo(_NOTCH_W + _NOTCH_W * 0.4, h + _NOTCH_H)
            p.lineTo(_NOTCH_W * 2, h + _NOTCH_H)
            p.lineTo(_NOTCH_W * 2 + _NOTCH_W * 0.6, h)
            p.lineTo(w - r, h)
            p.arcTo(w - r * 2, h - r * 2, r * 2, r * 2, 270, 90)
            p.lineTo(w, r)
            p.arcTo(w - r * 2, 0, r * 2, r * 2, 0, 90)
            p.closeSubpath()
            return p

        # COMMAND / CONTROL
        p.moveTo(r, 0)
        if has_top:
            # top notch (male puzzle)
            p.lineTo(_NOTCH_W, 0)
            p.lineTo(_NOTCH_W + _NOTCH_W * 0.4, -_NOTCH_H)
            p.lineTo(_NOTCH_W * 2, -_NOTCH_H)
            p.lineTo(_NOTCH_W * 2 + _NOTCH_W * 0.6, 0)
        p.lineTo(w - r, 0)
        p.arcTo(w - r * 2, 0, r * 2, r * 2, 90, -90)
        p.lineTo(w, h - r)
        p.arcTo(w - r * 2, h - r * 2, r * 2, r * 2, 0, -90)
        if has_bottom:
            p.lineTo(_NOTCH_W * 2 + _NOTCH_W * 0.6, h)
            p.lineTo(_NOTCH_W * 2, h + _NOTCH_H)
            p.lineTo(_NOTCH_W + _NOTCH_W * 0.4, h + _NOTCH_H)
            p.lineTo(_NOTCH_W, h)
        p.lineTo(r, h)
        p.arcTo(0, h - r * 2, r * 2, r * 2, 270, -90)
        p.lineTo(0, r)
        p.arcTo(0, 0, r * 2, r * 2, 180, -90)
        p.closeSubpath()
        return p

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing)
        path = self._shape_path()

        # shadow
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 40))
        painter.translate(_SHADOW, _SHADOW)
        painter.drawPath(path)
        painter.translate(-_SHADOW, -_SHADOW)

        # fill
        color = _hex(self.defn.color)
        painter.setBrush(QBrush(color))
        painter.setPen(QPen(_darker(self.defn.color, 150), 1.5))
        painter.drawPath(path)

        # label
        painter.setPen(Qt.white)
        painter.setFont(_FONT)
        h = min(_BLOCK_H, self._total_height())
        painter.drawText(QRectF(10, 0, _BLOCK_W - 20, h), Qt.AlignVCenter | Qt.AlignLeft,
                         self._short_label())

        # selected highlight
        if self.isSelected():
            painter.setPen(QPen(QColor(255, 255, 255, 180), 2, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)

    def _short_label(self) -> str:
        label = self.defn.label
        for fd in self.defn.fields:
            placeholder = f"[{fd.name}]"
            if placeholder in label:
                label = label.replace(placeholder, "")
        return label.strip()

    def _build_field_widgets(self) -> None:
        if not self.defn.fields:
            return
        x = 10.0
        y = float(_BLOCK_H + 2)
        for fd in self.defn.fields:
            if fd.type == "select":
                w = QComboBox()
                w.addItems(fd.options)
                val = self.instance.field_values.get(fd.name, fd.default)
                idx = w.findText(str(val))
                if idx >= 0:
                    w.setCurrentIndex(idx)
                w.setFixedWidth(100)
                w.setFixedHeight(20)
                w.setStyleSheet("font-size:8pt; background:#fff; color:#333;")
                w.currentTextChanged.connect(
                    lambda text, name=fd.name: self._on_field_change(name, text))
                proxy = self.scene().addWidget(w) if self.scene() else QGraphicsProxyWidget(self)
                proxy.setParentItem(self)
                proxy.setPos(x, y)
                self._field_widgets.append(proxy)
                x += 108
            else:
                le = QLineEdit(str(self.instance.field_values.get(fd.name, fd.default)))
                le.setFixedWidth(80)
                le.setFixedHeight(20)
                le.setStyleSheet("font-size:8pt; background:#fff; color:#333; border-radius:3px;")
                le.textChanged.connect(
                    lambda text, name=fd.name: self._on_field_change(name, text))
                proxy = QGraphicsProxyWidget(self)
                proxy.setWidget(le)
                proxy.setPos(x, y)
                self._field_widgets.append(proxy)
                x += 88

    def _on_field_change(self, name: str, value: str) -> None:
        self.instance.field_values[name] = value
        self.canvas.program_changed.emit()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.instance.x = self.pos().x()
            self.instance.y = self.pos().y()
            self.moved.emit()
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self.canvas.program_changed.emit()
        self._try_snap()

    def _try_snap(self) -> None:
        """Snap this block below the nearest compatible block."""
        my_top = self.scenePos()
        best_dist = 30.0
        best = None
        for item in self.scene().items():
            if item is self or not isinstance(item, BlockItem):
                continue
            snap_pt = item.scenePos() + QPointF(0, item._total_height())
            dx = my_top.x() - snap_pt.x()
            dy = my_top.y() - snap_pt.y()
            d = math.hypot(dx, dy)
            if d < best_dist:
                best_dist = d
                best = (item, snap_pt)

        if best:
            other, pt = best
            self.setPos(pt + QPointF(0, _NOTCH_H))
            self.instance.x = self.pos().x()
            self.instance.y = self.pos().y()
            # link in program model
            other.instance.next = self.instance
            self.canvas.program_changed.emit()


# ── Palette entry ─────────────────────────────────────────────────────────────

class PaletteBlock(QLabel):
    def __init__(self, defn: BlockDef, canvas: "ScratchCanvas") -> None:
        super().__init__(defn.label.replace("[", "").replace("]", "").strip())
        self.defn   = defn
        self.canvas = canvas
        color = defn.color
        self.setStyleSheet(f"""
            QLabel {{
                background-color: {color};
                color: white;
                border-radius: 6px;
                padding: 4px 8px;
                font-weight: bold;
                font-size: 9pt;
            }}
            QLabel:hover {{
                background-color: {QColor(color).lighter(120).name()};
            }}
        """)
        self.setFixedHeight(28)
        self.setCursor(Qt.OpenHandCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            drag = QDrag(self)
            mime = QMimeData()
            mime.setText(self.defn.id)
            drag.setMimeData(mime)
            pix = QPixmap(self.size())
            self.render(pix)
            drag.setPixmap(pix)
            drag.setHotSpot(event.pos())
            drag.exec(Qt.CopyAction)


# ── Canvas scene ──────────────────────────────────────────────────────────────

class BlockScene(QGraphicsScene):
    def __init__(self, canvas: "ScratchCanvas") -> None:
        super().__init__()
        self.canvas = canvas
        self.setBackgroundBrush(QBrush(QColor("#F9F9F9")))

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        block_id = event.mimeData().text()
        if block_id in BLOCK_BY_ID:
            pos = event.scenePos()
            inst = BlockInstance(block_id=block_id, x=pos.x(), y=pos.y())
            # set field defaults
            for fd in BLOCK_BY_ID[block_id].fields:
                inst.field_values[fd.name] = fd.default
            self.canvas.add_block(inst)
            event.acceptProposedAction()


class BlockView(QGraphicsView):
    def __init__(self, scene: BlockScene) -> None:
        super().__init__(scene)
        self.setAcceptDrops(True)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setSceneRect(-2000, -2000, 6000, 6000)
        self.setStyleSheet("background: #F9F9F9; border: none;")
        self._zoom = 1.0

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.1 if event.angleDelta().y() > 0 else 0.9
            self._zoom = max(0.3, min(3.0, self._zoom * factor))
            self.resetTransform()
            self.scale(self._zoom, self._zoom)
        else:
            super().wheelEvent(event)


# ── Main ScratchCanvas widget ─────────────────────────────────────────────────

class ScratchCanvas(QWidget):
    program_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._program = BlockProgram()
        self._items: dict[str, BlockItem] = {}  # uid -> BlockItem

        self._scene = BlockScene(self)
        self._view  = BlockView(self._scene)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── palette ──
        palette_panel = self._build_palette()
        layout.addWidget(palette_panel)

        # ── canvas area ──
        canvas_frame = QWidget()
        canvas_frame.setStyleSheet("background: #F0F0F0;")
        vl = QVBoxLayout(canvas_frame)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        toolbar = self._build_toolbar()
        vl.addWidget(toolbar)
        vl.addWidget(self._view)

        layout.addWidget(canvas_frame, 1)

    # ── palette ──────────────────────────────────────────────────────────────

    def _build_palette(self) -> QWidget:
        panel = QFrame()
        panel.setFixedWidth(200)
        panel.setStyleSheet("background: #2E2E3E; border-right: 1px solid #1A1A2A;")

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        title = QLabel("Blocks")
        title.setStyleSheet("color: #AAB; font-weight: bold; font-size: 11pt;")
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(2)

        # Group by category
        from itertools import groupby
        cats = list(BlockCategory)
        for cat in cats:
            blocks_in_cat = [b for b in BLOCK_LIBRARY if b.category == cat]
            if not blocks_in_cat:
                continue
            cat_label = QLabel(cat.name.replace("_", " ").title())
            cat_label.setStyleSheet(f"""
                color: {CATEGORY_COLORS[cat]};
                font-weight: bold;
                font-size: 8pt;
                margin-top: 6px;
            """)
            inner_layout.addWidget(cat_label)
            for defn in blocks_in_cat:
                pb = PaletteBlock(defn, self)
                inner_layout.addWidget(pb)

        inner_layout.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(scroll)
        return panel

    def _build_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setFixedHeight(38)
        bar.setStyleSheet("background: #2E2E3E; border-bottom: 1px solid #1A1A2A;")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        def btn(label: str, color: str) -> QPushButton:
            b = QPushButton(label)
            b.setFixedHeight(28)
            b.setStyleSheet(f"""
                QPushButton {{
                    background: {color}; color: white;
                    border-radius: 4px; font-weight: bold; padding: 0 10px;
                }}
                QPushButton:hover {{ background: {QColor(color).lighter(120).name()}; }}
            """)
            return b

        self._btn_run   = btn("▶  Run",    "#4CAF50")
        self._btn_stop  = btn("■  Stop",   "#f44336")
        self._btn_clear = btn("Clear",     "#607D8B")
        layout.addWidget(self._btn_run)
        layout.addWidget(self._btn_stop)
        layout.addStretch()
        layout.addWidget(self._btn_clear)

        self._btn_clear.clicked.connect(self.clear_canvas)
        return bar

    @property
    def run_button(self) -> QPushButton:
        return self._btn_run

    @property
    def stop_button(self) -> QPushButton:
        return self._btn_stop

    # ── program management ────────────────────────────────────────────────────

    def add_block(self, inst: BlockInstance) -> BlockItem:
        # remove orphaned next references that may conflict
        self._program.stacks.append(inst)
        item = BlockItem(inst, self)
        self._scene.addItem(item)
        self._items[inst.uid] = item
        self.program_changed.emit()
        return item

    def clear_canvas(self) -> None:
        self._scene.clear()
        self._items.clear()
        self._program = BlockProgram()
        self.program_changed.emit()

    @property
    def program(self) -> BlockProgram:
        return self._program

    def get_program_dict(self) -> dict:
        # Rebuild stacks from scene items (position-sorted)
        stacks = []
        for item in self._scene.items():
            if isinstance(item, BlockItem):
                item.instance.x = item.pos().x()
                item.instance.y = item.pos().y()
                defn = item.instance.definition
                if defn.type == BlockType.HAT:
                    stacks.append(item.instance)
        self._program.stacks = stacks
        return self._program.to_dict()

    def load_program_dict(self, d: dict) -> None:
        self.clear_canvas()
        prog = BlockProgram.from_dict(d)
        # flat import: add all instances
        def _add_recursive(inst: BlockInstance) -> None:
            self._program.stacks.append(inst)
            item = BlockItem(inst, self)
            self._scene.addItem(item)
            self._items[inst.uid] = item
            # next blocks
            cur = inst.next
            while cur:
                nitem = BlockItem(cur, self)
                self._scene.addItem(nitem)
                self._items[cur.uid] = nitem
                cur = cur.next
            for child in inst.children:
                _add_recursive(child)

        for stack in prog.stacks:
            _add_recursive(stack)
        self.program_changed.emit()
