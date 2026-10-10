-- Introspection values reference VM frames and IR, never host Lua closures.
local V = require("diaspore.values")
local M = {}
function M.function_info(I, fn)
  if fn.code then return end
  local free, cells = {}, {}
  local function capture(name)
    if fn.meta.localset[name] or fn.meta.gdecl[name] or cells[name] then return end
    local frame = fn.closure
    while frame do
      if frame.localset and frame.localset[name] then
        frame.cells = frame.cells or {}
        frame.cells[name] = frame.cells[name] or { kind = "cell", frame = frame, name = name }
        cells[name] = frame.cells[name]
        free[#free + 1] = name
        return
      end
      frame = frame.parent
    end
  end
  local function walk(node)
    if type(node) ~= "table" or node == V.NULL then return end
    if node.op == "name" then capture(node.id) end
    for key, value in pairs(node) do
      if key ~= "__fnmeta" then walk(value) end
    end
  end
  walk(fn.node.body)
  for name in pairs(fn.meta.ndecl) do capture(name) end
  table.sort(free)
  local closure = {}
  for _, name in ipairs(free) do closure[#closure + 1] = cells[name] end
  fn.cells = #closure > 0 and V.tuple(closure) or V.NONE
  local names, seen = {}, {}
  local function add(name)
    if name and not seen[name] then names[#names + 1] = name; seen[name] = true end
  end
  for _, name in ipairs(fn.meta.args) do add(name) end
  for _, name in ipairs(fn.meta.kwonly) do add(name) end
  add(fn.meta.vararg); add(fn.meta.kwarg)
  for _, name in ipairs(fn.node.locals or {}) do add(name) end
  local module = I:module_source(fn.module_name)
  local flags = 3
  if fn.meta.vararg then flags = flags | 4 end
  if fn.meta.kwarg then flags = flags | 8 end
  if fn.meta.asyncgen then flags = flags | 512
  elseif fn.meta.coro then flags = flags | 128
  elseif fn.meta.gen then flags = flags | 32 end
  fn.code = { kind = "code", fn = fn, fields = {
    co_name = fn.meta.name, co_qualname = fn.meta.name,
    co_filename = module and module.file or fn.module_name,
    co_firstlineno = fn.node.ln or 1, co_argcount = #fn.meta.args,
    co_posonlyargcount = fn.meta.posonly or 0, co_kwonlyargcount = #fn.meta.kwonly,
    co_nlocals = #names, co_varnames = V.tuple(names), co_freevars = V.tuple(free),
    co_flags = flags,
  } }
end
--- Keep activation records reachable through a public frame's caller chain.
function M.retain(entry)
  while entry do
    entry.fr.escaped = true
    local previous = entry.previous
    if entry.retained and entry.retained_previous == previous then return end
    entry.retained, entry.retained_previous = true, previous
    entry = previous
  end
end
function M.frame(I, entry)
  M.retain(entry)
  if entry.frame_value then return entry.frame_value end
  local frame = entry.fr
  local code
  if frame.fn then M.function_info(I, frame.fn); code = frame.fn.code
  else
    local module = I:module_source(frame.module_name)
    code = { kind = "code", fields = { co_name = frame.fname,
      co_filename = module and module.file or frame.module_name,
      co_firstlineno = 1, co_argcount = 0, co_kwonlyargcount = 0,
      co_posonlyargcount = 0, co_nlocals = 0, co_varnames = V.tuple({}),
      co_freevars = V.tuple({}), co_flags = 0 } }
  end
  entry.frame_value = { kind = "frame", frame = frame, code = code, entry = entry }
  return entry.frame_value
end
-- Record only frames traversed by this propagation event. The wrapper tracks
-- which live stack frames were already recorded, independently of a reused
-- exception's existing traceback.
function M.capture(I, err, first)
  local recorded = err.trace_frames or {}
  local previous = err.value.d.__traceback__ or V.NONE
  for index = #I.stack, math.max(1, first), -1 do
    local entry = I.stack[index]
    if not recorded[entry] then
      previous = { kind = "traceback", fields = { tb_lineno = entry.fr.line or 1,
        tb_next = previous, tb_frame = M.frame(I, entry) } }
      recorded[entry] = true
    end
  end
  err.trace_frames = recorded
  err.value.d.__traceback__ = previous
end
function M.attr(I, value, name)
  if value.kind == "code" or value.kind == "traceback" then return value.fields[name] end
  if value.kind == "cell" and name == "cell_contents" then
    local result = value.frame.vars[value.name]
    if result == nil then V.throw("ValueError", "Cell is empty") end
    return result
  end
  if value.kind == "frame" then
    if name == "f_back" then
      local previous = value.entry.previous
      return previous and M.frame(I, previous) or V.NONE
    end
    if name == "f_code" then return value.code end
    if name == "f_lineno" then return value.frame.line or 1 end
    if name == "f_globals" then return I:live_dict(value.frame.globals) end
    if name == "f_locals" then return I:frame_dict(value.frame.vars) end
    if name == "f_builtins" then return I:frame_dict(I.builtins) end
  end
end
return M
