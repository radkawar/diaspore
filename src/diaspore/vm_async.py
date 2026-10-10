"""Drive async-generator operations without conflating await and async yield."""


class AsyncYield:
    """Mark an async yield separately from a suspension inside await."""

    def __init__(self, value):
        """Store the asynchronously yielded value."""
        self.value = value


class AsyncGeneratorOperation:
    """Implement one asend, athrow, or aclose awaitable over a VM generator."""

    def __init__(self, generator, token, closing=False):
        """Bind one operation to its generator and initial resume token."""
        self.generator = generator
        self.token = token
        self.closing = closing
        self.started = False
        self.done = False

    def __iter__(self):
        """Return this operation's suspension iterator."""
        return self

    def __await__(self):
        """Return the iterator driving this awaitable."""
        return self

    def __next__(self):
        """Resume the operation without sending a value."""
        return self.send(None)

    def send(self, value):
        """Resume the operation, distinguishing awaited and yielded values."""
        from .vm import DiasporeError, make_exc

        if self.done:
            raise RuntimeError("cannot reuse already awaited async-generator operation")
        if not self.started:
            if value is not None:
                raise TypeError("can't send non-None value to a just-started coroutine")
            if self.generator.chan.state.get("async_running"):
                raise RuntimeError("asynchronous generator is already running")
            if (
                not self.generator.chan.state["started"]
                and self.token[0] == "send"
                and self.token[1] is not None
            ):
                raise TypeError("can't send non-None value to a just-started async generator")
            self.generator.chan.state["async_running"] = True
            self.started = True
            token = self.token
        else:
            token = ("send", value)
        try:
            result = self.generator._advance(token)
        except StopIteration:
            self._finish()
            if self.closing:
                raise StopIteration(None) from None
            raise DiasporeError(make_exc("StopAsyncIteration")) from None
        except DiasporeError as error:
            self._finish()
            if self.closing and error.value.cls.name in ("GeneratorExit", "StopAsyncIteration"):
                raise StopIteration(None) from None
            raise
        except BaseException:
            self._finish()
            raise
        if isinstance(result, AsyncYield):
            self._finish()
            if self.closing:
                raise RuntimeError("async generator ignored GeneratorExit")
            raise StopIteration(result.value)
        return result

    def throw(self, exception):
        """Inject a sandbox exception into the generator."""
        from .vm import BASE_EXC, DiasporeClass, DiasporeInstance

        interp = self.generator.fn.interp
        if isinstance(exception, DiasporeClass):
            exception = interp.call(exception, [], {})
        if not isinstance(exception, DiasporeInstance) or not exception.cls.issub(BASE_EXC):
            raise TypeError("exceptions must derive from BaseException")
        self.token = ("throw", exception)
        if self.started:
            self.generator.chan.state["async_running"] = False
            self.started = False
        return self.send(None)

    def close(self):
        """Mark the operation finished and release its running state."""
        self._finish()

    def _finish(self):
        self.done = True
        self.generator.chan.state["async_running"] = False
