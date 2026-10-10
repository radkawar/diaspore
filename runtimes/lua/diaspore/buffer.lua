-- Mutable byte storage and live, strided buffer views.
local V = require("diaspore.values")
local bigint = require("diaspore.bigint")
local H
local M = {}
local formats = { B = "I1", b = "i1", c = "c1", ["?"] = "I1",
  h = "h", H = "H", i = "i", I = "I", l = "l", L = "L",
  q = "i8", Q = "I8", n = "j", N = "J", f = "f", d = "d" }
local integer_bounds = {}
for fmt, packing in pairs(formats) do
  if fmt:match("^[bBhHiIlLqQnN]$") then
    local unsigned = fmt:match("^[BHILQN]$") ~= nil
    local bits = string.packsize(packing) * 8
    local upper = bigint.lshift(1, unsigned and bits or bits - 1)
    integer_bounds[fmt] = { unsigned and 0 or bigint.neg(upper), bigint.sub(upper, 1), upper }
  end
end

local function kind(v) return type(v) == "table" and v.kind or nil end

-- Appends retain immutable chunks until a string consumer requests .b. Charge
-- each chunk once when enqueued, including the flat prefix after a prior read.
local bytearray_mt = {
  __index = function(v, key)
    if key ~= "b" then return nil end
    local chunks = rawget(v, "_chunks")
    if chunks ~= nil then
      rawset(v, "_data", table.concat(chunks))
      rawset(v, "_chunks", nil)
    end
    return rawget(v, "_data")
  end,
  __newindex = function(v, key, value)
    if key == "b" then
      rawset(v, "_data", value)
      rawset(v, "_chunks", nil)
      rawset(v, "_size", #value)
    else
      rawset(v, key, value)
    end
  end,
}
function M.is_buffer(v)
  local k = kind(v)
  return k == "bytes" or k == "bytearray" or k == "memoryview"
end
function M.check(v)
  if kind(v) == "memoryview" and v.released then
    V.throw("ValueError", "operation forbidden on released memoryview object")
  end
end
function M.length(v)
  M.check(v)
  local k = kind(v)
  if k == "memoryview" then return v.length end
  if k == "bytearray" then return v._size end
  return #v.b
end
function M.bytearray(s)
  return setmetatable({ kind = "bytearray", _data = s, _size = #s,
    exports = setmetatable({}, { __mode = "k" }) }, bytearray_mt)
end
local function view(base, offset, length, stride, fmt, readonly)
  local result = { kind = "memoryview", base = base, offset = offset,
    length = length, stride = stride, format = fmt, readonly = readonly,
    itemsize = string.packsize(formats[fmt]) }
  if base.kind == "bytearray" then base.exports[result] = true end
  return result
end
function M.memoryview(v)
  if not M.is_buffer(v) then
    V.throw("TypeError", "memoryview: a bytes-like object is required, not '" .. V.type_name(v) .. "'")
  end
  M.check(v)
  if v.kind == "memoryview" then
    return view(v.base, v.offset, v.length, v.stride, v.format, v.readonly)
  end
  return view(v, 0, M.length(v), 1, "B", v.kind == "bytes")
end
function M.data(v)
  M.check(v)
  if not M.is_buffer(v) then
    V.throw("TypeError", "a bytes-like object is required, not '" .. V.type_name(v) .. "'")
  end
  if v.kind ~= "memoryview" then return v.b end
  if v.stride == v.itemsize then
    return v.base.b:sub(v.offset + 1, v.offset + v.length * v.itemsize)
  end
  local parts = {}
  for i = 0, v.length - 1 do
    local start = v.offset + i * v.stride
    parts[#parts + 1] = v.base.b:sub(start + 1, start + v.itemsize)
  end
  return table.concat(parts)
end
local function index(v, key)
  local i = V.as_index(key)
  if i == nil then V.throw("TypeError", "buffer indices must be integers or slices") end
  if bigint.is_bigint(i) then V.throw("IndexError", "cannot fit 'int' into an index-sized integer") end
  local n = M.length(v)
  if i < 0 then i = i + n end
  if i < 0 or i >= n then V.throw("IndexError", "index out of bounds") end
  return i
end
function M.get(v, key, ops)
  M.check(v)
  if kind(key) == "slice" then
    local start, stop, step = ops.slice_indices(key, M.length(v))
    step = V.clamp_index(step, -math.maxinteger, math.maxinteger)
    local length = 0
    if step > 0 and stop > start then length = (stop - start - 1) // step + 1 end
    if step < 0 and stop < start then length = (start - stop - 1) // -step + 1 end
    if v.kind == "memoryview" then
      -- CPython stores sliced strides in a signed host-sized field.
      return view(v.base, v.offset + start * v.stride, length, v.stride * step, v.format, v.readonly)
    end
    local parts = {}
    ops.for_slice(start, stop, step, function(i) parts[#parts + 1] = v.b:sub(i + 1, i + 1) end)
    return M.bytearray(table.concat(parts))
  end
  local i = index(v, key)
  if v.kind ~= "memoryview" then return v.b:byte(i + 1) end
  local result = string.unpack(formats[v.format], v.base.b, v.offset + i * v.stride + 1)
  if v.format == "?" then return result ~= 0 end
  if v.format == "c" then return V.bytes(result) end
  if (v.format == "Q" or v.format == "N" or v.format == "L") and result < 0 then
    return bigint.add(result, bigint.lshift(1, 64))
  end
  return result
end
function M.resize(v, size, borrowed_root)
  if size ~= M.length(v) and (borrowed_root == v or next(v.exports) ~= nil) then
    V.throw("BufferError", "Existing exports of data: object cannot be re-sized")
  end
end

--- Append immutable byte data without copying the existing prefix.
-- String reads materialize queued chunks once; exports still prohibit resizing.
function M.append(I, v, data)
  local size, added = v._size, #data
  if added > math.maxinteger - size then V.throw("OverflowError", "bytearray is too large") end
  M.resize(v, size + added)
  if added == 0 then return v end
  local chunks = v._chunks
  local charged = added
  if chunks == nil then charged = charged + #v._data end
  I:fuel_n(charged // 16 + 1)
  if chunks == nil then
    chunks = {}
    if #v._data > 0 then chunks[1] = v._data end
    v._chunks, v._data = chunks, nil
  end
  chunks[#chunks + 1] = data
  v._size = size + added
  return v
end

--- Concatenate bytes-like operands, honoring exports for in-place mutation.
function M.concat(I, v, source, inplace)
  if kind(source) == "memoryview"
      and (source.released or source.length > 1 and source.stride ~= source.itemsize) then
    V.throw("TypeError", "can't concat memoryview to bytearray")
  end
  local added = M.length(source)
  local root = source
  if kind(source) == "memoryview" then
    added = added * source.itemsize
    root = source.base
  end
  if added > math.maxinteger - v._size then V.throw("OverflowError", "bytearray is too large") end
  local size = v._size + added
  if inplace then M.resize(v, size, root) end
  local data = M.data(source)
  if inplace then return M.append(I, v, data) end
  I:fuel_n(size // 16 + 1)
  return M.bytearray(v.b .. data)
end
local function byte(value)
  local n = V.as_index(value)
  if n == nil then V.throw("TypeError", "an integer is required") end
  if bigint.is_bigint(n) or n < 0 or n > 255 then V.throw("ValueError", "byte must be in range(0, 256)") end
  return string.char(n)
end
function M.sequence(I, value)
  if M.is_buffer(value) then return M.data(value) end
  local parts = {}
  for _, item in ipairs(require("diaspore.iter").iter_to_array(I, value)) do
    parts[#parts + 1] = byte(item)
  end
  return table.concat(parts)
end
function M.set(I, v, key, value, ops)
  M.check(v)
  local isview = v.kind == "memoryview"
  if isview and v.readonly then V.throw("TypeError", "cannot modify read-only memory") end
  local base = isview and v.base or v
  if kind(key) == "slice" then
    local start, stop, step = ops.slice_indices(key, M.length(v))
    if step == 1 and stop < start then stop = start end
    local indices = {}
    ops.for_slice(start, stop, step, function(i) indices[#indices + 1] = i end)
    if isview then
      if not M.is_buffer(value) then V.throw("TypeError", "a bytes-like object is required") end
      M.check(value)
      local fmt = kind(value) == "memoryview" and value.format or "B"
      if fmt ~= v.format or M.length(value) ~= #indices then
        V.throw("ValueError", "memoryview assignment: lvalue and rvalue have different structures")
      end
    end
    local data = M.sequence(I, value)
    if not isview and step == 1 then
      if start == M.length(v) then M.append(I, v, data); return end
      M.resize(v, #v.b - #indices + #data)
      v.b = v.b:sub(1, start) .. data .. v.b:sub(stop + 1)
      return
    end
    local size = isview and v.itemsize or 1
    if #data ~= #indices * size then
      V.throw("ValueError", "attempt to assign bytes of different size to extended slice")
    end
    I:fuel_n(#base.b // 16 + #data // 16 + 1)
    if isview and step == 1 and v.stride == size then
      local offset = v.offset + start * size
      base.b = base.b:sub(1, offset) .. data .. base.b:sub(offset + #data + 1)
      return
    end
    local output = {}
    for i = 1, #base.b do output[i] = base.b:sub(i, i) end
    for j, i in ipairs(indices) do
      local offset = isview and (v.offset + i * v.stride) or i
      for byte = 1, size do
        local source = (j - 1) * size + byte
        output[offset + byte] = data:sub(source, source)
      end
    end
    base.b = table.concat(output)
    return
  end
  local i = index(v, key)
  local data
  if not isview or v.format == "B" then data = byte(value)
  elseif v.format == "?" then data = string.char(I:truth(value) and 1 or 0)
  else
    local converted = value
    if v.format == "c" then
      if kind(value) ~= "bytes" or #value.b ~= 1 then V.throw("TypeError", "memoryview: invalid type for format 'c'") end
      converted = value.b
    elseif v.format ~= "d" and v.format ~= "f" then
      converted = V.as_index(value)
      if converted == nil then V.throw("TypeError", "memoryview: invalid type for integer format") end
      local bounds = integer_bounds[v.format]
      local outside
      if bigint.is_bigint(converted) then
        outside = bigint.cmp(converted, bounds[1]) < 0 or bigint.cmp(converted, bounds[2]) > 0
      else
        outside = converted < bounds[1] or not bigint.is_bigint(bounds[2]) and converted > bounds[2]
      end
      if outside then
        V.throw("ValueError", "memoryview: invalid value for format '" .. v.format .. "'")
      end
      if bigint.is_bigint(converted) then converted = bigint.sub(converted, bounds[3]) end
    else
      converted = V.as_float(value)
      if converted == nil then V.throw("TypeError", "memoryview: invalid type for float format") end
    end
    local ok
    ok, data = pcall(string.pack, formats[v.format], converted)
    if not ok then V.throw("ValueError", "memoryview: invalid value for format '" .. v.format .. "'") end
  end
  local offset = isview and (v.offset + i * v.stride) or i
  base.b = base.b:sub(1, offset) .. data .. base.b:sub(offset + #data + 1)
end
function M.delete(v, key, ops)
  local indices = {}
  if kind(key) == "slice" then
    local start, stop, step = ops.slice_indices(key, #v.b)
    ops.for_slice(start, stop, step, function(i) indices[i + 1] = true end)
  else indices[index(v, key) + 1] = true end
  local parts = {}
  for i = 1, #v.b do if not indices[i] then parts[#parts + 1] = v.b:sub(i, i) end end
  M.resize(v, #parts)
  v.b = table.concat(parts)
end
function M.attr(I, v, name)
  H = H or require("diaspore.helpers")
  local isview = v.kind == "memoryview"
  if isview and (name == "release" or name == "__exit__") then
    return V.builtin(name, function(_, args, kw)
      local count = name == "release" and 0 or 3
      H.arg_count(name, args, count, count); H.no_kw(name, kw)
      if v.base.kind == "bytearray" then v.base.exports[v] = nil end
      v.released = true
      return V.NONE
    end)
  end
  M.check(v)
  if isview then
    if name == "obj" then return v.base end
    if name == "readonly" then return v.readonly end
    if name == "format" then return v.format end
    if name == "itemsize" then return v.itemsize end
    if name == "ndim" then return 1 end
    if name == "nbytes" then return v.length * v.itemsize end
    if name == "shape" then return V.tuple({ v.length }) end
    if name == "strides" then return V.tuple({ v.stride }) end
    if name == "suboffsets" then return V.tuple({}) end
    if name == "contiguous" or name == "c_contiguous" or name == "f_contiguous" then return v.length <= 1 or v.stride == v.itemsize end
    if name == "tobytes" then return V.builtin(name, function(_, args, kw)
      H.arg_count(name, args, 0, 1)
      local order = args[1] or H.kw_get(kw, "order") or "C"
      if order ~= "C" and order ~= "F" and order ~= "A" then V.throw("ValueError", "order must be 'C', 'F' or 'A'") end
      return V.bytes(M.data(v))
    end) end
    if name == "tolist" then return V.builtin(name, function(_, args, kw)
      H.arg_count(name, args, 0, 0); H.no_kw(name, kw)
      local items = {}
      for i = 0, M.length(v) - 1 do items[#items + 1] = M.get(v, i) end
      return V.list(items)
    end) end
    if name == "toreadonly" then return V.builtin(name, function(_, args, kw)
      H.arg_count(name, args, 0, 0); H.no_kw(name, kw); M.check(v)
      return view(v.base, v.offset, v.length, v.stride, v.format, true)
    end) end
    if name == "cast" then return V.builtin(name, function(_, args, kw)
      H.arg_count(name, args, 1, 2); M.check(v)
      local fmt = args[1]
      if type(fmt) ~= "string" then V.throw("TypeError", "cast format must be str") end
      if fmt:sub(1, 1) == "@" then fmt = fmt:sub(2) end
      if not formats[fmt] then V.throw("ValueError", "destination format must be a native single character format") end
      if v.stride ~= v.itemsize then V.throw("TypeError", "memoryview: casts are restricted to C-contiguous views") end
      if v.itemsize ~= 1 and string.packsize(formats[fmt]) ~= 1 then V.throw("TypeError", "memoryview: cannot cast between two non-byte formats") end
      local size = string.packsize(formats[fmt])
      local nbytes = v.length * v.itemsize
      if nbytes % size ~= 0 then V.throw("TypeError", "memoryview: length is not a multiple of itemsize") end
      local shape = args[2] or H.kw_get(kw, "shape")
      if shape then
        local dims = require("diaspore.iter").iter_to_array(I, shape)
        if #dims ~= 1 then V.throw("NotImplementedError", "diaspore-lua supports one-dimensional memoryviews") end
        local dim = V.as_index(dims[1])
        if dim == nil then V.throw("TypeError", "memoryview: elements of shape must be integers") end
        dim = V.to_size(dim)
        if dim <= 0 then V.throw("ValueError", "memoryview.cast(): elements of shape must be integers > 0") end
        if dim ~= nbytes // size then V.throw("TypeError", "memoryview: product(shape) * itemsize != buffer size") end
      end
      return view(v.base, v.offset, nbytes // size, size, fmt, v.readonly)
    end) end
    if name == "__enter__" then return V.builtin(name, function() M.check(v); return v end) end
  else
    if name == "append" or name == "extend" or name == "insert" then return V.builtin(name, function(_, args, kw)
      H.arg_count(name, args, name == "insert" and 2 or 1, name == "insert" and 2 or 1); H.no_kw(name, kw)
      if name == "extend" and kind(args[1]) == "memoryview" then
        local source = args[1]
        if source.released or source.length > 1 and source.stride ~= source.itemsize then
          V.throw("TypeError", "can't set bytearray slice from memoryview")
        end
      end
      local data = name == "extend" and M.sequence(I, args[1]) or byte(args[name == "insert" and 2 or 1])
      local size = M.length(v)
      local offset = size
      if name == "insert" then
        offset = V.as_index(args[1]); if offset == nil then V.throw("TypeError", "an integer is required") end
        offset = V.to_size(offset)
        if offset < 0 then offset = math.max(0, offset + size) end
        offset = math.min(offset, size)
      end
      if offset == size then
        M.append(I, v, data)
      else
        if #data > math.maxinteger - size then V.throw("OverflowError", "bytearray is too large") end
        M.resize(v, size + #data)
        I:fuel_n((size + #data) // 16 + 1)
        v.b = v.b:sub(1, offset) .. data .. v.b:sub(offset + 1)
      end
      return V.NONE
    end) end
    if name == "clear" then return V.builtin(name, function(_, args, kw)
      H.arg_count(name, args, 0, 0); H.no_kw(name, kw); M.resize(v, 0); v.b = ""; return V.NONE
    end) end
    if name == "copy" then return V.builtin(name, function(_, args, kw)
      H.arg_count(name, args, 0, 0); H.no_kw(name, kw); return M.bytearray(v.b)
    end) end
    if name == "reverse" then return V.builtin(name, function(_, args, kw)
      H.arg_count(name, args, 0, 0); H.no_kw(name, kw); v.b = v.b:reverse(); return V.NONE
    end) end
    if name == "pop" or name == "remove" then return V.builtin(name, function(_, args, kw)
      H.arg_count(name, args, name == "remove" and 1 or 0, 1); H.no_kw(name, kw)
      local i
      if name == "remove" then
        i = v.b:find(byte(args[1]), 1, true)
        if not i then V.throw("ValueError", "value not found in bytearray") end
      else i = index(v, args[1] or -1) + 1 end
      M.resize(v, #v.b - 1)
      local result = v.b:byte(i)
      v.b = v.b:sub(1, i - 1) .. v.b:sub(i + 1)
      return name == "remove" and V.NONE or result
    end) end
  end
  if name == "hex" then return V.builtin(name, function(_, args, kw)
    H.arg_count(name, args, 0, 2)
    local sep, group = args[1], args[2]
    for _, entry in ipairs(kw) do
      if entry.k == "sep" then
        if sep ~= nil then V.throw("TypeError", "multiple values for argument 'sep'") end
        sep = entry.v
      elseif entry.k == "bytes_per_sep" then
        if group ~= nil then V.throw("TypeError", "multiple values for argument 'bytes_per_sep'") end
        group = entry.v
      else V.throw("TypeError", "invalid keyword argument '" .. entry.k .. "'") end
    end
    group = group == nil and 1 or V.as_index(group)
    if group == nil then V.throw("TypeError", "bytes_per_sep must be an integer") end
    if bigint.is_bigint(group) or group < -0x80000000 or group > 0x7fffffff then
      V.throw("OverflowError", "Python int too large to convert to C int")
    end
    if sep ~= nil then
      local size
      if kind(sep) == "bytes" then sep = sep.b; size = #sep
      elseif type(sep) == "string" then size = V.str_len(sep)
      else V.throw("TypeError", "sep must be str or bytes") end
      if size ~= 1 then V.throw("ValueError", "sep must be length 1.") end
      if #sep ~= 1 or sep:byte() > 127 then V.throw("ValueError", "sep must be ASCII.") end
    end
    local data = M.data(v)
    I:fuel_n(#data)
    if sep == nil or group == 0 then
      return (data:gsub(".", function(c) return string.format("%02x", c:byte()) end))
    end
    local parts, width = {}, math.abs(group)
    for i = 1, #data do
      parts[#parts + 1] = string.format("%02x", data:byte(i))
      if i < #data and (group < 0 and i % width == 0 or group > 0 and (#data - i) % width == 0) then
        parts[#parts + 1] = sep
      end
    end
    return table.concat(parts)
  end) end
end
local function integer_options(I, args, kw, first_default, first_name)
  H = H or require("diaspore.helpers")
  H.arg_count(first_name, args, 0, 2)
  local first, order, signed = args[1], args[2] or "big", false
  for _, entry in ipairs(kw) do
    if entry.k == first_name then
      if first ~= nil then V.throw("TypeError", "multiple values for argument '" .. first_name .. "'") end
      first = entry.v
    elseif entry.k == "byteorder" then
      if args[2] ~= nil then V.throw("TypeError", "multiple values for argument 'byteorder'") end
      order = entry.v
    elseif entry.k == "signed" then signed = I:truth(entry.v)
    else V.throw("TypeError", "invalid keyword argument '" .. entry.k .. "'") end
  end
  if first == nil then first = first_default end
  if first == nil then V.throw("TypeError", "missing required argument '" .. first_name .. "'") end
  if type(order) ~= "string" then V.throw("TypeError", "byteorder must be str") end
  if order ~= "big" and order ~= "little" then V.throw("ValueError", "byteorder must be either 'little' or 'big'") end
  return first, order, signed
end
--- Decode a byte sequence as an arbitrary-precision Python integer.
function M.from_bytes(I, args, kw)
  local value, order, signed = integer_options(I, args, kw, nil, "bytes")
  local data = M.sequence(I, value)
  I:fuel_n(#data)
  return bigint.from_bytes(data, order, signed)
end
--- Encode an integer, rejecting lengths outside the host index range.
function M.to_bytes(I, value, args, kw)
  local length, order, signed = integer_options(I, args, kw, 1, "length")
  length = V.as_index(length)
  if length == nil then V.throw("TypeError", "length must be an integer") end
  length = V.to_size(length)
  if length < 0 then V.throw("ValueError", "length argument must be non-negative") end
  I:fuel_n(length)
  return V.bytes(bigint.to_bytes(value, length, order, signed))
end
return M
