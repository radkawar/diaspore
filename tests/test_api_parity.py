"""Inventory source-required APIs against the reference VM and bundled library.

Run with CPython 3.12: uv run --python 3.12 pytest -q -s tests/test_api_parity.py.
DIASPORE_API_REPORT optionally selects the combined JSON inventory destination
(default: build/api-parity.json). build/api-parity-upstream.json always retains
the complete non-gating upstream backlog, including resolved and unresolved uses.

This is an API/argument-binding detector, not an execution or semantic parity
suite. It never imports or runs bundled library sources. Native/builtin lookup
uses the real reference VM dispatch; library signatures come from their ASTs.
Dynamic exports, arbitrary function return types, decorators, and unknown star
arguments remain explicitly unresolved rather than passing. Other runtimes,
value restrictions, side effects, and code-generation support need execution
coverage. Deliberate exclusions in docs/spec.md sections 11.10 and 12 are not
filtered out: a source dependency on an excluded API is still a dependency gap.
Only maintained package/compiler/C-layer source dependencies gate this test.
Exact paths in tools/stdlib_manifest.json identify non-gating upstream source;
directory placement or module-name patterns never confer that exemption. Derived
overlays not listed in the manifest, including _io.py, remain maintained code.

The scan is lexical and conservative, not a whole-program points-to analysis.
It tracks aliases, annotated parameters, literal containers, explicit constructor
and descriptor field writes, and known builtin return types. Conditional aliases,
subclass-only contracts, optional probes/imports, and native instance layouts stay
unresolved when their runtime target cannot be established. Branches ruled out by
inert reference-VM constants are retained as unresolved inventory, not API passes.
It does not prove general reachability, mutation-sensitive container contents,
arbitrary decorators, or dynamic imports. Unresolved counts and locations are
retained even when all proven gaps are fixed.
"""

from __future__ import annotations

import ast
import builtins
import inspect
import io
import json
import os
import sys
import sysconfig
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from tokenize import open as open_source
from typing import Any

import pytest

from diaspore import vm
from diaspore.compiler import NATIVE_MODULES

from .conftest import ROOT


@dataclass(frozen=True)
class Value:
    kind: str
    module: str = ""
    parts: tuple[str, ...] = ()
    element: Value | None = None
    mapped: Value | None = None
    reason: str = ""

    @property
    def label(self) -> str:
        text = ".".join((self.module, *self.parts)).strip(".")
        return text + (" instance" if self.kind == "instance" else "")


UNKNOWN = Value("unknown", reason="value is not statically typed")
DEFAULT = object()
SAMPLES = {
    "str": "",
    "bytes": b"",
    "bytearray": bytearray(),
    "list": [],
    "tuple": (),
    "dict": {},
    "set": set(),
    "frozenset": frozenset(),
    "int": 0,
    "float": 0.0,
    "bool": False,
    "complex": 0j,
    "range": range(0),
    "slice": slice(0),
    "NoneType": None,
    "text_file": vm.DiasporeFile(io.StringIO()),
    "binary_file": vm.DiasporeFile(io.BytesIO()),
}


def primitive(name: str, element: Value | None = None) -> Value:
    return Value("instance", "builtins", (name,), element)


def common(values: list[Value]) -> Value:
    if not values:
        return UNKNOWN
    first = values[0]
    return (
        first
        if all(value == first for value in values)
        else Value("unknown", reason="control flow or container has multiple possible types")
    )


@dataclass
class Definition:
    node: ast.AST
    module: str
    path: Path
    parts: tuple[str, ...]
    alias: Value | None = None
    members: dict[str, Definition] = field(default_factory=dict)
    dynamic: bool = False


@dataclass
class Binding:
    status: str
    reason: str = ""
    definition: Definition | None = None
    runtime: Any = DEFAULT
    bound: bool = False
    redirect: Value | None = None


class Index:
    """Parse each source once and lazily index CPython sources without importing them."""

    def __init__(self, root: Path, paths: list[Path]):
        self.root = root
        self.stdlib_roots = tuple(
            root / "src" / "diaspore" / name for name in ("stdlib", "cpython")
        )
        self.hostlib = Path(sysconfig.get_path("stdlib"))
        self.trees: dict[Path, ast.Module] = {}
        self.modules: dict[str, Definition] = {}
        self.host: dict[str, Definition | None] = {}
        self.paths = paths
        self.static_cache: dict[tuple[Value, bool], Binding] = {}
        self.environment_cache: dict[tuple[str, bool], dict[str, Value]] = {}
        for path in paths:
            name = self.module_name(path)
            with open_source(path) as source:
                tree = ast.parse(source.read(), filename=str(path))
            self.trees[path] = tree
            existing = self.modules.get(name)
            if existing is None or path.is_relative_to(self.stdlib_roots[0]):
                self.modules[name] = self.make_module(name, path, tree)

    def module_name(self, path: Path) -> str:
        source_root = next(
            (root for root in self.stdlib_roots if path.is_relative_to(root)), self.root / "src"
        )
        if not path.is_relative_to(source_root):
            return "__dspc__"
        relative = path.relative_to(source_root)
        parts = list(relative.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    def import_name(self, node: ast.ImportFrom, module: str, path: Path) -> str:
        if not node.level:
            return node.module or ""
        package = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
        keep = len(package) - node.level + 1
        if keep < 0:
            return "<invalid-relative-import>"
        return ".".join(package[:keep] + ([node.module] if node.module else []))

    def make_module(self, name: str, path: Path, tree: ast.Module) -> Definition:
        module = Definition(tree, name, path, ())
        self.index_body(module, tree.body)
        self.index_instance_writes(module, tree)
        return module

    def index_instance_writes(self, module: Definition, tree: ast.Module) -> None:
        """Record explicit constructor/descriptor writes without running factories."""

        def scan(body: list[ast.stmt], owner: Definition | None = None) -> None:
            receivers: dict[str, Definition] = {}
            if owner is not None:
                receivers["self"] = owner
            for statement in body:
                if isinstance(statement, ast.ClassDef):
                    item = module.members.get(statement.name)
                    if item is not None:
                        scan(statement.body, item)
                    continue
                if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    scan(statement.body, owner)
                    continue
                for node in ast.walk(statement):
                    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                        call = node.value
                        item = None
                        if isinstance(call.func, ast.Name):
                            candidate = module.members.get(call.func.id)
                            if candidate and isinstance(candidate.node, ast.ClassDef):
                                item = candidate
                        elif (
                            isinstance(call.func, ast.Attribute)
                            and call.func.attr == "__new__"
                            and call.args
                            and isinstance(call.args[0], ast.Name)
                        ):
                            if call.args[0].id == "cls":
                                item = owner
                            else:
                                candidate = module.members.get(call.args[0].id)
                                if candidate and isinstance(candidate.node, ast.ClassDef):
                                    item = candidate
                        if item:
                            for target in node.targets:
                                if isinstance(target, ast.Name):
                                    receivers[target.id] = item
                    receiver = None
                    name = None
                    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
                        receiver, name = node.value, node.attr
                    elif (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "object"
                        and node.func.attr == "__setattr__"
                        and len(node.args) == 3
                        and isinstance(node.args[1], ast.Constant)
                        and isinstance(node.args[1].value, str)
                    ):
                        receiver, name = node.args[0], node.args[1].value
                    if isinstance(receiver, ast.Name) and receiver.id in receivers:
                        item = receivers[receiver.id]
                        item.members.setdefault(
                            name,
                            Definition(node, item.module, item.path, (*item.parts, name)),
                        )

        scan(tree.body)

    def subclass_defines(self, owner: Definition, name: str) -> bool:
        """Detect known subclass contracts without choosing a runtime subtype."""

        def descends(item: Definition, seen: frozenset[tuple[str, tuple[str, ...]]]) -> bool:
            key = (item.module, item.parts)
            if key in seen:
                return False
            for base in item.node.bases:
                value = self.expression(base, self.environment(item.module))
                parent = self.static(value).definition
                if parent is owner:
                    return True
                if parent and isinstance(parent.node, ast.ClassDef):
                    if descends(parent, seen | {key}):
                        return True
            return False

        for module in self.modules.values():
            for item in module.members.values():
                if isinstance(item.node, ast.ClassDef) and name in item.members:
                    if item is not owner and descends(item, frozenset()):
                        return True
        return False

    def index_body(self, owner: Definition, body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                item = Definition(node, owner.module, owner.path, (*owner.parts, node.name))
                owner.members[node.name] = item
                if isinstance(node, ast.ClassDef):
                    self.index_body(item, node.body)
                    for statement in node.body:
                        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            for child in ast.walk(statement):
                                if isinstance(child, ast.Attribute) and isinstance(
                                    child.ctx, ast.Store
                                ):
                                    if (
                                        isinstance(child.value, ast.Name)
                                        and child.value.id == "self"
                                    ):
                                        item.members.setdefault(
                                            child.attr,
                                            Definition(
                                                child,
                                                owner.module,
                                                owner.path,
                                                (*item.parts, child.attr),
                                            ),
                                        )
                                if isinstance(child, (ast.Assign, ast.AnnAssign)):
                                    targets = (
                                        child.targets
                                        if isinstance(child, ast.Assign)
                                        else [child.target]
                                    )
                                    for target in targets:
                                        if not (
                                            isinstance(target, ast.Attribute)
                                            and isinstance(target.value, ast.Name)
                                            and target.value.id == "self"
                                        ):
                                            continue
                                        prior = item.members.get(target.attr)
                                        if prior is None:
                                            item.members[target.attr] = Definition(
                                                child,
                                                owner.module,
                                                owner.path,
                                                (*item.parts, target.attr),
                                            )
                                        elif isinstance(prior.node, (ast.Assign, ast.AnnAssign)):
                                            old = (
                                                ast.dump(prior.node.value)
                                                if prior.node.value
                                                else None
                                            )
                                            new = ast.dump(child.value) if child.value else None
                                            if old != new:
                                                item.members[target.attr] = Definition(
                                                    target,
                                                    owner.module,
                                                    owner.path,
                                                    (*item.parts, target.attr),
                                                )
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name == "*":
                        owner.dynamic = True
                        continue
                    if isinstance(node, ast.Import):
                        name = alias.asname or alias.name.split(".")[0]
                        target = Value("module", alias.name if alias.asname else name)
                    else:
                        name = alias.asname or alias.name
                        target = Value(
                            "symbol",
                            self.import_name(node, owner.module, owner.path),
                            (alias.name,),
                        )
                    owner.members[name] = Definition(
                        node, owner.module, owner.path, (*owner.parts, name), target
                    )
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                if any(
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Call)
                    and isinstance(target.value.func, ast.Name)
                    and target.value.func.id in ("globals", "locals")
                    for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                ):
                    owner.dynamic = True
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    for child in ast.walk(target):
                        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                            owner.members[child.id] = Definition(
                                node, owner.module, owner.path, (*owner.parts, child.id)
                            )
            else:
                # Conditional exports are possible exports, not guaranteed imports.
                for _, value in ast.iter_fields(node):
                    if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
                        before = dict(owner.members)
                        self.index_body(owner, value)
                        for name, definition in owner.members.items():
                            if before.get(name) is not definition:
                                definition.dynamic = True
                    elif isinstance(value, list):
                        for handler in value:
                            if isinstance(handler, ast.ExceptHandler):
                                before = dict(owner.members)
                                self.index_body(owner, handler.body)
                                for name, definition in owner.members.items():
                                    if before.get(name) is not definition:
                                        definition.dynamic = True

    def host_module(self, name: str) -> Definition | None:
        if not name:
            return None
        if name not in self.host:
            relative = Path(*name.split("."))
            candidates = [
                self.hostlib / relative.with_suffix(".py"),
                self.hostlib / relative / "__init__.py",
            ]
            self.host[name] = None
            for path in candidates:
                if path.is_file():
                    try:
                        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                    except (SyntaxError, UnicodeError):
                        break
                    self.host[name] = self.make_module(name, path, tree)
                    break
        return self.host[name]

    def external(self, module: str) -> bool:
        return (
            module in self.modules and not module.startswith("diaspore") and module != "__dspc__"
        ) or module.split(".")[0] in (sys.stdlib_module_names | set(NATIVE_MODULES) | {"builtins"})

    def static(
        self, value: Value, host: bool = False, seen: frozenset[tuple] = frozenset()
    ) -> Binding:
        key = (value, host)
        if key not in self.static_cache:
            self.static_cache[key] = self._static(value, host, seen)
        return self.static_cache[key]

    def _static(self, value: Value, host: bool, seen: frozenset[tuple]) -> Binding:
        key = (value.module, value.parts, host)
        if key in seen:
            return Binding("unresolved", "cyclic alias or inheritance")
        seen = seen | {key}
        module = self.host_module(value.module) if host else self.modules.get(value.module)
        if module is None:
            if not host and value.module in set(NATIVE_MODULES) | {"builtins"}:
                return Binding("redirect", redirect=value)
            if "." in value.module:
                root, *parts = value.module.split(".")
                if root in self.modules:
                    return self.static(
                        Value(value.kind, root, (*parts, *value.parts), reason=value.reason),
                        host,
                        seen,
                    )
            return Binding("missing", f"module {value.module!r} is not bundled or native")
        current = module
        for offset, name in enumerate(value.parts):
            member = current.members.get(name)
            if member is None and isinstance(current.node, ast.ClassDef):
                if name in vars(object) or name in vars(type) or name == "__dict__":
                    return Binding(
                        "unresolved", "source class runtime metadata is not instantiated"
                    )
                env = self.environment(current.module, host)
                for base in current.node.bases:
                    parent = self.expression(base, env)
                    found = self.static(
                        Value("symbol", parent.module, (*parent.parts, name)), host, seen
                    )
                    if found.status == "present":
                        member = found.definition
                        break
                    if found.status == "unresolved":
                        return found
                if member is None and any(
                    name in current.members for name in ("__getattr__", "__getattribute__")
                ):
                    return Binding("unresolved", "class implements dynamic attribute lookup")
            if member is None and not isinstance(current.node, (ast.Module, ast.ClassDef)):
                return Binding("unresolved", "attribute receiver is an assigned or generated value")
            if member is None:
                submodule = ".".join((value.module, *value.parts[: offset + 1]))
                sub = self.host_module(submodule) if host else self.modules.get(submodule)
                if sub is not None:
                    current = sub
                    continue
                if current.dynamic or "__getattr__" in current.members:
                    return Binding("unresolved", "exports include wildcard or dynamic lookup")
                return Binding(
                    "missing",
                    f"{'.'.join((current.module, *current.parts))} does not define {name!r}",
                )
            if member.dynamic:
                return Binding(
                    "unresolved", "declaration depends on control flow", definition=member
                )
            if member.alias:
                target = member.alias
                submodule = ".".join((target.module, *target.parts))
                sub = self.host_module(submodule) if host else self.modules.get(submodule)
                if sub is not None:
                    return self.static(
                        Value(value.kind, submodule, value.parts[offset + 1 :]), host, seen
                    )
                return self.static(
                    Value(value.kind, target.module, (*target.parts, *value.parts[offset + 1 :])),
                    host,
                    seen,
                )
            if isinstance(member.node, (ast.Assign, ast.AnnAssign)):
                target = self.expression(member.node.value, self.environment(member.module, host))
                if target.kind in ("module", "symbol") and (target.module, target.parts) != key[:2]:
                    return self.static(
                        Value(
                            value.kind, target.module, (*target.parts, *value.parts[offset + 1 :])
                        ),
                        host,
                        seen,
                    )
            current = member
        return Binding("present", definition=current, bound=value.kind == "method")

    def environment(self, module: str, host: bool = False) -> dict[str, Value]:
        key = (module, host)
        if key not in self.environment_cache:
            item = self.host_module(module) if host else self.modules.get(module)
            self.environment_cache[key] = (
                {}
                if item is None
                else {
                    name: (
                        Value("unknown", reason="alias depends on control flow")
                        if definition.dynamic
                        else definition.alias or Value("symbol", module, (name,))
                    )
                    for name, definition in item.members.items()
                }
            )
        return self.environment_cache[key]

    def expression(self, node: ast.AST | None, env: dict[str, Value]) -> Value:
        if isinstance(node, ast.Name):
            return env.get(
                node.id,
                Value("symbol", "builtins", (node.id,)) if hasattr(builtins, node.id) else UNKNOWN,
            )
        if isinstance(node, ast.Attribute):
            owner = self.expression(node.value, env)
            if owner.kind in ("module", "symbol"):
                return Value("symbol", owner.module, (*owner.parts, node.attr))
        return UNKNOWN


class Surface:
    """Resolve through real VM dispatch without executing any source call site."""

    def __init__(self, index: Index):
        self.index = index
        previous_limit = sys.getrecursionlimit()
        try:
            self.interp = vm.Interp(
                {"diaspore": 1, "main": "__api__", "modules": {}},
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )
        finally:
            sys.setrecursionlimit(previous_limit)
        self.cache: dict[Value, Binding] = {}
        self.native_results: dict[Value, dict[str, Binding] | None] = {}

    def lookup(self, value: Value) -> Binding:
        if value not in self.cache:
            self.cache[value] = Binding("unresolved", "recursive API inheritance")
            self.cache[value] = self._lookup(value)
        return self.cache[value]

    def _lookup(self, value: Value) -> Binding:
        if value.kind == "unknown":
            return Binding("unresolved", value.reason)
        if value.kind == "native_method":
            factory = Value("symbol", value.module, value.parts[:-1])
            members = self.native_results.get(factory)
            if members is None:
                return Binding("unresolved", "native factory has a dynamic return surface")
            try:
                receiver = vm.NativeObject(factory.label, members)
                return self.interp.get_attr(receiver, value.parts[-1])
            except vm.DiasporeError as exc:
                return Binding("missing", self.interp.exc_str(exc.value))
        if not self.index.external(value.module):
            if value.module.startswith("diaspore") or value.module == "__dspc__":
                return Binding("internal", "project-local value")
            return Binding("unresolved", "import or value is outside the known stdlib inventory")
        if value.module == "builtins" or value.module in self.interp.natives:
            if value.module == "builtins":
                if not value.parts:
                    native = self.interp.natives.get("builtins")
                    return (
                        Binding("present", runtime=native)
                        if native is not None
                        else Binding("missing", "builtin namespace module is not exposed")
                    )
                name, *parts = value.parts
                if value.kind in ("instance", "method"):
                    if name not in SAMPLES:
                        return Binding("unresolved", f"no inert representative for {name}")
                    if name not in self.interp.builtins and name not in (
                        "NoneType",
                        "text_file",
                        "binary_file",
                    ):
                        return Binding("missing", f"builtin {name!r} is not exposed")
                    obj = SAMPLES[name]
                else:
                    if name not in self.interp.builtins:
                        return Binding("missing", f"builtin {name!r} is not exposed")
                    obj = self.interp.builtins[name]
            else:
                obj = self.interp.natives[value.module]
                parts = list(value.parts)
            try:
                for name in parts:
                    if value.kind == "method" and (
                        inspect.isclass(obj) or isinstance(obj, (vm.DiasporeClass, vm.BuiltinType))
                    ):
                        return Binding(
                            "unresolved", "native class instance requires constructor analysis"
                        )
                    obj = self.interp.get_attr(obj, name)
            except vm.DiasporeError as exc:
                return Binding("missing", self.interp.exc_str(exc.value))
            return Binding("present", runtime=obj)
        if value.module in NATIVE_MODULES and value.module not in self.index.modules:
            return Binding("missing", f"native module {value.module!r} has no runtime provider")
        binding = self.index.static(value)
        if binding.redirect is not None:
            return self.lookup(binding.redirect)
        if binding.status == "missing" and len(value.parts) >= 2:
            owner = self.index.static(Value("symbol", value.module, value.parts[:-1]))
            if owner.definition and isinstance(owner.definition.node, ast.ClassDef):
                definition = owner.definition
                name = value.parts[-1]
                if value.reason == "polymorphic receiver" and self.index.subclass_defines(
                    definition, name
                ):
                    return Binding("unresolved", "attribute is provided by a runtime subclass")
                for base in definition.node.bases:
                    parent = self.index.expression(base, self.index.environment(definition.module))
                    if parent.kind == "unknown":
                        return Binding("unresolved", "base class is dynamically selected")
                    inherited = self.lookup(
                        Value(value.kind, parent.module, (*parent.parts, value.parts[-1]))
                    )
                    if inherited.status != "missing":
                        return inherited
        return binding

    def native_result(self, target: Value) -> Value:
        if target not in self.native_results:
            self.native_results[target] = None
            binding = self.lookup(target)
            fn = binding.runtime
            if fn is not DEFAULT and inspect.isfunction(fn):
                try:
                    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
                except (OSError, TypeError, SyntaxError):
                    tree = None
                if tree:
                    functions = {
                        node.name: node
                        for node in ast.walk(tree)
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    }
                    shapes = [
                        node.value
                        for node in getattr(tree.body[0], "body", [])
                        if isinstance(node, ast.Return)
                        and isinstance(node.value, ast.Call)
                        and isinstance(node.value.func, ast.Name)
                        and node.value.func.id == "NativeObject"
                    ]
                    if len(shapes) == 1 and len(shapes[0].args) == 2 and not shapes[0].keywords:
                        exports = shapes[0].args[1]
                        if isinstance(exports, ast.Dict) and all(
                            isinstance(key, ast.Constant) and isinstance(key.value, str)
                            for key in exports.keys
                        ):
                            members = {}
                            for key, value in zip(exports.keys, exports.values):
                                if isinstance(value, ast.Name):
                                    value = functions.get(value.id, value)
                                if isinstance(value, (ast.Lambda, ast.FunctionDef)):
                                    members[key.value] = Binding(
                                        "present",
                                        definition=Definition(
                                            value,
                                            target.module,
                                            Path(inspect.getfile(fn)),
                                            (*target.parts, key.value),
                                        ),
                                    )
                                else:
                                    members[key.value] = Binding(
                                        "unresolved", "native method delegates to a captured value"
                                    )
                            self.native_results[target] = members
        if self.native_results[target] is not None:
            return Value("native_instance", target.module, target.parts)
        return UNKNOWN

    def signature(self, value: Value, binding: Binding) -> tuple[inspect.Signature | None, str]:
        if binding.runtime is not DEFAULT:
            target = binding.runtime
            if isinstance(target, vm.BuiltinType):
                target = target.ctor
            if isinstance(target, vm.DiasporeClass):
                return None, "VM class constructor requires runtime binding analysis"
            try:
                signature = inspect.signature(target)
            except (TypeError, ValueError):
                return None, "native callable has no inspectable signature"
            variadic = any(
                p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in signature.parameters.values()
            )
            if variadic and inspect.isfunction(target):
                # These wrappers can forward to stricter argument validators.
                if value.module == "builtins" and len(value.parts) == 2:
                    host = getattr(builtins, value.parts[0], None)
                    try:
                        return inspect.signature(getattr(host, value.parts[1])), ""
                    except (AttributeError, TypeError, ValueError):
                        pass
                return signature, "variadic native wrapper may delegate argument validation"
            return signature, ""
        definition = binding.definition
        if definition is None:
            return None, binding.reason or "no callable definition"
        node = definition.node
        bound = binding.bound
        if isinstance(node, ast.ClassDef):
            if node.keywords:
                return None, "class keywords can change constructor binding"
            constructor = self.index.static(
                Value("method", definition.module, (*definition.parts, "__init__"))
            )
            if constructor.status != "present":
                return None, "constructor is inherited or dynamically supplied"
            definition = constructor.definition
            node = definition.node
            bound = True
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Lambda):
            node = node.value
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return None, "callable is assigned, generated, or has no function definition"
        decorators = getattr(node, "decorator_list", [])
        names = [ast.unparse(decorator) for decorator in decorators]
        if any(name not in ("staticmethod", "classmethod", "property") for name in names):
            return None, "decorator can replace the function signature: " + ", ".join(names)
        if "property" in names:
            return None, "calling a property value requires its return type"
        if "staticmethod" in names:
            bound = False
        if "classmethod" in names:
            bound = True
        signature = ast_signature(node.args)
        if bound:
            parameters = list(signature.parameters.values())
            if parameters and parameters[0].kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ):
                signature = signature.replace(parameters=parameters[1:])
        return signature, ""


def ast_signature(args: ast.arguments) -> inspect.Signature:
    parameters = []
    positional = [*args.posonlyargs, *args.args]
    first_default = len(positional) - len(args.defaults)
    for offset, arg in enumerate(positional):
        kind = (
            inspect.Parameter.POSITIONAL_ONLY
            if offset < len(args.posonlyargs)
            else (inspect.Parameter.POSITIONAL_OR_KEYWORD)
        )
        default = DEFAULT if offset >= first_default else inspect.Parameter.empty
        parameters.append(inspect.Parameter(arg.arg, kind, default=default))
    if args.vararg:
        parameters.append(inspect.Parameter(args.vararg.arg, inspect.Parameter.VAR_POSITIONAL))
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        parameters.append(
            inspect.Parameter(
                arg.arg,
                inspect.Parameter.KEYWORD_ONLY,
                default=DEFAULT if default is not None else inspect.Parameter.empty,
            )
        )
    if args.kwarg:
        parameters.append(inspect.Parameter(args.kwarg.arg, inspect.Parameter.VAR_KEYWORD))
    return inspect.Signature(parameters)


class LocalNames(ast.NodeVisitor):
    """Find lexical bindings without walking into a nested scope."""

    def __init__(self):
        self.names: set[str] = set()
        self.shared: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(alias.asname or alias.name for alias in node.names if alias.name != "*")

    def visit_Global(self, node: ast.Global) -> None:
        self.shared.update(node.names)

    visit_Nonlocal = visit_Global

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        pass

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp


class Scanner(ast.NodeVisitor):
    """Track lexical aliases and conservative receiver types in one source pass."""

    def __init__(self, index: Index, surface: Surface, path: Path):
        self.index = index
        self.surface = surface
        self.path = path
        self.module = index.module_name(path)
        self.env = dict(index.environment(self.module))
        self.rows: list[dict[str, Any]] = []
        self.values: dict[int, Value] = {}
        self.class_value: Value | None = None
        self.optional_imports = False
        self.inactive_branch = False

    def record(
        self,
        node: ast.AST,
        value: Value,
        operation: str,
        shape: tuple[int, list[str], bool, bool] | None = None,
    ) -> None:
        binding = self.surface.lookup(value)
        if binding.status == "internal":
            return
        status = "gap" if binding.status == "missing" else binding.status
        reason = binding.reason
        if operation == "import" and self.optional_imports and status == "gap":
            status, reason = "unresolved", "missing import is handled by an exception fallback"
        conditional = bool(binding.definition and binding.definition.dynamic)
        if conditional:
            reason = "declaration is conditional; import-time availability is unresolved"
        signature_text = None
        if shape is not None and status == "present":
            signature, reason = self.surface.signature(value, binding)
            if conditional:
                reason = (
                    reason or "declaration is conditional; import-time availability is unresolved"
                )
            if signature is None:
                status = "unresolved"
            else:
                count, keywords, star, double_star = shape
                signature_text = str(signature).replace(repr(DEFAULT), "<default>")
                try:
                    if len(keywords) != len(set(keywords)):
                        raise TypeError("multiple values for a keyword argument")
                    binder = signature.bind_partial if star or double_star else signature.bind
                    binder(*([DEFAULT] * count), **dict.fromkeys(keywords, DEFAULT))
                except TypeError as exc:
                    status, reason = "gap", f"call rejected by {signature_text}: {exc}"
                else:
                    if star or double_star or reason:
                        status = "unresolved"
                        reason = reason or "dynamic *args/**kwargs prevent full binding"
        elif conditional and status == "present":
            status = "unresolved"
        if self.inactive_branch:
            status, reason = "unresolved", "branch is inactive for the reference VM constants"
        self.rows.append(
            {
                "status": "resolved" if status == "present" else status,
                "file": str(self.path.relative_to(self.index.root)),
                "line": node.lineno,
                "column": node.col_offset + 1,
                "expression": ast.unparse(node),
                "api": value.label or "<unknown receiver>",
                "operation": operation,
                "reason": reason,
                "signature": signature_text,
                "arguments": None
                if shape is None
                else {
                    "positional": shape[0],
                    "keywords": shape[1],
                    "dynamic_star": shape[2],
                    "dynamic_double_star": shape[3],
                },
            }
        )

    def evaluate(self, node: ast.AST | None) -> Value:
        if node is None:
            return UNKNOWN
        key = id(node)
        if key not in self.values:
            self.values[key] = self.infer(node)
        return self.values[key]

    def annotation(self, node: ast.AST | None) -> Value:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                return self.annotation(ast.parse(node.value, mode="eval").body)
            except SyntaxError:
                return UNKNOWN
        if isinstance(node, ast.Subscript):
            base = self.index.expression(node.value, self.env)
            if base.module == "builtins" and base.parts and base.parts[-1] in SAMPLES:
                if base.parts[-1] == "dict" and isinstance(node.slice, ast.Tuple):
                    args = node.slice.elts
                    if len(args) == 2:
                        return Value(
                            "instance",
                            "builtins",
                            ("dict",),
                            self.annotation(args[0]),
                            mapped=self.annotation(args[1]),
                        )
                return primitive(base.parts[-1], self.annotation(node.slice))
            return self.annotation(node.value)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return common([self.annotation(node.left), self.annotation(node.right)])
        value = self.index.expression(node, self.env)
        if value.kind in ("symbol", "module"):
            return Value("instance", value.module, value.parts)
        return UNKNOWN

    def receiver_type(self, value: Value) -> Value:
        if value.kind not in ("symbol", "method"):
            return value
        binding = self.surface.lookup(value)
        if binding.status == "internal":
            binding = self.index.static(value)
        if binding.runtime is not DEFAULT and type(binding.runtime).__name__ in SAMPLES:
            return primitive(type(binding.runtime).__name__)
        definition = binding.definition
        if definition and isinstance(definition.node, (ast.Assign, ast.AnnAssign)):
            expression = definition.node.value
            if isinstance(expression, ast.Constant):
                return primitive(type(expression.value).__name__)
            if isinstance(expression, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
                return primitive(type(expression).__name__.lower())
            if isinstance(expression, ast.Call):
                constructor = self.index.expression(
                    expression.func, self.index.environment(definition.module)
                )
                if constructor.module == "builtins" and len(constructor.parts) == 1:
                    if constructor.parts[0] in SAMPLES:
                        return primitive(constructor.parts[0])
        return value

    def infer(self, node: ast.AST) -> Value:
        if isinstance(node, ast.Name):
            if node.id == "__file__":
                return primitive("str")
            return self.receiver_type(self.index.expression(node, self.env))
        if isinstance(node, ast.Constant):
            return primitive(type(node.value).__name__)
        if isinstance(node, (ast.JoinedStr, ast.FormattedValue)):
            return primitive("str")
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return primitive(
                type(node).__name__.lower(), common([self.evaluate(item) for item in node.elts])
            )
        if isinstance(node, ast.Dict):
            return Value(
                "instance",
                "builtins",
                ("dict",),
                common([self.evaluate(key) for key in node.keys if key]),
                mapped=common([self.evaluate(item) for item in node.values]),
            )
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            name = {
                ast.ListComp: "list",
                ast.SetComp: "set",
                ast.DictComp: "dict",
                ast.GeneratorExp: "generator",
            }[type(node)]
            return primitive(
                name, self.evaluate(node.key if isinstance(node, ast.DictComp) else node.elt)
            )
        if isinstance(node, ast.Attribute):
            owner = self.receiver_type(self.evaluate(node.value))
            if owner.kind == "unknown":
                return owner
            if owner.kind == "native_instance":
                return Value("native_method", owner.module, (*owner.parts, node.attr))
            if owner.kind == "instance":
                return Value("method", owner.module, (*owner.parts, node.attr), reason=owner.reason)
            return Value("symbol", owner.module, (*owner.parts, node.attr), reason=owner.reason)
        if isinstance(node, ast.Call):
            target = self.evaluate(node.func)
            if target == Value("symbol", "builtins", ("open",)):
                mode = (
                    node.args[1]
                    if len(node.args) > 1
                    else next(
                        (kw.value for kw in node.keywords if kw.arg == "mode"), ast.Constant("r")
                    )
                )
                if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
                    return primitive("binary_file" if "b" in mode.value else "text_file")
                return Value("unknown", reason="open mode is dynamic")
            if target.module == "builtins" and len(target.parts) == 1:
                name = target.parts[0]
                if name in SAMPLES:
                    item = self.evaluate(node.args[0]).element if node.args else None
                    return primitive(name, item)
            native = self.surface.native_result(target)
            if native.kind != "unknown":
                return native
            definition = self.index.static(target)
            if definition.status != "present":
                definition = self.index.static(target, host=True)
            if definition.definition and isinstance(definition.definition.node, ast.ClassDef):
                return Value("instance", target.module, target.parts)
            if definition.definition and isinstance(
                definition.definition.node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                annotation = definition.definition.node.returns
                if annotation:
                    previous = self.env
                    self.env = self.index.environment(definition.definition.module)
                    try:
                        return self.annotation(annotation)
                    finally:
                        self.env = previous
            if target.kind == "method" and target.module == "builtins":
                return self.builtin_result(target)
            return Value(
                "unknown",
                reason=f"return type of {target.label or 'dynamic callable'} "
                "is not statically resolved",
            )
        if isinstance(node, ast.Subscript):
            value = self.evaluate(node.value)
            if isinstance(node.slice, ast.Slice):
                return value
            if value.module == "builtins" and value.parts in (("str",), ("bytes",)):
                return primitive("str" if value.parts == ("str",) else "int")
            if value.module == "builtins" and value.parts == ("dict",):
                return value.mapped or UNKNOWN
            return value.element or UNKNOWN
        if isinstance(node, (ast.IfExp, ast.BoolOp)):
            options = [node.body, node.orelse] if isinstance(node, ast.IfExp) else node.values
            return common([self.evaluate(option) for option in options])
        if isinstance(node, ast.BinOp):
            left, right = self.evaluate(node.left), self.evaluate(node.right)
            if left == right and isinstance(
                node.op, (ast.Add, ast.Sub, ast.Mult, ast.Mod, ast.BitOr, ast.BitAnd, ast.BitXor)
            ):
                return left
            if isinstance(node.op, ast.Mult):
                if right == primitive("int"):
                    return left
                if left == primitive("int"):
                    return right
        if isinstance(node, ast.UnaryOp):
            return (
                primitive("bool") if isinstance(node.op, ast.Not) else self.evaluate(node.operand)
            )
        if isinstance(node, ast.Compare):
            return primitive("bool")
        if isinstance(node, ast.NamedExpr):
            return self.evaluate(node.value)
        return UNKNOWN

    def builtin_result(self, target: Value) -> Value:
        # Type transfer rules, not a list of source uses or permitted APIs.
        owner, method = target.parts[:2]
        if owner in ("text_file", "binary_file") and method in ("read", "readline"):
            return primitive("str" if owner == "text_file" else "bytes")
        if owner in ("str", "bytes"):
            if method in ("split", "rsplit", "splitlines"):
                return primitive("list", primitive(owner))
            if method in ("partition", "rpartition"):
                return primitive("tuple", primitive(owner))
            if method in (
                "strip",
                "lstrip",
                "rstrip",
                "lower",
                "upper",
                "replace",
                "join",
                "capitalize",
                "casefold",
                "center",
                "expandtabs",
                "ljust",
                "rjust",
                "removeprefix",
                "removesuffix",
                "swapcase",
                "title",
                "zfill",
                "format",
            ):
                return primitive(owner)
            if method == "encode" and owner == "str":
                return primitive("bytes")
            if method == "decode" and owner == "bytes":
                return primitive("str")
        if method == "copy" and owner in ("list", "dict", "set", "frozenset", "bytearray"):
            return primitive(owner)
        return Value("unknown", reason=f"return type of {target.label} is not statically resolved")

    def assign(self, target: ast.AST, value: Value) -> None:
        if isinstance(target, ast.Name):
            self.env[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            for child in target.elts:
                self.assign(child, value.element or UNKNOWN)
        elif isinstance(target, ast.Starred):
            self.assign(target.value, primitive("list", value.element))

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.record(node, Value("module", alias.name), "import")
            name = alias.asname or alias.name.split(".")[0]
            self.env[name] = Value("module", alias.name if alias.asname else name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = self.index.import_name(node, self.module, self.path)
        if module == "__future__":
            # Compiler directives are not runtime module API accesses.
            return
        for alias in node.names:
            if alias.name == "*":
                self.record(
                    node, Value("unknown", reason=f"wildcard import from {module}"), "import"
                )
                continue
            value = Value("symbol", module, (alias.name,))
            self.record(node, value, "import")
            self.env[alias.asname or alias.name] = value

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.visit(node.value)
        if isinstance(node.ctx, ast.Load):
            self.record(node, self.evaluate(node), "attribute")

    def visit_Call(self, node: ast.Call) -> None:
        self.generic_visit(node)
        target = self.evaluate(node.func)
        positional = 0
        star = False
        double_star = False
        keywords = []
        for arg in node.args:
            if isinstance(arg, ast.Starred):
                if isinstance(arg.value, (ast.Tuple, ast.List)) and not any(
                    isinstance(item, ast.Starred) for item in arg.value.elts
                ):
                    positional += len(arg.value.elts)
                else:
                    star = True
            else:
                positional += 1
        for keyword in node.keywords:
            if keyword.arg is not None:
                keywords.append(keyword.arg)
            elif isinstance(keyword.value, ast.Dict) and all(
                isinstance(key, ast.Constant) and isinstance(key.value, str)
                for key in keyword.value.keys
            ):
                keywords.extend(key.value for key in keyword.value.keys)
            else:
                double_star = True
        self.record(node, target, "call", (positional, keywords, star, double_star))
        if target.module == "builtins" and target.parts in (("getattr",), ("hasattr",)):
            if len(node.args) >= 2:
                owner = self.evaluate(node.args[0])
                name = node.args[1]
                if isinstance(name, ast.Constant) and isinstance(name.value, str):
                    value = (
                        Value(
                            "method" if owner.kind == "instance" else "symbol",
                            owner.module,
                            (*owner.parts, name.value),
                        )
                        if owner.kind != "unknown"
                        else owner
                    )
                    if (
                        target.parts == ("hasattr",) or len(node.args) >= 3
                    ) and self.surface.lookup(value).status == "missing":
                        value = Value("unknown", reason="optional attribute probe has a fallback")
                    self.record(node, value, "reflective attribute")
                else:
                    self.record(
                        node,
                        Value("unknown", reason="dynamic attribute name"),
                        "reflective attribute",
                    )

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        value = self.evaluate(node.value)
        for target in node.targets:
            self.assign(target, value)
            if not isinstance(target, ast.Name):
                self.visit(target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value:
            self.visit(node.value)
        value = self.evaluate(node.value) if node.value else self.annotation(node.annotation)
        if value.kind == "unknown":
            value = self.annotation(node.annotation)
        self.assign(node.target, value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        left = self.evaluate(node.target)
        right = self.evaluate(node.value)
        self.assign(node.target, left if left == right else UNKNOWN)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self.assign(node.target, self.evaluate(node.value))

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        before = dict(self.env)
        self.assign(node.target, self.evaluate(node.iter).element or UNKNOWN)
        for statement in node.body:
            self.visit(statement)
        after = self.env
        self.env = {
            key: common([before.get(key, UNKNOWN), after.get(key, UNKNOWN)])
            for key in before.keys() | after.keys()
        }
        for statement in node.orelse:
            self.visit(statement)

    visit_AsyncFor = visit_For

    def constant(self, node: ast.AST) -> Any:
        """Read literals or inert native constants without evaluating source code."""
        try:
            return ast.literal_eval(node)
        except (ValueError, TypeError):
            binding = self.surface.lookup(self.evaluate(node))
            value = binding.runtime
            if type(value) in (str, bytes, int, float, bool, type(None)):
                return value
            return DEFAULT

    def condition(self, node: ast.AST) -> bool | None:
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left = self.constant(node.left)
            right = self.constant(node.comparators[0])
            if left is DEFAULT or right is DEFAULT:
                return None
            try:
                match node.ops[0]:
                    case ast.Eq():
                        return left == right
                    case ast.NotEq():
                        return left != right
                    case ast.In():
                        return left in right
                    case ast.NotIn():
                        return left not in right
            except TypeError:
                return None
        return None

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        before = dict(self.env)
        condition = self.condition(node.test)
        previous_inactive = self.inactive_branch
        self.inactive_branch = previous_inactive or condition is False
        for statement in node.body:
            self.visit(statement)
        left = self.env
        self.env = dict(before)
        self.inactive_branch = previous_inactive or condition is True
        for statement in node.orelse:
            self.visit(statement)
        right = self.env
        self.inactive_branch = previous_inactive
        self.env = {
            key: common([left.get(key, UNKNOWN), right.get(key, UNKNOWN)])
            for key in left.keys() | right.keys()
        }

    def visit_While(self, node: ast.While) -> None:
        self.visit_If(node)

    def visit_Try(self, node: ast.Try) -> None:
        before = dict(self.env)
        previous_optional = self.optional_imports
        for handler in node.handlers:
            types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
            if handler.type is None or any(
                isinstance(node, ast.Name)
                and node.id in ("ImportError", "ModuleNotFoundError", "Exception", "BaseException")
                for node in types
            ):
                self.optional_imports = True
        for statement in node.body:
            self.visit(statement)
        self.optional_imports = previous_optional
        for statement in node.orelse:
            self.visit(statement)
        branches = [self.env]
        for handler in node.handlers:
            self.env = dict(before)
            if handler.type:
                self.visit(handler.type)
            if handler.name:
                self.env[handler.name] = self.annotation(handler.type)
            for statement in handler.body:
                self.visit(statement)
            if handler.name:
                self.env[handler.name] = UNKNOWN
            branches.append(self.env)
        names = set().union(*(branch.keys() for branch in branches))
        self.env = {
            name: common([branch.get(name, UNKNOWN) for branch in branches]) for name in names
        }
        for statement in node.finalbody:
            self.visit(statement)

    visit_TryStar = visit_Try

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self.assign(target, UNKNOWN)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars:
                value = self.evaluate(item.context_expr)
                if value.module != "builtins" or value.parts not in (
                    ("text_file",),
                    ("binary_file",),
                ):
                    value = Value(
                        "unknown",
                        reason=("context manager __enter__ result is not statically resolved"),
                    )
                self.assign(item.optional_vars, value)
        for statement in node.body:
            self.visit(statement)

    visit_AsyncWith = visit_With

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for expression in [
            *node.decorator_list,
            *node.args.defaults,
            *(item for item in node.args.kw_defaults if item),
        ]:
            self.visit(expression)
        previous = self.env
        self.env = dict(previous)
        locals_ = LocalNames()
        for statement in node.body:
            locals_.visit(statement)
        for name in locals_.names - locals_.shared:
            self.env[name] = UNKNOWN
        args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        for arg in args:
            self.env[arg.arg] = self.annotation(arg.annotation)
            if arg.annotation:
                self.visit(arg.annotation)
        if node.returns:
            self.visit(node.returns)
        if args and self.class_value and args[0].arg in ("self", "cls"):
            self.env[args[0].arg] = Value(
                "instance" if args[0].arg == "self" else "symbol",
                self.class_value.module,
                self.class_value.parts,
                reason="polymorphic receiver",
            )
        if node.args.vararg:
            self.env[node.args.vararg.arg] = primitive("tuple")
        if node.args.kwarg:
            self.env[node.args.kwarg.arg] = primitive("dict", primitive("str"))
        for statement in node.body:
            self.visit(statement)
        self.env = previous

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for expression in [
            *node.bases,
            *node.decorator_list,
            *(keyword.value for keyword in node.keywords),
        ]:
            self.visit(expression)
        previous = self.env
        previous_class = self.class_value
        parts = (*previous_class.parts, node.name) if previous_class else (node.name,)
        self.class_value = Value("symbol", self.module, parts)
        self.env = dict(previous)
        for statement in node.body:
            self.visit(statement)
        self.env = previous
        self.class_value = previous_class

    def visit_Lambda(self, node: ast.Lambda) -> None:
        previous = self.env
        self.env = dict(previous)
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            self.env[arg.arg] = UNKNOWN
        self.visit(node.body)
        self.env = previous

    def comprehension(self, node: ast.AST) -> None:
        previous = self.env
        self.env = dict(previous)
        for generator in node.generators:
            self.visit(generator.iter)
            self.assign(generator.target, self.evaluate(generator.iter).element or UNKNOWN)
            for condition in generator.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        self.evaluate(node)
        self.env = previous

    visit_ListComp = comprehension
    visit_SetComp = comprehension
    visit_DictComp = comprehension
    visit_GeneratorExp = comprehension


def source_paths(root: Path) -> list[Path]:
    """Discover the entire package, including every current or future stdlib file."""
    return [*sorted((root / "src" / "diaspore").rglob("*.py")), root / "selfhost" / "dspc.py"]


def upstream_paths(root: Path) -> set[str]:
    """Classify only the exact installed source paths listed in the manifest."""
    manifest = root / "tools" / "stdlib_manifest.json"
    if not manifest.is_file():
        # Synthetic detector fixtures need not materialize a vendored library.
        # The repository entry-point test requires the real manifest below.
        return set()
    entries = json.loads(manifest.read_text(encoding="utf-8"))["modules"]
    return {str(Path("src/diaspore/cpython") / entry["path"]) for entry in entries}


def counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        status: sum(row["status"] == status for row in rows)
        for status in ("resolved", "gap", "unresolved")
    }


def gating_gaps(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row for row in report["uses"] if row["ownership"] == "maintained" and row["status"] == "gap"
    ]


def inventory(root: Path, paths: list[Path]) -> dict[str, Any]:
    index = Index(root, paths)
    surface = Surface(index)
    upstream = upstream_paths(root)
    rows = []
    for path in paths:
        scanner = Scanner(index, surface, path)
        scanner.visit(index.trees[path])
        ownership = "upstream" if str(path.relative_to(root)) in upstream else "maintained"
        for row in scanner.rows:
            row["ownership"] = ownership
        rows.extend(scanner.rows)
    rows.sort(key=lambda row: (row["file"], row["line"], row["column"], row["operation"]))
    scope = [str(path.relative_to(root)) for path in paths]
    groups = {}
    for ownership in ("maintained", "upstream"):
        selected = [row for row in rows if row["ownership"] == ownership]
        groups[ownership] = {
            "gating": ownership == "maintained",
            "scope": [path for path in scope if (path in upstream) == (ownership == "upstream")],
            "counts": counts(selected),
        }
    return {
        "python": sys.version,
        "runtime": "diaspore-py",
        "scope": scope,
        "uses": rows,
        "counts": counts(rows),
        "groups": groups,
        "ownership_manifest": "tools/stdlib_manifest.json",
    }


def test_source_api_parity(record_property) -> None:
    assert sys.version_info[:2] == (3, 12), "API oracle must be pinned to CPython 3.12"
    assert (ROOT / "tools" / "stdlib_manifest.json").is_file(), (
        "stdlib ownership manifest is required"
    )
    paths = source_paths(ROOT)
    report = inventory(ROOT, paths)
    destination = os.environ.get("DIASPORE_API_REPORT")
    report_path = Path(destination) if destination else ROOT / "build" / "api-parity.json"
    backlog_path = ROOT / "build" / "api-parity-upstream.json"
    assert report_path.resolve() != backlog_path.resolve(), (
        "DIASPORE_API_REPORT must not overwrite the separately retained upstream backlog"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    upstream = report["groups"]["upstream"]
    backlog = {key: report[key] for key in ("python", "runtime", "ownership_manifest")}
    backlog.update(upstream)
    backlog["uses"] = [row for row in report["uses"] if row["ownership"] == "upstream"]
    backlog_path.parent.mkdir(parents=True, exist_ok=True)
    backlog_path.write_text(json.dumps(backlog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    record_property("api_inventory", str(report_path))
    record_property("api_upstream_backlog", str(backlog_path))
    for status, count in report["counts"].items():
        record_property("api_" + status, count)
    for ownership, group in report["groups"].items():
        for status, count in group["counts"].items():
            record_property(f"api_{ownership}_{status}", count)
        print(
            f"API {ownership}: {len(group['scope'])} files; {group['counts']}; "
            f"gating={group['gating']}"
        )
    print(f"Full inventory: {report_path}; upstream backlog: {backlog_path}")
    gaps = gating_gaps(report)
    assert not gaps, "\n".join(
        [
            f"{len(gaps)} maintained-source API dependency gaps; unresolved uses are NOT passes.",
            f"Full inventory (including every unresolved location): {report_path}",
            f"Non-gating upstream source backlog: {backlog_path}",
            *[
                f"{row['file']}:{row['line']}:{row['column']}: {row['api']}: "
                f"{row['reason']} [source: {row['expression']}]"
                for row in gaps
            ],
        ]
    )


def test_detector_tracks_aliases_receivers_and_keywords(tmp_path: Path) -> None:
    package = tmp_path / "src" / "diaspore"
    library = package / "stdlib"
    library.mkdir(parents=True)
    provider = library / "example_api.py"
    provider.write_text(
        "class Factory:\n"
        "    def __init__(self, value=None): pass\n"
        "    def action(self, *, supported=False): pass\n",
        encoding="utf-8",
    )
    caller = package / "consumer.py"
    caller.write_text(
        "from example_api import Factory as Alias\n"
        "obj = Alias()\n"
        "obj.action(supported=True)\n"
        "obj.action(unsupported=True)\n"
        "obj.absent()\n"
        "names = ['x']\n"
        "for name in names:\n"
        "    name.lower()\n"
        "    name.format_map({})\n"
        "def dynamic(arg):\n"
        "    arg.unknown(keyword=True)\n"
        "obj.action(**kwargs)\n",
        encoding="utf-8",
    )
    report = inventory(tmp_path, [provider, caller])
    rows = [row for row in report["uses"] if row["file"].endswith("consumer.py")]
    assert any(
        row["line"] == 3 and row["operation"] == "call" and row["status"] == "resolved"
        for row in rows
    )
    assert any(
        row["line"] == 4 and row["status"] == "gap" and "unsupported" in row["reason"]
        for row in rows
    )
    assert any(row["line"] == 5 and row["status"] == "gap" for row in rows)
    assert any(
        row["line"] == 8 and row["operation"] == "call" and row["status"] == "resolved"
        for row in rows
    )
    assert any(row["line"] == 11 and row["status"] == "unresolved" for row in rows)
    assert any(row["line"] == 12 and row["status"] == "unresolved" for row in rows)


def test_detector_tracks_files_native_results_and_source_growth(tmp_path: Path) -> None:
    package = tmp_path / "src" / "diaspore"
    package.mkdir(parents=True)
    selfhost = tmp_path / "selfhost"
    selfhost.mkdir()
    (selfhost / "dspc.py").write_text("", encoding="utf-8")
    caller = package / "consumer.py"
    caller.write_text(
        "import math as codec\n"
        "import tasks\n"
        "codec.sqrt(missing_option=True)\n"
        "with open('never-opened', encoding='utf-8') as stream:\n"
        "    stream.read().lower()\n"
        "rng = tasks.lock()\n"
        "rng.not_an_api()\n"
        "def shadow(codec):\n"
        "    codec.sqrt(missing_option=True)\n",
        encoding="utf-8",
    )
    first = inventory(tmp_path, source_paths(tmp_path))
    assert any(row["line"] == 3 and row["status"] == "gap" for row in first["uses"])
    assert any(
        row["line"] == 5 and row["api"] == "builtins.str.lower" and row["status"] == "resolved"
        for row in first["uses"]
    )
    assert any(row["line"] == 7 and row["status"] == "gap" for row in first["uses"])
    assert any(row["line"] == 9 and row["status"] == "unresolved" for row in first["uses"])
    added = package / "new_dependency.py"
    added.write_text("import math\nmath.not_an_api()\n", encoding="utf-8")
    second = inventory(tmp_path, source_paths(tmp_path))
    assert len(second["scope"]) == len(first["scope"]) + 1
    assert any(row["api"] == "math.not_an_api" and row["status"] == "gap" for row in second["uses"])


def test_detector_resolves_relative_reexports_and_builtin_bases(tmp_path: Path) -> None:
    package = tmp_path / "src" / "diaspore"
    library = package / "stdlib" / "samplepkg"
    library.mkdir(parents=True)
    init = library / "__init__.py"
    init.write_text("from . import helpers\n", encoding="utf-8")
    helper = library / "helpers.py"
    helper.write_text(
        "def action(*, enabled=True): pass\nclass Mapping(dict): pass\n", encoding="utf-8"
    )
    caller = package / "consumer.py"
    caller.write_text(
        "import samplepkg\n"
        "from samplepkg.helpers import Mapping\n"
        "samplepkg.helpers.action(enabled=True)\n"
        "samplepkg.helpers.action(absent=True)\n"
        "mapping = Mapping()\n"
        "mapping.get('key')\n",
        encoding="utf-8",
    )
    report = inventory(tmp_path, [init, helper, caller])
    rows = [row for row in report["uses"] if row["file"].endswith("consumer.py")]
    assert any(
        row["line"] == 3 and row["operation"] == "call" and row["status"] == "resolved"
        for row in rows
    )
    assert any(row["line"] == 4 and row["status"] == "gap" for row in rows)
    assert any(
        row["line"] == 6 and row["operation"] == "call" and row["status"] == "resolved"
        for row in rows
    )


def test_ast_signatures_preserve_parameter_kinds() -> None:
    node = ast.parse("def f(a, /, b=0, *args, c, d=1, **kw): pass").body[0]
    signature = ast_signature(node.args)
    signature.bind(1, c=2)
    with pytest.raises(TypeError, match="positional[ -]only"):
        signature.bind(a=1, c=2)
    with pytest.raises(TypeError, match="missing"):
        signature.bind(1)


def test_manifest_ownership_keeps_maintained_gaps_gating(tmp_path: Path) -> None:
    package = tmp_path / "src" / "diaspore"
    library = package / "stdlib"
    library.mkdir(parents=True)
    tools = tmp_path / "tools"
    tools.mkdir()
    manifest = tools / "stdlib_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "modules": [
                    {
                        "name": "arbitrary_inventory_name",
                        "path": "dependency.py",
                        "source": "Lib/dependency.py",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    vendor = package / "cpython"
    vendor.mkdir()
    vendored = vendor / "dependency.py"
    same_basename = package / "dependency.py"
    adapter = library / "_io.py"
    source = "import math\nmath.not_an_api()\n"
    for path in (vendored, same_basename, adapter):
        path.write_text(source, encoding="utf-8")
    report = inventory(tmp_path, [vendored, same_basename, adapter])
    upstream = report["groups"]["upstream"]
    maintained = report["groups"]["maintained"]
    assert upstream["scope"] == ["src/diaspore/cpython/dependency.py"]
    assert set(maintained["scope"]) == {
        "src/diaspore/dependency.py",
        "src/diaspore/stdlib/_io.py",
    }
    assert upstream["gating"] is False
    assert maintained["gating"] is True
    assert upstream["counts"]["gap"] > 0
    assert maintained["counts"]["gap"] > 0
    gaps = gating_gaps(report)
    assert gaps
    assert {row["file"] for row in gaps} == set(maintained["scope"])
    assert all(row["ownership"] == "maintained" for row in gaps)
    assert any(row["ownership"] == "upstream" and row["status"] == "gap" for row in report["uses"])
    assert report["counts"]["gap"] == upstream["counts"]["gap"] + maintained["counts"]["gap"]


def test_detector_merges_conditional_aliases_and_dynamic_exports(tmp_path: Path) -> None:
    package = tmp_path / "src" / "diaspore"
    library = package / "stdlib"
    library.mkdir(parents=True)
    provider = library / "provider.py"
    provider.write_text(
        "import math as selected\n"
        "if unknown_platform:\n"
        "    import unavailable_backend as selected\n"
        "from provider.selected import pi\n"
        "try:\n"
        "    from absent_accelerator import action\n"
        "except ImportError:\n"
        "    def action(value): pass\n"
        "def static(value): pass\n",
        encoding="utf-8",
    )
    generated = library / "generated.py"
    generated.write_text(
        "for name in names:\n    globals()[name] = make_export(name)\n",
        encoding="utf-8",
    )
    caller = package / "consumer.py"
    caller.write_text(
        "import provider\n"
        "import generated\n"
        "provider.selected.sqrt(4)\n"
        "provider.pi\n"
        "provider.action(1)\n"
        "generated.digest(b'input')\n"
        "provider.static(unexpected=True)\n"
        "provider.misspelled()\n",
        encoding="utf-8",
    )
    report = inventory(tmp_path, [provider, generated, caller])
    rows = [row for row in report["uses"] if row["file"].endswith("consumer.py")]
    for line in (3, 4, 5, 6):
        uses = [row for row in rows if row["line"] == line]
        assert uses and all(row["status"] == "unresolved" for row in uses)
    for line in (7, 8):
        assert any(row["line"] == line and row["status"] == "gap" for row in rows)
    optional = [
        row for row in report["uses"] if row["file"].endswith("provider.py") and row["line"] == 6
    ]
    assert optional and all(row["status"] == "unresolved" for row in optional)


def test_detector_tracks_instance_writes_and_subclass_contracts(tmp_path: Path) -> None:
    library = tmp_path / "src" / "diaspore" / "stdlib"
    library.mkdir(parents=True)
    provider = library / "provider.py"
    provider.write_text(
        "class Base:\n"
        "    def method(self):\n"
        "        self.parameter\n"
        "        self.misspelled\n"
        "        self.__dict__.copy()\n"
        "        self.__class__.__name__\n"
        "class Derived(Base):\n"
        "    parameter = 4\n"
        "class State:\n"
        "    def __new__(cls):\n"
        "        result = object.__new__(cls)\n"
        "        object.__setattr__(result, 'data', 1)\n"
        "        return result\n"
        "    def read(self):\n"
        "        return self.data, self.extra\n"
        "def make_state():\n"
        "    value = object.__new__(State)\n"
        "    value.extra = 2\n"
        "    return value\n"
        "import math\n"
        "hasattr(math, 'absent')\n"
        "getattr(math, 'absent', None)\n"
        "getattr(math, 'absent')\n",
        encoding="utf-8",
    )
    rows = inventory(tmp_path, [provider])["uses"]
    for line in (3, 5, 6):
        uses = [row for row in rows if row["line"] == line]
        assert uses and all(row["status"] == "unresolved" for row in uses)
    assert any(row["line"] == 4 and row["status"] == "gap" for row in rows)
    fields = [row for row in rows if row["line"] == 15]
    assert fields and all(row["status"] == "resolved" for row in fields)
    for line in (21, 22):
        assert any(
            row["line"] == line
            and row["operation"] == "reflective attribute"
            and row["status"] == "unresolved"
            for row in rows
        )
    assert any(row["line"] == 23 and row["status"] == "gap" for row in rows)


def test_detector_retains_inactive_native_constant_branches(tmp_path: Path) -> None:
    library = tmp_path / "src" / "diaspore" / "stdlib"
    library.mkdir(parents=True)
    source = library / "platform_adapter.py"
    source.write_text(
        "import sys\n"
        "if sys.platform in {'not-a-diaspore-runtime'}:\n"
        "    import absent_platform_provider\n"
        "    absent_platform_provider.action()\n"
        "else:\n"
        "    import math\n"
        "    math.not_an_api()\n",
        encoding="utf-8",
    )
    rows = inventory(tmp_path, [source])["uses"]
    inactive = [row for row in rows if row["line"] in (3, 4)]
    assert inactive and all(row["status"] == "unresolved" for row in inactive)
    assert all("inactive" in row["reason"] for row in inactive)
    assert any(row["line"] == 7 and row["status"] == "gap" for row in rows)
