import _ast
import ast
import builtins


print("identity", _ast.AST is ast.AST, _ast.Name is ast.Name, _ast.Module is ast.Module)
print("flags", _ast.PyCF_ONLY_AST, _ast.PyCF_TYPE_COMMENTS, _ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
for cls in (_ast.Module, _ast.FunctionDef, _ast.ClassDef, _ast.arguments, _ast.arg,
            _ast.Name, _ast.Constant, _ast.keyword, _ast.alias, _ast.MatchClass):
    print("schema", cls.__name__, cls.__module__, cls._fields, cls.__match_args__, cls._attributes)

name = _ast.Name("café", _ast.Load(), lineno=2, col_offset=4, end_lineno=2, end_col_offset=9)
print("constructor", ast.dump(name, include_attributes=True))
name.extra = "annotation"
reduced = name.__reduce__()
print("reduce", reduced[0] is _ast.Name, reduced[1], sorted(reduced[2]))
print("optional", _ast.Constant().kind, _ast.Return().value, _ast.arg().annotation,
      _ast.keyword().arg, _ast.alias().asname, _ast.Name().end_lineno)
print("missing", hasattr(_ast.Name(), "id"), hasattr(_ast.Name(), "lineno"), hasattr(_ast.Name(), "col_offset"))
for make in (lambda: _ast.Name("x", _ast.Load(), 3),
             lambda: _ast.Name("x", id="y"),
             lambda: _ast.Load(1)):
    try:
        make()
    except TypeError as error:
        print("constructor error", str(error))

only_ast = _ast.PyCF_ONLY_AST
for mode, source in (("exec", "x = 1\ny = x + 2\n"),
                     ("eval", "lambda a=2: a + 1"),
                     ("single", "x = 1; y = 2"),
                     ("func_type", "(int, str) -> bool"),
                     ("func_type", "(*int, **str) -> tuple[int, str]"),
                     ("func_type", "() -> None")):
    tree = builtins.compile(source, "sample.py", mode, only_ast,
                            dont_inherit=True, optimize=2, _feature_version=12)
    print("compile", mode, ast.dump(tree))
    print("same tree", compile(tree, "copy.py", mode, only_ast) is tree)

for source in (b"x = 1\n", bytearray(b"x = 2\n"), b"\xef\xbb\xbfx = 3\n",
               b"# coding: latin-1\nvalue = 'caf\xe9'\n"):
    print("encoded", ast.dump(compile(source, "encoded.py", "exec", only_ast)))
print("await", ast.dump(compile("await f()", "await.py", "eval",
                                only_ast | _ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)))
for mode, source in (("single", "x = 1\ny = 2\n"), ("eval", "x = 1"),
                     ("func_type", "(int,) -> str"), ("exec", "x =")):
    try:
        compile(source, "invalid.py", mode, only_ast)
    except SyntaxError as error:
        print("syntax error", mode, error.filename, error.lineno)
for mode, flags, optimize in (("bad", only_ast, -1), ("exec", only_ast | 0x8000, -1),
                              ("exec", only_ast, 3)):
    try:
        compile("pass", "invalid.py", mode, flags, optimize=optimize)
    except ValueError as error:
        print("compile error", str(error))

operators = ast.parse("a + b + c", mode="eval").body
print("operator singleton", operators.op is operators.left.op)
print("context singleton", operators.left.left.ctx is operators.right.ctx)


def positions(label, source, mode="exec"):
    tree = ast.parse(source, mode=mode)
    print("source", label)
    for node in ast.walk(tree):
        if "lineno" in node._attributes:
            location = (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)
            print(type(node).__name__, location, repr(ast.get_source_segment(source, node)))
    return tree


positions("utf8", "á = 1\nβ = á + (2 * 3)\nγ = 'é'; δ = β\n")
positions("multiline", "result = (\n    call('café',\n         key=β,\n         **options)\n)\n")
positions("tab", "if ready:\n\tvalue = 'é'\n\tother = value;\n")
positions("continued", "total = first + \\\n    second\n")
positions("trailers", "(obj).field[1:][row, :end:step, ...,]", "eval")
positions("operators", "(a + b) * -c ** 2 + d + e if not p and q or r else s < t <= u", "eval")
positions("tuples", "a, (b, c), *rest = values\ndel a, obj.field, values[0]\n")
positions("containers", "[[], (), {}, {1, 2}, {'x': 1, **other}, (*items,)]", "eval")
positions("comprehensions", "[x * 2 for x, y in pairs if x > y]", "eval")
positions("generator call", "consume(x for x in values if x)", "eval")
positions("mapping comprehension", "{key: value for key, value in pairs}", "eval")
positions("set comprehension", "{item for item in values}", "eval")
positions("named expression", "[(count := item), lambda a=1, *, b=2: a + b]", "eval")
positions("string concatenation", "('one'\n u'two'\n 'three')", "eval")
positions("triple string", "value = '''first\ncafé\nlast'''\n")

positions("definitions", '''@decorate
async def sample(a: int, /, b: str = "x", *args: tuple, flag=True, **kwargs: dict) -> str:
    """Function docs."""
    async for item in source:
        async with resource() as handle:
            await handle.send(item)
    return b
class Sample(*bases, metaclass=Meta, **options):
    value: int = 3
''')
positions("statements", '''import package.part as renamed, other
from ..package import (first as local, second)
from package import *
def sample():
    global value
    nonlocal outer
    for item in values:
        if item:
            continue
        elif other:
            break
        else:
            pass
    else:
        value += 1
    while value:
        value -= 1
    else:
        assert value, "message"
    try:
        with manager() as current, other():
            yield current
        yield from values
    except ValueError as error:
        raise RuntimeError(error) from error
    else:
        return None
    finally:
        cleanup();
''')
positions("exception group", "try:\n    action()\nexcept* ValueError as errors:\n    handle(errors)\n")
positions("patterns", '''match subject:
    case {"x": [first, *rest], **other} if first:
        pass
    case Sample(field=result) | Other(field=result):
        pass
    case (1, -2, None, True, False):
        pass
    case package.VALUE as captured:
        pass
    case [_, *tail]:
        pass
''')
positions("fstring", 'f"café {value!r:>{width}s} tail"', "eval")
positions("fstring debug", 'f"prefix { value = !s} suffix"', "eval")
positions("fstring braces", 'f"{{{value}}}"', "eval")
positions("fstring concatenation", 'u"prefix " f"{value}" " suffix"', "eval")
positions("fstring multiline", 'f"""first\né {\n    value + 1\n!r}\nlast"""', "eval")
positions("fstring tuple", 'f"{first, second}"', "eval")
positions("fstring nested format", 'f"{value:{width}}"', "eval")

created = ast.fix_missing_locations(ast.Expression(body=ast.Constant(value=7)))
print("fixed", ast.dump(created, include_attributes=True))
parsed = ast.parse("left + right", mode="eval")
replacement = ast.copy_location(ast.Name(id="other", ctx=ast.Load()), parsed.body.right)
print("copied", ast.dump(replacement, include_attributes=True))
ast.increment_lineno(parsed, 3)
print("incremented", ast.dump(parsed, include_attributes=True))

positions("function type", "(list[int], *tuple[str], **dict[str, int]) -> bool", "func_type")
positions("constants", "[b'x', None, True, ..., 1j]", "eval")
positions("await expression", "await (factory())", "eval")

for source in ("a + b * -2", "[x for x in values if x]",
               "lambda a=1, *, b=2: (a, b)", "f'{value!r:>{width}s}'"):
    original = ast.parse(source, mode="eval")
    rendered = ast.unparse(original)
    print("unparse expression", ast.dump(ast.parse(rendered, mode="eval")) == ast.dump(original))
original = ast.parse("class Child(Base):\n    def method(self, a: int = 1):\n        return self.value + a\n")
rendered = ast.unparse(original)
print("unparse module", ast.dump(ast.parse(rendered)) == ast.dump(original))
