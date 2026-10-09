import { maybeGunzip } from "./flow.ts";
import { isDSPB, dspbDecode } from "./dspb.ts";

/** A Diaspore intermediate-representation bundle. */
export interface Bundle {
  diaspore: 1;
  main: string;
  modules: Record<string, unknown>;
  [key: string]: unknown;
}

/** Decode a JSON, gzip, or DSPB bundle from bytes or JSON text. */
export function loadBundle(data: Uint8Array | string): Bundle {
  const bytes = maybeGunzip(Buffer.from(data));
  const value: unknown = isDSPB(bytes) ? dspbDecode(bytes) : JSON.parse(bytes.toString("utf8"));
  if (typeof value !== "object" || value === null || !("diaspore" in value) || value.diaspore !== 1 || !("main" in value) || typeof value.main !== "string" || !("modules" in value) || typeof value.modules !== "object" || value.modules === null || Array.isArray(value.modules)) {
    throw new Error("invalid Diaspore bundle");
  }
  return value as Bundle;
}

/** The sandbox interpreter and its embedding options. */
export { Interp, type InterpOpts, type Bindings, type HostFunction, type JournalEntry, type Policy } from "./interp.ts";
/** Errors and suspension signals that can cross the embedding boundary. */
export { DiasporeError, DiasporeTrap, SuspendSig } from "./values.ts";
