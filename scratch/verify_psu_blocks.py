"""Quick check that PSU block definitions are available in the Blockly page."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from scratch.blockly_canvas import BlocklyCanvas

app = QApplication(sys.argv)
canvas = BlocklyCanvas()
canvas.show()

def check():
    p = canvas._view.page()
    checks = [
        ("psu_set_voltage", "Blockly.Blocks['psu_set_voltage'] ? 'OK' : 'MISSING'"),
        ("psu_set_current", "Blockly.Blocks['psu_set_current'] ? 'OK' : 'MISSING'"),
        ("psu_output",      "Blockly.Blocks['psu_output'] ? 'OK' : 'MISSING'"),
        ("psu_read_voltage","Blockly.Blocks['psu_read_voltage'] ? 'OK' : 'MISSING'"),
        ("psu_read_current","Blockly.Blocks['psu_read_current'] ? 'OK' : 'MISSING'"),
        ("psu_read_power",  "Blockly.Blocks['psu_read_power'] ? 'OK' : 'MISSING'"),
        ("psu_is_output_on","Blockly.Blocks['psu_is_output_on'] ? 'OK' : 'MISSING'"),
    ]
    pending = [len(checks)]
    results = {}
    def make_cb(name):
        def cb(r):
            results[name] = r
            pending[0] -= 1
            if pending[0] == 0:
                for k, v in results.items():
                    print(f"  {k}: {v}", flush=True)
                failed = [k for k,v in results.items() if v != 'OK']
                if failed:
                    print(f"FAIL: {failed}", flush=True)
                else:
                    print("PASS: all PSU blocks defined", flush=True)
                QTimer.singleShot(500, app.quit)
        return cb
    for name, js in checks:
        p.runJavaScript(js, make_cb(name))

def on_load(ok):
    print(f"Page load: {'OK' if ok else 'FAILED'}", flush=True)
    if ok:
        QTimer.singleShot(3000, check)
    else:
        app.quit()

canvas._view.loadFinished.connect(on_load)
sys.exit(app.exec())
