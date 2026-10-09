"""Provide iterator helpers in the Diaspore subset."""


def count(start=0, step=1):
    n = start
    while True:
        yield n
        n += step


def cycle(iterable):
    saved = []
    for item in iterable:
        yield item
        saved.append(item)
    while saved:
        for item in saved:
            yield item


def repeat(obj, times=None):
    if times is None:
        while True:
            yield obj
    else:
        for _ in range(times):
            yield obj


def chain(*iterables):
    for it in iterables:
        for item in it:  # noqa: UP028 - Do not forward send/throw to input iterators.
            yield item


def _from_iterable(iterables):
    for it in iterables:
        for item in it:  # noqa: UP028 - Do not forward send/throw to input iterators.
            yield item


chain.from_iterable = _from_iterable


def islice(iterable, *args):
    s = slice(*args)
    start = s.start or 0
    stop = s.stop
    step = s.step or 1
    it = iter(iterable)
    for _ in range(start):
        next(it, None)
    i = start
    while stop is None or i < stop:
        try:
            val = next(it)
        except StopIteration:
            return
        yield val
        for _ in range(step - 1):
            next(it, None)
        i += step


def takewhile(predicate, iterable):
    for x in iterable:
        if predicate(x):
            yield x
        else:
            break


def dropwhile(predicate, iterable):
    it = iter(iterable)
    for x in it:
        if not predicate(x):
            yield x
            break
    for x in it:
        yield x


def filterfalse(predicate, iterable):
    for x in iterable:
        if not (predicate(x) if predicate else x):
            yield x


def accumulate(iterable, func=None):
    it = iter(iterable)
    try:
        total = next(it)
    except StopIteration:
        return
    yield total
    for x in it:
        total = func(total, x) if func else total + x
        yield total


def product(*iterables, repeat=1):
    pools = [list(it) for it in iterables] * repeat
    result = [[]]
    for pool in pools:
        result = [x + [y] for x in result for y in pool]
    for prod in result:
        yield tuple(prod)


def permutations(iterable, r=None):
    pool = list(iterable)
    n = len(pool)
    r = n if r is None else r
    if r > n:
        return
    for prod in product(range(n), repeat=r):
        if len(set(prod)) == r:
            yield tuple(pool[i] for i in prod)


def combinations(iterable, r):
    pool = list(iterable)
    n = len(pool)
    if r > n:
        return
    indices = list(range(r))
    yield tuple(pool[i] for i in indices)
    while True:
        found = -1
        for i in reversed(range(r)):
            if indices[i] != i + n - r:
                found = i
                break
        if found == -1:
            return
        indices[found] += 1
        for j in range(found + 1, r):
            indices[j] = indices[j - 1] + 1
        yield tuple(pool[i] for i in indices)


def starmap(function, iterable):
    for args in iterable:
        yield function(*args)


def zip_longest(*iterables, fillvalue=None):
    iterators = [iter(it) for it in iterables]
    num_active = len(iterators)
    if not num_active:
        return
    while True:
        values = []
        done = 0
        for it in iterators:
            try:
                values.append(next(it))
            except StopIteration:
                done += 1
                values.append(fillvalue)
        if done == num_active:
            return
        yield tuple(values)


def pairwise(iterable):
    it = iter(iterable)
    try:
        prev = next(it)
    except StopIteration:
        return
    for cur in it:
        yield (prev, cur)
        prev = cur
