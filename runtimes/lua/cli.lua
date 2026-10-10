-- diaspore-lua CLI: compile Python sources and execute JSON bundles on Lua 5.4.
--
-- Usage:
--   lua5.4 cli.lua build <entry.py> -o <bundle.dsp.json> [--path DIR] [--self-contained]
--   lua5.4 cli.lua run <bundle.dsp.json> [prog-args...]
--          [--allow-net [SPEC]] [--allow-fs [SPEC]] [--allow-spawn]
--          [--max-steps N] [--max-depth N] [--deterministic]
--
-- Limitations: no tasks / net, gzip bundles, DSPB bundles, or durable flows.
-- See runtimes/lua/README.md.

local script = arg[0] or "cli.lua"
local dir = script:match("^(.*)[/\\][^/\\]*$") or "."
package.path = dir .. "/?.lua;" .. dir .. "/?/init.lua;" .. package.path

local values = require("diaspore.values")
local json = require("diaspore.json")
local interp = require("diaspore.interp")

local function usage()
  io.stderr:write(
    "usage: diaspore-lua build <entry.py> -o <bundle.dsp.json> [--path DIR] [--self-contained]\n"
    .. "       diaspore-lua run <bundle.dsp.json> [prog-args...] [--allow-net [SPEC]] [--allow-fs [SPEC]]\n"
    .. "                [--allow-spawn] [--max-steps N] [--max-depth N] [--deterministic]\n")
  os.exit(2, true)
end

local function die(msg)
  io.stderr:write("diaspore: " .. msg .. "\n")
  os.exit(2, true)
end

local function read_bundle(path)
  local f, msg = io.open(path, "rb")
  if f == nil then die(tostring(msg)) end
  local data = f:read("a")
  f:close()
  if #data >= 2 and data:byte(1) == 0x1F and data:byte(2) == 0x8B then
    die("gzip bundles are not supported by diaspore-lua; pass plain JSON")
  end
  if data:sub(1, 6) == "DSPB01" or data:sub(1, 6) == "DSPB02" then
    die("DSPB bundles are not supported by diaspore-lua; pass plain JSON")
  end
  local ok, bundle = pcall(json.parse, data)
  if not ok then
    local msg2 = "invalid bundle JSON"
    if values.is_exc(bundle) then
      local args = values.exc_args(bundle.value)
      if #args > 0 and type(args[1]) == "string" then msg2 = args[1] end
    end
    die(msg2)
  end
  return bundle
end

local function spec_for(kinds, tok)
  if tok == "all" then return tok end
  for _, k in ipairs(kinds) do
    if tok:sub(1, #k + 1) == k .. ":" then return tok end
  end
  return nil
end

local function parse_args(rest)
  local a = {
    pos = {}, net_specs = {}, fs_specs = {}, allow_spawn = false,
    max_steps = 0, max_depth = 1000, deterministic = false,
  }
  local function need_int(i, name)
    local tok = rest[i]
    local eq = tok:find("=", 1, true)
    local raw
    if eq then
      raw = tok:sub(eq + 1)
    else
      i = i + 1
      raw = rest[i]
      if raw == nil then die(name .. " requires a value") end
    end
    local n = math.tointeger(tonumber(raw))
    if n == nil or n < 0 then die(name .. " requires a non-negative integer") end
    return n, i
  end
  local function cap_spec(i, flag, kinds, out)
    local tok = rest[i]
    local spec = ""
    local eq = tok:find("=", 1, true)
    if eq then
      spec = tok:sub(eq + 1)
    elseif rest[i + 1] ~= nil then
      local s = spec_for(kinds, rest[i + 1])
      if s ~= nil then
        spec = s
        i = i + 1
      end
    end
    if spec ~= "" and spec ~= "all" then
      local cut = spec:find(":", 1, true)
      local kind = cut and spec:sub(1, cut - 1) or spec
      local addr = cut and spec:sub(cut + 1) or ""
      local ok = false
      for _, k in ipairs(kinds) do
        if kind == k then ok = true end
      end
      if not ok or addr == "" then
        die('bad ' .. flag .. ' spec "' .. spec .. '"')
      end
    end
    if spec == "all" then spec = "" end
    out[#out + 1] = spec
    return i
  end
  local i = 1
  while i <= #rest do
    local tok = rest[i]
    if tok == "--" then
      for j = i + 1, #rest do a.pos[#a.pos + 1] = rest[j] end
      break
    elseif tok == "--allow-net" or tok:sub(1, 12) == "--allow-net=" then
      i = cap_spec(i, "--allow-net", { "listen", "connect" }, a.net_specs)
    elseif tok == "--allow-fs" or tok:sub(1, 11) == "--allow-fs=" then
      i = cap_spec(i, "--allow-fs", { "read", "write" }, a.fs_specs)
    elseif tok == "--allow-spawn" then
      a.allow_spawn = true
    elseif tok == "--deterministic" then
      a.deterministic = true
    elseif tok == "--max-steps" or tok:sub(1, 12) == "--max-steps=" then
      a.max_steps, i = need_int(i, "--max-steps")
    elseif tok == "--max-depth" or tok:sub(1, 12) == "--max-depth=" then
      a.max_depth, i = need_int(i, "--max-depth")
    elseif tok == "--durable" or tok == "--snapshot" or tok == "--send" then
      die("diaspore-lua does not support durable flows")
    elseif tok == "--no-validate" then
      -- schema validation is a diaspore-py concern; accepted for CLI parity
    elseif tok:sub(1, 2) == "--" then
      die("unknown flag " .. tok)
    else
      a.pos[#a.pos + 1] = tok
    end
    i = i + 1
  end
  return a
end

local function caps_and_policy(a)
  local caps = {}
  local policy = {}
  local have_policy = false
  if #a.net_specs > 0 then
    caps.net = true
    local scoped = { listen = {}, connect = {} }
    local all_scoped = true
    for _, s in ipairs(a.net_specs) do
      if s == "" then
        all_scoped = false
      else
        local cut = s:find(":", 1, true)
        local kind = s:sub(1, cut - 1)
        table.insert(scoped[kind], s:sub(cut + 1))
      end
    end
    if all_scoped then
      policy.net = scoped
      have_policy = true
    end
  end
  if #a.fs_specs > 0 then
    caps.fs = true
    local scoped = { read = {}, write = {} }
    local all_scoped = true
    for _, s in ipairs(a.fs_specs) do
      if s == "" then
        all_scoped = false
      else
        local cut = s:find(":", 1, true)
        local kind = s:sub(1, cut - 1)
        table.insert(scoped[kind], s:sub(cut + 1))
      end
    end
    if all_scoped then
      policy.fs = scoped
      have_policy = true
    end
  end
  if a.allow_spawn then caps.spawn = true end
  if not have_policy then policy = nil end
  return caps, policy
end


local function cmd_run(rest)
  local a = parse_args(rest)
  if #a.pos == 0 then usage() end
  local bundle = read_bundle(a.pos[1])
  local caps, policy = caps_and_policy(a)
  if a.deterministic and next(caps) ~= nil then
    io.stderr:write("diaspore: deterministic mode forbids capabilities\n")
    return 2
  end
  local ok, I = pcall(interp.new, bundle, {
    argv = a.pos,
    caps = caps,
    policy = policy,
    max_steps = a.max_steps,
    max_depth = a.max_depth,
    deterministic = a.deterministic,
  })
  if not ok then
    if values.is_exc(I) then
      local args = values.exc_args(I.value)
      I = args[1] or values.type_name(I.value)
    end
    io.stderr:write("diaspore: " .. tostring(I) .. "\n")
    return 2
  end
  local code = I:run()
  local closed, err = pcall(I.close, I)
  if not closed then
    if values.is_exc(err) then I:ewrite(I:str(err.value) .. "\n")
    else io.stderr:write("diaspore: error during shutdown\n") end
    if code == 0 then code = 1 end
  end
  return code
end

local function cmd_build(rest)
  local entry, output
  local opts = { search_paths = {}, self_contained = false }
  local i = 1
  while i <= #rest do
    local tok = rest[i]
    if tok == "--self-contained" then
      opts.self_contained = true
    elseif tok == "-o" or tok == "--output" or tok == "--path" then
      i = i + 1
      if rest[i] == nil then die(tok .. " requires a value") end
      if tok == "--path" then opts.search_paths[#opts.search_paths + 1] = rest[i]
      else output = rest[i] end
    elseif tok:sub(1, 7) == "--path=" then
      opts.search_paths[#opts.search_paths + 1] = tok:sub(8)
    elseif tok:sub(1, 9) == "--output=" then
      output = tok:sub(10)
    elseif tok:sub(1, 1) == "-" then
      die("unknown flag " .. tok)
    elseif entry ~= nil then
      die("build accepts one entry file")
    else
      entry = tok
    end
    i = i + 1
  end
  if entry == nil or output == nil then usage() end
  local ok, encoded = pcall(function()
    local bundle = require("diaspore.compiler").compile_file(entry, opts)
    return json.stringify_raw(bundle)
  end)
  if not ok then
    if values.is_exc(encoded) then
      local args = values.exc_args(encoded.value)
      encoded = args[1] or values.type_name(encoded.value)
    end
    die(tostring(encoded))
  end
  local file, message = io.open(output, "wb")
  if file == nil then die(tostring(message)) end
  local written, write_error = file:write(encoded, "\n")
  local closed, close_error = file:close()
  if not written then die(tostring(write_error)) end
  if not closed then die(tostring(close_error)) end
  return 0
end

local function main()
  io.stdout:setvbuf("full", 65536)
  local args = arg
  if #args == 0 then usage() end
  local code
  if args[1] == "run" or args[1] == "build" then
    local rest = {}
    for j = 2, #args do rest[#rest + 1] = args[j] end
    code = args[1] == "build" and cmd_build(rest) or cmd_run(rest)
  elseif args[1] == "resume" then
    die("diaspore-lua does not support durable flows")
  else
    usage()
  end
  io.stdout:flush()
  os.exit(code, true)
end

main()
