/* Python instance layouts and native data descriptors for declared slots. */

import type { Interp } from "./interp.ts";
import { iterToArray } from "./iter.ts";
import {
  type G, type Value, type PyString, type KwArgs,
  Builtin, BuiltinType, DiasporeClass, DiasporeInstance, NativeObject,
  OBJECT, BASE_EXC, EXC_CLASSES, NONE, isPyString, chars, strConcat,
  strFromChars, strFromKey, strKey, strText, strCmp, typeName, diasporeErr,
} from "./values.ts";

type LayoutOwner = DiasporeClass | BuiltinType;
type SlotKind = "slot" | "dict" | "weakref";

interface Layout {
  hasDict: boolean;
  hasWeakref: boolean;
  slotCount: number;
  solid: LayoutOwner;
  variableSize: boolean;
}

const layouts = new WeakMap<LayoutOwner, Layout>();
const storage = new WeakMap<DiasporeInstance, Map<SlotDescriptor, Value>>();

function layout(owner: LayoutOwner): Layout {
  const known = layouts.get(owner);
  if (known !== undefined) return known;
  let value: Layout;
  if (owner instanceof BuiltinType) {
    value = {
      hasDict: owner.name === "type" || owner.name === "classmethod" || owner.name === "staticmethod",
      hasWeakref: owner.name === "type" || owner.name === "set" || owner.name === "frozenset" || owner.name === "GenericAlias",
      slotCount: 0,
      solid: owner,
      variableSize: owner.name === "int" || owner.name === "tuple" || owner.name === "bytes" || owner.name === "type",
    };
  } else if (owner === OBJECT) {
    value = { hasDict: false, hasWeakref: false, slotCount: 0, solid: OBJECT, variableSize: false };
  } else if (EXC_CLASSES.get(owner.name) === owner) {
    value = { hasDict: true, hasWeakref: false, slotCount: 0, solid: BASE_EXC, variableSize: false };
  } else {
    // Internal runtime classes are ordinary dictionary-backed classes.
    value = { hasDict: true, hasWeakref: true, slotCount: 0, solid: OBJECT, variableSize: false };
  }
  layouts.set(owner, value);
  return value;
}

function subtype(left: LayoutOwner, right: LayoutOwner): boolean {
  if (left === right || right === OBJECT) return true;
  if (left instanceof DiasporeClass) {
    return right instanceof DiasporeClass ? left.issub(right) : left.prim === right;
  }
  return false;
}

/** Whether instances have an attribute dictionary independently of their slots. */
export function instanceHasDict(cls: DiasporeClass): boolean {
  return layout(cls).hasDict;
}

/** Whether the layout reserves weak-reference support. */
export function instanceHasWeakref(cls: DiasporeClass): boolean {
  return layout(cls).hasWeakref;
}

/** Number of physical member slots, including inherited and duplicate declarations. */
export function instanceSlotCount(cls: DiasporeClass): number {
  return layout(cls).slotCount;
}

/** Native member/getset descriptor with storage separate from the instance dictionary. */
export class SlotDescriptor extends NativeObject {
  readonly owner: DiasporeClass;
  readonly name: string;
  readonly slotKind: SlotKind;

  constructor(owner: DiasporeClass, name: string, slotKind: SlotKind = "slot") {
    super(slotKind === "slot" ? "member_descriptor" : "getset_descriptor", new Map());
    this.owner = owner;
    this.name = name;
    this.slotKind = slotKind;
    this.props.set("__objclass__", owner);
    this.props.set("__name__", strFromKey(name));
    const qualified = owner.ns.get("__qualname__");
    this.props.set("__qualname__", strConcat(isPyString(qualified) ? qualified : strFromKey(owner.name), ".", strFromKey(name)));
    this.props.set("__doc__", slotKind === "dict" ? "dictionary for instance variables"
      : slotKind === "weakref" ? "list of weak references to the object" : NONE);
    for (const method of ["__get__", "__set__", "__delete__"]) {
      const descriptor = this;
      const callable = new Builtin(method, function* (_I, args, kw): G<Value> {
        descriptor.checkArguments(method, args, kw);
        if (method === "__get__") {
          if (args[0] === NONE) {
            if (args.length < 2 || args[1] === NONE) throw diasporeErr("TypeError", "__get__(None, None) is invalid");
            return descriptor;
          }
          return descriptor.get(args[0]);
        }
        if (method === "__set__") descriptor.set(args[0], args[1]);
        else descriptor.delete(args[0]);
        return NONE;
      });
      callable.self = this;
      this.methods.set(method, callable);
    }
  }

  private checkArguments(method: string, args: Value[], kw: KwArgs): void {
    if (kw.size !== 0) throw diasporeErr("TypeError", "wrapper " + method + "() takes no keyword arguments");
    const minimum = method === "__set__" ? 2 : 1;
    const maximum = method === "__get__" ? 2 : minimum;
    if (args.length < minimum || args.length > maximum) {
      const expected = minimum === maximum ? `${minimum} argument${minimum === 1 ? "" : "s"}`
        : args.length < minimum ? `at least ${minimum} argument` : `at most ${maximum} arguments`;
      const prefix = method === "__delete__" ? "" : " ";
      throw diasporeErr("TypeError", `${prefix}expected ${expected}, got ${args.length}`);
    }
  }

  private instance(value: Value): DiasporeInstance {
    if (!(value instanceof DiasporeInstance) || !value.cls.issub(this.owner)) {
      throw diasporeErr("TypeError", strConcat("descriptor '", strFromKey(this.name), "' for '",
        strFromKey(this.owner.name), "' objects doesn't apply to a '", typeName(value), "' object"));
    }
    return value;
  }

  /** Read the slot, rejecting unset storage rather than consulting the instance dictionary. */
  get(value: Value): Value {
    const instance = this.instance(value);
    if (this.slotKind === "dict") return instance.getDict();
    if (this.slotKind === "weakref") return NONE;
    const result = storage.get(instance)?.get(this);
    if (result === undefined) {
      throw diasporeErr("AttributeError", strConcat("'", strFromKey(instance.cls.name),
        "' object has no attribute '", strFromKey(this.name), "'"));
    }
    return result;
  }

  /** Write this exact owner's slot, even when a subclass redeclares the same name. */
  set(value: Value, assigned: Value): void {
    const instance = this.instance(value);
    if (this.slotKind === "dict") {
      instance.setDict(assigned);
      return;
    }
    if (this.slotKind === "weakref") {
      throw diasporeErr("AttributeError", `attribute '__weakref__' of '${this.owner.name}' objects is not writable`);
    }
    let slots = storage.get(instance);
    if (slots === undefined) {
      slots = new Map();
      storage.set(instance, slots);
    }
    slots.set(this, assigned);
  }

  /** Delete initialized storage without affecting a shadowed slot or dictionary entry. */
  delete(value: Value): void {
    const instance = this.instance(value);
    if (this.slotKind === "dict") {
      instance.deleteDict();
      return;
    }
    if (this.slotKind === "weakref") {
      throw diasporeErr("AttributeError", `attribute '__weakref__' of '${this.owner.name}' objects is not writable`);
    }
    const slots = storage.get(instance);
    if (slots === undefined || !slots.delete(this)) throw diasporeErr("AttributeError", strFromKey(this.name));
    if (slots.size === 0) storage.delete(instance);
  }
}

/** Validate class layout and install descriptors before __set_name__/__init_subclass__. */
export function* initializeSlots(I: Interp, cls: DiasporeClass): G<void> {
  const bases = cls.declaredBases.length === 0 ? [OBJECT] : cls.declaredBases;
  let bestBase = bases[0];
  let inherited = layout(bestBase);
  let hasDict = false;
  let hasWeakref = false;
  for (const base of bases) {
    if (base instanceof BuiltinType && base.name === "bool") {
      throw diasporeErr("TypeError", "type 'bool' is not an acceptable base type");
    }
    const candidate = layout(base);
    hasDict ||= candidate.hasDict;
    hasWeakref ||= candidate.hasWeakref;
    if (candidate.solid === inherited.solid) continue;
    if (subtype(candidate.solid, inherited.solid)) {
      bestBase = base;
      inherited = candidate;
    } else if (!subtype(inherited.solid, candidate.solid)) {
      throw diasporeErr("TypeError", "multiple bases have instance lay-out conflict");
    }
  }
  let addDict = false;
  let addWeakref = false;
  const declared = cls.ns.get("__slots__");
  const names: string[] = [];
  if (declared === undefined) {
    addDict = !hasDict;
    addWeakref = !hasWeakref && !inherited.variableSize;
  } else {
    const primitive = declared instanceof DiasporeInstance ? declared.prim : declared;
    const items = isPyString(primitive) ? [primitive] : yield* iterToArray(I, declared);
    if (items.length !== 0 && inherited.variableSize) {
      throw diasporeErr("TypeError", `nonempty __slots__ not supported for subtype of '${bestBase.name}'`);
    }
    for (const item of items) {
      const name = item instanceof DiasporeInstance ? item.prim : item;
      if (!isPyString(name)) throw diasporeErr("TypeError", `__slots__ items must be strings, not '${typeName(item)}'`);
      const valid = yield* I.invoke(yield* I.getAttr(name, "isidentifier"), [], new Map());
      if (valid !== true) throw diasporeErr("TypeError", "__slots__ must be identifiers");
      const text = strText(name);
      if (text === "__dict__") {
        if (hasDict || addDict) throw diasporeErr("TypeError", "__dict__ slot disallowed: we already got one");
        addDict = true;
      } else if (text === "__weakref__") {
        if (hasWeakref || addWeakref) throw diasporeErr("TypeError", "__weakref__ slot disallowed: we already got one");
        addWeakref = true;
      } else {
        let mangled: PyString = name;
        if (text.startsWith("__") && !text.endsWith("__")) {
          const className = chars(strFromKey(cls.name));
          let first = 0;
          while (className[first] === "_") first++;
          if (first < className.length) mangled = strConcat("_", strFromChars(className.slice(first)), name);
        }
        const key = strKey(mangled);
        if (cls.ns.has(key)) {
          throw diasporeErr("ValueError", strConcat("'", mangled, "' in __slots__ conflicts with class variable"));
        }
        names.push(key);
      }
    }
  }
  names.sort((left, right) => strCmp(strFromKey(left), strFromKey(right)));
  const metadata: Layout = {
    hasDict: hasDict || addDict,
    hasWeakref: hasWeakref || addWeakref,
    slotCount: inherited.slotCount + names.length,
    solid: names.length === 0 ? inherited.solid : cls,
    variableSize: inherited.variableSize,
  };
  layouts.set(cls, metadata);
  for (let index = 0; index < names.length; index++) {
    if (index + 1 < names.length && names[index] === names[index + 1]) continue;
    cls.ns.set(names[index], new SlotDescriptor(cls, names[index]));
  }
  if (addDict && !cls.ns.has("__dict__")) cls.ns.set("__dict__", new SlotDescriptor(cls, "__dict__", "dict"));
  if (addWeakref && !cls.ns.has("__weakref__")) cls.ns.set("__weakref__", new SlotDescriptor(cls, "__weakref__", "weakref"));
}
