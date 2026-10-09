-- JSON parsing and CPython-parity serialization (spec section 11.3).
--
-- The parser produces "raw" Lua values used both for the bundle IR and (after
-- conversion) for json.loads:
--   object -> table  key -> raw value, plus [json.ORDER] = array of keys
--   array  -> plain array table, plus [json.ARRAY] = true
--   null   -> values.NULL     numbers -> Lua int (integral literal) or float

local values = require("diaspore.values")

local M = {}

M.ORDER = setmetatable({}, { __tostring = function() return "<order>" end })
M.ARRAY = setmetatable({}, { __tostring = function() return "<array>" end })

-- ---------------------------------------------------------------------------
-- parse

local Parser = {}
Parser.__index = Parser

local function fail(msg)
  values.throw("ValueError", "invalid JSON: " .. msg)
end

function Parser.new(s)
  return setmetatable({ s = s, i = 1, n = #s }, Parser)
end

function Parser:skip_ws()
  local s, i, n = self.s, self.i, self.n
  while i <= n do
    local c = s:byte(i)
    if c == 32 or c == 9 or c == 10 or c == 13 then
      i = i + 1
    else
      break
    end
  end
  self.i = i
end

function Parser:lit(word)
  if self.s:sub(self.i, self.i + #word - 1) == word then
    self.i = self.i + #word
    return true
  end
  return false
end

function Parser:value()
  if self.i > self.n then fail("Expecting value") end
  local c = self.s:sub(self.i, self.i)
  if c == "{" then return self:object() end
  if c == "[" then return self:array() end
  if c == '"' then return self:parse_string() end
  if c == "t" and self:lit("true") then return true end
  if c == "f" and self:lit("false") then return false end
  if c == "n" and self:lit("null") then return values.NULL end
  if c == "N" and self:lit("NaN") then return 0 / 0 end
  if c == "I" and self:lit("Infinity") then return math.huge end
  if c == "-" or (c >= "0" and c <= "9") then return self:number() end
  fail("Expecting value")
end

function Parser:number()
  local s = self.s
  local start = self.i
  if s:sub(self.i, self.i) == "-" then
    self.i = self.i + 1
    if self:lit("Infinity") then return -math.huge end
  end
  local is_float = false
  while self.i <= self.n do
    local c = s:sub(self.i, self.i)
    if c >= "0" and c <= "9" then
      self.i = self.i + 1
    elseif c == "." or c == "e" or c == "E" or c == "+" or c == "-" then
      if c == "." or c == "e" or c == "E" then is_float = true end
      self.i = self.i + 1
    else
      break
    end
  end
  local text = s:sub(start, self.i - 1)
  local v = tonumber(text)
  if v == nil then fail("Expecting value") end
  if is_float then return v + 0.0 end
  local iv = math.tointeger(v)
  if iv ~= nil then return iv end
  return v + 0.0 -- integer literal beyond int64: keep as float
end

function Parser:parse_string()
  local s = self.s
  self.i = self.i + 1 -- opening quote
  local parts = {}
  local runstart = self.i
  while true do
    if self.i > self.n then fail("Unterminated string starting at") end
    local c = s:sub(self.i, self.i)
    if c == '"' then
      parts[#parts + 1] = s:sub(runstart, self.i - 1)
      self.i = self.i + 1
      return table.concat(parts)
    end
    if c == "\\" then
      parts[#parts + 1] = s:sub(runstart, self.i - 1)
      self.i = self.i + 1
      if self.i > self.n then fail("Unterminated string starting at") end
      local e = s:sub(self.i, self.i)
      if e == '"' then
        parts[#parts + 1] = '"'
      elseif e == "\\" then
        parts[#parts + 1] = "\\"
      elseif e == "/" then
        parts[#parts + 1] = "/"
      elseif e == "b" then
        parts[#parts + 1] = "\b"
      elseif e == "f" then
        parts[#parts + 1] = "\f"
      elseif e == "n" then
        parts[#parts + 1] = "\n"
      elseif e == "r" then
        parts[#parts + 1] = "\r"
      elseif e == "t" then
        parts[#parts + 1] = "\t"
      elseif e == "u" then
        local r = self:u_escape()
        if r >= 0xD800 and r <= 0xDBFF and s:sub(self.i + 1, self.i + 2) == "\\u" then
          local save = self.i
          self.i = self.i + 2
          local r2 = self:u_escape()
          if r2 >= 0xDC00 and r2 <= 0xDFFF then
            r = 0x10000 + ((r - 0xD800) << 10) + (r2 - 0xDC00)
          else
            self.i = save
          end
        end
        parts[#parts + 1] = utf8.char(r)
      else
        fail("Invalid \\escape")
      end
      self.i = self.i + 1
      runstart = self.i
    else
      self.i = self.i + 1
    end
  end
end

-- Parses the 4 hex digits after \u; on entry self.i is at 'u'.
function Parser:u_escape()
  local hex = self.s:sub(self.i + 1, self.i + 4)
  if #hex < 4 or not hex:match("^%x%x%x%x$") then fail("Invalid \\uXXXX escape") end
  self.i = self.i + 4
  return tonumber(hex, 16)
end

function Parser:array()
  self.i = self.i + 1 -- '['
  local out = { [M.ARRAY] = true }
  self:skip_ws()
  if self.i <= self.n and self.s:sub(self.i, self.i) == "]" then
    self.i = self.i + 1
    return out
  end
  while true do
    self:skip_ws()
    out[#out + 1] = self:value()
    self:skip_ws()
    if self.i > self.n then fail("Expecting ',' delimiter") end
    local c = self.s:sub(self.i, self.i)
    if c == "," then
      self.i = self.i + 1
    elseif c == "]" then
      self.i = self.i + 1
      return out
    else
      fail("Expecting ',' delimiter")
    end
  end
end

function Parser:object()
  self.i = self.i + 1 -- '{'
  local order = {}
  local out = { [M.ORDER] = order }
  self:skip_ws()
  if self.i <= self.n and self.s:sub(self.i, self.i) == "}" then
    self.i = self.i + 1
    return out
  end
  while true do
    self:skip_ws()
    if self.i > self.n or self.s:sub(self.i, self.i) ~= '"' then
      fail("Expecting property name enclosed in double quotes")
    end
    local k = self:parse_string()
    self:skip_ws()
    if self.i > self.n or self.s:sub(self.i, self.i) ~= ":" then
      fail("Expecting ':' delimiter")
    end
    self.i = self.i + 1
    self:skip_ws()
    if out[k] == nil then order[#order + 1] = k end
    out[k] = self:value()
    self:skip_ws()
    if self.i > self.n then fail("Expecting ',' delimiter") end
    local c = self.s:sub(self.i, self.i)
    if c == "," then
      self.i = self.i + 1
    elseif c == "}" then
      self.i = self.i + 1
      return out
    else
      fail("Expecting ',' delimiter")
    end
  end
end

--- Parse JSON text into raw form (see header).
function M.parse(s)
  local p = Parser.new(s)
  p:skip_ws()
  local v = p:value()
  p:skip_ws()
  if p.i <= p.n then fail("Extra data") end
  return v
end

--- Convert a raw parsed value into diaspore values (for json.loads).
function M.to_diaspore(v)
  if v == values.NULL then return values.NONE end
  local t = type(v)
  if t ~= "table" then return v end
  if v[M.ARRAY] then
    local items = {}
    for i = 1, #v do items[i] = M.to_diaspore(v[i]) end
    return values.list(items)
  end
  local d = values.new_dict()
  local order = v[M.ORDER]
  for i = 1, #order do
    local k = order[i]
    values.dict_set_hk(d, "s" .. k, k, M.to_diaspore(v[k]))
  end
  return d
end

-- ---------------------------------------------------------------------------
-- dumps (diaspore values -> JSON text with CPython separators and \uXXXX escapes)

local format -- lazy: avoid load cycle
local function py_float_str(f)
  if format == nil then format = require("diaspore.format") end
  return format.py_float_str(f)
end

--- CPython json's default ensure_ascii=True escaping.
function M.escape_string(s)
  local b = { '"' }
  for _, cp in utf8.codes(s, true) do
    if cp == 0x22 then
      b[#b + 1] = '\\"'
    elseif cp == 0x5C then
      b[#b + 1] = "\\\\"
    elseif cp == 0x0A then
      b[#b + 1] = "\\n"
    elseif cp == 0x0D then
      b[#b + 1] = "\\r"
    elseif cp == 0x09 then
      b[#b + 1] = "\\t"
    elseif cp == 0x08 then
      b[#b + 1] = "\\b"
    elseif cp == 0x0C then
      b[#b + 1] = "\\f"
    elseif cp < 0x20 then
      b[#b + 1] = string.format("\\u%04x", cp)
    elseif cp < 0x80 then
      b[#b + 1] = string.char(cp)
    elseif cp > 0xFFFF then
      local v = cp - 0x10000
      b[#b + 1] = string.format("\\u%04x\\u%04x", 0xD800 + (v >> 10), 0xDC00 + (v & 0x3FF))
    else
      b[#b + 1] = string.format("\\u%04x", cp)
    end
  end
  b[#b + 1] = '"'
  return table.concat(b)
end

local function write_value(b, v, indent, has_indent, depth)
  local function pad(d)
    if has_indent then b[#b + 1] = "\n" .. string.rep(" ", indent * d) end
  end
  local item_sep = has_indent and "," or ", "
  local t = type(v)
  if t == "boolean" then
    b[#b + 1] = v and "true" or "false"
    return
  end
  if t == "number" then
    if math.type(v) == "integer" then
      b[#b + 1] = tostring(v)
    elseif v == math.huge then
      b[#b + 1] = "Infinity"
    elseif v == -math.huge then
      b[#b + 1] = "-Infinity"
    elseif v ~= v then
      b[#b + 1] = "NaN"
    else
      b[#b + 1] = py_float_str(v)
    end
    return
  end
  if t == "string" then
    b[#b + 1] = M.escape_string(v)
    return
  end
  local k = v.kind
  if k == "none" then
    b[#b + 1] = "null"
    return
  end
  if k == "list" or k == "tuple" then
    local items = v.items
    if #items == 0 then
      b[#b + 1] = "[]"
      return
    end
    b[#b + 1] = "["
    for i = 1, #items do
      if i > 1 then b[#b + 1] = item_sep end
      pad(depth + 1)
      write_value(b, items[i], indent, has_indent, depth + 1)
    end
    pad(depth)
    b[#b + 1] = "]"
    return
  end
  if k == "dict" then
    if v.size == 0 then
      b[#b + 1] = "{}"
      return
    end
    b[#b + 1] = "{"
    local entries = values.dict_entries(v)
    for i = 1, #entries do
      if i > 1 then b[#b + 1] = item_sep end
      pad(depth + 1)
      local key = entries[i].k
      if type(key) ~= "string" then
        values.throw("TypeError", "json keys must be str")
      end
      b[#b + 1] = M.escape_string(key)
      b[#b + 1] = ": "
      write_value(b, entries[i].v, indent, has_indent, depth + 1)
    end
    pad(depth)
    b[#b + 1] = "}"
    return
  end
  values.throw("TypeError",
    "Object of type " .. values.type_name(v) .. " is not JSON serializable")
end

--- dumps(v, indent): indent is a diaspore value (NONE or int).
function M.dumps(v, indent_v)
  local has_indent = false
  local indent = 0
  if not (type(indent_v) == "table" and indent_v.kind == "none") then
    local n = values.as_index(indent_v)
    if n == nil then values.throw("TypeError", "indent must be an int or None") end
    has_indent = true
    if n > 0 then indent = n end
  end
  local b = {}
  write_value(b, v, indent, has_indent, 0)
  return table.concat(b)
end

return M
