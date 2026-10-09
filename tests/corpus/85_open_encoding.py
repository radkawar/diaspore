# diaspore-caps: fs
for encoding in [None, "utf8", "UTF-8", "utf_8"]:
    with open(__file__, encoding=encoding) as source:
        print(source.readline().strip())
try:
    open(__file__, "rb", encoding="utf-8")
except ValueError as exc:
    print(str(exc))
