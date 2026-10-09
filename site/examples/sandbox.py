"""The browser host grants no filesystem, network or task capabilities."""

import json

import net
import tasks

try:
    open("/etc/passwd")
except PermissionError as e:
    print("file access blocked ->", e)

try:
    net.listen("127.0.0.1", 8080)
except PermissionError as e:
    print("network blocked     ->", e)

try:
    tasks.spawn(len, [1, 2, 3])
except PermissionError as e:
    print("task spawn blocked  ->", e)

print()
print("pure compute is always allowed:")
print("  sum of squares:", sum(i * i for i in range(100)))
print("  sorted:", sorted("sandbox"))
print("  json round-trip:", json.loads(json.dumps({"safe": True})))
