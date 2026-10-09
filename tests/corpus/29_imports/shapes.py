print("loading shapes")

PI = 3.14159
counter = 0


class Circle:
    def __init__(self, r):
        self.r = r

    def area(self):
        return round(PI * self.r ** 2, 4)

    def name(self):
        return "circle"


class Square:
    def __init__(self, side):
        self.side = side

    def area(self):
        return self.side ** 2

    def name(self):
        return "square"


def area_of(shape):
    return shape.area()


def describe(shape):
    return f"{shape.name()}:{shape.area()}"


counter += 1
