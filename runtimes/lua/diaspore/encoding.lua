-- Fast text codecs; other registered codecs execute in the bundled sandbox.
local V = require("diaspore.values")
local M = {}
local aliases = { utf8 = "utf-8", utf_8 = "utf-8", u8 = "utf-8", utf = "utf-8", cp65001 = "utf-8",
  ascii = "ascii", us_ascii = "ascii", ["646"] = "ascii", ansi_x3_4_1968 = "ascii", iso646_us = "ascii",
  latin1 = "latin-1", latin_1 = "latin-1", latin = "latin-1", l1 = "latin-1", iso_8859_1 = "latin-1",
  iso8859_1 = "latin-1", iso_8859_1_1987 = "latin-1", cp819 = "latin-1", ibm819 = "latin-1", ["8859"] = "latin-1" }
local policies = { strict = true, ignore = true, replace = true, backslashreplace = true, surrogateescape = true }
function M.arguments(args, kw)
  local encoding, errors = args[1], args[2]
  for _, entry in ipairs(kw) do
    if entry.k == "encoding" then
      if encoding ~= nil then V.throw("TypeError", "encoding given by name and position") end
      encoding = entry.v
    elseif entry.k == "errors" then
      if errors ~= nil then V.throw("TypeError", "errors given by name and position") end
      errors = entry.v
    else V.throw("TypeError", "invalid keyword argument '" .. entry.k .. "'") end
  end
  return encoding, errors
end
local function codec_error(name, encoding, object, start, stop, reason)
  local exc = V.make_exc(name, encoding, object, start, stop, reason)
  exc.d.encoding, exc.d.object, exc.d.start, exc.d["end"], exc.d.reason = encoding, object, start, stop, reason
  V.throw_val(exc)
end
local function options(encoding, errors)
  if encoding == nil then encoding = "utf-8" end
  if errors == nil then errors = "strict" end
  if type(encoding) ~= "string" then V.throw("TypeError", "encoding must be str") end
  if type(errors) ~= "string" then V.throw("TypeError", "errors must be str") end
  if encoding:find("\0", 1, true) or errors:find("\0", 1, true) then V.throw("ValueError", "embedded null character") end
  return aliases[encoding:lower():gsub("[%-%s]", "_")], errors, encoding
end
local function delegate(I, operation, value, encoding, errors)
  if I and (I:has_module("codecs") or I.modules.codecs) then
    local module = I:import_module("codecs")
    return I:call(I:get_attr(module, operation), { value, encoding, errors }, {})
  end
  V.throw("LookupError", "unknown encoding: " .. encoding .. " (bundle it with `import encodings." .. encoding:gsub("%-", "_") .. "`)")
end
local function escaped(cp)
  if cp <= 255 then return string.format("\\x%02x", cp) end
  if cp <= 65535 then return string.format("\\u%04x", cp) end
  return string.format("\\U%08x", cp)
end
function M.encode(I, text, encoding, errors)
  local codec, policy, original = options(encoding, errors)
  if not codec or not policies[policy] then return delegate(I, "encode", text, original, policy) end
  if I then I:fuel_n(#text) end
  if codec == "utf-8" and not text:find("\237[\160-\191]") then return V.bytes(text) end
  local limit = codec == "ascii" and 127 or 255
  local chars, codepoints = {}, {}
  for _, cp in utf8.codes(text, true) do codepoints[#codepoints + 1] = cp end
  local i = 1
  while i <= #codepoints do
    local cp = codepoints[i]
    local invalid = codec == "utf-8" and cp >= 0xd800 and cp <= 0xdfff or codec ~= "utf-8" and cp > limit
    if not invalid then chars[#chars + 1] = codec == "utf-8" and utf8.char(cp) or string.char(cp); i = i + 1
    elseif policy == "surrogateescape" and cp >= 0xdc80 and cp <= 0xdcff then
      chars[#chars + 1] = string.char(cp - 0xdc00); i = i + 1
    elseif policy == "replace" then chars[#chars + 1] = "?"; i = i + 1
    elseif policy == "ignore" then i = i + 1
    elseif policy == "backslashreplace" then chars[#chars + 1] = escaped(cp); i = i + 1
    else
      local stop = i
      while stop < #codepoints do
        local next_cp = codepoints[stop + 1]
        if codec == "utf-8" and (next_cp < 0xd800 or next_cp > 0xdfff)
            or codec ~= "utf-8" and next_cp <= limit then break end
        stop = stop + 1
      end
      codec_error("UnicodeEncodeError", codec, text, i - 1, stop,
        codec == "utf-8" and "surrogates not allowed" or "ordinal not in range(" .. (limit + 1) .. ")")
    end
  end
  return V.bytes(table.concat(chars))
end
function M.decode(I, bytes, encoding, errors)
  local codec, policy, original = options(encoding, errors)
  if not codec or not policies[policy] then return delegate(I, "decode", bytes, original, policy) end
  local data, output = bytes.b, {}
  if I then I:fuel_n(#data) end
  if codec == "latin-1" then
    for i = 1, #data do output[#output + 1] = utf8.char(data:byte(i)) end
    return table.concat(output)
  end
  local i = 1
  while i <= #data do
    local byte = data:byte(i)
    if byte < 128 then output[#output + 1] = string.char(byte); i = i + 1
    else
      local stop, reason = i, codec == "ascii" and "ordinal not in range(128)" or "invalid start byte"
      local count = 0
      if codec == "utf-8" then
        if byte >= 0xc2 and byte <= 0xdf then count = 2
        elseif byte >= 0xe0 and byte <= 0xef then count = 3
        elseif byte >= 0xf0 and byte <= 0xf4 then count = 4 end
      end
      local valid = count > 0
      if valid then
        for offset = 1, count - 1 do
          local next_byte = data:byte(i + offset)
          if not next_byte then reason = "unexpected end of data"; stop = #data; valid = false; break end
          local low, high = 128, 191
          if offset == 1 then
            if byte == 0xe0 then low = 160 elseif byte == 0xed then high = 159
            elseif byte == 0xf0 then low = 144 elseif byte == 0xf4 then high = 143 end
          end
          if next_byte < low or next_byte > high then reason = "invalid continuation byte"; stop = i + offset - 1; valid = false; break end
        end
      end
      if valid then output[#output + 1] = data:sub(i, i + count - 1); i = i + count
      else
        if policy == "strict" then codec_error("UnicodeDecodeError", codec, bytes, i - 1, stop, reason)
        elseif policy == "replace" then output[#output + 1] = utf8.char(0xfffd)
        elseif policy == "backslashreplace" then
          for j = i, stop do output[#output + 1] = string.format("\\x%02x", data:byte(j)) end
        elseif policy == "surrogateescape" then
          for j = i, stop do output[#output + 1] = utf8.char(0xdc00 + data:byte(j)) end
        end
        i = stop + 1
      end
    end
  end
  return table.concat(output)
end
return M
