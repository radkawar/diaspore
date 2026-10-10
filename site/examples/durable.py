"""Compile, pause, and resume a workflow inside Diaspore."""

import io

from diaspore import Completed, Machine
from diaspore.compiler import ModuleCompiler

WORKFLOW = """
import flow

print("order 42: placed")
approved = flow.suspend("needs approval")
print("order 42: approved by", approved)
paid = flow.suspend("waiting for payment")
print("order 42: paid", paid, "- shipping")
"""

module = ModuleCompiler("order", "order.py", WORKFLOW).compile()
bundle = {"diaspore": 1, "main": "order", "modules": {"order": module}}
print("compiled the workflow:", len(str(bundle)), "chars of JSON IR\n")


def step(run, label):
    # Each resume uses a fresh VM and the preceding portable snapshot.
    out = io.StringIO()
    vm = Machine(bundle, stdout=out, durable=True)
    result = run(vm)
    print(f"[{label}]", out.getvalue().strip())
    if isinstance(result, Completed):
        print("  done, exit", result.code)
        return None
    print("  paused:", result.payload, "| snapshot:", len(str(result.snapshot)), "chars")
    return result.snapshot


snapshot = step(lambda vm: vm.run_durable(), "run")
snapshot = step(lambda vm: vm.resume(snapshot, send="alice"), "resume 1")
step(lambda vm: vm.resume(snapshot, send="$19.99"), "resume 2")
