"""Provide string constants and Template in the Diaspore subset."""

ascii_lowercase = "abcdefghijklmnopqrstuvwxyz"
ascii_uppercase = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
ascii_letters = ascii_lowercase + ascii_uppercase
digits = "0123456789"
hexdigits = digits + "abcdef" + "ABCDEF"
octdigits = "01234567"
punctuation = r"""!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~"""
whitespace = " \t\n\r\x0b\x0c"
printable = digits + ascii_letters + punctuation + whitespace


def capwords(s, sep=None):
    return (sep or " ").join(x.capitalize() for x in s.split(sep))


class Template:
    def __init__(self, template):
        self.template = template

    def substitute(self, mapping=None, **kws):
        import re
        data = dict(mapping or {})
        data.update(kws)

        def repl(m):
            key = m.group("named") or m.group("braced")
            if key is not None:
                return str(data[key])
            if m.group("escaped") is not None:
                return "$"
            return m.group()
        pattern = (
            r"\$(?:(?P<escaped>\$)|(?P<named>[_a-z][_a-z0-9]*)|"
            r"\{(?P<braced>[_a-z][_a-z0-9]*)\})"
        )
        return re.sub(pattern, repl, self.template, flags=re.IGNORECASE)

    def safe_substitute(self, mapping=None, **kws):
        import re
        data = dict(mapping or {})
        data.update(kws)

        def repl(m):
            key = m.group("named") or m.group("braced")
            if key is not None and key in data:
                return str(data[key])
            return m.group()
        pattern = (
            r"\$(?:(?P<escaped>\$)|(?P<named>[_a-z][_a-z0-9]*)|"
            r"\{(?P<braced>[_a-z][_a-z0-9]*)\})"
        )
        return re.sub(pattern, repl, self.template, flags=re.IGNORECASE)
