# diaspore-caps: spawn
"""Pin explicit rejections for operations missing from native tasks."""

from queue import Queue
from threading import Condition, Event, Lock, RLock, Thread


def unsupported(label, operation):
    try:
        operation()
    except NotImplementedError:
        print(label)
    else:
        raise AssertionError(label)


unsupported("reentrant lock", RLock)
unsupported("default condition lock", Condition)
unsupported("custom condition lock", lambda: Condition(object()))
unsupported("thread name", lambda: Thread(name="worker", daemon=True))
thread = Thread()
unsupported("non-daemon thread", thread.start)
thread.daemon = True
thread.start()
thread.join()
unsupported("timed thread join", lambda: thread.join(1))

lock = Lock()
unsupported("timed lock", lambda: lock.acquire(timeout=1))
for operation in [lambda: lock.acquire(False, 0), lambda: lock.acquire(timeout=-2)]:
    try:
        operation()
    except ValueError as exc:
        print(exc)

condition = Condition(lock)
with condition:
    unsupported("timed condition", lambda: condition.wait(1))
    unsupported("timed predicate", lambda: condition.wait_for(lambda: False, 1))
    print("condition retains lock", lock.locked())
event = Event()
unsupported("timed event", lambda: event.wait(1))
queue = Queue(1)
unsupported("timed queue put", lambda: queue.put(1, timeout=1))
unsupported("timed queue get", lambda: queue.get(timeout=1))
print("rejected put unchanged", queue.empty(), queue.unfinished_tasks)


def fail():
    raise ValueError("unhandled worker failure")


failed = Thread(target=fail, daemon=True)
failed.start()
unsupported("unhandled target exception", failed.join)
print("failed thread finished", failed.is_alive())
