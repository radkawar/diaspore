"""Build the Python wheel's compact stdlib from the sdist's source inputs."""

import os
import subprocess
import sys

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Generate precompiled runtime data without trusting an existing artifact."""

    def initialize(self, version, build_data):
        """Compile current sources before packaging a wheel, including editable wheels."""
        if self.target_name != "wheel":
            return
        source = os.path.join(self.root, "src")
        artifact = os.path.join(source, "diaspore", "stdlib.dsps")
        subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, sys.argv[1]); "
             "from diaspore.stdlib_store import write_stdlib_store; "
             "write_stdlib_store(sys.argv[2])", source, artifact],
            cwd=self.root, check=True,
        )
        build_data["force_include"][artifact] = "diaspore/stdlib.dsps"
        # The native compiler imports these helpers; other library sources live in DSPB.
        for name in ("__init__.py", "_pyparse.py"):
            path = os.path.join(source, "diaspore", "stdlib", name)
            build_data["force_include"][path] = "diaspore/stdlib/" + name
