print("init pkg.sub.deep, __name__ =", __name__)
from . import LABEL
from .. import NAME as PKG_NAME
from ..const import BASE

treasure = f"gold[{LABEL}|{PKG_NAME}|{BASE}]"


def dig(n):
    return "x" * n + str(BASE)
