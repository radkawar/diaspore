class AsyncRange:
    def __init__(self, n):
        self.n = n
        self.i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.i >= self.n:
            raise StopAsyncIteration
        v = self.i
        self.i += 1
        return v * 10


class AsyncCtx:
    def __init__(self, name):
        self.name = name

    async def __aenter__(self):
        print("enter", self.name)
        return self.name.upper()

    async def __aexit__(self, *exc):
        print("exit", self.name)
        return False


async def collect():
    out = []
    async for v in AsyncRange(4):
        out.append(v)
    return out


async def use_ctx():
    async with AsyncCtx("res") as r:
        print("body", r)
    return "ctx-done"


def run(coro):
    it = coro.__await__() if hasattr(coro, "__await__") else coro
    try:
        while True:
            it.send(None)
    except StopIteration as e:
        return e.args[0] if e.args else None


print(run(collect()))
print(run(use_ctx()))
print("done")
