print(repr(NotImplemented), str(NotImplemented), NotImplemented is NotImplemented)


class Decline:
    def __add__(self, other):
        print("left add")
        return NotImplemented

    def __radd__(self, other):
        print("decline reflected")
        return NotImplemented

    def __iadd__(self, other):
        print("inplace add")
        return NotImplemented

    def __eq__(self, other):
        print("decline eq")
        return NotImplemented

    def __ne__(self, other):
        print("decline ne")
        return NotImplemented

    def __lt__(self, other):
        print("decline lt")
        return NotImplemented


class Accept:
    def __radd__(self, other):
        print("right add")
        return 42

    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False

    def __gt__(self, other):
        return True


left = Decline()
right = Accept()
print(left + right)
value = left
value += right
print(value)
print(left == right, left != right, left < right)
print(left == left, left != left)
print(left == Decline(), left != Decline())
for pair in [(left, left), (left, 1), (1, left)]:
    try:
        pair[0] + pair[1]
    except TypeError:
        print("unsupported add")
try:
    left < left
except TypeError:
    print("unsupported ordering")


class Base:
    def __add__(self, other):
        print("base add")
        return 1

    def __radd__(self, other):
        print("base reflected")
        return 2

    def __eq__(self, other):
        print("base eq")
        return False


class Sub(Base):
    def __radd__(self, other):
        print("sub reflected")
        return NotImplemented

    def __eq__(self, other):
        print("sub eq")
        return NotImplemented


print(Base() + Sub())
print(Base() == Sub())


class Equal:
    def __eq__(self, other):
        return True


print(Equal() != Equal())
