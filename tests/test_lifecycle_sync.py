"""Fixture tests for DB ↔ disk sync (HL2)."""
from mechanicus.lifecycle import disk_sync_status, revert_spec, write_adopted
from mechanicus.store import (
    get_adopted_artifact,
    get_deps_fingerprint,
    get_spec,
    mark_adopted,
    record_deps_fingerprint,
    save_generation,
)


def _two_generations():
    spec_id, first = save_generation(
        "greet",
        "Return hello",
        "def greet():\n    return 'one'\n",
        "import impl\n\ndef test_greet():\n    assert impl.greet() == 'one'\n",
        "import impl\n\ndef test_check():\n    assert impl.greet()\n",
    )
    mark_adopted(spec_id, first)
    _, second = save_generation(
        "greet",
        "Return hello",
        "def greet():\n    return 'two'\n",
        "import impl\n\ndef test_greet():\n    assert impl.greet() == 'two'\n",
        "import impl\n\ndef test_check():\n    assert impl.greet()\n",
    )
    mark_adopted(spec_id, second)
    return spec_id, first, second


def test_disk_sync_none_missing_differ_ok(project):
    assert disk_sync_status(project, "missing") == "none"
    spec_id, generation = save_generation(
        "greet",
        "Return hello",
        "def greet():\n    return 'hello'\n",
        "import impl\n\ndef test_greet():\n    assert impl.greet() == 'hello'\n",
        "import impl\n\ndef test_check():\n    assert impl.greet()\n",
    )
    mark_adopted(spec_id, generation)
    assert disk_sync_status(project, "greet") == "missing"
    assert write_adopted(project, "greet") is True
    assert disk_sync_status(project, "greet") == "ok"
    (project / "greet.py").write_text("def greet():\n    return 'stale'\n")
    assert disk_sync_status(project, "greet") == "differ"


def test_revert_writes_disk_by_default(project):
    spec_id, first, second = _two_generations()
    assert write_adopted(project, "greet") is True
    assert (project / "greet.py").read_text() == "def greet():\n    return 'two'\n"
    assert get_adopted_artifact(spec_id, "code")["generation"] == second

    revert_spec("greet", first, project, write_disk=True)
    assert get_adopted_artifact(spec_id, "code")["generation"] == first
    assert (project / "greet.py").read_text() == "def greet():\n    return 'one'\n"
    assert disk_sync_status(project, "greet") == "ok"


def test_revert_db_only_leaves_disk(project):
    spec_id, first, second = _two_generations()
    assert write_adopted(project, "greet") is True
    revert_spec("greet", first, project, write_disk=False)
    assert get_adopted_artifact(spec_id, "code")["generation"] == first
    assert (project / "greet.py").read_text() == "def greet():\n    return 'two'\n"
    assert disk_sync_status(project, "greet") == "differ"


def test_revert_keeps_db_when_disk_write_blocked(project, monkeypatch):
    from mechanicus import lifecycle

    spec_id, first, second = _two_generations()
    assert write_adopted(project, "greet") is True

    def blocked(root, name):
        print("Skipped blocked write")
        return False

    monkeypatch.setattr(lifecycle, "write_adopted", blocked)
    revert_spec("greet", first, project, write_disk=True)
    assert get_adopted_artifact(spec_id, "code")["generation"] == first
    assert get_spec("greet") is not None
    assert (project / "greet.py").read_text() == "def greet():\n    return 'two'\n"


def test_revert_clears_deps_fingerprint(project):
    lib_id, lib_gen = save_generation("lib", "library", "VALUE = 1\n", "t", "c")
    mark_adopted(lib_id, lib_gen)
    app_id, first = save_generation(
        "app",
        "application",
        "import lib\nVALUE = 1\n",
        "t",
        "c",
    )
    mark_adopted(app_id, first)
    record_deps_fingerprint("app", ["lib"])
    _, second = save_generation(
        "app",
        "application",
        "import lib\nVALUE = 2\n",
        "t",
        "c",
    )
    mark_adopted(app_id, second)
    record_deps_fingerprint("app", ["lib"])
    assert get_deps_fingerprint("app") is not None
    revert_spec("app", first, project, write_disk=False)
    assert get_adopted_artifact(app_id, "code")["generation"] == first
    assert get_deps_fingerprint("app") is None
