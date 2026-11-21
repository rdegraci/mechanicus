"""Fixture tests for last adopt result (ML6)."""
from mechanicus.lifecycle import adopt_spec, list_specs, show_spec
from mechanicus.store import get_spec, mark_adopted, save_generation


def test_adopt_ok_records_result(project, monkeypatch):
    monkeypatch.setattr(
        "mechanicus.adopt.run_tests",
        lambda code, tests, check, modules=None: (True, "ok"),
    )
    spec_id, generation = save_generation(
        "greet",
        "Return hello",
        "def greet():\n    return 'hello'\n",
        "import impl\n\ndef test_greet():\n    assert impl.greet() == 'hello'\n",
        "import impl\n\ndef test_check():\n    assert impl.greet()\n",
    )
    adopt_spec("greet", modules=[], required=[])
    spec = get_spec("greet")
    assert spec["adopt_status"] == "ok"
    assert spec["adopt_reason"] == "ok"
    assert spec["adopt_generation"] == generation
    assert spec_id == spec["id"]


def test_adopt_longer_records_failed(project):
    spec_id, first = save_generation("mod", "beh", "x=1\n", "t", "c")
    mark_adopted(spec_id, first)
    _, second = save_generation("mod", "beh", "x = 1\ny = 2\n", "t", "c")
    adopt_spec("mod", modules=[], required=[], allow_longer=False)
    spec = get_spec("mod")
    assert spec["adopt_status"] == "failed"
    assert spec["adopt_reason"] == "longer"
    assert spec["adopt_generation"] == second
    assert "lines" in (spec["adopt_detail"] or "")


def test_adopt_no_tests_records_failed(project):
    save_generation("mod", "beh", "x = 1\n", "", "check text")
    adopt_spec("mod", modules=[], required=[])
    spec = get_spec("mod")
    assert spec["adopt_status"] == "failed"
    assert spec["adopt_reason"] == "no_tests"


def test_adopt_harness_failure_records(project, monkeypatch):
    monkeypatch.setattr("mechanicus.adopt.configured_repair_attempts", lambda: 0)
    monkeypatch.setattr(
        "mechanicus.adopt.run_tests",
        lambda code, tests, check, modules=None: (False, "AssertionError: boom"),
    )
    spec_id, generation = save_generation(
        "mod",
        "beh",
        "x = 1\n",
        "import impl\n\ndef test_x():\n    assert False\n",
        "import impl\n\ndef test_check():\n    assert False\n",
    )
    adopt_spec("mod", modules=[], required=[])
    spec = get_spec("mod")
    assert spec["adopt_status"] == "failed"
    assert spec["adopt_reason"] == "harness"
    assert spec["adopt_generation"] == generation
    assert spec_id == spec["id"]


def test_save_generation_clears_adopt_result(project, monkeypatch):
    monkeypatch.setattr(
        "mechanicus.adopt.run_tests",
        lambda code, tests, check, modules=None: (True, "ok"),
    )
    save_generation("mod", "beh", "x = 1\n", "t", "c")
    adopt_spec("mod", modules=[], required=[])
    assert get_spec("mod")["adopt_status"] == "ok"
    save_generation("mod", "beh", "x = 2\n", "t", "c")
    spec = get_spec("mod")
    assert spec["adopt_status"] == "none"
    assert spec["adopt_reason"] == ""
    assert spec["adopt_generation"] is None


def test_show_and_specs_include_adopt(project, monkeypatch, capsys):
    monkeypatch.setattr(
        "mechanicus.adopt.run_tests",
        lambda code, tests, check, modules=None: (True, "ok"),
    )
    save_generation("greet", "Return hello", "x = 1\n", "t", "c")
    adopt_spec("greet", modules=[], required=[])
    show_spec("greet", project)
    out = capsys.readouterr().out
    assert "Adopt: ok" in out
    assert "reason=ok" in out
    list_specs(project)
    out = capsys.readouterr().out
    assert "adopt=ok" in out
