-- Installed compiled code stays private; interpreters receive independent IR copies.
local J = require("diaspore.json")
local V = require("diaspore.values")
local H = require("diaspore.helpers")
local Big = require("diaspore.bigint")
local SHA256 = require("diaspore.sha256")
local M = {}
local stores = setmetatable({}, { __mode = "k" })
local installed, compiler
local source = debug.getinfo(1, "S").source
local directory = source:match("^@(.+[/\\])")

local function fail(message)
  error("diaspore-lua: " .. message, 0)
end

local function read_artifact(name)
  if not directory then fail("cannot locate installed " .. name .. " from module source") end
  local path = directory .. "../" .. name
  local file, message = io.open(path, "rb")
  if not file then fail("cannot load installed " .. name .. ": " .. tostring(message)) end
  local text, read_error = file:read("a")
  file:close()
  if not text then fail("cannot read installed " .. name .. ": " .. tostring(read_error)) end
  return J.parse(text)
end

local function copy(value, seen)
  if type(value) ~= "table" or value == V.NULL or Big.is_bigint(value) then return value end
  seen = seen or {}
  if seen[value] then return seen[value] end
  local result = {}
  seen[value] = result
  for key, item in pairs(value) do result[key] = copy(item, seen) end
  return result
end

local function object(value)
  return type(value) == "table" and value ~= V.NULL and not Big.is_bigint(value) and not value[J.ARRAY]
end

local function validate(store)
  if not object(store) or store.diaspore_stdlib ~= 1 then fail("invalid compiled stdlib store: expected diaspore_stdlib version 1") end
  if type(store.id) ~= "string" or #store.id ~= 71 or not store.id:match("^sha256:[0-9a-f]+$") then
    fail("invalid compiled stdlib store id: expected sha256:<64 lowercase hexadecimal digits>")
  end
  if not object(store.modules) then fail("invalid compiled stdlib store: modules must be an object") end
  for name, module in pairs(store.modules) do
    if name ~= J.ORDER then
      if type(name) ~= "string" or not object(module) or module.name ~= name or type(module.file) ~= "string"
          or type(module.body) ~= "table" or module.body == V.NULL or module.body[J.ORDER] then
        fail("invalid compiled stdlib module: " .. tostring(name))
      end
    end
  end
  local canonical = J.stringify_raw({ diaspore_stdlib = 1, modules = store.modules }, false)
  local computed = "sha256:" .. SHA256.digest(canonical)
  if store.id ~= computed then
    fail("compiled stdlib content id mismatch: declared " .. store.id .. ", computed " .. computed)
  end
end

local function hex(bytes)
  return (bytes:gsub(".", function(byte) return string.format("%02x", byte:byte()) end))
end

local function read_container()
  if not directory then fail("cannot locate installed stdlib.dsps from module source") end
  local file, message = io.open(directory .. "../stdlib.dsps", "rb")
  if not file then fail("cannot load installed stdlib.dsps: " .. tostring(message)) end
  local ok, result = pcall(function()
    local header = file:read(42)
    if not header or #header ~= 42 or header:sub(1, 6) ~= "DSPS02" then
      fail("invalid compiled stdlib container header")
    end
    local length = string.unpack("<I4", header, 7)
    local size = file:seek("end")
    if not size or length < 38 or length > 64 * 1024 * 1024 or length > size - 42 then
      fail("invalid compiled stdlib index length")
    end
    assert(file:seek("set", 42))
    local index = file:read(length)
    if not index or #index ~= length then fail("truncated compiled stdlib index") end
    if SHA256.digest(index) ~= hex(header:sub(11, 42)) then
      fail("compiled stdlib index digest mismatch")
    end
    local pos = 1
    local function take(n)
      if n > #index - pos + 1 then fail("truncated compiled stdlib index record") end
      local bytes = index:sub(pos, pos + n - 1)
      pos = pos + n
      return bytes
    end
    local function uint() return string.unpack("<I4", take(4)) end
    local function text()
      local n = string.unpack("<I2", take(2))
      local value = take(n)
      if n == 0 or value:find("\0", 1, true) or not utf8.len(value) then
        fail("invalid compiled stdlib index text")
      end
      return value
    end
    local raw = { id = "sha256:" .. hex(take(32)), modules = {}, records = {},
      names = {}, file = file, payload_start = 42 + length }
    local dictionary_length = string.unpack("<I2", take(2))
    if dictionary_length > 32768 then fail("invalid compiled stdlib dictionary length") end
    if dictionary_length > #index - pos + 1 then fail("truncated compiled stdlib dictionary") end
    -- Raw payloads do not use the dictionary, but it is covered by the index hash.
    pos = pos + dictionary_length
    local count = uint()
    if count > (#index - pos + 1) // 51 then fail("invalid compiled stdlib module count") end
    local previous, offset = nil, 0
    local identifier = require("diaspore.identifier")
    for i = 1, count do
      local name, path = text(), text()
      if previous and name <= previous or name:sub(1, 1) == "."
          or name:sub(-1) == "." or name:find("..", 1, true) then
        fail("invalid or unsorted compiled stdlib module name")
      end
      for part in name:gmatch("[^.]+") do
        if not identifier.isidentifier(part) then fail("invalid compiled stdlib module name") end
      end
      local start, stored, decoded = uint(), uint(), uint()
      local codec = take(1):byte()
      if codec ~= 0 then
        fail("unsupported compiled stdlib codec " .. codec .. " (diaspore-lua requires raw codec 0)")
      end
      if start ~= offset or stored < 6 or stored > 256 * 1024 * 1024
          or decoded ~= stored or start + stored > 0xffffffff
          or start + stored > size - raw.payload_start then
        fail("invalid compiled stdlib payload span")
      end
      raw.records[name] = { file = path, offset = start, length = stored, digest = hex(take(32)) }
      raw.names[i] = name
      previous, offset = name, start + stored
    end
    if pos ~= #index + 1 then fail("trailing compiled stdlib index bytes") end
    if raw.payload_start + offset ~= size then fail("trailing compiled stdlib payload bytes") end
    return raw
  end)
  if not ok then file:close(); error(result, 0) end
  return result
end

local function handle(raw)
  local result = setmetatable({}, {
    __index = function(_, key) if key == "id" then return raw.id end end,
    __newindex = function() fail("compiled stdlib handles are immutable") end,
    __metatable = false,
  })
  stores[result] = raw
  return result
end

--- Load an installed store lazily, or isolate a caller-supplied parsed store.
function M.load(override)
  if override ~= nil then
    if stores[override] then return override end
    local raw = copy(override)
    validate(raw)
    return handle(raw)
  end
  if installed == nil then installed = handle(read_container()) end
  return installed
end

local function raw_store(store)
  local raw = stores[store]
  if raw == nil then fail("expected a compiled stdlib handle") end
  return raw
end

--- Test membership without cloning a compiled module.
function M.has_module(store, name)
  local raw = raw_store(store)
  return (raw.records or raw.modules)[name] ~= nil
end

local function module_ir(store, name)
  local raw = raw_store(store)
  local module = raw.modules[name]
  if module ~= nil or not raw.records then return module end
  local record = raw.records[name]
  if not record then return nil end
  if not raw.file:seek("set", raw.payload_start + record.offset) then
    fail("cannot seek compiled stdlib payload: " .. name)
  end
  local payload = raw.file:read(record.length)
  if not payload or #payload ~= record.length then fail("truncated compiled stdlib payload: " .. name) end
  if SHA256.digest(payload) ~= record.digest then fail("compiled stdlib payload digest mismatch: " .. name) end
  local bundle = require("diaspore.dspb").decode(payload)
  module = bundle.modules[name]
  local first, only = next(bundle.modules)
  if bundle.main ~= name or not module or first ~= name or next(bundle.modules, first) ~= nil
      or only.name ~= name or module.file ~= record.file then
    fail("compiled stdlib payload module mismatch: " .. name)
  end
  raw.modules[name] = module
  return module
end

--- Return indexed file metadata without decoding a module.
function M.module_file(store, name)
  local raw = raw_store(store)
  local module = (raw.records or raw.modules)[name]
  return module and module.file
end

--- Return a private raw IR copy, or nil for an unknown module.
function M.module(store, name)
  return copy(module_ir(store, name))
end

--- Return a fresh sorted array of installed module names.
function M.module_names(store)
  local names = {}
  local raw = raw_store(store)
  if raw.names then
    for i, name in ipairs(raw.names) do names[i] = name end
    return names
  end
  for name in pairs(raw.modules) do
    if type(name) == "string" then names[#names + 1] = name end
  end
  table.sort(names)
  return names
end

local function sandbox(value)
  if value == V.NULL then return V.NONE end
  if type(value) ~= "table" or Big.is_bigint(value) then return value end
  if value[J.ARRAY] or #value > 0 and not value[J.ORDER] then
    local items = {}
    for index, item in ipairs(value) do items[index] = sandbox(item) end
    return V.list(items)
  end
  local result = V.new_dict()
  local order = value[J.ORDER]
  if order then
    for _, key in ipairs(order) do V.dict_set_hk(result, "s" .. key, key, sandbox(value[key])) end
  else
    for key, item in pairs(value) do V.dict_set_hk(result, "s" .. key, key, sandbox(item)) end
  end
  return result
end

--- Build the private bridge without opening an artifact until a method is called.
function M.make_module(override)
  local store
  local function resolve()
    if store == nil then store = M.load(override) end
    return store
  end
  local ns = { __name__ = "_diaspore_stdlib", __doc__ = V.NONE }
  local function add(name, count, fn)
    ns[name] = V.builtin(name, function(I, args, kw)
      H.arg_count(name, args, count, count)
      H.no_kw(name, kw)
      return fn(I, args)
    end)
  end
  add("get_id", 0, function() return resolve().id end)
  add("module_names", 0, function(I)
    local names = M.module_names(resolve())
    I:fuel_n(#names)
    return V.list(names)
  end)
  add("get_module", 1, function(I, args)
    local name = H.want_str("module name", args[1])
    local module = module_ir(resolve(), name)
    if module == nil then return V.NONE end
    I:fuel_n(1)
    return sandbox(module)
  end)
  return { kind = "nativemodule", name = "_diaspore_stdlib", ns = ns }
end

--- Read the installed compiler only on demand and isolate its mutable IR caches.
function M.compiler_bundle(override)
  if override ~= nil then return copy(override) end
  if compiler == nil then compiler = read_artifact("dspc.dsp.json") end
  return copy(compiler)
end

return M
