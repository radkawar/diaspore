"""Focused Lua integer differential tests against CPython (no host bigint dependency)."""

import json
import math
import operator
import random
import shutil
import struct
import subprocess
from pathlib import Path

LUA_DIR = Path(__file__).resolve().parent.parent / "runtimes" / "lua"


def _run(cases):
    """Execute core calls and compare exact integer or binary64 results."""
    script = [
        'package.path = "./?.lua;./?/init.lua;" .. package.path',
        'local b = require("diaspore.bigint")',
        "local function emit(ok, x, y)",
        '  if not ok then print("error:" .. x.value.cls.name); return end',
        '  if math.type(x) == "float" then',
        '    print((string.pack(">d", x):gsub(".", function(c) '
        'return string.format("%02x", c:byte()) end)))',
        '  elseif y ~= nil then print(b.tostring(x) .. "," .. b.tostring(y))',
        '  elseif type(x) == "string" then print(x)',
        "  else print(b.tostring(x)) end",
        "end",
    ]
    expected = []
    for expression, value in cases:
        script.append(f"emit(pcall(function() return {expression} end))")
        if isinstance(value, type) and issubclass(value, Exception):
            expected.append("error:" + value.__name__)
        elif isinstance(value, float):
            expected.append(struct.pack(">d", value).hex())
        elif isinstance(value, tuple):
            expected.append(",".join(map(str, value)))
        else:
            expected.append(str(value))
    lua = shutil.which("lua5.4") or shutil.which("lua")
    assert lua is not None, "Lua 5.4 is required for the integer core tests"
    result = subprocess.run(
        [lua, "-"],
        input="\n".join(script),
        text=True,
        capture_output=True,
        cwd=LUA_DIR,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    actual = result.stdout.splitlines()
    assert len(actual) == len(expected), result.stderr
    for (expression, _), want, got in zip(cases, expected, actual, strict=True):
        assert got == want, f"{expression}: expected {want}, got {got}"


def _int(value):
    return f'b.parse("{value}")'


def _case(name, args, function):
    try:
        expected = function(*args)
    except (ValueError, OverflowError, ZeroDivisionError) as error:
        expected = type(error)
    return f"b.{name}({','.join(_int(a) for a in args)})", expected


def test_arithmetic_and_bitwise_differential():
    """Compare arithmetic and bit operations across native and promoted values."""
    rng = random.Random(982734)
    cases = []
    edges = [0, 1, -1, 2**63 - 1, -(2**63), 2**63, -(2**63) - 1]
    pairs = [(a, b) for a in edges for b in edges]
    for _ in range(250):
        a = rng.getrandbits(rng.randrange(1, 1800)) * rng.choice([-1, 1])
        b = rng.getrandbits(rng.randrange(1, 1200)) * rng.choice([-1, 1])
        pairs.append((a, b))
    binary = {
        "add": operator.add,
        "sub": operator.sub,
        "mul": operator.mul,
        "divmod": divmod,
        "band": operator.and_,
        "bor": operator.or_,
        "bxor": operator.xor,
        "cmp": lambda a, b: (a > b) - (a < b),
    }
    for a, b in pairs:
        for name, function in binary.items():
            cases.append(_case(name, (a, b), function))
        for name, function in [
            ("neg", operator.neg),
            ("abs", abs),
            ("bnot", operator.invert),
            ("hash", hash),
            ("bit_length", int.bit_length),
            ("bit_count", int.bit_count),
        ]:
            cases.append(_case(name, (a,), function))
        shift = rng.randrange(0, 2000)
        cases.append(_case("lshift", (a, shift), operator.lshift))
        cases.append(_case("rshift", (a, shift), operator.rshift))
    _run(cases)


def test_powers_parsing_bases_and_demotion():
    """Match Python powers, radix parsing, rendering, and integer demotion."""
    rng = random.Random(5723)
    cases = []
    for _ in range(100):
        a, e = rng.randrange(-(10**30), 10**30), rng.randrange(0, 45)
        cases.append(_case("pow", (a, e), pow))
        modulus = rng.randrange(-(10**45), 10**45) or 1
        cases.append(_case("pow", (a, rng.randrange(-50, 80), modulus), pow))
    for a in [0, 1, -1, 2**63, -(2**63), 2**256 + 9341, -(2**521 - 1)]:
        for base in range(2, 37):
            value, digits = abs(a), ""
            while value:
                value, rem = divmod(value, base)
                digits = "0123456789abcdefghijklmnopqrstuvwxyz"[rem] + digits
            text = ("-" if a < 0 else "") + (digits or "0")
            cases.append((f"b.tostring({_int(a)}, {base})", text))
            cases.append((f"b.parse({json.dumps(text)}, {base})", a))
    for text in [
        "0",
        "00",
        "00_0",
        "012",
        "0x_ff",
        "-0b_101",
        "+0o77",
        "1_2",
        "1__2",
        "_1",
        "1_",
        "0_x1",
        "0x",
        "",
        "  -123  ",
    ]:
        for base in [0, 2, 10, 16, 36]:
            try:
                expected = int(text, base)
            except ValueError:
                expected = ValueError
            cases.append((f"b.parse({json.dumps(text)}, {base})", expected))
    cases += [
        ('math.type(b.sub(b.parse("9223372036854775808"), 1))', "integer"),
        ('math.type(b.neg(b.parse("9223372036854775808")))', "integer"),
        ('math.type(b.mul(b.parse("9223372036854775808"), 0))', "integer"),
        ('math.type(b.rshift(b.parse("18446744073709551616"), 2))', "integer"),
    ]
    _run(cases)


def test_float_conversion_and_true_division():
    """Preserve Python rounding and overflow at integer-float boundaries."""
    rng = random.Random(43673)
    cases = []
    integers = [
        0,
        1,
        -1,
        2**53 - 1,
        2**53 + 1,
        2**53 + 3,
        2**63 - 1,
        -(2**63),
        2**1024 - 2**970 - 1,
        2**1024 - 2**970,
        2**1024,
        2**2000,
    ]
    for _ in range(180):
        integers.append(rng.getrandbits(rng.randrange(1, 2200)) * rng.choice([-1, 1]))
    for a in integers:
        cases.append(_case("to_float", (a,), float))
        b = rng.getrandbits(rng.randrange(1, 2300)) * rng.choice([-1, 1]) or 1
        cases.append(_case("truediv", (a, b), operator.truediv))
    for a, b in [
        (1, 2**1074),
        (1, 2**1075),
        (3, 2**1075),
        (5, 2**1075),
        (2**4000 + 1, 2**3990 + 3),
        (-(2**4000), 2**4000),
        (2**1024 - 2**970 - 1, 1),
        (2**1024 - 2**970, 1),
        (2**63 - 1, 3),
        (2**63 + 1, 3),
        (0, -(2**2000)),
    ]:
        cases.append(_case("truediv", (a, b), operator.truediv))
    floats = [
        0.0,
        -0.0,
        0.5,
        -0.5,
        1.5,
        -1.5,
        1e20,
        -1e300,
        5e-324,
        float.fromhex("0x1.fffffffffffffp+1023"),
    ]
    floats += [
        math.ldexp(rng.random() * rng.choice([-1, 1]), rng.randrange(-1074, 1024))
        for _ in range(100)
    ]
    for value in floats:
        cases.append((f"b.from_float({value.hex()})", int(value)))
        a = int(value) + rng.choice([-1, 0, 1])
        cases.append((f"b.cmp_float({_int(a)}, {value.hex()})", (a > value) - (a < value)))
    cases += [("b.from_float(math.huge)", OverflowError), ("b.from_float(0/0)", ValueError)]
    _run(cases)


def test_byte_conversion():
    """Match signed and unsigned byte conversions in both byte orders."""
    rng = random.Random(746823)
    cases = []
    for _ in range(180):
        data = rng.randbytes(rng.randrange(0, 150))
        literal = '"' + "".join(f"\\x{byte:02x}" for byte in data) + '"'
        for order in ["big", "little"]:
            for signed in [False, True]:
                value = int.from_bytes(data, order, signed=signed)
                arguments = f"{json.dumps(order)}, {str(signed).lower()}"
                cases.append((f"b.from_bytes({literal}, {arguments})", value))
                expression = f"b.to_bytes({_int(value)}, {len(data)}, {arguments})"
                cases.append(
                    (
                        f'({expression}:gsub(".", function(c) '
                        'return string.format("%02x", c:byte()) end))',
                        data.hex(),
                    )
                )
    for value, size, signed in [
        (256, 1, False),
        (-1, 1, False),
        (128, 1, True),
        (-129, 1, True),
        (1, 0, False),
        (-1, 0, True),
    ]:
        try:
            expected = value.to_bytes(size, "big", signed=signed).hex()
        except OverflowError:
            expected = OverflowError
        expression = f'b.to_bytes({_int(value)}, {size}, "big", {str(signed).lower()})'
        cases.append(
            (
                f'({expression}:gsub(".", function(c) return string.format("%02x", c:byte()) end))',
                expected,
            )
        )
    _run(cases)


def test_native_fast_paths_do_not_allocate():
    """Keep native integer operations allocation-free."""
    _run(
        [
            (
                """(function()
      local function exercise()
        for i = 1, 10000 do
          local x = b.add(i, 17)
          x = b.sub(x, 4)
          x = b.mul(x, 19)
          x = b.floordiv(x, 3)
          x = b.mod(x, 37)
          x = b.neg(x)
          x = b.abs(x)
          x = b.lshift(x, 4)
          x = b.rshift(x, 2)
          x = b.band(x, 127)
          x = b.bor(x, 8)
          x = b.bxor(x, 1)
          x = b.bnot(x)
          b.bit_length(x)
          b.bit_count(x)
          b.hash(x)
          b.cmp(x, 4)
          b.is_int(x)
          b.from_float(b.to_float(x))
          b.truediv(x, 17)
          b.pow(7, 10)
        end
      end
      exercise()
      collectgarbage("collect")
      collectgarbage("stop")
      local before = collectgarbage("count")
      exercise()
      local allocated = collectgarbage("count") - before
      collectgarbage("restart")
      return allocated
    end)()""",
                0.0,
            )
        ]
    )


def test_extreme_shift_counts():
    """Handle enormous and negative shift counts without host-sized truncation."""
    cases = []
    for value in [0, 1, -1, 2**100, -(2**100)]:
        for count in [-(10**100), -1, 10**100]:
            for name, function in [("lshift", operator.lshift), ("rshift", operator.rshift)]:
                cases.append(_case(name, (value, count), function))
    _run(cases)
