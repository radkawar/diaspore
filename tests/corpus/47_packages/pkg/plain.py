print("init pkg.plain, __name__ =", __name__)
plain_value = 42
_private_value = "secret"


def plain_func(n):
    return n * plain_value
