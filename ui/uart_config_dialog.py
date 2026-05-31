"""UART / Serial port configuration dialog."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QComboBox, QPushButton, QFrame, QLineEdit,
    QGroupBox, QDialogButtonBox, QMessageBox, QCheckBox,
    QSpinBox,
)

_DARK   = "#1A1A2A"
_CARD   = "#252535"
_ACCENT = "#4C97FF"
_TEXT   = "#FFFFFF"
_DIM    = "#8888AA"

_DIALOG_STYLE = f"""
QDialog, QWidget {{ background: {_DARK}; color: {_TEXT}; }}
QGroupBox {{
    color: {_TEXT}; font-weight: bold;
    border: 1px solid #444460; border-radius: 6px;
    margin-top: 10px; background: {_CARD};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
QComboBox, QLineEdit, QSpinBox {{
    background: #333350; color: {_TEXT};
    border: 1px solid #555580; border-radius: 4px;
    padding: 4px 8px; font-size: 9pt; min-height: 22px;
}}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{ background: #333350; color: {_TEXT}; selection-background-color: {_ACCENT}; }}
QLabel {{ color: {_TEXT}; font-size: 9pt; }}
QCheckBox {{ color: {_TEXT}; font-size: 9pt; }}
QCheckBox::indicator {{ width: 14px; height: 14px; }}
"""

_BTN_STYLE = """
QPushButton {{
    background: {bg}; color: white; border-radius: 4px;
    font-weight: bold; padding: 5px 14px; font-size: 9pt; min-height: 26px;
}}
QPushButton:hover {{ background: {hover}; }}
QPushButton:disabled {{ background: #444; color: #666; }}
"""


@dataclass
class SerialConfig:
    port:      str   = ""
    baudrate:  int   = 9600
    bytesize:  int   = 8        # 5 6 7 8
    parity:    str   = "N"      # N E O M S
    stopbits:  float = 1.0      # 1 1.5 2
    timeout:   float = 1.0      # seconds
    simulate:  bool  = False    # use simulation when True or port empty

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SerialConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class _PortScanner(QThread):
    """Scans serial ports in background so the UI doesn't freeze."""
    finished = Signal(list)   # list of (port, description)

    def run(self) -> None:
        ports = []
        try:
            import serial.tools.list_ports
            for p in serial.tools.list_ports.comports():
                desc = p.description or p.device
                ports.append((p.device, desc))
        except ImportError:
            pass
        self.finished.emit(ports)


class UartConfigDialog(QDialog):
    """
    Modal dialog for configuring the serial / UART interface.

    Usage::
        dlg = UartConfigDialog(current_config, parent=self)
        if dlg.exec():
            new_cfg = dlg.config
    """

    def __init__(self, config: Optional[SerialConfig] = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Serial Port Configuration")
        self.setMinimumWidth(460)
        self.setStyleSheet(_DIALOG_STYLE)
        self._cfg = SerialConfig() if config is None else SerialConfig(**asdict(config))
        self._scanner: Optional[_PortScanner] = None

        self._build_ui()
        self._populate_from_config()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(12)

        # ── Port group ──────────────────────────────────────────────────────
        port_grp = QGroupBox("Port")
        pg = QGridLayout(port_grp)
        pg.setContentsMargins(12, 18, 12, 10)
        pg.setSpacing(8)

        pg.addWidget(QLabel("Port:"), 0, 0)
        self._combo_port = QComboBox()
        self._combo_port.setEditable(True)
        self._combo_port.setMinimumWidth(200)
        self._combo_port.lineEdit().setPlaceholderText("e.g. COM3  or  /dev/ttyUSB0")
        pg.addWidget(self._combo_port, 0, 1)

        self._btn_scan = QPushButton("⟳  Scan")
        self._btn_scan.setStyleSheet(_BTN_STYLE.format(bg="#607D8B", hover="#78909C"))
        self._btn_scan.clicked.connect(self._scan_ports)
        pg.addWidget(self._btn_scan, 0, 2)

        self._lbl_port_desc = QLabel("")
        self._lbl_port_desc.setStyleSheet(f"color: {_DIM}; font-size: 8pt;")
        pg.addWidget(self._lbl_port_desc, 1, 1, 1, 2)

        self._combo_port.currentTextChanged.connect(self._on_port_text_changed)
        root.addWidget(port_grp)

        # ── Parameters group ─────────────────────────────────────────────────
        param_grp = QGroupBox("Parameters")
        gg = QGridLayout(param_grp)
        gg.setContentsMargins(12, 18, 12, 10)
        gg.setSpacing(8)

        def lbl(text: str) -> QLabel:
            l = QLabel(text)
            l.setStyleSheet(f"color: {_DIM};")
            return l

        # Baud rate
        gg.addWidget(lbl("Baud rate:"), 0, 0)
        self._combo_baud = QComboBox()
        self._combo_baud.addItems(["1200","2400","4800","9600","14400","19200",
                                   "38400","57600","115200","230400","460800","921600"])
        gg.addWidget(self._combo_baud, 0, 1)

        # Data bits
        gg.addWidget(lbl("Data bits:"), 1, 0)
        self._combo_data = QComboBox()
        self._combo_data.addItems(["5","6","7","8"])
        self._combo_data.setCurrentText("8")
        gg.addWidget(self._combo_data, 1, 1)

        # Parity
        gg.addWidget(lbl("Parity:"), 2, 0)
        self._combo_parity = QComboBox()
        self._combo_parity.addItems(["None (N)","Even (E)","Odd (O)","Mark (M)","Space (S)"])
        gg.addWidget(self._combo_parity, 2, 1)

        # Stop bits
        gg.addWidget(lbl("Stop bits:"), 3, 0)
        self._combo_stop = QComboBox()
        self._combo_stop.addItems(["1","1.5","2"])
        gg.addWidget(self._combo_stop, 3, 1)

        # Timeout
        gg.addWidget(lbl("Timeout (s):"), 0, 2)
        self._spin_timeout = QSpinBox()
        self._spin_timeout.setRange(0, 30)
        self._spin_timeout.setValue(1)
        self._spin_timeout.setSuffix(" s")
        gg.addWidget(self._spin_timeout, 0, 3)

        root.addWidget(param_grp)

        # ── Simulation fallback ──────────────────────────────────────────────
        sim_grp = QGroupBox("Fallback")
        sg = QVBoxLayout(sim_grp)
        sg.setContentsMargins(12, 18, 12, 10)
        self._chk_simulate = QCheckBox("Use simulation mode when device is not found")
        self._chk_simulate.setChecked(True)
        sg.addWidget(self._chk_simulate)
        root.addWidget(sim_grp)

        # ── Test button ──────────────────────────────────────────────────────
        test_row = QHBoxLayout()
        self._btn_test = QPushButton("Test Connection")
        self._btn_test.setStyleSheet(_BTN_STYLE.format(bg="#FF9800", hover="#FFA726"))
        self._btn_test.clicked.connect(self._test_connection)
        self._lbl_test_result = QLabel("")
        self._lbl_test_result.setStyleSheet("font-size: 9pt;")
        test_row.addWidget(self._btn_test)
        test_row.addWidget(self._lbl_test_result)
        test_row.addStretch()
        root.addLayout(test_row)

        # ── Dialog buttons ───────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #333355;")
        root.addWidget(sep)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.setStyleSheet(f"""
            QPushButton {{
                background: #455A64; color: white; border-radius: 4px;
                padding: 5px 18px; font-weight: bold; min-width: 80px;
            }}
            QPushButton[text="OK"] {{ background: {_ACCENT}; }}
            QPushButton:hover {{ background: #607D8B; }}
        """)
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

        # trigger initial port scan
        self._scan_ports()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _populate_from_config(self) -> None:
        if self._cfg.port:
            self._combo_port.setCurrentText(self._cfg.port)

        baud_str = str(self._cfg.baudrate)
        idx = self._combo_baud.findText(baud_str)
        self._combo_baud.setCurrentIndex(idx if idx >= 0 else 3)  # default 9600

        self._combo_data.setCurrentText(str(self._cfg.bytesize))

        parity_map = {"N": 0, "E": 1, "O": 2, "M": 3, "S": 4}
        self._combo_parity.setCurrentIndex(parity_map.get(self._cfg.parity, 0))

        stop_map = {1.0: 0, 1.5: 1, 2.0: 2}
        self._combo_stop.setCurrentIndex(stop_map.get(float(self._cfg.stopbits), 0))

        self._spin_timeout.setValue(int(self._cfg.timeout))
        self._chk_simulate.setChecked(self._cfg.simulate)

    def _scan_ports(self) -> None:
        self._btn_scan.setEnabled(False)
        self._btn_scan.setText("Scanning…")
        self._scanner = _PortScanner()
        self._scanner.finished.connect(self._on_scan_done)
        self._scanner.start()

    def _on_scan_done(self, ports: list) -> None:
        self._btn_scan.setEnabled(True)
        self._btn_scan.setText("⟳  Scan")
        current = self._combo_port.currentText()
        self._combo_port.blockSignals(True)
        self._combo_port.clear()
        self._port_descs: dict[str, str] = {}
        for port, desc in ports:
            self._combo_port.addItem(f"{port}  —  {desc}", userData=port)
            self._port_descs[port] = desc
        if current:
            # restore user's selection if it was in the list
            idx = self._combo_port.findData(current)
            if idx >= 0:
                self._combo_port.setCurrentIndex(idx)
            else:
                self._combo_port.setEditText(current)
        self._combo_port.blockSignals(False)
        if not ports:
            self._lbl_port_desc.setText("No serial ports found.")

    def _on_port_text_changed(self, text: str) -> None:
        # When user picks from dropdown, extract actual port name
        data = self._combo_port.currentData()
        desc = self._port_descs.get(data or text.split("  —  ")[0], "")
        self._lbl_port_desc.setText(desc)

    def _current_port(self) -> str:
        data = self._combo_port.currentData()
        if data:
            return data
        raw = self._combo_port.currentText().split("  —  ")[0].strip()
        return raw

    def _build_config(self) -> SerialConfig:
        parity_map = {0: "N", 1: "E", 2: "O", 3: "M", 4: "S"}
        stop_map   = {0: 1.0, 1: 1.5, 2: 2.0}
        return SerialConfig(
            port     = self._current_port(),
            baudrate = int(self._combo_baud.currentText()),
            bytesize = int(self._combo_data.currentText()),
            parity   = parity_map[self._combo_parity.currentIndex()],
            stopbits = stop_map[self._combo_stop.currentIndex()],
            timeout  = float(self._spin_timeout.value()),
            simulate = self._chk_simulate.isChecked(),
        )

    def _test_connection(self) -> None:
        cfg = self._build_config()
        port = cfg.port
        if not port:
            self._lbl_test_result.setText("⚠  No port selected.")
            self._lbl_test_result.setStyleSheet("color: #FF9800; font-size: 9pt;")
            return
        try:
            import serial
            s = serial.Serial(
                port=port, baudrate=cfg.baudrate,
                bytesize=cfg.bytesize, parity=cfg.parity,
                stopbits=cfg.stopbits, timeout=0.3,
            )
            s.close()
            self._lbl_test_result.setText(f"✔  {port} opened successfully.")
            self._lbl_test_result.setStyleSheet("color: #4CAF50; font-size: 9pt;")
        except Exception as e:
            self._lbl_test_result.setText(f"✘  {e}")
            self._lbl_test_result.setStyleSheet("color: #f44336; font-size: 9pt;")

    def _on_accept(self) -> None:
        cfg = self._build_config()
        if not cfg.port and not cfg.simulate:
            QMessageBox.warning(self, "No Port",
                "Please select a serial port or enable simulation fallback.")
            return
        self._cfg = cfg
        self.accept()

    @property
    def config(self) -> SerialConfig:
        return self._cfg
