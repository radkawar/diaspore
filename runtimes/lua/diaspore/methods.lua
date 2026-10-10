-- Methods on builtin types (spec section 9.1), implemented natively with CPython
-- semantics (code-point string indexing, Python split/strip/title rules).

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
local chars = values.chars
local as_index = values.as_index
local need_args = H.need_args
local no_kw = H.no_kw
local function want_int(what, v)
  return values.to_size(H.want_int(what, v))
end
local want_str = H.want_str

local function is_none(v)
  return type(v) == "table" and v.kind == "none"
end

local function kindof(v)
  if type(v) == "table" then return v.kind end
  return nil
end

local function split_arguments(name, args, kw)
  need_args(name, args, 0, 2)
  local sep, count = args[1], args[2]
  for _, entry in ipairs(kw) do
    if entry.k == "sep" then
      if sep ~= nil then throw("TypeError", name .. "() got multiple values for argument 'sep'") end
      sep = entry.v
    elseif entry.k == "maxsplit" then
      if count ~= nil then throw("TypeError", name .. "() got multiple values for argument 'maxsplit'") end
      count = entry.v
    else throw("TypeError", "'" .. entry.k .. "' is an invalid keyword argument for " .. name .. "()") end
  end
  return sep, count == nil and -1 or want_int("maxsplit", count)
end

local function split_literal(data, sep, count, from_right)
  if sep == "" then throw("ValueError", "empty separator") end
  if from_right then data, sep = data:reverse(), sep:reverse() end
  local parts, first = {}, 1
  while count < 0 or #parts < count do
    local at = data:find(sep, first, true)
    if at == nil then break end
    parts[#parts + 1] = data:sub(first, at - 1)
    first = at + #sep
  end
  parts[#parts + 1] = data:sub(first)
  if from_right then
    for i = 1, #parts // 2 do
      local j = #parts - i + 1
      parts[i], parts[j] = parts[j]:reverse(), parts[i]:reverse()
    end
    if #parts % 2 == 1 then
      local middle = #parts // 2 + 1
      parts[middle] = parts[middle]:reverse()
    end
  end
  return parts
end

-- ---------------------------------------------------------------------------
-- str helpers

local function py_title(s)
  local b = {}
  local prev_cased = false
  for _, cp in utf8.codes(s, true) do
    local cased = H.is_cased_cp(cp)
    if cased then
      b[#b + 1] = prev_cased and H.lower_cp(cp) or H.upper_cp(cp)
    else
      b[#b + 1] = utf8.char(cp)
    end
    prev_cased = cased
  end
  return table.concat(b)
end

local function split_whitespace(s, maxsplit, from_right)
  local cs = chars(s)
  local n = #cs
  -- collect all fields
  local fields = {}
  local i = 1
  while i <= n do
    while i <= n and H.is_space_ch(cs[i]) do i = i + 1 end
    if i > n then break end
    local j = i
    while j <= n and not H.is_space_ch(cs[j]) do j = j + 1 end
    fields[#fields + 1] = table.concat(cs, "", i, j - 1)
    i = j
  end
  if maxsplit < 0 or #fields <= maxsplit then return fields end
  if not from_right then
    local parts = {}
    local pos = 1
    while pos <= n and H.is_space_ch(cs[pos]) do pos = pos + 1 end
    while #parts < maxsplit and pos <= n do
      local j = pos
      while j <= n and not H.is_space_ch(cs[j]) do j = j + 1 end
      parts[#parts + 1] = table.concat(cs, "", pos, j - 1)
      pos = j
      while pos <= n and H.is_space_ch(cs[pos]) do pos = pos + 1 end
    end
    if pos <= n then parts[#parts + 1] = table.concat(cs, "", pos, n) end
    return parts
  end
  local tail = {}
  local last = n
  while last >= 1 and H.is_space_ch(cs[last]) do last = last - 1 end
  while #tail < maxsplit and last >= 1 do
    local j = last
    while j >= 1 and not H.is_space_ch(cs[j]) do j = j - 1 end
    table.insert(tail, 1, table.concat(cs, "", j + 1, last))
    last = j
    while last >= 1 and H.is_space_ch(cs[last]) do last = last - 1 end
  end
  if last >= 1 then table.insert(tail, 1, table.concat(cs, "", 1, last)) end
  return tail
end

local LINE_BREAK_CPS = {
  [0x0A] = true, [0x0B] = true, [0x0C] = true, [0x1C] = true, [0x1D] = true,
  [0x1E] = true, [0x85] = true, [0x2028] = true, [0x2029] = true,
}

local function split_lines(s, keepends)
  local out, first = {}, 1
  for pos, cp in utf8.codes(s, true) do
    if pos >= first and (LINE_BREAK_CPS[cp] or cp == 0x0D) then
      local last = pos
      if cp == 0x0D and s:byte(pos + 1) == 0x0A then last = pos + 1
      elseif cp == 0x85 then last = pos + 1
      elseif cp == 0x2028 or cp == 0x2029 then last = pos + 2 end
      out[#out + 1] = s:sub(first, keepends and last or pos - 1)
      first = last + 1
    end
  end
  if first <= #s then out[#out + 1] = s:sub(first) end
  return out
end

local function str_list(parts)
  local items = {}
  for i = 1, #parts do items[i] = parts[i] end
  return values.list(items)
end

-- Resolves optional start/end arguments against a code-point array;
-- returns 0-based [start, end).
local function str_region(rs, args, from)
  local n = #rs
  local start, stop = 0, n
  local function adjust(v, def, is_start)
    if is_none(v) then return def end
    local x = as_index(v)
    if x == nil then
      throw("TypeError",
        "slice indices must be integers or None or have an __index__ method")
    end
    x = values.clamp_index(x, -n, is_start and n + 1 or n)
    if x < 0 then
      x = x + n
      if x < 0 then x = 0 end
    end
    return x
  end
  if #args >= from then start = adjust(args[from], 0, true) end
  if #args >= from + 1 then stop = adjust(args[from + 1], n, false) end
  return start, stop
end

local function runes_match(rs, sub, at) -- at 0-based
  if at < 0 or at + #sub > #rs then return false end
  for j = 1, #sub do
    if rs[at + j] ~= sub[j] then return false end
  end
  return true
end

local function find_runes(rs, sub, start, stop, last) -- 0-based region, ret 0-based or -1
  if start > stop then return -1 end
  if #sub == 0 then
    if last then return stop end
    return start
  end
  if last then
    for i = stop - #sub, start, -1 do
      if runes_match(rs, sub, i) then return i end
    end
    return -1
  end
  for i = start, stop - #sub do
    if runes_match(rs, sub, i) then return i end
  end
  return -1
end

local function count_runes(rs, sub)
  if #sub == 0 then return #rs + 1 end
  local count = 0
  local i = 0
  while i + #sub <= #rs do
    if runes_match(rs, sub, i) then
      count = count + 1
      i = i + #sub
    else
      i = i + 1
    end
  end
  return count
end

local function py_replace(s, old, rep, count)
  if count == 0 then return s end
  if old == "" then
    local out = {}
    local done = 0
    local rs = chars(s)
    for i = 1, #rs do
      if count < 0 or done < count then
        out[#out + 1] = rep
        done = done + 1
      end
      out[#out + 1] = rs[i]
    end
    if count < 0 or done < count then out[#out + 1] = rep end
    return table.concat(out)
  end
  local out = {}
  local idx = 1
  local done = 0
  while true do
    if count >= 0 and done >= count then break end
    local at = s:find(old, idx, true)
    if at == nil then break end
    out[#out + 1] = s:sub(idx, at - 1)
    out[#out + 1] = rep
    idx = at + #old
    done = done + 1
  end
  out[#out + 1] = s:sub(idx)
  return table.concat(out)
end

local function slice_chars(rs, from, to) -- 1-based inclusive
  return table.concat(rs, "", from, to)
end

-- ---------------------------------------------------------------------------
-- str methods

local function str_method(s, n)
  if n == "maketrans" then
    return function(I, args, kw)
      need_args(n, args, 1, 3); no_kw(n, kw)
      local result = values.new_dict()
      if #args == 1 then
        local mapping = I:raw(args[1])
        if kindof(mapping) ~= "dict" then throw("TypeError", "if you give only one argument to maketrans it must be a dict") end
        for _, entry in ipairs(values.dict_entries(mapping)) do
          local key = entry.k
          if type(key) == "string" then
            if values.str_len(key) ~= 1 then throw("ValueError", "string keys in translate table must be of length 1") end
            key = utf8.codepoint(key, 1, -1, true)
          elseif not bigint.is_int(key) and type(key) ~= "boolean" then
            throw("TypeError", "keys in translate table must be strings or integers")
          end
          values.dict_set(result, key, entry.v)
        end
      else
        local from, to = chars(want_str("maketrans", args[1])), chars(want_str("maketrans", args[2]))
        if #from ~= #to then throw("ValueError", "the first two maketrans arguments must have equal length") end
        for i, character in ipairs(from) do
          values.dict_set(result, utf8.codepoint(character, 1, -1, true), utf8.codepoint(to[i], 1, -1, true))
        end
        if #args == 3 then
          for _, cp in utf8.codes(want_str("maketrans", args[3]), true) do values.dict_set(result, cp, NONE) end
        end
      end
      return result
    end
  elseif n == "translate" then
    return function(I, args, kw)
      need_args(n, args, 1, 1); no_kw(n, kw)
      local result = {}
      for _, cp in utf8.codes(s, true) do
        I:fuel()
        local saved = #I.stack
        local ok, replacement = pcall(ops.get_index, I, args[1], cp)
        if not ok then
          if values.is_exc(replacement) and values.exc_isa(replacement.value, "LookupError") then
            I:trim(saved); replacement = cp
          else error(replacement, 0) end
        end
        if replacement ~= NONE then
          if type(replacement) == "string" then result[#result + 1] = replacement
          elseif bigint.is_int(replacement) or type(replacement) == "boolean" then
            local number = values.as_index(replacement)
            if bigint.cmp(number, 0) < 0 or bigint.cmp(number, 0x10ffff) > 0 then throw("ValueError", "character mapping must be in range(0x110000)") end
            result[#result + 1] = utf8.char(number)
          else throw("TypeError", "character mapping must return integer, None or str") end
        end
      end
      return table.concat(result)
    end
  elseif n == "upper" then
    return function(_I, args, kw)
      no_kw(n, kw)
      return H.str_upper(s)
    end
  elseif n == "lower" then
    return function(_I, args, kw)
      no_kw(n, kw)
      return H.str_lower(s)
    end
  elseif n == "title" then
    return function(_I, args, kw)
      no_kw(n, kw)
      return py_title(s)
    end
  elseif n == "capitalize" then
    return function(_I, args, kw)
      no_kw(n, kw)
      local rs = chars(s)
      if #rs == 0 then return s end
      local first = H.upper_cp(utf8.codepoint(rs[1], 1, 1, true))
      return first .. H.str_lower(slice_chars(rs, 2, #rs))
    end
  elseif n == "casefold" then
    return function(_I, args, kw)
      no_kw(n, kw)
      return H.str_casefold(s)
    end
  elseif n == "strip" or n == "lstrip" or n == "rstrip" then
    return function(_I, args, kw)
      no_kw(n, kw)
      need_args(n, args, 0, 1)
      local in_set
      if #args == 1 and not is_none(args[1]) then
        local cset = {}
        for _, ch in ipairs(chars(want_str(n .. " arg", args[1]))) do
          cset[ch] = true
        end
        in_set = function(ch) return cset[ch] == true end
      else
        in_set = H.is_space_ch
      end
      local rs = chars(s)
      local lo, hi = 1, #rs
      if n ~= "rstrip" then
        while lo <= hi and in_set(rs[lo]) do lo = lo + 1 end
      end
      if n ~= "lstrip" then
        while hi >= lo and in_set(rs[hi]) do hi = hi - 1 end
      end
      return slice_chars(rs, lo, hi)
    end
  elseif n == "split" or n == "rsplit" then
    return function(_I, args, kw)
      local sep, maxsplit = split_arguments(n, args, kw)
      if sep ~= nil and not is_none(sep) then
        sep = want_str("sep", sep)
      else sep = nil end
      local parts
      if sep == nil then
        parts = split_whitespace(s, maxsplit, n == "rsplit")
      else
        parts = split_literal(s, sep, maxsplit, n == "rsplit")
      end
      return str_list(parts)
    end
  elseif n == "expandtabs" then
    return function(I, args, kw)
      need_args(n, args, 0, 1)
      local tabsize = args[1]
      for _, entry in ipairs(kw) do
        if entry.k ~= "tabsize" then throw("TypeError", "'" .. entry.k .. "' is an invalid keyword argument for expandtabs()") end
        if tabsize ~= nil then throw("TypeError", "expandtabs() got multiple values for argument 'tabsize'") end
        tabsize = entry.v
      end
      tabsize = tabsize == nil and 8 or H.want_int("tabsize", tabsize)
      if bigint.is_bigint(tabsize) or tabsize < -0x80000000 or tabsize > 0x7fffffff then
        throw("OverflowError", "Python int too large to convert to C int")
      end
      if not s:find("\t", 1, true) then return s end
      local parts, first, column, size = {}, 1, 0, #s
      for pos, cp in utf8.codes(s, true) do
        if cp == 9 then
          parts[#parts + 1] = s:sub(first, pos - 1)
          local padding = tabsize > 0 and tabsize - column or 0
          size = size - 1
          if size > math.maxinteger - padding then throw("OverflowError", "expanded string is too long") end
          size = size + padding
          I:fuel_n(padding)
          parts[#parts + 1] = string.rep(" ", padding)
          first, column = pos + 1, 0
        elseif cp == 10 or cp == 13 then
          column = 0
        elseif tabsize > 0 then
          column = column + 1
          if column == tabsize then column = 0 end
        end
      end
      parts[#parts + 1] = s:sub(first)
      return table.concat(parts)
    end
  elseif n == "splitlines" then
    return function(I, args, kw)
      need_args(n, args, 0, 1)
      local keepends = args[1]
      for _, entry in ipairs(kw) do
        if entry.k ~= "keepends" then throw("TypeError", "'" .. entry.k .. "' is an invalid keyword argument for splitlines()") end
        if keepends ~= nil then throw("TypeError", "splitlines() got multiple values for argument 'keepends'") end
        keepends = entry.v
      end
      I:fuel_n(#s)
      return str_list(split_lines(s, keepends ~= nil and I:truth(keepends)))
    end
  elseif n == "join" then
    return function(I, args, kw)
      no_kw(n, kw)
      need_args(n, args, 1, 1)
      local items = iter.iter_to_array(I, args[1])
      local parts = {}
      for i = 1, #items do
        local x = items[i]
        if type(x) ~= "string" then
          throw("TypeError", "sequence item " .. (i - 1)
            .. ": expected str instance, " .. type_name(x) .. " found")
        end
        parts[i] = x
      end
      return table.concat(parts, s)
    end
  elseif n == "replace" then
    return function(_I, args, kw)
      no_kw(n, kw)
      need_args(n, args, 2, 3)
      local old = want_str("replace old", args[1])
      local rep = want_str("replace new", args[2])
      local count = -1
      if #args == 3 then count = want_int("count", args[3]) end
      return py_replace(s, old, rep, count)
    end
  elseif n == "find" or n == "rfind" or n == "index" or n == "count" then
    return function(_I, args, kw)
      no_kw(n, kw)
      need_args(n, args, 1, 3)
      local sub = want_str("sub", args[1])
      local rs = chars(s)
      local start, stop = str_region(rs, args, 2)
      if n == "count" then
        if start > stop then return 0 end
        local region = {}
        for i = start + 1, stop do region[#region + 1] = rs[i] end
        return count_runes(region, chars(sub))
      end
      local idx = find_runes(rs, chars(sub), start, stop, n == "rfind")
      if n == "index" and idx < 0 then
        throw("ValueError", "substring not found")
      end
      return idx
    end
  elseif n == "startswith" or n == "endswith" then
    return function(_I, args, kw)
      no_kw(n, kw)
      need_args(n, args, 1, 3)
      local rs = chars(s)
      local start, stop = str_region(rs, args, 2)
      local region = slice_chars(rs, start + 1, stop)
      local pats
      if kindof(args[1]) == "tuple" then
        pats = args[1].items
      else
        pats = { args[1] }
      end
      for _, p in ipairs(pats) do
        if type(p) ~= "string" then
          throw("TypeError", n .. " first arg must be str or a tuple of str, not "
            .. type_name(p))
        end
        if n == "startswith" then
          if start <= stop and region:sub(1, #p) == p then return true end
        else
          if start <= stop and (p == "" or region:sub(-#p) == p) then return true end
        end
      end
      return false
    end
  elseif n == "isdigit" or n == "isdecimal" or n == "isalpha" or n == "isalnum" or n == "isspace" then
    return function(_I, args, kw)
      no_kw(n, kw)
      need_args(n, args, 0, 0)
      if s == "" then return false end
      for _, cp in utf8.codes(s, true) do
        local ok
        if n == "isdecimal" then
          ok = H.decimal_value(cp) ~= nil
        elseif n == "isdigit" then
          ok = H.is_digit_cp(cp)
        elseif n == "isalpha" then
          ok = H.is_alpha_cp(cp)
        elseif n == "isalnum" then
          ok = H.is_alpha_cp(cp) or H.is_digit_cp(cp)
        else
          ok = H.is_space_cp(cp)
        end
        if not ok then return false end
      end
      return true
    end
  elseif n == "zfill" then
    return function(_I, args, kw)
      no_kw(n, kw)
      need_args(n, args, 1, 1)
      local w = want_int("width", args[1])
      local rs = chars(s)
      if #rs >= w then return s end
      local sign, body = "", s
      if #rs > 0 and (rs[1] == "+" or rs[1] == "-") then
        sign = rs[1]
        body = slice_chars(rs, 2, #rs)
      end
      return sign .. string.rep("0", w - #rs) .. body
    end
  elseif n == "ljust" or n == "rjust" then
    return function(_I, args, kw)
      no_kw(n, kw)
      need_args(n, args, 1, 2)
      local w = want_int("width", args[1])
      local fill = " "
      if #args == 2 then
        local f = want_str("fillchar", args[2])
        if #chars(f) ~= 1 then
          throw("TypeError", "The fill character must be exactly one character long")
        end
        fill = f
      end
      local size = #chars(s)
      if w <= size then return s end
      local pad = w - size
      if n == "ljust" then return s .. string.rep(fill, pad) end
      return string.rep(fill, pad) .. s
    end
  elseif n == "encode" then
    return function(_I, args, kw)
      need_args(n, args, 0, 2)
      local enc, errors = require("diaspore.encoding").arguments(args, kw)
      return H.encode_str_to(s, enc, errors, _I)
    end
  elseif n == "partition" or n == "rpartition" then
    return function(_I, args, kw)
      no_kw(n, kw)
      need_args(n, args, 1, 1)
      local sep = want_str("sep", args[1])
      if sep == "" then throw("ValueError", "empty separator") end
      local i
      if n == "partition" then
        i = s:find(sep, 1, true)
        if i == nil then return values.tuple({ s, "", "" }) end
      else
        local from = 1
        i = nil
        while true do
          local at = s:find(sep, from, true)
          if at == nil then break end
          i = at
          from = at + 1
        end
        if i == nil then return values.tuple({ "", "", s }) end
      end
      return values.tuple({ s:sub(1, i - 1), sep, s:sub(i + #sep) })
    end
  elseif n == "removeprefix" then
    return function(_I, args, kw)
      no_kw(n, kw)
      need_args(n, args, 1, 1)
      local p = want_str("prefix", args[1])
      if p ~= "" and s:sub(1, #p) == p then return s:sub(#p + 1) end
      return s
    end
  elseif n == "removesuffix" then
    return function(_I, args, kw)
      no_kw(n, kw)
      need_args(n, args, 1, 1)
      local p = want_str("suffix", args[1])
      if p ~= "" and s:sub(-#p) == p then return s:sub(1, #s - #p) end
      return s
    end
  elseif n == "center" then
    return function(_I, args, kw)
      no_kw(n, kw)
      need_args(n, args, 1, 2)
      local w = want_int("width", args[1])
      local fill = " "
      if #args == 2 then
        local f = want_str("fillchar", args[2])
        if #chars(f) ~= 1 then
          throw("TypeError", "The fill character must be exactly one character long")
        end
        fill = f
      end
      local size = #chars(s)
      if w <= size then return s end
      local marg = w - size
      local left = (marg >> 1) + (marg & w & 1)
      return string.rep(fill, left) .. s .. string.rep(fill, marg - left)
    end
  elseif n == "islower" or n == "isupper" then
    return function(_I, args, kw)
      no_kw(n, kw)
      local has_cased = false
      for _, cp in utf8.codes(s, true) do
        if H.is_cased_cp(cp) then
          has_cased = true
          if n == "islower" and not H.is_lower_cp(cp) then return false end
          if n == "isupper" and not H.is_upper_cp(cp) then return false end
        end
      end
      return has_cased
    end
  elseif n == "swapcase" then
    return function(_I, args, kw)
      no_kw(n, kw)
      local b = {}
      for _, cp in utf8.codes(s, true) do
        if H.is_upper_cp(cp) then
          b[#b + 1] = H.lower_cp(cp)
        elseif H.is_lower_cp(cp) then
          b[#b + 1] = H.upper_cp(cp)
        else
          b[#b + 1] = utf8.char(cp)
        end
      end
      return table.concat(b)
    end
  end
  return nil
end

-- ---------------------------------------------------------------------------
-- list methods

local function seq_index_of(I, items, args) -- 0-based or -1
  local start, stop = 0, #items
  if #args >= 2 then
    start = values.clamp_index(H.want_int("start", args[2]), -#items, #items)
    if start < 0 then
      start = start + #items
      if start < 0 then start = 0 end
    end
  end
  if #args >= 3 then
    stop = values.clamp_index(H.want_int("stop", args[3]), -#items, #items)
    if stop < 0 then stop = stop + #items end
    if stop > #items then stop = #items end
  end
  for i = start, math.min(stop, #items) - 1 do
    if ops.eq_elem(I, items[i + 1], args[1]) then return i end
  end
  return -1
end

local function list_method(l, n)
  if n == "append" then
    return function(_I, args)
      need_args(n, args, 1, 1)
      l.items[#l.items + 1] = args[1]
      return NONE
    end
  elseif n == "extend" then
    return function(I, args)
      need_args(n, args, 1, 1)
      local items = iter.iter_to_array(I, args[1])
      for i = 1, #items do l.items[#l.items + 1] = items[i] end
      return NONE
    end
  elseif n == "insert" then
    return function(_I, args)
      need_args(n, args, 2, 2)
      local i = want_int("index", args[1])
      if i < 0 then
        i = i + #l.items
        if i < 0 then i = 0 end
      elseif i > #l.items then
        i = #l.items
      end
      table.insert(l.items, i + 1, args[2])
      return NONE
    end
  elseif n == "pop" then
    return function(_I, args)
      need_args(n, args, 0, 1)
      if #l.items == 0 then throw("IndexError", "pop from empty list") end
      local i = #l.items - 1
      if #args == 1 then
        i = want_int("index", args[1])
        if i < 0 then i = i + #l.items end
        if i < 0 or i >= #l.items then
          throw("IndexError", "pop index out of range")
        end
      end
      return table.remove(l.items, i + 1)
    end
  elseif n == "remove" then
    return function(I, args)
      need_args(n, args, 1, 1)
      for i = 1, #l.items do
        if ops.eq_elem(I, l.items[i], args[1]) then
          table.remove(l.items, i)
          return NONE
        end
      end
      throw("ValueError", "list.remove(x): x not in list")
    end
  elseif n == "clear" then
    return function()
      l.items = {}
      return NONE
    end
  elseif n == "index" then
    return function(I, args)
      need_args(n, args, 1, 3)
      local i = seq_index_of(I, l.items, args)
      if i < 0 then
        throw("ValueError", ops.repr(I, args[1]) .. " is not in list")
      end
      return i
    end
  elseif n == "count" then
    return function(I, args)
      need_args(n, args, 1, 1)
      local count = 0
      for i = 1, #l.items do
        if ops.eq_elem(I, l.items[i], args[1]) then count = count + 1 end
      end
      return count
    end
  elseif n == "reverse" then
    return function()
      local items = l.items
      local len = #items
      for i = 1, len // 2 do
        items[i], items[len + 1 - i] = items[len + 1 - i], items[i]
      end
      return NONE
    end
  elseif n == "sort" then
    return function(I, args, kw)
      if #args > 0 then
        throw("TypeError", "sort() takes no positional arguments")
      end
      local key, reverse = H.key_reverse_kw("sort", I, kw)
      ops.sort_values(I, l.items, key, reverse)
      return NONE
    end
  elseif n == "copy" then
    return function()
      local out = {}
      for i = 1, #l.items do out[i] = l.items[i] end
      return values.list(out)
    end
  end
  return nil
end

-- ---------------------------------------------------------------------------
-- dict methods

local function dict_method(d, n)
  if n == "get" then
    return function(_I, args)
      need_args(n, args, 1, 2)
      local e = values.dict_entry(d, args[1])
      if e ~= nil then return e.v end
      if #args == 2 then return args[2] end
      return NONE
    end
  elseif n == "keys" or n == "values" or n == "items" then
    return function(_I, args, kw)
      need_args(n, args, 0, 0)
      no_kw(n, kw)
      return values.dict_view(d, n)
    end
  elseif n == "pop" then
    return function(_I, args)
      need_args(n, args, 1, 2)
      local v = values.dict_del(d, args[1])
      if v ~= nil then return v end
      if #args == 2 then return args[2] end
      values.throw_val(values.make_exc("KeyError", args[1]))
    end
  elseif n == "update" then
    return function(I, args, kw)
      need_args(n, args, 0, 1)
      if #args == 1 then H.dict_update_from(I, d, args[1]) end
      for i = 1, #kw do
        values.dict_set_hk(d, "s" .. kw[i].k, kw[i].k, kw[i].v)
      end
      return NONE
    end
  elseif n == "setdefault" then
    return function(_I, args)
      need_args(n, args, 1, 2)
      local hk = values.hash_key(args[1])
      local e = values.dict_entry_hk(d, hk, args[1])
      if e ~= nil then return e.v end
      local dv = #args == 2 and args[2] or NONE
      values.dict_set_hk(d, hk, args[1], dv)
      return dv
    end
  elseif n == "clear" then
    return function()
      values.dict_clear(d)
      return NONE
    end
  elseif n == "copy" then
    return function()
      return values.dict_copy(d)
    end
  end
  return nil
end

-- ---------------------------------------------------------------------------
-- set methods

local function to_set(I, v)
  if kindof(v) == "set" then return v end
  local out = values.new_set()
  local items = iter.iter_to_array(I, v)
  for i = 1, #items do values.set_add(out, items[i]) end
  return out
end

local function set_method(s, n)
  if s.frozen and not M.METHOD_SETS.frozenset[n] then return nil end
  if n == "add" then
    return function(_I, args)
      need_args(n, args, 1, 1)
      values.set_add(s, args[1])
      return NONE
    end
  elseif n == "remove" then
    return function(_I, args)
      need_args(n, args, 1, 1)
      if not values.set_discard(s, args[1]) then
        values.throw_val(values.make_exc("KeyError", args[1]))
      end
      return NONE
    end
  elseif n == "discard" then
    return function(_I, args)
      need_args(n, args, 1, 1)
      values.set_discard(s, args[1])
      return NONE
    end
  elseif n == "pop" then
    return function(_I, args, kw)
      need_args(n, args, 0, 0); no_kw(n, kw)
      return values.set_pop(s)
    end
  elseif n == "clear" then
    return function()
      values.set_clear(s)
      return NONE
    end
  elseif n == "copy" then
    return function(_I, args)
      need_args(n, args, 0, 0)
      if s.frozen then return s end
      return values.set_copy(s)
    end
  elseif n == "difference_update" then
    return function(I, args, kw)
      no_kw(n, kw)
      for _, source in ipairs(args) do
        local other = I:raw(source)
        if other == s then
          values.set_clear(s)
        elseif kindof(other) == "set" then
          for slot, entry in pairs(other.map) do
            I:fuel()
            values.set_discard(s, entry.v, entry.hk or slot)
          end
        else
          local it = iter.get_iter(I, source)
          while true do
            local value, more = it.next()
            if not more then break end
            I:fuel()
            values.set_discard(s, value)
          end
        end
      end
      return NONE
    end
  elseif n == "union" or n == "intersection" or n == "difference"
      or n == "symmetric_difference" then
    return function(I, args)
      if n == "symmetric_difference" then need_args(n, args, 1, 1) end
      local out = values.set_copy(s)
      local op = (n == "union" and "bitor") or (n == "intersection" and "bitand")
        or (n == "difference" and "sub") or "bitxor"
      for _, a in ipairs(args) do
        out = ops.set_op(op, out, to_set(I, a))
      end
      return out
    end
  elseif n == "issubset" then
    return function(I, args)
      need_args(n, args, 1, 1)
      return ops.set_is_subset(s, to_set(I, args[1]))
    end
  elseif n == "issuperset" then
    return function(I, args)
      need_args(n, args, 1, 1)
      return ops.set_is_subset(to_set(I, args[1]), s)
    end
  elseif n == "isdisjoint" then
    return function(I, args)
      need_args(n, args, 1, 1)
      local it = iter.get_iter(I, args[1])
      while true do
        local value, more = it.next()
        if not more then return true end
        if values.set_has(s, value) then return false end
      end
    end
  end
  return nil
end

-- ---------------------------------------------------------------------------
-- tuple methods

local function tuple_method(t, n)
  if n == "count" then
    return function(I, args)
      need_args(n, args, 1, 1)
      local count = 0
      for i = 1, #t.items do
        if ops.eq_elem(I, t.items[i], args[1]) then count = count + 1 end
      end
      return count
    end
  elseif n == "index" then
    return function(I, args)
      need_args(n, args, 1, 3)
      local i = seq_index_of(I, t.items, args)
      if i < 0 then throw("ValueError", "tuple.index(x): x not in tuple") end
      return i
    end
  end
  return nil
end

-- ---------------------------------------------------------------------------
-- bytes methods

local WS_BYTES = { [0x20] = true, [0x09] = true, [0x0A] = true, [0x0D] = true,
                   [0x0B] = true, [0x0C] = true }

local function bytes_fields(data, count, from_right)
  if from_right then data = data:reverse() end
  local out, i, size = {}, 1, #data
  while i <= size do
    while i <= size and WS_BYTES[data:byte(i)] do i = i + 1 end
    if i > size then break end
    if count >= 0 and #out == count then out[#out + 1] = data:sub(i); break end
    local j = i
    while j <= size and not WS_BYTES[data:byte(j)] do j = j + 1 end
    out[#out + 1] = data:sub(i, j - 1)
    i = j
  end
  if from_right then
    for j = 1, #out // 2 do
      local last = #out - j + 1
      out[j], out[last] = out[last]:reverse(), out[j]:reverse()
    end
    if #out % 2 == 1 then
      local middle = #out // 2 + 1
      out[middle] = out[middle]:reverse()
    end
  end
  return out
end

local function bytes_like(value)
  if not buffer.is_buffer(value) then
    throw("TypeError", "a bytes-like object is required, not '" .. type_name(value) .. "'")
  end
  buffer.check(value)
  if kindof(value) == "memoryview" and value.length > 1 and value.stride ~= value.itemsize then
    throw("BufferError", "memoryview: underlying buffer is not C-contiguous")
  end
  return buffer.data(value)
end

--- Build the identity byte translation table with the requested substitutions.
function M.bytes_maketrans(_I, args, kw)
  need_args("maketrans", args, 2, 2); no_kw("maketrans", kw)
  local from, to = bytes_like(args[1]), bytes_like(args[2])
  if #from ~= #to then throw("ValueError", "maketrans arguments must have same length") end
  local result = {}
  for byte = 0, 255 do result[byte + 1] = string.char(byte) end
  for i = 1, #from do result[from:byte(i) + 1] = to:sub(i, i) end
  return values.bytes(table.concat(result))
end

local function bytes_method(b, n)
  local data = b.b
  if n == "decode" then
    return function(_I, args, kw)
      need_args(n, args, 0, 2)
      local enc, errors = require("diaspore.encoding").arguments(args, kw)
      return H.decode_bytes(b, enc, errors, _I)
    end
  elseif n == "find" then
    return function(_I, args)
      need_args(n, args, 1, 1)
      if kindof(args[1]) ~= "bytes" then
        throw("TypeError", "argument should be a bytes-like object, not '"
          .. type_name(args[1]) .. "'")
      end
      local needle = args[1].b
      if needle == "" then return 0 end
      local at = data:find(needle, 1, true)
      if at == nil then return -1 end
      return at - 1
    end
  elseif n == "maketrans" then
    return M.bytes_maketrans
  elseif n == "join" then
    return function(I, args, kw)
      need_args(n, args, 1, 1); no_kw(n, kw)
      local items = iter.iter_to_array(I, args[1])
      local parts, size = {}, 0
      for i, item in ipairs(items) do
        if not buffer.is_buffer(item) or kindof(item) == "memoryview"
            and (item.released or item.length > 1 and item.stride ~= item.itemsize) then
          throw("TypeError", "sequence item " .. (i - 1) .. ": expected a bytes-like object, "
            .. type_name(item) .. " found")
        end
        local part = bytes_like(item)
        local added = #part
        if i > 1 then added = values.to_size(bigint.add(added, #data), "join() result is too long") end
        size = values.to_size(bigint.add(size, added), "join() result is too long")
        parts[i] = part
      end
      I:fuel_n(size)
      return values.bytes(table.concat(parts, data))
    end
  elseif n == "translate" then
    return function(I, args, kw)
      need_args(n, args, 1, 2)
      local deleted = args[2]
      for _, entry in ipairs(kw) do
        if entry.k ~= "delete" then throw("TypeError", "'" .. entry.k .. "' is an invalid keyword argument for translate()") end
        if deleted ~= nil then throw("TypeError", "translate() got multiple values for argument 'delete'") end
        deleted = entry.v
      end
      local translation = not is_none(args[1]) and bytes_like(args[1]) or nil
      if translation ~= nil and #translation ~= 256 then throw("ValueError", "translation table must be 256 characters long") end
      local removed = {}
      if deleted ~= nil then
        local chars = bytes_like(deleted)
        for i = 1, #chars do removed[chars:byte(i)] = true end
      end
      local parts = {}
      I:fuel_n(#data)
      for i = 1, #data do
        local byte = data:byte(i)
        if not removed[byte] then
          parts[#parts + 1] = translation and translation:sub(byte + 1, byte + 1) or data:sub(i, i)
        end
      end
      return values.bytes(table.concat(parts))
    end
  elseif n == "split" or n == "rsplit" then
    return function(_I, args, kw)
      local sep, maxsplit = split_arguments(n, args, kw)
      local parts
      if sep == nil or is_none(sep) then
        parts = bytes_fields(data, maxsplit, n == "rsplit")
      else
        parts = split_literal(data, bytes_like(sep), maxsplit, n == "rsplit")
      end
      local out = {}
      for i = 1, #parts do out[i] = values.bytes(parts[i]) end
      return values.list(out)
    end
  elseif n == "strip" or n == "lstrip" or n == "rstrip" then
    return function(_I, args)
      need_args(n, args, 0, 1)
      local set = {}
      if #args == 0 or is_none(args[1]) then
        for k in pairs(WS_BYTES) do set[k] = true end
      else
        local cut = args[1]
        if kindof(cut) ~= "bytes" then
          throw("TypeError", "a bytes-like object is required, not '"
            .. type_name(cut) .. "'")
        end
        for i = 1, #cut.b do set[cut.b:byte(i)] = true end
      end
      local lo, hi = 1, #data
      if n ~= "rstrip" then
        while lo <= hi and set[data:byte(lo)] do lo = lo + 1 end
      end
      if n ~= "lstrip" then
        while hi >= lo and set[data:byte(hi)] do hi = hi - 1 end
      end
      return values.bytes(data:sub(lo, hi))
    end
  elseif n == "replace" then
    return function(_I, args)
      need_args(n, args, 2, 3)
      local old, rep = args[1], args[2]
      if kindof(old) ~= "bytes" then
        throw("TypeError", "a bytes-like object is required, not '"
          .. type_name(old) .. "'")
      end
      if kindof(rep) ~= "bytes" then
        throw("TypeError", "a bytes-like object is required, not '"
          .. type_name(rep) .. "'")
      end
      local count = -1
      if #args == 3 then count = want_int("count", args[3]) end
      local out = {}
      local idx = 1
      local done = 0
      if #old.b > 0 then
        while true do
          if count >= 0 and done >= count then break end
          local at = data:find(old.b, idx, true)
          if at == nil then break end
          out[#out + 1] = data:sub(idx, at - 1)
          out[#out + 1] = rep.b
          idx = at + #old.b
          done = done + 1
        end
      end
      out[#out + 1] = data:sub(idx)
      return values.bytes(table.concat(out))
    end
  elseif n == "startswith" or n == "endswith" then
    return function(_I, args)
      need_args(n, args, 1, 1)
      local pats
      if kindof(args[1]) == "tuple" then
        pats = args[1].items
      else
        pats = { args[1] }
      end
      for _, p in ipairs(pats) do
        if kindof(p) ~= "bytes" then
          throw("TypeError", n .. " first arg must be bytes or a tuple of bytes, not "
            .. type_name(p))
        end
        local pb = p.b
        if #pb <= #data then
          if n == "startswith" then
            if data:sub(1, #pb) == pb then return true end
          else
            if pb == "" or data:sub(-#pb) == pb then return true end
          end
        end
      end
      return false
    end
  elseif n == "ljust" or n == "rjust" or n == "center" or n == "zfill" then
    return function(I, args, kw)
      need_args(n, args, 1, n == "zfill" and 1 or 2); no_kw(n, kw)
      local width = want_int("width", args[1])
      local fill = " "
      if args[2] ~= nil then
        local k = kindof(args[2])
        if (k ~= "bytes" and k ~= "bytearray") or #args[2].b ~= 1 then
          throw("TypeError", n .. "() argument 2 must be a byte string of length 1, not " .. type_name(args[2]))
        end
        fill = args[2].b
      end
      if width <= #data then return values.bytes(data) end
      local padding = width - #data
      I:fuel_n(width)
      if n == "zfill" then
        local first = data:sub(1, 1)
        if first == "+" or first == "-" then return values.bytes(first .. string.rep("0", padding) .. data:sub(2)) end
        return values.bytes(string.rep("0", padding) .. data)
      end
      if n == "ljust" then return values.bytes(data .. string.rep(fill, padding)) end
      if n == "rjust" then return values.bytes(string.rep(fill, padding) .. data) end
      local left = (padding >> 1) + (padding & width & 1)
      return values.bytes(string.rep(fill, left) .. data .. string.rep(fill, padding - left))
    end
  elseif n == "upper" or n == "lower" then
    return function()
      if n == "upper" then return values.bytes(data:upper()) end
      return values.bytes(data:lower())
    end
  end
  return nil
end

-- ---------------------------------------------------------------------------
-- entry point

--- Look up a method on a builtin value, or return nil.
function M.builtin_method(_I, o, n)
  local t = type(o)
  local k = t == "table" and o.kind or nil
  local fn = nil
  if (t == "number" or t == "boolean" or k == "bigint") and values.is_int_kind(o) then
    local value = values.as_index(o)
    if n == "to_bytes" then
      return values.builtin(n, function(I, args, kw) return buffer.to_bytes(I, value, args, kw) end)
    end
    if n == "bit_length" or n == "bit_count" or n == "conjugate" or n == "as_integer_ratio"
        or n == "is_integer" or n == "__int__" or n == "__index__" or n == "__float__"
        or n == "__trunc__" or n == "__floor__" or n == "__ceil__" then
      return values.builtin(n, function(_, args, kw)
        need_args(n, args, 0, 0); no_kw(n, kw)
        if n == "bit_length" then return bigint.bit_length(value) end
        if n == "bit_count" then return bigint.bit_count(value) end
        if n == "as_integer_ratio" then return values.tuple({ value, 1 }) end
        if n == "is_integer" then return true end
        if n == "__float__" then return bigint.to_float(value) end
        return value
      end)
    end
  end
  if t == "string" and (n == "isidentifier" or n == "isascii" or n == "isprintable") then
    return values.builtin(n, function(_, args, kw)
      need_args(n, args, 0, 0); no_kw(n, kw)
      if n == "isascii" then return values.is_ascii(o) end
      return require("diaspore.identifier")[n](o)
    end)
  end
  if t == "string" and (n == "format" or n == "format_map") then
    return values.builtin(n, function(I, args, kw)
      if n == "format_map" then
        need_args(n, args, 1, 1); no_kw(n, kw)
        return require("diaspore.interpolation").format(I, o, {}, {}, args[1], ops)
      end
      return require("diaspore.interpolation").format(I, o, args, kw, nil, ops)
    end)
  end
  if (k == "bytes" or k == "bytearray") and n == "maketrans" then
    return values.builtin(n, M.bytes_maketrans)
  end
  if k == "bytearray" or k == "memoryview" or k == "bytes" and n == "hex" then
    local attr = buffer.attr(_I, o, n)
    if attr ~= nil then return attr end
    if k == "bytearray" and bytes_method(values.bytes(o.b), n) ~= nil then
      return values.builtin(n, function(I, args, kw)
        local converted = {}
        for i, value in ipairs(args) do
          converted[i] = buffer.is_buffer(value) and n ~= "join" and n ~= "translate"
            and n ~= "split" and n ~= "rsplit" and n ~= "ljust" and n ~= "rjust" and n ~= "center"
            and values.bytes(buffer.data(value)) or value
        end
        local result = bytes_method(values.bytes(o.b), n)(I, converted, kw)
        if kindof(result) == "bytes" then return buffer.bytearray(result.b) end
        if kindof(result) == "list" then
          for i, value in ipairs(result.items) do
            if kindof(value) == "bytes" then result.items[i] = buffer.bytearray(value.b) end
          end
        end
        return result
      end)
    end
  end
  if t == "string" then
    fn = str_method(o, n)
  else
    if k == "list" then
      fn = list_method(o, n)
    elseif k == "dict" then
      fn = dict_method(o, n)
    elseif k == "mappingproxy" then
      if n == "keys" or n == "values" or n == "items" then
        return values.builtin(n, function(_I2, args, kw)
          need_args(n, args, 0, 0)
          no_kw(n, kw)
          return values.dict_view(o, n)
        end)
      end
      if n == "get" or n == "copy" then
        fn = function(I, args, kw)
          return dict_method(values.proxy_dict(o), n)(I, args, kw)
        end
      end
    elseif k == "set" then
      fn = set_method(o, n)
    elseif k == "tuple" then
      fn = tuple_method(o, n)
    elseif k == "bytes" then
      fn = bytes_method(o, n)
    elseif k == "dictview" and o.view ~= "values" and n == "isdisjoint" then
      fn = function(I, args, kw)
        need_args(n, args, 1, 1)
        no_kw(n, kw)
        local it = iter.get_iter(I, args[1])
        while true do
          local item, more = it.next()
          if not more then return true end
          if ops.contains(I, o, item) then return false end
        end
      end
    end
  end
  if fn == nil then
    throw("AttributeError", "'" .. type_name(o) .. "' object has no attribute '" .. n .. "'")
  end
  return values.builtin(n, function(I, args, kw)
    if k == "set" and o.frozen then no_kw(n, kw or {}) end
    return fn(I, args, kw or {})
  end)
end

-- Method-name sets per builtin type (for builtin-subclass attribute routing).
local function set_of(names)
  local s = {}
  for _, n in ipairs(names) do s[n] = true end
  return s
end

M.METHOD_SETS = {
  int = set_of({ "bit_length", "bit_count", "to_bytes", "conjugate", "as_integer_ratio",
    "is_integer", "__int__", "__index__", "__float__", "__trunc__", "__floor__", "__ceil__" }),
  str = set_of({ "format", "format_map", "upper", "lower", "title", "capitalize", "casefold", "strip",
    "isidentifier", "isascii", "isprintable",
    "maketrans", "translate",
    "lstrip", "rstrip", "split", "rsplit", "splitlines", "expandtabs", "join", "replace",
    "find", "rfind", "index", "count", "startswith", "endswith", "isdigit", "isdecimal",
    "isalpha", "isalnum", "isspace", "zfill", "ljust", "rjust", "encode",
    "partition", "rpartition", "removeprefix", "removesuffix", "center",
    "islower", "isupper", "swapcase" }),
  list = set_of({ "append", "extend", "insert", "pop", "remove", "clear",
    "index", "count", "reverse", "sort", "copy" }),
  dict = set_of({ "get", "keys", "values", "items", "pop", "update",
    "setdefault", "clear", "copy" }),
  set = set_of({ "add", "remove", "discard", "pop", "clear", "copy", "union",
    "intersection", "difference", "difference_update", "symmetric_difference", "issubset",
    "issuperset", "isdisjoint" }),
  frozenset = set_of({ "copy", "union", "intersection", "difference",
    "symmetric_difference", "issubset", "issuperset", "isdisjoint" }),
  tuple = set_of({ "count", "index" }),
  bytes = set_of({ "hex", "decode", "find", "split", "rsplit", "strip", "lstrip", "rstrip",
    "replace", "startswith", "endswith", "upper", "lower", "join", "maketrans", "translate",
    "ljust", "rjust", "center", "zfill" }),
  bytearray = set_of({ "hex", "decode", "find", "split", "rsplit", "strip", "lstrip", "rstrip",
    "replace", "startswith", "endswith", "upper", "lower", "join", "maketrans", "translate",
    "ljust", "rjust", "center", "zfill",
    "append", "extend", "insert", "clear", "copy", "reverse", "pop", "remove" }),
}

return M
