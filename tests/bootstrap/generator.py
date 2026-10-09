def sequence(limit):
    try:
        for value in range(limit):
            yield value * value
    finally:
        print("generator closed")


def delegated():
    yield from sequence(4)
    yield 99


print(list(delegated()))
