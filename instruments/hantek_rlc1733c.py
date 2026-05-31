"""
Hantek RLC 1733C driver.

Communication: USB-HID (Vendor ID 0x0483, Product ID 0x5740 typical) or USB-CDC serial.
The 1733C enumerates as a USB HID device. Each 64-byte report encodes measurement data.

Report structure (read, 64 bytes):
  [0]  Report ID (always 0)
  [1]  Status: 0x00=ok, 0x01=overload, 0x02=underload
  [2]  Primary function code (see MeasureMode)
  [3]  Secondary function code
  [4]  Frequency index (see TestFrequency)
  [5]  Range index
  [6]  Flags (bit0=hold, bit1=rel, bit2=beep)
  [7-10]  Primary value as IEEE-754 float (little-endian)
  [11-14] Secondary value as IEEE-754 float (little-endian)
  [15-18] Phase angle as IEEE-754 float (little-endian)
  [19]  Primary unit string index
  [20]  Secondary unit string index

Write report (command, 64 bytes):
  [0]  Report ID
  [1]  Command (0x01=set_mode, 0x02=set_freq, 0x03=set_range, 0x04=hold, 0x05=rel, 0x06=beep)
  [2]  Argument byte
  [3..63] 0x00
"""

from __future__ import annotations

import struct
import time
import math
import random
import threading
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Optional

# USB identifiers – may vary by firmware revision
_VENDOR_ID = 0x0483
_PRODUCT_IDS = (0x5740, 0x0001)  # try both

# Serial fallback settings (USB-CDC)
_SERIAL_BAUD = 9600
_SERIAL_TIMEOUT = 0.5


class MeasureMode(IntEnum):
    Ls = 0x01   # Inductance series
    Lp = 0x02   # Inductance parallel
    Cs = 0x03   # Capacitance series
    Cp = 0x04   # Capacitance parallel
    Rs = 0x05   # Resistance series
    Rp = 0x06   # Resistance parallel
    Z  = 0x07   # Impedance
    D  = 0x08   # Dissipation factor
    Q  = 0x09   # Quality factor
    Theta = 0x0A  # Phase angle
    ESR = 0x0B  # Equivalent series resistance
    DCR = 0x0C  # DC resistance


class TestFrequency(IntEnum):
    F100Hz   = 0
    F120Hz   = 1
    F1kHz    = 2
    F10kHz   = 3
    F100kHz  = 4

    @property
    def hz(self) -> float:
        return (100, 120, 1000, 10000, 100000)[self.value]

    @property
    def label(self) -> str:
        return ("100 Hz", "120 Hz", "1 kHz", "10 kHz", "100 kHz")[self.value]


_PRIMARY_UNITS = ["H", "H", "F", "F", "Ω", "Ω", "Ω", "—", "—", "°", "Ω", "Ω"]
_SECONDARY_UNITS = ["D", "Q", "D", "Q", "Q", "D", "Q", "R", "D", "Z", "Z", "Z"]

_MODE_PREFIXES = {
    MeasureMode.Ls: "Ls",
    MeasureMode.Lp: "Lp",
    MeasureMode.Cs: "Cs",
    MeasureMode.Cp: "Cp",
    MeasureMode.Rs: "Rs",
    MeasureMode.Rp: "Rp",
    MeasureMode.Z:  "Z",
    MeasureMode.D:  "D",
    MeasureMode.Q:  "Q",
    MeasureMode.Theta: "θ",
    MeasureMode.ESR: "ESR",
    MeasureMode.DCR: "DCR",
}


def _eng(value: float, unit: str) -> str:
    """Format value with SI engineering prefix."""
    if value == 0 or not math.isfinite(value):
        return f"0.000 {unit}"
    exp = int(math.floor(math.log10(abs(value)) / 3) * 3)
    exp = max(-12, min(12, exp))
    prefixes = {-12: "p", -9: "n", -6: "µ", -3: "m", 0: "", 3: "k", 6: "M", 9: "G", 12: "T"}
    prefix = prefixes.get(exp, "")
    scaled = value / (10 ** exp)
    return f"{scaled:.4g} {prefix}{unit}"


@dataclass
class Measurement:
    mode: MeasureMode
    frequency: TestFrequency
    primary: float
    secondary: float
    phase: float
    overload: bool = False
    hold: bool = False
    rel: bool = False
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
        return _MODE_PREFIXES.get(self.mode, str(self.mode))


class _SimulatedDevice:
    """Generates realistic fake measurements for demo/testing without hardware."""

    def __init__(self) -> None:
        self._mode = MeasureMode.Cs
        self._freq = TestFrequency.F1kHz
        self._hold = False
        self._hold_meas: Optional[Measurement] = None
        self._t0 = time.time()

    def set_mode(self, mode: MeasureMode) -> None:
        self._mode = mode

    def set_frequency(self, freq: TestFrequency) -> None:
        self._freq = freq

    def set_hold(self, hold: bool) -> None:
        self._hold = hold
        if hold:
            self._hold_meas = self._generate()

    def read(self) -> Measurement:
        if self._hold and self._hold_meas:
            return self._hold_meas
        return self._generate()

    def _generate(self) -> Measurement:
        t = time.time() - self._t0
        noise = lambda: 1 + 0.005 * random.gauss(0, 1)

        if self._mode in (MeasureMode.Cs, MeasureMode.Cp):
            primary = 100e-9 * noise()   # 100 nF cap
            secondary = 0.02 * noise()   # D=0.02
        elif self._mode in (MeasureMode.Ls, MeasureMode.Lp):
            primary = 10e-3 * noise()    # 10 mH inductor
            secondary = 50 * noise()     # Q=50
        elif self._mode in (MeasureMode.Rs, MeasureMode.Rp, MeasureMode.DCR):
            primary = 1000 * noise()     # 1 kΩ
            secondary = 0.001 * noise()
        elif self._mode == MeasureMode.Z:
            primary = 1000 * noise()
            secondary = 50 * noise()     # Q
        elif self._mode == MeasureMode.ESR:
            primary = 0.5 * noise()      # 0.5 Ω ESR
            secondary = 100e-9 * noise()
        elif self._mode == MeasureMode.Q:
            primary = 50 * noise()
            secondary = 10e-3 * noise()
        elif self._mode == MeasureMode.D:
            primary = 0.02 * noise()
            secondary = 100e-9 * noise()
        elif self._mode == MeasureMode.Theta:
            primary = -45 + 2 * math.sin(t * 0.1)
            secondary = 1000 * noise()
        else:
            primary = 1.0 * noise()
            secondary = 0.0

        return Measurement(
            mode=self._mode,
            frequency=self._freq,
            primary=primary,
            secondary=secondary,
            phase=-45.0,
        )


class HantekRLC1733C:
    """
    Driver for the Hantek RLC 1733C LCR meter.

    Tries USB-HID first, falls back to USB-CDC serial, then simulation mode.
    Pass a SerialConfig (from ui.uart_config_dialog) to connect_with_config()
    to use an explicit port instead of auto-discovery.
    """

    def __init__(self, simulate: bool = False) -> None:
        self._simulate = simulate
        self._hid_dev = None
        self._serial_dev = None
        self._sim = _SimulatedDevice()
        self._mode = MeasureMode.Cs
        self._freq = TestFrequency.F1kHz
        self._lock = threading.Lock()
        self._connected = False
        self._serial_config: Optional[dict] = None   # last applied SerialConfig dict
        self._connection_info: str = ""              # human-readable description

        self._poll_thread: Optional[threading.Thread] = None
        self._polling = False
        self._poll_interval = 0.5
        self._callbacks: list[Callable[[Measurement], None]] = []

    # ── connection ──────────────────────────────────────────────────────────

    def connect_with_config(self, serial_cfg) -> tuple[bool, str]:
        """
        Connect using an explicit SerialConfig (from UartConfigDialog).

        Returns (success, description) where description says what connected.
        """
        self.disconnect()
        port     = getattr(serial_cfg, "port", "") or ""
        baudrate = getattr(serial_cfg, "baudrate", _SERIAL_BAUD)
        bytesize = getattr(serial_cfg, "bytesize", 8)
        parity   = getattr(serial_cfg, "parity", "N")
        stopbits = getattr(serial_cfg, "stopbits", 1.0)
        timeout  = getattr(serial_cfg, "timeout", _SERIAL_TIMEOUT)
        simulate_fallback = getattr(serial_cfg, "simulate", True)

        self._serial_config = serial_cfg.to_dict() if hasattr(serial_cfg, "to_dict") else {}

        if port:
            # Try HID first on that port (VID/PID via HID)
            try:
                import hid
                for pid in _PRODUCT_IDS:
                    try:
                        dev = hid.device()
                        dev.open(_VENDOR_ID, pid)
                        dev.set_nonblocking(False)
                        self._hid_dev = dev
                        self._connected = True
                        self._simulate = False
                        self._connection_info = f"USB-HID  VID={_VENDOR_ID:#06x} PID={pid:#06x}"
                        return True, self._connection_info
                    except OSError:
                        pass
            except ImportError:
                pass

            # Try explicit serial port
            try:
                import serial
                s = serial.Serial(
                    port=port, baudrate=baudrate,
                    bytesize=bytesize, parity=parity,
                    stopbits=stopbits, timeout=timeout,
                )
                self._serial_dev = s
                self._connected = True
                self._simulate = False
                self._connection_info = f"{port}  {baudrate} {bytesize}{parity}{stopbits}"
                return True, self._connection_info
            except Exception as e:
                if not simulate_fallback:
                    return False, str(e)

        # No port or serial failed → simulation fallback
        if simulate_fallback or not port:
            self._simulate = True
            self._connected = True
            self._connection_info = "Simulation"
            return True, self._connection_info

        return False, "Could not connect"

    def connect(self) -> bool:
        if self._simulate:
            self._connected = True
            self._connection_info = "Simulation"
            return True
        # Try HID auto-discovery
        try:
            import hid
            for pid in _PRODUCT_IDS:
                try:
                    dev = hid.device()
                    dev.open(_VENDOR_ID, pid)
                    dev.set_nonblocking(False)
                    self._hid_dev = dev
                    self._connected = True
                    self._connection_info = f"USB-HID  VID={_VENDOR_ID:#06x} PID={pid:#06x}"
                    return True
                except OSError:
                    pass
        except ImportError:
            pass

        # Try serial auto-discovery (USB-CDC Hantek device)
        try:
            import serial
            import serial.tools.list_ports
            for port in serial.tools.list_ports.comports():
                vid_hex = f"{port.vid:04X}" if port.vid else ""
                if "0483" in vid_hex or "hantek" in (port.description or "").lower():
                    self._serial_dev = serial.Serial(
                        port.device, _SERIAL_BAUD, timeout=_SERIAL_TIMEOUT)
                    self._connected = True
                    self._connection_info = f"{port.device}  {_SERIAL_BAUD} 8N1 (auto)"
                    return True
        except Exception:
            pass

        # Fall back to simulation
        self._simulate = True
        self._connected = True
        self._connection_info = "Simulation (no device found)"
        return True  # always succeeds (simulation)

    @property
    def connection_info(self) -> str:
        return self._connection_info

    def disconnect(self) -> None:
        self.stop_polling()
        with self._lock:
            if self._hid_dev:
                try:
                    self._hid_dev.close()
                except Exception:
                    pass
                self._hid_dev = None
            if self._serial_dev:
                try:
                    self._serial_dev.close()
                except Exception:
                    pass
                self._serial_dev = None
            self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def is_simulated(self) -> bool:
        return self._simulate

    # ── configuration ────────────────────────────────────────────────────────

    def set_mode(self, mode: MeasureMode) -> None:
        self._mode = mode
        if self._simulate:
            self._sim.set_mode(mode)
            return
        self._write_command(0x01, mode.value)

    def set_frequency(self, freq: TestFrequency) -> None:
        self._freq = freq
        if self._simulate:
            self._sim.set_frequency(freq)
            return
        self._write_command(0x02, freq.value)

    def set_hold(self, hold: bool) -> None:
        if self._simulate:
            self._sim.set_hold(hold)
            return
        self._write_command(0x04, 0x01 if hold else 0x00)

    def set_rel(self, rel: bool) -> None:
        if self._simulate:
            return
        self._write_command(0x05, 0x01 if rel else 0x00)

    def trigger_beep(self) -> None:
        if self._simulate:
            return
        self._write_command(0x06, 0x01)

    # ── measurement ──────────────────────────────────────────────────────────

    def measure(self) -> Measurement:
        with self._lock:
            if self._simulate:
                return self._sim.read()
            if self._hid_dev:
                return self._read_hid()
            if self._serial_dev:
                return self._read_serial()
        return self._sim.read()

    # ── polling ──────────────────────────────────────────────────────────────

    def add_callback(self, cb: Callable[[Measurement], None]) -> None:
        self._callbacks.append(cb)

    def remove_callback(self, cb: Callable[[Measurement], None]) -> None:
        self._callbacks.discard(cb) if hasattr(self._callbacks, "discard") else None
        if cb in self._callbacks:
            self._callbacks.remove(cb)

    def start_polling(self, interval: float = 0.5) -> None:
        self._poll_interval = interval
        if self._polling:
            return
        self._polling = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def stop_polling(self) -> None:
        self._polling = False
        if self._poll_thread:
            self._poll_thread.join(timeout=2)
            self._poll_thread = None

    def _poll_loop(self) -> None:
        while self._polling:
            try:
                m = self.measure()
                for cb in list(self._callbacks):
                    try:
                        cb(m)
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(self._poll_interval)

    # ── raw I/O ──────────────────────────────────────────────────────────────

    def _write_command(self, cmd: int, arg: int) -> None:
        buf = bytes([0x00, cmd, arg]) + bytes(61)
        if self._hid_dev:
            try:
                self._hid_dev.write(buf)
            except Exception:
                pass
        elif self._serial_dev:
            try:
                self._serial_dev.write(buf)
            except Exception:
                pass

    def _read_hid(self) -> Measurement:
        try:
            data = self._hid_dev.read(64, timeout_ms=1000)
            return self._parse_report(data)
        except Exception:
            return self._sim.read()

    def _read_serial(self) -> Measurement:
        try:
            self._serial_dev.write(bytes([0x00, 0x00, 0x00] + [0] * 61))
            data = self._serial_dev.read(64)
            if len(data) >= 19:
                return self._parse_report(data)
        except Exception:
            pass
        return self._sim.read()

    def _parse_report(self, data: bytes | list) -> Measurement:
        if len(data) < 19:
            return self._sim.read()
        status  = data[1]
        mode_b  = data[2]
        freq_b  = data[4]
        flags   = data[6]
        primary  = struct.unpack_from("<f", bytes(data), 7)[0]
        secondary = struct.unpack_from("<f", bytes(data), 11)[0]
        phase   = struct.unpack_from("<f", bytes(data), 15)[0]

        try:
            mode = MeasureMode(mode_b)
        except ValueError:
            mode = self._mode
        try:
            freq = TestFrequency(freq_b)
        except ValueError:
            freq = self._freq

        return Measurement(
            mode=mode,
            frequency=freq,
            primary=primary,
            secondary=secondary,
            phase=phase,
            overload=bool(status & 0x01),
            hold=bool(flags & 0x01),
            rel=bool(flags & 0x02),
        )

    # ── config serialisation ─────────────────────────────────────────────────

    def get_config(self) -> dict:
        cfg = {
            "mode": self._mode.value,
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
