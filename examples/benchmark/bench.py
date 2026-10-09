import time


def fib(n):
    return n if n < 2 else fib(n - 1) + fib(n - 2)

t0 = time.monotonic()
f = fib(24)
t1 = time.monotonic()
total = 0
for i in range(300000):
    total += i * i
t2 = time.monotonic()
parts = []
for i in range(30000):
    parts.append(f"item-{i}")
s = ",".join(parts)
t3 = time.monotonic()
d = {}
for i in range(50000):
    d[f"k{i % 1000}"] = i
    hit = d.get(f"k{i % 997}", 0)
t4 = time.monotonic()
print(f"check {f} {total} {len(s)} {len(d)}")
print(
    f"fib(24)={t1-t0:.3f}s loop300k={t2-t1:.3f}s "
    f"strings30k={t3-t2:.3f}s dict50k={t4-t3:.3f}s"
)
