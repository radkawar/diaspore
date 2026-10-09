import random
import sys

count = sys.stdout.write("stream\n")
sys.stdout.flush()
print(count)
print(repr(sys.stdin.read()), repr(sys.stdin.readline()))
limit = sys.getrecursionlimit()
sys.setrecursionlimit(300)
print(sys.getrecursionlimit())
sys.setrecursionlimit(limit)
try:
    sys.setrecursionlimit(0)
except ValueError:
    print("invalid recursion limit")
try:
    sys.setrecursionlimit(1.5)
except TypeError:
    print("noninteger recursion limit")

first = random.Random(123)
second = random.Random(123)
print(first.random() == second.random())
random.seed(987)
random.random()
print(first.random() == second.random())
first.seed(42)
second.seed(42)
print(first.random() == second.random())
print(first.randint(5, 5), second.choice(["only"]))
values = [1, 2, 3, 4]
first.shuffle(values)
print(sorted(values))
print(0 <= random.Random().random() < 1)
