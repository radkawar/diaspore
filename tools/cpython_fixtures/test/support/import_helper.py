"""Regression imports retain real failures; import isolation is unsupported."""

import unittest


def import_module(name, deprecated=False, *, required_on=()):
    """Import a module without hiding genuine import failures."""
    if deprecated or required_on:
        raise NotImplementedError("detector import_module options are unsupported")
    return __import__(name, fromlist=["*"])


def import_fresh_module(name, fresh=(), blocked=(), *, deprecated=False, usefrozen=False):
    """Skip tests requiring isolated module caches."""
    raise unittest.SkipTest("detector cannot isolate or block the VM module cache")


def forget(name):
    """Reject requests to invalidate the VM module cache."""
    raise NotImplementedError("detector cannot invalidate the VM module cache")


def unload(name):
    """Reject requests to invalidate the VM module cache."""
    raise NotImplementedError("detector cannot invalidate the VM module cache")
