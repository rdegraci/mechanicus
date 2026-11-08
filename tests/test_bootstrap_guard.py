"""Fixture tests for bootstrap name refusal."""
from mechanicus.bootstrap_guard import (
    BOOTSTRAP_NAMES,
    is_bootstrap_name,
    refuse_bootstrap,
)


def test_bootstrap_names_include_core():
    for name in ("bootstrap", "config", "store", "paths", "__main__", "__init__"):
        assert name in BOOTSTRAP_NAMES
        assert is_bootstrap_name(name) is True


def test_leaf_names_are_not_bootstrap():
    for name in ("jev", "harness", "codegen", "lifecycle", "plan_build", "draft"):
        assert name not in BOOTSTRAP_NAMES
        assert is_bootstrap_name(name) is False


def test_is_bootstrap_name_strips_py_and_slashes():
    assert is_bootstrap_name("config.py") is True
    assert is_bootstrap_name("  store  ") is True
    assert is_bootstrap_name("not_a_bootstrap") is False


def test_refuse_bootstrap_prints_and_returns(capsys):
    assert refuse_bootstrap("config", "built") is True
    captured = capsys.readouterr()
    assert "bootstrap module" in captured.out
    assert "built" in captured.out
    assert refuse_bootstrap("greet", "built") is False
    assert capsys.readouterr().out == ""
