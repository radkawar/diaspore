"""Check PEP 695 parsing and lowering against CPython 3.12."""

import ast
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

from diaspore import Machine, compile_bundle
from diaspore.compiler import CPYTHON_DIR, DiasporeCompileError, ModuleCompiler, compact_bundle
from diaspore.dspb import decode, encode
from diaspore.stdlib import _pyparse
from diaspore.stdlib._pyparse import CompileError
from diaspore.validator import validate_bundle

ROOT = Path(__file__).resolve().parents[1]
sys.modules.setdefault("_pyparse", _pyparse)
_ast = importlib.import_module("diaspore.stdlib._ast")


def selfhost_compiler():
    """Load the maintained compiler on CPython with its subset parser."""
    sys.path.insert(0, str(ROOT / "src" / "diaspore" / "stdlib"))
    try:
        spec = importlib.util.spec_from_file_location("pep695_dspc", ROOT / "selfhost" / "dspc.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.ModuleCompiler
    finally:
        sys.path.pop(0)


@pytest.mark.parametrize("source", [
    "type A = int\n",
    "type A[T: int, *Ts, **P] = tuple[T, *Ts]\n",
    "def f[T: (str, int), **P](x: T, *args: P.args) -> T: return x\n",
    "async def f[T](x: T) -> T: return x\n",
    "class C[T](list[T]):\n    type A[U] = tuple[T, U]\n",
    "type = 1\ntype += 2\n",
    "text = f'quoted {str('value')}'\n",
    "if ready:\n\tvalue = 'é'\n\tother = value;\n",
    "def f(): pass;\nclass C: pass;\nif ready: pass; pass;\n",
    'f"{{{value}}}"\n',
    'f"é{{{{{value}}}}}x"\n',
])
def test_ast_type_parameters(source):
    """Preserve the upstream AST fields and exact source locations."""
    actual = _ast._compile(source, "<test>", "exec", _ast.PyCF_ONLY_AST)
    expected = ast.parse(source)

    def shape(value):
        if isinstance(value, (ast.AST, _ast.AST)):
            return (type(value).__name__,
                    [(name, shape(getattr(value, name))) for name in value._fields],
                    [(name, getattr(value, name)) for name in value._attributes
                     if hasattr(value, name)])
        if isinstance(value, list):
            return [shape(item) for item in value]
        return value

    assert shape(actual) == shape(expected)


@pytest.mark.parametrize("path", [
    Path(CPYTHON_DIR) / "typing.py",
    Path(CPYTHON_DIR) / "traceback.py",
    ROOT / "tests" / "corpus" / "113_pep695.py",
])
def test_native_selfhost_lowering(path):
    """Both lowerers accept unchanged typing and emit the same generic IR."""
    source = path.read_text()
    native = ModuleCompiler(path.stem, str(path), source)
    portable = selfhost_compiler()(path.stem, str(path), source)
    compiled = native.compile()
    assert compiled == portable.compile()
    assert native.imports == portable.imports
    bundle = {"diaspore": 1, "main": path.stem, "modules": {path.stem: compiled}}
    validate_bundle(bundle)
    assert decode(encode(bundle)) == compact_bundle(bundle)


@pytest.mark.parametrize("source", [
    "type A[] = int\n",
    "type A[T, T] = int\n",
    "def f[*Ts: int](): pass\n",
    "class C[**P: int]: pass\n",
    "type A = (x := int)\n",
    "type A[T: (x := int)] = T\n",
    "type A = (yield int)\n",
    "def f[T]():\n def g():\n  nonlocal T\n",
    "class C[T]:\n def f(self):\n  nonlocal T\n",
    "class C[T]:\n nonlocal T\n",
    "def f[T]():\n class C:\n  nonlocal T\n",
    "def f[T](x: (y := int)): pass\n",
    "class C[T]((x := list)): pass\n",
    "type A = await pending\n",
])
def test_invalid_type_parameters(source):
    """The subset parser rejects invalid type parameter declarations."""
    with pytest.raises(CompileError):
        selfhost_compiler()("test", "<test>", source).compile()
    with pytest.raises(DiasporeCompileError):
        ModuleCompiler("test", "<test>", source).compile()


@pytest.mark.parametrize("parameters", ["", "[T]"])
def test_function_docstring_ir(parameters):
    """Retain function documentation for runtime and property metadata."""
    source = 'def f' + parameters + '():\n    \"\"\"Function documentation.\"\"\"\n    return 1\n'
    native = ModuleCompiler("test", "<test>", source).compile()
    portable = selfhost_compiler()("test", "<test>", source).compile()
    assert native == portable

    def functions(value):
        if isinstance(value, dict):
            if value.get("op") in ("func", "funcexpr") and value["name"] == "f":
                yield value
            for child in value.values():
                yield from functions(child)
        elif isinstance(value, list):
            for child in value:
                yield from functions(child)

    function, = functions(native)
    assert function["body"][0]["v"] == {
        "op": "const", "t": "str", "v": "Function documentation."
    }


def test_selfhost_vm_compiles_upstream_typing(tmp_path):
    """Run the real self-hosted lowerer in the VM against unchanged typing.py."""
    entry = tmp_path / "compile_typing.py"
    entry.write_text(
        "import json\n"
        "import sys\n"
        "from dspc import ModuleCompiler\n"
        "with open(sys.argv[1]) as source:\n"
        "    text = source.read()\n"
        "print(json.dumps(ModuleCompiler('typing', '<stdlib>/typing.py', text).compile()))\n"
    )
    path = Path(CPYTHON_DIR) / "typing.py"
    bundle = compile_bundle(str(entry), search_paths=[str(ROOT / "selfhost")])
    out, err = io.StringIO(), io.StringIO()
    machine = Machine(
        bundle, argv=[str(entry), str(path)],
        caps={"fs"}, policy={"fs": {"read": [str(path)], "write": []}},
        stdout=out, stderr=err, max_depth=3000,
    )
    assert machine.run() == 0, err.getvalue()
    expected = ModuleCompiler("typing", "<stdlib>/typing.py", path.read_text()).compile()
    assert json.loads(out.getvalue()) == expected


def test_module_and_class_docstrings():
    """Class documentation does not inherit from a documented base class."""
    source = (
        '"""Module documentation."""\n'
        'class Base:\n    """Class documentation."""\n'
        'class Child(Base):\n    pass\n'
        'print(__doc__, Base.__doc__, Child.__doc__)\n'
    )
    module = ModuleCompiler("docs", "<docs>", source).compile()
    assert module == selfhost_compiler()("docs", "<docs>", source).compile()
    out, err = io.StringIO(), io.StringIO()
    machine = Machine(
        {"diaspore": 1, "main": "docs", "modules": {"docs": module}},
        stdout=out, stderr=err,
    )
    assert machine.run() == 0, err.getvalue()
    assert out.getvalue() == "Module documentation. Class documentation. None\n"


def test_absent_class_docstring_preserves_slots():
    """Do not invent class variables that conflict with declared slots."""
    source = (
        "class Meta(type):\n"
        "    def __new__(cls, name, bases, namespace):\n"
        "        print(name, '__doc__' in namespace)\n"
        "        return super().__new__(cls, name, bases, namespace)\n"
        "class Slotted(metaclass=Meta):\n"
        "    __slots__ = ('__doc__',)\n"
        "class Documented(metaclass=Meta):\n"
        "    'Explicit documentation.'\n"
        "class Assigned(metaclass=Meta):\n"
        "    __doc__ = None\n"
        "value = Slotted()\n"
        "value.__doc__ = 'Instance documentation.'\n"
        "print(value.__doc__, Documented.__doc__, Assigned.__doc__)\n"
    )
    module = ModuleCompiler("docs", "<docs>", source).compile()
    assert module == selfhost_compiler()("docs", "<docs>", source).compile()
    out, err = io.StringIO(), io.StringIO()
    machine = Machine(
        {"diaspore": 1, "main": "docs", "modules": {"docs": module}},
        stdout=out, stderr=err,
    )
    assert machine.run() == 0, err.getvalue()
    assert out.getvalue() == (
        "Slotted False\nDocumented True\nAssigned True\n"
        "Instance documentation. Explicit documentation. None\n"
    )
