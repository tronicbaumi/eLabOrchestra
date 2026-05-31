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

import math
import time
import random
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


# ── simulated device ──────────────────────────────────────────────────────────

class _SimulatedLMG450:
    """Physics-inspired simulation of all four channels."""

    def __init__(self) -> None:
        self._achan  = 1
        self._avrg   = 1
        self._t0     = time.time()
        self._int_running = False
        self._int_start   = 0.0
        self._int_energy  = [0.0] * 5   # index 1-4
        self._int_charge  = [0.0] * 5
        # Each channel has a slightly different "load"
        self._loads = [
            dict(urms=230.0, irms=5.0,  pf=0.97, freq=50.0),   # ch1
            dict(urms=115.0, irms=10.0, pf=0.85, freq=60.0),   # ch2
            dict(urms=48.0,  irms=20.0, pf=0.92, freq=50.0),   # ch3
            dict(urms=400.0, irms=3.0,  pf=0.99, freq=50.0),   # ch4
        ]

    def _noise(self, scale=0.01):
        return scale * (random.random() - 0.5)

    def write(self, cmd: str) -> None:
        cmd = cmd.strip().upper()
        if cmd.startswith("ACHAN"):
            try:
                self._achan = int(cmd.split()[-1])
            except ValueError:
                pass
        elif cmd.startswith("AVRG"):
            try:
                self._avrg = int(cmd.split()[-1])
            except ValueError:
                pass
        elif cmd == "STRTITIME":
            self._int_running = True
            self._int_start   = time.time()
        elif cmd == "STPTITIME":
            if self._int_running:
                dt = time.time() - self._int_start
                ld = self._loads[self._achan - 1]
                p  = ld["urms"] * ld["irms"] * ld["pf"]
                self._int_energy[self._achan] += p * dt / 3600.0
                self._int_charge[self._achan] += ld["irms"] * dt / 3600.0
                self._int_running = False
        elif cmd == "RSTITIME":
            self._int_energy[self._achan] = 0.0
            self._int_charge[self._achan] = 0.0
            self._int_start = time.time()

    def query(self, cmd: str) -> str:
        cmd = cmd.strip().upper()
        ld  = self._loads[self._achan - 1]

        if cmd == "*IDN?":
            return "ZES ZIMMER,LMG450,SIM000001,FW:V4.2.0"
        if cmd == "UTRMS?":
            return f"{ld['urms'] + self._noise(0.05):.4f}"
        if cmd == "ITRMS?":
            return f"{ld['irms'] + self._noise(0.02):.5f}"
        if cmd == "P?":
            return f"{ld['urms']*ld['irms']*ld['pf'] + self._noise(1.0):.4f}"
        if cmd == "Q?":
            pf   = ld["pf"]
            q    = ld["urms"] * ld["irms"] * math.sqrt(max(0, 1 - pf**2))
            return f"{q + self._noise(0.5):.4f}"
        if cmd == "S?":
            return f"{ld['urms']*ld['irms'] + self._noise(0.5):.4f}"
        if cmd == "LAMDA?":
            return f"{ld['pf'] + self._noise(0.001):.5f}"
        if cmd == "PHI?":
            phi = math.degrees(math.acos(ld["pf"]))
            return f"{phi + self._noise(0.05):.4f}"
        if cmd == "FU?":
            return f"{ld['freq'] + self._noise(0.01):.4f}"
        if cmd == "UBDC?":
            return f"{self._noise(0.1):.5f}"
        if cmd == "IBDC?":
            return f"{self._noise(0.005):.6f}"

        # integration
        if cmd == "WH?":
            if self._int_running:
                dt = time.time() - self._int_start
                p  = ld["urms"] * ld["irms"] * ld["pf"]
                return f"{self._int_energy[self._achan] + p*dt/3600.0:.6f}"
            return f"{self._int_energy[self._achan]:.6f}"
        if cmd == "AH?":
            if self._int_running:
                dt = time.time() - self._int_start
                return f"{self._int_charge[self._achan] + ld['irms']*dt/3600.0:.6f}"
            return f"{self._int_charge[self._achan]:.6f}"
        if cmd == "ITIME?":
            if self._int_running:
                return f"{time.time() - self._int_start:.2f}"
            return "0.00"

        # harmonics
        if cmd.startswith("UHAR") and cmd.endswith("?"):
            n_str = cmd[4:-1]
            try:
                n = int(n_str)
            except ValueError:
                return "0"
            # fundamental = full amplitude, harmonics decay with 1/n
            amp = ld["urms"] / n if n >= 1 else 0.0
            return f"{amp * (1 + self._noise(0.05)):.4f}"
        if cmd.startswith("IHAR") and cmd.endswith("?"):
            n_str = cmd[4:-1]
            try:
                n = int(n_str)
            except ValueError:
                return "0"
            amp = ld["irms"] / n if n >= 1 else 0.0
            return f"{amp * (1 + self._noise(0.05)):.5f}"
        if cmd == "UTHD?":
            return f"{5.2 + self._noise(0.2):.2f}"
        if cmd == "ITHD?":
            return f"{8.7 + self._noise(0.3):.2f}"

        # aggregates
        if cmd == "PSUM?":
            total = sum(ld["urms"]*ld["irms"]*ld["pf"] for ld in self._loads)
            return f"{total:.4f}"
        if cmd == "QSUM?":
            total = sum(ld["urms"]*ld["irms"]*math.sqrt(max(0,1-ld["pf"]**2)) for ld in self._loads)
            return f"{total:.4f}"
        if cmd == "SSUM?":
            total = sum(ld["urms"]*ld["irms"] for ld in self._loads)
            return f"{total:.4f}"
        if cmd == "WPSUM?":
            return f"{sum(self._int_energy[1:5]):.6f}"
        if cmd == "AHPSUM?":
            return f"{sum(self._int_charge[1:5]):.6f}"

        return "0"

    def close(self) -> None:
        pass


# ── real serial transport ─────────────────────────────────────────────────────

class _SerialLMG450:
    def __init__(self, port: str, baudrate: int = 57600, timeout: float = 2.0):
        import serial
        self._ser = serial.Serial(
            port=port, baudrate=baudrate, bytesize=8,
            parity="N", stopbits=1, timeout=timeout,
            xonxoff=False, rtscts=False, dsrdtr=False,
        )
        time.sleep(0.3)
        self._ser.flushInput()

    def write(self, cmd: str) -> None:
        self._ser.write((cmd.strip() + "\r\n").encode())
        time.sleep(0.05)

    def query(self, cmd: str) -> str:
        self._ser.flushInput()
        self.write(cmd)
        raw = self._ser.readline().decode(errors="replace")
        return raw.strip()

    def close(self) -> None:
        self._ser.close()


# ── public driver ─────────────────────────────────────────────────────────────

class LMG450:
    """
    Driver for the ZES Zimmer LMG450 four-channel power analyser.

    Quick-start::
        lmg = LMG450(simulate=True)
        ok, info = lmg.connect()
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

    def __init__(self, simulate: bool = False) -> None:
        self._simulate  = simulate
        self._dev       = None
        self._lock      = threading.Lock()
        self._connected = False
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
            return "Simulation (LMG450)"
        return getattr(self, "_port_info", "Serial")

    def connect(self, port: str = "", baudrate: int = DEFAULT_BAUD,
                timeout: float = 2.0) -> tuple[bool, str]:
        if port and not self._simulate:
            try:
                self._dev = _SerialLMG450(port, baudrate, timeout)
                idn = self._dev.query("*IDN?")
                self._dev.write("CONT ON")
                self._port_info = f"{port}  {baudrate} bps  |  {idn}"
                self._simulate  = False
                self._connected = True
                return True, self._port_info
            except Exception as e:
                self._dev = None
                if not self._simulate:
                    return False, str(e)
        # simulation fallback
        self._dev       = _SimulatedLMG450()
        self._simulate  = True
        self._connected = True
        return True, "Simulation mode (ZES Zimmer LMG450)"

    def connect_with_config(self, cfg) -> tuple[bool, str]:
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
