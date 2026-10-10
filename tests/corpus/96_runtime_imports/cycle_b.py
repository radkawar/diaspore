from cycle_a import *
observed = early
try:
    late
except NameError:
    observed += ":partial"
