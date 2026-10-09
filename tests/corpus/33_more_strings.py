s = "key=value=more"
print(s.partition("="), s.rpartition("="), s.partition("@"))
print("diaspore.json".removesuffix(".json"), "diaspore.json".removesuffix(".xml"))
print("app_main".removeprefix("app_"), "main".removeprefix("app_"))
print("hi".center(8), "|", "hi".center(9, "*"), "|", "toolong".center(3))
print("abc".islower(), "Abc".islower(), "ABC".isupper(), "AbC".isupper(), "123".islower())
print("MiXeD".swapcase(), "hello WORLD".swapcase())
print(format(42), format(42, "06d"), format(3.14159, ".2f"), format(255, "x"))
print(format("txt", ">6"), format("txt", "*^7"), format(0.5, ".0%"))
b = b"a-b-c"
print(b.replace(b"-", b"+"), b"  pad  ".lstrip(), b"  pad  ".rstrip())
print({1, 2, 3}.symmetric_difference({2, 3, 4}), sorted({1, 2} ^ {2, 3}))
head, sep, tail = "GET /path HTTP/1.1".partition(" ")
print(head, repr(sep), tail)
proto = "HTTP/1.1"
print(proto.partition("/")[2], proto.rpartition(".")[0])
