-- Float/str/bytes formatting with CPython parity: py_float_str (repr of
-- float), str/bytes reprs, and the format-spec mini-language (spec section 10).
-- Decimal rounding leans on C printf via string.format (glibc printf is
-- correctly rounded with ties-to-even, exactly like CPython's dtoa).

local values = require("diaspore.values")
local integers = require("diaspore.bigint")

local M = {}

local chars = values.chars

-- ---------------------------------------------------------------------------
-- py_float_str: CPython repr(float)

local function exp_string(exp)
  local sign = exp < 0 and "-" or "+"
  local es = tostring(math.abs(exp))
  if #es < 2 then es = "0" .. es end
  return sign .. es
end

-- Shortest round-trip digits of a finite nonzero float, via %e probing.
-- Returns (neg, digits, exp10).
local function shortest_digits(f)
  local s
  for p = 0, 17 do
    s = string.format("%." .. p .. "e", f)
    if tonumber(s) == f then break end
  end
  local sign, intd, frac, exp = s:match("^(%-?)(%d)%.?(%d*)e([%+%-]%d+)$")
  return sign == "-", intd .. frac, tonumber(exp)
end

--- Render a float using Python repr conventions.
function M.py_float_str(f)
  if f ~= f then return "nan" end
  if f == math.huge then return "inf" end
  if f == -math.huge then return "-inf" end
  if f == 0.0 then
    if 1.0 / f < 0 then return "-0.0" end
    return "0.0"
  end
  local neg, digits, exp = shortest_digits(f)
  local out
  if exp < -4 or exp >= 16 then
    if #digits > 1 then
      out = digits:sub(1, 1) .. "." .. digits:sub(2)
    else
      out = digits
    end
    out = out .. "e" .. exp_string(exp)
  elseif exp >= #digits - 1 then
    out = digits .. string.rep("0", exp - #digits + 1) .. ".0"
  elseif exp >= 0 then
    out = digits:sub(1, exp + 1) .. "." .. digits:sub(exp + 2)
  else
    out = "0." .. string.rep("0", -exp - 1) .. digits
  end
  if neg then return "-" .. out end
  return out
end

-- ---------------------------------------------------------------------------
-- str / bytes reprs
local unicode -- Load Unicode category tables only for non-ASCII representations.

--- Quote a UTF-8 string using Python repr conventions.
function M.repr_string(s)
  local q, qb = "'", 0x27
  if s:find("'", 1, true) and not s:find('"', 1, true) then
    q, qb = '"', 0x22
  end
  local b = { q }
  for _, cp in utf8.codes(s, true) do
    if cp == 0x5C then
      b[#b + 1] = "\\\\"
    elseif cp == qb then
      b[#b + 1] = "\\" .. q
    elseif cp == 0x0A then
      b[#b + 1] = "\\n"
    elseif cp == 0x0D then
      b[#b + 1] = "\\r"
    elseif cp == 0x09 then
      b[#b + 1] = "\\t"
    elseif cp < 0x20 or cp == 0x7F then
      b[#b + 1] = string.format("\\x%02x", cp)
    else
      local character = utf8.char(cp)
      if cp < 0x7F then
        b[#b + 1] = character
      else
        unicode = unicode or require("diaspore.identifier")
        if unicode.isprintable(character) then
          b[#b + 1] = character
        elseif cp <= 0xFF then
          b[#b + 1] = string.format("\\x%02x", cp)
        elseif cp <= 0xFFFF then
          b[#b + 1] = string.format("\\u%04x", cp)
        else
          b[#b + 1] = string.format("\\U%08x", cp)
        end
      end
    end
  end
  b[#b + 1] = q
  return table.concat(b)
end

--- Quote a byte string using Python repr conventions.
function M.repr_bytes(bs)
  local data = bs.b
  local has_single = data:find("'", 1, true) ~= nil
  local has_double = data:find('"', 1, true) ~= nil
  local qb = (has_single and not has_double) and 0x22 or 0x27
  local q = string.char(qb)
  local b = { "b", q }
  for i = 1, #data do
    local c = data:byte(i)
    if c == 0x5C then
      b[#b + 1] = "\\\\"
    elseif c == qb then
      b[#b + 1] = "\\" .. q
    elseif c == 0x0A then
      b[#b + 1] = "\\n"
    elseif c == 0x0D then
      b[#b + 1] = "\\r"
    elseif c == 0x09 then
      b[#b + 1] = "\\t"
    elseif c < 0x20 or c >= 0x7F then
      b[#b + 1] = string.format("\\x%02x", c)
    else
      b[#b + 1] = string.char(c)
    end
  end
  b[#b + 1] = q
  return table.concat(b)
end

--- Render a range constructor expression.
function M.repr_range(r)
  if r.step == 1 then
    return "range(" .. integers.tostring(r.start) .. ", " .. integers.tostring(r.stop) .. ")"
  end
  return "range(" .. integers.tostring(r.start) .. ", " .. integers.tostring(r.stop) .. ", " .. integers.tostring(r.step) .. ")"
end

-- ---------------------------------------------------------------------------
-- fixed / scientific / general formatting of |f| (af >= 0 or nan)

local function exact_decimal(f)
  if f == 0 then return "0", 0 end
  local mantissa, exponent = math.frexp(f)
  local coefficient = integers.from_float(mantissa * 2.0 ^ 53)
  exponent = exponent - 53
  if exponent >= 0 then
    return integers.tostring(integers.mul(coefficient, integers.pow(2, exponent))), 0
  end
  return integers.tostring(integers.mul(coefficient, integers.pow(5, -exponent))), -exponent
end

local function rounded_digits(digits, keep)
  if keep >= #digits then return digits .. string.rep("0", keep - #digits) end
  if keep < 0 then return "0" end
  local result = digits:sub(1, keep)
  local next_digit = digits:byte(keep + 1) - 48
  local odd = keep > 0 and (digits:byte(keep) - 48) % 2 == 1
  if next_digit > 5 or next_digit == 5 and (odd or digits:sub(keep + 2):find("[1-9]")) then
    local i = #result
    while i > 0 and result:sub(i, i) == "9" do i = i - 1 end
    if i == 0 then return "1" .. string.rep("0", #result) end
    result = result:sub(1, i - 1) .. string.char(result:byte(i) + 1) .. string.rep("0", #result - i)
  end
  return result == "" and "0" or result
end

--- Format decimal floating-point digits without Lua's printf precision limit.
function M.float_digits(f, precision, conversion, alternate)
  if f ~= f then return "nan" end
  if f == math.huge then return "inf" end
  if precision <= 99 then
    return string.format("%" .. (alternate and "#" or "") .. "." .. precision .. conversion, f)
  end
  local digits, scale = exact_decimal(f)
  local exponent = #digits - scale - 1
  if conversion == "f" then
    local rounded = rounded_digits(digits, #digits - scale + precision)
    if #rounded <= precision then rounded = string.rep("0", precision + 1 - #rounded) .. rounded end
    return rounded:sub(1, #rounded - precision) .. "." .. rounded:sub(#rounded - precision + 1)
  end
  local significant = conversion == "e" and precision + 1 or precision
  local rounded = rounded_digits(digits, significant)
  if #rounded > significant then exponent = exponent + 1; rounded = rounded:sub(1, significant) end
  if conversion == "e" or exponent < -4 or exponent >= significant then
    local fraction = rounded:sub(2)
    if conversion == "g" and not alternate then fraction = fraction:gsub("0+$", "") end
    return rounded:sub(1, 1) .. ((fraction ~= "" or alternate) and "." .. fraction or "") .. "e" .. exp_string(exponent)
  end
  local result
  if exponent < 0 then result = "0." .. string.rep("0", -exponent - 1) .. rounded
  elseif exponent + 1 >= #rounded then result = rounded .. string.rep("0", exponent + 1 - #rounded) .. (alternate and "." or "")
  else result = rounded:sub(1, exponent + 1) .. "." .. rounded:sub(exponent + 2) end
  if not alternate and result:find(".", 1, true) then result = result:gsub("0+$", ""):gsub("%.$", "") end
  return result
end

--- Fixed-point with exactly prec fraction digits (correct rounding, half-even).
function M.fixed_str(af, prec)
  if af ~= af then return "nan" end
  if af == math.huge then return "inf" end
  return M.float_digits(af, prec, "f", false)
end

--- Scientific with prec digits after the point.
function M.exp_str(af, prec)
  if af ~= af then return "nan" end
  if af == math.huge then return "inf" end
  local s = M.float_digits(af, prec, "e", false)
  -- normalize exponent to Python style (>= 2 digits; C already does this)
  local mant, exp = s:match("^(.-)e([%+%-]%d+)$")
  return mant .. "e" .. exp_string(tonumber(exp))
end

--- %g-style with prec significant digits (scientific iff exp < -4 or
-- exp >= prec; trailing zeros stripped) - C %g implements exactly this.
function M.g_str(af, prec)
  if af ~= af then return "nan" end
  if af == math.huge then return "inf" end
  if prec < 1 then prec = 1 end
  if af == 0.0 then return "0" end
  local s = M.float_digits(af, prec, "g", false)
  local mant, exp = s:match("^(.-)e([%+%-]%d+)$")
  if mant then return mant .. "e" .. exp_string(tonumber(exp)) end
  return s
end

-- ---------------------------------------------------------------------------
-- rounding helpers

--- round(f) -> int with ties to even (exact).
function M.round_float_to_int(f)
  local neg = f < 0
  local af = math.abs(f)
  local fl = math.floor(af) -- stays float when out of integer range
  local diff = af - fl
  local r
  if diff > 0.5 then
    r = fl + 1
  elseif diff < 0.5 then
    r = fl
  else
    r = (fl % 2 == 0) and fl or fl + 1
  end
  return integers.from_float(neg and -r or r)
end

--- round(f, nd) with correct decimal rounding (ties to even). Returns float.
function M.round_float_digits(f, nd)
  if f ~= f or f == math.huge or f == -math.huge then return f end
  if nd >= 0 then
    if nd > 1074 then return f end
    local result = tonumber(M.float_digits(math.abs(f), nd, "f", false)) + 0.0
    return (f < 0 or f == 0 and 1.0 / f < 0) and -result or result
  end
  if f == 0.0 then return f end
  -- negative ndigits: round at 10^-nd via significant-digit formatting
  local es = string.format("%.16e", math.abs(f))
  local e10 = tonumber(es:match("e([%+%-]%d+)$"))
  local sig = e10 + nd + 1
  if sig <= 0 then
    if sig == 0 and nd >= -308 then
      local scale = 10.0 ^ -nd
      if math.abs(f) > scale / 2 then return f < 0 and -scale or scale end
    end
    return f < 0 and -0.0 or 0.0
  end
  if sig > 17 then return f end
  return tonumber(string.format("%." .. (sig - 1) .. "e", f)) + 0.0
end

--- Deterministic-clock helper: round to 6 decimal places.
function M.round6(x)
  return tonumber(string.format("%.6f", x)) + 0.0
end

-- ---------------------------------------------------------------------------
-- integer digit strings (sign-magnitude, min-int safe)


--- |n| rendered in base (2, 8, 10, 16 lower, 17 => 16 upper).
function M.int_mag_str(n, base)
  local s = integers.tostring(n, base == 17 and 16 or base)
  if s:sub(1, 1) == "-" then s = s:sub(2) end
  return base == 17 and s:upper() or s
end

-- ---------------------------------------------------------------------------
-- format-spec mini-language (spec section 10)


--- Parse the Python format-spec mini-language.
function M.parse_fmt_spec(spec)
  local fs = {
    fill = " ", align = "", sign = "", zero = false, width = 0,
    comma = false, has_prec = false, prec = 0, typ = "",
  }
  local rs = chars(spec)
  local n = #rs
  local i = 1
  local function is_align(r)
    return r == "<" or r == ">" or r == "^" or r == "="
  end
  if n >= 2 and is_align(rs[2]) then
    fs.fill = rs[1]
    fs.align = rs[2]
    i = 3
  elseif n >= 1 and is_align(rs[1]) then
    fs.align = rs[1]
    i = 2
  end
  if i <= n and (rs[i] == "+" or rs[i] == "-" or rs[i] == " ") then
    fs.sign = rs[i]
    i = i + 1
  end
  if i <= n and rs[i] == "z" then fs.coerce_zero = true; i = i + 1 end
  if i <= n and rs[i] == "#" then fs.alternate = true; i = i + 1 end
  if i <= n and rs[i] == "0" then
    fs.zero = true
    if fs.align == "" then fs.align = "="; fs.implicit_align = true end
    if not (n >= 2 and is_align(rs[2])) then fs.fill = "0" end
    i = i + 1
  end
  while i <= n and rs[i] >= "0" and rs[i] <= "9" do
    fs.width = fs.width * 10 + (rs[i]:byte() - 48)
    i = i + 1
  end
  if i <= n and (rs[i] == "," or rs[i] == "_") then
    fs.comma = rs[i]
    i = i + 1
  end
  if i <= n and rs[i] == "." then
    i = i + 1
    if i > n or rs[i] < "0" or rs[i] > "9" then
      values.throw("ValueError", "Format specifier missing precision")
    end
    fs.has_prec = true
    while i <= n and rs[i] >= "0" and rs[i] <= "9" do
      fs.prec = fs.prec * 10 + (rs[i]:byte() - 48)
      i = i + 1
    end
  end
  if i <= n then
    fs.typ = rs[i]
    i = i + 1
  end
  if i <= n then
    values.throw("ValueError", "Invalid format specifier " .. M.repr_string(spec))
  end
  fs.original = spec
  return fs
end

local function pad(s, rune_len, align, fill, width)
  local n = width - rune_len
  if n <= 0 then return s end
  if align == "<" then
    return s .. string.rep(fill, n)
  elseif align == ">" then
    return string.rep(fill, n) .. s
  elseif align == "^" then
    local left = n // 2
    return string.rep(fill, left) .. s .. string.rep(fill, n - left)
  end
  return s
end

local function pad_number(body, neg, fs)
  local sign = ""
  if neg then
    sign = "-"
  elseif fs.sign == "+" then
    sign = "+"
  elseif fs.sign == " " then
    sign = " "
  end
  local align = fs.align == "" and ">" or fs.align
  if align == "=" then
    local n = fs.width - #sign - values.str_len(body)
    if n < 0 then n = 0 end
    local prefix = body:match("^0[bBoOxX]") or ""
    return sign .. prefix .. string.rep(fs.fill, n) .. body:sub(#prefix + 1)
  end
  local s = sign .. body
  return pad(s, values.str_len(s), align, fs.fill, fs.width)
end

local function group_digits(s, separator, size)
  size = size or 3
  separator = separator or ","
  if #s <= size then return s end
  local b = {}
  local lead = #s % size
  if lead > 0 then b[#b + 1] = s:sub(1, lead) end
  for i = lead + 1, #s, size do
    if #b > 0 then b[#b + 1] = separator end
    b[#b + 1] = s:sub(i, i + size - 1)
  end
  return table.concat(b)
end

-- Under zero padding the padding zeros take part in the thousands grouping,
-- CPython-style (format(-42, "08,d") == "-000,042").
local function group_with_zero_fill(digits, rest, neg, fs)
  local grouped = group_digits(digits, fs.comma, fs.group_size)
  if fs.align == "=" and fs.fill == "0" then
    local sign_len = (neg or fs.sign == "+" or fs.sign == " ") and 1 or 0
    local room = fs.width - sign_len - values.str_len(rest)
    while #grouped < room do
      digits = "0" .. digits
      grouped = group_digits(digits, fs.comma, fs.group_size)
    end
  end
  return grouped .. rest
end

local function format_str_spec(s, fs)
  if fs.implicit_align then fs.align = "<" end
  if fs.alternate then values.throw("ValueError", "Alternate form (#) not allowed in string format specifier") end
  if fs.coerce_zero then values.throw("ValueError", "Negative zero coercion (z) not allowed in string format specifier") end
  if fs.comma then values.throw("ValueError", "Cannot specify '" .. fs.comma .. "' with 's'.") end
  if fs.typ ~= "" and fs.typ ~= "s" then
    values.throw("ValueError",
      "Unknown format code '" .. fs.typ .. "' for object of type 'str'")
  end
  if fs.sign ~= "" then
    values.throw("ValueError", "Sign not allowed in string format specifier")
  end
  if fs.align == "=" then
    values.throw("ValueError", "'=' alignment not allowed in string format specifier")
  end
  local rs = chars(s)
  local rl = #rs
  if fs.has_prec and rl > fs.prec then
    local cut = {}
    for i = 1, fs.prec do cut[i] = rs[i] end
    s = table.concat(cut)
    rl = fs.prec
  end
  local align = fs.align == "" and "<" or fs.align
  return pad(s, rl, align, fs.fill, fs.width)
end

local format_float_spec -- forward

local function format_int_spec(n, fs)
  local t = fs.typ
  if t:match("^[fFeEgG%%]$") then return format_float_spec(integers.to_float(n), fs) end
  if fs.coerce_zero then values.throw("ValueError", "Negative zero coercion (z) not allowed in integer format specifier") end
  if fs.has_prec then values.throw("ValueError", "Precision not allowed in integer format specifier") end
  if t == "c" then
    if fs.sign ~= "" then values.throw("ValueError", "Sign not allowed with integer format specifier 'c'") end
    if fs.alternate then values.throw("ValueError", "Alternate form (#) not allowed with integer format specifier 'c'") end
    if integers.cmp(n, 0) < 0 or integers.cmp(n, 0x10ffff) > 0 then values.throw("OverflowError", "%c arg not in range(0x110000)") end
    return pad_number(utf8.char(n), false, fs)
  end
  if not (t == "" or t == "d" or t == "n" or t == "b" or t == "o" or t == "x" or t == "X") then
    values.throw("ValueError", "Unknown format code '" .. t .. "' for object of type 'int'")
  end
  local neg = integers.cmp(n, 0) < 0
  local base = t == "b" and 2 or t == "o" and 8 or t == "x" and 16 or t == "X" and 17 or 10
  if fs.comma and (t == "n" or fs.comma == "," and base ~= 10) then
    values.throw("ValueError", "Cannot specify '" .. fs.comma .. "' with '" .. t .. "'.")
  end
  local body = M.int_mag_str(n, base)
  local prefix = fs.alternate and (t == "b" and "0b" or t == "o" and "0o" or t == "x" and "0x" or t == "X" and "0X") or ""
  if fs.comma then
    fs.group_size = base == 10 and 3 or 4
    fs.width = fs.width - #prefix
    body = group_with_zero_fill(body, "", neg, fs)
    fs.width = fs.width + #prefix
  end
  return pad_number(prefix .. body, neg, fs)
end

format_float_spec = function(f, fs)
  local neg = f == f and (f < 0 or (f == 0.0 and 1.0 / f < 0))
  local av = math.abs(f)
  local prec = fs.has_prec and fs.prec or 6
  local body
  local t = fs.typ:lower()
  if t == "f" or t == "e" or t == "g" or t == "n" or t == "%" then
    if t == "n" and fs.comma then values.throw("ValueError", "Cannot specify '" .. fs.comma .. "' with 'n'.") end
    local conversion = t == "%" and "f" or t == "n" and "g" or t
    body = M.float_digits(t == "%" and av * 100 or av, prec, conversion, fs.alternate)
    if t == "%" then body = body .. "%" end
  elseif t == "" then
    if fs.has_prec and av == av and av ~= math.huge then
      local significant = math.max(1, prec)
      local scientific = M.float_digits(av, significant - 1, "e", fs.alternate)
      local exponent = tonumber(scientific:match("e([%+%-]%d+)$"))
      if exponent < -4 or exponent >= significant - 1 then
        body = scientific
        if not fs.alternate then body = body:gsub("(%..-)0+e", "%1e"):gsub("%.e", "e") end
      else
        body = M.float_digits(av, math.max(0, significant - exponent - 1), "f", fs.alternate)
        if not fs.alternate then body = body:gsub("(%..-)0+$", "%1"):gsub("%.$", ".0") end
        if not body:find(".", 1, true) then body = body .. ".0" end
      end
    else
      body = M.py_float_str(av)
      if fs.alternate and not body:find(".", 1, true) then body = body:gsub("e", ".e") end
    end
  else
    values.throw("ValueError", "Unknown format code '" .. fs.typ .. "' for object of type 'float'")
  end
  if fs.coerce_zero and tonumber((body:gsub("%%$", ""))) == 0 then neg = false end
  if fs.comma and body:match("^%d") then
    local dot = body:find("[.e%%]")
    if dot then
      body = group_with_zero_fill(body:sub(1, dot - 1), body:sub(dot), neg, fs)
    else
      body = group_with_zero_fill(body, "", neg, fs)
    end
  end
  if fs.typ:match("^[EFG]$") then body = body:upper() end
  return pad_number(body, neg, fs)
end

--- Format a value using its Python format protocol.
function M.format_value(I, v, spec)
  local t = type(v)
  if spec == "" then
    if t == "string" then return v end
    if t == "number" then
      if math.type(v) == "integer" then return tostring(v) end
      return M.py_float_str(v)
    end
    if t == "boolean" then return v and "True" or "False" end
  end
  if t == "table" and v.kind == "instance" then
    local method = values.class_lookup(v.cls, "__format__")
    if method ~= nil and not (v.prim ~= nil and method == values.OBJECT.ns.__format__) then
      local result = I:call_dunder(v, "__format__", { spec })
      if type(result) ~= "string" then values.throw("TypeError", "__format__ must return a str, not " .. values.type_name(result)) end
      return result
    end
    if v.prim ~= nil then return M.format_value(I, v.prim, spec) end
    if spec ~= "" then values.throw("TypeError", "unsupported format string passed to " .. v.cls.name .. ".__format__") end
    return I:str(v)
  end
  if integers.is_bigint(v) then
    if spec == "" then return integers.tostring(v) end
    return format_int_spec(v, M.parse_fmt_spec(spec))
  end
  if t ~= "number" and t ~= "boolean" and t ~= "string" then
    local k = v.kind
    if not (k == "bytes" or k == "none" or k == "list" or k == "tuple"
        or k == "dict" or k == "set" or k == "range") then
      v = I:str(v)
      t = "string"
    end
  end
  if spec == "" then return I:str(v) end
  local fs = M.parse_fmt_spec(spec)
  if t == "boolean" then
    v = v and 1 or 0
    t = "number"
  end
  if t == "number" then
    if math.type(v) == "integer" then return format_int_spec(v, fs) end
    return format_float_spec(v, fs)
  end
  if t == "string" then return format_str_spec(v, fs) end
  values.throw("TypeError",
    "unsupported format string passed to " .. values.type_name(v) .. ".__format__")
end

return M
