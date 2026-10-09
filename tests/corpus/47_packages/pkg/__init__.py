print("init pkg, __name__ =", __name__)
NAME = "pkg"
VERSION = 3
from . import util
from .const import BASE
print("pkg sees const BASE =", BASE)
