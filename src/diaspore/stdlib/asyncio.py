"""Provide cooperative tasks with FIFO scheduling and logical-clock sleeps."""


class _Sleep:
    def __init__(self, delay):
        self.delay = delay

    def __await__(self):
        yield ("sleep", self.delay)


class _Future:
    def __init__(self):
        self.done = False
        self.value = None
        self.error = None
        self.waiters = []

    def set_result(self, v):
        self.done = True
        self.value = v

    def set_exception(self, e):
        self.done = True
        self.error = e

    def __await__(self):
        while not self.done:
            yield ("wait", self)
        if self.error is not None:
            raise self.error
        return self.value


class Task:
    def __init__(self, coro):
        self.coro = coro.__await__() if hasattr(coro, "__await__") else coro
        self.future = _Future()
        self.wake_at = 0.0
        self.blocked_on = None


class _Loop:
    def __init__(self):
        self.clock = 0.0
        self.ready = []
        self.sleeping = []

    def create_task(self, coro):
        t = Task(coro)
        self.ready.append(t)
        return t

    def run(self, main_coro):
        root = self.create_task(main_coro)
        while self.ready or self.sleeping:
            if not self.ready:
                self.sleeping.sort(key=lambda t: t.wake_at)
                self.clock = self.sleeping[0].wake_at
                woken = [t for t in self.sleeping if t.wake_at <= self.clock]
                self.sleeping = [t for t in self.sleeping if t.wake_at > self.clock]
                self.ready.extend(woken)
            task = self.ready.pop(0)
            if task.future.done:
                continue
            self._step(task, None)
        if root.future.error is not None:
            raise root.future.error
        return root.future.value

    def _step(self, task, send_value):
        try:
            cmd = task.coro.send(send_value)
        except StopIteration as e:
            task.future.set_result(_stop_value(e))
            self._wake_waiters(task)
            return
        except Exception as e:
            task.future.set_exception(e)
            self._wake_waiters(task)
            return
        kind = cmd[0]
        if kind == "sleep":
            task.wake_at = self.clock + cmd[1]
            self.sleeping.append(task)
        elif kind == "wait":
            fut = cmd[1]
            if fut.done:
                self.ready.append(task)
            else:
                fut.waiters.append(task)

    def _wake_waiters(self, task):
        for w in task.future.waiters:
            self.ready.append(w)
        task.future.waiters = []


def _stop_value(e):
    return e.args[0] if e.args else None


_LOOP = None


def sleep(delay):
    return _Sleep(delay)


def run(coro):
    global _LOOP
    _LOOP = _Loop()
    result = _LOOP.run(coro)
    _LOOP = None
    return result


def create_task(coro):
    return _LOOP.create_task(coro)


async def gather(*coros):
    tasks = [create_task(c) for c in coros]
    results = []
    for t in tasks:
        results.append(await t.future)
    return results
