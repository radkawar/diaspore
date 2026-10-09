import re

log = "2026-08-24 12:01:33 ERROR db: connection refused (attempt 3)"
m = re.search(r"(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2}) (\w+)", log)
print(bool(m), m.group(0))
print(m.group(1), m.group(4), m.group(7), m.groups())
print(m.start(), m.end(), m.span(), m.span(7))

print(bool(re.match(r"\w+", "hello world")), bool(re.match(r"world", "hello world")))
print(bool(re.fullmatch(r"[a-z]+", "hello")), bool(re.fullmatch(r"[a-z]+", "hello!")))
print(re.search(r"zz", "abc"))

print(re.findall(r"\d+", "a1 bb22 ccc333 d"))
print(re.findall(r"([a-z]+)(\d+)", "a1 bb22 ccc333"))
print(re.findall(r"\b\w{3}\b", "the cat sat on a mat"))

print(re.sub(r"\s+", " ", "too   much\t\twhitespace"))
print(re.sub(r"(\w+)@(\w+)", r"\2.\1", "user@example and admin@site"))
print(re.sub(r"a", "X", "banana", 2))

print(re.split(r"[,;]\s*", "one, two;three ,four"))
print(re.split(r"(\d)", "a1b2c", 1))
print(re.escape("1+1=2 (really?)"))

print(bool(re.search(r"hello", "Say HELLO", re.IGNORECASE)))
print(re.findall(r"^\w+", "first line\nsecond line", re.MULTILINE))
print(bool(re.search(r"a.c", "a\nc")), bool(re.search(r"a.c", "a\nc", re.DOTALL)))
print(re.findall(r"(?i)[aeiou]+", "ProGrAmmIng"))

ident = r"^[A-Za-z_][A-Za-z_0-9]*$"
for cand in ["valid_name", "_private", "2fast", "kebab-case", "CamelCase9"]:
    print(cand, "->", bool(re.match(ident, cand)))

semver = r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.]+))?$"
for v in ["1.2.3", "10.0.99-rc.1", "1.2", "v1.2.3"]:
    mm = re.fullmatch(semver, v)
    print(v, "->", mm.groups() if mm else None)

print("regex done")
