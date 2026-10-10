/* POSIX OSError construction and subclass selection, matching CPython 3.12. */

import {
  type G, type Value, type KwArgs, DiasporeClass, DiasporeInstance, DiasporeTuple,
  EXC_CLASSES, NONE, Property, PyNoneT, StaticMethodV, diasporeErr, typeName,
} from "./values.ts";
import { bi } from "./builtins.ts";
import { indexInteger } from "./buffers.ts";
import type { Interp } from "./interp.ts";
import { charactersWritten, initializeOSError, selectOSErrorClass } from "./os-error-values.ts";

function usesInit(cls: DiasporeClass): boolean {
  const base = EXC_CLASSES.get("OSError")!;
  // Class lookup returns raw descriptors, including the inherited static __new__.
  return cls.lookup("__init__") !== base.ns.get("__init__") && cls.lookup("__new__") === base.ns.get("__new__");
}

function* initialize(I: Interp, instance: DiasporeInstance, args: Value[], kw: KwArgs): G<void> {
  if (kw.size !== 0) throw diasporeErr("TypeError", `${instance.cls.name}() takes no keyword arguments`);
  const filename = args.length >= 3 && args.length <= 5 ? args[2] : NONE;
  const raw = filename instanceof DiasporeInstance && filename.prim !== null ? filename.prim : filename;
  let written: bigint | undefined;
  if (instance.cls === EXC_CLASSES.get("BlockingIOError") && !(filename instanceof PyNoneT) &&
      (typeof raw === "bigint" || typeof raw === "number" || typeof raw === "boolean" ||
       filename instanceof DiasporeInstance && (filename.cls.lookup("__index__") !== undefined ||
         filename.cls.lookup("__int__") !== undefined || filename.cls.lookup("__float__") !== undefined))) {
    written = yield* indexInteger(I, filename);
    if (written < -9223372036854775808n || written > 9223372036854775807n) {
      throw diasporeErr("ValueError", `cannot fit '${typeName(filename)}' into an index-sized integer`);
    }
  }
  initializeOSError(instance, args, written);
}

/** Install OSError's native allocation and deferred subclass initialization hooks. */
export function installOSErrorProtocol(): void {
  const cls = EXC_CLASSES.get("OSError")!;
  for (const name of ["errno", "strerror", "filename", "filename2"]) cls.ns.set(name, NONE);
  cls.ns.set("characters_written", new Property(
    bi("characters_written", function* (_I, args): G<Value> {
      const written = charactersWritten.get(args[0] as DiasporeInstance);
      if (written === undefined || written === -1n) throw diasporeErr("AttributeError", "characters_written");
      return written;
    }),
    bi("characters_written", function* (I, args): G<Value> {
      const written = yield* indexInteger(I, args[1]);
      if (written < -9223372036854775808n || written > 9223372036854775807n) {
        throw diasporeErr("OverflowError", "Python int too large to convert to C ssize_t");
      }
      charactersWritten.set(args[0] as DiasporeInstance, written);
      return NONE;
    }),
    bi("characters_written", function* (_I, args): G<Value> {
      const instance = args[0] as DiasporeInstance;
      const written = charactersWritten.get(instance);
      if (written === undefined || written === -1n) throw diasporeErr("AttributeError", "characters_written");
      charactersWritten.delete(instance);
      return NONE;
    }),
  ));
  cls.ns.set("__new__", new StaticMethodV(bi("__new__", function* (I, args, kw): G<Value> {
    const requested = args[0];
    if (!(requested instanceof DiasporeClass) || !requested.issub(cls)) {
      throw diasporeErr("TypeError", "OSError.__new__ requires an OSError subtype");
    }
    const values = args.slice(1);
    const defer = usesInit(requested);
    const type = defer ? requested : selectOSErrorClass(requested, values);
    const instance = new DiasporeInstance(type);
    if (defer) instance.exc.set("args", new DiasporeTuple());
    else yield* initialize(I, instance, values, kw);
    return instance;
  })));
  cls.ns.set("__init__", bi("__init__", function* (I, args, kw): G<Value> {
    const instance = args[0];
    if (!(instance instanceof DiasporeInstance) || !instance.cls.issub(cls)) {
      throw diasporeErr("TypeError", "OSError.__init__ requires an OSError instance");
    }
    if (usesInit(instance.cls)) yield* initialize(I, instance, args.slice(1), kw);
    return NONE;
  }));
}
