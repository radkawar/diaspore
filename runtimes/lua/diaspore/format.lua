-- Float/str/bytes formatting with CPython parity: py_float_str (repr of
-- float), str/bytes reprs, and the format-spec mini-language (spec section 10).
-- Decimal rounding leans on C printf via string.format (glibc printf is
-- correctly rounded with ties-to-even, exactly like CPython's dtoa).

local values = require("diaspore.values")

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
      b[#b + 1] = utf8.char(cp)
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
    return "range(" .. r.start .. ", " .. r.stop .. ")"
  end
  return "range(" .. r.start .. ", " .. r.stop .. ", " .. r.step .. ")"
end

-- ---------------------------------------------------------------------------
-- fixed / scientific / general formatting of |f| (af >= 0 or nan)

--- Fixed-point with exactly prec fraction digits (correct rounding, half-even).
function M.fixed_str(af, prec)
  if af ~= af then return "nan" end
  if af == math.huge then return "inf" end
  if prec > 99 then
    values.throw("ValueError", "precision too large for diaspore-lua")
  end
  return string.format("%." .. prec .. "f", af)
end

--- Scientific with prec digits after the point.
function M.exp_str(af, prec)
  if af ~= af then return "nan" end
  if af == math.huge then return "inf" end
  if prec > 99 then
    values.throw("ValueError", "precision too large for diaspore-lua")
  end
  local s = string.format("%." .. prec .. "e", af)
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
  if prec > 99 then
    values.throw("ValueError", "precision too large for diaspore-lua")
  end
  if af == 0.0 then return "0" end
  local s = string.format("%." .. prec .. "g", af)
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
  local iv = math.tointeger(r)
  if iv == nil then
    values.throw("OverflowError", "diaspore-lua: integer overflow (int64 limit)")
  end
  if neg then return -iv end
  return iv
end

--- round(f, nd) with correct decimal rounding (ties to even). Returns float.
function M.round_float_digits(f, nd)
  if f ~= f or f == math.huge or f == -math.huge then return f end
  if nd >= 0 then
    if nd > 99 then return f end
    return tonumber(string.format("%." .. nd .. "f", f)) + 0.0
  end
  if f == 0.0 then return f end
  -- negative ndigits: round at 10^-nd via significant-digit formatting
  local es = string.format("%.16e", math.abs(f))
  local e10 = tonumber(es:match("e([%+%-]%d+)$"))
  local sig = e10 + nd + 1
  if sig <= 0 then
    if f < 0 or 1.0 / f < 0 then return -0.0 end
    return 0.0
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

local MININT_STR = {
  [2] = "1" .. string.rep("0", 63),
  [8] = "1000000000000000000000",
  [10] = "9223372036854775808",
  [16] = "8000000000000000",
}

--- |n| rendered in base (2, 8, 10, 16 lower, 17 => 16 upper).
function M.int_mag_str(n, base)
  local upper = false
  if base == 17 then
    base = 16
    upper = true
  end
  local s
  if n == math.mininteger then
    s = MININT_STR[base]
  else
    local mag = n < 0 and -n or n
    if base == 10 then
      s = tostring(mag)
    elseif base == 16 then
      s = string.format("%x", mag)
    elseif base == 8 then
      s = string.format("%o", mag)
    else -- base 2
      if mag == 0 then
        s = "0"
      else
        local bits = {}
        while mag > 0 do
          bits[#bits + 1] = tostring(mag & 1)
          mag = mag >> 1
        end
        local rev = {}
        for i = #bits, 1, -1 do rev[#rev + 1] = bits[i] end
        s = table.concat(rev)
      end
    end
  end
  if upper then s = s:upper() end
  return s
end

-- ---------------------------------------------------------------------------
-- format-spec mini-language (spec section 10)

local TYPE_CODES = { d = true, b = true, o = true, x = true, X = true,
                     f = true, e = true, g = true, ["%"] = true, s = true }

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
  if i <= n and rs[i] == "0" then
    fs.zero = true
    if fs.align == "" then
      fs.align = "="
      fs.fill = "0"
    end
    i = i + 1
  end
  while i <= n and rs[i] >= "0" and rs[i] <= "9" do
    fs.width = fs.width * 10 + (rs[i]:byte() - 48)
    i = i + 1
  end
  if i <= n and rs[i] == "," then
    fs.comma = true
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
  if fs.typ ~= "" and not TYPE_CODES[fs.typ] then
    values.throw("ValueError", "Unknown format code '" .. fs.typ .. "'")
  end
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
    return sign .. string.rep(fs.fill, n) .. body
  end
  local s = sign .. body
  return pad(s, values.str_len(s), align, fs.fill, fs.width)
end

local function group_digits(s)
  if #s <= 3 then return s end
  local b = {}
  local lead = #s % 3
  if lead > 0 then b[#b + 1] = s:sub(1, lead) end
  for i = lead + 1, #s, 3 do
    if #b > 0 then b[#b + 1] = "," end
    b[#b + 1] = s:sub(i, i + 2)
  end
  return table.concat(b)
end

-- Under zero padding the padding zeros take part in the thousands grouping,
-- CPython-style (format(-42, "08,d") == "-000,042").
local function group_with_zero_fill(digits, rest, neg, fs)
  local grouped = group_digits(digits)
  if fs.align == "=" and fs.fill == "0" then
    local sign_len = (neg or fs.sign == "+" or fs.sign == " ") and 1 or 0
    local room = fs.width - sign_len - values.str_len(rest)
    while #grouped < room do
      digits = "0" .. digits
      grouped = group_digits(digits)
    end
  end
  return grouped .. rest
end

local function format_str_spec(s, fs)
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
  if fs.typ == "f" or fs.typ == "e" or fs.typ == "g" or fs.typ == "%" then
    return format_float_spec(n + 0.0, fs)
  end
  if fs.has_prec then
    values.throw("ValueError", "Precision not allowed in integer format specifier")
  end
  local neg = n < 0
  local body
  local t = fs.typ
  if t == "" or t == "d" then
    body = M.int_mag_str(n, 10)
    if fs.comma then body = group_with_zero_fill(body, "", neg, fs) end
  elseif t == "b" then
    body = M.int_mag_str(n, 2)
  elseif t == "o" then
    body = M.int_mag_str(n, 8)
  elseif t == "x" then
    body = M.int_mag_str(n, 16)
  else -- X
    body = M.int_mag_str(n, 17)
  end
  return pad_number(body, neg, fs)
end

format_float_spec = function(f, fs)
  local neg = f == f and (f < 0 or (f == 0.0 and 1.0 / f < 0))
  local av = math.abs(f)
  local prec = fs.has_prec and fs.prec or 6
  local body
  local t = fs.typ
  if t == "f" then
    body = M.fixed_str(av, prec)
  elseif t == "e" then
    body = M.exp_str(av, prec)
  elseif t == "%" then
    if av == math.huge or av ~= av then
      body = M.fixed_str(av, prec)
    else
      body = M.fixed_str(av * 100, prec) .. "%"
    end
  elseif t == "g" then
    body = M.g_str(av, prec)
  elseif t == "" then
    if fs.has_prec then
      body = M.g_str(av, prec)
    else
      local s = M.py_float_str(av)
      if s:sub(1, 1) == "-" then s = s:sub(2) end
      body = s
    end
  else
    values.throw("ValueError",
      "Unknown format code '" .. t .. "' for object of type 'float'")
  end
  if fs.comma then
    local dot = body:find("[.e%%]")
    if dot then
      body = group_with_zero_fill(body:sub(1, dot - 1), body:sub(dot), neg, fs)
    else
      body = group_with_zero_fill(body, "", neg, fs)
    end
  end
  return pad_number(body, neg, fs)
end

--- format(v, spec) - v must already be int/float/bool/str here; the interp
-- routes other values through str() first (I is the interp, for I:str).
function M.format_value(I, v, spec)
  local t = type(v)
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
  if t == "boolean" and fs.typ ~= "s" then
    v = v and 1 or 0
    t = "number"
  end
  if t == "number" then
    if math.type(v) == "integer" then return format_int_spec(v, fs) end
    return format_float_spec(v, fs)
  end
  if t == "string" then return format_str_spec(v, fs) end
  if t == "boolean" then return format_str_spec(v and "True" or "False", fs) end
  if fs.typ == "s" or fs.typ == "" then
    return format_str_spec(I:str(v), fs)
  end
  values.throw("TypeError",
    "unsupported format string passed to " .. values.type_name(v) .. ".__format__")
end

return M
