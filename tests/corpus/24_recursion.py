def depth_sum(n):
    if n == 0:
        return 0
    return n + depth_sum(n - 1)


print(depth_sum(500))

memo = {}


def fib(n):
    if n in memo:
        return memo[n]
    r = n if n < 2 else fib(n - 1) + fib(n - 2)
    memo[n] = r
    return r


print(fib(60), fib(90))


def ack(m, n):
    if m == 0:
        return n + 1
    if n == 0:
        return ack(m - 1, 1)
    return ack(m - 1, ack(m, n - 1))


print(ack(2, 3), ack(1, 5))


def hanoi(n, src, dst, via, moves):
    if n == 0:
        return
    hanoi(n - 1, src, via, dst, moves)
    moves.append((src, dst))
    hanoi(n - 1, via, dst, src, moves)


moves = []
hanoi(4, "A", "C", "B", moves)
print(len(moves), moves[:3], moves[-1])


class Node:
    def __init__(self, val, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right

    def total(self):
        t = self.val
        if self.left:
            t += self.left.total()
        if self.right:
            t += self.right.total()
        return t

    def depth(self):
        l = self.left.depth() if self.left else 0
        r = self.right.depth() if self.right else 0
        return 1 + max(l, r)


tree = Node(1, Node(2, Node(4), Node(5)), Node(3, None, Node(6, Node(7))))
print(tree.total(), tree.depth())


def walk(node):
    if node is None:
        return
    yield from walk(node.left)
    yield node.val
    yield from walk(node.right)


print(list(walk(tree)))


def gcd(a, b):
    return a if b == 0 else gcd(b, a % b)


print(gcd(1071, 462), gcd(17, 5))


def deep():
    def f(n):
        return 1 if n == 0 else 1 + f(n - 1)
    try:
        return f(100000)
    except RecursionError:
        return "recursion capped"


print(deep())


def power_set(items):
    if not items:
        return [[]]
    rest = power_set(items[1:])
    return rest + [[items[0]] + s for s in rest]


print(sorted(power_set([1, 2, 3])))
