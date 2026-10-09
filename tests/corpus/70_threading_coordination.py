# diaspore-caps: spawn
"""Exercise deterministic daemon-thread and condition synchronization."""

from threading import Condition, Event, Lock, Thread

lock = Lock()
print(lock.locked(), lock.acquire(False), lock.locked())
print(lock.acquire(False), lock.acquire(timeout=0))
lock.release()
with lock as acquired:
    print(acquired, lock.locked())
print(lock.locked())
try:
    lock.release()
except RuntimeError as exc:
    print(exc)

entered = Event()
proceed = Event()
result = []


def calculate(value, *, scale):
    entered.set()
    proceed.wait()
    result.append(value * scale)


thread = Thread(target=calculate, args=(6,), kwargs={"scale": 7}, daemon=True)
print(thread.is_alive(), thread.daemon)
try:
    thread.join()
except RuntimeError as exc:
    print(exc)
print(thread.start())
print(entered.wait(), thread.is_alive())
try:
    thread.daemon = False
except RuntimeError as exc:
    print(exc)
print(thread.join(0), thread.is_alive())
proceed.set()
print(thread.join(), thread.is_alive(), result)
print(thread.join())
try:
    thread.start()
except RuntimeError as exc:
    print(exc)

flag = Event()
print(flag.is_set(), flag.wait(0), flag.wait(-1))
print(flag.set(), flag.is_set(), flag.wait(0))
print(flag.clear(), flag.is_set())

condition = Condition(Lock())
ready = [Event(), Event()]
first_done = Event()
state = {"permits": 0}
completed = []


def wait_for_permit(index):
    with condition:
        ready[index].set()
        while state["permits"] == 0:
            condition.wait()
        state["permits"] -= 1
        completed.append(index)
    first_done.set()


workers = [Thread(target=wait_for_permit, args=(i,), daemon=True) for i in range(2)]
for worker in workers:
    worker.start()
for event in ready:
    event.wait()
with condition:
    condition.notify(0)
    state["permits"] = 1
    condition.notify(1)
first_done.wait()
with condition:
    print("one notified", len(completed))
    state["permits"] = 1
    condition.notify_all()
for worker in workers:
    worker.join()
print("all notified", sorted(completed))
with condition:
    print(condition.wait(0), condition.wait(-1))
    print(condition.wait_for(lambda: "ready"))
    print(condition.wait_for(lambda: False, 0))
for method in [condition.wait, condition.notify, condition.notify_all]:
    try:
        method()
    except RuntimeError as exc:
        print(exc)

counter = {"value": 0}


def increment():
    for i in range(40):
        with lock:
            counter["value"] += 1


workers = [Thread(target=increment, daemon=True) for i in range(4)]
for worker in workers:
    worker.start()
for worker in workers:
    worker.join()
print("counter", counter["value"])


class NamedWork(Thread):
    def run(self):
        result.append("subclass")


worker = NamedWork(daemon=True)
worker.start()
worker.join()
print(result)
