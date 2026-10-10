/* Compile Python using the generated self-hosted compiler bundle. */

import * as nodeFs from "node:fs";
import * as nodePath from "node:path";
import { gzipSync, gunzipSync } from "node:zlib";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { Interp } from "./interp.ts";
import { dspbEncode, dspbDecode, DSPBError, DSPB_MAGIC } from "./dspb.ts";
import type { Bundle } from "./index.ts";

const EMBED_URL = new URL("../dspc.dsp.json.gz", import.meta.url);
let cachedCompiler: Record<string, unknown> | null = null;
const REPO_ROOT = fileURLToPath(new URL("../../..", import.meta.url));

/** Options for compiling Python against the shipped standard library. */
export interface CompileOptions {
  paths?: string[];
  debug?: boolean;
  selfContained?: boolean;
  stdlib?: boolean;
}

/** A diagnostic emitted by the embedded Python compiler. */
export class CompileError extends Error {
  readonly exitCode: number;

  constructor(message: string, exitCode: number) {
    super(message);
    this.exitCode = exitCode;
  }
}

function die(msg: string): never {
  process.stderr.write(`diaspore: ${msg}\n`);
  process.exit(2);
}

function loadEmbeddedCompiler(): Record<string, unknown> {
  if (cachedCompiler !== null) return cachedCompiler;
  try {
    const raw = nodeFs.readFileSync(EMBED_URL);
    cachedCompiler = JSON.parse(gunzipSync(raw).toString("utf8")) as Record<string, unknown>;
    return cachedCompiler;
  } catch (error) {
    throw new Error("cannot load embedded compiler bundle: " + (error as Error).message);
  }
}

type DspcResult = { text: string } | { code: number; error: string };

/** Compile with the embedded compiler, resolving user imports only from granted paths. */
function runDspc(entry: string, options: CompileOptions = {}): DspcResult {
  const compiler = loadEmbeddedCompiler();
  const paths = options.paths ?? [];
  const entryDir = nodePath.dirname(entry);
  const argv = ["dspc", entry];
  if (options.debug) argv.push("--debug");
  if (options.selfContained) argv.push("--self-contained");
  if (options.stdlib === false) argv.push("--no-stdlib");
  for (const path of paths) argv.push("--path", path);
  const chunks: string[] = [];
  const errors: string[] = [];
  const interpreter = new Interp(compiler, {
    argv,
    caps: ["fs"],
    policy: { fs: { read: [entryDir, entry, ...paths], write: [] } },
    maxDepth: 3000,
    stdout: (text) => chunks.push(text),
    stderr: (text) => errors.push(text),
  });
  try {
    const code = interpreter.run();
    if (code !== 0) return { code, error: errors.join("").trimEnd() };
    return { text: chunks.join("").replace(/\n+$/, "") };
  } finally {
    interpreter.close();
  }
}

/** Compile a Python file, linking the shipped standard library unless disabled. */
export function compileFile(entry: string, options: CompileOptions = {}): Bundle {
  const result = runDspc(entry, options);
  if ("code" in result) {
    throw new CompileError(result.error || `embedded compiler exited with code ${result.code}`, result.code);
  }
  try {
    return JSON.parse(result.text) as Bundle;
  } catch (error) {
    throw new Error("embedded compiler produced invalid JSON: " + (error as Error).message);
  }
}

/** Compile an entry file using CLI arguments and return an exit code. */
export function cmdBuild(rest: string[]): number {
  let entry = "";
  let out = "";
  let debug = false;
  let pretty = false;
  let selfContained = false;
  let stdlib = true;
  const paths: string[] = [];
  for (let i = 0; i < rest.length; i++) {
    const tok = rest[i];
    if (tok === "-o" || tok === "--output" || tok.startsWith("-o=") || tok.startsWith("--output=")) {
      const eq = tok.indexOf("=");
      if (eq >= 0) {
        out = tok.slice(eq + 1);
      } else {
        i++;
        if (i >= rest.length) die("-o requires a value");
        out = rest[i];
      }
    } else if (tok === "--path" || tok.startsWith("--path=")) {
      const eq = tok.indexOf("=");
      if (eq >= 0) {
        paths.push(tok.slice(eq + 1));
      } else {
        i++;
        if (i >= rest.length) die("--path requires a value");
        paths.push(rest[i]);
      }
    } else if (tok === "--debug") {
      debug = true;
    } else if (tok === "--pretty") {
      pretty = true;
    } else if (tok === "--self-contained") {
      selfContained = true;
    } else if (tok === "--no-stdlib") {
      stdlib = false;
    } else if (tok === "--binding" || tok.startsWith("--binding=")) {
      die(
        "--binding is not supported by the embedded compiler " +
          "(declare bindings when building with the Python CLI: uv run diaspore build --binding NAME)",
      );
    } else if (tok.startsWith("-")) {
      die("unknown flag " + tok);
    } else {
      if (entry !== "") die("build takes exactly one entry file");
      entry = tok;
    }
  }
  if (entry === "") {
    process.stderr.write("usage: diaspore-ts build <entry.py> [-o OUT[.gz]] [--path DIR]... [--debug] [--pretty] [--self-contained] [--no-stdlib]\n");
    return 2;
  }
  if (!nodeFs.existsSync(entry)) {
    die(`stat ${entry}: no such file or directory`);
  }
  let r: DspcResult;
  try {
    r = runDspc(entry, { debug, paths, selfContained, stdlib });
  } catch (error) {
    die((error as Error).message);
  }
  if ("code" in r) {
    if (r.error) process.stderr.write(r.error + "\n");
    return r.code;
  }
  const text = r.text;
  let bundle: Record<string, unknown>;
  try {
    bundle = JSON.parse(text) as Record<string, unknown>;
  } catch (e) {
    die("embedded compiler produced invalid JSON: " + (e as Error).message);
  }
  if (out === "") {
    process.stdout.write(text + "\n");
    return 0;
  }
  const inner = out.endsWith(".gz") ? out.slice(0, -3) : out;
  let payload: Buffer;
  if (inner.endsWith(".dspb")) {
    try {
      payload = dspbEncode(bundle); // DSPB binary container by extension
    } catch (e) {
      die("cannot encode DSPB bundle: " + (e as Error).message);
    }
  } else if (pretty) {
    payload = Buffer.from(JSON.stringify(bundle, null, 2) + "\n", "utf8");
  } else {
    payload = Buffer.from(text + "\n", "utf8");
  }
  if (out.endsWith(".gz")) {
    payload = gzipSync(payload, { level: 9 }); // mtime 0: reproducible output
  }
  try {
    nodeFs.writeFileSync(out, payload);
  } catch (e) {
    die((e as Error).message);
  }
  const mods = bundle.modules as Record<string, unknown> | undefined;
  const n = mods !== undefined ? Object.keys(mods).length : 0;
  process.stderr.write(
    `diaspore: built ${out} (${n} module${n === 1 ? "" : "s"}, ${payload.length} bytes)\n`,
  );
  return 0;
}


// Mirrors the native compiler's compact_bundle pass (spec 2.2) so verbose
// and compact emissions of the same bundle compare equal.
const OMIT_LIST_KEYS = [
  "orelse",
  "finalbody",
  "handlers",
  "kw",
  "args",
  "elts",
  "entries",
  "bases",
  "gdecl",
  "ndecl",
  "locals",
  "defaults",
  "kwonly",
  "kwdefaults",
];
const OMIT_NULL_KEYS = ["vararg", "kwarg", "conv", "spec", "t", "name"];

function stripDefaults(v: unknown): void {
  if (Array.isArray(v)) {
    for (const x of v) stripDefaults(x);
    return;
  }
  if (v === null || typeof v !== "object") return;
  const m = v as Record<string, unknown>;
  const isConst = m.op === "const";
  for (const k of OMIT_LIST_KEYS) {
    const val = m[k];
    if (Array.isArray(val) && val.length === 0) delete m[k];
  }
  for (const k of OMIT_NULL_KEYS) {
    if (k in m && m[k] === null && !(isConst && k === "t")) delete m[k];
  }
  if (m.gen === false) delete m.gen;
  for (const x of Object.values(m)) stripDefaults(x);
}

function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) {
      if (!deepEqual(a[i], b[i])) return false;
    }
    return true;
  }
  if (a !== null && b !== null && typeof a === "object" && typeof b === "object") {
    const ka = Object.keys(a as object).sort();
    const kb = Object.keys(b as object).sort();
    if (!deepEqual(ka, kb)) return false;
    for (const k of ka) {
      if (!deepEqual((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k])) {
        return false;
      }
    }
    return true;
  }
  return false;
}

function normalized(jsonText: string): unknown {
  const v = JSON.parse(jsonText) as unknown;
  stripDefaults(v);
  return v;
}

function nativeBuild(entry: string, outPath: string): boolean {
  const r = spawnSync("uv", ["run", "diaspore", "build", entry, "-o", outPath], {
    cwd: REPO_ROOT,
    encoding: "utf8",
  });
  if (r.status !== 0) {
    process.stderr.write(r.stderr ?? "");
    return false;
  }
  return true;
}

// Malformed DSPB must fail before allocating from untrusted counts.

function vint(n: number): number[] {
  const out: number[] = [];
  for (;;) {
    const b = n % 128;
    n = Math.floor(n / 128);
    if (n > 0) out.push(b | 0x80);
    else {
      out.push(b);
      return out;
    }
  }
}

/** Header for a hand-crafted single-module bundle whose body bytes follow:
 * strtab ["m"], semver 1, main "m", 1 module (name/file "m", no src),
 * body node-count 1. */
function hostileHead(): number[] {
  return [
    ...vint(1), ...vint(1), 0x6d, // strtab: 1 entry, len 1, "m"
    ...vint(1), // semver
    ...vint(0), // main -> "m"
    ...vint(1), // module count
    ...vint(0), // name
    ...vint(0), // file
    0, // no src
    ...vint(1), // body: 1 node
  ];
}

function rejects(data: Buffer): boolean {
  try {
    dspbDecode(data);
    return false;
  } catch (e) {
    if (e instanceof DSPBError) return true;
    throw e; // anything else is exactly the bug the battery hunts
  }
}

function hostileDSPB(w: (s: string) => void): number {
  let failures = 0;
  const check = (label: string, ok: boolean) => {
    if (ok) {
      w(`ok  hostile-dspb ${label}\n`);
    } else {
      w(`FAIL hostile-dspb ${label}\n`);
      failures++;
    }
  };
  const smoke = runDspc("tests/corpus/smoke.py");
  if ("code" in smoke) {
    w(`FAIL hostile-dspb setup: build smoke.py exit ${smoke.code}\n`);
    return 1;
  }
  const data = dspbEncode(JSON.parse(smoke.text) as Record<string, unknown>);
  try {
    // 1. truncation sweep: every cut rejects cleanly
    let ok = true;
    const cuts: number[] = [];
    for (let i = 0; i < 40; i++) cuts.push(i);
    cuts.push(Math.floor(data.length / 2), data.length - 1);
    for (const cut of cuts) {
      if (!rejects(data.subarray(0, cut))) ok = false;
    }
    check("truncation sweep", ok);
    // Bit flips may produce valid bundles or DSPBError, but not host errors.
    ok = true;
    for (let pos = 6; pos < Math.min(data.length, 220); pos++) {
      const corrupted = Buffer.from(data);
      corrupted[pos] ^= 0xff;
      try {
        dspbDecode(corrupted);
      } catch (e) {
        if (!(e instanceof DSPBError)) {
          ok = false;
          break;
        }
      }
    }
    check("bitflip sweep", ok);
    // 3. lied module count (2^60) rejected before allocation
    const lied = Buffer.from([
      ...vint(1), ...vint(1), 0x6d, ...vint(1), ...vint(0), ...vint(2 ** 60),
    ]);
    check("lied module count", rejects(Buffer.concat([DSPB_MAGIC, lied])));
    // 4. 50k-deep unary nesting hits the depth guard, not the JS stack
    // (opcode indices per docs/dspb.md: unary=8, const=0; const-kind none=5)
    const deep: number[] = hostileHead();
    const body = Buffer.alloc(50_000 * 3 + 3);
    for (let i = 0; i < 50_000; i++) body.set([8, 0, 0], i * 3);
    body.set([0, 0, 5], 50_000 * 3);
    check(
      "50k-deep nesting",
      rejects(Buffer.concat([DSPB_MAGIC, Buffer.from(deep), body])),
    );
    // 5. bad opcode / enum bytes
    ok = true;
    for (const tail of [
      [250, 0], // bad opcode
      [0, 0, 99], // const with bad kind byte
      [7, 0, 200, 0, 0, 5], // binop with bad kind byte
    ]) {
      if (!rejects(Buffer.concat([DSPB_MAGIC, Buffer.from(hostileHead()), Buffer.from(tail)]))) {
        ok = false;
      }
    }
    check("bad opcode/enum bytes", ok);
  } catch (e) {
    w(`FAIL hostile-dspb: uncaught ${(e as Error).constructor.name}: ${(e as Error).message}\n`);
    failures++;
  }
  return failures;
}

/** Compare compiler output with the native compiler and check DSPB decoding. */
export function cmdSelftestBuild(): number {
  const w = (s: string) => process.stderr.write(s);
  // Build with relative entry paths from the repo root on both sides so the
  // bundles' "file" fields agree byte-for-byte.
  process.chdir(REPO_ROOT);
  let failures = hostileDSPB(w); // pure-ts; runs even without uv
  const hasUV = spawnSync("uv", ["--version"], { encoding: "utf8" }).status === 0;
  if (!hasUV) {
    w("selftest-build: uv not available; cannot compare against the native compiler\n");
    return 1;
  }
  const tmpDir = nodeFs.mkdtempSync(nodePath.join(REPO_ROOT, ".diasporets-selftest-"));
  try {
    const appDir = nodePath.join(tmpDir, "app");
    const firstDir = nodePath.join(tmpDir, "first");
    const secondDir = nodePath.join(tmpDir, "second");
    for (const dir of [appDir, firstDir, secondDir]) nodeFs.mkdirSync(dir);
    const pathEntry = nodePath.join(appDir, "main.py");
    const pathOut = nodePath.join(tmpDir, "paths.json");
    nodeFs.writeFileSync(pathEntry, "import shared\nimport extra\nprint(shared.value + extra.value)\n");
    nodeFs.writeFileSync(nodePath.join(firstDir, "shared.py"), "value = 10\n");
    nodeFs.writeFileSync(nodePath.join(secondDir, "shared.py"), "value = 100\n");
    nodeFs.writeFileSync(nodePath.join(secondDir, "extra.py"), "value = 2\n");
    for (const [paths, expected] of [
      [[firstDir, secondDir], "12\n"],
      [[secondDir, firstDir], "102\n"],
    ] as const) {
      const code = cmdBuild([pathEntry, "--path", paths[0], `--path=${paths[1]}`, "-o", pathOut]);
      let actual = "";
      let runCode = -1;
      if (code === 0) {
        const bundle = JSON.parse(nodeFs.readFileSync(pathOut, "utf8")) as Record<string, unknown>;
        runCode = new Interp(bundle, { stdout: (s) => { actual += s; } }).run();
      }
      if (code === 0 && runCode === 0 && actual === expected) {
        w(`ok  repeated --path (${nodePath.basename(paths[0])} first)\n`);
      } else {
        w(`FAIL repeated --path (${nodePath.basename(paths[0])} first)\n`);
        failures++;
      }
    }
    const missingPath = spawnSync(
      process.execPath,
      [fileURLToPath(new URL("./cli.ts", import.meta.url)), "build", pathEntry, "--path"],
      { encoding: "utf8" },
    );
    if (missingPath.status === 2 && missingPath.stderr.includes("--path requires a value")) {
      w("ok  --path requires a value\n");
    } else {
      w("FAIL --path requires a value\n");
      failures++;
    }
    const entries = [
      "tests/corpus/smoke.py",
      "tests/corpus/13_classes.py",
      "tests/corpus/29_imports/main.py",
      "tests/corpus/47_packages/main.py",
      "selfhost/dspc.py",
    ];
    for (const entry of entries) {
      const r = runDspc(entry);
      if ("code" in r) {
        w(`FAIL diaspore-ts build ${entry}: exit ${r.code}\n`);
        failures++;
        continue;
      }
      const py = nodePath.join(tmpDir, "py.json");
      if (!nativeBuild(entry, py)) {
        w(`FAIL uv run diaspore build ${entry}\n`);
        failures++;
        continue;
      }
      if (deepEqual(normalized(r.text), normalized(nodeFs.readFileSync(py, "utf8")))) {
        w(`ok  parity ${entry}\n`);
      } else {
        w(`FAIL bundle divergence on ${entry}\n`);
        failures++;
      }
    }
    // 3. DSPB container cross-parity (docs/dspb.md): ts-encode -> py-decode and
    // py-encode -> ts-decode must both equal the compact JSON model.
    const lbEntry = "tests/corpus/29_imports/main.py";
    const rl = runDspc(lbEntry);
    if ("code" in rl) {
      w(`FAIL diaspore-ts build ${lbEntry} for DSPB check: exit ${rl.code}\n`);
      failures++;
    } else {
      const compactModel = JSON.parse(rl.text) as unknown;
      const lbPath = nodePath.join(tmpDir, "x.dspb");
      nodeFs.writeFileSync(lbPath, dspbEncode(compactModel as Record<string, unknown>));
      const roundtrip = dspbDecode(nodeFs.readFileSync(lbPath));
      const pyDec = spawnSync(
        "uv",
        [
          "run",
          "python",
          "-c",
          "import sys, json; from diaspore.dspb import encode, decode\n" +
            "d = decode(open(sys.argv[1], 'rb').read())\n" +
            "open(sys.argv[2], 'wb').write(encode(d))\n" +
            "print(json.dumps(d))",
          lbPath,
          nodePath.join(tmpDir, "y.dspb"),
        ],
        { cwd: REPO_ROOT, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 },
      );
      const tsDecOfPyEnc =
        pyDec.status === 0 ? dspbDecode(nodeFs.readFileSync(nodePath.join(tmpDir, "y.dspb"))) : null;
      if (
        pyDec.status === 0 &&
        deepEqual(roundtrip, compactModel) &&
        deepEqual(JSON.parse(pyDec.stdout) as unknown, compactModel) &&
        deepEqual(tsDecOfPyEnc, compactModel)
      ) {
        w(`ok  DSPB cross-parity ${lbEntry} (ts-encode -> py-decode, py-encode -> ts-decode)\n`);
      } else {
        w(`FAIL DSPB cross-parity on ${lbEntry}\n`);
        if (pyDec.status !== 0) w(pyDec.stderr ?? "");
        failures++;
      }
    }
  } finally {
    nodeFs.rmSync(tmpDir, { recursive: true, force: true });
  }
  if (failures > 0) {
    w(`selftest-build: ${failures} failure(s)\n`);
    return 1;
  }
  w("selftest-build: all checks passed\n");
  return 0;
}
