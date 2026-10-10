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

local function handle(raw)
  validate(raw)
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
    return handle(copy(override))
  end
  if installed == nil then installed = handle(read_artifact("stdlib.json")) end
  return installed
end

local function raw_store(store)
  local raw = stores[store]
  if raw == nil then fail("expected a compiled stdlib handle") end
  return raw
end

--- Test membership without cloning a compiled module.
function M.has_module(store, name)
  return raw_store(store).modules[name] ~= nil
end

--- Return a private raw IR copy, or nil for an unknown module.
function M.module(store, name)
  return copy(raw_store(store).modules[name])
end

--- Return a fresh sorted array of installed module names.
function M.module_names(store)
  local names = {}
  for name in pairs(raw_store(store).modules) do
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
    local module = raw_store(resolve()).modules[name]
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
