# diaspore-caps: spawn
"""Concurrency with deterministic observable output: results are collected via
join order and channel causality, never print interleaving."""

import tasks


def square(n):
    return n * n


ts = [tasks.spawn(square, i) for i in range(8)]
print([t.join() for t in ts])
print(ts[0].done())

ch = tasks.channel()


def produce(n):
    for i in range(n):
        ch.send(i)
    ch.close()
    return "produced"


def consume():
    total, count = 0, 0
    for v in ch:
        total += v
        count += 1
    return total, count


p = tasks.spawn(produce, 100)
c = tasks.spawn(consume)
print(p.join(), c.join())

req, rep = tasks.channel(1), tasks.channel(1)


def worker():
    got = req.recv()
    rep.send(got + " pong")
    return "worker done"


w = tasks.spawn(worker)
req.send("ping")
print(rep.recv(), w.join())

counter = {"n": 0}
lk = tasks.lock()


def bump(times):
    for _ in range(times):
        with lk:
            counter["n"] += 1


workers = [tasks.spawn(bump, 500) for _ in range(4)]
for wk in workers:
    wk.join()
print(counter["n"])


def fan_in(sources):
    out = tasks.channel()
    done = tasks.channel()

    def pump(src):
        for v in src:
            out.send(v)
        done.send(True)

    for src in sources:
        tasks.spawn(pump, src)

    def closer():
        for _ in range(len(sources)):
            done.recv()
        out.close()

    tasks.spawn(closer)
    return out


a, b = tasks.channel(), tasks.channel()


def feed(chan, values):
    for v in values:
        chan.send(v)
    chan.close()


tasks.spawn(feed, a, [1, 2, 3])
tasks.spawn(feed, b, [10, 20, 30])
merged = fan_in([a, b])
print(sorted(merged))


def boom():
    raise ValueError("task failed")


t = tasks.spawn(boom)
try:
    t.join()
except ValueError as e:
    print("caught from task:", e)

cc = tasks.channel()
cc.close()
try:
    cc.send(1)
except RuntimeError as e:
    print(e)
try:
    cc.recv()
except RuntimeError as e:
    print(e)

print("tasks done")
