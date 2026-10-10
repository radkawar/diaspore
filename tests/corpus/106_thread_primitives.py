# diaspore-caps: spawn
"""Exercise real thread ownership, sentinel completion, and context isolation."""

import _thread
import contextvars
import sys
import threading
import time


def error(operation, *args, **kwargs):
    try:
        operation(*args, **kwargs)
    except Exception as exc:
        print(type(exc).__name__)
    else:
        print("no error")


main_ident = _thread.get_ident()
print("main identity", main_ident == threading.get_ident(),
      threading.current_thread() is threading.main_thread(),
      threading.main_thread().ident == main_ident)
error(_thread.start_new_thread, None, ())
error(_thread.start_new_thread, lambda: None, [])
error(_thread.start_new_thread, lambda: None, (), None)

started = threading.Event()
proceed = threading.Event()
low_result = []
sentinels = []
initial_count = _thread._count()


def low_worker(value, *, multiplier):
    sentinel = _thread._set_sentinel()
    sentinel.acquire()
    sentinels.append(sentinel)
    low_result.append((_thread.get_ident(), value * multiplier))
    started.set()
    try:
        proceed.wait()
        _thread.exit()
    finally:
        low_result.append("finally")


low_ident = _thread.start_new_thread(low_worker, (6,), {"multiplier": 7})
started.wait()
print("low thread", low_ident != main_ident, low_result[0][0] == low_ident,
      low_result[0][1], sentinels[0].locked(), _thread._count() == initial_count + 1)
proceed.set()
print("exit sentinel", sentinels[0].acquire(timeout=5))
sentinels[0].release()
print("exit cleanup", low_result[1:])

recursive = _thread.RLock()
recursive.acquire()
recursive.acquire()
worker_results = []
variable = contextvars.ContextVar("thread value", default="default")
main_token = variable.set("main")
local = threading.local()
local.value = "main local"
captured = contextvars.copy_context()


def ownership_worker():
    worker_results.append((recursive.acquire(False), recursive.acquire(timeout=0),
                           recursive._is_owned(), recursive._recursion_count()))
    try:
        recursive.release()
    except RuntimeError:
        worker_results.append("wrong owner")
    worker_results.append((variable.get(), hasattr(local, "value")))
    variable.set("worker")
    local.value = "worker local"
    worker_results.append((captured.run(variable.get), variable.get(), local.value))
    worker_results.append(threading.current_thread() is worker)


worker = threading.Thread(target=ownership_worker, name="ownership", daemon=True)
print("before start", worker.ident, worker.is_alive())
error(worker.join)
worker.start()
worker.join()
print("ownership", worker_results)
print("after join", worker.ident != main_ident, worker.is_alive(),
      recursive._is_owned(), recursive._recursion_count(), variable.get(), local.value)
print("repeat join", worker.join())
error(worker.start)
recursive.release()
recursive.release()

held = threading.Lock()
held.acquire()
timeouts = []


def timed_worker():
    timeouts.append(held.acquire(timeout=0.01))


timed = threading.Thread(target=timed_worker, daemon=True)
timed.start()
timed.join()
print("timed contention", timeouts, held.locked())
held.release()

cross_lock = threading.Lock()
cross_lock.acquire()
releaser = threading.Thread(target=cross_lock.release, daemon=True)
releaser.start()
releaser.join()
print("cross-thread release", cross_lock.locked(), cross_lock.acquire(False))
cross_lock.release()

condition = threading.Condition()
waiting = threading.Event()
state = {"ready": False}
condition_results = []


def condition_worker():
    with condition:
        condition.acquire()
        waiting.set()
        while not state["ready"]:
            condition.wait()
        condition_results.append(condition._lock._recursion_count())
        condition.release()


waiter = threading.Thread(target=condition_worker, daemon=True)
waiter.start()
waiting.wait()
with condition:
    state["ready"] = True
    condition.notify()
waiter.join()
print("condition recursion", condition_results)
with condition:
    condition.acquire()
    print("condition timeout", condition.wait(0.001), condition._lock._recursion_count())
    condition.release()

shared = contextvars.Context()
context_entered = threading.Event()
context_leave = threading.Event()
context_results = []


def context_body():
    variable.set("shared")
    context_entered.set()
    context_leave.wait()
    context_results.append(variable.get())


def context_worker():
    shared.run(context_body)
    context_results.append(variable.get())


context_thread = threading.Thread(target=context_worker, daemon=True)
context_thread.start()
context_entered.wait()
error(shared.run, lambda: None)
shared_copy = shared.copy()
print("copy entered context", shared_copy.run(variable.get), variable.get())
context_leave.set()
context_thread.join()
print("context release", context_results, shared.run(variable.get))
variable.reset(main_token)
print("main reset", variable.get())

hook_records = []
previous_hook = threading.excepthook


def thread_hook(args):
    hook_records.append((args.exc_type.__name__, args.exc_value.args,
                         args.exc_traceback is not None, args.thread.name))


def fail_thread():
    raise ValueError("thread failure")


threading.excepthook = thread_hook
try:
    failing = threading.Thread(target=fail_thread, name="failure", daemon=True)
    failing.start()
    failing.join()
finally:
    threading.excepthook = previous_hook
print("thread exception hook", hook_records)

unraisable_records = []
unraisable_done = threading.Event()
unraisable_sentinels = []
previous_unraisable = sys.unraisablehook


def unraisable_hook(args):
    unraisable_records.append((args.exc_type.__name__, args.exc_value.args,
                               args.exc_traceback is not None,
                               args.object is fail_low_thread))
    unraisable_done.set()


def fail_low_thread():
    sentinel = _thread._set_sentinel()
    sentinel.acquire()
    unraisable_sentinels.append(sentinel)
    raise ValueError("low thread failure")


sys.unraisablehook = unraisable_hook
try:
    _thread.start_new_thread(fail_low_thread, ())
    unraisable_done.wait()
    completed = unraisable_sentinels[0].acquire(timeout=5)
    if completed:
        unraisable_sentinels[0].release()
finally:
    sys.unraisablehook = previous_unraisable
print("low exception hook", completed, unraisable_records)

try:
    _thread.interrupt_main()
except KeyboardInterrupt:
    print("main interrupted")

print("non-daemon shutdown")


def finish_after_main():
    time.sleep(0.02)
    print("non-daemon completed")


shutdown_thread = threading.Thread(target=finish_after_main, daemon=False)
shutdown_thread.start()
