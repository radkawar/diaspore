"""Exercise live references, explicit contexts, and uncontended thread locks."""

import _thread
import _weakref
import contextvars
import weakref
from functools import lru_cache


def error(operation, *args, **kwargs):
    try:
        operation(*args, **kwargs)
    except Exception as exc:
        print(type(exc).__name__)
    else:
        print("no error")


def describe(context):
    return sorted((variable.name, value) for variable, value in context.items())


def context_operations():
    value = contextvars.ContextVar("value")
    defaulted = contextvars.ContextVar("defaulted", default="default")
    print("defaults", value.name, value.get("fallback"), defaulted.get(), defaulted.get(None))
    error(value.get)
    error(contextvars.ContextVar, 7)
    error(contextvars.ContextVar, "x", "extra")
    error(contextvars.ContextVar, name="x")
    error(setattr, value, "name", "renamed")
    error(contextvars.Token)

    empty = contextvars.Context()
    print("empty", len(empty), bool(empty), value in empty, empty.get(value), empty.get(defaulted))
    print("mapping default", empty.get(defaulted, "mapping"), list(empty.keys()), list(empty.values()))
    error(lambda: empty[value])
    error(lambda: empty["value"])
    error(lambda: "value" in empty)
    error(empty.get, "value")
    error(hash, empty)
    error(contextvars.Context, {})

    first = value.set("first")
    print("token", isinstance(first, contextvars.Token), first.var is value,
          first.old_value is contextvars.Token.MISSING)
    error(setattr, first, "var", defaulted)
    error(setattr, first, "old_value", "replace")
    snapshot = contextvars.copy_context()
    twin = snapshot.copy()
    print("snapshot", describe(snapshot), snapshot == twin, snapshot is twin,
          list(snapshot) == [value], snapshot[value], len(snapshot))
    second = value.set("second")
    print("old value", second.old_value, value.get(), snapshot[value])
    print("copy run", snapshot.run(value.get), twin.run(value.get))
    print("keyword run", snapshot.run(lambda *, suffix: value.get() + suffix, suffix="!"))
    snapshot.run(value.set, "changed")
    print("independent", value.get(), snapshot[value], twin[value], snapshot != twin)

    iteration = snapshot.items()
    snapshot.run(value.set, "new")
    print("iteration snapshot", [(key.name, item) for key, item in iteration])
    print("views", [key.name for key in snapshot.keys()], list(snapshot.values()))
    error(defaulted.reset, second)
    error(snapshot.run, value.reset, second)
    error(value.reset, None)
    value.reset(second)
    print("reset", value.get())
    error(value.reset, second)
    value.reset(first)
    print("unset", value.get("missing"), value in contextvars.copy_context())

    missing_value = value.set(contextvars.Token.MISSING)
    replacement = value.set("replacement")
    value.reset(replacement)
    print("explicit missing", value.get() is contextvars.Token.MISSING,
          value in contextvars.copy_context())
    value.reset(missing_value)
    outer = value.set("outer")
    inner = value.set("inner")
    value.reset(outer)
    print("out of order", value.get("absent"))
    value.reset(inner)
    print("restored inner", value.get())

    def fail():
        value.set("during failure")
        raise ValueError("context body")

    error(snapshot.run, fail)
    print("exception restoration", value.get(), snapshot[value])
    error(snapshot.run, snapshot.run, lambda: None)
    print("reenter after exit", snapshot.run(lambda: "allowed"))
    print("nested", snapshot.run(twin.run, value.get), value.get())

    def reader():
        yield value.get()
        yield value.get()

    lazy = snapshot.run(reader)
    print("creation does not enter generator", next(lazy), next(lazy))
    resumed = reader()
    print("generator context", snapshot.run(next, resumed), next(resumed))


contextvars.Context().run(context_operations)


class Item:
    def __init__(self, value):
        self.value = value

    def __hash__(self):
        return hash(self.value)

    def __eq__(self, other):
        if not isinstance(other, Item):
            return NotImplemented
        return self.value == other.value

    def add(self, increment):
        return self.value + increment


item = Item(10)
peer = Item(10)
different = Item(11)
callback_events = []


def callback(reference):
    callback_events.append(reference)


basic = weakref.ref(item)
observed = weakref.ref(item, callback)
print("references", basic() is item, observed() is item,
      basic is weakref.ref(item), basic == weakref.ref(peer),
      basic != weakref.ref(different), hash(basic) == hash(item))
print("callbacks", basic.__callback__, observed.__callback__ is callback, callback_events)
error(setattr, observed, "__callback__", None)


class TaggedRef(weakref.ref):
    def __new__(cls, obj, callback, tag):
        reference = weakref.ref.__new__(cls, obj, callback)
        reference.tag = tag
        return reference

    def __init__(self, obj, callback, tag):
        super().__init__(obj, callback)


tagged = TaggedRef(item, callback, "tag")
wrapped = weakref.proxy(item)
wrapped_callback = weakref.proxy(item, callback)
print("subclass", tagged() is item, tagged.tag, isinstance(tagged, weakref.ReferenceType))
print("proxy", type(wrapped) is weakref.ProxyType, wrapped.value, wrapped.add(2),
      wrapped is weakref.proxy(item), wrapped == item)
references = weakref.getweakrefs(item)
print("counts", weakref.getweakrefcount(item), len(references),
      all(any(entry is expected for entry in references)
          for expected in (basic, observed, tagged, wrapped, wrapped_callback)))
references.clear()
print("independent list", weakref.getweakrefcount(item))
wrapped.extra = "added"
print("attribute writes", item.extra)
del wrapped.extra
print("attribute deletion", hasattr(item, "extra"))
error(hash, wrapped)
error(weakref.ref, [])
error(weakref.proxy, {})
error(weakref.ref, 1)
print("nonweak counts", weakref.getweakrefcount(1), weakref.getweakrefs([]))


class Slotted:
    __slots__ = ()


class WeakSlotted:
    __slots__ = ("__weakref__",)


error(weakref.ref, Slotted())
weak_slotted = WeakSlotted()
print("weak slot", weakref.ref(weak_slotted)() is weak_slotted)


class Unhashable:
    __hash__ = None


unhashable = Unhashable()
error(hash, weakref.ref(unhashable))
weak_method = weakref.WeakMethod(item.add)
print("weak method", weak_method()(3), weak_method == weakref.WeakMethod(item.add))


class Callable:
    def __call__(self, value, *, increment=0):
        return value + increment


callable_item = Callable()
callable_proxy = weakref.proxy(callable_item)
print("callable proxy", type(callable_proxy) is weakref.CallableProxyType,
      callable(callable_proxy), callable_proxy(4, increment=5), callable(wrapped))


class Sequence(list):
    pass


sequence = Sequence([1, 2, 3])
sequence_proxy = weakref.proxy(sequence)
print("sequence proxy", len(sequence_proxy), bool(sequence_proxy),
      list(sequence_proxy), list(reversed(sequence_proxy)), 2 in sequence_proxy)
sequence_proxy[1] = 20
del sequence_proxy[0]
print("sequence mutation", sequence_proxy[0], sequence)
sequence_proxy += [4]
print("inplace proxy result", sequence_proxy is sequence, sequence)


class Number(float):
    pass


number = Number(8)
numeric = weakref.proxy(number)
print("numeric proxy", numeric + 2, 2 + numeric, numeric - 2, 20 - numeric,
      numeric * 2, 2 * numeric, numeric / 2, 16 / numeric,
      numeric // 3, 20 // numeric, numeric % 3, 20 % numeric,
      numeric ** 2, 2 ** numeric, divmod(numeric, 3), divmod(20, numeric))
print("numeric conversions", int(numeric), float(numeric), -numeric, +numeric,
      abs(numeric), numeric < 10, numeric <= 8, numeric > 7, numeric >= 8)

live = {"item": basic}
print("dead removal", _weakref._remove_dead_weakref(live, "item"), live["item"] is basic,
      _weakref._remove_dead_weakref(live, "absent"))
error(_weakref._remove_dead_weakref, {"item": 1}, "item")

key = Item(30)
value = Item(40)
weak_keys = weakref.WeakKeyDictionary({key: "key value"})
weak_values = weakref.WeakValueDictionary({"value": value})
weak_set = weakref.WeakSet([key, value])
print("containers", weak_keys[key], weak_values["value"] is value,
      sorted(entry.value for entry in weak_set), key in weak_set, 1 in weak_set)
weak_set.discard(key)
print("container mutation", len(weak_set), weak_set.pop() is value, len(weak_set))
print("container refs", weak_keys.keyrefs()[0]() is key, weak_values.valuerefs()[0]() is value)
finalizer = weakref.finalize(value, lambda number: number + 1, 7)
print("explicit finalizer", finalizer.alive, finalizer.peek()[0] is value,
      finalizer(), finalizer.alive, finalizer())
detached = weakref.finalize(key, lambda: "unused")
print("detach finalizer", detached.detach()[0] is key, detached.alive, detached())

lock = _thread.allocate_lock()
print("lock", type(lock) is _thread.LockType, lock.locked(), lock.acquire(False),
      lock.acquire(False), lock.acquire(timeout=0))
lock.release()
with lock as acquired:
    print("lock context", acquired, lock.locked())
print("lock released", lock.locked(), _thread.get_ident() == _thread.get_ident())
error(lock.release)
error(lock.acquire, False, 0)
error(lock.acquire, True, -2)
error(lock.acquire, True, _thread.TIMEOUT_MAX * 2)
error(lock.acquire, "blocking")
recursive = _thread.RLock()
print("rlock", recursive._is_owned(), recursive.acquire(), recursive.acquire(False),
      recursive._recursion_count())
state = recursive._release_save()
print("saved recursion", state[0], state[1] == _thread.get_ident(), recursive._is_owned())
recursive._acquire_restore(state)
recursive.release()
print("restored recursion", recursive._is_owned(), recursive._recursion_count())
recursive.release()
error(recursive.release)
try:
    _thread.exit()
except SystemExit:
    print("thread exit")
print("stack default", _thread.stack_size())

calls = []


@lru_cache(maxsize=2)
def cached(value):
    calls.append(value)
    return value * value


print("functools lock", cached(3), cached(3), calls, cached.cache_info().hits)
