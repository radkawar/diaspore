import { accessSync, constants, copyFileSync } from "node:fs";

for (const file of [
  "../dspc.dsp.json.gz",
  "../stdlib.json.gz",
  "../cpython/LICENSE",
  "../cpython/UNICODE_LICENSE",
  "../cpython/stdlib_manifest.json",
]) {
  try {
    accessSync(new URL(file, import.meta.url), constants.R_OK);
  } catch {
    console.error(`Missing ${file.slice(3)}; run \`make generate\` at the repository root before packing.`);
    process.exit(1);
  }
}

copyFileSync(new URL("../../../LICENSE", import.meta.url), new URL("../LICENSE", import.meta.url));
