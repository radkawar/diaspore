"""AST node types and the portable parser interface used by builtin compile.

Only source positions retained by the parser are attached to parsed nodes.
Public traversal, transformation and rendering live in CPython's ast module.
"""

from _pyparse import CompileError as _CompileError
from _pyparse import Parser as _Parser
from _pyparse import tokenize as _tokenize

PyCF_ONLY_AST = 1024
PyCF_TYPE_COMMENTS = 4096
PyCF_ALLOW_TOP_LEVEL_AWAIT = 8192

__all__ = (
    "AST",
    "mod",
    "stmt",
    "expr",
    "expr_context",
    "boolop",
    "operator",
    "unaryop",
    "cmpop",
    "comprehension",
    "excepthandler",
    "arguments",
    "arg",
    "keyword",
    "alias",
    "withitem",
    "match_case",
    "pattern",
    "type_ignore",
    "type_param",
    "Module",
    "Interactive",
    "Expression",
    "FunctionType",
    "FunctionDef",
    "AsyncFunctionDef",
    "ClassDef",
    "Return",
    "Delete",
    "Assign",
    "TypeAlias",
    "AugAssign",
    "AnnAssign",
    "For",
    "AsyncFor",
    "While",
    "If",
    "With",
    "AsyncWith",
    "Match",
    "Raise",
    "Try",
    "TryStar",
    "Assert",
    "Import",
    "ImportFrom",
    "Global",
    "Nonlocal",
    "Expr",
    "Pass",
    "Break",
    "Continue",
    "BoolOp",
    "NamedExpr",
    "BinOp",
    "UnaryOp",
    "Lambda",
    "IfExp",
    "Dict",
    "Set",
    "ListComp",
    "SetComp",
    "DictComp",
    "GeneratorExp",
    "Await",
    "Yield",
    "YieldFrom",
    "Compare",
    "Call",
    "FormattedValue",
    "JoinedStr",
    "Constant",
    "Attribute",
    "Subscript",
    "Starred",
    "Name",
    "List",
    "Tuple",
    "Slice",
    "Load",
    "Store",
    "Del",
    "And",
    "Or",
    "Add",
    "Sub",
    "Mult",
    "MatMult",
    "Div",
    "Mod",
    "Pow",
    "LShift",
    "RShift",
    "BitOr",
    "BitXor",
    "BitAnd",
    "FloorDiv",
    "Invert",
    "Not",
    "UAdd",
    "USub",
    "Eq",
    "NotEq",
    "Lt",
    "LtE",
    "Gt",
    "GtE",
    "Is",
    "IsNot",
    "In",
    "NotIn",
    "ExceptHandler",
    "MatchValue",
    "MatchSingleton",
    "MatchSequence",
    "MatchMapping",
    "MatchClass",
    "MatchStar",
    "MatchAs",
    "MatchOr",
    "TypeIgnore",
    "TypeVar",
    "ParamSpec",
    "TypeVarTuple",
    "PyCF_ONLY_AST",
    "PyCF_TYPE_COMMENTS",
    "PyCF_ALLOW_TOP_LEVEL_AWAIT",
)


class AST:
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields
    _attributes = ()

    def __init__(self, *args, **kwargs):
        fields = type(self)._fields
        if len(args) > len(fields):
            suffix = "" if len(fields) == 1 else "s"
            raise TypeError(
                type(self).__name__
                + " constructor takes at most "
                + str(len(fields))
                + " positional argument"
                + suffix
            )
        for name, value in zip(fields, args):
            setattr(self, name, value)
        for name, value in kwargs.items():
            if name in fields and fields.index(name) < len(args):
                raise TypeError(
                    type(self).__name__ + " got multiple values for argument '" + name + "'"
                )
            setattr(self, name, value)

    def __reduce__(self):
        return type(self), (), self.__dict__


class mod(AST):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class stmt(AST):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class expr(AST):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class expr_context(AST):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class boolop(AST):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class operator(AST):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class unaryop(AST):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class cmpop(AST):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class comprehension(AST):
    __module__ = "ast"
    _fields = ("target", "iter", "ifs", "is_async")
    __match_args__ = _fields


class excepthandler(AST):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class arguments(AST):
    __module__ = "ast"
    _fields = ("posonlyargs", "args", "vararg", "kwonlyargs", "kw_defaults", "kwarg", "defaults")
    __match_args__ = _fields
    vararg = None
    kwarg = None


class arg(AST):
    __module__ = "ast"
    _fields = ("arg", "annotation", "type_comment")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    annotation = None
    type_comment = None
    end_lineno = None
    end_col_offset = None


class keyword(AST):
    __module__ = "ast"
    _fields = ("arg", "value")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    arg = None
    end_lineno = None
    end_col_offset = None


class alias(AST):
    __module__ = "ast"
    _fields = ("name", "asname")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    asname = None
    end_lineno = None
    end_col_offset = None


class withitem(AST):
    __module__ = "ast"
    _fields = ("context_expr", "optional_vars")
    __match_args__ = _fields
    optional_vars = None


class match_case(AST):
    __module__ = "ast"
    _fields = ("pattern", "guard", "body")
    __match_args__ = _fields
    guard = None


class pattern(AST):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")


class type_ignore(AST):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class type_param(AST):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")


class Module(mod):
    __module__ = "ast"
    _fields = ("body", "type_ignores")
    __match_args__ = _fields


class Interactive(mod):
    __module__ = "ast"
    _fields = ("body",)
    __match_args__ = _fields


class Expression(mod):
    __module__ = "ast"
    _fields = ("body",)
    __match_args__ = _fields


class FunctionType(mod):
    __module__ = "ast"
    _fields = ("argtypes", "returns")
    __match_args__ = _fields


class FunctionDef(stmt):
    __module__ = "ast"
    _fields = ("name", "args", "body", "decorator_list", "returns", "type_comment", "type_params")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    returns = None
    type_comment = None
    end_lineno = None
    end_col_offset = None


class AsyncFunctionDef(stmt):
    __module__ = "ast"
    _fields = ("name", "args", "body", "decorator_list", "returns", "type_comment", "type_params")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    returns = None
    type_comment = None
    end_lineno = None
    end_col_offset = None


class ClassDef(stmt):
    __module__ = "ast"
    _fields = ("name", "bases", "keywords", "body", "decorator_list", "type_params")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class Return(stmt):
    __module__ = "ast"
    _fields = ("value",)
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    value = None
    end_lineno = None
    end_col_offset = None


class Delete(stmt):
    __module__ = "ast"
    _fields = ("targets",)
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class Assign(stmt):
    __module__ = "ast"
    _fields = ("targets", "value", "type_comment")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    type_comment = None
    end_lineno = None
    end_col_offset = None


class TypeAlias(stmt):
    __module__ = "ast"
    _fields = ("name", "type_params", "value")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class AugAssign(stmt):
    __module__ = "ast"
    _fields = ("target", "op", "value")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class AnnAssign(stmt):
    __module__ = "ast"
    _fields = ("target", "annotation", "value", "simple")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    value = None
    end_lineno = None
    end_col_offset = None


class For(stmt):
    __module__ = "ast"
    _fields = ("target", "iter", "body", "orelse", "type_comment")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    type_comment = None
    end_lineno = None
    end_col_offset = None


class AsyncFor(stmt):
    __module__ = "ast"
    _fields = ("target", "iter", "body", "orelse", "type_comment")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    type_comment = None
    end_lineno = None
    end_col_offset = None


class While(stmt):
    __module__ = "ast"
    _fields = ("test", "body", "orelse")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class If(stmt):
    __module__ = "ast"
    _fields = ("test", "body", "orelse")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class With(stmt):
    __module__ = "ast"
    _fields = ("items", "body", "type_comment")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    type_comment = None
    end_lineno = None
    end_col_offset = None


class AsyncWith(stmt):
    __module__ = "ast"
    _fields = ("items", "body", "type_comment")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    type_comment = None
    end_lineno = None
    end_col_offset = None


class Match(stmt):
    __module__ = "ast"
    _fields = ("subject", "cases")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class Raise(stmt):
    __module__ = "ast"
    _fields = ("exc", "cause")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    exc = None
    cause = None
    end_lineno = None
    end_col_offset = None


class Try(stmt):
    __module__ = "ast"
    _fields = ("body", "handlers", "orelse", "finalbody")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class TryStar(stmt):
    __module__ = "ast"
    _fields = ("body", "handlers", "orelse", "finalbody")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class Assert(stmt):
    __module__ = "ast"
    _fields = ("test", "msg")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    msg = None
    end_lineno = None
    end_col_offset = None


class Import(stmt):
    __module__ = "ast"
    _fields = ("names",)
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class ImportFrom(stmt):
    __module__ = "ast"
    _fields = ("module", "names", "level")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    module = None
    level = None
    end_lineno = None
    end_col_offset = None


class Global(stmt):
    __module__ = "ast"
    _fields = ("names",)
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class Nonlocal(stmt):
    __module__ = "ast"
    _fields = ("names",)
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class Expr(stmt):
    __module__ = "ast"
    _fields = ("value",)
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class Pass(stmt):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class Break(stmt):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class Continue(stmt):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class BoolOp(expr):
    __module__ = "ast"
    _fields = ("op", "values")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class NamedExpr(expr):
    __module__ = "ast"
    _fields = ("target", "value")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class BinOp(expr):
    __module__ = "ast"
    _fields = ("left", "op", "right")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class UnaryOp(expr):
    __module__ = "ast"
    _fields = ("op", "operand")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class Lambda(expr):
    __module__ = "ast"
    _fields = ("args", "body")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class IfExp(expr):
    __module__ = "ast"
    _fields = ("test", "body", "orelse")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class Dict(expr):
    __module__ = "ast"
    _fields = ("keys", "values")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class Set(expr):
    __module__ = "ast"
    _fields = ("elts",)
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class ListComp(expr):
    __module__ = "ast"
    _fields = ("elt", "generators")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class SetComp(expr):
    __module__ = "ast"
    _fields = ("elt", "generators")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class DictComp(expr):
    __module__ = "ast"
    _fields = ("key", "value", "generators")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class GeneratorExp(expr):
    __module__ = "ast"
    _fields = ("elt", "generators")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class Await(expr):
    __module__ = "ast"
    _fields = ("value",)
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class Yield(expr):
    __module__ = "ast"
    _fields = ("value",)
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    value = None
    end_lineno = None
    end_col_offset = None


class YieldFrom(expr):
    __module__ = "ast"
    _fields = ("value",)
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class Compare(expr):
    __module__ = "ast"
    _fields = ("left", "ops", "comparators")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class Call(expr):
    __module__ = "ast"
    _fields = ("func", "args", "keywords")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class FormattedValue(expr):
    __module__ = "ast"
    _fields = ("value", "conversion", "format_spec")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    format_spec = None
    end_lineno = None
    end_col_offset = None


class JoinedStr(expr):
    __module__ = "ast"
    _fields = ("values",)
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class Constant(expr):
    __module__ = "ast"
    _fields = ("value", "kind")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    kind = None
    end_lineno = None
    end_col_offset = None


class Attribute(expr):
    __module__ = "ast"
    _fields = ("value", "attr", "ctx")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class Subscript(expr):
    __module__ = "ast"
    _fields = ("value", "slice", "ctx")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class Starred(expr):
    __module__ = "ast"
    _fields = ("value", "ctx")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class Name(expr):
    __module__ = "ast"
    _fields = ("id", "ctx")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class List(expr):
    __module__ = "ast"
    _fields = ("elts", "ctx")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class Tuple(expr):
    __module__ = "ast"
    _fields = ("elts", "ctx")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    end_lineno = None
    end_col_offset = None


class Slice(expr):
    __module__ = "ast"
    _fields = ("lower", "upper", "step")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    lower = None
    upper = None
    step = None
    end_lineno = None
    end_col_offset = None


class Load(expr_context):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class Store(expr_context):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class Del(expr_context):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class And(boolop):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class Or(boolop):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class Add(operator):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class Sub(operator):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class Mult(operator):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class MatMult(operator):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class Div(operator):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class Mod(operator):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class Pow(operator):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class LShift(operator):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class RShift(operator):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class BitOr(operator):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class BitXor(operator):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class BitAnd(operator):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class FloorDiv(operator):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class Invert(unaryop):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class Not(unaryop):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class UAdd(unaryop):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class USub(unaryop):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class Eq(cmpop):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class NotEq(cmpop):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class Lt(cmpop):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class LtE(cmpop):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class Gt(cmpop):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class GtE(cmpop):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class Is(cmpop):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class IsNot(cmpop):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class In(cmpop):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class NotIn(cmpop):
    __module__ = "ast"
    _fields = ()
    __match_args__ = _fields


class ExceptHandler(excepthandler):
    __module__ = "ast"
    _fields = ("type", "name", "body")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    type = None
    name = None
    end_lineno = None
    end_col_offset = None


class MatchValue(pattern):
    __module__ = "ast"
    _fields = ("value",)
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")


class MatchSingleton(pattern):
    __module__ = "ast"
    _fields = ("value",)
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")


class MatchSequence(pattern):
    __module__ = "ast"
    _fields = ("patterns",)
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")


class MatchMapping(pattern):
    __module__ = "ast"
    _fields = ("keys", "patterns", "rest")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    rest = None


class MatchClass(pattern):
    __module__ = "ast"
    _fields = ("cls", "patterns", "kwd_attrs", "kwd_patterns")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")


class MatchStar(pattern):
    __module__ = "ast"
    _fields = ("name",)
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    name = None


class MatchAs(pattern):
    __module__ = "ast"
    _fields = ("pattern", "name")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    pattern = None
    name = None


class MatchOr(pattern):
    __module__ = "ast"
    _fields = ("patterns",)
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")


class TypeIgnore(type_ignore):
    __module__ = "ast"
    _fields = ("lineno", "tag")
    __match_args__ = _fields


class TypeVar(type_param):
    __module__ = "ast"
    _fields = ("name", "bound")
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    bound = None


class ParamSpec(type_param):
    __module__ = "ast"
    _fields = ("name",)
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")


class TypeVarTuple(type_param):
    __module__ = "ast"
    _fields = ("name",)
    __match_args__ = _fields
    _attributes = ("lineno", "col_offset", "end_lineno", "end_col_offset")


_OPERATORS = {
    "add": Add,
    "sub": Sub,
    "mul": Mult,
    "div": Div,
    "floordiv": FloorDiv,
    "mod": Mod,
    "pow": Pow,
    "lshift": LShift,
    "rshift": RShift,
    "bitand": BitAnd,
    "bitor": BitOr,
    "bitxor": BitXor,
    "matmul": MatMult,
    "neg": USub,
    "pos": UAdd,
    "not": Not,
    "invert": Invert,
    "and": And,
    "or": Or,
    "eq": Eq,
    "ne": NotEq,
    "lt": Lt,
    "le": LtE,
    "gt": Gt,
    "ge": GtE,
    "in": In,
    "notin": NotIn,
    "is": Is,
    "isnot": IsNot,
}

_OPERATOR_NODES = {name: cls() for name, cls in _OPERATORS.items()}
_CONTEXT_NODES = {Load: Load(), Store: Store(), Del: Del()}


def _positions(node, data):
    if "ln" in data and "lineno" in node._attributes:
        node.lineno = data["ln"]
    for name in node._attributes:
        if name in data:
            setattr(node, name, data[name])
    return node


def _context(node, context):
    if isinstance(node, (Name, Attribute, Subscript, Starred, List, Tuple)):
        node.ctx = _CONTEXT_NODES[context]
        if isinstance(node, (List, Tuple)):
            for child in node.elts:
                _context(child, context)
        elif isinstance(node, Starred):
            _context(node.value, context)
    return node


def _argument(name, params):
    if name is None:
        return None
    data = params["argnodes"][name]
    return _positions(
        arg(
            arg=name, annotation=_convert(data["annotation"]), type_comment=data.get("type_comment")
        ),
        data,
    )


def _arguments(params):
    args = [_argument(name, params) for name in params["args"]]
    count = params["posonly"]
    return arguments(
        posonlyargs=args[:count],
        args=args[count:],
        vararg=_argument(params["vararg"], params),
        kwonlyargs=[_argument(name, params) for name in params["kwonly"]],
        kw_defaults=_convert(params["kwdefaults"]),
        kwarg=_argument(params["kwarg"], params),
        defaults=_convert(params["defaults"]),
    )


def _keywords(items):
    return [_positions(keyword(arg=item["n"], value=_convert(item["v"])), item) for item in items]


def _fparts(parts):
    values = []
    for part in parts:
        if "lit" in part:
            if part["lit"] == "":
                continue
            node = Constant(value=part["lit"], kind=part.get("prefix"))
        else:
            conv = -1 if part["conv"] is None else ord(part["conv"])
            node = FormattedValue(
                value=_convert(part["v"]), conversion=conv, format_spec=_convert(part["spec"])
            )
        values.append(_positions(node, part))
    return values


def _convert(data):
    if data is None:
        return None
    if isinstance(data, list):
        return [_convert(child) for child in data]
    tag = data["t"]
    if tag == "Module":
        return Module(body=_convert(data["body"]), type_ignores=[])
    if tag == "Const":
        value = data["v"]
        if data["kind"] == "complex":
            value = complex(0, value)
        elif data["kind"] == "ellipsis":
            value = ...
        node = Constant(value=value, kind=data.get("prefix"))
    elif tag == "Name":
        node = Name(id=data["id"], ctx=_CONTEXT_NODES[Load])
    elif tag in ("List", "Tuple", "Set"):
        cls = {"List": List, "Tuple": Tuple, "Set": Set}[tag]
        node = cls(elts=_convert(data["elts"]))
        if tag != "Set":
            node.ctx = _CONTEXT_NODES[Load]
    elif tag == "Dict":
        node = Dict(
            keys=[_convert(item["k"]) for item in data["entries"]],
            values=[_convert(item["v"]) for item in data["entries"]],
        )
    elif tag == "Attr":
        node = Attribute(value=_convert(data["o"]), attr=data["n"], ctx=_CONTEXT_NODES[Load])
    elif tag == "Sub":
        node = Subscript(
            value=_convert(data["o"]), slice=_convert(data["k"]), ctx=_CONTEXT_NODES[Load]
        )
    elif tag == "Slice":
        node = Slice(
            lower=_convert(data["lower"]),
            upper=_convert(data["upper"]),
            step=_convert(data["step"]),
        )
    elif tag == "Starred":
        node = Starred(value=_convert(data["v"]), ctx=_CONTEXT_NODES[Load])
    elif tag == "BinOp":
        node = BinOp(
            left=_convert(data["l"]), op=_OPERATOR_NODES[data["kind"]], right=_convert(data["r"])
        )
    elif tag == "Unary":
        node = UnaryOp(op=_OPERATOR_NODES[data["kind"]], operand=_convert(data["v"]))
    elif tag == "Bool":
        node = BoolOp(op=_OPERATOR_NODES[data["kind"]], values=_convert(data["values"]))
    elif tag == "Cmp":
        node = Compare(
            left=_convert(data["left"]),
            ops=[_OPERATOR_NODES[op] for op in data["ops"]],
            comparators=_convert(data["comps"]),
        )
    elif tag == "Cond":
        node = IfExp(
            test=_convert(data["test"]), body=_convert(data["then"]), orelse=_convert(data["else"])
        )
    elif tag == "Call":
        node = Call(
            func=_convert(data["f"]), args=_convert(data["args"]), keywords=_keywords(data["kw"])
        )
    elif tag == "Named":
        node = NamedExpr(
            target=_context(_convert(data["target"]), Store), value=_convert(data["v"])
        )
    elif tag == "Lambda":
        node = Lambda(args=_arguments(data["params"]), body=_convert(data["body"]))
    elif tag == "Fstr":
        node = JoinedStr(values=_fparts(data["parts"]))
    elif tag == "Comp":
        generators = []
        for gen in data["gens"]:
            generators.append(
                comprehension(
                    target=_context(_convert(gen["target"]), Store),
                    iter=_convert(gen["iter"]),
                    ifs=_convert(gen["ifs"]),
                    is_async=gen.get("async", 0),
                )
            )
        if data["kind"] == "dict":
            node = DictComp(
                key=_convert(data["key"]), value=_convert(data["val"]), generators=generators
            )
        else:
            cls = {"list": ListComp, "set": SetComp, "gen": GeneratorExp}[data["kind"]]
            node = cls(elt=_convert(data["elt"]), generators=generators)
    elif tag in ("Expr", "Return", "Yield", "YieldFrom", "Await"):
        cls = {
            "Expr": Expr,
            "Return": Return,
            "Yield": Yield,
            "YieldFrom": YieldFrom,
            "Await": Await,
        }[tag]
        node = cls(value=_convert(data["v"]))
    elif tag in ("Pass", "Break", "Continue"):
        node = {"Pass": Pass, "Break": Break, "Continue": Continue}[tag]()
    elif tag in ("Global", "Nonlocal"):
        node = {"Global": Global, "Nonlocal": Nonlocal}[tag](names=data["names"])
    elif tag == "Assign":
        node = Assign(
            targets=[_context(_convert(t), Store) for t in data["targets"]],
            value=_convert(data["v"]),
            type_comment=None,
        )
    elif tag == "Ann":
        node = AnnAssign(
            target=_context(_convert(data["target"]), Store),
            annotation=_convert(data["annotation"]),
            value=_convert(data["v"]),
            simple=int(data["simple"]),
        )
    elif tag == "Aug":
        node = AugAssign(
            target=_context(_convert(data["target"]), Store),
            op=_OPERATOR_NODES[data["kind"]],
            value=_convert(data["v"]),
        )
    elif tag == "Del":
        node = Delete(targets=[_context(_convert(t), Del) for t in data["targets"]])
    elif tag in ("If", "While"):
        cls = If if tag == "If" else While
        node = cls(
            test=_convert(data["test"]),
            body=_convert(data["then"] if tag == "If" else data["body"]),
            orelse=_convert(data["orelse"]),
        )
    elif tag in ("For", "AsyncFor"):
        cls = For if tag == "For" else AsyncFor
        node = cls(
            target=_context(_convert(data["target"]), Store),
            iter=_convert(data["iter"]),
            body=_convert(data["body"]),
            orelse=_convert(data["orelse"]),
            type_comment=None,
        )
    elif tag == "Func":
        cls = AsyncFunctionDef if data["coro"] else FunctionDef
        node = cls(
            name=data["name"],
            args=_arguments(data["params"]),
            body=_convert(data["body"]),
            decorator_list=_convert(data["decorators"]),
            returns=_convert(data["returns"]),
            type_comment=None,
            type_params=_convert(data.get("type_params", [])),
        )
    elif tag == "Class":
        node = ClassDef(
            name=data["name"],
            bases=_convert(data["bases"]),
            keywords=_keywords(data["keywords"]),
            body=_convert(data["body"]),
            decorator_list=_convert(data["decorators"]),
            type_params=_convert(data.get("type_params", [])),
        )
    elif tag == "TypeAlias":
        node = TypeAlias(
            name=_context(_convert(data["name"]), Store),
            type_params=_convert(data["type_params"]),
            value=_convert(data["v"]),
        )
    elif tag == "TypeVar":
        node = TypeVar(name=data["name"], bound=_convert(data["bound"]))
    elif tag == "ParamSpec":
        node = ParamSpec(name=data["name"])
    elif tag == "TypeVarTuple":
        node = TypeVarTuple(name=data["name"])
    elif tag == "Raise":
        node = Raise(exc=_convert(data["exc"]), cause=_convert(data["cause"]))
    elif tag == "Assert":
        node = Assert(test=_convert(data["test"]), msg=_convert(data["msg"]))
    elif tag == "Try":
        handlers = []
        for handler in data["handlers"]:
            handlers.append(
                _positions(
                    ExceptHandler(
                        type=_convert(handler["type"]),
                        name=handler["name"],
                        body=_convert(handler["body"]),
                    ),
                    handler,
                )
            )
        cls = TryStar if data["star"] else Try
        node = cls(
            body=_convert(data["body"]),
            handlers=handlers,
            orelse=_convert(data["orelse"]),
            finalbody=_convert(data["final"]),
        )
    elif tag in ("With", "AsyncWith"):
        items = []
        for item in data["items"]:
            items.append(
                withitem(
                    context_expr=_convert(item["ctx"]),
                    optional_vars=_context(_convert(item["vars"]), Store),
                )
            )
        cls = With if tag == "With" else AsyncWith
        node = cls(items=items, body=_convert(data["body"]), type_comment=None)
    elif tag in ("Import", "ImportFrom"):
        names = [
            _positions(alias(name=item["n"], asname=item["as"]), item) for item in data["names"]
        ]
        if tag == "Import":
            node = Import(names=names)
        else:
            node = ImportFrom(module=data["module"], names=names, level=data["level"])
    elif tag == "Match":
        cases = []
        for case in data["cases"]:
            cases.append(
                match_case(
                    pattern=_convert(case["pattern"]),
                    guard=_convert(case["guard"]),
                    body=_convert(case["body"]),
                )
            )
        node = Match(subject=_convert(data["subject"]), cases=cases)
    elif tag == "MVal":
        node = MatchValue(value=_convert(data["v"]))
    elif tag == "MSing":
        node = MatchSingleton(value=data["c"]["v"])
    elif tag == "MAs":
        node = MatchAs(pattern=_convert(data["pat"]), name=data["name"])
    elif tag == "MStar":
        node = MatchStar(name=data["name"])
    elif tag in ("MOr", "MSeq"):
        cls = MatchOr if tag == "MOr" else MatchSequence
        node = cls(patterns=_convert(data["pats"]))
    elif tag == "MMap":
        node = MatchMapping(
            keys=_convert(data["keys"]), patterns=_convert(data["pats"]), rest=data["rest"]
        )
    elif tag == "MCls":
        node = MatchClass(
            cls=_convert(data["cls"]),
            patterns=_convert(data["pos"]),
            kwd_attrs=data["attrs"],
            kwd_patterns=_convert(data["kpats"]),
        )
    else:
        raise NotImplementedError("AST node " + tag)
    return _positions(node, data)


def _integer(value):
    if isinstance(value, int):
        return value
    method = getattr(type(value), "__index__", None)
    if method is None:
        raise TypeError("'" + type(value).__name__ + "' object cannot be interpreted as an integer")
    result = method(value)
    if not isinstance(result, int):
        raise TypeError("__index__ returned non-int (type " + type(result).__name__ + ")")
    return result


def _source_text(source):
    if isinstance(source, str):
        return source
    if not isinstance(source, (bytes, bytearray)):
        raise TypeError("compile() arg 1 must be a string, bytes or AST object")
    source = bytes(source)
    bom = source.startswith(b"\xef\xbb\xbf")
    if bom:
        source = source[3:]
    encoding = "utf-8"
    for line in source.split(b"\n", 2)[:2]:
        text = line.decode("latin-1").lstrip(" \t\f")
        if text and not text.startswith("#") and text != "\r":
            break
        if not text.startswith("#"):
            continue
        start = text.find("coding")
        while start >= 0:
            end = start + 6
            if end < len(text) and text[end] in ":=":
                end += 1
                while end < len(text) and text[end] in " \t":
                    end += 1
                start = end
                while end < len(text) and (
                    "a" <= text[end] <= "z"
                    or "A" <= text[end] <= "Z"
                    or "0" <= text[end] <= "9"
                    or text[end] in "-_."
                ):
                    end += 1
                if end > start:
                    encoding = text[start:end]
                    break
            start = text.find("coding", end)
        if start >= 0:
            break
    normalized = encoding.lower().replace("_", "-")
    if bom and normalized not in ("utf-8", "utf8") and not normalized.startswith("utf-8-"):
        raise SyntaxError("encoding problem: " + encoding + " with BOM")
    try:
        return source.decode(encoding)
    except LookupError:
        raise SyntaxError("unknown encoding: " + encoding) from None
    except UnicodeDecodeError as error:
        raise SyntaxError(str(error)) from None


def _function_type(parser):
    parser.req_op("(")
    args = []
    starred = False
    if not parser.at_op(")"):
        while True:
            if parser.eat_op("**"):
                args.append(_convert(parser.test()))
                break
            if starred:
                raise parser.perr("invalid syntax")
            starred = parser.eat_op("*")
            args.append(_convert(parser.test()))
            if not parser.eat_op(","):
                break
    parser.req_op(")")
    parser.req_op("->")
    return FunctionType(argtypes=args, returns=_convert(parser.test()))


def _compile(
    source, filename, mode, flags=0, dont_inherit=False, optimize=-1, *, _feature_version=-1
):
    """Return VM AST objects for the runtime's PyCF_ONLY_AST compile branch."""
    flags = _integer(flags)
    _integer(dont_inherit)
    optimize = _integer(optimize)
    version = _integer(_feature_version)
    if flags & ~0x1FE7610:
        raise ValueError("compile(): unrecognised flags")
    if optimize < -1 or optimize > 2:
        raise ValueError("compile(): invalid optimize value")
    if not isinstance(mode, str):
        raise TypeError("compile() argument 'mode' must be str")
    if mode not in ("exec", "eval", "single", "func_type"):
        raise ValueError("compile() mode must be 'exec', 'eval', 'single' or 'func_type'")
    if not flags & PyCF_ONLY_AST:
        raise ValueError("_ast._compile requires PyCF_ONLY_AST")
    if isinstance(source, AST):
        return source
    if flags & PyCF_TYPE_COMMENTS:
        raise NotImplementedError("the parser does not retain type comments")
    if 0 <= version < 12:
        raise NotImplementedError("the parser does not select historical Python grammars")
    if flags & (0x0200 | 0x4000 | 0x400000):
        raise NotImplementedError("the parser does not support interactive or Barry grammar flags")
    source = _source_text(source)
    if "\x00" in source:
        raise SyntaxError("source code string cannot contain null bytes")
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    try:
        parser = _Parser(_tokenize(source, filename), filename)
        while parser.kind() == "nl":
            parser.adv()
        if mode == "exec":
            return _convert(parser.parse_module())
        if mode == "eval":
            tree = Expression(body=_convert(parser.testlist_star()))
        elif mode == "single":
            tree = Interactive(body=_convert(parser.parse_line()))
            single_end_line = parser.toks[parser.i - 1][2]
        else:
            tree = _function_type(parser)
        while parser.kind() == "nl":
            parser.adv()
        if parser.kind() != "end":
            if mode == "single":
                raise _CompileError(
                    "multiple statements found while compiling a single statement",
                    filename,
                    single_end_line,
                )
            raise parser.perr("unexpected token")
        return tree
    except _CompileError as error:
        exc = SyntaxError(error.msg.removeprefix("syntax error: "))
        exc.filename = filename
        exc.lineno = error.line
        lines = source.split("\n")
        if 1 <= error.line <= len(lines):
            exc.text = lines[error.line - 1] + "\n"
        raise exc from None
