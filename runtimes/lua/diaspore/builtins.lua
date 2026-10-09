-- Builtin functions and type constructors (spec section 9).

local values = require("diaspore.values")
local format = require("diaspore.format")
local iter = require("diaspore.iter")
local ops = require("diaspore.ops")
local H = require("diaspore.helpers")

local M = {}

local NONE = values.NONE
local throw = values.throw
local type_name = values.type_name
local as_index = values.as_index
local as_float = values.as_float
local bi = values.builtin
local arg_count = H.arg_count

local function is_none(v)
  return type(v) == "table" and v.kind == "none"
end

local function kindof(v)
  if type(v) == "table" then return v.kind end
  return nil
end

-- ---------------------------------------------------------------------------
-- number parsing

local function strip_underscores(s)
  if not s:find("_", 1, true) then return s end
  local b = {}
  for i = 1, #s do
    local c = s:sub(i, i)
    if c == "_" then
      if i == 1 or i == #s or s:sub(i - 1, i - 1) == "_" then return nil end
    else
      b[#b + 1] = c
    end
  end
  return table.concat(b)
end

local DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"

local function parse_py_int(orig, base)
  local function fail()
    throw("ValueError", "invalid literal for int() with base " .. base .. ": "
      .. format.repr_string(orig))
  end
  local s = orig:match("^%s*(.-)%s*$")
  local sign = ""
  if s:sub(1, 1) == "+" or s:sub(1, 1) == "-" then
    sign = s:sub(1, 1)
    s = s:sub(2)
  end
  local lower = s:lower()
  if base == 16 and lower:sub(1, 2) == "0x" then
    lower = lower:sub(3)
  elseif base == 8 and lower:sub(1, 2) == "0o" then
    lower = lower:sub(3)
  elseif base == 2 and lower:sub(1, 2) == "0b" then
    lower = lower:sub(3)
  end
  local cleaned = strip_underscores(lower)
  if cleaned == nil or cleaned == "" then fail() end
  local out = 0
  for i = 1, #cleaned do
    local d = DIGITS:find(cleaned:sub(i, i), 1, true)
    if d == nil or d > base then fail() end
    out = out * base + (d - 1)
  end
  if sign == "-" then return -out end
  return out
end
M.parse_py_int = parse_py_int

local function parse_py_float(orig)
  local function fail()
    throw("ValueError", "could not convert string to float: "
      .. format.repr_string(orig))
  end
  local s = orig:match("^%s*(.-)%s*$")
  if s == "" then fail() end
  local sign, word = s:match("^([+-]?)([iInNaAfF][%a]*)$")
  if word ~= nil then
    local w = word:lower()
    if w == "nan" then return 0 / 0 end
    if w == "inf" or w == "infinity" then
      return sign == "-" and -math.huge or math.huge
    end
    fail()
  end
  if s:find("[xXpP]") then fail() end
  local cleaned = strip_underscores(s:lower())
  if cleaned == nil then fail() end
  -- validate: [+-]? (digits [. digits*] | [digits] . digits) [e [+-]? digits]
  local body = cleaned
  local expo = nil
  local epos = body:find("e", 1, true)
  if epos ~= nil then
    expo = body:sub(epos + 1)
    body = body:sub(1, epos - 1)
    if not expo:match("^[+-]?%d+$") then fail() end
  end
  if not body:match("^[+-]?%d*%.?%d*$") then fail() end
  if not body:find("%d") then fail() end
  local _, dots = body:gsub("%.", "")
  if dots > 1 then fail() end
  local f = tonumber(cleaned)
  if f == nil then fail() end
  return f + 0.0
end
M.parse_py_float = parse_py_float

-- ---------------------------------------------------------------------------
-- misc numeric helpers

local function fnv_hash(s)
  local h = 0xcbf29ce484222325
  for i = 1, #s do
    h = h ~ s:byte(i)
    h = h * 0x100000001b3
  end
  return h & 0x7fffffffffffffff
end

local function trunc_float_to_int(f)
  if f ~= f then throw("ValueError", "cannot convert float NaN to integer") end
  if f == math.huge or f == -math.huge then
    throw("OverflowError", "cannot convert float infinity to integer")
  end
  local t = f >= 0 and math.floor(f) or math.ceil(f)
  local iv = math.tointeger(t)
  if iv == nil then
    throw("OverflowError", "diaspore-lua: integer overflow (int64 limit)")
  end
  return iv
end

local function mulmod(a, b, m)
  -- a, b in [0, m); m > 0 (int64-overflow-safe)
  if m <= 3037000499 then return (a * b) % m end
  local result = 0
  while b > 0 do
    if b & 1 == 1 then
      result = result + a
      if result < 0 or result >= m then result = result - m end
    end
    a = a + a
    if a < 0 or a >= m then a = a - m end
    b = b >> 1
  end
  return result
end

local function mod_pow(base, exp, mod)
  local am = mod < 0 and -mod or mod
  local result = 1 % am
  local b = base % am
  if b < 0 then b = b + am end
  local e = exp
  while e > 0 do
    if e & 1 == 1 then result = mulmod(result, b, am) end
    b = mulmod(b, b, am)
    e = e >> 1
  end
  if mod < 0 and result ~= 0 then result = result - am end
  return result
end

-- ---------------------------------------------------------------------------
-- builtins

local function attr_name(v)
  if type(v) == "string" then return v end
  throw("TypeError", "attribute name must be string, not '" .. type_name(v) .. "'")
end

values.OBJECT.ns.__setattr__ = bi("__setattr__", function(I, args, kw)
  arg_count("__setattr__", args, 3, 3)
  H.no_kw("__setattr__", kw)
  I:object_set_attr(args[1], attr_name(args[2]), args[3])
  return NONE
end)
values.OBJECT.ns.__delattr__ = bi("__delattr__", function(I, args, kw)
  arg_count("__delattr__", args, 2, 2)
  H.no_kw("__delattr__", kw)
  I:object_del_attr(args[1], attr_name(args[2]))
  return NONE
end)
values.OBJECT.ns.__hash__ = bi("__hash__", function(_I, args, kw)
  arg_count("__hash__", args, 1, 1)
  H.no_kw("__hash__", kw)
  return values.obj_id(args[1]) % (1 << 61)
end)

local function bi_print(I, args, kw)
  local sep = " "
  local endv = "\n"
  for i = 1, #kw do
    local k, v = kw[i].k, kw[i].v
    if k == "sep" then
      if not is_none(v) then
        if type(v) ~= "string" then
          throw("TypeError", "sep must be None or a string, not " .. type_name(v))
        end
        sep = v
      end
    elseif k == "end" then
      if not is_none(v) then
        if type(v) ~= "string" then
          throw("TypeError", "end must be None or a string, not " .. type_name(v))
        end
        endv = v
      end
    else
      throw("TypeError", "'" .. k .. "' is an invalid keyword argument for print()")
    end
  end
  -- CPython streams arguments: if str() of a later arg raises, earlier
  -- args are already on stdout - observable, so we match it
  local ok, err = pcall(function()
    for i = 1, #args do
      if i > 1 then I:write(sep) end
      I:write(I:str(args[i]))
    end
    I:write(endv)
  end)
  I:flush()
  if not ok then error(err, 0) end
  return NONE
end

local function bi_input(I, args, kw)
  arg_count("input", args, 0, 1)
  if #args == 1 then
    I:write(I:str(args[1]))
    I:flush()
  end
  local line = io.read("l")
  if line == nil then
    throw("EOFError", "EOF when reading a line")
  end
  return line
end

local function bi_exit(_I, args, kw)
  arg_count("exit", args, 0, 1)
  local code = #args == 1 and args[1] or 0
  values.throw_val(values.make_exc("SystemExit", code))
end
M.bi_exit = bi_exit

local function int_arg(v)
  local n = as_index(v)
  if n == nil then
    throw("TypeError", "'" .. type_name(v) .. "' object cannot be interpreted as an integer")
  end
  return n
end

local function bi_enumerate(I, args, kw)
  arg_count("enumerate", args, 1, 2)
  local start = 0
  if #args == 2 then start = int_arg(args[2]) end
  local kv = H.kw_get(kw, "start")
  if kv ~= nil then start = int_arg(kv) end
  return iter.enum_iter(iter.get_iter(I, args[1]), start)
end

local function bi_zip(I, args, kw)
  local its = {}
  for i = 1, #args do its[i] = iter.get_iter(I, args[i]) end
  return iter.zip_iter(its)
end

local function bi_map(I, args, kw)
  if #args < 2 then
    throw("TypeError", "map() must have at least two arguments.")
  end
  local its = {}
  for i = 2, #args do its[i - 1] = iter.get_iter(I, args[i]) end
  return iter.map_iter(I, args[1], its)
end

local function bi_filter(I, args, kw)
  arg_count("filter", args, 2, 2)
  local it = iter.get_iter(I, args[2])
  local fn = args[1]
  if is_none(fn) then fn = nil end
  return iter.filter_iter(I, fn, it)
end

local function bi_reversed(I, args, kw)
  arg_count("reversed", args, 1, 1)
  local v = args[1]
  local items
  local k = kindof(v)
  if k == "dictview" then return iter.dict_view_iter(v, true) end
  if k == "list" or k == "tuple" then
    items = {}
    for i = 1, #v.items do items[i] = v.items[i] end
  elseif type(v) == "string" then
    local rs = values.chars(v)
    items = {}
    for i = 1, #rs do items[i] = rs[i] end
  elseif k == "bytes" then
    items = {}
    for i = 1, #v.b do items[i] = v.b:byte(i) end
  elseif k == "range" then
    items = {}
    local n = values.range_len(v)
    for i = 0, n - 1 do items[i + 1] = v.start + i * v.step end
  else
    throw("TypeError", "'" .. type_name(v) .. "' object is not reversible")
  end
  local len = #items
  for i = 1, len // 2 do
    items[i], items[len + 1 - i] = items[len + 1 - i], items[i]
  end
  return iter.seq_iter(items, "reversed")
end

local function bi_sorted(I, args, kw)
  arg_count("sorted", args, 1, 1)
  H.charge_sized(I, args[1])
  local items = iter.iter_to_array(I, args[1])
  I:fuel_n(#items)
  local key, reverse = H.key_reverse_kw("sorted", I, kw)
  ops.sort_values(I, items, key, reverse)
  return values.list(items)
end

local function bi_sum(I, args, kw)
  arg_count("sum", args, 1, 2)
  local total = 0
  if #args == 2 then total = args[2] end
  local kv = H.kw_get(kw, "start")
  if kv ~= nil then total = kv end
  local it = iter.get_iter(I, args[1])
  while true do
    I:fuel()
    local x, ok = it.next()
    if not ok then return total end
    total = ops.binop(I, "add", total, x)
  end
end

local MISSING = {}

local function min_max(I, name, args, kw, is_min)
  if #args == 0 then
    throw("TypeError", name .. " expected at least 1 argument, got 0")
  end
  local key = nil
  local deflt = MISSING
  for i = 1, #kw do
    local k, v = kw[i].k, kw[i].v
    if k == "key" then
      if not is_none(v) then key = v end
    elseif k == "default" then
      deflt = v
    else
      throw("TypeError", "'" .. k .. "' is an invalid keyword argument for " .. name .. "()")
    end
  end
  local items
  if #args == 1 then
    H.charge_sized(I, args[1])
    items = iter.iter_to_array(I, args[1])
    I:fuel_n(#items)
    if #items == 0 then
      if deflt ~= MISSING then return deflt end
      throw("ValueError", name .. "() iterable argument is empty")
    end
  else
    items = args
  end
  local best = items[1]
  local best_k = key ~= nil and I:call(key, { best }, {}) or best
  for i = 2, #items do
    local x = items[i]
    local xk = key ~= nil and I:call(key, { x }, {}) or x
    local pick
    if is_min then
      pick = ops.compare(I, "lt", xk, best_k)
    else
      pick = ops.compare(I, "lt", best_k, xk)
    end
    if I:truth(pick) then
      best, best_k = x, xk
    end
  end
  return best
end

local function bi_any(I, args, kw)
  arg_count("any", args, 1, 1)
  local it = iter.get_iter(I, args[1])
  while true do
    I:fuel()
    local x, ok = it.next()
    if not ok then return false end
    if I:truth(x) then return true end
  end
end

local function bi_all(I, args, kw)
  arg_count("all", args, 1, 1)
  local it = iter.get_iter(I, args[1])
  while true do
    I:fuel()
    local x, ok = it.next()
    if not ok then return true end
    if not I:truth(x) then return false end
  end
end

local function bi_abs(I, args, kw)
  arg_count("abs", args, 1, 1)
  local v = args[1]
  local n = as_index(v)
  if n ~= nil then
    if n < 0 then return -n end
    return n
  end
  if math.type(v) == "float" then return math.abs(v) end
  if kindof(v) == "complex" then
    return math.sqrt(v.re * v.re + v.im * v.im)
  end
  if kindof(v) == "instance" and values.class_lookup(v.cls, "__abs__") ~= nil then
    return I:call_dunder(v, "__abs__", {})
  end
  throw("TypeError", "bad operand type for abs(): '" .. type_name(v) .. "'")
end

local function bi_round(_I, args, kw)
  arg_count("round", args, 1, 2)
  local has_nd = false
  local nd = 0
  if #args == 2 and not is_none(args[2]) then
    nd = int_arg(args[2])
    has_nd = true
  end
  local iv = as_index(args[1])
  if iv ~= nil then
    if not has_nd or nd >= 0 then return iv end
    -- negative ndigits on an int: round at 10^-nd, half to even
    if nd < -18 then return 0 end
    local p = 1
    for _ = 1, -nd do p = p * 10 end
    local neg = iv < 0
    local mag = neg and -iv or iv
    local q = mag // p
    local r = mag % p
    local out = q
    local twice = r * 2
    if twice > p or (twice == p and q % 2 == 1) then out = q + 1 end
    out = out * p
    if neg then return -out end
    return out
  end
  if math.type(args[1]) ~= "float" then
    throw("TypeError", "type " .. type_name(args[1]) .. " doesn't define __round__ method")
  end
  local f = args[1]
  if not has_nd then
    if f ~= f then throw("ValueError", "cannot convert float NaN to integer") end
    if f == math.huge or f == -math.huge then
      throw("OverflowError", "cannot convert float infinity to integer")
    end
    return format.round_float_to_int(f)
  end
  if f ~= f or f == math.huge or f == -math.huge then return f end
  return format.round_float_digits(f, nd)
end

local function bi_divmod(I, args, kw)
  arg_count("divmod", args, 2, 2)
  local q = ops.binop(I, "floordiv", args[1], args[2])
  local r = ops.binop(I, "mod", args[1], args[2])
  return values.tuple({ q, r })
end

local function bi_pow(I, args, kw)
  arg_count("pow", args, 2, 3)
  if #args == 3 then
    local a = as_index(args[1])
    local b = as_index(args[2])
    local m = as_index(args[3])
    if a == nil or b == nil or m == nil then
      throw("TypeError", "pow() 3rd argument not allowed unless all arguments are integers")
    end
    if m == 0 then throw("ValueError", "pow() 3rd argument cannot be 0") end
    if b < 0 then throw("ValueError", "base is not invertible for the given modulus") end
    return mod_pow(a, b, m)
  end
  return ops.binop(I, "pow", args[1], args[2])
end

local function bi_ord(_I, args, kw)
  arg_count("ord", args, 1, 1)
  local v = args[1]
  if type(v) == "string" then
    local n = values.str_len(v)
    if n ~= 1 then
      throw("TypeError", "ord() expected a character, but string of length " .. n .. " found")
    end
    return (utf8.codepoint(v, 1, 1, true))
  end
  if kindof(v) == "bytes" then
    if #v.b ~= 1 then
      throw("TypeError", "ord() expected a character, but string of length " .. #v.b .. " found")
    end
    return v.b:byte(1)
  end
  throw("TypeError", "ord() expected string of length 1, but " .. type_name(v) .. " found")
end

local function bi_chr(_I, args, kw)
  arg_count("chr", args, 1, 1)
  local n = int_arg(args[1])
  if n < 0 or n >= 0x110000 then
    throw("ValueError", "chr() arg not in range(0x110000)")
  end
  return utf8.char(n)
end

local function base_conv(name, prefix, base)
  return function(_I, args, kw)
    arg_count(name, args, 1, 1)
    local n = int_arg(args[1])
    local sign = n < 0 and "-" or ""
    return sign .. prefix .. format.int_mag_str(n, base)
  end
end

local function bi_hash(I, args, kw)
  arg_count("hash", args, 1, 1)
  return values.hash_value(args[1])
end

local function bi_id(_I, args, kw)
  arg_count("id", args, 1, 1)
  local v = args[1]
  if type(v) == "table" then return values.obj_id(v) end
  return fnv_hash(values.hash_key(v))
end

local function bi_next(I, args, kw)
  arg_count("next", args, 1, 2)
  local v = args[1]
  local it = nil
  if kindof(v) == "iter" then
    it = v
  elseif kindof(v) == "instance" and values.class_lookup(v.cls, "__next__") ~= nil then
    it = iter.inst_iter(I, v)
  end
  if it == nil then
    throw("TypeError", "'" .. type_name(v) .. "' object is not an iterator")
  end
  local x, ok = it.next()
  if ok then return x end
  if #args == 2 then return args[2] end
  local sa = it.stop_args
  it.stop_args = nil
  if sa ~= nil and #sa > 0 then
    values.throw_val(values.make_exc("StopIteration", sa[1]))
  end
  values.throw_val(values.make_exc("StopIteration"))
end

local function bi_getattr(I, args, kw)
  arg_count("getattr", args, 2, 3)
  local name = attr_name(args[2])
  local saved = #I.stack
  local ok, res = pcall(I.get_attr, I, args[1], name)
  if ok then return res end
  if #args == 3 and values.is_exc(res) and values.exc_isa(res.value, "AttributeError") then
    I:trim(saved)
    return args[3]
  end
  error(res, 0)
end

local function bi_hasattr(I, args, kw)
  arg_count("hasattr", args, 2, 2)
  local name = attr_name(args[2])
  local saved = #I.stack
  local ok, res = pcall(I.get_attr, I, args[1], name)
  if ok then return true end
  if values.is_exc(res) and values.exc_isa(res.value, "AttributeError") then
    I:trim(saved)
    return false
  end
  error(res, 0)
end

-- ---------------------------------------------------------------------------
-- isinstance / issubclass / callable / type

local function value_matches_type(v, bt)
  local name = bt.name
  if name == "int" then return values.is_int_kind(v) end
  if name == "bool" then return type(v) == "boolean" end
  if name == "float" then return math.type(v) == "float" end
  if name == "str" then return type(v) == "string" end
  if name == "bytes" then return kindof(v) == "bytes" end
  if name == "list" then return kindof(v) == "list" end
  if name == "tuple" then return kindof(v) == "tuple" end
  if name == "dict" then return kindof(v) == "dict" end
  if name == "set" then return kindof(v) == "set" and not v.frozen end
  if name == "frozenset" then return kindof(v) == "set" and v.frozen == true end
  if name == "range" then return kindof(v) == "range" end
  if name == "slice" then return kindof(v) == "slice" end
  if name == "complex" then return kindof(v) == "complex" end
  if name == "NoneType" then return kindof(v) == "none" end
  if name == "NotImplementedType" then return v == values.NOT_IMPLEMENTED end
  if name == "type" then return kindof(v) == "class" or kindof(v) == "btype" end
  if kindof(v) == "dictview" then return name == "dict_" .. v.view end
  if kindof(v) == "nativeobject" then return v.pytype == bt end
  return false
end

local function bi_isinstance(_I, args, kw)
  arg_count("isinstance", args, 2, 2)
  local v = args[1]
  local classes
  if kindof(args[2]) == "tuple" then
    classes = args[2].items
  else
    classes = { args[2] }
  end
  for _, c in ipairs(classes) do
    local ck = kindof(c)
    if ck == "class" then
      if c == values.OBJECT then return true end
      if kindof(v) == "instance" and values.issub(v.cls, c) then return true end
    elseif ck == "btype" then
      if kindof(v) == "instance" then
        -- builtin-subclass instance: match its primitive base type
        if v.prim ~= nil then
          if v.cls.prim ~= nil and (v.cls.prim.name == c.name
              or (c.name == "int" and v.cls.prim.name == "bool")) then
            return true
          end
          if value_matches_type(v.prim, c) then return true end
        end
      elseif value_matches_type(v, c) then
        return true
      end
    else
      throw("TypeError", "isinstance() arg 2 must be a type or tuple of types")
    end
  end
  return false
end

local function bi_issubclass(_I, args, kw)
  arg_count("issubclass", args, 2, 2)
  local a = args[1]
  local ak = kindof(a)
  if ak ~= "class" and ak ~= "btype" then
    throw("TypeError", "issubclass() arg 1 must be a class")
  end
  local classes
  if kindof(args[2]) == "tuple" then
    classes = args[2].items
  else
    classes = { args[2] }
  end
  for _, c in ipairs(classes) do
    if c == values.OBJECT then return true end
  end
  for _, c in ipairs(classes) do
    local ck = kindof(c)
    if ak == "class" and ck == "class" and values.issub(a, c) then return true end
    if ak == "btype" and ck == "btype" then
      if a.name == c.name or (a.name == "bool" and c.name == "int") then return true end
    end
  end
  return false
end

local function bi_callable(_I, args, kw)
  arg_count("callable", args, 1, 1)
  local k = kindof(args[1])
  if k == "function" or k == "boundmethod" or k == "class" or k == "btype"
      or k == "builtin" then
    return true
  end
  if k == "instance" then
    return values.class_lookup(args[1].cls, "__call__") ~= nil
  end
  return false
end

-- ---------------------------------------------------------------------------
-- type constructors

local function ctor_int(I, args, kw)
  arg_count("int", args, 0, 2)
  if #args == 0 then return 0 end
  local v = args[1]
  if #args == 2 then
    local base = int_arg(args[2])
    if type(v) ~= "string" then
      throw("TypeError", "int() can't convert non-string with explicit base")
    end
    if base ~= 0 and (base < 2 or base > 36) then
      throw("ValueError", "int() base must be >= 2 and <= 36, or 0")
    end
    return parse_py_int(v, base)
  end
  if kindof(v) == "instance" then
    for _, d in ipairs({ "__int__", "__index__", "__trunc__" }) do
      if values.class_lookup(v.cls, d) ~= nil then
        return I:call_dunder(v, d, {})
      end
    end
    if v.prim ~= nil and (math.type(v.prim) ~= nil or type(v.prim) == "string") then
      return ctor_int(I, { v.prim }, {})
    end
    throw("TypeError",
      "int() argument must be a string or a real number, not '" .. v.cls.name .. "'")
  end
  local n = as_index(v)
  if n ~= nil then return n end
  if math.type(v) == "float" then return trunc_float_to_int(v) end
  if type(v) == "string" then return parse_py_int(v, 10) end
  throw("TypeError",
    "int() argument must be a string or a real number, not '" .. type_name(v) .. "'")
end

local function ctor_float(I, args, kw)
  arg_count("float", args, 0, 1)
  if #args == 0 then return 0.0 end
  local v = args[1]
  if kindof(v) == "instance" then
    if values.class_lookup(v.cls, "__float__") ~= nil then
      return I:call_dunder(v, "__float__", {})
    end
    if v.prim ~= nil and math.type(v.prim) ~= nil then return v.prim + 0.0 end
    throw("TypeError",
      "float() argument must be a string or a real number, not '" .. v.cls.name .. "'")
  end
  if math.type(v) == "float" then return v end
  local n = as_index(v)
  if n ~= nil then return n + 0.0 end
  if type(v) == "string" then return parse_py_float(v) end
  throw("TypeError",
    "float() argument must be a string or a real number, not '" .. type_name(v) .. "'")
end

local function ctor_bool(I, args, kw)
  arg_count("bool", args, 0, 1)
  if #args == 0 then return false end
  return I:truth(args[1])
end

local function ctor_str(I, args, kw)
  if #args > 1 or #kw > 0 then
    throw("TypeError", "diaspore str() takes one argument")
  end
  if #args == 0 then return "" end
  return I:str(args[1])
end

local function ctor_bytes(I, args, kw)
  arg_count("bytes", args, 0, 2)
  if #args == 0 then return values.bytes("") end
  local v = args[1]
  if type(v) == "string" then
    if #args < 2 then
      throw("TypeError", "string argument without an encoding")
    end
    return H.encode_str_to(v, H.want_str("encoding", args[2]))
  end
  if kindof(v) == "bytes" then return values.bytes(v.b) end
  local n = as_index(v)
  if n ~= nil then
    if n < 0 then throw("ValueError", "negative count") end
    return values.bytes(string.rep("\0", n))
  end
  local items = iter.iter_to_array(I, v)
  local out = {}
  for i = 1, #items do
    local x = as_index(items[i])
    if x == nil then
      throw("TypeError",
        "'" .. type_name(items[i]) .. "' object cannot be interpreted as an integer")
    end
    if x < 0 or x > 255 then throw("ValueError", "bytes must be in range(0, 256)") end
    out[i] = string.char(x)
  end
  return values.bytes(table.concat(out))
end

local function ctor_list(I, args, kw)
  if #args == 1 then H.charge_sized(I, args[1]) end
  arg_count("list", args, 0, 1)
  if #args == 0 then return values.list({}) end
  return values.list(iter.iter_to_array(I, args[1]))
end

local function ctor_tuple(I, args, kw)
  if #args == 1 then H.charge_sized(I, args[1]) end
  arg_count("tuple", args, 0, 1)
  if #args == 0 then return values.tuple({}) end
  return values.tuple(iter.iter_to_array(I, args[1]))
end

local function ctor_set(I, args, kw)
  if #args == 1 then H.charge_sized(I, args[1]) end
  arg_count("set", args, 0, 1)
  local out = values.new_set()
  if #args == 1 then
    local items = iter.iter_to_array(I, args[1])
    for i = 1, #items do values.set_add(out, items[i]) end
  end
  return out
end

local function ctor_frozenset(I, args, kw)
  arg_count("frozenset", args, 0, 1)
  H.no_kw("frozenset", kw)
  if #args == 1 and kindof(args[1]) == "set" then
    if args[1].frozen then return args[1] end
    local out = values.set_copy(args[1])
    out.frozen = true
    return out
  end
  local out = ctor_set(I, args, kw)
  out.frozen = true
  return out
end

local function ctor_dict(I, args, kw)
  if #args > 1 then
    throw("TypeError", "dict expected at most 1 argument, got " .. #args)
  end
  local out = values.new_dict()
  if #args == 1 then
    local v = args[1]
    -- unwrap a dict-subclass instance
    if kindof(v) == "instance" and kindof(v.prim) == "dict" then v = v.prim end
    if kindof(v) == "instance" and values.class_lookup(v.cls, "keys") ~= nil then
      for _, k in ipairs(iter.iter_to_array(I, I:call_dunder(v, "keys", {}))) do
        values.dict_set(out, k, ops.get_index(I, v, k))
      end
    else
      H.dict_update_from(I, out, v)
    end
  end
  for i = 1, #kw do
    values.dict_set_hk(out, "s" .. kw[i].k, kw[i].k, kw[i].v)
  end
  return out
end

-- Parse a Python complex string like "3+4j", "1j", "-2.5", "(1+2j)".
local function parse_complex_str(s)
  local function fail()
    throw("ValueError", "complex() arg is a malformed string")
  end
  local t = s:match("^%s*(.-)%s*$")
  if t:sub(1, 1) == "(" and t:sub(-1) == ")" then t = t:sub(2, -2) end
  if t == "" then fail() end
  if t == "j" or t == "J" then return values.complex(0.0, 1.0) end
  -- pure imaginary: <num>j
  local imnum = t:match("^([%+%-]?[%d%.eE%+%-]*)[jJ]$")
  if imnum ~= nil and not t:sub(1, #t - 1):find("[jJ]") then
    if imnum == "" or imnum == "+" then imnum = "1" end
    if imnum == "-" then imnum = "-1" end
    local ok, im = pcall(parse_py_float, imnum)
    if ok then return values.complex(0.0, im) end
  end
  -- real only
  local ok, re = pcall(parse_py_float, t)
  if ok then return values.complex(re, 0.0) end
  -- real + imag j: split on the last +/- that isn't an exponent sign
  for i = #t, 2, -1 do
    local c = t:sub(i, i)
    if (c == "+" or c == "-") then
      local prev = t:sub(i - 1, i - 1)
      if prev ~= "e" and prev ~= "E" then
        local rp = t:sub(1, i - 1)
        local ip = t:sub(i)
        if ip:sub(-1) == "j" or ip:sub(-1) == "J" then
          local imn = ip:sub(1, -2)
          if imn == "+" then imn = "1" end
          if imn == "-" then imn = "-1" end
          local okr, rv = pcall(parse_py_float, rp)
          local oki, iv = pcall(parse_py_float, imn)
          if okr and oki then return values.complex(rv, iv) end
        end
      end
    end
  end
  fail()
end

local function ctor_complex(I, args, kw)
  arg_count("complex", args, 0, 2)
  if #args == 0 then return values.complex(0.0, 0.0) end
  local re = args[1]
  if #args == 1 then
    if type(re) == "string" then return parse_complex_str(re) end
    if kindof(re) == "complex" then return re end
    local f = as_float(re)
    if f ~= nil then return values.complex(f, 0.0) end
    throw("TypeError", "complex() argument must be a string or a number")
  end
  local im = args[2]
  local rf, imf = 0.0, 0.0
  if kindof(re) == "complex" then
    rf, imf = re.re, re.im
  else
    local f = as_float(re)
    if f == nil then throw("TypeError", "complex() argument must be a string or a number") end
    rf = f
  end
  if kindof(im) == "complex" then
    rf = rf - im.im
    imf = imf + im.re
  else
    local f = as_float(im)
    if f == nil then throw("TypeError", "complex() argument must be a string or a number") end
    imf = imf + f
  end
  return values.complex(rf, imf)
end

local function ctor_range(_I, args, kw)
  arg_count("range", args, 1, 3)
  local vals = {}
  for i = 1, #args do vals[i] = int_arg(args[i]) end
  if #vals == 1 then return values.range(0, vals[1], 1) end
  if #vals == 2 then return values.range(vals[1], vals[2], 1) end
  if vals[3] == 0 then throw("ValueError", "range() arg 3 must not be zero") end
  return values.range(vals[1], vals[2], vals[3])
end

-- ---------------------------------------------------------------------------
-- make_builtins

local function ctor_slice(_I, args, kw)
  arg_count("slice", args, 1, 3)
  H.no_kw("slice", kw)
  if #args == 1 then
    return { kind = "slice", lower = NONE, upper = args[1], step = NONE }
  end
  local step = NONE
  if #args == 3 then step = args[3] end
  return { kind = "slice", lower = args[1], upper = args[2], step = step }
end

--- Build the builtin namespace for an interpreter.
function M.make_builtins(I)
  local types = {
    int = values.btype("int", ctor_int),
    float = values.btype("float", ctor_float),
    bool = values.btype("bool", ctor_bool),
    str = values.btype("str", ctor_str),
    bytes = values.btype("bytes", ctor_bytes),
    list = values.btype("list", ctor_list),
    tuple = values.btype("tuple", ctor_tuple),
    dict = values.btype("dict", ctor_dict),
    set = values.btype("set", ctor_set),
    frozenset = values.btype("frozenset", ctor_frozenset),
    range = values.btype("range", ctor_range),
    slice = values.btype("slice", ctor_slice),
    complex = values.btype("complex", ctor_complex),
    NoneType = values.btype("NoneType", function() return NONE end),
  }
  local notimplemented_type = values.btype("NotImplementedType", function(_I, args, kw)
    arg_count("NotImplementedType", args, 0, 0)
    H.no_kw("NotImplementedType", kw)
    return values.NOT_IMPLEMENTED
  end)
  local view_types = {}
  for _, view in ipairs({ "keys", "values", "items" }) do
    local name = "dict_" .. view
    view_types[view] = values.btype(name, function()
      throw("TypeError", "cannot create '" .. name .. "' instances")
    end)
  end
  types.type = values.btype("type", function(I2, args, kw)
    if #args ~= 1 then -- type(name, bases, ns): dynamic class creation
      if #args ~= 3 then
        throw("TypeError", "type() takes 1 or 3 arguments")
      end
      local name, bases, ns = args[1], args[2], args[3]
      if type(name) ~= "string" or kindof(bases) ~= "tuple" or kindof(ns) ~= "dict" then
        throw("TypeError", "type() argument types: (str, tuple, dict)")
      end
      local checked = {}
      for _, b in ipairs(bases.items) do
        if kindof(b) ~= "class" then
          throw("TypeError", "cannot subclass " .. ops.repr(I2, b) .. " in diaspore")
        end
        checked[#checked + 1] = b
      end
      local ns_map = {}
      local ns_order = {}
      local entries = values.dict_entries(ns)
      for _, e in ipairs(entries) do
        if type(e.k) ~= "string" then
          throw("TypeError", "type() namespace keys must be str")
        end
        ns_map[e.k] = e.v
        ns_order[#ns_order + 1] = e.k
      end
      return I2:build_class(name, checked, ns_map, ns_order, nil, {})
    end
    local v = args[1]
    local k = kindof(v)
    if k == "instance" then return v.cls end
    if k == "class" or k == "btype" then return types.type end
    if k == "dictview" then return view_types[v.view] end
    if k == "nativeobject" and v.pytype ~= nil then return v.pytype end
    local t = type(v)
    if t == "boolean" then return types.bool end
    if t == "number" then
      return math.type(v) == "integer" and types.int or types.float
    end
    if t == "string" then return types.str end
    if k == "bytes" then return types.bytes end
    if k == "list" then return types.list end
    if k == "tuple" then return types.tuple end
    if k == "dict" then return types.dict end
    if k == "set" then return v.frozen and types.frozenset or types.set end
    if k == "range" then return types.range end
    if k == "slice" then return types.slice end
    if k == "complex" then return types.complex end
    if k == "none" then return types.NoneType end
    if k == "notimplemented" then return notimplemented_type end
    local name = type_name(v)
    return values.btype(name, function()
      throw("TypeError", "cannot construct '" .. name .. "' values")
    end)
  end)
  I.types = types

  local b = {}
  local function add(name, fn)
    b[name] = bi(name, fn)
  end

  add("print", bi_print)
  add("len", function(I2, args, kw)
    arg_count("len", args, 1, 1)
    return ops.diaspore_len(I2, args[1])
  end)
  add("enumerate", bi_enumerate)
  add("zip", bi_zip)
  add("map", bi_map)
  add("filter", bi_filter)
  add("reversed", bi_reversed)
  add("sorted", bi_sorted)
  add("sum", bi_sum)
  add("min", function(I2, args, kw) return min_max(I2, "min", args, kw, true) end)
  add("max", function(I2, args, kw) return min_max(I2, "max", args, kw, false) end)
  add("any", bi_any)
  add("all", bi_all)
  add("abs", bi_abs)
  add("round", bi_round)
  add("divmod", bi_divmod)
  add("pow", bi_pow)
  add("ord", bi_ord)
  add("chr", bi_chr)
  add("hex", base_conv("hex", "0x", 16))
  add("bin", base_conv("bin", "0b", 2))
  add("oct", base_conv("oct", "0o", 8))
  add("repr", function(I2, args, kw)
    arg_count("repr", args, 1, 1)
    return ops.repr(I2, args[1])
  end)
  add("ascii", function(I2, args, kw)
    arg_count("ascii", args, 1, 1)
    return ops.ascii(I2, args[1])
  end)
  add("isinstance", bi_isinstance)
  add("issubclass", bi_issubclass)
  add("callable", bi_callable)
  add("hash", bi_hash)
  add("id", bi_id)
  add("iter", function(I2, args, kw)
    arg_count("iter", args, 1, 1)
    return iter.get_iter(I2, args[1])
  end)
  add("next", bi_next)
  add("getattr", bi_getattr)
  add("setattr", function(I2, args, kw)
    arg_count("setattr", args, 3, 3)
    I2:set_attr(args[1], attr_name(args[2]), args[3])
    return NONE
  end)
  add("delattr", function(I2, args, kw)
    arg_count("delattr", args, 2, 2)
    H.no_kw("delattr", kw)
    I2:del_attr(args[1], attr_name(args[2]))
    return NONE
  end)
  add("hasattr", bi_hasattr)
  add("input", bi_input)
  add("open", function(I2, args, kw)
    return I2.natives_open(I2, args, kw)
  end)
  add("exit", bi_exit)
  add("staticmethod", function(_I2, args, kw)
    arg_count("staticmethod", args, 1, 1)
    return { kind = "staticmethod", fn = args[1] }
  end)
  add("classmethod", function(_I2, args, kw)
    arg_count("classmethod", args, 1, 1)
    return { kind = "classmethod", fn = args[1] }
  end)
  add("format", function(I2, args, kw)
    arg_count("format", args, 1, 2)
    local spec = ""
    if #args == 2 then
      if type(args[2]) ~= "string" then
        throw("TypeError", "format() argument 2 must be str, not " .. type_name(args[2]))
      end
      spec = args[2]
    end
    return format.format_value(I2, args[1], spec)
  end)

  -- `.await` (unspellable): the compiler lowers `await x` to
  -- yield-from `.await(x)` - resolve x to the generator that drives it.
  add(".await", function(I2, args, kw)
    local x = args[1]
    local k = kindof(x)
    if k == "iter" and x.iname == "generator" then return x end
    if k == "instance" and values.class_lookup(x.cls, "__await__") ~= nil then
      local r = I2:call_dunder(x, "__await__", {})
      if kindof(r) == "iter" and r.iname == "generator" then return r end
      throw("TypeError", "__await__() must return a coroutine or generator")
    end
    throw("TypeError",
      "object " .. type_name(x) .. " can't be used in 'await' expression")
  end)

  add("property", function(_I2, args, kw)
    local fget = (#args >= 1 and not is_none(args[1])) and args[1] or nil
    local fset = (#args >= 2 and not is_none(args[2])) and args[2] or nil
    local fdel = (#args >= 3 and not is_none(args[3])) and args[3] or nil
    local doc = (#args >= 4 and not is_none(args[4])) and args[4] or nil
    for i = 1, #kw do
      local k, v = kw[i].k, kw[i].v
      if k == "fget" then fget = v elseif k == "fset" then fset = v
      elseif k == "fdel" then fdel = v elseif k == "doc" then doc = v end
    end
    return values.property(fget, fset, fdel, doc)
  end)
  add("globals", function(I2, args, kw)
    arg_count("globals", args, 0, 0)
    return I2:frame_dict((I2:caller_frame() and I2:caller_frame().globals) or nil)
  end)
  add("locals", function(I2, args, kw)
    arg_count("locals", args, 0, 0)
    return I2:frame_dict((I2:caller_frame() and I2:caller_frame().vars) or nil)
  end)
  add("vars", function(I2, args, kw)
    arg_count("vars", args, 0, 1)
    if #args == 0 then
      return I2:frame_dict((I2:caller_frame() and I2:caller_frame().vars) or nil)
    end
    local o = args[1]
    local k = kindof(o)
    if k == "instance" then
      local d = values.new_dict()
      for key, val in pairs(o.d) do values.dict_set(d, key, val) end
      return d
    end
    if k == "module" then return I2:frame_dict(o.frame.vars) end
    if k == "class" then return I2:get_attr(o, "__dict__") end
    throw("TypeError", "vars() argument must have __dict__")
  end)
  add("dir", function(I2, args, kw)
    arg_count("dir", args, 0, 1)
    local names = {}
    local seen = {}
    local function addn(n) if not seen[n] then seen[n] = true; names[#names + 1] = n end end
    if #args == 0 then
      local fr = I2:caller_frame()
      if fr ~= nil then for key in pairs(fr.vars) do addn(key) end end
    else
      local o = args[1]
      local k = kindof(o)
      if k == "instance" then
        for key in pairs(o.d) do addn(key) end
        for _, c in ipairs(o.cls.mro) do for key in pairs(c.ns) do addn(key) end end
      elseif k == "class" then
        for _, c in ipairs(o.mro) do for key in pairs(c.ns) do addn(key) end end
      elseif k == "module" then
        for key in pairs(o.frame.vars) do addn(key) end
      elseif k == "nativemodule" then
        for key in pairs(o.ns) do addn(key) end
      end
    end
    ops.sort_values(I2, names, nil, false)
    return values.list(names)
  end)
  add("compile", function(I2, args, kw)
    arg_count("compile", args, 1, 6)
    return I2:compile_snippet(args[1], #args >= 2 and args[2] or "<string>",
      #args >= 3 and args[3] or "exec")
  end)
  add("eval", function(I2, args, kw)
    arg_count("eval", args, 1, 3)
    return I2:exec_snippet(args[1], args[2], args[3], "eval")
  end)
  add("exec", function(I2, args, kw)
    arg_count("exec", args, 1, 3)
    return I2:exec_snippet(args[1], args[2], args[3], "exec")
  end)

  b.super = values.SUPER_MARKER
  b.type = types.type
  b.object = values.OBJECT
  b.Ellipsis = values.ELLIPSIS
  b.NotImplemented = values.NOT_IMPLEMENTED
  for name, t in pairs(types) do
    if name ~= "NoneType" and name ~= "type" then b[name] = t end
  end
  for _, name in ipairs(values.EXC_ORDER) do
    b[name] = values.EXC_CLASSES[name]
  end
  return b
end

return M
