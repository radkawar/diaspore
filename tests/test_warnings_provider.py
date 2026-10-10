"""Compare the portable warning engine with CPython's filtering and frame rules."""

import pytest

from .conftest import run_cpython, run_diaspore_py


@pytest.mark.parametrize("first", ["warnings", "_warnings"])
def test_warning_filters_registries_and_stacklevel(tmp_path, first):
    source = tmp_path / "warning_checks.py"
    source.write_text(
        "import "
        + first
        + "\n"
        + """
import warnings
import _warnings

def issue():
    warnings.warn("from helper", UserWarning, stacklevel=2)

with warnings.catch_warnings(record=True) as records:
    warnings.simplefilter("always")
    issue()
    print(records[0].message, records[0].category.__name__, records[0].lineno)
    print(records[0].filename.endswith("warning_checks.py"))

for action in ("ignore", "always", "default", "module", "once", "error"):
    with warnings.catch_warnings(record=True) as records:
        warnings.simplefilter(action)
        registry = {}
        try:
            for line in (10, 10, 11):
                _warnings.warn_explicit("repeated", UserWarning, "sample.py", line,
                                        module="sample", registry=registry)
        except UserWarning as error:
            print(action, "raised", str(error))
        else:
            print(action, len(records))

with warnings.catch_warnings(record=True) as records:
    warnings.simplefilter("default")
    registry = {}
    for _ in range(2):
        warnings.warn_explicit("cache", UserWarning, "sample.py", 2, registry=registry)
    warnings.simplefilter("always")
    warnings.warn_explicit("cache", UserWarning, "sample.py", 2, registry=registry)
    print("invalidated", len(records))

with warnings.catch_warnings(record=True) as records:
    warnings.simplefilter("always")
    warnings.filterwarnings("ignore", message="hidden", category=RuntimeWarning,
                            module="matched", lineno=5)
    for message, category, module, line in (
            ("hidden", RuntimeWarning, "matched", 5),
            ("shown", RuntimeWarning, "matched", 5),
            ("hidden", UserWarning, "matched", 5),
            ("hidden", RuntimeWarning, "other", 5),
            ("hidden", RuntimeWarning, "matched", 6)):
        warnings.warn_explicit(message, category, "sample.py", line, module=module)
    print("matched", len(records))

shown = []
with warnings.catch_warnings():
    warnings.simplefilter("always")
    def display(message, category, filename, lineno, file=None, line=None):
        shown.append((str(message), category.__name__, lineno))
    warnings.showwarning = display
    warnings.warn_explicit("displayed", RuntimeWarning, "sample.py", 23)
print(shown)
"""
    )
    expected = run_cpython(source)
    assert expected.code == 0, expected.err_line
    result = run_diaspore_py(source)
    assert result.code == 0, result.err_line
    assert result.stdout == expected.stdout
