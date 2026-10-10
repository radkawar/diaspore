-- Native modules and capability-checked host I/O.

local values = require("diaspore.values")
local format = require("diaspore.format")
local H = require("diaspore.helpers")
local bigint = require("diaspore.bigint")
local ops = require("diaspore.ops")

local M = {}

local NONE = values.NONE
local throw = values.throw
local type_name = values.type_name
local as_index = values.as_index
local as_float = values.as_float
local bi = values.builtin
local arg_count = H.arg_count

local function native_module(name, ns)
  ns.__name__ = name
  if ns.__doc__ == nil then ns.__doc__ = NONE end
  return { kind = "nativemodule", name = name, ns = ns }
end

local function is_none(v)
  return type(v) == "table" and v.kind == "none"
end

-- Pure Lua has no task or network provider.
local function stub_module(name, fns)
  local ns = {}
  for _, f in ipairs(fns) do
    ns[f] = bi(f, function()
      throw("RuntimeError", "diaspore-lua does not support " .. name .. "")
    end)
  end
  return native_module(name, ns)
end

-- ---------------------------------------------------------------------------
-- time

local function make_time_module(I)
  local time_fn, mono_fn, sleep_fn
  if I.deterministic then
    local clock = 0.0
    local function tick()
      clock = format.round6(clock + 0.001)
      return clock
    end
    time_fn = bi("time", tick)
    mono_fn = bi("monotonic", tick)
    sleep_fn = bi("sleep", function(_I, args, kw)
      arg_count("sleep", args, 1, 1)
      local secs = as_float(args[1])
      if secs == nil then throw("TypeError", "an integer or float is required") end
      clock = format.round6(clock + secs)
      return NONE
    end)
  else
    time_fn = bi("time", function()
      return os.time() + 0.0
    end)
    mono_fn = bi("monotonic", function()
      return os.clock() + 0.0
    end)
    sleep_fn = bi("sleep", function(_I, args, kw)
      arg_count("sleep", args, 1, 1)
      local secs = as_float(args[1])
      if secs == nil then throw("TypeError", "an integer or float is required") end
      if secs < 0 then throw("ValueError", "sleep length must be non-negative") end
      -- pure-Lua sleep: busy wait on the CPU clock
      local until_ = os.clock() + secs
      while os.clock() < until_ do end
      return NONE
    end)
  end
  return native_module("time", {
    time = time_fn,
    monotonic = mono_fn,
    sleep = sleep_fn,
  })
end

-- ---------------------------------------------------------------------------
-- math

local function domain_err()
  throw("ValueError", "math domain error")
end

local function math_fn(name, arity, f)
  return bi(name, function(_I, args, kw)
    arg_count(name, args, arity, arity)
    local xs = {}
    for i = 1, #args do
      local x = as_float(args[i])
      if x == nil then
        throw("TypeError", "must be real number, not " .. type_name(args[i]))
      end
      xs[i] = x
    end
    return f(xs)
  end)
end

local function make_math_module()
  local coefficients = { 676.5203681218851, -1259.1392167224028,
    771.32342877765313, -176.61502916214059, 12.507343278686905,
    -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7 }
  local function log_gamma(x)
    if x ~= x then return x end
    if math.abs(x) == math.huge then return math.huge end
    if x <= 0 and x == math.floor(x) then domain_err() end
    if x < 0.5 then return math.log(math.pi) - math.log(math.abs(math.sin(math.pi * x))) - log_gamma(1 - x) end
    x = x - 1
    local sum = 0.99999999999980993
    for i, coefficient in ipairs(coefficients) do sum = sum + coefficient / (x + i) end
    local t = x + 7.5
    local result = 0.9189385332046727 + (x + 0.5) * math.log(t) - t + math.log(sum)
    if result == math.huge then throw("OverflowError", "math range error") end
    return result
  end
  local function int_round(name, f)
    return bi(name, function(_I, args, kw)
      arg_count(name, args, 1, 1)
      local n = as_index(args[1])
      if n ~= nil then return n end
      if math.type(args[1]) ~= "float" then
        throw("TypeError", "must be real number, not " .. type_name(args[1]))
      end
      local x = args[1]
      if x ~= x then throw("ValueError", "cannot convert float NaN to integer") end
      if x == math.huge or x == -math.huge then
        throw("OverflowError", "cannot convert float infinity to integer")
      end
      return bigint.from_float(f(x))
    end)
  end
  return native_module("math", {
    pi = math.pi,
    tau = 2 * math.pi,
    acos = math_fn("acos", 1, function(xs)
      if xs[1] < -1 or xs[1] > 1 then domain_err() end
      return math.acos(xs[1])
    end),
    isfinite = math_fn("isfinite", 1, function(xs)
      return xs[1] == xs[1] and math.abs(xs[1]) ~= math.huge
    end),
    lgamma = math_fn("lgamma", 1, function(xs) return log_gamma(xs[1]) end),
    e = math.exp(1.0),
    inf = math.huge,
    nan = 0 / 0,
    floor = int_round("floor", math.floor),
    ceil = int_round("ceil", math.ceil),
    sqrt = math_fn("sqrt", 1, function(xs)
      if xs[1] < 0 then domain_err() end
      return math.sqrt(xs[1])
    end),
    fabs = math_fn("fabs", 1, function(xs) return math.abs(xs[1]) end),
    copysign = math_fn("copysign", 2, function(xs)
      local negative = string.pack(">d", xs[2]):byte(1) >= 128
      return negative and -math.abs(xs[1]) or math.abs(xs[1])
    end),
    frexp = math_fn("frexp", 1, function(xs)
      local x = xs[1]
      if x == 0 or x ~= x or math.abs(x) == math.huge then
        return values.tuple({ x, 0 })
      end
      local bits = string.unpack(">I8", string.pack(">d", x))
      local exponent = (bits >> 52) & 0x7ff
      local shift = 0
      if exponent == 0 then
        x = x * 2.0 ^ 54
        bits = string.unpack(">I8", string.pack(">d", x))
        exponent = (bits >> 52) & 0x7ff
        shift = 54
      end
      local fraction = string.unpack(">d", string.pack(">I8",
        (bits & 0x800fffffffffffff) | (1022 << 52)))
      return values.tuple({ fraction, exponent - 1022 - shift })
    end),
    ldexp = bi("ldexp", function(_I, args, kw)
      arg_count("ldexp", args, 2, 2)
      H.no_kw("ldexp", kw)
      local x, exponent = as_float(args[1]), as_index(args[2])
      if x == nil then throw("TypeError", "must be real number, not " .. type_name(args[1])) end
      if exponent == nil then throw("TypeError", "Expected an int as second argument to ldexp.") end
      if x == 0 or x ~= x or math.abs(x) == math.huge then return x end
      if bigint.cmp(exponent, 2098) > 0 then throw("OverflowError", "math range error") end
      if bigint.cmp(exponent, -2098) < 0 then return x * 0.0 end
      while exponent > 1023 do x = x * 2.0 ^ 1023; exponent = exponent - 1023 end
      while exponent < -1022 do x = x * 2.0 ^ -1022; exponent = exponent + 1022 end
      local result = x * 2.0 ^ exponent
      if math.abs(result) == math.huge then throw("OverflowError", "math range error") end
      return result
    end),
    pow = math_fn("pow", 2, function(xs)
      local x, y = xs[1], xs[2]
      if x == 0.0 and y < 0 then domain_err() end
      if x < 0 and y ~= math.floor(y) and y ~= math.huge and y ~= -math.huge then
        domain_err()
      end
      return x ^ y
    end),
    log = bi("log", function(_I, args, kw)
      arg_count("log", args, 1, 2); H.no_kw("log", kw)
      local x = as_float(args[1])
      if x == nil then throw("TypeError", "must be real number") end
      if x <= 0 then domain_err() end
      if #args == 1 then return math.log(x) end
      local base = as_float(args[2])
      if base == nil then throw("TypeError", "must be real number") end
      if base <= 0 then domain_err() end
      if base == 1 then throw("ZeroDivisionError", "float division by zero") end
      return math.log(x) / math.log(base)
    end),
    log2 = math_fn("log2", 1, function(xs)
      if xs[1] <= 0 then domain_err() end
      return math.log(xs[1], 2)
    end),
    log10 = math_fn("log10", 1, function(xs)
      if xs[1] <= 0 then domain_err() end
      return math.log(xs[1], 10)
    end),
    exp = math_fn("exp", 1, function(xs) return math.exp(xs[1]) end),
    sin = math_fn("sin", 1, function(xs) return math.sin(xs[1]) end),
    cos = math_fn("cos", 1, function(xs) return math.cos(xs[1]) end),
    tan = math_fn("tan", 1, function(xs) return math.tan(xs[1]) end),
    hypot = math_fn("hypot", 2, function(xs)
      local x, y = math.abs(xs[1]), math.abs(xs[2])
      if x == math.huge or y == math.huge then return math.huge end
      if x < y then x, y = y, x end
      if x == 0.0 then return 0.0 end
      local r = y / x
      return x * math.sqrt(1.0 + r * r)
    end),
    fmod = math_fn("fmod", 2, function(xs)
      if xs[2] == 0.0 then domain_err() end
      return math.fmod(xs[1], xs[2])
    end),
    isnan = bi("isnan", function(_I, args, kw)
      arg_count("isnan", args, 1, 1)
      local x = as_float(args[1])
      if x == nil then
        throw("TypeError", "must be real number, not " .. type_name(args[1]))
      end
      return x ~= x
    end),
    isinf = bi("isinf", function(_I, args, kw)
      arg_count("isinf", args, 1, 1)
      local x = as_float(args[1])
      if x == nil then
        throw("TypeError", "must be real number, not " .. type_name(args[1]))
      end
      return x == math.huge or x == -math.huge
    end),
  })
end

-- ---------------------------------------------------------------------------
-- sys

local function sys_stream(I, name)
  local methods = {}
  if name == "stdin" then
    local function read_size(method, args, kw)
      arg_count(method, args, 0, 1)
      H.no_kw(method, kw)
      if #args == 0 or is_none(args[1]) then return -1 end
      local size = as_index(args[1])
      if size == nil then throw("TypeError", "argument should be integer or None") end
      return values.to_size(size)
    end
    methods.read = bi("read", function(_I, args, kw)
      local size = read_size("read", args, kw)
      if size == 0 then return "" end
      local text, message = io.stdin:read(size < 0 and "a" or size)
      if message ~= nil then throw("OSError", message) end
      return text or ""
    end)
    methods.readline = bi("readline", function(_I, args, kw)
      local size = read_size("readline", args, kw)
      if size < 0 then
        local text, message = io.stdin:read("L")
        if message ~= nil then throw("OSError", message) end
        return text or ""
      end
      local parts = {}
      for _ = 1, size do
        local ch, message = io.stdin:read(1)
        if message ~= nil then throw("OSError", message) end
        if ch == nil then break end
        parts[#parts + 1] = ch
        if ch == "\n" then break end
      end
      return table.concat(parts)
    end)
  else
    methods.write = bi("write", function(_I, args, kw)
      arg_count("write", args, 1, 1)
      H.no_kw("write", kw)
      local text = H.want_str("write() argument", args[1])
      if name == "stdout" then I:write(text) else I:ewrite(text) end
      return values.str_len(text)
    end)
  end
  methods.flush = bi("flush", function(_I, args, kw)
    arg_count("flush", args, 0, 0)
    H.no_kw("flush", kw)
    if name == "stdout" then I:flush()
    elseif name == "stderr" and not I.stderr then io.stderr:flush() end
    return NONE
  end)
  return { kind = "nativeobject", nkind = "TextIOWrapper", methods = methods,
    props = { name = "<" .. name .. ">", mode = name == "stdin" and "r" or "w",
      encoding = "utf-8", closed = false } }
end

local function make_sys_module(I)
  local argv = {}
  for i = 1, #I.argv do argv[i] = I.argv[i] end
  local namespace = values.new_class("SimpleNamespace", {}, {})
  namespace.interp = I
  namespace.ns.__init__ = bi("__init__", function(_, args, kw)
    arg_count("SimpleNamespace", args, 1, 1)
    for _, entry in ipairs(kw) do args[1].d[entry.k] = entry.v end
    return NONE
  end)
  local function record(fields)
    local result = values.new_instance(namespace)
    result.d = fields
    return result
  end
  local version_type = values.new_class("version_info", { I.types.tuple }, {})
  local version = values.new_instance(version_type)
  version.prim = values.tuple({ 3, 12, 12, "final", 0 })
  version.d = { major = 3, minor = 12, micro = 12, releaselevel = "final", serial = 0 }
  local function numeric_info(name, names, items)
    local cls = values.new_class(name, { I.types.tuple }, {
      __module__ = "sys", __qualname__ = name, __slots__ = values.tuple({}),
      n_fields = #names, n_sequence_fields = #names, n_unnamed_fields = 0,
    })
    cls.interp, cls.has_dict, cls.has_weakref = I, false, false
    local indices = {}
    local function readonly() throw("AttributeError", "readonly attribute") end
    for index, field in ipairs(names) do
      indices[field] = index
      cls.ns[field] = values.property(bi(field, function(_, args)
        return args[1].prim.items[index]
      end), bi(field, readonly), bi(field, readonly))
    end
    local function set_attr(_, args)
      local field = args[2]
      if indices[field] then readonly() end
      if values.class_lookup(cls, field) ~= nil then
        throw("AttributeError", "'sys." .. name .. "' object attribute '" .. field .. "' is read-only")
      end
      throw("AttributeError", "'sys." .. name .. "' object has no attribute '" .. field .. "'")
    end
    cls.ns.__setattr__ = bi("__setattr__", set_attr)
    cls.ns.__delattr__ = bi("__delattr__", set_attr)
    cls.ns.__repr__ = bi("__repr__", function(vm, args)
      local parts = {}
      for index, field in ipairs(names) do
        parts[index] = field .. "=" .. ops.repr(vm, args[1].prim.items[index])
      end
      return "sys." .. name .. "(" .. table.concat(parts, ", ") .. ")"
    end)
    local result = values.new_instance(cls)
    result.prim = values.tuple(items)
    return result
  end
  local float_info = numeric_info("float_info",
    { "max", "max_exp", "max_10_exp", "min", "min_exp", "min_10_exp",
      "dig", "mant_dig", "epsilon", "radix", "rounds" },
    { 0x1.fffffffffffffp1023, 1024, 308, 0x1p-1022, -1021, -307, 15, 53, 0x1p-52, 2, 1 })
  local int_info = numeric_info("int_info",
    { "bits_per_digit", "sizeof_digit", "default_max_str_digits", "str_digits_check_threshold" },
    { 24, string.packsize("j"), 0, 0 })
  local hash_info = numeric_info("hash_info",
    { "width", "modulus", "inf", "nan", "imag", "algorithm", "hash_bits", "seed_bits", "cutoff" },
    { 64, 0x1fffffffffffffff, 314159, values.hash_value(0 / 0), 1000003, "fnv1a64", 64, 0, 0 })
  local unraisable = bi("unraisablehook", function(_, args, kw)
    arg_count("unraisablehook", args, 1, 1); H.no_kw("unraisablehook", kw)
    local record = args[1]
    local message = I:get_attr(record, "err_msg")
    local exception = I:get_attr(record, "exc_value")
    local cls = I:get_attr(record, "exc_type")
    I:ewrite((message == NONE and "Exception ignored" or I:str(message)) .. ":\n")
    if cls ~= NONE then I:ewrite(I:get_attr(cls, "__name__") .. ": " .. I:str(exception) .. "\n") end
    return NONE
  end)
  local excepthook = require("diaspore.sys_exceptions").make_excepthook(I)
  return native_module("sys", {
    excepthook = excepthook,
    __excepthook__ = excepthook,
    unraisablehook = unraisable,
    __unraisablehook__ = unraisable,
    argv = values.list(argv),
    warnoptions = values.list({}),
    platform = "diaspore-lua",
    byteorder = string.pack("I2", 1):byte(1) == 1 and "little" or "big",
    maxsize = math.maxinteger,
    maxunicode = 0x10ffff,
    version_info = version,
    float_info = float_info,
    int_info = int_info,
    hash_info = hash_info,
    version = "3.12.12 (Diaspore Lua)",
    hexversion = 0x030c0cf0,
    implementation = record({ name = "diaspore", cache_tag = NONE,
      version = version, hexversion = 0x030c0cf0 }),
    flags = record({ debug = 0, inspect = 0, interactive = 0, optimize = 0,
      dont_write_bytecode = 1, no_user_site = 1, no_site = 1,
      ignore_environment = 1, verbose = 0, bytes_warning = 0, quiet = 0,
      hash_randomization = 0, isolated = 1, dev_mode = false, utf8_mode = 1,
      warn_default_encoding = 0, safe_path = true, int_max_str_digits = 0 }),
    modules = I.modules_dict,
    builtin_module_names = values.tuple({ "builtins", "sys", "time", "math", "_os", "posix",
      "_abc", "_json", "_operator", "_diaspore_stdlib", "net", "re", "flow" }),
    getdefaultencoding = bi("getdefaultencoding", function(_I, args, kw)
      arg_count("getdefaultencoding", args, 0, 0); H.no_kw("getdefaultencoding", kw)
      return "utf-8"
    end),
    getfilesystemencoding = bi("getfilesystemencoding", function(_I, args, kw)
      arg_count("getfilesystemencoding", args, 0, 0); H.no_kw("getfilesystemencoding", kw)
      return "utf-8"
    end),
    getfilesystemencodeerrors = bi("getfilesystemencodeerrors", function(_I, args, kw)
      arg_count("getfilesystemencodeerrors", args, 0, 0); H.no_kw("getfilesystemencodeerrors", kw)
      return "surrogateescape"
    end),
    intern = bi("intern", function(_I, args, kw)
      arg_count("intern", args, 1, 1); H.no_kw("intern", kw)
      return H.want_str("intern() argument", args[1])
    end),
    exception = bi("exception", function(_I, args, kw)
      arg_count("exception", args, 0, 0); H.no_kw("exception", kw)
      return I.exc_stack[#I.exc_stack] or NONE
    end),
    exc_info = bi("exc_info", function(_I, args, kw)
      arg_count("exc_info", args, 0, 0); H.no_kw("exc_info", kw)
      local exc = I.exc_stack[#I.exc_stack]
      if not exc then return values.tuple({ NONE, NONE, NONE }) end
      return values.tuple({ exc.cls, exc, exc.d.__traceback__ or NONE })
    end),
    stdin = sys_stream(I, "stdin"),
    stdout = sys_stream(I, "stdout"),
    stderr = sys_stream(I, "stderr"),
    _getframe = bi("_getframe", function(_I, args, kw)
      arg_count("_getframe", args, 0, 1); H.no_kw("_getframe", kw)
      local depth = args[1] == nil and 0 or as_index(args[1])
      if depth == nil then throw("TypeError", "an integer is required") end
      if bigint.cmp(depth, -2147483648) < 0 or bigint.cmp(depth, 2147483647) > 0 then
        throw("OverflowError", "Python int too large to convert to C int")
      end
      if depth < 0 then depth = 0 end
      local entry = I.stack[#I.stack]
      while depth > 0 and entry do entry, depth = entry.previous, depth - 1 end
      if not entry then throw("ValueError", "call stack is not deep enough") end
      return require("diaspore.metadata").frame(I, entry)
    end),
    getrecursionlimit = bi("getrecursionlimit", function(_I, args, kw)
      arg_count("getrecursionlimit", args, 0, 0)
      H.no_kw("getrecursionlimit", kw)
      return I.recursion_limit
    end),
    setrecursionlimit = bi("setrecursionlimit", function(_I, args, kw)
      arg_count("setrecursionlimit", args, 1, 1)
      H.no_kw("setrecursionlimit", kw)
      local limit = as_index(args[1])
      if limit == nil then throw("TypeError", "an integer is required") end
      if bigint.cmp(limit, -2147483648) < 0 or bigint.cmp(limit, 2147483647) > 0 then
        throw("OverflowError", "Python int too large to convert to C int")
      end
      if limit < 1 then throw("ValueError", "recursion limit must be greater or equal than 1") end
      limit = math.min(limit, I.max_depth)
      if limit <= I.depth then
        throw("RecursionError", "cannot set the recursion limit to " .. limit
          .. " at the recursion depth " .. I.depth .. ": the limit is too low")
      end
      I.recursion_limit = limit
      return NONE
    end),
    exit = bi("exit", function(_I, args, kw)
      arg_count("exit", args, 0, 1)
      H.no_kw("sys.exit", kw)
      local code = args[1]
      if code == NONE then code = nil end
      values.throw_val(values.make_exc("SystemExit", code))
    end),
    stderr_write = bi("stderr_write", function(I2, args, kw)
      arg_count("stderr_write", args, 1, 1)
      local s = H.want_str("stderr_write arg", args[1])
      I2:ewrite(s)
      return NONE
    end),
  })
end

-- ---------------------------------------------------------------------------
-- flow (durable flows are not supported in diaspore-lua)

local function make_flow_module()
  return native_module("flow", {
    suspend = bi("suspend", function(_I, args, kw)
      arg_count("suspend", args, 0, 1)
      throw("RuntimeError", "flow.suspend() requires a durable run (diaspore run --durable)")
    end),
  })
end

-- ---------------------------------------------------------------------------
-- private filesystem queries used by the bundled os and pathlib modules

local function os_path(I, name, args, kw)
  arg_count(name, args, 1, 1)
  H.no_kw(name, kw)
  local path = H.want_str("path", args[1])
  if path:find("\0", 1, true) then throw("ValueError", "embedded null byte") end
  return I:require_fs("read", path)
end

local function metadata_unavailable()
  local message = "diaspore-lua cannot read file metadata"
  local exception = values.make_exc("OSError", 38, message)
  exception.d.errno = 38
  exception.d.strerror = message
  values.throw_val(exception)
end

local function make_os_module(I)
  local module = native_module("_os", {
    urandom = bi("urandom", function(I, args, kw)
      arg_count("urandom", args, 1, 1); H.no_kw("urandom", kw)
      if I.deterministic then throw("PermissionError", "entropy is unavailable in deterministic mode") end
      local length = as_index(args[1])
      if length == nil then throw("TypeError", "an integer is required") end
      length = values.to_size(length)
      if length < 0 then throw("ValueError", "negative argument not allowed") end
      I:fuel_n(length)
      if length == 0 then return values.bytes("") end
      local source = io.open("/dev/urandom", "rb")
      if source == nil then throw("NotImplementedError", "OS entropy source is unavailable") end
      local data = source:read(length)
      source:close()
      if data == nil or #data ~= length then throw("OSError", "OS entropy source returned insufficient data") end
      return values.bytes(data)
    end),
    getcwd = bi("getcwd", function(I, args, kw)
      arg_count("getcwd", args, 0, 0)
      H.no_kw("getcwd", kw)
      I:require_cap("fs")
      local cwd = os.getenv("PWD")
      if cwd == nil then
        throw("RuntimeError", "diaspore-lua cannot determine working directory without PWD")
      end
      return cwd
    end),
    kind = bi("kind", function(I, args, kw)
      local path = os_path(I, "kind", args, kw)
      local f, message, errno = io.open(path, "rb")
      if f ~= nil then
        local _
        _, message, errno = f:read(0)
        f:close()
        if errno == 21 then return "dir" end
        if message == nil then return "file" end
      elseif errno == 2 or errno == 20 then
        return NONE
      elseif errno == 21 then
        return "dir"
      end
      throw(errno == 13 and "PermissionError" or "OSError", tostring(message))
    end),
    mkdir = bi("mkdir", function(I, args, kw)
      arg_count("mkdir", args, 1, 2); H.no_kw("mkdir", kw)
      local path = H.want_str("path", args[1])
      if path:find("\0", 1, true) then throw("ValueError", "embedded null byte") end
      I:require_fs("write", path)
      local exception = values.make_exc("OSError", 38, "diaspore-lua cannot create directories")
      exception.d.errno, exception.d.strerror = 38, "diaspore-lua cannot create directories"
      values.throw_val(exception)
    end),
    listdir = bi("listdir", function(I, args, kw)
      os_path(I, "listdir", args, kw)
      throw("NotImplementedError", "diaspore-lua cannot list directories")
    end),
    stat = bi("stat", function(I, args, kw)
      os_path(I, "stat", args, kw)
      metadata_unavailable()
    end),
    lstat = bi("lstat", function(I, args, kw)
      os_path(I, "lstat", args, kw)
      metadata_unavailable()
    end),
    readlink = bi("readlink", function(I, args, kw)
      os_path(I, "readlink", args, kw)
      metadata_unavailable()
    end),
    realpath = bi("realpath", function(I, args, kw)
      return os_path(I, "realpath", args, kw)
    end),
  })
  for name, method in pairs(require("diaspore.fds").methods(I)) do module.ns[name] = method end
  if I.fs_overlay ~= nil then
    for name, method in pairs(module.ns) do
      if name ~= "urandom" then
        module.ns[name] = bi(name, function()
          throw("NotImplementedError", "compiler filesystem exposes only read-only open()")
        end)
      end
    end
  end
  return module
end

-- ---------------------------------------------------------------------------
-- open() and file methods (capability "fs")

--- Open a file after checking filesystem capabilities.
function M.bi_open(I, args, kw)
  arg_count("open", args, 1, 2)
  local path = H.want_str("path", args[1])
  local mode = "r"
  if #args == 2 then mode = H.want_str("mode", args[2]) end
  local kv = H.kw_get(kw, "mode")
  if kv ~= nil then mode = H.want_str("mode", kv) end
  if mode ~= "r" and mode ~= "w" and mode ~= "a" and mode ~= "rb" and mode ~= "wb" then
    throw("ValueError", "invalid mode: '" .. mode .. "'")
  end
  local encoding = H.kw_get(kw, "encoding")
  if encoding ~= nil and not is_none(encoding) then
    if mode == "rb" or mode == "wb" then
      throw("ValueError", "binary mode doesn't take an encoding argument")
    end
    if type(encoding) ~= "string" then
      throw("TypeError", "open() argument 'encoding' must be str or None, not " .. type_name(encoding))
    end
    local normalized = encoding:lower():gsub("_", "-")
    if normalized ~= "utf8" and normalized ~= "utf-8" then
      throw("NotImplementedError", "only UTF-8 text is supported")
    end
  end
  if I.fs_overlay ~= nil then return require("diaspore.overlay").open(I, path, mode) end
  local kind = (mode == "r" or mode == "rb") and "read" or "write"
  local real = I:require_fs(kind, path)
  local lua_mode = mode
  if mode == "r" or mode == "w" or mode == "a" then lua_mode = mode .. "b" end
  local f, msg, errno = io.open(real, lua_mode)
  if f == nil then
    if errno ~= nil then
      local emsg = tostring(msg or "")
      local prefix = real .. ": "
      if emsg:sub(1, #prefix) == prefix then emsg = emsg:sub(#prefix + 1) end
      throw("OSError", "[Errno " .. errno .. "] " .. emsg .. ": "
        .. format.repr_string(path))
    end
    throw("OSError", tostring(msg))
  end
  return { kind = "file", f = f, binary = (mode == "rb" or mode == "wb"),
    writable = kind == "write", closed = false }
end

--- Look up an operation on a sandbox file.
function M.file_method(I, fo, n)
  if n == "read" then
    return bi("read", function(_I, args, kw)
      H.need_args("read", args, 0, 1)
      if fo.closed then throw("ValueError", "I/O operation on closed file.") end
      local count = -1
      if #args == 1 and not is_none(args[1]) then
        local v = as_index(args[1])
        if v == nil then
          throw("TypeError", "argument should be integer or None, not '"
            .. type_name(args[1]) .. "'")
        end
        count = values.to_size(v)
      end
      local data
      if count < 0 then
        data = fo.f:read("a")
      else
        data = fo.f:read(count) or ""
      end
      if fo.binary then return values.bytes(data) end
      return data
    end)
  elseif n == "readline" then
    return bi("readline", function()
      if fo.closed then throw("ValueError", "I/O operation on closed file.") end
      local line = fo.f:read("L")
      if line == nil then line = "" end
      if fo.binary then return values.bytes(line) end
      return line
    end)
  elseif n == "write" then
    return bi("write", function(_I, args, kw)
      H.need_args("write", args, 1, 1)
      if fo.closed then throw("ValueError", "I/O operation on closed file.") end
      local v = args[1]
      if fo.binary then
        local data = require("diaspore.buffer").data(v)
        fo.f:write(data)
        return #data
      end
      if type(v) ~= "string" then
        throw("TypeError", "write() argument must be str, not " .. type_name(v))
      end
      fo.f:write(v)
      return values.str_len(v)
    end)
  elseif n == "close" then
    return bi("close", function(I2)
      I2:close_file(fo)
      return NONE
    end)
  end
  throw("AttributeError", "'file' object has no attribute '" .. n .. "'")
end

-- ---------------------------------------------------------------------------

--- Build the native module namespaces for an interpreter.
function M.make_native_modules(I)
  local modules = {
    builtins = native_module("builtins", I.builtins),
    time = make_time_module(I),
    math = make_math_module(),
    sys = make_sys_module(I),
    _os = make_os_module(I),
    _json = require("diaspore.json_native").make_module(I),
    _abc = require("diaspore.abc").make_module(I),
    _diaspore_stdlib = require("diaspore.stdlib").make_module(I.stdlib or I.stdlib_override),
    _operator = native_module("_operator", {
      add = bi("add", function(I2, args, kw)
        arg_count("add", args, 2, 2); H.no_kw("add", kw)
        return ops.binop(I2, "add", args[1], args[2], false)
      end),
      index = bi("index", function(I2, args, kw)
        arg_count("index", args, 1, 1); H.no_kw("index", kw)
        return H.index_value(I2, args[1])
      end),
    }),
    net = stub_module("net", { "listen", "connect", "accept", "recv", "send", "close", "peer" }),
    re = require("diaspore.regex").make_module(I),
    flow = make_flow_module(),
  }
  for name, module in pairs(modules) do
    if module.ns.__name__ == nil then module.ns.__name__ = module.name or name end
    if module.ns.__doc__ == nil then module.ns.__doc__ = NONE end
    if module.ns.__package__ == nil then module.ns.__package__ = (module.name or name):match("^(.*)%.[^.]+$") or "" end
    if module.ns.__loader__ == nil then module.ns.__loader__ = NONE end
    if module.ns.__spec__ == nil then module.ns.__spec__ = NONE end
  end
  return modules
end

return M
