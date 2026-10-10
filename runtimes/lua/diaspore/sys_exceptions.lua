--- Display sandbox exceptions through the live sys.stderr stream.
local V = require("diaspore.values")
local Big = require("diaspore.bigint")
local H = require("diaspore.helpers")
local ops = require("diaspore.ops")
local pcall = require("diaspore.protected").call

local M = {}
local NONE = V.NONE
local function kind(value) return type(value) == "table" and value.kind or nil end
local function exception(value)
  return kind(value) == "instance" and V.issub(value.cls, V.BASE_EXC)
end
local function optional(I, value, name)
  local saved = #I.stack
  local ok, result = pcall(I.get_attr, I, value, name)
  if ok then return result end
  if V.is_exc(result) and V.exc_isa(result.value, "AttributeError") then
    I:trim(saved)
    return nil
  end
  error(result, 0)
end
local function safe_string(I, value, what, repr)
  local saved = #I.stack
  local ok, result = pcall(repr and ops.repr or ops.str, I, value)
  if ok then return result end
  if not V.is_exc(result) then error(result, 0) end
  I:trim(saved)
  return "<" .. what .. (repr and " repr() failed>" or " str() failed>")
end

--- Render an exception with its actual traceback, chaining, notes, and groups.
function M.display_exception(I, value, supplied_traceback)
  local module = I.modules.sys or I.natives.sys
  local sys = module.kind == "module" and module.frame.vars or module.ns
  local file = sys.stderr
  if file == NONE then return NONE end
  if file == nil then I:ewrite("lost sys.stderr\n"); return NONE end
  if exception(value) and kind(supplied_traceback) == "traceback"
      and (value.d.__traceback__ == nil or value.d.__traceback__ == NONE) then
    value.d.__traceback__ = supplied_traceback
  end
  local seen = {}
  local depth, need_close = 0, false
  local function indent() return string.rep("  ", depth) end
  local function write(text)
    I:call(I:get_attr(file, "write"), { text }, {})
  end
  local function line(text)
    write(indent() .. (depth > 0 and "| " or "") .. text)
  end
  local function frames(error, group)
    local tb = error.d.__traceback__
    local limit = V.as_index(sys.tracebacklimit) or 1000
    if Big.cmp(limit, 0) <= 0 or kind(tb) ~= "traceback" then return end
    local entries = {}
    while kind(tb) == "traceback" do
      entries[#entries + 1] = tb
      tb = tb.fields.tb_next
    end
    if group then
      write(indent() .. (depth == 1 and "+ " or "| ") .. "Exception Group Traceback (most recent call last):\n")
    else
      line("Traceback (most recent call last):\n")
    end
    local previous, repeats = nil, 0
    local function flush_repeats()
      if repeats > 3 then
        line("  [Previous line repeated " .. (repeats - 3) .. " more time" .. (repeats == 4 and "" or "s") .. "]\n")
      end
    end
    local first = #entries - V.clamp_index(limit, 0, #entries) + 1
    for i = first, #entries do
      local entry = entries[i].fields
      local frame = entry.tb_frame
      local code = I:get_attr(frame, "f_code")
      local filename = I:get_attr(code, "co_filename")
      local name = I:get_attr(code, "co_name")
      local lineno = entry.tb_lineno
      local key = filename .. "\0" .. name .. "\0" .. tostring(lineno)
      if key ~= previous then flush_repeats(); previous, repeats = key, 0 end
      repeats = repeats + 1
      if repeats <= 3 then
        line('  File "' .. filename .. '", line ' .. lineno .. ", in " .. name .. "\n")
        local module = frame.frame and I:module_source(frame.frame.module_name)
        local source = module and module.src and module.src[lineno]
        if source ~= nil then
          source = source:match("^%s*(.-)%s*$")
          if source ~= "" then line("    " .. source .. "\n") end
        end
      end
    end
    flush_repeats()
  end
  local function get_notes(exc)
    local saved = #I.stack
    local ok, notes = pcall(optional, I, exc, "__notes__")
    if ok then return notes end
    if not V.is_exc(notes) then error(notes, 0) end
    I:trim(saved)
    return V.list({ "Ignored error getting __notes__: " .. safe_string(I, notes.value, "exception", true) })
  end
  local function notes(note_values)
    if note_values == nil then return end
    local raw = I:raw(note_values)
    local k = kind(raw)
    local sequence = k == "list" or k == "tuple" or k == "range"
      or (k ~= "dict" and kind(note_values) == "instance" and V.class_lookup(note_values.cls, "__getitem__") ~= nil)
    if not sequence or type(raw) == "string" or k == "bytes" then
      line(safe_string(I, note_values, "__notes__", true) .. "\n")
      return
    end
    local count = ops.diaspore_len(I, note_values)
    for index = 0, count - 1 do
      local note = safe_string(I, ops.get_index(I, note_values, index), "note")
      local parts = I:call(I:get_attr(note, "splitlines"), { true }, {})
      for _, part in ipairs(parts.items) do line(part) end
      write("\n")
    end
  end
  local function syntax(exc)
    if not V.issub(exc.cls, V.EXC_CLASSES.SyntaxError)
        and optional(I, exc, "print_file_and_line") == nil then return exc end
    local saved = #I.stack
    local ok, fields = pcall(function()
      local function index(name, default)
        local val = optional(I, exc, name)
        if val == nil or val == NONE then return default end
        return V.to_size(H.index_value(I, val))
      end
      return { message = I:get_attr(exc, "msg"), filename = I:get_attr(exc, "filename"),
        lineno = index("lineno", nil), offset = index("offset", -1),
        end_lineno = index("end_lineno", nil), end_offset = index("end_offset", -1),
        text = I:get_attr(exc, "text") }
    end)
    if not ok then
      if not V.is_exc(fields) then error(fields, 0) end
      I:trim(saved)
      return exc
    end
    if fields.lineno == nil then return exc end
    local filename = fields.filename == NONE and "<string>" or ops.str(I, fields.filename)
    line('  File "' .. filename .. '", line ' .. Big.tostring(fields.lineno) .. "\n")
    if fields.text ~= NONE then
      if type(fields.text) ~= "string" then V.throw("TypeError", "bad argument type for built-in operation") end
      local text = fields.text
      local offset = V.clamp_index(fields.offset, -1, #text + 1)
      local stop = fields.end_lineno and Big.cmp(fields.end_lineno, fields.lineno) > 0
        and #text or V.clamp_index(fields.end_offset, -1, #text + 1)
      local count = stop > 0 and stop > offset and stop - offset or 1
      local position, start = offset - 1, 1
      while text:sub(start, start):match("[ \t\f]") do start, position = start + 1, position - 1 end
      local finish = text:find("\0", start, true) or (#text + 1)
      local length = finish - start
      if length > 0 and text:sub(finish - 1, finish - 1) == "\n" then length = length - 1 end
      position = math.min(position, length)
      while true do
        local newline = text:find("\n", start, true)
        if newline == nil or newline >= finish or newline - start >= position then break end
        local skipped = newline - start + 1
        start, position, length = newline + 1, position - skipped, length - skipped
      end
      local source = text:sub(start, finish - 1)
      line("    " .. source .. (source:sub(-1) == "\n" and "" or "\n"))
      if position >= 0 then line("    " .. string.rep(" ", position) .. string.rep("^", count) .. "\n") end
    end
    return fields.message
  end
  local function single(exc, group)
    frames(exc, group)
    local note_values = get_notes(exc)
    local message_value = syntax(exc)
    local saved = #I.stack
    local ok, module = pcall(I.get_attr, I, exc.cls, "__module__")
    if not ok then
      if not V.is_exc(module) then error(module, 0) end
      I:trim(saved)
      module = V.EXC_CLASSES[exc.cls.name] == exc.cls and "builtins" or nil
    end
    local prefix = type(module) ~= "string" and "<unknown>."
      or ((module == "builtins" or module == "__main__") and "" or module .. ".")
    local name = exc.cls.ns.__qualname__ or exc.cls.name
    if type(name) ~= "string" then name = "<unknown>" end
    local message = message_value == NONE and "" or safe_string(I, message_value, "exception")
    line(prefix .. name .. (message == "" and "" or ": " .. message) .. "\n")
    notes(note_values)
  end
  local render
  render = function(error)
    if not exception(error) then
      line("TypeError: print_exception(): Exception expected for value, " .. V.type_name(error) .. " found\n")
      return
    end
    seen[error] = true
    local cause, context = error.d.__cause__, error.d.__context__
    local chained = exception(cause) and cause
      or (not I:truth(error.d.__suppress_context__ or false) and exception(context) and context)
    if chained and not seen[chained] then
      local close = need_close
      render(chained)
      need_close = close
      line("\n")
      line(chained == cause and "The above exception was the direct cause of the following exception:\n"
        or "During handling of the above exception, another exception occurred:\n")
      line("\n")
    end
    local children = error.d.exceptions
    if not V.issub(error.cls, V.EXC_CLASSES.BaseExceptionGroup) or kind(children) ~= "tuple" then
      single(error, false)
      return
    end
    if depth > 10 then line("... (max_group_depth is 10)\n"); return end
    if depth == 0 then depth = depth + 1 end
    single(error, true)
    local count = math.min(#children.items, 16)
    need_close = false
    for index = 1, count do
      local last = index == count
      if last then need_close = true end
      write(indent() .. (index == 1 and "+-" or "  ") .. "+---------------- "
        .. (index == 16 and "..." or tostring(index)) .. " ----------------\n")
      depth = depth + 1
      if index == 16 then
        local remaining = #children.items - 15
        line("and " .. remaining .. " more exception" .. (remaining == 1 and "" or "s") .. "\n")
      else render(children.items[index]) end
      if last and need_close then write(indent() .. "+------------------------------------\n"); need_close = false end
      depth = depth - 1
    end
    if depth == 1 then depth = depth - 1 end
  end
  local saved = #I.stack
  local ok, failure = pcall(render, value)
  if not ok then
    if not V.is_exc(failure) then error(failure, 0) end
    I:trim(saved)
    I:ewrite("lost sys.stderr\n")
  end
  saved = #I.stack
  ok, failure = pcall(function() I:call(I:get_attr(file, "flush"), {}, {}) end)
  if not ok then
    if not V.is_exc(failure) then error(failure, 0) end
    I:trim(saved)
  end
  return NONE
end

--- Create the default hook shared by sys.excepthook and sys.__excepthook__.
function M.make_excepthook(I)
  return V.builtin("excepthook", function(_I, args, kw)
    H.no_kw("sys.excepthook", kw)
    if #args ~= 3 then V.throw("TypeError", "excepthook expected 3 arguments, got " .. #args) end
    M.display_exception(I, args[2], args[3])
    return NONE
  end)
end

return M
