import os
import sys

from diaspore import Machine, compile_bundle

source = os.path.join(os.path.dirname(__file__), sys.argv[1])
bundle = compile_bundle(source)
print("compiled", len(bundle["modules"]))
print("exit", Machine(bundle, argv=[source]).run())
