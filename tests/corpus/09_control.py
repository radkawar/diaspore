x = 7
if x > 10:
    print("big")
elif x > 5:
    print("medium")
else:
    print("small")

i = 0
while i < 5:
    i += 1
    if i == 2:
        continue
    if i == 4:
        break
    print("while", i)
else:
    print("no break")

while i < 6:
    i += 1
    print("while2", i)
else:
    print("while2 done")

for n in range(8):
    if n % 2:
        continue
    if n > 5:
        break
    print("for", n)
else:
    print("unreached")

for n in range(3):
    print("for2", n)
else:
    print("for2 done")

for a in range(3):
    for b in range(3):
        if b > a:
            break
        print("nest", a, b)

val = "yes" if x > 5 else "no"
print(val, "even" if x % 2 == 0 else "odd")

def noisy(v, label):
    print("eval", label)
    return v

print(noisy(True, "a") or noisy(True, "b"))
print(noisy(False, "c") and noisy(True, "d"))
print(noisy(0, "e") or noisy("", "f") or "fallback")
print(1 or 2, 0 and 5, None or 0, "" or [], 2 and 3)

if (m := x * 2) > 10:
    print("walrus", m)
data = [1, 2, 3, 4]
while (item := data.pop()) > 2:
    print("popped", item)
print("left", data)
total = 0
for v in range(10):
    total += v
    pass
print(total)
