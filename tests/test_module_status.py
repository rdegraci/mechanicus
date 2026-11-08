"""Fixture tests for module DAG build status (HL4)."""
from mechanicus.plan_build import (
    build_one,
    format_module_text,
    module_statuses,
    modules_command,
)
from mechanicus.paths import write_text_file
from mechanicus.store import mark_adopted, pin_spec, save_generation


def _write_module(root, name, uses, behavior="do work"):
    path = root / f"{name}.module"
    write_text_file(path, format_module_text(uses, behavior))
    return path


def _by_name(rows):
    return {row["name"]: row for row in rows}


def test_module_statuses_ready_and_blocked(project):
    _write_module(project, "lib", [], "library")
    _write_module(project, "app", ["lib"], "application")
    _write_module(project, "broken", ["missing"], "needs missing")
    rows = _by_name(module_statuses(project))
    assert rows["lib"]["status"] == "ready"
    assert rows["app"]["status"] == "ready"
    assert rows["app"]["uses"] == ["lib"]
    assert rows["broken"]["status"] == "blocked"
    assert "missing" in rows["broken"]["reason"]
    assert rows["lib"]["sync"] == "none"


def test_module_statuses_cyclic_and_pinned(project):
    _write_module(project, "a", ["b"], "module a")
    _write_module(project, "b", ["a"], "module b")
    rows = _by_name(module_statuses(project))
    assert rows["a"]["status"] == "cyclic"
    assert rows["b"]["status"] == "cyclic"

    save_generation("a", "module a", "x = 1\n", "t", "c")
    pin_spec("a")
    rows = _by_name(module_statuses(project))
    assert rows["a"]["status"] == "pinned"
    assert rows["b"]["status"] == "cyclic"


def test_module_statuses_empty_and_layout(project):
    write_text_file(project / "empty.module", "\n")
    (project / "pkg").mkdir()
    (project / "pkg" / "__init__.py").write_text("")
    write_text_file(project / "pkg.module", "shadows package")
    rows = _by_name(module_statuses(project))
    assert rows["empty"]["status"] == "empty"
    assert rows["pkg"]["status"] == "layout"


def test_module_statuses_outcomes_overlay(project):
    _write_module(project, "lib", [], "library")
    rows = module_statuses(project, outcomes={"lib": ("built", "adopted and wrote disk")})
    assert rows[0]["status"] == "built"
    assert rows[0]["reason"] == "adopted and wrote disk"


def test_build_one_unchanged_when_already_adopted(project):
    _write_module(project, "lib", [], "library")
    from mechanicus.plan_build import module_records

    record = module_records(project)[0]
    spec_id, generation = save_generation(
        "lib",
        record["text"],
        "x = 1\n",
        "import impl\n\ndef test_ok():\n    assert True\n",
        "import impl\n\ndef test_check():\n    assert True\n",
    )
    mark_adopted(spec_id, generation)
    status, reason = build_one(project, record)
    assert status == "unchanged"
    assert "already adopted" in reason
    assert (project / "lib.py").is_file()


def test_modules_command_prints_table(project, capsys):
    _write_module(project, "lib", [], "library")
    modules_command(project)
    out = capsys.readouterr().out
    assert "name" in out
    assert "status" in out
    assert "lib" in out
    assert "ready" in out
