/* CPython's default sys.excepthook, operating only on sandbox values and frames. */

import {
  type G, type Value, type PyString, Builtin, BASE_EXC, EXC_CLASSES,
  CodeObject, DiasporeError, DiasporeInstance, DiasporeList, DiasporeTuple,
  DiasporeRange, DiasporeDict,
  NativeObject, NONE, PyNoneT, asIndex, isPyString, strConcat, strLen,
  strText, typeName, diasporeErr,
} from "./values.ts";
import { bi } from "./builtins.ts";
import { noKw } from "./helpers.ts";
import { diasporeLen, getIndex } from "./ops.ts";
import { encodeText, decodeBytes } from "./codecs.ts";
import { indexInteger } from "./buffers.ts";
import { tracebackSource } from "./introspection.ts";
import type { Interp } from "./interp.ts";

function exception(value: Value): value is DiasporeInstance {
  return value instanceof DiasporeInstance && value.cls.issub(BASE_EXC);
}

function traceback(value: Value): value is NativeObject {
  return value instanceof NativeObject && value.kind === "traceback";
}

function* optional(I: Interp, value: Value, name: string): G<Value | undefined> {
  try {
    return yield* I.getAttr(value, name);
  } catch (error) {
    if (error instanceof DiasporeError && exception(error.value) &&
        error.value.cls.issub(EXC_CLASSES.get("AttributeError")!)) return undefined;
    throw error;
  }
}

function* safeString(I: Interp, value: Value, what: string, repr = false): G<PyString> {
  try {
    return repr ? yield* I.repr(value) : yield* I.str(value);
  } catch (error) {
    if (!(error instanceof DiasporeError)) throw error;
    return `<${what} ${repr ? "repr" : "str"}() failed>`;
  }
}

/** Display an exception using the current sys.stderr; the supplied type is irrelevant. */
export function* displayException(I: Interp, value: Value, suppliedTraceback: Value): G<void> {
  const sys = I.natives.get("sys")!;
  const file = sys.ns.get("stderr");
  if (file instanceof PyNoneT) return;
  if (file === undefined) {
    if (!I.replaying) I.ewrite("lost sys.stderr\n");
    return;
  }
  if (exception(value) && traceback(suppliedTraceback)) {
    const current = value.exc.get("__traceback__");
    if (current === undefined || current instanceof PyNoneT) value.exc.set("__traceback__", suppliedTraceback);
  }
  const seen = new Set<Value>();
  let depth = 0;
  let needClose = false;
  const indent = () => "  ".repeat(depth);
  const margin = () => indent() + (depth ? "| " : "");
  function* write(text: PyString): G<void> {
    yield* I.invoke(yield* I.getAttr(file!, "write"), [text], new Map());
  }
  function* line(text: PyString): G<void> {
    yield* write(strConcat(margin(), text));
  }
  function* frames(error: DiasporeInstance, group: boolean): G<void> {
    let tb = error.exc.get("__traceback__") ?? NONE;
    const limitValue = sys.ns.get("tracebacklimit");
    const limit = limitValue === undefined ? 1000n : asIndex(limitValue) ?? 1000n;
    if (limit <= 0n || !traceback(tb)) return;
    const entries: NativeObject[] = [];
    while (traceback(tb)) {
      entries.push(tb);
      tb = tb.props.get("tb_next") ?? NONE;
    }
    if (group) {
      yield* write(indent() + (depth === 1 ? "+ " : "| ") + "Exception Group Traceback (most recent call last):\n");
    } else {
      yield* line("Traceback (most recent call last):\n");
    }
    let previous = "";
    let repeats = 0;
    function* flushRepeats(): G<void> {
      if (repeats > 3) yield* line(`  [Previous line repeated ${repeats - 3} more time${repeats === 4 ? "" : "s"}]\n`);
    }
    const start = limit >= BigInt(entries.length) ? 0 : entries.length - Number(limit);
    for (let index = start; index < entries.length; index++) {
      const entry = entries[index];
      const frame = entry.props.get("tb_frame")!;
      const code = yield* I.getAttr(frame, "f_code");
      const filename = code instanceof CodeObject ? code.filename : strText(yield* I.str(yield* I.getAttr(code, "co_filename")));
      const name = strText(yield* I.str(yield* I.getAttr(code, "co_name")));
      const lineno = Number(asIndex(entry.props.get("tb_lineno")!) ?? 0n);
      const key = `${filename}\0${name}\0${lineno}`;
      if (key !== previous) {
        yield* flushRepeats();
        previous = key;
        repeats = 0;
      }
      if (++repeats > 3) continue;
      yield* line(`  File "${filename}", line ${lineno}, in ${name}\n`);
      const source = tracebackSource(frame, lineno);
      if (source !== undefined && source.trim()) yield* line(`    ${source.trim()}\n`);
    }
    yield* flushRepeats();
  }
  function* getNotes(error: DiasporeInstance): G<Value | undefined> {
    try {
      return yield* optional(I, error, "__notes__");
    } catch (failure) {
      if (!(failure instanceof DiasporeError)) throw failure;
      return new DiasporeList([strConcat("Ignored error getting __notes__: ", yield* I.repr(failure.value))]);
    }
  }
  function* notes(value: Value | undefined): G<void> {
    if (value === undefined) return;
    const primitive = value instanceof DiasporeInstance && value.prim !== null ? value.prim : value;
    const sequence = primitive instanceof DiasporeList || primitive instanceof DiasporeTuple ||
      primitive instanceof DiasporeRange || !(primitive instanceof DiasporeDict) &&
      value instanceof DiasporeInstance && value.cls.lookup("__getitem__") !== undefined;
    if (!sequence || isPyString(primitive) || primitive instanceof Uint8Array) {
      yield* line(strConcat(yield* safeString(I, value, "__notes__", true), "\n"));
      return;
    }
    const count = yield* diasporeLen(I, value);
    for (let index = 0n; index < count; index++) {
      const note = yield* safeString(I, yield* getIndex(I, value, index), "note");
      // Splitlines retains separators; a final newline is always printed for each note.
      const parts = (yield* I.invoke(yield* I.getAttr(note, "splitlines"), [true], new Map())) as DiasporeList;
      for (const part of parts.items) yield* line(part as PyString);
      yield* write("\n");
    }
  }
  function* syntax(error: DiasporeInstance): G<Value> {
    let message: Value, filename: Value, lineno: bigint, offset: bigint;
    let endLine: bigint, endOffset: bigint, text: Value;
    try {
      if ((yield* optional(I, error, "print_file_and_line")) === undefined) return error;
      message = yield* I.getAttr(error, "msg");
      filename = yield* I.getAttr(error, "filename");
      lineno = yield* indexInteger(I, yield* I.getAttr(error, "lineno"));
      const start = yield* I.getAttr(error, "offset");
      offset = start instanceof PyNoneT ? -1n : yield* indexInteger(I, start);
      const endLineValue = yield* optional(I, error, "end_lineno");
      endLine = endLineValue === undefined || endLineValue instanceof PyNoneT ? lineno : yield* indexInteger(I, endLineValue);
      const end = yield* optional(I, error, "end_offset");
      endOffset = end === undefined || end instanceof PyNoneT ? -1n : yield* indexInteger(I, end);
      text = yield* I.getAttr(error, "text");
    } catch (failure) {
      if (!(failure instanceof DiasporeError)) throw failure;
      return error;
    }
    yield* line(strConcat('  File "', filename instanceof PyNoneT ? "<string>" : yield* I.str(filename), `", line ${lineno}\n`));
    if (!(text instanceof PyNoneT)) {
      if (!isPyString(text)) throw diasporeErr("TypeError", "bad argument type for built-in operation");
      const bytes = encodeText(text);
      let end = endLine > lineno ? bytes.length : Number(endOffset);
      end = Math.min(end, bytes.length + 1);
      const count = end > 0 && end > Number(offset) ? end - Number(offset) : 1;
      let position = Number(offset) - 1, start = 0;
      while (bytes[start] === 32 || bytes[start] === 9 || bytes[start] === 12) { start++; position--; }
      let stop = bytes.indexOf(0, start);
      if (stop < 0) stop = bytes.length;
      let length = stop - start;
      if (length > 0 && bytes[stop - 1] === 10) length--;
      position = Math.min(position, length);
      for (;;) {
        const newline = bytes.indexOf(10, start);
        if (newline < 0 || newline >= stop || newline - start >= position) break;
        const skipped = newline - start + 1;
        start += skipped; position -= skipped; length -= skipped;
      }
      yield* write(strConcat("    ", decodeBytes(bytes.subarray(start, stop)), bytes[start + length] === 10 ? "" : "\n"));
      if (position >= 0) yield* write("    " + " ".repeat(position) + "^".repeat(count) + "\n");
    }
    return message;
  }
  function* single(error: DiasporeInstance, group: boolean): G<void> {
    yield* frames(error, group);
    const noteValues = yield* getNotes(error);
    const messageValue = yield* syntax(error);
    let module: Value | undefined;
    try { module = yield* I.getAttr(error.cls, "__module__"); }
    catch (failure) { if (!(failure instanceof DiasporeError)) throw failure; }
    const prefix = !isPyString(module) ? "<unknown>." :
      strText(module) === "builtins" || strText(module) === "__main__" ? "" : strConcat(module, ".");
    const qualified = error.cls.ns.get("__qualname__") ?? error.cls.name;
    const name = isPyString(qualified) ? qualified : "<unknown>";
    const message = messageValue instanceof PyNoneT ? "" : yield* safeString(I, messageValue, "exception");
    yield* line(strConcat(prefix, name, strLen(message) ? strConcat(": ", message) : "", "\n"));
    yield* notes(noteValues);
  }
  function* render(error: Value): G<void> {
    seen.add(error);
    if (!exception(error)) {
      yield* line(`TypeError: print_exception(): Exception expected for value, ${typeName(error)} found\n`);
      return;
    }
    const cause = error.exc.get("__cause__") ?? NONE;
    const context = error.exc.get("__context__") ?? NONE;
    const suppressed = yield* I.truth(error.exc.get("__suppress_context__") ?? false);
    const chained = exception(cause) ? cause : !suppressed && exception(context) ? context : NONE;
    if (exception(chained) && !seen.has(chained)) {
      const close = needClose;
      yield* render(chained);
      needClose = close;
      yield* line("\n");
      yield* line(chained === cause ? "The above exception was the direct cause of the following exception:\n" :
        "During handling of the above exception, another exception occurred:\n");
      yield* line("\n");
    }
    const groupClass = EXC_CLASSES.get("BaseExceptionGroup");
    const children = error.exc.get("exceptions");
    if (groupClass === undefined || !error.cls.issub(groupClass) || !(children instanceof DiasporeTuple)) {
      yield* single(error, false);
      return;
    }
    if (depth > 10) { yield* line("... (max_group_depth is 10)\n"); return; }
    if (depth === 0) depth++;
    yield* single(error, true);
    const count = Math.min(children.items.length, 16);
    needClose = false;
    for (let index = 0; index < count; index++) {
      const last = index === count - 1;
      if (last) needClose = true;
      yield* write(indent() + (index === 0 ? "+-" : "  ") + `+---------------- ${index === 15 ? "..." : index + 1} ----------------\n`);
      depth++;
      if (index === 15) {
        const remaining = children.items.length - 15;
        yield* line(`and ${remaining} more exception${remaining === 1 ? "" : "s"}\n`);
      } else yield* render(children.items[index]);
      if (last && needClose) { yield* write(indent() + "+------------------------------------\n"); needClose = false; }
      depth--;
    }
    if (depth === 1) depth--;
  }
  try {
    yield* render(value);
  } catch (error) {
    if (!(error instanceof DiasporeError)) throw error;
    if (!I.replaying) I.ewrite("lost sys.stderr\n");
  }
  try {
    yield* I.invoke(yield* I.getAttr(file, "flush"), [], new Map());
  } catch (error) {
    if (!(error instanceof DiasporeError)) throw error;
  }
}

/** Construct the shared sys.excepthook and sys.__excepthook__ callable. */
export function makeExceptionHook(): Builtin {
  return bi("excepthook", function* (I, args, kw): G<Value> {
    noKw("sys.excepthook", kw);
    if (args.length !== 3) throw diasporeErr("TypeError", `excepthook expected 3 arguments, got ${args.length}`);
    yield* displayException(I, args[1], args[2]);
    return NONE;
  });
}
