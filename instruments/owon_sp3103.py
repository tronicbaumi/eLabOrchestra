"""
OWON SP3103 DC Power Supply driver.

Communication: USB-CDC serial (SCPI-like protocol).
Default: 9600 baud, 8N1.

Command set used by this driver:
  *IDN?           → identify string
  VSET1:<v>       → set voltage channel 1 (0.00 – 30.00 V)
  ISET1:<i>       → set current limit channel 1 (0.000 – 3.000 A)
  VSET1?          → read set voltage
  ISET1?          → read set current limit
  OUT1            → enable output
  OUT0            → disable output
  OUTP?           → read output state ("1" or "0")
  VOUT1?          → read actual output voltage
  IOUT1?          → read actual output current

Power is computed locally from V × I.
"""

from __future__ import annotations

import time
import random
import threading
from dataclasses import dataclass, field
from typing import Optional, Callable


# ── data classes ─────────────────────────────────────────────────────────────

@dataclass
class PSUState:
    """Snapshot of one measurement cycle."""
    set_voltage:  float = 0.0     # V  (programmed)
    set_current:  float = 0.0     # A  (programmed limit)
    meas_voltage: float = 0.0     # V  (actual)
    meas_current: float = 0.0     # A  (actual)
    output_on:    bool  = False
    overload:     bool  = False   # CV/CC limit hit

    @property
    def power(self) -> float:
        return round(self.meas_voltage * self.meas_current, 4)


# ── simulated device ─────────────────────────────────────────────────────────

class _SimulatedSP3103:
    """Fake SP3103 that returns plausible values without hardware."""

    def __init__(self) -> None:
        self._vset  = 5.0
        self._iset  = 1.0
        self._out   = False
        self._t0    = time.time()

    def write(self, cmd: str) -> None:
        cmd = cmd.strip()
        if cmd.startswith("VSET1:"):
            self._vset = float(cmd[6:])
        elif cmd.startswith("ISET1:"):
            self._iset = float(cmd[6:])
        elif cmd == "OUT1":
            self._out = True
        elif cmd == "OUT0":
            self._out = False

    def query(self, cmd: str) -> str:
        cmd = cmd.strip()
        if cmd == "*IDN?":
            return "OWON,SP3103,SIM000001,V1.0"
        if cmd == "VSET1?":
            return f"{self._vset:.2f}"
        if cmd == "ISET1?":
            return f"{self._iset:.3f}"
        if cmd == "OUTP?":
            return "1" if self._out else "0"
        if cmd == "VOUT1?":
            if not self._out:
                return "0.000"
            ripple = 0.02 * (random.random() - 0.5)
            return f"{max(0.0, self._vset + ripple):.3f}"
        if cmd == "IOUT1?":
            if not self._out:
                return "0.000"
            load  = min(self._iset, self._vset / 5.0)  # simulate 5 Ω load
            ripple = 0.005 * (random.random() - 0.5)
            return f"{max(0.0, load + ripple):.3f}"
        return "0"

    def close(self) -> None:
        pass


# ── real serial connection ────────────────────────────────────────────────────

class _SerialSP3103:
    def __init__(self, port: str, baudrate: int = 9600, timeout: float = 1.0):
        import serial
        self._ser = serial.Serial(
            port=port, baudrate=baudrate, bytesize=8,
            parity='N', stopbits=1, timeout=timeout,
        )
        time.sleep(0.2)

    def write(self, cmd: str) -> None:
        self._ser.write((cmd.strip() + "\n").encode())
        time.sleep(0.05)

    def query(self, cmd: str) -> str:
        self.write(cmd)
        return self._ser.readline().decode(errors="replace").strip()

    def close(self) -> None:
        self._ser.close()


# ── public driver ─────────────────────────────────────────────────────────────

class OwonSP3103:
    """
    Driver for the OWON SP3103 30 V / 3 A single-channel DC power supply.

    Usage::
        psu = OwonSP3103(simulate=True)
        ok, info = psu.connect()
        psu.set_voltage(12.0)
        psu.set_current(0.5)
        psu.set_output(True)
        state = psu.measure()
        print(state.meas_voltage, state.meas_current, state.power)
        psu.disconnect()
    """

    MAX_VOLTAGE = 30.0
    MAX_CURRENT = 3.0

    def __init__(self, simulate: bool = False) -> None:
        self._simulate  = simulate
        self._dev       = None
        self._lock      = threading.Lock()
        self._connected = False
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._state     = PSUState()
        self._poll_cbs: list[Callable[[PSUState], None]] = []

    # ── connection ────────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def is_simulated(self) -> bool:
        return self._simulate

    @property
    def connection_info(self) -> str:
        if not self._connected:
            return "Disconnected"
        if self._simulate:
            return "Simulation (no hardware)"
        return getattr(self, "_port_info", "Serial")

    def connect(self, port: str = "", baudrate: int = 9600,
                timeout: float = 1.0) -> tuple[bool, str]:
        """Connect to device. Returns (success, info_string)."""
        if port and not self._simulate:
            try:
                self._dev = _SerialSP3103(port, baudrate, timeout)
                idn = self._dev.query("*IDN?")
                self._port_info = f"{port}  {baudrate} bps  |  {idn}"
                self._simulate  = False
                self._connected = True
                return True, self._port_info
            except Exception as e:
                self._dev = None
                if not self._simulate:
                    return False, str(e)

        # fall through to simulation
        self._dev      = _SimulatedSP3103()
        self._simulate = True
        self._connected = True
        return True, "Simulation mode (OWON SP3103)"

    def connect_with_config(self, cfg) -> tuple[bool, str]:
        """Connect using a SerialConfig dataclass (same type used by UartConfigDialog)."""
        if cfg.simulate or not cfg.port:
            self._simulate = True
            return self.connect()
        ok, info = self.connect(port=cfg.port, baudrate=cfg.baudrate, timeout=cfg.timeout)
        if not ok and cfg.simulate:
            self._simulate = True
            return self.connect()
        return ok, info

    def disconnect(self) -> None:
        self.stop_polling()
        if self._dev:
            try:
                self._dev.close()
            except Exception:
                pass
            self._dev = None
        self._connected = False

    # ── commands ──────────────────────────────────────────────────────────────

    def set_voltage(self, volts: float) -> None:
        volts = max(0.0, min(self.MAX_VOLTAGE, float(volts)))
        with self._lock:
            if self._dev:
                self._dev.write(f"VSET1:{volts:.2f}")
        self._state.set_voltage = volts

    def set_current(self, amps: float) -> None:
        amps = max(0.0, min(self.MAX_CURRENT, float(amps)))
        with self._lock:
            if self._dev:
                self._dev.write(f"ISET1:{amps:.3f}")
        self._state.set_current = amps

    def set_output(self, on: bool) -> None:
        with self._lock:
            if self._dev:
                self._dev.write("OUT1" if on else "OUT0")
        self._state.output_on = on

    def measure(self) -> PSUState:
        with self._lock:
            if not self._dev:
                return PSUState()
            try:
                vout = float(self._dev.query("VOUT1?") or 0)
                iout = float(self._dev.query("IOUT1?") or 0)
                outp = self._dev.query("OUTP?").strip() == "1"
                self._state.meas_voltage = round(vout, 4)
                self._state.meas_current = round(iout, 4)
                self._state.output_on    = outp
            except Exception:
                pass
        return PSUState(
            set_voltage  = self._state.set_voltage,
            set_current  = self._state.set_current,
            meas_voltage = self._state.meas_voltage,
            meas_current = self._state.meas_current,
            output_on    = self._state.output_on,
        )

    # ── config helpers (for profile save/restore) ─────────────────────────────

    def get_config(self) -> dict:
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
