import io
import _pyio


def exercise(module):
    print(module.__name__)
    text = module.StringIO("alpha\r\nbeta\rgamma\n", newline=None)
    print(repr(text.getvalue()), repr(text.newlines))
    print(repr(text.readline()), text.tell())
    print(text.seek(0), list(text))
    print(text.flush(), text.closed, text.readable(), text.writable(), text.seekable())
    print(text.seek(0), text.write("XY"), repr(text.getvalue()))
    print(text.seek(24), text.write("!"), repr(text.getvalue()))
    print(text.truncate(4), repr(text.getvalue()), text.tell())
    try:
        text.seek(1, io.SEEK_CUR)
    except OSError as exc:
        print(type(exc).__name__)
    print(text.close(), text.closed, text.close())
    for operation in (text.flush, text.getvalue, text.read, text.tell):
        try:
            operation()
        except ValueError as exc:
            print(type(exc).__name__)

    with module.StringIO(newline="\r\n") as translated:
        print(translated.write("a\nb\n"), repr(translated.getvalue()))
        translated.seek(0)
        print(repr(translated.readline()), repr(translated.readline()))
    print(translated.closed)

    surrogate = module.StringIO("x\ud800\udcffy")
    print(repr(surrogate.getvalue()), repr(surrogate.read()))
    surrogate.close()

    binary = module.BytesIO(b"abc\ndef")
    print(binary.read(2), binary.tell(), binary.readline())
    print(binary.seek(-2, io.SEEK_END), binary.read())
    binary.seek(0)
    target = bytearray(b"----")
    print(binary.readinto(target), bytes(target))
    print(binary.seek(10), binary.write(b"Z"), binary.getvalue())
    print(binary.truncate(5), binary.getvalue(), binary.tell())
    print(binary.flush(), binary.readable(), binary.writable(), binary.seekable())
    try:
        binary.write("text")
    except TypeError as exc:
        print(type(exc).__name__)
    binary.close()
    for operation in (binary.flush, binary.getvalue, binary.read):
        try:
            operation()
        except ValueError as exc:
            print(type(exc).__name__)

    raw = module.BytesIO()
    writer = module.BufferedWriter(raw, buffer_size=4)
    print(writer.write(b"ab"), raw.getvalue())
    writer.flush()
    print(raw.getvalue(), writer.detach() is raw)
    raw.seek(0)
    reader = module.BufferedReader(raw, buffer_size=2)
    print(reader.peek(1), reader.read(1), reader.read())
    reader.close()
    print(raw.closed)

    raw = module.BytesIO(b"a\r\nb\rc\n")
    wrapper = module.TextIOWrapper(raw, encoding="utf-8", newline=None)
    print(repr(wrapper.read()), repr(wrapper.newlines))
    print(wrapper.seek(0), repr(wrapper.readline()))
    wrapper.close()
    print(raw.closed)


exercise(io)
exercise(_pyio)
print(isinstance(io.StringIO(), io.TextIOBase))
print(isinstance(_pyio.StringIO(), io.TextIOBase))
print(isinstance(io.BytesIO(), io.BufferedIOBase))
print(isinstance(_pyio.BytesIO(), io.BufferedIOBase))
