"""Provide context-local values with explicit, exception-safe context switching.

Context copies share an immutable value dictionary until a write. Scheduler
callbacks must enter their task's Context with run(), as CPython asyncio does.
Thread lifecycle teardown removes the exited worker's current context before
its identity may be reused; explicitly retained Context objects remain valid.
"""

from _thread import allocate_lock, get_ident


class _TokenMissing:
    __slots__ = ()

    def __repr__(self):
        return "<Token.MISSING>"


_MISSING = _TokenMissing()
_UNSET = object()
_contexts = {}
_context_lock = allocate_lock()


def _current_context():
    ident = get_ident()
    context = _contexts.get(ident)
    if context is None:
        context = Context()
        _contexts[ident] = context
    return context


def _clear_thread_context(ident):
    """Release an exited thread's current context without changing saved contexts."""
    _contexts.pop(ident, None)


def _check_key(key):
    if not isinstance(key, ContextVar):
        raise TypeError("a ContextVar key was expected, got " + repr(key))


class Context:
    """Store a read-only mapping of context variables to explicitly set values."""

    __slots__ = ("_values", "_entered", "__weakref__")
    __hash__ = None

    def __new__(cls):
        self = object.__new__(cls)
        self._values = {}
        self._entered = False
        return self

    def __init_subclass__(cls, **kwargs):
        raise TypeError("type '_contextvars.Context' is not an acceptable base type")

    def __len__(self):
        return len(self._values)

    def __iter__(self):
        return iter(self._values)

    def __getitem__(self, key):
        _check_key(key)
        return self._values[key]

    def __contains__(self, key):
        _check_key(key)
        return key in self._values

    def __eq__(self, other):
        if not isinstance(other, Context):
            return NotImplemented
        return self._values == other._values

    def __ne__(self, other):
        result = self.__eq__(other)
        if result is NotImplemented:
            return NotImplemented
        return not result

    def get(self, key, default=None, /):
        """Return an explicitly set value, ignoring the variable's default."""
        _check_key(key)
        return self._values.get(key, default)

    def keys(self):
        """Return an iterator over variables set in this context."""
        return iter(self._values)

    def values(self):
        """Return an iterator over the context's values."""
        return iter(self._values.values())

    def items(self):
        """Return an iterator over variable-value pairs."""
        return iter(self._values.items())

    def copy(self):
        """Return an independent shallow copy in constant time."""
        context = object.__new__(Context)
        context._values = self._values
        context._entered = False
        return context

    def run(self, callable, /, *args, **kwargs):
        """Call a function in this context and restore the previous context."""
        ident = get_ident()
        with _context_lock:
            if self._entered:
                raise RuntimeError("cannot enter context: " + repr(self) + " is already entered")
            previous = _current_context()
            self._entered = True
            _contexts[ident] = self
        try:
            return callable(*args, **kwargs)
        finally:
            with _context_lock:
                _contexts[ident] = previous
                self._entered = False


class ContextVar:
    """Identify a value stored separately in each current context."""

    __slots__ = ("_name", "_default")

    def __new__(cls, name, /, *, default=_UNSET):
        if not isinstance(name, str):
            raise TypeError("context variable name must be a str")
        self = object.__new__(cls)
        self._name = name
        self._default = default
        return self

    def __init_subclass__(cls, **kwargs):
        raise TypeError("type '_contextvars.ContextVar' is not an acceptable base type")

    @classmethod
    def __class_getitem__(cls, item):
        from types import GenericAlias

        return GenericAlias(cls, item)

    @property
    def name(self):
        """Return the variable's read-only name."""
        return self._name

    def __repr__(self):
        text = "<ContextVar name=" + repr(self._name)
        if self._default is not _UNSET:
            text += " default=" + repr(self._default)
        return text + " at " + hex(id(self)) + ">"

    def get(self, default=_UNSET, /):
        """Return the current value, call default, or variable default."""
        values = _current_context()._values
        if self in values:
            return values[self]
        if default is not _UNSET:
            return default
        if self._default is not _UNSET:
            return self._default
        raise LookupError(self)

    def set(self, value, /):
        """Set a value and return a token for restoring its previous binding."""
        context = _current_context()
        token = object.__new__(Token)
        token._context = context
        token._var = self
        token._had_value = self in context._values
        token._old_value = context._values.get(self, _MISSING)
        token._used = False
        values = context._values.copy()
        values[self] = value
        context._values = values
        return token

    def reset(self, token, /):
        """Consume a token created by this variable in the current context."""
        if not isinstance(token, Token):
            raise TypeError("expected an instance of Token, got " + repr(token))
        if token._used:
            raise RuntimeError(repr(token) + " has already been used once")
        if token._var is not self:
            raise ValueError(repr(token) + " was created by a different ContextVar")
        context = _current_context()
        if token._context is not context:
            raise ValueError(repr(token) + " was created in a different Context")
        token._used = True
        values = context._values.copy()
        if not token._had_value:
            values.pop(self, None)
        else:
            values[self] = token._old_value
        context._values = values


class Token:
    """Record a ContextVar binding for a single reset operation."""

    __slots__ = ("_context", "_var", "_old_value", "_had_value", "_used")

    MISSING = _MISSING

    def __new__(cls, *args, **kwargs):
        raise RuntimeError("Tokens can only be created by ContextVars")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("type '_contextvars.Token' is not an acceptable base type")

    @classmethod
    def __class_getitem__(cls, item):
        from types import GenericAlias

        return GenericAlias(cls, item)

    @property
    def var(self):
        """Return the variable that created this token."""
        return self._var

    @property
    def old_value(self):
        """Return the previous explicit value, or Token.MISSING."""
        return self._old_value

    def __repr__(self):
        used = "used " if self._used else ""
        return "<Token " + used + "var=" + repr(self._var) + " at " + hex(id(self)) + ">"


def copy_context():
    """Return a constant-time shallow copy of the current context."""
    return _current_context().copy()
