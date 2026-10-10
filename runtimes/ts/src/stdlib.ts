/* Content-addressed compiled stdlib storage; no runtime source lookup. */

import { readFileSync } from "node:fs";
import { bundleDigest, maybeGunzip } from "./flow.ts";
import { fromHost } from "./host.ts";
import { bi } from "./builtins.ts";
import { argCount, noKw } from "./helpers.ts";
import {
  type G, type Value, DiasporeInstance, DiasporeList, NativeModule,
  NONE, diasporeErr, isPyString, strCmp, strKey, typeName,
} from "./values.ts";

/** Immutable compiled modules shipped with a runtime or supplied by an embedder. */
export interface StdlibStore {
  readonly diaspore_stdlib: 1;
  readonly id: string;
  readonly modules: Readonly<Record<string, Record<string, any>>>;
}

const STORE_URL = new URL("../stdlib.json.gz", import.meta.url);
const validatedStores = new WeakSet<object>();
const moduleSizes = new WeakMap<object, number>();
let installedStore: StdlibStore | undefined;

function freezeJSON(value: unknown, sizes: WeakMap<object, number>): number {
  if (value === null || typeof value === "string" || typeof value === "boolean") return 1;
  if (typeof value === "number" && Number.isFinite(value)) return 1;
  if (typeof value !== "object" || value === null ||
      (!Array.isArray(value) && Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null)) {
    throw new Error("invalid Diaspore stdlib store: module IR must contain only JSON values");
  }
  const previous = sizes.get(value);
  if (previous !== undefined) {
    if (previous < 0) throw new Error("invalid Diaspore stdlib store: cyclic module IR");
    return previous;
  }
  sizes.set(value, -1);
  let size = 1;
  for (const child of Object.values(value)) size += freezeJSON(child, sizes);
  Object.freeze(value);
  sizes.set(value, size);
  return size;
}

/** Validate the store's canonical content id and freeze its shared module IR. */
export function validateStdlibStore(value: unknown): StdlibStore {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("invalid Diaspore stdlib store: expected an object");
  }
  if (validatedStores.has(value)) return value as StdlibStore;
  const store = value as Record<string, unknown>;
  if (store.diaspore_stdlib !== 1 || typeof store.id !== "string" || !/^sha256:[0-9a-f]{64}$/.test(store.id)) {
    throw new Error("invalid Diaspore stdlib store: unsupported version or content id");
  }
  const modules = store.modules;
  if (typeof modules !== "object" || modules === null || Array.isArray(modules) ||
      (Object.getPrototypeOf(modules) !== Object.prototype && Object.getPrototypeOf(modules) !== null)) {
    throw new Error("invalid Diaspore stdlib store: modules must be an object");
  }
  const sizes = new WeakMap<object, number>();
  for (const [name, module] of Object.entries(modules)) {
    if (typeof module !== "object" || module === null || Array.isArray(module) ||
        module.name !== name || typeof module.file !== "string" || !Array.isArray(module.body)) {
      throw new Error(`invalid Diaspore stdlib store: invalid module ${name}`);
    }
    moduleSizes.set(module, freezeJSON(module, sizes));
  }
  Object.freeze(modules);
  const actual = "sha256:" + bundleDigest({ diaspore_stdlib: 1, modules });
  if (store.id !== actual) {
    throw new Error(`Diaspore stdlib content id mismatch: store declares ${store.id}, content is ${actual}`);
  }
  Object.freeze(store);
  validatedStores.add(store);
  return store as unknown as StdlibStore;
}

/** Lazily load and cache the installed compiled stdlib artifact. */
export function loadStdlibStore(): StdlibStore {
  if (installedStore !== undefined) return installedStore;
  try {
    const bytes = maybeGunzip(readFileSync(STORE_URL));
    installedStore = validateStdlibStore(JSON.parse(bytes.toString("utf8")));
    return installedStore;
  } catch (error) {
    throw new Error("cannot load installed Diaspore stdlib store: " +
      (error instanceof Error ? error.message : String(error)), { cause: error });
  }
}

/** Expose only immutable installed code, copied into sandbox containers on demand. */
export function makeStdlibModule(getStore: () => StdlibStore = loadStdlibStore): NativeModule {
  function store(): StdlibStore {
    try {
      return validateStdlibStore(getStore());
    } catch (error) {
      throw diasporeErr("RuntimeError", error instanceof Error ? error.message : String(error));
    }
  }

  return new NativeModule("_diaspore_stdlib", new Map<string, Value>([
    ["get_id", bi("get_id", function* (_I, args, kw): G<Value> {
      noKw("get_id", kw);
      argCount("get_id", args, 0, 0);
      return store().id;
    })],
    ["module_names", bi("module_names", function* (I, args, kw): G<Value> {
      noKw("module_names", kw);
      argCount("module_names", args, 0, 0);
      const names = Object.keys(store().modules).sort(strCmp);
      I.fuelN(names.length);
      return new DiasporeList(names);
    })],
    ["get_module", bi("get_module", function* (I, args, kw): G<Value> {
      noKw("get_module", kw);
      argCount("get_module", args, 1, 1);
      const value = args[0] instanceof DiasporeInstance && args[0].prim !== null ? args[0].prim : args[0];
      if (!isPyString(value)) throw diasporeErr("TypeError", `module name must be str, not ${typeName(args[0])}`);
      const modules = store().modules;
      const name = strKey(value);
      if (!Object.hasOwn(modules, name)) return NONE;
      const module = modules[name];
      I.fuelN(moduleSizes.get(module)!);
      return fromHost(module);
    })],
  ]));
}
