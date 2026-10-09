b = b"hello world"
print(b, len(b), b[0], b[-1])
print(b[0:5], b[6:], b[::2], b[::-1])
print(b + b" again", b"ab" * 3)
print(b.find(b"world"), b.find(b"zz"), b.find(b"l"))
print(b.split(b" "), b"a,b,,c".split(b","))
print(b.startswith(b"hell"), b.endswith(b"rld"), b.startswith(b"world"))
print(b.upper(), b"MiXeD".lower())
print(b"  padded  ".strip())
print(b.decode("utf-8"), b"caf\xc3\xa9".decode("utf-8"))
print("héllo".encode("utf-8"), "abc".encode("latin-1"))
print("héllo".encode("utf-8").decode("utf-8"))
print(b"a" < b"b", b"abc" == b"abc", b"a" in b"cat" if False else b"o" in b)
print(bytes(), bytes(3), bytes([72, 105]))
print(list(b"AB"), [x + 1 for x in b"AB"])
for byte in b"hi":
    print("byte", byte)
crlf = b"GET / HTTP/1.1\r\nHost: x\r\n\r\nbody"
sep = crlf.find(b"\r\n\r\n")
head, rest = crlf[:sep], crlf[sep + 4:]
print(head.split(b"\r\n"), rest)
print(repr(b"quote'\" tab\t"), str(b"x"))
total = 0
for v in b"bytes":
    total += v
print(total)
