"""An order workflow that suspends for approval, payment, and delivery."""

import random
import time

import flow


def build_order():
    return {
        "id": f"ord-{random.randint(1000, 9999)}",
        "items": [
            {"sku": "bolt", "qty": 4, "unit": 12.5},
            {"sku": "plate", "qty": 2, "unit": 40.0},
        ],
        "created": time.time(),
    }


def total(order):
    return sum(item["qty"] * item["unit"] for item in order["items"])


order = build_order()
amount = total(order)
print(f"[wf] {order['id']}: created, total {amount}")

if amount > 100:
    print(f"[wf] {order['id']}: needs human approval")
    decision = flow.suspend({
        "step": "approval",
        "order": order["id"],
        "amount": amount,
    })
    if decision != "approved":
        print(f"[wf] {order['id']}: rejected ({decision})")
        exit(1)
    print(f"[wf] {order['id']}: approved")

payment = flow.suspend({
    "step": "payment",
    "order": order["id"],
    "amount": amount,
})
print(f"[wf] {order['id']}: paid via {payment['txn']}")

for attempt in range(1, 4):
    print(f"[wf] {order['id']}: shipping attempt {attempt} scheduled")
    result = flow.suspend({"step": "ship", "order": order["id"], "attempt": attempt})
    if result == "delivered":
        print(f"[wf] {order['id']}: delivered on attempt {attempt}")
        break
    print(f"[wf] {order['id']}: attempt {attempt} failed ({result})")
else:
    print(f"[wf] {order['id']}: undeliverable, refunding")
    exit(2)

print(f"[wf] {order['id']}: complete")
