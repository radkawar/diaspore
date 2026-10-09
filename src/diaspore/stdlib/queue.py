"""Provide a task-safe FIFO Queue with completion accounting.

Queue supports bounded and unbounded storage, blocking or nonblocking put/get,
qsize/empty/full, task_done, and join. Timeout None waits indefinitely; timeout
zero polls. Other timeouts raise explicitly because native tasks provide no
timed waiting. All queues require the spawn capability. Native channels store
items and threading conditions coordinate capacity and unfinished work.
"""

from threading import Condition, Lock

import tasks as _tasks

__all__ = ["Empty", "Full", "Queue"]


class Empty(Exception):
    """Indicate that a nonblocking get found no available item."""


class Full(Exception):
    """Indicate that a nonblocking put found no free capacity."""


def _blocking(block, timeout):
    if timeout is None:
        return bool(block)
    if block and timeout < 0:
        raise ValueError("'timeout' must be a non-negative number")
    if timeout != 0:
        raise NotImplementedError("timed queue operations are not supported")
    return False


class Queue:
    """Provide a FIFO queue whose maxsize is unbounded when nonpositive."""

    def __init__(self, maxsize=0):
        self.maxsize = maxsize
        self._items = _tasks.channel()
        self.mutex = Lock()
        self.not_empty = Condition(self.mutex)
        self.not_full = Condition(self.mutex)
        self.all_tasks_done = Condition(self.mutex)
        self.unfinished_tasks = 0

    def qsize(self):
        """Return the current item count, which may change after returning."""
        with self.mutex:
            return self._items.len()

    def empty(self):
        """Return whether no items are currently queued."""
        with self.mutex:
            return self._items.len() == 0

    def full(self):
        """Return whether a bounded queue has reached its capacity."""
        with self.mutex:
            return self.maxsize > 0 and self._items.len() >= self.maxsize

    def put(self, item, block=True, timeout=None):
        """Enqueue item, waiting for capacity or raising Full when polling."""
        blocking = _blocking(block, timeout)
        with self.not_full:
            while self.maxsize > 0 and self._items.len() >= self.maxsize:
                if not blocking:
                    raise Full
                self.not_full.wait()
            self._items.send(item)
            self.unfinished_tasks += 1
            self.not_empty.notify()

    def put_nowait(self, item):
        """Enqueue item without waiting for capacity."""
        return self.put(item, False)

    def get(self, block=True, timeout=None):
        """Remove the oldest item, waiting or raising Empty when polling."""
        blocking = _blocking(block, timeout)
        with self.not_empty:
            while self._items.len() == 0:
                if not blocking:
                    raise Empty
                self.not_empty.wait()
            item = self._items.recv()
            self.not_full.notify()
            return item

    def get_nowait(self):
        """Remove the oldest item without waiting for data."""
        return self.get(False)

    def task_done(self):
        """Mark one enqueued item as processed, rejecting excess calls."""
        with self.all_tasks_done:
            if self.unfinished_tasks <= 0:
                raise ValueError("task_done() called too many times")
            self.unfinished_tasks -= 1
            if self.unfinished_tasks == 0:
                self.all_tasks_done.notify_all()

    def join(self):
        """Wait until task_done has been called for every enqueued item."""
        with self.all_tasks_done:
            while self.unfinished_tasks:
                self.all_tasks_done.wait()
