from __future__ import annotations

module_value: list [ int ] = []
print(__annotations__)


class Model:
    plain: MissingName
    quoted: "Forward"
    generic: dict [ str , list [ int ] ]
    union: int|str|None
    tuple_index: tuple[int, str]
    singleton: tuple[int,]
    sliced: Missing [ 1 : 9 : 2 ]
    call: make_type(1, key = "value")
    arithmetic: (a + b) * (c - d)
    conditional: left if flag else right
    nested: (left if flag else right) | int
    literal: {"one": 1, "two": -2}
    function: lambda x: x + 1
    assigned: MissingName = 8


for key in Model.__annotations__:
    print(key, Model.__annotations__[key])
print(Model.assigned)


def function(arg: unknown_name) -> another_name:
    local: not_evaluated = arg
    return local


print(function(5))
