/* UTF-8 transport with surrogatepass for DSPB's Python string table. */

import { type PyString, strFromChars } from "./values.ts";

/** Encode Unicode scalars and lone UTF-16 surrogates without replacement. */
export function encodeWTF8(value: PyString): Uint8Array {
  let length = 0;
  for (const char of value) {
    const point = char.codePointAt(0)!;
    length += point < 0x80 ? 1 : point < 0x800 ? 2 : point < 0x10000 ? 3 : 4;
  }
  const result = new Uint8Array(length);
  let offset = 0;
  for (const char of value) {
    const point = char.codePointAt(0)!;
    if (point < 0x80) result[offset++] = point;
    else if (point < 0x800) {
      result[offset++] = 0xc0 | point >> 6;
      result[offset++] = 0x80 | point & 0x3f;
    } else if (point < 0x10000) {
      result[offset++] = 0xe0 | point >> 12;
      result[offset++] = 0x80 | point >> 6 & 0x3f;
      result[offset++] = 0x80 | point & 0x3f;
    } else {
      result[offset++] = 0xf0 | point >> 18;
      result[offset++] = 0x80 | point >> 12 & 0x3f;
      result[offset++] = 0x80 | point >> 6 & 0x3f;
      result[offset++] = 0x80 | point & 0x3f;
    }
  }
  return result;
}

/** Decode canonical UTF-8 plus surrogate code points, rejecting all other errors. */
export function decodeWTF8(value: Uint8Array): PyString {
  let result = "";
  let points: string[] | null = null;
  for (let offset = 0; offset < value.length;) {
    const first = value[offset++];
    if (first < 0x80) {
      const char = String.fromCharCode(first);
      if (points === null) result += char;
      else points.push(char);
      continue;
    }
    const length = first >= 0xc2 && first <= 0xdf ? 2 : first >= 0xe0 && first <= 0xef ? 3 : first >= 0xf0 && first <= 0xf4 ? 4 : 0;
    if (!length || offset + length - 1 > value.length) throw new TypeError("invalid UTF-8");
    let point = first & (0x7f >> length);
    for (let index = 1; index < length; index++) {
      const byte = value[offset++];
      if ((byte & 0xc0) !== 0x80) throw new TypeError("invalid UTF-8");
      point = point << 6 | byte & 0x3f;
    }
    if ((length === 2 && point < 0x80) || (length === 3 && point < 0x800) ||
      (length === 4 && point < 0x10000) || point > 0x10ffff) throw new TypeError("invalid UTF-8");
    const char = String.fromCodePoint(point);
    const previous = result.charCodeAt(result.length - 1);
    if (points === null && previous >= 0xd800 && previous <= 0xdbff && point >= 0xdc00 && point <= 0xdfff) {
      points = Array.from(result);
    }
    if (points === null) result += char;
    else points.push(char);
  }
  return points === null ? result : strFromChars(points);
}
