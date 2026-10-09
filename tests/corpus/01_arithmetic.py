print(7 + 3, 7 - 3, 7 * 3, 7 / 3, 7 // 3, 7 % 3, 7 ** 3)
print(-7 // 3, -7 % 3, 7 // -3, 7 % -3)
print(2 ** 10, 2 ** 62, (-2) ** 3)
print(divmod(17, 5), divmod(-17, 5))
print(abs(-4), abs(4.5), abs(-0.0))
print(round(2.5), round(3.5), round(-2.5), round(2.675, 2), round(7, None))
print(1 << 10, 1024 >> 3, 0b1100 & 0b1010, 0b1100 | 0b1010, 0b1100 ^ 0b1010, ~5)
print(1 < 2 < 3, 1 < 2 > 3, 3 >= 3 == 3)
print(10 == 10.0, 1 != 2, True + True, True * 5)
x = 10
x += 5
x *= 2
x -= 3
x //= 2
x **= 2
x %= 7
print(x)
y = 2.0
y /= 8
print(y)
print(5 / 2, 4 / 2, 1 / 3)
print(int(3.99), int(-3.99), int("42"), int("-17"), int("ff", 16), int("101", 2))
print(float(3), float("2.5"), float("1e3"))
print(pow(2, 10), pow(2, 10, 97), pow(2, -1))
print(max(1, 2.5), min(-1, -2.5))
