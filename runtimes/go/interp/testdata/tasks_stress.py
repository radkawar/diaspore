import tasks

counter = {"n": 0}
lk = tasks.lock()

def bump(times):
    for _ in range(times):
        with lk:
            counter["n"] += 1

ws = [tasks.spawn(bump, 250) for _ in range(8)]
for w in ws:
    w.join()

out = tasks.channel()
done = tasks.channel()

def feed(base):
    for i in range(50):
        out.send(base + i)
    done.send(True)

for b in [0, 1000, 2000, 3000]:
    tasks.spawn(feed, b)

def closer():
    for _ in range(4):
        done.recv()
    out.close()

tasks.spawn(closer)
total = 0
for v in out:
    total += v
print(counter["n"], total)
