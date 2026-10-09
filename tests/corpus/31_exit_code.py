import sys

print("working")
if len(sys.argv) > 99:
    print("unreachable")
print("about to exit with 7")
sys.exit(7)
print("never printed")
