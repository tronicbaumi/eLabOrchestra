"""Verify all LMG450 Blockly blocks load without errors."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from scratch.blockly_canvas import BlocklyCanvas

app = QApplication(sys.argv)
canvas = BlocklyCanvas()
canvas.show()

LMG_BLOCKS = [
    "lmg_select_channel", "lmg_set_averaging",
    "lmg_integration_start", "lmg_integration_stop", "lmg_integration_reset",
    "lmg_read_voltage", "lmg_read_current", "lmg_read_power",
    "lmg_read_reactive", "lmg_read_apparent", "lmg_read_pf",
    "lmg_read_phase", "lmg_read_frequency", "lmg_read_energy",
    "lmg_read_charge", "lmg_read_thd_v", "lmg_read_thd_i",
    "lmg_read_harmonic_v", "lmg_read_harmonic_i",
    "lmg_read_psum", "lmg_read_qsum", "lmg_read_ssum",
]

def check():
    p = canvas._view.page()
    results = {}
    pending = [len(LMG_BLOCKS)]
    def make_cb(name):
        def cb(r):
            results[name] = r
            pending[0] -= 1
            if pending[0] == 0:
                for k, v in results.items():
                    print(f"  {'✓' if v=='OK' else '✗'} {k}: {v}", flush=True)
                failed = [k for k,v in results.items() if v != 'OK']
                print(f"\n{'PASS: all ' + str(len(LMG_BLOCKS)) + ' LMG blocks defined' if not failed else 'FAIL: ' + str(failed)}", flush=True)
                QTimer.singleShot(300, app.quit)
        return cb
    for name in LMG_BLOCKS:
        p.runJavaScript(f"Blockly.Blocks['{name}'] ? 'OK' : 'MISSING'", make_cb(name))

def on_load(ok):
    print(f"Page: {'OK' if ok else 'FAILED'}", flush=True)
    if ok: QTimer.singleShot(3000, check)
    else: app.quit()

canvas._view.loadFinished.connect(on_load)
sys.exit(app.exec())
