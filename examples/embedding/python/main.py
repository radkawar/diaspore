"""Load a pricing policy and supply customer lookup bindings."""

import json
import sys
from pathlib import Path

from diaspore import Machine

CUSTOMERS = {
    "acme": ("enterprise", 100_000),
    "startup": ("pro", 5_000),
    "anon": ("free", 100),
}

bundle = json.loads(Path(sys.argv[1]).read_text())
vm = Machine(bundle, bindings={"host": {
    "customer_tier": lambda name: CUSTOMERS[name][0],
    "credit_limit": lambda name: CUSTOMERS[name][1],
}}, max_steps=100_000)
if vm.run() != 0:
    raise SystemExit("policy initialization failed")
for name, qty, unit, rush in [
    ("acme", 100, 9.99, True),
    ("startup", 3, 49.0, False),
    ("anon", 5, 2.0, False),
]:
    order = {"customer": name, "items": [{"qty": qty, "unit": unit}], "rush": rush}
    price = vm.call("plugin", "price", order)
    risk = vm.call("plugin", "risk_score", order)
    print(f"{name}: total={price:.2f} risk={risk}")
