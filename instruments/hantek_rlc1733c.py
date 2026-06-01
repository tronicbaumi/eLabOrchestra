"""
Hantek RLC 1733C / 1832C / 1833C LCR Meter driver.

Communication: USB CDC virtual serial port.
  The device appears as a standard COM port (VID=0x0483, PID=0x5740).
  Baud rate is irrelevant for USB CDC, but 9600 is used for compatibility.

Protocol: SCPI text commands, terminated with <LF> (0x0A) or <CR><LF>.
  The device sends responses terminated with <CR><LF>.

Supported commands (Chapter 4, manual V1.0.2):
  *IDN?                               → <model>,<sw_ver>,<serial>,<hw_ver>
  *GTL                                → unlock front-panel keyboard
  FREQuency <hz>                      → set frequency
  FREQuency?                          → query frequency (returns integer Hz)
  FUNCtion:impa <R|L|C|Z|Auto>       → set main parameter
  FUNCtion:impa?                      → query main parameter
  FUNCtion:impb <X|Q|D|THETA|ESR>    → set secondary parameter
  FUNCtion:impb?                      → query secondary parameter
  FUNCtion:RANGe <AUTO|10|100|1000|10000|100000>
  FUNCtion:RANGe?
  FUNCtion:LEVel <300|600>            → set signal level (mVrms)
  FUNCtion:LEVel?
  FUNCtion:EQUivalent <SER|PAL>       → set equivalent mode
  FUNCtion:EQUivalent?
  FETCh?                              → <NR3>,<NR3>,<NR1>
                                         primary value, secondary value, range index
"""

from __future__ import annotations

import time
import math
import threading
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Optional


# USB identifiers for auto-discovery via serial port list
_VENDOR_ID  = 0x0483
_PRODUCT_ID = 0x5740

_SERIAL_BAUD    = 9600   # nominal; USB CDC ignores baud rate
_SERIAL_TIMEOUT = 0.5    # seconds — keep short so lock contention doesn't stall the UI


class MeasureMode(IntEnum):
    Ls    = 0x01   # Inductance, series equivalent
    Lp    = 0x02   # Inductance, parallel equivalent
    Cs    = 0x03   # Capacitance, series equivalent
    Cp    = 0x04   # Capacitance, parallel equivalent
    Rs    = 0x05   # Resistance, series
    Rp    = 0x06   # Resistance, parallel
    Z     = 0x07   # Impedance
    D     = 0x08   # Dissipation factor (C main)
    Q     = 0x09   # Quality factor (L main)
    Theta = 0x0A   # Phase angle (Z main)
    ESR   = 0x0B   # Equivalent series resistance (C main)
    DCR   = 0x0C   # DC resistance


class TestFrequency(IntEnum):
    F100Hz  = 0
    F120Hz  = 1
    F1kHz   = 2
    F10kHz  = 3
    F100kHz = 4

    @property
    def hz(self) -> int:
        return (100, 120, 1000, 10000, 100000)[self.value]

    @property
    def label(self) -> str:
        return ("100 Hz", "120 Hz", "1 kHz", "10 kHz", "100 kHz")[self.value]


# SCPI command mappings for each mode
# (impa_cmd, impb_cmd, equiv_cmd or None)
_MODE_SCPI: dict[MeasureMode, tuple[str, str, str | None]] = {
    MeasureMode.Ls:    ("L",    "Q",     "SER"),
    MeasureMode.Lp:    ("L",    "Q",     "PAL"),
    MeasureMode.Cs:    ("C",    "D",     "SER"),
    MeasureMode.Cp:    ("C",    "D",     "PAL"),
    MeasureMode.Rs:    ("R",    "Q",     "SER"),
    MeasureMode.Rp:    ("R",    "Q",     "PAL"),
    MeasureMode.Z:     ("Z",    "THETA", None),
    MeasureMode.D:     ("C",    "D",     None),
    MeasureMode.Q:     ("L",    "Q",     None),
    MeasureMode.Theta: ("Z",    "THETA", None),
    MeasureMode.ESR:   ("C",    "ESR",   None),
    MeasureMode.DCR:   ("R",    "D",     None),
}

# Indexed by MeasureMode.value - 1  (Ls=0 … DCR=11)
# Primary unit reflects impa: L→H, C→F, R/Z→Ω
# Secondary unit reflects impb: Q→Q, D→D, THETA→°, ESR→Ω
_PRIMARY_UNITS   = ["H", "H", "F", "F", "Ω", "Ω", "Ω", "F", "H", "Ω", "F", "Ω"]
#                   Ls   Lp   Cs   Cp   Rs   Rp   Z    D    Q   Th  ESR  DCR
_SECONDARY_UNITS = ["Q", "Q", "D", "D", "Q", "Q", "°", "D", "Q", "°", "Ω", "D"]

_MODE_LABELS = {
    MeasureMode.Ls:    "Ls",
    MeasureMode.Lp:    "Lp",
    MeasureMode.Cs:    "Cs",
    MeasureMode.Cp:    "Cp",
    MeasureMode.Rs:    "Rs",
    MeasureMode.Rp:    "Rp",
    MeasureMode.Z:     "Z",
    MeasureMode.D:     "D",
    MeasureMode.Q:     "Q",
    MeasureMode.Theta: "θ",
    MeasureMode.ESR:   "ESR",
    MeasureMode.DCR:   "DCR",
}


def _eng(value: float, unit: str) -> str:
    """Format value with SI engineering prefix."""
    if value == 0 or not math.isfinite(value):
        return f"0.000 {unit}"
    exp = int(math.floor(math.log10(abs(value)) / 3) * 3)
    exp = max(-12, min(12, exp))
    prefixes = {-12: "p", -9: "n", -6: "µ", -3: "m", 0: "",
                3: "k", 6: "M", 9: "G", 12: "T"}
    prefix = prefixes.get(exp, "")
    scaled = value / (10 ** exp)
    return f"{scaled:.4g} {prefix}{unit}"


@dataclass
class Measurement:
    mode:      MeasureMode
    frequency: TestFrequency
    primary:   float
    secondary: float
    phase:     float          # degrees; equals secondary when in Theta mode
    overload:  bool  = False
    hold:      bool  = False
    rel:       bool  = False
    timestamp: float = field(default_factory=time.time)

    @property
    def primary_unit(self) -> str:
        idx = self.mode.value - 1
        return _PRIMARY_UNITS[idx] if 0 <= idx < len(_PRIMARY_UNITS) else "?"

    @property
    def secondary_unit(self) -> str:
        idx = self.mode.value - 1
        return _SECONDARY_UNITS[idx] if 0 <= idx < len(_SECONDARY_UNITS) else "?"

    @property
    def primary_str(self) -> str:
        if self.overload:
            return "OL"
        return _eng(self.primary, self.primary_unit)

    @property
    def secondary_str(self) -> str:
        return f"{self.secondary:.4g} {self.secondary_unit}"

    @property
    def mode_label(self) -> str:
        return _MODE_LABELS.get(self.mode, str(self.mode))


class _SerialLCR:
    """Low-level SCPI serial transport."""

    def __init__(self, port: str, baudrate: int = _SERIAL_BAUD,
                 timeout: float = _SERIAL_TIMEOUT, log=None) -> None:
        import serial
        self._ser = serial.Serial(
            port=port, baudrate=baudrate,
            bytesize=8, parity="N", stopbits=1,
            timeout=timeout,
        )
        self._log = log
        time.sleep(0.1)
        self._ser.reset_input_buffer()

    def write(self, cmd: str) -> None:
        cmd = cmd.strip()
        self._ser.write((cmd + "\n").encode())
        if self._log:
            self._log("TX", cmd)

    def query(self, cmd: str) -> str:
        # Flush stale bytes from any prior timeout before sending the new command
        self._ser.reset_input_buffer()
        self.write(cmd)
        raw = self._ser.readline().decode(errors="replace").strip()
        if self._log:
            self._log("RX", raw)
        return raw

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:
            pass


class HantekRLC1733C:
    """
    Driver for the Hantek RLC 1733C / 1832C / 1833C LCR meter.

    The instrument communicates via USB CDC virtual serial port using SCPI.
    Auto-discovery looks for VID=0x0483, PID=0x5740 on any COM port.
    An explicit port can be passed via connect_with_config().
    """

    def __init__(self) -> None:
        self._dev:   Optional[_SerialLCR] = None
        self._lock   = threading.Lock()
        self._connected   = False
        self._log_cb      = None
        self._serial_config: Optional[dict] = None
        self._connection_info: str = ""

        self._mode   = MeasureMode.Cs
        self._freq   = TestFrequency.F1kHz
        self._hold   = False
        self._held_meas: Optional[Measurement] = None

        self._poll_thread:  Optional[threading.Thread] = None
        self._poll_stop     = threading.Event()
        self._poll_interval = 0.5
        self._callbacks: list[Callable[[Measurement], None]] = []

    # ── logging ──────────────────────────────────────────────────────────────

    def set_log_callback(self, cb) -> None:
        self._log_cb = cb

    def _log(self, direction: str, text: str) -> None:
        if self._log_cb:
            try:
                self._log_cb(direction, text)
            except Exception:
                pass

    # ── connection ────────────────────────────────────────────────────────────

    def connect_with_config(self, serial_cfg) -> tuple[bool, str]:
        """
        Connect using a SerialConfig. Tries the specified port first;
        if no port given, auto-discovers by VID/PID.
        Returns (success, info_string).
        """
        self.disconnect()
        port     = getattr(serial_cfg, "port", "") or ""
        baudrate = getattr(serial_cfg, "baudrate", _SERIAL_BAUD)
        timeout  = getattr(serial_cfg, "timeout",  _SERIAL_TIMEOUT)
        self._serial_config = serial_cfg.to_dict() if hasattr(serial_cfg, "to_dict") else {}

        if port:
            return self._try_connect(port, baudrate, timeout)

        # Auto-discover by VID/PID
        auto_port = self._find_port()
        if auto_port:
            return self._try_connect(auto_port, baudrate, timeout)

        return False, "Device not found — connect via USB and select COM port"

    def connect(self) -> bool:
        """Auto-discover and connect. Returns True on success."""
        port = self._find_port()
        if port:
            ok, _ = self._try_connect(port)
            return ok
        return False

    def _find_port(self) -> str:
        """Scan serial ports for VID=0x0483 PID=0x5740 and return device name."""
        try:
            import serial.tools.list_ports
            for p in serial.tools.list_ports.comports():
                if p.vid == _VENDOR_ID and p.pid == _PRODUCT_ID:
                    return p.device
                # Fallback: match by description string
                desc = (p.description or "").lower()
                if "hantek" in desc or "lcr" in desc:
                    return p.device
        except Exception:
            pass
        return ""

    def _try_connect(self, port: str, baudrate: int = _SERIAL_BAUD,
                     timeout: float = _SERIAL_TIMEOUT) -> tuple[bool, str]:
        try:
            dev = _SerialLCR(port, baudrate, timeout, log=self._log)
            idn = dev.query("*IDN?")
            if not idn:
                dev.close()
                return False, f"{port}: no response to *IDN?"
            self._dev = dev
            self._connected = True
            self._connection_info = f"{port}  {baudrate} bps  |  {idn}"
            return True, self._connection_info
        except Exception as e:
            return False, str(e)

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def connection_info(self) -> str:
        return self._connection_info

    def disconnect(self) -> None:
        self.stop_polling()
        with self._lock:
            if self._dev:
                try:
                    self._dev.write("*GTL")   # release keyboard lock
                except Exception:
                    pass
                self._dev.close()
                self._dev = None
            self._connected = False

    # ── instrument configuration ──────────────────────────────────────────────

    def set_mode(self, mode: MeasureMode) -> None:
        self._mode = mode
        impa, impb, equiv = _MODE_SCPI.get(mode, ("C", "D", None))
        with self._lock:
            if self._dev:
                self._log("TX", f"set_mode {mode.name}")
                self._dev.write(f"FUNCtion:impa {impa}")
                self._dev.write(f"FUNCtion:impb {impb}")
                if equiv:
                    self._dev.write(f"FUNCtion:EQUivalent {equiv}")

    def set_frequency(self, freq: TestFrequency) -> None:
        self._freq = freq
        with self._lock:
            if self._dev:
                self._log("TX", f"set_frequency {freq.label}")
                self._dev.write(f"FREQuency {freq.hz}")

    def set_hold(self, hold: bool) -> None:
        self._hold = hold
        if not hold:
            self._held_meas = None
        self._log("TX", f"hold {'ON' if hold else 'OFF'}")

    def set_rel(self, rel: bool) -> None:
        self._log("TX", f"rel {'ON' if rel else 'OFF'}")
        # REL mode is handled on-device; no remote command in this firmware version

    def set_level(self, mv: int) -> None:
        """Set signal level: 300 or 600 (mVrms)."""
        with self._lock:
            if self._dev:
                self._dev.write(f"FUNCtion:LEVel {mv}")

    def set_range(self, r: str) -> None:
        """Set range: 'AUTO', '10', '100', '1000', '10000', '100000'."""
        with self._lock:
            if self._dev:
                self._dev.write(f"FUNCtion:RANGe {r}")

    def trigger_beep(self) -> None:
        pass   # no SCPI beep command in this device

    # ── measurement ───────────────────────────────────────────────────────────

    def _default_measurement(self) -> Measurement:
        return Measurement(mode=self._mode, frequency=self._freq,
                           primary=0.0, secondary=0.0, phase=0.0)

    def measure(self) -> Measurement:
        if self._hold and self._held_meas is not None:
            return self._held_meas

        with self._lock:
            if not self._dev:
                return self._default_measurement()
            try:
                resp = self._dev.query("FETCh?")
                m = self._parse_fetch(resp)
                if self._hold:
                    self._held_meas = m
                return m
            except Exception:
                return self._default_measurement()

    def _parse_fetch(self, resp: str) -> Measurement:
        """
        Parse FETCh? response: '<NR3>,<NR3>,<NR1>'
        e.g. '1.234567E-04,2.345678E-02,3'
        """
        parts = [p.strip() for p in resp.split(",")]
        overload = False

        try:
            primary = float(parts[0])
            # Values ≥ 9.9E+37 indicate overload / out of range
            if not math.isfinite(primary) or abs(primary) >= 9.9e37:
                overload = True
                primary = 0.0
        except (ValueError, IndexError):
            overload = True
            primary = 0.0

        try:
            secondary = float(parts[1])
            if not math.isfinite(secondary):
                secondary = 0.0
        except (ValueError, IndexError):
            secondary = 0.0

        # Phase: use secondary value when in theta/Z mode
        phase = secondary if self._mode in (MeasureMode.Theta, MeasureMode.Z) else 0.0

        return Measurement(
            mode=self._mode,
            frequency=self._freq,
            primary=primary,
            secondary=secondary,
            phase=phase,
            overload=overload,
            hold=self._hold,
        )

    # ── polling ───────────────────────────────────────────────────────────────

    def add_callback(self, cb: Callable[[Measurement], None]) -> None:
        self._callbacks.append(cb)

    def remove_callback(self, cb: Callable[[Measurement], None]) -> None:
        if cb in self._callbacks:
            self._callbacks.remove(cb)

    def start_polling(self, interval: float = 0.5) -> None:
        self._poll_interval = interval
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def stop_polling(self) -> None:
        self._poll_stop.set()
        if self._poll_thread:
            self._poll_thread.join(timeout=2)
            self._poll_thread = None

    def _poll_loop(self) -> None:
        while not self._poll_stop.is_set():
            try:
                m = self.measure()
                for cb in list(self._callbacks):
                    try:
                        cb(m)
                    except Exception:
                        pass
            except Exception:
                pass
            self._poll_stop.wait(self._poll_interval)

    # ── config serialisation ──────────────────────────────────────────────────

    def get_config(self) -> dict:
        cfg: dict = {
            "mode":      self._mode.value,
            "frequency": self._freq.value,
        }
        if self._serial_config:
            cfg["serial"] = self._serial_config
        return cfg

    def apply_config(self, cfg: dict) -> None:
        if "serial" in cfg:
            self._serial_config = cfg["serial"]
        if "mode" in cfg:
            self.set_mode(MeasureMode(cfg["mode"]))
        if "frequency" in cfg:
            self.set_frequency(TestFrequency(cfg["frequency"]))
