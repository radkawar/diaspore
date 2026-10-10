"""Percent operands and brace replacement-field semantics."""


def report_error(operation, *args):
    try:
        operation(*args)
    except Exception as exc:
        print(type(exc).__name__, str(exc))


def percent(template, value):
    return template % value


def braces(template, *args, **kwargs):
    return template.format(*args, **kwargs)


def mapped(template, values):
    return template.format_map(values)


print('%#012x|%#012X|%#012o' % (-255, 255, 9))
print('%+08.5d|%-08d|% .0d|%u|%i' % (42, -42, 0, -3, 3.9))
print('%*.*s|%*.*f|%.*g' % (-7, 2, 'caf\u00e9', 9, 3, 1.25, 0, 1234.0))
print('%#g|%#.0f|%#.3g|%#.3G' % (1.0, 3.0, 0.00012, 12345.0))
print('%e|%E|%F|%+08f' % (0.0001, 123.0, float('inf'), -0.0))
print('%s|%r|%a|%5.2s|%3c|%c' % ('caf\u00e9', 'caf\u00e9', 'caf\u00e9', '\u03b1\u03b2\u03b3', '\u03bb', 0x1f642))
print('%d|%#x|%#.35o' % (2 ** 100, -(2 ** 100), 2 ** 80))
print('%ld|%hi|%Lu|%%' % (2, 3, 4))
print('%s' % ([1, 2],))
print('%s' % ((1, 2),))
print('%(outer(inner))s:%()s:%(tuple)r' % {'outer(inner)': 'nested', '': 'empty', 'tuple': (1, 2)})
print('literal' % {}, '%%' % ())
print(b'%#08x|%b|%.2s|%c|%a|%r' % (31, bytearray(b'abc'), memoryview(b'xyz'), b'!', '\u00e9', '\u03bb'))
print(b'%(word)b:%(number)04d' % {b'word': b'yes', b'number': 7})
print(bytearray(b'%b:%03d') % (b'value', 5))


class Numeric:
    def __index__(self):
        return 65

    def __float__(self):
        return 1.25


class BytesValue:
    def __bytes__(self):
        return b'custom'


class Mapping:
    def __getitem__(self, key):
        return 'lookup:' + key


print('%d|%x|%.2f|%c' % (Numeric(), Numeric(), Numeric(), Numeric()))
print(b'%b|%s|%c' % (BytesValue(), BytesValue(), Numeric()))
print('%(name)s' % Mapping())
for template, value in (
    ('%', ()), ('%(', {}), ('%(x)s', (1,)), ('%(x)s', {}),
    ('%q', 1), ('%5%', 1), ('%s %s', 1), ('plain', 1),
    ('%s', (1, 2)), ('%*s', ('bad', 'x')), ('%*s', (Numeric(), 'x')),
    ('%.*s', ('bad', 'x')), ('%(x)*s', {'x': 3}), ('%(x)s %s', {'x': 'x'}),
    ('%d', '1'), ('%x', 1.5), ('%c', 'long'), ('%c', -1),
    ('%d', float('nan')), ('%d', float('inf')), ('%f', 10 ** 400),
    (b'%b', 'text'), (b'%c', 256), (b'%c', b'long'), (b'plain', b'x'),
):
    report_error(percent, template, value)


class Field:
    def __init__(self):
        self.values = [{'key': 'resolved'}]

    def __format__(self, spec):
        return 'field<' + spec + '>'


class Missing(dict):
    def __missing__(self, key):
        return 'missing:' + key


field = Field()
print('{0.values[0][key]}:{0.values[0][key]!r:>12}'.format(field))
print('{[a]}:{.values[0][key]}'.format({'a': 'first'}, field))
print('{left} {} {right} {}'.format('one', 'two', left='L', right='R'))
print('{0:{1}.{2}f}|{0!r:>{1}}'.format(1.25, 8, 3))
print('{:{}}|{:.{}f}'.format('x', 4, 1.25, 2))
print('{0:{width}}|{0:{{}}}'.format(field, width='custom'))
print('{{{0}}}|{{}}|}}'.format('escaped'))
print('{0[a:b!c]}|{0[-1]}|{0[01]}'.format({'a:b!c': 'punctuation', '-1': 'string', 1: 'integer'}))
print('{\u0661}|{0[\u0662]}'.format({2: 'index'}, 'position'))
print('{known}:{unknown}'.format_map(Missing(known='present')))
print('literal'.format_map(None))
print('{0!a:>12}'.format('\u00e9'))


class BadFormat:
    def __format__(self, spec):
        return 123


for template, args in (
    ('{', ()), ('}', ()), ('{x', ()), ('{0{1}', (1, 2)),
    ('{0!', (1,)), ('{0!r', (1,)), ('{0!rr}', (1,)),
    ('{0!z}', (1,)), ('{0:', (1,)), ('{0:{1}', (1, 2)),
    ('{} {0}', (1,)), ('{0} {}', (1,)), ('{1}', (1,)),
    ('{missing}', ()), ('{0[]}', ({},)), ('{0.}', (field,)),
    ('{0[a]bad}', ({'a': 1},)), ('{0:{1:{2}}}', (1, 2, 3)),
    ('{0:x}', ([],)), ('{0}', (BadFormat(),)),
):
    report_error(braces, template, *args)
report_error(mapped, '{}', {})
report_error(mapped, '{0}', {0: 'zero'})
report_error(mapped, '{name}', None)
