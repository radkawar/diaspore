-- The Diaspore interpreter: frames, execution, calls, attributes, modules,
-- capabilities, fuel. Mirrors src/diaspore/vm.py (reference) and runtimes/ts/src/interp.ts.
--
-- Control flow: `break`/`continue`/`return` are signals returned up the
-- statement chain ({tag=...}); diaspore exceptions and traps are Lua errors
-- (tagged tables) caught with pcall only at try/with/iterator boundaries.
-- The hot call path stays unprotected; catches use coroutine-segmented pcall
-- so nested guest try/with blocks do not exhaust Lua's bounded C-call stack.

local values = require("diaspore.values")
local format = require("diaspore.format")
local ops = require("diaspore.ops")
local iter = require("diaspore.iter")
local methods = require("diaspore.methods")
local builtins = require("diaspore.builtins")
local natives = require("diaspore.natives")
local buffer = require("diaspore.buffer")
local metadata = require("diaspore.metadata")
local typeexpr = require("diaspore.typeexpr")
local bigint = require("diaspore.bigint")
local pcall = require("diaspore.protected").call

local M = {}

local NULL = values.NULL
local NONE = values.NONE
local throw = values.throw
local type_name = values.type_name
local class_lookup = values.class_lookup
local kindof = function(v)
  if type(v) == "table" then return v.kind end
  return nil
end

local function has(n, k)
  local v = n[k]
  return v ~= nil and v ~= NULL
end

local function get(n, k)
  local v = n[k]
  if v == NULL then return nil end
  return v
end

local EMPTY = {}

-- ---------------------------------------------------------------------------
-- base64 (bytes constants)

local B64 = {}
do
  local alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
  for i = 1, 64 do B64[alphabet:byte(i)] = i - 1 end
end

local function b64decode(s)
  local out = {}
  local acc, bits = 0, 0
  for i = 1, #s do
    local v = B64[s:byte(i)]
    if v ~= nil then
      acc = (acc << 6) | v
      bits = bits + 6
      if bits >= 8 then
        bits = bits - 8
        out[#out + 1] = string.char((acc >> bits) & 0xFF)
      end
    end
  end
  return table.concat(out)
end

-- ---------------------------------------------------------------------------
-- frames

local function new_frame(vars, parent, localset, gdecl, ndecl, is_class,
                         globals_, fname, module_name)
  return {
    vars = vars, parent = parent, localset = localset,
    gdecl = gdecl, ndecl = ndecl, is_class = is_class,
    globals = globals_ or vars, fname = fname, module_name = module_name,
    line = 0, defclass = nil, self_ = nil, is_gen = false,
  }
end

local function to_set(arr)
  if arr == nil then return EMPTY end
  local s = {}
  for i = 1, #arr do s[arr[i]] = true end
  return s
end

local function name_list(names)
  local quoted = {}
  for i = 1, #names do quoted[i] = "'" .. names[i] .. "'" end
  if #quoted == 1 then return quoted[1] end
  if #quoted == 2 then return quoted[1] .. " and " .. quoted[2] end
  local head = table.concat(quoted, ", ", 1, #quoted - 1)
  return head .. ", and " .. quoted[#quoted]
end

local function normalize_path(p)
  if p:sub(1, 1) ~= "/" then
    local cwd = os.getenv("PWD") or "."
    p = cwd .. "/" .. p
  end
  local parts = {}
  for seg in p:gmatch("[^/]+") do
    if seg == ".." then
      if #parts > 0 then parts[#parts] = nil end
    elseif seg ~= "." then
      parts[#parts + 1] = seg
    end
  end
  return "/" .. table.concat(parts, "/")
end

-- ---------------------------------------------------------------------------

local Interp = {}
Interp.__index = Interp
M.Interp = Interp

--- Construct an interpreter with resource limits and capabilities.
function M.new(bundle, opts)
  opts = opts or {}
  if bundle.diaspore ~= 1 or type(bundle.main) ~= "string" or bundle.modules == nil then
    error("not a diaspore v1 bundle", 0)
  end
  local I = setmetatable({}, Interp)
  I.bundle = bundle
  I.main = bundle.main
  I.stdlib_override = opts.stdlib_store
  I.linked_ir = {}
  if bundle.stdlib ~= nil then
    if type(bundle.stdlib) ~= "string" then error("bundle stdlib reference must be a string", 0) end
    I.stdlib = require("diaspore.stdlib").load(opts.stdlib_store)
    if bundle.stdlib ~= I.stdlib.id then
      error("stdlib id mismatch: bundle requires " .. bundle.stdlib .. ", installed " .. I.stdlib.id, 0)
    end
  end
  I.argv = opts.argv and #opts.argv > 0 and opts.argv or { bundle.main }
  I.caps = opts.caps or {}
  I.policy = opts.policy or {}
  I.deterministic = opts.deterministic or false
  I.max_steps = opts.max_steps or 0
  I.max_depth = opts.max_depth or 1000
  I.recursion_limit = I.max_depth
  I.stdout = opts.stdout
  I.stderr = opts.stderr
  I.steps = 0
  I.depth = 0
  I.modules_dict = values.new_dict()
  I.modules = setmetatable({}, {
    __index = function(_, name)
      local entry = values.dict_entry(I.modules_dict, name)
      if entry then return entry.v end
    end,
    __newindex = function(_, name, mod)
      if mod == nil then values.dict_del(I.modules_dict, name)
      else values.dict_set(I.modules_dict, name, mod) end
    end,
  })
  I.exc_stack = {}
  I.repring = {}
  I.stack = {} -- active entries: {fr=, depth0=, previous=}
  I.natives_open = natives.bi_open
  I.builtins = builtins.make_builtins(I)
  I.compiler_bundle = opts.compiler_bundle
  I.fs_overlay = opts.fs_overlay
  for _, name in ipairs({ "__new__", "__init__", "__prepare__" }) do
    I.types.type.ns[name] = I:builtin_unbound("type", name)
  end
  I.natives = natives.make_native_modules(I)
  if not I:has_module("builtins") then I.modules.builtins = I.natives.builtins end
  if not I:has_module("sys") then I.modules.sys = I.natives.sys end
  I.classes = setmetatable({}, { __mode = "k" })
  return I
end

-- ------------------------------------------------------------------- output

function Interp:write(s)
  if self.stdout then self.stdout(s) else io.stdout:write(s) end
end

function Interp:ewrite(s)
  self:flush()
  if self.stderr then self.stderr(s) else io.stderr:write(s) end
end

function Interp:flush()
  if not self.stdout then io.stdout:flush() end
end

-- ------------------------------------------------------------------- fuel

function Interp:fuel()
  local s = self.steps < math.maxinteger and self.steps + 1 or math.maxinteger
  self.steps = s
  if self.max_steps > 0 and s > self.max_steps then
    values.trap("diaspore: step budget exceeded (" .. self.max_steps .. ")")
  end
end

function Interp:fuel_n(n)
  if self.max_steps > 0 and bigint.cmp(n, self.max_steps - self.steps) > 0 then
    values.trap("diaspore: step budget exceeded (" .. self.max_steps .. ")")
  end
  if bigint.cmp(n, math.maxinteger - self.steps) > 0 then
    self.steps = math.maxinteger
  else
    self.steps = self.steps + n
  end
end

-- Unwind the recorded call stack to a saved height (used when a pcall site
-- catches-and-consumes a diaspore exception); restores the recursion depth too.
function Interp:trim(saved)
  local st = self.stack
  local n = #st
  if n > saved then
    local d0 = st[saved + 1].depth0
    if d0 ~= nil then self.depth = d0 end
    for i = n, saved + 1, -1 do st[i] = nil end
  end
end

-- ------------------------------------------------------------------- entry

local function system_exit_status(I, value)
  local code = I:get_attr(value, "code")
  if kindof(code) == "instance" and code.prim ~= nil and values.issub(code.cls, I.types.int) then
    code = code.prim
  end
  if kindof(code) == "none" then return 0 end
  if type(code) == "boolean" then return code and 1 or 0 end
  if math.type(code) == "integer" then return code & 0xFF end
  if bigint.is_bigint(code) then return 255 end
  I:ewrite(ops.str(I, code) .. "\n")
  return 1
end

local function runtime_failure_status(I, err)
  if values.is_trap(err) then
    I:ewrite(err.msg .. "\n")
    return err.code
  end
  if type(err) == "string" and err:find("stack overflow", 1, true) then
    I:ewrite("RecursionError: maximum recursion depth exceeded\n")
    return 1
  end
  I:ewrite("diaspore-lua internal error: " .. tostring(err) .. "\n")
  return 70
end

function Interp:run()
  local ok, err = pcall(Interp.import_module, self, self.main)
  self:flush()
  if ok then return 0 end
  if values.is_exc(err) then
    local v = err.value
    if values.exc_isa(v, "SystemExit") then return system_exit_status(self, v) end
    self:prepare_exception(err, 1)
    local traceback = v.d.__traceback__ or NONE
    self:trim(0)
    local sys = self.modules.sys or self.natives.sys
    local namespace = sys.kind == "module" and sys.frame.vars or sys.ns
    namespace.last_exc, namespace.last_type = v, v.cls
    namespace.last_value, namespace.last_traceback = v, traceback
    local display = require("diaspore.sys_exceptions").display_exception
    local function header(text)
      local stream = namespace.stderr
      if stream == nil then self:ewrite(text)
      elseif stream ~= NONE then self:call(self:get_attr(stream, "write"), { text }, {}) end
    end
    local hook = namespace.excepthook
    if hook == nil then
      header("sys.excepthook is missing\n")
      display(self, v, traceback)
    else
      local hook_ok, hook_error = pcall(Interp.call, self, hook, { v.cls, v, traceback }, {})
      if not hook_ok then
        if not values.is_exc(hook_error) then return runtime_failure_status(self, hook_error) end
        local failure = hook_error.value
        if values.exc_isa(failure, "SystemExit") then return system_exit_status(self, failure) end
        self:prepare_exception(hook_error, 1)
        self:trim(0)
        header("Error in sys.excepthook:\n")
        display(self, failure, failure.d.__traceback__ or NONE)
        header("\nOriginal exception was:\n")
        display(self, v, traceback)
      end
    end
    self:flush()
    return 1
  end
  return runtime_failure_status(self, err)
end

function Interp:close()
  if self.closed then return end
  self.closed = true
  local failure
  for _, hook in ipairs({ { "threading", "_shutdown" }, { "atexit", "_run_exitfuncs" } }) do
    local module = self.modules[hook[1]]
    if module ~= nil and module ~= NONE then
      local namespace = module.kind == "module" and module.frame.vars or module.ns
      local callback = namespace[hook[2]]
      if callback ~= nil then
        local saved = #self.stack
        local ok, err = pcall(Interp.call, self, callback, {}, {})
        self:trim(saved)
        if not ok then failure = failure or err end
      end
    end
  end
  self:flush()
  if failure then error(failure, 0) end
end

-- ------------------------------------------------------------------- modules

function Interp:has_module(name)
  return self.bundle.modules[name] ~= nil
    or self.stdlib ~= nil and require("diaspore.stdlib").has_module(self.stdlib, name)
end

function Interp:module_source(name)
  local module = self.bundle.modules[name] or self.linked_ir[name]
  if module == nil and self.stdlib ~= nil then
    module = require("diaspore.stdlib").module(self.stdlib, name)
    self.linked_ir[name] = module
  end
  return module
end

function Interp:import_module(name)
  local cached = self.modules[name]
  if cached == NONE then throw("ModuleNotFoundError", "import of " .. name .. " halted; None in sys.modules") end
  if cached ~= nil then return cached end
  local m = self:module_source(name)
  if m ~= nil then
    local parent = nil
    local parent_name = name:match("^(.*)%.[^.]*$")
    if parent_name ~= nil then
      -- packages: ancestors execute first (each exactly once)
      parent = self:import_module(parent_name)
    end
    local fr = new_frame({}, nil, nil, EMPTY, EMPTY, false, nil, "<module>", name)
    fr.vars.__name__ = (name == self.main) and "__main__" or name
    fr.vars.__file__ = get(m, "file") or name
    fr.vars.__doc__ = NONE
    local package = fr.vars.__file__:match("/__init__%.py$") and name or parent_name or ""
    fr.vars.__package__ = package
    if package == name then fr.vars.__path__ = values.list({ fr.vars.__file__:match("^(.*)/") or "" }) end
    local mod = { kind = "module", name = name, frame = fr }
    self.modules[name] = mod
    if name == self.main then self.modules.__main__ = mod end
    local st = self.stack
    local sn = #st + 1
    st[sn] = { fr = fr, depth0 = self.depth, previous = st[sn - 1] }
    local ok, err = pcall(Interp.exec_block, self, m.body, fr)
    if not ok then
      if name ~= self.main then self.modules[name] = nil end
      error(err, 0)
    end
    st[sn] = nil
    if parent ~= nil and parent.kind == "module" then
      -- the executed submodule becomes an attribute of its parent package
      parent.frame.vars[name:match("([^.]*)$")] = mod
    end
    return mod
  end
  local nat = self.natives[name]
  if nat ~= nil then
    self.modules[name] = nat
    return nat
  end
  throw("ModuleNotFoundError", "No module named '" .. name .. "'")
end

-- ------------------------------------------------------------------- names

function Interp:load_name(fr, id)
  if id == ".builtins" then return self.natives.builtins end
  if fr.gdecl[id] then
    local v = fr.globals[id]
    if v ~= nil then return v end
  elseif fr.localset ~= nil and fr.localset[id] then
    local v = fr.vars[id]
    if v ~= nil then return v end
    throw("UnboundLocalError", "cannot access local variable '" .. id
      .. "' where it is not associated with a value")
  else
    local current = fr.vars[id]
    if current ~= nil then return current end
    local f = fr.parent
    local class_visible = fr.annotation_scope
    while f ~= nil do
      if not f.is_class or class_visible then
        local v = f.vars[id]
        if v ~= nil then return v end
      end
      if not f.annotation_scope then class_visible = false end
      f = f.parent
    end
    local g = fr.globals[id]
    if g ~= nil then return g end
  end
  local b = self.builtins[id]
  if b ~= nil then return b end
  throw("NameError", "name '" .. id .. "' is not defined")
end

function Interp:store_name(fr, id, v)
  if fr.gdecl[id] then
    fr.globals[id] = v
  elseif fr.ndecl[id] then
    local f = fr.parent
    while f ~= nil do
      if f.localset ~= nil and f.localset[id] then
        f.vars[id] = v
        return
      end
      f = f.parent
    end
    throw("RuntimeError", "nonlocal binding for '" .. id .. "' not found")
  else
    if fr.order ~= nil and fr.vars[id] == nil then
      fr.order[#fr.order + 1] = id -- class bodies track definition order
    end
    fr.vars[id] = v
  end
end

-- ------------------------------------------------------------------- statements

local STMTS = {}

function Interp:exec_block(stmts, fr)
  if stmts == nil or stmts == NULL then return nil end
  for i = 1, #stmts do
    local s = stmts[i]
    self:fuel()
    local ln = s.ln
    if ln ~= nil and ln ~= NULL then fr.line = ln end
    local h = STMTS[s.op]
    if h == nil then
      values.trap("diaspore: bad statement op " .. tostring(s.op), 4)
    end
    local sig = h(self, s, fr)
    if sig ~= nil then return sig end
  end
  return nil
end

STMTS["expr"] = function(I, s, fr)
  I:ev(s.v, fr)
  return nil
end

STMTS["assign"] = function(I, s, fr)
  local v = I:ev(s.v, fr)
  local targets = s.targets
  for i = 1, #targets do
    I:assign(targets[i], v, fr)
  end
  return nil
end

function Interp:assign(t, v, fr)
  local op = t.op
  if op == "name" then
    self:store_name(fr, t.id, v)
  elseif op == "attr" then
    self:set_attr(self:ev(t.o, fr), t.n, v)
  elseif op == "index" then
    ops.set_index(self, self:ev(t.o, fr), self:ev_key(t.k, fr), v)
  elseif op == "tuple" or op == "list" then
    self:unpack(get(t, "elts") or EMPTY, v, fr)
  else
    throw("TypeError", "cannot assign to this target")
  end
end

function Interp:unpack(elts, v, fr)
  local star = nil
  for i = 1, #elts do
    if elts[i].op == "starred" then
      star = i
      break
    end
  end
  local saved = #self.stack
  local ok, items = pcall(iter.iter_to_array, self, v)
  if not ok then
    if values.is_exc(items) and values.exc_isa(items.value, "TypeError") then
      self:trim(saved)
      throw("TypeError", "cannot unpack non-iterable " .. type_name(v) .. " object")
    end
    error(items, 0)
  end
  local n = #items
  if star == nil then
    if n < #elts then
      throw("ValueError", "not enough values to unpack (expected " .. #elts
        .. ", got " .. n .. ")")
    end
    if n > #elts then
      throw("ValueError", "too many values to unpack (expected " .. #elts .. ")")
    end
    for i = 1, #elts do self:assign(elts[i], items[i], fr) end
  else
    local after = #elts - star
    if n < #elts - 1 then
      throw("ValueError", "not enough values to unpack (expected at least "
        .. (#elts - 1) .. ", got " .. n .. ")")
    end
    for i = 1, star - 1 do self:assign(elts[i], items[i], fr) end
    local mid = {}
    for i = star, n - after do mid[#mid + 1] = items[i] end
    self:assign(elts[star].v, values.list(mid), fr)
    for i = 1, after do
      self:assign(elts[star + i], items[n - after + i], fr)
    end
  end
end

STMTS["augassign"] = function(I, s, fr)
  local t = s.target
  local kind = s.kind
  local rhs = I:ev(s.v, fr)
  if t.op == "name" then
    local cur = I:load_name(fr, t.id)
    I:store_name(fr, t.id, ops.binop(I, kind, cur, rhs, true))
  elseif t.op == "attr" then
    local o = I:ev(t.o, fr)
    local cur = I:get_attr(o, t.n)
    I:set_attr(o, t.n, ops.binop(I, kind, cur, rhs, true))
  elseif t.op == "index" then
    local o = I:ev(t.o, fr)
    local k = I:ev_key(t.k, fr)
    local cur = ops.get_index(I, o, k)
    ops.set_index(I, o, k, ops.binop(I, kind, cur, rhs, true))
  else
    throw("TypeError", "illegal augmented assignment target")
  end
  return nil
end

STMTS["if"] = function(I, s, fr)
  if ops.truth(I, I:ev(s.test, fr)) then
    return I:exec_block(s.then_ or s["then"], fr)
  end
  return I:exec_block(get(s, "orelse"), fr)
end

STMTS["while"] = function(I, s, fr)
  local broke = false
  while ops.truth(I, I:ev(s.test, fr)) do
    I:fuel()
    local sig = I:exec_block(s.body, fr)
    if sig ~= nil then
      if sig.tag == "break" then
        broke = true
        break
      elseif sig.tag ~= "continue" then
        return sig
      end
    end
  end
  if not broke then return I:exec_block(get(s, "orelse"), fr) end
  return nil
end

STMTS["for"] = function(I, s, fr)
  local it = iter.get_iter(I, I:ev(s.iter, fr))
  local broke = false
  while true do
    I:fuel()
    local x, ok = it.next()
    if not ok then break end
    I:assign(s.target, x, fr)
    local sig = I:exec_block(s.body, fr)
    if sig ~= nil then
      if sig.tag == "break" then
        broke = true
        break
      elseif sig.tag ~= "continue" then
        return sig
      end
    end
  end
  if not broke then return I:exec_block(get(s, "orelse"), fr) end
  return nil
end

STMTS["break"] = function()
  return values.BREAK
end

STMTS["continue"] = function()
  return values.CONTINUE
end

STMTS["pass"] = function()
  return nil
end

STMTS["return"] = function(I, s, fr)
  if has(s, "v") then return values.ret_sig(I:ev(s.v, fr)) end
  return values.ret_sig(NONE)
end

STMTS["func"] = function(I, s, fr)
  I:store_name(fr, s.name, I:make_function(s, fr))
  return nil
end

-- A base is a diaspore class or a subclassable builtin type (dict/list/set/str/
-- int/float/tuple/bytes/bool/frozenset).
local PRIM_BASES = {
  dict = true, list = true, set = true, str = true, int = true,
  float = true, tuple = true, bytes = true, bool = true, frozenset = true,
  type = true, bytearray = true, GenericAlias = true, property = true,
  staticmethod = true, classmethod = true,
}

local function check_base(I, bv)
  local k = kindof(bv)
  if k == "class" then return bv end
  if k == "btype" and PRIM_BASES[bv.name] then return bv end
  throw("TypeError", "cannot subclass " .. ops.repr(I, bv) .. " in diaspore")
end

STMTS["class"] = function(I, s, fr)
  local binding = s.binding
  if binding ~= nil and type(binding) ~= "string" then throw("TypeError", "class binding must be a string") end
  local bases, original_bases = {}, {}
  local bnodes = get(s, "bases") or EMPTY
  for i = 1, #bnodes do
    local bn = bnodes[i]
    if bn.op == "starred" then
      local spread = iter.iter_to_array(I, I:ev(bn.v, fr))
      for j = 1, #spread do original_bases[#original_bases + 1] = spread[j] end
    else
      original_bases[#original_bases + 1] = I:ev(bn, fr)
    end
  end
  local changed_bases = false
  local original_tuple = values.tuple(original_bases)
  for _, base in ipairs(original_bases) do
    if kindof(base) == "class" or kindof(base) == "btype" then
      bases[#bases + 1] = check_base(I, base)
    else
      local replacement = I:call(I:get_attr(base, "__mro_entries__"), { original_tuple }, {})
      if kindof(replacement) ~= "tuple" then throw("TypeError", "__mro_entries__ must return a tuple") end
      for _, resolved in ipairs(replacement.items) do bases[#bases + 1] = check_base(I, resolved) end
      changed_bases = true
    end
  end
  local metaclass = has(s, "metaclass") and I:ev(s.metaclass, fr) or nil
  local kwargs = {}
  local knodes = get(s, "kwargs") or EMPTY
  for i = 1, #knodes do
    local kn = knodes[i]
    if kn.n == nil or kn.n == NULL then
      local d = I:ev(kn.v, fr)
      if kindof(d) ~= "dict" then
        throw("TypeError", "argument after ** must be a mapping")
      end
      for _, e in ipairs(values.dict_entries(d)) do
        if type(e.k) ~= "string" then throw("TypeError", "keywords must be strings") end
        if e.k == "metaclass" then
          metaclass = e.v
        else
          kwargs[#kwargs + 1] = { k = e.k, v = e.v }
        end
      end
    elseif kn.n == "metaclass" then
      metaclass = I:ev(kn.v, fr)
    else
      kwargs[#kwargs + 1] = { k = kn.n, v = I:ev(kn.v, fr) }
    end
  end
  if metaclass == nil then
    metaclass = I.types.type
    for _, base in ipairs(bases) do
      if base.metaclass and base.metaclass ~= I.types.type then metaclass = base.metaclass end
    end
  end
  local prepare = kindof(metaclass) == "class" and class_lookup(metaclass, "__prepare__")
    or kindof(metaclass) == "btype" and metaclass.ns.__prepare__ or nil
  local prepared = values.new_dict()
  if prepare ~= nil then
    if kindof(prepare) == "classmethod" then prepare = { kind = "boundmethod", fn = prepare.fn, recv = metaclass } end
    prepared = I:call(prepare, { s.name, values.tuple(bases) }, kwargs)
  end
  local raw = I:raw(prepared)
  if kindof(raw) ~= "dict" then throw("TypeError", "__prepare__() must return a mapping") end
  local vars = setmetatable({}, {
    __index = function(_, name)
      local saved = #I.stack
      local ok, result = pcall(ops.get_index, I, prepared, name)
      if ok then return result end
      if values.is_exc(result) and values.exc_isa(result.value, "KeyError") then I:trim(saved); return nil end
      error(result, 0)
    end,
    __newindex = function(_, name, value)
      if value == nil then ops.del_index(I, prepared, name)
      else ops.set_index(I, prepared, name, value) end
    end,
  })
  local cf = new_frame(vars, fr, nil, EMPTY, EMPTY, true, fr.globals, s.name, fr.module_name)
  cf.order = {}
  I:store_name(cf, "__module__", fr.globals.__name__ or fr.module_name)
  I:store_name(cf, "__qualname__", s.name)
  local sn = #I.stack + 1
  I.stack[sn] = { fr = cf, depth0 = I.depth, previous = I.stack[sn - 1] }
  I:exec_block(s.body, cf)
  I.stack[sn] = nil
  if changed_bases then I:store_name(cf, "__orig_bases__", original_tuple) end
  cf.vars = {}
  for _, entry in ipairs(values.dict_entries(raw)) do cf.vars[entry.k] = entry.v end
  local cls = I:build_class(s.name, bases, cf.vars, cf.order, metaclass, kwargs, prepared)
  if kindof(cls) == "class" then cf.vars = cls.ns end
  I:store_name(fr, binding or s.name, cls)
  return nil
end

-- Metaclasses construct the class before inherited subclass hooks run.
function Interp:build_class(name, bases, ns_map, ns_order, metaclass, kwargs, prepared, direct_new)
  kwargs = kwargs or {}
  if metaclass == nil then
    metaclass = self.types.type
    for _, base in ipairs(bases) do
      local candidate = base.metaclass
      if candidate and candidate ~= self.types.type then
        if metaclass == self.types.type or values.issub(candidate, metaclass) then metaclass = candidate
        elseif not values.issub(metaclass, candidate) then throw("TypeError", "metaclass conflict") end
      end
    end
  end
  local function make(cls_name, cls_bases, cls_ns)
    for _, special in ipairs({ "__new__", "__init_subclass__", "__class_getitem__" }) do
      local fn = cls_ns[special]
      if kindof(fn) == "function" then
        cls_ns[special] = { kind = special == "__new__" and "staticmethod" or "classmethod", fn = fn }
      end
    end
    local made = values.new_class(cls_name, cls_bases, cls_ns)
    made.interp = self
    made.ns_order = {}
    for i = 1, #ns_order do made.ns_order[i] = ns_order[i] end
    for _, v in pairs(made.ns) do
      local vk = kindof(v)
      if vk == "function" and v.defclass == nil then
        v.defclass = made
      elseif (vk == "staticmethod" or vk == "classmethod")
          and kindof(v.fn) == "function" and v.fn.defclass == nil then
        v.fn.defclass = made
      end
    end
    return made
  end
  local plain_meta = direct_new or metaclass == self.types.type
  if not plain_meta then
    local namespace = prepared
    if namespace == nil then
      namespace = values.new_dict()
      local seen = {}
      for _, key in ipairs(ns_order) do
        if not seen[key] and ns_map[key] ~= nil then
          seen[key] = true
          values.dict_set_hk(namespace, "s" .. key, key, ns_map[key])
        end
      end
      for key, value in pairs(ns_map) do
        if not seen[key] then values.dict_set_hk(namespace, "s" .. key, key, value) end
      end
    end
    local result = self:call(metaclass, { name, values.tuple(bases), namespace }, kwargs)
    return result
  end
  local ns = {}
  for k, v in pairs(ns_map) do ns[k] = v end
  local cls = make(name, bases, ns)
  self.classes[cls] = true
  cls.metaclass = metaclass
  self:install_class_slots(cls)
  if cls.ns.__doc__ == nil then
    cls.ns.__doc__ = NONE
    cls.ns_order[#cls.ns_order + 1] = "__doc__"
  end
  for key, descriptor in pairs(cls.ns) do
    if kindof(descriptor) == "instance" and class_lookup(descriptor.cls, "__set_name__") then
      self:call_dunder(descriptor, "__set_name__", { cls, key })
    end
  end
  for index = 2, #cls.mro do
    local init_sub = cls.mro[index].ns.__init_subclass__
    if init_sub ~= nil then
      local fn = kindof(init_sub) == "classmethod" and init_sub.fn or init_sub
      self:call({ kind = "boundmethod", fn = fn, recv = cls }, {}, kwargs)
      return cls
    end
  end
  if #kwargs > 0 then throw("TypeError", name .. ".__init_subclass__() takes no keyword arguments") end
  return cls
end

function Interp:install_class_slots(cls)
  local inherited_dict, inherited_weakref = false, false
  for _, base in ipairs(cls.declared_bases) do
    if kindof(base) == "class" and base ~= values.OBJECT then
      inherited_dict = inherited_dict or base.has_dict ~= false
      inherited_weakref = inherited_weakref or base.has_weakref ~= false
    end
  end
  local declared = cls.ns.__slots__
  local names = {}
  local wants_dict, wants_weakref = declared == nil, declared == nil
  if declared ~= nil then
    local raw = self:raw(declared)
    names = type(raw) == "string" and { raw } or iter.iter_to_array(self, declared)
    if #names > 0 and cls.prim ~= nil
        and (cls.prim.name == "int" or cls.prim.name == "tuple" or cls.prim.name == "bytes") then
      throw("TypeError", "nonempty __slots__ not supported for subtype of '" .. cls.prim.name .. "'")
    end
    for index, name in ipairs(names) do
      name = self:raw(name)
      if type(name) ~= "string" then throw("TypeError", "__slots__ items must be strings") end
      if not self:call(methods.builtin_method(self, name, "isidentifier"), {}, {}) then
        throw("TypeError", "__slots__ must be identifiers")
      end
      if name == "__dict__" then
        if wants_dict or inherited_dict then throw("TypeError", "__dict__ slot disallowed: we already got one") end
        wants_dict = true
      elseif name == "__weakref__" then
        if wants_weakref or inherited_weakref then throw("TypeError", "__weakref__ slot disallowed: we already got one") end
        wants_weakref = true
      else
        local owner_name = cls.name:gsub("^_+", "")
        if name:sub(1, 2) == "__" and name:sub(-2) ~= "__" and owner_name ~= "" then
          name = "_" .. owner_name .. name
        end
        if cls.ns[name] ~= nil then throw("ValueError", "'" .. name .. "' in __slots__ conflicts with class variable") end
      end
      names[index] = name
    end
  end
  cls.has_dict = inherited_dict or wants_dict
  cls.has_weakref = inherited_weakref or wants_weakref
  if cls.prim and (cls.prim.name == "int" or cls.prim.name == "tuple" or cls.prim.name == "bytes") then
    cls.has_weakref = inherited_weakref
  end
  local function install(name, storage)
    if cls.ns[name] == nil then cls.ns_order[#cls.ns_order + 1] = name end
    cls.ns[name] = { kind = "descriptor", name = name, owner = cls, storage = storage,
      descriptor = storage == "slot" and "member_descriptor" or "getset_descriptor" }
  end
  table.sort(names)
  for _, name in ipairs(names) do
    if name ~= "__dict__" and name ~= "__weakref__" then install(name, "slot") end
  end
  if wants_dict and not inherited_dict and cls.ns.__dict__ == nil then install("__dict__", "dict") end
  if cls.has_weakref and not inherited_weakref and cls.ns.__weakref__ == nil then install("__weakref__", "weakref") end
end

function Interp:storage_descriptor(descriptor, obj, action, value)
  if kindof(obj) ~= "instance" or not values.issub(obj.cls, descriptor.owner) then
    throw("TypeError", "descriptor '" .. descriptor.name .. "' requires a '" .. descriptor.owner.name .. "' object")
  end
  local storage = descriptor.storage
  if storage == "dict" then
    if action == "get" then return obj.dict_value or self:live_dict(obj.d) end
    if action == "delete" then obj.d, obj.dict_value = {}, nil; return NONE end
    local raw = self:raw(value)
    if kindof(raw) ~= "dict" then throw("TypeError", "__dict__ must be set to a dictionary") end
    obj.d, obj.dict_value = self:dictionary_namespace(raw), value
    return NONE
  end
  if storage == "weakref" then
    if action ~= "get" then throw("AttributeError", "attribute '__weakref__' is not writable") end
    local module = self.modules._weakref
    local namespace = module and (module.frame and module.frame.vars or module.ns)
    if namespace and namespace.getweakrefs then
      local refs = self:call(namespace.getweakrefs, { obj }, {})
      return refs.items[1] or NONE
    end
    return NONE
  end
  if action == "set" then
    obj.slot_values = obj.slot_values or {}
    obj.slot_values[descriptor] = value
    return NONE
  end
  local current = obj.slot_values and obj.slot_values[descriptor]
  if current == nil then throw("AttributeError", "'" .. obj.cls.name .. "' object has no attribute '" .. descriptor.name .. "'") end
  if action == "delete" then obj.slot_values[descriptor] = nil; return NONE end
  return current
end

function Interp:prepare_exception(err, first)
  if not values.is_exc(err) then return end
  local exc = err.value
  if not err.context_set then
    local active = self.exc_stack[#self.exc_stack]
    if active ~= nil and active ~= exc then
      local cursor, seen = active, {}
      while cursor ~= nil and cursor ~= NONE and not seen[cursor] do
        seen[cursor] = true
        local context = cursor.d.__context__
        if context == exc then cursor.d.__context__ = NONE; break end
        cursor = context
      end
      exc.d.__context__ = active
    end
    err.context_set = true
  end
  metadata.capture(self, err, first or #self.stack)
end

local function active_call(I, exc, first, fn, ...)
  I.exc_stack[#I.exc_stack + 1] = exc
  local ok, result, extra = pcall(fn, ...)
  if not ok and values.is_exc(result) then I:prepare_exception(result, first) end
  I.exc_stack[#I.exc_stack] = nil
  return ok, result, extra
end

STMTS["raise"] = function(I, s, fr)
  if not has(s, "exc") then
    if #I.exc_stack == 0 then
      throw("RuntimeError", "No active exception to re-raise")
    end
    local err = values.exc_err(I.exc_stack[#I.exc_stack])
    err.context_set = true
    err.trace_frames = { [I.stack[#I.stack]] = true }
    error(err, 0)
  end
  local v = I:ev(s.exc, fr)
  local cause
  if has(s, "cause") then cause = I:ev(s.cause, fr) end
  if kindof(v) == "class" then
    if not values.issub(v, values.BASE_EXC) then
      throw("TypeError", "exceptions must derive from BaseException")
    end
    v = I:call(v, {}, {})
  end
  if not (kindof(v) == "instance" and values.issub(v.cls, values.BASE_EXC)) then
    throw("TypeError", "exceptions must derive from BaseException")
  end
  if cause ~= nil then
    if kindof(cause) == "class" and values.issub(cause, values.BASE_EXC) then
      cause = I:call(cause, {}, {})
    end
    if cause ~= NONE and not (kindof(cause) == "instance" and values.issub(cause.cls, values.BASE_EXC)) then
      throw("TypeError", "exception causes must derive from BaseException")
    end
    v.d.__cause__, v.d.__suppress_context__ = cause, true
  end
  local err = values.exc_err(v)
  I:prepare_exception(err, #I.stack)
  error(err, 0)
end

function Interp:handler_matches(h, exc, fr)
  if not has(h, "t") then return true end
  local t = self:ev(h.t, fr)
  local classes
  if kindof(t) == "tuple" then
    classes = t.items
  else
    classes = { t }
  end
  for _, c in ipairs(classes) do
    if not (kindof(c) == "class" and values.issub(c, values.BASE_EXC)) then
      throw("TypeError",
        "catching classes that do not inherit from BaseException is not allowed")
    end
    if values.issub(exc.cls, c) then return true end
  end
  return false
end

-- (matching_subgroup_or_nil, residual_or_nil) for except* handler h against
-- exc, treating a non-group exception as a singleton group (PEP 654).
function Interp:split_group(exc, h, fr)
  local t = self:ev(h.t, fr)
  local classes = kindof(t) == "tuple" and t.items or { t }
  local function matches(e)
    for _, c in ipairs(classes) do
      if kindof(c) == "class" and values.issub(e.cls, c) then return true end
    end
    return false
  end
  if kindof(exc) == "instance"
      and values.issub(exc.cls, values.EXC_CLASSES.BaseExceptionGroup) then
    local members = {}
    local mem = exc.d.exceptions
    if mem ~= nil and mem.kind == "tuple" then members = mem.items end
    local matched, unmatched = {}, {}
    for _, e in ipairs(members) do
      if kindof(e) == "instance" and matches(e) then
        matched[#matched + 1] = e
      else
        unmatched[#unmatched + 1] = e
      end
    end
    if #matched == 0 then return nil, exc end
    local msg = exc.d.message or ""
    local function derive(members)
      local group = values.make_group(msg, members, true)
      for _, name in ipairs({ "__traceback__", "__cause__", "__context__", "__suppress_context__" }) do
        if exc.d[name] ~= nil then group.d[name] = exc.d[name] end
      end
      local notes = exc.d.__notes__
      if kindof(notes) == "list" then
        local copied = {}
        for index, note in ipairs(notes.items) do copied[index] = note end
        group.d.__notes__ = values.list(copied)
      elseif notes ~= nil then
        group.d.__notes__ = notes
      end
      return group
    end
    local rest = #unmatched > 0 and derive(unmatched) or nil
    return derive(matched), rest
  end
  if kindof(exc) == "instance" and matches(exc) then
    return values.make_group("", { exc }), nil
  end
  return nil, exc
end

local function try_star_phase(I, s, fr, saved)
  local ok, res = pcall(Interp.exec_block, I, get(s, "body"), fr)
  local pending, pending_error
  if ok then
    if res ~= nil then return res end
  else
    local err = res
    if values.is_trap(err) or not values.is_exc(err) then error(err, 0) end
    I:prepare_exception(err, saved)
    pending_error = err
    I:trim(saved)
    pending = err.value
  end
  if pending == nil then
    return I:exec_block(get(s, "orelse"), fr)
  end
  local handlers = get(s, "handlers") or EMPTY
  for hi = 1, #handlers do
    local h = handlers[hi]
    local matched_ok, match, rest = active_call(I, pending, saved, Interp.split_group, I, pending, h, fr)
    if not matched_ok then error(match, 0) end
    if match ~= nil then
      local hname = get(h, "name")
      if hname ~= nil then fr.vars[hname] = match end
      local ok2, res2 = active_call(I, match, saved, Interp.exec_block, I, get(h, "body"), fr)
      if hname ~= nil then fr.vars[hname] = nil end
      if not ok2 then error(res2, 0) end
      if res2 ~= nil then return res2 end
      pending = rest
      if pending == nil then break end
    end
  end
  if pending ~= nil then pending_error.value = pending; error(pending_error, 0) end
  return nil
end

-- body + handlers + orelse (finally handled by the caller)
local function try_phase(I, s, fr, saved)
  if get(s, "star") then return try_star_phase(I, s, fr, saved) end
  local ok, res = pcall(Interp.exec_block, I, get(s, "body"), fr)
  if ok then
    if res ~= nil then return res end -- return/break/continue skip orelse
    return I:exec_block(get(s, "orelse"), fr)
  end
  local err = res
  if values.is_trap(err) or not values.is_exc(err) then error(err, 0) end
  I:prepare_exception(err, saved)
  I:trim(saved)
  local handlers = get(s, "handlers") or EMPTY
  for hi = 1, #handlers do
    local h = handlers[hi]
    local matched_ok, matched = active_call(I, err.value, saved, Interp.handler_matches, I, h, err.value, fr)
    if not matched_ok then error(matched, 0) end
    if matched then
      local hname = get(h, "name")
      if hname ~= nil then fr.vars[hname] = err.value end
      local ok2, res2 = active_call(I, err.value, saved, Interp.exec_block, I, get(h, "body"), fr)
      if hname ~= nil then fr.vars[hname] = nil end -- deleted even on error
      if not ok2 then error(res2, 0) end
      return res2
    end
  end
  error(err, 0)
end

STMTS["try"] = function(I, s, fr)
  -- `finally` runs for diaspore-level control flow (fallthrough, exceptions,
  -- return/break/continue) but NOT for traps (they abandon execution).
  local saved = #I.stack
  local ok, res = pcall(try_phase, I, s, fr, saved)
  if not ok then
    local err = res
    if values.is_trap(err) or not values.is_exc(err) then error(err, 0) end
    I:prepare_exception(err, saved)
    I:trim(saved)
    local finally_ok, fsig = active_call(I, err.value, saved, Interp.exec_block, I, get(s, "finalbody"), fr)
    if not finally_ok then error(fsig, 0) end
    if fsig ~= nil then return fsig end -- control flow in finally wins
    error(err, 0)
  end
  local fsig = I:exec_block(get(s, "finalbody"), fr)
  if fsig ~= nil then return fsig end
  return res
end

local function with_items(I, items, i, body, fr)
  if i > #items then return I:exec_block(body, fr) end
  local item = items[i]
  local statement_line = fr.line
  local ctx = I:ev(item.ctx, fr)
  local enter, exit_
  local k = kindof(ctx)
  if k == "file" then
    enter = function() return ctx end
    exit_ = function()
      I:close_file(ctx)
      return false
    end
  elseif k == "memoryview" then
    enter = function() return I:call(buffer.attr(I, ctx, "__enter__"), {}, {}) end
    exit_ = function(a, b, c) return I:call(buffer.attr(I, ctx, "__exit__"), { a, b, c }, {}) end
  elseif k == "nativeobject" and ctx.methods.__enter__ ~= nil then
    enter = function() return I:call(ctx.methods.__enter__, {}, {}) end
    exit_ = function(a, b, c) return I:call(ctx.methods.__exit__, { a, b, c }, {}) end
  elseif k == "instance" then
    if class_lookup(ctx.cls, "__enter__") == nil
        or class_lookup(ctx.cls, "__exit__") == nil then
      throw("TypeError", "'" .. ctx.cls.name
        .. "' object does not support the context manager protocol")
    end
    enter = function() return I:call_dunder(ctx, "__enter__", {}) end
    exit_ = function(a, b, c) return I:call_dunder(ctx, "__exit__", { a, b, c }) end
  else
    throw("TypeError", "'" .. type_name(ctx)
      .. "' object does not support the context manager protocol")
  end
  local entered = enter()
  if has(item, "name") then I:assign(item.name, entered, fr) end
  local saved = #I.stack
  local ok, res = pcall(with_items, I, items, i + 1, body, fr)
  if ok then
    fr.line = statement_line
    exit_(NONE, NONE, NONE) -- fallthrough and return/break/continue unwinding
    return res
  end
  local err = res
  if values.is_trap(err) or not values.is_exc(err) then
    error(err, 0) -- abandonment: no further diaspore code, __exit__ included
  end
  local inst = err.value
  I:prepare_exception(err, saved)
  I:trim(saved)
  fr.line = statement_line
  local exit_ok, suppressed = active_call(I, inst, saved, exit_, inst.cls, inst, inst.d.__traceback__)
  if not exit_ok then error(suppressed, 0) end
  if ops.truth(I, suppressed) then
    return nil
  end
  error(err, 0)
end

STMTS["with"] = function(I, s, fr)
  return with_items(I, s.items, 1, s.body, fr)
end

STMTS["import"] = function(I, s, fr)
  local mod = I:import_module(s.module)
  local bind = get(s, "bind")
  if bind == "none" then return nil end
  if bind == "root" then
    local root = I.modules[s.module:match("^([^.]*)")]
    I:store_name(fr, s.alias, root)
  else
    I:store_name(fr, s.alias, mod)
  end
  return nil
end

STMTS["importfrom"] = function(I, s, fr)
  local mod = I:import_module(s.module)
  for _, spec in ipairs(s.names) do
    if spec.n == "*" then
      local ns = mod.kind == "module" and mod.frame.vars or mod.ns
      local saved = #I.stack
      local found, names = pcall(Interp.get_attr, I, mod, "__all__")
      if not found then
        if not values.is_exc(names) or not values.exc_isa(names.value, "AttributeError") then error(names, 0) end
        I:trim(saved)
        names = nil
      end
      if names ~= nil then
        local i = 0
        while true do
          local saved = #I.stack
          local ok, name = pcall(ops.get_index, I, names, i)
          if not ok then
            if values.is_exc(name) and values.issub(name.value.cls, values.EXC_CLASSES.IndexError) then
              I:trim(saved)
              break
            end
            error(name, 0)
          end
          if type(name) ~= "string" then
            throw("TypeError", "Item in " .. s.module .. ".__all__ must be str, not " .. type_name(name))
          end
          I:store_name(fr, name, I:get_attr(mod, name))
          i = i + 1
        end
      else
        for name, value in pairs(ns) do
          if name:sub(1, 1) ~= "_" then I:store_name(fr, name, value) end
        end
      end
    else
      local saved = #I.stack
      local ok, v = pcall(Interp.get_attr, I, mod, spec.n)
      if not ok then
        if values.is_exc(v) and values.issub(v.value.cls, values.EXC_CLASSES.AttributeError) then
          I:trim(saved)
          throw("ImportError", "cannot import name '" .. spec.n
            .. "' from '" .. s.module .. "'")
        end
        error(v, 0)
      end
      I:store_name(fr, spec.alias, v)
    end
  end
  return nil
end

STMTS["del"] = function(I, s, fr)
  for _, t in ipairs(s.targets) do
    local op = t.op
    if op == "name" then
      local id = t.id
      if fr.vars[id] ~= nil then
        fr.vars[id] = nil
      elseif fr.localset ~= nil and fr.localset[id] then
        throw("UnboundLocalError", "cannot access local variable '" .. id
          .. "' where it is not associated with a value")
      elseif fr.globals[id] ~= nil then
        fr.globals[id] = nil
      else
        throw("NameError", "name '" .. id .. "' is not defined")
      end
    elseif op == "index" then
      local o = I:ev(t.o, fr)
      local key = I:ev_key(t.k, fr)
      ops.del_index(I, o, key)
    elseif op == "attr" then
      I:del_attr(I:ev(t.o, fr), t.n)
    else
      throw("TypeError", "cannot delete this target")
    end
  end
  return nil
end

-- ------------------------------------------------------------------- expressions

local EXPRS = {}

function Interp:ev(e, fr)
  local op = e.op
  if op == "name" then return self:load_name(fr, e.id) end
  if op == "const" and e.__cv ~= nil then return e.__cv end
  local h = EXPRS[op]
  if h == nil then
    values.trap("diaspore: bad expression op " .. tostring(op), 4)
  end
  return h(self, e, fr)
end

function Interp:ev_key(k, fr)
  if k.op == "slicelit" then
    return {
      kind = "slice",
      lower = has(k, "lower") and self:ev(k.lower, fr) or NONE,
      upper = has(k, "upper") and self:ev(k.upper, fr) or NONE,
      step = has(k, "step") and self:ev(k.step, fr) or NONE,
    }
  end
  return self:ev(k, fr)
end

local function string_literal(node, text)
  if node.codepoints == nil then return text end
  if node.__string == nil then
    local parts = {}
    for i, codepoint in ipairs(node.codepoints) do parts[i] = utf8.char(codepoint) end
    node.__string = table.concat(parts)
  end
  return node.__string
end

local function const_val(e)
  local t = e.t
  if t == "none" then return NONE end
  if t == "ellipsis" then return values.ELLIPSIS end
  if t == "complex" then
    local re = bigint.is_int(e.re) and bigint.to_float(e.re) or tonumber(e.re)
    local im = bigint.is_int(e.im) and bigint.to_float(e.im) or tonumber(e.im)
    return values.complex(re * 1.0, im * 1.0)
  end
  if t == "int" then
    if bigint.is_int(e.v) then return e.v end
    if type(e.v) == "string" then return bigint.parse(e.v, 10) end
    return bigint.from_float(e.v)
  end
  if t == "float" then
    local v = e.v
    if v == "inf" then return math.huge end
    if v == "-inf" then return -math.huge end
    if v == "nan" then return 0 / 0 end
    return (bigint.is_int(v) and bigint.to_float(v) or tonumber(v)) * 1.0
  end
  if t == "str" then return string_literal(e, e.v) end
  if t == "bool" then return e.v end
  if t == "bytes" then return values.bytes(b64decode(e.v)) end
  values.trap("diaspore: bad const type " .. tostring(t), 4)
end

EXPRS["const"] = function(_I, e, _fr)
  local c = e.__cv
  if c ~= nil then return c end
  c = const_val(e)
  e.__cv = c
  return c
end

EXPRS["attr"] = function(I, e, fr)
  return I:get_attr(I:ev(e.o, fr), e.n)
end

EXPRS["index"] = function(I, e, fr)
  return ops.get_index(I, I:ev(e.o, fr), I:ev_key(e.k, fr))
end

EXPRS["binop"] = function(I, e, fr)
  return ops.binop(I, e.kind, I:ev(e.l, fr), I:ev(e.r, fr), false)
end

EXPRS["unary"] = function(I, e, fr)
  local kind = e.kind
  local v = I:ev(e.v, fr)
  if kind == "not" then return not ops.truth(I, v) end
  return ops.unary(I, kind, v)
end

EXPRS["boolop"] = function(I, e, fr)
  local vals = e.values
  local n = #vals
  local v = NONE
  if e.kind == "and" then
    for i = 1, n do
      v = I:ev(vals[i], fr)
      if i == n or not ops.truth(I, v) then return v end
    end
  else
    for i = 1, n do
      v = I:ev(vals[i], fr)
      if i == n or ops.truth(I, v) then return v end
    end
  end
  return v
end

EXPRS["compare"] = function(I, e, fr)
  local left = I:ev(e.left, fr)
  local kinds = e.ops
  local comps = e.comps
  local result = true
  for i = 1, #kinds do
    local right = I:ev(comps[i], fr)
    result = ops.compare(I, kinds[i], left, right)
    if i < #kinds and not I:truth(result) then return result end
    left = right
  end
  return result
end

EXPRS["cond"] = function(I, e, fr)
  if ops.truth(I, I:ev(e.test, fr)) then
    return I:ev(e["then"], fr)
  end
  return I:ev(e["else"], fr)
end

function Interp:eval_elts(elts, fr)
  local out = {}
  for i = 1, #elts do
    local x = elts[i]
    if x.op == "starred" then
      local spread = iter.iter_to_array(self, self:ev(x.v, fr))
      for j = 1, #spread do out[#out + 1] = spread[j] end
    else
      out[#out + 1] = self:ev(x, fr)
    end
  end
  return out
end

EXPRS["list"] = function(I, e, fr)
  return values.list(I:eval_elts(get(e, "elts") or EMPTY, fr))
end

EXPRS["tuple"] = function(I, e, fr)
  return values.tuple(I:eval_elts(get(e, "elts") or EMPTY, fr))
end

EXPRS["set"] = function(I, e, fr)
  local out = values.new_set()
  local elts = I:eval_elts(get(e, "elts") or EMPTY, fr)
  for i = 1, #elts do values.set_add(out, elts[i]) end
  return out
end

EXPRS["dict"] = function(I, e, fr)
  local out = values.new_dict()
  local entries = get(e, "entries") or EMPTY
  for i = 1, #entries do
    local entry = entries[i]
    if not has(entry, "k") then
      local v = I:ev(entry.v, fr)
      if kindof(v) ~= "dict" then
        throw("TypeError", "argument after ** must be a mapping")
      end
      local es = values.dict_entries(v)
      for j = 1, #es do
        local item = es[j]
        values.dict_set_hk(out, item.hk or values.hash_key(item.k), item.k, item.v)
      end
    else
      local k = I:ev(entry.k, fr)
      local v = I:ev(entry.v, fr)
      values.dict_set(out, k, v)
    end
  end
  return out
end

EXPRS["fstring"] = function(I, e, fr)
  local out = {}
  for _, p in ipairs(e.parts) do
    if type(p.s) == "string" then
      out[#out + 1] = string_literal(p, p.s)
    else
      local v = I:ev(p.v, fr)
      local conv = get(p, "conv")
      if conv == "r" then
        v = ops.repr(I, v)
      elseif conv == "s" then
        v = ops.str(I, v)
      elseif conv == "a" then
        v = ops.ascii(I, v)
      end
      local spec = ""
      if has(p, "spec") then spec = I:ev(p.spec, fr) end
      out[#out + 1] = format.format_value(I, v, spec)
    end
  end
  return table.concat(out)
end

EXPRS["funcexpr"] = function(I, e, fr)
  return I:make_function(e, fr)
end
EXPRS["func"] = EXPRS["funcexpr"]

EXPRS["yield"] = function(I, e, fr)
  if not fr.is_gen then
    values.trap("diaspore: yield outside generator frame", 4)
  end
  local v = has(e, "v") and I:ev(e.v, fr) or NONE
  local token = coroutine.yield(v)
  if token.kind == "send" then return token.value end
  values.throw_val(token.exc) -- throw()/close() delivered at the yield point
end

EXPRS["yieldfrom"] = function(I, e, fr)
  if not fr.is_gen then
    values.trap("diaspore: yield outside generator frame", 4)
  end
  local it = iter.get_iter(I, I:ev(e.v, fr))
  local delegating = it.iname == "generator"
  local y, more
  if delegating then
    y, more = it.advance(iter.SEND_NONE)
  else
    y, more = it.next()
  end
  if not more then return it.retval end
  while true do
    local token = coroutine.yield(y)
    if token.kind ~= "send" then
      -- forward throw/close into the subgenerator (PEP 380)
      if delegating then
        local exc = token.exc
        if values.exc_isa(exc, "GeneratorExit") then
          iter.gen_close(I, it)
          values.throw_val(exc)
        end
        y, more = it.advance({ kind = "throw", exc = exc })
        if not more then return it.retval end
      else
        values.throw_val(token.exc)
      end
    else
      if delegating then
        y, more = it.advance({ kind = "send", value = token.value })
      else
        y, more = it.next() -- sent value discarded by plain iterators
      end
      if not more then return it.retval end
    end
  end
end

EXPRS["namedexpr"] = function(I, e, fr)
  local v = I:ev(e.v, fr)
  I:store_name(fr, e.target.id, v)
  return v
end

local function kw_set(kwargs, k, v)
  for i = 1, #kwargs do
    if kwargs[i].k == k then
      kwargs[i].v = v
      return
    end
  end
  kwargs[#kwargs + 1] = { k = k, v = v }
end

EXPRS["call"] = function(I, e, fr)
  local f = I:ev(e.f, fr)
  local python_function = type(f) == "table" and f.kind == "function"
  local args = python_function and I.free_args or nil
  if args then I.free_args, args.next = args.next, nil else args = {} end
  local anodes = get(e, "args") or EMPTY
  for i = 1, #anodes do
    local a = anodes[i]
    if a.op == "starred" then
      local spread = iter.iter_to_array(I, I:ev(a.v, fr))
      for j = 1, #spread do args[#args + 1] = spread[j] end
    else
      args[#args + 1] = I:ev(a, fr)
    end
  end
  local knodes = get(e, "kw") or EMPTY
  local kwargs = #knodes > 0 and {} or EMPTY
  for i = 1, #knodes do
    local kw = knodes[i]
    if not has(kw, "n") then
      local d = I:ev(kw.v, fr)
      if kindof(d) ~= "dict" then
        throw("TypeError", "argument after ** must be a mapping")
      end
      local es = values.dict_entries(d)
      for j = 1, #es do
        if type(es[j].k) ~= "string" then
          throw("TypeError", "keywords must be strings")
        end
        kw_set(kwargs, es[j].k, es[j].v)
      end
    else
      kw_set(kwargs, kw.n, I:ev(kw.v, fr))
    end
  end
  if f == values.SUPER_MARKER then return I:make_super(args, fr) end
  if python_function then
    I:fuel()
    return I:call_function(f, args, kwargs, true)
  end
  return I:call(f, args, kwargs)
end

-- ------------------------------------------------------------------- functions

local function function_doc(node)
  local first = node.body and node.body[1]
  local constant = first and first.op == "expr" and first.v
  if constant and constant.op == "const" and constant.t == "str" then return string_literal(constant, constant.v) end
  return NONE
end

function Interp:make_function(node, fr)
  local closure = fr
  if not get(node, "annotation_scope") then
    while closure ~= nil and closure.is_class do closure = closure.parent end
  end
  if closure and closure.is_exec and closure.vars ~= closure.globals then
    closure = new_frame(closure.globals, nil, nil, EMPTY, EMPTY, false,
      closure.globals, "<module>", fr.module_name)
  end
  local captured = closure
  while captured and not captured.closure_retained do
    captured.escaped, captured.closure_retained = true, true
    captured = captured.parent
  end
  local meta = node.__fnmeta
  if meta == nil then
    local p = get(node, "params") or EMPTY
    meta = {
      args = get(p, "args") or EMPTY,
      posonly = get(p, "posonly") or 0,
      vararg = get(p, "vararg"),
      kwonly = get(p, "kwonly") or EMPTY,
      kwarg = get(p, "kwarg"),
      defaults_nodes = get(p, "defaults") or EMPTY,
      kwdefaults_nodes = get(p, "kwdefaults") or EMPTY,
      localset = to_set(get(node, "locals")),
      gdecl = to_set(get(node, "gdecl")),
      ndecl = to_set(get(node, "ndecl")),
      gen = get(node, "gen") or false,
      coro = get(node, "coro") or false,
      asyncgen = get(node, "asyncgen") or false,
      annotation_scope = get(node, "annotation_scope") or false,
      name = node.name,
    }
    meta.generator_name = meta.asyncgen and "async_generator"
      or meta.coro and "coroutine" or meta.gen and "generator" or nil
    meta.plain_positional = meta.vararg == nil and meta.kwarg == nil and #meta.kwonly == 0
    node.__fnmeta = meta
  end
  local fn = {
    kind = "function", node = node, name = meta.name, meta = meta,
    closure = closure, module_name = fr.module_name, defclass = nil,
    defaults = {}, kwdefaults = {}, doc = function_doc(node),
  }
  for i = 1, #meta.defaults_nodes do
    fn.defaults[i] = self:ev(meta.defaults_nodes[i], fr)
  end
  for i = 1, #meta.kwonly do
    local d = meta.kwdefaults_nodes[i]
    if d ~= nil and d ~= NULL then
      fn.kwdefaults[meta.kwonly[i]] = self:ev(d, fr)
    end
  end
  return fn
end

local function contains_str(arr, s)
  for i = 1, #arr do
    if arr[i] == s then return true end
  end
  return false
end

function Interp:bind_args(fn, args, kwargs, out)
  local meta = fn.meta
  local names = meta.args
  out = out or {}
  local nargs = #args
  local nnames = #names
  if nargs > nnames then
    if meta.vararg ~= nil then
      local rest = {}
      for i = nnames + 1, nargs do rest[#rest + 1] = args[i] end
      out[meta.vararg] = values.tuple(rest)
    else
      local have_defaults = #fn.defaults > 0
      local took
      if have_defaults then
        took = "from " .. (nnames - #fn.defaults) .. " to " .. nnames
      else
        took = tostring(nnames)
      end
      local sfx = (nnames ~= 1 or have_defaults) and "s" or ""
      local verb = nargs ~= 1 and "were" or "was"
      throw("TypeError", fn.name .. "() takes " .. took .. " positional argument"
        .. sfx .. " but " .. nargs .. " " .. verb .. " given")
    end
  elseif meta.vararg ~= nil then
    out[meta.vararg] = values.tuple({})
  end
  local npos = math.min(nargs, nnames)
  for i = 1, npos do out[names[i]] = args[i] end
  local extra = {}
  for i = 1, #kwargs do
    local k, v = kwargs[i].k, kwargs[i].v
    local positional_only = false
    for j = 1, meta.posonly do if names[j] == k then positional_only = true; break end end
    if positional_only then
      if meta.kwarg == nil then
        throw("TypeError", fn.name .. "() got some positional-only arguments passed as keyword arguments: '" .. k .. "'")
      end
      extra[#extra + 1] = { k = k, v = v }
    elseif contains_str(names, k) then
      if out[k] ~= nil then
        throw("TypeError", fn.name .. "() got multiple values for argument '" .. k .. "'")
      end
      out[k] = v
    elseif contains_str(meta.kwonly, k) then
      out[k] = v
    elseif meta.kwarg ~= nil then
      extra[#extra + 1] = { k = k, v = v }
    else
      throw("TypeError", fn.name .. "() got an unexpected keyword argument '" .. k .. "'")
    end
  end
  local ndefaults = #fn.defaults
  local nrequired = ndefaults > 0 and (nnames - ndefaults) or nnames
  local missing = {}
  for i = 1, nrequired do
    if out[names[i]] == nil then missing[#missing + 1] = names[i] end
  end
  if #missing > 0 then
    throw("TypeError", fn.name .. "() missing " .. #missing
      .. " required positional argument" .. (#missing > 1 and "s" or "")
      .. ": " .. name_list(missing))
  end
  for i = 1, ndefaults do
    local name = names[nnames - ndefaults + i]
    if out[name] == nil then out[name] = fn.defaults[i] end
  end
  local miss_kw = {}
  for i = 1, #meta.kwonly do
    local name = meta.kwonly[i]
    if out[name] == nil then
      local d = fn.kwdefaults[name]
      if d ~= nil then
        out[name] = d
      else
        miss_kw[#miss_kw + 1] = name
      end
    end
  end
  if #miss_kw > 0 then
    throw("TypeError", fn.name .. "() missing " .. #miss_kw
      .. " required keyword-only argument" .. (#miss_kw > 1 and "s" or "")
      .. ": " .. name_list(miss_kw))
  end
  if meta.kwarg ~= nil then
    local d = values.new_dict()
    for i = 1, #extra do
      values.dict_set_hk(d, "s" .. extra[i].k, extra[i].k, extra[i].v)
    end
    out[meta.kwarg] = d
  end
  return out
end

-- ------------------------------------------------------------------- calling

function Interp:call(f, args, kwargs)
  self:fuel()
  local t = type(f)
  if t ~= "table" then
    throw("TypeError", "'" .. type_name(f) .. "' object is not callable")
  end
  local k = f.kind
  if k == "function" then return self:call_function(f, args, kwargs) end
  if k == "GenericAlias" then return typeexpr.call(self, f, args, kwargs) end
  if k == "staticmethod" then return self:call(f.fn, args, kwargs) end
  if k == "boundmethod" then
    local a2 = { f.recv }
    for i = 1, #args do a2[i + 1] = args[i] end
    return self:call(f.fn, a2, kwargs)
  end
  if k == "class" then
    local meta = f.metaclass
    local hook = kindof(meta) == "class" and class_lookup(meta, "__call__")
    if hook then return self:call(self:bind_attr(hook, f, meta), args, kwargs) end
    return self:instantiate(f, args, kwargs)
  end
  if k == "btype" then return f.ctor(self, args, kwargs) end
  if k == "builtin" then return f.fn(self, args, kwargs) end
  if k == "nativeobject" and f.methods.__call__ ~= nil then
    return self:call(f.methods.__call__, args, kwargs)
  end
  if k == "instance" then
    if class_lookup(f.cls, "__call__") ~= nil then
      return self:call_dunder_kw(f, "__call__", args, kwargs)
    end
    if kindof(f.prim) == "GenericAlias" then return typeexpr.call(self, f, args, kwargs) end
    throw("TypeError", "'" .. f.cls.name .. "' object is not callable")
  end
  if k == "supermarker" then
    throw("RuntimeError", "super(): no arguments and no enclosing method")
  end
  throw("TypeError", "'" .. type_name(f) .. "' object is not callable")
end

function Interp:call_function(fn, args, kwargs, owned_args)
  local meta = fn.meta
  local entry = fn.free_frame
  if entry then fn.free_frame, entry.next = entry.next, nil end
  local vars = entry and entry.fr.vars or {}
  local names = meta.args
  if meta.plain_positional and #args == #names and #kwargs == 0 then
    for i = 1, #names do vars[names[i]] = args[i] end
  else
    self:bind_args(fn, args, kwargs, vars)
  end
  if owned_args then
    for i = #args, 1, -1 do args[i] = nil end
    args.next, self.free_args = self.free_args, args
  end
  local fr
  if entry then
    fr = entry.fr
    fr.line = 0
  else
    fr = new_frame(vars, fn.closure, meta.localset, meta.gdecl, meta.ndecl,
      false, fn.closure ~= nil and fn.closure.globals or nil, fn.name, fn.module_name)
    fr.fn = fn
    if meta.annotation_scope then fr.annotation_scope = true end
    if fn.unoptimized then fr.parent, fr.is_exec = nil, true end
  end
  fr.defclass = fn.defclass
  if fr.defclass and #meta.args > 0 then fr.self_ = vars[meta.args[1]] end
  if meta.generator_name then
    fr.is_gen = true
    local result = iter.new_generator(self, fn, fr)
    result.iname = meta.generator_name
    return result
  end
  if self.depth >= self.recursion_limit then
    throw("RecursionError", "maximum recursion depth exceeded")
  end
  local st = self.stack
  local sn = #st + 1
  entry = entry or { fr = fr }
  entry.depth0, entry.previous = self.depth, st[sn - 1]
  st[sn] = entry
  self.depth = self.depth + 1
  local sig, result = nil, NONE
  if fn.unoptimized and fn.code.mode == "eval" then
    result = self:ev(fn.code.nodes[1].v, fr)
  else
    local body = fn.node.body
    if #body == 1 and body[1].op == "return" then
      local statement = body[1]
      self:fuel()
      local line = statement.ln
      if line ~= nil and line ~= NULL then fr.line = line end
      if has(statement, "v") then result = self:ev(statement.v, fr) end
    else
      sig = self:exec_block(body, fr)
    end
  end
  self.depth = self.depth - 1
  st[sn] = nil
  -- Escaping frames keep their namespace and caller chain. Only private
  -- activations may be recycled, and never across a code/closure replacement.
  if not fr.escaped and not fn.unoptimized and fn.meta == meta
      and fn.closure == fr.parent and getmetatable(vars) == nil then
    for name in pairs(vars) do vars[name] = nil end
    if fr.defclass then fr.self_ = nil end
    entry.previous = nil
    entry.next, fn.free_frame = fn.free_frame, entry
  end
  if sig ~= nil then
    if sig.tag == "return" then return sig.value end
    values.trap("diaspore: control-flow signal escaped a function body", 4)
  end
  return result
end

local function prim_empty(name)
  if name == "dict" then return values.new_dict() end
  if name == "list" then return values.list({}) end
  if name == "bytearray" then return buffer.bytearray("") end
  if name == "set" then return values.new_set() end
  if name == "frozenset" then return values.new_set(true) end
  if name == "str" then return "" end
  if name == "bytes" then return values.bytes("") end
  if name == "tuple" then return values.tuple({}) end
  if name == "property" then return values.property() end
  if name == "int" or name == "bool" then return 0 end
  return 0.0 -- float
end

local IMMUTABLE_PRIM = {
  str = true, bytes = true, int = true, float = true, tuple = true, bool = true,
  frozenset = true, GenericAlias = true,
}

function Interp:instantiate(cls, args, kwargs)
  local abstract = class_lookup(cls, "__abstractmethods__")
  if abstract ~= nil and self:truth(abstract) then throw("TypeError", "Can't instantiate abstract class " .. cls.name) end
  local new = class_lookup(cls, "__new__")
  local init = class_lookup(cls, "__init__")
  local custom_new = new ~= nil and new ~= values.OBJECT.ns.__new__
    and not (cls.prim ~= nil and new.primitive_new == cls.prim)
  local ismeta = cls.prim == self.types.type
  local inst
  if custom_new or ismeta then
    if not custom_new then new = self:builtin_unbound("type", "__new__") end
    if kindof(new) == "staticmethod" then new = new.fn end
    local forwarded = { cls }
    for _, arg in ipairs(args) do forwarded[#forwarded + 1] = arg end
    inst = self:call(new, forwarded, kwargs)
    local actual = kindof(inst) == "instance" and inst.cls or kindof(inst) == "class" and inst.metaclass
    if actual == nil or not values.issub(actual, cls) then return inst end
  else
    inst = values.new_instance(cls)
    if cls.prim then
      if IMMUTABLE_PRIM[cls.prim.name] or init == values.OBJECT.ns.__init__ or init == nil then
        inst.prim = cls.prim.ctor(self, args, kwargs)
      else inst.prim = prim_empty(cls.prim.name) end
    elseif (init == nil or init == values.OBJECT.ns.__init__) and (#args > 0 or #kwargs > 0) then
      throw("TypeError", cls.name .. "() takes no arguments")
    end
  end
  if init and init ~= values.OBJECT.ns.__init__ then
    local result = self:call(self:bind_attr(init, inst, cls), args, kwargs)
    if result ~= NONE then throw("TypeError", "__init__() should return None, not '" .. type_name(result) .. "'") end
  end
  return inst
end

-- Raw builtin value of obj (unwrap a builtin-subclass instance).
function Interp:raw(obj)
  if kindof(obj) == "instance" and obj.prim ~= nil then return obj.prim end
  return obj
end

-- `dict.__getitem__`, `list.append`, ... as unbound functions that operate on
-- the RAW primitive (bypassing subclass overrides).
local PRIM_METHOD_SETS -- lazy (methods module)
local PRIM_UNARY = { __neg__ = "neg", __pos__ = "pos", __invert__ = "invert" }
local PRIM_PROTOCOLS = {
  str = "read", bytes = "read", tuple = "read", range = "read", mappingproxy = "read",
  bytearray = "write", list = "write", dict = "write", memoryview = "view",
  set = "set", frozenset = "set",
}
local NATIVE_DESCRIPTORS = {
  "__new__", "__init__", "__repr__", "__str__", "__hash__", "__eq__", "__ne__",
  "__getitem__", "__setitem__", "__delitem__", "__len__", "__contains__", "__iter__",
  "__abs__", "__neg__", "__pos__", "__invert__", "__reversed__", "__or__", "__ror__",
  "__ior__", "from_bytes", "maketrans",
  "__mro__", "__bases__", "__dict__",
}
function Interp:builtin_unbound(tname, method)
  local owner = self.types[tname] or self.internal_types[tname]
  if owner and owner.ns[method] ~= nil then return owner.ns[method] end
  if tname == "bool" and method ~= "__new__" and method ~= "__repr__" then
    return self:builtin_unbound("int", method)
  end
  local descriptor = self:make_builtin_descriptor(tname, method)
  if descriptor ~= nil then
    if owner then owner.ns[method] = descriptor end
    return descriptor
  end
  return values.OBJECT.ns[method]
end

function Interp:populate_builtin_namespace(owner)
  if owner.namespace_ready then return end
  owner.namespace_ready = true
  for _, name in ipairs(NATIVE_DESCRIPTORS) do self:builtin_unbound(owner.name, name) end
  for name in pairs(methods.METHOD_SETS[owner.name] or EMPTY) do
    self:builtin_unbound(owner.name, name)
  end
end

function Interp:make_builtin_descriptor(tname, method)
  if tname == "type" then
    if method == "__repr__" then
      return values.builtin(method, function(_, args, kwargs)
        if #args == 0 then
          throw("TypeError", "descriptor '__repr__' of 'type' object needs an argument")
        end
        local result = ops.type_repr(args[1])
        if #args ~= 1 then throw("TypeError", "expected 0 arguments, got " .. (#args - 1)) end
        if next(kwargs) ~= nil then throw("TypeError", "wrapper __repr__() takes no keyword arguments") end
        return result
      end, "wrapper_descriptor")
    end
    if method == "__mro__" or method == "__bases__" or method == "__dict__" then
      return { kind = "descriptor", name = method, owner = self.types.type,
        descriptor = "getset_descriptor", type_field = true }
    end
    if method == "__new__" then
      local constructor = values.builtin(method, function(I, args, kw)
        local bases, namespace = I:raw(args[3]), I:raw(args[4])
        if #args ~= 4 or kindof(bases) ~= "tuple" or kindof(namespace) ~= "dict" then
          throw("TypeError", "type.__new__ requires metaclass, name, bases and dict")
        end
        local ns, order = {}, {}
        for _, entry in ipairs(values.dict_entries(namespace)) do
          if type(entry.k) ~= "string" then throw("TypeError", "type namespace keys must be strings") end
          ns[entry.k] = entry.v; order[#order + 1] = entry.k
        end
        -- type.__new__ bypasses the metaclass call but establishes it before hooks.
        return I:build_class(args[2], bases.items, ns, order, args[1], kw, nil, true)
      end)
      constructor.bound_self = self.types.type
      return constructor
    end
    if method == "__init__" then return values.builtin(method, function() return NONE end) end
    if method == "__prepare__" then return values.builtin(method, function() return values.new_dict() end) end
  end
  if tname == "int" and method == "from_bytes" then
    return { kind = "classmethod", fn = values.builtin(method, function(I, args, kwargs)
      local rest = {}
      for index = 2, #args do rest[index - 1] = args[index] end
      local result = buffer.from_bytes(I, rest, kwargs)
      if args[1] == I.types.int then return result end
      return I:call(args[1], { result }, {})
    end) }
  end
  if tname == "str" and method == "maketrans" then
    return { kind = "staticmethod", fn = methods.builtin_method(self, "", method) }
  end
  if (tname == "bytes" or tname == "bytearray") and method == "maketrans" then
    return { kind = "staticmethod", fn = values.builtin(method, methods.bytes_maketrans) }
  end
  local unary = PRIM_UNARY[method]
  if (tname == "int" or tname == "bool" or tname == "float" or tname == "complex")
      and (method == "__abs__" or unary ~= nil)
      and (method ~= "__invert__" or tname == "int" or tname == "bool") then
    return values.builtin(method, function(I, args, kwargs)
      if #args ~= 1 or next(kwargs) ~= nil then throw("TypeError", method .. "() takes exactly one argument") end
      local receiver = I:raw(args[1])
      local valid = (tname == "int" or tname == "bool") and values.is_int_kind(receiver)
        or tname == "float" and math.type(receiver) == "float"
        or tname == "complex" and kindof(receiver) == "complex"
      if not valid then throw("TypeError", "descriptor '" .. method .. "' requires a '" .. tname .. "' object") end
      if unary ~= nil then return ops.unary(I, unary, receiver) end
      return I:call(I.builtins.abs, { receiver }, {})
    end, "wrapper_descriptor")
  end
  if method == "__reversed__" and (tname == "list" or tname == "dict" or tname == "range") then
    return values.builtin(method, function(I, args, kwargs)
      if #args ~= 1 or next(kwargs) ~= nil then throw("TypeError", method .. "() takes exactly one argument") end
      local receiver = I:raw(args[1])
      if type_name(receiver) ~= tname then throw("TypeError", "descriptor '" .. method .. "' requires a '" .. tname .. "' object") end
      return I:call(I.builtins.reversed, { receiver }, {})
    end, "method_descriptor")
  end
  if tname == "dict" and (method == "__or__" or method == "__ror__" or method == "__ior__") then
    return values.builtin(method, function(I, args, kwargs)
      if #args ~= 2 or next(kwargs) ~= nil then throw("TypeError", method .. "() takes exactly two arguments") end
      if kindof(I:raw(args[1])) ~= "dict" then throw("TypeError", "descriptor '" .. method .. "' requires a 'dict' object") end
      if method == "__ror__" then return ops.dict_union(I, args[2], args[1], false) end
      return ops.dict_union(I, args[1], args[2], method == "__ior__")
    end, "wrapper_descriptor")
  end
  if tname == "dict" and (method == "__eq__" or method == "__ne__") then
    return values.builtin(method, function(I, args, kwargs)
      if #args ~= 2 or next(kwargs) ~= nil then throw("TypeError", method .. "() takes exactly two arguments") end
      local receiver, other = I:raw(args[1]), I:raw(args[2])
      if kindof(receiver) ~= "dict" then throw("TypeError", "descriptor '" .. method .. "' requires a 'dict' object") end
      if kindof(other) ~= "dict" then return values.NOT_IMPLEMENTED end
      local equal = ops.eq(I, receiver, other)
      if method == "__ne__" then return not equal end
      return equal
    end, "wrapper_descriptor")
  end
  if method == "__hash__" then
    if tname == "list" or tname == "dict" or tname == "set" or tname == "bytearray" then return NONE end
    if tname == "int" or tname == "bool" or tname == "float" or tname == "complex"
        or tname == "str" or tname == "bytes" or tname == "tuple" or tname == "range"
        or tname == "frozenset" then
      return values.builtin(method, function(I, args, kwargs)
        if #args ~= 1 or next(kwargs) ~= nil then throw("TypeError", method .. "() takes exactly one argument") end
        local receiver = I:raw(args[1])
        local valid = (tname == "int" or tname == "bool") and values.is_int_kind(receiver)
          or type_name(receiver) == tname
        if not valid then throw("TypeError", "descriptor '" .. method .. "' requires a '" .. tname .. "' object") end
        return values.hash_value(receiver)
      end, "wrapper_descriptor")
    end
  end
  if tname == "dict" and method == "__init__" then return values.builtin(method, function(I, args, kw)
    if #args == 0 then throw("TypeError", "descriptor '__init__' of 'dict' object needs an argument") end
    local receiver = I:raw(args[1])
    if kindof(receiver) ~= "dict" then
      throw("TypeError", "descriptor '__init__' requires a 'dict' object but received a '" .. type_name(args[1]) .. "'")
    end
    local forwarded = {}
    for index = 2, #args do forwarded[index - 1] = args[index] end
    return I:call(methods.builtin_method(I, receiver, "update"), forwarded, kw)
  end) end
  if tname == "GenericAlias" or tname == "UnionType" then
    if method == "__getitem__" then return values.builtin(method, function(I, args)
      return typeexpr.getitem(I, args[1], args[2])
    end) end
    if method == "__iter__" and tname == "GenericAlias" then return values.builtin(method, function(I, args)
      return typeexpr.iter(I, args[1])
    end) end
    if method == "__eq__" or method == "__ne__" then return values.builtin(method, function(I, args)
      local equal = typeexpr.equal(I, args[1], args[2], ops)
      if equal == nil then return values.NOT_IMPLEMENTED end
      if method == "__ne__" then return not equal end
      return equal
    end) end
    if method == "__hash__" then return values.builtin(method, function(_, args)
      return typeexpr.hash(args[1])
    end) end
  end
  local primitive_type = self.types[tname] or self.internal_types[tname]
  if method == "__new__" and primitive_type ~= nil then
    local constructor = values.builtin(method, function(I, args, kw)
      local cls = args[1]
      local rest = {}
      for i = 2, #args do rest[#rest + 1] = args[i] end
      local prim = primitive_type.ctor(I, rest, kw)
      if cls == primitive_type then return prim end
      if kindof(cls) ~= "class" or cls.prim ~= primitive_type then
        throw("TypeError", tname .. ".__new__ requires a subtype")
      end
      local result = values.new_instance(cls)
      result.prim = prim
      return result
    end)
    constructor.bound_self = primitive_type
    constructor.primitive_new = primitive_type
    return constructor
  end
  local dunder = {
    __repr__ = function(_I, a) return ops.repr(self, self:raw(a[1])) end,
    __str__ = function(_I, a) return self:str(self:raw(a[1])) end,
    __getitem__ = function(_I, a) return ops.get_index(self, self:raw(a[1]), a[2]) end,
    __setitem__ = function(_I, a) ops.set_index(self, self:raw(a[1]), a[2], a[3]); return NONE end,
    __delitem__ = function(_I, a) ops.del_index(self, self:raw(a[1]), a[2]); return NONE end,
    __len__ = function(_I, a) return ops.diaspore_len(self, self:raw(a[1])) end,
    __contains__ = function(_I, a) return ops.contains(self, self:raw(a[1]), a[2]) end,
    __iter__ = function(_I, a) return iter.get_iter(self, self:raw(a[1])) end,
  }
  local protocol = PRIM_PROTOCOLS[tname]
  local supported = method == "__repr__" and (protocol ~= nil or tname == "int"
      or tname == "bool" or tname == "float" or tname == "complex"
      or tname == "GenericAlias" or tname == "UnionType")
    or method == "__str__" and (tname == "str" or tname == "bytes" or tname == "bytearray")
    or protocol ~= nil and (method == "__len__" or method == "__iter__"
      or method == "__contains__" and protocol ~= "view"
      or method == "__getitem__" and protocol ~= "set"
      or (method == "__setitem__" or method == "__delitem__") and (protocol == "write" or protocol == "view"))
  if supported and dunder[method] then return values.builtin(method, dunder[method]) end
  if PRIM_METHOD_SETS == nil then PRIM_METHOD_SETS = methods.METHOD_SETS end
  local allowed = PRIM_METHOD_SETS[tname == "bool" and "int" or tname]
  if allowed and allowed[method] then
    return values.builtin(method, function(I2, a, kw)
      local self_ = a[1]
      if (tname == "int" or tname == "bool") and not values.is_int_kind(self:raw(self_)) then
        throw("TypeError", "descriptor '" .. method .. "' requires an 'int' object")
      end
      local rest = {}
      for i = 2, #a do rest[i - 1] = a[i] end
      local bound = methods.builtin_method(self, self:raw(self_), method)
      return bound.fn(I2, rest, kw)
    end, "method_descriptor")
  end
  return nil
end

-- Native descriptors participate at their actual position in the class MRO.
-- Their implementations are shared with direct builtin-type attribute access.
function Interp:lookup_type_attr(cls, name)
  for _, base in ipairs(cls.mro or { cls, values.OBJECT }) do
    local value = base.ns[name]
    if value ~= nil then return value end
    if kindof(base) == "btype" then
      value = self:builtin_unbound(base.name, name)
      if value ~= nil and value ~= values.OBJECT.ns[name] then return value end
    end
  end
end

function Interp:type_field(cls, name)
  local kind = kindof(cls)
  if kind ~= "class" and kind ~= "btype" then
    throw("TypeError", "descriptor '" .. name .. "' requires a 'type' object")
  end
  if name == "__dict__" then
    if kind == "btype" then self:populate_builtin_namespace(cls) end
    return { kind = "mappingproxy", cls = cls }
  end
  if name == "__mro__" then
    if cls.mro_value == nil then
      local mro = cls.mro
      if mro == nil then
        mro = cls == self.types.bool and { cls, self.types.int, values.OBJECT } or { cls, values.OBJECT }
      end
      cls.mro_value = values.tuple(mro)
    end
    return cls.mro_value
  end
  if cls.bases_value == nil then
    local bases = cls.declared_bases
    if bases == nil or #bases == 0 and cls ~= values.OBJECT then
      bases = { cls == self.types.bool and self.types.int or values.OBJECT }
    end
    cls.bases_value = values.tuple(bases)
  end
  return cls.bases_value
end

function Interp:call_dunder(obj, name, args)
  return self:call_dunder_kw(obj, name, args, {})
end

function Interp:call_dunder_kw(obj, name, args, kwargs)
  local fn = class_lookup(obj.cls, name)
  if fn == nil then
    throw("AttributeError", "'" .. obj.cls.name .. "' object has no attribute '"
      .. name .. "'")
  end
  return self:call(self:bind_attr(fn, obj, obj.cls), args, kwargs)
end

function Interp:call_dunder_or_err(obj, name, args, errmsg)
  if class_lookup(obj.cls, name) == nil then
    throw("TypeError", errmsg)
  end
  return self:call_dunder(obj, name, args)
end

function Interp:make_super(args, fr)
  if #args == 0 then
    if fr.defclass == nil or fr.self_ == nil then
      throw("RuntimeError", "super(): __class__ cell not found")
    end
    return { kind = "superobj", cls = fr.defclass, obj = fr.self_ }
  end
  if #args == 2 and kindof(args[1]) == "class" then
    return { kind = "superobj", cls = args[1], obj = args[2] }
  end
  throw("TypeError", "super() argument error")
end

-- ------------------------------------------------------------------- attributes

function Interp:bind_attr(v, inst, cls, class_access)
  local k = kindof(v)
  if k == "function" then
    if class_access then return v end
    return { kind = "boundmethod", fn = v, recv = inst }
  end
  if k == "staticmethod" then return v.fn end
  if k == "classmethod" then return { kind = "boundmethod", fn = v.fn, recv = cls } end
  if k == "builtin" then
    if class_access or v.bound_self ~= nil then return v end
    return { kind = "boundmethod", fn = v, recv = inst }
  end
  if k == "property" then
    if class_access then return v end
    if v.fget == nil then throw("AttributeError", "property has no getter") end
    return self:call(v.fget, { inst }, {})
  end
  if k == "descriptor" and v.type_field then
    if class_access then return v end
    return self:type_field(inst, v.name)
  end
  if k == "descriptor" and v.storage then
    if class_access then return v end
    return self:storage_descriptor(v, inst, "get")
  end
  if k == "instance" and class_lookup(v.cls, "__get__") ~= nil then
    return self:call_dunder(v, "__get__", { inst, cls })
  end
  return v
end

function Interp:get_attr(o, n)
  if kindof(o) == "instance" then
    local hook = class_lookup(o.cls, "__getattribute__")
    if hook and hook ~= values.OBJECT.ns.__getattribute__ then
      return self:call(self:bind_attr(hook, o, o.cls), { n }, {})
    end
  end
  return self:default_get_attr(o, n)
end

function Interp:default_get_attr(o, n)
  local k = kindof(o)
  local primitive_methods = methods.METHOD_SETS[k]
  if k == nil and type(o) == "string" then primitive_methods = methods.METHOD_SETS.str end
  if primitive_methods and primitive_methods[n] then
    return methods.builtin_method(self, o, n)
  end
  if n == "__class__" and k ~= "instance" then return self.types.type.ctor(self, { o }, {}) end
  if k == "GenericAlias" or k == "UnionType" then
    local result = typeexpr.attr(self, o, n)
    if result ~= nil then return result end
  end
  if k == "descriptor" then
    if n == "__name__" then return o.name end
    if n == "__objclass__" then return o.owner end
    if n == "__get__" then return values.builtin(n, function(I, args)
      if args[1] == NONE then return o end
      if o.type_field then return I:type_field(args[1], o.name) end
      if o.storage then return I:storage_descriptor(o, args[1], "get") end
      return I:get_attr(args[1], o.name)
    end) end
    if o.storage and (n == "__set__" or n == "__delete__") then return values.builtin(n, function(I, args)
      return I:storage_descriptor(o, args[1], n == "__set__" and "set" or "delete", args[2])
    end) end
  end
  if k == "code" or k == "cell" or k == "frame" or k == "traceback" then
    local value = metadata.attr(self, o, n)
    if value ~= nil then return value end
    throw("AttributeError", "'" .. k .. "' object has no attribute '" .. n .. "'")
  end
  if k == "instance" then
    if n == "__dict__" and o.cls.has_dict == nil and o.cls ~= values.OBJECT then
      return o.dict_value or self:live_dict(o.d)
    end
    local cv = self:lookup_type_attr(o.cls, n)
    if kindof(cv) == "descriptor" and cv.storage then return self:storage_descriptor(cv, o, "get") end
    if kindof(cv) == "instance" and class_lookup(cv.cls, "__get__") ~= nil
        and (class_lookup(cv.cls, "__set__") ~= nil
          or class_lookup(cv.cls, "__delete__") ~= nil) then
      return self:call_dunder(cv, "__get__", { o, o.cls })
    end
    if kindof(cv) ~= "property" then
      local dv = o.d[n]
      if dv ~= nil then return dv end
    end
    if (n == "exceptions" or n == "message")
        and values.issub(o.cls, values.EXC_CLASSES.BaseExceptionGroup) then
      local gv = o.d[n]
      if gv ~= nil then return gv end
    end
    if kindof(cv) == "property" then
      if cv.fget == nil then
        throw("AttributeError", "property '" .. n .. "' of '"
          .. o.cls.name .. "' object has no getter")
      end
      return self:call(cv.fget, { o }, {})
    end
    if cv ~= nil then return self:bind_attr(cv, o, o.cls) end
    if n == "__class__" then return o.cls end
    if o.prim ~= nil then
      if kindof(o.prim) == "property" or kindof(o.prim) == "staticmethod" or kindof(o.prim) == "classmethod" then
        return self:get_attr(o.prim, n)
      end
      if kindof(o.prim) == "GenericAlias" then
        local value = typeexpr.attr(self, o.prim, n)
        if value ~= nil then return value end
      end
      local allowed = methods.METHOD_SETS[type_name(o.prim)]
      if allowed and allowed[n] then
        return methods.builtin_method(self, o.prim, n)
      end
    end
    local ga = class_lookup(o.cls, "__getattr__")
    if ga ~= nil then
      return self:call(self:bind_attr(ga, o, o.cls), { n }, {})
    end
    throw("AttributeError", "'" .. o.cls.name .. "' object has no attribute '" .. n .. "'")
  end
  if k == "btype" then
    if n == "__name__" then return o.name end
    if n == "__dict__" or n == "__bases__" or n == "__mro__" then return self:type_field(o, n) end
    if o.ns and o.ns[n] ~= nil then
      local value = o.ns[n]
      if kindof(value) == "builtin" and value.descriptor == "classmethod_descriptor" then
        return { kind = "boundmethod", fn = value, recv = o }
      end
      return self:bind_attr(value, NONE, o, true)
    end
    if n == "__module__" then return o.module or "builtins" end
    if n == "__qualname__" then return o.name end
    local unbound = self:builtin_unbound(o.name, n)
    if unbound ~= nil then return self:bind_attr(unbound, NONE, o, true) end
    throw("AttributeError", "type object '" .. o.name .. "' has no attribute '" .. n .. "'")
  end
  if k == "class" then
    if n == "__subclasses__" then return values.builtin(n, function()
      local result = {}
      for cls in pairs(self.classes) do
        local bases = cls.declared_bases
        if #bases == 0 and o == values.OBJECT then result[#result + 1] = cls
        else for _, base in ipairs(bases) do if base == o then result[#result + 1] = cls; break end end end
      end
      return values.list(result)
    end) end
    if n == "__class__" then return o.metaclass or self.types.type end
    local meta = o.metaclass or self.types.type
    local mv = self:lookup_type_attr(meta, n)
    if mv ~= nil then
      if kindof(mv) == "descriptor" and mv.type_field then return self:type_field(o, n) end
      if kindof(mv) == "property" and mv.fget then return self:call(mv.fget, { o }, {}) end
      if kindof(mv) == "instance" and class_lookup(mv.cls, "__get__") and class_lookup(mv.cls, "__set__") then
        return self:call_dunder(mv, "__get__", { o, meta })
      end
    end
    if n == "__bases__" or n == "__dict__" then return self:type_field(o, n) end
    local v = self:lookup_type_attr(o, n)
    if v == nil then
      if n == "__name__" or n == "__qualname__" then return o.name end
      if n == "__mro__" then return self:type_field(o, n) end
      if mv ~= nil then return self:bind_attr(mv, o, meta) end
      if o.prim ~= nil then
        local inherited = self:builtin_unbound(o.prim.name, n)
        if inherited then return inherited end
      end
      throw("AttributeError", "type object '" .. o.name .. "' has no attribute '" .. n .. "'")
    end
    local vk = kindof(v)
    if vk == "classmethod" then return { kind = "boundmethod", fn = v.fn, recv = o } end
    if vk == "staticmethod" then return v.fn end
    if vk == "instance" and class_lookup(v.cls, "__get__") ~= nil then
      return self:call_dunder(v, "__get__", { NONE, o })
    end
    return v
  end
  if k == "staticmethod" or k == "classmethod" then
    if n == "__func__" or n == "__wrapped__" then return o.fn end
    if n == "__isabstractmethod__" then return kindof(o.fn) == "function" and o.fn.attrs ~= nil and o.fn.attrs[n] or false end
    if n == "__get__" then return values.builtin(n, function(_, args)
      if k == "staticmethod" then return o.fn end
      local cls = args[2]
      if cls == nil or cls == NONE then cls = self.types.type.ctor(self, { args[1] }, {}) end
      return { kind = "boundmethod", fn = o.fn, recv = cls }
    end) end
  end
  if k == "property" then
    if n == "__isabstractmethod__" then
      for _, fn in ipairs({ o.fget or NONE, o.fset or NONE, o.fdel or NONE }) do
        if kindof(fn) == "function" and fn.attrs and self:truth(fn.attrs.__isabstractmethod__ or false) then return true end
      end
      return false
    end
    local method = self.types.property.ns[n]
    if method ~= nil then return self:bind_attr(method, o, self.types.property) end
    if n == "__doc__" then return o.doc == nil and NONE or o.doc end
    if n == "fget" then return o.fget or NONE end
    if n == "fset" then return o.fset or NONE end
    if n == "fdel" then return o.fdel or NONE end
    throw("AttributeError", "'property' object has no attribute '" .. n .. "'")
  end
  if k == "function" then
    if n == "__get__" then return values.builtin(n, function(_, args)
      if args[1] == NONE then return o end
      return { kind = "boundmethod", fn = o, recv = args[1] }
    end) end
    if n == "__code__" or n == "__closure__" then
      metadata.function_info(self, o)
      return n == "__code__" and o.code or o.cells
    end
    if n == "__globals__" then return o.globals_mapping or self:live_dict(o.closure.globals) end
    if n == "__defaults__" then
      if o.defaults_value ~= nil then return o.defaults_value end
      return #o.defaults > 0 and values.tuple(o.defaults) or NONE
    end
    if n == "__kwdefaults__" then
      if o.kwdefaults_dict ~= nil then return o.kwdefaults_dict end
      if next(o.kwdefaults) == nil then return NONE end
      o.kwdefaults_dict = self:frame_dict(o.kwdefaults)
      o.kwdefaults = self:dictionary_namespace(o.kwdefaults_dict)
      return o.kwdefaults_dict
    end
    if n == "__module__" and (o.attrs == nil or o.attrs[n] == nil) then
      if o.module_value ~= nil then return o.module_value end
      return o.closure.globals.__name__ or o.module_name
    end
    if o.attrs ~= nil and o.attrs[n] ~= nil then return o.attrs[n] end
    if n == "__name__" then return o.name_value or o.name end
    if n == "__qualname__" then return o.qualname or o.name end
    if n == "__annotations__" then
      o.attrs = o.attrs or {}
      o.attrs[n] = values.new_dict()
      return o.attrs[n]
    end
    if n == "__doc__" then return o.doc == nil and NONE or o.doc end
    if n == "__dict__" then
      o.attrs = o.attrs or {}
      local d = values.new_dict()
      for key, val in pairs(o.attrs) do values.dict_set(d, key, val) end
      return d
    end
    throw("AttributeError", "'function' object has no attribute '" .. n .. "'")
  end
  if k == "builtin" then
    if n == "__name__" then return o.name end
    if n == "__qualname__" then
      return o.bound_self and o.bound_self.name .. "." .. o.name or o.name
    end
    if n == "__self__" and o.bound_self ~= nil then return o.bound_self end
  end
  if k == "boundmethod" then
    if n == "__name__" then return ops.callable_name(o.fn) end
    if n == "__self__" then return o.recv end
    if n == "__func__" then return o.fn end
    return self:get_attr(o.fn, n)
  end
  if k == "range" and (n == "start" or n == "stop" or n == "step") then return o[n] end
  if k == "slice" then
    if n == "start" then return o.lower end
    if n == "stop" then return o.upper end
    if n == "step" then return o.step end
    if n == "indices" then
      return values.builtin("indices", function(_I, a)
        local len = values.as_index(a[1])
        if len == nil then throw("TypeError", "slice indices must be integers") end
        local start, stop, step = ops.slice_indices(o, len)
        return values.tuple({ start, stop, step })
      end)
    end
    throw("AttributeError", "'slice' object has no attribute '" .. n .. "'")
  end
  if k == "complex" then
    if n == "real" then return o.re end
    if n == "imag" then return o.im end
    if n == "conjugate" then
      return values.builtin("conjugate", function() return values.complex(o.re, -o.im) end)
    end
    throw("AttributeError", "'complex' object has no attribute '" .. n .. "'")
  end
  if values.is_int_kind(o) then
    if n == "real" or n == "numerator" then return values.as_index(o) end
    if n == "imag" then return 0 end
    if n == "denominator" then return 1 end
  elseif type(o) == "number" then
    if n == "real" then return o end
    if n == "imag" then return 0.0 end
    if n == "conjugate" then
      return values.builtin("conjugate", function() return o end)
    end
    if n == "is_integer" and math.type(o) == "float" then
      return values.builtin("is_integer", function()
        return o == o and o ~= math.huge and o ~= -math.huge and math.floor(o) == o
      end)
    end
  end
  if k == "superobj" then
    local mro, owner, receiver, class_access = nil, nil, o.obj, false
    if kindof(o.obj) == "instance" then
      owner = o.obj.cls
    elseif not values.issub(o.obj, o.cls) then
      owner = o.obj.metaclass or self.types.type
    else
      owner, receiver, class_access = o.obj, NONE, true
    end
    mro = owner.mro or { owner, values.OBJECT }
    local start = 1
    for i = 1, #mro do
      if mro[i] == o.cls then
        start = i + 1
        break
      end
    end
    for i = start, #mro do
      local v = mro[i].ns[n]
      if v == nil and kindof(mro[i]) == "btype" then
        v = self:builtin_unbound(mro[i].name, n)
        if v == values.OBJECT.ns[n] then v = nil end
      end
      if v ~= nil then
        if n == "__new__" then return kindof(v) == "staticmethod" and v.fn or v end
        return self:bind_attr(v, receiver, owner, class_access)
      end
    end
    throw("AttributeError", "'super' object has no attribute '" .. n .. "'")
  end
  if k == "module" then
    if n == "__dict__" then return self:live_dict(o.frame.vars) end
    local v = o.frame.vars[n]
    if v ~= nil then return v end
    if o.frame.vars.__getattr__ ~= nil then return self:call(o.frame.vars.__getattr__, { n }, {}) end
    throw("AttributeError", "module '" .. o.name .. "' has no attribute '" .. n .. "'")
  end
  if k == "nativemodule" then
    if n == "__dict__" then return self:live_dict(o.ns) end
    local v = o.ns[n]
    if v ~= nil then return v end
    throw("AttributeError", "module '" .. o.name .. "' has no attribute '" .. n .. "'")
  end
  if k == "file" then
    return natives.file_method(self, o, n)
  end
  if k == "nativeobject" then
    if n:sub(1, 1) ~= "." and n:sub(1, 2) ~= "__" then
      local pv = o.props[n]
      if pv ~= nil then return pv end
      local mv = o.methods[n]
      if mv ~= nil then return mv end
    end
    throw("AttributeError", "'" .. o.nkind .. "' object has no attribute '" .. n .. "'")
  end
  if k == "iter" and o.advance ~= nil then
    local m = iter.generator_method(self, o, n)
    if m ~= nil then return m end
    throw("AttributeError", "'generator' object has no attribute '" .. n .. "'")
  end
  if n:sub(1, 2) == "__" and self.types[type_name(o)] ~= nil then
    local method = self:builtin_unbound(type_name(o), n)
    if method ~= nil then return self:bind_attr(method, o, self.types[type_name(o)]) end
  end
  return methods.builtin_method(self, o, n)
end

function Interp:set_attr(o, n, v)
  local k = kindof(o)
  if (k == "module" or k == "nativemodule") and n == "__dict__" then
    throw("AttributeError", "readonly attribute")
  end
  if k == "nativemodule" then o.ns[n] = v; return end
  if k == "cell" and n == "cell_contents" then o.frame.vars[o.name] = v; return end
  if k == "property" and n == "__doc__" then o.doc = v; return end
  if k == "function" then
    if n == "__doc__" then o.doc = v; return end
    if n == "__defaults__" then
      if v ~= NONE and kindof(v) ~= "tuple" then throw("TypeError", "__defaults__ must be set to a tuple object") end
      o.defaults = v == NONE and {} or v.items
      o.defaults_value = v
      return
    end
    if n == "__kwdefaults__" then
      if v ~= NONE and kindof(v) ~= "dict" then throw("TypeError", "__kwdefaults__ must be set to a dict object") end
      o.kwdefaults_dict = v ~= NONE and v or nil
      o.kwdefaults = v == NONE and {} or self:dictionary_namespace(v)
      return
    end
    if n == "__globals__" or n == "__closure__" then throw("AttributeError", "readonly attribute") end
    if n == "__code__" then
      metadata.function_info(self, o)
      local replacement = self:function_from_code(v, o.closure.globals, o.name, values.tuple(o.defaults), o.cells)
      o.node, o.meta, o.code, o.closure, o.cells, o.module_name =
        replacement.node, replacement.meta, replacement.code, replacement.closure, replacement.cells, replacement.module_name
      o.unoptimized = replacement.unoptimized
      o.free_frame = nil
      return
    end
    if (n == "__name__" or n == "__qualname__") and type(v) ~= "string" then
      throw("TypeError", n .. " must be set to a string object")
    end
    if n == "__name__" then
      o.name = v
      o.name_value = nil
    else
      o.attrs = o.attrs or {}
      o.attrs[n] = v
    end
    return
  end
  if k == "instance" then
    local hook = class_lookup(o.cls, "__setattr__")
    if hook ~= nil and hook ~= values.OBJECT.ns.__setattr__ then
      self:call(self:bind_attr(hook, o, o.cls), { n, v }, {})
    else
      self:object_set_attr(o, n, v)
    end
    return
  end
  if k == "class" then
    if n == "__dict__" then throw("AttributeError", "attribute '__dict__' of 'type' objects is not writable") end
    if o.ns[n] == nil and o.ns_order ~= nil then o.ns_order[#o.ns_order + 1] = n end
    o.ns[n] = v
    return
  end
  if k == "module" then
    o.frame.vars[n] = v
    return
  end
  throw("AttributeError", "'" .. type_name(o) .. "' object has no attribute '" .. n .. "'")
end

local EXCEPTION_METADATA = {
  __cause__ = true, __context__ = true, __traceback__ = true, __suppress_context__ = true,
}

function Interp:object_set_attr(o, n, v)
  if kindof(o) ~= "instance" then
    throw("TypeError", "can't apply this __setattr__ to " .. type_name(o) .. " object")
  end
  local descriptor = class_lookup(o.cls, n)
  if kindof(descriptor) == "descriptor" and descriptor.storage then
    self:storage_descriptor(descriptor, o, "set", v)
    return
  end
  if kindof(descriptor) == "property" then
    if descriptor.fset == nil then
      throw("AttributeError", "property '" .. n .. "' of '"
        .. o.cls.name .. "' object has no setter")
    end
    self:call(descriptor.fset, { o, v }, {})
    return
  end
  if kindof(descriptor) == "instance"
      and (class_lookup(descriptor.cls, "__set__") ~= nil
        or class_lookup(descriptor.cls, "__delete__") ~= nil) then
    self:call_dunder(descriptor, "__set__", { o, v })
    return
  end
  if o.cls == values.OBJECT or o.cls.has_dict == false then
    throw("AttributeError", "'" .. o.cls.name .. "' object has no attribute '" .. n .. "'")
  end
  if EXCEPTION_METADATA[n] and values.issub(o.cls, values.BASE_EXC) then
    if n == "__cause__" or n == "__context__" then
      if v ~= NONE and not (kindof(v) == "instance" and values.issub(v.cls, values.BASE_EXC)) then
        throw("TypeError", "exception " .. (n == "__cause__" and "cause" or "context") .. " must be None or derive from BaseException")
      end
      if n == "__cause__" then o.d.__suppress_context__ = true end
    elseif n == "__traceback__" then
      if v ~= NONE and kindof(v) ~= "traceback" then throw("TypeError", "__traceback__ must be a traceback or None") end
    elseif type(v) ~= "boolean" then
      throw("TypeError", "attribute value type must be bool")
    end
  end
  o.d[n] = v
end

function Interp:object_del_attr(o, n)
  if kindof(o) ~= "instance" then
    throw("TypeError", "can't apply this __delattr__ to " .. type_name(o) .. " object")
  end
  local descriptor = class_lookup(o.cls, n)
  if kindof(descriptor) == "descriptor" and descriptor.storage then
    self:storage_descriptor(descriptor, o, "delete")
    return
  end
  if kindof(descriptor) == "property" then
    if descriptor.fdel == nil then
      throw("AttributeError", "property '" .. n .. "' of '"
        .. o.cls.name .. "' object has no deleter")
    end
    self:call(descriptor.fdel, { o }, {})
    return
  end
  if kindof(descriptor) == "instance"
      and (class_lookup(descriptor.cls, "__set__") ~= nil
        or class_lookup(descriptor.cls, "__delete__") ~= nil) then
    self:call_dunder(descriptor, "__delete__", { o })
    return
  end
  if EXCEPTION_METADATA[n] and values.issub(o.cls, values.BASE_EXC) then
    if n == "__suppress_context__" then throw("TypeError", "can't delete numeric/char attribute") end
    throw("TypeError", n .. " may not be deleted")
  end
  if o.d[n] == nil then
    throw("AttributeError", "'" .. o.cls.name .. "' object has no attribute '" .. n .. "'")
  end
  o.d[n] = nil
end

function Interp:del_attr(o, n)
  local k = kindof(o)
  if (k == "module" or k == "nativemodule") and n == "__dict__" then
    throw("AttributeError", "readonly attribute")
  end
  if k == "instance" then
    local hook = class_lookup(o.cls, "__delattr__")
    if hook ~= nil and hook ~= values.OBJECT.ns.__delattr__ then
      self:call(self:bind_attr(hook, o, o.cls), { n }, {})
    else
      self:object_del_attr(o, n)
    end
    return
  end
  local attrs
  if k == "class" then attrs = o.ns
  elseif k == "module" then attrs = o.frame.vars
  elseif k == "nativemodule" then attrs = o.ns
  elseif k == "function" then attrs = o.attrs
  end
  if attrs ~= nil and attrs[n] ~= nil then
    attrs[n] = nil
    if k == "class" and o.ns_order ~= nil then
      for i = #o.ns_order, 1, -1 do
        if o.ns_order[i] == n then table.remove(o.ns_order, i) end
      end
    end
    return
  end
  if k == "class" then
    throw("AttributeError", "type object '" .. o.name .. "' has no attribute '" .. n .. "'")
  end
  throw("AttributeError", "'" .. type_name(o) .. "' object has no attribute '" .. n .. "'")
end

-- ------------------------------------------------------------------- reflection

-- The diaspore frame from which the currently-running builtin was called (the
-- innermost function/module frame on the call stack).
function Interp:caller_frame()
  local top = self.stack[#self.stack]
  return top and top.fr or nil
end

-- Optimized function locals are exposed as snapshots.
function Interp:frame_dict(vars)
  local d = values.new_dict()
  if vars ~= nil then
    for key, val in pairs(vars) do
      if type(key) == "string" then values.dict_set(d, key, val) end
    end
  end
  return d
end

-- Namespace adapters preserve writes and function-global references across exec.
local function namespace_table(mapping)
  return setmetatable({}, {
    __mapping = mapping,
    __index = function(_, name)
      local entry = values.dict_entry(mapping, name)
      if entry then return entry.v end
    end,
    __newindex = function(_, name, value)
      if value == nil then values.dict_del(mapping, name)
      else values.dict_set(mapping, name, value) end
    end,
    __pairs = function()
      local entries, index = values.dict_entries(mapping), 0
      return function()
        index = index + 1
        local entry = entries[index]
        if entry then return entry.k, entry.v end
      end
    end,
  })
end

function Interp:live_dict(vars)
  if vars == nil then return values.new_dict() end
  local mt = getmetatable(vars)
  if mt ~= nil and mt.__mapping ~= nil then return mt.__mapping end
  local mapping = self:frame_dict(vars)
  for key in pairs(vars) do rawset(vars, key, nil) end
  setmetatable(vars, getmetatable(namespace_table(mapping)))
  return mapping
end

function Interp:dictionary_namespace(mapping)
  return namespace_table(mapping)
end

function Interp:function_from_code(code, globals, name, defaults, cells)
  if kindof(code) ~= "code" then throw("TypeError", "function code must be a code object") end
  local rawdefaults, rawcells = self:raw(defaults), self:raw(cells)
  if defaults ~= NONE and kindof(rawdefaults) ~= "tuple" then throw("TypeError", "argdefs must be a tuple") end
  if cells ~= NONE and kindof(rawcells) ~= "tuple" then throw("TypeError", "closure must be a tuple") end
  local freevars = code.fields.co_freevars.items
  local cellitems = cells == NONE and EMPTY or rawcells.items
  if #freevars ~= #cellitems then throw("ValueError", "code and closure have different numbers of free variables") end
  local aliases = {}
  for i, freevar in ipairs(freevars) do
    if kindof(cellitems[i]) ~= "cell" then throw("TypeError", "closure contains a non-cell value") end
    aliases[freevar] = cellitems[i]
  end
  local variables = setmetatable({}, {
    __index = function(_, key)
      local cell = aliases[key]
      if cell then return cell.frame.vars[cell.name] end
      return globals[key]
    end,
    __newindex = function(_, key, value)
      local cell = aliases[key]
      if cell then cell.frame.vars[cell.name] = value else globals[key] = value end
    end,
  })
  local original = code.fn
  if original == nil then
    original = {
      node = { name = code.fields.co_name, body = code.nodes, ln = code.fields.co_firstlineno },
      module_name = code.module_name,
      meta = { name = code.fields.co_name, args = EMPTY, kwonly = EMPTY, posonly = 0,
        gdecl = EMPTY, ndecl = EMPTY, plain_positional = true },
    }
  end
  local closure = new_frame(variables, nil, to_set(freevars), EMPTY, EMPTY,
    false, globals, "<closure>", original.module_name)
  local module_value = globals.__name__
  if module_value == nil then module_value = NONE end
  return {
    kind = "function", node = original.node, name = name or original.meta.name,
    qualname = code.fields.co_qualname or code.fields.co_name, module_value = module_value,
    meta = original.meta, module_name = original.module_name, closure = closure,
    defaults = defaults == NONE and {} or rawdefaults.items, defaults_value = defaults,
    kwdefaults = {}, code = code, cells = cells, doc = function_doc(original.node),
    unoptimized = code.fn == nil,
  }
end

function Interp:compile_snippet(source, filename, mode)
  if kindof(source) == "code" and source.nodes then return source end
  if buffer.is_buffer(source) then source = require("diaspore.helpers").decode_bytes(values.bytes(buffer.data(source)), "utf-8") end
  if type(source) ~= "string" then throw("TypeError", "compile() arg 1 must be a string, bytes or code object") end
  if type(filename) ~= "string" then throw("TypeError", "compile() filename must be a string") end
  if mode ~= "exec" and mode ~= "eval" and mode ~= "single" then throw("ValueError", "compile() mode must be 'exec', 'eval' or 'single'") end
  local bundle = require("diaspore.compiler").compile(self, source, filename, mode)
  local module = bundle.modules[bundle.main]
  self.snippet_count = (self.snippet_count or 0) + 1
  local name = ".snippet." .. self.snippet_count
  for key, value in pairs(bundle.modules) do
    if key ~= bundle.main and self.bundle.modules[key] == nil then self.bundle.modules[key] = value end
  end
  module.file = filename
  self.bundle.modules[name] = module
  return { kind = "code", nodes = module.body, mode = mode, module_name = name,
    fields = { co_name = "<module>", co_filename = filename, co_firstlineno = 1,
      co_argcount = 0, co_posonlyargcount = 0, co_kwonlyargcount = 0,
      co_varnames = values.tuple({}), co_freevars = values.tuple({}), co_nlocals = 0, co_flags = 0 } }
end

function Interp:exec_snippet(code, glob, loc, mode)
  if type(code) == "string" or buffer.is_buffer(code) then code = self:compile_snippet(code, "<string>", mode) end
  if kindof(code) ~= "code" or code.nodes == nil then throw("TypeError", "exec/eval arg must be a string, bytes or compiled code") end
  local caller = self:caller_frame()
  local globals, locals
  if glob == nil or glob == NONE then
    globals = caller and caller.globals or {}
    if loc == nil or loc == NONE then locals = caller and caller.vars or globals end
  else
    if kindof(glob) ~= "dict" then throw("TypeError", "globals must be a dict") end
    globals = namespace_table(glob)
    if loc == nil or loc == NONE or loc == glob then locals = globals end
  end
  if locals == nil then
    if kindof(loc) ~= "dict" then throw("TypeError", "locals must be a mapping") end
    locals = namespace_table(loc)
  end
  if globals.__builtins__ == nil then globals.__builtins__ = self.natives.builtins end
  local fr = new_frame(locals, nil, nil, EMPTY, EMPTY, false, globals, "<module>", code.module_name)
  fr.is_exec = true
  local sn = #self.stack + 1
  self.stack[sn] = { fr = fr, depth0 = self.depth, previous = self.stack[sn - 1] }
  local result = NONE
  if code.mode == "eval" then result = self:ev(code.nodes[1].v, fr)
  else self:exec_block(code.nodes, fr) end
  self.stack[sn] = nil
  return result
end

-- ------------------------------------------------------------------- protocols

function Interp:truth(v)
  return ops.truth(self, v)
end

function Interp:str(v)
  return ops.str(self, v)
end

function Interp:repr(v)
  return ops.repr(self, v)
end

function Interp:close_file(fo)
  if not fo.closed then
    fo.f:close()
    fo.closed = true
  end
end

-- ------------------------------------------------------------------- capabilities

function Interp:require_cap(cap)
  if not self.caps[cap] then
    throw("PermissionError",
      "diaspore: capability '" .. cap .. "' not granted (run with --allow-" .. cap .. ")")
  end
end

function Interp:require_net(kind, host, port)
  self:require_cap("net")
  local pol = self.policy.net
  if pol == nil then return end
  for _, p in ipairs(pol[kind] or EMPTY) do
    local idx = nil
    for i = #p, 1, -1 do
      if p:sub(i, i) == ":" then
        idx = i
        break
      end
    end
    if idx ~= nil and idx > 1 then
      local ph = p:sub(1, idx - 1)
      local pp = p:sub(idx + 1)
      local host_ok = ph == "*" or ph:lower() == host:lower()
      local port_ok = pp == "*" or pp == tostring(port)
      if host_ok and port_ok then return end
    end
  end
  throw("PermissionError",
    "diaspore: net." .. kind .. " " .. host .. ":" .. port .. " not permitted by policy")
end

function Interp:require_fs(kind, path)
  self:require_cap("fs")
  local real = normalize_path(path)
  local pol = self.policy.fs
  if pol == nil then return real end
  for _, root in ipairs(pol[kind] or EMPTY) do
    local rroot = normalize_path(root)
    if real == rroot or real:sub(1, #rroot + 1) == rroot .. "/" then
      return real
    end
  end
  throw("PermissionError",
    "diaspore: fs." .. kind .. " " .. path .. " not permitted by policy")
end

return M
