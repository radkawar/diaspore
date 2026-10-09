-- Operators, comparisons, indexing/slicing, truthiness, repr/str, sorting -
-- CPython-parity semantics mirroring diaspore-py (reference), diaspore-go, diaspore-ts.

local values = require("diaspore.values")
local format = require("diaspore.format")
local iter = require("diaspore.iter")

local M = {}

local NONE = values.NONE
local NOT_IMPLEMENTED = values.NOT_IMPLEMENTED
local throw = values.throw
local type_name = values.type_name
local as_index = values.as_index
local as_float = values.as_float
local is_number = values.is_number
local class_lookup = values.class_lookup
local hash_key = values.hash_key

local BIN_SYM = {
  add = "+", sub = "-", mul = "*", div = "/", floordiv = "//", mod = "%",
  pow = "**", lshift = "<<", rshift = ">>", bitand = "&", bitor = "|",
  bitxor = "^", matmul = "@",
}

local BIN_DUNDER = {
  add = { "__add__", "__radd__" },
  sub = { "__sub__", "__rsub__" },
  mul = { "__mul__", "__rmul__" },
  div = { "__truediv__", "__rtruediv__" },
  floordiv = { "__floordiv__", "__rfloordiv__" },
  mod = { "__mod__", "__rmod__" },
  pow = { "__pow__", "__rpow__" },
  lshift = { "__lshift__", "__rlshift__" },
  rshift = { "__rshift__", "__rrshift__" },
  bitand = { "__and__", "__rand__" },
  bitor = { "__or__", "__ror__" },
  bitxor = { "__xor__", "__rxor__" },
  matmul = { "__matmul__", "__rmatmul__" },
}

local function binop_type_error(kind, l, r)
  throw("TypeError", "unsupported operand type(s) for " .. BIN_SYM[kind]
    .. ": '" .. type_name(l) .. "' and '" .. type_name(r) .. "'")
end

local function is_inst(v)
  return type(v) == "table" and v.kind == "instance"
end

local function kindof(v)
  if type(v) == "table" then return v.kind end
  return nil
end

local function is_set_view(v)
  return kindof(v) == "dictview" and v.view ~= "values"
end

local function is_setlike(v)
  return kindof(v) == "set" or is_set_view(v)
end

local function setlike_subset(I, a, b)
  if kindof(a) == "set" and kindof(b) == "set" then return M.set_is_subset(a, b) end
  local it = iter.get_iter(I, a)
  while true do
    local item, more = it.next()
    if not more then return true end
    if not M.contains(I, b, item) then return false end
  end
end

local function iterable_set(I, v)
  local out = values.new_set()
  local it = iter.get_iter(I, v)
  while true do
    local item, more = it.next()
    if not more then return out end
    values.set_add(out, item)
  end
end

local function view_binop(I, kind, l, r)
  if not is_set_view(l) and not is_set_view(r) then return NOT_IMPLEMENTED end
  if kind == "bitand" then
    local view, other = l, r
    if not is_set_view(l) then view, other = r, l end
    local out = values.new_set()
    local it = iter.get_iter(I, other)
    while true do
      local item, more = it.next()
      if not more then return out end
      if M.contains(I, view, item) then values.set_add(out, item) end
    end
  end
  if kind == "bitor" or kind == "sub" or kind == "bitxor" then
    return M.set_op(kind, iterable_set(I, l), iterable_set(I, r))
  end
  return NOT_IMPLEMENTED
end

-- ---------------------------------------------------------------------------
-- int / float arithmetic

-- Python ** for non-negative int exponent: exponentiation by squaring.
-- int64 overflow wraps (diaspore-lua limitation).
local function int_pow(a, b)
  local result = 1
  local base = a
  while b > 0 do
    if b & 1 == 1 then result = result * base end
    base = base * base
    b = b >> 1
  end
  return result
end

local function int_binop(I, kind, a, b)
  if kind == "add" then return a + b end
  if kind == "sub" then return a - b end
  if kind == "mul" then return a * b end
  if kind == "div" then
    if b == 0 then throw("ZeroDivisionError", "division by zero") end
    return (a + 0.0) / (b + 0.0)
  end
  if kind == "floordiv" then
    if b == 0 then throw("ZeroDivisionError", "integer division or modulo by zero") end
    return a // b
  end
  if kind == "mod" then
    if b == 0 then throw("ZeroDivisionError", "integer modulo by zero") end
    return a % b
  end
  if kind == "pow" then
    if b >= 0 then
      local cost = b // 8
      if cost > 1 then I:fuel_n(cost) end
      return int_pow(a, b)
    end
    if a == 0 then
      throw("ZeroDivisionError", "0.0 cannot be raised to a negative power")
    end
    return (a + 0.0) ^ (b + 0.0)
  end
  if kind == "lshift" then
    if b < 0 then throw("ValueError", "negative shift count") end
    if b >= 64 then return 0 end
    return a << b
  end
  if kind == "rshift" then
    if b < 0 then throw("ValueError", "negative shift count") end
    if b >= 63 then
      if a < 0 then return -1 end
      return 0
    end
    return a // (1 << b) -- arithmetic (sign-preserving floor) shift
  end
  if kind == "bitand" then return a & b end
  if kind == "bitor" then return a | b end
  if kind == "bitxor" then return a ~ b end
  throw("TypeError", "unknown int op " .. kind)
end

local function float_binop(kind, a, b)
  if kind == "add" then return a + b end
  if kind == "sub" then return a - b end
  if kind == "mul" then return a * b end
  if kind == "div" then
    if b == 0.0 then throw("ZeroDivisionError", "float division by zero") end
    return a / b
  end
  if kind == "floordiv" then
    if b == 0.0 then throw("ZeroDivisionError", "float floor division by zero") end
    return a // b
  end
  if kind == "mod" then
    if b == 0.0 then throw("ZeroDivisionError", "float modulo") end
    local m = math.fmod(a, b)
    if m ~= 0.0 and (m < 0) ~= (b < 0) then m = m + b end
    return m
  end
  if kind == "pow" then
    if a == 0.0 and b < 0 then
      throw("ZeroDivisionError", "0.0 cannot be raised to a negative power")
    end
    return a ^ b
  end
  throw("TypeError", "unsupported operand type(s) for " .. BIN_SYM[kind]
    .. ": 'float' and 'float'")
end

-- ---------------------------------------------------------------------------
-- sequence repeat / set ops

-- seq * n; returns nil when the pair is not a repeat form.
local function seq_repeat(seq, count, inplace)
  local sk = kindof(seq)
  local is_seq = type(seq) == "string" or sk == "bytes" or sk == "list" or sk == "tuple"
  local n = as_index(count)
  if n == nil then
    if is_seq and math.type(count) == "float" then
      throw("TypeError",
        "can't multiply sequence by non-int of type '" .. type_name(count) .. "'")
    end
    return nil
  end
  if not is_seq then return nil end
  if n < 0 then n = 0 end
  if type(seq) == "string" then return seq:rep(n) end
  if sk == "bytes" then return values.bytes(seq.b:rep(n)) end
  local items = {}
  local src = seq.items
  local m = #src
  for i = 1, n do
    local base = (i - 1) * m
    for j = 1, m do items[base + j] = src[j] end
  end
  if sk == "tuple" then return values.tuple(items) end
  if inplace then
    seq.items = items
    return seq
  end
  return values.list(items)
end

--- Apply a set union, intersection, difference, or symmetric difference.
function M.set_op(kind, a, b)
  local out = values.new_set(a.frozen)
  local ap = values.set_pairs(a)
  local bp = values.set_pairs(b)
  if kind == "bitor" then
    for _, p in ipairs(ap) do values.set_add_hk(out, p[1], p[2]) end
    for _, p in ipairs(bp) do values.set_add_hk(out, p[1], p[2]) end
  elseif kind == "bitand" then
    for _, p in ipairs(ap) do
      if values.set_has_hk(b, p[1], p[2]) then values.set_add_hk(out, p[1], p[2]) end
    end
  elseif kind == "sub" then
    for _, p in ipairs(ap) do
      if not values.set_has_hk(b, p[1], p[2]) then values.set_add_hk(out, p[1], p[2]) end
    end
  elseif kind == "bitxor" then
    for _, p in ipairs(ap) do
      if not values.set_has_hk(b, p[1], p[2]) then values.set_add_hk(out, p[1], p[2]) end
    end
    for _, p in ipairs(bp) do
      if not values.set_has_hk(a, p[1], p[2]) then values.set_add_hk(out, p[1], p[2]) end
    end
  end
  return out
end

--- Test whether every element of the first set is in the second.
function M.set_is_subset(a, b)
  for hk, e in pairs(a.map) do
    if not values.set_has_hk(b, e.hk or hk, e.v) then return false end
  end
  return true
end

-- ---------------------------------------------------------------------------
-- complex (backed by two floats)

-- CPython-style component formatting: shortest float repr, integral values
-- drop the trailing ".0" (repr(3+4j) == "(3+4j)").
local function complex_part(f)
  local s = format.py_float_str(f)
  if s:sub(-2) == ".0" then return s:sub(1, -3) end
  return s
end

--- Render a complex number using Python repr conventions.
function M.complex_repr(z)
  local re, im = z.re, z.im
  if re == 0.0 and 1.0 / re > 0 then -- real is +0.0: omit it
    return complex_part(im) .. "j"
  end
  local rs = complex_part(re)
  local ims = complex_part(im)
  if ims:sub(1, 1) == "-" then
    return "(" .. rs .. ims .. "j)"
  end
  return "(" .. rs .. "+" .. ims .. "j)"
end

local function as_complex(v)
  local k = kindof(v)
  if k == "complex" then return v.re, v.im end
  local f = as_float(v)
  if f ~= nil then return f, 0.0 end
  return nil
end

--- Apply arithmetic to complex operands.
function M.complex_binop(I, kind, l, r)
  local ar, ai = as_complex(l)
  local br, bi = as_complex(r)
  if ar == nil or br == nil then
    binop_type_error(kind, l, r)
  end
  if kind == "add" then return values.complex(ar + br, ai + bi) end
  if kind == "sub" then return values.complex(ar - br, ai - bi) end
  if kind == "mul" then
    return values.complex(ar * br - ai * bi, ar * bi + ai * br)
  end
  if kind == "div" then
    local d = br * br + bi * bi
    if d == 0.0 then throw("ZeroDivisionError", "complex division by zero") end
    return values.complex((ar * br + ai * bi) / d, (ai * br - ar * bi) / d)
  end
  if kind == "pow" then
    -- integer exponent: repeated squaring (exact for small integral powers)
    if bi == 0.0 and br == math.floor(br) and math.abs(br) <= 100 then
      local n = math.floor(br)
      local neg = n < 0
      if neg then n = -n end
      local rr, ri = 1.0, 0.0
      local xr, xi = ar, ai
      while n > 0 do
        if n & 1 == 1 then
          rr, ri = rr * xr - ri * xi, rr * xi + ri * xr
        end
        xr, xi = xr * xr - xi * xi, 2 * xr * xi
        n = n >> 1
      end
      if neg then
        local d = rr * rr + ri * ri
        return values.complex(rr / d, -ri / d)
      end
      return values.complex(rr, ri)
    end
    -- general: exp(log(base) * exp)
    if ar == 0.0 and ai == 0.0 then
      if br == 0.0 and bi == 0.0 then return values.complex(1.0, 0.0) end
      throw("ZeroDivisionError", "0.0 to a negative or complex power")
    end
    local logr = math.log(math.sqrt(ar * ar + ai * ai))
    local logi = math.atan(ai, ar)
    local pr = logr * br - logi * bi
    local pi = logr * bi + logi * br
    local ex = math.exp(pr)
    return values.complex(ex * math.cos(pi), ex * math.sin(pi))
  end
  binop_type_error(kind, l, r)
end

--- Compare complex operands by their real and imaginary parts.
function M.complex_eq(l, r)
  local ar, ai = as_complex(l)
  local br, bi = as_complex(r)
  if ar == nil or br == nil then return false end
  return ar == br and ai == bi
end

-- ---------------------------------------------------------------------------
-- binop

local function strict_subtype(l, r)
  if not is_inst(r) then return false end
  if is_inst(l) then return l.cls ~= r.cls and values.issub(r.cls, l.cls) end
  return r.cls.prim ~= nil and r.cls.prim.name == type_name(l)
end

local function primitive(v)
  if is_inst(v) and v.prim ~= nil then return v.prim end
  return v
end

local function binary_slot(I, kind, obj, other, name, reflected, inplace)
  if is_inst(obj) and class_lookup(obj.cls, name) ~= nil then
    return I:call_dunder(obj, name, { other })
  end
  if is_set_view(obj) then
    if reflected then return view_binop(I, kind, other, obj) end
    return view_binop(I, kind, obj, other)
  end
  local a, b = primitive(obj), primitive(other)
  if is_inst(a) or is_inst(b) or kind == "matmul" then return NOT_IMPLEMENTED end
  local ak, bk = kindof(a), kindof(b)
  local supported = false
  if as_index(a) ~= nil then
    supported = as_index(b) ~= nil
  elseif math.type(a) == "float" then
    supported = is_number(b) and (kind == "add" or kind == "sub" or kind == "mul"
      or kind == "div" or kind == "floordiv" or kind == "mod" or kind == "pow")
  elseif ak == "complex" then
    supported = (is_number(b) or bk == "complex")
      and (kind == "add" or kind == "sub" or kind == "mul" or kind == "div" or kind == "pow")
  elseif type(a) == "string" or ak == "bytes" or ak == "list" or ak == "tuple" then
    supported = kind == "mul" and as_index(b) ~= nil
    if kind == "add" and not reflected then
      supported = type_name(a) == type_name(b)
    end
  elseif ak == "set" then
    supported = bk == "set"
      and (kind == "bitor" or kind == "bitand" or kind == "bitxor" or kind == "sub")
  end
  if not supported then return NOT_IMPLEMENTED end
  if reflected then return M.binop(I, kind, b, a, false) end
  return M.binop(I, kind, a, b, inplace)
end

--- Apply a binary operator, including user-defined methods.
function M.binop(I, kind, l, r, inplace)
  if kind == "mod" and (type(l) == "string" or kindof(l) == "bytes") then
    throw("TypeError", "diaspore does not support %-formatting; use f-strings")
  end
  if kind == "mul" then
    -- meter allocation bombs: "x" * n, [0] * n, ...
    local seq, n = l, r
    if as_index(l) ~= nil then
      seq, n = r, l
    end
    local count = as_index(n)
    if count ~= nil and count > 0 then
      local size = -1
      if type(seq) == "string" then
        size = #seq
      else
        local sk = kindof(seq)
        if sk == "bytes" then
          size = #seq.b
        elseif sk == "list" or sk == "tuple" then
          size = #seq.items
        end
      end
      if size >= 0 then I:fuel_n(size * count // 16 + 1) end
    end
  end
  local l_inst = is_inst(l)
  local r_inst = is_inst(r)
  if l_inst or r_inst then
    local names = BIN_DUNDER[kind]
    if names ~= nil then
      if inplace and l_inst then
        local iname = "__i" .. names[1]:sub(3)
        if class_lookup(l.cls, iname) ~= nil then
          local result = I:call_dunder(l, iname, { r })
          if result ~= NOT_IMPLEMENTED then return result end
          inplace = false
        end
      end
      local right = r_inst and class_lookup(r.cls, names[2]) or nil
      local same_type = l_inst and r_inst and l.cls == r.cls
      local right_first = strict_subtype(l, r) and right ~= nil
        and (not l_inst or right ~= class_lookup(l.cls, names[2]))
      if right_first then
        local result = I:call_dunder(r, names[2], { l })
        if result ~= NOT_IMPLEMENTED then return result end
      end
      local result = binary_slot(I, kind, l, r, names[1], false, inplace)
      if result ~= NOT_IMPLEMENTED then return result end
      if not right_first and not same_type then
        result = binary_slot(I, kind, r, l, names[2], true, false)
        if result ~= NOT_IMPLEMENTED then return result end
      end
    end
    binop_type_error(kind, l, r)
  end
  if is_set_view(l) or is_set_view(r) then
    local result = view_binop(I, kind, l, r)
    if result ~= NOT_IMPLEMENTED then return result end
  end
  if kind == "matmul" then
    binop_type_error(kind, l, r) -- dunder-only: no builtin type supports @
  end
  if kindof(l) == "complex" or kindof(r) == "complex" then
    return M.complex_binop(I, kind, l, r)
  end

  -- int op int (bool counts as int)
  local li = as_index(l)
  if li ~= nil then
    local ri = as_index(r)
    if ri ~= nil then return int_binop(I, kind, li, ri) end
  end
  -- numeric with a float involved
  if is_number(l) and is_number(r) then
    return float_binop(kind, as_float(l), as_float(r))
  end

  local lk = kindof(l)
  if kind == "add" then
    if type(l) == "string" then
      if type(r) == "string" then return l .. r end
      throw("TypeError", 'can only concatenate str (not "' .. type_name(r) .. '") to str')
    end
    if lk == "bytes" then
      if kindof(r) == "bytes" then return values.bytes(l.b .. r.b) end
      throw("TypeError", 'can only concatenate bytes (not "' .. type_name(r) .. '") to bytes')
    end
    if lk == "list" then
      if kindof(r) == "list" then
        if inplace then
          local li_ = l.items
          for _, x in ipairs(r.items) do li_[#li_ + 1] = x end
          return l
        end
        local items = {}
        for _, x in ipairs(l.items) do items[#items + 1] = x end
        for _, x in ipairs(r.items) do items[#items + 1] = x end
        return values.list(items)
      end
      if inplace then
        local saved = #I.stack
        local ok, items = pcall(iter.iter_to_array, I, r)
        if ok then
          local li_ = l.items
          for _, x in ipairs(items) do li_[#li_ + 1] = x end
          return l
        end
        I:trim(saved)
      end
      throw("TypeError", 'can only concatenate list (not "' .. type_name(r) .. '") to list')
    end
    if lk == "tuple" then
      if kindof(r) == "tuple" then
        local items = {}
        for _, x in ipairs(l.items) do items[#items + 1] = x end
        for _, x in ipairs(r.items) do items[#items + 1] = x end
        return values.tuple(items)
      end
      throw("TypeError", 'can only concatenate tuple (not "' .. type_name(r) .. '") to tuple')
    end
  elseif kind == "mul" then
    local res = seq_repeat(l, r, inplace)
    if res ~= nil then return res end
    res = seq_repeat(r, l, false)
    if res ~= nil then return res end
  elseif kind == "bitor" or kind == "bitand" or kind == "bitxor" or kind == "sub" then
    if lk == "set" and kindof(r) == "set" then return M.set_op(kind, l, r) end
  end
  binop_type_error(kind, l, r)
end

local UNARY_SYM = { neg = "-", pos = "+", invert = "~" }

--- Apply a unary operator, including user-defined methods.
function M.unary(I, kind, v)
  if is_inst(v) then
    if kind == "neg" and class_lookup(v.cls, "__neg__") ~= nil then
      return I:call_dunder(v, "__neg__", {})
    end
    throw("TypeError", "bad operand type for unary " .. UNARY_SYM[kind]
      .. ": '" .. v.cls.name .. "'")
  end
  local n = as_index(v)
  if kind == "neg" then
    if n ~= nil then return -n end
    if math.type(v) == "float" then return -v end
    if kindof(v) == "complex" then return values.complex(-v.re, -v.im) end
  elseif kind == "pos" then
    if n ~= nil then return n end
    if math.type(v) == "float" then return v end
    if kindof(v) == "complex" then return v end
  elseif kind == "invert" then
    if n ~= nil then return ~n end
  end
  throw("TypeError", "bad operand type for unary " .. UNARY_SYM[kind]
    .. ": '" .. type_name(v) .. "'")
end

-- ---------------------------------------------------------------------------
-- comparisons

local CMP_SYM = { lt = "<", le = "<=", gt = ">", ge = ">=" }
local CMP_DUNDER = {
  lt = "__lt__", le = "__le__", gt = "__gt__", ge = "__ge__",
  eq = "__eq__", ne = "__ne__",
}
local CMP_REFLECT = { lt = "gt", le = "ge", gt = "lt", ge = "le", eq = "eq", ne = "ne" }

local function compare_slot(I, obj, other, kind)
  if is_inst(obj) then
    local name = CMP_DUNDER[kind]
    if class_lookup(obj.cls, name) ~= nil then
      return I:call_dunder(obj, name, { other })
    end
    if kind == "ne" and class_lookup(obj.cls, "__eq__") ~= nil then
      local result = I:call_dunder(obj, "__eq__", { other })
      if result ~= NOT_IMPLEMENTED then return not I:truth(result) end
      return NOT_IMPLEMENTED
    end
  end
  local a, b = primitive(obj), primitive(other)
  if is_inst(a) or is_inst(b) then return NOT_IMPLEMENTED end
  local ak, bk = kindof(a), kindof(b)
  local equality = kind == "eq" or kind == "ne"
  local supported = is_number(a) and is_number(b)
    or type(a) == "string" and type(b) == "string"
    or ak ~= nil and ak == bk and (ak == "bytes" or ak == "list"
      or ak == "tuple" or ak == "set" or equality)
  if is_setlike(a) and is_setlike(b) then supported = true end
  if equality and (ak == "complex" or bk == "complex") then
    supported = (is_number(a) or ak == "complex") and (is_number(b) or bk == "complex")
  end
  if not supported then return NOT_IMPLEMENTED end
  return M.compare(I, kind, a, b)
end

local function rich_compare(I, kind, l, r)
  local right_first = strict_subtype(l, r)
  if right_first then
    local result = compare_slot(I, r, l, CMP_REFLECT[kind])
    if result ~= NOT_IMPLEMENTED then return result end
  end
  local result = compare_slot(I, l, r, kind)
  if result ~= NOT_IMPLEMENTED then return result end
  if not right_first then return compare_slot(I, r, l, CMP_REFLECT[kind]) end
  return NOT_IMPLEMENTED
end

local function order_type_error(kind, l, r)
  throw("TypeError", "'" .. CMP_SYM[kind] .. "' not supported between instances of '"
    .. type_name(l) .. "' and '" .. type_name(r) .. "'")
end

-- Orders two non-NaN numbers exactly (Lua mixed comparisons are exact).
local function num_cmp(l, r)
  local a = type(l) == "boolean" and (l and 1 or 0) or l
  local b = type(r) == "boolean" and (r and 1 or 0) or r
  if a < b then return -1 end
  if a > b then return 1 end
  return 0
end

--- Test sandbox object identity.
function M.is_identical(l, r)
  local t = type(l)
  if t == "number" then
    if type(r) ~= "number" or math.type(l) ~= math.type(r) then return false end
    if math.type(l) == "float" then
      if l ~= l then return r ~= r end
      if l == 0.0 and r == 0.0 then return (1 / l > 0) == (1 / r > 0) end
    end
    return l == r
  end
  if t == "boolean" then return l == r end
  if t == "string" then return type(r) == "string" and l == r end
  local k = l.kind
  if k == "none" then return kindof(r) == "none" end
  if k == "ellipsis" then return kindof(r) == "ellipsis" end
  if k == "bytes" then
    return kindof(r) == "bytes" and (l == r or (#l.b == 0 and #r.b == 0))
  end
  if k == "tuple" then
    -- CPython interns the empty tuple, so `() is ()` is True.
    return kindof(r) == "tuple" and (l == r or (#l.items == 0 and #r.items == 0))
  end
  return l == r
end

local cmp3 -- forward

local function cmp_seq(I, a, b)
  local n = math.min(#a, #b)
  for i = 1, n do
    if not M.eq_elem(I, a[i], b[i]) then
      local lt = I:truth(M.compare(I, "lt", a[i], b[i]))
      return lt and -1 or 1, true
    end
  end
  if #a < #b then return -1, true end
  if #a > #b then return 1, true end
  return 0, true
end

cmp3 = function(I, l, r, kind)
  if is_number(l) and is_number(r) then
    if l ~= l or r ~= r then return 0, false end -- NaN
    return num_cmp(l, r), true
  end
  if type(l) == "string" and type(r) == "string" then
    return values.str_cmp(l, r), true
  end
  local lk, rk = kindof(l), kindof(r)
  if lk == "bytes" and rk == "bytes" then
    if l.b < r.b then return -1, true end
    if l.b > r.b then return 1, true end
    return 0, true
  end
  if lk == "list" and rk == "list" then return cmp_seq(I, l.items, r.items) end
  if lk == "tuple" and rk == "tuple" then return cmp_seq(I, l.items, r.items) end
  if is_setlike(l) and is_setlike(r) then
    local sub = setlike_subset(I, l, r)
    local sup = setlike_subset(I, r, l)
    if sub and sup then return 0, true end
    if sub then return -1, true end
    if sup then return 1, true end
    return 0, false -- incomparable sets: every ordering is False
  end
  order_type_error(kind, l, r)
end

--- Apply a comparison or membership operator.
function M.compare(I, kind, l, r)
  if kind == "is" then return M.is_identical(l, r) end
  if kind == "isnot" then return not M.is_identical(l, r) end
  if kind == "in" then return M.contains(I, r, l) end
  if kind == "notin" then return not M.contains(I, r, l) end
  if is_inst(l) or is_inst(r) then
    local result = rich_compare(I, kind, l, r)
    if result ~= NOT_IMPLEMENTED then return result end
    if kind == "eq" then return M.is_identical(l, r) end
    if kind == "ne" then return not M.is_identical(l, r) end
    order_type_error(kind, l, r)
  end
  if kind == "eq" then return M.eq(I, l, r) end
  if kind == "ne" then return not M.eq(I, l, r) end
  local c, ordered = cmp3(I, l, r, kind)
  if not ordered then return false end
  if kind == "lt" then return c < 0 end
  if kind == "le" then return c <= 0 end
  if kind == "gt" then return c > 0 end
  if kind == "ge" then return c >= 0 end
  throw("TypeError", "unknown comparison " .. kind)
end

local function eq_seq(I, a, b)
  if #a ~= #b then return false end
  for i = 1, #a do
    if not M.eq_elem(I, a[i], b[i]) then return false end
  end
  return true
end

--- Compare sandbox values for equality.
function M.eq(I, l, r)
  if is_inst(l) or is_inst(r) then return I:truth(M.compare(I, "eq", l, r)) end
  if kindof(l) == "mappingproxy" then l = values.namespace_dict(l.cls) end
  if kindof(r) == "mappingproxy" then r = values.namespace_dict(r.cls) end
  if is_set_view(l) or is_set_view(r) then
    if not is_setlike(l) or not is_setlike(r) then return false end
    return M.diaspore_len(I, l) == M.diaspore_len(I, r) and setlike_subset(I, l, r)
  end
  if kindof(l) == "complex" or kindof(r) == "complex" then
    if is_number(l) or is_number(r) or kindof(l) == "complex" or kindof(r) == "complex" then
      if (is_number(l) or kindof(l) == "complex")
          and (is_number(r) or kindof(r) == "complex") then
        return M.complex_eq(l, r)
      end
    end
    return false
  end
  if is_number(l) and is_number(r) then
    if l ~= l or r ~= r then return false end
    return num_cmp(l, r) == 0
  end
  if type(l) == "string" then return type(r) == "string" and l == r end
  local lk = kindof(l)
  if lk == "bytes" then return kindof(r) == "bytes" and l.b == r.b end
  if lk == "none" then return kindof(r) == "none" end
  if lk == "list" then
    if kindof(r) == "list" then return eq_seq(I, l.items, r.items) end
    return false
  end
  if lk == "tuple" then
    if kindof(r) == "tuple" then return eq_seq(I, l.items, r.items) end
    return false
  end
  if lk == "dict" then
    if kindof(r) ~= "dict" or l.size ~= r.size then return false end
    for hk, e in pairs(l.map) do
      local be = values.dict_entry_hk(r, e.hk or hk, e.k)
      if be == nil then return false end
      if not M.eq_elem(I, e.v, be.v) then return false end
    end
    return true
  end
  if lk == "set" then
    if kindof(r) ~= "set" or l.size ~= r.size then return false end
    return M.set_is_subset(l, r)
  end
  if lk == "range" then
    if kindof(r) ~= "range" then return false end
    local la = values.range_len(l)
    if la ~= values.range_len(r) then return false end
    if la == 0 then return true end
    if l.start ~= r.start then return false end
    return la == 1 or l.step == r.step
  end
  return M.is_identical(l, r)
end

--- Element equality inside containers (CPython's RichCompareBool: identity
-- implies equality - [nan] == [nan] while nan == nan stays False).
function M.eq_elem(I, a, b)
  if M.is_identical(a, b) then return true end
  if math.type(a) == "float" and math.type(b) == "float" and a ~= a and b ~= b then
    return true
  end
  return M.eq(I, a, b)
end

local function iter_contains(I, container, item)
  local it = iter.get_iter(I, container)
  while true do
    local x, ok = it.next()
    if not ok then return false end
    if M.eq_elem(I, x, item) then return true end
  end
end

--- Test membership using the sandbox containment protocol.
function M.contains(I, container, item)
  local ck = kindof(container)
  if ck == "dictview" then
    local d = values.view_dict(container)
    if container.view == "keys" then return values.dict_has(d, item) end
    if container.view == "items" then
      if kindof(item) ~= "tuple" or #item.items ~= 2 then return false end
      local entry = values.dict_entry(d, item.items[1])
      return entry ~= nil and M.eq_elem(I, entry.v, item.items[2])
    end
    return iter_contains(I, container, item)
  end
  if ck == "instance" then
    if class_lookup(container.cls, "__contains__") == nil and container.prim ~= nil then
      return M.contains(I, container.prim, item)
    end
    if class_lookup(container.cls, "__contains__") ~= nil then
      return I:truth(I:call_dunder(container, "__contains__", { item }))
    end
    return iter_contains(I, container, item)
  end
  if type(container) == "string" then
    if type(item) ~= "string" then
      throw("TypeError", "'in <string>' requires string as left operand, not "
        .. type_name(item))
    end
    return container:find(item, 1, true) ~= nil
  end
  if ck == "bytes" then
    if kindof(item) == "bytes" then
      return container.b:find(item.b, 1, true) ~= nil or #item.b == 0
    end
    local n = as_index(item)
    if n ~= nil then
      if n < 0 or n > 255 then throw("ValueError", "byte must be in range(0, 256)") end
      return container.b:find(string.char(n), 1, true) ~= nil
    end
    throw("TypeError", "a bytes-like object is required, not '" .. type_name(item) .. "'")
  end
  if ck == "list" or ck == "tuple" then
    for _, x in ipairs(container.items) do
      if M.eq_elem(I, x, item) then return true end
    end
    return false
  end
  if ck == "dict" then return values.dict_has(container, item) end
  if ck == "mappingproxy" then
    hash_key(item)
    return type(item) == "string" and container.cls.ns[item] ~= nil
  end
  if ck == "set" then return values.set_has(container, item) end
  if ck == "range" then
    local n = as_index(item)
    if n == nil then
      if math.type(item) == "float" then
        n = math.tointeger(item)
        if n == nil then return false end
      else
        return false
      end
    end
    local r = container
    if r.step > 0 then
      return n >= r.start and n < r.stop and (n - r.start) % r.step == 0
    end
    return n <= r.start and n > r.stop and (r.start - n) % -r.step == 0
  end
  if ck == "iter" then return iter_contains(I, container, item) end
  throw("TypeError", "argument of type '" .. type_name(container) .. "' is not iterable")
end

-- ---------------------------------------------------------------------------
-- truthiness & length

--- Evaluate sandbox truthiness.
function M.truth(I, v)
  local t = type(v)
  if t == "boolean" then return v end
  if t == "number" then return v ~= 0 end -- NaN is truthy (nan ~= 0)
  if t == "string" then return v ~= "" end
  local k = v.kind
  if k == "none" then return false end
  if k == "bytes" then return #v.b > 0 end
  if k == "list" or k == "tuple" then return #v.items > 0 end
  if k == "dict" or k == "set" then return v.size > 0 end
  if k == "dictview" then return M.diaspore_len(I, v.source) > 0 end
  if k == "mappingproxy" then return next(v.cls.ns) ~= nil end
  if k == "range" then return values.range_len(v) > 0 end
  if k == "instance" then
    if class_lookup(v.cls, "__bool__") == nil and class_lookup(v.cls, "__len__") == nil
        and v.prim ~= nil then
      return M.truth(I, v.prim)
    end
    if class_lookup(v.cls, "__bool__") ~= nil then
      local r = I:call_dunder(v, "__bool__", {})
      if type(r) ~= "boolean" then
        throw("TypeError", "__bool__ should return bool, returned " .. type_name(r))
      end
      return r
    end
    if class_lookup(v.cls, "__len__") ~= nil then
      return M.diaspore_len(I, v) ~= 0
    end
    return true
  end
  if k == "complex" then return v.re ~= 0.0 or v.im ~= 0.0 end
  return true
end

--- Return a sandbox value length.
function M.diaspore_len(I, v)
  if type(v) == "string" then return values.str_len(v) end
  local k = kindof(v)
  if k == "bytes" then return #v.b end
  if k == "list" or k == "tuple" then return #v.items end
  if k == "dict" or k == "set" then return v.size end
  if k == "dictview" then return M.diaspore_len(I, v.source) end
  if k == "mappingproxy" then
    local count = 0
    for _ in pairs(v.cls.ns) do count = count + 1 end
    return count
  end
  if k == "range" then return values.range_len(v) end
  if k == "instance" then
    if class_lookup(v.cls, "__len__") == nil and v.prim ~= nil then
      return M.diaspore_len(I, v.prim)
    end
    local r = I:call_dunder_or_err(v, "__len__", {},
      "object of type '" .. v.cls.name .. "' has no len()")
    if math.type(r) == "integer" and type(r) ~= "boolean" then return r end
    throw("TypeError", "__len__ should return an int")
  end
  throw("TypeError", "object of type '" .. type_name(v) .. "' has no len()")
end

-- ---------------------------------------------------------------------------
-- indexing & slicing

local function seq_index_check(n, idx, kind_msg)
  local i = idx
  if i < 0 then i = i + n end
  if i < 0 or i >= n then throw("IndexError", kind_msg) end
  return i
end

--- Resolves a slice against a sequence of length n, CPython-style.
function M.slice_indices(sl, n)
  local function get_int(v)
    if kindof(v) == "none" then return nil end
    local i = as_index(v)
    if i == nil then
      throw("TypeError",
        "slice indices must be integers or None or have an __index__ method")
    end
    return i
  end
  local step_v = get_int(sl.step)
  local step = 1
  if step_v ~= nil then
    if step_v == 0 then throw("ValueError", "slice step cannot be zero") end
    step = step_v
  end
  local function clamp(v)
    if v < 0 then
      v = v + n
      if v < 0 then return step < 0 and -1 or 0 end
    elseif v >= n then
      return step < 0 and n - 1 or n
    end
    return v
  end
  local low_v = get_int(sl.lower)
  local up_v = get_int(sl.upper)
  local start, stop
  if step > 0 then
    start, stop = 0, n
  else
    start, stop = n - 1, -1
  end
  if low_v ~= nil then start = clamp(low_v) end
  if up_v ~= nil then stop = clamp(up_v) end
  return start, stop, step
end

--- Visit zero-based indices in a normalized slice.
function M.for_slice(start, stop, step, fn)
  if step > 0 then
    for i = start, stop - 1, step do fn(i) end
  else
    for i = start, stop + 1, step do fn(i) end
  end
end

--- Copy the selected slice of a Lua array.
function M.slice_seq(items, start, stop, step)
  local out = {}
  M.for_slice(start, stop, step, function(i)
    out[#out + 1] = items[i + 1]
  end)
  return out
end

local function slice_range(r, sl)
  local n = values.range_len(r)
  local start, stop, step = M.slice_indices(sl, n)
  local count = 0
  M.for_slice(start, stop, step, function() count = count + 1 end)
  local new_start = r.start + start * r.step
  local new_step = r.step * step
  local new_stop = new_start + count * new_step
  return values.range(new_start, new_stop, new_step)
end

--- Read an indexed item or slice.
function M.get_index(I, o, k)
  local ok_ = kindof(o)
  if ok_ == "mappingproxy" then
    hash_key(k)
    local v
    if type(k) == "string" then v = o.cls.ns[k] end
    if v ~= nil then return v end
    values.throw_val(values.make_exc("KeyError", k))
  end
  if ok_ == "instance" then
    if class_lookup(o.cls, "__getitem__") == nil and o.prim ~= nil then
      if kindof(o.prim) == "dict" and not values.dict_has(o.prim, k)
          and class_lookup(o.cls, "__missing__") ~= nil then
        return I:call_dunder(o, "__missing__", { k })
      end
      return M.get_index(I, o.prim, k)
    end
    return I:call_dunder_or_err(o, "__getitem__", { k },
      "'" .. o.cls.name .. "' object is not subscriptable")
  end
  if ok_ == "list" or ok_ == "tuple" then
    if kindof(k) == "slice" then
      local start, stop, step = M.slice_indices(k, #o.items)
      local sliced = M.slice_seq(o.items, start, stop, step)
      if ok_ == "list" then return values.list(sliced) end
      return values.tuple(sliced)
    end
    local n = as_index(k)
    if n == nil then
      throw("TypeError", ok_ .. " indices must be integers or slices, not " .. type_name(k))
    end
    return o.items[seq_index_check(#o.items, n, ok_ .. " index out of range") + 1]
  end
  if type(o) == "string" then
    if kindof(k) == "slice" then
      if values.is_ascii(o) then
        local start, stop, step = M.slice_indices(k, #o)
        if step == 1 then return o:sub(start + 1, stop) end
        local b = {}
        M.for_slice(start, stop, step, function(i)
          b[#b + 1] = o:sub(i + 1, i + 1)
        end)
        return table.concat(b)
      end
      local rs = values.chars(o)
      local start, stop, step = M.slice_indices(k, #rs)
      local b = {}
      M.for_slice(start, stop, step, function(i)
        b[#b + 1] = rs[i + 1]
      end)
      return table.concat(b)
    end
    local n = as_index(k)
    if n == nil then
      throw("TypeError", "string indices must be integers, not '" .. type_name(k) .. "'")
    end
    if values.is_ascii(o) then
      local i = seq_index_check(#o, n, "string index out of range")
      return o:sub(i + 1, i + 1)
    end
    local rs = values.chars(o)
    local i = seq_index_check(#rs, n, "string index out of range")
    return rs[i + 1]
  end
  if ok_ == "bytes" then
    local data = o.b
    if kindof(k) == "slice" then
      local start, stop, step = M.slice_indices(k, #data)
      if step == 1 then return values.bytes(data:sub(start + 1, stop)) end
      local b = {}
      M.for_slice(start, stop, step, function(i)
        b[#b + 1] = data:sub(i + 1, i + 1)
      end)
      return values.bytes(table.concat(b))
    end
    local n = as_index(k)
    if n == nil then
      throw("TypeError", "byte indices must be integers or slices, not " .. type_name(k))
    end
    return data:byte(seq_index_check(#data, n, "index out of range") + 1)
  end
  if ok_ == "dict" then
    if kindof(k) == "slice" then throw("TypeError", "unhashable type: 'slice'") end
    local e = values.dict_entry(o, k)
    if e == nil then
      values.throw_val(values.make_exc("KeyError", k))
    end
    return e.v
  end
  if ok_ == "range" then
    if kindof(k) == "slice" then return slice_range(o, k) end
    local n = as_index(k)
    if n == nil then
      throw("TypeError", "range indices must be integers or slices, not " .. type_name(k))
    end
    local i = seq_index_check(values.range_len(o), n, "range object index out of range")
    return o.start + i * o.step
  end
  throw("TypeError", "'" .. type_name(o) .. "' object is not subscriptable")
end

local function set_list_slice(I, l, sl, v)
  local items = iter.iter_to_array(I, v)
  local start, stop, step = M.slice_indices(sl, #l.items)
  if step == 1 then
    if stop < start then stop = start end
    local out = {}
    for i = 1, start do out[#out + 1] = l.items[i] end
    for _, x in ipairs(items) do out[#out + 1] = x end
    for i = stop + 1, #l.items do out[#out + 1] = l.items[i] end
    l.items = out
    return
  end
  local idxs = {}
  M.for_slice(start, stop, step, function(i) idxs[#idxs + 1] = i end)
  if #idxs ~= #items then
    throw("ValueError", "attempt to assign sequence of size " .. #items
      .. " to extended slice of size " .. #idxs)
  end
  for j = 1, #idxs do l.items[idxs[j] + 1] = items[j] end
end

--- Assign an indexed item or slice.
function M.set_index(I, o, k, v)
  local ok_ = kindof(o)
  if ok_ == "instance" then
    if class_lookup(o.cls, "__setitem__") == nil and o.prim ~= nil then
      M.set_index(I, o.prim, k, v)
      return
    end
    I:call_dunder_or_err(o, "__setitem__", { k, v },
      "'" .. o.cls.name .. "' object does not support item assignment")
    return
  end
  if ok_ == "list" then
    if kindof(k) == "slice" then
      set_list_slice(I, o, k, v)
      return
    end
    local n = as_index(k)
    if n == nil then
      throw("TypeError", "list indices must be integers or slices, not " .. type_name(k))
    end
    o.items[seq_index_check(#o.items, n, "list assignment index out of range") + 1] = v
    return
  end
  if ok_ == "dict" then
    if kindof(k) == "slice" then throw("TypeError", "unhashable type: 'slice'") end
    values.dict_set(o, k, v)
    return
  end
  throw("TypeError", "'" .. type_name(o) .. "' object does not support item assignment")
end

--- Delete an indexed item or slice.
function M.del_index(I, o, k)
  local ok_ = kindof(o)
  if ok_ == "instance" then
    if class_lookup(o.cls, "__delitem__") == nil and o.prim ~= nil then
      M.del_index(I, o.prim, k)
      return
    end
    I:call_dunder_or_err(o, "__delitem__", { k },
      "'" .. o.cls.name .. "' object doesn't support item deletion")
    return
  end
  if ok_ == "list" then
    if kindof(k) == "slice" then
      local start, stop, step = M.slice_indices(k, #o.items)
      local kill = {}
      M.for_slice(start, stop, step, function(i) kill[i + 1] = true end)
      local out = {}
      for i = 1, #o.items do
        if not kill[i] then out[#out + 1] = o.items[i] end
      end
      o.items = out
      return
    end
    local n = as_index(k)
    if n == nil then
      throw("TypeError", "list indices must be integers or slices, not " .. type_name(k))
    end
    table.remove(o.items, seq_index_check(#o.items, n, "list assignment index out of range") + 1)
    return
  end
  if ok_ == "dict" then
    if values.dict_del(o, k) == nil then
      values.throw_val(values.make_exc("KeyError", k))
    end
    return
  end
  throw("TypeError", "'" .. type_name(o) .. "' object doesn't support item deletion")
end

-- ---------------------------------------------------------------------------
-- repr / str

local function repr_join(I, items)
  local parts = {}
  for i = 1, #items do parts[i] = M.repr(I, items[i]) end
  return table.concat(parts, ", ")
end

--- Render a value using the sandbox repr protocol.
function M.repr(I, v)
  local t = type(v)
  if t == "number" then
    if math.type(v) == "integer" then return tostring(v) end
    return format.py_float_str(v)
  end
  if t == "boolean" then return v and "True" or "False" end
  if t == "string" then return format.repr_string(v) end
  local k = v.kind
  if k == "dictview" then
    local name = type_name(v)
    if I.repring[v] then return name .. "([...])" end
    I.repring[v] = true
    local ok, text = pcall(function()
      return name .. "(" .. M.repr(I, values.list(iter.iter_to_array(I, v))) .. ")"
    end)
    I.repring[v] = nil
    if not ok then error(text, 0) end
    return text
  end
  if k == "list" or k == "tuple" or k == "dict" then
    if I.repring[v] then
      if k == "list" then return "[...]" end
      if k == "tuple" then return "(...)" end
      return "{...}"
    end
    I.repring[v] = true
    local out
    local ok, err = pcall(function()
      if k == "list" then
        out = "[" .. repr_join(I, v.items) .. "]"
      elseif k == "tuple" then
        if #v.items == 1 then
          out = "(" .. M.repr(I, v.items[1]) .. ",)"
        else
          out = "(" .. repr_join(I, v.items) .. ")"
        end
      else
        local parts = {}
        local entries = values.dict_entries(v)
        for i = 1, #entries do
          parts[i] = M.repr(I, entries[i].k) .. ": " .. M.repr(I, entries[i].v)
        end
        out = "{" .. table.concat(parts, ", ") .. "}"
      end
    end)
    I.repring[v] = nil
    if not ok then error(err, 0) end
    return out
  end
  if k == "none" then return "None" end
  if k == "ellipsis" then return "Ellipsis" end
  if k == "notimplemented" then return "NotImplemented" end
  if k == "mappingproxy" then
    return "mappingproxy(" .. M.repr(I, values.namespace_dict(v.cls)) .. ")"
  end
  if k == "bytes" then return format.repr_bytes(v) end
  if k == "range" then return format.repr_range(v) end
  if k == "set" then
    if v.size == 0 then return v.frozen and "frozenset()" or "set()" end
    local text = "{" .. repr_join(I, values.set_elems(v)) .. "}"
    return v.frozen and "frozenset(" .. text .. ")" or text
  end
  if k == "slice" then
    return "slice(" .. M.repr(I, v.lower) .. ", " .. M.repr(I, v.upper)
      .. ", " .. M.repr(I, v.step) .. ")"
  end
  if k == "complex" then return M.complex_repr(v) end
  if k == "instance" then
    if values.issub(v.cls, values.BASE_EXC)
        and class_lookup(v.cls, "__repr__") == nil then
      return v.cls.name .. "(" .. repr_join(I, values.exc_args(v)) .. ")"
    end
    if class_lookup(v.cls, "__repr__") == nil and v.prim ~= nil then
      return M.repr(I, v.prim) -- builtin subclass uses builtin repr
    end
    if class_lookup(v.cls, "__repr__") ~= nil then
      local r = I:call_dunder(v, "__repr__", {})
      if type(r) ~= "string" then
        throw("TypeError", "__repr__ returned non-string")
      end
      return r
    end
    return "<" .. v.cls.name .. " object>"
  end
  if k == "class" or k == "btype" then return "<class '" .. v.name .. "'>" end
  if k == "function" then return "<function " .. v.name .. ">" end
  if k == "boundmethod" then return "<bound method " .. M.callable_name(v.fn) .. ">" end
  if k == "module" or k == "nativemodule" then return "<module '" .. v.name .. "'>" end
  if k == "file" then return "<file>" end
  if k == "nativeobject" then return "<" .. v.nkind .. ">" end
  if k == "builtin" then return "<builtin " .. v.name .. ">" end
  if k == "iter" and v.iname == "generator" then
    return "<generator " .. v.fname .. ">"
  end
  return "<" .. type_name(v) .. ">"
end

--- Return a display name for a callable.
function M.callable_name(f)
  if type(f) == "table" then
    local k = f.kind
    if k == "function" or k == "builtin" or k == "class" or k == "btype" then
      return f.name
    end
  end
  return "function"
end

--- str(e) for an exception instance without a user __str__.
function M.exc_str(I, inst)
  if class_lookup(inst.cls, "__str__") ~= nil then return M.str(I, inst) end
  local args = values.exc_args(inst)
  if values.issub(inst.cls, values.EXC_CLASSES.KeyError) and #args == 1 then
    return M.repr(I, args[1])
  end
  local encode = values.issub(inst.cls, values.EXC_CLASSES.UnicodeEncodeError)
  local decode = values.issub(inst.cls, values.EXC_CLASSES.UnicodeDecodeError)
  if #args == 5 and (encode or decode) then
    local encoding = inst.d.encoding or args[1]
    local object = inst.d.object or args[2]
    local start = inst.d.start or args[3]
    local stop = inst.d["end"] or args[4]
    local reason = inst.d.reason or args[5]
    local part
    if encode then
      if stop == start + 1 then
        local cp = utf8.codepoint(object, utf8.offset(object, start + 1), nil, true)
        local escape = cp <= 0xff and string.format("\\x%02x", cp)
          or (cp <= 0xffff and string.format("\\u%04x", cp) or string.format("\\U%08x", cp))
        part = "character '" .. escape .. "' in position " .. start
      else
        part = "characters in position " .. start .. "-" .. (stop - 1)
      end
    elseif stop == start + 1 then
      part = string.format("byte 0x%02x in position %d", object.b:byte(start + 1), start)
    else
      part = "bytes in position " .. start .. "-" .. (stop - 1)
    end
    return "'" .. encoding .. "' codec can't " .. (encode and "encode " or "decode ")
      .. part .. ": " .. reason
  end
  if #args == 0 then return "" end
  if #args == 1 then return M.str(I, args[1]) end
  return M.repr(I, values.tuple(args))
end

--- Render a value using the sandbox string protocol.
function M.str(I, v)
  if type(v) == "string" then return v end
  if is_inst(v) then
    if values.issub(v.cls, values.BASE_EXC)
        and class_lookup(v.cls, "__str__") == nil then
      return M.exc_str(I, v)
    end
    for _, name in ipairs({ "__str__", "__repr__" }) do
      if class_lookup(v.cls, name) ~= nil then
        local r = I:call_dunder(v, name, {})
        if type(r) ~= "string" then
          throw("TypeError", name .. " returned non-string (" .. type_name(r) .. ")")
        end
        return r
      end
    end
    if v.prim ~= nil then return M.str(I, v.prim) end
    return "<" .. v.cls.name .. " object>"
  end
  return M.repr(I, v)
end

--- ascii(v): repr with non-ASCII backslash-escaped (CPython escape rules).
function M.ascii(I, v)
  local r = M.repr(I, v)
  local out = {}
  for _, cp in utf8.codes(r, true) do
    if cp < 0x80 then
      out[#out + 1] = string.char(cp)
    elseif cp <= 0xFF then
      out[#out + 1] = string.format("\\x%02x", cp)
    elseif cp <= 0xFFFF then
      out[#out + 1] = string.format("\\u%04x", cp)
    else
      out[#out + 1] = string.format("\\U%08x", cp)
    end
  end
  return table.concat(out)
end

-- ---------------------------------------------------------------------------
-- sorting (stable merge sort; comparison order matches diaspore-go/diaspore-ts so
-- error messages from mixed-type sorts are identical)

local function stable_sort(items, less)
  local n = #items
  if n < 2 then return end
  local buf = {}
  local function rec(lo, hi) -- [lo, hi) 0-based half-open, like the TS VM
    if hi - lo < 2 then return end
    local mid = (lo + hi) // 2
    rec(lo, mid)
    rec(mid, hi)
    local i, j, k = lo, mid, lo
    while i < mid and j < hi do
      if less(items[j + 1], items[i + 1]) then
        buf[k + 1] = items[j + 1]
        j = j + 1
      else
        buf[k + 1] = items[i + 1]
        i = i + 1
      end
      k = k + 1
    end
    while i < mid do
      buf[k + 1] = items[i + 1]
      i = i + 1
      k = k + 1
    end
    while j < hi do
      buf[k + 1] = items[j + 1]
      j = j + 1
      k = k + 1
    end
    for t = lo, hi - 1 do items[t + 1] = buf[t + 1] end
  end
  rec(0, n)
end

local function reverse_in_place(items)
  local n = #items
  for i = 1, n // 2 do
    items[i], items[n + 1 - i] = items[n + 1 - i], items[i]
  end
end

--- Sorts items in place, stably, with optional key and reverse (Python
-- semantics: reverse is applied around the stable sort).
function M.sort_values(I, items, key, reverse)
  local use_key = key ~= nil and kindof(key) ~= "none"
  local function less(a, b)
    return I:truth(M.compare(I, "lt", a, b))
  end
  if reverse then reverse_in_place(items) end
  if use_key then
    local dec = {}
    for i = 1, #items do
      dec[i] = { k = I:call(key, { items[i] }, {}), v = items[i] }
    end
    stable_sort(dec, function(a, b) return less(a.k, b.k) end)
    for i = 1, #items do items[i] = dec[i].v end
  else
    stable_sort(items, less)
  end
  if reverse then reverse_in_place(items) end
end

return M
