"""Filesystem environment helpers do not grant ambient host authority."""

import unittest

TESTFN = "@cpython_detector_test_file"


def unlink(*args):
    """Skip tests requiring host filesystem mutation."""
    raise unittest.SkipTest("detector does not grant host filesystem mutation")


def temp_cwd(*args, **kwargs):
    """Skip tests requiring changes to the process working directory."""
    raise unittest.SkipTest("detector cannot change process working directory")


def temp_dir(*args, **kwargs):
    """Skip tests requiring host temporary directories."""
    raise unittest.SkipTest("detector does not grant host temporary directories")


def change_cwd(*args, **kwargs):
    """Skip tests requiring changes to the process working directory."""
    raise unittest.SkipTest("detector cannot change process working directory")
