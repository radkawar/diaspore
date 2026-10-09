# Key/value server

A line-protocol server stores strings in memory and supports lazy key expiry.

## Source

- `server.py`: store, protocol dispatcher, and TCP server.

## Build

From the repository root after `make build`:

```sh
uv run diaspore build examples/kv/server.py -o build/kv.dsp.json
```

## Run

Start either runtime, then run the client in another terminal (requires netcat);
stop the server with Ctrl-C before switching runtimes.

```sh
uv run diaspore run build/kv.dsp.json --allow-net -- 7070
build/diaspore-go run build/kv.dsp.json --allow-net -- 7070

printf 'SET greeting hello\nGET greeting\nKEYS\nQUIT\n' | nc 127.0.0.1 7070
```

## Output

The client receives:

```text
+OK
$hello
*greeting
+bye
```
