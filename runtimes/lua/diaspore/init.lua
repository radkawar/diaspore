--- Lua embedding API for Diaspore JSON bundles.
local interp = require("diaspore.interp")
local json = require("diaspore.json")
local values = require("diaspore.values")
local pcall = require("diaspore.protected").call

local M = {}
local VM = {}
VM.__index = VM
local DICT = {}

--- None sentinel for values stored inside host tables.
M.none = values.NONE

--- Mark a host table as a dictionary, including an empty dictionary.
function M.dict(t)
  return setmetatable(t or {}, DICT)
end

local function into_vm(v, seen)
  if v == nil or v == M.none then return values.NONE end
  local t = type(v)
  if t == "boolean" or t == "number" or t == "string" then return v end
  if t ~= "table" then error("unsupported host value: " .. t, 0) end
  seen = seen or {}
  if seen[v] then return seen[v] end
  local count, array = 0, getmetatable(v) ~= DICT
  for k in pairs(v) do
    count = count + 1
    if math.type(k) ~= "integer" or k < 1 then array = false end
  end
  array = array and count == #v
  local out = array and values.list({}) or values.new_dict()
  seen[v] = out
  if array then
    for i = 1, #v do out.items[i] = into_vm(v[i], seen) end
  else
    for k, x in pairs(v) do values.dict_set(out, into_vm(k, seen), into_vm(x, seen)) end
  end
  return out
end

local function from_vm(v, seen, nested)
  if v == values.NONE then
    if nested then return M.none end
    return nil
  end
  if type(v) ~= "table" then return v end
  seen = seen or {}
  if seen[v] then return seen[v] end
  local out = {}
  seen[v] = out
  if v.kind == "list" or v.kind == "tuple" then
    for i = 1, #v.items do out[i] = from_vm(v.items[i], seen, true) end
  elseif v.kind == "dict" then
    setmetatable(out, DICT)
    for _, entry in ipairs(values.dict_entries(v)) do
      out[from_vm(entry.k, seen, true)] = from_vm(entry.v, seen, true)
    end
  else
    error("unsupported sandbox value: " .. tostring(v.kind), 0)
  end
  return out
end

--- Load a plain JSON bundle from a file. Gzip and DSPB are not supported.
function M.load(path)
  local f, err = io.open(path, "rb")
  if not f then error(err, 0) end
  local data = f:read("a")
  f:close()
  if data:sub(1, 2) == "\31\139" then
    error("gzip bundles are not supported by diaspore-lua; pass plain JSON", 0)
  end
  if data:sub(1, 6) == "DSPB01" or data:sub(1, 6) == "DSPB02" then
    error("DSPB bundles are not supported by diaspore-lua; pass plain JSON", 0)
  end
  return json.parse(data)
end

--- Compile source to a raw bundle linked to the shipped stdlib by default.
-- opts accepts filename, search_paths, self_contained, compiler_bundle, stdlib_store.
function M.compile(source, opts)
  return require("diaspore.compiler").compile_source(source, opts or {})
end

--- Compile an entry file and its imports to a raw bundle.
-- Set opts.self_contained to include reachable stdlib modules in the bundle.
function M.compile_file(entry, opts)
  return require("diaspore.compiler").compile_file(entry, opts or {})
end

--- Construct a VM with bindings, limits, capabilities, argv, and output callbacks.
-- opts.stdlib_store supplies the compiled store used by a linked bundle.
function M.new(bundle, opts)
  opts = opts or {}
  local I = interp.new(bundle, opts)
  for module, bindings in pairs(opts.bindings or {}) do
    local ns = {}
    for name, fn in pairs(bindings) do
      ns[name] = values.builtin(name, function(_, args, kw)
        if #kw > 0 then
          values.throw("TypeError", name .. "() takes no keyword arguments")
        end
        local ok, result = pcall(function()
          local host_args = {}
          for i = 1, #args do host_args[i] = from_vm(args[i]) end
          return into_vm(fn(table.unpack(host_args, 1, #args)))
        end)
        if not ok then
          if values.is_exc(result) or values.is_trap(result) then error(result, 0) end
          values.throw("RuntimeError", tostring(result))
        end
        return result
      end)
    end
    I.natives[module] = { kind = "nativemodule", name = module, ns = ns }
  end
  return setmetatable({ interp = I }, VM)
end

--- Execute the main module and return its exit code.
function VM:run()
  return self.interp:run()
end

--- Run imported shutdown and atexit hooks once after the final host call.
function VM:close()
  return self.interp:close()
end

--- Call a sandbox function with host values and return a host value.
function VM:call(module, fn, ...)
  local I = self.interp
  local args = {}
  for i = 1, select("#", ...) do args[i] = into_vm((select(i, ...))) end
  local saved = #I.stack
  local ok, result = pcall(function()
    local mod = I:import_module(module)
    return from_vm(I:call(I:get_attr(mod, fn), args, {}))
  end)
  I:trim(saved)
  I:flush()
  if not ok then error(result, 0) end
  return result
end

return M
