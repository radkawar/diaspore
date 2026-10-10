"""Keep compiler directives and real __future__ module bindings independent."""

import contextlib
import io

import pytest

from diaspore import Machine, compile_bundle
from diaspore.compiler import ModuleCompiler

from .test_pep695 import selfhost_compiler
from .test_selfhost import SELFHOST, selfhost_compile


@pytest.fixture(scope="module")
def compiler_bundle():
    """Compile the real self-hosted compiler for runtime dependency checks."""
    return compile_bundle(str(SELFHOST))


@pytest.mark.parametrize("source", [
    "import __future__\n"
    "import __future__ as features\n"
    "print(features is __future__)\n"
    "print(features.annotations.compiler_flag, features.annotations.getMandatoryRelease())\n",
    "from __future__ import annotations as postponed, division\n"
    "import __future__ as features\n"
    "class Example:\n"
    " field: Missing\n"
    " def method[T](self, value: T) -> T: return value\n"
    "print(postponed is features.annotations, division is features.division)\n"
    "print(Example.__annotations__, Example.method.__annotations__)\n",
    "from __future__ import annotations\n"
    "import __future__\n"
    "print(annotations is __future__.annotations)\n"
    "print(annotations.getOptionalRelease())\n",
])
def test_future_import_runtime_bindings(source, tmp_path, compiler_bundle):
    """Both import forms bind genuine features and retain future flag semantics."""
    expected = io.StringIO()
    with contextlib.redirect_stdout(expected):
        exec(compile(source, "<future>", "exec", dont_inherit=True), {"__name__": "__main__"})
    native = ModuleCompiler("future", "<future>", source)
    portable = selfhost_compiler()("future", "<future>", source)
    assert portable.compile() == native.compile()
    assert portable.imports == native.imports
    assert "__future__" in native.imports
    entry = tmp_path / "future.py"
    entry.write_text(source)
    bundle = compile_bundle(str(entry), self_contained=True)
    assert "__future__" in bundle["modules"]
    assert "stdlib" not in bundle
    assert selfhost_compile(compiler_bundle, entry, self_contained=True) == bundle
    out, err = io.StringIO(), io.StringIO()
    assert Machine(bundle, stdout=out, stderr=err).run() == 0, err.getvalue()
    assert out.getvalue() == expected.getvalue()
