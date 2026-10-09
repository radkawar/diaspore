# Diaspore security model

Diaspore runs Python-subset programs with explicit capabilities and resource
limits. The threat model assumes an adversary controls source code or a bundle,
but not the runtime, host process, embedding code, or deployment configuration.
The runtime must prevent access to host objects and authority not granted by the
host. Resource isolation also requires operating-system controls.

## Untrusted inputs

The compiler is not a security boundary. Treat bundles as untrusted data even
when they were produced by `dspc` (`selfhost/dspc.py`). The typed IR is defined in
`src/diaspore/ir.py`; generate its JSON Schema with `diaspore schema` or
`make schema`, or use the [published schema](https://radkawar.github.io/diaspore/ir.schema.json).
Schema validation does not establish that a program is safe or will terminate.
Apply input-size limits to source, bundles, compressed containers, and snapshots
before loading them.

## Capabilities and bindings

Without grants, programs can compute, allocate, use the configured standard
streams, inspect supplied arguments, and call available native modules such as
`math`, `json`, `random`, `time`, and `re`. Clock access and randomness do not
require capabilities. Standard input is also available through `input()`.

| Capability | Authority | Scoped grants |
|---|---|---|
| `net` | TCP listeners and outbound connections | `--allow-net listen:HOST:PORT` or `connect:HOST:PORT`; `*` matches any host or port |
| `fs` | File access through `open` | `--allow-fs read:PATH` or `write:PATH`; resolved-path prefix matching |
| `spawn` | Tasks, channels, and locks | `--allow-spawn`; concurrency model in [spec section 11.9](spec.md#11-native-modules) |

A bare grant permits all operations covered by that capability. Scoped grants
deny operations not listed. Denied operations raise `PermissionError`.
Availability of native modules and capabilities differs by runtime; consult the
runtime README before deploying a policy.

Filesystem checks resolve symlinks before matching paths. They are not a
replacement for isolated mounts, especially where another process can modify
the filesystem between checking and opening a path. Network policy matches the
requested address, not a pinned DNS result. Use IP-based policies or a binding
that enforces destination policy when DNS changes are a concern.

Environment access, subprocesses, and FFI are not exposed as native sandbox APIs.
Where supported, `exec`, `eval`, and `compile` compile Diaspore-subset code and
execute it inside the VM; they do not execute arbitrary host Python.

Host bindings run with host privileges. Validate their inputs, keep their
interfaces narrow, and expose only the authority the program needs. A binding
can invalidate the sandbox's isolation if it evaluates input as host code or
performs unrestricted file or network operations.

## Resource limits

- Fuel (`--max-steps`) meters statements, calls, and selected bulk operations.
  Exhaustion is an uncatchable trap with exit code 3.
- Recursion depth (`--max-depth`, default 1000) raises a catchable
  `RecursionError` when exceeded.
- Fuel does not bound peak memory, time blocked in I/O or host bindings, or open
  descriptors. Set process memory and CPU limits, descriptor limits, and an
  external timeout for hostile workloads. Limit compiler resources as well as
  runtime resources.

## Runtime isolation

The four runtimes are diaspore-py, diaspore-go, diaspore-ts, and diaspore-lua.
Their implementations and trusted dependencies differ. The Python reference VM
uses allowlisted operations on CPython values; Go, TypeScript, and Lua implement
sandbox values and operations within their respective host environments.
Memory safety and allowlists do not prove that an interpreter is escape-free.

For untrusted multi-tenant workloads, run each tenant in a separate restricted
process or container. Grant minimal filesystem and network access to that
process, regardless of which runtime it uses. Do not rely on an interpreter bug
being limited to a wrong result or a controlled crash.

## Durable flows and nondeterminism

Durable mode forbids `net`, `fs`, and `spawn` capabilities. Supported effects,
including host-binding results, are journaled for replay; snapshots contain JSON
data rather than interpreter internals (see [spec section 14](spec.md#14-durable-flows-suspend--resume)).

Snapshots contain effect results and can therefore contain secrets. Protect
their confidentiality and integrity. The bundle digest binds a snapshot to a
program; it does not authenticate the journal or its author. Accept snapshots
only from trusted storage or authenticate them at the application boundary.

Clock values, randomness, set iteration order, object identities, and allocation
behavior can be observable. Diaspore does not provide timing-side-channel
isolation. Durable programs must remain deterministic between journaled effects.

## Reporting

Report vulnerabilities by opening a
[GitHub security advisory](https://github.com/radkawar/diaspore/security/advisories/new).
