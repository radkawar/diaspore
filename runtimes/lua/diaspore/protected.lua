-- Protected guest execution must not consume Lua's bounded C-call stack.
-- Catches normally use pcall; deeper catches run in coroutine segments driven
-- iteratively. Generator resumption uses the same dispatcher, preserving its
-- suspended segments without nesting host resume calls or adding guest frames.
local M = {}
local CALL, RESUME = {}, {}
local SEGMENT_DEPTH = 32
local depths = setmetatable({}, { __mode = "k" })
local contexts = setmetatable({}, { __mode = "k" })
local direct_depths = setmetatable({}, { __mode = "k" })

local function segment(fn)
  local co = coroutine.create(fn)
  depths[co] = 0
  return co
end

local function context(co)
  local ctx = contexts[co]
  if not ctx then
    ctx = { stack = { co }, active = false }
    contexts[co], depths[co] = ctx, 0
  end
  return ctx
end

local function restore_depth(co, depth, ...)
  depths[co] = depth
  return ...
end

local function restore_direct_depth(co, depth, ...)
  direct_depths[co] = depth
  return ...
end

local function dispatch(root, ...)
  local initial = context(root)
  initial.active = true
  local frames = { initial }
  local args, first = table.pack(...), 1
  while true do
    local ctx = frames[#frames]
    local co = ctx.stack[#ctx.stack]
    local result = table.pack(coroutine.resume(co, table.unpack(args, first, args.n)))
    local finished = not result[1] or coroutine.status(co) == "dead"
    if not finished and result[2] == CALL then
      ctx.stack[#ctx.stack + 1] = segment(result[3])
      args, first = result, 4
    elseif not finished and result[2] == RESUME then
      local child = context(result[3])
      child.active = true
      frames[#frames + 1] = child
      args, first = result, 4
    else
      if finished then
        depths[co] = nil
        ctx.stack[#ctx.stack] = nil
      end
      if not finished or #ctx.stack == 0 then
        if finished then contexts[co] = nil end
        ctx.active = false
        frames[#frames] = nil
        if #frames == 0 then return table.unpack(result, 1, result.n) end
      end
      args, first = result, 1
    end
  end
end

function M.resume(co, ...)
  local ctx = contexts[co]
  if ctx and ctx.active then return false, "cannot resume non-suspended coroutine" end
  if depths[coroutine.running()] ~= nil then
    return coroutine.yield(RESUME, co, ...)
  end
  return dispatch(co, ...)
end

function M.call(fn, ...)
  local co = coroutine.running()
  local depth = depths[co]
  if depth ~= nil then
    if depth == SEGMENT_DEPTH then return coroutine.yield(CALL, fn, ...) end
    depths[co] = depth + 1
    return restore_depth(co, depth, pcall(fn, ...))
  end
  local direct_depth = direct_depths[co] or 0
  if direct_depth < SEGMENT_DEPTH then
    direct_depths[co] = direct_depth + 1
    return restore_direct_depth(co, direct_depth, pcall(fn, ...))
  end
  local root = coroutine.create(fn)
  local result = table.pack(M.resume(root, ...))
  while result[1] and coroutine.status(root) ~= "dead" do
    result = table.pack(M.resume(root, coroutine.yield(table.unpack(result, 2, result.n))))
  end
  return table.unpack(result, 1, result.n)
end

return M
