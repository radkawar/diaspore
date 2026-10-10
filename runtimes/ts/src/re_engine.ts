import { type PyString, type Value, chars, diasporeErr } from "./values.ts";
import { REGEX_WORD, REGEX_DIGIT, REGEX_SPACE, REGEX_CASES, REGEX_XID_START, REGEX_XID_CONTINUE } from "./re_unicode.ts";
import { REGEX_LOWER } from "./re_case_lower.ts";
import { regexError } from "./re_error.ts";

/** Python flag values shared by the parser and native API. */
export const FLAGS = { T: 1, I: 2, L: 4, M: 8, S: 16, U: 32, X: 64, DEBUG: 128, A: 256 };
type Node =
  | { k: "char"; test: (c: string) => boolean }
  | { k: "seq" | "alt"; nodes: Node[] }
  | { k: "group"; child: Node; id: number }
  | { k: "repeat"; child: Node; min: number; max: number; lazy: boolean }
  | { k: "anchor"; kind: string; flags: number }
  | { k: "ref"; id: number; flags: number }
  | { k: "look"; child: Node; positive: boolean; width: number }
  | { k: "atomic"; child: Node };
/** Capture offsets are Python code-point or byte offsets. */
export interface RegexResult { pos: number; caps: number[]; last: number | null }
/** Parsed Python expression, without host-dependent capture behavior. */
export interface RegexCode { root: Node; groups: number; names: Map<string, number>; flags: number }
const unicodeCases = new Map<string, readonly string[]>();
const asciiCases = new Map<string, readonly string[]>();
for (const [cp, group] of REGEX_CASES) {
  if (cp !== group[0]) continue;
  const chars = group.map(c => String.fromCodePoint(c));
  for (const c of chars) unicodeCases.set(c, chars);
}
for (let cp = 65; cp <= 90; cp++) {
  const chars = [String.fromCharCode(cp), String.fromCharCode(cp + 32)];
  for (const c of chars) asciiCases.set(c, chars);
}
function inRanges(c: string, ranges: readonly number[]): boolean {
  const cp = c.codePointAt(0)!;
  let lo = 0, hi = ranges.length / 2;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (cp < ranges[mid * 2]) hi = mid;
    else if (cp > ranges[mid * 2 + 1]) lo = mid + 1;
    else return true;
  }
  return false;
}
const asciiWord = /^[a-zA-Z0-9_]$/;
const asciiSpace = /^[ \t\n\r\f\v]$/;
const escapes: Record<string, string> = { a: "\x07", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t", v: "\v" };
function variants(c: string, flags: number): readonly string[] {
  return flags & FLAGS.I ? (flags & FLAGS.A ? asciiCases : unicodeCases).get(c) ?? [c] : [c];
}
function literal(c: string, flags: number): Node {
  if (!(flags & FLAGS.I)) return { k: "char", test: x => x === c };
  const choices = variants(c, flags);
  return { k: "char", test: x => choices.includes(x) };
}
function category(c: string, flags: number): (x: string) => boolean {
  const a = !!(flags & FLAGS.A);
  const lower = c.toLowerCase();
  const ranges = lower === "d" ? REGEX_DIGIT : lower === "w" ? REGEX_WORD : REGEX_SPACE;
  const ascii = lower === "d" ? /^[0-9]$/ : lower === "w" ? asciiWord : asciiSpace;
  return x => (a ? ascii.test(x) : inRanges(x, ranges)) === (c === lower);
}
function width(n: Node): number {
  switch (n.k) {
    case "char": return 1;
    case "anchor": case "look": return 0;
    case "ref": return -1;
    case "atomic": case "group": return width(n.child);
    case "repeat": { const w = width(n.child); return w >= 0 && n.min === n.max ? w * n.min : -1; }
    case "seq": { let total = 0; for (const c of n.nodes) { const w = width(c); if (w < 0) return -1; total += w; } return total; }
    case "alt": { const w = width(n.nodes[0]); return n.nodes.every(c => width(c) === w) ? w : -1; }
  }
}

/** Parse Python code points and reject unsupported constructs before matching. */
export function compileRegex(source: PyString, flags: number, bytes: boolean, pattern: Value = source): RegexCode {
  if (flags & ~(FLAGS.A | FLAGS.I | FLAGS.M | FLAGS.S | FLAGS.U | FLAGS.X)) throw diasporeErr("ValueError", "unsupported regular expression flags");
  if (bytes && flags & FLAGS.U) throw diasporeErr("ValueError", "cannot use UNICODE flag with a bytes pattern");
  if (flags & FLAGS.A && flags & FLAGS.U) throw diasporeErr("ValueError", "ASCII and UNICODE flags are incompatible");
  let declared = flags;
  flags = bytes ? flags : flags & FLAGS.A ? flags : flags | FLAGS.U;
  const text = chars(source);
  let p = 0, groups = 0, current = flags | (bytes ? FLAGS.A : 0), depth = 0;
  const fail: (message: string, position?: number) => never = (message, position = p) => { throw regexError(message, pattern, position); };
  const names = new Map<string, number>(), open = new Set<number>();
  const skip = () => { if (current & FLAGS.X) for (;;) { if (p < text.length && /[ \t\n\r\f\v]/.test(text[p])) { p++; continue; } if (text[p] === "#") { while (p < text.length && text[p] !== "\n") p++; continue; } break; } };
  const name = (end: string): string => {
    let s = "";
    while (p < text.length && text[p] !== end) {
      const point = text[p++], code = point.codePointAt(0)!;
      if (code >= 0xd800 && code <= 0xdfff) fail("invalid group name");
      s += point;
    }
    if (text[p++] !== end) fail("unterminated group name");
    return s;
  };
  const reference = (s: string, position: number): number => {
    const id = /^[0-9]+$/.test(s) ? Number(s) : names.get(s);
    if (id === undefined || id <= 0 || id > groups) fail(`invalid group reference ${s}`, position);
    if (open.has(id)) fail("cannot refer to an open group", position);
    return id;
  };
  const escape = (inside: boolean): Node | string => {
    const start = p - 1;
    const c = text[p++];
    if (c === undefined) fail("bad escape (end of pattern)", start);
    if ("dDsSwW".includes(c)) return { k: "char", test: category(c, current) };
    if (!inside && "AbBZ".includes(c)) return { k: "anchor", kind: c, flags: current };
    if (escapes[c] !== undefined) return escapes[c];
    if (c === "x" || c === "u" || c === "U") {
      if (bytes && c !== "x") fail(`bad escape \\${c}`, start);
      const count = c === "x" ? 2 : c === "u" ? 4 : 8;
      const hex = text.slice(p, p + count).join("");
      if (hex.length !== count || !/^[0-9a-fA-F]+$/.test(hex)) fail(`incomplete escape \\${c}${hex}`, start);
      p += count;
      const cp = parseInt(hex, 16); if (cp > 0x10ffff) fail("bad Unicode escape");
      return String.fromCodePoint(cp);
    }
    if (/[0-9]/.test(c)) {
      let s = c;
      if (c === "0" || inside || /[0-7]/.test(c) && /^[0-7]{2}$/.test(text.slice(p, p + 2).join(""))) {
        while (s.length < 3 && p < text.length && /^[0-7]$/.test(text[p])) s += text[p++];
        if (!/^[0-7]+$/.test(s) || parseInt(s, 8) > 255) fail("invalid octal escape");
        return String.fromCodePoint(parseInt(s, 8));
      }
      if (p < text.length && /^[0-9]$/.test(text[p])) s += text[p++];
      return { k: "ref", id: reference(s, start + 1), flags: current };
    }
    if (/^[a-zA-Z]$/.test(c)) fail(`bad escape \\${c}`, start);
    return c;
  };
  const charClass = (): Node => {
    const start = p - 1;
    const negate = text[p] === "^"; if (negate) p++;
    const tests: ((c: string) => boolean)[] = [];
    const f = current;
    let first = true, closed = false;
    const item = (): Node | string => text[p] === "\\" ? (p++, escape(true)) : text[p++];
    while (p < text.length) {
      if (text[p] === "]" && !first) { p++; closed = true; break; }
      first = false;
      const a = item();
      if (text[p] === "-" && p + 1 < text.length && text[p + 1] !== "]") {
        p++; const b = item();
        if (typeof a !== "string" || typeof b !== "string") fail("bad character range");
        const lo = a.codePointAt(0)!, hi = b.codePointAt(0)!;
        if (lo > hi) fail("bad character range");
        tests.push(c => variants(c, f).some(v => { const cp = v.codePointAt(0)!; return cp >= lo && cp <= hi; }));
      } else if (typeof a === "string") tests.push((literal(a, current) as Extract<Node, { k: "char" }>).test);
      else if (a.k === "char") tests.push(a.test);
      else fail("invalid character class escape");
    }
    if (!closed) fail("unterminated character set", start);
    return { k: "char", test: c => tests.some(test => test(c)) !== negate };
  };
  const expression = (): Node => {
    if (++depth > 200) fail("regular expression nesting is too deep");
    const alternatives: Node[] = [], seq: Node[] = [];
    while (p < text.length) {
      skip(); if (p >= text.length || text[p] === ")") break;
      if (text[p] === "|") { p++; alternatives.push({ k: "seq", nodes: seq.splice(0) }); continue; }
      const start = p, ch = text[p++]; let atom: Node;
      if (ch === "[") atom = charClass();
      else if (ch === "\\") { const e = escape(false); atom = typeof e === "string" ? literal(e, current) : e; }
      else if (ch === ".") { const all = !!(current & FLAGS.S); atom = { k: "char", test: c => all || c !== "\n" }; }
      else if (ch === "^" || ch === "$") atom = { k: "anchor", kind: ch, flags: current };
      else if (ch === "(") {
        const saved = current; let id = 0, kind = "group", positive = true, behind = false;
        let ref: Node | undefined;
        if (text[p] === "?") {
          p++;
          if (text[p] === "#") { p++; name(")"); continue; }
          if (text[p] === ":") p++;
          else if (text[p] === "P" && text[p + 1] === "=") {
            p += 2; const nameStart = p, n = name(")");
            if (!names.has(n)) fail(`unknown group name '${n}'`, nameStart);
            ref = { k: "ref", id: reference(n, nameStart), flags: current };
          }
          else if (text[p] === "P" && text[p + 1] === "<") {
            p += 2; const nameStart = p, n = name(">");
            const valid = bytes ? /^[a-zA-Z_][a-zA-Z0-9_]*$/.test(n) : Array.from(n).every((c, i) => c === "_" || inRanges(c, i === 0 ? REGEX_XID_START : REGEX_XID_CONTINUE));
            if (!n) fail("missing group name", nameStart);
            if (!valid) fail(`bad character in group name '${n}'`, nameStart);
            if (names.has(n)) fail(`redefinition of group name '${n}' as group ${groups + 1}; was group ${names.get(n)}`, nameStart);
            id = ++groups; names.set(n, id);
          } else if (text[p] === "=" || text[p] === "!") { positive = text[p++] === "="; kind = "look"; }
          else if (text[p] === "<" && (text[p + 1] === "=" || text[p + 1] === "!")) { p++; positive = text[p++] === "="; kind = "look"; behind = true; }
          else if (text[p] === ">") { p++; kind = "atomic"; }
          else {
            let add = 0, remove = 0, subtract = false, saw = false;
            while (p < text.length && /[aiLmsux-]/.test(text[p])) {
              const f = text[p++]; if (f === "-") { if (subtract) fail("invalid inline flags"); subtract = true; continue; }
              const bit: number = ({ a: FLAGS.A, i: FLAGS.I, L: FLAGS.L, m: FLAGS.M, s: FLAGS.S, u: FLAGS.U, x: FLAGS.X } as Record<string, number>)[f];
              if (bit === FLAGS.L) fail("LOCALE regular expressions are unsupported");
              if (subtract && bit & (FLAGS.A | FLAGS.U)) fail("cannot turn off ASCII or UNICODE flags");
              if (subtract) remove |= bit; else add |= bit; saw = true;
            }
            if (!saw || subtract && !remove || add & remove || add & FLAGS.A && add & FLAGS.U || bytes && add & FLAGS.U) fail("invalid inline flags or unsupported group");
            if (text[p] === ")" && ((declared | add) & FLAGS.A) && ((declared | add) & FLAGS.U)) fail("ASCII and UNICODE flags are incompatible");
            if (add & (FLAGS.A | FLAGS.U)) current &= ~(FLAGS.A | FLAGS.U);
            current = (current | add) & ~remove;
            if (text[p] === ")") {
              if (subtract || depth !== 1 || seq.length || alternatives.length) fail("global flags not at the start");
              p++; declared |= add; flags = bytes ? declared : current; continue;
            }
            if (text[p++] !== ":") fail("missing flag group colon");
          }
        } else id = ++groups;
        if (ref) atom = ref;
        else {
          if (id) open.add(id);
          const child = expression(); if (text[p++] !== ")") fail("missing ), unterminated subpattern", start);
          if (id) open.delete(id); current = saved;
          if (kind === "look") { const w = behind ? width(child) : 0; if (w < 0) throw regexError("look-behind requires fixed-width pattern"); atom = { k: "look", child, positive, width: w }; }
          else if (kind === "atomic") atom = { k: "atomic", child };
          else atom = id ? { k: "group", child, id } : child;
        }
      } else if ("*+?".includes(ch) || ch === "{" && /^(?:\d+(?:,\d*)?|,\d*)\}/.test(text.slice(p).join(""))) fail("nothing to repeat", start);
      else atom = literal(ch, current);
      skip();
      let min = -1, max = -1;
      const quantifierStart = p, q = text[p];
      if (q === "*" || q === "+" || q === "?") { p++; min = q === "+" ? 1 : 0; max = q === "?" ? 1 : Infinity; }
      else if (q === "{") {
        const m = /^\{(\d*)(?:,(\d*))?\}/.exec(text.slice(p).join(""));
        if (m && (m[1] || m[2] !== undefined)) {
          p += m[0].length; min = Number(m[1] || 0); max = m[2] === undefined ? min : m[2] ? Number(m[2]) : Infinity;
          if (min > max) fail("min repeat greater than max repeat", quantifierStart + 1);
          if (min > 4294967294 || max !== Infinity && max > 4294967294) throw diasporeErr("OverflowError", "the repetition number is too large");
        }
      }
      if (min >= 0) {
        if (atom.k === "anchor") fail("nothing to repeat", quantifierStart);
        const lazy = text[p] === "?", possessive = text[p] === "+"; if (lazy || possessive) p++;
        atom = { k: "repeat", child: atom, min, max, lazy };
        if (possessive) atom = { k: "atomic", child: atom };
        const next = text.slice(p).join("");
        if (/^[*+?]/.test(next) || /^\{(?:\d+(?:,\d*)?|,\d*)\}/.test(next)) fail("multiple repeat");
      }
      seq.push(atom);
    }
    alternatives.push({ k: "seq", nodes: seq }); depth--;
    return alternatives.length === 1 ? alternatives[0] : { k: "alt", nodes: alternatives };
  };
  const root = expression(); if (p !== text.length) fail("unbalanced parenthesis");
  return { root, groups, names, flags };
}

type Task = { node: Node; next: Task | null } | { end: number; start: number; next: Task | null } | { repeat: Extract<Node, { k: "repeat" }>; count: number; previous: number; next: Task | null };
/** Backtracking is iterative and charged to the interpreter fuel budget. */
export function* executeRegex(code: RegexCode, chars: string[], start: number, end: number, fuel: () => void, initial?: RegexResult, root = code.root): Generator<RegexResult> {
  type State = RegexResult & { task: Task | null };
  const stack: State[] = [{ pos: start, caps: initial?.caps ?? new Array((code.groups + 1) * 2).fill(-1), last: initial?.last ?? null, task: { node: root, next: null } }];
  while (stack.length) {
    let s = stack.pop()!;
    for (;;) {
      fuel(); const t = s.task;
      if (!t) { yield s; break; }
      s = { ...s, task: t.next };
      if ("end" in t) { const caps = s.caps.slice(); caps[t.end * 2] = t.start; caps[t.end * 2 + 1] = s.pos; s = { ...s, caps, last: t.end }; continue; }
      if ("repeat" in t) {
        const n = t.repeat, canStop = t.count >= n.min;
        const canMore = t.count < n.max && (t.previous !== s.pos || !canStop);
        const more = (): State => ({ ...s, task: { node: n.child, next: { repeat: n, count: t.count + 1, previous: s.pos, next: t.next } } });
        if (canStop && canMore) { if (n.lazy) stack.push(more()); else { stack.push(s); s = more(); } continue; }
        if (canMore) { s = more(); continue; }
        if (canStop) continue;
        break;
      }
      const n = t.node;
      if (n.k === "char") { if (s.pos >= end || !n.test(chars[s.pos])) break; s.pos++; }
      else if (n.k === "seq") { for (let i = n.nodes.length - 1; i >= 0; i--) s.task = { node: n.nodes[i], next: s.task }; }
      else if (n.k === "alt") { for (let i = n.nodes.length - 1; i > 0; i--) stack.push({ ...s, task: { node: n.nodes[i], next: t.next } }); s.task = { node: n.nodes[0], next: t.next }; }
      else if (n.k === "group") s.task = { node: n.child, next: { end: n.id, start: s.pos, next: t.next } };
      else if (n.k === "repeat") s.task = { repeat: n, count: 0, previous: -1, next: t.next };
      else if (n.k === "ref") {
        const a = s.caps[n.id * 2], b = s.caps[n.id * 2 + 1];
        if (a < 0 || s.pos + b - a > end) break;
        let ok = true;
        for (let i = a; i < b; i++) {
          fuel();
          let left = chars[i].codePointAt(0)!, right = chars[s.pos + i - a].codePointAt(0)!;
          if (n.flags & FLAGS.I) {
            if (n.flags & FLAGS.A) {
              if (left >= 65 && left <= 90) left += 32;
              if (right >= 65 && right <= 90) right += 32;
            } else {
              left = REGEX_LOWER[left] ?? left;
              right = REGEX_LOWER[right] ?? right;
            }
          }
          if (left !== right) { ok = false; break; }
        }
        if (!ok) break; s.pos += b - a;
      } else if (n.k === "anchor") {
        const isWord = category("w", n.flags), left = s.pos > 0 && isWord(chars[s.pos - 1]), right = s.pos < end && isWord(chars[s.pos]);
        const ok = n.kind === "A" ? s.pos === 0 : n.kind === "Z" ? s.pos === end : n.kind === "^" ? s.pos === 0 || !!(n.flags & FLAGS.M) && chars[s.pos - 1] === "\n" : n.kind === "$" ? s.pos === end || chars[s.pos] === "\n" && (!!(n.flags & FLAGS.M) || s.pos === end - 1) : n.kind === "b" ? left !== right : left === right && end > 0;
        if (!ok) break;
      } else if (n.k === "atomic" || n.k === "look") {
        let hit: RegexResult | undefined;
        const begin = n.k === "look" ? s.pos - n.width : s.pos;
        if (begin >= 0) for (const result of executeRegex(code, chars, begin, end, fuel, s, n.child)) { if (n.k !== "look" || !n.width || result.pos === s.pos) { hit = result; break; } }
        if (n.k === "atomic") { if (!hit) break; s = { ...hit, task: t.next }; }
        else { if (!!hit !== n.positive) break; if (hit) s = { ...s, caps: hit.caps, last: hit.last }; }
      }
    }
  }
}
