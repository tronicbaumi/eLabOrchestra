"""
X2Cscope instrument driver.

Wraps the pyx2cscope library (pip install pyx2cscope) to provide a stable
interface consistent with the rest of the eLabOrchestra instrument drivers.

Typical flow:
    drv = X2CScopeDriver()
    ok, info = drv.connect("COM3", 115200)
    ok, count = drv.load_elf("firmware.elf")
    value = drv.read_variable("AppData.u16Speed")
    drv.write_variable("AppData.u16Speed", 1500)
    drv.disconnect()
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass
class X2CVarInfo:
    """Metadata for one ELF variable."""
    name:      str
    address:   int  = 0
    data_type: str  = ""
    value:     Any  = None   # last read value (None = not yet read)


class X2CScopeDriver:
    """
    Thread-safe wrapper around pyx2cscope.X2CScope.

    All public methods are safe to call from any thread.
    """

    def __init__(self) -> None:
        self._lock  = threading.Lock()
        self._x2c   = None          # pyx2cscope.X2CScope instance
        self._vars: dict[str, object] = {}   # name → LimitedScopeVariable
        self._var_info: dict[str, X2CVarInfo] = {}
        self._elf_path: Optional[Path] = None
        self._connected = False
        self._port    = ""
        self._baudrate = 115200

        # polling
        self._poll_cbs: list[Callable[[], None]] = []
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_stop = threading.Event()
        self._poll_names: list[str] = []   # only these are polled (watch list)

    # ── connection ────────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def port(self) -> str:
        return self._port

    @property
    def baudrate(self) -> int:
        return self._baudrate

    def connect(self, port: str, baudrate: int = 115200) -> tuple[bool, str]:
        """Open the serial link to the target MCU."""
        try:
            from pyx2cscope.x2cscope import X2CScope  # optional dep
        except ImportError:
            return False, "pyx2cscope not installed — run: pip install pyx2cscope"

        # join the poll thread before taking the lock (it also takes the
        # lock in read_variable — joining while holding it would deadlock)
        self.stop_polling()
        with self._lock:
            if self._connected:
                self._do_disconnect()
            try:
                x2c = X2CScope(port=port, baud_rate=baudrate)
                x2c.connect()
                self._x2c      = x2c
                self._port     = port
                self._baudrate = baudrate
                self._connected = True
                return True, f"Connected to {port} @ {baudrate}"
            except Exception as exc:
                self._x2c = None
                self._connected = False
                return False, str(exc)

    def connect_with_config(self, cfg) -> tuple[bool, str]:
        """Connect using a SerialConfig dataclass (same type used by UartConfigDialog)."""
        return self.connect(port=cfg.port, baudrate=cfg.baudrate)

    def disconnect(self) -> None:
        # join the poll thread before taking the lock (it also takes the
        # lock in read_variable — joining while holding it would deadlock)
        self.stop_polling()
        with self._lock:
            self._do_disconnect()

    def _do_disconnect(self) -> None:
        """Must be called with self._lock held and polling already stopped."""
        if self._x2c:
            try:
                self._x2c.disconnect()
            except Exception:
                pass
        self._x2c = None
        self._connected = False
        self._vars.clear()
        self._var_info.clear()

    # ── ELF loading ───────────────────────────────────────────────────────────

    @property
    def elf_path(self) -> Optional[Path]:
        return self._elf_path

    @property
    def variable_names(self) -> list[str]:
        with self._lock:
            return list(self._var_info.keys())

    def load_elf(self, path: str | Path) -> tuple[bool, int | str]:
        """
        Load the ELF file and populate the variable list.
        Returns (True, variable_count) on success or (False, error_message).
        The target does NOT need to be connected to load the ELF; variable
        addresses are resolved from the debug symbols in the ELF.
        """
        path = Path(path)
        if not path.is_file():
            return False, f"File not found: {path}"

        with self._lock:
            if self._x2c is None:
                return False, "Not connected — connect first, then load ELF"
            try:
                self._x2c.load_elf(str(path))
                names = self._x2c.list_variables()
                self._vars.clear()
                self._var_info.clear()
                for name in names:
                    try:
                        var = self._x2c.get_variable(name)
                        info = X2CVarInfo(
                            name=name,
                            address=getattr(var, "address", 0),
                            data_type=getattr(var, "data_type", ""),
                        )
                        self._vars[name] = var
                        self._var_info[name] = info
                    except Exception:
                        pass
                self._elf_path = path
                return True, len(self._var_info)
            except Exception as exc:
                return False, str(exc)

    def get_var_info(self, name: str) -> Optional[X2CVarInfo]:
        return self._var_info.get(name)

    # ── read / write ──────────────────────────────────────────────────────────

    def read_variable(self, name: str) -> Any:
        """Read a variable from the MCU. Returns None on failure."""
        with self._lock:
            var = self._vars.get(name)
            if var is None:
                return None
            try:
                val = var.get_value()
                if name in self._var_info:
                    self._var_info[name].value = val
                return val
            except Exception:
                return None

    def write_variable(self, name: str, value: float) -> bool:
        """Write a value to a variable on the MCU. Returns True on success."""
        with self._lock:
            var = self._vars.get(name)
            if var is None:
                return False
            try:
                var.set_value(value)
                return True
            except Exception:
                return False

    def get_cached_value(self, name: str) -> Any:
        """Last value read by the poll thread — no target I/O.
        None until the variable has been polled at least once."""
        with self._lock:
            info = self._var_info.get(name)
            return info.value if info else None

    def set_poll_variables(self, names: list[str]) -> None:
        """Restrict background polling to these variables (the watch list).
        An empty list disables variable polling."""
        with self._lock:
            self._poll_names = list(names)

    # ── polling (background refresh) ─────────────────────────────────────────

    def add_poll_callback(self, cb: Callable[[], None]) -> None:
        self._poll_cbs.append(cb)

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
            if self._connected:
                with self._lock:
                    names = list(self._poll_names)
                for name in names:
                    if self._poll_stop.is_set():
                        break
                    self.read_variable(name)
            for cb in list(self._poll_cbs):
                try:
                    cb()
                except Exception:
                    pass
            self._poll_stop.wait(interval)

    # ── config persistence ────────────────────────────────────────────────────

    def get_config(self) -> dict:
        return {
            "port":     self._port,
            "baudrate": self._baudrate,
            "elf_path": str(self._elf_path) if self._elf_path else "",
        }

    def apply_config(self, cfg: dict) -> None:
        self._port     = cfg.get("port",     self._port)
        self._baudrate = cfg.get("baudrate",  self._baudrate)
        elf = cfg.get("elf_path", "")
        if elf:
            self._elf_path = Path(elf)
