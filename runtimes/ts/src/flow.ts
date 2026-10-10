/* Durable flows (spec 14): canonical bundle digest, snapshot build/parse. */

import { createHash } from "node:crypto";
import { gunzipSync } from "node:zlib";

import { type Value, type PyString, NONE, PyStr, DiasporeList, DiasporeDict, strCmp } from "./values.ts";
import { pyFloatStr } from "./format.ts";
import { jsonDumps, jsonLoads } from "./json.ts";
import type { Interp, JournalEntry } from "./interp.ts";

/** Transparently decompress gzip content (magic 1f 8b). */
export function maybeGunzip(data: Buffer): Buffer {
  if (data.length >= 2 && data[0] === 0x1f && data[1] === 0x8b) {
    return gunzipSync(data);
  }
  return data;
}

// --------------------------------------------------------------------------
// canonical JSON digest

/**
 * Canonical SHA-256 of a bundle (spec 14): UTF-8, compact separators,
 * sorted keys, non-ASCII unescaped, no HTML escaping; byte-identical to
 * diaspore-py's bundle_digest. Operates on the raw JSON.parse'd bundle.
 */
export function bundleDigest(bundle: unknown): string {
  const parts: string[] = [];
  canonicalJSON(parts, bundle);
  return createHash("sha256").update(Buffer.from(parts.join(""), "utf8")).digest("hex");
}

function canonicalJSON(b: string[], v: unknown): void {
  if (v === null) {
    b.push("null");
    return;
  }
  if (v instanceof PyStr) {
    b.push(canonicalEscape(v));
    return;
  }
  switch (typeof v) {
    case "boolean":
      b.push(v ? "true" : "false");
      return;
    case "string":
      b.push(canonicalEscape(v));
      return;
    case "number":
      if (Number.isInteger(v) && Math.abs(v) < 1e15) {
        b.push(String(v));
      } else {
        b.push(pyFloatStr(v));
      }
      return;
    case "bigint":
      b.push(v.toString());
      return;
  }
  if (Array.isArray(v)) {
    b.push("[");
    for (let i = 0; i < v.length; i++) {
      if (i > 0) b.push(",");
      canonicalJSON(b, v[i]);
    }
    b.push("]");
    return;
  }
  const obj = v as Record<string, unknown>;
  const keys = Object.keys(obj).sort(strCmp);
  b.push("{");
  for (let i = 0; i < keys.length; i++) {
    if (i > 0) b.push(",");
    b.push(canonicalEscape(keys[i]));
    b.push(":");
    canonicalJSON(b, obj[keys[i]]);
  }
  b.push("}");
}

/** Canonical JSON escaping, retaining Unicode scalars but escaping lone surrogates. */
export function canonicalEscape(s: PyString): string {
  let b = '"';
  for (const ch of s) {
    const r = ch.codePointAt(0) as number;
    switch (ch) {
      case '"':
        b += '\\"';
        break;
      case "\\":
        b += "\\\\";
        break;
      case "\n":
        b += "\\n";
        break;
      case "\r":
        b += "\\r";
        break;
      case "\t":
        b += "\\t";
        break;
      case "\b":
        b += "\\b";
        break;
      case "\f":
        b += "\\f";
        break;
      default:
        if (r < 0x20 || (r >= 0xd800 && r <= 0xdfff)) b += "\\u" + r.toString(16).padStart(4, "0");
        else b += ch;
    }
  }
  return b + '"';
}

// --------------------------------------------------------------------------
// snapshots

function linkedStdlib(bundle: unknown): string | null {
  if (typeof bundle !== "object" || bundle === null || !Object.hasOwn(bundle, "stdlib")) return null;
  const reference = (bundle as Record<string, unknown>).stdlib;
  if (typeof reference !== "string" || !/^sha256:[0-9a-f]{64}$/.test(reference)) {
    throw new Error("invalid bundle stdlib content id");
  }
  return reference;
}

/** Serializes the flow snapshot; journal object values keep their key order. */
export function buildSnapshot(I: Interp, bundle: unknown): string {
  const parts: string[] = [];
  parts.push('{"diaspore_flow": 1, "bundle_sha256": ');
  parts.push(canonicalEscape(bundleDigest(bundle)));
  const stdlib = linkedStdlib(bundle);
  if (stdlib !== null) {
    parts.push(', "stdlib": ');
    parts.push(canonicalEscape(stdlib));
  }
  parts.push(', "argv": ');
  parts.push(jsonDumps(new DiasporeList(I.argv.map((a) => a as Value)), NONE));
  parts.push(', "journal": [');
  for (let i = 0; i < I.journal.length; i++) {
    const e = I.journal[i];
    if (i > 0) parts.push(", ");
    parts.push('{"m": ');
    parts.push(canonicalEscape(e.m));
    parts.push(', "f": ');
    parts.push(canonicalEscape(e.f));
    parts.push(', "r": ');
    let rJSON: string;
    try {
      rJSON = jsonDumps(e.r, NONE);
    } catch {
      rJSON = "null";
    }
    parts.push(rJSON);
    parts.push("}");
  }
  parts.push("]}");
  return parts.join("");
}

/** Validated snapshot metadata and recorded effects. */
export interface ParsedSnapshot {
  argv: string[] | null;
  journal: JournalEntry[];
  bundleSha: string | null;
  stdlib: string | null;
}

/** Parses and validates a snapshot file's content (may be gzipped JSON).
 * Throws Error with a host-facing message on malformed input. */
export function parseSnapshot(data: Buffer): ParsedSnapshot {
  let v: Value;
  try {
    v = jsonLoads(maybeGunzip(data).toString("utf8"));
  } catch {
    throw new Error("bad snapshot: not valid JSON");
  }
  if (!(v instanceof DiasporeDict)) throw new Error("not a diaspore flow snapshot");
  if (v.getHK("sdiaspore_flow") !== 1n) throw new Error("not a diaspore flow snapshot");
  let bundleSha: string | null = null;
  const sha = v.getHK("sbundle_sha256");
  if (typeof sha === "string" && sha !== "") bundleSha = sha;
  const reference = v.getHK("sstdlib");
  if (reference !== undefined && (typeof reference !== "string" || !/^sha256:[0-9a-f]{64}$/.test(reference))) {
    throw new Error("bad snapshot: invalid stdlib content id");
  }
  const stdlib = reference === undefined ? null : reference as string;
  let argv: string[] | null = null;
  const av = v.getHK("sargv");
  if (av instanceof DiasporeList && av.items.length > 0) {
    argv = [];
    for (const x of av.items) {
      if (typeof x === "string") argv.push(x);
    }
  }
  const jv = v.getHK("sjournal");
  if (!(jv instanceof DiasporeList)) throw new Error("not a diaspore flow snapshot");
  const journal: JournalEntry[] = [];
  for (const raw of jv.items) {
    if (!(raw instanceof DiasporeDict)) throw new Error("malformed journal entry");
    const m = raw.getHK("sm");
    const f = raw.getHK("sf");
    let r = raw.getHK("sr");
    if (typeof m !== "string" || typeof f !== "string") {
      throw new Error("malformed journal entry");
    }
    if (r === undefined) r = NONE;
    journal.push({ m, f, r });
  }
  return { argv, journal, bundleSha, stdlib };
}

/** Reject a snapshot whose linked library or program digest differs from the bundle. */
export function validateSnapshotBundle(snapshot: ParsedSnapshot, bundle: unknown): void {
  const stdlib = linkedStdlib(bundle);
  if (snapshot.stdlib !== stdlib) {
    throw new Error(`snapshot stdlib mismatch (snapshot ${snapshot.stdlib ?? "none"}, bundle ${stdlib ?? "none"})`);
  }
  if (snapshot.bundleSha !== null) {
    const actual = bundleDigest(bundle);
    if (snapshot.bundleSha !== actual) {
      throw new Error(
        `snapshot was taken from a different bundle ` +
        `(snapshot ${snapshot.bundleSha.slice(0, 12)}..., this bundle ${actual.slice(0, 12)}...)`,
      );
    }
  }
}

/** Compact JSON of a suspend payload for the CLI's stderr line. */
export function payloadJSON(v: Value): string {
  try {
    return jsonDumps(v, NONE);
  } catch {
    return "null";
  }
}
