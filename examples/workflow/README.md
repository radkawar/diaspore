# Durable order workflow

An order moves from Python to Go to TypeScript and back to Python through
portable snapshots.

## Source

- `order.py`: suspends for approval, payment, and delivery.
- `run.sh`: runs the four stages and checks that suspension exits with code 7.

## Build

From the repository root after `make build`:

```sh
uv run diaspore build examples/workflow/order.py -o build/order.dsp.json
```

## Run

The runner starts diaspore-py with `--durable --deterministic`, resumes on
diaspore-go with `"approved"`, then diaspore-ts with `{"txn":"tx-1"}`, then
diaspore-py with `"delivered"`. Each suspension replaces
`build/order.flow.json`.

```sh
sh examples/workflow/run.sh
```

## Output

Standard output is below; each suspension also reports its payload and snapshot
path on standard error.

```text
[wf] ord-7311: created, total 130.0
[wf] ord-7311: needs human approval
[wf] ord-7311: approved
[wf] ord-7311: paid via tx-1
[wf] ord-7311: shipping attempt 1 scheduled
[wf] ord-7311: delivered on attempt 1
[wf] ord-7311: complete
```
