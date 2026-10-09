import { type Value, NONE, PyNoneT, DiasporeList, DiasporeTuple, DiasporeDict, diasporeErr, typeName } from "./values.ts";

/** Convert a host value to a sandbox value without retaining mutable host containers. */
export function fromHost(value: unknown): Value {
  if (value === null || value === undefined) return NONE;
  if (typeof value === "string" || typeof value === "boolean" || typeof value === "bigint") return value;
  if (typeof value === "number") return Number.isSafeInteger(value) ? BigInt(value) : value;
  if (Array.isArray(value)) return new DiasporeList(value.map(fromHost));
  if (typeof value === "object" && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null)) {
    const result = new DiasporeDict();
    for (const [key, item] of Object.entries(value)) result.setHK("s" + key, key, fromHost(item));
    return result;
  }
  throw diasporeErr("TypeError", "unsupported host value");
}

/** Convert sandbox containers to host values, preserving integers outside the safe number range. */
export function toHost(value: Value): unknown {
  if (value instanceof PyNoneT) return null;
  if (typeof value === "bigint") return value >= BigInt(Number.MIN_SAFE_INTEGER) && value <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(value) : value;
  if (typeof value === "string" || typeof value === "boolean" || typeof value === "number") return value;
  if (value instanceof DiasporeList || value instanceof DiasporeTuple) return value.items.map(toHost);
  if (value instanceof DiasporeDict) {
    const result: Record<string, unknown> = {};
    for (const [key, item] of value.entries()) {
      if (typeof key !== "string") throw diasporeErr("TypeError", "host dictionaries must have str keys");
      Object.defineProperty(result, key, { value: toHost(item), enumerable: true, writable: true, configurable: true });
    }
    return result;
  }
  throw diasporeErr("TypeError", `unsupported sandbox value: ${typeName(value)}`);
}
