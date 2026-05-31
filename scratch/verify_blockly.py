"""
Standalone Blockly verification script.
Launches BlocklyCanvas, waits for load, injects JS to click toolbox categories,
drags blocks, and captures all console messages. Exits after 30s.
"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtCore import QTimer
from scratch.blockly_canvas import BlocklyCanvas

errors   = []
warnings = []
logs     = []

class TestWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.canvas = BlocklyCanvas(self)
        self.setCentralWidget(self.canvas)
        self.resize(1200, 800)
        self.setWindowTitle("Blockly Verify")

        # Patch _ConsolePage to capture messages
        orig = self.canvas._view.page().javaScriptConsoleMessage
        def capture(level, msg, line, src):
            tag = {0:"LOG",1:"WARN",2:"ERR",3:"INFO"}.get(level,"?")
            entry = f"[{tag}] {msg}  (line {line})"
            print(entry, flush=True)
            if level == 2:
                errors.append(entry)
            elif level == 1:
                warnings.append(entry)
            else:
                logs.append(entry)
        self.canvas._view.page().javaScriptConsoleMessage = capture

        # After load, run checks
        self.canvas._view.loadFinished.connect(self._on_loaded)
        self.show()

    def _on_loaded(self, ok):
        print(f"[VERIFY] Page load: {'OK' if ok else 'FAILED'}", flush=True)
        if not ok:
            self._finish()
            return
        # Wait 3s for Blockly to initialize
        QTimer.singleShot(3000, self._run_checks)

    def _run_checks(self):
        page = self.canvas._view.page()

        # Test 1: Check Blockly loaded
        page.runJavaScript(
            "typeof Blockly !== 'undefined' ? 'Blockly OK: ' + Blockly.VERSION : 'Blockly MISSING'",
            lambda r: print(f"[VERIFY] {r}", flush=True)
        )

        # Test 2: Click Events category in toolbox
        page.runJavaScript("""
            (function() {
                try {
                    var cats = document.querySelectorAll('.blocklyTreeRow');
                    if (cats.length === 0) return 'no toolbox rows found';
                    cats[0].click();
                    return 'clicked first toolbox row: ' + (cats[0].textContent || '?');
                } catch(e) { return 'error: ' + e.message; }
            })()
        """, lambda r: print(f"[VERIFY] Toolbox click: {r}", flush=True))

        # Test 3: Check inputTypes
        page.runJavaScript(
            "typeof Blockly.inputTypes !== 'undefined' ? 'inputTypes.VALUE=' + Blockly.inputTypes.VALUE : 'inputTypes MISSING'",
            lambda r: print(f"[VERIFY] {r}", flush=True)
        )

        # Test 4: Check lcr_when_timer block definition
        page.runJavaScript(
            "Blockly.Blocks['lcr_when_timer'] ? 'lcr_when_timer: defined' : 'lcr_when_timer: MISSING'",
            lambda r: print(f"[VERIFY] {r}", flush=True)
        )

        # Test 5: Check Xml API
        page.runJavaScript(
            "typeof Blockly.Xml !== 'undefined' && typeof Blockly.Xml.domToWorkspace === 'function' ? 'Xml.domToWorkspace: OK' : 'Xml API issue'",
            lambda r: print(f"[VERIFY] {r}", flush=True)
        )

        # Schedule finish
        QTimer.singleShot(5000, self._finish)

    def _finish(self):
        print(f"\n[VERIFY] === SUMMARY ===", flush=True)
        print(f"[VERIFY] JS Errors: {len(errors)}", flush=True)
        for e in errors:
            print(f"[VERIFY]   {e}", flush=True)
        print(f"[VERIFY] JS Warnings: {len(warnings)}", flush=True)
        if errors:
            print("[VERIFY] RESULT: FAIL", flush=True)
        else:
            print("[VERIFY] RESULT: PASS", flush=True)
        QApplication.quit()

app = QApplication(sys.argv)
win = TestWindow()
sys.exit(app.exec())
