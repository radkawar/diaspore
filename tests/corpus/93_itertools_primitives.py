from itertools import (
    accumulate, batched, chain, combinations, combinations_with_replacement,
    compress, count, cycle, dropwhile, filterfalse, groupby, islice, pairwise,
    permutations, product, repeat, starmap, takewhile, tee, zip_longest,
)


def error(operation):
    try:
        operation()
    except Exception as exc:
        print(type(exc).__name__)


print(list(islice(count(3, -2), 5)))
print(list(islice(count(0.5, 0.25), 4)))
print(list(repeat("x", 3)), list(repeat("x", -2)))
print(list(islice(repeat("z"), 3)))
print(repr(count(3)), repr(count(3, 2)), repr(repeat("a", 2)))
print(list(chain([1, 2], (), [3])))
print(list(chain.from_iterable([[1], [], [2, 3]])))
print(list(islice(range(10), 1, 8, 3)))
source = iter(range(10))
print(list(islice(source, 2, 6, 3)), next(source))
source = iter(range(10))
print(list(islice(source, 5, 2)), next(source))
print(list(islice(range(6), None, None, None)))

print(list(accumulate([1, 2, 3])), list(accumulate([1, 2, 3], initial=10)))
print(list(accumulate([], initial=10)), list(accumulate([], initial=None)))
print(list(accumulate([2, 3, 4], lambda a, b: a * b)))
print(list(islice(cycle([1, 2, 3]), 8)), list(cycle([])))
print(list(compress("ABCDEF", [1, 0, 1, 0])))
print(list(dropwhile(lambda x: x < 3, [1, 2, 3, 1, 4])))
source = iter([1, 2, 3, 4])
print(list(takewhile(lambda x: x < 3, source)), list(source))
print(list(filterfalse(None, [0, 1, "", "x", False, None])))
print(list(filterfalse(lambda x: x % 2, range(6))))
print(list(starmap(pow, [(2, 3), [3, 2]])))
print(list(zip_longest([1], [2, 3, 4], [], fillvalue="-")))
print(list(zip_longest()), list(pairwise([1, 2, 3, 4])))
print(list(pairwise([])), list(pairwise([1])))
print(list(batched(range(7), 3)), list(batched([], 2)))

print(list(product("ab", [1, 2])))
print(list(product("ab", repeat=2)))
print(list(product()), list(product([], repeat=0)), list(product([], [1])))
print(list(permutations("abc", 2)))
print(list(permutations("ab")), list(permutations("ab", 3)))
print(list(combinations("abcd", 2)))
print(list(combinations_with_replacement("abc", 2)))
print(list(permutations([], 0)), list(combinations([], 0)))
print(list(combinations_with_replacement([], 0)), list(combinations_with_replacement([], 1)))
print(list(islice(product(range(1000), repeat=5), 3)))
print(list(islice(permutations(range(100), 20), 1)))

for operation in [
    lambda: count("a"), lambda: count(0, []), lambda: repeat(1, None),
    lambda: repeat(1, 2.5), lambda: islice([], -1),
    lambda: islice([], 0, 2, 0), lambda: islice([], 0, 2, -1),
    lambda: islice([], 1.5), lambda: islice([], -1, 2),
    lambda: islice([], 2 ** 100), lambda: tee([], -1),
    lambda: tee([], 2.5), lambda: batched([], 0),
    lambda: product([], repeat=-1), lambda: permutations([], -1),
    lambda: permutations([], 1.5), lambda: combinations([], -1),
    lambda: combinations_with_replacement([], -1),
    lambda: cycle(1), lambda: accumulate(1), lambda: pairwise(1),
    lambda: chain.from_iterable(1), lambda: zip_longest(1),
    lambda: filterfalse(None, 1), lambda: compress([], 1),
    lambda: starmap(None, 1), lambda: groupby(1),
]:
    error(operation)

print(tee(1, 0), list(product(1, repeat=0)))
print(list(repeat(object="k", times=2)))
print(list(combinations(iterable="abc", r=2)))
print(list(batched(iterable="abc", n=2)))
for operation in [
    lambda: cycle(iterable=[]), lambda: pairwise(iterable=[]),
    lambda: chain(iterables=[]), lambda: islice([], stop=2),
    lambda: tee([], n=2), lambda: starmap(function=abs, iterable=[]),
    lambda: batched([], 2, strict=True),
]:
    error(operation)


class Index:
    def __index__(self):
        return 2


print(list(repeat("x", Index())))
print(list(islice(range(5), Index())))
print(list(combinations("abc", Index())))
error(lambda: permutations("abc", Index()))


class FalseCallable:
    def __bool__(self):
        return False

    def __call__(self, *values):
        return sum(values)


print(list(accumulate([1, 2, 3], FalseCallable())))
print(list(filterfalse(FalseCallable(), [0, 1, 2])))

observed = []


def traced(values):
    for value in values:
        observed.append(value)
        yield value


iterator = chain.from_iterable(traced([[1, 2], [3]]))
print(observed)
print(next(iterator), observed)
print(next(iterator), observed)
print(next(iterator), observed)
observed.clear()
iterator = islice(traced(range(8)), 1, 6, 3)
print(observed)
print(next(iterator), observed)
print(next(iterator), observed)
print(next(iterator, "end"), observed)
observed.clear()
iterator = product(traced([1, 2]), repeat=2)
print(observed, next(iterator), observed)

observed.clear()
first, second = tee(traced(range(5)))
print(observed, next(first), next(first), observed)
third, fourth = tee(first)
print(next(third), next(fourth), next(second), observed)
print(list(first), list(second), list(third), list(fourth), observed)

observed.clear()
groups = groupby(traced("AAABBCCDA"))
key, old = next(groups)
print(key, next(old), observed)
key, current = next(groups)
print(key, list(old), list(current), observed)
print([(key, list(group)) for key, group in groups], observed)
print([(key, list(group)) for key, group in groupby([1, 3, 2, 4, 5], lambda x: x % 2)])
print([(key, list(group)) for key, group in groupby([None, None, 1, None])])


class Recovering:
    def __init__(self, values):
        self.values = iter(values)

    def __iter__(self):
        return self

    def __next__(self):
        value = next(self.values)
        if value == "error":
            raise ValueError("source error")
        return value


for factory in [
    lambda source: chain(source, [9]), lambda source: cycle(source),
    lambda source: accumulate(source), lambda source: filterfalse(None, source),
    lambda source: dropwhile(lambda x: False, source),
    lambda source: takewhile(lambda x: True, source),
    lambda source: compress(source, repeat(True)),
]:
    iterator = factory(Recovering([0, "error", 2, 0]))
    print(next(iterator))
    error(lambda: next(iterator))
    print(next(iterator))

for factory in [
    lambda source: islice(source, 10), lambda source: zip_longest(source, [4, 5, 6]),
]:
    iterator = factory(Recovering([1, "error", 2]))
    print(next(iterator))
    error(lambda: next(iterator))
    print(next(iterator, "end"))

iterator = pairwise(Recovering([1, "error", 2]))
error(lambda: next(iterator))
print(next(iterator, "end"))
iterator = batched(Recovering([1, "error", 2]), 2)
error(lambda: next(iterator))
print(next(iterator, "end"))

first, second = tee(Recovering([1, "error", 2]))
print(next(first), next(second))
error(lambda: next(first))
print(next(second), next(first))


class NonRestartable:
    def __init__(self):
        self.calls = 0

    def __iter__(self):
        return self

    def __next__(self):
        self.calls += 1
        raise StopIteration


source = NonRestartable()
print(list(zip_longest(source, [1, 2, 3])), source.calls)


def reenter():
    yield 1
    try:
        next(first)
    except RuntimeError as exc:
        yield str(exc)


first, second = tee(reenter())
print(next(first), next(first), list(second))
