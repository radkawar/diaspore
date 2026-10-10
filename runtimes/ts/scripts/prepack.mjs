import { accessSync, constants, copyFileSync } from "node:fs";

// Ship the generated DSPS02 preset-dictionary store alongside the gzip compiler bundle.
for (const file of [
  "../dspc.dsp.json.gz",
  "../stdlib.dsps",
  "../cpython/LICENSE",
  "../cpython/UNICODE_LICENSE",
]) {
  try {
    accessSync(new URL(file, import.meta.url), constants.R_OK);
  } catch {
    console.error(`Missing ${file.slice(3)}; run \`make generate\` at the repository root before packing.`);
    process.exit(1);
  }
}

copyFileSync(new URL("../../../LICENSE", import.meta.url), new URL("../LICENSE", import.meta.url));
