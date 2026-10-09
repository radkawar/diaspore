l = [1, 2]
l.append(l)
print(l)
print(len(l), l[2] is l, l[2][2] is l)

d = {"name": "root"}
d["self"] = d
print(d)
print(d["self"]["self"]["name"])

a = [1]
b = [2, a]
a.append(b)
print(a)
print(b)

t = (1, l)
print(t)

nested = {"outer": {"inner": []}}
nested["outer"]["inner"].append(nested)
print(nested)

l.pop()
print(l)
