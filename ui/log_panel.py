"""
Multi-instrument CSV data logging panel.

All four instruments are supported.  A tree on the left lets the user
select exactly which fields to log; the table on the right shows the
live data.  Logging interval is configurable.

Performance note
────────────────
All instruments are read from their drivers' thread-safe cached snapshots
(get_state() / last_measurement()) — no serial round-trips on the GUI
thread.  The drivers' background poll threads (500 ms) keep the caches
fresh.
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QFileDialog, QLineEdit,
    QHeaderView, QFrame, QSplitter, QTreeWidget, QTreeWidgetItem,
    QAbstractItemView,
)

from instruments import (
    HantekRLC1733C, OwonSP3103, LMG450, MagtrolDSP7000, Array3721A,
)

_DARK_BG = "#1A1A2A"
_CARD_BG = "#252535"
_TEXT    = "#FFFFFF"
_DIM     = "#8888AA"
_ACCENT  = "#4C97FF"

_TREE_STYLE = f"""
QTreeWidget {{
    background: {_CARD_BG}; color: {_TEXT};
    border: none; font-size: 9pt;
    outline: none;
}}
QTreeWidget::item {{ padding: 2px 4px; }}
QTreeWidget::item:selected {{ background: {_ACCENT}; }}
QTreeWidget::item:hover   {{ background: #333358; }}
QHeaderView::section      {{ background: #1E1E30; color: {_DIM}; border: 1px solid #333; padding: 4px; }}
"""

_TABLE_STYLE = f"""
QTableWidget {{
    background: {_CARD_BG}; color: {_TEXT}; gridline-color: #333;
    border: none; font-size: 9pt;
}}
QHeaderView::section {{
    background: #1E1E30; color: {_DIM}; border: 1px solid #333; padding: 4px;
    font-size: 8pt;
}}
QTableWidget::item:selected {{ background: {_ACCENT}; }}
"""


# ── field definitions ──────────────────────────────────────────────────────────

@dataclass
class FieldDef:
    group:   str                        # tree parent label
    key:     str                        # unique key → CSV column name
    label:   str                        # display / header label
    unit:    str                        # shown in header  (may be "")
    default: bool                       # checked on startup
    fetch:   Callable[[dict], Any]      # snap → value


def _lcr(attr):
    return lambda s: getattr(s["lcr"], attr) if s.get("lcr") else ""

def _psu(attr):
    return lambda s: getattr(s["psu"], attr) if s.get("psu") else ""

def _lmg(ch, attr):
    key = f"lmg_ch{ch}"
    return lambda s: getattr(s[key], attr) if s.get(key) else ""

def _agg(attr):
    return lambda s: getattr(s["lmg_agg"], attr) if s.get("lmg_agg") else ""

def _dsp(ch, attr):
    key = f"dsp_ch{ch}"
    return lambda s: getattr(s[key], attr) if s.get(key) else ""

def _eload(attr):
    return lambda s: getattr(s["eload"], attr) if s.get("eload") else ""


FIELDS: list[FieldDef] = [
    # ── LCR Meter ──────────────────────────────────────────────────────────────
    FieldDef("LCR Meter (Hantek 1733C)", "lcr_primary",   "Primary",    "",      True,  _lcr("primary")),
    FieldDef("LCR Meter (Hantek 1733C)", "lcr_secondary", "Secondary",  "",      True,  _lcr("secondary")),
    FieldDef("LCR Meter (Hantek 1733C)", "lcr_mode",      "Mode",       "",      False, lambda s: s["lcr"].mode_label if s.get("lcr") else ""),
    FieldDef("LCR Meter (Hantek 1733C)", "lcr_frequency", "Frequency",  "",      False, lambda s: s["lcr"].frequency.label if s.get("lcr") else ""),
    FieldDef("LCR Meter (Hantek 1733C)", "lcr_overload",  "Overload",   "",      False, lambda s: int(s["lcr"].overload) if s.get("lcr") else ""),

    # ── Power Supply ───────────────────────────────────────────────────────────
    FieldDef("Power Supply (OWON SP3103)", "psu_vmeas",  "Voltage",       "V",   True,  _psu("meas_voltage")),
    FieldDef("Power Supply (OWON SP3103)", "psu_imeas",  "Current",       "A",   True,  _psu("meas_current")),
    FieldDef("Power Supply (OWON SP3103)", "psu_power",  "Power",         "W",   True,  _psu("power")),
    FieldDef("Power Supply (OWON SP3103)", "psu_vset",   "V set",         "V",   False, _psu("set_voltage")),
    FieldDef("Power Supply (OWON SP3103)", "psu_iset",   "I limit",       "A",   False, _psu("set_current")),
    FieldDef("Power Supply (OWON SP3103)", "psu_output", "Output on",     "",    False, lambda s: int(s["psu"].output_on) if s.get("psu") else ""),

    # ── LMG450 channel 1-4 ────────────────────────────────────────────────────
    *[
        field
        for ch in range(1, 5)
        for field in [
            FieldDef(f"LMG450 — Ch {ch}", f"lmg{ch}_urms",  "Vrms",    "V",    ch == 1, _lmg(ch, "urms")),
            FieldDef(f"LMG450 — Ch {ch}", f"lmg{ch}_irms",  "Arms",    "A",    ch == 1, _lmg(ch, "irms")),
            FieldDef(f"LMG450 — Ch {ch}", f"lmg{ch}_p",     "P",       "W",    ch == 1, _lmg(ch, "p")),
            FieldDef(f"LMG450 — Ch {ch}", f"lmg{ch}_q",     "Q",       "VAr",  False,   _lmg(ch, "q")),
            FieldDef(f"LMG450 — Ch {ch}", f"lmg{ch}_s",     "S",       "VA",   False,   _lmg(ch, "s")),
            FieldDef(f"LMG450 — Ch {ch}", f"lmg{ch}_lamda", "PF λ",    "",     ch == 1, _lmg(ch, "lamda")),
            FieldDef(f"LMG450 — Ch {ch}", f"lmg{ch}_phi",   "Phase",   "°",    False,   _lmg(ch, "phi")),
            FieldDef(f"LMG450 — Ch {ch}", f"lmg{ch}_fu",    "Freq",    "Hz",   False,   _lmg(ch, "fu")),
            FieldDef(f"LMG450 — Ch {ch}", f"lmg{ch}_ubdc",  "VDC",     "V",    False,   _lmg(ch, "ubdc")),
            FieldDef(f"LMG450 — Ch {ch}", f"lmg{ch}_ibdc",  "IDC",     "A",    False,   _lmg(ch, "ibdc")),
            FieldDef(f"LMG450 — Ch {ch}", f"lmg{ch}_wh",    "Energy",  "Wh",   False,   _lmg(ch, "wh")),
            FieldDef(f"LMG450 — Ch {ch}", f"lmg{ch}_ah",    "Charge",  "Ah",   False,   _lmg(ch, "ah")),
            FieldDef(f"LMG450 — Ch {ch}", f"lmg{ch}_uthd",  "THD-V",   "%",    False,   _lmg(ch, "uthd")),
            FieldDef(f"LMG450 — Ch {ch}", f"lmg{ch}_ithd",  "THD-I",   "%",    False,   _lmg(ch, "ithd")),
        ]
    ],

    # ── LMG450 aggregate ──────────────────────────────────────────────────────
    FieldDef("LMG450 — Aggregate", "lmg_psum",   "P sum",      "W",   False, _agg("psum")),
    FieldDef("LMG450 — Aggregate", "lmg_qsum",   "Q sum",      "VAr", False, _agg("qsum")),
    FieldDef("LMG450 — Aggregate", "lmg_ssum",   "S sum",      "VA",  False, _agg("ssum")),
    FieldDef("LMG450 — Aggregate", "lmg_wpsum",  "Energy sum", "Wh",  False, _agg("wpsum")),
    FieldDef("LMG450 — Aggregate", "lmg_ahpsum", "Charge sum", "Ah",  False, _agg("ahpsum")),

    # ── DSP7000 channels 1-2 ──────────────────────────────────────────────────
    *[
        field
        for ch in (1, 2)
        for field in [
            FieldDef(f"DSP7000 — Ch {ch}", f"dsp{ch}_speed",     "Speed",     "rpm", True,  _dsp(ch, "speed")),
            FieldDef(f"DSP7000 — Ch {ch}", f"dsp{ch}_torque",    "Torque",    "Nm",  True,  _dsp(ch, "torque")),
            FieldDef(f"DSP7000 — Ch {ch}", f"dsp{ch}_power",     "Power",     "W",   True,  _dsp(ch, "power")),
            FieldDef(f"DSP7000 — Ch {ch}", f"dsp{ch}_direction", "Direction", "",    False, _dsp(ch, "direction")),
        ]
    ],

    # ── Array 3721A Electronic Load ───────────────────────────────────────────
    FieldDef("E-Load (Array 3721A)", "eload_vmeas",    "Voltage",    "V",  True,  _eload("meas_voltage")),
    FieldDef("E-Load (Array 3721A)", "eload_imeas",    "Current",    "A",  True,  _eload("meas_current")),
    FieldDef("E-Load (Array 3721A)", "eload_power",    "Power",      "W",  True,  lambda s: s["eload"].power if s.get("eload") else ""),
    FieldDef("E-Load (Array 3721A)", "eload_mode",     "Mode",       "",   False, _eload("mode")),
    FieldDef("E-Load (Array 3721A)", "eload_setval",   "Set Level",  "",   False, _eload("set_value")),
    FieldDef("E-Load (Array 3721A)", "eload_input",    "Input on",   "",   False, lambda s: int(s["eload"].input_on) if s.get("eload") else ""),
]

# pre-build lookup by key
_FIELD_MAP: dict[str, FieldDef] = {f.key: f for f in FIELDS}


# ── panel ──────────────────────────────────────────────────────────────────────

class LogPanel(QWidget):
    """
    Multi-instrument data logging panel.

    Pass all four instrument driver instances.  The old single-device
    constructor still works for backwards compatibility.
    """

    def __init__(self,
                 lcr: HantekRLC1733C,
                 psu: OwonSP3103 | None = None,
                 lmg: LMG450 | None = None,
                 dsp: MagtrolDSP7000 | None = None,
                 eload: Array3721A | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._lcr   = lcr
        self._psu   = psu
        self._lmg   = lmg
        self._dsp   = dsp
        self._eload = eload

        self._log_file: Path | None = None
        self._writer:   csv.DictWriter | None = None
        self._fh        = None
        self._rows:     list[dict] = []
        self._active_keys: list[str] = []   # ordered list of checked field keys
        self._flush_counter: int = 0
        self._TABLE_MAX_ROWS = 2000

        self.setStyleSheet(f"background: {_DARK_BG};")
        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        # ── toolbar ──────────────────────────────────────────────────────────
        bar = QFrame()
        bar.setStyleSheet(f"background: {_CARD_BG}; border-radius: 6px;")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(8, 5, 8, 5)
        bl.setSpacing(6)

        self._lbl_file = QLabel("No file selected")
        self._lbl_file.setStyleSheet(f"color: {_DIM}; font-size: 9pt;")
        bl.addWidget(self._lbl_file, 1)

        self._btn_choose = _btn("📁  File…",     "#607D8B")
        self._btn_start  = _btn("▶  Start Log",  "#4CAF50")
        self._btn_stop   = _btn("■  Stop Log",   "#f44336")
        self._btn_clear  = _btn("Clear",          "#455A64")
        self._btn_stop.setEnabled(False)

        for b in (self._btn_choose, self._btn_start, self._btn_stop, self._btn_clear):
            bl.addWidget(b)
        root.addWidget(bar)

        # ── interval / row count ──────────────────────────────────────────────
        irow = QHBoxLayout()
        lbl_i = QLabel("Interval (s):")
        lbl_i.setStyleSheet(f"color: {_DIM}; font-size: 9pt;")
        irow.addWidget(lbl_i)
        self._interval_edit = QLineEdit("1.0")
        self._interval_edit.setFixedWidth(55)
        self._interval_edit.setStyleSheet(
            f"background:#333350; color:{_TEXT}; border:1px solid #555; border-radius:3px; padding:2px 4px;")
        irow.addWidget(self._interval_edit)
        irow.addStretch()
        self._lbl_count = QLabel("0 rows")
        self._lbl_count.setStyleSheet(f"color:{_DIM}; font-size:9pt;")
        irow.addWidget(self._lbl_count)
        root.addLayout(irow)

        # ── splitter: field tree | log table ─────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(4)
        splitter.setStyleSheet("QSplitter::handle { background: #333355; }")

        # field selector tree
        self._tree = QTreeWidget()
        self._tree.setHeaderLabel("Logged fields")
        self._tree.setStyleSheet(_TREE_STYLE)
        self._tree.setMinimumWidth(220)
        self._tree.setMaximumWidth(340)
        self._build_tree()
        self._tree.itemChanged.connect(self._on_tree_item_changed)
        splitter.addWidget(self._tree)

        # log table
        self._table = QTableWidget(0, 1)
        self._table.setHorizontalHeaderLabels(["Timestamp"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setStyleSheet(_TABLE_STYLE)
        self._table.setAlternatingRowColors(True)
        splitter.addWidget(self._table)

        splitter.setSizes([240, 800])
        root.addWidget(splitter, 1)

        # ── wiring ────────────────────────────────────────────────────────────
        self._btn_choose.clicked.connect(self._choose_file)
        self._btn_start.clicked.connect(self._start_log)
        self._btn_stop.clicked.connect(self._stop_log)
        self._btn_clear.clicked.connect(self._clear)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._log_row)

        # initialise column set from defaults
        self._refresh_table_columns()

    def _build_tree(self) -> None:
        """Populate the field selector tree from FIELDS."""
        self._tree.blockSignals(True)
        groups: dict[str, QTreeWidgetItem] = {}

        for f in FIELDS:
            if f.group not in groups:
                parent = QTreeWidgetItem(self._tree, [f.group])
                parent.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                parent.setCheckState(0, Qt.Unchecked)
                parent.setExpanded(False)
                groups[f.group] = parent
            else:
                parent = groups[f.group]

            hdr = f"{f.label} ({f.unit})" if f.unit else f.label
            child = QTreeWidgetItem(parent, [hdr])
            child.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            child.setCheckState(0, Qt.Checked if f.default else Qt.Unchecked)
            child.setData(0, Qt.UserRole, f.key)

        # set parent check states
        for parent in groups.values():
            self._update_parent_check(parent)

        self._tree.blockSignals(False)

    def _update_parent_check(self, parent: QTreeWidgetItem) -> None:
        n = parent.childCount()
        checked = sum(
            1 for i in range(n) if parent.child(i).checkState(0) == Qt.Checked
        )
        if checked == 0:
            parent.setCheckState(0, Qt.Unchecked)
        elif checked == n:
            parent.setCheckState(0, Qt.Checked)
        else:
            parent.setCheckState(0, Qt.PartiallyChecked)

    def _on_tree_item_changed(self, item: QTreeWidgetItem, col: int) -> None:
        self._tree.blockSignals(True)
        if item.parent() is None:
            # top-level group — propagate to children
            state = item.checkState(0)
            if state != Qt.PartiallyChecked:
                for i in range(item.childCount()):
                    item.child(i).setCheckState(0, state)
        else:
            self._update_parent_check(item.parent())
        self._tree.blockSignals(False)
        self._refresh_table_columns()

    def _checked_keys(self) -> list[str]:
        """Return field keys for all checked leaves, in definition order."""
        checked = set()
        root = self._tree.invisibleRootItem()
        for gi in range(root.childCount()):
            group = root.child(gi)
            for ci in range(group.childCount()):
                child = group.child(ci)
                if child.checkState(0) == Qt.Checked:
                    checked.add(child.data(0, Qt.UserRole))
        return [f.key for f in FIELDS if f.key in checked]

    def _refresh_table_columns(self) -> None:
        """Rebuild table columns to match currently checked fields."""
        self._active_keys = self._checked_keys()
        headers = ["Timestamp"] + [
            (f"{_FIELD_MAP[k].label} ({_FIELD_MAP[k].unit})"
             if _FIELD_MAP[k].unit else _FIELD_MAP[k].label)
            for k in self._active_keys
        ]
        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)

    # ── logging control ───────────────────────────────────────────────────────

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
        self._active_keys = self._checked_keys()
        self._refresh_table_columns()
        interval_ms = int(float(self._interval_edit.text() or 1.0) * 1000)

        fieldnames = ["timestamp"] + self._active_keys
        self._fh = open(self._log_file, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=fieldnames)
        self._writer.writeheader()

        self._timer.start(interval_ms)
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._btn_choose.setEnabled(False)

    def _stop_log(self) -> None:
        self._timer.stop()
        if self._fh:
            self._fh.flush()
            self._fh.close()
            self._fh = None
        self._flush_counter = 0
        self._writer = None
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._btn_choose.setEnabled(True)

    def _clear(self) -> None:
        self._table.setRowCount(0)
        self._rows.clear()
        self._lbl_count.setText("0 rows")

    # ── data collection ───────────────────────────────────────────────────────

    def _build_snap(self) -> dict:
        """
        Collect one measurement snapshot from the drivers' thread-safe
        cached state — no serial I/O on the GUI thread.
        """
        # Build prefix set once instead of calling any(startswith) per check
        prefixes: set[str] = set()
        for k in self._active_keys:
            prefixes.add(k.split("_")[0])

        snap: dict = {}

        if "lcr" in prefixes:
            snap["lcr"] = (self._lcr.last_measurement()
                           if self._lcr.connected else None)

        if self._psu and "psu" in prefixes:
            snap["psu"] = self._psu.get_state() if self._psu.connected else None

        if self._lmg and self._lmg.connected:
            lmg_state = self._lmg.get_state()
            for ch in range(1, 5):
                if f"lmg{ch}" in prefixes:
                    snap[f"lmg_ch{ch}"] = lmg_state.channels[ch - 1]
            if "lmg" in prefixes:
                snap["lmg_agg"] = lmg_state.aggregate

        if self._dsp and self._dsp.connected:
            dsp_state = self._dsp.get_state()
            for ch in (1, 2):
                if f"dsp{ch}" in prefixes:
                    snap[f"dsp_ch{ch}"] = dsp_state.channels[ch - 1]

        if self._eload and "eload" in prefixes:
            snap["eload"] = (self._eload.get_state()
                             if self._eload.connected else None)

        return snap

    def _log_row(self) -> None:
        snap = self._build_snap()
        ts = (f"{time.strftime('%Y-%m-%d %H:%M:%S')}"
              f".{int(time.time() * 1000) % 1000:03d}")
        row: dict = {"timestamp": ts}
        for key in self._active_keys:
            try:
                val = _FIELD_MAP[key].fetch(snap)
                # None / "" = no value received → leave the cell empty,
                # never substitute a zero
                row[key] = "" if val is None or val == "" else (
                    f"{val:.6g}" if isinstance(val, float) else str(val)
                )
            except Exception:
                row[key] = ""

        if self._writer:
            self._writer.writerow(row)
            self._flush_counter += 1
            if self._flush_counter >= 10:
                self._fh.flush()
                self._flush_counter = 0

        self._rows.append(row)
        self._add_table_row(row, ts)
        if len(self._rows) % 10 == 0:
            self._lbl_count.setText(f"{len(self._rows)} rows")

    def _add_table_row(self, row: dict, ts: str) -> None:
        # Evict oldest row when cap is reached to bound memory and repaint cost
        if self._table.rowCount() >= self._TABLE_MAX_ROWS:
            self._table.removeRow(0)
        r = self._table.rowCount()
        self._table.insertRow(r)
        vals = [ts] + [row.get(k, "") for k in self._active_keys]
        for col, val in enumerate(vals):
            item = QTableWidgetItem(str(val))
            item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self._table.setItem(r, col, item)
        self._table.scrollToBottom()

    # ── external API (called by Blockly executor) ─────────────────────────────

    def log_external(self, label: str, value) -> None:
        """Force one log row (called by Blockly lcr_log_value block)."""
        if self._writer:
            self._log_row()


# ── helpers ────────────────────────────────────────────────────────────────────

def _btn(label: str, color: str) -> QPushButton:
    b = QPushButton(label)
    b.setFixedHeight(28)
    b.setStyleSheet(f"""
        QPushButton {{
            background: {color}; color: white; border-radius: 4px;
            font-size: 9pt; font-weight: bold; padding: 0 10px;
        }}
        QPushButton:hover   {{ background: {color}cc; }}
        QPushButton:disabled {{ background: #444; color: #666; }}
    """)
    return b
