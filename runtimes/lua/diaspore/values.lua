-- Diaspore VM (Lua 5.4 host) - core value model, exception hierarchy, hashing.
--
-- Diaspore values map to these Lua types:
--   int   -> Lua integer or tagged bigint        float -> Lua float
--   bool  -> Lua boolean                         str   -> Lua string (UTF-8)
--   everything else -> a table with a `kind` field:
--   none ellipsis bytes list tuple dict set range slice class instance
--   function boundmethod staticmethod classmethod superobj supermarker
--   module nativemodule builtin btype nativeobject file iter
-- No diaspore value is ever Lua nil (absence is nil; JSON null in raw IR is NULL).

local bigint = require("diaspore.bigint")
local pcall = require("diaspore.protected").call

local M = {}

-- Sentinel for JSON null inside raw parsed IR nodes.
M.NULL = setmetatable({}, { __tostring = function() return "<null>" end })

M.NONE = { kind = "none" }
M.ELLIPSIS = { kind = "ellipsis" }
M.NOT_IMPLEMENTED = { kind = "notimplemented" }
M.SUPER_MARKER = { kind = "supermarker" }

-- Statement-flow signals (returned, not raised).
M.BREAK = { tag = "break" }
M.CONTINUE = { tag = "continue" }
--- Create a return control-flow signal.
function M.ret_sig(v) return { tag = "return", value = v } end

-- ---------------------------------------------------------------------------
-- identity

local next_id = 1
local ids = setmetatable({}, { __mode = "k" })

--- Return a stable identity for an object while it remains live.
function M.obj_id(v)
  local i = ids[v]
  if not i then
    i = next_id
    next_id = next_id + 1
    ids[v] = i
  end
  return i
end

-- ---------------------------------------------------------------------------
-- constructors

--- Wrap a Lua string as sandbox bytes.
function M.bytes(s) return { kind = "bytes", b = s } end
--- Wrap an array as a sandbox list.
function M.list(items) return { kind = "list", items = items or {} } end
--- Wrap an array as a sandbox tuple.
function M.tuple(items) return { kind = "tuple", items = items or {} } end
--- Wrap a native callable as a sandbox builtin.
function M.builtin(name, fn, descriptor)
  return { kind = "builtin", name = name, fn = fn, descriptor = descriptor }
end
--- Create a builtin type with its constructor.
function M.btype(name, ctor) return { kind = "btype", name = name, ctor = ctor, ns = {} } end

--- Create a sandbox range.
function M.range(start, stop, step)
  return { kind = "range", start = start, stop = stop, step = step }
end

--- Return the number of elements in a range.
function M.range_len(r)
  if bigint.cmp(r.step, 0) > 0 then
    if bigint.cmp(r.stop, r.start) <= 0 then return 0 end
    return bigint.add(bigint.floordiv(bigint.sub(bigint.sub(r.stop, r.start), 1), r.step), 1)
  end
  if bigint.cmp(r.stop, r.start) >= 0 then return 0 end
  return bigint.add(bigint.floordiv(bigint.sub(bigint.sub(r.start, r.stop), 1), bigint.neg(r.step)), 1)
end

-- ---------------------------------------------------------------------------
-- exceptions / errors (Lua error values are tagged tables)

--- Wrap an exception instance for Lua error propagation.
function M.exc_err(inst) return { tag = "exc", value = inst } end

--- Raise a sandbox exception instance.
function M.throw_val(inst) error(M.exc_err(inst), 0) end

--- Raise an uncatchable VM trap with an exit code.
function M.trap(msg, code) error({ tag = "trap", msg = msg, code = code or 3 }, 0) end

--- Test whether a Lua error carries a sandbox exception.
function M.is_exc(e) return type(e) == "table" and e.tag == "exc" end
--- Test whether a Lua error carries a VM trap.
function M.is_trap(e) return type(e) == "table" and e.tag == "trap" end

-- ---------------------------------------------------------------------------
-- numeric helpers

--- Return an integer value for int-kind inputs, or nil.
function M.as_index(v)
  local t = type(v)
  if t == "number" then
    if math.type(v) == "integer" then return v end
    return nil
  end
  if t == "boolean" then return v and 1 or 0 end
  if bigint.is_bigint(v) then return v end
  return nil
end

--- Test whether a value is an integer or boolean.
function M.is_int_kind(v)
  return type(v) == "boolean" or bigint.is_int(v)
end

--- Require an integer to fit a host-sized index.
function M.to_size(n, message)
  if bigint.is_bigint(n) then
    M.throw("OverflowError", message or "Python int too large to convert to C ssize_t")
  end
  return n
end

--- Clamp an integer without converting or rounding arbitrary precision values.
function M.clamp_index(n, low, high)
  if bigint.cmp(n, low) < 0 then return low end
  if bigint.cmp(n, high) > 0 then return high end
  return n
end

--- Convert a numeric value to a float, or return nil.
function M.as_float(v)
  local t = type(v)
  if t == "number" then return v * 1.0 end
  if t == "boolean" then return v and 1.0 or 0.0 end
  if bigint.is_bigint(v) then return bigint.to_float(v) end
  return nil
end

--- Test whether a value is numeric, including booleans.
function M.is_number(v)
  local t = type(v)
  return t == "number" or t == "boolean" or bigint.is_bigint(v)
end

-- ---------------------------------------------------------------------------
-- naming

--- Return the Python-facing type name of a value.
function M.type_name(v)
  local t = type(v)
  if t == "number" then
    return math.type(v) == "integer" and "int" or "float"
  end
  if t == "boolean" then return "bool" end
  if t == "string" then return "str" end
  local k = v.kind
  if bigint.is_bigint(v) then return "int" end
  if k == "none" then return "NoneType" end
  if k == "notimplemented" then return "NotImplementedType" end
  if k == "set" and v.frozen then return "frozenset" end
  if k == "dictview" then return "dict_" .. v.view end
  if k == "instance" then return v.cls.name end
  if k == "class" or k == "btype" then return "type" end
  if k == "function" then return "function" end
  if k == "boundmethod" then
    if v.fn.descriptor == "wrapper_descriptor" then return "method-wrapper" end
    return "method"
  end
  if k == "builtin" then return v.descriptor or "builtin_function_or_method" end
  if k == "module" or k == "nativemodule" then return "module" end
  if k == "nativeobject" then return v.nkind end
  if k == "staticmethod" then return "staticmethod" end
  if k == "classmethod" then return "classmethod" end
  if k == "superobj" or k == "supermarker" then return "super" end
  if k == "iter" then return v.iname end
  if k == "property" then return "property" end
  if k == "descriptor" then return v.descriptor end
  return k -- bytes list tuple dict set range slice ellipsis file complex
end

--- Create a complex number with floating-point components.
function M.complex(re, im)
  -- multiply (not add) coerces int->float while preserving signed zero
  return { kind = "complex", re = re * 1.0, im = im * 1.0 }
end

--- Create a property descriptor.
function M.property(fget, fset, fdel, doc)
  return { kind = "property", fget = fget, fset = fset, fdel = fdel, doc = doc }
end

-- ---------------------------------------------------------------------------
-- hashing (unique comparable string encoding with Python numeric unification)

--- Encode a hash key with Python numeric equality rules.
function M.hash_key(v)
  local t = type(v)
  if t == "number" then
    if math.type(v) == "integer" then return "i" .. v end
    if v ~= v then return "fnan" end
    local iv = math.tointeger(v)
    if iv then return "i" .. iv end
    if v ~= math.huge and v ~= -math.huge and v == math.floor(v) then
      return "i" .. bigint.tostring(bigint.from_float(v))
    end
    return "f" .. string.pack("d", v)
  end
  if t == "boolean" then return v and "i1" or "i0" end
  if t == "string" then return "s" .. v end
  local k = v.kind
  if bigint.is_bigint(v) then return "i" .. bigint.tostring(v) end
  if k == "instance" then return "h" .. M.hash_value(v) end
  if k == "bytes" then return "b" .. v.b end
  if k == "memoryview" then
    local buffer = require("diaspore.buffer")
    buffer.check(v)
    if not v.readonly then M.throw("ValueError", "cannot hash writable memoryview object") end
    if v.format ~= "B" and v.format ~= "b" and v.format ~= "c" then
      M.throw("ValueError", "memoryview: hashing is restricted to formats 'B', 'b' or 'c'")
    end
    M.hash_key(v.base)
    return "b" .. buffer.data(v)
  end
  if k == "none" then return "N" end
  if k == "ellipsis" then return "E" end
  if k == "tuple" then
    local parts = { "t" }
    local custom = false
    local hash = 0xcbf29ce484222325
    for i = 1, #v.items do
      local sub = M.hash_key(v.items[i])
      if sub:sub(1, 1) == "h" then custom = true end
      hash = (hash ~ M.hash_code(v.items[i], sub)) * 0x100000001b3
      parts[#parts + 1] = #sub .. ":" .. sub
    end
    if custom then
      hash = hash ~ #v.items
      return "h" .. (hash == -1 and -2 or hash)
    end
    return table.concat(parts)
  end
  if k == "range" then
    local n = M.range_len(v)
    return "r" .. M.hash_key(M.tuple({ n, n == 0 and M.NONE or v.start,
      (n == 0 or n == 1) and M.NONE or v.step }))
  end
  if k == "complex" then
    -- imag==0 unifies with the real number (CPython: hash(1+0j)==hash(1))
    if v.im == 0.0 then return M.hash_key(v.re) end
    return "c" .. string.pack("dd", v.re, v.im)
  end
  if k == "set" and v.frozen then
    if v.hash == nil then
      local hash = 0
      for slot, entry in pairs(v.map) do
        local item_hash = M.hash_code(entry.v, entry.hk or slot)
        hash = hash ~ (((item_hash ~ 89869747) ~ (item_hash << 16)) * 3644798167)
      end
      hash = hash ~ ((v.size + 1) * 1927868237)
      hash = hash ~ (hash >> 11) ~ (hash >> 25)
      hash = hash * 69069 + 907133923
      v.hash = hash == -1 and 590923713 or hash
    end
    return "h" .. v.hash
  end
  if k == "slice" then
    -- slices are hashable since CPython 3.12
    return "S" .. M.hash_key(v.lower) .. "|" .. M.hash_key(v.upper)
      .. "|" .. M.hash_key(v.step)
  end
  if k == "list" or k == "dict" or k == "set" or k == "mappingproxy" or k == "bytearray"
      or (k == "dictview" and v.view ~= "values") then
    M.throw_val(M.make_exc("TypeError", "unhashable type: '" .. M.type_name(v) .. "'"))
  end
  if k == "GenericAlias" or k == "UnionType" then
    return "h" .. require("diaspore.typeexpr").hash(v)
  end
  return "p" .. M.obj_id(v)
end

local function fnv_hash(s)
  local h = 0xcbf29ce484222325
  for i = 1, #s do h = (h ~ s:byte(i)) * 0x100000001b3 end
  return h & 0x7fffffffffffffff
end

local function float_hash(v)
  if v == math.huge then return 314159 end
  if v == -math.huge then return -314159 end
  if v ~= v then return fnv_hash("fnan") end
  local bits = string.unpack("<I8", string.pack("<d", v))
  local exponent = (bits >> 52) & 0x7ff
  local mantissa = bits & 0xfffffffffffff
  local shift
  if exponent == 0 then
    shift = -1074
  else
    mantissa = mantissa | 0x10000000000000
    shift = exponent - 1075
  end
  shift = shift % 61
  local modulus = 0x1fffffffffffffff
  local hash = ((mantissa << shift) & modulus) | (mantissa >> (61 - shift))
  if hash == modulus then hash = 0 end
  if bits < 0 then hash = -hash end
  return hash == -1 and -2 or hash
end

--- Return a numeric hash from a key already hashed by the value protocol.
function M.hash_code(v, encoded)
  if encoded:sub(1, 1) == "h" then return math.tointeger(encoded:sub(2)) end
  if type(v) == "table" and v.kind == "tuple" then
    local hash = 0xcbf29ce484222325
    for _, item in ipairs(v.items) do
      hash = (hash ~ M.hash_value(item)) * 0x100000001b3
    end
    hash = hash ~ #v.items
    return hash == -1 and -2 or hash
  end
  if type(v) == "table" and v.kind == "complex" then
    local hash = float_hash(v.re) + 1000003 * float_hash(v.im)
    return hash == -1 and -2 or hash
  end
  local n = M.as_index(v)
  if n ~= nil then return bigint.hash(n) end
  if math.type(v) == "float" then return float_hash(v) end
  return fnv_hash(encoded)
end

--- Hash a value, honoring inherited user hash methods.
function M.hash_value(v)
  if type(v) == "table" and v.kind == "instance" then
    local method = M.class_lookup(v.cls, "__hash__")
    if method == M.NONE then
      M.throw("TypeError", "unhashable type: '" .. v.cls.name .. "'")
    end
    if method == nil or method == M.OBJECT.ns.__hash__ then
      if v.prim ~= nil then
        local inherited = true
        for _, cls in ipairs(v.cls.mro) do
          if cls == M.OBJECT then break end
          if cls.ns.__hash__ ~= nil then inherited = false; break end
        end
        if inherited then
          local kind = type(v.prim) == "table" and v.prim.kind or nil
          if kind == "dict" or kind == "list" or (kind == "set" and not v.prim.frozen) then
            M.throw("TypeError", "unhashable type: '" .. v.cls.name .. "'")
          end
          return M.hash_value(v.prim)
        end
      end
      return M.obj_id(v) % (1 << 61)
    end
    local result = v.cls.interp:call_dunder(v, "__hash__", {})
    local n = M.as_index(result)
    if n == nil and type(result) == "table" and result.kind == "instance" then
      n = M.as_index(result.prim)
    end
    if n == nil then M.throw("TypeError", "__hash__ method should return an integer") end
    if bigint.is_bigint(n) then return bigint.hash(n) end
    return n == -1 and -2 or n
  end
  return M.hash_code(v, M.hash_key(v))
end

local function key_interp(key)
  if type(key) ~= "table" then return nil end
  if key.kind == "instance" then return key.cls.interp end
  if key.kind == "tuple" then
    for _, item in ipairs(key.items) do
      local I = key_interp(item)
      if I ~= nil then return I end
    end
  elseif key.kind == "set" and key.frozen then
    for _, entry in pairs(key.map) do
      local I = key_interp(entry.v)
      if I ~= nil then return I end
    end
  end
end

local key_ops
local function find_slot(c, hk, key)
  if not c.slow_keys and hk:byte(1) ~= 104 then return hk, c.map[hk] end
  local hash = M.hash_code(key, hk)
  for i, slot in ipairs(c.keys) do
    local entry = c.map[slot]
    if entry ~= nil and entry.pos == i then
      local stored = entry.v
      if c.kind == "dict" then stored = entry.k end
      if entry.hash == nil then entry.hash = M.hash_code(stored, entry.hk or slot) end
      if entry.hash == hash then
        if stored == key then return slot, entry, hash end
        local I = key_interp(stored) or key_interp(key)
        if I ~= nil or (not (type(stored) == "table" and stored.kind == "instance")
            and not (type(key) == "table" and key.kind == "instance")) then
          key_ops = key_ops or require("diaspore.ops")
          if key_ops.eq_elem(I, stored, key) then return slot, entry, hash end
        elseif hk == (entry.hk or slot) then
          return slot, entry, hash
        end
      end
    end
  end
  if c.map[hk] ~= nil then return {}, nil, hash end
  return hk, nil, hash
end

--- Look up an entry using a previously computed hash key.
function M.dict_entry_hk(d, hk, key)
  local _, entry = find_slot(d, hk, key)
  return entry
end

--- Look up an entry with collision-aware user equality.
function M.dict_entry(d, key)
  return M.dict_entry_hk(d, M.hash_key(key), key)
end

-- ---------------------------------------------------------------------------
-- ordered dict / set (insertion order; tombstoned key array with compaction)

--- Create an empty insertion-ordered dictionary.
function M.new_dict() return { kind = "dict", keys = {}, map = {}, size = 0, dead = 0 } end
--- Create an empty insertion-ordered set.
function M.new_set(frozen)
  return { kind = "set", keys = {}, map = {}, size = 0, dead = 0, frozen = frozen }
end

--- Snapshot a class namespace in definition order.
function M.namespace_dict(cls)
  local d = M.new_dict()
  for _, key in ipairs(cls.ns_order or {}) do
    if cls.ns[key] ~= nil then M.dict_set(d, key, cls.ns[key]) end
  end
  for key, val in pairs(cls.ns) do
    if not M.dict_has(d, key) then M.dict_set(d, key, val) end
  end
  return d
end

--- Return the dictionary exposed by a read-only namespace proxy.
function M.proxy_dict(proxy)
  return proxy.mapping or M.namespace_dict(proxy.cls)
end

--- Create a live dictionary keys, values, or items view.
function M.dict_view(source, view)
  return { kind = "dictview", source = source, view = view }
end

--- Return the dictionary currently exposed by a view.
function M.view_dict(view)
  if view.source.kind == "mappingproxy" then return M.proxy_dict(view.source) end
  return view.source
end

local function compact(c)
  local nk = {}
  for i = 1, #c.keys do
    local hk = c.keys[i]
    local e = c.map[hk]
    if e and e.pos == i then
      nk[#nk + 1] = hk
      e.pos = #nk
    end
  end
  c.keys = nk
  c.dead = 0
end
M.compact = compact

--- Store a dictionary entry using a precomputed hash key.
function M.dict_set_hk(d, hk, key, val)
  local slot, e, hash = find_slot(d, hk, key)
  if e then
    e.v = val
  else
    local pos = #d.keys + 1
    d.keys[pos] = slot
    d.map[slot] = { k = key, v = val, pos = pos, hk = hk, hash = hash }
    if hk:sub(1, 1) == "h" then d.slow_keys = true end
    d.size = d.size + 1
  end
end

--- Store a dictionary entry.
function M.dict_set(d, key, val) M.dict_set_hk(d, M.hash_key(key), key, val) end

--- Look up a dictionary entry, or return nil.
function M.dict_get(d, key)
  local e = M.dict_entry(d, key)
  if e then return e.v end
  return nil
end

--- Test whether a dictionary contains a key.
function M.dict_has(d, key) return M.dict_entry(d, key) ~= nil end

--- Delete an entry using a precomputed hash key and return its value.
function M.dict_del_hk(d, hk)
  local e = d.map[hk]
  if not e then return nil end
  d.map[hk] = nil
  d.size = d.size - 1
  d.dead = d.dead + 1
  if d.dead > 8 and d.dead * 2 > #d.keys then compact(d) end
  return e.v
end

--- Delete a dictionary entry and return its value, or nil.
function M.dict_del(d, key)
  local slot = find_slot(d, M.hash_key(key), key)
  return M.dict_del_hk(d, slot)
end

--- Array of live entries {k=,v=,pos=} in insertion order.
function M.dict_entries(d)
  local out = {}
  for i = 1, #d.keys do
    local hk = d.keys[i]
    local e = d.map[hk]
    if e and e.pos == i then out[#out + 1] = e end
  end
  return out
end

--- Make a shallow dictionary copy.
function M.dict_copy(d)
  local out = M.new_dict()
  out.slow_keys = d.slow_keys
  for i = 1, #d.keys do
    local hk = d.keys[i]
    local e = d.map[hk]
    if e and e.pos == i then
      local pos = #out.keys + 1
      out.keys[pos] = hk
      out.map[hk] = { k = e.k, v = e.v, pos = pos, hk = e.hk, hash = e.hash }
      out.size = out.size + 1
    end
  end
  return out
end

--- Remove all dictionary entries.
function M.dict_clear(d)
  d.keys = {}
  d.map = {}
  d.size = 0
  d.dead = 0
  d.slow_keys = nil
end

--- Insert a set element using a precomputed hash key.
function M.set_add_hk(s, hk, val)
  local slot, entry, hash = find_slot(s, hk, val)
  if entry == nil then
    local pos = #s.keys + 1
    s.keys[pos] = slot
    s.map[slot] = { v = val, pos = pos, hk = hk, hash = hash }
    if hk:sub(1, 1) == "h" then s.slow_keys = true end
    s.size = s.size + 1
  end
end

--- Insert a set element.
function M.set_add(s, val) M.set_add_hk(s, M.hash_key(val), val) end

--- Test whether a set contains a value.
function M.set_has(s, val)
  local _, entry = find_slot(s, M.hash_key(val), val)
  return entry ~= nil
end

--- Test set membership using a previously computed hash key.
function M.set_has_hk(s, hk, val)
  local _, entry = find_slot(s, hk, val)
  return entry ~= nil
end

--- Remove a set element, optionally reusing its previously computed hash key.
function M.set_discard(s, val, hashed)
  local hk, e = find_slot(s, hashed or M.hash_key(val), val)
  if not e then return false end
  s.map[hk] = nil
  s.size = s.size - 1
  s.dead = s.dead + 1
  if s.dead > 8 and s.dead * 2 > #s.keys then compact(s) end
  return true
end

--- Remove an arbitrary set element without hashing it again.
function M.set_pop(s)
  local slot, entry = next(s.map)
  if entry == nil then M.throw("KeyError", "pop from an empty set") end
  s.map[slot] = nil
  s.size = s.size - 1
  s.dead = s.dead + 1
  if s.dead > 8 and s.dead * 2 > #s.keys then compact(s) end
  return entry.v
end

--- Array of live elements in insertion order.
function M.set_elems(s)
  local out = {}
  for i = 1, #s.keys do
    local hk = s.keys[i]
    local e = s.map[hk]
    if e and e.pos == i then out[#out + 1] = e.v end
  end
  return out
end

--- Array of live {hk, v} pairs in insertion order.
function M.set_pairs(s)
  local out = {}
  for i = 1, #s.keys do
    local hk = s.keys[i]
    local e = s.map[hk]
    if e and e.pos == i then out[#out + 1] = { e.hk or hk, e.v } end
  end
  return out
end

--- Make a shallow set copy.
function M.set_copy(s)
  local out = M.new_set(s.frozen)
  out.slow_keys = s.slow_keys
  out.hash = s.hash
  for i = 1, #s.keys do
    local hk = s.keys[i]
    local e = s.map[hk]
    if e and e.pos == i then
      local pos = #out.keys + 1
      out.keys[pos] = hk
      out.map[hk] = { v = e.v, pos = pos, hk = e.hk, hash = e.hash }
      out.size = out.size + 1
    end
  end
  return out
end

--- Remove all set elements.
function M.set_clear(s)
  s.keys = {}
  s.map = {}
  s.size = 0
  s.dead = 0
  s.slow_keys = nil
end

-- ---------------------------------------------------------------------------
-- classes

local OBJECT -- forward

local function c3_mro(cls)
  local seqs = { { cls } }
  for i = 1, #cls.bases do
    local m = {}
    local bmro = cls.bases[i].mro
    for j = 1, #bmro do m[j] = bmro[j] end
    seqs[#seqs + 1] = m
  end
  local basecopy = {}
  for i = 1, #cls.bases do basecopy[i] = cls.bases[i] end
  seqs[#seqs + 1] = basecopy

  local out = {}
  local live = {}
  for _, s in ipairs(seqs) do
    if #s > 0 then live[#live + 1] = s end
  end
  while #live > 0 do
    local head = nil
    for _, seq in ipairs(live) do
      local cand = seq[1]
      local in_tail = false
      for _, s in ipairs(live) do
        for j = 2, #s do
          if s[j] == cand then
            in_tail = true
            break
          end
        end
        if in_tail then break end
      end
      if not in_tail then
        head = cand
        break
      end
    end
    if head == nil then
      M.throw_val(M.make_exc("TypeError", "cannot create a consistent MRO"))
    end
    out[#out + 1] = head
    for _, seq in ipairs(live) do
      if seq[1] == head then table.remove(seq, 1) end
    end
    local nl = {}
    for _, s in ipairs(live) do
      if #s > 0 then nl[#nl + 1] = s end
    end
    live = nl
  end
  return out
end

--- bases may contain a btype (builtin base: dict/list/str/...); it is recorded
-- as `prim` and dropped from the diaspore base list. Instances of such a class
-- carry an underlying primitive value that unoverridden operations act on.
function M.new_class(name, bases, ns, prim)
  if ns.__eq__ ~= nil and ns.__hash__ == nil then ns.__hash__ = M.NONE end
  local diaspore_bases = {}
  for i = 1, #bases do
    if type(bases[i]) == "table" and bases[i].kind == "class" then
      diaspore_bases[#diaspore_bases + 1] = bases[i]
    end
  end
  if prim == nil then
    for i = 1, #diaspore_bases do
      if diaspore_bases[i].prim ~= nil then
        prim = diaspore_bases[i].prim
        break
      end
    end
  end
  for i = 1, #bases do
    if not (type(bases[i]) == "table" and bases[i].kind == "class") then
      prim = bases[i]
    end
  end
  local cls = { kind = "class", name = name, bases = diaspore_bases,
    declared_bases = bases, ns = ns, prim = prim }
  cls.mro = c3_mro(cls)
  if prim ~= nil then
    local present = false
    for _, base in ipairs(cls.mro) do if base == prim then present = true end end
    if not present then
      local position = #cls.mro + 1
      for i, base in ipairs(cls.mro) do if base == OBJECT then position = i; break end end
      table.insert(cls.mro, position, prim)
    end
  end
  if OBJECT ~= nil then
    local found = false
    for _, c in ipairs(cls.mro) do
      if c == OBJECT then
        found = true
        break
      end
    end
    if not found then cls.mro[#cls.mro + 1] = OBJECT end
  end
  return cls
end

--- Resolve an attribute through the class method resolution order.
function M.class_lookup(cls, n)
  local mro = cls.mro
  for i = 1, #mro do
    local v = mro[i].ns[n]
    if v ~= nil then return v end
  end
  return nil
end

--- Test whether a class inherits from another class.
function M.issub(cls, other)
  local mro = cls.mro or { cls }
  for i = 1, #mro do
    if mro[i] == other then return true end
  end
  return false
end

local SYNTAX_FIELDS = { "msg", "filename", "lineno", "offset", "text",
  "end_lineno", "end_offset", "print_file_and_line" }

--- Create an instance with the defaults required by its native base layout.
function M.new_instance(cls)
  local d = {}
  if M.BASE_EXC ~= nil and M.issub(cls, M.BASE_EXC) then
    d.args = M.tuple({})
    d.__traceback__, d.__cause__, d.__context__ = M.NONE, M.NONE, M.NONE
    d.__suppress_context__ = false
    if M.issub(cls, M.EXC_CLASSES.SyntaxError) then
      for _, name in ipairs(SYNTAX_FIELDS) do d[name] = M.NONE end
    end
    if M.issub(cls, M.EXC_CLASSES.SystemExit) then d.code = M.NONE end
  end
  return { kind = "instance", cls = cls, d = d }
end

-- ---------------------------------------------------------------------------
-- builtin exception hierarchy

local EXC_TREE = {
  { "BaseException", nil },
  { "SystemExit", "BaseException" },
  { "KeyboardInterrupt", "BaseException" },
  { "GeneratorExit", "BaseException" },
  { "BaseExceptionGroup", "BaseException" },
  { "Exception", "BaseException" },
  { "ArithmeticError", "Exception" },
  { "ZeroDivisionError", "ArithmeticError" },
  { "OverflowError", "ArithmeticError" },
  { "AssertionError", "Exception" },
  { "AttributeError", "Exception" },
  { "BufferError", "Exception" },
  { "EOFError", "Exception" },
  { "ImportError", "Exception" },
  { "ModuleNotFoundError", "ImportError" },
  { "LookupError", "Exception" },
  { "IndexError", "LookupError" },
  { "KeyError", "LookupError" },
  { "NameError", "Exception" },
  { "UnboundLocalError", "NameError" },
  { "OSError", "Exception" },
  { "PermissionError", "OSError" },
  { "RuntimeError", "Exception" },
  { "NotImplementedError", "RuntimeError" },
  { "RecursionError", "RuntimeError" },
  { "StopIteration", "Exception" },
  { "StopAsyncIteration", "Exception" },
  { "TypeError", "Exception" },
  { "ValueError", "Exception" },
  { "SyntaxError", "Exception" },
  { "IndentationError", "SyntaxError" },
  { "TabError", "IndentationError" },
  { "SystemError", "Exception" },
  { "ReferenceError", "Exception" },
  { "BufferError", "Exception" },
  { "MemoryError", "Exception" },
  { "FloatingPointError", "ArithmeticError" },
  { "UnicodeError", "ValueError" },
  { "UnicodeDecodeError", "UnicodeError" },
  { "UnicodeEncodeError", "UnicodeError" },
  { "UnicodeTranslateError", "UnicodeError" },
  { "BlockingIOError", "OSError" },
  { "ChildProcessError", "OSError" },
  { "ConnectionError", "OSError" },
  { "BrokenPipeError", "ConnectionError" },
  { "ConnectionResetError", "ConnectionError" },
  { "ConnectionAbortedError", "ConnectionError" },
  { "ConnectionRefusedError", "ConnectionError" },
  { "FileExistsError", "OSError" },
  { "FileNotFoundError", "OSError" },
  { "InterruptedError", "OSError" },
  { "IsADirectoryError", "OSError" },
  { "NotADirectoryError", "OSError" },
  { "ProcessLookupError", "OSError" },
  { "TimeoutError", "OSError" },
  { "Warning", "Exception" },
  { "UserWarning", "Warning" },
  { "DeprecationWarning", "Warning" },
  { "PendingDeprecationWarning", "Warning" },
  { "SyntaxWarning", "Warning" },
  { "RuntimeWarning", "Warning" },
  { "FutureWarning", "Warning" },
  { "ImportWarning", "Warning" },
  { "UnicodeWarning", "Warning" },
  { "BytesWarning", "Warning" },
  { "ResourceWarning", "Warning" },
  { "EncodingWarning", "Warning" },
}

OBJECT = M.new_class("object", {}, {})
M.OBJECT = OBJECT

M.EXC_CLASSES = {}
M.EXC_ORDER = {}
for _, pair in ipairs(EXC_TREE) do
  local name, parent = pair[1], pair[2]
  local bases = {}
  if parent then bases[1] = M.EXC_CLASSES[parent] end
  local cls = M.new_class(name, bases, {})
  M.EXC_CLASSES[name] = cls
  M.EXC_ORDER[#M.EXC_ORDER + 1] = name
end

-- ExceptionGroup inherits from both BaseExceptionGroup and Exception (C3).
M.EXC_CLASSES.ExceptionGroup = M.new_class("ExceptionGroup",
  { M.EXC_CLASSES.BaseExceptionGroup, M.EXC_CLASSES.Exception }, {})
M.EXC_ORDER[#M.EXC_ORDER + 1] = "ExceptionGroup"

M.EXC_CLASSES.BaseException.ns.__init__ = M.builtin("__init__", function(_I, args, _kw)
  local self = args[1]
  local rest = {}
  for i = 2, #args do rest[i - 1] = args[i] end
  self.d.args = M.tuple(rest)
  if M.issub(self.cls, M.EXC_CLASSES.UnicodeDecodeError)
      or M.issub(self.cls, M.EXC_CLASSES.UnicodeEncodeError) then
    if #rest ~= 5 then M.throw("TypeError", self.cls.name .. " requires five arguments") end
    self.d.encoding, self.d.object, self.d.start, self.d["end"], self.d.reason = table.unpack(rest)
  elseif M.issub(self.cls, M.EXC_CLASSES.UnicodeTranslateError) then
    if #rest ~= 4 then M.throw("TypeError", "UnicodeTranslateError requires four arguments") end
    self.d.object, self.d.start, self.d["end"], self.d.reason = table.unpack(rest)
  end
  return M.NONE
end)

M.EXC_CLASSES.SyntaxError.ns.__init__ = M.builtin("__init__", function(I, args, kw)
  local self = args[1]
  if self == nil then M.throw("TypeError", "descriptor '__init__' of 'SyntaxError' object needs an argument") end
  if type(self) ~= "table" or self.kind ~= "instance" or not M.issub(self.cls, M.EXC_CLASSES.SyntaxError) then
    M.throw("TypeError", "descriptor '__init__' requires a 'SyntaxError' object but received a '" .. M.type_name(self) .. "'")
  end
  if #kw > 0 then M.throw("TypeError", M.type_name(args[1]) .. "() takes no keyword arguments") end
  M.EXC_CLASSES.BaseException.ns.__init__.fn(I, args, kw)
  if #args >= 2 then self.d.msg = args[2] end
  if #args == 3 then
    local details = require("diaspore.iter").iter_to_array(I, args[3])
    self.d.end_lineno, self.d.end_offset = M.NONE, M.NONE
    if #details < 4 then M.throw("TypeError", "function takes at least 4 arguments (" .. #details .. " given)") end
    if #details > 6 then M.throw("TypeError", "function takes at most 6 arguments (" .. #details .. " given)") end
    self.d.filename, self.d.lineno, self.d.offset, self.d.text = details[1], details[2], details[3], details[4]
    if #details >= 5 then self.d.end_lineno = details[5] end
    if #details == 5 then M.throw("TypeError", "end_offset must be provided when end_lineno is provided") end
    if #details == 6 then self.d.end_offset = details[6] end
  end
  return M.NONE
end, "wrapper_descriptor")

local function init_system_exit_code(self)
  local args = self.d.args.items
  if #args == 1 then self.d.code = args[1]
  elseif #args > 1 then self.d.code = self.d.args end
end

M.EXC_CLASSES.SystemExit.ns.__init__ = M.builtin("__init__", function(I, args, kw)
  local self = args[1]
  if self == nil then M.throw("TypeError", "descriptor '__init__' of 'SystemExit' object needs an argument") end
  if type(self) ~= "table" or self.kind ~= "instance" or not M.issub(self.cls, M.EXC_CLASSES.SystemExit) then
    M.throw("TypeError", "descriptor '__init__' requires a 'SystemExit' object but received a '" .. M.type_name(self) .. "'")
  end
  if #kw > 0 then M.throw("TypeError", M.type_name(self) .. "() takes no keyword arguments") end
  M.EXC_CLASSES.BaseException.ns.__init__.fn(I, args, kw)
  init_system_exit_code(self)
  return M.NONE
end, "wrapper_descriptor")

M.BASE_EXC = M.EXC_CLASSES.BaseException

local function exception_method_args(name, args, kw)
  local self = args[1]
  if self == nil then
    M.throw("TypeError", "descriptor '" .. name .. "' of 'BaseException' object needs an argument")
  end
  if type(self) ~= "table" or self.kind ~= "instance" or not M.issub(self.cls, M.BASE_EXC) then
    M.throw("TypeError", "descriptor '" .. name .. "' for 'BaseException' objects doesn't apply to a '"
      .. M.type_name(self) .. "' object")
  end
  if #kw > 0 then M.throw("TypeError", "BaseException." .. name .. "() takes no keyword arguments") end
  if #args ~= 2 then
    M.throw("TypeError", "BaseException." .. name .. "() takes exactly one argument (" .. (#args - 1) .. " given)")
  end
  return self, args[2]
end

M.BASE_EXC.ns.add_note = M.builtin("add_note", function(I, args, kw)
  local self, note = exception_method_args("add_note", args, kw)
  if type(I:raw(note)) ~= "string" then
    M.throw("TypeError", "note must be a str, not '" .. M.type_name(note) .. "'")
  end
  local saved = #I.stack
  local ok, notes = pcall(I.get_attr, I, self, "__notes__")
  if not ok then
    if not M.is_exc(notes) or not M.exc_isa(notes.value, "AttributeError") then error(notes, 0) end
    I:trim(saved)
    notes = M.list({})
    I:set_attr(self, "__notes__", notes)
  end
  local raw = I:raw(notes)
  if type(raw) ~= "table" or raw.kind ~= "list" then
    M.throw("TypeError", "Cannot add note: __notes__ is not a list")
  end
  raw.items[#raw.items + 1] = note
  return M.NONE
end, "method_descriptor")

M.BASE_EXC.ns.with_traceback = M.builtin("with_traceback", function(_I, args, kw)
  local self, tb = exception_method_args("with_traceback", args, kw)
  if tb ~= M.NONE and not (type(tb) == "table" and tb.kind == "traceback") then
    M.throw("TypeError", "__traceback__ must be a traceback or None")
  end
  self.d.__traceback__ = tb
  return self
end, "method_descriptor")

--- Build an ExceptionGroup / BaseExceptionGroup instance from a message + list.
function M.make_group(message, excs, base)
  local name = "ExceptionGroup"
  if base then
    for _, e in ipairs(excs) do
      if not (type(e) == "table" and e.kind == "instance"
          and M.issub(e.cls, M.EXC_CLASSES.Exception)) then
        name = "BaseExceptionGroup"
        break
      end
    end
  end
  local inst = M.new_instance(M.EXC_CLASSES[name])
  local items = {}
  for i = 1, #excs do items[i] = excs[i] end
  inst.d.args = M.tuple({ message, M.list(items) })
  inst.d.message = message
  inst.d.exceptions = M.tuple(items)
  return inst
end

M.EXC_CLASSES.BaseExceptionGroup.ns.__init__ = M.builtin("__init__", function(_I, args, _kw)
  local self = args[1]
  if #args ~= 3 then
    M.throw("TypeError", "BaseExceptionGroup() takes exactly 2 arguments")
  end
  local message, excs = args[2], args[3]
  if type(message) ~= "string" then
    M.throw("TypeError", "argument 1 must be str, not " .. M.type_name(message))
  end
  local items = {}
  if type(excs) == "table" and (excs.kind == "list" or excs.kind == "tuple") then
    for i = 1, #excs.items do items[i] = excs.items[i] end
  else
    M.throw("TypeError", "second argument (exceptions) must be a sequence")
  end
  if #items == 0 then
    M.throw("ValueError", "second argument (exceptions) must be a non-empty sequence")
  end
  for _, e in ipairs(items) do
    if not (type(e) == "table" and e.kind == "instance"
        and M.issub(e.cls, M.EXC_CLASSES.BaseException)) then
      M.throw("ValueError", "second argument (exceptions) must be a sequence of exceptions")
    end
  end
  self.d.args = M.tuple({ message, M.list(items) })
  self.d.message = message
  self.d.exceptions = M.tuple(items)
  return M.NONE
end)

--- Construct an exception with positional arguments.
function M.make_exc(name, ...)
  local cls = M.EXC_CLASSES[name] or M.EXC_CLASSES.RuntimeError
  local inst = M.new_instance(cls)
  inst.d.args = M.tuple({ ... })
  if M.issub(cls, M.EXC_CLASSES.SystemExit) then init_system_exit_code(inst) end
  return inst
end

--- Raise a diaspore exception (name from the builtin hierarchy).
function M.throw(name, msg)
  error(M.exc_err(M.make_exc(name, msg)), 0)
end

--- Return the positional arguments stored on an exception.
function M.exc_args(inst)
  local a = inst.d.args
  if a ~= nil and a.kind == "tuple" then return a.items end
  return {}
end

--- Test whether an exception belongs to a named exception class.
function M.exc_isa(v, name)
  local cls = M.EXC_CLASSES[name]
  return type(v) == "table" and v.kind == "instance" and cls ~= nil and M.issub(v.cls, cls)
end

-- ---------------------------------------------------------------------------
-- UTF-8 code-point helpers (diaspore str = Lua string holding UTF-8)

--- Count UTF-8 code points.
function M.str_len(s)
  return utf8.len(s, 1, -1, true) or #s
end

local chars_key, chars_val = nil, nil

--- Array of code-point substrings (cached for the most recent string).
function M.chars(s)
  if s == chars_key then return chars_val end
  local out = {}
  if not s:find("[\128-\255]") then
    for i = 1, #s do out[i] = s:sub(i, i) end
  else
    local i = 1
    local n = #s
    while i <= n do
      local nxt = utf8.offset(s, 2, i)
      if nxt == nil then nxt = n + 1 end
      out[#out + 1] = s:sub(i, nxt - 1)
      i = nxt
    end
  end
  chars_key = s
  chars_val = out
  return out
end

--- Test whether every byte is ASCII.
function M.is_ascii(s)
  return not s:find("[\128-\255]")
end

--- Python-style string comparison: by code points (UTF-8 byte order == code
-- point order, so plain Lua string comparison is exact).
function M.str_cmp(a, b)
  if a < b then return -1 end
  if a > b then return 1 end
  return 0
end

return M
