from collections import Counter, OrderedDict, defaultdict, deque
from _collections import _count_elements


def error(operation):
    try:
        operation()
    except Exception as exc:
        print(type(exc).__name__)


for bound in (None, 0, 1, 3):
    source = iter(range(5))
    values = deque(source, bound)
    print(values, values.maxlen, list(source))
    values.append(5)
    values.appendleft(-1)
    values.extend([6, 7])
    values.extendleft([-2, -3])
    print(values, list(reversed(values)))
    values.rotate(2)
    print(values)
    values.rotate(-3)
    values.reverse()
    print(values, values.copy() == values)

error(lambda: deque([], -1))
error(lambda: deque(None))
error(lambda: deque([], 1.5))
error(lambda: deque().pop())
error(lambda: deque().popleft())
error(lambda: deque([1])[1:])
error(lambda: deque([1])[4])
error(lambda: deque([1]).rotate(0.5))
error(lambda: setattr(deque(), "maxlen", 2))
error(lambda: hash(deque()))

values = deque("abca", 6)
print(values.index("a"), values.index("a", 1), values.index("a", -2))
print(values.count("a"), "b" in values, "z" in values)
error(lambda: values.index("a", 1, 3))
error(lambda: values.index("a", None))
values.remove("a")
values.insert(1, "x")
values.insert(-1, "y")
values[0] = "B"
del values[-2]
print(values, values.pop(), values.popleft(), values)
values.extend(values)
print(values)
values.extendleft(values)
print(values)
error(lambda: deque([1], 1).insert(0, 2))

for index in (-10, -3, -1, 0, 1, 3, 10):
    values = deque("abc")
    values.insert(index, "X")
    print(index, values)

values = deque("abc", 5)
print(values + deque("de"), values * 3, 2 * values, values * 0)
values += "de"
values *= 2
print(values)
error(lambda: values + [1])
print(deque([1, 2]) == deque([1, 2], 3), deque([1]) == [1])
print(deque([1]) < deque([1, 2]), deque([2]) > deque([1, 3]))
print(deque([1]) <= deque([1]), deque([1]) >= deque([1]))

values = deque([1, 2, 3])
forward = iter(values)
backward = reversed(values)
print(next(forward), next(backward))
values[1] = 20
print(next(forward), next(backward))
values.reverse()
print(next(forward), next(backward))
values.append(4)
error(lambda: next(forward))
error(lambda: next(backward))

values = deque([1, 2])
iterator = iter(values)
values.rotate(0)
error(lambda: next(iterator))
values = deque([1])
iterator = iter(values)
values.rotate(0)
print(next(iterator), next(iterator, "end"))

empty = deque(maxlen=0)
iterator = iter(empty)
empty.extend([1, 2])
print(next(iterator, "end"), empty)
empty.append(3)
error(lambda: next(iterator))

values = deque([1, 2], 3)
iterator = iter(values)
values.__init__([4, 5], 1)
print(values)
error(lambda: next(iterator))
values.__init__(values)
print(values, values.maxlen)


class Mutating:
    def __init__(self, values, result):
        self.values = values
        self.result = result

    def __eq__(self, other):
        self.values.clear()
        return self.result


for comparison in (False, True):
    for operation in ("count", "index", "remove", "contains"):
        values = deque()
        values.append(Mutating(values, comparison))
        try:
            if operation == "contains":
                result = 1 in values
            else:
                result = getattr(values, operation)(1)
            print(operation, comparison, result)
        except Exception as exc:
            print(operation, comparison, type(exc).__name__)

values = deque()
values.append(values)
print(values)


class DequeChild(deque):
    def __init__(self, iterable=(), maxlen=None):
        super().__init__(iterable, maxlen)
        self.label = "child"


child = DequeChild([1, 2], 3)
cloned = child.copy()
print(type(cloned).__name__, list(cloned), cloned.maxlen, cloned.label)


class EmptyChild(deque):
    def __init__(self):
        self.label = "empty"


print(list(EmptyChild()), EmptyChild().maxlen)

factories = defaultdict(list, first=[1])
factories["second"].append(2)
print(factories, factories.get("missing"), "missing" in factories)
print(factories.copy(), factories.copy()["first"] is factories["first"])
print(defaultdict(default_factory=7))
error(lambda: defaultdict(7))
error(lambda: defaultdict(None, {}, {}))
error(lambda: defaultdict()["missing"])
factories.default_factory = int
print(factories["third"], factories.default_factory is int)
del factories.default_factory
print(factories.default_factory)
error(lambda: factories["fourth"])
factories.default_factory = 7
error(lambda: factories["fourth"])

left = defaultdict(int, a=1, b=2)
right = defaultdict(list, b=[3], c=[4])
merged = left | right
reflected = {"z": 0, "a": 9} | left
print(merged, merged.default_factory is int, list(merged))
print(reflected, reflected.default_factory is int, list(reflected))
left |= [("c", 3)]
print(left)

recursive = defaultdict(None)
recursive["self"] = recursive
print(recursive)

counts = {"a": 4}
print(_count_elements(counts, "abaca"), counts)


class CountingMap(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default) + 10


counts = CountingMap()
_count_elements(counts, "aba")
print(dict(counts))

counter = Counter("mississippi")
print(sorted(counter.items()), list(counter.elements()), counter.total())
counter.update("sip")
print(counter.most_common(2), counter["missing"])
ordered = OrderedDict([("a", 1), ("b", 2), ("c", 3)])
ordered.move_to_end("b", last=False)
print(list(ordered.items()), list(reversed(ordered)))
print(ordered.popitem(last=False), list(ordered.items()))
print(ordered == OrderedDict(reversed(list(ordered.items()))), ordered == dict(ordered))
