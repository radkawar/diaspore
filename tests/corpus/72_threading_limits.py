# diaspore-caps: spawn
"""Compare locks, timed waits and thread lifecycle with CPython."""

import threading
from queue import Empty, Full, Queue
from threading import Condition, Event, Lock, RLock, Thread

recursive = RLock()
print("recursive acquire", recursive.acquire(), recursive.acquire())
recursive.release()
recursive.release()
with Condition() as acquired:
    print("default condition acquired", acquired)
try:
    Condition(object())
except AttributeError:
    print("invalid condition lock")

thread = Thread(name="worker", daemon=False)
print("thread configuration", thread.name, thread.daemon)
thread.start()
thread.join()
print("timed completed join", thread.join(0.001), thread.is_alive())

lock = Lock()
print("timed lock", lock.acquire(timeout=0.001))
lock.release()
for operation in [lambda: lock.acquire(False, 0), lambda: lock.acquire(timeout=-2)]:
    try:
        operation()
    except ValueError as exc:
        print(exc)

condition = Condition(lock)
with condition:
    print("timed condition", condition.wait(0.001))
    print("timed predicate", condition.wait_for(lambda: False, 0.001))
    print("condition retains lock", lock.locked())
event = Event()
print("timed event", event.wait(0.001))
event.set()
print("set event", event.wait(0.001))
queue = Queue(1)
print("timed queue put", queue.put(1, timeout=0.001))
try:
    queue.put(2, timeout=0.001)
except Full:
    print("full queue timeout")
print("timed queue get", queue.get(timeout=0.001))
queue.task_done()
try:
    queue.get(timeout=0.001)
except Empty:
    print("empty queue timeout")
print("queue unchanged", queue.empty(), queue.unfinished_tasks)

failures = []
saved_hook = threading.excepthook


def record_failure(args):
    failures.append((args.exc_type.__name__, str(args.exc_value), args.thread.name))


def fail():
    raise ValueError("unhandled worker failure")


threading.excepthook = record_failure
try:
    failed = Thread(target=fail, name="failed-worker", daemon=True)
    failed.start()
    failed.join()
    print("worker exception", failures)
    print("failed thread finished", failed.is_alive())
finally:
    threading.excepthook = saved_hook
