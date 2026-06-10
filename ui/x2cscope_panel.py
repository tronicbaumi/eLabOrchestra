"""
X2Cscope panel — embedded target variable monitor and control.

Provides:
  • UART configuration + connect / disconnect
  • ELF file loading (resolves variable names and addresses)
  • Variable browser table with live-read values
  • Quick-write widget for modifying target variables
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QGroupBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QAbstractItemView, QSplitter,
    QMessageBox,
)

if TYPE_CHECKING:
    from instruments.x2cscope_driver import X2CScopeDriver

_DARK   = "#1A1A2A"
_CARD   = "#252535"
_ACCENT = "#4C97FF"
_TEXT   = "#FFFFFF"
_DIM    = "#8888AA"
_X2C    = "#FF6F00"   # X2Cscope amber accent


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
        QPushButton:hover {{ background:{color}cc; }}
        QPushButton:disabled {{ background:#444; color:#666; }}
    """)
    return b


def _card_style() -> str:
    return (f"QGroupBox {{"
            f"  color:{_TEXT}; font-weight:bold;"
            f"  border:1px solid #444460; border-radius:6px;"
            f"  margin-top:10px; background:{_CARD};"
            f"}}"
            f"QGroupBox::title {{ subcontrol-origin:margin; left:10px; padding:0 4px; }}")


# ── panel ─────────────────────────────────────────────────────────────────────

class X2CScopePanel(QWidget):
    """
    Left-side instrument panel for X2Cscope embedded variable access.

    Pass the shared X2CScopeDriver instance.
    Emits status_changed(text, colour_hex) on connect/disconnect so the
    main-window status strip can display the live connection state.
    """

    status_changed = Signal(str, str)   # (text, colour_hex)

    def __init__(self, device: "X2CScopeDriver", parent=None) -> None:
        super().__init__(parent)
        self._dev = device
        self._all_var_names: list[str] = []   # all names from ELF
        self._displayed_names: list[str] = [] # after filter

        self.setStyleSheet(f"background:{_DARK};")
        self._build_ui()

        self._was_connected = False

        # refresh watch values and Load-ELF button state every 500 ms
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(500)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        root.addWidget(self._build_elf_group())

        # filter + variable table above, watch panel below
        splitter = QSplitter(Qt.Vertical)
        splitter.setHandleWidth(4)
        splitter.setStyleSheet("QSplitter::handle { background:#333355; }")
        splitter.addWidget(self._build_browser_group())
        splitter.addWidget(self._build_watch_group())
        splitter.setSizes([300, 180])
        root.addWidget(splitter, 1)

        root.addWidget(self._build_write_group())

    def _build_elf_group(self) -> QGroupBox:
        grp = QGroupBox("ELF File")
        grp.setStyleSheet(_card_style())
        lay = QHBoxLayout(grp)
        lay.setContentsMargins(8, 14, 8, 8)
        lay.setSpacing(6)

        self._elf_edit = QLineEdit()
        self._elf_edit.setPlaceholderText("Path to firmware .elf …")
        self._elf_edit.setStyleSheet(_input_style())
        lay.addWidget(self._elf_edit, 1)

        browse_btn = _btn("Browse…", "#546E7A", 72)
        browse_btn.clicked.connect(self._browse_elf)
        lay.addWidget(browse_btn)

        self._btn_load_elf = _btn("Load ELF", _X2C, 80)
        self._btn_load_elf.clicked.connect(self._load_elf)
        self._btn_load_elf.setEnabled(False)
        lay.addWidget(self._btn_load_elf)

        self._elf_lbl = QLabel("")
        self._elf_lbl.setStyleSheet(f"color:{_DIM}; font-size:8pt;")
        lay.addWidget(self._elf_lbl)

        # restore saved elf path
        cfg = self._dev.get_config()
        if cfg.get("elf_path"):
            self._elf_edit.setText(cfg["elf_path"])

        return grp

    def _build_browser_group(self) -> QGroupBox:
        grp = QGroupBox("Variable Browser")
        grp.setStyleSheet(_card_style())
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(8, 14, 8, 8)
        lay.setSpacing(4)

        # filter row
        frow = QHBoxLayout()
        frow.addWidget(_lbl("Filter:"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("type to filter…")
        self._filter_edit.setStyleSheet(_input_style())
        self._filter_edit.textChanged.connect(self._apply_filter)
        frow.addWidget(self._filter_edit, 1)

        self._btn_add_watch = _btn("Add to Watch ↓", _X2C, 110)
        self._btn_add_watch.clicked.connect(self._add_to_watch)
        self._btn_add_watch.setEnabled(False)
        frow.addWidget(self._btn_add_watch)

        lay.addLayout(frow)

        # variable table
        self._var_table = QTableWidget(0, 3)
        self._var_table.setHorizontalHeaderLabels(["Variable", "Type", "Address"])
        self._var_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._var_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._var_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._var_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._var_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._var_table.setStyleSheet(_table_style())
        self._var_table.selectionModel().selectionChanged.connect(
            lambda *_: self._btn_add_watch.setEnabled(
                len(self._var_table.selectedItems()) > 0))
        lay.addWidget(self._var_table, 1)

        return grp

    def _build_watch_group(self) -> QGroupBox:
        grp = QGroupBox("Live Watch")
        grp.setStyleSheet(_card_style())
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(8, 14, 8, 8)
        lay.setSpacing(4)

        hrow = QHBoxLayout()
        hrow.addStretch()
        self._btn_remove_watch = _btn("Remove", "#607D8B", 72)
        self._btn_remove_watch.clicked.connect(self._remove_from_watch)
        hrow.addWidget(self._btn_remove_watch)
        lay.addLayout(hrow)

        self._watch_table = QTableWidget(0, 2)
        self._watch_table.setHorizontalHeaderLabels(["Variable", "Value"])
        self._watch_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._watch_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._watch_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._watch_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._watch_table.setStyleSheet(_table_style())
        lay.addWidget(self._watch_table, 1)

        return grp

    def _build_write_group(self) -> QGroupBox:
        grp = QGroupBox("Quick Write")
        grp.setStyleSheet(_card_style())
        lay = QHBoxLayout(grp)
        lay.setContentsMargins(8, 14, 8, 8)
        lay.setSpacing(6)

        lay.addWidget(_lbl("Variable:"))
        self._write_var_edit = QLineEdit()
        self._write_var_edit.setPlaceholderText("e.g. AppData.u16Speed")
        self._write_var_edit.setStyleSheet(_input_style())
        lay.addWidget(self._write_var_edit, 2)

        lay.addWidget(_lbl("Value:"))
        self._write_val_edit = QLineEdit()
        self._write_val_edit.setPlaceholderText("0")
        self._write_val_edit.setStyleSheet(_input_style())
        lay.addWidget(self._write_val_edit, 1)

        self._btn_write = _btn("Write", _X2C, 60)
        self._btn_write.clicked.connect(self._quick_write)
        lay.addWidget(self._btn_write)

        self._btn_read_one = _btn("Read", "#546E7A", 60)
        self._btn_read_one.clicked.connect(self._quick_read)
        lay.addWidget(self._btn_read_one)

        return grp

    # ── timer tick ────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        """Called every 500 ms: sync Load-ELF button and refresh watch values.
        Also auto-loads ELF when connection is first established."""
        connected = self._dev.connected
        self._btn_load_elf.setEnabled(connected)

        if connected and not self._was_connected:
            # just became connected — try auto-loading saved ELF path
            elf_path = self._elf_edit.text().strip()
            if elf_path and Path(elf_path).is_file():
                self._load_elf()
        self._was_connected = connected

        if connected:
            self._refresh_watch()

    # ── ELF actions ───────────────────────────────────────────────────────────

    def _browse_elf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open ELF File", str(Path.home()),
            "ELF Files (*.elf *.axf *.out);;All Files (*)")
        if path:
            self._elf_edit.setText(path)

    def _load_elf(self) -> None:
        path = self._elf_edit.text().strip()
        if not path:
            return
        ok, result = self._dev.load_elf(path)
        if ok:
            count = result
            self._elf_lbl.setText(f"✓ {count} variables")
            self._elf_lbl.setStyleSheet("color:#81C784; font-size:8pt;")
            self._populate_var_table()
        else:
            self._elf_lbl.setText(f"✗ {result}")
            self._elf_lbl.setStyleSheet("color:#EF9A9A; font-size:8pt;")

    # ── variable browser ──────────────────────────────────────────────────────

    def _populate_var_table(self) -> None:
        self._all_var_names = self._dev.variable_names
        self._apply_filter(self._filter_edit.text())

    def _apply_filter(self, text: str) -> None:
        txt = text.lower()
        self._displayed_names = [
            n for n in self._all_var_names if txt in n.lower()
        ] if txt else list(self._all_var_names)

        self._var_table.setRowCount(0)
        self._var_table.setRowCount(len(self._displayed_names))
        for row, name in enumerate(self._displayed_names):
            info = self._dev.get_var_info(name)
            self._var_table.setItem(row, 0, _cell(name))
            self._var_table.setItem(row, 1, _cell(info.data_type if info else ""))
            addr = f"0x{info.address:08X}" if info else ""
            self._var_table.setItem(row, 2, _cell(addr))

    def _add_to_watch(self) -> None:
        for idx in self._var_table.selectionModel().selectedRows():
            name = self._displayed_names[idx.row()]
            # avoid duplicates
            existing = [
                self._watch_table.item(r, 0).text()
                for r in range(self._watch_table.rowCount())
            ]
            if name not in existing:
                r = self._watch_table.rowCount()
                self._watch_table.insertRow(r)
                self._watch_table.setItem(r, 0, _cell(name))
                self._watch_table.setItem(r, 1, _cell("—"))
        self._sync_poll_variables()

    def _remove_from_watch(self) -> None:
        rows = sorted(
            {idx.row() for idx in self._watch_table.selectionModel().selectedRows()},
            reverse=True)
        for r in rows:
            self._watch_table.removeRow(r)
        self._sync_poll_variables()

    def _sync_poll_variables(self) -> None:
        """Tell the driver poll thread which variables to keep fresh."""
        names = [self._watch_table.item(r, 0).text()
                 for r in range(self._watch_table.rowCount())]
        self._dev.set_poll_variables(names)

    # ── live watch refresh ────────────────────────────────────────────────────

    def _refresh_watch(self) -> None:
        for r in range(self._watch_table.rowCount()):
            name = self._watch_table.item(r, 0).text()
            # cached value — target I/O happens on the driver poll thread
            val  = self._dev.get_cached_value(name)
            display = "—" if val is None else f"{val}"
            item = self._watch_table.item(r, 1)
            if item is None:
                self._watch_table.setItem(r, 1, _cell(display))
            else:
                item.setText(display)

    # ── quick write / read ────────────────────────────────────────────────────

    def _quick_write(self) -> None:
        name = self._write_var_edit.text().strip()
        val_str = self._write_val_edit.text().strip()
        if not name:
            return
        try:
            value = float(val_str)
        except ValueError:
            QMessageBox.warning(self, "Invalid Value", f"Not a number: {val_str!r}")
            return
        ok = self._dev.write_variable(name, value)
        if not ok:
            QMessageBox.warning(self, "Write Failed",
                f"Could not write '{name}'. Check that the ELF is loaded and "
                "the variable name matches exactly.")

    def _quick_read(self) -> None:
        name = self._write_var_edit.text().strip()
        if not name:
            return
        val = self._dev.read_variable(name)
        if val is None:
            self._write_val_edit.setText("—")
        else:
            self._write_val_edit.setText(f"{val}")


# ── style helpers ─────────────────────────────────────────────────────────────

def _lbl(text: str) -> QLabel:
    l = QLabel(text)
    l.setStyleSheet(f"color:{_DIM}; font-size:9pt;")
    return l


def _input_style() -> str:
    return (f"QLineEdit {{ background:#333350; color:{_TEXT}; border:1px solid #555580;"
            f" border-radius:4px; padding:2px 6px; font-size:9pt; }}")

def _table_style() -> str:
    return (f"QTableWidget {{ background:{_CARD}; color:{_TEXT}; gridline-color:#333;"
            f" border:none; font-size:9pt; }}"
            f"QHeaderView::section {{ background:#1E1E30; color:{_DIM};"
            f" border:1px solid #333; padding:4px; font-size:8pt; }}"
            f"QTableWidget::item:selected {{ background:{_ACCENT}; }}")

def _cell(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(str(text))
    item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
    return item
