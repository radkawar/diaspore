# HTTP server

A Python HTTP server runs from the same bundle on diaspore-py and diaspore-go
with explicit network access.

## Source

- `server.py`: routes, JSON handlers, and optional concurrent connections.
- `httpkit.py`: request parsing and response encoding.

## Build

From the repository root after `make build`:

```sh
uv run diaspore build examples/http/server.py -o build/http.dsp.json
```

## Run

Start either runtime, then run the curl commands in another terminal; stop the
server with Ctrl-C before switching runtimes.

```sh
uv run diaspore run build/http.dsp.json --allow-net
build/diaspore-go run build/http.dsp.json --allow-net

curl -sS 'http://127.0.0.1:8080/api/echo?msg=hello+diaspore'; printf '\n'
curl -sS http://127.0.0.1:8080/api/sum -d '{"values":[1,2,3.5]}'; printf '\n'
```

## Output

The curl responses are identical on both runtimes:

```text
{"params": {"msg": "hello diaspore"}, "headers": 3}
{"sum": 6.5, "count": 3}
```
