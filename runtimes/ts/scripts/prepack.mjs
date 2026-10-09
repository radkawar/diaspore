import { accessSync, constants, copyFileSync } from "node:fs";

try {
  accessSync(new URL("../dspc.dsp.json.gz", import.meta.url), constants.R_OK);
} catch {
  console.error("Missing dspc.dsp.json.gz; run `make generate` at the repository root before packing.");
  process.exit(1);
}

copyFileSync(new URL("../../../LICENSE", import.meta.url), new URL("../LICENSE", import.meta.url));
