"""CPython host-thread helpers remain explicit environment skips."""

import unittest


def requires_working_threading(*args, **kwargs):
    """Mark tests requiring host-thread lifecycle controls as skipped."""
    return unittest.skip("requires CPython host-thread lifecycle controls")


def reap_threads(func):
    """Skip a test requiring host-thread lifecycle controls."""
    return unittest.skip("requires CPython host-thread lifecycle controls")(func)


def start_threads(*args, **kwargs):
    """Skip tests requiring host-thread lifecycle controls."""
    raise unittest.SkipTest("requires CPython host-thread lifecycle controls")
