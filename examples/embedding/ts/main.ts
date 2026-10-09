import { readFileSync } from "node:fs";
import { Interp, loadBundle } from "../../../runtimes/ts/src/index.ts";

const customers = {
  acme: { tier: "enterprise", credit: 100_000 },
  startup: { tier: "pro", credit: 5_000 },
  anon: { tier: "free", credit: 100 },
};
const vm = new Interp(loadBundle(readFileSync(process.argv[2])), {
  bindings: { host: {
    customer_tier: (name) => customers[String(name) as keyof typeof customers].tier,
    credit_limit: (name) => customers[String(name) as keyof typeof customers].credit,
  } },
  maxSteps: 100_000,
});
if (vm.run() !== 0) throw new Error("policy initialization failed");
for (const [name, qty, unit, rush] of [
  ["acme", 100, 9.99, true],
  ["startup", 3, 49.0, false],
  ["anon", 5, 2.0, false],
] as const) {
  const order = { customer: name, items: [{ qty, unit }], rush };
  const price = Number(vm.call("plugin", "price", order));
  const risk = vm.call("plugin", "risk_score", order);
  console.log(`${name}: total=${price.toFixed(2)} risk=${risk}`);
}
