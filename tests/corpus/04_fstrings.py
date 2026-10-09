name = "diaspore"
n = 42
pi = 3.14159265
print(f"plain {name} and {n}")
print(f"{n:5}|{n:<5}|{n:>5}|{n:^5}|")
print(f"{n:05d}|{n:+d}|{-n:+d}|{n: d}|")
print(f"{n:x}|{n:X}|{n:b}|{n:o}|{255:#x}" if False else f"{n:x}|{n:X}|{n:b}|{n:o}|")
print(f"{pi:.2f}|{pi:10.3f}|{pi:<10.3f}|{pi:e}|")
print(f"{0.25:%}|{0.25:.1%}|")
print(f"{1234567:,}|{1234567.891:,.2f}|")
print(f"{name:*^10}|{name:.2}|")
print(f"{name!r} vs {name!s}")
print(f"{'nested'} {1 + 2} {[i for i in range(3)]}")
w = 8
print(f"{pi:{w}.2f}|")
print(f"{{literal braces}} {n}")
print(f"{None} {True} {[1, 'a']} {('t', 2)} {{'k': 1}}".replace("  ", " "))
val = -7.5
print(f"{val:+08.2f}|{val:=+8.1f}|" if False else f"{val:+08.2f}|")
print(f"result={n * 2}")
