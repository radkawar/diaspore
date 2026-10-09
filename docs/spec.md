# Diaspore IR Specification (v1)

Diaspore compiles a substantial subset of Python into a portable JSON program
(a bundle, extension `.dsp.json`). The four runtimes are diaspore-py,
diaspore-go, diaspore-ts, and diaspore-lua. This document defines the shared IR
and execution contract. Runtime-specific limitations are documented in their
READMEs; not every runtime implements every operation below.

The typed IR in `src/diaspore/ir.py` defines the machine-readable structure.
Generate JSON Schema with `diaspore schema` or `make schema` (which writes
`build/ir.schema.json`), or use the
[published schema](https://radkawar.github.io/diaspore/ir.schema.json).

The Python package is in `src/diaspore`, including pure-subset standard-library
shims in `src/diaspore/stdlib`. The other runtimes are in `runtimes/go`,
`runtimes/ts`, and `runtimes/lua`. The self-hosted compiler is
`dspc` (`selfhost/dspc.py`).

## 1. Bundle format

```json
{
  "diaspore": 1,
  "main": "server",
  "modules": {
    "server": {"name": "server", "file": "examples/http/server.py", "body": [ ...stmt nodes... ]},
    "router": {"name": "router", "file": "examples/http/router.py", "body": [ ... ]}
  }
}
```

- `diaspore` - format version (integer `1`).
- `main` - name of the entry module. The VM executes its `body` top to bottom.
- `modules` - every user module the entry (transitively) imports, compiled in.
  Module names may be dotted: a package is a directory with `__init__.py`
  (its body compiles under the package's name, e.g. `"pkg"`, with submodules
  as `"pkg.sub"`). Importing a dotted module executes its ancestors first
  (each exactly once), and after a submodule executes, the VM sets it as an
  attribute (module-global) of its parent package - so `pkg.sub.deep` chains
  resolve as plain attribute access. Relative imports and `import *` do not
  reach the VM: the compiler resolves them statically (star-expansion uses the
  target's literal `__all__`, else its non-underscore top-level assignments).
- A module may carry `"src": ["line 1", ...]` (emitted by `diaspore build --debug`
  and always by `diaspore run x.py`): VMs then print the offending source line
  under each traceback frame. Purely diagnostic - never affects semantics.

The Python compiler API `compile_bundle(entry, search_paths=(...))` searches
the entry directory, supplied import roots in order, then the bundled standard
library. Pass `stdlib=False` to omit that final search root. The self-hosted
compiler accepts repeated `--path DIR` arguments and searches the entry
directory before those roots. These are compile-time source lookup paths;
the VM imports modules from the resulting bundle rather than searching the
host filesystem.

Every node is a JSON object with an `"op"` field. Statement nodes carry
`"ln"` (1-based source line) for tracebacks. Fields that are optional are
either absent or `null`; VMs must treat both identically.

## 2. Values

| Diaspore type | Notes |
|---|---|
| `int`     | Arbitrary precision (CPython semantics). The Go VM uses an int64 fast path and promotes to big integers on overflow, demoting results that fit - observable behavior is identical. |
| `float`   | IEEE 754 double. |
| `complex` | Real and imaginary components are IEEE 754 doubles. |
| `bool`    | `True`/`False`. `bool` is a subclass of `int` (`True == 1`, `isinstance(True, int)`). |
| `str`     | Unicode string. |
| `bytes`   | Immutable byte string. |
| `NoneType`| `None`. |
| `list`, `tuple`, `dict`, `set` | `dict` preserves insertion order (as in CPython). `set` iteration order is unspecified and VM-dependent (as in CPython, whose sets are hash-ordered) - programs must not depend on it; the conformance corpus always sorts sets before printing. |
| `range`   | Lazy; `range(start, stop, step)`. |
| function, bound method, class, instance, module, generator, file | See below. |

Hashable (usable as dict key / set member): `int`, `float`, `bool`, `str`,
`bytes`, `None`, `tuple` of hashables. Numeric keys unify like Python:
`1`, `1.0`, and `True` are the same key. Class instances hash by identity.

### 2.1 Constants

```json
{"op":"const","t":"int","v":"42"}
{"op":"const","t":"float","v":"2.5"}        // also "inf", "-inf", "nan"
{"op":"const","t":"complex","re":"1.0","im":"2.0"}
{"op":"const","t":"str","v":"hello"}
{"op":"const","t":"bytes","v":"aGVsbG8="}   // base64
{"op":"const","t":"bool","v":true}
{"op":"const","t":"none"}
{"op":"const","t":"ellipsis"}               // `...`: singleton, truthy, repr "Ellipsis"
```

Numbers are string-encoded so no JSON reader can corrupt them.

### 2.2 Canonical compact emission

Emitters omit fields whose value equals the default; readers MUST treat an
absent field as its default. The defaults: `orelse`/`finalbody`/`handlers`/
`kw`/`args`/`elts`/`entries`/`bases`/`gdecl`/`ndecl`/`locals`/`defaults`/
`kwonly`/`kwdefaults`/`kwargs` -> `[]`; `gen`/`coro`/`star` -> `false`; `vararg`/`kwarg`/`conv`/
`spec`/handler `t`/handler `name`/with-item `name` -> `null`; `alias` on a
`bind:"none"` import is omitted. (`const.t` is a discriminator and is never
omitted.) Verbose bundles that spell out defaults remain valid input
everywhere. This reduces bundle size and also applies inside the gzip container (section 15).

## 3. Expression nodes

| op | fields | meaning |
|---|---|---|
| `const`   | `t`, `v` | literal (see section 2.1) |
| `name`    | `id` | variable load (resolution: section 7) |
| `attr`    | `o`, `n` | `o.n` |
| `index`   | `o`, `k` | `o[k]`; `k` may be a `slicelit` |
| `slicelit`| `lower?`, `upper?`, `step?` (exprs) | only valid as `index.k` |
| `call`    | `f`, `args`: [expr \| `starred`], `kw`: [{`n`: str\|null, `v`: expr}] | `n: null` means `**expansion` |
| `binop`   | `kind`, `l`, `r` | `add sub mul div floordiv mod pow lshift rshift bitand bitor bitxor matmul` (`matmul` is dunder-only: no builtin type supports `@`) |
| `unary`   | `kind`, `v` | `neg pos not invert` |
| `boolop`  | `kind` (`and`/`or`), `values`: [expr] | short-circuit, returns last evaluated operand |
| `compare` | `left`, `ops`: [str], `comps`: [expr] | chained; kinds `eq ne lt le gt ge in notin is isnot` |
| `cond`    | `test`, `then`, `else` | ternary |
| `list` / `tuple` / `set` | `elts`: [expr \| `starred`] | display |
| `dict`    | `entries`: [{`k`: expr\|null, `v`: expr}] | `k: null` means `**expansion` |
| `starred` | `v` | only inside call args / displays / assign targets |
| `fstring` | `parts`: [{`s`: str} \| {`v`: expr, `conv`: "r"\|"s"\|"a"\|null, `spec`: expr\|null}] | section 10 format specs |
| `funcexpr`| same fields as `func` (section 5), `name` is `"<lambda>"`, `"<listcomp>"`, etc. | function as a value |
| `yield`   | `v?` | only inside generator functions |
| `yieldfrom` | `v` | delegate to sub-iterable |
| `namedexpr` | `target` (a `name`), `v` | walrus `:=`; assigns then yields the value |

The compiler desugars comprehensions (list/set/dict/generator) into an
immediately-invoked `funcexpr` whose parameter is named `.0` (the outermost
iterable is evaluated in the enclosing scope, per Python). Hidden temporaries
use dot-prefixed names (`.0`, `.r`, `.m1`, ...) which cannot collide with user
identifiers; they are *not* listed in `locals`, so they resolve through the
ordinary non-strict frame walk. Lambdas, decorators, `assert`, and `match`
statements (PEP 634 semantics: literal/capture/wildcard/or/as/sequence/
mapping/keyword-class patterns with guards; positional class patterns are
rejected) are also desugared - VMs never see them.

## 4. Statement nodes

| op | fields |
|---|---|
| `expr`     | `v` |
| `assign`   | `targets`: [target], `v` - multi-target `a = b = v` assigns left-to-right |
| `augassign`| `target` (name/attr/index), `kind` (binop kind), `v` - the object/index of an attr/index target is evaluated once |
| `if`       | `test`, `then`: [stmt], `orelse`: [stmt] |
| `while`    | `test`, `body`, `orelse` (runs if loop ends without `break`) |
| `for`      | `target`, `iter`, `body`, `orelse` |
| `break` / `continue` / `pass` | - |
| `func`     | section 5 |
| `return`   | `v?` |
| `class`    | `name`, `bases`: [expr], `kwargs`: [{`n`: str\|null, `v`: expr}], `body`: [stmt] |
| `raise`    | `exc?`, `cause?` (cause is evaluated then ignored except for chaining display, which VMs may omit) |
| `try`      | `star?` (bool; `except*`), `body`, `handlers`: [{`t`: expr\|null, `name`: str\|null, `body`}], `orelse`, `finalbody` |
| `with`     | `items`: [{`ctx`: expr, `name`: target\|null}], `body` |
| `import`   | `module`: str (may be dotted), `alias`: str?, `bind`: `"root"`\|`"none"`? - default binds the named module to `alias`; `"root"` binds the root package (plain `import a.b.c`); `"none"` executes without binding (compiler-emitted before `from pkg import submodule`) |
| `importfrom` | `module`: str, `names`: [{`n`: str, `alias`: str}] |
| `del`      | `targets`: [target] |

Targets are `name`, `attr`, `index`, or `tuple`/`list` nodes (patterns,
possibly containing one `starred` element for `a, *rest = ...`).

Slice mutation is supported on lists: `l[a:b] = iterable` (any step,
CPython semantics - extended slices require matching lengths) and
`del l[a:b:c]`. The Python VM inherits this from its host; the Go VM
implements it natively.

`except` handler semantics match Python: the bound name is deleted after the
handler body (even on exception). Handler `t` may be a single class or a tuple
expression. Bare `except:` has `t: null`.

## 5. Functions

```json
{"op":"func","name":"f","gen":false,
 "params":{"args":["a","b"],"defaults":[<expr>],
           "vararg":"rest"|null,
           "kwonly":["k"],"kwdefaults":[<expr>|null],
           "kwarg":"kw"|null},
 "locals":["a","b","rest","k","kw","x"],
 "gdecl":[], "ndecl":[],
 "body":[...], "ln":3}
```

- `defaults` align with the tail of `args`. `kwdefaults` align 1:1 with
  `kwonly` (null = required keyword-only).
- Defaults are evaluated once, at definition time, in the defining scope.
- `locals` is the complete set of local variable names (compiler-computed with
  Python's "assignment anywhere makes it local" rule). `gdecl`/`ndecl` are names
  declared `global`/`nonlocal` (the statements themselves are compiled away).
- `gen: true` marks a generator function (contains `yield`).
- `coro: true` records an `async def`; its `gen` is also true. `await` lowers
  to `yieldfrom` over the awaitable protocol. `async for` and `async with`
  are desugared by the compiler.

Argument binding follows Python: positionals fill `args` left to right,
overflow goes to `vararg` (a tuple) or raises `TypeError`; keywords fill
matching `args`/`kwonly` names, unknown keywords go to `kwarg` (a dict) or
raise `TypeError`; missing required args raise `TypeError`.
`*expr` in call args splices any iterable; `**expr` splices a dict (str keys).

Calling a generator function runs nothing; it returns a generator which
evaluates the body lazily. Generators support the iterator protocol
(`iter`/`next`, `for`, unpacking, `list(...)`). `return x` inside a generator
raises `StopIteration` with value `x` (delivered to `yield from`).
Generators also support `send(value)`, `throw(exception)`, and `close()`.

## 6. Classes and objects

`class` executes its body in a fresh namespace, then creates a class object with
the evaluated bases (default `object`). Multiple inheritance uses the C3 MRO
(same linearization as Python; `TypeError` on inconsistent hierarchies).

- Attribute lookup checks a `property` before the instance dict, then searches
  MRO class dicts. Plain functions found on a
  class bind as methods (`self` prepended). `staticmethod(f)` / `classmethod(f)`
  wrappers are supported as builtins.
- Instantiation `C(...)` creates the instance then calls `C.__init__` if defined
  (must return `None`).
- `super()` (zero-arg, inside a method) and `super(C, obj)` both work: lookup
  starts *after* `C` in `type(obj)`'s MRO.
- Supported dunder hooks on user classes:
  `__init__ __repr__ __str__ __eq__ __ne__ __lt__ __le__ __gt__ __ge__`
  `__add__ __radd__ __sub__ __rsub__ __mul__ __rmul__ __truediv__ __floordiv__ __mod__`
  `__matmul__ __rmatmul__` - and for augmented assignment, `__iadd__ __isub__
  __imul__ __imatmul__` etc. (`__i<op>__` tried first, falling back to the
  plain operator; there is no `NotImplemented` protocol) -
  `__neg__ __len__ __getitem__ __setitem__ __delitem__ __contains__ __iter__ __next__`
  `__call__ __bool__ __hash__`(ignored - identity hashing) `__enter__ __exit__ __getattr__`.
  `__eq__` falls back to identity; ordering ops without hooks raise `TypeError`.
  `x != y` uses `__ne__`, else negated `__eq__`. `in` uses `__contains__`, else iteration.
  `str(x)` uses `__str__`, else `__repr__`, else `<ClassName object>`.

Class keywords are carried in `kwargs`; `n: null` expands a mapping. An
explicit metaclass is called with `(name, bases, namespace)` and class keywords.
`type` and metaclass subclasses without construction hooks use ordinary class
creation. Remaining keywords are passed to a base `__init_subclass__` hook,
or raise `TypeError` when no hook accepts them. This is not the full CPython
metaclass protocol: class namespaces are created before the metaclass call.

## 7. Scoping and name resolution

Frames are dictionaries linked lexically (a closure captures its defining
frame). For a `name` load in a function whose `locals` contains `id`:
read the current frame - if unset, raise `UnboundLocalError` (a subclass of
`NameError`). Otherwise walk enclosing function frames (checking their dicts),
then module globals, then builtins; if unfound raise `NameError`.

Stores: if `id in gdecl` -> module globals. If `id in ndecl` -> the nearest
enclosing frame whose `locals` contains `id` (compiler guarantees existence).
Otherwise the current frame (module frame at top level). Closures therefore
share state naturally (`nonlocal` counters work; late binding in loops matches
Python).

Two VM-implementation notes that are part of the contract:

- Class bodies execute in their own (non-strict) frame, but a function
  defined inside a class body captures the nearest enclosing *non-class* frame
  as its closure - methods do not see class attributes as bare names (Python
  semantics).
- Generators may be implemented with any concurrency primitive (the Python
  VM uses a paused thread, the Go VM a goroutine); execution must remain
  strictly ping-pong (the consumer is blocked while the generator body runs),
  and a generator abandoned before exhaustion need not run pending `finally`
  blocks.

## 8. Exceptions

Core builtin exception hierarchy:

```
BaseException
|- KeyboardInterrupt
|- SystemExit
+- Exception
   |- ArithmeticError -- ZeroDivisionError, OverflowError
   |- AssertionError
   |- AttributeError
   |- LookupError -- IndexError, KeyError
   |- NameError -- UnboundLocalError
   |- OSError -- PermissionError
   |- RuntimeError -- NotImplementedError, RecursionError
   |- StopIteration
   |- TypeError
   +- ValueError
```

Additional exception classes include `GeneratorExit`, `StopAsyncIteration`,
`ImportError`/`ModuleNotFoundError`, and `SyntaxError`/`IndentationError`/`TabError`.
`BaseExceptionGroup` derives from `BaseException`; `ExceptionGroup` derives
from both `BaseExceptionGroup` and `Exception`.

`raise C` auto-instantiates `C()`. Exception instances expose `.args` (tuple);
`str(e)` is `""` for no args, `str(args[0])` for one, else `repr(args)`.
`except (A, B) as e:` matches by `isinstance`. User classes may subclass any
builtin exception. An uncaught exception prints a traceback to stderr
(`module:line` frames, innermost last, then `Type: message`) and exits with
status 1. `SystemExit(code)` exits silently with that code.

Message parity: for the operations the conformance suite exercises, the runtimes
raise the same exception *type* and the same *message* - the CPython message
verbatim (e.g. `division by zero`, `list index out of range`,
`'str' object has no attribute 'foo'`). See `tests/corpus/` for the pinned set.

## 9. Builtins

`print(*a, sep=" ", end="\n")`, `len`, `range`, `enumerate(it, start=0)`,
`zip`, `map`, `filter` (these three return lazy iterators), `reversed`,
`sorted(it, key=None, reverse=False)` (stable), `sum(it, start=0)`, `min`/`max`
(1 iterable or 2+ args; optional `key=`, `default=`), `any`, `all`, `abs`,
`round(x, nd=None)` (banker's rounding; `round(2.5) == 2`; with `nd` returns float),
`divmod`, `pow(a, b)`, `int(x, base=10)`, `float`, `str`, `bool`, `bytes`,
`list`, `tuple`, `dict`, `set`, `ord`, `chr`, `hex`, `bin`, `oct`, `repr`,
`type` (1-arg inspection or 3-arg class construction), `isinstance`, `issubclass`, `callable`, `hash`,
`iter` (1-arg), `next(it, default?)`, `getattr(o, n, default?)`, `setattr`,
`hasattr`, `id`, `input(prompt?)`, `open(path, mode="r")` (section 11.7),
`staticmethod`, `classmethod`, `property`, `super`, `exit(code=0)`, `ascii(v)`
(repr with non-ASCII backslash-escaped; also the f-string `!a` conversion),
`format(v, spec="")` (the section 10 format engine), `object` (universal base:
every class ends its MRO with it; `isinstance(x, object)` is `True` for every
value), `Ellipsis` - plus every exception class from section 8, and the builtin type
objects (`type(1) is int`, `print(type(1))` -> `<class 'int'>`).

Every module executes with `__name__` bound in its globals: `"__main__"` for
the entry module, the module's own name otherwise.

`bool(x)`: `False` for `None`, `False`, numeric zero, empty str/bytes/
containers/range; user classes via `__bool__` then `__len__`; else `True`.

### 9.1 Methods on builtin types (the pinned reflection surface)

The Python VM implements these by reflection into the host type (a curated
allowlist so the runtimes stay in lockstep); the Go VM implements them natively
with CPython semantics.

- str: `upper lower title capitalize casefold strip lstrip rstrip split rsplit splitlines
  join replace find rfind index count startswith endswith isdigit isalpha isalnum isspace
  islower isupper swapcase zfill ljust rjust center encode
  partition rpartition removeprefix removesuffix` - `split(None)` collapses
  whitespace runs; `split(sep)` keeps empties; `strip` variants take an optional
  char-set string; `startswith`/`endswith` accept a tuple; `partition` returns a
  3-tuple (`(s, "", "")` on no match; `rpartition` -> `("", "", s)`).
- list: `append extend insert pop remove clear index count reverse sort copy`
  (`sort(key=None, reverse=False)`, stable).
- dict: `get keys values items pop update setdefault clear copy` -
  `keys()/values()/items()` return lists (documented deviation from CPython's
  views; `items()` is a list of tuples).
- set: `add remove discard clear copy union intersection difference
  symmetric_difference issubset issuperset` (the `| & - ^` operators work too).
- tuple: `count index`.
- bytes: `decode(encoding="utf-8") find split strip lstrip rstrip replace
  startswith endswith upper lower`.
- generator: `send`, `throw`, `close`; iterators support the iterator protocol.

`str(bytes)` is the repr (`b'...'`). `str.encode`/`bytes.decode` support
`"utf-8"` and `"latin-1"`.

## 10. Formatting (exact-parity requirements)

- `str(float)` must match CPython's `repr`: shortest round-trip digits;
  scientific notation only when `exp < -4` or `exp >= 16`; integral floats get
  `.0`; exponent styled `e+NN`/`e-NN` (two+ digits). Examples:
  `0.1` -> `0.1`, `1/3` -> `0.3333333333333333`, `1e16` -> `1e+16`,
  `1e15` -> `1000000000000000.0`, `123456789.0` -> `123456789.0`,
  `1e-4` -> `0.0001`, `1e-5` -> `1e-05`, `float('inf')` -> `inf`, `nan` -> `nan`.
- `repr(str)`: prefer single quotes; use double quotes iff the string contains
  `'` and not `"`. Escape `\\`, the quote, `\n`, `\r`, `\t`; other chars < 0x20
  as `\xNN`. Non-ASCII printables pass through.
- Container reprs recurse with `repr` and CPython spacing: `[1, 2]`,
  `(1,)`, `{'a': 1}`, `{1, 2}`, `set()`, `range(0, 5)`.
- f-string format specs implement this subset of the mini-language:
  `[[fill]align][sign][0][width][,][.precision][type]` with types
  `d b o x X f e g % s` and none. Alignment `< > ^`; sign `+ -` and space; `,`
  thousands grouping for `d`/`f`/none-numeric; `.Nf` fixed precision;
  default float formatting is `str(float)`. `!r` applies `repr` first.

## 11. Native modules

Importable without being in the bundle. `import time`, `from math import sqrt`,
etc. If a bundled user module shares a name, the bundle wins.

- 11.1 `time` - `time()` (epoch float), `monotonic()`, `sleep(secs)`.
- 11.2 `math` - `pi e inf nan sqrt floor ceil fabs pow log log2 log10 exp sin cos tan hypot fmod isnan isinf`.
  `floor`/`ceil` return ints. Domain errors raise `ValueError: math domain error`.
- 11.3 `json` - `dumps(v, indent=None)` / `loads(s)`; CPython separators
  (`, ` and `: `; with indent: `,` + newline). Keys must be str; values map
  to diaspore types (`loads` numbers: int if integral literal else float).
- 11.4 `random` - `seed(n)`, `random()`, `randint(a, b)`, `choice(seq)`,
  `shuffle(list)`. *Sequences are VM-specific* - excluded from conformance.
- 11.5 `sys` - `argv` (list[str]: program name then args after `--`),
  `exit(code=0)`, `platform` (`"diaspore-py"`, `"diaspore-go"`, `"diaspore-ts"`, or `"diaspore-lua"`), `stderr_write(s)`.
- 11.6 `net` (TCP) -
  `listen(host, port) -> int` (listener id; SO_REUSEADDR),
  `connect(host, port) -> int` (outbound; connection id),
  `accept(lid) -> int` (blocks; connection id),
  `recv(cid, maxbytes) -> bytes` (empty = peer closed),
  `send(cid, data: bytes) -> int` (sends all; returns len),
  `close(id)`, `peer(cid) -> str` ("ip:port").
  Errors raise `OSError`. `listen` and `connect` are additionally checked
  against the scoped net policy (section 13.1) when one is set.
- 11.7 `fs` is *not* a module - file I/O goes through builtin `open`:
  modes `r w a rb wb`; methods `read(n=-1) readline() write(data) close()`;
  usable as a context manager; text mode is UTF-8.
- 11.8 `re` - a *validated portable subset* of Python regular expressions
  (chosen so RE2 engines implement it exactly; patterns outside it raise
  `ValueError` on the runtimes rather than silently diverging).
  Allowed: literals, `.`, `[...]` classes (ranges, negation), escapes
  (`\d \D \w \W \s \S \b \B` and escaped metachars), anchors `^ $`,
  quantifiers `* + ? {m} {m,n}` plus non-greedy variants, groups `(...)`,
  non-capturing `(?:...)`, alternation `|`, inline flags `(?ims)`.
  Rejected: backreferences, lookaround, named/atomic/conditional groups,
  `\A`/`\Z`, possessive quantifiers.
  API: `search(p, s, flags=0)`, `match`, `fullmatch` -> Match-or-None;
  `findall` (CPython result-shape rules), `split(p, s, maxsplit=0)`
  (capturing groups appear in the result), `sub(p, repl, s, count=0)`
  (repl supports `\1`-`\9` and `\\` only), `escape`;
  flags `IGNORECASE=2, MULTILINE=8, DOTALL=16`.
  Match objects: `group(n=0)`, `groups()` (unmatched -> `None`),
  `start/end/span(n=0)`.
- 11.9 `tasks` (requires the `spawn` capability) - concurrency with
  GIL semantics: one logical interpreter lock, diaspore-level operations are
  atomic, and the lock is released while blocked (channel ops, lock acquire,
  `net` calls, `time.sleep`, `join`). Tasks interleave; they do not run diaspore
  code in parallel. The Python VM uses host threads (CPython's GIL provides
  the model); the Go VM uses goroutines under an explicit run-lock.
  API: `spawn(fn, *args) -> Task` (`.join()` blocks and returns the result or
  re-raises the task's exception; `.done() -> bool`);
  `channel(capacity=0) -> Channel` (`capacity=0` unbounded, else bounded
  blocking; `.send(v)`, `.recv()`, `.close()`, `.len()`; iterating a channel
  yields values until it is closed; `send` on a closed channel raises
  `RuntimeError: send on closed channel`, `recv` on a closed drained channel
  `RuntimeError: recv on closed channel`);
  `lock() -> Lock` (`.acquire()`, `.release()`, usable with `with`).
  Programs are responsible for their own determinism; the conformance corpus
  uses join ordering and channel causality (concurrency programs are pinned
  with committed `.expected` files, since CPython has no `tasks`).

## 12. Deliberate deviations and exclusions

The compiler supports `async`/`await`, `async for`, `async with`, complex
literals, class keywords including `metaclass`, and `except*`. The runtimes
provide `property` and generator `send`/`throw`/`close`. Exception groups expose
`message` and `exceptions`; `except*` handlers select matching subgroups.

`exec`, `eval`, and `compile` operate on Diaspore-subset code, not host Python.
Go embedding requires `interp.WithSnippetCompiler(dspc.CompileSnippet)` for
source snippets. TypeScript uses its embedded compiler; the Python VM uses its
compiler. Lua does not provide runtime source compilation.

Unsupported or restricted features include:

- Host threads; use the `tasks` API where available (section 11.9).
- General user-defined descriptor protocols and `__slots__` storage semantics.
  Builtin `property` is supported, but is not a general descriptor implementation.
- Custom instance hashing in the reference VM: dictionary and set keys use
  identity for user instances.
- `str.format`; use f-strings.
- Positional class patterns in `match`; keyword class patterns are supported.
- Percent-formatting on strings and bytes. It raises `TypeError` with
  `diaspore does not support %-formatting; use f-strings`.

The compiler reports unsupported syntax with a `file:line` error.
`tests/corpus/` compares supported programs with CPython and the four runtimes
(diaspore-py, diaspore-go, diaspore-ts, diaspore-lua). Runtime-specific exclusions
and programs with native APIs are handled separately; this is not a claim of
complete CPython compatibility.

Behavior not fixed by the shared conformance contract includes distinct NaN
objects in containers, `re.split` with negative `maxsplit`, whether abandoned
generators run `finally` during collection (section 7), and exact closed-file
I/O error text. Programs must not depend on these details. `with` does run
`__exit__` on `return` and `break`.

## 13. Sandboxing: capabilities, limits, bindings

Programs have no ambient filesystem or network authority. Standard streams,
clock access, and randomness are available without capability grants. Other
external operations require a capability or an explicit host binding.

### 13.1 Capabilities

| capability | gates | CLI flag |
|---|---|---|
| `net`   | the `net` module   | `--allow-net` |
| `fs`    | builtin `open`     | `--allow-fs` |
| `spawn` | the `tasks` module | `--allow-spawn` |

A gated call without its grant raises `PermissionError` (message:
`` diaspore: capability 'net' not granted (run with --allow-net) `` - same text in
the runtimes, so policy failures are testable). `time`, `math`, `json`, `random`,
`re`, `sys` are always available.

Scoped grants. A bare flag grants everything; repeatable scoped specs
narrow it to an explicit allowlist (anything unlisted is denied):

```
--allow-net listen:127.0.0.1:8080  --allow-net connect:api.example.com:443
--allow-net 'connect:*:443'                 # '*' wildcards host or port
--allow-fs  read:/data  --allow-fs write:/tmp/out   # real-path prefix match
```

Denials read `` diaspore: net.connect api.evil.com:80 not permitted by policy `` /
`` diaspore: fs.read /etc/shadow not permitted by policy ``. Embedding APIs take
the same shape as a `policy` mapping
(`{"net": {"listen": [...], "connect": [...]}, "fs": {"read": [...], "write": [...]}}`).

Deterministic mode (`--deterministic` / `deterministic=True`): a logical
clock (starts at 0, +1ms per `time()` call; `sleep(s)` advances it without
sleeping) and a fixed-seed RNG make runs bit-identical; capabilities are
forbidden. For record/replay determinism with real effects, use durable flows
(section 14) instead.

### 13.2 Resource limits

- Step budget ("fuel") - `--max-steps N`: every statement execution and
  function call consumes fuel, and bulk builtins (`sum`, `list(range(...))`,
  `sorted`, `min`/`max`, `any`/`all`) charge at least one unit per element so
  wide host-speed loops can't sidestep the budget. Exhaustion is a trap, not
  an exception: sandboxed code cannot catch it. The VM prints
  `diaspore: step budget exceeded (N)` to stderr and exits with status 3.
  Default: unlimited. Fuel bounds *CPU loops*, not allocation size - for
  hostile tenants, pair it with OS-level memory/time limits on the VM process.
- Recursion depth - `--max-depth N` (default 1000 diaspore frames): exceeding
  it raises `RecursionError: maximum recursion depth exceeded` (catchable,
  like Python).
- Wall-clock - hosts embed their own timeouts around `Run` (not a VM flag).

Embedded VMs expose the same knobs programmatically.

### 13.3 Host bindings (embedding API)

The four runtimes are embeddable libraries; the CLI is a thin wrapper. A host registers
functions under a native module name (conventionally `host`), and the sandboxed
program imports and calls them like any module:

```python
# host side (Python)
from diaspore.vm import Machine
m = Machine(bundle, bindings={"host": {"lookup": my_fn}}, max_steps=1_000_000)
m.run()
```

```go
// host side (Go)
vm := interp.New(bundle, interp.WithBindings("host", map[string]interp.NativeFunc{...}),
                 interp.WithMaxSteps(1_000_000))
code := vm.Run()
```

Binding functions receive diaspore values and return one diaspore value (Python host:
natural Python values; Go host: `interp.Value`). They may raise/return errors,
which surface in the sandbox as diaspore exceptions. A user module in the bundle
shadows a binding module of the same name - hosts should pick distinct names.
Binding modules are declared at compile time (`diaspore build --binding host`,
or `compile_bundle(entry, extra_modules={"host"})`) so import resolution stays
fully static. `examples/embedding/` demonstrates both directions.

---

## 14. Durable flows (suspend / resume)

Durable mode pauses execution into a portable JSON snapshot and resumes it
later in another process or on another compatible runtime.

The mechanism is *journaled replay* (no interpreter state is serialized):

- In a durable run (`diaspore run --durable` / `Machine(durable=True)`), every
  nondeterministic native call is an effect: `time.time/monotonic/sleep`,
  all `random.*`, `input`, and every host-binding call. Live effects execute
  and append `{"m": module, "f": fn, "r": result}` to the journal; results
  must be canonical JSON values (no tuples) so replay is exact.
- `flow.suspend(payload)` (native module `flow`; payload must be JSON)
  aborts the execution mid-flight - no further diaspore code runs, *including
  `finally` blocks* - and hands the host `payload` plus a snapshot:

  ```json
  {"diaspore_flow": 1, "bundle_sha256": "...", "argv": [...],
   "journal": [{"m": "time", "f": "time", "r": 1787570616.82}, ...]}
  ```
- Resume re-executes the bundle from the top in *replay* mode: journaled
  effects return their recorded results without executing, stdout/stderr is
  suppressed, and the pending `suspend` returns the host-provided `send`
  value (appended to the journal as `{"m":"flow","f":"suspend","r":send}`).
  When the journal is exhausted, execution continues live.
- A replay that requests a different effect than the journal recorded is a
  trap (`diaspore: journal divergence ...`, exit 5): durable programs must be
  deterministic between effects. Durable mode forbids capabilities
  (`net`/`fs`/`spawn`); route I/O through host bindings and avoid relying on
  nonportable values such as object identities or set iteration order.
- `bundle_sha256` is the SHA-256 of the bundle's canonical JSON: UTF-8,
  compact separators (`,`/`:`), keys sorted, non-ASCII unescaped, no
  HTML-escaping. Resume must reject a snapshot whose digest doesn't match.
- CLI: suspend exits with code 7 and writes the snapshot
  (`--snapshot PATH`, default `TARGET.flow.json`);
  `diaspore resume BUNDLE SNAPSHOT [--send JSON] [--snapshot OUT]` continues.
  Embedding: `run_durable()` / `resume(snapshot, send)` return
  `Completed(code)` or `Suspended(payload, snapshot)`.

## 15. Bundle encodings

JSON is the canonical form for hashing, validation, tooling, and human
inspection. Containers are
a transport detail sniffed by magic bytes on load:

- plain JSON (default output)
- gzip (`1f 8b`, `x.dsp.json.gz`)
- DSPB (`DSPB01`, `x.dspb`, [docs/dspb.md](dspb.md)): optional binary
  encoding. Gzip decoding re-sniffs its payload, so `x.dspb.gz` is also valid.
  `decode(encode(b)) == compact(b)` exactly; digests are computed on the
  decoded model, so the container never affects durable flows.

The IR structure is defined by `src/diaspore/ir.py` and its generated
[JSON Schema](https://radkawar.github.io/diaspore/ir.schema.json), regardless of container.
