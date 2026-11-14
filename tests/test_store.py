"""Fixture tests for SQLite specs and artifacts."""
from mechanicus.store import (
    clear_deps_fingerprint,
    deps_are_current,
    deps_fingerprint_for,
    generation_numbers,
    get_adopted_artifact,
    get_deps_fingerprint,
    get_latest_artifact,
    get_spec,
    list_spec_names,
    longer_than_adopted,
    mark_adopted,
    pin_spec,
    record_deps_fingerprint,
    save_generation,
    unpin_spec,
)


def test_save_generation_creates_spec_and_artifacts(project):
    spec_id, generation = save_generation(
        "greet",
        "Return hello",
        "def greet():\n    return 'hello'\n",
        "import impl\n\ndef test_greet():\n    assert impl.greet() == 'hello'\n",
        "import impl\n\ndef test_check():\n    assert impl.greet()\n",
    )
    assert generation == 1
    spec = get_spec("greet")
    assert spec is not None
    assert spec["id"] == spec_id
    assert spec["behavior"] == "Return hello"
    assert spec["pinned"] == 0
    assert list_spec_names() == ["greet"]
    code = get_latest_artifact(spec_id, "code")
    assert code["content"].startswith("def greet")
    assert code["adopted"] == 0
    assert code["generation"] == 1
    assert generation_numbers(spec_id) == [1]


def test_save_generation_increments_and_updates_behavior(project):
    save_generation("mod", "first", "a = 1\n", "t1", "c1")
    spec_id, generation = save_generation("mod", "second", "a = 2\n", "t2", "c2")
    assert generation == 2
    assert get_spec("mod")["behavior"] == "second"
    assert generation_numbers(spec_id) == [1, 2]


def test_mark_adopted_switches_generation(project):
    spec_id, first = save_generation("mod", "beh", "x = 1\n", "t", "c")
    _, second = save_generation("mod", "beh", "x = 2\n", "t", "c")
    assert mark_adopted(spec_id, first) is True
    assert get_adopted_artifact(spec_id, "code")["generation"] == first
    assert mark_adopted(spec_id, second) is True
    adopted = get_adopted_artifact(spec_id, "code")
    assert adopted["generation"] == second
    assert adopted["content"] == "x = 2\n"
    latest = get_latest_artifact(spec_id, "code")
    assert latest["generation"] == second


def test_mark_adopted_missing_generation(project):
    spec_id, generation = save_generation("mod", "beh", "x = 1\n", "t", "c")
    assert mark_adopted(spec_id, generation + 9) is False


def test_longer_than_adopted_same_behavior(project):
    spec_id, first = save_generation("mod", "beh", "x=1\n", "t", "c")
    mark_adopted(spec_id, first)
    _, second = save_generation("mod", "beh", "x = 1\ny = 2\n", "t", "c")
    candidate = get_latest_artifact(spec_id, "code")
    longer = longer_than_adopted(spec_id, candidate)
    assert longer is not None
    assert longer["generation"] == first


def test_longer_than_adopted_different_behavior_allows(project):
    spec_id, first = save_generation("mod", "old", "x=1\n", "t", "c")
    mark_adopted(spec_id, first)
    save_generation("mod", "new", "x = 1\ny = 2\n", "t", "c")
    candidate = get_latest_artifact(spec_id, "code")
    assert longer_than_adopted(spec_id, candidate) is None


def test_pin_and_unpin(project):
    save_generation("mod", "beh", "x = 1\n", "t", "c")
    assert pin_spec("mod") is True
    assert get_spec("mod")["pinned"] == 1
    code = get_latest_artifact(get_spec("mod")["id"], "code")
    assert code["pinned"] == 1
    assert unpin_spec("mod") is True
    assert get_spec("mod")["pinned"] == 0
    assert get_latest_artifact(get_spec("mod")["id"], "code")["pinned"] == 0


def test_pin_missing_spec(project):
    assert pin_spec("missing") is False
    assert unpin_spec("missing") is False


def test_save_generation_rejects_non_string(project):
    try:
        save_generation("mod", "beh", None, "t", "c")
    except TypeError as error:
        assert "code" in str(error)
    else:
        raise AssertionError("expected TypeError")


def test_deps_fingerprint_round_trip(project):
    lib_id, lib_gen = save_generation("lib", "library", "VALUE = 1\n", "t", "c")
    mark_adopted(lib_id, lib_gen)
    app_id, app_gen = save_generation("app", "application", "import lib\n", "t", "c")
    assert get_deps_fingerprint("app") is None
    assert deps_are_current("app", ["lib"]) is True
    mark_adopted(app_id, app_gen)
    assert deps_are_current("app", ["lib"]) is False
    assert deps_are_current("app", []) is True
    assert record_deps_fingerprint("app", ["lib"]) is True
    stored = get_deps_fingerprint("app")
    assert stored == deps_fingerprint_for(["lib"])
    assert '"lib"' in stored
    assert deps_are_current("app", ["lib"]) is True


def test_deps_are_current_false_when_dep_generation_bumps(project):
    lib_id, first = save_generation("lib", "library", "VALUE = 1\n", "t", "c")
    mark_adopted(lib_id, first)
    app_id, app_gen = save_generation("app", "application", "import lib\n", "t", "c")
    mark_adopted(app_id, app_gen)
    record_deps_fingerprint("app", ["lib"])
    _, second = save_generation("lib", "library", "VALUE = 2\n", "t", "c")
    mark_adopted(lib_id, second)
    assert deps_are_current("app", ["lib"]) is False
    record_deps_fingerprint("app", ["lib"])
    assert deps_are_current("app", ["lib"]) is True


def test_clear_deps_fingerprint(project):
    lib_id, lib_gen = save_generation("lib", "library", "VALUE = 1\n", "t", "c")
    mark_adopted(lib_id, lib_gen)
    app_id, app_gen = save_generation("app", "application", "import lib\n", "t", "c")
    mark_adopted(app_id, app_gen)
    record_deps_fingerprint("app", ["lib"])
    assert get_deps_fingerprint("app") is not None
    assert clear_deps_fingerprint("app") is True
    assert get_deps_fingerprint("app") is None
    assert deps_are_current("app", ["lib"]) is False
