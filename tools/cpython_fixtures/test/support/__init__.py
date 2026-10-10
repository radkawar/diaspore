"""Minimal regression support, never a claim of CPython host facilities.

Missing helpers remain import/attribute errors. Environment-only facilities
skip with a reason; language assertions still execute and report failures.
This module is used only by the Diaspore detector, never by its CPython baseline.
"""

import unittest

verbose = 0
is_jython = False
is_emscripten = False
is_wasi = False
has_subprocess_support = False
has_fork_support = False
has_socket_support = False
has_dynamic_linking = False
MISSING_C_DOCSTRINGS = True


def check_syntax_error(testcase, statement, errtext="", *, lineno=None, offset=None):
    """Assert that compilation raises SyntaxError with the requested details."""
    import re

    try:
        compile(statement, "<test string>", "exec")
    except SyntaxError as error:
        if errtext and re.search(errtext, str(error)) is None:
            testcase.fail(f"syntax error message does not match {errtext!r}: {error}")
        if lineno is not None:
            testcase.assertEqual(error.lineno, lineno)
        if offset is not None:
            testcase.assertEqual(error.offset, offset)
    else:
        testcase.fail("SyntaxError not raised")


def cpython_only(test):
    """Skip a test requiring CPython implementation internals."""
    return unittest.skip("requires CPython implementation internals, not a Diaspore VM")(test)


def check_impl_detail(**guards):
    """Return whether implementation guards select Diaspore."""
    if not guards:
        return False
    return guards.get("diaspore", False)


def requires_resource(resource):
    """Mark a test as requiring an unavailable host resource."""
    return unittest.skip(f"detector does not grant host resource: {resource}")


def is_resource_enabled(resource):
    """Return False because host test resources are not granted."""
    return False


def requires(resource, msg=None):
    """Skip a test requiring an unavailable host resource."""
    raise unittest.SkipTest(msg or f"detector does not grant host resource: {resource}")


def gc_collect():
    """Skip tests that require control of host garbage collection."""
    raise unittest.SkipTest("detector cannot force or observe VM garbage collection")


def no_tracing(func):
    """Skip a test requiring control of VM tracing."""
    return unittest.skip("detector cannot control VM tracing state")(func)


def run_with_locale(*args):
    """Skip tests that require changing the process locale."""
    raise unittest.SkipTest("detector cannot set process locale")


def run_with_locales(*args):
    """Mark tests requiring changes to the process locale as skipped."""
    return unittest.skip("detector cannot set process locale")


def captured_stderr():
    """Skip tests that require redirecting the process error stream."""
    raise unittest.SkipTest("detector cannot redirect VM process streams")


def captured_stdout():
    """Skip tests that require redirecting the process output stream."""
    raise unittest.SkipTest("detector cannot redirect VM process streams")


def captured_stdin():
    """Skip tests that require redirecting the process input stream."""
    raise unittest.SkipTest("detector cannot redirect VM process streams")


class SuppressCrashReport:
    """Expose unsupported crash-report control as an explicit test skip."""

    def __enter__(self):
        """Skip tests that require configuring host crash reporting."""
        raise unittest.SkipTest("detector cannot configure host crash reporting")

    def __exit__(self, *args):
        """Leave any exception unsuppressed."""
        return False


def check_sizeof(*args):
    """Skip tests that require CPython object memory layout."""
    raise unittest.SkipTest("requires CPython object memory layout")


def calcobjsize(*args):
    """Skip tests that require CPython object memory layout."""
    raise unittest.SkipTest("requires CPython object memory layout")


def calcvobjsize(*args):
    """Skip tests that require CPython object memory layout."""
    raise unittest.SkipTest("requires CPython object memory layout")


def check_free_after_iterating(*args):
    """Skip tests that require CPython reference counting."""
    raise unittest.SkipTest("requires CPython reference counting")


def iter_builtin_types():
    """Reject unavailable builtin-type inventory queries."""
    raise NotImplementedError("test.support.iter_builtin_types is unavailable in the detector")


def iter_slot_wrappers():
    """Reject unavailable slot-wrapper inventory queries."""
    raise NotImplementedError("test.support.iter_slot_wrappers is unavailable in the detector")


def load_package_tests(*args):
    """Reject filesystem discovery in favor of explicit module selection."""
    raise NotImplementedError(
        "test.support filesystem package discovery is unavailable; select nested modules explicitly"
    )
