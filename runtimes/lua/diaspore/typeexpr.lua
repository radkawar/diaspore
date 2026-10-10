-- PEP 585/604 runtime types and CPython's typing substitution protocols.
local V = require("diaspore.values")
local H = require("diaspore.helpers")
local pcall = require("diaspore.protected").call
local M = {}
local function kind(value) return type(value) == "table" and value.kind or nil end
local function is_class(value) return kind(value) == "class" or kind(value) == "btype" end
local function tuple_items(value)
  if kind(value) == "tuple" then return value.items end
  if kind(value) == "instance" and kind(value.prim) == "tuple" then return value.prim.items end
end

--- Return the native representation of a type expression or its subclass.
function M.unwrap(value)
  if kind(value) == "instance" and value.prim ~= nil then
    local primitive = kind(value.prim)
    if primitive == "GenericAlias" or primitive == "UnionType" then return value.prim end
  end
  return value
end

local function optional(I, value, name)
  local saved = #I.stack
  local ok, result = pcall(I.get_attr, I, value, name)
  if ok then return result end
  if V.is_exc(result) and V.exc_isa(result.value, "AttributeError") then I:trim(saved); return nil end
  error(result, 0)
end

--- Construct an alias without evaluating, validating or expanding its arguments.
function M.alias(origin, args, I)
  return { kind = "GenericAlias", origin = origin, interp = I,
    args = tuple_items(args) and args or V.tuple({ args }), starred = false }
end

--- Collect free parameters in identity order through the typing protocols.
function M.parameters(I, value)
  value = M.unwrap(value)
  if value.parameters ~= nil then return value.parameters end
  local result, seen = {}, {}
  local function add(parameter)
    if not seen[parameter] then result[#result + 1] = parameter; seen[parameter] = true end
  end
  for _, arg in ipairs(tuple_items(value.args)) do
    if not is_class(arg) then
      if optional(I, arg, "__typing_subst__") ~= nil then add(arg)
      else
        local parameters = optional(I, arg, "__parameters__")
        for _, parameter in ipairs(tuple_items(parameters) or {}) do add(parameter) end
      end
    end
  end
  value.parameters = V.tuple(result)
  return value.parameters
end

local function interpreter(value)
  local raw = M.unwrap(value)
  if type(raw) ~= "table" then return nil end
  if raw.interp then return raw.interp end
  if raw.cls and raw.cls.interp then return raw.cls.interp end
  if raw.args then
    for _, arg in ipairs(tuple_items(raw.args) or {}) do
      local I = interpreter(arg)
      if I then return I end
    end
  end
end

--- Compare aliases structurally and unions as sets; nil means another type.
function M.equal(I, left, right, ops)
  left, right = M.unwrap(left), M.unwrap(right)
  local k = kind(left)
  if (k ~= "GenericAlias" and k ~= "UnionType") or kind(right) ~= k then return nil end
  I = I or interpreter(left) or interpreter(right)
  ops = ops or require("diaspore.ops")
  if k == "GenericAlias" then
    if not not left.starred ~= not not right.starred then return false end
    return ops.eq_elem(I, left.origin, right.origin) and ops.eq_elem(I, left.args, right.args)
  end
  local first, second = V.new_set(), V.new_set()
  for _, arg in ipairs(tuple_items(left.args)) do V.set_add(first, arg) end
  for _, arg in ipairs(tuple_items(right.args)) do V.set_add(second, arg) end
  return ops.eq(I, first, second)
end

--- Hash with the same constituent hashes as tuples and frozensets.
function M.hash(value)
  value = M.unwrap(value)
  local hash
  if value.kind == "GenericAlias" then
    hash = V.hash_value(value.origin) ~ V.hash_value(value.args)
  else
    local members = V.new_set(true)
    for _, arg in ipairs(tuple_items(value.args)) do V.set_add(members, arg) end
    hash = V.hash_value(members)
  end
  return hash == -1 and -2 or hash
end

--- Test the operands accepted by native union construction.
function M.is_type(value, I)
  local raw = M.unwrap(value)
  local k = kind(raw)
  if k == "class" or k == "btype" or k == "GenericAlias" or k == "UnionType" or raw == V.NONE then return true end
  if I and kind(value) == "instance" then
    local module = I.modules._typing
    if module then
      local alias_type = optional(I, module, "TypeAliasType")
      if alias_type ~= nil and value.cls == alias_type then return true end
    end
  end
  return false
end

--- Flatten native unions, normalize None, and deduplicate generic aliases.
function M.union(left, right, I)
  I = I or interpreter(left) or interpreter(right)
  if not M.is_type(left, I) or not M.is_type(right, I) then return V.NOT_IMPLEMENTED end
  local args, ops = {}, require("diaspore.ops")
  local function add(value)
    if value == V.NONE then
      if not I then V.throw("RuntimeError", "union construction requires an interpreter") end
      value = I:call(I.types.type, { V.NONE }, {})
    end
    local raw = M.unwrap(value)
    if kind(raw) == "UnionType" then
      for _, member in ipairs(tuple_items(raw.args)) do add(member) end
      return
    end
    for _, existing in ipairs(args) do
      if existing == value then return end
      if kind(M.unwrap(existing)) == "GenericAlias" and kind(raw) == "GenericAlias"
          and ops.eq_elem(I, existing, value) then return end
    end
    args[#args + 1] = value
  end
  add(left); add(right)
  if #args == 1 then return args[1] end
  return { kind = "UnionType", args = V.tuple(args), interp = I }
end

local function parameter_index(parameters, parameter)
  for i, existing in ipairs(parameters) do if existing == parameter then return i end end
end

local function iterable_parameter(value)
  local raw = M.unwrap(value)
  return kind(raw) == "GenericAlias" or (kind(value) == "instance" and V.class_lookup(value.cls, "__iter__") ~= nil)
end

--- Apply ordinary, ParamSpec and TypeVarTuple substitutions to a native alias.
function M.getitem(I, original, item)
  local value, ops = M.unwrap(original), require("diaspore.ops")
  local parameters = M.parameters(I, value).items
  if #parameters == 0 then V.throw("TypeError", ops.repr(I, original) .. " is not a generic class") end
  local supplied = {}
  for _, arg in ipairs(tuple_items(item) or { item }) do
    local unpacked = not is_class(arg) and optional(I, arg, "__typing_unpacked_tuple_args__") or nil
    local items = tuple_items(unpacked)
    if items and items[#items] ~= V.ELLIPSIS then
      for _, nested in ipairs(items) do supplied[#supplied + 1] = nested end
    else supplied[#supplied + 1] = arg end
  end
  item = V.tuple(supplied)
  for _, parameter in ipairs(parameters) do
    local prepare = optional(I, parameter, "__typing_prepare_subst__")
    if prepare ~= nil and prepare ~= V.NONE then item = I:call(prepare, { original, item }, {}) end
  end
  supplied = tuple_items(item) or { item }
  if #supplied ~= #parameters then
    V.throw("TypeError", "Too " .. (#supplied > #parameters and "many" or "few") .. " arguments for "
      .. ops.repr(I, original) .. "; actual " .. #supplied .. ", expected " .. #parameters)
  end
  local result = {}
  for _, arg in ipairs(tuple_items(value.args)) do
    if is_class(arg) then result[#result + 1] = arg
    else
      local unpack = optional(I, arg, "__typing_is_unpacked_typevartuple__")
      unpack = unpack ~= nil and I:truth(unpack)
      local substitute = optional(I, arg, "__typing_subst__")
      local replaced = arg
      if substitute ~= nil then
        local index = parameter_index(parameters, arg)
        replaced = I:call(substitute, { supplied[index] }, {})
      else
        local nested = tuple_items(optional(I, arg, "__parameters__"))
        if nested and #nested > 0 then
          local replacement_args = {}
          for _, parameter in ipairs(nested) do
            local index = parameter_index(parameters, parameter)
            local replacement = parameter
            if index then replacement = supplied[index] end
            if index and iterable_parameter(parameters[index]) and tuple_items(replacement) then
              for _, expanded in ipairs(tuple_items(replacement)) do replacement_args[#replacement_args + 1] = expanded end
            else replacement_args[#replacement_args + 1] = replacement end
          end
          replaced = ops.get_index(I, arg, V.tuple(replacement_args))
        end
      end
      if unpack then
        local items = tuple_items(replaced)
        if not items then V.throw("TypeError", "unpacked substitution must return a tuple") end
        for _, expanded in ipairs(items) do result[#result + 1] = expanded end
      else result[#result + 1] = replaced end
    end
  end
  if value.kind == "GenericAlias" then
    local alias = M.alias(value.origin, V.tuple(result), I)
    alias.starred = value.starred
    return alias
  end
  if #result == 0 then return { kind = "UnionType", args = V.tuple({}), interp = I } end
  local union = result[1]
  for i = 2, #result do union = ops.binop(I, "bitor", union, result[i], false) end
  return union
end

--- Produce the single starred alias consumed by iterable unpacking.
function M.iter(I, value)
  value = M.unwrap(value)
  if kind(value) ~= "GenericAlias" then V.throw("TypeError", "'types.UnionType' object is not iterable") end
  local pending = true
  return { kind = "iter", iname = "generic_alias_iterator", retval = V.NONE,
    next = function()
      if not pending then return V.NONE, false end
      pending = false
      local alias = M.alias(value.origin, value.args, I)
      alias.starred = true
      return alias, true
    end }
end

--- Instantiate the origin and retain the originating alias where writable.
function M.call(I, original, args, kwargs)
  local value = M.unwrap(original)
  local instance = I:call(value.origin, args, kwargs)
  local saved = #I.stack
  local ok, error_value = pcall(I.set_attr, I, instance, "__orig_class__", original)
  if not ok then
    if V.is_exc(error_value) and (V.exc_isa(error_value.value, "AttributeError") or V.exc_isa(error_value.value, "TypeError")) then
      I:trim(saved)
    else error(error_value, 0) end
  end
  return instance
end

--- Resolve native metadata without forwarding alias-specific fields to origins.
function M.attr(I, original, name)
  local value = M.unwrap(original)
  if name == "__args__" then return value.args end
  if name == "__parameters__" then return M.parameters(I, value) end
  if value.kind ~= "GenericAlias" then return nil end
  if name == "__origin__" then return value.origin end
  if name == "__unpacked__" then return not not value.starred end
  if name == "__typing_unpacked_tuple_args__" then
    return value.starred and value.origin == I.types.tuple and value.args or V.NONE
  end
  if name == "__mro_entries__" then return V.builtin(name, function(_, args, kwargs)
    H.arg_count(name, args, 1, 1); H.no_kw(name, kwargs)
    return V.tuple({ value.origin })
  end) end
  if name == "__reduce__" then return V.builtin(name, function(_, args, kwargs)
    H.arg_count(name, args, 0, 0); H.no_kw(name, kwargs)
    if value.starred then return V.tuple({ I.builtins.next, V.tuple({ M.iter(I, value) }) }) end
    return V.tuple({ I:call(I.types.type, { original }, {}), V.tuple({ value.origin, value.args }) })
  end) end
  if name == "__copy__" or name == "__deepcopy__" or name == "__reduce_ex__" then
    V.throw("AttributeError", "'types.GenericAlias' object has no attribute '" .. name .. "'")
  end
  return I:get_attr(value.origin, name)
end

--- Render qualified type names, starred aliases and order-preserving unions.
function M.repr(I, value, ops)
  value = M.unwrap(value)
  local function display(arg, union)
    local k = kind(arg)
    if k == "ellipsis" then return "..." end
    if arg == V.NONE then return "None" end
    if is_class(arg) then
      if union and k == "btype" and arg.name == "NoneType" then return "None" end
      local module = arg.ns and arg.ns.__module__
      local name = arg.ns and arg.ns.__qualname__ or arg.name
      if module and module ~= V.NONE and module ~= "builtins" then return I:str(module) .. "." .. I:str(name) end
      return I:str(name)
    end
    if k == "list" then
      local parts = {}
      for _, item in ipairs(arg.items) do parts[#parts + 1] = display(item, false) end
      return "[" .. table.concat(parts, ", ") .. "]"
    end
    return ops.repr(I, arg)
  end
  local parts = {}
  for _, arg in ipairs(tuple_items(value.args)) do parts[#parts + 1] = display(arg, value.kind == "UnionType") end
  if value.kind == "GenericAlias" then
    local args = #parts == 0 and "()" or table.concat(parts, ", ")
    return (value.starred and "*" or "") .. display(value.origin, false) .. "[" .. args .. "]"
  end
  return table.concat(parts, " | ")
end

return M
