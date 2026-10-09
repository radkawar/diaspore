"""Python syntax trees backed by the shared subset parser.

Parsing accepts the syntax supported by the Diaspore compiler. Source locations
include line numbers retained by the parser; columns and end positions are not
synthesized. Type comments and grammar-version selection are not supported.
"""

from _pyparse import CompileError, Parser, tokenize


class AST:
    _fields = ()
    _attributes = ()

    def __init__(self, *args, **kwargs):
        if len(args) > len(self._fields):
            raise TypeError("too many positional arguments")
        for name, value in zip(self._fields, args):
            if name in kwargs:
                raise TypeError("multiple values for field " + name)
            setattr(self, name, value)
        for name, value in kwargs.items():
            setattr(self, name, value)


class mod(AST):
    _fields = ()


class stmt(AST):
    _fields = ()
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class expr(AST):
    _fields = ()
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class expr_context(AST):
    _fields = ()


class boolop(AST):
    _fields = ()


class operator(AST):
    _fields = ()


class unaryop(AST):
    _fields = ()


class cmpop(AST):
    _fields = ()


class comprehension(AST):
    _fields = ('target', 'iter', 'ifs', 'is_async')


class excepthandler(AST):
    _fields = ()
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class arguments(AST):
    _fields = ('posonlyargs', 'args', 'vararg', 'kwonlyargs', 'kw_defaults', 'kwarg', 'defaults')
    vararg = None
    kwarg = None


class arg(AST):
    _fields = ('arg', 'annotation', 'type_comment')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    annotation = None
    type_comment = None
    end_lineno = None
    end_col_offset = None


class keyword(AST):
    _fields = ('arg', 'value')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    arg = None
    end_lineno = None
    end_col_offset = None


class alias(AST):
    _fields = ('name', 'asname')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    asname = None
    end_lineno = None
    end_col_offset = None


class withitem(AST):
    _fields = ('context_expr', 'optional_vars')
    optional_vars = None


class match_case(AST):
    _fields = ('pattern', 'guard', 'body')
    guard = None


class pattern(AST):
    _fields = ()
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')


class type_ignore(AST):
    _fields = ()


class type_param(AST):
    _fields = ()
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')


class slice(AST):
    _fields = ()


class Module(mod):
    _fields = ('body', 'type_ignores')


class Interactive(mod):
    _fields = ('body',)


class Expression(mod):
    _fields = ('body',)


class FunctionType(mod):
    _fields = ('argtypes', 'returns')


class FunctionDef(stmt):
    _fields = ('name', 'args', 'body', 'decorator_list', 'returns', 'type_comment', 'type_params')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    returns = None
    type_comment = None
    end_lineno = None
    end_col_offset = None


class AsyncFunctionDef(stmt):
    _fields = ('name', 'args', 'body', 'decorator_list', 'returns', 'type_comment', 'type_params')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    returns = None
    type_comment = None
    end_lineno = None
    end_col_offset = None


class ClassDef(stmt):
    _fields = ('name', 'bases', 'keywords', 'body', 'decorator_list', 'type_params')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class Return(stmt):
    _fields = ('value',)
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    value = None
    end_lineno = None
    end_col_offset = None


class Delete(stmt):
    _fields = ('targets',)
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class Assign(stmt):
    _fields = ('targets', 'value', 'type_comment')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    type_comment = None
    end_lineno = None
    end_col_offset = None


class TypeAlias(stmt):
    _fields = ('name', 'type_params', 'value')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class AugAssign(stmt):
    _fields = ('target', 'op', 'value')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class AnnAssign(stmt):
    _fields = ('target', 'annotation', 'value', 'simple')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    value = None
    end_lineno = None
    end_col_offset = None


class For(stmt):
    _fields = ('target', 'iter', 'body', 'orelse', 'type_comment')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    type_comment = None
    end_lineno = None
    end_col_offset = None


class AsyncFor(stmt):
    _fields = ('target', 'iter', 'body', 'orelse', 'type_comment')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    type_comment = None
    end_lineno = None
    end_col_offset = None


class While(stmt):
    _fields = ('test', 'body', 'orelse')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class If(stmt):
    _fields = ('test', 'body', 'orelse')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class With(stmt):
    _fields = ('items', 'body', 'type_comment')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    type_comment = None
    end_lineno = None
    end_col_offset = None


class AsyncWith(stmt):
    _fields = ('items', 'body', 'type_comment')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    type_comment = None
    end_lineno = None
    end_col_offset = None


class Match(stmt):
    _fields = ('subject', 'cases')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class Raise(stmt):
    _fields = ('exc', 'cause')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    exc = None
    cause = None
    end_lineno = None
    end_col_offset = None


class Try(stmt):
    _fields = ('body', 'handlers', 'orelse', 'finalbody')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class TryStar(stmt):
    _fields = ('body', 'handlers', 'orelse', 'finalbody')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class Assert(stmt):
    _fields = ('test', 'msg')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    msg = None
    end_lineno = None
    end_col_offset = None


class Import(stmt):
    _fields = ('names',)
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class ImportFrom(stmt):
    _fields = ('module', 'names', 'level')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    module = None
    level = None
    end_lineno = None
    end_col_offset = None


class Global(stmt):
    _fields = ('names',)
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class Nonlocal(stmt):
    _fields = ('names',)
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class Expr(stmt):
    _fields = ('value',)
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class Pass(stmt):
    _fields = ()
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class Break(stmt):
    _fields = ()
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class Continue(stmt):
    _fields = ()
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class BoolOp(expr):
    _fields = ('op', 'values')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class NamedExpr(expr):
    _fields = ('target', 'value')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class BinOp(expr):
    _fields = ('left', 'op', 'right')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class UnaryOp(expr):
    _fields = ('op', 'operand')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class Lambda(expr):
    _fields = ('args', 'body')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class IfExp(expr):
    _fields = ('test', 'body', 'orelse')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class Dict(expr):
    _fields = ('keys', 'values')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class Set(expr):
    _fields = ('elts',)
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class ListComp(expr):
    _fields = ('elt', 'generators')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class SetComp(expr):
    _fields = ('elt', 'generators')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class DictComp(expr):
    _fields = ('key', 'value', 'generators')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class GeneratorExp(expr):
    _fields = ('elt', 'generators')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class Await(expr):
    _fields = ('value',)
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class Yield(expr):
    _fields = ('value',)
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    value = None
    end_lineno = None
    end_col_offset = None


class YieldFrom(expr):
    _fields = ('value',)
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class Compare(expr):
    _fields = ('left', 'ops', 'comparators')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class Call(expr):
    _fields = ('func', 'args', 'keywords')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class FormattedValue(expr):
    _fields = ('value', 'conversion', 'format_spec')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    format_spec = None
    end_lineno = None
    end_col_offset = None


class JoinedStr(expr):
    _fields = ('values',)
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class Constant(expr):
    _fields = ('value', 'kind')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    kind = None
    end_lineno = None
    end_col_offset = None


class Attribute(expr):
    _fields = ('value', 'attr', 'ctx')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class Subscript(expr):
    _fields = ('value', 'slice', 'ctx')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class Starred(expr):
    _fields = ('value', 'ctx')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class Name(expr):
    _fields = ('id', 'ctx')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class List(expr):
    _fields = ('elts', 'ctx')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class Tuple(expr):
    _fields = ('elts', 'ctx')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    end_lineno = None
    end_col_offset = None


class Slice(expr):
    _fields = ('lower', 'upper', 'step')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    lower = None
    upper = None
    step = None
    end_lineno = None
    end_col_offset = None


class Load(expr_context):
    _fields = ()


class Store(expr_context):
    _fields = ()


class Del(expr_context):
    _fields = ()


class And(boolop):
    _fields = ()


class Or(boolop):
    _fields = ()


class Add(operator):
    _fields = ()


class Sub(operator):
    _fields = ()


class Mult(operator):
    _fields = ()


class MatMult(operator):
    _fields = ()


class Div(operator):
    _fields = ()


class Mod(operator):
    _fields = ()


class Pow(operator):
    _fields = ()


class LShift(operator):
    _fields = ()


class RShift(operator):
    _fields = ()


class BitOr(operator):
    _fields = ()


class BitXor(operator):
    _fields = ()


class BitAnd(operator):
    _fields = ()


class FloorDiv(operator):
    _fields = ()


class Invert(unaryop):
    _fields = ()


class Not(unaryop):
    _fields = ()


class UAdd(unaryop):
    _fields = ()


class USub(unaryop):
    _fields = ()


class Eq(cmpop):
    _fields = ()


class NotEq(cmpop):
    _fields = ()


class Lt(cmpop):
    _fields = ()


class LtE(cmpop):
    _fields = ()


class Gt(cmpop):
    _fields = ()


class GtE(cmpop):
    _fields = ()


class Is(cmpop):
    _fields = ()


class IsNot(cmpop):
    _fields = ()


class In(cmpop):
    _fields = ()


class NotIn(cmpop):
    _fields = ()


class ExceptHandler(excepthandler):
    _fields = ('type', 'name', 'body')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    type = None
    name = None
    end_lineno = None
    end_col_offset = None


class MatchValue(pattern):
    _fields = ('value',)
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')


class MatchSingleton(pattern):
    _fields = ('value',)
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')


class MatchSequence(pattern):
    _fields = ('patterns',)
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')


class MatchMapping(pattern):
    _fields = ('keys', 'patterns', 'rest')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    rest = None


class MatchClass(pattern):
    _fields = ('cls', 'patterns', 'kwd_attrs', 'kwd_patterns')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')


class MatchStar(pattern):
    _fields = ('name',)
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    name = None


class MatchAs(pattern):
    _fields = ('pattern', 'name')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    pattern = None
    name = None


class MatchOr(pattern):
    _fields = ('patterns',)
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')


class TypeIgnore(type_ignore):
    _fields = ('lineno', 'tag')


class TypeVar(type_param):
    _fields = ('name', 'bound')
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')
    bound = None


class ParamSpec(type_param):
    _fields = ('name',)
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')


class TypeVarTuple(type_param):
    _fields = ('name',)
    _attributes = ('lineno', 'col_offset', 'end_lineno', 'end_col_offset')


_OPERATORS = {
    "add": Add, "sub": Sub, "mul": Mult, "div": Div, "floordiv": FloorDiv,
    "mod": Mod, "pow": Pow, "lshift": LShift, "rshift": RShift,
    "bitand": BitAnd, "bitor": BitOr, "bitxor": BitXor, "matmul": MatMult,
    "neg": USub, "pos": UAdd, "not": Not, "invert": Invert,
    "and": And, "or": Or, "eq": Eq, "ne": NotEq, "lt": Lt, "le": LtE,
    "gt": Gt, "ge": GtE, "in": In, "notin": NotIn, "is": Is, "isnot": IsNot,
}


def _context(node, context):
    if isinstance(node, (Name, Attribute, Subscript, Starred, List, Tuple)):
        node.ctx = context()
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
    return arg(arg=name, annotation=_convert(data["annotation"]),
               type_comment=None, lineno=data["ln"])


def _arguments(params):
    args = [_argument(name, params) for name in params["args"]]
    count = params["posonly"]
    return arguments(posonlyargs=args[:count], args=args[count:],
                     vararg=_argument(params["vararg"], params),
                     kwonlyargs=[_argument(name, params) for name in params["kwonly"]],
                     kw_defaults=_convert(params["kwdefaults"]),
                     kwarg=_argument(params["kwarg"], params),
                     defaults=_convert(params["defaults"]))


def _keywords(items):
    return [keyword(arg=item["n"], value=_convert(item["v"])) for item in items]


def _fparts(parts, line):
    values = []
    for part in parts:
        if "lit" in part:
            values.append(Constant(value=part["lit"], kind=None, lineno=line))
        else:
            conv = -1 if part["conv"] is None else ord(part["conv"])
            values.append(FormattedValue(value=_convert(part["v"]), conversion=conv,
                                         format_spec=_convert(part["spec"]), lineno=line))
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
        node = Name(id=data["id"], ctx=Load())
    elif tag in ("List", "Tuple", "Set"):
        cls = {"List": List, "Tuple": Tuple, "Set": Set}[tag]
        node = cls(elts=_convert(data["elts"]))
        if tag != "Set":
            node.ctx = Load()
    elif tag == "Dict":
        node = Dict(keys=[_convert(item["k"]) for item in data["entries"]],
                    values=[_convert(item["v"]) for item in data["entries"]])
    elif tag == "Attr":
        node = Attribute(value=_convert(data["o"]), attr=data["n"], ctx=Load())
    elif tag == "Sub":
        node = Subscript(value=_convert(data["o"]), slice=_convert(data["k"]), ctx=Load())
    elif tag == "Slice":
        node = Slice(lower=_convert(data["lower"]), upper=_convert(data["upper"]),
                     step=_convert(data["step"]))
    elif tag == "Starred":
        node = Starred(value=_convert(data["v"]), ctx=Load())
    elif tag == "BinOp":
        node = BinOp(left=_convert(data["l"]), op=_OPERATORS[data["kind"]](),
                     right=_convert(data["r"]))
    elif tag == "Unary":
        node = UnaryOp(op=_OPERATORS[data["kind"]](), operand=_convert(data["v"]))
    elif tag == "Bool":
        node = BoolOp(op=_OPERATORS[data["kind"]](), values=_convert(data["values"]))
    elif tag == "Cmp":
        node = Compare(left=_convert(data["left"]),
                       ops=[_OPERATORS[op]() for op in data["ops"]],
                       comparators=_convert(data["comps"]))
    elif tag == "Cond":
        node = IfExp(test=_convert(data["test"]), body=_convert(data["then"]),
                     orelse=_convert(data["else"]))
    elif tag == "Call":
        node = Call(func=_convert(data["f"]), args=_convert(data["args"]),
                    keywords=_keywords(data["kw"]))
    elif tag == "Named":
        node = NamedExpr(target=_context(_convert(data["target"]), Store),
                         value=_convert(data["v"]))
    elif tag == "Lambda":
        node = Lambda(args=_arguments(data["params"]), body=_convert(data["body"]))
    elif tag == "Fstr":
        node = JoinedStr(values=_fparts(data["parts"], data["ln"]))
    elif tag == "Comp":
        generators = []
        for gen in data["gens"]:
            generators.append(comprehension(target=_context(_convert(gen["target"]), Store),
                                             iter=_convert(gen["iter"]), ifs=_convert(gen["ifs"]),
                                             is_async=gen.get("async", 0)))
        if data["kind"] == "dict":
            node = DictComp(key=_convert(data["key"]), value=_convert(data["val"]),
                            generators=generators)
        else:
            cls = {"list": ListComp, "set": SetComp, "gen": GeneratorExp}[data["kind"]]
            node = cls(elt=_convert(data["elt"]), generators=generators)
    elif tag in ("Expr", "Return", "Yield", "YieldFrom", "Await"):
        cls = {"Expr": Expr, "Return": Return, "Yield": Yield,
               "YieldFrom": YieldFrom, "Await": Await}[tag]
        node = cls(value=_convert(data["v"]))
    elif tag in ("Pass", "Break", "Continue"):
        node = {"Pass": Pass, "Break": Break, "Continue": Continue}[tag]()
    elif tag in ("Global", "Nonlocal"):
        node = {"Global": Global, "Nonlocal": Nonlocal}[tag](names=data["names"])
    elif tag == "Assign":
        node = Assign(targets=[_context(_convert(t), Store) for t in data["targets"]],
                      value=_convert(data["v"]), type_comment=None)
    elif tag == "Ann":
        node = AnnAssign(target=_context(_convert(data["target"]), Store),
                         annotation=_convert(data["annotation"]), value=_convert(data["v"]),
                         simple=int(data["simple"]))
    elif tag == "Aug":
        node = AugAssign(target=_context(_convert(data["target"]), Store),
                         op=_OPERATORS[data["kind"]](), value=_convert(data["v"]))
    elif tag == "Del":
        node = Delete(targets=[_context(_convert(t), Del) for t in data["targets"]])
    elif tag in ("If", "While"):
        cls = If if tag == "If" else While
        node = cls(test=_convert(data["test"]),
                   body=_convert(data["then"] if tag == "If" else data["body"]),
                   orelse=_convert(data["orelse"]))
    elif tag in ("For", "AsyncFor"):
        cls = For if tag == "For" else AsyncFor
        node = cls(target=_context(_convert(data["target"]), Store),
                   iter=_convert(data["iter"]), body=_convert(data["body"]),
                   orelse=_convert(data["orelse"]), type_comment=None)
    elif tag == "Func":
        cls = AsyncFunctionDef if data["coro"] else FunctionDef
        node = cls(name=data["name"], args=_arguments(data["params"]),
                   body=_convert(data["body"]), decorator_list=_convert(data["decorators"]),
                   returns=_convert(data["returns"]), type_comment=None, type_params=[])
    elif tag == "Class":
        node = ClassDef(name=data["name"], bases=_convert(data["bases"]),
                        keywords=_keywords(data["keywords"]), body=_convert(data["body"]),
                        decorator_list=_convert(data["decorators"]), type_params=[])
    elif tag == "Raise":
        node = Raise(exc=_convert(data["exc"]), cause=_convert(data["cause"]))
    elif tag == "Assert":
        node = Assert(test=_convert(data["test"]), msg=_convert(data["msg"]))
    elif tag == "Try":
        handlers = []
        for handler in data["handlers"]:
            handlers.append(ExceptHandler(type=_convert(handler["type"]), name=handler["name"],
                                          body=_convert(handler["body"])))
        cls = TryStar if data["star"] else Try
        node = cls(body=_convert(data["body"]), handlers=handlers,
                   orelse=_convert(data["orelse"]), finalbody=_convert(data["final"]))
    elif tag in ("With", "AsyncWith"):
        items = []
        for item in data["items"]:
            items.append(withitem(context_expr=_convert(item["ctx"]),
                                  optional_vars=_context(_convert(item["vars"]), Store)))
        cls = With if tag == "With" else AsyncWith
        node = cls(items=items, body=_convert(data["body"]), type_comment=None)
    elif tag in ("Import", "ImportFrom"):
        names = [alias(name=item["n"], asname=item["as"]) for item in data["names"]]
        if tag == "Import":
            node = Import(names=names)
        else:
            node = ImportFrom(module=data["module"], names=names, level=data["level"])
    elif tag == "Match":
        cases = []
        for case in data["cases"]:
            cases.append(match_case(pattern=_convert(case["pattern"]),
                                    guard=_convert(case["guard"]), body=_convert(case["body"])))
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
        node = MatchMapping(keys=_convert(data["keys"]), patterns=_convert(data["pats"]),
                            rest=data["rest"])
    elif tag == "MCls":
        node = MatchClass(cls=_convert(data["cls"]), patterns=_convert(data["pos"]),
                          kwd_attrs=data["attrs"], kwd_patterns=_convert(data["kpats"]))
    else:
        raise NotImplementedError("AST node " + tag)
    if "ln" in data:
        node.lineno = data["ln"]
    return node


def parse(source, filename="<unknown>", mode="exec", *, type_comments=False,
          feature_version=None):
    if type_comments or feature_version is not None:
        raise NotImplementedError("type comments and grammar versions")
    if mode not in ("exec", "eval", "single"):
        raise ValueError("compile() mode must be 'exec', 'eval' or 'single'")
    if isinstance(source, bytes):
        source = source.decode("utf-8")
    try:
        parser = Parser(tokenize(source, filename), filename)
        if mode == "eval":
            node = _convert(parser.testlist_star())
            while parser.kind() == "nl":
                parser.adv()
            if parser.kind() != "end":
                raise parser.perr("unexpected token")
            return Expression(body=node)
        tree = _convert(parser.parse_module())
        if mode == "single":
            return Interactive(body=tree.body)
        return tree
    except CompileError as error:
        exc = SyntaxError(error.msg.removeprefix("syntax error: "))
        exc.filename = filename
        exc.lineno = error.line
        raise exc from None


def iter_fields(node):
    for field in node._fields:
        if hasattr(node, field):
            yield field, getattr(node, field)


def iter_child_nodes(node):
    for _field, value in iter_fields(node):
        if isinstance(value, AST):
            yield value
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, AST):
                    yield child


def walk(node):
    pending = [node]
    index = 0
    while index < len(pending):
        current = pending[index]
        index += 1
        pending.extend(iter_child_nodes(current))
        yield current


class NodeVisitor:
    def visit(self, node):
        visitor = getattr(self, "visit_" + type(node).__name__, self.generic_visit)
        return visitor(node)

    def generic_visit(self, node):
        for child in iter_child_nodes(node):
            self.visit(child)


class NodeTransformer(NodeVisitor):
    def generic_visit(self, node):
        for field, value in iter_fields(node):
            if isinstance(value, list):
                replacements = []
                for child in value:
                    if isinstance(child, AST):
                        child = self.visit(child)
                        if child is None:
                            continue
                        if not isinstance(child, AST):
                            replacements.extend(child)
                            continue
                    replacements.append(child)
                value[:] = replacements
            elif isinstance(value, AST):
                replacement = self.visit(value)
                if replacement is None:
                    delattr(node, field)
                else:
                    setattr(node, field, replacement)
        return node


def copy_location(new_node, old_node):
    for name in ("lineno", "col_offset", "end_lineno", "end_col_offset"):
        if name in old_node._attributes and name in new_node._attributes:
            value = getattr(old_node, name, None)
            if value is not None or name.startswith("end_"):
                setattr(new_node, name, value)
    return new_node


def _fix_locations(node, line, column, end_line, end_column):
    for name, value in (("lineno", line), ("col_offset", column),
                        ("end_lineno", end_line), ("end_col_offset", end_column)):
        if name in node._attributes:
            if not hasattr(node, name) or (name.startswith("end_") and getattr(node, name) is None):
                setattr(node, name, value)
    for child in iter_child_nodes(node):
        _fix_locations(child, getattr(node, "lineno", line),
                       getattr(node, "col_offset", column),
                       getattr(node, "end_lineno", end_line),
                       getattr(node, "end_col_offset", end_column))


def fix_missing_locations(node):
    _fix_locations(node, 1, 0, 1, 0)
    return node


def get_docstring(node, clean=True):
    if not isinstance(node, (Module, FunctionDef, AsyncFunctionDef, ClassDef)):
        raise TypeError("node cannot have docstrings")
    if not node.body or not isinstance(node.body[0], Expr):
        return None
    value = node.body[0].value
    if not isinstance(value, Constant) or not isinstance(value.value, str):
        return None
    text = value.value
    if not clean:
        return text
    expanded = []
    column = 0
    for char in text:
        if char == "\t":
            count = 8 - column % 8
            expanded.append(" " * count)
            column += count
        else:
            expanded.append(char)
            if char in "\r\n":
                column = 0
            else:
                column += 1
    lines = "".join(expanded).split("\n")
    margin = None
    for line in lines[1:]:
        stripped = line.lstrip()
        if stripped:
            indent = len(line) - len(stripped)
            margin = indent if margin is None else min(margin, indent)
    if lines:
        lines[0] = lines[0].lstrip()
    if margin is not None:
        for index in range(1, len(lines)):
            lines[index] = lines[index][margin:]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return "\n".join(lines)


def _number(node):
    if isinstance(node, Constant) and type(node.value) in (int, float, complex):
        return node.value
    raise ValueError("malformed node or string")


def _signed_number(node):
    if isinstance(node, UnaryOp) and isinstance(node.op, (UAdd, USub)):
        value = _number(node.operand)
        return value if isinstance(node.op, UAdd) else -value
    return _number(node)


def literal_eval(node_or_string):
    node = node_or_string
    if isinstance(node, str):
        node = parse(node.lstrip(" \t"), mode="eval")
    if isinstance(node, Expression):
        node = node.body
    if isinstance(node, Constant):
        return node.value
    if isinstance(node, (Tuple, List, Set)):
        values = [literal_eval(child) for child in node.elts]
        if isinstance(node, Tuple):
            return tuple(values)
        if isinstance(node, Set):
            return set(values)
        return values
    if isinstance(node, Dict):
        if len(node.keys) != len(node.values):
            raise ValueError("malformed node or string")
        return {literal_eval(key): literal_eval(value)
                for key, value in zip(node.keys, node.values)}
    if isinstance(node, Call) and isinstance(node.func, Name) and node.func.id == "set":
        if not node.args and not node.keywords:
            return set()
    if isinstance(node, BinOp) and isinstance(node.op, (Add, Sub)):
        left = _signed_number(node.left)
        right = _number(node.right)
        if isinstance(left, (int, float)) and isinstance(right, complex):
            return left + right if isinstance(node.op, Add) else left - right
    return _signed_number(node)


def _dump(node, annotate_fields, include_attributes, indent, level):
    prefix = ""
    separator = ", "
    if indent is not None:
        level += 1
        prefix = "\n" + indent * level
        separator = "," + prefix
    if isinstance(node, AST):
        values = []
        simple = True
        keywords = annotate_fields
        names = list(node._fields)
        if include_attributes:
            names.extend(node._attributes)
        for name in names:
            if not hasattr(node, name):
                keywords = True
                continue
            value = getattr(node, name)
            if value is None and getattr(type(node), name, ...) is None:
                keywords = True
                continue
            text, child_simple = _dump(value, annotate_fields, include_attributes, indent, level)
            simple = simple and child_simple
            if keywords or name in node._attributes:
                text = name + "=" + text
            values.append(text)
        start = type(node).__name__ + "("
        if simple and len(values) <= 3:
            return start + ", ".join(values) + ")", not values
        return start + prefix + separator.join(values) + ")", False
    if isinstance(node, list):
        if not node:
            return "[]", True
        values = [_dump(value, annotate_fields, include_attributes, indent, level)[0]
                  for value in node]
        return "[" + prefix + separator.join(values) + "]", False
    return repr(node), True


def dump(node, annotate_fields=True, include_attributes=False, *, indent=None):
    if not isinstance(node, AST):
        raise TypeError("expected AST")
    if isinstance(indent, int):
        indent = " " * indent
    return _dump(node, annotate_fields, include_attributes, indent, 0)[0]


def _operator_name(node):
    for name, cls in _OPERATORS.items():
        if type(node) is cls:
            return name
    raise NotImplementedError("operator " + type(node).__name__)


def _parser_arguments(node):
    args = node.posonlyargs + node.args
    return {"args": [item.arg for item in args], "posonly": len(node.posonlyargs),
            "defaults": [_parser_expr(item) for item in node.defaults],
            "vararg": node.vararg.arg if node.vararg is not None else None,
            "kwarg": node.kwarg.arg if node.kwarg is not None else None,
            "kwonly": [item.arg for item in node.kwonlyargs],
            "kwdefaults": [_parser_expr(item) for item in node.kw_defaults]}


def _parser_expr(node):
    if node is None:
        return None
    if isinstance(node, Name):
        data = {"t": "Name", "id": node.id}
    elif isinstance(node, Constant):
        value = node.value
        if value is None:
            kind = "none"
        elif value is ...:
            kind = "ellipsis"
        else:
            kind = type(value).__name__
        if isinstance(value, complex):
            if value.real:
                raise NotImplementedError("unparsing non-imaginary complex constants")
            value = value.imag
        data = {"t": "Const", "kind": kind, "v": value, "prefix": node.kind}
    elif isinstance(node, (List, Tuple, Set)):
        data = {"t": type(node).__name__, "elts": [_parser_expr(item) for item in node.elts]}
    elif isinstance(node, Dict):
        data = {"t": "Dict", "entries": [
            {"k": _parser_expr(key), "v": _parser_expr(value)}
            for key, value in zip(node.keys, node.values)]}
    elif isinstance(node, Attribute):
        data = {"t": "Attr", "o": _parser_expr(node.value), "n": node.attr}
    elif isinstance(node, Subscript):
        data = {"t": "Sub", "o": _parser_expr(node.value), "k": _parser_expr(node.slice)}
    elif isinstance(node, Slice):
        data = {"t": "Slice", "lower": _parser_expr(node.lower),
                "upper": _parser_expr(node.upper), "step": _parser_expr(node.step)}
    elif isinstance(node, BinOp):
        data = {"t": "BinOp", "kind": _operator_name(node.op),
                "l": _parser_expr(node.left), "r": _parser_expr(node.right)}
    elif isinstance(node, UnaryOp):
        data = {"t": "Unary", "kind": _operator_name(node.op), "v": _parser_expr(node.operand)}
    elif isinstance(node, BoolOp):
        data = {"t": "Bool", "kind": _operator_name(node.op),
                "values": [_parser_expr(item) for item in node.values]}
    elif isinstance(node, Compare):
        data = {"t": "Cmp", "left": _parser_expr(node.left),
                "ops": [_operator_name(item) for item in node.ops],
                "comps": [_parser_expr(item) for item in node.comparators]}
    elif isinstance(node, IfExp):
        data = {"t": "Cond", "test": _parser_expr(node.test),
                "then": _parser_expr(node.body), "else": _parser_expr(node.orelse)}
    elif isinstance(node, Call):
        data = {"t": "Call", "f": _parser_expr(node.func),
                "args": [_parser_expr(item) for item in node.args],
                "kw": [{"n": item.arg, "v": _parser_expr(item.value)} for item in node.keywords]}
    elif isinstance(node, NamedExpr):
        data = {"t": "Named", "target": _parser_expr(node.target), "v": _parser_expr(node.value)}
    elif isinstance(node, Lambda):
        data = {"t": "Lambda", "params": _parser_arguments(node.args),
                "body": _parser_expr(node.body)}
    elif isinstance(node, (Starred, Await, Yield, YieldFrom)):
        data = {"t": type(node).__name__, "v": _parser_expr(node.value)}
    elif isinstance(node, JoinedStr):
        parts = []
        for item in node.values:
            if isinstance(item, Constant):
                parts.append({"lit": item.value})
            elif isinstance(item, FormattedValue):
                parts.append({"t": "FV", "v": _parser_expr(item.value),
                              "conv": None if item.conversion == -1 else chr(item.conversion),
                              "spec": _parser_expr(item.format_spec)})
            else:
                raise ValueError("invalid joined string")
        data = {"t": "Fstr", "parts": parts}
    elif isinstance(node, (ListComp, SetComp, DictComp, GeneratorExp)):
        kind = {"ListComp": "list", "SetComp": "set",
                "DictComp": "dict", "GeneratorExp": "gen"}[type(node).__name__]
        data = {"t": "Comp", "kind": kind, "gens": [
            {"target": _parser_expr(item.target), "iter": _parser_expr(item.iter),
             "ifs": [_parser_expr(test) for test in item.ifs], "async": item.is_async}
            for item in node.generators]}
        if isinstance(node, DictComp):
            data["key"] = _parser_expr(node.key)
            data["val"] = _parser_expr(node.value)
        else:
            data["elt"] = _parser_expr(node.elt)
    else:
        raise NotImplementedError("unparsing " + type(node).__name__)
    data["ln"] = getattr(node, "lineno", 1)
    return data


def unparse(node):
    """Render expressions, including annotations, as Python source."""
    from _pyunparse import unparse_annotation

    if isinstance(node, Expression):
        node = node.body
    return unparse_annotation(_parser_expr(node))
