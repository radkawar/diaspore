"""Bootstrap warning primitives from CPython's portable warning implementation.

The upstream module defines its Python engine before importing this provider.
Capturing those functions preserves its filtering, registries, display hooks,
and frame traversal without adding wrapper frames to stacklevel calculations.
"""

import re
import sys
import warnings as _warnings

# Importing this provider first lets warnings finish its pure-Python fallback;
# importing warnings first reaches us before its filters have been initialized.
if hasattr(_warnings, "filters"):
    filters = _warnings.filters
    _defaultaction = _warnings.defaultaction
    _onceregistry = _warnings.onceregistry
else:
    filters = []
    if not hasattr(sys, "gettotalrefcount"):
        filters = [
            ("default", None, DeprecationWarning, re.compile("__main__"), 0),
            ("ignore", None, DeprecationWarning, None, 0),
            ("ignore", None, PendingDeprecationWarning, None, 0),
            ("ignore", None, ImportWarning, None, 0),
            ("ignore", None, ResourceWarning, None, 0),
        ]
    _defaultaction = "default"
    _onceregistry = {}

_warnings._filters_version = getattr(_warnings, "_filters_version", 1)
warn = _warnings.warn
warn_explicit = _warnings.warn_explicit


def _filters_mutated():
    _warnings._filters_version += 1
