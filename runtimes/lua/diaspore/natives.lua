-- Native modules (spec section 11): time, math, json, random, sys, flow - plus the
-- diaspore-lua stubs for net/re/tasks - plus builtin open() and file methods.

local values = require("diaspore.values")
local format = require("diaspore.format")
local json = require("diaspore.json")
local iter = require("diaspore.iter")
local H = require("diaspore.helpers")

local M = {}

local NONE = values.NONE
local throw = values.throw
local type_name = values.type_name
local as_index = values.as_index
local as_float = values.as_float
local bi = values.builtin
local arg_count = H.arg_count

local function native_module(name, ns)
  return { kind = "nativemodule", name = name, ns = ns }
end

local function is_none(v)
  return type(v) == "table" and v.kind == "none"
end

-- diaspore-lua: tasks, net, and re exist but every function raises RuntimeError.
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
      local r = f(x)
      local iv = math.tointeger(r)
      if iv == nil then
        throw("OverflowError", "diaspore-lua: integer overflow (int64 limit)")
      end
      return iv
    end)
  end
  return native_module("math", {
    pi = math.pi,
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
    pow = math_fn("pow", 2, function(xs)
      local x, y = xs[1], xs[2]
      if x == 0.0 and y < 0 then domain_err() end
      if x < 0 and y ~= math.floor(y) and y ~= math.huge and y ~= -math.huge then
        domain_err()
      end
      return x ^ y
    end),
    log = math_fn("log", 1, function(xs)
      if xs[1] <= 0 then domain_err() end
      return math.log(xs[1])
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
-- json

local function make_json_module()
  return native_module("json", {
    dumps = bi("dumps", function(_I, args, kw)
      arg_count("dumps", args, 1, 2)
      local indent = NONE
      if #args == 2 then indent = args[2] end
      local kv = H.kw_get(kw, "indent")
      if kv ~= nil then indent = kv end
      return json.dumps(args[1], indent)
    end),
    loads = bi("loads", function(_I, args, kw)
      arg_count("loads", args, 1, 1)
      return json.to_diaspore(json.parse(H.want_str("loads arg", args[1])))
    end),
  })
end

-- ---------------------------------------------------------------------------
-- random (algorithm is VM-specific; excluded from conformance)

local function make_random_type(I)
  local Random
  Random = values.btype("Random", function(_I, args, kw)
    arg_count("Random", args, 0, 1)
    H.no_kw("Random", kw)
    local state = {}
    local rng = { kind = "nativeobject", nkind = "Random", pytype = Random, props = {} }
    local function seed(value)
      local n
      if value == nil or is_none(value) then
        n = I.deterministic and 0
          or (os.time() ~ math.floor(os.clock() * 1000000) ~ values.obj_id(rng))
      else
        n = as_index(value)
        if n == nil then
          local t = type(value)
          if t == "string" then value = values.bytes(value)
          elseif t ~= "number" and not (t == "table" and value.kind == "bytes") then
            throw("TypeError", "seed must be None, int, float, str, or bytes")
          end
          n = values.hash_value(value)
        end
      end
      -- SplitMix64 initializes the four words of the independent xoshiro state.
      for i = 1, 4 do
        n = n + 0x9e3779b97f4a7c15
        local z = (n ~ (n >> 30)) * 0xbf58476d1ce4e5b9
        z = (z ~ (z >> 27)) * 0x94d049bb133111eb
        state[i] = z ~ (z >> 31)
      end
    end
    local function next_bits()
      local x = state[2] * 5
      local result = ((x << 7) | (x >> 57)) * 9
      local t = state[2] << 17
      state[3] = state[3] ~ state[1]
      state[4] = state[4] ~ state[2]
      state[2] = state[2] ~ state[3]
      state[1] = state[1] ~ state[4]
      state[3] = state[3] ~ t
      state[4] = (state[4] << 45) | (state[4] >> 19)
      return result
    end
    local function randint(a, b)
      if b < a then throw("ValueError", "empty range for randint()") end
      local size = b - a + 1
      if size <= 0 then
        while true do
          I:fuel()
          local n = next_bits()
          if n >= a and n <= b then return n end
        end
      end
      local mask = size - 1
      mask = mask | (mask >> 1)
      mask = mask | (mask >> 2)
      mask = mask | (mask >> 4)
      mask = mask | (mask >> 8)
      mask = mask | (mask >> 16)
      mask = mask | (mask >> 32)
      while true do
        I:fuel()
        local n = next_bits() & mask
        if n < size then return a + n end
      end
    end
    rng.methods = {
      seed = bi("seed", function(_I2, a, keywords)
        arg_count("seed", a, 0, 1)
        H.no_kw("seed", keywords)
        seed(a[1])
        return NONE
      end),
      random = bi("random", function(_I2, a, keywords)
        arg_count("random", a, 0, 0)
        H.no_kw("random", keywords)
        return (next_bits() >> 11) * 0x1.0p-53
      end),
      randint = bi("randint", function(_I2, a, keywords)
        arg_count("randint", a, 2, 2)
        H.no_kw("randint", keywords)
        local lower, upper = as_index(a[1]), as_index(a[2])
        if lower == nil or upper == nil then throw("TypeError", "randint requires integers") end
        return randint(lower, upper)
      end),
      choice = bi("choice", function(I2, a, keywords)
        arg_count("choice", a, 1, 1)
        H.no_kw("choice", keywords)
        local items = iter.iter_to_array(I2, a[1])
        if #items == 0 then throw("IndexError", "Cannot choose from an empty sequence") end
        return items[randint(1, #items)]
      end),
      shuffle = bi("shuffle", function(_I2, a, keywords)
        arg_count("shuffle", a, 1, 1)
        H.no_kw("shuffle", keywords)
        if type(a[1]) ~= "table" or a[1].kind ~= "list" then
          throw("TypeError", "shuffle requires a list")
        end
        local items = a[1].items
        for i = #items, 2, -1 do
          local j = randint(1, i)
          items[i], items[j] = items[j], items[i]
        end
        return NONE
      end),
    }
    seed(args[1])
    return rng
  end)
  return Random
end

local function make_random_module(I)
  if I.deterministic then
    math.randomseed(0)
  else
    math.randomseed(os.time())
  end
  return native_module("random", {
    Random = make_random_type(I),
    seed = bi("seed", function(_I, args, kw)
      arg_count("seed", args, 1, 1)
      local n = as_index(args[1])
      if n == nil then throw("TypeError", "seed must be an integer in diaspore") end
      math.randomseed(n)
      return NONE
    end),
    random = bi("random", function()
      return math.random()
    end),
    randint = bi("randint", function(_I, args, kw)
      arg_count("randint", args, 2, 2)
      local a = as_index(args[1])
      local b = as_index(args[2])
      if a == nil or b == nil then throw("TypeError", "randint requires integers") end
      if b < a then throw("ValueError", "empty range for randint()") end
      return math.random(a, b)
    end),
    choice = bi("choice", function(I2, args, kw)
      arg_count("choice", args, 1, 1)
      local items = iter.iter_to_array(I2, args[1])
      if #items == 0 then
        throw("IndexError", "Cannot choose from an empty sequence")
      end
      return items[math.random(1, #items)]
    end),
    shuffle = bi("shuffle", function(_I, args, kw)
      arg_count("shuffle", args, 1, 1)
      local l = args[1]
      if type(l) ~= "table" or l.kind ~= "list" then
        throw("TypeError", "shuffle requires a list")
      end
      local items = l.items
      for i = #items, 2, -1 do
        local j = math.random(1, i)
        items[i], items[j] = items[j], items[i]
      end
      return NONE
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
      return size
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
  return native_module("sys", {
    argv = values.list(argv),
    platform = "diaspore-lua",
    stdin = sys_stream(I, "stdin"),
    stdout = sys_stream(I, "stdout"),
    stderr = sys_stream(I, "stderr"),
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
      local code = #args == 1 and args[1] or 0
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

local function make_os_module()
  return native_module("_os", {
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
    listdir = bi("listdir", function(I, args, kw)
      os_path(I, "listdir", args, kw)
      throw("NotImplementedError", "diaspore-lua cannot list directories")
    end),
    realpath = bi("realpath", function(I, args, kw)
      return os_path(I, "realpath", args, kw)
    end),
  })
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
        count = v
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
        if type(v) ~= "table" or v.kind ~= "bytes" then
          throw("TypeError", "a bytes-like object is required, not '"
            .. type_name(v) .. "'")
        end
        fo.f:write(v.b)
        return #v.b
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
  return {
    time = make_time_module(I),
    math = make_math_module(),
    json = make_json_module(),
    random = make_random_module(I),
    sys = make_sys_module(I),
    _os = make_os_module(),
    net = stub_module("net", { "listen", "connect", "accept", "recv", "send", "close", "peer" }),
    re = stub_module("re", { "search", "match", "fullmatch", "findall", "split", "sub", "escape" }),
    tasks = stub_module("tasks", { "spawn", "channel", "lock" }),
    flow = make_flow_module(),
  }
end

return M
