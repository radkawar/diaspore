"""Subprocess-dependent CPython tests are explicit environment skips."""

import unittest


def assert_python_ok(*args, **kwargs):
    """Skip tests requiring a child Python interpreter."""
    raise unittest.SkipTest("detector cannot start a child Python interpreter")


def assert_python_failure(*args, **kwargs):
    """Skip tests requiring a child Python interpreter."""
    raise unittest.SkipTest("detector cannot start a child Python interpreter")


def run_python_until_end(*args, **kwargs):
    """Skip tests requiring a child Python interpreter."""
    raise unittest.SkipTest("detector cannot start a child Python interpreter")


def make_script(*args, **kwargs):
    """Skip tests requiring host filesystem writes."""
    raise unittest.SkipTest("detector does not grant host filesystem writes")
