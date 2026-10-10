-- Standalone DSPB01 decoding for compiled store modules.
local J = require("diaspore.json")
local NULL = require("diaspore.values").NULL
local M = {}
local OPS = { "const", "name", "attr", "index", "slicelit", "starred", "call", "binop",
  "unary", "boolop", "compare", "cond", "list", "tuple", "set", "dict", "fstring",
  "func", "funcexpr", "yield", "yieldfrom", "namedexpr", "expr", "assign", "augassign",
  "if", "while", "for", "break", "continue", "pass", "return", "class", "raise", "try",
  "with", "import", "importfrom", "del" }
local BIN = { "add", "sub", "mul", "div", "floordiv", "mod", "pow", "lshift", "rshift",
  "bitand", "bitor", "bitxor", "matmul" }
local CMP = { "eq", "ne", "lt", "le", "gt", "ge", "in", "notin", "is", "isnot" }
local UNARY = { "neg", "pos", "not", "invert" }
local CONST = { "int", "float", "str", "bytes", "bool", "none", "ellipsis", "complex" }
local OPT = { slicelit = { "lower", "upper", "step" }, yield = { "v" },
  ["return"] = { "v" }, raise = { "exc", "cause" }, import = { "alias" } }
local function fail(message) error("invalid DSPB: " .. message, 0) end
local function array() return { [J.ARRAY] = true } end
local function nonempty(target, key, items)
  if #items > 0 then target[key] = items end
end

--- Decode and validate the compact IR of a standalone DSPB01 bundle.
function M.decode(data)
  if type(data) ~= "string" or data:sub(1, 6) ~= "DSPB01" then fail("expected DSPB01") end
  local pos, strings, depth = 7, {}, 0
  local function byte()
    local b = data:byte(pos)
    if b == nil then fail("truncated payload") end
    pos = pos + 1
    return b
  end
  local function unsigned()
    local n, shift = 0, 0
    for _ = 1, 9 do
      local b = byte()
      n = n | ((b & 127) << shift)
      if b < 128 then return n end
      shift = shift + 7
    end
    fail("varint overflow")
  end
  local function count()
    local n = unsigned()
    if n > #data - pos + 1 then fail("count exceeds remaining payload") end
    return n
  end
  local function flags(mask)
    local b = byte()
    if b & ~mask ~= 0 then fail("invalid flags") end
    return b
  end
  local function boolean() return flags(1) == 1 end
  local function enum(items)
    local item = items[byte() + 1]
    if item == nil then fail("invalid enum") end
    return item
  end
  local function str()
    local value = strings[unsigned() + 1]
    if value == nil then fail("invalid string reference") end
    return value
  end
  local function strlist()
    local items = array()
    for i = 1, count() do items[i] = str() end
    return items
  end
  for i = 1, count() do
    local n = unsigned()
    if n > #data - pos + 1 then fail("truncated string table") end
    local text = data:sub(pos, pos + n - 1)
    pos = pos + n
    -- Lua's lax UTF-8 mode admits surrogates, but also values above Unicode.
    local length = utf8.len(text, 1, -1, true)
    if not length then fail("invalid UTF-8 in string table") end
    for _, cp in utf8.codes(text, true) do
      if cp > 0x10ffff then fail("invalid Unicode codepoint") end
    end
    strings[i] = text
  end
  local function literal(target, key, text)
    target[key] = text
    if not text:find("\237[\160-\191]") then return end
    local points = array()
    for _, cp in utf8.codes(text, true) do points[#points + 1] = cp end
    target.codepoints = points
    local parts, i = {}, 1
    while i <= #points do
      local cp, nextcp = points[i], points[i + 1]
      if cp >= 0xd800 and cp <= 0xdbff and nextcp and nextcp >= 0xdc00 and nextcp <= 0xdfff then
        cp = 0x10000 + (cp - 0xd800) * 1024 + nextcp - 0xdc00
        i = i + 1
      end
      parts[#parts + 1] = utf8.char(cp)
      i = i + 1
    end
    target[key] = table.concat(parts)
  end
  local node
  local function nodes()
    local items = array()
    for i = 1, count() do items[i] = node() end
    return items
  end
  local function keywords()
    local items = array()
    for i = 1, count() do
      local name = boolean() and str() or NULL
      items[i] = { n = name, v = node() }
    end
    return items
  end
  node = function()
    depth = depth + 1
    if depth > 512 then fail("node nesting too deep") end
    local op = enum(OPS)
    local opts = OPT[op] or {}
    local f = flags((1 << (#opts + 1)) - 1)
    local ln = f & 1 ~= 0 and unsigned() or nil
    local n = { op = op }
    if op == "const" then
      n.t = enum(CONST)
      if n.t == "str" then literal(n, "v", str())
      elseif n.t == "int" or n.t == "float" or n.t == "bytes" then n.v = str()
      elseif n.t == "complex" then n.re, n.im = str(), str()
      elseif n.t == "bool" then n.v = boolean() end
    elseif op == "name" then n.id = str()
    elseif op == "attr" then n.o, n.n = node(), str()
    elseif op == "index" then n.o, n.k = node(), node()
    elseif op == "slicelit" or op == "yield" or op == "return" or op == "raise" then
      for i, key in ipairs(opts) do if f & (1 << i) ~= 0 then n[key] = node() end end
    elseif op == "starred" or op == "yieldfrom" or op == "expr" then n.v = node()
    elseif op == "call" then
      n.f = node()
      nonempty(n, "args", nodes())
      nonempty(n, "kw", keywords())
    elseif op == "binop" or op == "augassign" then
      n.kind = enum(BIN)
      if op == "binop" then n.l, n.r = node(), node()
      else n.target, n.v = node(), node() end
    elseif op == "unary" then n.kind, n.v = enum(UNARY), node()
    elseif op == "boolop" then
      n.kind = boolean() and "or" or "and"
      n.values = nodes()
      if #n.values < 2 then fail("boolop needs at least two values") end
    elseif op == "compare" then
      n.left, n.ops = node(), array()
      for i = 1, count() do n.ops[i] = enum(CMP) end
      n.comps = nodes()
      if #n.ops == 0 or #n.ops ~= #n.comps then fail("invalid comparison operands") end
    elseif op == "cond" then n.test, n["then"], n["else"] = node(), node(), node()
    elseif op == "list" or op == "tuple" or op == "set" then nonempty(n, "elts", nodes())
    elseif op == "dict" then
      local entries = array()
      for i = 1, count() do
        local key = boolean() and node() or NULL
        entries[i] = { k = key, v = node() }
      end
      nonempty(n, "entries", entries)
    elseif op == "fstring" then
      n.parts = array()
      for i = 1, count() do
        local part, pf = {}, flags(3)
        if pf == 0 then literal(part, "s", str())
        else
          if pf & 1 == 0 then fail("invalid fstring flags") end
          part.v = node()
          local conv = byte()
          if conv > 3 then fail("invalid fstring conversion") end
          part.conv = ({ "r", "s", "a" })[conv]
          if pf & 2 ~= 0 then part.spec = node() end
        end
        n.parts[i] = part
      end
    elseif op == "func" or op == "funcexpr" then
      n.name = str()
      local gf = flags(15)
      for i, key in ipairs({ "gen", "coro", "asyncgen", "annotation_scope" }) do
        if gf & (1 << (i - 1)) ~= 0 then n[key] = true end
      end
      local pf, p = flags(7), {}
      local posonly = pf & 4 ~= 0 and unsigned() or 0
      if posonly > 0 then p.posonly = posonly end
      local args = strlist()
      if posonly > #args then fail("invalid positional-only count") end
      nonempty(p, "args", args)
      nonempty(p, "defaults", nodes())
      if pf & 1 ~= 0 then p.vararg = str() end
      nonempty(p, "kwonly", strlist())
      local defaults = array()
      for i = 1, count() do defaults[i] = boolean() and node() or NULL end
      nonempty(p, "kwdefaults", defaults)
      if pf & 2 ~= 0 then p.kwarg = str() end
      n.params = p
      nonempty(n, "locals", strlist())
      nonempty(n, "gdecl", strlist())
      nonempty(n, "ndecl", strlist())
      n.body = nodes()
    elseif op == "namedexpr" then
      n.target, n.v = node(), node()
      if n.target.op ~= "name" then fail("namedexpr requires a name target") end
    elseif op == "assign" then
      n.targets, n.v = nodes(), node()
      if #n.targets == 0 then fail("assignment requires a target") end
    elseif op == "if" or op == "while" then
      n.test = node()
      n[op == "if" and "then" or "body"] = nodes()
      nonempty(n, "orelse", nodes())
    elseif op == "for" then
      n.target, n.iter, n.body = node(), node(), nodes()
      nonempty(n, "orelse", nodes())
    elseif op == "class" then
      n.name = str()
      local cf = flags(3)
      if cf & 2 ~= 0 then n.binding = str() end
      if cf & 1 ~= 0 then n.kwargs = keywords() end
      nonempty(n, "bases", nodes())
      n.body = nodes()
    elseif op == "try" then
      if boolean() then n.star = true end
      n.body = nodes()
      local handlers = array()
      for i = 1, count() do
        local hf, handler = flags(3), {}
        if hf & 1 ~= 0 then handler.t = node() end
        if hf & 2 ~= 0 then handler.name = str() end
        handler.body = nodes()
        handlers[i] = handler
      end
      nonempty(n, "handlers", handlers)
      nonempty(n, "orelse", nodes())
      nonempty(n, "finalbody", nodes())
    elseif op == "with" then
      n.items = array()
      for i = 1, count() do
        local named = boolean()
        local item = { ctx = node() }
        if named then item.name = node() end
        n.items[i] = item
      end
      n.body = nodes()
    elseif op == "import" then
      n.module = str()
      local bind = byte()
      if bind > 2 then fail("invalid import binding") end
      n.bind = ({ "root", "none" })[bind]
      if f & 2 ~= 0 then n.alias = str() end
    elseif op == "importfrom" then
      n.module, n.names = str(), array()
      for i = 1, count() do n.names[i] = { n = str(), alias = str() } end
    elseif op == "del" then n.targets = nodes()
    end
    n.ln = ln
    depth = depth - 1
    return n
  end
  if unsigned() ~= 1 then fail("unsupported semantic version") end
  local bundle = { diaspore = 1, main = str(), modules = {} }
  for _ = 1, count() do
    local name, file = str(), str()
    if bundle.modules[name] then fail("duplicate module name") end
    local module = { name = name, file = file }
    if boolean() then module.src = strlist() end
    module.body = nodes()
    bundle.modules[name] = module
  end
  if not bundle.modules[bundle.main] then fail("main module is missing") end
  if pos ~= #data + 1 then fail("trailing payload bytes") end
  return bundle
end

return M
