package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestArbitraryPrecisionRanges(t *testing.T) {
	cases := []struct {
		name   string
		source string
	}{
		{"bounds_length_and_membership", `huge = 1 << 1000
r = range(huge)
assert (r.start, r.stop, r.step) == (0, huge, 1)
assert bool(r) and not range(huge, huge)
assert r[0] == 0 and r[-1] == huge - 1
assert r[huge - 1] == huge - 1 and r[-huge] == 0
assert huge - 1 in r and huge not in r
assert r.index(huge - 1) == huge - 1
assert r.count(huge - 1) == 1 and r.count(huge) == 0
try:
    len(r)
except OverflowError:
    pass
else:
    raise AssertionError('unbounded range length did not overflow')
for index in (huge, -huge - 1):
    try:
        r[index]
    except IndexError:
        pass
    else:
        raise AssertionError('out-of-bounds wide index accepted')
assert len(range(huge, huge + 11, 3)) == 4
assert list(range(huge, huge + 11, 3)) == [huge, huge + 3, huge + 6, huge + 9]
assert list(range(huge, huge - 11, -3)) == [huge, huge - 3, huge - 6, huge - 9]
assert repr(range(huge, huge + 1)) == 'range(' + str(huge) + ', ' + str(huge + 1) + ')'
assert repr(range(huge, huge - 1, -3)) == 'range(' + str(huge) + ', ' + str(huge - 1) + ', -3)'

limit = (1 << 63) - 1
wide = range(-limit - 1, limit)
assert wide[-1] == limit - 1
assert wide[1 << 63] == 0
assert wide[-(1 << 63)] == -1
assert wide.index(0) == 1 << 63
assert wide.index(limit - 1) == (1 << 64) - 2
assert 0 in wide and limit not in wide
try:
    len(wide)
except OverflowError:
    pass
else:
    raise AssertionError('int64 endpoint distance overflowed silently')
assert list(range(limit, -limit - 1, -(1 << 63))) == [limit, -1]
assert len(range(0, limit)) == limit

assert 2.0 in range(4) and range(4).index(2.0) == 2
assert range(4).count(2 + 0j) == 1
assert 2.5 not in range(4)
class Even:
    def __eq__(self, other):
        return other % 2 == 0
assert range(5).count(Even()) == 3
assert range(1, 5).index(Even()) == 1
assert Even() in range(1, 3)
class Broken:
    def __eq__(self, other):
        raise RuntimeError('comparison failed')
try:
    Broken() in range(2)
except RuntimeError as error:
    assert str(error) == 'comparison failed'
else:
    raise AssertionError('membership comparison exception lost')
`},
		{"slices_equality_and_hash", `huge = 1 << 1000
for original in (range(12), range(-10, 10, 3), range(20, -5, -4), range(0)):
    values = list(original)
    for start in (None, -20, -3, 0, 3, 20):
        for stop in (None, -20, -3, 0, 3, 20):
            for step in (None, -7, -1, 1, 3, 10):
                section = slice(start, stop, step)
                assert list(original[section]) == values[section]

r = range(huge, -huge, -7)
last = huge - ((2 * huge - 1) // 7) * 7
assert r[-1] == last
assert list(r[:2]) == [huge, huge - 7]
assert list(r[-2:]) == [last + 7, last]
assert r[::-1][0] == last and r[::-1][-1] == huge
assert r[::] == r and r[::] is not r
assert not r[0:0:-huge]
assert r[0:0:-huge].step == 7 * huge
assert range(10)[::huge] == range(0, 10, huge)
assert range(10)[::-huge] == range(9, -1, -huge)
assert list(range(10)[::huge]) == [0]
assert list(range(10)[::-huge]) == [9]
large = range(0, huge, 2)
assert len(large[::huge]) == 1
assert (large[::-huge].start, large[::-huge].stop, large[::-huge].step) == (huge - 2, -2, -2 * huge)
try:
    r[::0]
except ValueError:
    pass
else:
    raise AssertionError('zero slice step accepted')

empty = range(huge, huge, huge)
assert empty == range(0)
assert hash(empty) == hash(range(0)) == hash((0, None, None))
one = range(huge, huge + 1, huge)
assert one == range(huge, huge + 2, huge + 1)
assert hash(one) == hash((1, huge, None))
a = range(huge, huge + 10, 3)
b = range(huge, huge + 12, 3)
assert a == b and hash(a) == hash(b)
assert {a: 'same'}[b] == 'same'
assert hash(a) == hash((4, huge, 3))
assert len({range(0), empty, a, b}) == 2
assert a != range(huge + 1, huge + 11, 3)
assert a != [huge, huge + 3, huge + 6, huge + 9]
assert a.__eq__([]) is NotImplemented
`},
		{"iterators_and_index_protocol", `huge = 1 << 1000
assert type(iter(range(3))).__name__ == 'range_iterator'
assert type(iter(range(huge))).__name__ == 'longrange_iterator'
assert type(iter(range(huge, huge))).__name__ == 'longrange_iterator'
limit = (1 << 63) - 1
assert type(iter(range(limit - 1, limit, 2))).__name__ == 'longrange_iterator'
assert type(reversed(range(-limit - 1, -limit))).__name__ == 'longrange_iterator'
assert next(reversed(range(-limit - 1, -limit))) == -limit - 1
assert next(reversed(range(huge))) == huge - 1
assert list(reversed(range(huge, huge + 7, 2))) == [huge + 6, huge + 4, huge + 2, huge]
iterator = iter(range(huge))
assert iterator.__length_hint__() == huge
assert next(iterator) == 0
iterator.__setstate__(huge - 3)
assert next(iterator) == huge - 2
assert iterator.__length_hint__() == 1
assert next(iterator) == huge - 1
assert next(iterator, 'done') == 'done'
assert iterator.__length_hint__() == 0

for original in (range(10, 20, 2), range(huge, huge + 10, 2)):
    iterator = iter(original)
    assert iter(iterator) is iterator
    assert iterator.__next__() == original.start
    iterator.__setstate__(1)
    assert iterator.__length_hint__() == 3
    reduced = iterator.__reduce__()
    assert reduced[2] is None
    assert list(reduced[0](*reduced[1])) == list(original[2:])
    iterator.__setstate__(-1)
    assert next(iterator) == original[2]
    iterator.__setstate__(20)
    assert next(iterator, 'done') == 'done'
try:
    iter(range(3)).__setstate__(huge)
except OverflowError:
    pass
else:
    raise AssertionError('small iterator state conversion did not overflow')

class Index:
    def __init__(self, value):
        self.value = value
    def __index__(self):
        return self.value
assert list(range(Index(huge), Index(huge + 6), Index(2))) == [huge, huge + 2, huge + 4]
assert range(huge)[Index(-1)] == huge - 1
assert list(range(10)[Index(1):Index(8):Index(3)]) == [1, 4, 7]
events = []
class RecordingIndex:
    def __init__(self, name, value):
        self.name = name
        self.value = value
    def __index__(self):
        events.append(self.name)
        return self.value
assert list(range(10)[RecordingIndex('start', 1):RecordingIndex('stop', 8):RecordingIndex('step', 3)]) == [1, 4, 7]
assert events == ['step', 'start', 'stop']
events.clear()
try:
    range(10)[RecordingIndex('start', 1)::RecordingIndex('step', 0)]
except ValueError:
    pass
else:
    raise AssertionError('zero indexed step accepted')
assert events == ['step']
for invalid in (1.5, '3', None):
    try:
        range(invalid)
    except TypeError:
        pass
    else:
        raise AssertionError('non-index constructor argument accepted')
`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			compiled, err := dspc.CompileSource("/src/main.py", tc.source, dspc.CompileOptions{})
			if err != nil || compiled.Code != 0 {
				t.Fatalf("compile: %v %s", err, compiled.Stderr)
			}
			bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
			if err != nil {
				t.Fatal(err)
			}
			var stderr bytes.Buffer
			vm := interp.New(bundle, interp.WithStderr(&stderr))
			defer vm.Close()
			if code := vm.Run(); code != 0 {
				t.Fatalf("Run() = %d: %s", code, stderr.String())
			}
		})
	}
}
