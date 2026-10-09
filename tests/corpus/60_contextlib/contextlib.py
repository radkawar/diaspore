"""contextlib-lite, written in the diaspore subset (VM-independent).

Provides @contextmanager, suppress, closing, nullcontext, ExitStack, and the
ContextDecorator base. @contextmanager drives a generator via send/throw —
the exact generator protocol diaspore now implements.
"""


class _GeneratorContextManager:
    def __init__(self, func, args, kwds):
        self.gen = func(*args, **kwds)
        self.func = func
        self.args = args
        self.kwds = kwds

    def __enter__(self):
        try:
            return next(self.gen)
        except StopIteration:
            raise RuntimeError("generator didn't yield") from None

    def __exit__(self, typ, value, tb):
        if typ is None:
            try:
                next(self.gen)
            except StopIteration:
                return False
            raise RuntimeError("generator didn't stop")
        else:
            if value is None:
                value = typ()
            try:
                self.gen.throw(value)
            except StopIteration as exc:
                return exc is not value
            except RuntimeError as exc:
                if exc is value:
                    return False
                raise
            except BaseException as exc:
                if exc is not value:
                    raise
                return False
            raise RuntimeError("generator didn't stop after throw()")


def contextmanager(func):
    def helper(*args, **kwds):
        return _GeneratorContextManager(func, args, kwds)
    return helper


class suppress:
    def __init__(self, *exceptions):
        self._exceptions = exceptions

    def __enter__(self):
        return None

    def __exit__(self, exctype, excinst, exctb):
        return exctype is not None and issubclass(exctype, self._exceptions)


class closing:
    def __init__(self, thing):
        self.thing = thing

    def __enter__(self):
        return self.thing

    def __exit__(self, *exc_info):
        self.thing.close()
        return False


class nullcontext:
    def __init__(self, enter_result=None):
        self.enter_result = enter_result

    def __enter__(self):
        return self.enter_result

    def __exit__(self, *excinfo):
        return False


class ExitStack:
    def __init__(self):
        self._callbacks = []

    def enter_context(self, cm):
        result = cm.__enter__()
        self._callbacks.append(cm.__exit__)
        return result

    def callback(self, func, *args, **kwds):
        def _cb(exc_type, exc, tb):
            func(*args, **kwds)
            return False
        self._callbacks.append(_cb)

    def push(self, exit_cb):
        self._callbacks.append(exit_cb)
        return exit_cb

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        suppressed = False
        while self._callbacks:
            cb = self._callbacks.pop()
            try:
                if cb(exc_type, exc, tb):
                    suppressed = True
                    exc_type, exc, tb = None, None, None
            except BaseException as new_exc:
                exc_type, exc, tb = type(new_exc), new_exc, None
        if exc is not None and not suppressed:
            raise exc
        return suppressed
