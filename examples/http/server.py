"""An HTTP/1.1 server with routing and optional concurrent request handling."""

import sys
import time

import net
import tasks
from httpkit import html_response, json_response, parse_request, response


class App:
    def __init__(self, name):
        self.name = name
        self.routes = {}
        self.hits = {}
        self.started = time.time()

    def route(self, method, path):
        def register(fn):
            self.routes[(method, path)] = fn
            return fn
        return register

    def dispatch(self, req):
        self.hits[req.path] = self.hits.get(req.path, 0) + 1
        handler = self.routes.get((req.method, req.path))
        if handler is None:
            for m, p in self.routes:
                if p == req.path and m != req.method:
                    return response(405, f"method {req.method} not allowed for {req.path}\n")
            return json_response(404, {"error": "not found", "path": req.path})
        try:
            return handler(req)
        except Exception as e:
            return json_response(500, {"error": str(e)})

    def serve(self, host, port):
        listener = net.listen(host, port)
        concurrent = self._concurrency_available()
        mode = "concurrent" if concurrent else "sequential (grant --allow-spawn for tasks)"
        print(f"[{self.name}] listening on http://{host}:{port} "
              f"(platform: {sys.platform}, {mode})")
        while True:
            conn = net.accept(listener)
            if concurrent:
                tasks.spawn(self._serve_conn, conn)
            else:
                self._serve_conn(conn)

    def _concurrency_available(self):
        try:
            tasks.lock()
            return True
        except PermissionError:
            return False

    def _serve_conn(self, conn):
        try:
            self.handle(conn)
        except OSError as e:
            print(f"[{self.name}] connection error: {e}")
        net.close(conn)

    def handle(self, conn):
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = net.recv(conn, 4096)
            if not chunk:
                return
            raw += chunk
        req = parse_request(raw)
        need = int(req.headers.get("content-length", "0"))
        while len(req.body) < need:
            req.body += net.recv(conn, 4096)
        reply = self.dispatch(req)
        net.send(conn, reply)
        print(f"[{self.name}] {req.method} {req.path} -> {reply.split(b' ')[1].decode('latin-1')}")


app = App("diaspore-http")


@app.route("GET", "/")
def index(req):
    rows = "".join(
        f"<li><code>{m} {p}</code></li>" for (m, p) in sorted(app.routes.keys())
    )
    return html_response(200, (
        "<!doctype html><title>diaspore</title>"
        "<h1>Hello from diaspore</h1>"
        f"<p>This server is Python, compiled to JSON, interpreted by <b>{sys.platform}</b>.</p>"
        f"<ul>{rows}</ul>"
    ))


@app.route("GET", "/api/time")
def api_time(req):
    return json_response(200, {
        "epoch": round(time.time(), 3),
        "uptime": round(time.time() - app.started, 3),
        "platform": sys.platform,
    })


@app.route("GET", "/api/echo")
def api_echo(req):
    return json_response(200, {"params": req.params, "headers": len(req.headers)})


@app.route("POST", "/api/sum")
def api_sum(req):
    payload = req.json()
    values = payload.get("values", [])
    if not isinstance(values, list):
        return json_response(400, {"error": "values must be a list"})
    return json_response(200, {"sum": sum(values), "count": len(values)})


@app.route("GET", "/api/stats")
def api_stats(req):
    return json_response(200, {"hits": app.hits, "routes": len(app.routes)})


@app.route("GET", "/api/slow")
def api_slow(req):
    seconds = min(float(req.params.get("s", "1")), 10.0)
    time.sleep(seconds)
    return json_response(200, {"slept": seconds})


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    app.serve("127.0.0.1", port)


main()
