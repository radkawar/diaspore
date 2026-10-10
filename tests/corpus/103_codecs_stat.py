import codecs
import errno
import stat


for name in ("utf-8", "UTF_8", "ascii", "us-ascii", "latin-1", "iso8859-1"):
    codec = codecs.lookup(name)
    print(name, codec.name, codec.encode("ABC"), codec.decode(b"ABC"))

print(codecs.encode("A\u00e9\u20ac\U0001f600", "utf-8"))
print(codecs.decode(b"A\xc3\xa9\xe2\x82\xac\xf0\x9f\x98\x80", "utf-8"))
print(codecs.encode("\x00\x7f\x80\xff", "latin-1"))
print(repr(codecs.decode(b"\x00\x7f\x80\xff", "latin-1")))

for name in ("ascii", "latin-1", "utf-8"):
    encoder = codecs.getincrementalencoder(name)()
    decoder = codecs.getincrementaldecoder(name)()
    print(name, encoder.encode("A"), encoder.encode("B", final=True))
    print(decoder.decode(b"A"), decoder.decode(b"B", final=True))
    print(encoder.getstate(), decoder.getstate())
    encoder.reset()
    decoder.reset()
    print(encoder.getstate(), decoder.getstate())

utf8 = codecs.getincrementaldecoder("utf-8")()
for part in (b"A\xe2", b"\x82", b"\xac\xf0\x9f", b"\x98\x80"):
    print(repr(utf8.decode(part)), utf8.getstate())
print(repr(utf8.decode(b"", final=True)))
utf8.reset()
print(repr(utf8.decode(b"\xc3")), utf8.getstate())
state = utf8.getstate()
utf8.reset()
utf8.setstate(state)
print(repr(utf8.decode(b"\xa9", final=True)))
print(list(codecs.iterdecode([b"\xe2", b"\x82\xac!"], "utf-8")))
print(list(codecs.iterencode(["A", "\u20ac"], "utf-8")))

for errors in ("ignore", "replace", "backslashreplace"):
    print(errors, repr(codecs.decode(b"a\xffb\xe2\x82", "utf-8", errors)))
    print(errors, codecs.encode("a\u00e9\u20acb", "ascii", errors))
print(codecs.encode("\u00e9\u20ac", "ascii", "xmlcharrefreplace"))

for data in (b"\x80", b"\xe2\x82", b"\xe2A", b"\xf4\x90\x80\x80"):
    try:
        codecs.decode(data, "utf-8")
    except UnicodeDecodeError as exc:
        print(exc.encoding, exc.object, exc.start, exc.end, exc.reason)
try:
    codecs.encode("a\u00e9\u20acb", "ascii")
except UnicodeEncodeError as exc:
    print(exc.encoding, repr(exc.object), exc.start, exc.end, exc.reason)
print(codecs.encode("\u00e9\u20ac\U0001f600\ud800", "ascii", "namereplace"))


def callback(exc):
    print("callback", type(exc).__name__, exc.start, exc.end)
    return "<bad>", exc.end


codecs.register_error("corpus_replace", callback)
print(codecs.lookup_error("corpus_replace") is callback)
print(codecs.encode("a\u20acb", "ascii", "corpus_replace"))
print(repr(codecs.decode(b"a\xffb", "ascii", "corpus_replace")))

escaped = codecs.decode(b"a\x80\xffb", "utf-8", "surrogateescape")
print(repr(escaped), codecs.encode(escaped, "utf-8", "surrogateescape"))
surrogates = "\ud800\udcff"
encoded = codecs.encode(surrogates, "utf-8", "surrogatepass")
print(encoded, repr(codecs.decode(encoded, "utf-8", "surrogatepass")))
print(codecs.decode(codecs.encode("abc", "utf-8-sig"), "utf-8-sig"))
try:
    codecs.lookup("corpus-does-not-exist")
except LookupError as exc:
    print(type(exc).__name__)

print(errno.EACCES, errno.EBADF, errno.EINVAL, errno.ENOENT, errno.EISDIR, errno.ESPIPE)
print(errno.EAGAIN == errno.EWOULDBLOCK, errno.ENOTSUP == errno.EOPNOTSUPP)
print(errno.errorcode[errno.ENOENT], errno.errorcode[errno.EAGAIN])
for kind in (stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK, stat.S_IFIFO,
             stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFBLK):
    mode = kind | 0o754
    print(stat.S_IFMT(mode) == kind, oct(stat.S_IMODE(mode)), stat.filemode(mode))
    print(stat.S_ISREG(mode), stat.S_ISDIR(mode), stat.S_ISLNK(mode),
          stat.S_ISFIFO(mode), stat.S_ISSOCK(mode), stat.S_ISCHR(mode), stat.S_ISBLK(mode))
print(stat.filemode(stat.S_IFREG | 0o6754))
print(stat.filemode(stat.S_IFDIR | 0o1700))
print(stat.filemode(0), stat.filemode(0o170000))


class Mode:
    def __index__(self):
        return stat.S_IFREG | 0o640


print(stat.filemode(Mode()), stat.S_ISREG(Mode()), oct(stat.S_IMODE(Mode())))
