package interp_test

import (
	"bytes"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestNativeAccurateMathAggregates(t *testing.T) {
	cases := []struct{ name, source string }{
		{"fsum_partials_and_protocols", `import math
assert math.fsum([]) == 0.0
assert math.copysign(1.0, math.fsum([-0.0])) == 1.0
assert math.fsum([0.1] * 10) == 1.0
assert math.fsum([1e16, 1, -1e16]) == 1.0
assert math.fsum([1e16, 1, 1e-16]) == 1.0000000000000002e16
assert math.fsum([5e-324] * 3) == 1.5e-323
assert math.fsum(memoryview(b'ab')) == 195.0
partials = [2.0**exponent for exponent in range(-1000, 1000, 53)]
assert math.fsum(partials + [-value for value in reversed(partials)]) == 0.0
assert math.fsum([math.inf, 3]) == math.inf
assert math.isnan(math.fsum([math.nan, 3]))
for values, kind, message in (
    ([1e308, 1e308], OverflowError, 'intermediate overflow in fsum'),
    ([math.inf, -math.inf], ValueError, '-inf + inf in fsum'),
    ([math.inf, math.nan, -math.inf], ValueError, '-inf + inf in fsum'),
):
    try:
        math.fsum(values)
    except kind as exc:
        assert str(exc) == message
    else:
        raise AssertionError('invalid floating sum accepted')
class FloatValue:
    def __float__(self):
        return 3.0
class IndexValue:
    def __index__(self):
        return 4
assert math.fsum([FloatValue(), IndexValue()]) == 7.0
class FloatSubclass(float):
    def __float__(self):
        raise AssertionError('native float storage must bypass override')
class IntSubclass(int):
    def __float__(self):
        return 7.0
assert math.fsum([FloatSubclass(1.5)]) == 1.5
assert math.fsum([IntSubclass(2)]) == 7.0
class Growing:
    def __float__(self):
        values.append(1.0)
        return 2.0
values = [Growing()]
assert math.fsum(values) == 3.0
def broken():
    yield math.inf
    raise LookupError('iterator failed')
try:
    math.fsum(broken())
except LookupError as exc:
    assert str(exc) == 'iterator failed'
else:
    raise AssertionError('iteration error suppressed by infinity')
for value in ('1', object(), 10**1000):
    try:
        math.fsum([value])
    except (TypeError, OverflowError):
        pass
    else:
        raise AssertionError('invalid real-number conversion accepted')
`},
		{"distance_scaling_and_iteration", `import math
assert math.dist([], []) == 0.0
assert math.dist([0, 0], [3, 4]) == 5.0
assert math.dist([1] * 64, [0] * 64) == 8.0
assert math.dist([1e308, 1e308], [0, 0]) == 1.4142135623730951e308
assert math.dist([5e-324, 5e-324], [0, 0]) == 5e-324
assert math.dist([1e-323, 1e-323], [0, 0]) == 1.5e-323
assert math.dist([1e-308] * 3, [0] * 3) == 1.732050807568877e-308
assert math.dist([0.1, 0.2, 0.3], [0, 0, 0]) == 0.3741657386773941
assert math.dist([2.0**500, 2.0**500, 2.0**449], [0, 0, 0]) == 4.629273392631434e150
assert math.dist([1e308], [-1e308]) == math.inf
assert math.dist([math.nan, math.inf], [0, 0]) == math.inf
assert math.isnan(math.dist([math.inf], [math.inf]))
assert math.copysign(1.0, math.dist([-0.0], [0.0])) == 1.0
class Tuple(tuple):
    def __iter__(self):
        raise AssertionError('tuple storage must be read directly')
assert math.dist(Tuple((0, 0)), (3, 4)) == 5.0
try:
    math.fsum(Tuple((1, 2)))
except AssertionError:
    pass
else:
    raise AssertionError('fsum must honor tuple iteration override')
class MustNotConvert:
    def __float__(self):
        raise AssertionError('dimension validation must precede conversion')
try:
    math.dist([MustNotConvert()], [])
except ValueError as exc:
    assert str(exc) == 'both points must have the same number of dimensions'
else:
    raise AssertionError('mismatched dimensions accepted')
right = [0, 0]
class Mutating:
    def __float__(self):
        right[0] = 100
        return 4.0
assert math.dist([Mutating(), 3], right) == 5.0
assert right == [100, 0]
seen = []
class Coordinate:
    def __init__(self, name, value):
        self.name = name
        self.value = value
    def __float__(self):
        seen.append(self.name)
        return self.value
def point(name, values):
    seen.append(name + ' begin')
    for value in values:
        yield value
    seen.append(name + ' end')
left = point('left', [Coordinate('p0', 0.0), Coordinate('p1', 0.0)])
right = point('right', [Coordinate('q0', 3.0), Coordinate('q1', 4.0)])
assert math.dist(left, right) == 5.0
assert seen == ['left begin', 'left end', 'right begin', 'right end', 'p0', 'q0', 'p1', 'q1']
`},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			compiled, err := dspc.CompileSource("/src/main.py", test.source, dspc.CompileOptions{NoStdlib: true})
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
