RAW = (
    "GET /users/42?fields=name,email&limit=10 HTTP/1.1\r\n"
    "Host: api.example.com\r\n"
    "User-Agent: diaspore-test\r\n"
    "Accept: application/json\r\n"
    "X-Trace: abc123\r\n"
    "\r\n"
)


def parse_request(raw):
    sep = raw.find("\r\n\r\n")
    head, body = raw[:sep], raw[sep + 4:]
    lines = head.split("\r\n")
    method, target, version = lines[0].split(" ")
    path, query = (target.split("?", 1) + [""])[:2] if "?" in target else (target, "")
    headers = {}
    for line in lines[1:]:
        if not line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    params = {}
    if query:
        for pair in query.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                params[k] = v
            else:
                params[pair] = ""
    return {
        "method": method,
        "path": path,
        "version": version,
        "headers": headers,
        "params": params,
        "body": body,
    }


req = parse_request(RAW)
print(req["method"], req["path"], req["version"])
print(sorted(req["headers"].items()))
print(sorted(req["params"].items()))
print(req["params"]["fields"].split(","), int(req["params"]["limit"]) * 2)


def route(method, path, routes):
    for (m, pattern), handler in routes:
        if m != method:
            continue
        parts = pattern.split("/")
        got = path.split("/")
        if len(parts) != len(got):
            continue
        captures = {}
        ok = True
        for p, g in zip(parts, got):
            if p.startswith("<") and p.endswith(">"):
                captures[p[1:-1]] = g
            elif p != g:
                ok = False
                break
        if ok:
            return handler, captures
    return None, {}


routes = [
    (("GET", "/users/<id>"), "get_user"),
    (("GET", "/users"), "list_users"),
    (("POST", "/users"), "create_user"),
    (("GET", "/health"), "health"),
]

for method, path in [("GET", "/users/42"), ("GET", "/users"), ("POST", "/users"),
                     ("GET", "/health"), ("DELETE", "/users/1"), ("GET", "/nope")]:
    handler, caps = route(method, path, routes)
    print(method, path, "->", handler, sorted(caps.items()))


def status_line(code):
    reasons = {200: "OK", 201: "Created", 404: "Not Found", 500: "Internal Server Error"}
    return f"HTTP/1.1 {code} {reasons.get(code, 'Unknown')}"


def build_response(code, body, content_type="text/plain"):
    headers = [
        status_line(code),
        f"Content-Type: {content_type}",
        f"Content-Length: {len(body)}",
        "Connection: close",
    ]
    return "\r\n".join(headers) + "\r\n\r\n" + body


resp = build_response(200, "hello!")
print(repr(resp))
print(build_response(404, "gone").split("\r\n")[0])
