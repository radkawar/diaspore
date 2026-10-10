-- Native ABC bookkeeping keeps callback frames identical to CPython's _abc.
local V = require("diaspore.values")
local H = require("diaspore.helpers")
local iter = require("diaspore.iter")
local pcall = require("diaspore.protected").call
local M = {}
local function kind(value) return type(value) == "table" and value.kind end

--- Construct per-interpreter ABC registries and invalidation caches.
function M.make_module(I)
  local token = 0
  local data_type
  data_type = V.btype("_abc_data", function(_, args, kw)
    H.arg_count("_abc_data", args, 0, 0); H.no_kw("_abc_data", kw)
    return { kind = "nativeobject", nkind = "_abc._abc_data", pytype = data_type,
      props = {}, methods = {}, state = { registry = {}, positive = {}, negative = {}, version = token } }
  end)
  data_type.ns.__module__ = "_abc"
  local function optional(object, name, fallback)
    local saved = #I.stack
    local ok, value = pcall(I.get_attr, I, object, name)
    if ok then return value end
    if V.is_exc(value) and V.exc_isa(value.value, "AttributeError") then
      I:trim(saved)
      return fallback
    end
    error(value, 0)
  end
  local function state(cls)
    local data = I:get_attr(cls, "_abc_impl")
    if kind(data) ~= "nativeobject" or data.pytype ~= data_type then
      V.throw("TypeError", "_abc_impl is set to a wrong type")
    end
    return data.state
  end
  local function subclass(left, right)
    return I:truth(I:call(I.builtins.issubclass, { left, right }, {}))
  end
  local function hook(cls, name, args)
    return I:call(I:get_attr(cls, name), args, {})
  end
  local function check_subclass(cls, candidate)
    if kind(candidate) ~= "class" and kind(candidate) ~= "btype" then
      V.throw("TypeError", "issubclass() arg 1 must be a class")
    end
    local data = state(cls)
    if data.positive[candidate] then return true end
    if data.version < token then
      data.negative, data.version = {}, token
    elseif data.negative[candidate] then return false end
    local method = optional(cls, "__subclasshook__", V.NONE)
    if method ~= V.NONE then
      local result = I:call(method, { candidate }, {})
      if result ~= V.NOT_IMPLEMENTED then
        if type(result) ~= "boolean" then V.throw("AssertionError", "__subclasshook__ must return either False, True, or NotImplemented") end
        if result then data.positive[candidate] = true else data.negative[candidate] = true end
        return result
      end
    end
    if V.issub(candidate, cls) then data.positive[candidate] = true; return true end
    if data.registry[candidate] then return true end
    for registered in pairs(data.registry) do
      if subclass(candidate, registered) then data.positive[candidate] = true; return true end
    end
    local children = hook(cls, "__subclasses__", {})
    if kind(children) ~= "list" then V.throw("TypeError", "__subclasses__() must return a list") end
    for _, child in ipairs(children.items) do
      if subclass(candidate, child) then data.positive[candidate] = true; return true end
    end
    data.negative[candidate] = true
    return false
  end
  local ns = {}
  local function add(name, count, fn)
    ns[name] = V.builtin(name, function(_, args, kw)
      H.arg_count(name, args, count, count); H.no_kw(name, kw)
      return fn(table.unpack(args))
    end)
  end
  add("get_cache_token", 0, function() return token end)
  add("_abc_init", 1, function(cls)
    local abstract = V.new_set(true)
    for _, entry in ipairs(V.dict_entries(V.proxy_dict(I:get_attr(cls, "__dict__")))) do
      if I:truth(optional(entry.v, "__isabstractmethod__", false)) then V.set_add(abstract, entry.k) end
    end
    for _, base in ipairs(iter.iter_to_array(I, I:get_attr(cls, "__bases__"))) do
      for _, name in ipairs(iter.iter_to_array(I, optional(base, "__abstractmethods__", V.tuple({})))) do
        local member = optional(cls, name, V.NONE)
        if I:truth(optional(member, "__isabstractmethod__", false)) then V.set_add(abstract, name) end
      end
    end
    I:set_attr(cls, "__abstractmethods__", abstract)
    I:set_attr(cls, "_abc_impl", I:call(data_type, {}, {}))
    return V.NONE
  end)
  add("_abc_register", 2, function(cls, candidate)
    if kind(candidate) ~= "class" and kind(candidate) ~= "btype" then V.throw("TypeError", "Can only register classes") end
    if subclass(candidate, cls) then return candidate end
    if subclass(cls, candidate) then V.throw("RuntimeError", "Refusing to create an inheritance cycle") end
    state(cls).registry[candidate] = true
    token = token + 1
    return candidate
  end)
  add("_abc_subclasscheck", 2, check_subclass)
  add("_abc_instancecheck", 2, function(cls, object)
    local candidate = I:get_attr(object, "__class__")
    local data = state(cls)
    if data.positive[candidate] then return true end
    local actual = I:call(I.types.type, { object }, {})
    if actual == candidate then
      if data.version == token and data.negative[candidate] then return false end
      return hook(cls, "__subclasscheck__", { candidate })
    end
    local result = hook(cls, "__subclasscheck__", { candidate })
    if I:truth(result) then return result end
    return hook(cls, "__subclasscheck__", { actual })
  end)
  add("_reset_registry", 1, function(cls) state(cls).registry = {}; return V.NONE end)
  add("_reset_caches", 1, function(cls)
    local data = state(cls)
    data.positive, data.negative = {}, {}
    return V.NONE
  end)
  add("_get_dump", 1, function(cls)
    local data = state(cls)
    local ref = I:get_attr(I:import_module("_weakref"), "ref")
    local function references(members)
      local result = V.new_set()
      for member in pairs(members) do V.set_add(result, I:call(ref, { member }, {})) end
      return result
    end
    return V.tuple({ references(data.registry), references(data.positive), references(data.negative), data.version })
  end)
  return { kind = "nativemodule", name = "_abc", ns = ns }
end

return M
