"""Provide rational arithmetic in the Diaspore subset."""


def _gcd(a, b):
    a, b = abs(a), abs(b)
    while b:
        a, b = b, a % b
    return a


class Fraction:
    def __init__(self, numerator=0, denominator=None):
        if denominator is None:
            if isinstance(numerator, Fraction):
                self._n = numerator._n
                self._d = numerator._d
                return
            if isinstance(numerator, int):
                self._n = numerator
                self._d = 1
                return
            if isinstance(numerator, str):
                if "/" in numerator:
                    n, d = numerator.split("/")
                    numerator, denominator = int(n), int(d)
                else:
                    numerator, denominator = int(numerator), 1
            else:
                raise TypeError("Fraction requires int/str/Fraction")
        if denominator == 0:
            raise ZeroDivisionError(f"Fraction({numerator}, 0)")
        g = _gcd(numerator, denominator)
        if denominator < 0:
            numerator, denominator = -numerator, -denominator
        self._n = numerator // g
        self._d = denominator // g

    @property
    def numerator(self):
        return self._n

    @property
    def denominator(self):
        return self._d

    def __add__(self, o):
        o = _coerce(o)
        return Fraction(self._n * o._d + o._n * self._d, self._d * o._d)

    def __radd__(self, o):
        return self.__add__(o)

    def __sub__(self, o):
        o = _coerce(o)
        return Fraction(self._n * o._d - o._n * self._d, self._d * o._d)

    def __mul__(self, o):
        o = _coerce(o)
        return Fraction(self._n * o._n, self._d * o._d)

    def __rmul__(self, o):
        return self.__mul__(o)

    def __truediv__(self, o):
        o = _coerce(o)
        return Fraction(self._n * o._d, self._d * o._n)

    def __eq__(self, o):
        if isinstance(o, int):
            return self._d == 1 and self._n == o
        if isinstance(o, Fraction):
            return self._n == o._n and self._d == o._d
        return NotImplemented if False else float(self) == o

    def __lt__(self, o):
        o = _coerce(o)
        return self._n * o._d < o._n * self._d

    def __le__(self, o):
        o = _coerce(o)
        return self._n * o._d <= o._n * self._d

    def __hash__(self):
        return hash((self._n, self._d))

    def __float__(self):
        return self._n / self._d

    def __repr__(self):
        return f"Fraction({self._n}, {self._d})"

    def __str__(self):
        return f"{self._n}/{self._d}" if self._d != 1 else str(self._n)


def _coerce(o):
    if isinstance(o, Fraction):
        return o
    if isinstance(o, int):
        return Fraction(o)
    raise TypeError("cannot coerce to Fraction")
