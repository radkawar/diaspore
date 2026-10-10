/* Content-addressed compiled stdlib storage; no runtime source lookup. */

import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { inflateRawSync } from "node:zlib";
import { bundleDigest } from "./flow.ts";
import { DSPB_MAGIC, dspbDecode } from "./dspb.ts";
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

const STORE_URL = new URL("../stdlib.dsps", import.meta.url);
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

function parseStore(bytes: Buffer): StdlibStore {
  const invalid = (message: string): never => {
    throw new Error("invalid Diaspore stdlib store: " + message);
  };
  if (bytes.length < 80 || !bytes.subarray(0, 6).equals(Buffer.from("DSPS02"))) invalid("invalid DSPS02 header");
  const indexLength = bytes.readUInt32LE(6), payloadStart = 42 + indexLength;
  if (indexLength < 38 || indexLength > 64 * 1024 * 1024 || payloadStart > bytes.length) invalid("invalid index length");
  const index = bytes.subarray(42, payloadStart);
  if (!createHash("sha256").update(index).digest().equals(bytes.subarray(10, 42))) invalid("index digest mismatch");
  const id = "sha256:" + index.subarray(0, 32).toString("hex");
  const dictionaryLength = index.readUInt16LE(32);
  if (dictionaryLength > 32768 || dictionaryLength > index.length - 38) invalid("invalid dictionary length");
  const dictionary = index.subarray(34, 34 + dictionaryLength);
  const count = index.readUInt32LE(34 + dictionaryLength);
  if (count > Math.floor((index.length - 38 - dictionaryLength) / 51)) invalid("invalid module count");
  const utf8 = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
  let cursor = 38 + dictionaryLength, end = 0, previous: string | undefined;
  const take = (length: number): Buffer => {
    if (length > index.length - cursor) invalid("truncated index record");
    const value = index.subarray(cursor, cursor + length);
    cursor += length;
    return value;
  };
  const text = (): string => {
    const length = take(2).readUInt16LE(0);
    const value = utf8.decode(take(length));
    if (!value || value.includes("\0")) invalid("invalid index text");
    return value;
  };
  const modules: Record<string, Record<string, any>> = Object.create(null);
  for (let i = 0; i < count; i++) {
    const name = text(), file = text(), record = take(45);
    if (!name.split(".").every(part => /^[\p{XID_Start}_][\p{XID_Continue}_]*$/u.test(part)) ||
        (previous !== undefined && strCmp(previous, name) >= 0)) invalid("invalid or unsorted module name");
    previous = name;
    const offset = record.readUInt32LE(0), storedLength = record.readUInt32LE(4);
    const decodedLength = record.readUInt32LE(8), codec = record[12];
    if (offset !== end || storedLength < 1 || storedLength > 256 * 1024 * 1024 ||
        decodedLength < 6 || decodedLength > 256 * 1024 * 1024 ||
        storedLength > bytes.length - payloadStart - offset ||
        (codec !== 0 && codec !== 1) || (codec === 0 && storedLength !== decodedLength)) {
      invalid("invalid payload span, length or codec");
    }
    end += storedLength;
    const digest = record.subarray(13);
    let cached: Record<string, any> | undefined;
    Object.defineProperty(modules, name, {
      enumerable: true,
      get() {
        if (cached !== undefined) return cached;
        const stored = bytes.subarray(payloadStart + offset, payloadStart + offset + storedLength);
        if (!createHash("sha256").update(stored).digest().equals(digest)) invalid(`payload digest mismatch for ${name}`);
        const decoded = codec === 0 ? stored : inflateRawSync(stored, { dictionary, maxOutputLength: decodedLength });
        if (decoded.length !== decodedLength || !decoded.subarray(0, 6).equals(DSPB_MAGIC)) {
          invalid(`invalid decoded payload for ${name}`);
        }
        const bundle = dspbDecode(decoded);
        const entries = bundle.modules as Record<string, Record<string, any>>;
        if (bundle.diaspore !== 1 || bundle.main !== name || Object.hasOwn(bundle, "stdlib") ||
            Object.keys(entries).length !== 1 || !Object.hasOwn(entries, name)) invalid(`invalid module bundle for ${name}`);
        const module = entries[name];
        if (module.name !== name || module.file !== file || !Array.isArray(module.body)) invalid(`invalid module IR for ${name}`);
        moduleSizes.set(module, freezeJSON(module, new WeakMap()));
        cached = module;
        return module;
      },
    });
  }
  if (cursor !== index.length || payloadStart + end !== bytes.length) invalid("trailing index or payload bytes");
  const store: StdlibStore = Object.freeze({ diaspore_stdlib: 1, id, modules: Object.freeze(modules) });
  validatedStores.add(store);
  return store;
}

/** Decode a compact store index, privately retaining bytes for lazy module loads. */
export function decodeStdlibStore(bytes: Uint8Array): StdlibStore {
  return parseStore(Buffer.from(bytes));
}

/** Lazily load and cache the installed compiled stdlib artifact. */
export function loadStdlibStore(): StdlibStore {
  if (installedStore !== undefined) return installedStore;
  try {
    installedStore = parseStore(readFileSync(STORE_URL));
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
