try:
    from _diaspore_missing_accelerator import *
except ImportError:
    print("optional accelerator missing")

try:
    from _operator import *
except ImportError:
    def add(a, b):
        return a + b
print("accelerator fallback", add(20, 22))

from exports import *
print("computed exports", first, _private)
try:
    omitted
except NameError:
    print("all excludes public names")

import exports
exports.__all__.append("later")
exports.later = 19
from exports import *
print("live exports", later)

from public_names import *
print("live namespace", conditional, copied)
try:
    deleted
except NameError:
    print("deleted names excluded")
try:
    _hidden
except NameError:
    print("private names excluded")

from cycle_a import *
print("cycle", early, late, observed)

try:
    from broken import *
except ImportError as exc:
    print("import failure", str(exc))
try:
    partial
except NameError:
    print("failed module binds nothing")

try:
    from invalid_all import *
except TypeError:
    print("all requires string names")

try:
    from missing_export import *
except AttributeError:
    print("all missing attribute")
