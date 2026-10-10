/* Runtime generic aliases and unions retain their sandbox type operands. */
import { type Value, type G, NativeObject, DiasporeTuple, Builtin, BuiltinType, DiasporeClass, DiasporeInstance, DiasporeModule, DiasporeError, excIsA, NONE, NOT_IMPLEMENTED, hashValue, diasporeErr } from "./values.ts";
import type { Interp } from "./interp.ts";

function parameters(I: Interp, arguments_: Value[]): DiasporeTuple {
  const result: Value[] = [];
  const attribute = (value: Value, name: string): Value | undefined => {
    try { return I.drive(I.getAttr(value, name)); }
    catch (error) { if (error instanceof DiasporeError && excIsA(error.value, "AttributeError")) return undefined; throw error; }
  };
  for (const value of arguments_) {
    const nested = attribute(value, "__parameters__");
    if (nested instanceof DiasporeTuple) {
      for (const parameter of nested.items) if (!result.includes(parameter)) result.push(parameter);
    } else if (attribute(value, "__typing_subst__") !== undefined && !result.includes(value)) {
      result.push(value);
    }
  }
  return new DiasporeTuple(result);
}

/** Construct an alias with callable origin and PEP 560 base resolution. */
export function genericAlias(I: Interp, origin: Value, arguments_: Value): NativeObject {
  if (!I.types.has("GenericAlias")) I.types.set("GenericAlias", new BuiltinType("GenericAlias", function* (I2, args): G<Value> {
    if (args.length !== 2) throw diasporeErr("TypeError", "GenericAlias expected 2 arguments");
    return genericAlias(I2, args[0], args[1]);
  }));
  const args = arguments_ instanceof DiasporeTuple ? arguments_ : new DiasporeTuple([arguments_]);
  const alias = new NativeObject("GenericAlias", new Map([
    ["__mro_entries__", new Builtin("__mro_entries__", function* (): G<Value> { return new DiasporeTuple([origin]); })],
    ["__call__", new Builtin("__call__", function* (I2, positional, keywords): G<Value> { return yield* I2.invoke(origin, positional, keywords); })],
    ["__eq__", new Builtin("__eq__", function* (I2, positional): G<Value> {
      const other = positional[0];
      if (!(other instanceof NativeObject) || other.kind !== "GenericAlias") return NOT_IMPLEMENTED;
      return (yield* I2.eq(origin, other.props.get("__origin__")!)) &&
        (yield* I2.eq(args, other.props.get("__args__")!));
    })],
    ["__ne__", new Builtin("__ne__", function* (I2, positional): G<Value> {
      const result = yield* I2.invoke(alias.methods.get("__eq__")!, positional, new Map());
      return result === NOT_IMPLEMENTED ? result : !(yield* I2.truth(result));
    })],
    ["__hash__", new Builtin("__hash__", function* (): G<Value> {
      const hash = hashValue(origin) ^ hashValue(args);
      return hash === -1n ? -2n : hash;
    })],
  ]), new Map<string, Value>([["__origin__", origin], ["__args__", args], ["__parameters__", parameters(I, args.items)]]));
  alias.runtime = I;
  return alias;
}

/** Combine type operands, flattening nested unions and retaining operand order. */
export function unionType(I: Interp, left: Value, right: Value): Value | null {
  const args: Value[] = [];
  const provider = I.modules.get("_typing");
  const aliasType = provider instanceof DiasporeModule ? provider.frame.vars.get("TypeAliasType") : undefined;
  for (let value of [left, right]) {
    if (value === NONE) value = I.types.get("NoneType")!;
    if (value instanceof NativeObject && value.kind === "UnionType") {
      for (const member of (value.props.get("__args__") as DiasporeTuple).items) if (!args.includes(member)) args.push(member);
    } else if (value instanceof BuiltinType || value instanceof DiasporeClass ||
        (value instanceof DiasporeInstance && value.cls === aliasType) ||
        (value instanceof NativeObject && value.kind === "GenericAlias")) {
      if (!args.includes(value)) args.push(value);
    } else return null;
  }
  if (args.length === 1) return args[0];
  return new NativeObject("UnionType", new Map(), new Map<string, Value>([["__args__", new DiasporeTuple(args)], ["__parameters__", parameters(I, args)]]));
}
