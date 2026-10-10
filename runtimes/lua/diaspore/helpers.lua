-- Small argument-checking, kwargs, encoding, and Unicode-case helpers shared
-- by builtins, methods, and natives.
--
-- kwargs are represented as a dense array of {k=<name>, v=<value>} pairs in
-- call order (they are tiny; lookups scan).

local values = require("diaspore.values")
local bigint = require("diaspore.bigint")
local iter = require("diaspore.iter")
local pcall = require("diaspore.protected").call

local M = {}

local throw = values.throw
local type_name = values.type_name

-- ---------------------------------------------------------------------------
-- kwargs

--- Find a keyword value, or return nil when absent.
function M.kw_get(kw, name)
  for i = 1, #kw do
    if kw[i].k == name then return kw[i].v end
  end
  return nil
end

--- Reject keyword arguments with TypeError.
function M.no_kw(name, kw)
  if #kw > 0 then
    throw("TypeError", name .. "() takes no keyword arguments")
  end
end

-- ---------------------------------------------------------------------------
-- arg checks

--- Validate the positional argument count.
function M.arg_count(name, args, min, max)
  local n = #args
  if n < min or (max >= 0 and n > max) then
    if min == max then
      throw("TypeError", name .. "() takes exactly " .. min .. " argument"
        .. (min == 1 and "" or "s") .. " (" .. n .. " given)")
    end
    throw("TypeError", name .. " expected at most " .. max .. " arguments, got " .. n)
  end
end

--- Validate the positional argument count for a method.
function M.need_args(name, args, min, max)
  local n = #args
  if n < min then
    throw("TypeError", name .. " expected at least " .. min .. " arguments, got " .. n)
  end
  if max >= 0 and n > max then
    throw("TypeError", name .. " expected at most " .. max .. " arguments, got " .. n)
  end
end

--- Require an integer-compatible value.
function M.want_int(what, v)
  local n = values.as_index(v)
  if n == nil then
    throw("TypeError", what .. " must be an integer, not '" .. type_name(v) .. "'")
  end
  return n
end

--- Apply Python's exact integer-index protocol without float conversion.
function M.index_value(I, value)
  local number = values.as_index(I:raw(value))
  if number ~= nil then return number end
  if type(value) == "table" and value.kind == "instance"
      and values.class_lookup(value.cls, "__index__") ~= nil then
    local result = I:call_dunder(value, "__index__", {})
    number = values.as_index(I:raw(result))
    if number == nil then
      throw("TypeError", "__index__ returned non-int (type " .. type_name(result) .. ")")
    end
    return number
  end
  throw("TypeError", "'" .. type_name(value) .. "' object cannot be interpreted as an integer")
end

--- Require a string value.
function M.want_str(what, v)
  if type(v) == "string" then return v end
  throw("TypeError", what .. " must be str, not " .. type_name(v))
end

--- Charge fuel for the length of a sized container before a bulk host-speed
-- operation consumes it (mirrors diaspore-py's _charge_sized).
function M.charge_sized(I, v)
  local n = -1
  if type(v) == "string" then
    n = values.str_len(v)
  elseif type(v) == "table" then
    local k = v.kind
    if k == "bytes" then
      n = #v.b
    elseif k == "list" or k == "tuple" then
      n = #v.items
    elseif k == "dict" or k == "set" then
      n = v.size
    elseif k == "range" then
      n = values.range_len(v)
    end
  end
  if bigint.is_bigint(n) then
    I:fuel_n(math.maxinteger)
  elseif n > 0 then
    I:fuel_n(n)
  end
end

--- key=/reverse= keyword pair for sort()/sorted().
function M.key_reverse_kw(name, I, kw)
  local key = nil
  local reverse = false
  for i = 1, #kw do
    local k, v = kw[i].k, kw[i].v
    if k == "key" then
      key = v
    elseif k == "reverse" then
      reverse = I:truth(v)
    else
      throw("TypeError", "'" .. k .. "' is an invalid keyword argument for " .. name .. "()")
    end
  end
  if key ~= nil and type(key) == "table" and key.kind == "none" then key = nil end
  return key, reverse
end

--- Update a dictionary from a mapping or iterable of pairs.
function M.dict_update_from(I, d, src)
  if type(src) == "table" and src.kind == "instance"
      and type(src.prim) == "table" and src.prim.kind == "dict" then
    local native_iteration = true
    for _, base in ipairs(src.cls.mro) do
      if base.kind == "btype" then break end
      if base.ns.__iter__ ~= nil then native_iteration = false; break end
    end
    if native_iteration then src = src.prim end
  end
  if type(src) == "table" and src.kind == "mappingproxy" then
    src = values.proxy_dict(src)
  end
  if type(src) == "table" and src.kind == "dict" then
    local entries = values.dict_entries(src)
    for i = 1, #entries do
      local entry = entries[i]
      values.dict_set_hk(d, entry.hk or values.hash_key(entry.k), entry.k, entry.v)
    end
    return
  end
  local saved = #I.stack
  local has_keys, keys = pcall(I.get_attr, I, src, "keys")
  if has_keys then
    local names = iter.iter_to_array(I, I:call(keys, {}, {}))
    local ops = require("diaspore.ops")
    for _, key in ipairs(names) do values.dict_set(d, key, ops.get_index(I, src, key)) end
    return
  end
  if not values.is_exc(keys) or not values.exc_isa(keys.value, "AttributeError") then error(keys, 0) end
  I:trim(saved)
  local source = iter.get_iter(I, src)
  local index = 0
  while true do
    local pair, more = source.next()
    if not more then return end
    local depth = #I.stack
    local ok, kv = pcall(iter.iter_to_array, I, pair)
    if not ok then
      if values.is_exc(kv) and values.exc_isa(kv.value, "TypeError") then
        I:trim(depth)
        throw("TypeError", "cannot convert dictionary update sequence element #" .. index .. " to a sequence")
      end
      error(kv, 0)
    end
    if #kv ~= 2 then
      throw("ValueError", "dictionary update sequence element #" .. index
        .. " has length " .. #kv .. "; 2 is required")
    end
    values.dict_set(d, kv[1], kv[2])
    index = index + 1
  end
end

-- ---------------------------------------------------------------------------
-- encodings

--- Encode text using native fast codecs or a bundled codec.
function M.encode_str_to(s, enc, errors, I)
  return require("diaspore.encoding").encode(I, s, enc, errors)
end

--- Decode bytes using native fast codecs or a bundled codec.
function M.decode_bytes(b, enc, errors, I)
  return require("diaspore.encoding").decode(I, b, enc, errors)
end

-- ---------------------------------------------------------------------------
-- Unicode case & predicates (ASCII + Latin-1 exactly; an
-- approximation beyond that - diaspore-lua limitation)

--- Uppercased form of one code point, as a string.
function M.upper_cp(cp)
  if cp >= 0x61 and cp <= 0x7A then return string.char(cp - 32) end
  if cp == 0xDF then return "SS" end -- U+00DF
  if cp == 0xB5 then return utf8.char(0x39C) end -- U+00B5 -> U+039C
  if cp == 0xFF then return utf8.char(0x178) end -- U+00FF -> U+0178
  if cp >= 0xE0 and cp <= 0xFE and cp ~= 0xF7 then return utf8.char(cp - 32) end
  return utf8.char(cp)
end

--- Return the lowercase form of one code point.
function M.lower_cp(cp)
  if cp >= 0x41 and cp <= 0x5A then return string.char(cp + 32) end
  if cp >= 0xC0 and cp <= 0xDE and cp ~= 0xD7 then return utf8.char(cp + 32) end
  if cp == 0x178 then return utf8.char(0xFF) end
  return utf8.char(cp)
end

--- Test whether a code point is uppercase.
function M.is_upper_cp(cp)
  return (cp >= 0x41 and cp <= 0x5A)
    or (cp >= 0xC0 and cp <= 0xDE and cp ~= 0xD7)
    or cp == 0x178
end

--- Test whether a code point is lowercase.
function M.is_lower_cp(cp)
  return (cp >= 0x61 and cp <= 0x7A)
    or (cp >= 0xDF and cp <= 0xFF and cp ~= 0xF7)
    or cp == 0xB5
end

--- Test whether a code point is cased.
function M.is_cased_cp(cp)
  return M.is_upper_cp(cp) or M.is_lower_cp(cp)
end

--- Test whether a code point is alphabetic.
function M.is_alpha_cp(cp)
  if M.is_cased_cp(cp) then return true end
  if cp == 0xAA or cp == 0xBA then return true end
  -- pragmatic: treat letters beyond Latin-1 as alphabetic
  if cp >= 0x100 and cp <= 0x2AF then return true end
  if cp >= 0x370 and cp <= 0x1FFF and cp ~= 0x2028 and cp ~= 0x2029 then return true end
  if cp >= 0x2E80 and cp <= 0xD7FF then return true end
  if cp >= 0xF900 and cp <= 0xFDFF then return true end
  if cp >= 0x10000 and cp <= 0x2FA1F then return true end
  return false
end

--- Test whether a code point is an ASCII digit.
function M.is_digit_cp(cp)
  return cp >= 0x30 and cp <= 0x39
end

-- Unicode 15 decimal blocks each contain ten consecutive digits.
local DECIMAL_ZEROS = {
  0x30, 0x660, 0x6f0, 0x7c0, 0x966, 0x9e6, 0xa66, 0xae6, 0xb66, 0xbe6,
  0xc66, 0xce6, 0xd66, 0xde6, 0xe50, 0xed0, 0xf20, 0x1040, 0x1090, 0x17e0,
  0x1810, 0x1946, 0x19d0, 0x1a80, 0x1a90, 0x1b50, 0x1bb0, 0x1c40, 0x1c50,
  0xa620, 0xa8d0, 0xa900, 0xa9d0, 0xa9f0, 0xaa50, 0xabf0, 0xff10,
  0x104a0, 0x10d30, 0x11066, 0x110f0, 0x11136, 0x111d0, 0x112f0, 0x11450,
  0x114d0, 0x11650, 0x116c0, 0x11730, 0x118e0, 0x11950, 0x11c50, 0x11d50,
  0x11da0, 0x11f50, 0x16a60, 0x16ac0, 0x16b50, 0x1d7ce, 0x1d7d8, 0x1d7e2,
  0x1d7ec, 0x1d7f6, 0x1e140, 0x1e2f0, 0x1e4f0, 0x1e950, 0x1fbf0,
}

--- Return a Unicode decimal digit's value, or nil.
function M.decimal_value(cp)
  if cp <= 0x7f then return cp >= 0x30 and cp <= 0x39 and cp - 0x30 or nil end
  local low, high = 1, #DECIMAL_ZEROS
  while low <= high do
    local mid = (low + high) // 2
    local zero = DECIMAL_ZEROS[mid]
    if cp < zero then
      high = mid - 1
    elseif cp > zero + 9 then
      low = mid + 1
    else
      return cp - zero
    end
  end
  return nil
end

local SPACE_CPS = {
  [0x09] = true, [0x0A] = true, [0x0B] = true, [0x0C] = true, [0x0D] = true,
  [0x1C] = true, [0x1D] = true, [0x1E] = true, [0x1F] = true, [0x20] = true,
  [0x85] = true, [0xA0] = true, [0x1680] = true, [0x2028] = true,
  [0x2029] = true, [0x202F] = true, [0x205F] = true, [0x3000] = true,
}

--- Test whether a code point is whitespace.
function M.is_space_cp(cp)
  if SPACE_CPS[cp] then return true end
  return cp >= 0x2000 and cp <= 0x200A
end

--- Predicates on one-code-point strings.
function M.is_space_ch(ch)
  return M.is_space_cp(utf8.codepoint(ch, 1, 1, true))
end

--- Convert a string to uppercase.
function M.str_upper(s)
  if values.is_ascii(s) then return s:upper() end
  local out = {}
  for _, cp in utf8.codes(s, true) do out[#out + 1] = M.upper_cp(cp) end
  return table.concat(out)
end

--- Convert a string to lowercase.
function M.str_lower(s)
  if values.is_ascii(s) then return s:lower() end
  local out = {}
  for _, cp in utf8.codes(s, true) do out[#out + 1] = M.lower_cp(cp) end
  return table.concat(out)
end

--- Apply case folding to a string.
function M.str_casefold(s)
  local out = {}
  for _, cp in utf8.codes(s, true) do
    if cp == 0xDF or cp == 0x1E9E then
      out[#out + 1] = "ss"
    else
      out[#out + 1] = M.lower_cp(cp)
    end
  end
  return table.concat(out)
end

return M
