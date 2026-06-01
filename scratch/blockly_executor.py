"""
Executor for Blockly workspace programs.

The workspace is serialised to a JSON object by the JS side:
  { "stacks": [ <blockObj>, … ] }

Each blockObj:
  { type, id, fields:{…}, inputs:{…}, next: <blockObj>|null }

Hat blocks have type lcr_when_start or lcr_when_timer.
Statement blocks are connected via .next.
Value/reporter blocks are connected via .inputs.
Control blocks put their body in inputs.DO_BODY (or ELSE_BODY etc.).
"""

from __future__ import annotations

import time
import threading
import math
from typing import Any, Callable, Optional


# Try to import instrument types; fall back to stubs so executor can load standalone
try:
    from instruments import MeasureMode, TestFrequency
    _FREQ_MAP = {f.name: f for f in TestFrequency}
    _MODE_MAP = {m.name: m for m in MeasureMode}
except ImportError:
    _FREQ_MAP = {}
    _MODE_MAP = {}


class ExecContext:
    def __init__(self, device=None, psu=None, lmg=None, dsp=None, dashboard=None, eload=None) -> None:
        self.device     = device
        self.psu        = psu
        self.lmg        = lmg
        self.dsp        = dsp
        self.dashboard  = dashboard
        self.eload      = eload
        self._dsp_ch    = 1   # active channel for dsp commands
        self.variables: dict[str, Any] = {}
        self.running = True
        self._show_cbs:  list[Callable[[str, Any], None]] = []
        self._log_cbs:   list[Callable[[str, Any], None]] = []

    def add_show_cb(self, cb):  self._show_cbs.append(cb)
    def add_log_cb(self, cb):   self._log_cbs.append(cb)

    def show(self, label, value):
        for cb in list(self._show_cbs): cb(label, value)

    def log(self, label, value):
        for cb in list(self._log_cbs): cb(label, value)


class BlocklyExecutor:
    """Runs a program dict (from BlocklyBridge.program) in background threads."""

    def __init__(self, device=None, psu=None, lmg=None, dsp=None, dashboard=None, eload=None) -> None:
        self._device     = device
        self._psu        = psu
        self._lmg        = lmg
        self._dsp        = dsp
        self._dashboard  = dashboard
        self._eload      = eload
        self._threads: list[threading.Thread] = []
        self._ctx: Optional[ExecContext] = None
        self._show_cbs: list[Callable[[str, Any], None]] = []
        self._log_cbs:  list[Callable[[str, Any], None]] = []

    def add_show_callback(self, cb): self._show_cbs.append(cb)
    def add_log_callback(self,  cb): self._log_cbs.append(cb)

    @property
    def is_running(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    def run(self, program: dict) -> None:
        self.stop()
        ctx = ExecContext(device=self._device, psu=self._psu, lmg=self._lmg,
                          dsp=self._dsp, dashboard=self._dashboard,
                          eload=self._eload)
        for cb in self._show_cbs: ctx.add_show_cb(cb)
        for cb in self._log_cbs:  ctx.add_log_cb(cb)
        self._ctx = ctx

        for stack in program.get("stacks", []):
            btype = stack.get("type", "")
            if btype in ("lcr_when_start", "lcr_when_timer"):
                t = threading.Thread(target=self._run_hat, args=(stack, ctx), daemon=True)
                t.start()
                self._threads.append(t)

    def stop(self) -> None:
        if self._ctx:
            self._ctx.running = False
        for t in self._threads:
            t.join(timeout=1.0)
        self._threads.clear()
        self._ctx = None

    # ── hat ──────────────────────────────────────────────────────────────────

    def _run_hat(self, hat: dict, ctx: ExecContext) -> None:
        btype = hat.get("type")
        if btype == "lcr_when_start":
            self._run_next(hat, ctx)
        elif btype == "lcr_when_timer":
            interval = float(hat["fields"].get("INTERVAL", 1.0))
            while ctx.running:
                self._run_next(hat, ctx)
                self._sleep(interval, ctx)

    def _run_next(self, block: dict, ctx: ExecContext) -> None:
        """Execute the .next chain starting from block.next."""
        cur = block.get("next")
        while cur and ctx.running:
            self._exec(cur, ctx)
            cur = cur.get("next")

    def _exec(self, block: dict, ctx: ExecContext) -> None:
        if not block or not ctx.running:
            return
        btype  = block.get("type", "")
        fields = block.get("fields", {})
        inputs = block.get("inputs", {})
        dev    = ctx.device

        # ── Control ──────────────────────────────────────────────────────────
        if btype == "lcr_wait":
            secs = float(self._eval_input(inputs.get("SECS"), ctx) or 1.0)
            self._sleep(secs, ctx)

        elif btype == "controls_repeat_ext":
            times = int(self._eval_input(inputs.get("TIMES"), ctx) or 10)
            body  = inputs.get("DO_BODY")
            for _ in range(times):
                if not ctx.running: break
                self._run_body(body, ctx)

        elif btype == "controls_whileUntil":
            mode = fields.get("MODE", "WHILE")
            cond_block = inputs.get("BOOL")
            body = inputs.get("DO_BODY")
            while ctx.running:
                cond = bool(self._eval_input(cond_block, ctx))
                if mode == "WHILE" and not cond: break
                if mode == "UNTIL" and cond:     break
                self._run_body(body, ctx)

        elif btype == "controls_if":
            cond = bool(self._eval_input(inputs.get("IF0"), ctx))
            if cond:
                self._run_body(inputs.get("DO0_BODY"), ctx)
            else:
                self._run_body(inputs.get("ELSE_BODY"), ctx)

        elif btype == "variables_set":
            var   = fields.get("VAR", "var")
            value = self._eval_input(inputs.get("VALUE"), ctx)
            ctx.variables[var] = value

        elif btype == "variables_get":
            pass  # reporter, handled in _eval_input

        # ── Instrument ───────────────────────────────────────────────────────
        elif btype == "lcr_set_mode" and dev:
            mode_str = fields.get("MODE", "Cs")
            if mode_str in _MODE_MAP:
                dev.set_mode(_MODE_MAP[mode_str])

        elif btype == "lcr_set_frequency" and dev:
            freq_str = fields.get("FREQ", "F1kHz")
            if freq_str in _FREQ_MAP:
                dev.set_frequency(_FREQ_MAP[freq_str])

        elif btype == "lcr_hold" and dev:
            dev.set_hold(fields.get("STATE", "false") == "true")

        elif btype == "lcr_rel" and dev:
            dev.set_rel(fields.get("STATE", "false") == "true")

        elif btype == "lcr_beep" and dev:
            dev.trigger_beep()

        # ── Logging ──────────────────────────────────────────────────────────
        elif btype == "lcr_log_measurement":
            if dev:
                m = dev.measure()
                for attr in ("primary", "secondary", "phase"):
                    ctx.log(attr, getattr(m, attr))
                ctx.log("mode",      m.mode_label)
                ctx.log("frequency", m.frequency.label)

        elif btype == "lcr_log_value":
            label = fields.get("LABEL", "value")
            value = self._eval_input(inputs.get("VALUE"), ctx)
            ctx.log(label, value)

        elif btype == "lcr_show_value":
            label = fields.get("LABEL", "value")
            value = self._eval_input(inputs.get("VALUE"), ctx)
            ctx.show(label, value)

        elif btype == "lcr_start_log":
            fname = fields.get("FILE", "log.csv")
            ctx.log("__start_log__", fname)

        elif btype == "lcr_stop_log":
            ctx.log("__stop_log__", None)

        # ── Power Supply (OWON SP3103) ────────────────────────────────────────
        elif btype == "psu_set_voltage" and ctx.psu:
            volts = float(self._eval_input(inputs.get("VOLTAGE"), ctx) or 0)
            ctx.psu.set_voltage(volts)

        elif btype == "psu_set_current" and ctx.psu:
            amps = float(self._eval_input(inputs.get("CURRENT"), ctx) or 0)
            ctx.psu.set_current(amps)

        elif btype == "psu_output" and ctx.psu:
            state_str = fields.get("STATE", "ON")
            ctx.psu.set_output(state_str == "ON")

        elif btype == "psu_toggle_output" and ctx.psu:
            ctx.psu.set_output(not ctx.psu._state.output_on)

        # ── LMG450 Power Analyser ─────────────────────────────────────────────
        elif btype == "lmg_select_channel" and ctx.lmg:
            ch = int(self._eval_input(inputs.get("CHANNEL"), ctx) or
                     fields.get("CHANNEL", 1))
            ctx.lmg.select_channel(ch)

        elif btype == "lmg_set_averaging" and ctx.lmg:
            n = int(self._eval_input(inputs.get("COUNT"), ctx) or
                    fields.get("COUNT", 1))
            ctx.lmg.set_averaging(n)

        elif btype == "lmg_integration_start" and ctx.lmg:
            ctx.lmg.integration_start()

        elif btype == "lmg_integration_stop" and ctx.lmg:
            ctx.lmg.integration_stop()

        elif btype == "lmg_integration_reset" and ctx.lmg:
            ctx.lmg.integration_reset()

        # ── Magtrol DSP7000 ───────────────────────────────────────────────────
        elif btype == "dsp_select_channel" and ctx.dsp:
            ctx._dsp_ch = int(fields.get("CHANNEL", 1))

        elif btype == "dsp_set_speed" and ctx.dsp:
            rpm = float(self._eval_input(inputs.get("RPM"), ctx) or 0)
            ctx.dsp.set_speed(ctx._dsp_ch, rpm)

        elif btype == "dsp_reset_speed" and ctx.dsp:
            ctx.dsp.reset_speed(ctx._dsp_ch)

        elif btype == "dsp_set_torque" and ctx.dsp:
            t = float(self._eval_input(inputs.get("TORQUE"), ctx) or 0)
            ctx.dsp.set_torque(ctx._dsp_ch, t)

        elif btype == "dsp_reset_torque" and ctx.dsp:
            ctx.dsp.reset_torque(ctx._dsp_ch)

        elif btype == "dsp_set_current" and ctx.dsp:
            pct = float(self._eval_input(inputs.get("PCT"), ctx) or 0)
            ctx.dsp.set_current(ctx._dsp_ch, pct)

        elif btype == "dsp_ramp_up" and ctx.dsp:
            profile = fields.get("PROFILE", "0")
            rate    = float(self._eval_input(inputs.get("RATE"), ctx) or 100)
            ctx.dsp.ramp_up(ctx._dsp_ch, profile == "0", rate)

        elif btype == "dsp_ramp_down" and ctx.dsp:
            profile = fields.get("PROFILE", "0")
            rate    = float(self._eval_input(inputs.get("RATE"), ctx) or 100)
            ctx.dsp.ramp_down(ctx._dsp_ch, profile == "0", rate)

        elif btype == "dsp_abort_ramp" and ctx.dsp:
            ctx.dsp.abort_ramp(ctx._dsp_ch)

        elif btype == "dsp_reset_channel" and ctx.dsp:
            ctx.dsp.reset_channel(ctx._dsp_ch)

        elif btype == "dsp_set_speed_alarm" and ctx.dsp:
            rpm = float(self._eval_input(inputs.get("RPM"), ctx) or 0)
            ctx.dsp.set_speed_alarm(ctx._dsp_ch, rpm)

        elif btype == "dsp_set_torque_alarm" and ctx.dsp:
            t = float(self._eval_input(inputs.get("TORQUE"), ctx) or 0)
            ctx.dsp.set_torque_alarm(ctx._dsp_ch, t)

        elif btype == "dsp_set_power_alarm" and ctx.dsp:
            kw = float(self._eval_input(inputs.get("KW"), ctx) or 0)
            ctx.dsp.set_power_alarm(ctx._dsp_ch, kw)

        elif btype == "dsp_alarms_enable" and ctx.dsp:
            ctx.dsp.set_alarms(ctx._dsp_ch, fields.get("STATE", "1") == "1")

        elif btype == "dsp_freeze_pid" and ctx.dsp:
            ctx.dsp.freeze_pid(ctx._dsp_ch, fields.get("STATE", "0") == "1")

        elif btype == "dsp_tare" and ctx.dsp:
            ctx.dsp.tare(ctx._dsp_ch, fields.get("STATE", "0") == "1")

        elif btype == "dsp_save" and ctx.dsp:
            ctx.dsp.save(ctx._dsp_ch)

        # ── Dashboard / Visualization ─────────────────────────────────────────
        elif btype == "dash_set_gauge" and ctx.dashboard:
            name  = fields.get("NAME", "gauge1")
            value = float(self._eval_input(inputs.get("VALUE"), ctx) or 0)
            ctx.dashboard.set_gauge(name, value)

        elif btype == "dash_config_gauge" and ctx.dashboard:
            name   = fields.get("NAME",   "gauge1")
            label  = fields.get("LABEL",  "Value")
            colour = fields.get("COLOUR", "#4C97FF")
            mn     = float(self._eval_input(inputs.get("MIN"), ctx) or 0)
            mx     = float(self._eval_input(inputs.get("MAX"), ctx) or 100)
            ctx.dashboard.config_gauge(name, label, mn, mx, colour)

        elif btype == "dash_plot_yt" and ctx.dashboard:
            name  = fields.get("NAME", "chart1")
            value = float(self._eval_input(inputs.get("VALUE"), ctx) or 0)
            ctx.dashboard.plot_yt(name, value)

        elif btype == "dash_config_yt" and ctx.dashboard:
            name   = fields.get("NAME",   "chart1")
            label  = fields.get("LABEL",  "Signal")
            colour = fields.get("COLOUR", "#4C97FF")
            window = float(self._eval_input(inputs.get("WINDOW"), ctx) or 60)
            ctx.dashboard.config_yt(name, label, colour, window)

        elif btype == "dash_plot_xy" and ctx.dashboard:
            name = fields.get("NAME", "xy1")
            x    = float(self._eval_input(inputs.get("X"), ctx) or 0)
            y    = float(self._eval_input(inputs.get("Y"), ctx) or 0)
            ctx.dashboard.plot_xy(name, x, y)

        elif btype == "dash_config_xy" and ctx.dashboard:
            name   = fields.get("NAME",   "xy1")
            label  = fields.get("LABEL",  "XY Plot")
            colour = fields.get("COLOUR", "#FF6680")
            ctx.dashboard.config_xy(name, label, colour)

        elif btype == "dash_show_text" and ctx.dashboard:
            name  = fields.get("NAME", "text1")
            value = self._eval_input(inputs.get("VALUE"), ctx)
            ctx.dashboard.show_text(name, value)

        elif btype == "dash_config_text" and ctx.dashboard:
            name   = fields.get("NAME",   "text1")
            label  = fields.get("LABEL",  "Reading")
            colour = fields.get("COLOUR", "#9966FF")
            ctx.dashboard.config_text(name, label, colour)

        elif btype == "dash_clear_chart" and ctx.dashboard:
            ctx.dashboard.clear_chart(fields.get("NAME", "chart1"))

        elif btype == "dash_config_button" and ctx.dashboard:
            name   = fields.get("NAME",   "btn1")
            label  = fields.get("LABEL",  "Click me")
            colour = fields.get("COLOUR", "#E64A19")
            ctx.dashboard.config_button(name, label, colour)

        elif btype == "dash_config_checkbox" and ctx.dashboard:
            name    = fields.get("NAME",    "chk1")
            label   = fields.get("LABEL",   "Enable")
            default = fields.get("DEFAULT", "false") == "true"
            ctx.dashboard.config_checkbox(name, label, default)

        elif btype == "dash_config_dropdown" and ctx.dashboard:
            name    = fields.get("NAME",    "dd1")
            label   = fields.get("LABEL",   "Choose")
            options = [o.strip() for o in fields.get("OPTIONS", "A,B,C").split(",")]
            ctx.dashboard.config_dropdown(name, label, options)

        elif btype == "dash_config_slider" and ctx.dashboard:
            name   = fields.get("NAME",   "slider1")
            label  = fields.get("LABEL",  "Value")
            colour = fields.get("COLOUR", "#4C97FF")
            mn     = float(self._eval_input(inputs.get("MIN"),  ctx) or 0)
            mx     = float(self._eval_input(inputs.get("MAX"),  ctx) or 100)
            step   = float(self._eval_input(inputs.get("STEP"), ctx) or 1)
            ctx.dashboard.config_slider(name, label, mn, mx, step, colour)

        elif btype == "dash_set_slider" and ctx.dashboard:
            name  = fields.get("NAME", "slider1")
            value = float(self._eval_input(inputs.get("VALUE"), ctx) or 0)
            ctx.dashboard.set_slider(name, value)

        elif btype == "dash_config_knob" and ctx.dashboard:
            name   = fields.get("NAME",   "knob1")
            label  = fields.get("LABEL",  "Setpoint")
            colour = fields.get("COLOUR", "#4C97FF")
            mn     = float(self._eval_input(inputs.get("MIN"),  ctx) or 0)
            mx     = float(self._eval_input(inputs.get("MAX"),  ctx) or 100)
            step   = float(self._eval_input(inputs.get("STEP"), ctx) or 1)
            ctx.dashboard.config_knob(name, label, mn, mx, step, colour)

        elif btype == "dash_set_knob" and ctx.dashboard:
            name  = fields.get("NAME", "knob1")
            value = float(self._eval_input(inputs.get("VALUE"), ctx) or 0)
            ctx.dashboard.set_knob(name, value)

        elif btype == "dash_config_switch" and ctx.dashboard:
            name      = fields.get("NAME",      "sw1")
            label     = fields.get("LABEL",     "Enable")
            on_label  = fields.get("ON_LABEL",  "ON")
            off_label = fields.get("OFF_LABEL", "OFF")
            colour    = fields.get("COLOUR",    "#43A047")
            ctx.dashboard.config_switch(name, label, on_label, off_label, colour)

        elif btype == "dash_set_switch" and ctx.dashboard:
            name  = fields.get("NAME",  "sw1")
            state = fields.get("STATE", "false") == "true"
            ctx.dashboard.set_switch(name, state)

    # ── run a statement body ──────────────────────────────────────────────────

    def _run_body(self, first_block: Optional[dict], ctx: ExecContext) -> None:
        cur = first_block
        while cur and ctx.running:
            self._exec(cur, ctx)
            cur = cur.get("next")

    # ── evaluate a value/reporter input ──────────────────────────────────────

    def _eval_input(self, block: Optional[dict], ctx: ExecContext) -> Any:
        if block is None:
            return None
        btype  = block.get("type", "")
        fields = block.get("fields", {})
        inputs = block.get("inputs", {})
        dev    = ctx.device

        if btype == "math_number":
            return float(fields.get("NUM", 0))

        if btype == "text":
            return str(fields.get("TEXT", ""))

        if btype == "logic_boolean":
            return fields.get("BOOL", "TRUE") == "TRUE"

        if btype == "variables_get":
            return ctx.variables.get(fields.get("VAR", ""), 0)

        if btype == "math_arithmetic":
            a = self._eval_input(inputs.get("A"), ctx)
            b = self._eval_input(inputs.get("B"), ctx)
            op = fields.get("OP", "ADD")
            a, b = (float(x or 0) for x in (a, b))
            if op == "ADD":      return a + b
            if op == "MINUS":    return a - b
            if op == "MULTIPLY": return a * b
            if op == "DIVIDE":   return a / b if b else 0
            if op == "POWER":    return a ** b

        if btype == "math_single":
            n = float(self._eval_input(inputs.get("NUM"), ctx) or 0)
            op = fields.get("OP", "ROOT")
            if op == "ROOT":    return math.sqrt(abs(n))
            if op == "ABS":     return abs(n)
            if op == "NEG":     return -n
            if op == "LN":      return math.log(n) if n > 0 else 0
            if op == "LOG10":   return math.log10(n) if n > 0 else 0
            if op == "EXP":     return math.exp(n)
            if op == "POW10":   return 10 ** n

        if btype == "math_round":
            n = float(self._eval_input(inputs.get("NUM"), ctx) or 0)
            op = fields.get("OP", "ROUND")
            if op == "ROUND":   return round(n)
            if op == "ROUNDUP": return math.ceil(n)
            if op == "ROUNDDOWN": return math.floor(n)

        if btype == "math_constrain":
            val  = float(self._eval_input(inputs.get("VALUE"), ctx) or 0)
            low  = float(self._eval_input(inputs.get("LOW"),   ctx) or 0)
            high = float(self._eval_input(inputs.get("HIGH"),  ctx) or 100)
            return max(low, min(high, val))

        if btype == "logic_compare":
            a = self._eval_input(inputs.get("A"), ctx)
            b = self._eval_input(inputs.get("B"), ctx)
            op = fields.get("OP", "EQ")
            try:
                a, b = float(a), float(b)
            except (TypeError, ValueError):
                a, b = str(a), str(b)
            if op == "EQ":  return a == b
            if op == "NEQ": return a != b
            if op == "LT":  return a <  b
            if op == "LTE": return a <= b
            if op == "GT":  return a >  b
            if op == "GTE": return a >= b

        if btype == "logic_operation":
            a = bool(self._eval_input(inputs.get("A"), ctx))
            b = bool(self._eval_input(inputs.get("B"), ctx))
            op = fields.get("OP", "AND")
            if op == "AND": return a and b
            if op == "OR":  return a or b

        if btype == "logic_negate":
            return not bool(self._eval_input(inputs.get("BOOL"), ctx))

        if btype == "text_join":
            parts = []
            for i in range(10):
                key = f"ADD{i}"
                v = inputs.get(key)
                if v is None: break
                parts.append(str(self._eval_input(v, ctx) or ""))
            return "".join(parts)

        if btype == "text_length":
            return len(str(self._eval_input(inputs.get("VALUE"), ctx) or ""))

        # DSP7000 reporters
        if ctx.dsp:
            if btype == "dsp_read_speed":
                ch = int(fields.get("CHANNEL", 1))
                return ctx.dsp.read_channel(ch).speed
            if btype == "dsp_read_torque":
                ch = int(fields.get("CHANNEL", 1))
                return ctx.dsp.read_channel(ch).torque
            if btype == "dsp_read_power":
                ch = int(fields.get("CHANNEL", 1))
                return ctx.dsp.read_channel(ch).power
            if btype == "dsp_read_direction":
                ch = int(fields.get("CHANNEL", 1))
                return ctx.dsp.read_channel(ch).direction

        # Dashboard reporters
        if ctx.dashboard:
            if btype == "dash_button_clicked":
                return ctx.dashboard.get_button_clicked(fields.get("NAME", "btn1"))
            if btype == "dash_checkbox_state":
                return ctx.dashboard.get_checkbox(fields.get("NAME", "chk1"))
            if btype == "dash_dropdown_value":
                return ctx.dashboard.get_dropdown(fields.get("NAME", "dd1"))
            if btype == "dash_knob_value":
                return ctx.dashboard.get_knob(fields.get("NAME", "knob1"))
            if btype == "dash_switch_state":
                return ctx.dashboard.get_switch(fields.get("NAME", "sw1"))
            if btype == "dash_slider_value":
                return ctx.dashboard.get_slider(fields.get("NAME", "slider1"))

        # LMG450 reporters
        lmg = ctx.lmg
        if lmg and lmg.connected:
            if btype == "lmg_read_voltage":
                return lmg.measure_channel().urms
            if btype == "lmg_read_current":
                return lmg.measure_channel().irms
            if btype == "lmg_read_power":
                return lmg.measure_channel().p
            if btype == "lmg_read_reactive":
                return lmg.measure_channel().q
            if btype == "lmg_read_apparent":
                return lmg.measure_channel().s
            if btype == "lmg_read_pf":
                return lmg.measure_channel().lamda
            if btype == "lmg_read_phase":
                return lmg.measure_channel().phi
            if btype == "lmg_read_frequency":
                return lmg.measure_channel().fu
            if btype == "lmg_read_energy":
                return lmg.measure_channel().wh
            if btype == "lmg_read_charge":
                return lmg.measure_channel().ah
            if btype == "lmg_read_thd_v":
                return lmg.measure_channel().uthd
            if btype == "lmg_read_thd_i":
                return lmg.measure_channel().ithd
            if btype == "lmg_read_psum":
                return lmg.measure_aggregate().psum
            if btype == "lmg_read_qsum":
                return lmg.measure_aggregate().qsum
            if btype == "lmg_read_ssum":
                return lmg.measure_aggregate().ssum
            if btype == "lmg_read_harmonic_v":
                order = int(self._eval_input(inputs.get("ORDER"), ctx) or
                            fields.get("ORDER", 1))
                return lmg.measure_single_harmonic(lmg._active_ch, order, "U")
            if btype == "lmg_read_harmonic_i":
                order = int(self._eval_input(inputs.get("ORDER"), ctx) or
                            fields.get("ORDER", 1))
                return lmg.measure_single_harmonic(lmg._active_ch, order, "I")

        # PSU reporters
        psu = ctx.psu
        if psu and psu.connected:
            if btype == "psu_read_voltage":
                return psu.measure().meas_voltage
            if btype == "psu_read_current":
                return psu.measure().meas_current
            if btype == "psu_read_power":
                return psu.measure().power
            if btype == "psu_is_output_on":
                return psu.measure().output_on

        # Measurement reporters
        if dev:
            if btype == "lcr_read_primary":
                return dev.measure().primary
            if btype == "lcr_read_secondary":
                return dev.measure().secondary
            if btype == "lcr_read_phase":
                return dev.measure().phase
            if btype == "lcr_read_mode":
                return dev.measure().mode_label
            if btype == "lcr_read_freq":
                return dev.measure().frequency.label
            if btype == "lcr_is_overload":
                return dev.measure().overload

        return None

    def _sleep(self, secs: float, ctx: ExecContext) -> None:
        deadline = time.time() + secs
        while time.time() < deadline and ctx.running:
            time.sleep(0.05)
