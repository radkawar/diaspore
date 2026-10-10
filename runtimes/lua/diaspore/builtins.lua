-- Builtin functions and type constructors (spec section 9).

local values = require("diaspore.values")
local format = require("diaspore.format")
local iter = require("diaspore.iter")
local ops = require("diaspore.ops")
local H = require("diaspore.helpers")
local buffer = require("diaspore.buffer")
local bigint = require("diaspore.bigint")
local pcall = require("diaspore.protected").call

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

local function numeric_text(s)
  if not s:find("[\128-\255]") then return s end
  local out = {}
  for _, cp in utf8.codes(s, true) do
    local digit = H.decimal_value(cp)
    out[#out + 1] = digit ~= nil and string.char(48 + digit)
      or (cp >= 128 and H.is_space_cp(cp) and " " or utf8.char(cp))
  end
  return table.concat(out)
end

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
  local requested_base = base
  local function fail()
    throw("ValueError", "invalid literal for int() with base " .. requested_base .. ": "
      .. format.repr_string(orig))
  end
  local s = numeric_text(orig):match("^%s*(.-)%s*$")
  local sign = ""
  if s:sub(1, 1) == "+" or s:sub(1, 1) == "-" then
    sign = s:sub(1, 1)
    s = s:sub(2)
  end
  local lower = s:lower()
  local prefix = lower:sub(2, 2)
  local prefix_base = prefix == "x" and 16 or prefix == "o" and 8 or prefix == "b" and 2 or nil
  local auto = base == 0
  if auto then base = lower:sub(1, 1) == "0" and prefix_base or 10 end
  if lower:sub(1, 1) == "0" and prefix_base == base then
    lower = lower:sub(3)
    if lower:sub(1, 1) == "_" then lower = lower:sub(2) end
  end
  local cleaned = strip_underscores(lower)
  if cleaned == nil or cleaned == "" then fail() end
  if auto and base == 10 and cleaned:sub(1, 1) == "0" and cleaned:find("[1-9]") then fail() end
  for i = 1, #cleaned do
    local d = DIGITS:find(cleaned:sub(i, i), 1, true)
    if d == nil or d > base then fail() end
  end
  return bigint.parse(sign .. cleaned, base)
end
M.parse_py_int = parse_py_int

local function parse_py_float(orig)
  local function fail()
    throw("ValueError", "could not convert string to float: "
      .. format.repr_string(orig))
  end
  local s = numeric_text(orig):match("^%s*(.-)%s*$")
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
  for pos in s:gmatch("()_") do
    if not s:sub(pos - 1, pos - 1):match("%d") or not s:sub(pos + 1, pos + 1):match("%d") then fail() end
  end
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
  if f == 0 and cleaned:sub(1, 1) == "-" then return -0.0 end
  return f * 1.0
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
values.OBJECT.ns.__new__ = bi("__new__", function(_I, args, kw)
  if #args < 1 or kindof(args[1]) ~= "class" then throw("TypeError", "object.__new__ requires a type") end
  return values.new_instance(args[1])
end)
values.OBJECT.ns.__new__.bound_self = values.OBJECT
values.OBJECT.ns.__init__ = bi("__init__", function(_I, args, kw)
  if #args == 0 then throw("TypeError", "descriptor '__init__' needs an argument") end
  return NONE
end, "wrapper_descriptor")
values.OBJECT.ns.__getattribute__ = bi("__getattribute__", function(I2, args, kw)
  arg_count("__getattribute__", args, 2, 2)
  return I2:default_get_attr(args[1], attr_name(args[2]))
end)
values.OBJECT.ns.__repr__ = bi("__repr__", function(I, args, kw)
  arg_count("__repr__", args, 1, 1); H.no_kw("__repr__", kw)
  return ops.object_repr(I, args[1])
end, "wrapper_descriptor")
values.OBJECT.ns.__format__ = bi("__format__", function(I, args, kw)
  arg_count("__format__", args, 2, 2); H.no_kw("__format__", kw)
  local spec = I:raw(args[2])
  if type(spec) ~= "string" then throw("TypeError", "__format__() argument must be str, not " .. type_name(args[2])) end
  if spec ~= "" then
    throw("TypeError", "unsupported format string passed to " .. type_name(args[1]) .. ".__format__")
  end
  return I:str(args[1])
end)
values.OBJECT.ns.__str__ = bi("__str__", function(_I, args, kw)
  arg_count("__str__", args, 1, 1)
  return ops.repr(_I, args[1])
end, "wrapper_descriptor")
values.OBJECT.ns.__subclasshook__ = { kind = "classmethod", fn = bi("__subclasshook__", function()
  return values.NOT_IMPLEMENTED
end) }
values.OBJECT.ns.__init_subclass__ = { kind = "classmethod", fn = bi("__init_subclass__", function(_I, args, kw)
  arg_count("__init_subclass__", args, 1, 1)
  if #kw > 0 then throw("TypeError", args[1].name .. ".__init_subclass__() takes no keyword arguments") end
  return NONE
end) }
values.OBJECT.ns.__eq__ = bi("__eq__", function(_I, args, kw)
  arg_count("__eq__", args, 2, 2); H.no_kw("__eq__", kw)
  if ops.is_identical(args[1], args[2]) then return true end
  return values.NOT_IMPLEMENTED
end, "wrapper_descriptor")
values.OBJECT.ns.__ne__ = bi("__ne__", function(I2, args, kw)
  arg_count("__ne__", args, 2, 2); H.no_kw("__ne__", kw)
  local result = I2:call(I2:get_attr(args[1], "__eq__"), { args[2] }, {})
  if result == values.NOT_IMPLEMENTED then return result end
  return not I2:truth(result)
end, "wrapper_descriptor")
require("diaspore.object").install()

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
  local code = NONE
  if #args == 1 then code = args[1] end
  values.throw_val(values.make_exc("SystemExit", code))
end
M.bi_exit = bi_exit

local function int_arg(v, I)
  return H.index_value(I, v)
end

local function bi_enumerate(I, args, kw)
  arg_count("enumerate", args, 1, 2)
  local start = 0
  if #args == 2 then start = int_arg(args[2], I) end
  local kv = H.kw_get(kw, "start")
  if kv ~= nil then start = int_arg(kv, I) end
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
  H.no_kw("reversed", kw)
  local v = args[1]
  local k = kindof(v)
  if k == "instance" then
    local reverse = I:lookup_type_attr(v.cls, "__reversed__")
    if reverse ~= nil then
      if reverse == NONE then throw("TypeError", "'" .. type_name(v) .. "' object is not reversible") end
      return I:call(I:bind_attr(reverse, v, v.cls), {}, {})
    end
    if I:lookup_type_attr(v.cls, "__getitem__") == nil then
      throw("TypeError", "'" .. type_name(v) .. "' object is not reversible")
    end
  elseif k == "dictview" then
    return iter.dict_view_iter(v, true)
  elseif k == "dict" or k == "mappingproxy" then
    return iter.dict_view_iter(values.dict_view(v, "keys"), true)
  elseif k == "range" then
    local n = values.range_len(v)
    local start = bigint.add(v.start, bigint.mul(bigint.sub(n, 1), v.step))
    return iter.range_iter(values.range(start, bigint.sub(v.start, v.step), bigint.neg(v.step)))
  elseif k ~= "list" and k ~= "tuple" and type(v) ~= "string"
      and k ~= "bytes" and k ~= "bytearray" and k ~= "memoryview" then
    throw("TypeError", "'" .. type_name(v) .. "' object is not reversible")
  end
  local position = bigint.sub(ops.diaspore_len(I, v), 1)
  local done = false
  return { kind = "iter", iname = "reversed", retval = NONE, next = function()
    if done or bigint.cmp(position, 0) < 0 then return NONE, false end
    local saved = #I.stack
    local ok, value = pcall(ops.get_index, I, v, position)
    if not ok then
      if values.is_exc(value) and (values.exc_isa(value.value, "IndexError")
          or values.exc_isa(value.value, "StopIteration")) then
        I:trim(saved)
        done = true
        return NONE, false
      end
      error(value, 0)
    end
    position = bigint.sub(position, 1)
    return value, true
  end }
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
  H.no_kw("abs", kw)
  local v = args[1]
  if kindof(v) == "instance" then
    local method = I:lookup_type_attr(v.cls, "__abs__")
    if method ~= nil then return I:call(I:bind_attr(method, v, v.cls), {}, {}) end
    v = I:raw(v)
  end
  local n = as_index(v)
  if n ~= nil then return bigint.abs(n) end
  if math.type(v) == "float" then return math.abs(v) end
  if kindof(v) == "complex" then
    return math.sqrt(v.re * v.re + v.im * v.im)
  end
  throw("TypeError", "bad operand type for abs(): '" .. type_name(v) .. "'")
end

local function bi_round(_I, args, kw)
  arg_count("round", args, 1, 2)
  local has_nd = false
  local nd = 0
  if #args == 2 and not is_none(args[2]) then
    nd = int_arg(args[2], _I)
    has_nd = true
  end
  local ndkw = H.kw_get(kw, "ndigits")
  if ndkw ~= nil then
    if #args == 2 then throw("TypeError", "argument for round() given by name ('ndigits') and position (2)") end
    has_nd = not is_none(ndkw)
    if has_nd then nd = int_arg(ndkw, _I) end
  end
  if kindof(args[1]) == "instance" and values.class_lookup(args[1].cls, "__round__") then
    return _I:call_dunder(args[1], "__round__", has_nd and { nd } or {})
  end
  local iv = as_index(args[1])
  if iv ~= nil then
    if not has_nd or bigint.cmp(nd, 0) >= 0 then return iv end
    local mag = bigint.abs(iv)
    local digits = #bigint.tostring(mag)
    if bigint.cmp(nd, -digits) < 0 then return 0 end
    local p = bigint.pow(10, bigint.neg(nd))
    local q, r = bigint.divmod(mag, p)
    local comparison = bigint.cmp(bigint.mul(r, 2), p)
    local _, parity = bigint.divmod(q, 2)
    if comparison > 0 or (comparison == 0 and parity == 1) then q = bigint.add(q, 1) end
    local out = bigint.mul(q, p)
    return bigint.cmp(iv, 0) < 0 and bigint.neg(out) or out
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
  nd = values.clamp_index(nd, -400, 400)
  return format.round_float_digits(f, nd)
end

local function bi_divmod(I, args, kw)
  arg_count("divmod", args, 2, 2)
  local a, b = as_index(args[1]), as_index(args[2])
  if a ~= nil and b ~= nil then
    local q, r = bigint.divmod(a, b)
    return values.tuple({ q, r })
  end
  local q = ops.binop(I, "floordiv", args[1], args[2])
  local r = ops.binop(I, "mod", args[1], args[2])
  return values.tuple({ q, r })
end

local function bi_pow(I, args, kw)
  arg_count("pow", args, 2, 3)
  if #args == 3 and not is_none(args[3]) then
    local a = as_index(args[1])
    local b = as_index(args[2])
    local m = as_index(args[3])
    if a == nil or b == nil or m == nil then
      throw("TypeError", "pow() 3rd argument not allowed unless all arguments are integers")
    end
    if m == 0 then throw("ValueError", "pow() 3rd argument cannot be 0") end
    return bigint.pow(a, b, m)
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
  local n = int_arg(args[1], _I)
  if bigint.cmp(n, 0) < 0 or bigint.cmp(n, 0x110000) >= 0 then
    throw("ValueError", "chr() arg not in range(0x110000)")
  end
  return utf8.char(n)
end

local function base_conv(name, prefix, base)
  return function(_I, args, kw)
    arg_count(name, args, 1, 1)
    local n = int_arg(args[1], _I)
    local sign = bigint.cmp(n, 0) < 0 and "-" or ""
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
  elseif iter.is_iterator(I, v) then
    if #args == 1 then return iter.next_value(I, v) end
    local saved = #I.stack
    local ok, result = pcall(iter.next_value, I, v)
    if ok then return result end
    if values.is_exc(result) and values.exc_isa(result.value, "StopIteration") then
      I:trim(saved)
      return args[2]
    end
    error(result, 0)
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
  if name == "bytearray" or name == "memoryview" then return kindof(v) == name end
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
  return type_name(v) == name
end

local function bi_isinstance(_I, args, kw)
  arg_count("isinstance", args, 2, 2)
  local v = args[1]
  local classes
  if kindof(args[2]) == "tuple" or kindof(args[2]) == "UnionType" then
    classes = kindof(args[2]) == "tuple" and args[2].items or args[2].args.items
  else
    classes = { args[2] }
  end
  for _, c in ipairs(classes) do
    local ck = kindof(c)
    if ck == "class" then
      if c == values.OBJECT then return true end
      local actual = kindof(v) == "instance" and v.cls
        or (kindof(v) == "class" or kindof(v) == "btype") and (v.metaclass or _I.types.type)
      if actual == c then return true end
      local hook = c.metaclass and kindof(c.metaclass) == "class"
        and values.class_lookup(c.metaclass, "__instancecheck__")
      if hook then
        if _I:truth(_I:call(_I:bind_attr(hook, c, c.metaclass), { v }, {})) then return true end
      elseif actual and values.issub(actual, c) then
        return true
      end
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
  if kindof(args[2]) == "tuple" or kindof(args[2]) == "UnionType" then
    classes = kindof(args[2]) == "tuple" and args[2].items or args[2].args.items
  else
    classes = { args[2] }
  end
  for _, c in ipairs(classes) do
    if c == values.OBJECT then return true end
  end
  for _, c in ipairs(classes) do
    local ck = kindof(c)
    local hook = ck == "class" and c.metaclass and kindof(c.metaclass) == "class"
      and values.class_lookup(c.metaclass, "__subclasscheck__")
    if hook then
      if _I:truth(_I:call(_I:bind_attr(hook, c, c.metaclass), { a }, {})) then return true end
    elseif ak == "class" and (ck == "class" or ck == "btype") and values.issub(a, c) then
      return true
    elseif ak == "btype" and ck == "btype" then
      if a.name == c.name or (a.name == "bool" and c.name == "int") then return true end
    end
  end
  return false
end

local function bi_callable(_I, args, kw)
  arg_count("callable", args, 1, 1)
  local k = kindof(args[1])
  if k == "function" or k == "boundmethod" or k == "class" or k == "btype"
      or k == "builtin" or k == "staticmethod" or k == "GenericAlias" then
    return true
  end
  if k == "nativeobject" then return args[1].methods.__call__ ~= nil end
  if k == "instance" then
    return values.class_lookup(args[1].cls, "__call__") ~= nil
  end
  return false
end

-- ---------------------------------------------------------------------------
-- type constructors

local function ctor_int(I, args, kw)
  arg_count("int", args, 0, 2)
  local basekw = H.kw_get(kw, "base")
  if #args == 0 then
    if basekw ~= nil then throw("TypeError", "int() missing string argument") end
    return 0
  end
  local v = args[1]
  if buffer.is_buffer(v) then v = buffer.data(v) end
  if #args == 2 or basekw ~= nil then
    if #args == 2 and basekw ~= nil then throw("TypeError", "int() takes at most 2 arguments (3 given)") end
    local base = int_arg(basekw or args[2], I)
    if type(v) ~= "string" then
      throw("TypeError", "int() can't convert non-string with explicit base")
    end
    if base ~= 0 and (bigint.cmp(base, 2) < 0 or bigint.cmp(base, 36) > 0) then
      throw("ValueError", "int() base must be >= 2 and <= 36, or 0")
    end
    return parse_py_int(v, base)
  end
  if kindof(v) == "instance" then
    for _, d in ipairs({ "__int__", "__index__", "__trunc__" }) do
      if values.class_lookup(v.cls, d) ~= nil then
        local result = as_index(I:call_dunder(v, d, {}))
        if result == nil then throw("TypeError", d .. " returned non-int") end
        return result
      end
    end
    if v.prim ~= nil and (values.is_number(v.prim) or type(v.prim) == "string") then
      return ctor_int(I, { v.prim }, {})
    end
    throw("TypeError",
      "int() argument must be a string or a real number, not '" .. v.cls.name .. "'")
  end
  local n = as_index(v)
  if n ~= nil then return n end
  if math.type(v) == "float" then return bigint.from_float(v) end
  if type(v) == "string" then return parse_py_int(v, 10) end
  throw("TypeError",
    "int() argument must be a string or a real number, not '" .. type_name(v) .. "'")
end

local function ctor_float(I, args, kw)
  arg_count("float", args, 0, 1)
  if #args == 0 then return 0.0 end
  local v = args[1]
  if buffer.is_buffer(v) then v = buffer.data(v) end
  if kindof(v) == "instance" then
    if values.class_lookup(v.cls, "__float__") ~= nil then
      local result = I:call_dunder(v, "__float__", {})
      if math.type(result) ~= "float" then throw("TypeError", "__float__ returned non-float") end
      return result
    end
    if values.class_lookup(v.cls, "__index__") ~= nil then
      local result = as_index(I:call_dunder(v, "__index__", {}))
      if result == nil then throw("TypeError", "__index__ returned non-int") end
      return as_float(result)
    end
    if v.prim ~= nil and values.is_number(v.prim) then return as_float(v.prim) end
    throw("TypeError",
      "float() argument must be a string or a real number, not '" .. v.cls.name .. "'")
  end
  if math.type(v) == "float" then return v end
  local n = as_index(v)
  if n ~= nil then return as_float(n) end
  if type(v) == "string" then return parse_py_float(v) end
  throw("TypeError",
    "float() argument must be a string or a real number, not '" .. type_name(v) .. "'")
end

local function ctor_bool(I, args, kw)
  arg_count("bool", args, 0, 1)
  if #args == 0 then return false end
  return I:truth(args[1])
end

local function text_constructor_args(name, args, kw, source_name)
  arg_count(name, args, 0, 3)
  local source, encoding, errors = args[1], args[2], args[3]
  for _, entry in ipairs(kw) do
    if entry.k == source_name then
      if source ~= nil then throw("TypeError", "multiple values for argument '" .. source_name .. "'") end
      source = entry.v
    elseif entry.k == "encoding" then
      if encoding ~= nil then throw("TypeError", "multiple values for argument 'encoding'") end
      encoding = entry.v
    elseif entry.k == "errors" then
      if errors ~= nil then throw("TypeError", "multiple values for argument 'errors'") end
      errors = entry.v
    else throw("TypeError", "invalid keyword argument '" .. entry.k .. "'") end
  end
  return source, encoding, errors
end

local function ctor_str(I, args, kw)
  local source, encoding, errors = text_constructor_args("str", args, kw, "object")
  if source == nil then return "" end
  if encoding ~= nil or errors ~= nil then
    if not buffer.is_buffer(source) then throw("TypeError", "decoding to str: need a bytes-like object") end
    return H.decode_bytes(values.bytes(buffer.data(source)), encoding, errors, I)
  end
  return I:str(source)
end

local function ctor_bytes(I, args, kw, mutable)
  local v, encoding, errors = text_constructor_args(mutable and "bytearray" or "bytes", args, kw, "source")
  if v == nil then
    if encoding ~= nil or errors ~= nil then throw("TypeError", "encoding or errors without a string argument") end
    return values.bytes("")
  end
  if type(v) == "string" then
    if encoding == nil then throw("TypeError", "string argument without an encoding") end
    return H.encode_str_to(v, encoding, errors, I)
  end
  if encoding ~= nil or errors ~= nil then throw("TypeError", "encoding or errors without a string argument") end
  if not mutable and kindof(v) == "instance" and values.class_lookup(v.cls, "__bytes__") then
    local result = I:call_dunder(v, "__bytes__", {})
    if kindof(result) ~= "bytes" then throw("TypeError", "__bytes__ returned non-bytes") end
    return result
  end
  if buffer.is_buffer(v) then return values.bytes(buffer.data(v)) end
  local n = as_index(v)
  if n == nil and kindof(v) == "instance" and values.class_lookup(v.cls, "__index__") then
    n = as_index(I:call_dunder(v, "__index__", {}))
    if n == nil then throw("TypeError", "__index__ returned non-int") end
  end
  if n ~= nil then
    if bigint.cmp(n, 0) < 0 then throw("ValueError", "negative count") end
    n = values.to_size(n)
    I:fuel_n(n)
    return values.bytes(string.rep("\0", n))
  end
  local items = iter.iter_to_array(I, v)
  local out = {}
  for i = 1, #items do
    local x = as_index(items[i])
    if x == nil and kindof(items[i]) == "instance" and values.class_lookup(items[i].cls, "__index__") then
      x = as_index(I:call_dunder(items[i], "__index__", {}))
    end
    if x == nil then
      throw("TypeError",
        "'" .. type_name(items[i]) .. "' object cannot be interpreted as an integer")
    end
    if bigint.cmp(x, 0) < 0 or bigint.cmp(x, 255) > 0 then throw("ValueError", "bytes must be in range(0, 256)") end
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
  if #args == 1 then H.dict_update_from(I, out, args[1]) end
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
    imf = kindof(re) == "complex" and imf + im.re or im.re
  else
    local f = as_float(im)
    if f == nil then throw("TypeError", "complex() argument must be a string or a number") end
    imf = kindof(re) == "complex" and imf + f or f
  end
  return values.complex(rf, imf)
end

local function ctor_range(_I, args, kw)
  arg_count("range", args, 1, 3)
  local vals = {}
  for i = 1, #args do vals[i] = int_arg(args[i], _I) end
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
    bytearray = values.btype("bytearray", function(I2, args, kw)
      return buffer.bytearray(ctor_bytes(I2, args, kw, true).b)
    end),
    memoryview = values.btype("memoryview", function(_I, args, kw)
      arg_count("memoryview", args, 1, 1)
      H.no_kw("memoryview", kw)
      return buffer.memoryview(args[1])
    end),
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
  types.NoneType.ns.__doc__ = NONE
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
  local internal_types = {}
  types.type = values.btype("type", function(I2, args, kw)
    if #args ~= 1 then -- type(name, bases, ns): dynamic class creation
      if #args ~= 3 then
        throw("TypeError", "type() takes 1 or 3 arguments")
      end
      local name, bases, ns = I2:raw(args[1]), I2:raw(args[2]), I2:raw(args[3])
      if type(name) ~= "string" or kindof(bases) ~= "tuple" or kindof(ns) ~= "dict" then
        throw("TypeError", "type() argument types: (str, tuple, dict)")
      end
      local checked = {}
      for _, b in ipairs(bases.items) do
        if kindof(b) ~= "class" and kindof(b) ~= "btype" then
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
      return I2:build_class(name, checked, ns_map, ns_order, nil, kw, args[3])
    end
    local v = args[1]
    local k = kindof(v)
    if k == "instance" then return v.cls end
    if k == "class" then return v.metaclass or types.type end
    if k == "btype" then return types.type end
    if k == "dictview" then return view_types[v.view] end
    if k == "nativeobject" and v.pytype ~= nil then return v.pytype end
    local t = type(v)
    if t == "boolean" then return types.bool end
    if bigint.is_bigint(v) then return types.int end
    if t == "number" then
      return math.type(v) == "integer" and types.int or types.float
    end
    if t == "string" then return types.str end
    if k == "bytes" then return types.bytes end
    if k == "bytearray" or k == "memoryview" then return types[k] end
    if k == "staticmethod" or k == "classmethod" or k == "property" then return types[k] end
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
    if internal_types[name] == nil then
      internal_types[name] = values.btype(name, function(I3, args, kw)
        if name == "function" then
          local names = { "code", "globals", "name", "argdefs", "closure" }
          if #args + #kw > #names then
            throw("TypeError", "function() takes at most 5 arguments (" .. (#args + #kw) .. " given)")
          end
          local params = {}
          for index, value in ipairs(args) do params[index] = value end
          for _, entry in ipairs(kw) do
            local index
            for position, field in ipairs(names) do
              if field == entry.k then index = position; break end
            end
            if index == nil then throw("TypeError", "'" .. entry.k .. "' is an invalid keyword argument for function()") end
            if params[index] ~= nil then
              throw("TypeError", "argument for function() given by name ('" .. entry.k .. "') and position (" .. index .. ")")
            end
            params[index] = entry.v
          end
          for index = 1, 2 do
            if params[index] == nil then
              throw("TypeError", "function() missing required argument '" .. names[index] .. "' (pos " .. index .. ")")
            end
          end
          for index = 3, 5 do if params[index] == nil then params[index] = NONE end end
          local code, globals = params[1], I3:raw(params[2])
          if kindof(code) ~= "code" then throw("TypeError", "function() argument 'code' must be code, not " .. type_name(code)) end
          if kindof(globals) ~= "dict" then throw("TypeError", "function() argument 'globals' must be dict, not " .. type_name(params[2])) end
          local function_name = I3:raw(params[3])
          if function_name ~= NONE and type(function_name) ~= "string" then throw("TypeError", "arg 3 (name) must be None or string") end
          local defaults, closure = I3:raw(params[4]), I3:raw(params[5])
          if defaults ~= NONE and kindof(defaults) ~= "tuple" then throw("TypeError", "arg 4 (defaults) must be None or tuple") end
          local freevars = code.fields.co_freevars.items
          if closure == NONE and #freevars > 0 then throw("TypeError", "arg 5 (closure) must be tuple") end
          if closure ~= NONE and kindof(closure) ~= "tuple" then throw("TypeError", "arg 5 (closure) must be None or tuple") end
          local cells = closure == NONE and {} or closure.items
          if #cells ~= #freevars then
            throw("ValueError", code.fields.co_name .. " requires closure of length " .. #freevars .. ", not " .. #cells)
          end
          for _, cell in ipairs(cells) do
            if kindof(cell) ~= "cell" then throw("TypeError", "arg 5 (closure) expected cell, found " .. type_name(cell)) end
          end
          local fn = I3:function_from_code(code, I3:dictionary_namespace(globals),
            function_name ~= NONE and function_name or nil, params[4], params[5])
          fn.globals_mapping = params[2]
          if params[3] ~= NONE then fn.name_value = params[3] end
          return fn
        end
        if name == "GenericAlias" then
          arg_count(name, args, 2, 2); H.no_kw(name, kw)
          return require("diaspore.typeexpr").alias(args[1], args[2], I3)
        end
        if name == "mappingproxy" then
          arg_count(name, args, 1, 1); H.no_kw(name, kw)
          local mapping = I3:raw(args[1])
          if kindof(mapping) ~= "dict" then throw("TypeError", "mappingproxy() argument must be a mapping") end
          return { kind = "mappingproxy", mapping = mapping }
        end
        if name == "cell" then
          arg_count(name, args, 0, 1); H.no_kw(name, kw)
          return { kind = "cell", frame = { vars = { contents = args[1] } }, name = "contents" }
        end
        if name == "method" then
          arg_count(name, args, 2, 2); H.no_kw(name, kw)
          if args[2] == NONE then throw("TypeError", "instance must not be None") end
          return { kind = "boundmethod", fn = args[1], recv = args[2] }
        end
        throw("TypeError", "cannot construct '" .. name .. "' values")
      end)
      if name == "GenericAlias" or name == "UnionType" then internal_types[name].module = "types" end
      if name == "GenericAlias" then
        internal_types[name].ns.__new__ = I2:builtin_unbound(name, "__new__")
      end
      if name == "function" then
        for _, field in ipairs({ "__code__", "__globals__", "__closure__" }) do
          internal_types[name].ns[field] = { kind = "descriptor", name = field,
            owner = internal_types[name], descriptor = field == "__code__" and "getset_descriptor" or "member_descriptor" }
        end
      end
    end
    return internal_types[name]
  end)
  I.types = types
  I.internal_types = internal_types
  types.dict.ns.fromkeys = bi("fromkeys", function(I2, args, kw)
    arg_count("fromkeys", args, 2, 3); H.no_kw("fromkeys", kw)
    local result = I2:call(args[1], {}, {})
    local value = args[3] == nil and NONE or args[3]
    for _, key in ipairs(iter.iter_to_array(I2, args[2])) do ops.set_index(I2, result, key, value) end
    return result
  end, "classmethod_descriptor")

  local b = {}
  local function add(name, fn)
    b[name] = bi(name, fn)
  end

  add("_typing_union", function(I2, args, kw)
    arg_count("_typing_union", args, 2, 2); H.no_kw("_typing_union", kw)
    local typeexpr = require("diaspore.typeexpr")
    local provider = I2.modules._typing
    local alias_type = provider and provider.frame and provider.frame.vars.TypeAliasType
    local function supported(value)
      return typeexpr.is_type(value, I2)
        or (kindof(value) == "instance" and value.cls == alias_type)
    end
    if not supported(args[1]) or not supported(args[2]) then return values.NOT_IMPLEMENTED end
    local left = args[1] == NONE and types.NoneType or args[1]
    local right = args[2] == NONE and types.NoneType or args[2]
    return typeexpr.union(left, right, I2)
  end)

  add("__import__", function(I2, args, kw)
    arg_count("__import__", args, 0, 5)
    local params, names = {}, { name = 1, globals = 2, locals = 3, fromlist = 4, level = 5 }
    for i, value in ipairs(args) do params[i] = value end
    for _, entry in ipairs(kw) do
      local index = names[entry.k]
      if index == nil then throw("TypeError", "__import__() got an unexpected keyword argument '" .. entry.k .. "'") end
      if params[index] ~= nil then throw("TypeError", "__import__() got multiple values for argument '" .. entry.k .. "'") end
      params[index] = entry.v
    end
    local name = params[1]
    if name == nil then throw("TypeError", "__import__() missing required argument 'name' (pos 1)") end
    if type(name) ~= "string" then throw("TypeError", "module name must be a string") end
    local level = as_index(params[5] or 0)
    if level == nil then throw("TypeError", "'level' must be an integer") end
    level = values.to_size(level)
    if level < 0 then throw("ValueError", "level must be >= 0") end
    if level > 0 then
      local globals = params[2]
      if kindof(globals) ~= "dict" then throw("TypeError", "globals must be a dict") end
      local package = values.dict_get(globals, "__package__")
      if package == nil or package == NONE then
        package = values.dict_get(globals, "__name__")
        if type(package) == "string" and not values.dict_has(globals, "__path__") then
          package = package:match("^(.*)%.") or ""
        end
      end
      if type(package) ~= "string" then throw("TypeError", "package must be a string") end
      if package == "" then throw("ImportError", "attempted relative import with no known parent package") end
      for _ = 2, level do
        package = package:match("^(.*)%.")
        if package == nil then throw("ImportError", "attempted relative import beyond top-level package") end
      end
      name = name == "" and package or package .. "." .. name
    elseif name == "" then throw("ValueError", "Empty module name") end
    local module = I2:import_module(name)
    local fromlist = params[4]
    if fromlist ~= nil and fromlist ~= NONE and I2:truth(fromlist) then
      local ns = module.kind == "module" and module.frame.vars or module.ns
      for _, member in ipairs(iter.iter_to_array(I2, fromlist)) do
        if type(member) ~= "string" then throw("TypeError", "Item in from list must be str, not " .. type_name(member)) end
        local qualified = name .. "." .. member
        if member ~= "*" and ns[member] == nil and I2:has_module(qualified) then I2:import_module(qualified) end
      end
      return module
    end
    return I2:import_module(name:match("^[^.]+"))
  end)

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
    return iter.iter_object(I2, args[1])
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
  for _, descriptor_kind in ipairs({ "staticmethod", "classmethod" }) do
    types[descriptor_kind] = values.btype(descriptor_kind, function(_I2, args, kw)
      arg_count(descriptor_kind, args, 1, 1); H.no_kw(descriptor_kind, kw)
      return { kind = descriptor_kind, fn = args[1] }
    end)
    types[descriptor_kind].ns.__init__ = bi("__init__", function(I2, args, kw)
      arg_count(descriptor_kind, args, 2, 2); H.no_kw(descriptor_kind, kw)
      local receiver = args[1]
      local descriptor = { kind = descriptor_kind, fn = args[2] }
      if kindof(receiver) == "instance" then receiver.prim = descriptor else receiver.fn = args[2] end
      return NONE
    end)
    types[descriptor_kind].ns.__get__ = bi("__get__", function(I2, args, kw)
      arg_count("__get__", args, 2, 3); H.no_kw("__get__", kw)
      local descriptor = I2:raw(args[1])
      if descriptor_kind == "staticmethod" then return descriptor.fn end
      local cls = args[3]
      if cls == nil or cls == NONE then cls = types.type.ctor(I2, { args[2] }, {}) end
      return { kind = "boundmethod", fn = descriptor.fn, recv = cls }
    end)
  end
  types.staticmethod.ns.__call__ = bi("__call__", function(I2, args, kw)
    local forwarded = {}
    for i = 2, #args do forwarded[i - 1] = args[i] end
    return I2:call(I2:raw(args[1]).fn, forwarded, kw)
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
    if k == "iter" and (x.iname == "generator" or x.iname == "coroutine") then return x end
    if k == "instance" and values.class_lookup(x.cls, "__await__") ~= nil then
      local r = I2:call_dunder(x, "__await__", {})
      if kindof(r) == "iter" and (r.iname == "generator" or r.iname == "coroutine") then return r end
      throw("TypeError", "__await__() must return a coroutine or generator")
    end
    throw("TypeError",
      "object " .. type_name(x) .. " can't be used in 'await' expression")
  end)

  types.property = values.btype("property", function(I2, args, kw)
    arg_count("property", args, 0, 4)
    local fields, names = {}, { fget = 1, fset = 2, fdel = 3, doc = 4 }
    for i, value in ipairs(args) do fields[i] = value end
    for _, entry in ipairs(kw) do
      local index = names[entry.k]
      if index == nil then throw("TypeError", "property() got an unexpected keyword argument '" .. entry.k .. "'") end
      if fields[index] ~= nil then throw("TypeError", "property() got multiple values for argument '" .. entry.k .. "'") end
      fields[index] = entry.v
    end
    for i = 1, 4 do if fields[i] == NONE then fields[i] = nil end end
    local doc = fields[4]
    if doc == nil and fields[1] ~= nil then
      local saved = #I2.stack
      local ok, result = pcall(I2.get_attr, I2, fields[1], "__doc__")
      if ok then doc = result
      elseif values.is_exc(result) and values.exc_isa(result.value, "AttributeError") then I2:trim(saved)
      else error(result, 0) end
    end
    local descriptor = values.property(fields[1], fields[2], fields[3], doc)
    descriptor.getter_doc = fields[4] == nil
    return descriptor
  end)
  types.property.ns.__init__ = bi("__init__", function(I2, args, kw)
    local receiver, forwarded = args[1], {}
    for i = 2, #args do forwarded[#forwarded + 1] = args[i] end
    local descriptor = types.property.ctor(I2, forwarded, kw)
    if kindof(receiver) == "instance" then
      receiver.prim = descriptor
      receiver.d.__doc__ = descriptor.doc == nil and NONE or descriptor.doc
    else
      receiver.fget, receiver.fset, receiver.fdel, receiver.doc =
        descriptor.fget, descriptor.fset, descriptor.fdel, descriptor.doc
      receiver.getter_doc = descriptor.getter_doc
    end
    return NONE
  end)
  for _, method in ipairs({ "getter", "setter", "deleter" }) do
    types.property.ns[method] = bi(method, function(I2, args, kw)
      arg_count(method, args, 2, 2); H.no_kw(method, kw)
      local descriptor = I2:raw(args[1])
      local factory = kindof(args[1]) == "instance" and args[1].cls or types.property
      local forwarded = {
        descriptor.fget == nil and NONE or descriptor.fget,
        descriptor.fset == nil and NONE or descriptor.fset,
        descriptor.fdel == nil and NONE or descriptor.fdel,
        descriptor.doc == nil and NONE or descriptor.doc,
      }
      forwarded[method == "getter" and 1 or method == "setter" and 2 or 3] = args[2]
      if descriptor.getter_doc then forwarded[4] = NONE end
      return I2:call(factory, forwarded, {})
    end)
  end
  types.property.ns.__get__ = bi("__get__", function(I2, args, kw)
    arg_count("__get__", args, 2, 3); H.no_kw("__get__", kw)
    if args[2] == NONE then return args[1] end
    local descriptor = I2:raw(args[1])
    if descriptor.fget == nil then throw("AttributeError", "property has no getter") end
    return I2:call(descriptor.fget, { args[2] }, {})
  end)
  for _, method in ipairs({ "__set__", "__delete__" }) do
    types.property.ns[method] = bi(method, function(I2, args, kw)
      local count = method == "__set__" and 3 or 2
      arg_count(method, args, count, count); H.no_kw(method, kw)
      local descriptor = I2:raw(args[1])
      local target = descriptor.fdel
      if method == "__set__" then target = descriptor.fset end
      if target == nil then throw("AttributeError", "property has no " .. (method == "__set__" and "setter" or "deleter")) end
      local forwarded = { args[2] }
      if method == "__set__" then forwarded[2] = args[3] end
      I2:call(target, forwarded, {})
      return NONE
    end)
  end
  add("globals", function(I2, args, kw)
    arg_count("globals", args, 0, 0)
    return I2:live_dict((I2:caller_frame() and I2:caller_frame().globals) or nil)
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
      local depth = #I2.stack
      local ok, result = pcall(I2.get_attr, I2, o, "__dict__")
      if ok then return result end
      if not values.is_exc(result) or not values.exc_isa(result.value, "AttributeError") then error(result, 0) end
      I2:trim(depth)
      throw("TypeError", "vars() argument must have __dict__")
    end
    if k == "module" then return I2:live_dict(o.frame.vars) end
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
    local flags = H.kw_get(kw, "flags") or args[4] or 0
    flags = int_arg(flags, I2)
    if bigint.cmp(flags, -2147483648) < 0 or bigint.cmp(flags, 2147483647) > 0 then
      throw("OverflowError", "Python int too large to convert to C int")
    end
    if flags & 1024 ~= 0 then
      if not I2:has_module("_ast") and I2.modules._ast == nil then
        throw("RuntimeError", "AST compilation requires _ast (bundle it with `import ast`)")
      end
      local module = I2:import_module("_ast")
      local forwarded = {
        { k = "flags", v = flags },
        { k = "dont_inherit", v = H.kw_get(kw, "dont_inherit") or args[5] or false },
        { k = "optimize", v = H.kw_get(kw, "optimize") or args[6] or -1 },
        { k = "_feature_version", v = H.kw_get(kw, "_feature_version") or -1 },
      }
      return I2:call(I2:get_attr(module, "_compile"), {
        args[1], args[2] or H.kw_get(kw, "filename") or "<string>",
        args[3] or H.kw_get(kw, "mode") or "exec" }, forwarded)
    end
    return I2:compile_snippet(args[1], args[2] or "<string>", args[3] or "exec")
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
