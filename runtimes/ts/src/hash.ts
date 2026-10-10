/* Private incremental engines for the unchanged portable hash provider. */

import { createHash, type Hash } from "node:crypto";
import {
  type G, type Value, Builtin, DiasporeInstance, NativeModule, NativeObject,
  NONE, diasporeErr, isPyString, strConcat, strText,
} from "./values.ts";
import { acquireBuffer } from "./buffers.ts";
import { bi } from "./builtins.ts";
import { argCount, noKw } from "./helpers.ts";

const blockSizes = new Map<string, number>([
  ["md5", 64], ["sha1", 64], ["sha224", 64], ["sha256", 64],
  ["sha384", 128], ["sha512", 128],
]);

function wrap(state: Hash, blockSize: number): NativeObject {
  return new NativeObject("_diaspore_hash.HASH", new Map<string, Builtin>([
    ["update", bi("update", function* (I, args, kw): G<Value> {
      noKw("update", kw);
      argCount("update", args, 1, 1);
      const data = args[0] instanceof DiasporeInstance && args[0].prim !== null ? args[0].prim : args[0];
      if (isPyString(data)) throw diasporeErr("TypeError", "Strings must be encoded before hashing");
      const lease = acquireBuffer(data);
      if (lease === null) throw diasporeErr("TypeError", "object supporting the buffer API required");
      try {
        // Charge before mutation, matching the portable provider's compression blocks.
        I.fuelN(Math.floor(lease.data.byteLength / blockSize) + 1);
        state.update(lease.data);
      } finally {
        lease.release();
      }
      return NONE;
    })],
    ["digest", bi("digest", function* (I, args, kw): G<Value> {
      noKw("digest", kw);
      argCount("digest", args, 0, 0);
      I.fuelN(1);
      // Node finalizes a Hash on digest; finalize a copy so the engine stays live.
      return state.copy().digest();
    })],
    ["hexdigest", bi("hexdigest", function* (I, args, kw): G<Value> {
      noKw("hexdigest", kw);
      argCount("hexdigest", args, 0, 0);
      I.fuelN(1);
      return state.copy().digest("hex");
    })],
    ["copy", bi("copy", function* (I, args, kw): G<Value> {
      noKw("copy", kw);
      argCount("copy", args, 0, 0);
      I.fuelN(1);
      return wrap(state.copy(), blockSize);
    })],
  ]));
}

/** Build the private six-algorithm engine seam without exposing host hash objects. */
export function makeHashModule(): NativeModule {
  return new NativeModule("_diaspore_hash", new Map<string, Value>([
    ["new", bi("new", function* (_I, args, kw): G<Value> {
      noKw("new", kw);
      argCount("new", args, 1, 1);
      const name = args[0] instanceof DiasporeInstance && args[0].prim !== null ? args[0].prim : args[0];
      if (!isPyString(name)) throw diasporeErr("TypeError", "hash name must be a string");
      const algorithm = strText(name);
      const blockSize = blockSizes.get(algorithm);
      if (blockSize === undefined) throw diasporeErr("ValueError", strConcat("unsupported hash type ", name));
      return wrap(createHash(algorithm), blockSize);
    })],
  ]));
}
