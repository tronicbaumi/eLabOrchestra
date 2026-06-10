"""
Magtrol DSP7000 High-Speed Programmable Dynamometer Controller driver.

Serial protocol:
  - RS-232, default 9600 baud, 8N1, no flow control
  - Commands terminated with CR+LF  (\r\n)
  - Responses terminated with CR+LF
  - Two independent channels (1 and 2)

Key command groups:
  OD1/OD2     – read speed/torque/direction data string
  N1,xx / Q1,xx – set speed / torque setpoint
  I1,xx       – set brake current output (%)
  R1          – reset channel (brake off, free run)
  PU1 / PD1   – ramp up / ramp down
  PR1         – abort ramp
  ALS1 / ALT1 / ALP1 – alarm setpoints
  ALL1,0/1    – enable / disable all alarms
  FRZ1,0/1   – freeze / unfreeze PID
  TS1 / TR1   – tare on / off
  SAVE,#      – save config to NV memory
  STAT        – read 32-bit status flags
  *IDN?       – identification string
"""

from __future__ import annotations

import re
import math
import time
import threading
from dataclasses import dataclass, field
from typing import Optional, Callable

try:
    import serial
    _SERIAL_OK = True
except ImportError:
    _SERIAL_OK = False

# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class ChannelData:
    """Live measurement data for one channel.
    None = no valid reading received (never reported as 0)."""
    speed:     float | None = None   # rpm
    torque:    float | None = None   # Nm (or configured units)
    power:     float | None = None   # W  (computed: 2π·n/60 · T)
    direction: str   = ""            # "R"=CW, "L"=CCW, "A"=alarm


@dataclass
class DSP7000State:
    """Snapshot of instrument state (both channels)."""
    channels:         list[ChannelData] = field(default_factory=lambda: [ChannelData(), ChannelData()])
    speed_setpoint:   list[float]       = field(default_factory=lambda: [0.0, 0.0])
    torque_setpoint:  list[float]       = field(default_factory=lambda: [0.0, 0.0])
    current_output:   list[float]       = field(default_factory=lambda: [0.0, 0.0])
    brake_on:         list[bool]        = field(default_factory=lambda: [False, False])
    speed_alarm:      list[float]       = field(default_factory=lambda: [99999.0, 99999.0])
    torque_alarm:     list[float]       = field(default_factory=lambda: [9999.0, 9999.0])
    power_alarm:      list[float]       = field(default_factory=lambda: [99.9, 99.9])
    alarms_enabled:   list[bool]        = field(default_factory=lambda: [True, True])
    pid_frozen:       list[bool]        = field(default_factory=lambda: [False, False])
    tare_active:      list[bool]        = field(default_factory=lambda: [False, False])
    active_channel:   int               = 1   # 1 or 2

    def ch(self, channel: int) -> ChannelData:
        return self.channels[channel - 1]


# ── Real serial transport ──────────────────────────────────────────────────────

class _SerialDSP7000:
    """RS-232 transport for the real Magtrol DSP7000."""

    _TIMEOUT = 1.0

    def __init__(self, cfg, log=None) -> None:
        self._cfg  = cfg
        self._port = None
        self._lock = threading.Lock()
        self._log  = log

    def open(self) -> None:
        if not _SERIAL_OK:
            raise RuntimeError("pyserial not installed")
        self._port = serial.Serial(
            port     = self._cfg.port,
            baudrate = self._cfg.baudrate,
            bytesize = 8,
            parity   = serial.PARITY_NONE,
            stopbits = 1,
            timeout  = self._TIMEOUT,
        )

    def close(self) -> None:
        # taken under the transport lock so an in-flight query/send always
        # completes before the port disappears
        with self._lock:
            if self._port and self._port.is_open:
                self._port.close()
            self._port = None

    def _send(self, cmd: str) -> None:
        self._port.write((cmd + "\r\n").encode())

    def _recv(self) -> str:
        raw = self._port.readline()
        return raw.decode(errors="replace").strip()

    def query(self, cmd: str) -> str:
        with self._lock:
            if self._port is None:
                return ""
            self._send(cmd)
            if self._log:
                self._log("TX", cmd)
            resp = self._recv()
            if self._log:
                self._log("RX", resp)
            return resp

    def send(self, cmd: str) -> None:
        with self._lock:
            if self._port is None:
                return
            self._send(cmd)
            if self._log:
                self._log("TX", cmd)
            # small instruments echo or reply — drain any response
            time.sleep(0.02)
            if self._port.in_waiting:
                self._port.read(self._port.in_waiting)


# ── OD response parser ─────────────────────────────────────────────────────────

_OD_RE = re.compile(r"S\s*([\d.+-]+)\s*T\s*([\d.+-]+)\s*([RLA])", re.IGNORECASE)

def _parse_od(response: str) -> tuple[float | None, float | None, str]:
    """Parse OD response: 'SxxxxxxTxxxxxR' → (speed, torque, direction).
    A missing/garbled response yields (None, None, '') — never zeros."""
    m = _OD_RE.search(response or "")
    if m:
        try:
            return float(m.group(1)), float(m.group(2)), m.group(3).upper()
        except ValueError:
            pass
    return None, None, ""


# ── Public driver ──────────────────────────────────────────────────────────────

class MagtrolDSP7000:
    """
    Driver for the Magtrol DSP7000 High-Speed Programmable Dynamometer Controller.

    Usage:
        dsp = MagtrolDSP7000()
        dsp.connect_with_config(SerialConfig(port="COM3", baudrate=9600))
        data = dsp.read_channel(1)        # ChannelData
        dsp.set_speed(1, 1500.0)          # 1500 rpm, ch1
        dsp.reset_channel(1)              # free run
    """

    def __init__(self) -> None:
        self._real:    Optional[_SerialDSP7000] = None
        self._cfg      = None
        self._log_cb   = None
        self._poll_thread:  Optional[threading.Thread] = None
        self._poll_stop:    threading.Event = threading.Event()
        self._state:        DSP7000State = DSP7000State()
        self._lock:         threading.Lock = threading.Lock()
        self._callbacks:    list[Callable[[DSP7000State], None]] = []

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
        return self._real is not None

    def connect_with_config(self, cfg) -> tuple[bool, str]:
        """Connect using a SerialConfig. Returns (ok, info_string)."""
        self.disconnect()
        self._cfg = cfg
        if not cfg.port:
            return False, "No port specified"
        try:
            transport = _SerialDSP7000(cfg, log=self._log)
            transport.open()
            idn = transport.query("*IDN?")
            if not idn:
                transport.close()
                return False, "No response to *IDN? — wrong port or device not ready"
            self._real = transport
            return True, f"{cfg.port} @ {cfg.baudrate} bps  |  {idn}"
        except Exception as e:
            self._real = None
            return False, str(e)

    def disconnect(self) -> None:
        self.stop_polling()
        # unpublish first so no new commands start, then close (close waits
        # for any in-flight transaction via the transport lock)
        real, self._real = self._real, None
        if real:
            try:
                real.close()
            except Exception:
                pass

    def get_config(self):
        return self._cfg

    def apply_config(self, cfg) -> None:
        self.connect_with_config(cfg)

    def get_serial_config(self):
        return self._cfg

    # ── polling ───────────────────────────────────────────────────────────────

    def add_state_callback(self, cb: Callable[[DSP7000State], None]) -> None:
        self._callbacks.append(cb)

    def start_polling(self, interval: float = 0.5) -> None:
        self.stop_polling()
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, args=(interval,), daemon=True)
        self._poll_thread.start()

    def stop_polling(self) -> None:
        self._poll_stop.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=2.0)
        self._poll_thread = None

    def _poll_loop(self, interval: float) -> None:
        while not self._poll_stop.is_set():
            try:
                for ch in (1, 2):
                    cd = self.read_channel(ch)
                    with self._lock:
                        self._state.channels[ch - 1] = cd
                state = self.get_state()
                for cb in list(self._callbacks):
                    try:
                        cb(state)
                    except Exception:
                        pass
            except Exception:
                pass
            self._poll_stop.wait(interval)

    def get_state(self) -> DSP7000State:
        """Thread-safe snapshot of the last known state — no serial I/O.
        ChannelData objects are replaced wholesale by the poll thread, never
        mutated in place, so sharing references is safe."""
        with self._lock:
            s = self._state
            return DSP7000State(
                channels        = list(s.channels),
                speed_setpoint  = list(s.speed_setpoint),
                torque_setpoint = list(s.torque_setpoint),
                current_output  = list(s.current_output),
                brake_on        = list(s.brake_on),
                speed_alarm     = list(s.speed_alarm),
                torque_alarm    = list(s.torque_alarm),
                power_alarm     = list(s.power_alarm),
                alarms_enabled  = list(s.alarms_enabled),
                pid_frozen      = list(s.pid_frozen),
                tare_active     = list(s.tare_active),
                active_channel  = s.active_channel,
            )

    # ── measurement ───────────────────────────────────────────────────────────

    def identify(self) -> str:
        return self._query_cmd("*IDN?")

    def read_channel(self, ch: int = 1) -> ChannelData:
        """Query OD1 or OD2 and return ChannelData."""
        assert ch in (1, 2)
        real = self._real
        if real:
            resp = real.query(f"OD{ch}")
            speed, torque, direction = _parse_od(resp)
            power = ((2.0 * math.pi * speed / 60.0) * torque
                     if speed is not None and torque is not None else None)
            return ChannelData(speed=speed, torque=torque,
                               power=power, direction=direction)
        return ChannelData()

    def read_status(self) -> int:
        real = self._real
        if real:
            try:
                resp = real.query("STAT")
                return int(resp.strip(), 16)
            except Exception:
                return 0
        return 0


    # ── locked command primitives ─────────────────────────────────────────────
    # A local reference is taken before use so a concurrent disconnect()
    # (which sets self._real = None) can never cause an AttributeError
    # between the check and the call.  The transport serialises port access
    # with its own internal lock.

    def _send_cmd(self, cmd: str) -> None:
        real = self._real
        if real:
            real.send(cmd)

    def _query_cmd(self, cmd: str) -> str:
        real = self._real
        if real:
            return real.query(cmd)
        return ""

    # ── speed control ─────────────────────────────────────────────────────────

    def set_speed(self, ch: int, rpm: float) -> None:
        self._send_cmd(f"N{ch},{rpm:.2f}")

    def reset_speed(self, ch: int) -> None:
        """Release speed control (free run, brake off)."""
        self._send_cmd(f"N{ch}")

    def set_speed_pid(self, ch: int, p: int = 50, i: int = 10, d: int = 0) -> None:
        self._send_cmd(f"NP{ch},{p}")
        self._send_cmd(f"NI{ch},{i}")
        self._send_cmd(f"ND{ch},{d}")

    # ── torque control ────────────────────────────────────────────────────────

    def set_torque(self, ch: int, torque: float) -> None:
        self._send_cmd(f"Q{ch},{torque:.2f}")

    def reset_torque(self, ch: int) -> None:
        self._send_cmd(f"Q{ch}")

    def set_torque_pid(self, ch: int, p: int = 50, i: int = 10, d: int = 0) -> None:
        self._send_cmd(f"QP{ch},{p}")
        self._send_cmd(f"QI{ch},{i}")
        self._send_cmd(f"QD{ch},{d}")

    # ── current / brake output ────────────────────────────────────────────────

    def set_current(self, ch: int, pct: float) -> None:
        """Set brake current output 0–99.99 %."""
        pct = max(0.0, min(99.99, pct))
        self._send_cmd(f"I{ch},{pct:.2f}")

    def reset_current(self, ch: int) -> None:
        self._send_cmd(f"I{ch}")

    # ── ramp ──────────────────────────────────────────────────────────────────

    def ramp_up(self, ch: int, linear: bool = True, rate_or_time: float = 100.0) -> None:
        """PU command: ramp up to current speed setpoint."""
        mode = 0 if linear else 1
        self._send_cmd(f"PU{ch},{mode},{rate_or_time:.2f}")

    def ramp_down(self, ch: int, linear: bool = True, rate_or_time: float = 100.0) -> None:
        """PD command: ramp down to 0."""
        mode = 0 if linear else 1
        self._send_cmd(f"PD{ch},{mode},{rate_or_time:.2f}")

    def abort_ramp(self, ch: int) -> None:
        """PR command: halt ramp, return to free run."""
        self._send_cmd(f"PR{ch}")

    # ── channel reset ─────────────────────────────────────────────────────────

    def reset_channel(self, ch: int) -> None:
        """R1/R2: manual control on, brake off."""
        self._send_cmd(f"R{ch}")

    # ── alarms ────────────────────────────────────────────────────────────────

    def set_speed_alarm(self, ch: int, rpm: float) -> None:
        self._send_cmd(f"ALS{ch},{rpm:.2f}")

    def set_torque_alarm(self, ch: int, val: float) -> None:
        self._send_cmd(f"ALT{ch},{val:.2f}")

    def set_power_alarm(self, ch: int, kw: float) -> None:
        self._send_cmd(f"ALP{ch},{kw:.2f}")

    def set_alarms(self, ch: int, enable: bool) -> None:
        self._send_cmd(f"ALL{ch},{1 if enable else 0}")

    # ── PID / misc ────────────────────────────────────────────────────────────

    def freeze_pid(self, ch: int, freeze: bool) -> None:
        self._send_cmd(f"FRZ{ch},{1 if freeze else 0}")

    def tare(self, ch: int, enable: bool) -> None:
        self._send_cmd(f"TS{ch}" if enable else f"TR{ch}")

    def save(self, ch: int) -> None:
        self._send_cmd(f"SAVE,{ch}")
