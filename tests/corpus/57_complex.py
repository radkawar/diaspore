z = 3 + 4j
print(z, z.real, z.imag, abs(z), z.conjugate())
print(1j, 1j * 1j, (2 + 3j) + (1 - 1j), (2 + 3j) * (1 + 1j))
print(complex(1, 2), complex(5), complex("3+4j"), complex())
print((1 + 2j) / (3 + 4j), (1 + 2j) ** 2, 2 ** (1j))
print(complex(0, 1) == 1j, (1 + 0j) == 1, hash(1 + 0j) == hash(1))
print(type(1j), type(1j).__name__, isinstance(1j, complex))
print([1j, 2 + 0j, -3j, complex(0, -1)])
print((1.5 + 2.5j).real, (1.5 + 2.5j).imag)
print((5).real, (5).imag, (5).conjugate(), (3.0).real, (3.0).is_integer())
d = {1j: "i", 2 + 3j: "z"}
print(d[1j], d[complex(2, 3)])
try:
    1j < 2j
except TypeError as e:
    print("no ordering:", "not supported" in str(e) or "complex" in str(e))
print(f"{z}", f"{z!r}", f"{1j}")
print(complex(float('inf'), 1), complex(0, float('nan')))
