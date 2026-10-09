"""Provide shallow and deep copying in the Diaspore subset."""


def copy(x):
    cls = type(x)
    if x is None or isinstance(x, (int, float, bool, str, bytes, complex)):
        return x
    if isinstance(x, list):
        return list(x)
    if isinstance(x, dict):
        return dict(x)
    if isinstance(x, set):
        return set(x)
    if isinstance(x, tuple):
        return x
    cp = getattr(x, "__copy__", None)
    if cp is not None:
        return cp()
    new = cls.__new__(cls) if hasattr(cls, "__new__") else _blank(cls)
    if hasattr(x, "__dict__"):
        for k, v in vars(x).items():
            setattr(new, k, v)
    return new


def deepcopy(x, memo=None):
    if memo is None:
        memo = {}
    if x is None or isinstance(x, (int, float, bool, str, bytes, complex)):
        return x
    idx = id(x)
    if idx in memo:
        return memo[idx]
    if isinstance(x, list):
        y = []
        memo[idx] = y
        for item in x:
            y.append(deepcopy(item, memo))
        return y
    if isinstance(x, dict):
        y = {}
        memo[idx] = y
        for k, v in x.items():
            y[deepcopy(k, memo)] = deepcopy(v, memo)
        return y
    if isinstance(x, set):
        y = set()
        memo[idx] = y
        for item in x:
            y.add(deepcopy(item, memo))
        return y
    if isinstance(x, tuple):
        y = tuple(deepcopy(item, memo) for item in x)
        memo[idx] = y
        return y
    dc = getattr(x, "__deepcopy__", None)
    if dc is not None:
        return dc(memo)
    cls = type(x)
    new = cls.__new__(cls) if hasattr(cls, "__new__") else _blank(cls)
    memo[idx] = new
    if hasattr(x, "__dict__"):
        for k, v in vars(x).items():
            setattr(new, k, deepcopy(v, memo))
    return new


def _blank(cls):
    class _B:
        pass
    b = _B()
    return b
