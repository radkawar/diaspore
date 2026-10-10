-- Iterators and generators. An iterator is {kind="iter", iname=..., next=fn,
-- retval=NONE}; it.next() returns (value, ok). Generators run their body in a
-- Lua coroutine (strict ping-pong); a generator abandoned before exhaustion
-- never runs pending finally blocks (allowed by spec section 7).

local values = require("diaspore.values")
local bigint = require("diaspore.bigint")
local buffer = require("diaspore.buffer")
local protected = require("diaspore.protected")
local pcall = protected.call

local M = {}

local NONE = values.NONE
local throw = values.throw
local type_name = values.type_name

local function mk(iname, next_fn)
  return { kind = "iter", iname = iname, next = next_fn, retval = NONE }
end

--- Create an iterator over list.
function M.list_iter(l)
  local i = 0
  local it
  it = mk("list_iterator", function()
    if i >= #l.items then return NONE, false end
    i = i + 1
    return l.items[i], true
  end)
  return it
end

--- Create an iterator over sequence.
function M.seq_iter(items, iname)
  local i = 0
  return mk(iname or "iterator", function()
    if i >= #items then return NONE, false end
    i = i + 1
    return items[i], true
  end)
end

--- Create an iterator over string.
function M.str_iter(s)
  local rs = values.chars(s)
  local snapshot = {}
  for i = 1, #rs do snapshot[i] = rs[i] end
  return M.seq_iter(snapshot, "str_iterator")
end

--- Create an iterator over byte string.
function M.bytes_iter(b)
  local data = b.b
  local i = 0
  return mk("bytes_iterator", function()
    if i >= #data then return NONE, false end
    i = i + 1
    return data:byte(i), true
  end)
end

--- Create an iterator over range.
function M.range_iter(r)
  local cur = r.start
  local step = r.step
  local left = values.range_len(r)
  if type(cur) == "number" and type(step) == "number"
      and type(r.stop) == "number" and type(left) == "number" then
    return mk("range_iterator", function()
      if left == 0 then return NONE, false end
      local v = cur
      left = left - 1
      if left ~= 0 then cur = cur + step end
      return v, true
    end)
  end
  return mk("range_iterator", function()
    if left == 0 then return NONE, false end
    local v = cur
    cur = bigint.add(cur, step)
    left = bigint.sub(left, 1)
    return v, true
  end)
end

--- Iterate a live dictionary view, checking changes to its size.
function M.dict_view_iter(view, reverse)
  local d = values.view_dict(view)
  local size = d.size
  local position = reverse and #d.keys + 1 or 0
  local step = reverse and -1 or 1
  local done = false
  local name = "dict_" .. (reverse and "reverse" or "") .. view.view:sub(1, -2) .. "iterator"
  return mk(name, function()
    if done then return NONE, false end
    local current = values.view_dict(view)
    if current.size ~= size then
      throw("RuntimeError", "dictionary changed size during iteration")
    end
    while true do
      position = position + step
      if position < 1 or position > #d.keys then
        done = true
        return NONE, false
      end
      local entry = d.map[d.keys[position]]
      if entry ~= nil and entry.pos == position then
        if current ~= d then entry = values.dict_entry(current, entry.k) end
        if entry ~= nil then
          if view.view == "keys" then return entry.k, true end
          if view.view == "values" then return entry.v, true end
          return values.tuple({ entry.k, entry.v }), true
        end
      end
    end
  end)
end

--- Iterate live set entries, preserving size-change failures after detection.
function M.set_iter(s)
  local size, position, done = s.size, 0, false
  return mk("set_iterator", function()
    if done then return NONE, false end
    if s.size ~= size then
      size = -1
      throw("RuntimeError", "Set changed size during iteration")
    end
    while position < #s.keys do
      position = position + 1
      local entry = s.map[s.keys[position]]
      if entry ~= nil and entry.pos == position then return entry.v, true end
    end
    done = true
    return NONE, false
  end)
end

local function special_method(I, obj, name)
  if type(obj) ~= "table" then return nil end
  local cls
  if obj.kind == "instance" then cls = obj.cls
  elseif obj.kind == "class" or obj.kind == "btype" then cls = obj.metaclass or I.types.type
  else return nil end
  return I:lookup_type_attr(cls, name), cls
end

--- Test the iterator slot on an object's actual type, including metaclasses.
function M.is_iterator(I, obj)
  if type(obj) == "table" and obj.kind == "iter" then return true end
  return special_method(I, obj, "__next__") ~= nil
end

--- Advance a user-defined iterator through its type's next descriptor.
function M.next_value(I, obj)
  local method, cls = special_method(I, obj, "__next__")
  if method == nil then throw("TypeError", "'" .. type_name(obj) .. "' object is not an iterator") end
  return I:call(I:bind_attr(method, obj, cls), {}, {})
end

--- Adapts a diaspore object implementing __next__.
function M.inst_iter(I, obj)
  local it
  it = mk("iterator", function()
    local saved = #I.stack
    local ok, res = pcall(M.next_value, I, obj)
    if ok then return res, true end
    if values.is_exc(res) and values.exc_isa(res.value, "StopIteration") then
      I:trim(saved)
      local args = values.exc_args(res.value)
      if #args > 0 then it.retval = args[1] else it.retval = NONE end
      it.stop_args = args
      return NONE, false
    end
    error(res, 0)
  end)
  return it
end

--- Create an iterator over mapped input iterators.
function M.map_iter(I, fn, its)
  local done = false
  return mk("map", function()
    if done then return NONE, false end
    local args = {}
    for i = 1, #its do
      local x, ok = its[i].next()
      if not ok then
        done = true
        return NONE, false
      end
      args[i] = x
    end
    return I:call(fn, args, {}), true
  end)
end

--- Create an iterator over filtered input iterator.
function M.filter_iter(I, fn, it)
  local done = false
  return mk("filter", function()
    if done then return NONE, false end
    while true do
      local x, ok = it.next()
      if not ok then
        done = true
        return NONE, false
      end
      local keep = x
      if fn ~= nil then keep = I:call(fn, { x }, {}) end
      if I:truth(keep) then return x, true end
    end
  end)
end

--- Create an iterator over zipped input iterators.
function M.zip_iter(its)
  local done = false
  return mk("zip", function()
    if done or #its == 0 then
      done = true
      return NONE, false
    end
    local row = {}
    for i = 1, #its do
      local x, ok = its[i].next()
      if not ok then
        done = true
        return NONE, false
      end
      row[i] = x
    end
    return values.tuple(row), true
  end)
end

--- Create an iterator over enumerated input iterator.
function M.enum_iter(it, start)
  local n = start
  return mk("enumerate", function()
    local x, ok = it.next()
    if not ok then return NONE, false end
    local v = values.tuple({ n, x })
    if type(n) == "number" and n ~= math.maxinteger then
      n = n + 1
    else
      n = bigint.add(n, 1)
    end
    return v, true
  end)
end

-- ---------------------------------------------------------------------------
-- generators
--
-- The body runs in a Lua coroutine. Consumers advance it with a token:
--   {kind="send", value=v}   next()/send(v): the paused `yield` returns v
--   {kind="throw", exc=e}    throw(e)/close(): the paused `yield` raises e
-- The very first resume starts the body from the top; its token payload is
-- ignored (mirrors diaspore-py's _gen_body, which consumes the first token).
-- Exhaustion: gen.retval holds the return value; gen.stop_args is the
-- one-shot args tuple for the StopIteration that raising consumers emit.

local SEND_NONE = { kind = "send", value = NONE }
M.SEND_NONE = SEND_NONE

--- Create a coroutine-backed generator for a function frame.
function M.new_generator(I, fn, frame)
  local gen
  local co = coroutine.create(function(_first_token)
    local sig = I:exec_block(fn.node.body, frame)
    if sig ~= nil and sig.tag == "return" then return sig.value end
    return NONE
  end)
  gen = mk("generator", nil)
  gen.done = false
  gen.started = false
  local entry = { fr = frame }
  gen.stack = { entry }
  gen.fname = fn.name
  -- Raw advance: (value, true) on yield; (nil, false) on exhaustion
  -- (retval/stop_args set); diaspore errors propagate.
  gen.advance = function(token)
    if gen.running then throw("ValueError", "generator already executing") end
    if gen.done then
      gen.retval = NONE
      gen.stop_args = nil -- already-done: plain StopIteration
      return nil, false
    end
    if I.depth >= I.recursion_limit then
      throw("RecursionError", "maximum recursion depth exceeded")
    end
    gen.started, gen.running = true, true
    local prev, depth = I.stack, I.depth
    entry.previous, entry.depth0 = prev[#prev], depth
    if entry.frame_value then require("diaspore.metadata").retain(entry) end
    I.stack, I.depth = gen.stack, depth + 1
    local ok, a = protected.resume(co, token)
    if not ok and values.is_exc(a) then I:prepare_exception(a, 1) end
    I.stack, I.depth = prev, depth
    gen.running = false
    if not ok then
      gen.done = true
      error(a, 0)
    end
    if coroutine.status(co) == "dead" then
      gen.done = true
      gen.retval = a
      if type(a) == "table" and a.kind == "none" then
        gen.stop_args = {}
      else
        gen.stop_args = { a }
      end
      return nil, false
    end
    entry.previous, entry.retained_previous = nil, nil
    return a, true
  end
  gen.next = function()
    local v, more = gen.advance(SEND_NONE)
    if more then return v, true end
    return NONE, false
  end
  return gen
end

-- Raise the (one-shot) StopIteration for an exhausted generator.
local function raise_stop(gen)
  local sa = gen.stop_args
  gen.stop_args = nil
  gen.retval = NONE
  if sa ~= nil and #sa > 0 then
    values.throw_val(values.make_exc("StopIteration", sa[1]))
  end
  values.throw_val(values.make_exc("StopIteration"))
end

--- close() semantics shared by the .close() method and PEP 380 delegation.
function M.gen_close(I, gen)
  if gen.done or not gen.started then
    gen.done = true
    return NONE
  end
  local ge = values.make_exc("GeneratorExit")
  local saved = #I.stack
  local ok, v, more = pcall(gen.advance, { kind = "throw", exc = ge })
  if ok then
    if more then
      local _ = v
      throw("RuntimeError", "generator ignored GeneratorExit")
    end
    gen.stop_args = nil
    gen.retval = NONE
    return NONE
  end
  local err = v
  if values.is_exc(err)
      and (err.value == ge or values.exc_isa(err.value, "GeneratorExit")) then
    I:trim(saved)
    return NONE
  end
  error(err, 0)
end

--- The generator method surface: send / throw / close (spec: no other attrs).
function M.generator_method(I, gen, n)
  if n == "send" then
    return values.builtin("send", function(_I, args, _kw)
      if #args ~= 1 then
        throw("TypeError", "send() takes exactly 1 argument (" .. #args .. " given)")
      end
      local value = args[1]
      if not gen.started
          and not (type(value) == "table" and value.kind == "none") then
        throw("TypeError", "can't send non-None value to a just-started generator")
      end
      local v, more = gen.advance({ kind = "send", value = value })
      if more then return v end
      raise_stop(gen)
    end)
  elseif n == "throw" then
    return values.builtin("throw", function(_I, args, _kw)
      if #args ~= 1 then
        throw("TypeError", "throw() takes exactly 1 argument (" .. #args .. " given)")
      end
      local exc = args[1]
      if type(exc) == "table" and exc.kind == "class"
          and values.issub(exc, values.BASE_EXC) then
        exc = I:instantiate(exc, {}, {})
      end
      if not (type(exc) == "table" and exc.kind == "instance"
          and values.issub(exc.cls, values.BASE_EXC)) then
        throw("TypeError", "exceptions must derive from BaseException")
      end
      local v, more = gen.advance({ kind = "throw", exc = exc })
      if more then return v end
      raise_stop(gen)
    end)
  elseif n == "close" then
    return values.builtin("close", function(_I, _args, _kw)
      return M.gen_close(I, gen)
    end)
  end
  return nil
end

-- ---------------------------------------------------------------------------
-- protocol entry points

local function protocol_iterator(I, obj, method, cls)
  local result = I:call(I:bind_attr(method, obj, cls), {}, {})
  if M.is_iterator(I, result) then
    return result
  end
  throw("TypeError", "iter() returned non-iterator of type '" .. type_name(result) .. "'")
end

--- Return the Python-visible iterator, preserving user object identity.
function M.iter_object(I, obj)
  local method, cls = special_method(I, obj, "__iter__")
  if method ~= nil then return protocol_iterator(I, obj, method, cls) end
  return M.get_iter(I, obj)
end

--- Adapt an iterable to the internal next() -> (value, available) protocol.
function M.get_iter(I, v)
  local t = type(v)
  if t == "string" then return M.str_iter(v) end
  if t == "table" then
    local k = v.kind
    if k == "iter" then return v end
    if k == "GenericAlias" then return require("diaspore.typeexpr").iter(I, v) end
    if k == "dictview" then return M.dict_view_iter(v, false) end
    if k == "list" then return M.list_iter(v) end
    if k == "tuple" then return M.seq_iter(v.items) end
    if k == "bytes" then return M.bytes_iter(v) end
    if k == "bytearray" or k == "memoryview" then
      local i = 0
      local done = false
      return mk(k .. "_iterator", function()
        if done then return NONE, false end
        if i >= buffer.length(v) then done = true; return NONE, false end
        local result = buffer.get(v, i)
        i = i + 1
        return result, true
      end)
    end
    if k == "range" then return M.range_iter(v) end
    if k == "mappingproxy" then return M.get_iter(I, values.proxy_dict(v)) end
    if k == "dict" then
      local keys = {}
      local entries = values.dict_entries(v)
      for i = 1, #entries do keys[i] = entries[i].k end
      return M.seq_iter(keys)
    end
    if k == "set" then return M.set_iter(v) end
    if k == "nativeobject" then
      throw("TypeError", "'" .. v.nkind .. "' object is not iterable")
    end
    if k == "instance" or k == "class" or k == "btype" then
      local method, cls = special_method(I, v, "__iter__")
      if method == nil and k == "instance" and v.prim ~= nil then
        return M.get_iter(I, v.prim)
      end
      if method ~= nil then
        local it = protocol_iterator(I, v, method, cls)
        if it.kind == "iter" then return it end
        return M.inst_iter(I, it)
      end
      throw("TypeError", "'" .. type_name(v) .. "' object is not iterable")
    end
  end
  throw("TypeError", "'" .. type_name(v) .. "' object is not iterable")
end

--- Collect an iterable into a Lua array.
function M.iter_to_array(I, v)
  if type(v) == "table" then
    local k = v.kind
    if k == "list" or k == "tuple" then
      local out = {}
      local items = v.items
      for i = 1, #items do out[i] = items[i] end
      return out
    end
  end
  local it = M.get_iter(I, v)
  local out = {}
  while true do
    local x, ok = it.next()
    if not ok then return out end
    out[#out + 1] = x
  end
end

return M
