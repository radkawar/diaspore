"""A tiny regex engine (literal, '.', '*', '+', '?', '^', '$', [abc], [a-z])
in the diaspore subset — Pike-style recursive matching."""


def parse_class(pattern, i):
    """Return (set_of_chars, negate, next_index) for a [...] class at i."""
    i += 1
    negate = pattern[i] == "^"
    if negate:
        i += 1
    chars = set()
    while pattern[i] != "]":
        if pattern[i + 1] == "-" and pattern[i + 2] != "]":
            for code in range(ord(pattern[i]), ord(pattern[i + 2]) + 1):
                chars.add(chr(code))
            i += 3
        else:
            chars.add(pattern[i])
            i += 1
    return chars, negate, i + 1


def match_one(pattern, i, text, j):
    """Does the single pattern element at i match text[j]? Returns (ok, ni)
    where ni is the index just past this element."""
    if j >= len(text):
        return False, element_end(pattern, i)
    ch = pattern[i]
    if ch == "[":
        chars, negate, ni = parse_class(pattern, i)
        return (text[j] not in chars) if negate else (text[j] in chars), ni
    if ch == ".":
        return True, i + 1
    if ch == "\\":
        return text[j] == pattern[i + 1], i + 2
    return text[j] == ch, i + 1


def element_end(pattern, i):
    if pattern[i] == "[":
        _, _, ni = parse_class(pattern, i)
        return ni
    if pattern[i] == "\\":
        return i + 2
    return i + 1


def match_here(pattern, i, text, j):
    if i == len(pattern):
        return True
    if pattern[i] == "$" and i + 1 == len(pattern):
        return j == len(text)
    end = element_end(pattern, i)
    suffix = pattern[end] if end < len(pattern) else ""
    if suffix == "*":
        return match_star(pattern, i, end, text, j, 0)
    if suffix == "+":
        return match_star(pattern, i, end, text, j, 1)
    if suffix == "?":
        ok, _ = match_one(pattern, i, text, j)
        if ok and match_here(pattern, end + 1, text, j + 1):
            return True
        return match_here(pattern, end + 1, text, j)
    ok, _ = match_one(pattern, i, text, j)
    return ok and match_here(pattern, end, text, j + 1)


def match_star(pattern, i, end, text, j, minimum):
    count = 0
    while True:
        if count >= minimum and match_here(pattern, end + 1, text, j):
            return True
        ok, _ = match_one(pattern, i, text, j)
        if not ok:
            return False
        j += 1
        count += 1


def match(pattern, text):
    if pattern.startswith("^"):
        return match_here(pattern, 1, text, 0)
    for j in range(len(text) + 1):
        if match_here(pattern, 0, text, j):
            return True
    return False


TESTS = [
    ("abc", "xabcy", True),
    ("^abc", "xabcy", False),
    ("^abc", "abcy", True),
    ("abc$", "xabc", True),
    ("^abc$", "abc", True),
    ("^abc$", "abcd", False),
    ("a.c", "abc", True),
    ("a.c", "ac", False),
    ("ab*c", "ac", True),
    ("ab*c", "abbbbc", True),
    ("ab+c", "ac", False),
    ("ab+c", "abbc", True),
    ("ab?c", "ac", True),
    ("ab?c", "abc", True),
    ("ab?c", "abbc", False),
    ("^a*$", "", True),
    ("^a*$", "aaa", True),
    ("^a*$", "aab", False),
    ("[abc]+", "cab", True),
    ("^[abc]+$", "cabx", False),
    ("[a-z]+", "HELLO there", True),
    ("^[a-z]+$", "HELLO", False),
    ("[^0-9]+", "123", False),
    ("^[0-9]+$", "42", True),
    ("h[ae]llo", "hello", True),
    ("h[ae]llo", "hallo", True),
    ("h[ae]llo", "hullo", False),
    ("^-?[0-9]+\\.[0-9]+$", "-3.14", True),
    ("^-?[0-9]+\\.[0-9]+$", "314", False),
    ("^[a-z_][a-z_0-9]*$", "snake_case_2", True),
    ("^[a-z_][a-z_0-9]*$", "2fast", False),
    (".*", "anything", True),
]

for pattern, text, expected in TESTS:
    got = match(pattern, text)
    status = "ok " if got == expected else "FAIL"
    print(f"{status} {pattern!r:28} vs {text!r:16} -> {got}")

words = ["hello", "HELLO", "h3llo", "heeello", "hllo"]
print([w for w in words if match("^he*llo$", w)])
print([w for w in words if match("^[a-z0-9]+$", w)])
