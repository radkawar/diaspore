"""HTTP/1.1 request parsing and response encoding."""

REASONS = {
    200: "OK", 201: "Created", 204: "No Content",
    400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed",
    500: "Internal Server Error",
}


class Request:
    def __init__(self, method, path, version, headers, params, body):
        self.method = method
        self.path = path
        self.version = version
        self.headers = headers
        self.params = params
        self.body = body

    def __repr__(self):
        return f"<Request {self.method} {self.path}>"

    def json(self):
        import json
        return json.loads(self.body.decode("utf-8"))


def parse_query(query):
    params = {}
    if not query:
        return params
    for pair in query.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[unquote(k)] = unquote(v)
        elif pair:
            params[unquote(pair)] = ""
    return params


def unquote(s):
    out = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "%" and i + 3 <= len(s):
            try:
                out.append(chr(int(s[i + 1:i + 3], 16)))
                i += 3
                continue
            except ValueError:
                pass
        if ch == "+":
            out.append(" ")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def parse_request(raw):
    """Parse the head of an HTTP request (bytes through the blank line)."""
    sep = raw.find(b"\r\n\r\n")
    head = raw[:sep].decode("latin-1")
    body = raw[sep + 4:]
    lines = head.split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3:
        raise ValueError(f"malformed request line: {lines[0]}")
    method, target, version = parts
    if "?" in target:
        path, query = target.split("?", 1)
    else:
        path, query = target, ""
    headers = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    return Request(method, path, version, headers, parse_query(query), body)


def response(status, body, content_type="text/plain; charset=utf-8", extra=None):
    if isinstance(body, str):
        body = body.encode("utf-8")
    lines = [
        f"HTTP/1.1 {status} {REASONS.get(status, 'Unknown')}",
        f"Content-Type: {content_type}",
        f"Content-Length: {len(body)}",
        "Connection: close",
        "Server: diaspore",
    ]
    if extra:
        for k, v in extra.items():
            lines.append(f"{k}: {v}")
    head = "\r\n".join(lines) + "\r\n\r\n"
    return head.encode("latin-1") + body


def json_response(status, value):
    import json
    return response(status, json.dumps(value), "application/json")


def html_response(status, html):
    return response(status, html, "text/html; charset=utf-8")
