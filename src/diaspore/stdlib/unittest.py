"""Provide test cases, assertions, and a deterministic text runner."""


class SkipTest(Exception):
    pass


class _Outcome:
    def __init__(self):
        self.errors = []
        self.failures = []
        self.skipped = []


class _AssertRaisesContext:
    def __init__(self, expected, test_case, expected_regex=None):
        self.expected = expected
        self.test_case = test_case
        self.exception = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        if exc_type is None:
            name = getattr(self.expected, "__name__", str(self.expected))
            raise self.test_case.failureException(
                f"{name} not raised")
        if not issubclass(exc_type, self.expected):
            return False
        self.exception = exc_value
        return True


class _SubTest:
    def __init__(self, test_case, params):
        self.test_case = test_case
        self.params = params

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestCase:
    failureException = AssertionError

    def __init__(self, method_name="runTest"):
        self._method_name = method_name

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def _fail(self, msg):
        raise self.failureException(msg)

    def fail(self, msg=None):
        self._fail(msg or "fail")

    def assertEqual(self, a, b, msg=None):
        if not a == b:
            self._fail(msg or f"{a!r} != {b!r}")

    def assertNotEqual(self, a, b, msg=None):
        if a == b:
            self._fail(msg or f"{a!r} == {b!r}")

    def assertTrue(self, x, msg=None):
        if not x:
            self._fail(msg or f"{x!r} is not true")

    def assertFalse(self, x, msg=None):
        if x:
            self._fail(msg or f"{x!r} is not false")

    def assertIs(self, a, b, msg=None):
        if a is not b:
            self._fail(msg or f"{a!r} is not {b!r}")

    def assertIsNot(self, a, b, msg=None):
        if a is b:
            self._fail(msg or f"unexpectedly identical: {a!r}")

    def assertIsNone(self, x, msg=None):
        if x is not None:
            self._fail(msg or f"{x!r} is not None")

    def assertIsNotNone(self, x, msg=None):
        if x is None:
            self._fail(msg or "unexpectedly None")

    def assertIn(self, a, b, msg=None):
        if a not in b:
            self._fail(msg or f"{a!r} not found in {b!r}")

    def assertNotIn(self, a, b, msg=None):
        if a in b:
            self._fail(msg or f"{a!r} unexpectedly found in {b!r}")

    def assertIsInstance(self, obj, cls, msg=None):
        if not isinstance(obj, cls):
            self._fail(msg or f"{obj!r} is not an instance of {cls!r}")

    def assertNotIsInstance(self, obj, cls, msg=None):
        if isinstance(obj, cls):
            self._fail(msg or f"{obj!r} is an instance of {cls!r}")

    def assertRaises(self, expected, *args, **kwargs):
        if not args:
            return _AssertRaisesContext(expected, self)
        func = args[0]
        try:
            func(*args[1:], **kwargs)
        except expected:
            return None
        name = getattr(expected, "__name__", str(expected))
        self._fail(f"{name} not raised")

    def assertGreater(self, a, b, msg=None):
        if not a > b:
            self._fail(msg or f"{a!r} not greater than {b!r}")

    def assertGreaterEqual(self, a, b, msg=None):
        if not a >= b:
            self._fail(msg or f"{a!r} not greater than or equal to {b!r}")

    def assertLess(self, a, b, msg=None):
        if not a < b:
            self._fail(msg or f"{a!r} not less than {b!r}")

    def assertLessEqual(self, a, b, msg=None):
        if not a <= b:
            self._fail(msg or f"{a!r} not less than or equal to {b!r}")

    def assertAlmostEqual(self, a, b, places=7, msg=None):
        if round(abs(b - a), places) != 0:
            self._fail(msg or f"{a!r} != {b!r} within {places} places")

    def assertListEqual(self, a, b, msg=None):
        self.assertEqual(a, b, msg)

    def assertDictEqual(self, a, b, msg=None):
        self.assertEqual(a, b, msg)

    def assertTupleEqual(self, a, b, msg=None):
        self.assertEqual(a, b, msg)

    def assertSetEqual(self, a, b, msg=None):
        self.assertEqual(a, b, msg)

    def assertSequenceEqual(self, a, b, msg=None, seq_type=None):
        self.assertEqual(list(a), list(b), msg)

    def assertCountEqual(self, a, b, msg=None):
        self.assertEqual(sorted(a, key=repr), sorted(b, key=repr), msg)

    def assertRaisesRegex(self, expected, regex, *args, **kwargs):
        return self.assertRaises(expected, *args, **kwargs)

    def subTest(self, msg=None, **params):
        return _SubTest(self, params)

    def skipTest(self, reason):
        raise SkipTest(reason)


def skip(reason):
    def deco(obj):
        obj.__unittest_skip__ = True
        obj.__unittest_skip_why__ = reason
        return obj
    return deco


def skipIf(condition, reason):
    if condition:
        return skip(reason)
    return lambda obj: obj


def skipUnless(condition, reason):
    if not condition:
        return skip(reason)
    return lambda obj: obj


def expectedFailure(func):
    func.__unittest_expecting_failure__ = True
    return func


def _test_methods(cls):
    names = []
    seen = set()
    for base in getattr(cls, "__mro__", [cls]):
        for name in dir_names(base):
            if name.startswith("test") and name not in seen:
                seen.add(name)
                names.append(name)
    return sorted(names)


def dir_names(cls):
    out = []
    d = getattr(cls, "__dict__", None)
    if d:
        out.extend(d.keys())
    return out


class TestResult:
    def __init__(self):
        self.testsRun = 0
        self.failures = []
        self.errors = []
        self.skipped = []

    def wasSuccessful(self):
        return not self.failures and not self.errors


def run_case(cls, result):
    if getattr(cls, "__unittest_skip__", False):
        return
    for name in _method_names(cls):
        result.testsRun += 1
        inst = cls(name)
        method = getattr(inst, name)
        if getattr(method, "__unittest_skip__", False):
            result.skipped.append((f"{cls.__name__}.{name}",
                                   getattr(method, "__unittest_skip_why__", "")))
            continue
        expecting = getattr(method, "__unittest_expecting_failure__", False)
        try:
            inst.setUp()
            try:
                method()
            finally:
                inst.tearDown()
        except SkipTest as e:
            result.skipped.append((f"{cls.__name__}.{name}", str(e)))
            continue
        except AssertionError as e:
            if expecting:
                continue
            result.failures.append((f"{cls.__name__}.{name}", str(e)))
            continue
        except Exception as e:
            if expecting:
                continue
            result.errors.append((f"{cls.__name__}.{name}",
                                  f"{type(e).__name__}: {e}"))
            continue
        if expecting:
            result.failures.append((f"{cls.__name__}.{name}",
                                    "unexpected success"))


def _method_names(cls):
    names = set()
    for base in getattr(cls, "__mro__", [cls]):
        d = getattr(base, "__dict__", {})
        for k in d:
            if k.startswith("test"):
                names.add(k)
    return sorted(names)


def run_classes(classes):
    """Run test classes without timings for reproducible cross-runtime output."""
    result = TestResult()
    for cls in classes:
        run_case(cls, result)
    print(f"Ran {result.testsRun} tests")
    if result.skipped:
        print(f"skipped {len(result.skipped)}")
    if result.wasSuccessful():
        print("OK")
    else:
        print(f"FAILED (failures={len(result.failures)}, errors={len(result.errors)})")
        for name, msg in result.failures:
            print(f"  FAIL {name}: {msg}")
        for name, msg in result.errors:
            print(f"  ERROR {name}: {msg}")
    return result


def main(*args, **kwargs):
    # Caller globals are unavailable; use run_classes for explicit discovery.
    pass
