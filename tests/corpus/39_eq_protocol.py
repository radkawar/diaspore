class Num:
    def __init__(self, v):
        self.v = v

    def __eq__(self, other):
        return isinstance(other, Num) and self.v == other.v

    def __repr__(self):
        return f"Num({self.v})"


a, b = Num(1), Num(1)
print(a == b, a is b, a != b, a == Num(2))
print([Num(1)] == [Num(1)], [Num(1)] == [Num(2)], [Num(1)] == [Num(1), Num(2)])
print((Num(3),) == (Num(3),), (Num(3),) != (Num(4),))
print({"k": Num(5)} == {"k": Num(5)}, {"k": Num(5)} == {"k": Num(6)})
print({"k": Num(5)} == {"x": Num(5)}, {} == {}, {"a": 1} == {"a": 1, "b": 2})
print([[Num(1)], [Num(2)]] == [[Num(1)], [Num(2)]])
print([1, [2, [3, Num(4)]]] == [1, [2, [3, Num(4)]]])
print(Num(1) in [Num(0), Num(1)], Num(9) in [Num(0), Num(1)])
print(Num(2) in (Num(2),), Num(2) not in (Num(3),))
print([1, 2] == (1, 2), [1] == [1.0], (True,) == (1,))
print([Num(7), Num(8)].index(Num(8)), [Num(7)].count(Num(7)), (Num(5), Num(5)).count(Num(5)))
row = [Num(1), Num(2), Num(1)]
row.remove(Num(1))
print(row, row.index(Num(1)))
try:
    row.index(Num(99))
except ValueError as e:
    print("idx:", e)
try:
    row.remove(Num(99))
except ValueError as e:
    print("rm:", e)


class Loose:
    def __eq__(self, other):
        return True


print(Loose() == 42, 42 == Loose(), Loose() == "anything")
print([Loose()] == [0], {"z": Loose()} == {"z": "whatever"})


class NoEq:
    pass


x = NoEq()
print(x == x, x == NoEq(), [x] == [x], [NoEq()] == [NoEq()])
nan = float("nan")
print(nan == nan, [nan] == [nan], nan != nan)
print(nan in [nan], [nan].index(nan), [nan].count(nan))
mixed = [Num(1), "s", 3, None]
print(mixed == [Num(1), "s", 3, None], mixed == [Num(1), "s", 3, 0])
