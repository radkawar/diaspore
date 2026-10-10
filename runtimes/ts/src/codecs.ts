/* Bootstrap text codecs; extended codecs execute their bundled Python provider. */
import { type Value, type G, type PyString, chars, strText, strJoin, strConcat, diasporeErr, unicodeCodecError } from "./values.ts";
import type { Interp } from "./interp.ts";

const aliases: Record<string, string> = {
  utf8: "utf-8", utf_8: "utf-8", utf: "utf-8", u8: "utf-8", cp65001: "utf-8",
  ascii: "ascii", us_ascii: "ascii", "646": "ascii", "ansi_x3.4_1968": "ascii",
  ansi_x3_4_1968: "ascii", iso646_us: "ascii", us: "ascii",
  latin1: "latin-1", latin_1: "latin-1", latin: "latin-1", l1: "latin-1",
  iso_8859_1: "latin-1", iso8859_1: "latin-1", iso_8859_1_1987: "latin-1", iso8859: "latin-1", cp819: "latin-1", "8859": "latin-1", ibm819: "latin-1",
};
const handlers: Record<string, true> = { strict: true, ignore: true, replace: true, backslashreplace: true, surrogateescape: true, surrogatepass: true };

function encodingName(name: string): string | undefined {
  return aliases[name.toLowerCase().replace(/[ -]/g, "_")];
}

/** Encode bootstrap text without fusing explicit surrogate code points. */
export function encodeText(source: PyString, encodingValue: PyString = "utf-8", errorsValue: PyString = "strict"): Uint8Array {
  const encoding = strText(encodingValue), errors = strText(errorsValue);
  const enc = encodingName(encoding);
  if (enc === undefined) throw diasporeErr("LookupError", strConcat("unknown encoding: ", encodingValue));
  const limit = enc === "ascii" ? 127 : enc === "latin-1" ? 255 : 0x10ffff;
  let length = 0;
  for (const char of source) {
    const point = char.codePointAt(0)!;
    const invalid = point > limit || (enc === "utf-8" && point >= 0xd800 && point <= 0xdfff);
    if (!invalid) length += enc !== "utf-8" || point < 0x80 ? 1 : point < 0x800 ? 2 : point < 0x10000 ? 3 : 4;
    else if (errors === "surrogatepass" && enc === "utf-8") length += 3;
    else if (errors === "surrogateescape" && point >= 0xdc80 && point <= 0xdcff) length++;
    else if (errors === "ignore") continue;
    else if (errors === "backslashreplace") length += point <= 0xff ? 4 : point <= 0xffff ? 6 : 10;
    else if (errors === "replace") length++;
    else {
      const points = chars(source);
      let start = 0;
      while (start < points.length) {
        const p = points[start].codePointAt(0)!;
        if ((p > limit || (enc === "utf-8" && p >= 0xd800 && p <= 0xdfff)) &&
            !(errors === "surrogateescape" && p >= 0xdc80 && p <= 0xdcff)) break;
        start++;
      }
      let end = start + 1;
      while (end < points.length) {
        const p = points[end].codePointAt(0)!;
        if (!(p > limit || (enc === "utf-8" && p >= 0xd800 && p <= 0xdfff))) break;
        end++;
      }
      if (!handlers[errors]) throw diasporeErr("LookupError", strConcat("unknown error handler name '", errorsValue, "'"));
      throw unicodeCodecError("UnicodeEncodeError", enc, source, start, end,
        enc === "utf-8" ? "surrogates not allowed" : `ordinal not in range(${limit + 1})`);
    }
  }
  const result = new Uint8Array(length);
  let offset = 0;
  for (const char of source) {
    const point = char.codePointAt(0)!;
    const invalid = point > limit || (enc === "utf-8" && point >= 0xd800 && point <= 0xdfff);
    if (invalid && !(errors === "surrogatepass" && enc === "utf-8")) {
      if (errors === "ignore") continue;
      if (errors === "surrogateescape") { result[offset++] = point - 0xdc00; continue; }
      if (errors === "replace") { result[offset++] = 63; continue; }
      const escaped = point <= 0xff ? "\\x" + point.toString(16).padStart(2, "0") : point <= 0xffff ? "\\u" + point.toString(16).padStart(4, "0") : "\\U" + point.toString(16).padStart(8, "0");
      for (let index = 0; index < escaped.length; index++) result[offset++] = escaped.charCodeAt(index);
    } else if (enc !== "utf-8" || point < 0x80) result[offset++] = point;
    else if (point < 0x800) {
      result[offset++] = 0xc0 | point >> 6; result[offset++] = 0x80 | point & 63;
    } else if (point < 0x10000) {
      result[offset++] = 0xe0 | point >> 12; result[offset++] = 0x80 | point >> 6 & 63; result[offset++] = 0x80 | point & 63;
    } else {
      result[offset++] = 0xf0 | point >> 18; result[offset++] = 0x80 | point >> 12 & 63;
      result[offset++] = 0x80 | point >> 6 & 63; result[offset++] = 0x80 | point & 63;
    }
  }
  return result;
}

/** Decode bootstrap text while preserving surrogatepass and error boundaries. */
export function decodeBytes(source: Uint8Array, encodingValue: PyString = "utf-8", errorsValue: PyString = "strict"): PyString {
  const encoding = strText(encodingValue), errors = strText(errorsValue);
  const enc = encodingName(encoding);
  if (enc === undefined) throw diasporeErr("LookupError", strConcat("unknown encoding: ", encodingValue));
  let result = "";
  let parts: string[] | null = null;
  for (let offset = 0; offset < source.length;) {
    const start = offset, first = source[offset++];
    if (enc === "latin-1" || first < 128) { result += String.fromCharCode(first); continue; }
    let reason = enc === "ascii" ? "ordinal not in range(128)" : "invalid start byte";
    let end = offset;
    if (enc === "utf-8") {
      if (errors === "surrogatepass" && first === 0xed && start + 2 < source.length &&
          source[start + 1] >= 0xa0 && source[start + 1] <= 0xbf && (source[start + 2] & 0xc0) === 0x80) {
        const point = (first & 15) << 12 | (source[start + 1] & 63) << 6 | source[start + 2] & 63;
        parts ??= [];
        parts.push(result, String.fromCharCode(point));
        result = "";
        offset = start + 3;
        continue;
      }
      const length = first >= 0xc2 && first <= 0xdf ? 2 : first >= 0xe0 && first <= 0xef ? 3 : first >= 0xf0 && first <= 0xf4 ? 4 : 0;
      if (length) {
        let point = first & (0x7f >> length), valid = true;
        for (let index = 1; index < length; index++) {
          if (start + index >= source.length) { reason = "unexpected end of data"; end = source.length; valid = false; break; }
          const byte = source[start + index];
          if ((byte & 0xc0) !== 0x80 || (index === 1 && ((first === 0xe0 && byte < 0xa0) ||
            (first === 0xed && byte >= 0xa0) || (first === 0xf0 && byte < 0x90) || (first === 0xf4 && byte >= 0x90)))) {
            reason = "invalid continuation byte"; end = start + index; valid = false; break;
          }
          point = point << 6 | byte & 63;
        }
        if (valid) { result += String.fromCodePoint(point); offset = start + length; continue; }
      }
    }
    offset = end;
    if (errors === "ignore") continue;
    if (errors === "replace") { result += "\ufffd"; continue; }
    if (errors === "backslashreplace") {
      for (let index = start; index < end; index++) result += "\\x" + source[index].toString(16).padStart(2, "0");
      continue;
    }
    if (errors === "surrogateescape") {
      for (let index = start; index < end; index++) result += String.fromCharCode(0xdc00 + source[index]);
      continue;
    }
    if (!handlers[errors]) throw diasporeErr("LookupError", strConcat("unknown error handler name '", errorsValue, "'"));
    throw unicodeCodecError("UnicodeDecodeError", enc, source, start, end, reason);
  }
  if (parts === null) return result;
  parts.push(result);
  return strJoin(parts);
}

/** Use native bootstrap codecs or explicitly bundled or linked providers. */
export function* textCodec(I: Interp, operation: "encode" | "decode", value: PyString | Uint8Array, encodingValue: PyString, errorsValue: PyString): G<Value> {
  const encoding = strText(encodingValue), errors = strText(errorsValue);
  if (encodingName(encoding) !== undefined && handlers[errors]) {
    return operation === "encode" ? encodeText(value as PyString, encodingValue, errorsValue) : decodeBytes(value as Uint8Array, encodingValue, errorsValue);
  }
  if (I.moduleCode("codecs") === undefined) {
    const module = typeof encodingValue === "string" ? encodingValue.replace(/-/g, "_")
      : strJoin(Array.from(encodingValue, char => char === "-" ? "_" : char));
    throw diasporeErr("LookupError", strConcat("unknown encoding: ", encodingValue, " (bundle it with `import encodings.", module, "`)"));
  }
  const codecs = yield* I.importModule("codecs");
  return yield* I.invoke(yield* I.getAttr(codecs, operation), [value, encodingValue, errorsValue], new Map());
}
