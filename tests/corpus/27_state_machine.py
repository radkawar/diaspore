TRANSITIONS = {
    "created": {"pay": "paid", "cancel": "cancelled"},
    "paid": {"ship": "shipped", "refund": "refunded"},
    "shipped": {"deliver": "delivered"},
}

TERMINAL = {"cancelled", "refunded", "delivered"}


class Order:
    def __init__(self, oid):
        self.oid = oid
        self.state = "created"
        self.history = []

    def apply(self, event):
        if self.state in TERMINAL:
            raise RuntimeError(f"order {self.oid} is finished ({self.state})")
        allowed = TRANSITIONS.get(self.state, {})
        if event not in allowed:
            raise ValueError(f"cannot '{event}' from '{self.state}'")
        self.history.append((self.state, event))
        self.state = allowed[event]
        return self.state

    def __repr__(self):
        return f"Order({self.oid}, {self.state})"


def run_events(order, events):
    for ev in events:
        try:
            new = order.apply(ev)
            print(f"{order.oid}: {ev} -> {new}")
        except (ValueError, RuntimeError) as e:
            print(f"{order.oid}: rejected {ev}: {e}")


o1 = Order("A1")
run_events(o1, ["pay", "ship", "deliver", "refund"])
print(o1, o1.history)

o2 = Order("B2")
run_events(o2, ["ship", "cancel", "pay"])
print(o2, o2.history)


def simulate(events):
    counts = {}
    order = Order("SIM")
    for ev in events:
        try:
            order.apply(ev)
            counts[ev] = counts.get(ev, 0) + 1
        except Exception:
            counts["rejected"] = counts.get("rejected", 0) + 1
    return order.state, sorted(counts.items())


print(simulate(["pay", "ship", "deliver"]))
print(simulate(["cancel", "cancel", "pay"]))
print(simulate(["pay", "refund", "ship", "deliver"]))

reachable = set()
frontier = ["created"]
while frontier:
    state = frontier.pop()
    if state in reachable:
        continue
    reachable.add(state)
    for nxt in TRANSITIONS.get(state, {}).values():
        frontier.append(nxt)
print(sorted(reachable))
