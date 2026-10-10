"""Linked stdlib compiler, embedding, CLI, and durable integration acceptance."""

from __future__ import annotations

import gzip
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from diaspore import Completed, Machine, Suspended, cli, compile_bundle, dspb, stdlib_store
from diaspore.encoding import load_bundle_bytes, load_bundle_file, write_bundle_file
from diaspore.vm import bundle_digest

OTHER_ID = "sha256:" + "0" * 64
MALFORMED_REFERENCES = [
    None,
    1,
    False,
    [],
    {},
    "sha256:" + "A" * 64,
    "sha256:" + "0" * 63,
    "sha256:" + "0" * 65,
]
PROGRAMS = [
    ("print('hello')\n", None, "hello\n"),
    ("import json\nprint(json.dumps([1, 2]))\n", "json", "[1, 2]\n"),
    (
        "import itertools\nprint(list(itertools.chain([1], [2, 3])))\n",
        "itertools",
        "[1, 2, 3]\n",
    ),
]


def source_file(tmp_path: Path, source: str) -> Path:
    entry = tmp_path / "prog.py"
    entry.write_text(source, encoding="utf-8")
    return entry


def execute(bundle: dict) -> str:
    out, err = io.StringIO(), io.StringIO()
    machine = Machine(bundle, stdout=out, stderr=err)
    try:
        assert machine.run() == 0, err.getvalue()
        return out.getvalue()
    finally:
        machine.close()


@pytest.mark.parametrize("source,module,expected", PROGRAMS)
def test_default_bundle_links_installed_store(tmp_path, source, module, expected):
    bundle = compile_bundle(str(source_file(tmp_path, source)))
    assert bundle["stdlib"] == stdlib_store.get_stdlib_id()
    assert set(bundle["modules"]) == {"prog"}
    if module is not None:
        assert stdlib_store.get_stdlib_module(module) is not None
    assert execute(bundle) == expected


def test_stdlib_initialization_is_lazy_and_cached(tmp_path):
    bundle = compile_bundle(str(source_file(tmp_path, (
        "def load():\n"
        "    import json\n"
        "    return json.dumps([1])\n"
    ))))
    machine = Machine(bundle, stdout=io.StringIO(), stderr=io.StringIO())
    try:
        assert "json" not in machine.interp.modules
        assert "itertools" not in machine.interp.modules
        assert machine.run() == 0
        assert "json" not in machine.interp.modules
        assert machine.call("prog", "load") == "[1]"
        imported = machine.interp.modules["json"]
        assert machine.call("prog", "load") == "[1]"
        assert machine.interp.modules["json"] is imported
        assert "itertools" not in machine.interp.modules
        assert set(bundle["modules"]) == {"prog"}
    finally:
        machine.close()


def test_stdlib_module_globals_are_per_machine(tmp_path):
    bundle = compile_bundle(str(source_file(tmp_path, (
        "import json\n"
        "print(hasattr(json, 'application_marker'))\n"
        "json.application_marker = 1\n"
    ))))
    assert execute(bundle) == "False\n"
    assert execute(bundle) == "False\n"


@pytest.mark.parametrize("extra_path", [False, True])
def test_user_source_shadows_linked_store(tmp_path, extra_path):
    root = tmp_path / "library" if extra_path else tmp_path
    root.mkdir(exist_ok=True)
    (root / "json.py").write_text("def marker():\n    return 'local json'\n", encoding="utf-8")
    entry = source_file(tmp_path, "import json\nprint(json.marker())\n")
    bundle = compile_bundle(str(entry), search_paths=[str(root)] if extra_path else [])
    assert bundle["stdlib"] == stdlib_store.get_stdlib_id()
    assert set(bundle["modules"]) == {"prog", "json"}
    assert bundle["modules"]["json"]["file"] != "<stdlib>/json.py"
    assert execute(bundle) == "local json\n"


@pytest.mark.parametrize("extra_path", [False, True])
def test_user_package_shadows_parent_of_linked_child(tmp_path, extra_path):
    root = tmp_path / "library" if extra_path else tmp_path
    package = root / "json"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("marker = 'local package'\n", encoding="utf-8")
    entry = source_file(tmp_path, (
        "import json.decoder\n"
        "print(json.marker)\n"
        "print(json.decoder.JSONDecoder().decode('[1, 2]'))\n"
    ))
    bundle = compile_bundle(str(entry), search_paths=[str(root)] if extra_path else [])
    assert bundle["stdlib"] == stdlib_store.get_stdlib_id()
    assert set(bundle["modules"]) == {"prog", "json"}
    assert bundle["modules"]["json"]["file"] != "<stdlib>/json/__init__.py"
    assert execute(bundle) == "local package\n[1, 2]\n"


@pytest.mark.parametrize("source,module,expected", PROGRAMS)
def test_self_contained_bundle_embeds_reachable_stdlib(tmp_path, source, module, expected):
    bundle = compile_bundle(str(source_file(tmp_path, source)), self_contained=True)
    assert "stdlib" not in bundle
    if module is not None:
        assert module in bundle["modules"]
    with patch.object(stdlib_store, "get_stdlib_id", return_value=OTHER_ID) as installed:
        assert execute(bundle) == expected
    installed.assert_not_called()


@pytest.mark.parametrize("self_contained", [False, True])
def test_standalone_never_falls_back_to_installed_stdlib(tmp_path, monkeypatch, self_contained):
    bundle = compile_bundle(
        str(source_file(tmp_path, (
            "try:\n"
            "    import json\n"
            "except ImportError:\n"
            "    print('no implicit stdlib')\n"
            "else:\n"
            "    print('unexpected fallback')\n"
        ))),
        stdlib=False,
        self_contained=self_contained,
    )
    assert "stdlib" not in bundle
    assert set(bundle["modules"]) == {"prog"}

    def forbidden(*args):
        pytest.fail("standalone execution accessed the installed stdlib")

    for name in ("get_stdlib_id", "get_stdlib_module", "stdlib_module_names"):
        monkeypatch.setattr(stdlib_store, name, forbidden)
    assert execute(bundle) == "no implicit stdlib\n"


@pytest.mark.parametrize("validate", [False, True])
def test_link_mismatch_fails_before_side_effect(tmp_path, monkeypatch, validate):
    bundle = compile_bundle(str(source_file(tmp_path, (
        "import host\n"
        "host.effect()\n"
        "print('program ran')\n"
    ))), extra_modules={"host"})
    effects = []
    out = io.StringIO()
    monkeypatch.setattr(stdlib_store, "get_stdlib_id", lambda: OTHER_ID)
    with pytest.raises(ValueError) as exc:
        Machine(bundle, stdout=out, bindings={"host": {"effect": lambda: effects.append(1)}},
                validate=validate)
    assert bundle["stdlib"] in str(exc.value)
    assert OTHER_ID in str(exc.value)
    assert not effects
    assert out.getvalue() == ""


def test_stdlib_reference_participates_in_bundle_digest(tmp_path):
    bundle = compile_bundle(str(source_file(tmp_path, "pass\n")))
    assert bundle_digest(bundle) != bundle_digest(dict(bundle, stdlib=OTHER_ID))
    standalone = dict(bundle)
    del standalone["stdlib"]
    assert bundle_digest(bundle) != bundle_digest(standalone)


@pytest.mark.parametrize("self_contained", [False, True])
def test_durable_snapshot_retains_link_mode(tmp_path, self_contained, monkeypatch):
    bundle = compile_bundle(str(source_file(tmp_path, (
        "import flow, json\n"
        "print('start')\n"
        "answer = flow.suspend(json.dumps([1]))\n"
        "print(answer)\n"
    ))), self_contained=self_contained)
    out = io.StringIO()
    first = Machine(bundle, stdout=out, durable=True)
    try:
        result = first.run_durable()
        assert isinstance(result, Suspended)
        assert result.payload == "[1]"
        assert out.getvalue() == "start\n"
        snapshot = json.loads(json.dumps(result.snapshot))
    finally:
        first.close()
    if self_contained:
        assert "stdlib" not in snapshot
    else:
        assert snapshot["stdlib"] == bundle["stdlib"]
        assert snapshot["bundle_sha256"] == bundle_digest(bundle)
    if self_contained:
        monkeypatch.setattr(stdlib_store, "get_stdlib_id", lambda: OTHER_ID)
    resumed_out = io.StringIO()
    resumed = Machine(bundle, stdout=resumed_out, durable=True)
    try:
        completed = resumed.resume(snapshot, send="answer")
        assert isinstance(completed, Completed)
        assert completed.code == 0
        assert resumed_out.getvalue() == "answer\n"
    finally:
        resumed.close()


@pytest.mark.parametrize("snapshot_id", [None, OTHER_ID])
def test_durable_snapshot_rejects_reference_mismatch(tmp_path, snapshot_id):
    bundle = compile_bundle(str(source_file(tmp_path, (
        "import flow\nprint('start')\nflow.suspend(None)\nprint('finish')\n"
    ))))
    first = Machine(bundle, stdout=io.StringIO(), durable=True)
    try:
        result = first.run_durable()
        assert isinstance(result, Suspended)
        snapshot = dict(result.snapshot)
    finally:
        first.close()
    if snapshot_id is None:
        del snapshot["stdlib"]
    else:
        snapshot["stdlib"] = snapshot_id
    out = io.StringIO()
    resumed = Machine(bundle, stdout=out, durable=True)
    try:
        with pytest.raises(ValueError, match="stdlib") as exc:
            resumed.resume(snapshot)
        assert bundle["stdlib"] in str(exc.value)
        if snapshot_id is not None:
            assert snapshot_id in str(exc.value)
        assert out.getvalue() == ""
    finally:
        resumed.close()


def test_durable_resume_rechecks_installed_store(tmp_path, monkeypatch):
    bundle = compile_bundle(str(source_file(tmp_path, "import flow\nflow.suspend(None)\n")))
    first = Machine(bundle, stdout=io.StringIO(), durable=True)
    resumed = Machine(bundle, stdout=io.StringIO(), durable=True)
    try:
        result = first.run_durable()
        assert isinstance(result, Suspended)
        monkeypatch.setattr(stdlib_store, "get_stdlib_id", lambda: OTHER_ID)
        with pytest.raises(ValueError) as exc:
            resumed.resume(result.snapshot)
        assert bundle["stdlib"] in str(exc.value)
        assert OTHER_ID in str(exc.value)
    finally:
        first.close()
        resumed.close()


@pytest.mark.parametrize("command", ["build", "check", "run"])
def test_cli_help_documents_self_contained(command, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([command, "--help"])
    assert exc.value.code == 0
    assert "--self-contained" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["build", "check", "run"])
@pytest.mark.parametrize("self_contained", [False, True])
def test_cli_source_paths_forward_link_mode(tmp_path, command, self_contained, capsys):
    entry = source_file(tmp_path, "import json\nprint(json.dumps([1]))\n")
    args = [command, str(entry)]
    if self_contained:
        args.append("--self-contained")
    with patch.object(cli, "compile_bundle", wraps=compile_bundle) as compile_spy:
        assert cli.main(args) == 0
    assert compile_spy.call_count == 1
    assert compile_spy.call_args.kwargs["self_contained"] is self_contained
    captured = capsys.readouterr()
    if command == "run":
        assert captured.out == "[1]\n"
    if command == "check":
        assert "diaspore: ok" in captured.err
        if not self_contained:
            assert "warning" not in captured.err


@pytest.mark.parametrize("suffix", [".dsp.json", ".dsp.json.gz", ".dspb"])
@pytest.mark.parametrize("self_contained", [False, True])
def test_cli_build_and_run_containers(tmp_path, suffix, self_contained, capsys):
    entry = source_file(tmp_path, "import itertools\nprint(list(itertools.chain([1], [2])))\n")
    target = tmp_path / ("program" + suffix)
    args = ["build", str(entry), "-o", str(target)]
    if self_contained:
        args.append("--self-contained")
    assert cli.main(args) == 0
    bundle = load_bundle_file(target)
    if self_contained:
        assert "stdlib" not in bundle
        assert "itertools" in bundle["modules"]
    else:
        assert bundle["stdlib"] == stdlib_store.get_stdlib_id()
        assert set(bundle["modules"]) == {"prog"}
    capsys.readouterr()
    assert cli.main(["run", str(target)]) == 0
    assert capsys.readouterr().out == "[1, 2]\n"


@pytest.mark.parametrize("self_contained", [False, True])
def test_gzip_wrapped_dspb_preserves_stdlib_reference(tmp_path, self_contained):
    bundle = compile_bundle(str(source_file(tmp_path, "print('hello')\n")),
                            self_contained=self_contained)
    encoded = dspb.encode(bundle)
    assert encoded.startswith(b"DSPB01" if self_contained else b"DSPB02")
    decoded = load_bundle_bytes(gzip.compress(encoded))
    assert decoded == bundle
    assert execute(decoded) == "hello\n"


def test_cli_reports_link_mismatch_without_execution(tmp_path, capsys):
    bundle = compile_bundle(str(source_file(tmp_path, "print('program ran')\n")))
    installed_id = bundle["stdlib"]
    bundle["stdlib"] = OTHER_ID
    target = tmp_path / "mismatch.dsp.json"
    write_bundle_file(bundle, target)
    with pytest.raises(SystemExit) as exc:
        cli.main(["run", str(target)])
    assert exc.value.code != 0
    assert OTHER_ID in str(exc.value)
    assert installed_id in str(exc.value)
    assert str(exc.value).startswith("diaspore:")
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("mismatch", ["snapshot", "installed"])
def test_cli_resume_reports_stdlib_mismatch(tmp_path, monkeypatch, capsys, mismatch):
    bundle = compile_bundle(str(source_file(tmp_path, (
        "import flow\nflow.suspend(None)\nprint('resumed')\n"
    ))))
    machine = Machine(bundle, stdout=io.StringIO(), durable=True)
    try:
        outcome = machine.run_durable()
        assert isinstance(outcome, Suspended)
        snapshot = dict(outcome.snapshot)
    finally:
        machine.close()
    target = tmp_path / "program.dsp.json"
    snapshot_path = tmp_path / "program.flow.json"
    write_bundle_file(bundle, target)
    if mismatch == "snapshot":
        snapshot["stdlib"] = OTHER_ID
    else:
        monkeypatch.setattr(stdlib_store, "get_stdlib_id", lambda: OTHER_ID)
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cli.main(["resume", str(target), str(snapshot_path)])
    assert exc.value.code != 0
    assert bundle["stdlib"] in str(exc.value)
    assert OTHER_ID in str(exc.value)
    assert str(exc.value).startswith("diaspore:")
    assert capsys.readouterr().out == ""


def test_cli_dis_source_defaults_to_linked_without_search_paths(tmp_path, capsys):
    entry = source_file(tmp_path, "print('hello')\n")
    assert cli.main(["dis", str(entry)]) == 0
    bundle = json.loads(capsys.readouterr().out)
    assert bundle["stdlib"] == stdlib_store.get_stdlib_id()
    assert set(bundle["modules"]) == {"prog"}


@pytest.mark.parametrize("reference", MALFORMED_REFERENCES)
def test_machine_rejects_malformed_reference_without_schema_validation(tmp_path, reference):
    bundle = compile_bundle(str(source_file(tmp_path, "print('program ran')\n")), stdlib=False)
    bundle["stdlib"] = reference
    out = io.StringIO()
    with pytest.raises(ValueError, match="stdlib"):
        Machine(bundle, stdout=out, validate=False)
    assert out.getvalue() == ""


@pytest.mark.parametrize("reference", MALFORMED_REFERENCES)
@pytest.mark.parametrize("self_contained", [False, True])
def test_resume_rejects_malformed_snapshot_reference(tmp_path, reference, self_contained):
    bundle = compile_bundle(str(source_file(tmp_path, (
        "import flow\nflow.suspend(None)\nprint('resumed')\n"
    ))), self_contained=self_contained)
    out = io.StringIO()
    machine = Machine(bundle, stdout=out, durable=True, validate=False)
    try:
        outcome = machine.run_durable()
        assert isinstance(outcome, Suspended)
        snapshot = dict(outcome.snapshot, stdlib=reference)
        with pytest.raises(ValueError, match="stdlib"):
            machine.resume(snapshot)
        assert out.getvalue() == ""
    finally:
        machine.close()
