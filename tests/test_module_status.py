"""Fixture tests for module DAG build status (HL4)."""
from mechanicus.plan_build import (
    build_one,
    format_module_text,
    module_records,
    module_statuses,
    modules_command,
)
from mechanicus.paths import write_text_file
from mechanicus.store import mark_adopted, pin_spec, record_deps_fingerprint, save_generation


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


def test_module_statuses_stale_when_upstream_changes(project):
    _write_module(project, "lib", [], "library")
    _write_module(project, "app", ["lib"], "application")
    lib_id, lib_gen = save_generation("lib", "library", "VALUE = 1\n", "t", "c")
    mark_adopted(lib_id, lib_gen)
    record_deps_fingerprint("lib", [])
    app_text = format_module_text(["lib"], "application").strip()
    app_id, app_gen = save_generation("app", app_text, "import lib\n", "t", "c")
    mark_adopted(app_id, app_gen)
    record_deps_fingerprint("app", ["lib"])
    rows = _by_name(module_statuses(project))
    assert rows["app"]["status"] == "ready"
    _, lib_gen2 = save_generation("lib", "library", "VALUE = 2\n", "t", "c")
    mark_adopted(lib_id, lib_gen2)
    rows = _by_name(module_statuses(project))
    assert rows["app"]["status"] == "stale"
    assert rows["app"]["reason"] == "upstream changed"


def test_module_statuses_stale_when_deps_not_recorded(project):
    _write_module(project, "lib", [], "library")
    _write_module(project, "app", ["lib"], "application")
    lib_id, lib_gen = save_generation("lib", "library", "VALUE = 1\n", "t", "c")
    mark_adopted(lib_id, lib_gen)
    app_text = format_module_text(["lib"], "application").strip()
    app_id, app_gen = save_generation("app", app_text, "import lib\n", "t", "c")
    mark_adopted(app_id, app_gen)
    rows = _by_name(module_statuses(project))
    assert rows["app"]["status"] == "stale"
    assert rows["app"]["reason"] == "deps not recorded"


def test_module_statuses_stale_pinned_reason(project):
    _write_module(project, "lib", [], "library")
    _write_module(project, "app", ["lib"], "application")
    lib_id, lib_gen = save_generation("lib", "library", "VALUE = 1\n", "t", "c")
    mark_adopted(lib_id, lib_gen)
    app_text = format_module_text(["lib"], "application").strip()
    app_id, app_gen = save_generation("app", app_text, "import lib\n", "t", "c")
    mark_adopted(app_id, app_gen)
    record_deps_fingerprint("app", ["lib"])
    pin_spec("app")
    _, lib_gen2 = save_generation("lib", "library", "VALUE = 2\n", "t", "c")
    mark_adopted(lib_id, lib_gen2)
    rows = _by_name(module_statuses(project))
    assert rows["app"]["status"] == "stale"
    assert "pinned" in rows["app"]["reason"]
    assert "upstream" in rows["app"]["reason"]


def test_build_one_readopts_when_upstream_changes(project):
    _write_module(project, "lib", [], "library")
    _write_module(project, "app", ["lib"], "application")
    lib_text = format_module_text([], "library").strip()
    app_text = format_module_text(["lib"], "application").strip()
    lib_id, lib_gen = save_generation(
        "lib",
        lib_text,
        "VALUE = 1\n",
        "import impl\n\ndef test_ok():\n    assert impl.VALUE == 1\n",
        "import impl\n\ndef test_check():\n    assert impl.VALUE == 1\n",
    )
    mark_adopted(lib_id, lib_gen)
    record_deps_fingerprint("lib", [])
    app_code = "import lib\n\ndef run():\n    return lib.VALUE\n"
    app_tests = (
        "import impl\n\n"
        "def test_run():\n"
        "    assert impl.run() is not None\n"
    )
    app_check = (
        "import impl\n\n"
        "def test_check():\n"
        "    assert impl.run() is not None\n"
    )
    app_id, app_gen = save_generation("app", app_text, app_code, app_tests, app_check)
    mark_adopted(app_id, app_gen)
    record_deps_fingerprint("app", ["lib"])
    _, lib_gen2 = save_generation(
        "lib",
        lib_text,
        "VALUE = 2\n",
        "import impl\n\ndef test_ok():\n    assert impl.VALUE == 2\n",
        "import impl\n\ndef test_check():\n    assert impl.VALUE == 2\n",
    )
    mark_adopted(lib_id, lib_gen2)
    record_deps_fingerprint("lib", [])
    app_record = next(record for record in module_records(project) if record["name"] == "app")
    status, reason = build_one(project, app_record)
    assert status == "built"
    assert "re-adopted after upstream change" in reason
    assert (project / "app.py").read_text() == app_code
    rows = _by_name(module_statuses(project))
    assert rows["app"]["status"] == "ready"


def test_modules_command_prints_table(project, capsys):
    _write_module(project, "lib", [], "library")
    modules_command(project)
    out = capsys.readouterr().out
    assert "name" in out
    assert "status" in out
    assert "lib" in out
    assert "ready" in out
