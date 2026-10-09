# Runtime benchmark

Four small workloads compare CPython with the same Python source compiled for
each Diaspore runtime.

## Source

- `bench.py`: recursive Fibonacci, integer arithmetic, string building, and
  dictionary access.

## Build

From the repository root:

```sh
make build
```

## Run

This compiles `bench.py` to `build/bench.dsp.json`, runs the source on CPython,
then runs the bundle on all four runtimes.

```sh
make bench
```

## Output

Each runtime prints this checksum followed by its measured workload times:

```text
check 46368 8999955000050000 318889 1000
```

Measured on Linux x86-64, Intel Core i7-13700K; one run, seconds per workload.
These are in-program timings, excluding process startup and bundle loading;
timings vary between runs.

| Runtime | fib(24) | loop300k | strings30k | dict50k |
| --- | ---: | ---: | ---: | ---: |
| CPython | 0.003 | 0.014 | 0.003 | 0.009 |
| diaspore-py | 0.713 | 0.282 | 0.058 | 0.214 |
| diaspore-go | 0.004 | 0.015 | 0.006 | 0.008 |
| diaspore-ts | 0.328 | 0.143 | 0.061 | 0.169 |
| diaspore-lua | 0.359 | 0.321 | 0.065 | 0.222 |
