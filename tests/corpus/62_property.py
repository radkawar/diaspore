class Temp:
    def __init__(self, c):
        self._c = c
    @property
    def celsius(self):
        return self._c
    @celsius.setter
    def celsius(self, v):
        if v < -273:
            raise ValueError("too cold")
        self._c = v
    @property
    def fahrenheit(self):
        return self._c * 9 / 5 + 32

t = Temp(25)
print(t.celsius, t.fahrenheit)
t.celsius = 100
print(t.celsius, t.fahrenheit)
try:
    t.celsius = -300
except ValueError as e:
    print("caught:", e)

class ReadOnly:
    @property
    def x(self):
        return 42

r = ReadOnly()
print(r.x)
try:
    r.x = 5
except AttributeError as e:
    print("no setter")
