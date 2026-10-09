-- Methods on builtin types (spec section 9.1), implemented natively with CPython
-- semantics (code-point string indexing, Python split/strip/title rules).

local values = require("diaspore.values")
local format = require("diaspore.format")
local iter = require("diaspore.iter")
local ops = require("diaspore.ops")
local H = require("diaspore.helpers")

local M = {}

local NONE = values.NONE
local throw = values.throw
local type_name = values.type_name
local chars = values.chars
local as_index = values.as_index
local need_args = H.need_args
local no_kw = H.no_kw
local want_int = H.want_int
local want_str = H.want_str

local function is_none(v)
  return type(v) == "table" and v.kind == "none"
end

local function kindof(v)
  if type(v) == "table" then return v.kind end
  return nil
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
  if maxsplit < 0 or #fields - 1 <= maxsplit then return fields end
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

local function split_lines(s)
  local out = {}
  local cur = {}
  local cs = chars(s)
  local i = 1
  while i <= #cs do
    local ch = cs[i]
    local cp = utf8.codepoint(ch, 1, 1, true)
    if LINE_BREAK_CPS[cp] then
      out[#out + 1] = table.concat(cur)
      cur = {}
    elseif cp == 0x0D then
      if i + 1 <= #cs and cs[i + 1] == "\n" then i = i + 1 end
      out[#out + 1] = table.concat(cur)
      cur = {}
    else
      cur[#cur + 1] = ch
    end
    i = i + 1
  end
  if #cur > 0 then out[#out + 1] = table.concat(cur) end
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
  local function adjust(v, def)
    if is_none(v) then return def end
    local x = as_index(v)
    if x == nil then
      throw("TypeError",
        "slice indices must be integers or None or have an __index__ method")
    end
    if x < 0 then
      x = x + n
      if x < 0 then x = 0 end
    elseif x > n then
      x = n
    end
    return x
  end
  if #args >= from then start = adjust(args[from], 0) end
  if #args >= from + 1 then stop = adjust(args[from + 1], n) end
  if stop < start then stop = start end
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
  if n == "upper" then
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
      no_kw(n, kw)
      need_args(n, args, 0, 2)
      local maxsplit = -1
      if #args >= 2 then maxsplit = want_int("maxsplit", args[2]) end
      local sep = nil
      if #args >= 1 and not is_none(args[1]) then
        sep = want_str("sep", args[1])
        if sep == "" then throw("ValueError", "empty separator") end
      end
      local parts
      if sep == nil then
        parts = split_whitespace(s, maxsplit, n == "rsplit")
      elseif n == "split" then
        parts = {}
        local rest_from = 1
        while maxsplit < 0 or #parts < maxsplit do
          local at = s:find(sep, rest_from, true)
          if at == nil then break end
          parts[#parts + 1] = s:sub(rest_from, at - 1)
          rest_from = at + #sep
        end
        parts[#parts + 1] = s:sub(rest_from)
      else -- rsplit with sep
        local all = {}
        local from = 1
        while true do
          local at = s:find(sep, from, true)
          if at == nil then break end
          all[#all + 1] = s:sub(from, at - 1)
          from = at + #sep
        end
        all[#all + 1] = s:sub(from)
        if maxsplit >= 0 and #all - 1 > maxsplit then
          local keep = #all - maxsplit
          local head = table.concat(all, sep, 1, keep)
          parts = { head }
          for i = keep + 1, #all do parts[#parts + 1] = all[i] end
        else
          parts = all
        end
      end
      return str_list(parts)
    end
  elseif n == "splitlines" then
    return function(_I, args, kw)
      no_kw(n, kw)
      return str_list(split_lines(s))
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
          if region:sub(1, #p) == p then return true end
        else
          if p == "" or region:sub(-#p) == p then return true end
        end
      end
      return false
    end
  elseif n == "isdigit" or n == "isalpha" or n == "isalnum" or n == "isspace" then
    return function(_I, args, kw)
      no_kw(n, kw)
      if s == "" then return false end
      for _, cp in utf8.codes(s, true) do
        local ok
        if n == "isdigit" then
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
      local pad = w - #chars(s)
      if pad <= 0 then return s end
      if n == "ljust" then return s .. string.rep(fill, pad) end
      return string.rep(fill, pad) .. s
    end
  elseif n == "encode" then
    return function(_I, args, kw)
      need_args(n, args, 0, 1)
      local enc = "utf-8"
      if #args == 1 then enc = want_str("encoding", args[1]) end
      local kv = H.kw_get(kw, "encoding")
      if kv ~= nil then enc = want_str("encoding", kv) end
      return H.encode_str_to(s, enc)
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
      local marg = w - #chars(s)
      if marg <= 0 then return s end
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
    start = want_int("start", args[2])
    if start < 0 then
      start = start + #items
      if start < 0 then start = 0 end
    end
  end
  if #args >= 3 then
    stop = want_int("stop", args[3])
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

local function bytes_fields(data)
  local out = {}
  local i = 1
  local n = #data
  while i <= n do
    while i <= n and WS_BYTES[data:byte(i)] do i = i + 1 end
    if i > n then break end
    local j = i
    while j <= n and not WS_BYTES[data:byte(j)] do j = j + 1 end
    out[#out + 1] = data:sub(i, j - 1)
    i = j
  end
  return out
end

local function bytes_method(b, n)
  local data = b.b
  if n == "decode" then
    return function(_I, args, kw)
      need_args(n, args, 0, 1)
      local enc = "utf-8"
      if #args == 1 then enc = want_str("encoding", args[1]) end
      local kv = H.kw_get(kw, "encoding")
      if kv ~= nil then enc = want_str("encoding", kv) end
      return H.decode_bytes(b, enc)
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
  elseif n == "split" then
    return function(_I, args)
      need_args(n, args, 0, 2)
      local parts
      if #args == 0 or is_none(args[1]) then
        parts = bytes_fields(data)
      else
        local sep = args[1]
        if kindof(sep) ~= "bytes" then
          throw("TypeError", "a bytes-like object is required, not '"
            .. type_name(sep) .. "'")
        end
        if sep.b == "" then throw("ValueError", "empty separator") end
        parts = {}
        local from = 1
        while true do
          local at = data:find(sep.b, from, true)
          if at == nil then break end
          parts[#parts + 1] = data:sub(from, at - 1)
          from = at + #sep.b
        end
        parts[#parts + 1] = data:sub(from)
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
  local fn = nil
  if type(o) == "string" then
    fn = str_method(o, n)
  else
    local k = kindof(o)
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
          return dict_method(values.namespace_dict(o.cls), n)(I, args, kw)
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
    if kindof(o) == "set" and o.frozen then no_kw(n, kw or {}) end
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
  str = set_of({ "upper", "lower", "title", "capitalize", "casefold", "strip",
    "lstrip", "rstrip", "split", "rsplit", "splitlines", "join", "replace",
    "find", "rfind", "index", "count", "startswith", "endswith", "isdigit",
    "isalpha", "isalnum", "isspace", "zfill", "ljust", "rjust", "encode",
    "partition", "rpartition", "removeprefix", "removesuffix", "center",
    "islower", "isupper", "swapcase" }),
  list = set_of({ "append", "extend", "insert", "pop", "remove", "clear",
    "index", "count", "reverse", "sort", "copy" }),
  dict = set_of({ "get", "keys", "values", "items", "pop", "update",
    "setdefault", "clear", "copy" }),
  set = set_of({ "add", "remove", "discard", "clear", "copy", "union",
    "intersection", "difference", "symmetric_difference", "issubset",
    "issuperset", "isdisjoint" }),
  frozenset = set_of({ "copy", "union", "intersection", "difference",
    "symmetric_difference", "issubset", "issuperset", "isdisjoint" }),
  tuple = set_of({ "count", "index" }),
  bytes = set_of({ "decode", "find", "split", "strip", "lstrip", "rstrip",
    "replace", "startswith", "endswith", "upper", "lower" }),
}

return M
