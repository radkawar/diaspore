"""Exercise upstream Formatter and the private lazy parsing primitives."""

import _string
from string import Formatter, Template, capwords

formatter = Formatter()
for text in (
    '', 'literal', 'abc{{def}}ghi', '{{}}', '{{{0}}}', '{}:{}',
    'before {item.name[2]!r:>{width}} after', '{0[a:b!c]}',
    '{value:{width}.{precision}f}', '{x:{{}}}', '{x!z}',
):
    print(text, list(_string.formatter_parser(text)))

for text in (
    '', '0', '001', 'word', '0.name[2]', 'lookup[space key].value',
    'items[-1]', 'items[01]', 'items[a.b]', '.name', '[0]',
    '2\u0663', 'name[\u0662]', 'name[\u00b2]', 'first.attr with spaces',
):
    first, rest = _string.formatter_field_name_split(text)
    print(text, first, list(rest))

for text in ('{', '}', 'a}b', '{name', '{x{y}', '{x!', '{x!s',
             '{x!ss}', '{x:', '{x:{width}', '{x[missing}'):
    try:
        list(_string.formatter_parser(text))
    except ValueError as exc:
        print(text, str(exc))

for text in ('x.', 'x[]', 'x[missing', 'x[key]bad', 'x..y'):
    first, rest = _string.formatter_field_name_split(text)
    print('first', first)
    try:
        print(list(rest))
    except ValueError as exc:
        print(text, str(exc))

parts = _string.formatter_parser('ok{{then{')
print(next(parts))
try:
    next(parts)
except ValueError as exc:
    print('lazy parse', str(exc))
first, rest = _string.formatter_field_name_split('x.good[]')
print(first, next(rest))
try:
    next(rest)
except ValueError as exc:
    print('lazy field', str(exc))

for operation in (_string.formatter_parser, _string.formatter_field_name_split):
    for value in (None, 7, b'bytes'):
        try:
            operation(value)
        except TypeError:
            print('invalid parser type')
for text in ('9223372036854775808', 'x[9223372036854775808]'):
    try:
        first, rest = _string.formatter_field_name_split(text)
        list(rest)
    except ValueError as exc:
        print(str(exc))


class Item:
    name = 'Ada'
    values = [7, 12]


print(formatter.format('{0.name}: {0.values[1]:04d}', Item()))
print(formatter.format('{name!r:>8} {name!s} {name!a}', name='caf\u00e9'))
print(formatter.format('{value:{width}.{precision}f}', value=3.14159, width=8, precision=2))
print(formatter.format('{} {}', 'left', 'right'))
print(formatter.format('{0[a:b!c]}', {'a:b!c': 'bracket key'}))
print(formatter.format('{{{name}}}', name='Ada'))
print(formatter.vformat('{label}={0}', (12,), {'label': 'count'}))
for text in ('{} {0}', '{0} {}', '{x!z}', '{x:{width:{depth}}}'):
    try:
        formatter.format(text, 3, x=2, width=3, depth=4)
    except ValueError as exc:
        print(str(exc))


class UpperFormatter(Formatter):
    def convert_field(self, value, conversion):
        if conversion == 'u':
            return str(value).upper()
        return super().convert_field(value, conversion)


print(UpperFormatter().format('{name!u}', name='Ada'))
print(capwords('  hello\tWORLD\nagain '))
print(Template('$name owes $$${amount}').substitute(name='Ada', amount=12))
print(Template('$known $missing').safe_substitute(known='yes'))
