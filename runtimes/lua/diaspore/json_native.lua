-- CPython 3.12 _json accelerator over sandbox values. Public json stays bundled.
local V = require("diaspore.values")
local H = require("diaspore.helpers")
local J = require("diaspore.json")
local F = require("diaspore.format")
local Iter = require("diaspore.iter")
local Big = require("diaspore.bigint")
local pcall = require("diaspore.protected").call
local M = {}
local NONE = V.NONE
local LITERALS = { { "null", NONE }, { "true", true }, { "false", false } }
local CONSTANTS = { "NaN", "Infinity", "-Infinity" }

local function arguments(name, args, kwargs, names, required)
  H.arg_count(name, args, 0, #names)
  local result = {}
  for i, value in ipairs(args) do result[i] = value end
  for _, entry in ipairs(kwargs) do
    local index
    for i, field in ipairs(names) do if field == entry.k then index = i; break end end
    if index == nil then V.throw("TypeError", name .. "() got an unexpected keyword argument '" .. entry.k .. "'") end
    if result[index] ~= nil then V.throw("TypeError", name .. "() got multiple values for argument '" .. entry.k .. "'") end
    result[index] = entry.v
  end
  for i = 1, required do
    if result[i] == nil then V.throw("TypeError", name .. "() missing required argument '" .. names[i] .. "'") end
  end
  return result
end

local function kindof(value)
  return type(value) == "table" and value.kind or nil
end

local function integer(I, value)
  local raw = I:raw(value)
  local result = Big.is_bigint(raw) and raw or V.as_index(raw)
  if result == nil and kindof(value) == "instance" and V.class_lookup(value.cls, "__index__") then
    local indexed = I:call_dunder(value, "__index__", {})
    result = Big.is_bigint(indexed) and indexed or V.as_index(indexed)
  end
  if result == nil then V.throw("TypeError", "'" .. V.type_name(value) .. "' object cannot be interpreted as an integer") end
  if Big.is_bigint(result) then
    if Big.cmp(result, math.mininteger) < 0 or Big.cmp(result, math.maxinteger) > 0 then
      V.throw("OverflowError", "Python int too large to convert to C ssize_t")
    end
    return tonumber(Big.tostring(result))
  end
  return result
end

local function text(I, value)
  local raw = I:raw(value)
  if type(raw) ~= "string" then
    V.throw("TypeError", "first argument must be a string, not " .. V.type_name(value))
  end
  return raw
end

local function stop(position)
  local exception = V.make_exc("StopIteration", position)
  exception.d.value = position
  V.throw_val(exception)
end

local escapes = { ['"'] = '\\"', ['\\'] = '\\\\', ['\b'] = '\\b', ['\f'] = '\\f',
  ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }
local unescapes = { ['"'] = '"', ['\\'] = '\\', ['/'] = '/', b = '\b', f = '\f',
  n = '\n', r = '\r', t = '\t' }

local function escape(I, value, ascii)
  local source = text(I, value)
  I:fuel_n(#source // 16 + 1)
  if ascii then return J.escape_string(source) end
  return '"' .. source:gsub('[%z\1-\31\\"]', function(character)
    return escapes[character] or string.format("\\u%04x", character:byte())
  end) .. '"'
end

local Parser = {}
Parser.__index = Parser

local function parser(I, source, index, context, fail)
  I:fuel_n(#source // 16 + 1)
  local byte, length = #source + 1, 0
  for offset in utf8.codes(source, true) do
    if length == index then byte = offset; break end
    length = length + 1
  end
  return setmetatable({ I = I, source = source, byte = byte, position = index,
    available = byte <= #source or length == index, context = context, fail = fail }, Parser)
end

function Parser:move(count)
  self.byte, self.position = self.byte + count, self.position + count
end

function Parser:space()
  local source, byte = self.source, self.byte
  while true do
    local c = source:byte(byte)
    if c ~= 32 and c ~= 9 and c ~= 10 and c ~= 13 then break end
    byte = byte + 1
  end
  self:move(byte - self.byte)
end

function Parser:hex(byte, position)
  local digits = self.source:sub(byte + 1, byte + 4)
  if byte + 5 > #self.source or #digits ~= 4 or digits:find('[^0-9a-fA-F]') then
    self.fail("Invalid \\uXXXX escape", self.source, position)
  end
  return tonumber(digits, 16)
end

function Parser:string(strict)
  local begin, chunks, start = self.position - 1, {}, self.byte
  while self.byte <= #self.source do
    local c = self.source:byte(self.byte)
    if c == 34 then
      chunks[#chunks + 1] = self.source:sub(start, self.byte - 1)
      self:move(1)
      return table.concat(chunks)
    elseif c == 92 then
      chunks[#chunks + 1] = self.source:sub(start, self.byte - 1)
      self:move(1)
      local character = self.source:sub(self.byte, self.byte)
      if character == "" then self.fail("Unterminated string starting at", self.source, begin) end
      if character == "u" then
        local cp = self:hex(self.byte, self.position)
        self:move(5)
        if cp >= 0xd800 and cp <= 0xdbff and self.source:sub(self.byte, self.byte + 1) == "\\u"
            and self.byte + 6 <= #self.source then
          local second = self:hex(self.byte + 1, self.position + 1)
          if second >= 0xdc00 and second <= 0xdfff then
            cp = 0x10000 + (cp - 0xd800) * 0x400 + second - 0xdc00
            self:move(6)
          end
        end
        chunks[#chunks + 1] = utf8.char(cp)
      else
        local decoded = unescapes[character]
        if decoded == nil then self.fail("Invalid \\escape", self.source, self.position - 1) end
        chunks[#chunks + 1] = decoded
        self:move(1)
      end
      start = self.byte
    else
      if c < 32 and strict then self.fail("Invalid control character at", self.source, self.position) end
      self.byte = self.byte + (c < 0x80 and 1 or c < 0xe0 and 2 or c < 0xf0 and 3 or 4)
      self.position = self.position + 1
    end
  end
  self.fail("Unterminated string starting at", self.source, begin)
end

local function digit(c)
  return c ~= nil and c >= 48 and c <= 57
end

function Parser:number()
  local source, start = self.source, self.byte
  local byte = start
  if source:byte(byte) == 45 then byte = byte + 1 end
  local first = source:byte(byte)
  if first == 48 then byte = byte + 1
  elseif first and first >= 49 and first <= 57 then
    repeat byte = byte + 1 until not digit(source:byte(byte))
  else stop(self.position) end
  local floating = false
  if source:byte(byte) == 46 and digit(source:byte(byte + 1)) then
    floating, byte = true, byte + 2
    while digit(source:byte(byte)) do byte = byte + 1 end
  end
  if source:byte(byte) == 69 or source:byte(byte) == 101 then
    local exponent = byte + 1
    if source:byte(exponent) == 43 or source:byte(exponent) == 45 then exponent = exponent + 1 end
    if digit(source:byte(exponent)) then
      floating, byte = true, exponent + 1
      while digit(source:byte(byte)) do byte = byte + 1 end
    end
  end
  local token = source:sub(start, byte - 1)
  self:move(byte - start)
  local hook = self.context.parse_int
  if floating then hook = self.context.parse_float end
  return self.I:call(hook, { token }, {})
end

function Parser:scan(depth)
  self.I:fuel()
  local source, byte, context = self.source, self.byte, self.context
  local c = source:byte(byte)
  if c == 34 then self:move(1); return self:string(context.strict) end
  if c == 123 or c == 91 then
    if depth + self.I.depth >= self.I.recursion_limit then
      V.throw("RecursionError", "maximum recursion depth exceeded while decoding a JSON object")
    end
    self:move(1); self:space()
    local object = c == 123
    local closing = object and 125 or 93
    local pairs_hook = object and context.object_pairs_hook ~= NONE
    local result = object and not pairs_hook and V.new_dict() or V.list({})
    if source:byte(self.byte) ~= closing then
      while true do
        local key
        if object then
          if source:byte(self.byte) ~= 34 then
            self.fail("Expecting property name enclosed in double quotes", source, self.position)
          end
          self:move(1)
          key = self:string(context.strict)
          context.memo[key] = context.memo[key] or key
          key = context.memo[key]
          self:space()
          if source:byte(self.byte) ~= 58 then self.fail("Expecting ':' delimiter", source, self.position) end
          self:move(1); self:space()
        end
        local value = self:scan(depth + 1)
        if pairs_hook then result.items[#result.items + 1] = V.tuple({ key, value })
        elseif object then V.dict_set(result, key, value)
        else result.items[#result.items + 1] = value end
        self:space()
        if source:byte(self.byte) == closing then break end
        if source:byte(self.byte) ~= 44 then self.fail("Expecting ',' delimiter", source, self.position) end
        self:move(1); self:space()
      end
    end
    self:move(1)
    if pairs_hook then return self.I:call(context.object_pairs_hook, { result }, {}) end
    if object and context.object_hook ~= NONE then return self.I:call(context.object_hook, { result }, {}) end
    return result
  end
  for _, literal in ipairs(LITERALS) do
    if source:sub(byte, byte + #literal[1] - 1) == literal[1] then
      self:move(#literal[1]); return literal[2]
    end
  end
  for _, constant in ipairs(CONSTANTS) do
    if source:sub(byte, byte + #constant - 1) == constant then
      self:move(#constant)
      return self.I:call(context.parse_constant, { constant }, {})
    end
  end
  return self:number()
end

local function encoder(I, options)
  local markers = options.markers ~= NONE and I:raw(options.markers) or nil
  local function mark(value)
    if markers == nil then return nil end
    local id = V.obj_id(value)
    if V.dict_entry(markers, id) then V.throw("ValueError", "Circular reference detected") end
    V.dict_set(markers, id, value)
    return id
  end
  local function unmark(id)
    if id ~= nil and V.dict_del(markers, id) == nil then V.throw("KeyError", id) end
  end
  local function string_value(value)
    if options.fast_ascii ~= nil then return escape(I, value, options.fast_ascii) end
    local result = I:call(options.encoder, { value }, {})
    local raw = I:raw(result)
    if type(raw) ~= "string" then V.throw("TypeError", "encoder() must return a string, not " .. V.type_name(result)) end
    return raw
  end
  local function float_value(value, original)
    if value ~= value or value == math.huge or value == -math.huge then
      if not options.allow_nan then
        V.throw("ValueError", "Out of range float values are not JSON compliant: " .. I:repr(original))
      end
      return value ~= value and "NaN" or value > 0 and "Infinity" or "-Infinity"
    end
    return F.py_float_str(value)
  end
  local function key_value(value)
    local raw = I:raw(value)
    if type(raw) == "string" then return value end
    if raw == NONE then return "null" end
    if type(raw) == "boolean" then return raw and "true" or "false" end
    if Big.is_bigint(raw) then return Big.tostring(raw) end
    if type(raw) == "number" then
      return math.type(raw) == "integer" and tostring(raw) or float_value(raw, value)
    end
    if options.skipkeys then return nil end
    V.throw("TypeError", "keys must be str, int, float, bool or None, not " .. V.type_name(value))
  end
  local encode
  encode = function(value, output, depth)
    I:fuel()
    local raw = I:raw(value)
    local kind = kindof(raw)
    if raw == NONE then output[#output + 1] = "null"
    elseif type(raw) == "boolean" then output[#output + 1] = raw and "true" or "false"
    elseif type(raw) == "string" then output[#output + 1] = string_value(value)
    elseif Big.is_bigint(raw) then output[#output + 1] = Big.tostring(raw)
    elseif type(raw) == "number" then
      output[#output + 1] = math.type(raw) == "integer" and tostring(raw) or float_value(raw, value)
    else
      if depth + I.depth >= I.recursion_limit then V.throw("RecursionError", "maximum recursion depth exceeded while encoding a JSON object") end
      if kind == "list" or kind == "tuple" then
        local items = kindof(value) == "instance" and Iter.iter_to_array(I, value) or raw.items
        if #items == 0 then output[#output + 1] = "[]"; return end
        local id = mark(value)
        output[#output + 1] = "["
        for i, item in ipairs(items) do
          if i > 1 then output[#output + 1] = options.item_separator end
          encode(item, output, depth + 1)
        end
        output[#output + 1] = "]"
        unmark(id)
      elseif kind == "dict" then
        if raw.size == 0 then output[#output + 1] = "{}"; return end
        local id = mark(value)
        local entries
        if kindof(value) == "instance" then
          entries = Iter.iter_to_array(I, I:call(I:get_attr(value, "items"), {}, {}))
        elseif options.sort_keys then
          entries = {}
          for _, entry in ipairs(V.dict_entries(raw)) do entries[#entries + 1] = V.tuple({ entry.k, entry.v }) end
        end
        if entries and options.sort_keys then entries = I:call(I.builtins.sorted, { V.list(entries) }, {}).items end
        output[#output + 1] = "{"
        local first = true
        local function pair(key, item)
          local converted = key_value(key)
          if converted == nil then return end
          if not first then output[#output + 1] = options.item_separator end
          first = false
          output[#output + 1] = string_value(converted)
          output[#output + 1] = options.key_separator
          encode(item, output, depth + 1)
        end
        if entries then
          for _, entry in ipairs(entries) do
            local raw_entry = I:raw(entry)
            if kindof(raw_entry) ~= "tuple" or #raw_entry.items ~= 2 then V.throw("ValueError", "items must return 2-tuples") end
            pair(raw_entry.items[1], raw_entry.items[2])
          end
        else
          for _, entry in ipairs(V.dict_entries(raw)) do pair(entry.k, entry.v) end
        end
        output[#output + 1] = "}"
        unmark(id)
      else
        local id = mark(value)
        local replacement = I:call(options.default, { value }, {})
        encode(replacement, output, depth + 1)
        unmark(id)
      end
    end
  end
  return function(_, args, kwargs)
    local bound = arguments("_iterencode", args, kwargs, { "obj", "_current_indent_level" }, 2)
    integer(I, bound[2])
    local output = {}
    encode(bound[1], output, 0)
    return V.tuple({ table.concat(output) })
  end
end

--- Create the private native module used by bundled CPython json.
function M.make_module(I)
  local namespace = {}
  local function fail(message, source, position)
    local error_class = I:get_attr(I:import_module("json.decoder"), "JSONDecodeError")
    V.throw_val(I:call(error_class, { message, source, position }, {}))
  end
  for _, name in ipairs({ "encode_basestring", "encode_basestring_ascii" }) do
    namespace[name] = V.builtin(name, function(_, args, kwargs)
      H.arg_count(name, args, 1, 1); H.no_kw(name, kwargs)
      return escape(I, args[1], name == "encode_basestring_ascii")
    end)
  end
  namespace.scanstring = V.builtin("scanstring", function(_, args, kwargs)
    H.arg_count("scanstring", args, 2, 3); H.no_kw("scanstring", kwargs)
    local source, index = text(I, args[1]), integer(I, args[2])
    if index < 0 then V.throw("ValueError", "end is out of bounds") end
    local state = parser(I, source, index, nil, fail)
    if not state.available then V.throw("ValueError", "end is out of bounds") end
    local strict = args[3] == nil or I:truth(args[3])
    local value = state:string(strict)
    return V.tuple({ value, state.position })
  end)
  local scanner_type
  scanner_type = V.btype("Scanner", function(_, args, kwargs)
    local bound = arguments("make_scanner", args, kwargs, { "context" }, 1)
    local options = { memo = {} }
    local properties = {}
    for _, name in ipairs({ "strict", "object_hook", "object_pairs_hook", "parse_float", "parse_int", "parse_constant" }) do
      local value = I:get_attr(bound[1], name)
      if name == "strict" then value = I:truth(value) end
      options[name], properties[name] = value, value
    end
    return { kind = "nativeobject", nkind = "_json.Scanner", pytype = scanner_type, props = properties,
      methods = { __call__ = V.builtin("scan_once", function(_, call_args, call_kwargs)
        local call = arguments("scan_once", call_args, call_kwargs, { "string", "idx" }, 2)
        local source, index = text(I, call[1]), integer(I, call[2])
        if index < 0 then V.throw("ValueError", "idx cannot be negative") end
        local state = parser(I, source, index, options, fail)
        local ok, result = pcall(function()
          local value = state:scan(0)
          return V.tuple({ value, state.position })
        end)
        options.memo = {}
        if not ok then error(result, 0) end
        return result
      end) } }
  end)
  scanner_type.ns.__module__ = "_json"
  namespace.make_scanner = scanner_type
  local encoder_type
  encoder_type = V.btype("Encoder", function(_, args, kwargs)
    local names = { "markers", "default", "encoder", "indent", "key_separator", "item_separator", "sort_keys", "skipkeys", "allow_nan" }
    local bound = arguments("make_encoder", args, kwargs, names, 9)
    if bound[1] ~= NONE and kindof(I:raw(bound[1])) ~= "dict" then
      V.throw("TypeError", "make_encoder() argument 1 must be dict or None, not " .. V.type_name(bound[1]))
    end
    local options, properties = {}, {}
    for i, name in ipairs(names) do
      local value = bound[i]
      if i >= 7 then value = I:truth(value)
      elseif i == 5 or i == 6 then
        value = I:raw(value)
        if type(value) ~= "string" then
          V.throw("TypeError", "make_encoder() argument " .. i .. " must be str, not " .. V.type_name(bound[i]))
        end
      end
      options[name] = value
      if name ~= "allow_nan" then
        if i >= 7 then properties[name] = value else properties[name] = bound[i] end
      end
    end
    if options.encoder == namespace.encode_basestring_ascii then options.fast_ascii = true
    elseif options.encoder == namespace.encode_basestring then options.fast_ascii = false end
    return { kind = "nativeobject", nkind = "_json.Encoder", pytype = encoder_type, props = properties,
      methods = { __call__ = V.builtin("_iterencode", encoder(I, options)) } }
  end)
  encoder_type.ns.__module__ = "_json"
  namespace.make_encoder = encoder_type
  return { kind = "nativemodule", name = "_json", ns = namespace }
end

return M
