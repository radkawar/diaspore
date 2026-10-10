/* VM metadata is represented by sandbox values, never host reflection. */
import { type Value, DiasporeFunction, DiasporeTuple, DiasporeNamespace, DiasporeInstance, DiasporeDict,
  CodeObject, NativeObject, PyNoneT, NONE, DictionaryMap, diasporeErr, type DiasporeError } from "./values.ts";
import { Frame, type Interp } from "./interp.ts";

const codes = new WeakMap<DiasporeFunction, CodeObject>();
const closures = new WeakMap<DiasporeFunction, DiasporeTuple>();
const frameCells = new WeakMap<Frame, Map<string, NativeObject>>();
const frameObjects = new WeakMap<Frame, NativeObject>();
const templates = new WeakMap<CodeObject, Record<string, unknown>>();
const freeNames = new WeakMap<DiasporeFunction, DiasporeTuple>();

class CellProperties extends Map<string, Value> {
  private frame: Frame;
  private name: string;
  constructor(frame: Frame, name: string) { super(); this.frame = frame; this.name = name; }
  override get(key: string): Value | undefined {
    if (key !== "cell_contents") return undefined;
    const value = this.frame.vars.get(this.name);
    if (value === undefined) throw diasporeErr("ValueError", "Cell is empty");
    return value;
  }
  override set(key: string, value: Value): this {
    if (key !== "cell_contents") throw diasporeErr("AttributeError", "cell has no such attribute");
    this.frame.vars.set(this.name, value);
    return this;
  }
  override delete(key: string): boolean {
    if (key !== "cell_contents") throw diasporeErr("AttributeError", "cell has no such attribute");
    return this.frame.vars.delete(this.name);
  }
}

/** Resolve free variables to shared, writable lexical cells. */
export function functionClosure(fn: DiasporeFunction): Value {
  const cached = closures.get(fn);
  if (cached !== undefined) return cached.items.length ? cached : NONE;
  const names = new Set(fn.ndecl);
  const visit = (node: unknown, bound: Set<string> = new Set()): void => {
    if (Array.isArray(node)) { for (const item of node) visit(item, bound); return; }
    if (node === null || typeof node !== "object") return;
    const object = node as Record<string, unknown>;
    if (object.op === "name" && typeof object.id === "string" && !bound.has(object.id)) names.add(object.id);
    if (object.op === "def" || object.op === "lambda") {
      visit(object.params, bound);
      visit(object.decorators, bound);
      const nested = new Set(bound);
      for (const name of [...(object.locals as string[] ?? []), ...(object.gdecl as string[] ?? [])]) nested.add(name);
      for (const name of object.ndecl as string[] ?? []) if (!nested.has(name)) names.add(name);
      visit(object.body, nested);
      return;
    }
    for (const value of Object.values(object)) visit(value, bound);
  };
  visit(fn.node.body);
  const cells: Value[] = [], free: string[] = [];
  for (const name of [...names].sort()) {
    if (fn.localset.has(name) || fn.gdecl.has(name)) continue;
    let frame = fn.closure;
    while (frame !== null && (frame.localset === null || !frame.localset.has(name))) frame = frame.parent;
    if (frame === null) continue;
    let mapping = frameCells.get(frame);
    if (mapping === undefined) { mapping = new Map(); frameCells.set(frame, mapping); }
    let cell = mapping.get(name);
    if (cell === undefined) {
      cell = new NativeObject("cell", new Map(), new CellProperties(frame, name));
      mapping.set(name, cell);
    }
    cells.push(cell); free.push(name);
  }
  const result = new DiasporeTuple(cells);
  closures.set(fn, result);
  freeNames.set(fn, new DiasporeTuple(free));
  return cells.length ? result : NONE;
}

/** Return the stable code object describing a VM function's actual IR. */
export function functionCode(I: Interp, fn: DiasporeFunction): CodeObject {
  let code = codes.get(fn);
  if (code !== undefined) return code;
  functionClosure(fn);
  code = new CodeObject(fn.node.body as unknown[] ?? [], "exec", I.moduleCode(fn.moduleName)?.file ?? fn.moduleName);
  const parameters = [...fn.params.args, ...fn.params.kwonly];
  if (fn.params.vararg) parameters.push(fn.params.vararg);
  if (fn.params.kwarg) parameters.push(fn.params.kwarg);
  const variables = [...parameters, ...[...fn.localset].filter(name => !parameters.includes(name))];
  code.attrs = new Map<string, Value>([
    ["co_name", fn.name], ["co_qualname", fn.metadata.get("__qualname__") ?? fn.name], ["co_filename", code.filename],
    ["co_firstlineno", BigInt(fn.node.ln as number ?? 1)], ["co_argcount", BigInt(fn.params.args.length)],
    ["co_posonlyargcount", BigInt(fn.params.posonly)], ["co_kwonlyargcount", BigInt(fn.params.kwonly.length)],
    ["co_nlocals", BigInt(variables.length)], ["co_varnames", new DiasporeTuple(variables)],
    ["co_freevars", freeNames.get(fn)!],
    ["co_flags", BigInt(3 | (fn.params.vararg ? 4 : 0) | (fn.params.kwarg ? 8 : 0) | (fn.asyncgen ? 512 : fn.coro ? 128 : fn.gen ? 32 : 0))],
  ]);
  codes.set(fn, code);
  templates.set(code, fn.node);
  return code;
}


class ClosureBindings extends Map<string, Value> {
  readonly cells: Map<string, NativeObject>;
  constructor(cells: Map<string, NativeObject>) { super(); this.cells = cells; }
  override get(key: string): Value | undefined {
    const cell = this.cells.get(key);
    return cell === undefined ? undefined : cell.props.get("cell_contents");
  }
  override has(key: string): boolean { return this.cells.has(key); }
  override set(key: string, value: Value): this {
    const cell = this.cells.get(key);
    if (cell !== undefined) cell.props.set("cell_contents", value);
    return this;
  }
  override delete(key: string): boolean { return this.cells.get(key)?.props.delete("cell_contents") ?? false; }
}

function closureFrame(globals: Map<string, Value>, moduleName: string, names: DiasporeTuple, cells: DiasporeTuple): Frame {
  const bindings = new Map<string, NativeObject>();
  names.items.forEach((name, index) => bindings.set(name as string, cells.items[index] as NativeObject));
  const global = new Frame(globals, null, null, new Set(), new Set(), false, globals, "<module>", moduleName);
  const frame = new Frame(new ClosureBindings(bindings), global, new Set(bindings.keys()), new Set(), new Set(), false, globals, "<closure>", moduleName);
  frameCells.set(frame, bindings);
  return frame;
}

/** Build an executable function from VM code and supplied sandbox cells. */
export function functionFromCode(_I: Interp, args: Value[], kwargs: Map<string, Value>): DiasporeFunction {
  if (args.length > 5) throw diasporeErr("TypeError", "function() takes from 2 to 5 arguments");
  const parameters = ["code", "globals", "name", "argdefs", "closure"];
  const values = args.slice();
  for (const [name, value] of kwargs) {
    const index = parameters.indexOf(name);
    if (index < 0) throw diasporeErr("TypeError", `function() got an unexpected keyword argument '${name}'`);
    if (index < args.length) throw diasporeErr("TypeError", `function() got multiple values for argument '${name}'`);
    values[index] = value;
  }
  if (values[0] === undefined || values[1] === undefined) throw diasporeErr("TypeError", "function() requires code and globals");
  const [code, globals, name = NONE, defaults = NONE, closure = NONE] = values;
  if (!(code instanceof CodeObject)) throw diasporeErr("TypeError", "function() argument 'code' must be a code object");
  if (!(globals instanceof DiasporeDict)) throw diasporeErr("TypeError", "function() argument 'globals' must be a dict");
  if (!(typeof name === "string" || name instanceof PyNoneT)) throw diasporeErr("TypeError", "function() argument 'name' must be a string or None");
  if (!(defaults instanceof DiasporeTuple || defaults instanceof PyNoneT)) throw diasporeErr("TypeError", "function() argument 'argdefs' must be a tuple or None");
  if (!(closure instanceof DiasporeTuple || closure instanceof PyNoneT)) throw diasporeErr("TypeError", "function() argument 'closure' must be a tuple or None");
  const names = code.attrs.get("co_freevars") as DiasporeTuple | undefined ?? new DiasporeTuple([]);
  if (names.items.length && closure instanceof PyNoneT) throw diasporeErr("TypeError", "arg 5 (closure) must be tuple");
  const cells = closure instanceof DiasporeTuple ? closure : new DiasporeTuple([]);
  if (cells.items.length !== names.items.length) throw diasporeErr("ValueError", "closure length does not match code free variables");
  if (cells.items.some(cell => !(cell instanceof NativeObject) || cell.kind !== "cell")) throw diasporeErr("TypeError", "closure must contain only cells");
  const node = templates.get(code) ?? { name: "<module>", params: {}, locals: [], body: code.nodes };
  const moduleName = globals.get("__name__");
  const fn = new DiasporeFunction(node, closureFrame(new DictionaryMap(globals), typeof moduleName === "string" ? moduleName : "", names, cells), typeof moduleName === "string" ? moduleName : "");
  if (typeof name === "string") fn.name = name;
  fn.defaults = defaults instanceof DiasporeTuple ? defaults.items : [];
  fn.metadata.set("__qualname__", code.attrs.get("co_qualname") ?? node.name as string);
  const body = node.body as Record<string, any>[] | undefined;
  const doc = body?.[0];
  fn.metadata.set("__doc__", doc?.op === "expr" && doc.v?.op === "const" && doc.v.t === "str" ? _I.constVal(doc.v) : NONE);
  fn.metadata.set("__globals__", globals);
  closures.set(fn, cells); freeNames.set(fn, names); codes.set(fn, code);
  return fn;
}

/** Code replacement retains cell identities and binds the new free-variable names. */
export function replaceFunctionCode(_I: Interp, fn: DiasporeFunction, code: CodeObject): void {
  const oldClosure = functionClosure(fn);
  const cells = oldClosure instanceof DiasporeTuple ? oldClosure : new DiasporeTuple([]);
  const names = code.attrs.get("co_freevars") as DiasporeTuple | undefined ?? new DiasporeTuple([]);
  if (names.items.length !== cells.items.length) throw diasporeErr("ValueError", `${fn.name}() requires a code object with ${cells.items.length} free vars, not ${names.items.length}`);
  const node = templates.get(code) ?? { name: "<module>", params: {}, locals: [], body: code.nodes };
  const replacement = new DiasporeFunction(node, fn.closure, fn.moduleName);
  fn.node = node; fn.params = replacement.params; fn.localset = replacement.localset;
  fn.gdecl = replacement.gdecl; fn.ndecl = replacement.ndecl;
  fn.gen = replacement.gen; fn.coro = replacement.coro; fn.asyncgen = replacement.asyncgen;
  fn.closure = closureFrame(fn.closure?.globals ?? new Map(), fn.moduleName, names, cells);
  closures.set(fn, cells); freeNames.set(fn, names); codes.set(fn, code);
}

class FrameProperties extends Map<string, Value> {
  private runtime: Interp;
  private frame: Frame;
  constructor(runtime: Interp, frame: Frame) { super(); this.runtime = runtime; this.frame = frame; }
  override get(key: string): Value | undefined {
    switch (key) {
      case "f_lineno": return BigInt(this.frame.line);
      case "f_back": return this.frame.back === null ? NONE : frameObject(this.runtime, this.frame.back);
      default: return super.get(key);
    }
  }
  sourceLine(line: number): string | undefined {
    const module = this.runtime.moduleCode(this.frame.moduleName);
    const code = super.get("f_code");
    if (module === undefined || !(code instanceof CodeObject) || code.filename !== module.file) return undefined;
    const source = module.src as string[] | undefined;
    return source !== undefined && line > 0 && line <= source.length ? source[line - 1] : undefined;
  }
}

/** Return stable, live metadata for an actual sandbox frame. */
export function frameObject(I: Interp, frame: Frame): NativeObject {
  let object = frameObjects.get(frame);
  if (object !== undefined) return object;
  const code = frame.code ?? new CodeObject(I.moduleCode(frame.moduleName)?.body ?? [], "exec", I.moduleCode(frame.moduleName)?.file ?? frame.moduleName);
  if (!code.attrs.has("co_name")) code.attrs.set("co_name", frame.fname);
  const properties = new FrameProperties(I, frame);
  properties.set("f_code", code);
  properties.set("f_globals", new DiasporeNamespace(frame.globals));
  properties.set("f_locals", new DiasporeNamespace(frame.vars));
  properties.set("f_builtins", new DiasporeNamespace(I.builtins));
  object = new NativeObject("frame", new Map(), properties);
  frameObjects.set(frame, object);
  return object;
}

/** Resolve embedded source through the actual frame, never mutable Python globals. */
export function tracebackSource(frame: Value, line: number): string | undefined {
  return frame instanceof NativeObject && frame.props instanceof FrameProperties ?
    frame.props.sourceLine(line) : undefined;
}

/** Link traceback nodes to the real sandbox frames traversed by an exception. */
export function attachTraceback(I: Interp, error: DiasporeError, frame: Frame): void {
  if (!(error.value instanceof DiasporeInstance)) return;
  const object = frameObject(I, frame);
  const previous = error.value.exc.get("__traceback__") ?? NONE;
  if (previous instanceof NativeObject && previous.props.get("tb_frame") === object) return;
  error.value.exc.set("__traceback__", new NativeObject("traceback", new Map(), new Map<string, Value>([
    ["tb_frame", object], ["tb_lineno", BigInt(frame.line)], ["tb_lasti", BigInt(frame.line)], ["tb_next", previous],
  ])));
}
