-- Sandbox descriptor numbers never refer to ambient process descriptors.
local V = require("diaspore.values")
local H = require("diaspore.helpers")
local B = require("diaspore.buffer")
local M = {}
function M.methods(I)
  local handles, next_fd = {}, 3
  local function failure(message, errno)
    local name = errno == 2 and "FileNotFoundError" or errno == 13 and "PermissionError" or "OSError"
    local exc = V.make_exc(name, message or "I/O error")
    exc.d.errno = errno or V.NONE
    V.throw_val(exc)
  end
  local function lookup(fd, permission)
    local number = V.as_index(fd)
    if number == nil then V.throw("TypeError", "an integer is required") end
    local entry = handles[number]
    if entry == nil then failure("Bad file descriptor", 9) end
    if permission and not entry[permission] then failure("Bad file descriptor", 9) end
    I:require_fs(permission or (entry.write and "write" or "read"), entry.path)
    return entry, number
  end
  local methods = {}
  methods.open = V.builtin("open", function(_, args, kw)
    H.arg_count("open", args, 2, 3); H.no_kw("open", kw)
    local path = H.want_str("path", args[1])
    if path:find("\0", 1, true) then V.throw("ValueError", "embedded null byte") end
    local flags = V.as_index(args[2])
    if flags == nil then V.throw("TypeError", "an integer is required") end
    local access = flags & 3
    if access == 3 then V.throw("OSError", "Invalid argument") end
    local writable, readable = access ~= 0, access ~= 1
    local real = I:require_fs(writable and "write" or "read", path)
    if readable then I:require_fs("read", path) end
    if flags & ~(3 | 64 | 512 | 1024 | 524288) ~= 0 then
      V.throw("NotImplementedError", "diaspore-lua does not support these open flags")
    end
    if flags & 64 ~= 0 and (args[3] or 511) ~= 438 then
      V.throw("NotImplementedError", "diaspore-lua cannot set creation permissions")
    end
    if flags & (512 | 1024) ~= 0 and not writable then V.throw("OSError", "Invalid argument") end
    if flags & 64 ~= 0 and flags & (512 | 1024) == 0 then
      V.throw("NotImplementedError", "diaspore-lua cannot atomically create without truncation")
    end
    if flags & 64 == 0 and flags & (512 | 1024) ~= 0 then
      V.throw("NotImplementedError", "diaspore-lua cannot truncate or append atomically without creation")
    end
    local mode = not writable and "rb" or flags & 1024 ~= 0 and "a+b" or flags & 512 ~= 0 and "w+b" or "r+b"
    local file, message, errno = io.open(real, mode)
    if not file then failure(message, errno) end
    file:setvbuf("no")
    local fd = next_fd; next_fd = fd + 1
    handles[fd] = { file = file, path = path, read = readable, write = writable }
    return fd
  end)
  methods.read = V.builtin("read", function(_, args, kw)
    H.arg_count("read", args, 2, 2); H.no_kw("read", kw)
    local entry = lookup(args[1], "read")
    local count = V.as_index(args[2])
    if count == nil then V.throw("TypeError", "an integer is required") end
    if count < 0 then V.throw("OSError", "Invalid argument") end
    I:fuel_n(count)
    local data, message, errno = entry.file:read(count)
    if message then failure(message, errno) end
    return V.bytes(data or "")
  end)
  methods.write = V.builtin("write", function(_, args, kw)
    H.arg_count("write", args, 2, 2); H.no_kw("write", kw)
    local entry = lookup(args[1], "write")
    local value = args[2]
    if type(value) == "table" and value.kind == "memoryview" and value.stride ~= value.itemsize then
      V.throw("BufferError", "memoryview: underlying buffer is not C-contiguous")
    end
    local data = B.data(value)
    I:fuel_n(#data)
    local ok, message, errno = entry.file:write(data)
    if not ok then failure(message, errno) end
    return #data
  end)
  methods.lseek = V.builtin("lseek", function(_, args, kw)
    H.arg_count("lseek", args, 3, 3); H.no_kw("lseek", kw)
    local entry = lookup(args[1])
    local offset, whence = V.as_index(args[2]), V.as_index(args[3])
    if offset == nil or whence == nil then V.throw("TypeError", "an integer is required") end
    local mode = ({ [0] = "set", [1] = "cur", [2] = "end" })[whence]
    if not mode then V.throw("OSError", "Invalid argument") end
    local position, message, errno = entry.file:seek(mode, offset)
    if position == nil then failure(message, errno) end
    return position
  end)
  methods.close = V.builtin("close", function(_, args, kw)
    H.arg_count("close", args, 1, 1); H.no_kw("close", kw)
    local entry, fd = lookup(args[1])
    handles[fd] = nil
    local ok, message, errno = entry.file:close()
    if not ok then failure(message, errno) end
    return V.NONE
  end)
  methods.fstat = V.builtin("fstat", function(_, args, kw)
    H.arg_count("fstat", args, 1, 1); H.no_kw("fstat", kw)
    lookup(args[1])
    local message = "diaspore-lua cannot read file metadata"
    local exception = V.make_exc("OSError", 38, message)
    exception.d.errno, exception.d.strerror = 38, message
    V.throw_val(exception)
  end)
  methods.isatty = V.builtin("isatty", function(_, args, kw)
    H.arg_count("isatty", args, 1, 1); H.no_kw("isatty", kw)
    local fd = H.index_value(I, args[1])
    local bigint = require("diaspore.bigint")
    if bigint.cmp(fd, -2147483648) < 0 or bigint.cmp(fd, 2147483647) > 0 then
      V.throw("OverflowError", "Python int too large to convert to C int")
    end
    if handles[fd] ~= nil then lookup(fd) end
    -- Like a failed host terminal query, unavailable status is false, not metadata.
    return false
  end)
  for name, arity in pairs({ ftruncate = 2, set_inheritable = 2, get_inheritable = 1 }) do
    methods[name] = V.builtin(name, function(_, args, kw)
      H.arg_count(name, args, arity, arity); H.no_kw(name, kw)
      lookup(args[1], name == "ftruncate" and "write" or nil)
      V.throw("NotImplementedError", "diaspore-lua cannot implement " .. name .. " with the Lua standard library")
    end)
  end
  return methods
end
return M
