-- Small argument-checking, kwargs, encoding, and Unicode-case helpers shared
-- by builtins, methods, and natives.
--
-- kwargs are represented as a dense array of {k=<name>, v=<value>} pairs in
-- call order (they are tiny; lookups scan).

local values = require("diaspore.values")
local format = require("diaspore.format")
local iter = require("diaspore.iter")

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
  if n > 0 then I:fuel_n(n) end
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
  if type(src) == "table" and src.kind == "mappingproxy" then
    src = values.namespace_dict(src.cls)
  end
  if type(src) == "table" and src.kind == "dict" then
    local entries = values.dict_entries(src)
    for i = 1, #entries do
      local entry = entries[i]
      values.dict_set_hk(d, entry.hk or values.hash_key(entry.k), entry.k, entry.v)
    end
    return
  end
  local pairs_ = iter.iter_to_array(I, src)
  for i = 1, #pairs_ do
    local kv = iter.iter_to_array(I, pairs_[i])
    if #kv ~= 2 then
      throw("ValueError", "dictionary update sequence element #" .. (i - 1)
        .. " has length " .. #kv .. "; 2 is required")
    end
    values.dict_set(d, kv[1], kv[2])
  end
end

-- ---------------------------------------------------------------------------
-- encodings

local function ascii_error(name, object, start, stop)
  local reason = "ordinal not in range(128)"
  local exc = values.make_exc(name, "ascii", object, start, stop, reason)
  exc.d.encoding = "ascii"
  exc.d.object = object
  exc.d.start = start
  exc.d["end"] = stop
  exc.d.reason = reason
  values.throw_val(exc)
end

--- Encode a string as UTF-8, ASCII, or Latin-1 bytes.
function M.encode_str_to(s, enc)
  local e = enc:lower():gsub("_", "-")
  if e == "utf-8" or e == "utf8" then
    return values.bytes(s)
  end
  if e == "ascii" or e == "us-ascii" or e == "646" then
    if values.is_ascii(s) then return values.bytes(s) end
    local start, stop
    local position = 0
    for _, cp in utf8.codes(s, true) do
      if cp > 127 then
        if start == nil then start = position end
        stop = position + 1
      elseif start ~= nil then
        break
      end
      position = position + 1
    end
    ascii_error("UnicodeEncodeError", s, start, stop)
  end
  if e == "latin-1" or e == "latin1" or e == "iso-8859-1" then
    local out = {}
    for _, cp in utf8.codes(s, true) do
      if cp > 0xFF then
        throw("ValueError", "'latin-1' codec can't encode character "
          .. format.repr_string(utf8.char(cp)))
      end
      out[#out + 1] = string.char(cp)
    end
    return values.bytes(table.concat(out))
  end
  throw("ValueError", "unknown encoding: " .. enc)
end

--- Decode UTF-8, ASCII, or Latin-1 bytes.
function M.decode_bytes(b, enc)
  local e = enc:lower():gsub("_", "-")
  if e == "utf-8" or e == "utf8" then
    if utf8.len(b.b) == nil then
      throw("ValueError", "'utf-8' codec can't decode bytes: invalid data")
    end
    return b.b
  end
  if e == "ascii" or e == "us-ascii" or e == "646" then
    for i = 1, #b.b do
      if b.b:byte(i) > 127 then ascii_error("UnicodeDecodeError", b, i - 1, i) end
    end
    return b.b
  end
  if e == "latin-1" or e == "latin1" or e == "iso-8859-1" then
    local out = {}
    local data = b.b
    for i = 1, #data do out[i] = utf8.char(data:byte(i)) end
    return table.concat(out)
  end
  throw("ValueError", "unknown encoding: " .. enc)
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
