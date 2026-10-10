"""Register callbacks for explicit interpreter shutdown.

Callbacks run in reverse registration order after threading shutdown. Importing
this module does not install host-process hooks: each runtime calls the imported
registry only when its interpreter is closed, never on a durable suspension or
an embedding call. Callback failures are reported through sys.unraisablehook
and do not prevent the remaining callbacks from running.
"""

import sys as _sys

__all__ = ["register", "unregister"]

_callbacks = []
_default_hook = _sys.__unraisablehook__


class _UnraisableArgs:
    __slots__ = ("exc_type", "exc_value", "exc_traceback", "err_msg", "object")

    def __init__(self, exc_info, message, obj):
        self.exc_type, self.exc_value, self.exc_traceback = exc_info
        self.err_msg = message
        self.object = obj


def register(*args, **kwargs):
    """Register a callable with its arguments and return the callable.

    Raises:
        TypeError: No callable was supplied as the first positional argument.
    """
    if not args:
        raise TypeError("register() takes at least 1 argument (0 given)")
    func = args[0]
    if not callable(func):
        raise TypeError("the first argument must be callable")
    _callbacks.append((func, args[1:], kwargs))
    return func


def unregister(*args, **kwargs):
    """Remove every registration whose callable equals the positional argument."""
    if kwargs:
        raise TypeError("unregister() takes no keyword arguments")
    if len(args) != 1:
        raise TypeError("unregister() takes exactly one argument")
    func = args[0]
    for index in range(len(_callbacks)):
        callback = _callbacks[index]
        if callback is not None and (callback[0] is func or callback[0] == func):
            # CPython preserves registration slots until the registry is cleared.
            _callbacks[index] = None


def _clear():
    """Discard every registration without calling it."""
    _callbacks.clear()


def _ncallbacks():
    """Return the number of registration slots, including unregistered slots."""
    return len(_callbacks)


def _run_exitfuncs():
    """Run callbacks in reverse order and report, rather than raise, failures."""
    index = len(_callbacks) - 1
    try:
        while index >= 0:
            if index < len(_callbacks):
                callback = _callbacks[index]
                if callback is not None:
                    func, args, kwargs = callback
                    try:
                        func(*args, **kwargs)
                    except BaseException:
                        details = _UnraisableArgs(
                            _sys.exc_info(), "Exception ignored in atexit callback", func
                        )
                        hook = getattr(_sys, "unraisablehook", None)
                        if hook is None:
                            hook = _default_hook
                        try:
                            hook(details)
                        except BaseException:
                            try:
                                _default_hook(
                                    _UnraisableArgs(
                                        _sys.exc_info(),
                                        "Exception ignored in sys.unraisablehook",
                                        hook,
                                    )
                                )
                            except BaseException:
                                # CPython also ignores failures writing the diagnostic.
                                pass
            index -= 1
    finally:
        _clear()
