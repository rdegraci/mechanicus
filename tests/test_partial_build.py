"""Fixture tests for partial /build (ML8)."""
from mechanicus.plan_build import (
    build_command,
    build_one,
    format_module_text,
    module_records,
    module_statuses,
)
from mechanicus.paths import write_text_file
from mechanicus.store import (
    get_spec,
    mark_adopted,
    record_deps_fingerprint,
    save_generation,
)


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


def test_unchanged_with_uses_records_jev_skipped(project, monkeypatch, capsys):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    _write_module(project, "lib", [], "library")
    lib_text = module_records(project)[0]["text"]
    lib_id, lib_gen = save_generation(
        "lib",
        lib_text,
        "VALUE = 1\n",
        "import impl\n\ndef test_lib():\n    assert impl.VALUE == 1\n",
        "import impl\n\ndef test_check():\n    assert impl.VALUE\n",
    )
    mark_adopted(lib_id, lib_gen)
    record_deps_fingerprint("lib", [])
    _write_module(project, "app", ["lib"], "application uses lib")
    app_record = next(r for r in module_records(project) if r["name"] == "app")
    app_id, app_gen = save_generation(
        "app",
        app_record["text"],
        "import lib\n\ndef run():\n    return lib.VALUE\n",
        "import impl\n\ndef test_app():\n    assert impl.run() == 1\n",
        "import impl\n\ndef test_check():\n    assert impl.run()\n",
    )
    mark_adopted(app_id, app_gen)
    record_deps_fingerprint("app", ["lib"])
    status, reason = build_one(project, app_record)
    assert status == "unchanged"
    assert reason == "already adopted"
    spec = get_spec("app")
    assert spec["jev_status"] == "skipped"
    assert spec["jev_gate"] == "tests"
    out = capsys.readouterr().out
    assert "Checking uses:" in out


def test_unchanged_weak_uses_forces_rebuild(project, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    _write_module(project, "lib", [], "library")
    lib_text = module_records(project)[0]["text"]
    lib_id, lib_gen = save_generation(
        "lib",
        lib_text,
        "VALUE = 1\n",
        "import impl\n\ndef test_lib():\n    assert impl.VALUE == 1\n",
        "import impl\n\ndef test_check():\n    assert impl.VALUE\n",
    )
    mark_adopted(lib_id, lib_gen)
    record_deps_fingerprint("lib", [])
    _write_module(project, "app", ["lib"], "application uses lib")
    app_record = next(r for r in module_records(project) if r["name"] == "app")
    app_id, app_gen = save_generation(
        "app",
        app_record["text"],
        "def run():\n    return 1\n",
        "import impl\n\ndef test_app():\n    assert impl.run() == 1\n",
        "import impl\n\ndef test_check():\n    assert impl.run()\n",
    )
    mark_adopted(app_id, app_gen)
    record_deps_fingerprint("app", ["lib"])
    monkeypatch.setattr(
        "mechanicus.plan_build.review_adopted_uses",
        lambda name, required=None, swift=None: [("lib", 0.1)],
    )
    called = {}

    def fake_spec(name, behavior, modules=None, record=None, required=None):
        called["name"] = name

    monkeypatch.setattr("mechanicus.plan_build.spec_command", fake_spec)
    monkeypatch.setattr(
        "mechanicus.plan_build.adopt_spec",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "mechanicus.plan_build.strengthen_adopted",
        lambda *args, **kwargs: "adequate",
    )
    status, reason = build_one(project, app_record)
    assert called.get("name") == "app"
    assert status == "failed"
    assert reason == "uses coverage rebuild was not saved"


def test_weak_uses_rebuild_continues_when_generation_saved(project, monkeypatch):
    _write_module(project, "lib", [], "library")
    lib_text = module_records(project)[0]["text"]
    lib_id, lib_gen = save_generation(
        "lib",
        lib_text,
        "VALUE = 1\n",
        "import impl\n\ndef test_lib():\n    assert impl.VALUE == 1\n",
        "import impl\n\ndef test_check():\n    assert impl.VALUE\n",
    )
    mark_adopted(lib_id, lib_gen)
    record_deps_fingerprint("lib", [])
    _write_module(project, "app", ["lib"], "application uses lib")
    app_record = next(r for r in module_records(project) if r["name"] == "app")
    app_id, app_gen = save_generation(
        "app",
        app_record["text"],
        "def run():\n    return 1\n",
        "import impl\n\ndef test_app():\n    assert impl.run() == 1\n",
        "import impl\n\ndef test_check():\n    assert impl.run()\n",
    )
    mark_adopted(app_id, app_gen)
    record_deps_fingerprint("app", ["lib"])
    monkeypatch.setattr(
        "mechanicus.plan_build.review_adopted_uses",
        lambda name, required=None, swift=None: [("lib", 0.1)],
    )

    def fake_spec(name, behavior, modules=None, record=None, required=None):
        save_generation(
            name,
            record or behavior,
            "import lib\n\ndef run():\n    return lib.VALUE\n",
            "import impl\n\ndef test_app():\n    assert impl.run() == 1\n",
            "import impl\n\ndef test_check():\n    assert impl.run()\n",
        )

    monkeypatch.setattr("mechanicus.plan_build.spec_command", fake_spec)
    monkeypatch.setattr(
        "mechanicus.plan_build.adopt_spec",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "mechanicus.plan_build.strengthen_adopted",
        lambda *args, **kwargs: "adequate",
    )
    status, reason = build_one(project, app_record)
    assert status == "built"
    assert "not saved" not in reason
