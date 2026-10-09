/* Runtime code compilation for exec()/eval()/compile(): compile a source
 * snippet by running the embedded self-hosted compiler (ts/dspc.dsp.json.gz)
 * on this VM using the same compiler entry point as file builds. */

import * as nodeFs from "node:fs";
import * as nodePath from "node:path";
import * as nodeOs from "node:os";
import { gunzipSync } from "node:zlib";

import { STDLIB_DIR } from "./build.ts";
import { Interp } from "./interp.ts";
import { CodeObject, diasporeErr } from "./values.ts";

const EMBED_URL = new URL("../dspc.dsp.json.gz", import.meta.url);
let cachedCompiler: Record<string, unknown> | null = null;

function dspcBundle(): Record<string, unknown> {
  if (cachedCompiler === null) {
    const raw = nodeFs.readFileSync(EMBED_URL);
    cachedCompiler = JSON.parse(gunzipSync(raw).toString("utf8")) as Record<string, unknown>;
  }
  return cachedCompiler;
}

/**
 * Compile `source` to a CodeObject (list of IR statement nodes). `mode`:
 * "exec" keeps all statements; "eval" expects a single expression statement.
 * A malformed snippet surfaces as SyntaxError, matching CPython.
 */
export function compileSnippet(source: string, filename: string, mode: string): CodeObject {
  if (typeof source !== "string") {
    throw diasporeErr("TypeError", "compile() arg 1 must be a string");
  }
  const dir = nodeFs.mkdtempSync(nodePath.join(nodeOs.tmpdir(), "diaspore-exec-"));
  const file = nodePath.join(dir, "snippet.py");
  try {
    nodeFs.writeFileSync(file, source);
    const chunks: string[] = [];
    const errChunks: string[] = [];
    const vm = new Interp(dspcBundle(), {
      argv: ["dspc", file, "--path", STDLIB_DIR],
      caps: ["fs"],
      policy: { fs: { read: [dir, file, STDLIB_DIR], write: [] } },
      maxDepth: 3000,
      stdout: (s) => chunks.push(s),
      stderr: (s) => errChunks.push(s),
    });
    const code = vm.run();
    if (code !== 0) {
      // dspc prints "diaspore: compile error: file:line: msg" on failure
      const msg = errChunks.join("").trim();
      const m = /compile error: [^:]*:(?:\d+:)?\s*(.*)$/.exec(msg);
      throw diasporeErr("SyntaxError", m !== null ? m[1] : msg || "invalid syntax");
    }
    let bundle: { modules?: Record<string, { body?: unknown[] }> };
    try {
      bundle = JSON.parse(chunks.join("").replace(/\n+$/, ""));
    } catch {
      throw diasporeErr("SyntaxError", "invalid syntax");
    }
    const mods = bundle.modules ?? {};
    const mod = mods["snippet"] ?? Object.values(mods)[0];
    const body = (mod?.body ?? []) as unknown[];
    return new CodeObject(body, mode, filename);
  } finally {
    nodeFs.rmSync(dir, { recursive: true, force: true });
  }
}
