import asyncio


async def countdown(name, n):
    out = []
    while n > 0:
        await asyncio.sleep(0.01)
        out.append(n)
        n -= 1
    return name, out


async def producer(q_list):
    for i in range(3):
        await asyncio.sleep(0.01)
        q_list.append(i * i)
    return "produced"


async def main():
    print("start")
    a, b = await asyncio.gather(countdown("x", 3), countdown("y", 2))
    print(a, b)
    shared = []
    r = await producer(shared)
    print(r, shared)

    async def nested():
        await asyncio.sleep(0.005)
        return sum(await asyncio.gather(countdown("z", 1))[0][1] for _ in range(1)) \
            if False else 99

    print(await nested())
    total = 0
    for coro in [countdown("t", 1), countdown("t", 2)]:
        _, vals = await coro
        total += sum(vals)
    print("total", total)
    return "main-done"


result = asyncio.run(main())
print(result)


async def with_exception():
    await asyncio.sleep(0.01)
    raise ValueError("async boom")


async def catcher():
    try:
        await with_exception()
    except ValueError as e:
        return f"caught: {e}"


print(asyncio.run(catcher()))
print("done")
