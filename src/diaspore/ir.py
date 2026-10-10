"""Define Pydantic models for the Diaspore JSON IR in docs/spec.md.

These models support typed validation and JSON Schema generation. Runtime
validation uses the dependency-free diaspore.validator module.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_serializer, model_validator

BinKind = Literal[
    "add",
    "sub",
    "mul",
    "div",
    "floordiv",
    "mod",
    "pow",
    "lshift",
    "rshift",
    "bitand",
    "bitor",
    "bitxor",
    "matmul",
]
CmpKind = Literal["eq", "ne", "lt", "le", "gt", "ge", "in", "notin", "is", "isnot"]
CodePoint = Annotated[int, Field(strict=True, ge=0, le=0x10FFFF)]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class _N(_M):
    ln: int | None = None


class Const(_N):
    """A typed scalar constant."""

    op: Literal["const"]
    t: Literal["int", "float", "str", "bytes", "bool", "none", "ellipsis", "complex"]
    v: str | bool | None = None
    re: str | None = None
    im: str | None = None
    codepoints: list[CodePoint] = []

    @model_validator(mode="after")
    def _check_codepoints(self):
        if "codepoints" in self.model_fields_set and (self.t != "str" or not isinstance(self.v, str)):
            raise ValueError("codepoints requires a string constant")
        return self


class NameExpr(_N):
    """A variable lookup."""

    op: Literal["name"]
    id: str


class Attr(_N):
    """An attribute lookup."""

    op: Literal["attr"]
    o: Node
    n: str


class Index(_N):
    """A subscript lookup."""

    op: Literal["index"]
    o: Node
    k: Node


class SliceLit(_N):
    """A slice with optional bounds and step."""

    op: Literal["slicelit"]
    lower: Node | None = None
    upper: Node | None = None
    step: Node | None = None


class Starred(_N):
    """An iterable unpacking expression."""

    op: Literal["starred"]
    v: Node


class KwArg(_M):
    """A keyword argument, with a null name for mapping unpacking."""

    n: str | None
    v: Node


class Call(_N):
    """A function call with positional and keyword arguments."""

    op: Literal["call"]
    f: Node
    args: list[Node] = []
    kw: list[KwArg] = []


class BinOp(_N):
    """A binary arithmetic or bitwise operation."""

    op: Literal["binop"]
    kind: BinKind
    l: Node
    r: Node


class Unary(_N):
    """A unary operation."""

    op: Literal["unary"]
    kind: Literal["neg", "pos", "not", "invert"]
    v: Node


class BoolOp(_N):
    """A short-circuit Boolean operation."""

    op: Literal["boolop"]
    kind: Literal["and", "or"]
    values: list[Node] = Field(min_length=2)


class Compare(_N):
    """A comparison chain."""

    op: Literal["compare"]
    left: Node
    ops: list[CmpKind] = Field(min_length=1)
    comps: list[Node] = Field(min_length=1)


class CondExpr(_N):
    """A conditional expression."""

    op: Literal["cond"]
    test: Node
    then: Node
    else_: Node = Field(alias="else")


class ListExpr(_N):
    """A list display."""

    op: Literal["list"]
    elts: list[Node] = []


class TupleExpr(_N):
    """A tuple display."""

    op: Literal["tuple"]
    elts: list[Node] = []


class SetExpr(_N):
    """A set display."""

    op: Literal["set"]
    elts: list[Node] = []


class DictEntry(_M):
    """A dictionary entry, with a null key for mapping unpacking."""

    k: Node | None
    v: Node


class DictExpr(_N):
    """A dictionary display."""

    op: Literal["dict"]
    entries: list[DictEntry] = []


class FStrText(_M):
    """A literal segment of a formatted string."""

    s: str
    codepoints: list[CodePoint] = []


class FStrValue(_M):
    """A formatted string value with optional conversion and format spec."""

    v: Node
    conv: Literal["r", "s", "a"] | None = None
    spec: Node | None = None


class FString(_N):
    """A formatted string assembled from literal and value segments."""

    op: Literal["fstring"]
    parts: list[FStrText | FStrValue]


class Params(_M):
    """Function parameters and their default expressions."""

    args: list[str] = []
    posonly: int = Field(default=0, ge=0, strict=True)
    defaults: list[Node] = []
    vararg: str | None = None
    kwonly: list[str] = []
    kwdefaults: list[Node | None] = []
    kwarg: str | None = None

    @model_validator(mode="after")
    def _posonly_within_args(self) -> Params:
        if self.posonly > len(self.args):
            raise ValueError("posonly exceeds positional parameter count")
        return self


class Func(_N):
    """A function definition or lambda expression."""

    op: Literal["func", "funcexpr"]
    name: str
    gen: bool = False
    coro: bool = False
    asyncgen: bool = False
    annotation_scope: bool = False
    params: Params
    locals: list[str] = []
    gdecl: list[str] = []
    ndecl: list[str] = []
    body: list[Node]


class Yield(_N):
    """A generator yield expression."""

    op: Literal["yield"]
    v: Node | None = None


class YieldFrom(_N):
    """A delegated generator yield expression."""

    op: Literal["yieldfrom"]
    v: Node


class NamedExpr(_N):
    """An assignment expression."""

    op: Literal["namedexpr"]
    target: NameExpr
    v: Node


class ExprStmt(_N):
    """An expression evaluated for its side effects."""

    op: Literal["expr"]
    v: Node


class Assign(_N):
    """An assignment to one or more targets."""

    op: Literal["assign"]
    targets: list[Node] = Field(min_length=1)
    v: Node


class AugAssign(_N):
    """An augmented assignment."""

    op: Literal["augassign"]
    target: Node
    kind: BinKind
    v: Node


class If(_N):
    """A conditional statement."""

    op: Literal["if"]
    test: Node
    then: list[Node]
    orelse: list[Node] = []


class While(_N):
    """A while loop with an optional else block."""

    op: Literal["while"]
    test: Node
    body: list[Node]
    orelse: list[Node] = []


class For(_N):
    """An iterator loop with an optional else block."""

    op: Literal["for"]
    target: Node
    iter: Node
    body: list[Node]
    orelse: list[Node] = []


class Break(_N):
    """A statement that exits the enclosing loop."""

    op: Literal["break"]


class Continue(_N):
    """A statement that advances the enclosing loop."""

    op: Literal["continue"]


class Pass(_N):
    """A statement with no effect."""

    op: Literal["pass"]


class Return(_N):
    """A function return with an optional value."""

    op: Literal["return"]
    v: Node | None = None


class ClassDef(_N):
    """A class definition with bases and metaclass keyword arguments."""

    op: Literal["class"]
    name: str
    binding: str = Field(
        default=None,
        strict=True,
        json_schema_extra=lambda schema: schema.pop("default", None),
    )
    bases: list[Node] = []
    body: list[Node]
    kwargs: list[KwArg] = []


class Raise(_N):
    """An exception raise or reraise with an optional cause."""

    op: Literal["raise"]
    exc: Node | None = None
    cause: Node | None = None


class Handler(_M):
    """An exception handler with an optional type and binding."""

    t: Node | None = None
    name: str | None = None
    body: list[Node]


class Try(_N):
    """An exception-handling statement with optional cleanup."""

    op: Literal["try"]
    body: list[Node]
    handlers: list[Handler] = []
    orelse: list[Node] = []
    finalbody: list[Node] = []
    star: bool = False


class WithItem(_M):
    """A context manager expression and optional assignment target."""

    ctx: Node
    name: Node | None = None


class With(_N):
    """A context-management statement."""

    op: Literal["with"]
    items: list[WithItem]
    body: list[Node]


class Import(_N):
    """A module import with optional alias and binding mode."""

    op: Literal["import"]
    module: str
    alias: str | None = None
    bind: Literal["root", "none"] | None = None


class ImportName(_M):
    """A name imported from a module and its local alias."""

    n: str
    alias: str


class ImportFrom(_N):
    """An import of selected names from a module."""

    op: Literal["importfrom"]
    module: str
    names: list[ImportName]


class Del(_N):
    """A deletion of one or more targets."""

    op: Literal["del"]
    targets: list[Node]


Node = Annotated[
    Const
    | NameExpr
    | Attr
    | Index
    | SliceLit
    | Starred
    | Call
    | BinOp
    | Unary
    | BoolOp
    | Compare
    | CondExpr
    | ListExpr
    | TupleExpr
    | SetExpr
    | DictExpr
    | FString
    | Func
    | Yield
    | YieldFrom
    | NamedExpr
    | ExprStmt
    | Assign
    | AugAssign
    | If
    | While
    | For
    | Break
    | Continue
    | Pass
    | Return
    | ClassDef
    | Raise
    | Try
    | With
    | Import
    | ImportFrom
    | Del,
    Field(discriminator="op"),
]


class Module(_M):
    """A compiled module with optional source lines for tracebacks."""

    name: str
    file: str
    body: list[Node]
    src: list[str] | None = None


class Bundle(_M):
    """A versioned collection of modules and its entry point."""

    diaspore: Literal[1]
    main: str
    modules: dict[str, Module]
    # An omitted reference is standalone; an explicit null is not a reference.
    stdlib: str = Field(
        default=None,
        strict=True,
        min_length=71,
        max_length=71,
        pattern=r"^sha256:[0-9a-f]{64}$",
        json_schema_extra=lambda schema: schema.pop("default", None),
    )

    @model_serializer(mode="wrap")
    def _serialize(self, handler):
        data = handler(self)
        if self.stdlib is None:
            data.pop("stdlib", None)
        return data

    @model_validator(mode="after")
    def _main_exists(self) -> Bundle:
        if self.main not in self.modules:
            raise ValueError("main module missing from modules")
        return self


for _model in list(globals().values()):
    if isinstance(_model, type) and issubclass(_model, BaseModel) and _model.__module__ == __name__:
        _model.model_rebuild()

_bundle_adapter: TypeAdapter[Bundle] = TypeAdapter(Bundle)


def validate_bundle(data: object) -> Bundle:
    """Validate untrusted bundle data against the typed IR.

    Args:
        data: Decoded bundle data.

    Returns:
        The validated bundle model.

    Raises:
        pydantic.ValidationError: The data violates the bundle schema.
    """
    return _bundle_adapter.validate_python(data)


def bundle_schema() -> dict:
    """Generate the canonical IR JSON Schema.

    Run `make schema` to write the generated schema to build/ir.schema.json.

    Returns:
        The JSON Schema with generation instructions and a bundle title.
    """
    schema = _bundle_adapter.json_schema()
    schema["$comment"] = (
        "Generated from src/diaspore/ir.py (Pydantic models). "
        "Regenerate with `make schema` or "
        "`uv run diaspore schema -o build/ir.schema.json`."
    )
    schema["title"] = "Diaspore IR bundle (v1)"
    return schema
