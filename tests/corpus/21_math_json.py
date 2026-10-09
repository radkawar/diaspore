import math
import json
from math import sqrt, floor, ceil

print(math.pi, math.e)
print(sqrt(16), sqrt(2), math.sqrt(0.25))
print(floor(3.7), ceil(3.2), floor(-3.7), ceil(-3.2), floor(5), ceil(5))
print(math.fabs(-3.5), math.pow(2, 10), math.exp(0), math.log(math.e))
print(math.log2(1024), math.log10(1000), round(math.log(100, 10), 10) if False else math.hypot(3, 4))
print(round(math.sin(0), 10), round(math.cos(0), 10), round(math.tan(0), 10))
print(math.fmod(7, 3), math.fmod(-7, 3))
print(math.isnan(math.nan), math.isinf(math.inf), math.isnan(1.0), math.isinf(-math.inf))
try:
    math.sqrt(-1)
except ValueError as e:
    print("domain:", e)

data = {"name": "diaspore", "version": 1, "tags": ["json", "vm"], "meta": {"ok": True, "score": 9.5, "none": None}}
s = json.dumps(data)
print(s)
back = json.loads(s)
print(back == data, back["meta"]["score"], back["tags"][0])
print(json.dumps([1, 2.5, "three", None, True, False]))
print(json.dumps({"nested": {"deep": [{"x": 1}]}}))
print(json.dumps("plain string"), json.dumps(42), json.dumps(None))
print(json.loads("[1, 2.0, \"3\", null, true]"))
nums = json.loads("[1, 1.0, 100, 0.5]")
print([x * 3 for x in nums])
print(json.dumps({"b": 1, "a": 2}))
pretty = json.dumps({"k": [1, 2]}, indent=2)
print(pretty)
try:
    json.loads("{bad json")
except ValueError:
    print("bad json caught")
try:
    json.dumps({"vals": {1, 2}})
except TypeError:
    print("unserializable caught")
roundtrip = json.loads(json.dumps({"π": "unicode ✓"}))
print(roundtrip)
