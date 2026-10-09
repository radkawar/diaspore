class Vec:
    def __init__(self, xs):
        self.xs = xs

    def __matmul__(self, other):
        return sum(a * b for a, b in zip(self.xs, other.xs))

    def __rmatmul__(self, other):
        return f"r@{other}"

    def __repr__(self):
        return f"Vec({self.xs})"


a, b = Vec([1, 2, 3]), Vec([4, 5, 6])
print(a @ b, b @ a)
print(3 @ a)
try:
    a @ 3
except AttributeError as e:
    print("lhs dunder ran and failed:", e)


class Acc:
    def __init__(self):
        self.log = []

    def __imatmul__(self, other):
        self.log.append(other)
        return self


acc = Acc()
acc @= "x"
acc @= "y"
print(acc.log)
try:
    1 @ 2
except TypeError as e:
    print(e)

print(ascii("héllo"), ascii("plain"), ascii("naïve €5"))
print(ascii([1, "café", None]), ascii({"π": 3.14}))
print(f"{'héllo'!a}", f"{'ok'!a:>8}|")
print(ascii("tab\there"), ascii(42), ascii(2.5))
