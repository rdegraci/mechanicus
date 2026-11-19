"""Fixture tests for Jev ok/skipped/failed outcomes (ML7)."""
from mechanicus.jev import (
    clear_last_jev,
    last_jev_result,
    note_jev_result,
    weak_call_tests,
)
from mechanicus.lifecycle import list_specs, show_spec, spec_command
from mechanicus.store import get_spec, record_jev_result, save_generation


def test_note_jev_result_updates_last(project, capsys):
    clear_last_jev()
    note_jev_result("skipped", "no_key", "TYPESAFE_API_KEY is empty.", gate="tests")
    outcome = last_jev_result()
    assert outcome["status"] == "skipped"
    assert outcome["reason"] == "no_key"
    assert outcome["gate"] == "tests"
    out = capsys.readouterr().out
    assert "Jev: skipped" in out
    assert "reason=no_key" in out
    assert "gate=tests" in out


def test_record_jev_result_on_spec(project):
    save_generation("mod", "beh", "x = 1\n", "t", "c")
    assert record_jev_result("mod", "ok", "ok", gate="tests") is True
    spec = get_spec("mod")
    assert spec["jev_status"] == "ok"
    assert spec["jev_reason"] == "ok"
    assert spec["jev_gate"] == "tests"
    assert spec["jev_at"]


def test_save_generation_clears_jev_result(project):
    save_generation("mod", "beh", "x = 1\n", "t", "c")
    record_jev_result("mod", "skipped", "no_key", gate="tests")
    assert get_spec("mod")["jev_status"] == "skipped"
    save_generation("mod", "beh", "x = 2\n", "t", "c")
    spec = get_spec("mod")
    assert spec["jev_status"] == "none"
    assert spec["jev_reason"] == ""
    assert spec["jev_gate"] == ""
    assert spec["jev_at"] is None


def test_show_and_specs_include_jev(project, capsys):
    save_generation("greet", "Return hello", "x = 1\n", "t", "c")
    record_jev_result("greet", "skipped", "no_key", "no key", gate="tests")
    show_spec("greet", project)
    out = capsys.readouterr().out
    assert "Jev: skipped" in out
    assert "reason=no_key" in out
    assert "gate=tests" in out
    list_specs(project)
    out = capsys.readouterr().out
    assert "jev=skipped" in out


def test_weak_call_tests_skipped_without_key(project, monkeypatch, capsys):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    clear_last_jev()
    weak = weak_call_tests("beh", "import impl\n", "import impl\n", ["lib"])
    assert weak is None
    outcome = last_jev_result()
    assert outcome["status"] == "skipped"
    assert outcome["reason"] == "no_key"
    assert outcome["gate"] == "tests"
    out = capsys.readouterr().out
    assert "Jev: skipped" in out


def test_spec_persists_jev_skipped(project, monkeypatch, capsys):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(
        "mechanicus.lifecycle.generate_tests",
        lambda behavior, modules=None, required=None, name=None: type(
            "R",
            (),
            {
                "tests": "import impl\n\ndef test_x():\n    assert impl.x() == 1\n",
                "check": "import impl\n\ndef test_check():\n    assert impl.x()\n",
                "explanation": "ok",
            },
        )(),
    )
    monkeypatch.setattr(
        "mechanicus.lifecycle.sniff_test_sources",
        lambda tests, check: None,
    )
    monkeypatch.setattr(
        "mechanicus.lifecycle.run_tests",
        lambda code, tests, check, modules=None: (False, "AssertionError"),
    )
    monkeypatch.setattr(
        "mechanicus.lifecycle.classify_before_code_with_reason",
        lambda passed, output, tests, check: ("red", "failed"),
    )
    monkeypatch.setattr(
        "mechanicus.lifecycle.generate_code",
        lambda behavior, tests, check, modules=None, required=None, name=None: type(
            "C",
            (),
            {"code": "def x():\n    return 1\n", "explanation": "code"},
        )(),
    )
    spec_command("app", "application", modules={"lib": "VALUE = 1\n"}, required=["lib"])
    spec = get_spec("app")
    assert spec is not None
    assert spec["jev_status"] == "skipped"
    assert spec["jev_reason"] == "no_key"
    assert spec["jev_gate"] == "tests"
    out = capsys.readouterr().out
    assert "Jev: skipped" in out


def test_spec_persists_jev_failed(project, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(
        "mechanicus.lifecycle.generate_tests",
        lambda behavior, modules=None, required=None, name=None: type(
            "R",
            (),
            {
                "tests": "import impl\n\ndef test_x():\n    assert impl.x() == 1\n",
                "check": "import impl\n\ndef test_check():\n    assert impl.x()\n",
                "explanation": "ok",
            },
        )(),
    )
    monkeypatch.setattr(
        "mechanicus.lifecycle.sniff_test_sources",
        lambda tests, check: None,
    )
    monkeypatch.setattr(
        "mechanicus.lifecycle.run_tests",
        lambda code, tests, check, modules=None: (False, "AssertionError"),
    )
    monkeypatch.setattr(
        "mechanicus.lifecycle.classify_before_code_with_reason",
        lambda passed, output, tests, check: ("red", "failed"),
    )
    def _boom(state, questions):
        raise RuntimeError("boom")

    monkeypatch.setattr("mechanicus.jev.ask_jev", _boom)
    monkeypatch.setattr(
        "mechanicus.lifecycle.generate_code",
        lambda behavior, tests, check, modules=None, required=None, name=None: type(
            "C",
            (),
            {"code": "def x():\n    return 1\n", "explanation": "code"},
        )(),
    )
    spec_command("app", "application", modules={"lib": "VALUE = 1\n"}, required=["lib"])
    spec = get_spec("app")
    assert spec["jev_status"] == "failed"
    assert spec["jev_reason"] == "error"
    assert "boom" in (spec["jev_detail"] or "")
    assert spec["jev_gate"] == "tests"
