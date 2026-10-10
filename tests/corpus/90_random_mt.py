"""CPython-oracle MT19937 seeds, state transitions and public distributions."""

import random

for seed in [0, 1, -1, 2**32, -(2**65 + 19), 2**257 + 2**129 + 12345,
             3.25, -3.25, "diaspore", b"\x00\xffseed"]:
    rng = random.Random(seed)
    print("seed", seed)
    print([rng.random() for unused in range(5)])
    print([rng.getrandbits(bits) for bits in (0, 1, 5, 31, 32, 33, 63, 64, 65, 129)])
    state = rng.getstate()
    print(state[0], len(state[1]), state[1][-1], state[2])
    output = [rng.getrandbits(32) for unused in range(630)]
    rng.setstate(state)
    print(output == [rng.getrandbits(32) for unused in range(630)], output[:3], output[-3:])
    print(rng.randrange(-100, 200, 7), rng.randrange(2**100), rng.randint(-5, 5))
    values = list(range(10))
    rng.shuffle(values)
    print(values, rng.sample(values, 4), rng.choices(values, weights=range(1, 11), k=4))
    print(rng.randbytes(13))

rng = random.Random(8675309)
state = rng.getstate()
print(rng.getrandbits(0), state == rng.getstate())
legacy = tuple(word - 2**32 if word >= 2**31 else word for word in state[1])
rng.setstate((2, legacy, None))
print(state == rng.getstate())
first = rng.gauss(0, 1)
saved = rng.getstate()
second = rng.gauss(0, 1)
rng.setstate(saved)
print(first, second, rng.gauss(0, 1) == second)
for seed in ("repeatable", b"legacy"):
    rng.seed(seed, version=1)
    print(rng.getrandbits(100))

class Bits:
    def __index__(self):
        return 9

print(rng.getrandbits(Bits()))
for bits in (-1, 2.5, 2**40):
    try:
        rng.getrandbits(bits)
    except (TypeError, ValueError, OverflowError) as exc:
        print(type(exc).__name__)
for bad in ((1, (), None), (3, [], None), (3, (0,), None),
            (3, (0,) * 624 + (625,), None), (3, (-1,) * 624 + (0,), None)):
    before = rng.getstate()[1]
    try:
        rng.setstate(bad)
    except (TypeError, ValueError, OverflowError) as exc:
        print(type(exc).__name__, rng.getstate()[1] == before)

random.seed(12345)
print(random.random(), random.getrandbits(70))
random.seed(12345)
print(random.random(), random.getrandbits(70))
