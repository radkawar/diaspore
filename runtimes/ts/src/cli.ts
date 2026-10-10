#!/usr/bin/env node
/* diaspore-ts CLI: execute Diaspore JSON bundles (docs/spec.md).
 *
 * Usage:
 *   cli.ts run <bundle.dsp.json[.gz]> [prog-args...]
 *          [--allow-net [SPEC]] [--allow-fs [SPEC]] [--allow-spawn]
 *          [--max-steps N] [--max-depth N]
 *          [--durable] [--snapshot PATH] [--deterministic]
 *   cli.ts resume <bundle> <snapshot> [--send JSON] [--snapshot OUT]
 *          [--max-steps N] [--max-depth N]
 *
 * Mirrors diaspore-go's CLI: capability flags accept optional scoped specs,
 * flags may appear before or after the bundle path, and everything after
 * "--" is passed to the program verbatim.
 */

import * as nodeFs from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { Interp, type Policy, type JournalEntry } from "./interp.ts";
import { cmdBuild, cmdSelftestBuild } from "./build.ts";
import { loadBundle } from "./index.ts";
import { type Value, NONE, DiasporeError, SuspendSig } from "./values.ts";
import { maybeGunzip, buildSnapshot, parseSnapshot, validateSnapshotBundle, payloadJSON } from "./flow.ts";
import { jsonLoads } from "./json.ts";

function usage(): never {
  process.stderr.write(
    "usage: diaspore-ts run <bundle.dsp.json[.gz]> [prog-args...] [--allow-net [SPEC]] [--allow-fs [SPEC]]\n" +
      "                  [--allow-spawn] [--max-steps N] [--max-depth N]\n" +
      "                  [--durable] [--snapshot PATH] [--deterministic]\n" +
      "       diaspore-ts resume <bundle> <snapshot> [--send JSON] [--snapshot OUT] [--max-steps N] [--max-depth N]\n" +
      "       diaspore-ts build <entry.py> [-o OUT[.gz]] [--path DIR]... [--debug] [--pretty] [--self-contained] [--no-stdlib]\n" +
      "       diaspore-ts selftest-build\n",
  );
  process.exit(2);
}

function die(msg: string): never {
  process.stderr.write(`diaspore: ${msg}\n`);
  process.exit(2);
}

function readMaybeGzip(path: string): Buffer {
  let data: Buffer;
  try {
    data = nodeFs.readFileSync(path);
  } catch (e) {
    die((e as Error).message);
  }
  try {
    return maybeGunzip(data);
  } catch (e) {
    die("bad gzip container: " + (e as Error).message);
  }
}

function loadBundleFile(path: string): Record<string, unknown> {
  try {
    return loadBundle(readMaybeGzip(path));
  } catch (e) {
    die("invalid bundle: " + (e as Error).message);
  }
}

interface CliArgs {
  pos: string[];
  netSpecs: string[]; // "" = bare grant
  fsSpecs: string[];
  allowSpawn: boolean;
  maxSteps: number;
  maxDepth: number;
  durable: boolean;
  deterministic: boolean;
  snapshot: string;
  send: string;
  hasSend: boolean;
}

function specFor(kinds: string[], tok: string): string | null {
  for (const k of kinds) {
    if (tok.startsWith(k + ":") || tok === "all") return tok;
  }
  return null;
}

function parseArgs(rest: string[]): CliArgs {
  const a: CliArgs = {
    pos: [],
    netSpecs: [],
    fsSpecs: [],
    allowSpawn: false,
    maxSteps: 0,
    maxDepth: 1000,
    durable: false,
    deterministic: false,
    snapshot: "",
    send: "",
    hasSend: false,
  };
  const needVal = (i: number, name: string): [string, number] => {
    const eq = rest[i].indexOf("=");
    if (eq >= 0) return [rest[i].slice(eq + 1), i];
    i++;
    if (i >= rest.length) die(`${name} requires a value`);
    return [rest[i], i];
  };
  const needInt = (i: number, name: string): [number, number] => {
    const [raw, j] = needVal(i, name);
    const n = Number(raw);
    if (!Number.isInteger(n) || n < 0) die(`${name} requires a non-negative integer`);
    return [n, j];
  };
  for (let i = 0; i < rest.length; i++) {
    const tok = rest[i];
    if (tok === "--") {
      a.pos.push(...rest.slice(i + 1));
      break;
    } else if (tok === "--allow-net" || tok.startsWith("--allow-net=")) {
      let spec = "";
      const eq = tok.indexOf("=");
      if (eq >= 0) {
        spec = tok.slice(eq + 1);
      } else if (i + 1 < rest.length) {
        const s = specFor(["listen", "connect"], rest[i + 1]);
        if (s !== null) {
          spec = s;
          i++;
        }
      }
      if (spec !== "" && spec !== "all") {
        const cut = spec.indexOf(":");
        const kind = cut >= 0 ? spec.slice(0, cut) : spec;
        const addr = cut >= 0 ? spec.slice(cut + 1) : "";
        if ((kind !== "listen" && kind !== "connect") || addr === "") {
          die(`bad --allow-net spec "${spec}" (use listen:HOST:PORT or connect:HOST:PORT)`);
        }
      }
      if (spec === "all") spec = "";
      a.netSpecs.push(spec);
    } else if (tok === "--allow-fs" || tok.startsWith("--allow-fs=")) {
      let spec = "";
      const eq = tok.indexOf("=");
      if (eq >= 0) {
        spec = tok.slice(eq + 1);
      } else if (i + 1 < rest.length) {
        const s = specFor(["read", "write"], rest[i + 1]);
        if (s !== null) {
          spec = s;
          i++;
        }
      }
      if (spec !== "" && spec !== "all") {
        const cut = spec.indexOf(":");
        const kind = cut >= 0 ? spec.slice(0, cut) : spec;
        const path = cut >= 0 ? spec.slice(cut + 1) : "";
        if ((kind !== "read" && kind !== "write") || path === "") {
          die(`bad --allow-fs spec "${spec}" (use read:PATH or write:PATH)`);
        }
      }
      if (spec === "all") spec = "";
      a.fsSpecs.push(spec);
    } else if (tok === "--allow-spawn") {
      a.allowSpawn = true;
    } else if (tok === "--durable") {
      a.durable = true;
    } else if (tok === "--deterministic") {
      a.deterministic = true;
    } else if (tok === "--snapshot" || tok.startsWith("--snapshot=")) {
      [a.snapshot, i] = needVal(i, "--snapshot");
    } else if (tok === "--send" || tok.startsWith("--send=")) {
      [a.send, i] = needVal(i, "--send");
      a.hasSend = true;
    } else if (tok === "--max-steps" || tok.startsWith("--max-steps=")) {
      [a.maxSteps, i] = needInt(i, "--max-steps");
    } else if (tok === "--max-depth" || tok.startsWith("--max-depth=")) {
      [a.maxDepth, i] = needInt(i, "--max-depth");
    } else if (tok === "--no-validate") {
      // schema validation is a diaspore-py concern; accepted for CLI parity
    } else if (tok.startsWith("--")) {
      die("unknown flag " + tok);
    } else {
      a.pos.push(tok);
    }
  }
  return a;
}

function capsAndPolicy(a: CliArgs): { caps: Set<string>; policy: Policy | null } {
  const caps = new Set<string>();
  const policy: Policy = {};
  if (a.netSpecs.length > 0) {
    caps.add("net");
    const scoped: Record<string, string[]> = { listen: [], connect: [] };
    let allScoped = true;
    for (const s of a.netSpecs) {
      if (s === "") {
        allScoped = false;
        continue;
      }
      const cut = s.indexOf(":");
      scoped[s.slice(0, cut)].push(s.slice(cut + 1));
    }
    if (allScoped) policy.net = scoped;
  }
  if (a.fsSpecs.length > 0) {
    caps.add("fs");
    const scoped: Record<string, string[]> = { read: [], write: [] };
    let allScoped = true;
    for (const s of a.fsSpecs) {
      if (s === "") {
        allScoped = false;
        continue;
      }
      const cut = s.indexOf(":");
      scoped[s.slice(0, cut)].push(s.slice(cut + 1));
    }
    if (allScoped) policy.fs = scoped;
  }
  if (a.allowSpawn) caps.add("spawn");
  return { caps, policy: Object.keys(policy).length > 0 ? policy : null };
}

/** Writes the snapshot and reports suspension like diaspore-py / diaspore-go. */
function finishSuspend(I: Interp, bundle: unknown, sig: SuspendSig, snapshotPath: string): number {
  const snap = buildSnapshot(I, bundle);
  try {
    nodeFs.writeFileSync(snapshotPath, snap);
  } catch (e) {
    die("cannot write snapshot: " + (e as Error).message);
  }
  process.stderr.write(
    `diaspore: suspended (payload: ${payloadJSON(sig.payload)}); snapshot -> ${snapshotPath}\n`,
  );
  return 7;
}

function cmdRun(rest: string[]): number {
  const a = parseArgs(rest);
  if (a.pos.length === 0) usage();
  const bundle = loadBundleFile(a.pos[0]);
  const { caps, policy } = capsAndPolicy(a);
  let I: Interp;
  try {
    I = new Interp(bundle, {
      argv: a.pos,
      caps,
      policy,
      maxSteps: a.maxSteps,
      maxDepth: a.maxDepth,
      durable: a.durable,
      deterministic: a.deterministic,
    });
  } catch (e) {
    process.stderr.write(`diaspore: ${(e as Error).message}\n`);
    return 2;
  }
  let suspended = false;
  try {
    return I.run();
  } catch (e) {
    if (e instanceof SuspendSig) {
      suspended = true;
      const snap = a.snapshot !== "" ? a.snapshot : a.pos[0] + ".flow.json";
      return finishSuspend(I, bundle, e, snap);
    }
    throw e;
  } finally {
    if (!suspended) I.close();
  }
}

function cmdResume(rest: string[]): number {
  const a = parseArgs(rest);
  if (a.pos.length < 2) usage();
  const bundle = loadBundleFile(a.pos[0]);
  let snapData: Buffer;
  try {
    snapData = nodeFs.readFileSync(a.pos[1]);
  } catch (e) {
    die((e as Error).message);
  }
  let parsed;
  try {
    parsed = parseSnapshot(snapData);
  } catch (e) {
    die((e as Error).message);
  }
  try {
    validateSnapshotBundle(parsed, bundle);
  } catch (error) {
    die((error as Error).message);
  }
  let send: Value = NONE;
  if (a.hasSend) {
    try {
      send = jsonLoads(a.send);
    } catch (e) {
      const msg = e instanceof DiasporeError ? "invalid JSON" : (e as Error).message;
      die("bad --send JSON: " + msg);
    }
  }
  const journal: JournalEntry[] = [...parsed.journal, { m: "flow", f: "suspend", r: send }];
  let I: Interp;
  try {
    I = new Interp(bundle, {
      argv: parsed.argv ?? undefined,
      maxSteps: a.maxSteps,
      maxDepth: a.maxDepth,
      durable: true,
      journal,
    });
  } catch (e) {
    process.stderr.write(`diaspore: ${(e as Error).message}\n`);
    return 2;
  }
  let suspended = false;
  try {
    return I.run();
  } catch (e) {
    if (e instanceof SuspendSig) {
      suspended = true;
      const snapOut = a.snapshot !== "" ? a.snapshot : a.pos[1];
      return finishSuspend(I, bundle, e, snapOut);
    }
    throw e;
  } finally {
    if (!suspended) I.close();
  }
}

/**
 * The interpreter threads diaspore generators through JS generators, which costs
 * several JS stack frames per diaspore call frame; V8's default stack cannot hold
 * the spec's default 1000-frame diaspore recursion limit. The CLI re-execs
 * itself with a 6 MB stack, below the typical 8 MB OS thread stack.
 */
function reexecWithLargerStack(): never {
  const r = spawnSync(
    process.execPath,
    [
      ...(import.meta.url.endsWith(".ts") ? ["--experimental-strip-types", "--no-warnings"] : []),
      "--stack-size=6000",
      fileURLToPath(import.meta.url),
      ...process.argv.slice(2),
    ],
    { stdio: "inherit", env: { ...process.env, DIASPORE_TS_CHILD: "1" } },
  );
  process.exit(r.status ?? 1);
}

function main(): void {
  if (process.env.DIASPORE_TS_CHILD !== "1") reexecWithLargerStack();
  // A downstream reader closing the pipe (e.g. `| head`) must not crash the VM.
  const ignoreEpipe = (e: NodeJS.ErrnoException) => {
    if (e.code === "EPIPE") process.exit(0);
    throw e;
  };
  process.stdout.on("error", ignoreEpipe);
  process.stderr.on("error", ignoreEpipe);
  const args = process.argv.slice(2);
  if (args.length === 0) usage();
  let code: number;
  switch (args[0]) {
    case "run":
      code = cmdRun(args.slice(1));
      break;
    case "resume":
      code = cmdResume(args.slice(1));
      break;
    case "build":
      code = cmdBuild(args.slice(1));
      break;
    case "selftest-build":
      code = cmdSelftestBuild();
      break;
    default:
      usage();
  }
  process.exitCode = code;
}

main();
