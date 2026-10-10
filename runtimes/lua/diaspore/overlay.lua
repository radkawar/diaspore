-- Read-only virtual files used by the nested compiler interpreter.
local V = require("diaspore.values")
local M = {}
function M.normalize(path)
  local parts = {}
  for part in path:gmatch("[^/]+") do
    if part == ".." then parts[#parts] = nil
    elseif part ~= "." then parts[#parts + 1] = part end
  end
  return "/" .. table.concat(parts, "/")
end
function M.open(I, path, mode)
  I:require_cap("fs")
  if mode ~= "r" and mode ~= "rb" then V.throw("PermissionError", "compiler filesystem is read-only") end
  local data = I.fs_overlay[M.normalize(path)]
  if data == nil then V.throw("FileNotFoundError", "No such virtual file: " .. path) end
  local position = 1
  local handle = {}
  function handle:read(count)
    if count == "a" then
      local result = data:sub(position); position = #data + 1; return result
    end
    if count == "L" or count == "l" then
      if position > #data then return nil end
      local ending = data:find("\n", position, true)
      local result = data:sub(position, ending or #data)
      position = ending and ending + 1 or #data + 1
      if count == "l" then result = result:gsub("\n$", "") end
      return result
    end
    if position > #data then return nil end
    local result = data:sub(position, position + count - 1)
    position = position + #result
    return result
  end
  function handle:close() return true end
  function handle:flush() return true end
  return { kind = "file", f = handle, binary = mode == "rb", writable = false, closed = false }
end
return M
