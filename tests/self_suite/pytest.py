"""Minimal pytest API for unchanged self-suite assertions, not a runtime shim."""

_PARAMETERS = []


class Raises:
    def __init__(self, expected, match):
        self.expected = expected
        self.match = match
        self.value = None
        self.type = None
        self.tb = None

    def __enter__(self):
        return self

    def __exit__(self, kind, value, tb):
        if kind is None:
            raise AssertionError("DID NOT RAISE " + str(self.expected))
        if not isinstance(value, self.expected):
            return False
        if self.match is not None:
            import re

            if re.search(self.match, str(value)) is None:
                raise AssertionError("exception message does not match " + self.match)
        self.value = value
        self.type = kind
        self.tb = tb
        return True


def raises(expected, *args, match=None, **kwargs):
    context = Raises(expected, match)
    if args:
        with context:
            args[0](*args[1:], **kwargs)
    return context


class Mark:
    def parametrize(self, argnames, argvalues, ids=None):
        if isinstance(argnames, str):
            names = [name.strip() for name in argnames.split(",")]
        else:
            names = list(argnames)
        values = list(argvalues)

        def decorate(function):
            _PARAMETERS.append((function, names, values))
            return function

        return decorate


mark = Mark()


def cases(function):
    """Expand stacked parametrizations in pytest's decorator application order."""
    rows = [{}]
    for target, names, values in _PARAMETERS:
        if target is function:
            expanded = []
            for value in values:
                fields = [value] if len(names) == 1 else list(value)
                if len(fields) != len(names):
                    raise ValueError("parametrize names and values have different lengths")
                for row in rows:
                    result = dict(row)
                    for index in range(len(names)):
                        if names[index] in result:
                            raise ValueError("duplicate parametrization: " + names[index])
                        result[names[index]] = fields[index]
                    expanded.append(result)
            rows = expanded
    return rows
