"""
AI chatbot panel — generate Blockly programs from natural-language descriptions.

Uses the Anthropic Claude API (streaming) to turn plain-English prompts into
valid Blockly XML that can be loaded directly into the workspace.

API key is read from the ANTHROPIC_API_KEY environment variable.
If it is absent the user can paste it into the key field at the top of the panel.
"""

from __future__ import annotations

import os
import re
import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal, QObject, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextBrowser,
    QTextEdit, QPushButton, QLabel, QFrame, QSplitter,
    QLineEdit, QSizePolicy, QScrollArea,
)
from PySide6.QtGui import QTextCursor, QFont, QKeyEvent, QColor

if TYPE_CHECKING:
    from scratch.blockly_canvas import BlocklyCanvas

# ── colours (match app palette) ───────────────────────────────────────────────
_DARK   = "#1A1A2A"
_CARD   = "#252535"
_ACCENT = "#4C97FF"
_TEXT   = "#FFFFFF"
_DIM    = "#8888AA"
_USER   = "#2E3F6F"    # user bubble background
_ASST   = "#1E2E3E"    # assistant bubble background
_CODE   = "#0D1F2D"    # XML code block background


# ── system prompt ─────────────────────────────────────────────────────────────

_SYSTEM = """\
You are an expert assistant that writes Blockly visual programs for the
eLabOrchestra lab-automation platform. eLabOrchestra controls four instruments:
Hantek RLC 1733C (LCR meter), OWON SP3103 (power supply), ZES Zimmer LMG450
(power analyser), and Magtrol DSP7000 (dynamometer controller). It also has a
live Dashboard for showing gauges, charts, and interactive controls.

When the user asks you to create a program, respond with a brief explanation
followed by the XML wrapped in a ```xml ... ``` code fence. For questions or
clarifications you may respond in plain text without XML.

════════════════════════════════════════
AVAILABLE BLOCK TYPES
════════════════════════════════════════

── Events ──────────────────────────────
lcr_when_start           hat block — program entry point
lcr_when_timer           field INTERVAL (number, seconds)

── Hantek LCR 1833C ────────────────────
lcr_set_mode             field MODE: Rs Rp Cs Cp Ls Lp D Q DCR Z θ
lcr_set_freq             field FREQ: 100Hz 120Hz 1kHz 10kHz 100kHz
lcr_show_value           value VALUE, value LABEL
lcr_tare                 (no fields)
lcr_read_primary         reporter — primary value
lcr_read_secondary       reporter — secondary value

── OWON SP3103 Power Supply ────────────
psu_output               field STATE: ON OFF
psu_set_voltage          value VOLTS
psu_set_current_limit    value AMPS
psu_read_voltage         reporter
psu_read_current         reporter

── ZES Zimmer LMG450 ───────────────────
lmg_select_channel       field CH: 1 2 3 4
lmg_set_averaging        value COUNT
lmg_integration_start    (no fields)
lmg_integration_stop     (no fields)
lmg_integration_reset    (no fields)
lmg_read_voltage         reporter (Vrms)
lmg_read_current         reporter (Arms)
lmg_read_active_power    reporter (W)
lmg_read_reactive_power  reporter (VAr)
lmg_read_apparent_power  reporter (VA)
lmg_read_power_factor    reporter (λ)
lmg_read_phase_angle     reporter (°)
lmg_read_frequency       reporter (Hz)
lmg_read_energy          reporter (Wh)

── Magtrol DSP7000 Dynamometer ─────────
dsp_select_channel       field CHANNEL: 1 2
dsp_set_speed            value SPEED (rpm)
dsp_reset_speed          (no fields)
dsp_set_torque           value TORQUE (N·m)
dsp_reset_torque         (no fields)
dsp_set_current          value CURRENT (%)
dsp_ramp_up              field PROFILE: linear cosine  value RATE_TIME
dsp_ramp_down            field PROFILE: linear cosine  value RATE_TIME
dsp_abort_ramp           (no fields)
dsp_reset_channel        (no fields)
dsp_set_speed_alarm      value SPEED
dsp_set_torque_alarm     value TORQUE
dsp_set_power_alarm      value POWER
dsp_alarms_enable        field STATE: enabled disabled
dsp_freeze_pid           field STATE: freeze unfreeze
dsp_tare                 field STATE: ON OFF
dsp_save                 (no fields)
dsp_read_speed           reporter  value CHANNEL
dsp_read_torque          reporter  value CHANNEL
dsp_read_power           reporter  value CHANNEL
dsp_read_direction       reporter  value CHANNEL

── Dashboard Widgets ───────────────────
Configure once (at start):
  dash_config_gauge      field NAME LABEL COLOUR  value MIN MAX
  dash_config_yt_chart   field NAME LABEL COLOUR  value WINDOW
  dash_config_xy_chart   field NAME LABEL COLOUR
  dash_config_text       field NAME LABEL COLOUR
  dash_config_button     field NAME LABEL COLOUR
  dash_config_checkbox   field NAME LABEL DEFAULT(true/false)
  dash_config_dropdown   field NAME LABEL OPTIONS(comma-separated)
  dash_config_knob       field NAME LABEL COLOUR  value MIN MAX STEP
  dash_config_switch     field NAME ON_LABEL OFF_LABEL ON_COLOUR
  dash_config_slider     field NAME LABEL COLOUR  value MIN MAX STEP

Update in loop:
  dash_gauge_set         field NAME  value VALUE
  dash_yt_add            field NAME  value VALUE
  dash_xy_add            field NAME  value X Y
  dash_text_set          field NAME  value VALUE
  dash_knob_set          field NAME  value VALUE
  dash_switch_set        field NAME  field STATE: ON OFF
  dash_slider_set        field NAME  value VALUE

Read-back reporters:
  dash_button_clicked    field NAME → Boolean
  dash_checkbox_state    field NAME → Boolean
  dash_dropdown_value    field NAME → String
  dash_knob_value        field NAME → Number
  dash_switch_state      field NAME → Boolean
  dash_slider_value      field NAME → Number

── Control ─────────────────────────────
lcr_repeat_while         value COND  statement DO
controls_if              value IF0   statement DO0
lcr_break                (no fields)

── Timing ──────────────────────────────
lcr_wait                 value SECS
lcr_beep                 (no fields)

── Variables ───────────────────────────
variables_set            field VAR  value VALUE
variables_get            field VAR → value

── Math ────────────────────────────────
math_number              field NUM
math_arithmetic          field OP: ADD MINUS MULTIPLY DIVIDE POWER  value A B
math_single              field OP: ROOT ABS NEG LN LOG10 ...  value NUM

── Logic ───────────────────────────────
logic_boolean            field BOOL: TRUE FALSE
logic_compare            field OP: EQ NEQ LT LTE GT GTE  value A B
logic_operation          field OP: AND OR  value A B
logic_negate             value BOOL

── Text ────────────────────────────────
text                     field TEXT
text_join                mutation items="2"  value ADD0 ADD1 ...

── Logging ─────────────────────────────
lcr_log_start            field FILENAME (e.g. log.csv)
lcr_log_value            value VALUE  value LABEL
lcr_log_stop             (no fields)

════════════════════════════════════════
XML FORMAT
════════════════════════════════════════

Complete program skeleton:

```xml
<xml xmlns="https://developers.google.com/blockly/xml">
  <block type="lcr_when_start" x="20" y="20">
    <next>
      <!-- first block in sequence -->
      <block type="psu_output">
        <field name="STATE">ON</field>
        <next>
          <block type="lcr_wait">
            <value name="SECS">
              <block type="math_number">
                <field name="NUM">1</field>
              </block>
            </value>
          </block>
        </next>
      </block>
    </next>
  </block>
</xml>
```

Rules:
• Every program starts with lcr_when_start (or lcr_when_timer).
• Sequential blocks are chained via <next>...</next>.
• Value inputs wrap their child block in <value name="NAME">...</value>.
• Statement bodies (loops, if-branches) use <statement name="DO">...</statement>.
• Inline number literals use <block type="math_number"><field name="NUM">42</field></block>.
• Inline string literals use <block type="text"><field name="TEXT">hello</field></block>.
• x/y attributes only on the root (top-level) block.
• Always include xmlns="https://developers.google.com/blockly/xml" on the <xml> tag.
"""


# ── streaming worker ───────────────────────────────────────────────────────────

class _StreamWorker(QObject):
    """
    Runs the Anthropic streaming API in a background thread and emits
    signals back to the main thread.
    """

    chunk    = Signal(str)     # incremental text
    done     = Signal(str)     # full assembled response
    error    = Signal(str)     # error message

    def __init__(self, api_key: str, messages: list[dict], parent=None):
        super().__init__(parent)
        self._api_key  = api_key
        self._messages = messages

    def run(self) -> None:
        try:
            import anthropic                               # optional dependency
            client = anthropic.Anthropic(api_key=self._api_key)
            full = ""
            with client.messages.stream(
                model="claude-opus-4-5",
                max_tokens=4096,
                system=_SYSTEM,
                messages=self._messages,
            ) as stream:
                for text in stream.text_stream:
                    full += text
                    self.chunk.emit(text)
            self.done.emit(full)
        except ImportError:
            self.error.emit(
                "The 'anthropic' package is not installed.\n"
                "Run:  pip install anthropic"
            )
        except Exception as exc:
            self.error.emit(str(exc))


# ── main panel ─────────────────────────────────────────────────────────────────

class AIPanel(QWidget):
    """
    Chat panel that lets the user describe a lab automation program in plain
    English and get back a Blockly XML program generated by Claude.

    Pass a reference to `blockly_canvas` so the panel can inject the generated
    XML directly into the workspace.
    """

    # Emitted when the user clicks "Apply to Blockly" — carries the XML string.
    xml_ready = Signal(str)

    def __init__(self, blockly_canvas=None, parent=None):
        super().__init__(parent)
        self._canvas     = blockly_canvas
        self._history: list[dict] = []    # Anthropic messages list
        self._pending_xml: str = ""       # XML extracted from last response
        self._worker_thread: threading.Thread | None = None

        self._build_ui()
        self._apply_styles()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── API key bar ───────────────────────────────────────────────────────
        key_bar = QFrame()
        key_bar.setObjectName("keyBar")
        key_bar.setFixedHeight(38)
        key_layout = QHBoxLayout(key_bar)
        key_layout.setContentsMargins(10, 4, 10, 4)
        key_layout.setSpacing(6)

        key_lbl = QLabel("API Key:")
        key_lbl.setObjectName("dimLabel")
        key_layout.addWidget(key_lbl)

        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText(
            "sk-ant-…  (or set ANTHROPIC_API_KEY env var)")
        self._key_edit.setEchoMode(QLineEdit.Password)
        self._key_edit.setObjectName("keyEdit")
        # Pre-fill from environment
        env_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if env_key:
            self._key_edit.setText(env_key)
            self._key_edit.setPlaceholderText("(from ANTHROPIC_API_KEY env var)")
        key_layout.addWidget(self._key_edit, 1)

        self._key_toggle = QPushButton("Show")
        self._key_toggle.setObjectName("smallBtn")
        self._key_toggle.setFixedWidth(48)
        self._key_toggle.clicked.connect(self._toggle_key_visibility)
        key_layout.addWidget(self._key_toggle)

        root.addWidget(key_bar)

        # ── separator ─────────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setObjectName("separator")
        root.addWidget(sep)

        # ── chat history ──────────────────────────────────────────────────────
        self._chat = QTextBrowser()
        self._chat.setObjectName("chatView")
        self._chat.setOpenExternalLinks(False)
        self._chat.setReadOnly(True)
        root.addWidget(self._chat, 1)

        # ── apply bar (hidden until XML is ready) ─────────────────────────────
        self._apply_bar = QFrame()
        self._apply_bar.setObjectName("applyBar")
        self._apply_bar.setFixedHeight(44)
        self._apply_bar.setVisible(False)
        apply_layout = QHBoxLayout(self._apply_bar)
        apply_layout.setContentsMargins(10, 6, 10, 6)
        apply_layout.setSpacing(8)

        self._apply_lbl = QLabel("✓ Program ready")
        self._apply_lbl.setObjectName("applyLabel")
        apply_layout.addWidget(self._apply_lbl)
        apply_layout.addStretch()

        self._btn_apply = QPushButton("⬆  Apply to Blockly")
        self._btn_apply.setObjectName("applyBtn")
        self._btn_apply.clicked.connect(self._apply_xml)
        apply_layout.addWidget(self._btn_apply)

        self._btn_append = QPushButton("➕  Append to Blockly")
        self._btn_append.setObjectName("appendBtn")
        self._btn_append.clicked.connect(self._append_xml)
        apply_layout.addWidget(self._btn_append)

        root.addWidget(self._apply_bar)

        # ── input area ────────────────────────────────────────────────────────
        input_frame = QFrame()
        input_frame.setObjectName("inputFrame")
        input_layout = QVBoxLayout(input_frame)
        input_layout.setContentsMargins(8, 6, 8, 8)
        input_layout.setSpacing(4)

        hint = QLabel(
            "Describe the program you want — e.g. "
            "\"measure LCR impedance every second and log to CSV\""
        )
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        input_layout.addWidget(hint)

        input_row = QHBoxLayout()
        input_row.setSpacing(6)

        self._input = QTextEdit()
        self._input.setObjectName("inputEdit")
        self._input.setFixedHeight(72)
        self._input.setPlaceholderText("Type your prompt here… (Ctrl+Enter to send)")
        self._input.installEventFilter(self)
        input_row.addWidget(self._input, 1)

        btn_col = QVBoxLayout()
        btn_col.setSpacing(4)

        self._btn_send = QPushButton("Send")
        self._btn_send.setObjectName("sendBtn")
        self._btn_send.setFixedSize(72, 32)
        self._btn_send.clicked.connect(self._send)
        btn_col.addWidget(self._btn_send)

        self._btn_clear_chat = QPushButton("Clear")
        self._btn_clear_chat.setObjectName("clearBtn")
        self._btn_clear_chat.setFixedSize(72, 28)
        self._btn_clear_chat.clicked.connect(self._clear_chat)
        btn_col.addWidget(self._btn_clear_chat)

        btn_col.addStretch()
        input_row.addLayout(btn_col)
        input_layout.addLayout(input_row)

        root.addWidget(input_frame)

    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
            AIPanel {{
                background: {_DARK};
            }}
            #keyBar {{
                background: #0F0F1A;
                border-bottom: 1px solid #222240;
            }}
            #keyEdit {{
                background: {_CARD};
                color: {_TEXT};
                border: 1px solid #333355;
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 8pt;
            }}
            #smallBtn {{
                background: #333355;
                color: {_DIM};
                border: none;
                border-radius: 4px;
                font-size: 8pt;
                padding: 2px 4px;
            }}
            #smallBtn:hover {{ background: #444477; color: {_TEXT}; }}
            #dimLabel {{
                color: {_DIM};
                font-size: 8pt;
            }}
            #separator {{
                color: #222240;
                background: #222240;
                max-height: 1px;
            }}
            #chatView {{
                background: {_DARK};
                color: {_TEXT};
                border: none;
                font-size: 9pt;
                padding: 4px;
            }}
            #applyBar {{
                background: #1B3320;
                border-top: 1px solid #2E5730;
            }}
            #applyLabel {{
                color: #81C784;
                font-size: 9pt;
                font-weight: bold;
            }}
            #applyBtn {{
                background: #2E7D32;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 4px 14px;
                font-weight: bold;
                font-size: 9pt;
            }}
            #applyBtn:hover {{ background: #388E3C; }}
            #appendBtn {{
                background: #1565C0;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 4px 14px;
                font-weight: bold;
                font-size: 9pt;
            }}
            #appendBtn:hover {{ background: #1976D2; }}
            #inputFrame {{
                background: #0F0F1A;
                border-top: 1px solid #222240;
            }}
            #hintLabel {{
                color: {_DIM};
                font-size: 8pt;
                font-style: italic;
            }}
            #inputEdit {{
                background: {_CARD};
                color: {_TEXT};
                border: 1px solid #333355;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 9pt;
            }}
            #inputEdit:focus {{
                border-color: {_ACCENT};
            }}
            #sendBtn {{
                background: {_ACCENT};
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
                font-size: 9pt;
            }}
            #sendBtn:hover {{ background: #5BA3FF; }}
            #sendBtn:disabled {{ background: #333355; color: {_DIM}; }}
            #clearBtn {{
                background: #333355;
                color: {_DIM};
                border: none;
                border-radius: 4px;
                font-size: 8pt;
            }}
            #clearBtn:hover {{ background: #444477; color: {_TEXT}; }}
        """)

    # ── event filter (Ctrl+Enter to send) ────────────────────────────────────

    def eventFilter(self, obj, event) -> bool:
        if obj is self._input and isinstance(event, QKeyEvent):
            if (event.type() == QKeyEvent.KeyPress
                    and event.key() == Qt.Key_Return
                    and event.modifiers() & Qt.ControlModifier):
                self._send()
                return True
        return super().eventFilter(obj, event)

    # ── actions ───────────────────────────────────────────────────────────────

    def _toggle_key_visibility(self) -> None:
        if self._key_edit.echoMode() == QLineEdit.Password:
            self._key_edit.setEchoMode(QLineEdit.Normal)
            self._key_toggle.setText("Hide")
        else:
            self._key_edit.setEchoMode(QLineEdit.Password)
            self._key_toggle.setText("Show")

    def _send(self) -> None:
        prompt = self._input.toPlainText().strip()
        if not prompt:
            return

        api_key = self._key_edit.text().strip()
        if not api_key:
            self._append_error(
                "Please enter your Anthropic API key above, "
                "or set the ANTHROPIC_API_KEY environment variable."
            )
            return

        self._input.clear()
        self._apply_bar.setVisible(False)
        self._pending_xml = ""
        self._btn_send.setEnabled(False)

        # Show user bubble
        self._append_user(prompt)

        # Add to conversation history
        self._history.append({"role": "user", "content": prompt})

        # Open assistant bubble (will be filled by streaming)
        self._chat.append("")
        self._start_assistant_bubble()

        # Launch streaming in background thread
        worker = _StreamWorker(api_key, list(self._history))
        worker.chunk.connect(self._on_chunk)
        worker.done.connect(self._on_done)
        worker.error.connect(self._on_error)

        t = threading.Thread(target=worker.run, daemon=True)
        t.start()
        self._worker = worker   # keep alive

    def _clear_chat(self) -> None:
        self._chat.clear()
        self._history.clear()
        self._apply_bar.setVisible(False)
        self._pending_xml = ""

    def _apply_xml(self) -> None:
        if self._pending_xml and self._canvas:
            self._canvas.clear_workspace()
            self._canvas.load_workspace_xml(self._pending_xml)
            self._append_system("✓ Program applied — workspace replaced.")
        elif not self._canvas:
            self._append_error("No Blockly canvas connected.")

    def _append_xml(self) -> None:
        """Append the generated XML to the current workspace (don't clear first)."""
        if self._pending_xml and self._canvas:
            self._canvas.load_workspace_xml(self._pending_xml)
            self._append_system("✓ Program appended to workspace.")
        elif not self._canvas:
            self._append_error("No Blockly canvas connected.")

    # ── streaming callbacks ───────────────────────────────────────────────────

    def _start_assistant_bubble(self) -> None:
        """Insert the opening HTML for an assistant message bubble."""
        cursor = self._chat.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(
            f'<div style="'
            f'background:{_ASST}; border-radius:8px; '
            f'padding:8px 12px; margin:6px 2px 2px 2px;'
            f'">'
            f'<span style="color:{_DIM}; font-size:8pt;">🤖 Claude</span><br>'
        )
        self._chat.setTextCursor(cursor)
        self._chat.ensureCursorVisible()
        self._asst_text = ""    # accumulate for done callback

    def _on_chunk(self, text: str) -> None:
        """Append a streaming chunk to the current assistant bubble."""
        self._asst_text += text
        cursor = self._chat.textCursor()
        cursor.movePosition(QTextCursor.End)
        # Escape HTML special chars for safe insertion
        safe = (text
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\n", "<br>")
                .replace(" ", "&nbsp;"))
        cursor.insertHtml(f'<span style="color:{_TEXT};">{safe}</span>')
        self._chat.setTextCursor(cursor)
        self._chat.ensureCursorVisible()

    def _on_done(self, full_text: str) -> None:
        """Full response received — close bubble, extract XML if present."""
        # Close the assistant bubble div
        cursor = self._chat.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml("</div>")
        self._chat.setTextCursor(cursor)

        # Add to conversation history
        self._history.append({"role": "assistant", "content": full_text})

        # Extract XML from ```xml ... ``` fences
        xml = self._extract_xml(full_text)
        if xml:
            self._pending_xml = xml
            block_count = xml.count('<block type=')
            self._apply_lbl.setText(
                f"✓ Program ready  ({block_count} block{'s' if block_count != 1 else ''})"
            )
            self._apply_bar.setVisible(True)

        self._btn_send.setEnabled(True)
        self._chat.ensureCursorVisible()

    def _on_error(self, msg: str) -> None:
        self._append_error(msg)
        self._btn_send.setEnabled(True)

    # ── HTML helpers ──────────────────────────────────────────────────────────

    def _append_user(self, text: str) -> None:
        safe = (text
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\n", "<br>"))
        self._chat.append(
            f'<div style="'
            f'background:{_USER}; border-radius:8px; '
            f'padding:8px 12px; margin:2px 2px 6px 40px;'
            f'">'
            f'<span style="color:{_DIM}; font-size:8pt;">👤 You</span><br>'
            f'<span style="color:{_TEXT};">{safe}</span>'
            f'</div>'
        )
        self._chat.ensureCursorVisible()

    def _append_error(self, msg: str) -> None:
        safe = msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self._chat.append(
            f'<div style="'
            f'background:#3E1515; border-radius:8px; border-left:3px solid #f44336; '
            f'padding:8px 12px; margin:4px 2px;'
            f'">'
            f'<span style="color:#EF9A9A;">⚠ {safe}</span>'
            f'</div>'
        )
        self._chat.ensureCursorVisible()

    def _append_system(self, msg: str) -> None:
        self._chat.append(
            f'<div style="'
            f'padding:4px 12px; margin:2px;'
            f'">'
            f'<span style="color:{_DIM}; font-size:8pt; font-style:italic;">{msg}</span>'
            f'</div>'
        )
        self._chat.ensureCursorVisible()

    # ── XML extraction ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_xml(text: str) -> str:
        """
        Extract the first XML block from ```xml ... ``` fences.
        Falls back to searching for a bare <xml ...> ... </xml> fragment.
        """
        # Fenced code block
        m = re.search(r"```xml\s*([\s\S]*?)```", text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        # Bare XML
        m = re.search(r"(<xml[\s\S]*?</xml>)", text)
        if m:
            return m.group(1).strip()
        return ""
