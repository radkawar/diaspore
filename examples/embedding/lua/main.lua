package.path = "runtimes/lua/?.lua;runtimes/lua/?/init.lua;" .. package.path
local diaspore = require("diaspore")
local customers = {
    acme = { tier = "enterprise", credit = 100000 },
    startup = { tier = "pro", credit = 5000 },
    anon = { tier = "free", credit = 100 },
}
local vm = diaspore.new(diaspore.load(arg[1]), {
    bindings = { host = {
        customer_tier = function(name) return customers[name].tier end,
        credit_limit = function(name) return customers[name].credit end,
    } },
    max_steps = 100000,
})
assert(vm:run() == 0, "policy initialization failed")
for _, input in ipairs({
    { "acme", 100, 9.99, true },
    { "startup", 3, 49.0, false },
    { "anon", 5, 2.0, false },
}) do
    local order = {
        customer = input[1],
        items = { { qty = input[2], unit = input[3] } },
        rush = input[4],
    }
    local price = vm:call("plugin", "price", order)
    local risk = vm:call("plugin", "risk_score", order)
    print(string.format("%s: total=%.2f risk=%d", input[1], price, risk))
end
