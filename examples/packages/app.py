"""Encode a message and group connected stations using bundled PyPI packages."""

from morse3 import Morse
from unionfind import unionfind

message = Morse("hello world").stringToMorse().strip()
print("morse:", message)
print("decoded:", Morse(message).morseToString())

stations = unionfind(5)
stations.unite(0, 1)
stations.unite(1, 2)
stations.unite(3, 4)
print("stations:", stations.groups())
