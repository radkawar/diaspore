import {
  type Value, type G, type PyString, NONE, Builtin, DiasporeClass,
  DiasporeInstance, DiasporeError, DiasporeTuple, EXC_CLASSES,
  asIndex, chars, isPyString, strJoin, diasporeErr,
} from "./values.ts";

function initialize(instance: DiasporeInstance, message: Value, pattern: Value, pos: Value, messageText: PyString): void {
  instance.d.set("msg", message);
  instance.d.set("pattern", pattern);
  instance.d.set("pos", pos);
  let rendered = message;
  let line: Value = NONE, column: Value = NONE;
  if (pattern !== NONE && pos !== NONE) {
    const index = asIndex(pos);
    if (index === null) throw diasporeErr("TypeError", "integer argument expected");
    const points = isPyString(pattern) ? chars(pattern) : pattern instanceof Uint8Array ? pattern : null;
    if (points === null) throw diasporeErr("TypeError", "pattern must be a string or bytes");
    const newline = pattern instanceof Uint8Array ? 10 : "\n";
    const length = BigInt(points.length);
    const end = Number(index < 0n ? index + length < 0n ? 0n : index + length : index > length ? length : index);
    let count = 1n, previous = -1n, multiline = false;
    for (let i = 0; i < points.length; i++) if (points[i] === newline) {
      multiline = true;
      if (i < end) { count++; previous = BigInt(i); }
    }
    line = count; column = index - previous;
    rendered = strJoin([messageText, ` at position ${index}`, multiline ? ` (line ${line}, column ${column})` : ""]);
  }
  instance.d.set("lineno", line);
  instance.d.set("colno", column);
  instance.exc.set("args", new DiasporeTuple([rendered]));
}

/** The native re.error exception has CPython's diagnostic attributes. */
export const REGEX_ERROR = new DiasporeClass("error", [EXC_CLASSES.get("Exception")!], new Map<string, Value>([
  ["__module__", "re"],
  ["__init__", new Builtin("__init__", function* (I, args, kwargs): G<Value> {
    if (!(args[0] instanceof DiasporeInstance) || args.length > 4) throw diasporeErr("TypeError", "error() takes at most 3 arguments");
    const names = ["msg", "pattern", "pos"], values = args.slice(1);
    for (const [name, value] of kwargs) {
      const i = names.indexOf(name);
      if (i < 0) throw diasporeErr("TypeError", `error() got an unexpected keyword argument '${name}'`);
      if (i < args.length - 1) throw diasporeErr("TypeError", `error() got multiple values for '${name}'`);
      values[i] = value;
    }
    if (values[0] === undefined) throw diasporeErr("TypeError", "error() missing required argument 'msg'");
    const pattern = values[1] ?? NONE, pos = values[2] ?? NONE;
    const rendered = pattern !== NONE && pos !== NONE ? yield* I.str(values[0]) : "";
    initialize(args[0], values[0], pattern, pos, rendered);
    return NONE;
  })],
]));

/** Construct parser and replacement failures without losing source coordinates. */
export function regexError(message: string, pattern: Value = NONE, pos: number | null = null): DiasporeError {
  const instance = new DiasporeInstance(REGEX_ERROR);
  initialize(instance, message, pattern, pos === null ? NONE : BigInt(pos), message);
  return new DiasporeError(instance);
}
