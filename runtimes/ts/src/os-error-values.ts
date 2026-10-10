/* OSError value initialization with no interpreter or builtin dependencies. */

import {
  type Value, DiasporeClass, DiasporeInstance, DiasporeTuple, EXC_CLASSES,
  PyNoneT, asIndex, diasporeErr, typeName,
} from "./values.ts";

const errnoClasses: Record<string, string> = {
  "1": "PermissionError", "2": "FileNotFoundError", "3": "ProcessLookupError",
  "4": "InterruptedError", "10": "ChildProcessError", "11": "BlockingIOError",
  "13": "PermissionError", "17": "FileExistsError", "20": "NotADirectoryError",
  "21": "IsADirectoryError", "32": "BrokenPipeError", "103": "ConnectionAbortedError",
  "104": "ConnectionResetError", "108": "BrokenPipeError", "110": "TimeoutError",
  "111": "ConnectionRefusedError", "114": "BlockingIOError", "115": "BlockingIOError",
};
/** Native characters_written storage shared by exception factories and descriptors. */
export const charactersWritten = new WeakMap<DiasporeInstance, bigint>();

/** Select errno-specific subclasses only when constructing OSError itself. */
export function selectOSErrorClass(cls: DiasporeClass, args: Value[]): DiasporeClass {
  if (cls !== EXC_CLASSES.get("OSError") || args.length < 2 || args.length > 5) return cls;
  const value = args[0] instanceof DiasporeInstance && args[0].prim !== null ? args[0].prim : args[0];
  const errno = asIndex(value);
  const name = errno === null ? undefined : errnoClasses[errno.toString()];
  return name === undefined ? cls : EXC_CLASSES.get(name)!;
}

/** Populate native exception fields without invoking user conversion callbacks. */
export function initializeOSError(instance: DiasporeInstance, args: Value[], written?: bigint): void {
  instance.exc.delete("errno");
  instance.exc.delete("strerror");
  let saved = args;
  if (args.length >= 2 && args.length <= 5) {
    instance.exc.set("errno", args[0]);
    instance.exc.set("strerror", args[1]);
    const filename = args[2];
    if (filename !== undefined && !(filename instanceof PyNoneT)) {
      const primitive = filename instanceof DiasporeInstance && filename.prim !== null ? filename.prim : filename;
      if (written === undefined && instance.cls === EXC_CLASSES.get("BlockingIOError") &&
          (typeof primitive === "bigint" || typeof primitive === "boolean" || typeof primitive === "number")) {
        const index = asIndex(primitive);
        if (index === null) throw diasporeErr("TypeError", `'${typeName(filename)}' object cannot be interpreted as an integer`);
        if (index < -9223372036854775808n || index > 9223372036854775807n) {
          throw diasporeErr("ValueError", `cannot fit '${typeName(filename)}' into an index-sized integer`);
        }
        written = index;
      }
      if (written !== undefined) {
        charactersWritten.set(instance, written);
      } else {
        instance.exc.set("filename", filename);
        if (args[4] !== undefined && !(args[4] instanceof PyNoneT)) instance.exc.set("filename2", args[4]);
        saved = args.slice(0, 2);
      }
    }
  }
  instance.exc.set("args", new DiasporeTuple(saved));
}
