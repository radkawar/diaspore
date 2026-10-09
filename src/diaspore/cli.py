"""Diaspore CLI: build Python into JSON bundles, validate them, run them sandboxed."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .compiler import DiasporeCompileError, compile_bundle
from .encoding import load_bundle_file, write_bundle_file
from .validator import BundleValidationError, validate_bundle
from .vm import Machine, Suspended


def _caps_and_policy(args: argparse.Namespace) -> tuple[set, dict | None]:
    caps: set[str] = set()
    policy: dict = {}
    if args.allow_net:
        caps.add("net")
        if not all(s == "all" for s in args.allow_net):
            pol: dict[str, list[str]] = {"listen": [], "connect": []}
            for spec in args.allow_net:
                if spec == "all":
                    continue
                kind, _, addr = spec.partition(":")
                if kind not in pol or not addr:
                    raise SystemExit(
                        f"diaspore: bad --allow-net spec {spec!r} "
                        f"(use listen:HOST:PORT or connect:HOST:PORT; if you meant "
                        f"a bare grant, put --allow-net after the target or use "
                        f"--allow-net=all)")
                pol[kind].append(addr)
            policy["net"] = pol
    if args.allow_fs:
        caps.add("fs")
        if not all(s == "all" for s in args.allow_fs):
            pol = {"read": [], "write": []}
            for spec in args.allow_fs:
                if spec == "all":
                    continue
                kind, _, path = spec.partition(":")
                if kind not in pol or not path:
                    raise SystemExit(
                        f"diaspore: bad --allow-fs spec {spec!r} "
                        f"(use read:PATH or write:PATH)")
                pol[kind].append(path)
            policy["fs"] = pol
    if args.allow_spawn:
        caps.add("spawn")
    return caps, (policy or None)


def _finish_durable(outcome, snapshot_path: str) -> int:
    if isinstance(outcome, Suspended):
        Path(snapshot_path).write_text(json.dumps(outcome.snapshot), encoding="utf-8")
        print(f"diaspore: suspended (payload: {json.dumps(outcome.payload)}); "
              f"snapshot -> {snapshot_path}", file=sys.stderr)
        return 7
    return outcome.code


def _load_bundle(target: str, validate: bool, bindings: list[str] | None = None,
                 search_paths: list[str] | None = None) -> dict:
    path = Path(target)
    if path.suffix == ".py":
        # Embed source so in-memory compilation retains readable tracebacks.
        bundle = compile_bundle(str(path), extra_modules=set(bindings or ()), debug=True,
                                search_paths=search_paths)
    else:
        bundle = load_bundle_file(path)
    if validate:
        validate_bundle(bundle)
    return bundle


def cmd_build(args: argparse.Namespace) -> int:
    """Compile and write a validated bundle.

    Args:
        args: Parsed build command options.

    Returns:
        Zero on success.
    """
    bundle = compile_bundle(args.entry, extra_modules=set(args.binding),
                            debug=args.debug, search_paths=args.path)
    validate_bundle(bundle)
    out = args.output or str(Path(args.entry).with_suffix(".dsp.json"))
    size = write_bundle_file(bundle, out, pretty=args.pretty)
    n_modules = len(bundle["modules"])
    print(f"diaspore: built {out} ({n_modules} module{'s' if n_modules != 1 else ''}, "
          f"{size} bytes)", file=sys.stderr)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Compile and validate an entry point without writing a bundle.

    Args:
        args: Parsed check command options.

    Returns:
        Zero on success.
    """
    unresolved: dict = {}
    bundle = compile_bundle(args.entry, extra_modules=set(args.binding),
                            unresolved_out=unresolved, search_paths=args.path)
    validate_bundle(bundle)
    for dep, src in unresolved.items():
        print(f"diaspore: warning: import {dep!r} (from {src}) will raise "
              f"ImportError if executed", file=sys.stderr)
    print(f"diaspore: ok ({', '.join(sorted(bundle['modules']))})", file=sys.stderr)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Run a bundle or Python source file with the requested capabilities.

    Args:
        args: Parsed run command options and program arguments.

    Returns:
        The program exit code, or seven when execution suspends.
    """
    bundle = _load_bundle(args.target, validate=not args.no_validate,
                          bindings=args.binding, search_paths=args.path)
    caps, policy = _caps_and_policy(args)
    m = Machine(
        bundle,
        argv=[args.target, *args.args],
        caps=caps,
        policy=policy,
        max_steps=args.max_steps,
        max_depth=args.max_depth,
        durable=args.durable,
        deterministic=args.deterministic,
    )
    if args.durable:
        snap = args.snapshot or f"{args.target}.flow.json"
        return _finish_durable(m.run_durable(), snap)
    return m.run()


def cmd_resume(args: argparse.Namespace) -> int:
    """Resume a durable execution from a snapshot.

    Args:
        args: Parsed resume command options.

    Returns:
        The program exit code, or seven when execution suspends.
    """
    bundle = _load_bundle(args.bundle, validate=not args.no_validate)
    try:
        with open(args.snapshot_in, encoding="utf-8") as f:
            snapshot = json.load(f)
    except json.JSONDecodeError as e:
        raise SystemExit(f"diaspore: bad snapshot {args.snapshot_in}: {e.msg}") from None
    try:
        send = json.loads(args.send) if args.send is not None else None
    except json.JSONDecodeError:
        raise SystemExit(f"diaspore: --send must be JSON (got {args.send!r})") from None
    try:
        m = Machine(bundle, argv=snapshot.get("argv") or None, durable=True,
                    max_steps=args.max_steps, max_depth=args.max_depth)
        outcome = m.resume(snapshot, send)
    except ValueError as e:
        raise SystemExit(f"diaspore: {e}") from None
    out = args.snapshot or args.snapshot_in
    return _finish_durable(outcome, out)


def cmd_schema(args: argparse.Namespace) -> int:
    """Export the bundle JSON Schema.

    Args:
        args: Parsed schema command options.

    Returns:
        Zero on success, or two if schema dependencies are unavailable.
    """
    try:
        from .ir import bundle_schema  # Keep Pydantic optional for other commands.
    except ImportError:
        print("diaspore: schema export needs the dev tools: uv sync --group dev "
              "(runtime validation itself is dependency-free)", file=sys.stderr)
        return 2
    text = json.dumps(bundle_schema(), indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"diaspore: wrote {args.output}", file=sys.stderr)
    else:
        print(text)
    return 0


def cmd_dis(args: argparse.Namespace) -> int:
    """Print a validated bundle as indented JSON.

    Args:
        args: Parsed disassembly command options.

    Returns:
        Zero on success.
    """
    bundle = _load_bundle(args.target, validate=True)
    print(json.dumps(bundle, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns:
        A parser for all Diaspore commands.
    """
    p = argparse.ArgumentParser(
        prog="diaspore",
        description="Compile a Python subset to portable JSON; run it in a sandboxed VM.",
    )
    from . import __version__
    p.add_argument("--version", action="version", version=f"diaspore {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="compile entry.py (and its imports) to a .dsp.json bundle")
    b.add_argument("entry")
    b.add_argument("-o", "--output")
    b.add_argument("--pretty", action="store_true", help="indented JSON output")
    b.add_argument("--binding", action="append", default=[], metavar="NAME",
                   help="declare a host-binding module (repeatable)")
    b.add_argument("--path", action="append", default=[], metavar="DIR",
                   help="add a module search directory (repeatable)")
    b.add_argument("--debug", action="store_true",
                   help="embed source lines for source-mapped tracebacks")
    b.set_defaults(fn=cmd_build)

    c = sub.add_parser("check", help="compile + validate without writing output")
    c.add_argument("entry")
    c.add_argument("--binding", action="append", default=[], metavar="NAME")
    c.add_argument("--path", action="append", default=[], metavar="DIR",
                   help="add a module search directory (repeatable)")
    c.set_defaults(fn=cmd_check)

    r = sub.add_parser("run", help="run a .dsp.json bundle (or a .py, compiled in-memory)")
    r.add_argument("target")
    r.add_argument("args", nargs="*", help="argv for the program (after --)")
    r.add_argument("--allow-net", nargs="?", const="all", action="append", default=[],
                   metavar="SPEC",
                   help="grant net; optionally scoped (listen:HOST:PORT / "
                        "connect:HOST:PORT, '*' wildcards; repeatable)")
    r.add_argument("--allow-fs", nargs="?", const="all", action="append", default=[],
                   metavar="SPEC",
                   help="grant fs; optionally scoped (read:PATH / write:PATH; repeatable)")
    r.add_argument("--allow-spawn", action="store_true",
                   help="grant the 'spawn' capability (tasks module)")
    r.add_argument("--max-steps", type=int, default=None, help="step budget (trap on exhaustion)")
    r.add_argument("--max-depth", type=int, default=1000, help="max diaspore recursion depth")
    r.add_argument("--no-validate", action="store_true", help="skip IR schema validation")
    r.add_argument("--binding", action="append", default=[], metavar="NAME",
                   help="declare a host-binding module (repeatable; .py targets only)")
    r.add_argument("--path", action="append", default=[], metavar="DIR",
                   help="add a module search directory (repeatable; .py targets only)")
    r.add_argument("--durable", action="store_true",
                   help="durable run: flow.suspend() pauses to a portable snapshot")
    r.add_argument("--snapshot", metavar="PATH",
                   help="where to write the snapshot on suspend (default: TARGET.flow.json)")
    r.add_argument("--deterministic", action="store_true",
                   help="logical clock + seeded random (replayable runs)")
    r.set_defaults(fn=cmd_run)

    z = sub.add_parser("resume", help="resume a suspended durable run from its snapshot")
    z.add_argument("bundle", help="the same bundle the snapshot was taken from")
    z.add_argument("snapshot_in", metavar="snapshot", help="snapshot file from a suspended run")
    z.add_argument("--send", metavar="JSON",
                   help="value returned by the pending flow.suspend() (default null)")
    z.add_argument("--snapshot", metavar="PATH",
                   help="snapshot output on re-suspend (default: overwrite input)")
    z.add_argument("--max-steps", type=int, default=None)
    z.add_argument("--max-depth", type=int, default=1000)
    z.add_argument("--no-validate", action="store_true")
    z.set_defaults(fn=cmd_resume)

    s = sub.add_parser("schema", help="print the IR JSON Schema")
    s.add_argument("-o", "--output")
    s.set_defaults(fn=cmd_schema)

    d = sub.add_parser("dis", help="pretty-print a bundle's JSON")
    d.add_argument("target")
    d.set_defaults(fn=cmd_dis)

    return p


def main(argv: list[str] | None = None) -> int:
    """Dispatch a command and report compilation, validation, or file errors.

    Args:
        argv: Command-line arguments, or None to use sys.argv.

    Returns:
        The command exit code, or two for a reported error.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    prog_args: list[str] = []
    if "--" in argv:
        cut = argv.index("--")
        argv, prog_args = argv[:cut], argv[cut + 1:]
    args = build_parser().parse_args(argv)
    if hasattr(args, "args"):
        args.args = list(args.args) + prog_args
    try:
        return args.fn(args)
    except DiasporeCompileError as e:
        print(f"diaspore: compile error: {e}", file=sys.stderr)
        return 2
    except BundleValidationError as e:
        print(f"diaspore: invalid bundle: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"diaspore: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
