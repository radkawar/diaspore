import type { Interp } from "./interp.ts";
import { DiasporeError, excIsA, type Value } from "./values.ts";

const closed = new WeakSet<Interp>();
const suspended = new WeakSet<Interp>();

/** Records a durable suspension, which is not interpreter shutdown. */
export function markInterpreterSuspended(I: Interp): void {
  suspended.add(I);
}

/** Runs imported shutdown hooks once, then releases native interpreter resources. */
export function closeInterpreter(I: Interp, cleanup: () => void): void {
  if (closed.has(I) || suspended.has(I)) return;
  closed.add(I);
  try {
    for (const [moduleName, functionName] of [
      ["threading", "_shutdown"],
      ["atexit", "_run_exitfuncs"],
    ]) {
      const module = I.modules.get(moduleName);
      if (module === undefined) continue;
      let hook: Value;
      try {
        hook = I.drive(I.getAttr(module, functionName));
      } catch (error) {
        // A failed module import can leave its namespace only partly initialized.
        if (error instanceof DiasporeError && excIsA(error.value, "AttributeError")) continue;
        throw error;
      }
      try {
        I.drive(I.invoke(hook, [], new Map()));
      } catch (error) {
        if (!(error instanceof DiasporeError)) throw error;
        I.stderrW(`Exception ignored in ${moduleName}.${functionName}:\n`);
        I.printTraceback(error);
      }
    }
  } finally {
    cleanup();
  }
}
