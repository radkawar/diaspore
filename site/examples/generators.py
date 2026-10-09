def fibonacci():
    a, b = 0, 1
    while True:
        yield a
        a, b = b, a + b


def take(sequence, count):
    for _ in range(count):
        yield next(sequence)


print("The first twelve Fibonacci numbers:")
print(list(take(fibonacci(), 12)))
print()
print("Squares, computed on demand:")
squares = (n * n for n in range(1, 6))
for value in squares:
    print(f"  {value:>3}")
