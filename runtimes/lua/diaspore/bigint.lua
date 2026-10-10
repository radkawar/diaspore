--- Exact Python integers with an allocation-free native int64 fast path.
-- Public values are Lua integers or tables with kind "bigint". The sign and base
-- 2^24 limbs are private; use this module for every numeric operation. Results
-- are canonical and demote to native integers whenever they fit. Errors use
-- sandbox exceptions, loaded lazily to avoid a values/bigint dependency cycle.
local M = {}
local B, MASK, WIDTH = 16777216, 16777215, 24
local MIN, MAX = math.mininteger, math.maxinteger
local mt = {}
local function fail(name, message) require("diaspore.values").throw(name, message) end
local function trim(a)
  while #a > 0 and a[#a] == 0 do a[#a] = nil end
  return a
end
local function copy(a)
  local r = {}
  for i = 1, #a do r[i] = a[i] end
  return r
end
local function parts(x)
  if type(x) == "table" then return x.sign, x.limbs end
  if x == 0 then return 0, {} end
  local s, r = x < 0 and -1 or 1, {}
  -- Logical shifts also split abs(mininteger) without signed overflow.
  if x < 0 then x = -x end
  repeat r[#r + 1], x = x & MASK, x >> WIDTH until x == 0
  return s, r
end
local function pack(s, a)
  trim(a)
  local n = #a
  if n == 0 then return 0 end
  if n < 3 or (n == 3 and (a[3] < 32768 or
      (s < 0 and a[3] == 32768 and a[2] == 0 and a[1] == 0))) then
    local x = 0
    for i = n, 1, -1 do x = (x << WIDTH) | a[i] end
    return s < 0 and -x or x
  end
  return setmetatable({kind = "bigint", sign = s, limbs = a}, mt)
end
local function acmp(a, b)
  if #a ~= #b then return #a < #b and -1 or 1 end
  for i = #a, 1, -1 do
    if a[i] ~= b[i] then return a[i] < b[i] and -1 or 1 end
  end
  return 0
end
local function aadd(a, b)
  local r, carry = {}, 0
  for i = 1, math.max(#a, #b) do
    local v = (a[i] or 0) + (b[i] or 0) + carry
    r[i], carry = v & MASK, v >> WIDTH
  end
  if carry ~= 0 then r[#r + 1] = carry end
  return r
end
local function asub(a, b)
  local r, borrow = {}, 0
  for i = 1, #a do
    local v = a[i] - (b[i] or 0) - borrow
    r[i], borrow = v & MASK, v < 0 and 1 or 0
  end
  return trim(r)
end
local function smallmul(a, b)
  local r, carry = {}, 0
  for i = 1, #a do
    local v = a[i] * b + carry
    r[i], carry = v & MASK, v >> WIDTH
  end
  if carry ~= 0 then r[#r + 1] = carry end
  return trim(r)
end
local function smalldiv(a, b)
  local r, rem = {}, 0
  for i = #a, 1, -1 do
    local v = rem * B + a[i]
    r[i], rem = v // b, v % b
  end
  return trim(r), rem
end
local function amul(a, b)
  local r = {}
  for i = 1, #a + #b do r[i] = 0 end
  for i = 1, #a do
    local carry = 0
    for j = 1, #b do
      local k = i + j - 1
      local v = r[k] + a[i] * b[j] + carry
      r[k], carry = v & MASK, v >> WIDTH
    end
    r[i + #b] = carry
  end
  return trim(r)
end
local function adivmod(a, b)
  if acmp(a, b) < 0 then return {}, copy(a) end
  if #b == 1 then
    local q, r = smalldiv(a, b[1])
    return q, r == 0 and {} or {r}
  end
  -- Knuth D: normalization bounds quotient estimation to two corrections.
  local n, m = #b, #a - #b
  local scale = B // (b[n] + 1)
  local v, u, q = smallmul(b, scale), smallmul(a, scale), {}
  u[#a + 1] = u[#a + 1] or 0
  for j = m, 0, -1 do
    local top = u[j + n + 1] * B + u[j + n]
    local guess, rem = top // v[n], top % v[n]
    while guess >= B or guess * v[n - 1] > rem * B + u[j + n - 1] do
      guess, rem = guess - 1, rem + v[n]
      if rem >= B then break end
    end
    local borrow = 0
    for i = 1, n do
      local p = guess * v[i] + borrow
      local d = u[j + i] - (p & MASK)
      u[j + i], borrow = d & MASK, (p >> WIDTH) + (d < 0 and 1 or 0)
    end
    local topdiff = u[j + n + 1] - borrow
    u[j + n + 1] = topdiff & MASK
    if topdiff < 0 then
      guess = guess - 1
      local carry = 0
      for i = 1, n do
        local d = u[j + i] + v[i] + carry
        u[j + i], carry = d & MASK, d >> WIDTH
      end
      u[j + n + 1] = (u[j + n + 1] + carry) & MASK
    end
    q[j + 1] = guess
  end
  local r = {}
  for i = 1, n do r[i] = u[i] end
  r = smalldiv(r, scale)
  return trim(q), r
end

--- Return whether a value has the arbitrary-precision representation.
function M.is_bigint(x) return type(x) == "table" and getmetatable(x) == mt end
--- Return whether a value is an exact integer, excluding floats and booleans.
function M.is_int(x) return math.type(x) == "integer" or M.is_bigint(x) end
--- Compare integers, returning -1, 0, or 1.
function M.cmp(a, b)
  if type(a) == "number" and type(b) == "number" then return a < b and -1 or (a > b and 1 or 0) end
  local sa, aa = parts(a)
  local sb, bb = parts(b)
  if sa ~= sb then return sa < sb and -1 or 1 end
  return sa * acmp(aa, bb)
end
--- Add exact integers.
function M.add(a, b)
  if type(a) == "number" and type(b) == "number" then
    local r = a + b
    if (b >= 0 and r >= a) or (b < 0 and r < a) then return r end
  end
  local sa, aa = parts(a)
  local sb, bb = parts(b)
  if sa == 0 then return b end
  if sb == 0 then return a end
  if sa == sb then return pack(sa, aadd(aa, bb)) end
  local c = acmp(aa, bb)
  return c >= 0 and pack(sa, asub(aa, bb)) or pack(sb, asub(bb, aa))
end
--- Negate an integer, including the native signed minimum.
function M.neg(a)
  if type(a) == "number" and a ~= MIN then return -a end
  local s, aa = parts(a)
  return pack(-s, aa)
end
--- Subtract exact integers.
function M.sub(a, b)
  if type(a) == "number" and type(b) == "number" then
    local r = a - b
    if (b >= 0 and r <= a) or (b < 0 and r > a) then return r end
  end
  return M.add(a, M.neg(b))
end
--- Return the nonnegative magnitude.
function M.abs(a) return M.cmp(a, 0) < 0 and M.neg(a) or a end
--- Multiply exact integers, detecting wrapped int64 products.
function M.mul(a, b)
  if type(a) == "number" and type(b) == "number" then
    if a == 0 or b == 0 then return 0 end
    local r = a * b
    if not (r == MIN and b == -1) and r // b == a then return r end
  end
  local sa, aa = parts(a)
  local sb, bb = parts(b)
  return pack(sa * sb, amul(aa, bb))
end
--- Return floor quotient and remainder, with remainder having divisor's sign.
function M.divmod(a, b)
  if b == 0 then fail("ZeroDivisionError", "integer division or modulo by zero") end
  if type(a) == "number" and type(b) == "number" and not (a == MIN and b == -1) then
    return a // b, a % b
  end
  local sa, aa = parts(a)
  local sb, bb = parts(b)
  local q, r = adivmod(aa, bb)
  if sa * sb < 0 and #r > 0 then q, r = aadd(q, {1}), asub(bb, r) end
  return pack(sa * sb, q), pack(sb, r)
end
--- Return the floor quotient.
function M.floordiv(a, b) local q = M.divmod(a, b); return q end
--- Return the floor-division remainder.
function M.mod(a, b)
  if b == 0 then fail("ZeroDivisionError", "integer modulo by zero") end
  local _, r = M.divmod(a, b)
  return r
end

local function shiftcount(b, right)
  if M.cmp(b, 0) < 0 then fail("ValueError", "negative shift count") end
  if type(b) == "table" then
    if right then return nil end
    fail("OverflowError", "too many digits in integer")
  end
  return b
end
--- Shift left without discarding high bits.
function M.lshift(a, b)
  if a == 0 then
    if M.cmp(b, 0) < 0 then fail("ValueError", "negative shift count") end
    return 0
  end
  b = shiftcount(b, false)
  if b == 0 then return a end
  if type(a) == "number" and b < 63 then
    local r = a << b
    if r // (1 << b) == a then return r end
  end
  local s, aa = parts(a)
  local words, bits, r, carry = b // WIDTH, b % WIDTH, {}, 0
  for i = 1, words do r[i] = 0 end
  for i = 1, #aa do
    local v = (aa[i] << bits) | carry
    r[i + words], carry = v & MASK, v >> WIDTH
  end
  if carry ~= 0 then r[#aa + words + 1] = carry end
  return pack(s, r)
end
--- Arithmetic right shift with infinite sign extension.
function M.rshift(a, b)
  b = shiftcount(b, true)
  if b == nil then return M.cmp(a, 0) < 0 and -1 or 0 end
  if b == 0 then return a end
  if type(a) == "number" then
    if b >= 63 then return a < 0 and -1 or 0 end
    return a // (1 << b)
  end
  local s, aa = parts(a)
  local words, bits, r, discarded = b // WIDTH, b % WIDTH, {}, false
  if words >= #aa then return s < 0 and -1 or 0 end
  for i = 1, words do if aa[i] ~= 0 then discarded = true end end
  if aa[words + 1] & ((1 << bits) - 1) ~= 0 then discarded = true end
  for i = words + 1, #aa do
    r[i - words] = (aa[i] >> bits) | (((aa[i + 1] or 0) << (WIDTH - bits)) & MASK)
  end
  trim(r)
  if s < 0 and discarded then r = aadd(r, {1}) end
  return pack(s, r)
end
local function twos(a, n)
  local s, aa = parts(a)
  local r, carry = {}, 1
  for i = 1, n do
    local x = aa[i] or 0
    if s < 0 then x = (MASK ~ x) + carry; carry = x >> WIDTH end
    r[i] = x & MASK
  end
  return r
end
local function bitop(a, b, op)
  local _, aa = parts(a)
  local _, bb = parts(b)
  local n = math.max(#aa, #bb) + 1
  local x, y, r = twos(a, n), twos(b, n), {}
  for i = 1, n do
    if op == "and" then r[i] = x[i] & y[i]
    elseif op == "or" then r[i] = x[i] | y[i]
    else r[i] = x[i] ~ y[i] end
  end
  local s = 1
  if r[n] & (1 << (WIDTH - 1)) ~= 0 then
    s = -1
    local carry = 1
    for i = 1, n do
      local v = (MASK ~ r[i]) + carry
      r[i], carry = v & MASK, v >> WIDTH
    end
  end
  return pack(s, r)
end
--- Bitwise and with infinite two's-complement semantics.
function M.band(a, b)
  if type(a) == "number" and type(b) == "number" then return a & b end
  return bitop(a, b, "and")
end
--- Bitwise or with infinite two's-complement semantics.
function M.bor(a, b)
  if type(a) == "number" and type(b) == "number" then return a | b end
  return bitop(a, b, "or")
end
--- Bitwise exclusive or with infinite two's-complement semantics.
function M.bxor(a, b)
  if type(a) == "number" and type(b) == "number" then return a ~ b end
  return bitop(a, b, "xor")
end
--- Bitwise complement with infinite two's-complement semantics.
function M.bnot(a)
  if type(a) == "number" then return ~a end
  return M.sub(M.neg(a), 1)
end
--- Return the number of bits in the absolute value.
function M.bit_length(a)
  if type(a) == "number" then
    if a < 0 then a = -a end
    local n = 0
    while a ~= 0 do a, n = a >> 1, n + 1 end
    return n
  end
  local aa, n, x = a.limbs, (#a.limbs - 1) * WIDTH, a.limbs[#a.limbs]
  while x ~= 0 do x, n = x >> 1, n + 1 end
  return n
end
--- Count set bits in the absolute value.
function M.bit_count(a)
  if type(a) == "number" then
    if a < 0 then a = -a end
    local n = 0
    while a ~= 0 do a, n = a & (a - 1), n + 1 end
    return n
  end
  local n = 0
  for _, x in ipairs(a.limbs) do
    while x ~= 0 do x, n = x & (x - 1), n + 1 end
  end
  return n
end

local digits = "0123456789abcdefghijklmnopqrstuvwxyz"
--- Format an integer in base 2 through 36, without a prefix.
function M.tostring(a, base)
  base = base or 10
  if base < 2 or base > 36 then fail("ValueError", "base must be >= 2 and <= 36") end
  if type(a) == "number" and base == 10 then return tostring(a) end
  if a == 0 then return "0" end
  local s, aa = parts(a)
  local out = {}
  -- Decimal chunks avoid one full-limb division per character.
  local radix = base == 10 and 10000000 or base
  repeat
    local rem
    aa, rem = smalldiv(aa, radix)
    out[#out + 1] = base == 10 and (#aa == 0 and tostring(rem) or string.format("%07d", rem))
      or digits:sub(rem + 1, rem + 1)
  until #aa == 0
  local result = {}
  if s < 0 then result[1] = "-" end
  for i = #out, 1, -1 do result[#result + 1] = out[i] end
  return table.concat(result)
end
mt.__tostring = M.tostring
--- Parse Python ASCII integer syntax, including underscores and base-zero prefixes.
-- Callers normalize Unicode decimal digits/whitespace before invoking this hook.
function M.parse(text, base)
  base = base or 10
  if base ~= 0 and (base < 2 or base > 36) then fail("ValueError", "int() base must be >= 2 and <= 36, or 0") end
  local original, requested = text, base
  local function invalid() fail("ValueError", "invalid literal for int() with base " .. requested .. ": " .. string.format("%q", original)) end
  text = text:match("^%s*(.-)%s*$")
  local s, pos = 1, 1
  if text:sub(pos, pos) == "-" then s, pos = -1, pos + 1
  elseif text:sub(pos, pos) == "+" then pos = pos + 1 end
  local prefix = text:sub(pos, pos + 1):lower()
  local pbase = prefix == "0x" and 16 or (prefix == "0o" and 8 or (prefix == "0b" and 2 or nil))
  local zero_mode = base == 0 and not pbase
  if pbase and (base == 0 or base == pbase) then
    base, pos = pbase, pos + 2
    if text:sub(pos, pos) == "_" then pos = pos + 1 end
  elseif base == 0 then base = 10 end
  local first_zero = text:sub(pos, pos) == "0"
  local value, seen, underscore = 0, false, false
  for i = pos, #text do
    local c = text:byte(i)
    if c == 95 then
      if not seen or underscore then invalid() end
      underscore = true
    else
      local d = c >= 48 and c <= 57 and c - 48 or (c >= 65 and c <= 90 and c - 55 or (c >= 97 and c <= 122 and c - 87 or 99))
      if d >= base or (zero_mode and first_zero and d ~= 0) then invalid() end
      value, seen, underscore = M.add(M.mul(value, base), d), true, false
    end
  end
  if not seen or underscore then invalid() end
  return s < 0 and M.neg(value) or value
end

--- Truncate a finite float exactly, rejecting infinities and NaNs.
function M.from_float(x)
  if x ~= x then fail("ValueError", "cannot convert float NaN to integer") end
  if x == math.huge or x == -math.huge then fail("OverflowError", "cannot convert float infinity to integer") end
  local native = math.tointeger(x < 0 and math.ceil(x) or math.floor(x))
  if native then return native end
  local sign = x < 0 and -1 or 1
  local f, e = math.frexp(math.abs(x))
  local mantissa = math.tointeger(f * 9007199254740992.0)
  local value = e >= 53 and M.lshift(mantissa, e - 53) or M.rshift(mantissa, 53 - e)
  return sign < 0 and M.neg(value) or value
end
local function rounded_ratio(a, b, exponent)
  local shift = 52 - exponent
  if exponent < -1022 then shift = 1074 end
  local q, r
  if shift >= 0 then q, r = M.divmod(M.lshift(a, shift), b)
  else b = M.lshift(b, -shift); q, r = M.divmod(a, b) end
  local c = M.cmp(M.lshift(r, 1), b)
  if c > 0 or (c == 0 and M.band(q, 1) ~= 0) then q = M.add(q, 1) end
  return math.ldexp(q + 0.0, -shift)
end
--- Convert an integer to nearest-even binary64, rejecting overflow.
function M.to_float(a)
  if type(a) == "number" then return a + 0.0 end
  local sign = a.sign
  local magnitude = M.abs(a)
  local bits = M.bit_length(magnitude)
  if bits > 1024 then fail("OverflowError", "int too large to convert to float") end
  local result = rounded_ratio(magnitude, 1, bits - 1)
  if result == math.huge then fail("OverflowError", "int too large to convert to float") end
  return sign < 0 and -result or result
end
--- Compare an integer with a float exactly; NaN returns nil (unordered).
function M.cmp_float(a, b)
  if b ~= b then return nil end
  if b == math.huge then return -1 end
  if b == -math.huge then return 1 end
  local truncated = M.from_float(b)
  local c = M.cmp(a, truncated)
  if c ~= 0 then return c end
  if b % 1 ~= 0 then return b < 0 and 1 or -1 end
  return 0
end
--- Divide integers directly into binary64 without overflowing intermediates.
function M.truediv(a, b)
  if b == 0 then fail("ZeroDivisionError", "division by zero") end
  if type(a) == "number" and type(b) == "number"
      and a >= -9007199254740992 and a <= 9007199254740992
      and b >= -9007199254740992 and b <= 9007199254740992 then return a / b end
  local negative = (M.cmp(a, 0) < 0) ~= (M.cmp(b, 0) < 0)
  a, b = M.abs(a), M.abs(b)
  if a == 0 then return negative and -0.0 or 0.0 end
  local e = M.bit_length(a) - M.bit_length(b)
  if e >= 0 then if M.cmp(a, M.lshift(b, e)) < 0 then e = e - 1 end
  elseif M.cmp(M.lshift(a, -e), b) < 0 then e = e - 1 end
  if e > 1023 then fail("OverflowError", "integer division result too large for a float") end
  if e < -1075 then return negative and -0.0 or 0.0 end
  local result = rounded_ratio(a, b, e)
  if result == math.huge then fail("OverflowError", "integer division result too large for a float") end
  return negative and -result or result
end
--- Hash an integer with CPython's 64-bit numeric modulus.
function M.hash(a)
  local p = 2305843009213693951
  local h
  if type(a) == "number" then
    h = a % p
    if a < 0 and h ~= 0 then h = h - p end
  else
    h = M.mod(M.abs(a), p)
    if a.sign < 0 then h = -h end
  end
  return h == -1 and -2 or h
end
local function inverse(a, modulus)
  local oldr, r, oldt, t = modulus, M.mod(a, modulus), 0, 1
  while r ~= 0 do
    local q, rem = M.divmod(oldr, r)
    oldr, r, oldt, t = r, rem, t, M.sub(oldt, M.mul(q, t))
  end
  if oldr ~= 1 then fail("ValueError", "base is not invertible for the given modulus") end
  return M.mod(oldt, modulus)
end
--- Exponentiate integers; a modulus supports negative exponents via inversion.
function M.pow(a, b, modulus)
  local negative_modulus = false
  if modulus ~= nil then
    if modulus == 0 then fail("ValueError", "pow() 3rd argument cannot be 0") end
    negative_modulus = M.cmp(modulus, 0) < 0
    modulus = M.abs(modulus)
    if modulus == 1 then return 0 end
    if M.cmp(b, 0) < 0 then a, b = inverse(a, modulus), M.neg(b) end
    a = M.mod(a, modulus)
  elseif M.cmp(b, 0) < 0 then
    if a == 0 then fail("ZeroDivisionError", "0.0 cannot be raised to a negative power") end
    return M.to_float(a) ^ M.to_float(b)
  end
  local result = 1
  while b ~= 0 do
    if M.band(b, 1) ~= 0 then
      result = M.mul(result, a)
      if modulus then result = M.mod(result, modulus) end
    end
    b = M.rshift(b, 1)
    if b ~= 0 then
      a = M.mul(a, a)
      if modulus then a = M.mod(a, modulus) end
    end
  end
  if negative_modulus and result ~= 0 then result = M.sub(result, modulus) end
  return result
end

--- Read a byte string as an integer in the requested byte order.
function M.from_bytes(data, order, signed)
  order = order or "big"
  if order ~= "big" and order ~= "little" then fail("ValueError", "byteorder must be either 'little' or 'big'") end
  local n, limbs = #data, {}
  if n == 0 then return 0 end
  local high = data:byte(order == "big" and 1 or n)
  local negative = signed and high >= 128
  local carry = negative and 1 or 0
  for i = 0, n - 1 do
    local byte = data:byte(order == "big" and n - i or i + 1)
    if negative then
      byte = (255 ~ byte) + carry
      carry, byte = byte >> 8, byte & 255
    end
    local limb = i // 3 + 1
    limbs[limb] = (limbs[limb] or 0) | (byte << ((i % 3) * 8))
  end
  return pack(negative and -1 or 1, limbs)
end

--- Encode an integer as exactly length bytes, checking signedness and bounds.
function M.to_bytes(value, length, order, signed)
  length, order = length or 1, order or "big"
  if order ~= "big" and order ~= "little" then fail("ValueError", "byteorder must be either 'little' or 'big'") end
  if M.cmp(length, 0) < 0 then fail("ValueError", "length argument must be non-negative") end
  if type(length) == "table" or length > MAX // 8 then fail("OverflowError", "Python int too large to convert to C ssize_t") end
  local negative = M.cmp(value, 0) < 0
  if negative and not signed then fail("OverflowError", "can't convert negative int to unsigned") end
  if length == 0 then
    if value == 0 or (signed and value == -1) then return "" end
    fail("OverflowError", "int too big to convert")
  end
  local bits = M.bit_length(negative and M.bnot(value) or value)
  if bits > length * 8 - (signed and 1 or 0) then fail("OverflowError", "int too big to convert") end
  local _, limbs = parts(value)
  local out, carry = {}, negative and 1 or 0
  for i = 0, length - 1 do
    local byte = ((limbs[i // 3 + 1] or 0) >> ((i % 3) * 8)) & 255
    if negative then
      byte = (255 ~ byte) + carry
      carry, byte = byte >> 8, byte & 255
    end
    out[order == "big" and length - i or i + 1] = string.char(byte)
  end
  return table.concat(out)
end
return M
