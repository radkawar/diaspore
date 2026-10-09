-- The Diaspore interpreter: frames, execution, calls, attributes, modules,
-- capabilities, fuel. Mirrors src/diaspore/vm.py (reference) and runtimes/ts/src/interp.ts.
--
-- Control flow: `break`/`continue`/`return` are signals returned up the
-- statement chain ({tag=...}); diaspore exceptions and traps are Lua errors
-- (tagged tables) caught with pcall only at try/with/iterator boundaries.
-- This keeps the hot call path free of pcall, whose nesting is limited by
-- the C stack in Lua 5.4 (deep diaspore recursion must not consume it).

local values = require("diaspore.values")
local format = require("diaspore.format")
local ops = require("diaspore.ops")
local iter = require("diaspore.iter")
local methods = require("diaspore.methods")
local builtins = require("diaspore.builtins")
local natives = require("diaspore.natives")

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
  I.argv = opts.argv and #opts.argv > 0 and opts.argv or { bundle.main }
  I.caps = opts.caps or {}
  I.policy = opts.policy or {}
  I.deterministic = opts.deterministic or false
  I.max_steps = opts.max_steps or 0
  I.max_depth = opts.max_depth or 1000
  I.stdout = opts.stdout
  I.stderr = opts.stderr
  I.steps = 0
  I.depth = 0
  I.modules = {}
  I.exc_stack = {}
  I.repring = {}
  I.stack = {} -- call stack: {module=, fname=, fr=, depth0=}
  I.natives_open = natives.bi_open
  I.builtins = builtins.make_builtins(I)
  I.natives = natives.make_native_modules(I)
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
  local s = self.steps + 1
  self.steps = s
  if self.max_steps > 0 and s > self.max_steps then
    values.trap("diaspore: step budget exceeded (" .. self.max_steps .. ")")
  end
end

function Interp:fuel_n(n)
  local s = self.steps + n
  self.steps = s
  if self.max_steps > 0 and s > self.max_steps then
    values.trap("diaspore: step budget exceeded (" .. self.max_steps .. ")")
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

function Interp:run()
  local ok, err = pcall(Interp.import_module, self, self.main)
  self:flush()
  if ok then return 0 end
  if values.is_exc(err) then
    local v = err.value
    if values.exc_isa(v, "SystemExit") then
      local args = values.exc_args(v)
      local code = #args > 0 and args[1] or 0
      if kindof(code) == "none" then return 0 end
      if type(code) == "boolean" then return code and 1 or 0 end
      if math.type(code) == "integer" then return code & 0xFF end
      self:ewrite(ops.str(self, code) .. "\n")
      return 1
    end
    self:print_traceback(v)
    return 1
  end
  if values.is_trap(err) then
    self:ewrite(err.msg .. "\n")
    return err.code
  end
  if type(err) == "string" and err:find("stack overflow", 1, true) then
    self:ewrite("RecursionError: maximum recursion depth exceeded\n")
    return 1
  end
  self:ewrite("diaspore-lua internal error: " .. tostring(err) .. "\n")
  return 70
end

function Interp:print_traceback(v)
  local b = { "Traceback (most recent call last):\n" }
  local mods = self.bundle.modules
  for i = 1, #self.stack do
    local t = self.stack[i]
    local m = mods[t.module] or EMPTY
    local file = get(m, "file") or t.module
    local line = t.fr.line
    b[#b + 1] = '  File "' .. file .. '", line ' .. line .. ", in " .. t.fname .. "\n"
    local src = get(m, "src")
    if src ~= nil and line >= 1 and line <= #src then
      b[#b + 1] = "    " .. src[line]:match("^%s*(.-)%s*$") .. "\n"
    end
  end
  local name = kindof(v) == "instance" and v.cls.name or type_name(v)
  local msg
  if kindof(v) == "instance" then
    msg = ops.exc_str(self, v)
  else
    msg = ops.str(self, v)
  end
  if msg ~= "" then
    b[#b + 1] = name .. ": " .. msg .. "\n"
  else
    b[#b + 1] = name .. "\n"
  end
  self:ewrite(table.concat(b))
end

-- ------------------------------------------------------------------- modules

function Interp:import_module(name)
  local cached = self.modules[name]
  if cached ~= nil then return cached end
  local m = self.bundle.modules[name]
  if m ~= nil then
    local parent = nil
    local parent_name = name:match("^(.*)%.[^.]*$")
    if parent_name ~= nil then
      -- packages: ancestors execute first (each exactly once)
      parent = self:import_module(parent_name)
    end
    local fr = new_frame({}, nil, nil, EMPTY, EMPTY, false, nil, "<module>", name)
    fr.vars.__name__ = (name == self.main) and "__main__" or name
    local mod = { kind = "module", name = name, frame = fr }
    self.modules[name] = mod
    local st = self.stack
    local sn = #st + 1
    st[sn] = { module = name, fname = "<module>", fr = fr, depth0 = self.depth }
    self:exec_block(m.body, fr)
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
  if fr.gdecl[id] then
    local v = fr.globals[id]
    if v ~= nil then return v end
  elseif fr.localset ~= nil and fr.localset[id] then
    local v = fr.vars[id]
    if v ~= nil then return v end
    throw("UnboundLocalError", "cannot access local variable '" .. id
      .. "' where it is not associated with a value")
  else
    local f = fr
    while f ~= nil do
      local v = f.vars[id]
      if v ~= nil then return v end
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
-- int/float/tuple/bytes/bool).
local PRIM_BASES = {
  dict = true, list = true, set = true, str = true, int = true,
  float = true, tuple = true, bytes = true, bool = true,
}

local function check_base(I, bv)
  local k = kindof(bv)
  if k == "class" then return bv end
  if k == "btype" and PRIM_BASES[bv.name] then return bv end
  throw("TypeError", "cannot subclass " .. ops.repr(I, bv) .. " in diaspore")
end

STMTS["class"] = function(I, s, fr)
  local bases = {}
  local bnodes = get(s, "bases") or EMPTY
  for i = 1, #bnodes do
    local bn = bnodes[i]
    if bn.op == "starred" then
      local spread = iter.iter_to_array(I, I:ev(bn.v, fr))
      for j = 1, #spread do bases[#bases + 1] = check_base(I, spread[j]) end
    else
      bases[#bases + 1] = check_base(I, I:ev(bn, fr))
    end
  end
  local cf = new_frame({}, fr, nil, EMPTY, EMPTY, true, fr.globals, s.name, fr.module_name)
  cf.order = {}
  I:exec_block(s.body, cf)
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
  local cls = I:build_class(s.name, bases, cf.vars, cf.order, metaclass, kwargs)
  I:store_name(fr, s.name, cls)
  return nil
end

-- CPython-shaped class creation (spec feature round): an explicit metaclass
-- is called as metaclass(name, bases_tuple, ns_dict, **kwargs) unless it is
-- `type` (or a plain subclass adding no construction hooks, which degrades
-- to normal creation); remaining class kwargs dispatch to the nearest base's
-- __init_subclass__, else TypeError like CPython.
function Interp:build_class(name, bases, ns_map, ns_order, metaclass, kwargs)
  kwargs = kwargs or {}
  local function make(cls_name, cls_bases, cls_ns)
    local made = values.new_class(cls_name, cls_bases, cls_ns)
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
  local plain_meta = metaclass == nil
    or (kindof(metaclass) == "btype" and metaclass.name == "type")
    or (kindof(metaclass) == "class"
      and class_lookup(metaclass, "__new__") == nil
      and class_lookup(metaclass, "__call__") == nil
      and class_lookup(metaclass, "__init__") == nil)
  if not plain_meta then
    local bt = {}
    for i = 1, #bases do bt[i] = bases[i] end
    local nsd = values.new_dict()
    local seen = {}
    for i = 1, #ns_order do
      local key = ns_order[i]
      if not seen[key] and ns_map[key] ~= nil then
        seen[key] = true
        values.dict_set_hk(nsd, "s" .. key, key, ns_map[key])
      end
    end
    local result = self:call(metaclass, { name, values.tuple(bt), nsd }, kwargs)
    if kindof(result) ~= "class" then
      throw("TypeError", "metaclass returned " .. type_name(result) .. ", not a class")
    end
    return result
  end
  local ns = {}
  for k, v in pairs(ns_map) do ns[k] = v end
  local cls = make(name, bases, ns)
  if #kwargs > 0 then
    for _, base in ipairs(bases) do
      local init_sub = kindof(base) == "class"
        and class_lookup(base, "__init_subclass__") or nil
      if init_sub ~= nil then
        local fn = init_sub
        if kindof(fn) == "classmethod" then fn = fn.fn end
        self:call({ kind = "boundmethod", fn = fn, recv = cls }, {}, kwargs)
        return cls
      end
    end
    throw("TypeError", name .. ".__init_subclass__() takes no keyword arguments")
  end
  return cls
end

STMTS["raise"] = function(I, s, fr)
  if not has(s, "exc") then
    if #I.exc_stack == 0 then
      throw("RuntimeError", "No active exception to re-raise")
    end
    values.throw_val(I.exc_stack[#I.exc_stack])
  end
  local v = I:ev(s.exc, fr)
  if has(s, "cause") then I:ev(s.cause, fr) end
  if kindof(v) == "class" then
    if not values.issub(v, values.BASE_EXC) then
      throw("TypeError", "exceptions must derive from BaseException")
    end
    v = I:instantiate(v, {}, {})
  end
  if not (kindof(v) == "instance" and values.issub(v.cls, values.BASE_EXC)) then
    throw("TypeError", "exceptions must derive from BaseException")
  end
  values.throw_val(v)
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
    local rest = #unmatched > 0 and values.make_group(msg, unmatched) or nil
    return values.make_group(msg, matched), rest
  end
  if kindof(exc) == "instance" and matches(exc) then
    return values.make_group("", { exc }), nil
  end
  return nil, exc
end

local function try_star_phase(I, s, fr, saved)
  local ok, res = pcall(Interp.exec_block, I, get(s, "body"), fr)
  local pending = nil
  if ok then
    if res ~= nil then return res end
  else
    local err = res
    if values.is_trap(err) or not values.is_exc(err) then error(err, 0) end
    I:trim(saved)
    pending = err.value
  end
  if pending == nil then
    return I:exec_block(get(s, "orelse"), fr)
  end
  local handlers = get(s, "handlers") or EMPTY
  for hi = 1, #handlers do
    local h = handlers[hi]
    local match, rest = I:split_group(pending, h, fr)
    if match ~= nil then
      local hname = get(h, "name")
      if hname ~= nil then fr.vars[hname] = match end
      I.exc_stack[#I.exc_stack + 1] = match
      local ok2, res2 = pcall(Interp.exec_block, I, get(h, "body"), fr)
      I.exc_stack[#I.exc_stack] = nil
      if hname ~= nil then fr.vars[hname] = nil end
      if not ok2 then error(res2, 0) end
      if res2 ~= nil then return res2 end
      pending = rest
      if pending == nil then break end
    end
  end
  if pending ~= nil then values.throw_val(pending) end
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
  I:trim(saved)
  local handlers = get(s, "handlers") or EMPTY
  for hi = 1, #handlers do
    local h = handlers[hi]
    if I:handler_matches(h, err.value, fr) then
      local hname = get(h, "name")
      if hname ~= nil then fr.vars[hname] = err.value end
      I.exc_stack[#I.exc_stack + 1] = err.value
      local ok2, res2 = pcall(Interp.exec_block, I, get(h, "body"), fr)
      I.exc_stack[#I.exc_stack] = nil
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
    I:trim(saved)
    local fsig = I:exec_block(get(s, "finalbody"), fr)
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
  local ctx = I:ev(item.ctx, fr)
  local enter, exit_
  local k = kindof(ctx)
  if k == "file" then
    enter = function() return ctx end
    exit_ = function()
      I:close_file(ctx)
      return false
    end
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
    exit_(NONE, NONE, NONE) -- fallthrough and return/break/continue unwinding
    return res
  end
  local err = res
  if values.is_trap(err) or not values.is_exc(err) then
    error(err, 0) -- abandonment: no further diaspore code, __exit__ included
  end
  local inst = err.value
  if ops.truth(I, exit_(inst.cls, inst, NONE)) then
    I:trim(saved)
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
    local saved = #I.stack
    local ok, v = pcall(Interp.get_attr, I, mod, spec.n)
    if not ok then
      if values.is_exc(v) then
        I:trim(saved)
        throw("ImportError", "cannot import name '" .. spec.n
          .. "' from '" .. s.module .. "'")
      end
      error(v, 0)
    end
    I:store_name(fr, spec.alias, v)
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
      -- matches diaspore-py: instance-dict deletion only (no property deleter)
      local o = I:ev(t.o, fr)
      if kindof(o) == "instance" and o.d[t.n] ~= nil then
        o.d[t.n] = nil
      else
        throw("AttributeError", "'" .. type_name(o)
          .. "' object has no attribute '" .. t.n .. "'")
      end
    else
      throw("TypeError", "cannot delete this target")
    end
  end
  return nil
end

-- ------------------------------------------------------------------- expressions

local EXPRS = {}

function Interp:ev(e, fr)
  local h = EXPRS[e.op]
  if h == nil then
    values.trap("diaspore: bad expression op " .. tostring(e.op), 4)
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

local function const_val(e)
  local t = e.t
  if t == "none" then return NONE end
  if t == "ellipsis" then return values.ELLIPSIS end
  if t == "complex" then
    return values.complex(tonumber(e.re) + 0.0, tonumber(e.im) + 0.0)
  end
  if t == "int" then
    local n = math.tointeger(tonumber(e.v))
    if n ~= nil then return n end
    return tonumber(e.v) + 0.0 -- beyond int64: represented as a float
  end
  if t == "float" then
    local v = e.v
    if v == "inf" then return math.huge end
    if v == "-inf" then return -math.huge end
    if v == "nan" then return 0 / 0 end
    return tonumber(v) + 0.0
  end
  if t == "str" then return e.v end
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

EXPRS["name"] = function(I, e, fr)
  return I:load_name(fr, e.id)
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
  for i = 1, #kinds do
    local right = I:ev(comps[i], fr)
    if not ops.compare(I, kinds[i], left, right) then return false end
    left = right
  end
  return true
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
        values.dict_set_hk(out, values.hash_key(es[j].k), es[j].k, es[j].v)
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
      out[#out + 1] = p.s
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
  local args = {}
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
  local kwargs = {}
  local knodes = get(e, "kw") or EMPTY
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
  return I:call(f, args, kwargs)
end

-- ------------------------------------------------------------------- functions

function Interp:make_function(node, fr)
  local closure = fr
  while closure ~= nil and closure.is_class do closure = closure.parent end
  local meta = node.__fnmeta
  if meta == nil then
    local p = get(node, "params") or EMPTY
    meta = {
      args = get(p, "args") or EMPTY,
      vararg = get(p, "vararg"),
      kwonly = get(p, "kwonly") or EMPTY,
      kwarg = get(p, "kwarg"),
      defaults_nodes = get(p, "defaults") or EMPTY,
      kwdefaults_nodes = get(p, "kwdefaults") or EMPTY,
      localset = to_set(get(node, "locals")),
      gdecl = to_set(get(node, "gdecl")),
      ndecl = to_set(get(node, "ndecl")),
      gen = get(node, "gen") or false,
      name = node.name,
    }
    node.__fnmeta = meta
  end
  local fn = {
    kind = "function", node = node, name = meta.name, meta = meta,
    closure = closure, module_name = fr.module_name, defclass = nil,
    defaults = {}, kwdefaults = {},
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

function Interp:bind_args(fn, args, kwargs)
  local meta = fn.meta
  local names = meta.args
  local out = {}
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
    if contains_str(names, k) then
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
  if k == "boundmethod" then
    local a2 = { f.recv }
    for i = 1, #args do a2[i + 1] = args[i] end
    return self:call(f.fn, a2, kwargs)
  end
  if k == "class" then return self:instantiate(f, args, kwargs) end
  if k == "btype" then return f.ctor(self, args, kwargs) end
  if k == "builtin" then return f.fn(self, args, kwargs) end
  if k == "instance" then
    if class_lookup(f.cls, "__call__") ~= nil then
      return self:call_dunder_kw(f, "__call__", args, kwargs)
    end
    throw("TypeError", "'" .. f.cls.name .. "' object is not callable")
  end
  if k == "supermarker" then
    throw("RuntimeError", "super(): no arguments and no enclosing method")
  end
  throw("TypeError", "'" .. type_name(f) .. "' object is not callable")
end

function Interp:call_function(fn, args, kwargs)
  local vars = self:bind_args(fn, args, kwargs)
  local meta = fn.meta
  local fr = new_frame(vars, fn.closure, meta.localset, meta.gdecl, meta.ndecl,
    false, fn.closure ~= nil and fn.closure.globals or nil, fn.name, fn.module_name)
  fr.defclass = fn.defclass
  if #meta.args > 0 then fr.self_ = vars[meta.args[1]] end
  if meta.gen then
    fr.is_gen = true
    return iter.new_generator(self, fn, fr)
  end
  if self.depth >= self.max_depth then
    throw("RecursionError", "maximum recursion depth exceeded")
  end
  local st = self.stack
  local sn = #st + 1
  st[sn] = { module = fn.module_name, fname = fn.name, fr = fr, depth0 = self.depth }
  self.depth = self.depth + 1
  local sig = self:exec_block(fn.node.body, fr)
  self.depth = self.depth - 1
  st[sn] = nil
  if sig ~= nil then
    if sig.tag == "return" then return sig.value end
    values.trap("diaspore: control-flow signal escaped a function body", 4)
  end
  return NONE
end

local function prim_empty(name)
  if name == "dict" then return values.new_dict() end
  if name == "list" then return values.list({}) end
  if name == "set" then return values.new_set() end
  if name == "str" then return "" end
  if name == "bytes" then return values.bytes("") end
  if name == "tuple" then return values.tuple({}) end
  if name == "int" or name == "bool" then return 0 end
  return 0.0 -- float
end

local IMMUTABLE_PRIM = {
  str = true, bytes = true, int = true, float = true, tuple = true, bool = true,
}

function Interp:instantiate(cls, args, kwargs)
  local inst = values.new_instance(cls)
  if cls.prim ~= nil then
    local base = cls.prim.name
    -- immutable primitives take their value from the constructor args (like
    -- __new__); mutable ones start empty and inherited __init__ fills them
    if IMMUTABLE_PRIM[base] then
      if #args > 0 or #kwargs > 0 then
        inst.prim = cls.prim.ctor(self, args, kwargs)
      else
        inst.prim = prim_empty(base)
      end
    else
      inst.prim = prim_empty(base)
    end
  end
  local init = class_lookup(cls, "__init__")
  if init == nil then
    if (#args > 0 or #kwargs > 0) and cls.prim == nil then
      throw("TypeError", cls.name .. "() takes no arguments")
    end
    if cls.prim ~= nil and (cls.prim.name == "dict" or cls.prim.name == "list"
        or cls.prim.name == "set") and (#args > 0 or #kwargs > 0) then
      inst.prim = cls.prim.ctor(self, args, kwargs) -- inherited builtin __init__
    end
    return inst
  end
  local r = self:call(self:bind_attr(init, inst, cls), args, kwargs)
  if kindof(r) ~= "none" then
    throw("TypeError", "__init__() should return None, not '" .. type_name(r) .. "'")
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
function Interp:builtin_unbound(tname, method)
  local dunder = {
    __getitem__ = function(_I, a) return ops.get_index(self, self:raw(a[1]), a[2]) end,
    __setitem__ = function(_I, a) ops.set_index(self, self:raw(a[1]), a[2], a[3]); return NONE end,
    __len__ = function(_I, a) return ops.diaspore_len(self, self:raw(a[1])) end,
    __contains__ = function(_I, a) return ops.contains(self, self:raw(a[1]), a[2]) end,
    __iter__ = function(_I, a) return iter.get_iter(self, self:raw(a[1])) end,
  }
  if dunder[method] then return values.builtin(method, dunder[method]) end
  if PRIM_METHOD_SETS == nil then PRIM_METHOD_SETS = methods.METHOD_SETS end
  local allowed = PRIM_METHOD_SETS[tname]
  if allowed and allowed[method] then
    return values.builtin(method, function(I2, a, kw)
      local self_ = a[1]
      local rest = {}
      for i = 2, #a do rest[i - 1] = a[i] end
      local bound = methods.builtin_method(self, self:raw(self_), method)
      return bound.fn(I2, rest, kw)
    end)
  end
  return nil
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

function Interp:bind_attr(v, inst, cls)
  local k = kindof(v)
  if k == "function" then return { kind = "boundmethod", fn = v, recv = inst } end
  if k == "staticmethod" then return v.fn end
  if k == "classmethod" then return { kind = "boundmethod", fn = v.fn, recv = cls } end
  if k == "builtin" then return { kind = "boundmethod", fn = v, recv = inst } end
  return v
end

function Interp:get_attr(o, n)
  local k = kindof(o)
  if k == "instance" then
    if n == "__class__" then return o.cls end
    local dv = o.d[n]
    if dv ~= nil then return dv end
    if (n == "exceptions" or n == "message")
        and values.issub(o.cls, values.EXC_CLASSES.BaseExceptionGroup) then
      local gv = o.d[n]
      if gv ~= nil then return gv end
    end
    local cv = class_lookup(o.cls, n)
    if kindof(cv) == "property" then
      if cv.fget == nil then
        throw("AttributeError", "property '" .. n .. "' of '"
          .. o.cls.name .. "' object has no getter")
      end
      return self:call(self:bind_attr(cv.fget, o, o.cls), {}, {})
    end
    if cv ~= nil then return self:bind_attr(cv, o, o.cls) end
    if o.prim ~= nil then
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
    local unbound = self:builtin_unbound(o.name, n)
    if unbound ~= nil then return unbound end
    throw("AttributeError", "type object '" .. o.name .. "' has no attribute '" .. n .. "'")
  end
  if k == "class" then
    local v = class_lookup(o, n)
    if v == nil then
      if n == "__name__" or n == "__qualname__" then return o.name end
      if n == "__mro__" then
        local m = {}
        for i = 1, #o.mro do m[i] = o.mro[i] end
        return values.tuple(m)
      end
      if n == "__bases__" then
        if #o.bases == 0 then return values.tuple({ values.OBJECT }) end
        local b = {}
        for i = 1, #o.bases do b[i] = o.bases[i] end
        return values.tuple(b)
      end
      if n == "__dict__" then
        local d = values.new_dict()
        for key, val in pairs(o.ns) do values.dict_set(d, key, val) end
        return d
      end
      throw("AttributeError", "type object '" .. o.name .. "' has no attribute '" .. n .. "'")
    end
    local vk = kindof(v)
    if vk == "classmethod" then return { kind = "boundmethod", fn = v.fn, recv = o } end
    if vk == "staticmethod" then return v.fn end
    return v
  end
  if k == "property" then
    if n == "setter" then
      return values.builtin("setter", function(_I, a) return values.property(o.fget, a[1], o.fdel, o.doc) end)
    end
    if n == "getter" then
      return values.builtin("getter", function(_I, a) return values.property(a[1], o.fset, o.fdel, o.doc) end)
    end
    if n == "deleter" then
      return values.builtin("deleter", function(_I, a) return values.property(o.fget, o.fset, a[1], o.doc) end)
    end
    if n == "fget" then return o.fget or NONE end
    if n == "fset" then return o.fset or NONE end
    if n == "fdel" then return o.fdel or NONE end
    throw("AttributeError", "'property' object has no attribute '" .. n .. "'")
  end
  if k == "function" then
    if o.attrs ~= nil and o.attrs[n] ~= nil then return o.attrs[n] end
    if n == "__name__" or n == "__qualname__" then return o.name end
    if n == "__doc__" then return (o.attrs and o.attrs.__doc__) or NONE end
    if n == "__dict__" then
      o.attrs = o.attrs or {}
      local d = values.new_dict()
      for key, val in pairs(o.attrs) do values.dict_set(d, key, val) end
      return d
    end
    throw("AttributeError", "'function' object has no attribute '" .. n .. "'")
  end
  if k == "boundmethod" then
    if n == "__name__" then return ops.callable_name(o.fn) end
    if n == "__self__" then return o.recv end
    if n == "__func__" then return o.fn end
    return self:get_attr(o.fn, n)
  end
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
  if type(o) == "number" then
    if n == "real" then return o end
    if n == "imag" then return math.type(o) == "integer" and 0 or 0.0 end
    if n == "conjugate" then
      return values.builtin("conjugate", function() return o end)
    end
    if n == "numerator" and math.type(o) == "integer" then return o end
    if n == "denominator" and math.type(o) == "integer" then return 1 end
    if n == "bit_length" and math.type(o) == "integer" then
      return values.builtin("bit_length", function()
        local x = o < 0 and -o or o
        local b = 0
        while x > 0 do b = b + 1; x = x >> 1 end
        return b
      end)
    end
    if n == "is_integer" and math.type(o) == "float" then
      return values.builtin("is_integer", function()
        return o == o and o ~= math.huge and o ~= -math.huge and math.floor(o) == o
      end)
    end
  end
  if k == "superobj" then
    local mro
    if kindof(o.obj) == "instance" then
      mro = o.obj.cls.mro
    else
      mro = o.obj.mro
    end
    local start = 1
    for i = 1, #mro do
      if mro[i] == o.cls then
        start = i + 1
        break
      end
    end
    for i = start, #mro do
      local v = mro[i].ns[n]
      if v ~= nil then return self:bind_attr(v, o.obj, mro[i]) end
    end
    throw("AttributeError", "'super' object has no attribute '" .. n .. "'")
  end
  if k == "module" then
    local v = o.frame.vars[n]
    if v ~= nil then return v end
    throw("AttributeError", "module '" .. o.name .. "' has no attribute '" .. n .. "'")
  end
  if k == "nativemodule" then
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
  if k == "iter" and o.iname == "generator" then
    local m = iter.generator_method(self, o, n)
    if m ~= nil then return m end
    throw("AttributeError", "'generator' object has no attribute '" .. n .. "'")
  end
  return methods.builtin_method(self, o, n)
end

function Interp:set_attr(o, n, v)
  local k = kindof(o)
  if k == "function" then
    if n == "__name__" then
      o.name = v
    else
      o.attrs = o.attrs or {}
      o.attrs[n] = v
    end
    return
  end
  if k == "instance" then
    local prop = class_lookup(o.cls, n)
    if kindof(prop) == "property" then
      if prop.fset == nil then
        throw("AttributeError", "property '" .. n .. "' of '"
          .. o.cls.name .. "' object has no setter")
      end
      self:call(self:bind_attr(prop.fset, o, o.cls), { v }, {})
      return
    end
    o.d[n] = v
    return
  end
  if k == "class" then
    o.ns[n] = v
    return
  end
  if k == "module" then
    o.frame.vars[n] = v
    return
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

-- Snapshot a frame's variable table as a diaspore dict (globals()/locals()/vars()).
-- Frames are Lua tables; globals()/locals()/vars() return snapshots.
function Interp:frame_dict(vars)
  local d = values.new_dict()
  if vars ~= nil then
    for key, val in pairs(vars) do
      if type(key) == "string" then values.dict_set(d, key, val) end
    end
  end
  return d
end

-- exec/eval/compile need a Python-subset compiler; diaspore-lua ships no compiler
-- bundle, so runtime code execution is unavailable.
function Interp:compile_snippet(source, filename, mode)
  throw("NotImplementedError",
    "diaspore-lua does not support compile()/exec()/eval()")
end

function Interp:exec_snippet(code, glob, loc, mode)
  throw("NotImplementedError",
    "diaspore-lua does not support exec()/eval()")
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
