# diaspore-caps: spawn
"""Exercise FIFO capacity, nonblocking errors, and completion joins."""

from queue import Empty, Full, Queue
from threading import Event, Thread

queue = Queue(2)
print(queue.qsize(), queue.empty(), queue.full())
print(queue.put_nowait("first"), queue.put("second", block=False))
print(queue.qsize(), queue.empty(), queue.full())
for put in [lambda: queue.put_nowait("third"), lambda: queue.put("third", timeout=0)]:
    try:
        put()
    except Full as exc:
        print("full", str(exc))
print(queue.get_nowait(), queue.get(block=False))
print(queue.qsize(), queue.empty(), queue.full())
for get in [queue.get_nowait, lambda: queue.get(timeout=0)]:
    try:
        get()
    except Empty as exc:
        print("empty", str(exc))
print(queue.task_done(), queue.task_done(), queue.join())
try:
    queue.task_done()
except ValueError as exc:
    print(exc)
for operation in [lambda: queue.put(1, timeout=-1), lambda: queue.get(timeout=-1)]:
    try:
        operation()
    except ValueError as exc:
        print(exc)

unbounded = Queue(-1)
for i in range(5):
    unbounded.put_nowait(i)
print(unbounded.full(), unbounded.qsize())
print([unbounded.get_nowait() for i in range(5)])
for i in range(5):
    unbounded.task_done()
print(unbounded.join())

bounded = Queue(1)
consumed = []


def produce():
    for value in range(12):
        bounded.put(value)
    bounded.put(None)


def consume():
    while True:
        item = bounded.get()
        if item is None:
            bounded.task_done()
            break
        consumed.append(item)
        bounded.task_done()


producer = Thread(target=produce, daemon=True)
consumer = Thread(target=consume, daemon=True)
producer.start()
consumer.start()
producer.join()
bounded.join()
consumer.join()
print(consumed)
print(bounded.qsize(), bounded.unfinished_tasks)

work = Queue()
received = Event()
allow_done = Event()
joining = Event()
joined = Event()
work.put("payload")
processed = []


def process():
    processed.append(work.get())
    received.set()
    allow_done.wait()
    work.task_done()


def wait_until_done():
    joining.set()
    work.join()
    joined.set()


worker = Thread(target=process, daemon=True)
worker.start()
received.wait()
waiter = Thread(target=wait_until_done, daemon=True)
waiter.start()
joining.wait()
print("join waits for task_done", work.empty(), joined.is_set())
allow_done.set()
waiter.join()
worker.join()
print("join completed", joined.is_set(), processed, work.unfinished_tasks)
