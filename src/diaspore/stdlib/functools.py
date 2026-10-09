"""Provide functional programming helpers in the Diaspore subset."""


def reduce(function, iterable, *initial):
    it = iter(iterable)
    if initial:
        value = initial[0]
    else:
        try:
            value = next(it)
        except StopIteration:
            raise TypeError("reduce() of empty iterable with no initial value") from None
    for element in it:
        value = function(value, element)
    return value


def partial(func, *args, **keywords):
    def newfunc(*fargs, **fkeywords):
        merged = dict(keywords)
        merged.update(fkeywords)
        return func(*args, *fargs, **merged)
    newfunc.func = func
    newfunc.args = args
    newfunc.keywords = keywords
    return newfunc


def cache(user_function):
    return lru_cache()(user_function)


def lru_cache(maxsize=128, typed=False):
    if callable(maxsize):
        return lru_cache()(maxsize)

    def decorator(func):
        store = {}

        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            if key in store:
                return store[key]
            result = func(*args, **kwargs)
            store[key] = result
            return result

        wrapper.cache_clear = store.clear
        return wrapper
    return decorator


def wraps(wrapped):
    def decorator(wrapper):
        for attr in ("__name__", "__doc__"):
            try:
                setattr(wrapper, attr, getattr(wrapped, attr))
            except AttributeError:
                pass
        return wrapper
    return decorator


def cmp_to_key(mycmp):
    class K:
        def __init__(self, obj):
            self.obj = obj
        def __lt__(self, other):
            return mycmp(self.obj, other.obj) < 0
        def __gt__(self, other):
            return mycmp(self.obj, other.obj) > 0
        def __eq__(self, other):
            return mycmp(self.obj, other.obj) == 0
        def __le__(self, other):
            return mycmp(self.obj, other.obj) <= 0
        def __ge__(self, other):
            return mycmp(self.obj, other.obj) >= 0
    return K
