from _operator import index


class Index:
    def __index__(self):
        return 2 ** 100 + 7


class Integer(int):
    def __index__(self):
        return 99


class Bad:
    def __index__(self):
        return 1.5


class Failure:
    def __index__(self):
        raise ValueError("index failure")


for value in (0, -1, True, False, 2 ** 100, -(2 ** 100), Integer(7), Index()):
    result = index(value)
    print(result, type(result).__name__)

for value in (1.5, "3", None, Bad(), Failure()):
    try:
        index(value)
    except (TypeError, ValueError) as error:
        print(type(error).__name__, str(error))
