"""
OWON SP3103 DC Power Supply driver.

Communication: USB-CDC serial (SCPI-like protocol).
Default: 9600 baud, 8N1.

Command set used by this driver (standard SCPI per SP3103 manual):
  *IDN?           → identify string
  SYST:REM        → enter remote control mode
  VOLT <v>        → set voltage (0.00 – 30.00 V)
  CURR <i>        → set current limit (0.000 – 3.000 A)
  VOLT?           → read set voltage
  CURR?           → read set current limit
  OUTP ON|OFF     → enable/disable output
  OUTP?           → read output state ("1" or "0")
  MEAS:VOLT?      → read actual output voltage
  MEAS:CURR?      → read actual output current
  MEAS:POW?       → read actual output power

Power is also available directly from MEAS:POW?.
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field, replace
from typing import Optional, Callable


def _safe_float(raw: str | None, round_to: int | None = None) -> float | None:
    """Parse an instrument response; None for empty/unparseable (never 0)."""
    if not raw:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return round(v, round_to) if round_to is not None else v


# ── data classes ─────────────────────────────────────────────────────────────

@dataclass
class PSUState:
    """Snapshot of one measurement cycle.

    meas_voltage / meas_current are None until a valid reading has been
    received from the instrument — a missing or unparseable response is
    never reported as 0.0."""
    set_voltage:  float = 0.0            # V  (programmed)
    set_current:  float = 0.0            # A  (programmed limit)
    meas_voltage: float | None = None    # V  (actual)
    meas_current: float | None = None    # A  (actual)
    output_on:    bool  = False
    overload:     bool  = False          # CV/CC limit hit

    @property
    def power(self) -> float | None:
        if self.meas_voltage is None or self.meas_current is None:
            return None
        return round(self.meas_voltage * self.meas_current, 4)


# ── real serial connection ────────────────────────────────────────────────────

class _SerialSP3103:
    def __init__(self, port: str, baudrate: int = 9600, timeout: float = 1.0,
                 log=None):
        import serial
        self._ser = serial.Serial(
            port=port, baudrate=baudrate, bytesize=8,
            parity='N', stopbits=1, timeout=timeout,
        )
        self._log = log
        time.sleep(0.2)

    def write(self, cmd: str) -> None:
        cmd = cmd.strip()
        self._ser.write((cmd + "\n").encode())
        if self._log:
            self._log("TX", cmd)
        time.sleep(0.05)

    def query(self, cmd: str) -> str:
        cmd = cmd.strip()
        self._ser.write((cmd + "\n").encode())
        if self._log:
            self._log("TX", cmd)
        time.sleep(0.05)
        resp = self._ser.readline().decode(errors="replace").strip()
        if self._log:
            self._log("RX", resp)
        return resp

    def close(self) -> None:
        self._ser.close()


# ── public driver ─────────────────────────────────────────────────────────────

class OwonSP3103:
    """
    Driver for the OWON SP3103 30 V / 3 A single-channel DC power supply.

    Usage::
        psu = OwonSP3103()
        ok, info = psu.connect(port="COM3")
        psu.set_voltage(12.0)
        psu.set_current(0.5)
        psu.set_output(True)
        state = psu.measure()
        print(state.meas_voltage, state.meas_current, state.power)
        psu.disconnect()
    """

    MAX_VOLTAGE = 30.0
    MAX_CURRENT = 3.0

    def __init__(self) -> None:
        self._dev       = None
        # _io_lock serialises serial transactions (held per command, so a
        # slow poll cycle never blocks a set_* call for more than one query);
        # _state_lock guards the cached _state snapshot and is never held
        # during serial I/O.
        self._io_lock    = threading.Lock()
        self._state_lock = threading.Lock()
        self._connected = False
        self._log_cb    = None
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._state     = PSUState()
        self._poll_cbs: list[Callable[[PSUState], None]] = []

    # ── locked serial primitives ──────────────────────────────────────────────

    def _write_cmd(self, cmd: str) -> bool:
        with self._io_lock:
            if not self._dev:
                return False
            self._dev.write(cmd)
            return True

    def _query_cmd(self, cmd: str) -> str | None:
        with self._io_lock:
            if not self._dev:
                return None
            return self._dev.query(cmd)

    # ── connection ────────────────────────────────────────────────────────────

    def set_log_callback(self, cb) -> None:
        """cb(direction: str, message: str) — called for every TX/RX message."""
        self._log_cb = cb

    def _log(self, direction: str, text: str) -> None:
        if self._log_cb:
            try:
                self._log_cb(direction, text)
            except Exception:
                pass

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def connection_info(self) -> str:
        if not self._connected:
            return "Disconnected"
        return getattr(self, "_port_info", "Serial")

    def connect(self, port: str = "", baudrate: int = 9600,
                timeout: float = 1.0) -> tuple[bool, str]:
        """Connect to device. Returns (success, info_string)."""
        if not port:
            return False, "No port specified"
        try:
            dev = _SerialSP3103(port, baudrate, timeout, log=self._log)
            idn = dev.query("*IDN?")
            if not idn:
                dev.close()
                return False, "No response to *IDN? — wrong port or device not ready"
            dev.write("SYST:REM")
            with self._io_lock:
                self._dev = dev
            self._port_info = f"{port}  {baudrate} bps  |  {idn}"
            self._connected = True
            return True, self._port_info
        except Exception as e:
            return False, str(e)

    def connect_with_config(self, cfg) -> tuple[bool, str]:
        """Connect using a SerialConfig dataclass (same type used by UartConfigDialog)."""
        return self.connect(port=cfg.port, baudrate=cfg.baudrate, timeout=cfg.timeout)

    def disconnect(self) -> None:
        self._connected = False
        self.stop_polling()
        # take the I/O lock so the port is never closed mid-transaction
        with self._io_lock:
            if self._dev:
                try:
                    self._dev.close()
                except Exception:
                    pass
                self._dev = None

    # ── commands ──────────────────────────────────────────────────────────────

    def set_voltage(self, volts: float) -> None:
        volts = max(0.0, min(self.MAX_VOLTAGE, float(volts)))
        self._write_cmd(f"VOLT {volts:.2f}")
        with self._state_lock:
            self._state.set_voltage = volts

    def set_current(self, amps: float) -> None:
        amps = max(0.0, min(self.MAX_CURRENT, float(amps)))
        self._write_cmd(f"CURR {amps:.3f}")
        with self._state_lock:
            self._state.set_current = amps

    def set_output(self, on: bool) -> None:
        self._write_cmd("OUTP ON" if on else "OUTP OFF")
        with self._state_lock:
            self._state.output_on = on

    def measure(self) -> PSUState:
        """Query the instrument (blocking serial I/O) and update the cached
        state.  Runs on the poll thread in normal operation; UI code should
        read get_state() instead."""
        if not self._dev:
            return self.get_state()
        try:
            vraw = self._query_cmd("MEAS:VOLT?")
            iraw = self._query_cmd("MEAS:CURR?")
            outp = self._query_cmd("OUTP?")
        except Exception:
            vraw = iraw = outp = None
        with self._state_lock:
            # an empty/garbled response is "no data" (None), never 0
            self._state.meas_voltage = _safe_float(vraw, round_to=4)
            self._state.meas_current = _safe_float(iraw, round_to=4)
            if outp is not None:
                self._state.output_on = outp.strip() == "1"
            return replace(self._state)

    def get_state(self) -> PSUState:
        """Thread-safe snapshot of the last known state — no serial I/O."""
        with self._state_lock:
            return replace(self._state)

    # ── config helpers (for profile save/restore) ─────────────────────────────

    def get_config(self) -> dict:
        with self._state_lock:
            return {
                "set_voltage": self._state.set_voltage,
                "set_current": self._state.set_current,
            }

    def apply_config(self, cfg: dict) -> None:
        if "set_voltage" in cfg:
            self.set_voltage(cfg["set_voltage"])
        if "set_current" in cfg:
            self.set_current(cfg["set_current"])

    # ── polling ───────────────────────────────────────────────────────────────

    def add_poll_callback(self, cb: Callable[[PSUState], None]) -> None:
        self._poll_cbs.append(cb)

    def start_polling(self, interval: float = 0.5) -> None:
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, args=(interval,), daemon=True)
        self._poll_thread.start()

    def stop_polling(self) -> None:
        self._stop_event.set()
        if self._poll_thread:
            self._poll_thread.join(timeout=2.0)
            self._poll_thread = None

    def _poll_loop(self, interval: float) -> None:
        while not self._stop_event.is_set():
            if self._connected:
                state = self.measure()
                for cb in list(self._poll_cbs):
                    try:
                        cb(state)
                    except Exception:
                        pass
            self._stop_event.wait(interval)
