import math

buffer = bytearray(b'abcd')
view = memoryview(buffer)
print(type(buffer).__name__, type(view).__name__)
print(view.readonly, view.format, view.itemsize, view.ndim, view.nbytes)
print(view.shape, view.strides, view.c_contiguous)
view[1:3] = b'XY'
print(bytes(buffer), view.tobytes(), view.tolist())
subview = view[::2]
print(subview.tobytes(), subview.c_contiguous)
readonly = view.toreadonly()
print(readonly.readonly)
try:
    readonly[0] = 0
except TypeError:
    print('readonly')
try:
    buffer.append(0)
except BufferError:
    print('exported')
subview.release()
readonly.release()
view.release()
buffer.append(33)
print(bytes(buffer), buffer.hex())
print(memoryview(b'xy').cast('B').tolist())
print(int.from_bytes(b'\x01\x02'), int.from_bytes(b'\xff', signed=True))
print((258).to_bytes(2), (65).to_bytes())
print(math.copysign(2.0, -0.0), math.frexp(12.0), math.ldexp(0.75, 4))
print('%s:%04d:%.2f' % ('item', 7, 1.25))
print('%(name)s=%(value)+d' % {'name': 'count', 'value': 3})
print('{name}:{0:04d}:{1:.2f}'.format(7, 1.25, name='item'))
print('{name!r}={value}'.format_map({'name': 'count', 'value': 3}))


class Value:
    def __str__(self):
        return 'str-value'

    def __repr__(self):
        return 'repr-value'

    def __format__(self, spec):
        return 'format-' + spec


value = Value()
print('%s %r' % (value, value))
print('{0!s} {0!r} {0:custom}'.format(value))
