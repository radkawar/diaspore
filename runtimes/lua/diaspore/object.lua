--- Default object state and reduction protocols (docs/spec.md section 9).
local V = require("diaspore.values")
local Big = require("diaspore.bigint")
local iter = require("diaspore.iter")
local H = require("diaspore.helpers")
local pcall = require("diaspore.protected").call

local M = {}
local NONE = V.NONE
local function kind(value) return type(value) == "table" and value.kind or nil end
local function actual_type(I, obj) return I.types.type.ctor(I, { obj }, {}) end

local function optional_attr(I, obj, name)
  local saved = #I.stack
  local ok, result = pcall(I.get_attr, I, obj, name)
  if ok then return result end
  if V.is_exc(result) and V.exc_isa(result.value, "AttributeError") then
    I:trim(saved)
    return nil
  end
  error(result, 0)
end

local function copyreg_attr(I, name)
  return I:get_attr(I:import_module("copyreg"), name)
end

--- Return the instance dictionary and populated slots, without invoking a state override.
function M.getstate(I, obj)
  local cls = actual_type(I, obj)
  local state = NONE
  if kind(obj) == "instance" and cls.has_dict ~= false then
    local mapping = obj.dict_value or I:live_dict(obj.d)
    if I:raw(mapping).size > 0 then state = mapping end
  end
  local names = cls.ns.__slotnames__
  if names == nil then
    names = I:call(copyreg_attr(I, "_slotnames"), { cls }, {})
    if names ~= NONE and kind(I:raw(names)) ~= "list" then
      V.throw("TypeError", "copyreg._slotnames didn't return a list or None")
    end
  elseif names ~= NONE and kind(I:raw(names)) ~= "list" then
    V.throw("TypeError", cls.name .. ".__slotnames__ should be a list or None, not " .. V.type_name(names))
  end
  if names ~= NONE then
    names = I:raw(names)
    local slots = V.new_dict()
    local size = #names.items
    for _, name in ipairs(names.items) do
      local value = optional_attr(I, obj, name)
      if value ~= nil then V.dict_set(slots, name, value) end
      if #names.items ~= size then V.throw("RuntimeError", "__slotsname__ changed size during iteration") end
    end
    if slots.size > 0 then state = V.tuple({ state, slots }) end
  end
  return state
end

local function new_arguments(I, obj, cls)
  local method = I:lookup_type_attr(cls, "__getnewargs_ex__")
  if method ~= nil then
    local result = I:call(I:bind_attr(method, obj, cls), {}, {})
    local raw = I:raw(result)
    if kind(raw) ~= "tuple" then
      V.throw("TypeError", "__getnewargs_ex__ should return a tuple, not '" .. V.type_name(result) .. "'")
    end
    if #raw.items ~= 2 then
      V.throw("ValueError", "__getnewargs_ex__ should return a tuple of length 2, not " .. #raw.items)
    end
    local args, kwargs = raw.items[1], raw.items[2]
    if kind(I:raw(args)) ~= "tuple" then
      V.throw("TypeError", "first item of the tuple returned by __getnewargs_ex__ must be a tuple, not '" .. V.type_name(args) .. "'")
    end
    if kind(I:raw(kwargs)) ~= "dict" then
      V.throw("TypeError", "second item of the tuple returned by __getnewargs_ex__ must be a dict, not '" .. V.type_name(kwargs) .. "'")
    end
    return args, kwargs
  end
  method = I:lookup_type_attr(cls, "__getnewargs__")
  if method ~= nil then
    local args = I:call(I:bind_attr(method, obj, cls), {}, {})
    if kind(I:raw(args)) ~= "tuple" then
      V.throw("TypeError", "__getnewargs__ should return a tuple, not '" .. V.type_name(args) .. "'")
    end
    return args, nil
  end
  local raw = I:raw(obj)
  if V.is_int_kind(raw) then return V.tuple({ V.as_index(raw) }), nil end
  if type(raw) == "number" or type(raw) == "string" or kind(raw) == "bytes" or kind(raw) == "tuple" then
    return V.tuple({ raw }), nil
  end
  if kind(raw) == "complex" then return V.tuple({ raw.re, raw.im }), nil end
  return nil, nil
end

local function common_reduce(I, obj, protocol)
  if protocol < 2 then return I:call(copyreg_attr(I, "_reduce_ex"), { obj, protocol }, {}) end
  local cls = actual_type(I, obj)
  local args, kwargs = new_arguments(I, obj, cls)
  local newobj, newargs
  if kwargs ~= nil and I:raw(kwargs).size > 0 then
    newobj = copyreg_attr(I, "__newobj_ex__")
    newargs = V.tuple({ cls, args, kwargs })
  else
    newobj = copyreg_attr(I, "__newobj__")
    local items = { cls }
    if args ~= nil then
      for _, value in ipairs(I:raw(args).items) do items[#items + 1] = value end
    end
    newargs = V.tuple(items)
  end
  local state_method = optional_attr(I, obj, "__getstate__")
  local state
  local raw = I:raw(obj)
  local default_state = state_method == nil
    or (kind(state_method) == "boundmethod" and state_method.fn == V.OBJECT.ns.__getstate__)
  if default_state then
    if args == nil and kind(raw) ~= "list" and kind(raw) ~= "dict"
        and kind(raw) ~= "instance" and raw ~= NONE then
      V.throw("TypeError", "cannot pickle '" .. V.type_name(obj) .. "' object")
    end
    state = M.getstate(I, obj)
  else
    state = I:call(state_method, {}, {})
  end
  local listitems, dictitems = NONE, NONE
  if kind(raw) == "list" then listitems = iter.iter_object(I, obj) end
  if kind(raw) == "dict" then
    local items = I:call(I:get_attr(obj, "items"), {}, {})
    dictitems = iter.iter_object(I, items)
  end
  return V.tuple({ newobj, newargs, state, listitems, dictitems })
end

--- Install stable object descriptors used by reduction override detection.
function M.install()
  V.OBJECT.ns.__getstate__ = V.builtin("__getstate__", function(I, args, kw)
    H.arg_count("__getstate__", args, 1, 1)
    H.no_kw("__getstate__", kw)
    return M.getstate(I, args[1])
  end, "method_descriptor")
  V.OBJECT.ns.__reduce__ = V.builtin("__reduce__", function(I, args, kw)
    H.arg_count("__reduce__", args, 1, 1)
    H.no_kw("__reduce__", kw)
    return common_reduce(I, args[1], 0)
  end, "method_descriptor")
  V.OBJECT.ns.__reduce_ex__ = V.builtin("__reduce_ex__", function(I, args, kw)
    H.arg_count("__reduce_ex__", args, 2, 2)
    H.no_kw("__reduce_ex__", kw)
    local obj, protocol = args[1], V.as_index(args[2])
    if protocol == nil and kind(args[2]) == "instance" and V.class_lookup(args[2].cls, "__index__") ~= nil then
      protocol = V.as_index(I:call_dunder(args[2], "__index__", {}))
      if protocol == nil then V.throw("TypeError", "__index__ returned non-int") end
    end
    if protocol == nil then V.throw("TypeError", "'" .. V.type_name(args[2]) .. "' object cannot be interpreted as an integer") end
    if Big.cmp(protocol, -2147483648) < 0 or Big.cmp(protocol, 2147483647) > 0 then
      V.throw("OverflowError", "Python int too large to convert to C int")
    end
    local reduce = optional_attr(I, obj, "__reduce__")
    if reduce ~= nil and I:get_attr(actual_type(I, obj), "__reduce__") ~= V.OBJECT.ns.__reduce__ then
      return I:call(reduce, {}, {})
    end
    return common_reduce(I, obj, protocol)
  end, "method_descriptor")
end

return M
