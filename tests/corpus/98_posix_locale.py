# diaspore-caps: fs
"""Exercise upstream os and locale through their private platform layers."""

import locale
import os
import posix
import stat


class TextPath:
    def __fspath__(self):
        return 'alpha/beta'


class BytePath:
    def __fspath__(self):
        return b'alpha/beta'


class BadPath:
    def __fspath__(self):
        return 12


print(os.name, os.sep, os.pathsep, os.linesep == '\n')
print(os.fspath(TextPath()), os.fspath(BytePath()))
print(os.fsencode(TextPath()), os.fsdecode(BytePath()))
print(os.fsdecode(os.fsencode('caf\u00e9')))
for value in (BadPath(), 42, None):
    try:
        os.fspath(value)
    except TypeError:
        print('bad path')

record = os.stat_result(range(10))
print(isinstance(record, tuple), len(record), tuple(record), record[2:5])
print(record.st_mode, record.st_ino, record.st_size)
print(record.st_atime, record.st_mtime, record.st_ctime)
print(record.st_atime_ns, record.st_mtime_ns, record.st_ctime_ns)
print(repr(record))
precise = os.stat_result(range(10), {'st_mtime': 8.25, 'st_mtime_ns': 8250000000})
print(precise[8], precise.st_mtime, precise.st_mtime_ns)
try:
    precise.st_size = 1
except AttributeError:
    print('readonly stat')
for values in (range(9), range(20)):
    try:
        os.stat_result(values)
    except TypeError:
        print('bad stat length')

metadata = os.stat(__file__)
parent = os.path.dirname(__file__)
name = os.path.basename(__file__)
print(stat.S_ISREG(metadata.st_mode), metadata.st_size > 0)
print(len(metadata), metadata[6] == metadata.st_size)
print(metadata.st_mtime_ns // 1000000000 == metadata[8])
print(os.lstat(__file__).st_ino == metadata.st_ino)
print(os.path.isfile(__file__), os.path.isdir(parent))
try:
    os.mkdir(parent)
except FileExistsError:
    print('directory already exists', stat.S_ISDIR(os.stat(parent).st_mode))
print(name in os.listdir(parent), os.path.isabs(os.getcwd()))
print(os.getcwdb() == os.fsencode(os.getcwd()))
with os.scandir(parent) as entries:
    for entry in entries:
        if entry.name == name:
            print(entry.is_file(), entry.is_dir(), entry.is_symlink())
            print(os.fspath(entry) == entry.path, entry.inode() == metadata.st_ino)
            print(entry.stat().st_size == metadata.st_size)
entries = os.scandir(parent)
entries.close()
print(list(entries))
print(os.strerror(2), os.strerror(13), os.strerror(22), os.strerror(-123))

key = 'DIASPORE_CORPUS_ENVIRONMENT_CHECK'
saved = os.environ.get(key)
try:
    os.environ[key] = 'sandbox'
    print(os.getenv(key), os.environb[key.encode('ascii')])
    os.environb[key.encode('ascii')] = b'bytes'
    print(os.getenv(key))
    posix.putenv(key, 'direct')
    print(os.getenv(key))
    del os.environ[key]
    print(os.getenv(key, 'absent'))
finally:
    if saved is not None:
        os.environ[key] = saved
    else:
        os.environ.pop(key, None)
for name in ('', 'a=b'):
    try:
        posix.putenv(name, 'value')
    except ValueError:
        print('bad environment name')

previous = locale.setlocale(locale.LC_ALL)
try:
    print(locale.setlocale(locale.LC_ALL, 'C'))
    print(locale.setlocale(locale.LC_NUMERIC, 'POSIX'))
    print(locale.getlocale(locale.LC_NUMERIC))
    conventions = locale.localeconv()
    print(conventions['decimal_point'], conventions['thousands_sep'], conventions['grouping'])
    print(conventions['currency_symbol'], conventions['frac_digits'] == locale.CHAR_MAX)
    conventions['grouping'].append(3)
    print(locale.localeconv()['grouping'])
    print(locale.format_string('%.2f', 1234.5, grouping=True))
    print(locale.atoi('123'), locale.atof('123.5'))
    print(locale.strcoll('a', 'b') < 0, locale.strcoll('a', 'a') == 0)
    print(locale.strxfrm('caf\u00e9'))
    print(locale.getencoding())
    try:
        locale.currency(10)
    except ValueError:
        print('no C currency')
    for category, setting in ((locale.LC_ALL, 'diaspore_no_such_locale'), (99, 'C')):
        try:
            locale.setlocale(category, setting)
        except locale.Error:
            print('unsupported locale')
    print(locale.setlocale(locale.LC_ALL))
    for operation in (locale.strxfrm, lambda text: locale.strcoll(text, 'a')):
        try:
            operation('a\x00b')
        except ValueError:
            print('embedded null')
finally:
    locale.setlocale(locale.LC_ALL, previous)
