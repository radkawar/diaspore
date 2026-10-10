/* Sandbox-owned storage and native-format, shared buffer views (CPython 3.12). */
import {
  type Value, type G, type BuiltinFn, type KwArgs, NONE, ELLIPSIS, NOT_IMPLEMENTED,
  DiasporeClass, DiasporeInstance, DiasporeTuple, DiasporeList, DiasporeSlice,
  DiasporeIterBase, DiasporeError, Builtin, Property, asIndex, diasporeErr,
  bytesEq, excIsA, objId, unicodeCodecError, typeName, isPyString,
} from "./values.ts";
import type { Interp } from "./interp.ts";
import { iterToArray } from "./iter.ts";
import { sliceIndices } from "./ops.ts";
import { reprBytes } from "./format.ts";
import { percentFormat } from "./string-format.ts";
import { builtinMethod, parseBytesHex } from "./methods.ts";
import { argCount, noKw } from "./helpers.ts";
import { bytesFromArguments } from "./builtins.ts";
import { BUILTIN_DOCS } from "./builtin-docs.ts";

type Storage = { data: Uint8Array; scalars: DataView; exports: number };
type BufferState = {
  storage: Storage; owner: Value; view: boolean; readonly: boolean;
  offset: number; length: number; released: boolean; exports: number;
  format: string; code: string; itemsize: number; shape: readonly number[]; strides: readonly number[];
  hash?: Value;
};
const states = new WeakMap<DiasporeInstance, BufferState>();
const finalizers = new FinalizationRegistry<BufferState>(s => {
  if (!s.released) { s.released = true; s.storage.exports--; }
});
// The portable native ABI is little-endian LP64, as in the other runtimes.
const sizes: Readonly<Record<string, number>> = {
  c: 1, b: 1, B: 1, "?": 1, h: 2, H: 2, i: 4, I: 4,
  l: 8, L: 8, q: 8, Q: 8, n: 8, N: 8, P: 8, e: 2, f: 4, d: 8,
};
const ssizeMax = (1n << 63n) - 1n;
const floatBits = new DataView(new ArrayBuffer(8));

function storage(data: Uint8Array): Storage {
  return { data, scalars: new DataView(data.buffer, data.byteOffset, data.byteLength), exports: 0 };
}

function byteState(data: Uint8Array, owner: Value, view: boolean): BufferState {
  return { storage: storage(data), owner, view, readonly: view,
    offset: 0, length: data.length, released: false, exports: 0,
    format: "B", code: "B", itemsize: 1, shape: [data.length], strides: [1] };
}

function accessible(s: BufferState): BufferState {
  if (s.released) throw diasporeErr("ValueError", "operation forbidden on released memoryview object");
  return s;
}

function state(value: Value): BufferState {
  const result = value instanceof DiasporeInstance ? states.get(value) : undefined;
  if (result === undefined) throw diasporeErr("TypeError", "a bytes-like object is required");
  return accessible(result);
}

function sourceState(value: Value): BufferState | null {
  if (value instanceof Uint8Array) return byteState(value, value, true);
  if (!(value instanceof DiasporeInstance) || !states.has(value)) return null;
  return state(value);
}

function nbytes(s: BufferState): number {
  let size = s.itemsize;
  for (const dimension of s.shape) size *= dimension;
  return size;
}

function contiguous(s: BufferState, fortran = false): boolean {
  if (s.shape.includes(0)) return true;
  let expected = s.itemsize;
  for (let j = 0; j < s.shape.length; j++) {
    const dim = fortran ? j : s.shape.length - 1 - j;
    if (s.shape[dim] > 1 && s.strides[dim] !== expected) return false;
    expected *= s.shape[dim];
  }
  return true;
}

function elementOffset(s: BufferState, index: number, fortran = false): number {
  let offset = s.offset;
  for (let j = 0; j < s.shape.length; j++) {
    const dim = fortran ? j : s.shape.length - 1 - j;
    offset += (index % s.shape[dim]) * s.strides[dim];
    index = Math.floor(index / s.shape[dim]);
  }
  return offset;
}

function logicalBytes(s: BufferState, fortran = false, copy = false): Uint8Array {
  const size = nbytes(s);
  if (contiguous(s, fortran)) {
    const data = s.storage.data.subarray(s.offset, s.offset + size);
    return copy ? new Uint8Array(data) : data;
  }
  const result = new Uint8Array(size);
  for (let index = 0; index < size / s.itemsize; index++) {
    const offset = elementOffset(s, index, fortran);
    result.set(s.storage.data.subarray(offset, offset + s.itemsize), index * s.itemsize);
  }
  return result;
}

/** Read logical C-order bytes; only non-contiguous storage needs a copy. */
export function bufferBytes(value: Value): Uint8Array | null {
  if (value instanceof Uint8Array) return value;
  const s = sourceState(value);
  return s === null ? null : logicalBytes(s);
}

/** A native consumer must release its zero-copy export in a finally block. */
export type BufferExport = { data: Uint8Array; release(): void };

/** Acquire C-contiguous storage, locking resize and memoryview.release(). */
export function acquireBuffer(value: Value, writable = false): BufferExport | null {
  const s = sourceState(value);
  if (s === null) return null;
  if (writable && s.readonly) throw diasporeErr("TypeError", "underlying buffer is not writable");
  if (!contiguous(s)) throw diasporeErr("BufferError", "memoryview: underlying buffer is not C-contiguous");
  s.storage.exports++;
  s.exports++;
  let owner: Value | null = value;
  return { data: s.storage.data.subarray(s.offset, s.offset + nbytes(s)), release() {
    if (owner !== null) { owner = null; s.storage.exports--; s.exports--; }
  } };
}

function isIndex(value: Value): boolean {
  return asIndex(value) !== null || (value instanceof DiasporeInstance &&
    (asIndex(value.prim as Value) !== null || value.cls.lookup("__index__") !== undefined));
}

/** Convert through Python's exact integer-index protocol without numeric coercion. */
export function* indexInteger(I: Interp, value: Value): G<bigint> {
  let n = asIndex(value);
  if (n !== null) return n;
  if (value instanceof DiasporeInstance) {
    n = asIndex(value.prim as Value);
    if (n !== null) return n;
    if (value.cls.lookup("__index__") !== undefined) {
      const result = yield* I.callDunder(value, "__index__", []);
      n = asIndex(result instanceof DiasporeInstance ? result.prim as Value : result);
      if (n === null) throw diasporeErr("TypeError", `__index__ returned non-int (type ${typeName(result)})`);
      return n;
    }
  }
  throw diasporeErr("TypeError", `'${typeName(value)}' object cannot be interpreted as an integer`);
}

function* byte(I: Interp, value: Value): G<number> {
  const n = yield* indexInteger(I, value);
  if (n < 0n || n > 255n) throw diasporeErr("ValueError", "byte must be in range(0, 256)");
  return Number(n);
}

function* indexOf(I: Interp, value: Value, dimension: number | BufferState, dim?: number): G<number> {
  const n = yield* indexInteger(I, value), length = typeof dimension === "number" ? dimension : dimension.length;
  const adjusted = n < 0n ? n + BigInt(length) : n;
  if (adjusted < 0n || adjusted >= BigInt(length)) {
    throw diasporeErr("IndexError", dim === undefined ? "index out of bounds" : `index out of bounds on dimension ${dim + 1}`);
  }
  return Number(adjusted);
}

function* slice(I: Interp, key: DiasporeSlice, s: BufferState): G<[number, number, number]> {
  const step = key.step === NONE ? NONE : yield* indexInteger(I, key.step);
  const lower = key.lower === NONE ? NONE : yield* indexInteger(I, key.lower);
  const upper = key.upper === NONE ? NONE : yield* indexInteger(I, key.upper);
  return sliceIndices(new DiasporeSlice(lower, upper, step), s.length);
}

function resize(s: BufferState, start: number, count: number, replacement: Uint8Array): void {
  const length = s.length - count + replacement.length;
  if (length === s.length) { s.storage.data.set(replacement, start); return; }
  if (s.storage.exports !== 0) throw diasporeErr("BufferError", "Existing exports of data: object cannot be re-sized");
  const data = new Uint8Array(length);
  data.set(s.storage.data.subarray(0, start));
  data.set(replacement, start);
  data.set(s.storage.data.subarray(start + count), start + replacement.length);
  s.storage.data = data;
  s.storage.scalars = new DataView(data.buffer, data.byteOffset, data.byteLength);
  s.length = length;
  s.shape = [length];
}

function scalar(s: BufferState, offset: number): Value {
  const data = s.storage.scalars;
  switch (s.code) {
    case "B": return BigInt(data.getUint8(offset));
    case "b": return BigInt(data.getInt8(offset));
    case "c": return new Uint8Array([data.getUint8(offset)]);
    case "?": return data.getUint8(offset) !== 0;
    case "h": return BigInt(data.getInt16(offset, true));
    case "H": return BigInt(data.getUint16(offset, true));
    case "i": return BigInt(data.getInt32(offset, true));
    case "I": return BigInt(data.getUint32(offset, true));
    case "l": case "q": case "n": return data.getBigInt64(offset, true);
    case "L": case "Q": case "N": case "P": return data.getBigUint64(offset, true);
    case "f": return data.getFloat32(offset, true);
    case "d": return data.getFloat64(offset, true);
    case "e": {
      const bits = data.getUint16(offset, true), fraction = bits & 1023, exponent = (bits >> 10) & 31;
      const magnitude = exponent === 31 ? (fraction ? NaN : Infinity) :
        exponent === 0 ? fraction * 2 ** -24 : (1024 + fraction) * 2 ** (exponent - 25);
      return bits & 32768 ? -magnitude : magnitude;
    }
    default: throw diasporeErr("NotImplementedError", `memoryview: format ${s.format} not supported`);
  }
}

function halfBits(value: number): number {
  const sign = value < 0 || Object.is(value, -0) ? 32768 : 0;
  const magnitude = Math.abs(value);
  if (Number.isNaN(value)) {
    floatBits.setFloat64(0, value, true);
    return (floatBits.getUint16(6, true) & 0x8000) | 0x7e00;
  }
  if (magnitude === Infinity) return sign | 0x7c00;
  if (magnitude === 0) return sign;
  let exponent = Math.floor(Math.log2(magnitude));
  // Scaling by powers of two is exact; rounding is directly from double, not float.
  const scaled = magnitude * 2 ** (exponent < -14 ? 24 : 10 - exponent);
  let rounded = Math.floor(scaled);
  const remainder = scaled - rounded;
  if (remainder > 0.5 || (remainder === 0.5 && (rounded & 1))) rounded++;
  if (exponent < -14) return sign | rounded;
  if (rounded === 2048) { rounded = 1024; exponent++; }
  if (exponent > 15) throw diasporeErr("OverflowError", "float too large to pack with e format");
  return sign | ((exponent + 15) << 10) | (rounded - 1024);
}

function* real(I: Interp, value: Value): G<number> {
  if (typeof value === "number") return value;
  if (typeof value === "bigint" || typeof value === "boolean") {
    const result = Number(value);
    if (!Number.isFinite(result)) throw diasporeErr("OverflowError", "int too large to convert to float");
    return result;
  }
  if (value instanceof DiasporeInstance && typeof value.prim === "number") return value.prim;
  if (value instanceof DiasporeInstance && value.cls.lookup("__float__") !== undefined) {
    const result = yield* I.callDunder(value, "__float__", []);
    if (result instanceof DiasporeInstance && typeof result.prim === "number") return result.prim;
    if (typeof result !== "number") throw diasporeErr("TypeError", "__float__ returned non-float");
    return result;
  }
  if (isIndex(value)) return yield* real(I, yield* indexInteger(I, value));
  throw diasporeErr("TypeError", "a real number is required");
}

function* pack(I: Interp, s: BufferState, offset: number, value: Value): G<void> {
  const code = s.code;
  let converted: number | bigint;
  if (code === "?") converted = (yield* I.truth(value)) ? 1 : 0;
  else {
    try {
      if (code === "c") {
        if (!(value instanceof Uint8Array)) throw diasporeErr("TypeError", "expected bytes");
        if (value.length !== 1) throw diasporeErr("ValueError", "expected one byte");
        converted = value[0];
      } else if (code === "e" || code === "f" || code === "d") converted = yield* real(I, value);
      else {
        if (code === "P" && !((typeof value === "bigint" || typeof value === "boolean") ||
          (value instanceof DiasporeInstance && asIndex(value.prim as Value) !== null))) {
          throw diasporeErr("TypeError", "an integer is required");
        }
        converted = yield* indexInteger(I, value);
        const bits = BigInt(s.itemsize * 8), signed = "bhilqn".includes(code);
        const minimum = signed || code === "P" ? -(1n << (bits - 1n)) : 0n;
        const maximum = (1n << (signed ? bits - 1n : bits)) - 1n;
        if (converted < minimum || converted > maximum) throw diasporeErr("ValueError", "out of range");
      }
    } catch (error) {
      if (error instanceof DiasporeError) {
        if (excIsA(error.value, "TypeError")) throw diasporeErr("TypeError", `memoryview: invalid type for format '${code}'`);
        if (excIsA(error.value, "ValueError") || excIsA(error.value, "OverflowError")) {
          throw diasporeErr("ValueError", `memoryview: invalid value for format '${code}'`);
        }
      }
      throw error;
    }
  }
  accessible(s);
  const data = s.storage.scalars;
  switch (code) {
    case "B": case "b": case "c": case "?": data.setUint8(offset, Number(converted)); break;
    case "h": case "H": data.setUint16(offset, Number(converted), true); break;
    case "i": case "I": data.setUint32(offset, Number(converted), true); break;
    case "l": case "L": case "q": case "Q": case "n": case "N": case "P":
      data.setBigUint64(offset, converted as bigint, true); break;
    case "f": data.setFloat32(offset, converted as number, true); break;
    case "d": data.setFloat64(offset, converted as number, true); break;
    case "e": {
      try { data.setUint16(offset, halfBits(converted as number), true); }
      catch (error) {
        if (error instanceof DiasporeError && excIsA(error.value, "OverflowError")) {
          throw diasporeErr("ValueError", `memoryview: invalid value for format '${code}'`);
        }
        throw error;
      }
      break;
    }
    default: throw diasporeErr("NotImplementedError", `memoryview: format ${s.format} not supported`);
  }
}

function* itemOffset(I: Interp, s: BufferState, key: Value, assignment = false): G<number> {
  if (s.shape.length === 0) {
    if (key === ELLIPSIS || (key instanceof DiasporeTuple && key.items.length === 0)) return s.offset;
    throw diasporeErr("TypeError", "invalid indexing of 0-dim memory");
  }
  if (isIndex(key)) {
    if (s.shape.length > 1) throw diasporeErr("NotImplementedError", assignment ?
      "sub-views are not implemented" : "multi-dimensional sub-views are not implemented");
    return s.offset + (yield* indexOf(I, key, s.length, s.view ? 0 : undefined)) * s.strides[0];
  }
  if (key instanceof DiasporeTuple && key.items.every(isIndex)) {
    if (key.items.length < s.shape.length) throw diasporeErr("NotImplementedError", "sub-views are not implemented");
    if (key.items.length > s.shape.length) throw diasporeErr("TypeError", `cannot index ${s.shape.length}-dimension view with ${key.items.length}-element tuple`);
    let offset = s.offset;
    for (let dim = 0; dim < s.shape.length; dim++) {
      offset += (yield* indexOf(I, key.items[dim], s.shape[dim], dim)) * s.strides[dim];
    }
    return offset;
  }
  if (key instanceof DiasporeTuple && key.items.length && key.items.every(k => k instanceof DiasporeSlice)) {
    throw diasporeErr("NotImplementedError", assignment ?
      "memoryview slice assignments are currently restricted to ndim = 1" :
      "multi-dimensional slicing is not implemented");
  }
  throw diasporeErr("TypeError", "memoryview: invalid slice key");
}

function list(s: BufferState, dimension = 0, offset = s.offset): Value {
  if (dimension === s.shape.length) return scalar(s, offset);
  const items: Value[] = new Array(s.shape[dimension]);
  for (let index = 0; index < items.length; index++) {
    items[index] = list(s, dimension + 1, offset + index * s.strides[dimension]);
  }
  return new DiasporeList(items);
}

function argumentsFor(name: string, args: Value[], kw: KwArgs, names: string[], required: number): Value[] {
  argCount(name, args, 1, names.length + 1);
  const result = args.slice(1);
  for (const [key, value] of kw) {
    const index = names.indexOf(key);
    if (index < 0) throw diasporeErr("TypeError", `'${key}' is an invalid keyword argument for ${name}()`);
    if (index < args.length - 1) throw diasporeErr("TypeError", `argument for ${name}() given by name and position`);
    result[index] = value;
  }
  for (let j = 0; j < required; j++) {
    if (result[j] === undefined) throw diasporeErr("TypeError", `${name}() missing required argument '${names[j]}'`);
  }
  return result;
}

class BufferIterator extends DiasporeIterBase {
  kindName: string;
  private value: Value | null;
  private index = 0;
  constructor(value: Value) {
    super(); this.value = value;
    this.kindName = state(value).view ? "memory_iterator" : "bytearray_iterator";
  }
  *next(): G<[Value, boolean]> {
    if (this.value === null) return [NONE, false];
    const s = state(this.value);
    if (this.index >= s.length) { this.value = null; return [NONE, false]; }
    return [scalar(s, s.offset + this.index++ * s.strides[0]), true];
  }
}

/** Build buffer classes using the ordinary instance and descriptor protocols. */
export function makeBufferClasses(I: Interp): Map<string, Value> {
  const array = new DiasporeClass("bytearray", [], new Map<string, Value>([["__module__", "builtins"], ["__doc__", BUILTIN_DOCS.bytearray ?? NONE]]));
  const view = new DiasporeClass("memoryview", [], new Map<string, Value>([["__module__", "builtins"], ["__doc__", BUILTIN_DOCS.memoryview ?? NONE]]));
  array.runtime = view.runtime = I;
  const own = (data: Uint8Array): DiasporeInstance => {
    const result = new DiasporeInstance(array);
    states.set(result, byteState(data, result, false));
    return result;
  };
  const fromhex = new Builtin("fromhex", function* (I2, args, kwargs): G<Value> {
    const cls = args[0];
    const name = cls instanceof DiasporeClass ? cls.name : "bytearray";
    noKw(`${name}.fromhex`, kwargs);
    if (args.length !== 2) throw diasporeErr("TypeError", `${name}.fromhex() takes exactly one argument (${Math.max(0, args.length - 1)} given)`);
    const text = args[1] instanceof DiasporeInstance && args[1].prim !== null ? args[1].prim : args[1];
    if (!isPyString(text)) throw diasporeErr("TypeError", `fromhex() argument must be str, not ${text === NONE ? "None" : typeName(args[1])}`);
    I2.fuelN(typeof text === "string" ? text.length : text.points.length);
    const value = parseBytesHex(text);
    return cls === array ? own(value) : yield* I2.invoke(cls, [value], new Map());
  });
  fromhex.kind = "classmethod_descriptor";
  fromhex.objclass = array;
  array.ns.set("fromhex", fromhex);
  array.ns.set("maketrans", I.builtinUnbound("bytearray", "maketrans")!);
  const attach = (result: DiasporeInstance, source: BufferState): void => {
    const s = { ...source, view: true, released: false, exports: 0, hash: undefined };
    s.storage.exports++;
    states.set(result, s);
    finalizers.register(result, s, result);
  };
  const share = (s: BufferState): DiasporeInstance => {
    const result = new DiasporeInstance(view);
    attach(result, s);
    return result;
  };
  const replacementBytes = function* (I2: Interp, value: Value): G<Uint8Array> {
    const bytes = bufferBytes(value);
    if (bytes !== null) return bytes;
    const items = yield* iterToArray(I2, value);
    const result = new Uint8Array(items.length);
    for (let j = 0; j < items.length; j++) result[j] = yield* byte(I2, items[j]);
    return result;
  };
  array.ns.set("__init__", new Builtin("__init__", function* (I2, args, kw): G<Value> {
    const old = states.get(args[0] as DiasporeInstance);
    if (old !== undefined && old.length) resize(old, 0, old.length, new Uint8Array());
    const data = (yield* bytesFromArguments(I2, args.slice(1), kw, true)) as Uint8Array;
    if (old !== undefined) resize(old, 0, old.length, data);
    else states.set(args[0] as DiasporeInstance, byteState(data, args[0], false));
    return NONE;
  }));
  view.ns.set("__init__", new Builtin("__init__", function* (_I, args, kw): G<Value> {
    argCount("memoryview", args, 2, 2);
    if (kw.size) throw diasporeErr("TypeError", "memoryview() takes no keyword arguments");
    // memoryview's public __init__ does not reinitialize an existing view.
    if (states.has(args[0] as DiasporeInstance)) return NONE;
    const s = sourceState(args[1]);
    if (s === null) throw diasporeErr("TypeError", "memoryview: a bytes-like object is required");
    attach(args[0] as DiasporeInstance, s);
    return NONE;
  }));
  const common: Record<string, BuiltinFn> = {
    __len__: function* (_I, args) {
      const s = state(args[0]);
      if (s.shape.length === 0) throw diasporeErr("TypeError", "0-dim memory has no length");
      return BigInt(s.length);
    },
    __iter__: function* (_I, args) {
      const s = state(args[0]);
      if (s.shape.length === 0) throw diasporeErr("TypeError", "invalid indexing of 0-dim memory");
      if (s.shape.length !== 1) throw diasporeErr("NotImplementedError", "multi-dimensional sub-views are not implemented");
      return new BufferIterator(args[0]);
    },
    __bytes__: function* (_I, args) { return logicalBytes(state(args[0]), false, true); },
    __eq__: function* (I2, args) {
      const s = states.get(args[0] as DiasporeInstance)!;
      const otherState = args[1] instanceof DiasporeInstance ? states.get(args[1]) : undefined;
      if (s.released || otherState?.released) return args[0] === args[1];
      const other = sourceState(args[1]);
      if (other === null) return NOT_IMPLEMENTED;
      if (!s.view) return contiguous(other) ? bytesEq(logicalBytes(s), logicalBytes(other)) : NOT_IMPLEMENTED;
      if (s.shape.length !== other.shape.length) return false;
      for (let dim = 0; dim < s.shape.length; dim++) {
        if (s.shape[dim] !== other.shape[dim]) return false;
        if (s.shape[dim] === 0) break;
      }
      for (let index = 0; index < nbytes(s) / s.itemsize; index++) {
        if (!(yield* I2.eq(scalar(s, elementOffset(s, index)), scalar(other, elementOffset(other, index))))) return false;
      }
      return true;
    },
    __ne__: function* (I2, args, kw) {
      const equal = yield* common.__eq__(I2, args, kw);
      return equal === NOT_IMPLEMENTED ? NOT_IMPLEMENTED : !equal;
    },
    __getitem__: function* (I2, args) {
      const s = state(args[0]), key = args[1];
      if (!(key instanceof DiasporeSlice)) {
        if (!s.view) {
          const index = yield* indexOf(I2, key, s);
          return BigInt(s.storage.data[index]);
        }
        if (s.shape.length === 0 && key === ELLIPSIS) return args[0];
        const offset = yield* itemOffset(I2, s, key);
        return scalar(accessible(s), offset);
      }
      if (s.shape.length === 0) throw diasporeErr("TypeError", "invalid indexing of 0-dim memory");
      const [start, stop, step] = yield* slice(I2, key, s);
      accessible(s);
      const length = Math.max(0, Math.ceil((stop - start) / step));
      if (s.view) {
        const shape = s.shape.slice(), strides = s.strides.slice();
        shape[0] = length; strides[0] *= step;
        return share({ ...s, offset: s.offset + start * s.strides[0], length, shape, strides });
      }
      const data = new Uint8Array(length);
      for (let n = 0; n < length; n++) data[n] = s.storage.data[start + n * step];
      return own(data);
    },
    __setitem__: function* (I2, args) {
      const s = state(args[0]), key = args[1];
      if (s.readonly) throw diasporeErr("TypeError", "cannot modify read-only memory");
      if (!(key instanceof DiasporeSlice)) {
        if (!s.view) {
          const keyIndex = yield* indexInteger(I2, key), value = yield* byte(I2, args[2]);
          const index = yield* indexOf(I2, keyIndex, s);
          s.storage.data[index] = value;
        } else yield* pack(I2, s, yield* itemOffset(I2, s, key, true), args[2]);
        return NONE;
      }
      if (s.view && s.shape.length !== 1) {
        if (!s.shape.length) throw diasporeErr("TypeError", "invalid indexing of 0-dim memory");
        throw diasporeErr("NotImplementedError", "memoryview slice assignments are currently restricted to ndim = 1");
      }
      const other = s.view ? sourceState(args[2]) : null;
      if (s.view && other === null) throw diasporeErr("TypeError", "a bytes-like object is required");
      if (other !== null) { other.exports++; other.storage.exports++; }
      try {
        const replacementArray = other === null ? yield* replacementBytes(I2, args[2]) : null;
        const [start, stop, step] = yield* slice(I2, key, s);
        accessible(s);
        let replacement = other === null ? replacementArray! : logicalBytes(accessible(other));
        const length = Math.max(0, Math.ceil((stop - start) / step));
        if (!s.view && step === 1) { resize(s, start, length, replacement); return NONE; }
        if (s.view && (other!.code !== s.code || other!.itemsize !== s.itemsize ||
          other!.shape.length !== 1 || other!.length !== length)) {
          throw diasporeErr("ValueError", "memoryview assignment: lvalue and rvalue have different structures");
        }
        if (!s.view && length !== replacement.length) {
          throw diasporeErr("ValueError", `attempt to assign bytes of size ${replacement.length} to extended slice of size ${length}`);
        }
        if (length === 0) return NONE;
        const first = s.offset + start * s.strides[0], stride = step * s.strides[0];
        if (stride === s.itemsize) s.storage.data.set(replacement, first);
        else {
          if (replacement.buffer === s.storage.data.buffer) replacement = new Uint8Array(replacement);
          for (let n = 0; n < length; n++) {
            s.storage.data.set(replacement.subarray(n * s.itemsize, (n + 1) * s.itemsize), first + n * stride);
          }
        }
        return NONE;
      } finally {
        if (other !== null) { other.exports--; other.storage.exports--; }
      }
    },
  };
  for (const [name, fn] of Object.entries(common)) {
    array.ns.set(name, new Builtin(name, fn));
    view.ns.set(name, new Builtin(name, fn));
  }
  array.ns.set("__hash__", NONE);
  view.ns.set("__hash__", new Builtin("__hash__", function* (I2, args) {
    const s = states.get(args[0] as DiasporeInstance)!;
    if (s.hash !== undefined) return s.hash;
    accessible(s);
    if (!s.readonly) throw diasporeErr("ValueError", "cannot hash writable memoryview object");
    if (!"bBc".includes(s.code)) throw diasporeErr("ValueError", "memoryview: hashing is restricted to formats 'B', 'b' or 'c'");
    yield* I2.invoke(I2.builtins.get("hash")!, [s.owner], new Map());
    s.hash = yield* I2.invoke(I2.builtins.get("hash")!, [logicalBytes(accessible(s))], new Map());
    return s.hash;
  }));
  view.ns.set("__repr__", new Builtin("__repr__", function* (_I, args) {
    const s = states.get(args[0] as DiasporeInstance)!;
    return `<${s.released ? "released memory" : "memory"} at 0x${objId(args[0] as object).toString(16)}>`;
  }));
  view.ns.set("__delitem__", new Builtin("__delitem__", function* (_I, args) {
    const s = state(args[0]);
    throw diasporeErr("TypeError", s.readonly ? "cannot modify read-only memory" : "cannot delete memory");
  }));
  array.ns.set("__repr__", new Builtin("__repr__", function* (_I, args) {
    return "bytearray(" + reprBytes(bufferBytes(args[0])!) + ")";
  }));
  array.ns.set("__mod__", new Builtin("__mod__", function* (I2, args) {
    return own((yield* percentFormat(I2, bufferBytes(args[0])!, args[1])) as Uint8Array);
  }));
  array.ns.set("__add__", new Builtin("__add__", function* (_I, args) {
    const left = bufferBytes(args[0])!, other = sourceState(args[1]);
    if (other === null) return NOT_IMPLEMENTED;
    if (!contiguous(other)) throw diasporeErr("TypeError", "can't concat memoryview to bytearray");
    const right = logicalBytes(other), data = new Uint8Array(left.length + right.length);
    data.set(left); data.set(right, left.length);
    return own(data);
  }));
  array.ns.set("__iadd__", new Builtin("__iadd__", function* (_I, args) {
    const s = state(args[0]), other = sourceState(args[1]);
    if (other === null) return NOT_IMPLEMENTED;
    if (!contiguous(other)) throw diasporeErr("TypeError", "can't concat memoryview to bytearray");
    const exported = acquireBuffer(args[1])!;
    try { resize(s, s.length, 0, exported.data); }
    finally { exported.release(); }
    return args[0];
  }));
  for (const name of ["__mul__", "__rmul__", "__imul__"]) {
    array.ns.set(name, new Builtin(name, function* (I2, args) {
      if (!isIndex(args[1])) return NOT_IMPLEMENTED;
      let count = yield* indexInteger(I2, args[1]);
      if (count < -ssizeMax - 1n || count > ssizeMax) throw diasporeErr("OverflowError", "cannot fit 'int' into an index-sized integer");
      if (count < 0n) count = 0n;
      const s = state(args[0]), total = BigInt(s.length) * count, inplace = name === "__imul__";
      if (inplace && total === BigInt(s.length)) return args[0];
      if (total > ssizeMax) throw diasporeErr("MemoryError", "");
      if (inplace && s.storage.exports) throw diasporeErr("BufferError", "Existing exports of data: object cannot be re-sized");
      let data: Uint8Array;
      try { data = new Uint8Array(Number(total)); }
      catch (error) {
        if (error instanceof RangeError) throw diasporeErr("MemoryError", "");
        throw error;
      }
      if (data.length) {
        data.set(s.storage.data);
        for (let filled = s.length; filled < data.length; filled *= 2) {
          data.copyWithin(filled, 0, Math.min(filled, data.length - filled));
        }
      }
      if (!inplace) return own(data);
      s.storage = storage(data); s.length = data.length; s.shape = [data.length];
      return args[0];
    }));
  }
  array.ns.set("__delitem__", new Builtin("__delitem__", function* (I2, args) {
    const s = state(args[0]), key = args[1];
    if (!(key instanceof DiasporeSlice)) { resize(s, yield* indexOf(I2, key, s), 1, new Uint8Array()); return NONE; }
    const [start, stop, step] = yield* slice(I2, key, s);
    const count = Math.max(0, Math.ceil((stop - start) / step));
    if (!count) return NONE;
    if (s.storage.exports) throw diasporeErr("BufferError", "Existing exports of data: object cannot be re-sized");
    if (step === 1) resize(s, start, count, new Uint8Array());
    else {
      const data = new Uint8Array(s.length - count);
      const first = step > 0 ? start : start + (count - 1) * step, stride = Math.abs(step);
      for (let from = 0, to = 0; from < s.length; from++) {
        if (from < first || from >= first + count * stride || (from - first) % stride) data[to++] = s.storage.data[from];
      }
      resize(s, 0, s.length, data);
    }
    return NONE;
  }));
  const mutations: Record<string, BuiltinFn> = {
    append: function* (I2, args) {
      argCount("append", args, 2, 2);
      const value = yield* byte(I2, args[1]), s = state(args[0]);
      resize(s, s.length, 0, new Uint8Array([value])); return NONE;
    },
    extend: function* (I2, args) {
      argCount("extend", args, 2, 2);
      const data = yield* replacementBytes(I2, args[1]), s = state(args[0]);
      resize(s, s.length, 0, data); return NONE;
    },
    clear: function* (_I, args) { argCount("clear", args, 1, 1); const s = state(args[0]); resize(s, 0, s.length, new Uint8Array()); return NONE; },
    copy: function* (_I, args) { argCount("copy", args, 1, 1); return own(new Uint8Array(bufferBytes(args[0])!)); },
    reverse: function* (_I, args) { argCount("reverse", args, 1, 1); state(args[0]).storage.data.reverse(); return NONE; },
    pop: function* (I2, args) {
      argCount("pop", args, 1, 2);
      const s = state(args[0]);
      if (!s.length) throw diasporeErr("IndexError", "pop from empty bytearray");
      const n = yield* indexOf(I2, args[1] ?? -1n, s), value = s.storage.data[n];
      resize(s, n, 1, new Uint8Array()); return BigInt(value);
    },
    insert: function* (I2, args) {
      argCount("insert", args, 3, 3);
      const n = yield* indexInteger(I2, args[1]), value = yield* byte(I2, args[2]), s = state(args[0]);
      if (n < -ssizeMax - 1n || n > ssizeMax) throw diasporeErr("OverflowError", "Python int too large to convert to C ssize_t");
      const at = Math.min(s.length, Math.max(0, Number(n < 0n ? n + BigInt(s.length) : n)));
      resize(s, at, 0, new Uint8Array([value])); return NONE;
    },
    remove: function* (I2, args) {
      argCount("remove", args, 2, 2);
      const value = yield* byte(I2, args[1]), s = state(args[0]), n = s.storage.data.indexOf(value);
      if (n < 0) throw diasporeErr("ValueError", "value not found in bytearray");
      resize(s, n, 1, new Uint8Array()); return NONE;
    },
  };
  for (const [name, fn] of Object.entries(mutations)) array.ns.set(name, new Builtin(name, fn));
  for (const name of ["decode", "find", "split", "strip", "lstrip", "rstrip", "replace", "startswith", "endswith", "lower", "upper", "join", "ljust", "rjust", "center", "translate", "isascii"]) {
    const descriptor = new Builtin(name, function* (I2, args, kw) {
      if (args.length === 0) throw diasporeErr("TypeError", `unbound method bytearray.${name}() needs an argument`);
      if (!(args[0] instanceof DiasporeInstance) || !args[0].cls.mro.includes(array)) {
        throw diasporeErr("TypeError", `descriptor '${name}' for 'bytearray' objects doesn't apply to a '${typeName(args[0])}' object`);
      }
      const source = bufferBytes(args[0])!;
      const result = yield* I2.invoke(builtinMethod(I2, source, name), args.slice(1), kw);
      const owned = (value: Uint8Array): DiasporeInstance => own(value.buffer === source.buffer ? new Uint8Array(value) : value);
      if (result instanceof Uint8Array) return owned(result);
      if (result instanceof DiasporeList) return new DiasporeList(result.items.map(item => item instanceof Uint8Array ? owned(item) : item));
      return result;
    });
    descriptor.kind = "method_descriptor";
    descriptor.objclass = array;
    array.ns.set(name, descriptor);
  }
  for (const cls of [array, view]) cls.ns.set("hex", new Builtin("hex", function* (I2, args, kw) {
    const [separator, grouping] = argumentsFor("hex", args, kw, ["sep", "bytes_per_sep"], 0);
    let sep = "", size = 1;
    if (separator !== undefined) {
      if (typeof separator === "string") {
        if (separator.length !== 1) throw diasporeErr("ValueError", "sep must be length 1.");
        if (separator.charCodeAt(0) > 127) throw diasporeErr("ValueError", "sep must be ASCII.");
        sep = separator;
      } else if (separator instanceof Uint8Array) {
        if (separator.length !== 1) throw diasporeErr("ValueError", "sep must be length 1.");
        if (separator[0] > 127) throw diasporeErr("ValueError", "sep must be ASCII.");
        sep = String.fromCharCode(separator[0]);
      } else throw diasporeErr("TypeError", "sep must be str or bytes.");
    }
    if (grouping !== undefined) {
      const n = yield* indexInteger(I2, grouping);
      if (n < -2147483648n || n > 2147483647n) throw diasporeErr("OverflowError", "Python int too large to convert to C int");
      size = Number(n);
    }
    const data = bufferBytes(args[0])!;
    let result = "";
    for (let j = 0; j < data.length; j++) {
      if (j && size && (size > 0 ? (data.length - j) % size === 0 : j % -size === 0)) result += sep;
      result += data[j].toString(16).padStart(2, "0");
    }
    return result;
  }));
  const release: BuiltinFn = function* (_I, args) {
    const value = args[0] as DiasporeInstance, s = states.get(value)!;
    if (!s.released) {
      if (s.exports) throw diasporeErr("BufferError", `memoryview has ${s.exports} exported buffer${s.exports === 1 ? "" : "s"}`);
      s.released = true; s.storage.exports--;
      finalizers.unregister(value);
    }
    return NONE;
  };
  view.ns.set("release", new Builtin("release", function* (I2, args, kw) {
    argumentsFor("release", args, kw, [], 0);
    return yield* release(I2, args, kw);
  }));
  view.ns.set("__enter__", new Builtin("__enter__", function* (_I, args, kw) {
    argumentsFor("__enter__", args, kw, [], 0);
    state(args[0]); return args[0];
  }));
  view.ns.set("__exit__", new Builtin("__exit__", release));
  view.ns.set("tobytes", new Builtin("tobytes", function* (_I, args, kw) {
    const [order = "C"] = argumentsFor("tobytes", args, kw, ["order"], 0);
    const s = state(args[0]);
    if (order !== NONE && typeof order !== "string") throw diasporeErr("TypeError", "order must be str or None");
    if (order !== NONE && order !== "C" && order !== "F" && order !== "A") throw diasporeErr("ValueError", "order must be 'C', 'F' or 'A'");
    return logicalBytes(s, order === "F" || (order === "A" && contiguous(s, true)), true);
  }));
  view.ns.set("tolist", new Builtin("tolist", function* (_I, args, kw) {
    argumentsFor("tolist", args, kw, [], 0);
    return list(state(args[0]));
  }));
  view.ns.set("toreadonly", new Builtin("toreadonly", function* (_I, args, kw) {
    argumentsFor("toreadonly", args, kw, [], 0);
    return share({ ...state(args[0]), readonly: true });
  }));
  view.ns.set("cast", new Builtin("cast", function* (_I, args, kw) {
    const [format, shapeArg] = argumentsFor("cast", args, kw, ["format", "shape"], 1);
    if (typeof format !== "string") throw diasporeErr("TypeError", "cast() argument 'format' must be str");
    const s = state(args[0]);
    if (!contiguous(s)) throw diasporeErr("TypeError", "memoryview: casts are restricted to C-contiguous views");
    if ((shapeArg !== undefined || s.shape.length !== 1) && s.shape.includes(0)) {
      throw diasporeErr("TypeError", "memoryview: cannot cast view with zeros in shape or strides");
    }
    if (shapeArg !== undefined) {
      if (!(shapeArg instanceof DiasporeList || shapeArg instanceof DiasporeTuple)) throw diasporeErr("TypeError", "shape must be a list or a tuple");
      if (shapeArg.items.length > 64) throw diasporeErr("ValueError", "memoryview: number of dimensions must not exceed 64");
      if (s.shape.length !== 1 && shapeArg.items.length !== 1) throw diasporeErr("TypeError", "memoryview: cast must be 1D -> ND or ND -> 1D");
    }
    for (let j = 0; j < format.length; j++) {
      if (format.charCodeAt(j) > 127) throw unicodeCodecError("UnicodeEncodeError", "ascii", format, j, j + 1, "ordinal not in range(128)");
    }
    const code = format.startsWith("@") ? format.slice(1) : format, itemsize = sizes[code];
    if (code.length !== 1 || itemsize === undefined) {
      throw diasporeErr("ValueError", "memoryview: destination format must be a native single character format prefixed with an optional '@'");
    }
    if (!"bBc".includes(s.code) && !"bBc".includes(code)) throw diasporeErr("TypeError", "memoryview: cannot cast between two non-byte formats");
    const size = nbytes(s);
    if (size % itemsize) throw diasporeErr("TypeError", "memoryview: length is not a multiple of itemsize");
    let shape: number[];
    if (shapeArg === undefined) shape = [size / itemsize];
    else {
      shape = [];
      let product = BigInt(itemsize);
      for (const dimension of (shapeArg as DiasporeTuple | DiasporeList).items) {
        const n = asIndex(dimension instanceof DiasporeInstance ? dimension.prim as Value : dimension);
        if (n === null) throw diasporeErr("TypeError", "memoryview.cast(): elements of shape must be integers");
        if (n < -ssizeMax - 1n || n > ssizeMax) throw diasporeErr("OverflowError", "Python int too large to convert to C ssize_t");
        if (n <= 0n) throw diasporeErr("ValueError", "memoryview.cast(): elements of shape must be integers > 0");
        product *= n;
        if (product > ssizeMax) throw diasporeErr("ValueError", "memoryview.cast(): product(shape) > SSIZE_MAX");
        shape.push(Number(n));
      }
      if (product !== BigInt(size)) throw diasporeErr("TypeError", "memoryview: product(shape) * itemsize != buffer size");
    }
    const strides = new Array<number>(shape.length);
    for (let j = shape.length - 1, stride = itemsize; j >= 0; j--) {
      strides[j] = stride; stride *= shape[j];
    }
    return share({ ...s, format, code, itemsize, shape, strides, length: shape[0] ?? 1 });
  }));
  for (const name of ["obj", "nbytes", "readonly", "format", "itemsize", "ndim", "shape", "strides", "suboffsets", "c_contiguous", "f_contiguous", "contiguous"]) {
    view.ns.set(name, new Property(new Builtin(name, function* (_I, args) {
      const s = state(args[0]);
      switch (name) {
        case "obj": return s.owner;
        case "nbytes": return BigInt(nbytes(s));
        case "readonly": return s.readonly;
        case "format": return s.format;
        case "itemsize": return BigInt(s.itemsize);
        case "ndim": return BigInt(s.shape.length);
        case "shape": return new DiasporeTuple(s.shape.map(BigInt));
        case "strides": return new DiasporeTuple(s.strides.map(BigInt));
        case "suboffsets": return new DiasporeTuple([]);
        case "c_contiguous": return contiguous(s);
        case "f_contiguous": return contiguous(s, true);
        default: return contiguous(s) || contiguous(s, true);
      }
    }), null, null, null));
  }
  return new Map<string, Value>([["bytearray", array], ["memoryview", view]]);
}
