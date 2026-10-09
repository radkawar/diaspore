import shapes

print("loading textutil")

WORDS = ["alpha", "beta"]


def shout(s):
    return s.upper() + "!"


def slugify(s):
    out = []
    for ch in s.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    slug = "".join(out)
    return slug.strip("-")


def counter_from_shapes():
    return shapes.counter
