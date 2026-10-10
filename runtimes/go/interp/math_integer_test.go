package interp_test

import (
	"bytes"
	"strings"
	"testing"

	"github.com/radkawar/diaspore/runtimes/go/dspc"
	"github.com/radkawar/diaspore/runtimes/go/interp"
)

func TestMathIntegerFunctions(t *testing.T) {
	cases := []struct{ name, source string }{
		{"factorial", `import math
assert math.factorial(0) == 1
assert math.factorial(True) == 1
assert math.factorial(False) == 1
assert type(math.factorial(True)) is int
assert math.factorial(20) == 2432902008176640000
assert math.factorial(21) == 51090942171709440000
assert math.factorial(30) == 265252859812191058636308480000000
expected = 1
for index in range(1, 151):
    expected *= index
assert math.factorial(150) == expected
assert math.perm(30) == math.factorial(30)
assert math.perm(30, None) == math.factorial(30)
`},
		{"gcd_lcm", `import math
assert math.gcd() == 0 and math.lcm() == 1
assert math.gcd(-42) == 42 and math.lcm(-42) == 42
assert math.gcd(0, 0) == 0 and math.lcm(0, 0) == 0
assert math.gcd(-84, 126, 210) == 42
assert math.lcm(-4, 6, 10) == 60
assert math.gcd(True, False) == 1 and type(math.gcd(True)) is int
assert math.lcm(True, True) == 1 and type(math.lcm(True)) is int
assert math.gcd(-(1 << 63), 0) == 1 << 63
assert math.gcd(-(1 << 63), -(1 << 62)) == 1 << 62
assert math.lcm(-(1 << 63), 1) == 1 << 63
large = 1 << 200
assert math.gcd(large * 15, large * 21) == large * 3
assert math.lcm(large * 15, large * 21) == large * 105
left = (1 << 63) - 1
right = (1 << 63) - 3
assert math.lcm(left, right) == left * right
assert left == (1 << 63) - 1 and right == (1 << 63) - 3
assert math.gcd(large, -large) == large
assert large == 1 << 200
`},
		{"combinations_and_permutations", `import math
assert math.comb(100, 50) == 100891344545564193334812497256
assert math.perm(100, 3) == 970200
for n in range(16):
    for k in range(n + 2):
        if k > n:
            assert math.comb(n, k) == 0 and math.perm(n, k) == 0
        else:
            numerator = math.factorial(n) // math.factorial(n - k)
            assert math.perm(n, k) == numerator
            assert math.comb(n, k) == numerator // math.factorial(k)
large = 10 ** 100
assert math.comb(large, 0) == 1
assert math.comb(large, 1) == large
assert math.comb(large, large) == 1
assert math.comb(large, large - 1) == large
assert math.comb(large, 2) == large * (large - 1) // 2
assert math.comb(large, large - 2) == large * (large - 1) // 2
assert math.perm(large, 3) == large * (large - 1) * (large - 2)
assert math.perm(large, 0) == 1 and math.perm(large, 1) == large
assert math.comb(4, large) == 0 and math.perm(4, large) == 0
assert large == 10 ** 100
`},
		{"index_protocol_and_errors", `import math
events = []
class Index:
    def __init__(self, value, label):
        self.value, self.label = value, label
    def __index__(self):
        events.append(self.label)
        return self.value
assert math.factorial(Index(6, 'factorial')) == 720
assert math.gcd(Index(24, 'gcd-left'), Index(36, 'gcd-right')) == 12
assert math.lcm(Index(4, 'lcm-left'), Index(6, 'lcm-right')) == 12
assert math.comb(Index(6, 'n'), Index(2, 'k')) == 15
assert math.perm(Index(6, 'n'), Index(2, 'k')) == 30
assert events == ['factorial', 'gcd-left', 'gcd-right', 'lcm-left', 'lcm-right', 'n', 'k', 'n', 'k']
class Integer(int):
    def __index__(self):
        raise AssertionError('integer subclass index override called')
assert math.factorial(Integer(4)) == 24
assert math.gcd(Integer(6), 4) == 2
for function, args in ((math.factorial, (3.0,)), (math.gcd, (1, 2.0)), (math.lcm, (0, 2.0)), (math.comb, (5, 2.0)), (math.perm, (5, 2.0))):
    try:
        function(*args)
    except TypeError:
        pass
    else:
        raise AssertionError('non-index argument accepted')
for function, args in ((math.gcd, (1, Index('bad', 'bad'))), (math.lcm, (0, Index('bad', 'bad')))):
    try:
        function(*args)
    except TypeError as error:
        assert str(error) == '__index__ returned non-int (type str)'
    else:
        raise AssertionError('short circuit skipped index validation')
for function in (math.comb, math.perm):
    events.clear()
    try:
        function(Index(-1, 'n'), Index('bad', 'k'))
    except TypeError:
        pass
    else:
        raise AssertionError('negative n checked before k index')
    assert events == ['n', 'k']
    for args, message in (((-1, 0), 'n must be a non-negative integer'), ((1, -1), 'k must be a non-negative integer')):
        try:
            function(*args)
        except ValueError as error:
            assert str(error) == message
        else:
            raise AssertionError('negative argument accepted')
for value in (-1, -(1 << 100)):
    try:
        math.factorial(value)
    except ValueError as error:
        assert str(error) == 'factorial() not defined for negative values'
    else:
        raise AssertionError('negative factorial accepted')
for function, args, message in ((math.factorial, (1 << 100,), 'factorial() argument should not exceed 9223372036854775807'), (math.perm, (1 << 100, 1 << 64), 'k must not exceed 9223372036854775807'), (math.comb, (1 << 100, 1 << 64), 'min(n - k, k) must not exceed 9223372036854775807')):
    try:
        function(*args)
    except OverflowError as error:
        assert str(error) == message
    else:
        raise AssertionError('CPython integer-count bound ignored')
for function, args in ((math.factorial, (4,)), (math.gcd, (4, 6)), (math.lcm, (4, 6)), (math.comb, (4, 2)), (math.perm, (4, 2))):
    try:
        function(*args, n=4)
    except TypeError:
        pass
    else:
        raise AssertionError('positional-only math function accepted keyword')
`},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			compiled, err := dspc.CompileSource("/src/main.py", test.source, dspc.CompileOptions{})
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

func TestMathIntegerFuel(t *testing.T) {
	for _, expression := range []string{"factorial(1000000)", "factorial(9223372036854775807)", "comb(2000000, 1000000)", "perm(2000000, 1000000)"} {
		t.Run(expression, func(t *testing.T) {
			compiled, err := dspc.CompileSource("/src/main.py", "import math\nmath."+expression+"\n", dspc.CompileOptions{})
			if err != nil || compiled.Code != 0 {
				t.Fatalf("compile: %v %s", err, compiled.Stderr)
			}
			bundle, err := interp.LoadBundle([]byte(compiled.Bundle))
			if err != nil {
				t.Fatal(err)
			}
			var stderr bytes.Buffer
			vm := interp.New(bundle, interp.WithStderr(&stderr), interp.WithMaxSteps(1000))
			defer vm.Close()
			if code := vm.Run(); code != 3 || !strings.Contains(stderr.String(), "step budget exceeded") {
				t.Fatalf("unmetered native work: code=%d stderr=%s", code, stderr.String())
			}
		})
	}
}
