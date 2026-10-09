print("main starts, __name__ =", __name__)
import pkg
import pkg.sub.deep
from pkg import util, VERSION
from pkg.sub import deep
from pkg.sub.deep import treasure, dig
import pkg.util as put
from pkg.allstar import *
from pkg.plain import *

print("pkg:", pkg.NAME, pkg.VERSION, VERSION)
print("reach deep three ways:", pkg.sub.deep.treasure, deep.treasure, treasure)
print("dig:", dig(2), pkg.sub.deep.dig(3))
print("util:", util.helper(), put.helper(), pkg.util.helper())
print("star with __all__:", exported_one, exported_two)
try:
    print(hidden_from_star)
except NameError:
    print("__all__ respected: hidden_from_star not imported")
print("star without __all__:", plain_value, plain_func(5))
try:
    print(_private_value)
except NameError:
    print("underscore names not star-imported")
print("sub attr is module:", pkg.sub.LABEL)
print("main ends")
