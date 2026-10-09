def fib(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


print(fib(120), fib(200))


def fact(n):
    r = 1
    for i in range(2, n + 1):
        r *= i
    return r


print(fact(30))
print(fact(25) // fact(20), fact(25) % fact(20))

big = 2 ** 100
print(big, big + 1, big - 1, -big)
print(big * big, big // 3, big % 7, 10 ** 30 % 7)
print(2 ** 64, 2 ** 63, 2 ** 63 - 1, -(2 ** 63), -(2 ** 63) - 1)
print(9223372036854775807 + 1, -9223372036854775808 - 1)
print(9223372036854775807 * 2, abs(-9223372036854775808))
print((-(2 ** 70)) // 3, (-(2 ** 70)) % 3, (2 ** 70) // -3, (2 ** 70) % -3)
print(divmod(2 ** 70, 997), divmod(-(2 ** 70), 997))
print(pow(2, 100, 97), pow(3, 200, 1000000007))
print(1 << 100, (1 << 100) >> 37)

print(hex(2 ** 100), bin(2 ** 68), oct(2 ** 90))
print(f"{2 ** 100:x}", f"{2 ** 100:,}", f"{fact(30):>40}")
print(int("123456789012345678901234567890"), int("-" + "9" * 25))
print(str(fact(40)), repr(-fact(35)))

print(big == 2 ** 100, big == big + 1, big > 2 ** 99, -big < big)
print(big == 1e30, 10 ** 22 == float(10 ** 22), float(2 ** 100) > 2 ** 99)
print(sorted([2 ** 80, -(2 ** 80), 0, 2 ** 64, 17, -(2 ** 100)]))
print(max(fact(20), 2 ** 61, fib(90)), min(fact(20), 2 ** 61, fib(90)))

d = {2 ** 88: "big", 17: "small"}
d[2 ** 88] += "!"
print(d[2 ** 88], d, len(d), 2 ** 88 in d)
print({fib(100), fib(100), fib(101)} == {fib(101), fib(100)})

import json

blob = json.dumps({"huge": 2 ** 90, "list": [fact(21), -(2 ** 64)]})
print(blob)
back = json.loads(blob)
print(back["huge"] == 2 ** 90, back["list"])

print(sum([2 ** 62, 2 ** 62, 2 ** 62]), sum(fib(k) for k in range(150, 153)))
print(bool(big), bool(big - big), big - big == 0)
squares = [n * n for n in [10 ** 10, 10 ** 12, 3 ** 40]]
print(squares)
try:
    big / 0
except ZeroDivisionError as e:
    print("div:", e)
try:
    big // 0
except ZeroDivisionError as e:
    print("floordiv:", e)
print(big / (2 ** 99), (2 ** 200) / (2 ** 199))
