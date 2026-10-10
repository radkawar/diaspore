--- Compile runtime source with the packaged, self-hosted Diaspore compiler.
-- Compilation uses the installed compiled stdlib through its private bridge.
-- Runtime snippets see only their source, not the caller's host filesystem.
local V = require("diaspore.values")
local json = require("diaspore.json")
local pcall = require("diaspore.protected").call

local M = {}
local ENTRY = "/snippet/source.py"
local EVAL_TARGET = "__diaspore_eval_value__"

local function source_lines(source)
  local lines = {}
  source = source:gsub("\r\n", "\n"):gsub("\r", "\n")
  local start = 1
  while start <= #source do
    local stop = source:find("\n", start, true)
    if stop == nil then lines[#lines + 1] = source:sub(start); break end
    lines[#lines + 1] = source:sub(start, stop - 1)
    start = stop + 1
  end
  return lines
end

local function syntax_error(message, filename, line, lines)
  local number = line or V.NONE
  local text = line and lines[line] or nil
  if text ~= nil then text = text .. "\n" else text = V.NONE end
  local location = V.tuple({ filename, number, V.NONE, text })
  local exc = V.make_exc("SyntaxError", message, location)
  exc.d.msg, exc.d.filename = message, filename
  exc.d.lineno, exc.d.offset, exc.d.text = number, V.NONE, text
  exc.d.end_lineno, exc.d.end_offset = V.NONE, V.NONE
  V.throw_val(exc)
end

local function compiler_error(diagnostic, filename, lines, mode)
  local detail = diagnostic:match("^diaspore: compile error: (.-)%s*$")
  if detail == nil then
    V.throw("RuntimeError", "self-hosted compiler failed: " .. diagnostic)
  end
  local file, line, message = detail:match("^(.-):(%d+): (.*)$")
  if file ~= nil then line = tonumber(line)
  else file, message = detail:match("^(.-): (.*)$") end
  if file == nil then V.throw("RuntimeError", "invalid self-hosted compiler diagnostic: " .. diagnostic) end
  if file == ENTRY then
    file = filename
    if line ~= nil and mode == "eval" then line = math.max(1, line - 1) end
  else
    lines = {}
  end
  syntax_error(message, file, line, lines)
end

local function shift_lines(node)
  if type(node) ~= "table" then return end
  if type(node.ln) == "number" then node.ln = math.max(1, node.ln - 1) end
  for key, value in pairs(node) do
    -- Ignore json.ORDER/json.ARRAY metadata, which is not part of the IR.
    if type(key) ~= "table" and type(value) == "table" then shift_lines(value) end
  end
end

local function validate_expression(child, source, filename, lines)
  local parser_module = child:import_module("_pyparse")
  local compile_error = child:get_attr(parser_module, "CompileError")
  local function call(object, method, args)
    return child:call(child:get_attr(object, method), args or {}, {})
  end
  local ok, failure = pcall(function()
    local text = source:gsub("\r\n", "\n"):gsub("\r", "\n")
    local tokens = call(parser_module, "tokenize", { text, filename })
    local parser = call(parser_module, "Parser", { tokens, filename })
    while call(parser, "kind") == "nl" do call(parser, "adv") end
    call(parser, "testlist_star")
    while call(parser, "kind") == "nl" do call(parser, "adv") end
    if call(parser, "kind") ~= "end" then
      syntax_error("invalid syntax", filename, call(parser, "ln"), lines)
    end
  end)
  if not ok then
    if V.is_exc(failure) and V.issub(failure.value.cls, compile_error) then
      local message = child:get_attr(failure.value, "msg"):gsub("^syntax error: ", "")
      local line = child:get_attr(failure.value, "line")
      syntax_error(message, filename, line ~= V.NONE and line or nil, lines)
    end
    error(failure, 0)
  end
end

--- Compile source to a raw bundle, preserving the caller's filename in its main module.
-- The compiler and immutable stdlib store are loaded lazily or supplied by the host.
-- Modes exec and eval are supported. Eval returns one {op="expr", v=...}
-- statement after expression-grammar validation and compiler IR lowering.
-- Every nested VM step is charged to I, including failures and compiler imports.
function M.compile(I, source, filename, mode, options)
  options = options or {}
  if type(source) ~= "string" then V.throw("TypeError", "compile() arg 1 must be a string") end
  if type(filename) ~= "string" then V.throw("TypeError", "compile() filename must be a string") end
  if mode ~= "exec" and mode ~= "eval" then
    if mode == "single" then
      V.throw("NotImplementedError", "interactive compilation is not supported by the Lua compiler")
    end
    V.throw("ValueError", "compile() mode must be 'exec', 'eval' or 'single'")
  end
  local lines = source_lines(source)
  if source:find("\0", 1, true) then
    syntax_error("source code string cannot contain null bytes", filename, nil, lines)
  end
  local compiler_bundle = require("diaspore.stdlib").compiler_bundle(I.compiler_bundle)
  local compiler_source = source
  if mode == "eval" then compiler_source = EVAL_TARGET .. " = (\n" .. source .. "\n)\n" end
  local entry = options.entry or ENTRY
  local overlay = options.entry == nil and { [entry] = compiler_source } or nil
  local argv = { "dspc", "--debug" }
  local self_contained = options.self_contained
  if self_contained == nil then self_contained = I.stdlib == nil end
  if self_contained then argv[#argv + 1] = "--self-contained" end
  local roots = { options.entry and (entry:match("^(.*)/[^/]*$") or ".") or "/snippet" }
  for _, path in ipairs(options.search_paths or {}) do
    argv[#argv + 1], argv[#argv + 2] = "--path", path
    roots[#roots + 1] = path
  end
  argv[#argv + 1] = entry
  local output, diagnostics = {}, {}
  -- Require lazily: interp imports this helper for compile()/exec()/eval().
  local child = require("diaspore.interp").new(compiler_bundle, {
    argv = argv,
    caps = { fs = true },
    policy = { fs = { read = roots, write = {} } },
    fs_overlay = overlay,
    compiler_bundle = I.compiler_bundle,
    stdlib_store = I.stdlib or I.stdlib_override,
    deterministic = I.deterministic,
    max_depth = I.max_depth,
    stdout = function(chunk) output[#output + 1] = chunk end,
    stderr = function(chunk) diagnostics[#diagnostics + 1] = chunk end,
  })
  child.fuel = function(self)
    I:fuel()
    self.steps = self.steps + 1
  end
  child.fuel_n = function(self, count)
    I:fuel_n(count)
    self.steps = self.steps + count
  end
  -- run() converts traps and runtime failures into exit codes. Import directly
  -- so a parent resource-limit trap cannot be misreported as a SyntaxError.
  local ok, failure = pcall(child.import_module, child, child.main)
  if not ok then
    if V.is_exc(failure) and V.exc_isa(failure.value, "SystemExit") then
      local args = V.exc_args(failure.value)
      local status = #args == 0 and 0 or args[1]
      if status ~= 0 and status ~= V.NONE then
        compiler_error(table.concat(diagnostics), filename, lines, mode)
      end
    else
      error(failure, 0)
    end
  end
  local parsed, bundle = pcall(json.parse, table.concat(output))
  if not parsed then V.throw("RuntimeError", "self-hosted compiler produced invalid JSON") end
  if type(bundle) ~= "table" or bundle.diaspore ~= 1
    or type(bundle.main) ~= "string" or type(bundle.modules) ~= "table" then
    V.throw("RuntimeError", "self-hosted compiler produced an invalid bundle")
  end
  local main = bundle.modules[bundle.main]
  if type(main) ~= "table" or type(main.body) ~= "table" then
    V.throw("RuntimeError", "self-hosted compiler produced no main module")
  end
  if mode == "eval" then
    -- Reuse the parser's eval grammar as in _ast._compile: parentheses used
    -- for IR lowering must not admit empty input or implicit line joining.
    validate_expression(child, source, filename, lines)
    local stmt = main.body[1]
    local target = stmt and stmt.targets and stmt.targets[1]
    if #main.body ~= 1 or stmt.op ~= "assign" or #stmt.targets ~= 1
      or target.op ~= "name" or target.id ~= EVAL_TARGET then
      syntax_error("eval() argument must be an expression", filename, nil, lines)
    end
    local expression = stmt.v
    shift_lines(expression)
    main.body = { { op = "expr", v = expression, ln = 1 } }
  end
  main.file, main.src = filename, lines
  return bundle
end

--- Compile host-supplied source; unlike exec/eval, new bundles link by default.
function M.compile_source(source, opts)
  opts = opts or {}
  local parent = require("diaspore.interp").new({
    diaspore = 1, main = "__compile__", modules = {},
  }, opts)
  return M.compile(parent, source, opts.filename or "<string>", "exec", {
    self_contained = opts.self_contained == true,
  })
end

--- Compile a host filesystem entry and its local imports.
function M.compile_file(entry, opts)
  opts = opts or {}
  local file, message = io.open(entry, "rb")
  if file == nil then error(message, 0) end
  local source = file:read("a")
  file:close()
  local parent = require("diaspore.interp").new({
    diaspore = 1, main = "__compile__", modules = {},
  }, opts)
  return M.compile(parent, source, entry, "exec", {
    entry = entry, search_paths = opts.search_paths, self_contained = opts.self_contained == true,
  })
end

return M
