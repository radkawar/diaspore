import sys

def gen():
    try:
        yield 1
        yield 2
    finally:
        pass

def burn(n):
    for _ in range(n):
        g = gen()
        next(g)
    return n

print(burn(int(sys.argv[1]) if len(sys.argv) > 1 else 300))
