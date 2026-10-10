/* Runtime code compilation for exec()/eval()/compile(): compile a source
 * snippet by running the embedded self-hosted compiler (ts/dspc.dsp.json.gz)
 * on this VM using the same compiler entry point as file builds. */

import * as nodeFs from "node:fs";
import * as nodePath from "node:path";
import * as nodeOs from "node:os";

import { compileFile, CompileError } from "./build.ts";
import { type PyString, isPyString, CodeObject, diasporeErr } from "./values.ts";
import { encodeText } from "./codecs.ts";


/**
 * Compile `source` to a CodeObject (list of IR statement nodes). `mode`:
 * "exec" keeps all statements; "eval" expects a single expression statement.
 * A malformed snippet surfaces as SyntaxError, matching CPython.
 */
export function compileSnippet(source: PyString, filename: string, mode: string): CodeObject {
  if (!isPyString(source)) {
    throw diasporeErr("TypeError", "compile() arg 1 must be a string");
  }
  const dir = nodeFs.mkdtempSync(nodePath.join(nodeOs.tmpdir(), "diaspore-exec-"));
  const file = nodePath.join(dir, "snippet.py");
  try {
    nodeFs.writeFileSync(file, encodeText(source));
    let bundle;
    try {
      bundle = compileFile(file);
    } catch (error) {
      if (!(error instanceof CompileError)) throw error;
      const message = error.message;
      const match = /compile error: [^:]*:(?:\d+:)?\s*(.*)$/.exec(message);
      throw diasporeErr("SyntaxError", match !== null ? match[1] : message || "invalid syntax");
    }
    const mods = bundle.modules as Record<string, { body?: unknown[] }>;
    const mod = mods["snippet"] ?? Object.values(mods)[0];
    const body = (mod?.body ?? []) as unknown[];
    return new CodeObject(body, mode, filename);
  } finally {
    nodeFs.rmSync(dir, { recursive: true, force: true });
  }
}
