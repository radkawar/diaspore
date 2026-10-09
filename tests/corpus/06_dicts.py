d = {"b": 2, "a": 1, "c": 3}
print(d, len(d), d["a"], list(d.keys()), list(d.values()))
print(d.get("a"), d.get("z"), d.get("z", -1))
d["d"] = 4
d.update({"e": 5, "a": 10})
print(d)
print(d.pop("e"), d.pop("zz", "default"), d)
print(d.setdefault("a", 99), d.setdefault("f", 6), d)
print("a" in d, "zz" in d, "zz" not in d)
del d["f"]
print(d)
c = d.copy()
c["extra"] = True
print(sorted(d.items()), sorted(c.items()))
print(dict(), dict(a=1, b=2), dict([("x", 1), ("y", 2)]), dict({"k": "v"}))
nested = {"user": {"name": "ada", "langs": ["py", "go"]}}
print(nested["user"]["langs"][1], nested)
keyed = {1: "int", 2.5: "float", True: "bool?", (1, 2): "tuple", None: "none"}
print(keyed[1], keyed[1.0], keyed[True], keyed[(1, 2)], keyed[None])
print(keyed)
counts = {}
for ch in "abracadabra":
    counts[ch] = counts.get(ch, 0) + 1
print(sorted(counts.items()))
for k in d:
    print("iter", k, d[k])
for k, v in d.items():
    print("pair", k, v)
d.clear()
print(d, bool(d), bool({0: 0}))
