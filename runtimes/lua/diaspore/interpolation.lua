local V = require("diaspore.values")
local F = require("diaspore.format")
local B = require("diaspore.buffer")
local integers = require("diaspore.bigint")
local M = {}
local function kind(v) return type(v) == "table" and v.kind end
local function fail(message) V.throw("ValueError", message) end
local function truncate(s, count, binary)
  if binary then return s:sub(1, count) end
  local chars = V.chars(s)
  return table.concat(chars, "", 1, math.min(count, #chars))
end
function M.percent(I, template, value, ops)
  local binary = kind(template) == "bytes" or kind(template) == "bytearray"
  local text = binary and template.b or template
  local args = kind(value) == "tuple" and value.items or { value }
  local index, pos, parts, mapping = 1, 1, {}, false
  local function take()
    local result = args[index]
    if result == nil then V.throw("TypeError", "not enough arguments for format string") end
    index = index + 1
    return result
  end
  while pos <= #text do
    local start = text:find("%", pos, true)
    if not start then parts[#parts + 1] = text:sub(pos); break end
    parts[#parts + 1] = text:sub(pos, start - 1)
    pos = start + 1
    if text:sub(pos, pos) == "%" then parts[#parts + 1] = "%"; pos = pos + 1
    else
      local key
      if text:sub(pos, pos) == "(" then
        local depth, finish = 1, pos + 1
        while finish <= #text and depth > 0 do
          local c = text:sub(finish, finish)
          if c == "(" then depth = depth + 1 elseif c == ")" then depth = depth - 1 end
          finish = finish + 1
        end
        if depth ~= 0 then fail("incomplete format key") end
        key = text:sub(pos + 1, finish - 2); pos = finish; mapping = true
        if not (kind(value) == "dict" or kind(value) == "instance" and V.class_lookup(value.cls, "__getitem__")) then
          V.throw("TypeError", "format requires a mapping")
        end
        args, index = {}, 1
      end
      local flags = text:match("^[#0 +%-]*", pos) or ""
      pos = pos + #flags
      local width, precision = 0, nil
      if text:sub(pos, pos) == "*" then
        width = V.as_index(take()); pos = pos + 1
        if width == nil then V.throw("TypeError", "* wants int") end
        if width < 0 then flags = flags .. "-"; width = -width end
      else
        local digits = text:match("^%d*", pos) or ""
        width = tonumber(digits) or 0; pos = pos + #digits
      end
      if text:sub(pos, pos) == "." then
        pos = pos + 1
        if text:sub(pos, pos) == "*" then
          precision = V.as_index(take()); pos = pos + 1
          if precision == nil then V.throw("TypeError", "* wants int") end
          if precision < 0 then precision = 0 end
        else
          local digits = text:match("^%d*", pos) or ""
          precision = tonumber(digits) or 0; pos = pos + #digits
        end
      end
      if text:sub(pos, pos):match("[hlL]") then pos = pos + 1 end
      local conversion = text:sub(pos, pos); pos = pos + 1
      if conversion == "" then fail("incomplete format") end
      local arg
      if key then arg = ops.get_index(I, value, binary and V.bytes(key) or key) else arg = take() end
      local result, prefix = nil, ""
      if conversion == "s" or conversion == "r" or conversion == "a" or binary and conversion == "b" then
        if binary and (conversion == "s" or conversion == "b") then
          if B.is_buffer(arg) then result = B.data(arg)
          elseif kind(arg) == "instance" and V.class_lookup(arg.cls, "__bytes__") then
            local bytes = I:call_dunder(arg, "__bytes__", {})
            if kind(bytes) ~= "bytes" then V.throw("TypeError", "__bytes__ returned non-bytes") end
            result = bytes.b
          else V.throw("TypeError", "%b requires a bytes-like object, or an object that implements __bytes__, not '" .. V.type_name(arg) .. "'") end
        elseif conversion == "s" then result = I:str(arg)
        elseif conversion == "a" or binary then result = ops.ascii(I, arg)
        else result = ops.repr(I, arg) end
        if precision ~= nil then result = truncate(result, precision, binary) end
      elseif conversion == "c" then
        local number = V.as_index(arg)
        if number == nil and kind(arg) == "instance" and V.class_lookup(arg.cls, "__index__") then
          number = V.as_index(I:call_dunder(arg, "__index__", {}))
        end
        if number then
          if integers.cmp(number, 0) < 0 or integers.cmp(number, binary and 255 or 0x10ffff) > 0 then
            V.throw("OverflowError", binary and "%c arg not in range(256)" or "%c arg not in range(0x110000)")
          end
          result = binary and string.char(number) or utf8.char(number)
        elseif binary and B.is_buffer(arg) and #B.data(arg) == 1 then result = B.data(arg)
        elseif not binary and type(arg) == "string" and V.str_len(arg) == 1 then result = arg
        else V.throw("TypeError", binary and "%c requires an integer in range(256) or a single byte" or "%c requires int or char") end
      elseif conversion:match("[diuoxX]") then
        local number = V.as_index(arg)
        if number == nil and conversion:match("[diu]") and type(arg) == "number" then
          number = integers.from_float(arg)
        end
        if number == nil and kind(arg) == "instance" then
          local hook = conversion:match("[diu]") and V.class_lookup(arg.cls, "__int__") and "__int__" or "__index__"
          if V.class_lookup(arg.cls, hook) then number = V.as_index(I:call_dunder(arg, hook, {})) end
        end
        if number == nil then
          V.throw("TypeError", "%" .. conversion .. " format: " .. (conversion:match("[diu]") and "a real number" or "an integer") .. " is required, not " .. V.type_name(arg))
        end
        local base = conversion == "o" and 8 or conversion == "x" and 16 or conversion == "X" and 17 or 10
        result = F.int_mag_str(number, base)
        if precision and precision > #result then result = string.rep("0", precision - #result) .. result end
        if integers.cmp(number, 0) < 0 then prefix = "-" elseif flags:find("+", 1, true) then prefix = "+" elseif flags:find(" ", 1, true) then prefix = " " end
        if flags:find("#", 1, true) then
          if base == 8 then prefix = prefix .. "0o" elseif base == 16 then prefix = prefix .. "0x" elseif base == 17 then prefix = prefix .. "0X" end
        end
      elseif conversion:match("[eEfFgG]") then
        local number = V.as_float(arg)
        if number == nil and kind(arg) == "instance" and V.class_lookup(arg.cls, "__float__") then number = V.as_float(I:call_dunder(arg, "__float__", {})) end
        if number == nil then V.throw("TypeError", "must be real number, not " .. V.type_name(arg)) end
        local sign = flags:find("+", 1, true) and "+" or flags:find(" ", 1, true) and " " or ""
        local alternate = flags:find("#", 1, true) and "#" or ""
        local negative = number == number and (number < 0 or number == 0 and 1.0 / number < 0)
        result = F.float_digits(math.abs(number), precision or 6, conversion:lower(), alternate ~= "")
        result = (negative and "-" or sign) .. result
        if conversion:match("[EFG]") then result = result:upper() end
        if result:sub(1, 1):match("[+%- ]") then prefix = result:sub(1, 1); result = result:sub(2) end
      else
        local cp = utf8.codepoint(conversion)
        fail("unsupported format character '" .. conversion .. "' (0x" .. string.format("%x", cp) .. ") at index " .. tostring(binary and pos - 2 or V.str_len(text:sub(1, pos - 2))))
      end
      local count = binary and #result or V.str_len(result)
      local pad = math.max(0, width - count - #prefix)
      I:fuel_n(pad)
      if flags:find("-", 1, true) then result = prefix .. result .. string.rep(" ", pad)
      elseif flags:find("0", 1, true) and conversion:match("[diuoxXeEfFgG]") then result = prefix .. string.rep("0", pad) .. result
      else result = string.rep(" ", pad) .. prefix .. result end
      parts[#parts + 1] = result
    end
  end
  if not mapping and index <= #args and not (kind(value) == "dict" and index == 1) then V.throw("TypeError", "not all arguments converted during " .. (binary and "bytes" or "string") .. " formatting") end
  local result = table.concat(parts)
  if kind(template) == "bytearray" then return B.bytearray(result) end
  return binary and V.bytes(result) or result
end
local function decimal_index(text)
  if text == "" then return nil end
  local result = 0
  local decimal_value = require("diaspore.helpers").decimal_value
  for _, cp in utf8.codes(text) do
    local digit = decimal_value(cp)
    if digit == nil then return nil end
    if result > (math.maxinteger - digit) // 10 then V.throw("ValueError", "Too many decimal digits in format string") end
    result = result * 10 + digit
  end
  return result
end
function M.format(I, text, args, keywords, mapping, ops)
  local auto, manual, automatic = 0, false, false
  local function lookup(field)
    local root, suffix = field:match("^([^.%[]*)(.*)$")
    local root_index = decimal_index(root)
    local value
    if root == "" then
      if mapping then fail("Format string contains positional fields") end
      if manual then fail("cannot switch from manual field specification to automatic field numbering") end
      automatic = true; value = args[auto + 1]; auto = auto + 1
      if value == nil then V.throw("IndexError", "Replacement index " .. tostring(auto - 1) .. " out of range for positional args tuple") end
    elseif root_index ~= nil then
      if mapping then fail("Format string contains positional fields") end
      if automatic then fail("cannot switch from automatic field numbering to manual field specification") end
      manual = true; value = args[root_index + 1]
      if value == nil then V.throw("IndexError", "Replacement index " .. tostring(root_index) .. " out of range for positional args tuple") end
    elseif mapping then value = ops.get_index(I, mapping, root)
    else
      for _, entry in ipairs(keywords) do if entry.k == root then value = entry.v; break end end
      if value == nil then V.throw_val(V.make_exc("KeyError", root)) end
    end
    while suffix ~= "" do
      if suffix:sub(1, 1) == "." then
        local name, rest = suffix:match("^%.([^.%[]+)(.*)$")
        if not name then fail("Empty attribute in format string") end
        value = I:get_attr(value, name); suffix = rest
      elseif suffix:sub(1, 1) == "[" then
        local close = suffix:find("]", 2, true)
        if not close then fail("Missing ']' in format string") end
        local key = suffix:sub(2, close - 1)
        if key == "" then fail("Empty attribute in format string") end
        value = ops.get_index(I, value, decimal_index(key) or key)
        suffix = suffix:sub(close + 1)
      else fail("Only '.' or '[' may follow ']' in format field specifier") end
    end
    return value
  end
  local render
  render = function(source, depth)
    if depth > 2 then fail("Max string recursion exceeded") end
    local parts, pos = {}, 1
    while pos <= #source do
      local c = source:sub(pos, pos)
      if c == "{" or c == "}" then
        if source:sub(pos + 1, pos + 1) == c then parts[#parts + 1] = c; pos = pos + 2
        elseif c == "}" then fail("Single '}' encountered in format string")
        else
          if pos == #source then fail("Single '{' encountered in format string") end
          local start, balance, finish = pos + 1, 1, pos + 1
          while finish <= #source and balance > 0 do
            local ch = source:sub(finish, finish)
            if ch == "{" then balance = balance + 1 elseif ch == "}" then balance = balance - 1 end
            finish = finish + 1
          end
          if balance ~= 0 then
            local remaining = source:sub(start)
            if remaining:find("{", 1, true) and not remaining:find(":", 1, true) then fail("unexpected '{' in field name") end
            if remaining:sub(-1) == "!" then fail("end of string while looking for conversion specifier") end
            if remaining:find("[!:]") then fail("unmatched '{' in format spec") end
            fail("expected '}' before end of string")
          end
          local body = source:sub(start, finish - 2)
          local field, conversion, spec = body, nil, ""
          local bracket, split = false, nil
          for j = 1, #body do
            local ch = body:sub(j, j)
            if ch == "[" then bracket = true elseif ch == "]" then bracket = false
            elseif not bracket and (ch == ":" or ch == "!") then split = j; break end
          end
          if split then
            field = body:sub(1, split - 1)
            local rest = body:sub(split)
            if rest:sub(1, 1) == "!" then
              conversion = rest:sub(2, 2); rest = rest:sub(3)
              if conversion ~= "s" and conversion ~= "r" and conversion ~= "a" then fail("Unknown conversion specifier " .. conversion) end
            end
            if rest ~= "" and rest:sub(1, 1) ~= ":" then fail("expected ':' after conversion specifier") end
            spec = rest:sub(2)
          end
          local value = lookup(field)
          if conversion == "s" then value = I:str(value) elseif conversion == "r" then value = ops.repr(I, value) elseif conversion == "a" then value = ops.ascii(I, value) end
          spec = render(spec, depth + 1)
          parts[#parts + 1] = F.format_value(I, value, spec)
          pos = finish
        end
      else
        local finish = source:find("[{}]", pos) or (#source + 1)
        parts[#parts + 1] = source:sub(pos, finish - 1); pos = finish
      end
    end
    return table.concat(parts)
  end
  return render(text, 0)
end
return M
