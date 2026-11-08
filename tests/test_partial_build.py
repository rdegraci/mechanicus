"""Fixture tests for partial /build (ML8)."""
from mechanicus.plan_build import (
    build_command,
    format_module_text,
    module_records,
    module_statuses,
)
from mechanicus.paths import write_text_file
from mechanicus.store import mark_adopted, save_generation


def _write_module(root, name, uses, behavior="do work"):
    path = root / f"{name}.module"
    write_text_file(path, format_module_text(uses, behavior))
    return path


def test_build_one_unknown_name(project, capsys):
    _write_module(project, "lib", [], "library")
    build_command(project, "missing")
    out = capsys.readouterr().out
    assert "No module file" in out
    assert "missing" in out


def test_build_one_refuses_cycle(project, capsys):
    _write_module(project, "a", ["b"], "module a")
    _write_module(project, "b", ["a"], "module b")
    build_command(project, "a")
    out = capsys.readouterr().out
    assert "cycle" in out.lower()
    assert "a.py" not in {path.name for path in project.glob("*.py")}


def test_build_one_blocked_missing_dep(project, capsys):
    _write_module(project, "app", ["lib"], "application")
    build_command(project, "app")
    out = capsys.readouterr().out
    assert "lib" in out
    assert "not adopted" in out
    assert not (project / "app.py").exists()


def test_build_one_unchanged_when_adopted(project, capsys):
    _write_module(project, "lib", [], "library")
    record = module_records(project)[0]
    spec_id, generation = save_generation(
        "lib",
        record["text"],
        "x = 1\n",
        "import impl\n\ndef test_ok():\n    assert True\n",
        "import impl\n\ndef test_check():\n    assert True\n",
    )
    mark_adopted(spec_id, generation)
    build_command(project, "lib")
    out = capsys.readouterr().out
    assert (project / "lib.py").read_text() == "x = 1\n"
    assert "unchanged" in out or "already adopted" in out
    rows = {row["name"]: row for row in module_statuses(project)}
    # After build_command overlays outcomes only in its print; live status is ready/sync.
    assert rows["lib"]["sync"] == "ok"


def test_full_build_still_walks_graph(project, capsys):
    _write_module(project, "lib", [], "library")
    record = module_records(project)[0]
    spec_id, generation = save_generation(
        "lib",
        record["text"],
        "x = 1\n",
        "import impl\n\ndef test_ok():\n    assert True\n",
        "import impl\n\ndef test_check():\n    assert True\n",
    )
    mark_adopted(spec_id, generation)
    build_command(project)
    out = capsys.readouterr().out
    assert "modules before build" in out
    assert "modules after build" in out
    assert (project / "lib.py").is_file()
