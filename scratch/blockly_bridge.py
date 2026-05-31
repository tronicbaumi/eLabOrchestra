"""
Python ↔ Blockly JavaScript bridge via QWebChannel.

The Bridge object is registered on the channel under the name "bridge".
JavaScript calls Python slots; Python emits signals that JS connects to.
"""

from __future__ import annotations

import json
import time
import threading
from typing import Callable, Optional

from PySide6.QtCore import QObject, Slot, Signal, Property


class BlocklyBridge(QObject):
    """
    Exposed to JavaScript as `window.bridge` via QWebChannel.

    Signals (JS listens via .connect()):
        statusChanged(str)   – run status text ("Running…", "Stopped", …)
        messageChanged(str)  – one-line log / show message
        workspaceReady()     – workspace fully loaded

    Slots (JS calls via bridge.<slot>()):
        on_workspace_change(xml, json_str)
        get_workspace(callback)            – called once on init
    """

    statusChanged  = Signal(str)
    messageChanged = Signal(str)
    workspaceReady = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._workspace_xml: str = ""
        self._program_json:  str = "{}"
        self._change_callbacks: list[Callable[[str, str], None]] = []
        self._saved_xml: str = ""   # XML from last save/load

    # ── Slots called from JavaScript ─────────────────────────────────────────

    @Slot(str, str)
    def on_workspace_change(self, xml: str, json_str: str) -> None:
        """Called every time the Blockly workspace changes."""
        self._workspace_xml = xml
        self._program_json  = json_str
        for cb in list(self._change_callbacks):
            try:
                cb(xml, json_str)
            except Exception:
                pass

    @Slot(result=str)
    def get_workspace(self) -> str:
        """Called once on page init to restore saved workspace."""
        return self._saved_xml

    # ── Python → JS helpers ──────────────────────────────────────────────────

    def set_status(self, msg: str) -> None:
        self.statusChanged.emit(msg)

    def set_message(self, msg: str) -> None:
        self.messageChanged.emit(msg)

    # ── Program access ────────────────────────────────────────────────────────

    @property
    def workspace_xml(self) -> str:
        return self._workspace_xml

    @property
    def program(self) -> dict:
        try:
            return json.loads(self._program_json)
        except (json.JSONDecodeError, ValueError):
            return {"stacks": []}

    def save_xml(self) -> str:
        """Return current XML for persistence."""
        return self._workspace_xml

    def restore_xml(self, xml: str) -> None:
        """Store XML to be sent back to JS on next get_workspace() call."""
        self._saved_xml = xml

    def add_change_callback(self, cb: Callable[[str, str], None]) -> None:
        self._change_callbacks.append(cb)
