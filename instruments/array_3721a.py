"""
Array 3721A Programmable Electronic Load driver.

Communication: RS232 / USB-CDC (SCPI protocol).
Default RS232: 9600 baud, 8N1, hardware flow control ON.
USB: no flow control needed.

Command set (per 372x Series manual, Chapter 5):
  *IDN?                   → identify string
  SYSTem:REMote           → enter remote control mode
  SYSTem:LOCal            → return to local control
  MODE <mode>             → set operating mode
                            (CCL|CCH|CV|CRL|CRM|CRH|CPV|CPC)
  MODE?                   → query current mode
  CURRent <NRf>           → set CC level (A)
  CURRent?                → query CC set level
  VOLTage <NRf>           → set CV level (V)
  VOLTage?                → query CV set level
  RESistance <NRf>        → set CR level (Ω)
  RESistance?             → query CR set level
  POWer <NRf>             → set CP level (W)
  POWer?                  → query CP set level
  INPut ON|OFF            → enable / disable load input
  INPut?                  → query input state (0 / 1)
  MEASure:VOLTage?        → measured terminal voltage (V)
  MEASure:CURRent?        → measured terminal current (A)

Array 3721A ratings: 80 V / 40 A / 400 W
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, replace
from typing import Optional, Callable


# ── operating modes ───────────────────────────────────────────────────────────

class ELoadMode:
    CCL = "CCL"   # Constant Current Low range  (0–4 A,  better resolution)
    CCH = "CCH"   # Constant Current High range (0–40 A)
    CV  = "CV"    # Constant Voltage            (0–80 V)
    CRL = "CRL"   # Constant Resistance Low     (0.05–10 Ω)
    CRM = "CRM"   # Constant Resistance Medium  (2–100 Ω)
    CRH = "CRH"   # Constant Resistance High    (20–10000 Ω)
    CPV = "CPV"   # Constant Power (voltage src)(0–400 W)
    CPC = "CPC"   # Constant Power (current src)(0–400 W)

    ALL = [CCL, CCH, CV, CRL, CRM, CRH, CPV, CPC]


# ── state snapshot ────────────────────────────────────────────────────────────

def _safe_float(raw: str | None, round_to: int | None = None) -> float | None:
    """Parse an instrument response; None for empty/unparseable (never 0)."""
    if not raw:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return round(v, round_to) if round_to is not None else v


@dataclass
class ELoadState:
    """Snapshot of one measurement / status cycle.

    meas_voltage / meas_current are None until a valid reading has been
    received — a missing response is never reported as 0.0."""
    mode:         str   = ELoadMode.CCH
    set_value:    float = 0.0            # A / V / Ω / W depending on mode
    meas_voltage: float | None = None    # V  (actual terminal)
    meas_current: float | None = None    # A  (actual terminal)
    input_on:     bool  = False

    @property
    def power(self) -> float | None:
        if self.meas_voltage is None or self.meas_current is None:
            return None
        return round(self.meas_voltage * self.meas_current, 4)


# ── serial transport ──────────────────────────────────────────────────────────

class _SerialArray3721A:
    def __init__(self, port: str, baudrate: int = 9600, timeout: float = 1.0,
                 rtscts: bool = False, log=None):
        import serial
        self._ser = serial.Serial(
            port=port, baudrate=baudrate, bytesize=8,
            parity='N', stopbits=1, timeout=timeout,
            rtscts=rtscts,
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

class Array3721A:
    """
    Driver for the Array 3721A 80 V / 40 A / 400 W programmable electronic load.

    Usage::
        load = Array3721A()
        ok, info = load.connect(port="COM4")
        load.set_mode(ELoadMode.CCH)
        load.set_level(2.5)          # 2.5 A in CC mode
        load.set_input(True)         # enable load input
        state = load.measure()
        print(state.meas_voltage, state.meas_current, state.power)
        load.disconnect()
    """

    MAX_VOLTAGE    = 80.0
    MAX_CURRENT    = 40.0
    MAX_CURRENT_L  = 4.0    # CCL range
    MAX_POWER      = 400.0

    def __init__(self) -> None:
        self._dev        = None
        # _io_lock serialises serial transactions (held per command);
        # _state_lock guards the cached _state and is never held during I/O.
        self._io_lock    = threading.Lock()
        self._state_lock = threading.Lock()
        self._connected  = False
        self._log_cb     = None
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._state      = ELoadState()
        self._poll_cbs:  list[Callable[[ELoadState], None]] = []

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

    # ── logging ───────────────────────────────────────────────────────────────

    def set_log_callback(self, cb) -> None:
        self._log_cb = cb

    def _log(self, direction: str, text: str) -> None:
        if self._log_cb:
            try:
                self._log_cb(direction, text)
            except Exception:
                pass

    # ── connection ────────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def connection_info(self) -> str:
        if not self._connected:
            return "Disconnected"
        return getattr(self, "_port_info", "Serial")

    def connect(self, port: str = "", baudrate: int = 9600,
                timeout: float = 1.0, rtscts: bool = False) -> tuple[bool, str]:
        """Connect via RS232 or USB. Returns (success, info_string)."""
        if not port:
            return False, "No port specified"
        try:
            dev = _SerialArray3721A(
                port, baudrate, timeout, rtscts=rtscts, log=self._log)
            idn = dev.query("*IDN?")
            if not idn:
                dev.close()
                return False, "No response to *IDN? — wrong port or device not ready"
            dev.write("SYSTem:REMote")
            # sync mode state from device before publishing the connection
            try:
                m = dev.query("MODE?").strip().upper()
                if m in ELoadMode.ALL:
                    with self._state_lock:
                        self._state.mode = m
            except Exception:
                pass
            with self._io_lock:
                self._dev = dev
            self._port_info = f"{port}  {baudrate} bps  |  {idn}"
            self._connected = True
            return True, self._port_info
        except Exception as e:
            return False, str(e)

    def connect_with_config(self, cfg) -> tuple[bool, str]:
        """Connect using a SerialConfig dataclass."""
        return self.connect(port=cfg.port, baudrate=cfg.baudrate,
                            timeout=cfg.timeout)

    def disconnect(self) -> None:
        self._connected = False
        self.stop_polling()
        # take the I/O lock so the port is never closed mid-transaction
        with self._io_lock:
            if self._dev:
                try:
                    self._dev.write("SYSTem:LOCal")
                    self._dev.close()
                except Exception:
                    pass
                self._dev = None

    # ── commands ──────────────────────────────────────────────────────────────

    def set_mode(self, mode: str) -> None:
        """Set operating mode — use ELoadMode constants."""
        mode = mode.upper()
        if mode not in ELoadMode.ALL:
            raise ValueError(f"Unknown mode: {mode!r}")
        self._write_cmd(f"MODE {mode}")
        with self._state_lock:
            self._state.mode = mode

    def set_level(self, value: float) -> None:
        """Set the active set-point for the current mode."""
        with self._state_lock:
            mode = self._state.mode
        if mode in (ELoadMode.CCL, ELoadMode.CCH):
            limit = self.MAX_CURRENT_L if mode == ELoadMode.CCL else self.MAX_CURRENT
            value = max(0.0, min(limit, float(value)))
            cmd = f"CURRent {value:.4f}"
        elif mode == ELoadMode.CV:
            value = max(0.0, min(self.MAX_VOLTAGE, float(value)))
            cmd = f"VOLTage {value:.4f}"
        elif mode in (ELoadMode.CRL, ELoadMode.CRM, ELoadMode.CRH):
            value = max(0.0, float(value))
            cmd = f"RESistance {value:.4f}"
        elif mode in (ELoadMode.CPV, ELoadMode.CPC):
            value = max(0.0, min(self.MAX_POWER, float(value)))
            cmd = f"POWer {value:.4f}"
        else:
            return
        if not self._write_cmd(cmd):
            return
        with self._state_lock:
            self._state.set_value = value

    def set_input(self, on: bool) -> None:
        """Enable or disable the load input."""
        self._write_cmd("INPut ON" if on else "INPut OFF")
        with self._state_lock:
            self._state.input_on = on

    def measure(self) -> ELoadState:
        """Query measured V, I and input state (blocking serial I/O) and
        update the cached state.  Runs on the poll thread in normal
        operation; UI code should read get_state() instead."""
        if not self._dev:
            return self.get_state()
        try:
            vraw = self._query_cmd("MEASure:VOLTage?")
            iraw = self._query_cmd("MEASure:CURRent?")
            inp  = self._query_cmd("INPut?")
        except Exception:
            vraw = iraw = inp = None
        with self._state_lock:
            # an empty/garbled response is "no data" (None), never 0
            self._state.meas_voltage = _safe_float(vraw, round_to=4)
            self._state.meas_current = _safe_float(iraw, round_to=4)
            if inp is not None:
                self._state.input_on = inp.strip() in ("1", "ON")
            return replace(self._state)

    def get_state(self) -> ELoadState:
        """Thread-safe snapshot of the last known state — no serial I/O."""
        with self._state_lock:
            return replace(self._state)

    # ── config helpers ────────────────────────────────────────────────────────

    def get_config(self) -> dict:
        with self._state_lock:
            return {
                "mode":      self._state.mode,
                "set_value": self._state.set_value,
            }

    def apply_config(self, cfg: dict) -> None:
        if "mode" in cfg:
            self.set_mode(cfg["mode"])
        if "set_value" in cfg:
            self.set_level(cfg["set_value"])

    # ── polling ───────────────────────────────────────────────────────────────

    def add_poll_callback(self, cb: Callable[[ELoadState], None]) -> None:
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
