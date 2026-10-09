"""A key/value server with a line protocol and lazy key expiry."""

import sys
import time

import net


class Store:
    def __init__(self):
        self.data = {}
        self.expiry = {}

    def _alive(self, key):
        if key in self.expiry and time.time() > self.expiry[key]:
            del self.data[key]
            del self.expiry[key]
        return key in self.data

    def set(self, key, value):
        self.data[key] = value
        self.expiry.pop(key, None)

    def get(self, key):
        return self.data[key] if self._alive(key) else None

    def delete(self, key):
        if self._alive(key):
            del self.data[key]
            self.expiry.pop(key, None)
            return 1
        return 0

    def keys(self):
        return sorted(k for k in list(self.data.keys()) if self._alive(k))

    def expire(self, key, secs):
        if not self._alive(key):
            return 0
        self.expiry[key] = time.time() + secs
        return 1


def execute(store, line):
    match line.split(" "):
        case [cmd, key, *words] if cmd.upper() == "SET" and words:
            store.set(key, " ".join(words))
            return "+OK"
        case [cmd, key] if cmd.upper() == "GET":
            v = store.get(key)
            return "-missing" if v is None else f"${v}"
        case [cmd, key] if cmd.upper() == "DEL":
            return f":{store.delete(key)}"
        case [cmd] if cmd.upper() == "KEYS":
            return "*" + ",".join(store.keys())
        case [cmd, key, ttl] if cmd.upper() == "EXPIRE":
            try:
                return f":{store.expire(key, float(ttl))}"
            except ValueError:
                return "-bad ttl"
        case [cmd, *_rest]:
            return f"-unknown command: {cmd.upper()}"
        case _:
            return "-empty"


def serve(port):
    store = Store()
    listener = net.listen("127.0.0.1", port)
    print(f"kv: listening on 127.0.0.1:{port}")
    while True:
        conn = net.accept(listener)
        print(f"kv: client {net.peer(conn)}")
        buf = b""
        alive = True
        while alive:
            chunk = net.recv(conn, 1024)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                idx = buf.find(b"\n")
                line = buf[:idx].decode("utf-8").strip()
                buf = buf[idx + 1:]
                if not line:
                    continue
                if line.upper() == "QUIT":
                    net.send(conn, b"+bye\n")
                    alive = False
                    break
                reply = execute(store, line)
                net.send(conn, (reply + "\n").encode("utf-8"))
        net.close(conn)
        print("kv: client done")


serve(int(sys.argv[1]) if len(sys.argv) > 1 else 7070)
