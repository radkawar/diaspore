/* Python interpolation over sandbox values and their conversion protocols. */
import { type Value, type G, type KwArgs, type PyString, DiasporeTuple, DiasporeList, DiasporeDict, DiasporeInstance, DiasporeError, asIndex, asFloat, diasporeErr, makeExc, chars, bytesToBinStr, isPyString, strFromChars, strJoin, strConcat, strText, typeName } from "./values.ts";
import type { Interp } from "./interp.ts";
import { getIndex, asciiOf } from "./ops.ts";
import { formatValue } from "./format.ts";
import { bufferBytes } from "./buffers.ts";

function mappingOperand(value: Value): boolean {
  return value instanceof DiasporeDict || value instanceof DiasporeList || value instanceof Uint8Array || (value instanceof DiasporeInstance && (value.cls.lookup("__getitem__") !== undefined || (value.prim !== null && mappingOperand(value.prim))));
}

function* integerOperand(I: Interp, value: Value, decimal: boolean): G<bigint | null> {
  if (value instanceof DiasporeInstance) {
    const method = decimal && value.cls.lookup("__int__") !== undefined ? "__int__" : "__index__";
    if (value.cls.lookup(method) !== undefined) {
      const result = yield* I.callDunder(value, method, []);
      const n = asIndex(result);
      if (n === null) throw diasporeErr("TypeError", `${method} returned non-int (type ${typeName(result)})`);
      return n;
    }
    if (value.prim !== null) return yield* integerOperand(I, value.prim, decimal);
  }
  if (decimal && typeof value === "number") {
    if (Number.isNaN(value)) throw diasporeErr("ValueError", "cannot convert float NaN to integer");
    if (!Number.isFinite(value)) throw diasporeErr("OverflowError", "cannot convert float infinity to integer");
    return BigInt(Math.trunc(value));
  }
  return asIndex(value);
}

function* floatOperand(I: Interp, value: Value): G<number> {
  if (value instanceof DiasporeInstance) {
    if (value.cls.lookup("__float__") !== undefined) {
      const result = yield* I.callDunder(value, "__float__", []);
      if (typeof result !== "number") throw diasporeErr("TypeError", `${typeName(value)}.__float__ returned non-float (type ${typeName(result)})`);
      return result;
    }
    if (value.prim !== null) return yield* floatOperand(I, value.prim);
    const integer = yield* integerOperand(I, value, false);
    if (integer !== null) value = integer;
  }
  const result = asFloat(value);
  if (result === null) throw diasporeErr("TypeError", `must be real number, not ${typeName(value)}`);
  if (typeof value === "bigint" && !Number.isFinite(result)) throw diasporeErr("OverflowError", "int too large to convert to float");
  return result;
}

/** Apply old-style string or bytes interpolation without host coercion. */
export function* percentFormat(I: Interp, template: PyString | Uint8Array, values: Value): G<Value> {
  const binary = template instanceof Uint8Array;
  const source = chars(binary ? bytesToBinStr(template) : template);
  const positional = values instanceof DiasporeTuple ? values.items : [values];
  const isMapping = mappingOperand(values) && !(binary && (values instanceof Uint8Array || (values instanceof DiasporeInstance && values.cls.mro.some(cls => cls.name === "bytearray"))));
  let used = 0, mapping = false;
  const output: PyString[] = [];
  const take = (): Value => {
    if (used >= positional.length) throw diasporeErr("TypeError", "not enough arguments for format string");
    return positional[used++];
  };
  for (let cursor = 0; cursor < source.length;) {
    if (source[cursor] !== "%") { output.push(source[cursor++]); continue; }
    cursor++;
    if (source[cursor] === "%") { output.push("%"); cursor++; continue; }
    let key: Value | null = null;
    if (source[cursor] === "(") {
      if (!isMapping) throw diasporeErr("TypeError", "format requires a mapping");
      const start = ++cursor;
      let depth = 1;
      while (cursor < source.length && depth) {
        if (source[cursor] === "(") depth++;
        if (source[cursor] === ")") depth--;
        if (depth) cursor++;
      }
      if (depth) throw diasporeErr("ValueError", "incomplete format key");
      const name = strFromChars(source.slice(start, cursor++));
      key = binary ? Uint8Array.from(strText(name), char => char.charCodeAt(0)) : name;
      mapping = true;
    }
    // Mapping lookup precedes star consumption, and consumes the sole operand.
    const mapped = key === null ? null : yield* getIndex(I, values, key);
    if (key !== null) used = positional.length;
    let flags = "";
    while (cursor < source.length && "#0- +".includes(source[cursor])) flags += source[cursor++];
    let width = 0;
    if (source[cursor] === "*") {
      cursor++; const value = asIndex(take());
      if (value === null) throw diasporeErr("TypeError", "* wants int");
      width = Number(value); if (width < 0) { flags += "-"; width = -width; }
    } else while (/^[0-9]$/.test(source[cursor] ?? "")) width = width * 10 + Number(source[cursor++]);
    let precision: number | null = null;
    if (source[cursor] === ".") {
      cursor++; precision = 0;
      if (source[cursor] === "*") {
        cursor++; const value = asIndex(take());
        if (value === null) throw diasporeErr("TypeError", "* wants int");
        precision = Math.max(0, Number(value));
      } else while (/^[0-9]$/.test(source[cursor] ?? "")) precision = precision * 10 + Number(source[cursor++]);
    }
    if ("hlL".includes(source[cursor] ?? "\0")) cursor++;
    const conversion = source[cursor++];
    if (conversion === undefined) throw diasporeErr("ValueError", "incomplete format");
    const value = key === null ? take() : mapped!;
    let text: PyString;
    let numeric = false;
    if ("sra".includes(conversion) || (binary && conversion === "b")) {
      if (binary && (conversion === "s" || conversion === "b")) {
        let bytes = bufferBytes(value);
        if (bytes === null && value instanceof DiasporeInstance && value.cls.lookup("__bytes__") !== undefined) {
          const result = yield* I.callDunder(value, "__bytes__", []);
          if (!(result instanceof Uint8Array)) throw diasporeErr("TypeError", `__bytes__ returned non-bytes (type ${typeName(result)})`);
          bytes = result;
        }
        if (bytes === null) throw diasporeErr("TypeError", `%b requires a bytes-like object, or an object that implements __bytes__, not '${typeName(value)}'`);
        text = bytesToBinStr(bytes);
      } else if (conversion === "s") text = yield* I.str(value);
      else if (conversion === "r") text = binary ? yield* asciiOf(I, value) : yield* I.repr(value);
      else text = yield* asciiOf(I, value);
      if (precision !== null) text = strFromChars(chars(text).slice(0, precision));
    } else if (conversion === "c") {
      const n = yield* integerOperand(I, value, false);
      if (n !== null) {
        if (n < 0n || n > (binary ? 255n : 0x10ffffn)) throw diasporeErr("OverflowError", binary ? "%c arg not in range(256)" : "%c arg not in range(0x110000)");
        text = String.fromCodePoint(Number(n));
      } else if (!binary && isPyString(value) && chars(value).length === 1) text = value;
      else if (binary && bufferBytes(value)?.length === 1) text = String.fromCharCode(bufferBytes(value)![0]);
      else throw diasporeErr("TypeError", binary ? "%c requires an integer in range(256) or a single byte" : "%c requires int or char");
    } else if ("diuoxXeEfFgG".includes(conversion)) {
      numeric = true;
      const sign = flags.includes("+") ? "+" : flags.includes(" ") ? " " : "";
      if ("diuoxX".includes(conversion)) {
        const decimal = "diu".includes(conversion);
        const integer = yield* integerOperand(I, value, decimal);
        if (integer === null) throw diasporeErr("TypeError", `%${conversion} format: ${decimal ? "a real number" : "an integer"} is required, not ${typeName(value)}`);
        const base = conversion === "o" ? 8 : "xX".includes(conversion) ? 16 : 10;
        let digits = (integer < 0n ? -integer : integer).toString(base);
        if (conversion === "X") digits = digits.toUpperCase();
        if (precision !== null) digits = digits.padStart(precision, "0");
        const prefix = flags.includes("#") && base !== 10 ? (base === 8 ? "0o" : conversion === "X" ? "0X" : "0x") : "";
        text = (integer < 0n ? "-" : sign) + prefix + digits;
      } else {
        const number = yield* floatOperand(I, value);
        text = yield* formatValue(I, number, sign + (flags.includes("#") ? "#" : "") + "." + (precision ?? 6) + conversion);
      }
    } else throw diasporeErr("ValueError", `unsupported format character '${conversion}' (0x${conversion.codePointAt(0)!.toString(16)}) at index ${cursor - 1}`);
    const padding = width - chars(text).length;
    if (padding > 0) {
      I.fuelN(padding);
      if (flags.includes("-")) text = strConcat(text, " ".repeat(padding));
      else if (numeric && flags.includes("0")) {
        const host = strText(text);
        const prefix = /^[+ -]?(?:0[xXo])?/.exec(host)![0];
        text = prefix + "0".repeat(padding) + host.slice(prefix.length);
      } else text = strConcat(" ".repeat(padding), text);
    }
    output.push(text);
  }
  if (!mapping && used !== positional.length && !(used === 0 && isMapping)) throw diasporeErr("TypeError", `not all arguments converted during ${binary ? "bytes" : "string"} formatting`);
  return binary ? Uint8Array.from(strText(strJoin(output)), char => char.charCodeAt(0)) : strJoin(output);
}

function fieldIndex(name: string): bigint | null {
  if (!/^\p{Decimal_Number}+$/u.test(name)) return null;
  let result = 0n;
  for (const point of name) {
    const cp = point.codePointAt(0)!;
    let start = cp;
    while (start > 0 && /^\p{Decimal_Number}$/u.test(String.fromCodePoint(start - 1))) start--;
    result = result * 10n + BigInt((cp - start) % 10);
  }
  return result;
}

/** Resolve replacement fields, conversions and recursively nested format specs. */
export function* braceFormat(I: Interp, source: PyString, args: Value[], keywords: KwArgs, mapping?: Value): G<PyString> {
  let automatic = 0;
  let numbering: "auto" | "manual" | null = null;
  function* render(template: PyString, depth: number): G<PyString> {
    if (depth > 2) throw diasporeErr("ValueError", "Max string recursion exceeded");
    const points = chars(template), output: PyString[] = [];
    for (let cursor = 0; cursor < points.length;) {
      const character = points[cursor++];
      if (character !== "{" && character !== "}") { output.push(character); continue; }
      if (points[cursor] === character) { output.push(character); cursor++; continue; }
      if (character === "}" || cursor === points.length) throw diasporeErr("ValueError", `Single '${character}' encountered in format string`);
      const start = cursor;
      let bracket = false;
      while (cursor < points.length) {
        const point = points[cursor];
        if (point === "[") bracket = true;
        if (point === "]") bracket = false;
        if (!bracket && (point === "!" || point === ":" || point === "}")) break;
        if (!bracket && point === "{") throw diasporeErr("ValueError", "unexpected '{' in field name");
        cursor++;
      }
      if (cursor === points.length) throw diasporeErr("ValueError", "expected '}' before end of string");
      const name = points.slice(start, cursor);
      let conversion = "", spec: PyString = "";
      if (points[cursor] === "!") {
        conversion = points[++cursor] ?? "";
        if (!conversion) throw diasporeErr("ValueError", "end of string while looking for conversion specifier");
        cursor++;
        if (cursor < points.length && points[cursor] !== ":" && points[cursor] !== "}") throw diasporeErr("ValueError", "expected ':' after conversion specifier");
      }
      if (points[cursor] === ":") {
        const specStart = ++cursor;
        let nesting = 0;
        while (cursor < points.length) {
          if (points[cursor] === "{") nesting++;
          if (points[cursor] === "}") { if (nesting === 0) break; nesting--; }
          cursor++;
        }
        spec = strFromChars(points.slice(specStart, cursor));
      }
      if (cursor === points.length) throw diasporeErr("ValueError", "unmatched '{' in format spec");
      cursor++;
      let offset = name.findIndex(point => point === "." || point === "[");
      if (offset < 0) offset = name.length;
      const rootPoints = name.slice(0, offset);
      const root = rootPoints.join("");
      let index = fieldIndex(root), value: Value;
      if (root === "") {
        if (numbering === "manual") throw diasporeErr("ValueError", "cannot switch from manual field specification to automatic field numbering");
        numbering = "auto"; index = BigInt(automatic++);
      } else if (index !== null) {
        if (numbering === "auto") throw diasporeErr("ValueError", "cannot switch from automatic field numbering to manual field specification");
        numbering = "manual";
      }
      if (index !== null) {
        if (mapping !== undefined) throw diasporeErr("ValueError", "Format string contains positional fields");
        if (index >= BigInt(args.length)) throw diasporeErr("IndexError", `Replacement index ${index} out of range for positional args tuple`);
        value = args[Number(index)];
      } else if (mapping !== undefined) value = yield* getIndex(I, mapping, strFromChars(rootPoints));
      else {
        const item = keywords.get(root);
        if (item === undefined) throw new DiasporeError(makeExc("KeyError", root));
        value = item;
      }
      while (offset < name.length) {
        const kind = name[offset++];
        if (kind === ".") {
          const start = offset;
          while (offset < name.length && name[offset] !== "." && name[offset] !== "[") offset++;
          if (start === offset) throw diasporeErr("ValueError", "Empty attribute in format string");
          value = yield* I.getAttr(value, name.slice(start, offset).join(""));
        } else if (kind === "[") {
          const end = name.indexOf("]", offset);
          if (end < 0) throw diasporeErr("ValueError", "Missing ']' in format string");
          const key = name.slice(offset, end); offset = end + 1;
          if (key.length === 0) throw diasporeErr("ValueError", "Empty attribute in format string");
          value = yield* getIndex(I, value, fieldIndex(key.join("")) ?? strFromChars(key));
        } else throw diasporeErr("ValueError", "Only '.' or '[' may follow ']' in format field specifier");
      }
      if (conversion === "s") value = yield* I.str(value);
      else if (conversion === "r") value = yield* I.repr(value);
      else if (conversion === "a") value = yield* asciiOf(I, value);
      else if (conversion) throw diasporeErr("ValueError", `Unknown conversion specifier ${conversion}`);
      output.push(yield* formatValue(I, value, yield* render(spec, depth + 1)));
    }
    return strJoin(output);
  }
  return yield* render(source, 0);
}
