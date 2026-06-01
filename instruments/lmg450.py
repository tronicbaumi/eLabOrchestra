"""
ZES Zimmer LMG450 four-channel power analyser driver.

Communication: RS-232 (USB-CDC) – default 57600 baud, 8-N-1, no flow control.
Protocol: SCPI-style SHORT-form text commands, terminated with CR+LF.
Responses are terminated with CR+LF; numeric values are plain ASCII floats.

Channel model
─────────────
The LMG450 has up to 4 independent measurement channels.
All single-channel queries operate on the *active channel* (ACHAN n).
Multi-channel aggregate results (PSUM, QSUM, SSUM, WPSUM, AHPSUM) are
available regardless of the active channel.

Key SHORT-form commands used
────────────────────────────
  *IDN?          identify
  *RST           reset
  ACHAN <n>      select active channel (1-4)
  CONT <ON|OFF>  continuous-measurement mode
  AVRG <n>       averaging count (1-128)

Single-channel measurements (operate on active channel):
  UTRMS?         true-RMS voltage (V)
  ITRMS?         true-RMS current (A)
  P?             active power (W)
  Q?             reactive power (VAr)
  S?             apparent power (VA)
  LAMDA?         power factor  λ = P/S  (signed, -1…+1)
  PHI?           phase angle (°)
  FU?            voltage frequency (Hz)
  UBDC?          DC voltage component (V)
  IBDC?          DC current component (A)

Integration (active channel):
  WH?            energy (Wh)
  AH?            charge (Ah)
  STRTITIME      start integration timer
  STPTITIME      stop  integration timer
  RSTITIME       reset integration (zero Wh/Ah)
  ITIME?         elapsed integration time (s)

Harmonics (active channel, n = 1…50):
  UHAR<n>?       nth voltage harmonic amplitude (V)
  IHAR<n>?       nth current harmonic amplitude (A)
  UTHD?          total voltage harmonic distortion (%)
  ITHD?          total current harmonic distortion (%)

Multi-channel aggregates:
  PSUM?          sum of active powers (W)
  QSUM?          sum of reactive powers (VAr)
  SSUM?          sum of apparent powers (VA)
  WPSUM?         sum of energies (Wh)
  AHPSUM?        sum of charges (Ah)
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional


# ── data classes ──────────────────────────────────────────────────────────────

@dataclass
class ChannelMeasurement:
    """One snapshot for a single LMG450 channel."""
    channel:     int   = 1
    # basic
    urms:        float = 0.0    # V  – true RMS voltage
    irms:        float = 0.0    # A  – true RMS current
    p:           float = 0.0    # W  – active power
    q:           float = 0.0    # VAr – reactive power
    s:           float = 0.0    # VA  – apparent power
    lamda:       float = 0.0    # –   – power factor (signed)
    phi:         float = 0.0    # °   – phase angle
    fu:          float = 50.0   # Hz  – frequency
    ubdc:        float = 0.0    # V   – DC voltage
    ibdc:        float = 0.0    # A   – DC current
    # integration
    wh:          float = 0.0    # Wh  – energy
    ah:          float = 0.0    # Ah  – charge
    itime:       float = 0.0    # s   – elapsed integration time
    # harmonics
    u_harmonics: list[float] = field(default_factory=list)   # [0]=DC,  [1]=fund, …
    i_harmonics: list[float] = field(default_factory=list)
    uthd:        float = 0.0    # % voltage THD
    ithd:        float = 0.0    # % current THD


@dataclass
class AggregateMeasurement:
    """Sum values across all active channels."""
    psum:   float = 0.0   # W
    qsum:   float = 0.0   # VAr
    ssum:   float = 0.0   # VA
    wpsum:  float = 0.0   # Wh
    ahpsum: float = 0.0   # Ah


@dataclass
class LMG450State:
    channels:    list[ChannelMeasurement] = field(default_factory=list)
    aggregate:   AggregateMeasurement     = field(default_factory=AggregateMeasurement)
    active_chan: int = 1


# ── real serial transport ─────────────────────────────────────────────────────

class _SerialLMG450:
    def __init__(self, port: str, baudrate: int = 57600, timeout: float = 2.0,
                 log=None):
        import serial
        self._ser = serial.Serial(
            port=port, baudrate=baudrate, bytesize=8,
            parity="N", stopbits=1, timeout=timeout,
            xonxoff=False, rtscts=False, dsrdtr=False,
        )
        self._log = log
        time.sleep(0.3)
        self._ser.flushInput()

    def write(self, cmd: str) -> None:
        cmd = cmd.strip()
        self._ser.write((cmd + "\r\n").encode())
        if self._log:
            self._log("TX", cmd)
        time.sleep(0.05)

    def query(self, cmd: str) -> str:
        self._ser.flushInput()
        cmd = cmd.strip()
        self._ser.write((cmd + "\r\n").encode())
        if self._log:
            self._log("TX", cmd)
        time.sleep(0.05)
        raw = self._ser.readline().decode(errors="replace").strip()
        if self._log:
            self._log("RX", raw)
        return raw

    def close(self) -> None:
        self._ser.close()


# ── public driver ─────────────────────────────────────────────────────────────

class LMG450:
    """
    Driver for the ZES Zimmer LMG450 four-channel power analyser.

    Quick-start::
        lmg = LMG450()
        ok, info = lmg.connect(port="COM4", baudrate=57600)
        lmg.select_channel(1)
        m = lmg.measure_channel()
        print(m.urms, m.p, m.lamda)
        agg = lmg.measure_aggregate()
        print(agg.psum)
        lmg.disconnect()
    """

    N_CHANNELS   = 4
    MAX_HARMONIC = 50
    DEFAULT_BAUD = 57600

    def __init__(self) -> None:
        self._dev       = None
        self._lock      = threading.Lock()
        self._connected = False
        self._log_cb    = None
        self._active_ch = 1
        self._averaging = 1
        self._state     = LMG450State(
            channels  = [ChannelMeasurement(channel=n) for n in range(1, 5)],
            aggregate = AggregateMeasurement(),
        )
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_evt    = threading.Event()
        self._poll_cbs:   list[Callable[[LMG450State], None]] = []

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

    def connect(self, port: str = "", baudrate: int = DEFAULT_BAUD,
                timeout: float = 2.0) -> tuple[bool, str]:
        if not port:
            return False, "No port specified"
        try:
            self._dev = _SerialLMG450(port, baudrate, timeout, log=self._log)
            idn = self._dev.query("*IDN?")
            if not idn:
                self._dev.close()
                self._dev = None
                return False, "No response to *IDN? — wrong port or device not ready"
            self._dev.write("CONT ON")
            self._port_info = f"{port}  {baudrate} bps  |  {idn}"
            self._connected = True
            return True, self._port_info
        except Exception as e:
            self._dev = None
            return False, str(e)

    def connect_with_config(self, cfg) -> tuple[bool, str]:
        return self.connect(port=cfg.port, baudrate=cfg.baudrate, timeout=cfg.timeout)

    def disconnect(self) -> None:
        self.stop_polling()
        if self._dev:
            try:
                self._dev.write("CONT OFF")
                self._dev.close()
            except Exception:
                pass
            self._dev = None
        self._connected = False

    # ── single-channel measurement ────────────────────────────────────────────

    def select_channel(self, ch: int) -> None:
        ch = max(1, min(self.N_CHANNELS, int(ch)))
        with self._lock:
            if self._dev:
                self._dev.write(f"ACHAN {ch}")
        self._active_ch = ch

    def set_averaging(self, n: int) -> None:
        n = max(1, min(128, int(n)))
        with self._lock:
            if self._dev:
                self._dev.write(f"AVRG {n}")
        self._averaging = n

    def measure_channel(self, ch: int | None = None) -> ChannelMeasurement:
        if ch is not None:
            self.select_channel(ch)
        m = ChannelMeasurement(channel=self._active_ch)
        with self._lock:
            if not self._dev:
                return m
            q = self._dev.query
            try:
                m.urms  = float(q("UTRMS?") or 0)
                m.irms  = float(q("ITRMS?") or 0)
                m.p     = float(q("P?")     or 0)
                m.q     = float(q("Q?")     or 0)
                m.s     = float(q("S?")     or 0)
                m.lamda = float(q("LAMDA?") or 0)
                m.phi   = float(q("PHI?")   or 0)
                m.fu    = float(q("FU?")    or 0)
                m.ubdc  = float(q("UBDC?")  or 0)
                m.ibdc  = float(q("IBDC?")  or 0)
                m.wh    = float(q("WH?")    or 0)
                m.ah    = float(q("AH?")    or 0)
                m.itime = float(q("ITIME?") or 0)
                m.uthd  = float(q("UTHD?")  or 0)
                m.ithd  = float(q("ITHD?")  or 0)
            except Exception:
                pass
        self._state.channels[self._active_ch - 1] = m
        return m

    def measure_harmonics(self, ch: int | None = None,
                          max_order: int = 20) -> tuple[list[float], list[float]]:
        """
        Returns (u_harmonics, i_harmonics) each as a list of `max_order` floats.
        Index 0 = fundamental (n=1), index 1 = 2nd harmonic, …
        """
        if ch is not None:
            self.select_channel(ch)
        u_harms, i_harms = [], []
        with self._lock:
            if not self._dev:
                return u_harms, i_harms
            for n in range(1, max_order + 1):
                try:
                    u_harms.append(float(self._dev.query(f"UHAR{n}?") or 0))
                    i_harms.append(float(self._dev.query(f"IHAR{n}?") or 0))
                except Exception:
                    u_harms.append(0.0)
                    i_harms.append(0.0)
        return u_harms, i_harms

    def measure_single_harmonic(self, ch: int, order: int,
                                kind: str = "U") -> float:
        """kind = 'U' (voltage) or 'I' (current)."""
        self.select_channel(ch)
        cmd = f"{'U' if kind.upper()=='U' else 'I'}HAR{order}?"
        with self._lock:
            if not self._dev:
                return 0.0
            try:
                return float(self._dev.query(cmd) or 0)
            except Exception:
                return 0.0

    # ── aggregate measurement ─────────────────────────────────────────────────

    def measure_aggregate(self) -> AggregateMeasurement:
        a = AggregateMeasurement()
        with self._lock:
            if not self._dev:
                return a
            q = self._dev.query
            try:
                a.psum   = float(q("PSUM?")   or 0)
                a.qsum   = float(q("QSUM?")   or 0)
                a.ssum   = float(q("SSUM?")   or 0)
                a.wpsum  = float(q("WPSUM?")  or 0)
                a.ahpsum = float(q("AHPSUM?") or 0)
            except Exception:
                pass
        self._state.aggregate = a
        return a

    # ── integration control ───────────────────────────────────────────────────

    def integration_start(self) -> None:
        with self._lock:
            if self._dev:
                self._dev.write("STRTITIME")

    def integration_stop(self) -> None:
        with self._lock:
            if self._dev:
                self._dev.write("STPTITIME")

    def integration_reset(self) -> None:
        with self._lock:
            if self._dev:
                self._dev.write("RSTITIME")

    # ── config helpers ────────────────────────────────────────────────────────

    def get_config(self) -> dict:
        return {"active_channel": self._active_ch, "averaging": self._averaging}

    def apply_config(self, cfg: dict) -> None:
        if "active_channel" in cfg:
            self.select_channel(cfg["active_channel"])
        if "averaging" in cfg:
            self.set_averaging(cfg["averaging"])

    # ── polling ───────────────────────────────────────────────────────────────

    def add_poll_callback(self, cb: Callable[[LMG450State], None]) -> None:
        self._poll_cbs.append(cb)

    def start_polling(self, interval: float = 0.5,
                      channels: list[int] | None = None) -> None:
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._stop_evt.clear()
        chans = channels or [1]
        self._poll_thread = threading.Thread(
            target=self._poll_loop, args=(interval, chans), daemon=True)
        self._poll_thread.start()

    def stop_polling(self) -> None:
        self._stop_evt.set()
        if self._poll_thread:
            self._poll_thread.join(timeout=3.0)
            self._poll_thread = None

    def _poll_loop(self, interval: float, channels: list[int]) -> None:
        while not self._stop_evt.is_set():
            if self._connected:
                for ch in channels:
                    self.measure_channel(ch)
                self.measure_aggregate()
                for cb in list(self._poll_cbs):
                    try:
                        cb(self._state)
                    except Exception:
                        pass
            self._stop_evt.wait(interval)
