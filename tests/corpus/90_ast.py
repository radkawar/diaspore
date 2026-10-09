import ast


def tree_dump(node):
    if isinstance(node, ast.AST):
        fields = [name + "=" + tree_dump(value) for name, value in ast.iter_fields(node)]
        return type(node).__name__ + "(" + ", ".join(fields) + ")"
    if isinstance(node, list):
        return "[" + ", ".join(tree_dump(value) for value in node) + "]"
    return repr(node)


source = '''"""Module docs."""
@decorate
async def example(a: int, /, b=2, *args, flag=True, **kwargs) -> str:
    """Function docs.

    More text.
    """
    values = [x * 2 for x in args if x > 0]
    try:
        async with resource() as item:
            await item.send(f"{b!r:>{flag}s}")
    except ValueError as error:
        raise RuntimeError(error) from error
    return values
class Sample(Base, metaclass=Meta):
    field: int = 3
match value:
    case {"x": [first, *rest], **other} if first:
        pass
    case Sample(field=result) | Other(field=result):
        del result
'''
tree = ast.parse(source)
print(ast.get_docstring(tree))
print(tree_dump(tree))
print(ast.get_docstring(tree.body[1]))
print([type(node).__name__ for node in ast.walk(ast.parse('a + b * -2', mode='eval'))])
print([(name, type(value).__name__) for name, value in ast.iter_fields(tree)])
print([type(node).__name__ for node in ast.iter_child_nodes(tree)])
print(tree_dump(ast.parse('x[1:3, ...] = {1, 2}; x += 1; assert x, "empty"')))
print(tree_dump(ast.parse('lambda a=1: a if a else None', mode='eval')))
print(tree_dump(ast.parse('x = 1', mode='single')))
print(ast.dump(ast.parse('a + 2', mode='eval'), indent=2))
print(ast.dump(ast.parse('a + 2', mode='eval'), annotate_fields=False))

class Names(ast.NodeVisitor):
    def __init__(self):
        self.names = []

    def visit_Name(self, node):
        self.names.append((node.id, type(node.ctx).__name__))


visitor = Names()
visitor.visit(ast.parse('a, *rest = data\ndel a\nobj.field = rest[0]'))
print(visitor.names)


class Rewrite(ast.NodeTransformer):
    def visit_Name(self, node):
        if node.id == 'old':
            return ast.copy_location(ast.Name(id='new', ctx=node.ctx), node)
        return node

    def visit_Pass(self, node):
        return None


rewritten = Rewrite().visit(ast.parse('old = 3\npass\nprint(old)'))
print(tree_dump(rewritten))
created = ast.fix_missing_locations(ast.Expression(body=ast.Constant(value=7)))
print(ast.dump(created, include_attributes=True))
print(ast.literal_eval('{"a": [1, -2, None], "b": (True, b"x"), "c": {2, 3}}'))
print(ast.literal_eval('1 + 2j'), ast.literal_eval('set()'), ast.literal_eval('...'))
try:
    ast.literal_eval('f()')
except ValueError:
    print('not a literal')
try:
    ast.parse('x =')
except SyntaxError:
    print('syntax error')
