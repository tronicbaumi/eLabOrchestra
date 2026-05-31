"""
Block program executor.

Walks the BlockProgram tree and executes each block by calling registered
action handlers. Runs in a background thread per hat-triggered stack.
"""

from __future__ import annotations

import time
import threading
from typing import Any, Callable, Optional

from .blocks import BlockInstance, BlockProgram, BlockType

try:
    from instruments import HantekRLC1733C, MeasureMode, TestFrequency
except ImportError:
    HantekRLC1733C = None  # type: ignore
    from enum import IntEnum
    class MeasureMode(IntEnum):  # type: ignore
        Cs = 3
    class TestFrequency(IntEnum):  # type: ignore
        F1kHz = 2


_FREQ_MAP = {
    "100 Hz": TestFrequency.F100Hz,
    "120 Hz": TestFrequency.F120Hz,
    "1 kHz":  TestFrequency.F1kHz,
    "10 kHz": TestFrequency.F10kHz,
    "100 kHz":TestFrequency.F100kHz,
}
_MODE_MAP = {
    "Ls": MeasureMode.Ls, "Lp": MeasureMode.Lp,
    "Cs": MeasureMode.Cs, "Cp": MeasureMode.Cp,
    "Rs": MeasureMode.Rs, "Rp": MeasureMode.Rp,
    "Z":  MeasureMode.Z,  "D":  MeasureMode.D,
    "Q":  MeasureMode.Q,  "Theta": MeasureMode.Theta,
    "ESR":MeasureMode.ESR,"DCR": MeasureMode.DCR,
}


class ExecutionContext:
    """Shared state for a running program."""

    def __init__(self, device=None) -> None:
        self.device = device
        self.variables: dict[str, Any] = {}
        self.log_file: Optional[Any] = None
        self.running = True
        self._lock = threading.Lock()
        self._on_show: list[Callable[[str, Any], None]] = []
        self._on_log: list[Callable[[str, Any], None]] = []

    def add_show_callback(self, cb: Callable[[str, Any], None]) -> None:
        self._on_show.append(cb)

    def add_log_callback(self, cb: Callable[[str, Any], None]) -> None:
        self._on_log.append(cb)

    def show(self, label: str, value: Any) -> None:
        for cb in list(self._on_show):
            cb(label, value)

    def log(self, label: str, value: Any) -> None:
        for cb in list(self._on_log):
            cb(label, value)


class BlockExecutor:
    """Executes a BlockProgram against a device."""

    def __init__(self, device=None) -> None:
        self._device = device
        self._threads: list[threading.Thread] = []
        self._ctx: Optional[ExecutionContext] = None
        self._on_show: list[Callable[[str, Any], None]] = []
        self._on_log: list[Callable[[str, Any], None]] = []
        self._last_measurement = None

    def add_show_callback(self, cb: Callable[[str, Any], None]) -> None:
        self._on_show.append(cb)

    def add_log_callback(self, cb: Callable[[str, Any], None]) -> None:
        self._on_log.append(cb)

    def run(self, program: BlockProgram) -> None:
        self.stop()
        ctx = ExecutionContext(device=self._device)
        for cb in self._on_show:
            ctx.add_show_callback(cb)
        for cb in self._on_log:
            ctx.add_log_callback(cb)
        self._ctx = ctx

        for stack in program.stacks:
            defn = stack.definition
            if defn.type == BlockType.HAT:
                t = threading.Thread(target=self._run_hat, args=(stack, ctx), daemon=True)
                t.start()
                self._threads.append(t)

    def stop(self) -> None:
        if self._ctx:
            self._ctx.running = False
        for t in self._threads:
            t.join(timeout=1)
        self._threads.clear()
        self._ctx = None

    @property
    def is_running(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    # ── hat dispatch ─────────────────────────────────────────────────────────

    def _run_hat(self, hat: BlockInstance, ctx: ExecutionContext) -> None:
        bid = hat.block_id
        if bid == "on_start":
            self._run_sequence(hat.next, ctx)
        elif bid == "on_timer":
            interval = float(hat.get_field("interval") or 1.0)
            while ctx.running:
                self._run_sequence(hat.next, ctx)
                t = 0.0
                while t < interval and ctx.running:
                    time.sleep(0.05)
                    t += 0.05
        elif bid == "on_measure":
            # Run the body each time _last_measurement updates
            self._run_sequence(hat.next, ctx)

    def _run_sequence(self, block: Optional[BlockInstance], ctx: ExecutionContext) -> None:
        cur = block
        while cur and ctx.running:
            self._execute(cur, ctx)
            cur = cur.next

    def _execute(self, block: BlockInstance, ctx: ExecutionContext) -> None:
        bid = block.block_id
        dev = ctx.device

        if bid == "wait":
            secs = float(block.get_field("secs") or 1)
            deadline = time.time() + secs
            while time.time() < deadline and ctx.running:
                time.sleep(0.05)

        elif bid == "stop_all":
            ctx.running = False

        elif bid == "set_mode" and dev:
            mode_str = block.get_field("mode") or "Cs"
            dev.set_mode(_MODE_MAP.get(mode_str, MeasureMode.Cs))

        elif bid == "set_freq" and dev:
            freq_str = block.get_field("freq") or "1 kHz"
            dev.set_frequency(_FREQ_MAP.get(freq_str, TestFrequency.F1kHz))

        elif bid == "set_hold" and dev:
            dev.set_hold(block.get_field("state") == "ON")

        elif bid == "set_rel" and dev:
            dev.set_rel(block.get_field("state") == "ON")

        elif bid == "beep" and dev:
            dev.trigger_beep()

        elif bid == "log_measurement":
            if dev:
                m = dev.measure()
                self._last_measurement = m
                ctx.log("primary", m.primary)
                ctx.log("secondary", m.secondary)
                ctx.log("phase", m.phase)
                ctx.log("mode", m.mode_label)
                ctx.log("frequency", m.frequency.label)

        elif bid == "log_value":
            label = block.get_field("label") or "value"
            value = self._eval(block.get_field("value") or "0", ctx, dev)
            ctx.log(label, value)

        elif bid == "show_value":
            label = block.get_field("label") or "value"
            value = self._eval(block.get_field("value") or "0", ctx, dev)
            ctx.show(label, value)

        elif bid == "start_log":
            fname = block.get_field("file") or "log.csv"
            import csv, io
            try:
                ctx.log_file = open(fname, "w", newline="", encoding="utf-8")
                ctx._csv_writer = csv.writer(ctx.log_file)
                ctx._csv_writer.writerow(["timestamp", "label", "value"])
            except OSError:
                pass

        elif bid == "stop_log":
            if ctx.log_file:
                ctx.log_file.close()
                ctx.log_file = None

        elif bid == "set_var":
            var   = block.get_field("var") or "myVar"
            value = self._eval(block.get_field("value") or "0", ctx, dev)
            ctx.variables[var] = value

        elif bid == "change_var":
            var   = block.get_field("var") or "myVar"
            delta = float(self._eval(block.get_field("delta") or "1", ctx, dev) or 0)
            ctx.variables[var] = float(ctx.variables.get(var, 0)) + delta

        elif bid == "repeat":
            times = int(float(block.get_field("times") or 10))
            for _ in range(times):
                if not ctx.running:
                    break
                self._run_sequence(block.children[0] if block.children else None, ctx)

        elif bid == "repeat_forever":
            while ctx.running:
                self._run_sequence(block.children[0] if block.children else None, ctx)

        elif bid == "if_block":
            cond = self._eval(block.get_field("condition") or "False", ctx, dev)
            if cond:
                self._run_sequence(block.children[0] if block.children else None, ctx)

    def _eval(self, expr: Any, ctx: ExecutionContext, dev) -> Any:
        if not isinstance(expr, str):
            return expr
        # simple variable lookup
        if expr in ctx.variables:
            return ctx.variables[expr]
        # try numeric
        try:
            return float(expr)
        except ValueError:
            pass
        # try safe eval for operators
        try:
            return eval(expr, {"__builtins__": {}}, ctx.variables)  # noqa: S307
        except Exception:
            return expr

    def evaluate_reporter(self, block: BlockInstance, ctx: ExecutionContext, dev) -> Any:
        bid = block.block_id
        if bid == "read_primary" and dev:
            m = dev.measure()
            return m.primary
        if bid == "read_secondary" and dev:
            m = dev.measure()
            return m.secondary
        if bid == "read_phase" and dev:
            m = dev.measure()
            return m.phase
        if bid == "read_mode" and dev:
            return dev._mode.name
        if bid == "read_freq" and dev:
            return dev._freq.label
        if bid == "is_overload" and dev:
            m = dev.measure()
            return m.overload
        if bid == "get_var":
            return ctx.variables.get(block.get_field("var"), 0)
        if bid == "op_add":
            return float(self._eval(block.get_field("a"), ctx, dev) or 0) + \
                   float(self._eval(block.get_field("b"), ctx, dev) or 0)
        if bid == "op_sub":
            return float(self._eval(block.get_field("a"), ctx, dev) or 0) - \
                   float(self._eval(block.get_field("b"), ctx, dev) or 0)
        if bid == "op_mul":
            return float(self._eval(block.get_field("a"), ctx, dev) or 1) * \
                   float(self._eval(block.get_field("b"), ctx, dev) or 1)
        if bid == "op_div":
            b = float(self._eval(block.get_field("b"), ctx, dev) or 1)
            return float(self._eval(block.get_field("a"), ctx, dev) or 0) / b if b else 0
        return None
