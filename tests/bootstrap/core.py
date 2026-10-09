class Counter:
    value: int

    def __init__(self, value):
        self.value = value

    def increment(self):
        self.value += 1
        return self.value


counter = Counter(40)
print(f"counter={counter.increment()}")
data = {"first": 1, "second": 2}
keys = data.keys()
data["third"] = 3
print(list(keys), sorted(keys - ["second"]))
try:
    raise ValueError("inner error")
except ValueError as exc:
    print(f"caught {type(exc).__name__}: {exc}")
finally:
    print("finished", sum(data.values()))
