import re, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

with open(os.path.join(os.path.dirname(__file__), 'blockly_page.html')) as f:
    html = f.read()

defined = set(re.findall(r'"type":\s*"(lmg_[^"]+)"', html))
toolbox = set(re.findall(r'type:\s*"(lmg_[^"]+)"', html))
print(f"Defined ({len(defined)}): {sorted(defined)}")
print(f"Toolbox ({len(toolbox)}): {sorted(toolbox)}")
missing = defined - toolbox
print(f"Missing from toolbox: {missing or 'none'}")

with open(os.path.join(os.path.dirname(__file__), 'blockly_executor.py')) as f:
    ex = f.read()
not_in_ex = [b for b in defined if b not in ex]
print(f"Not handled in executor: {not_in_ex or 'none'}")
print("Static check OK" if not missing and not not_in_ex else "ISSUES FOUND")
