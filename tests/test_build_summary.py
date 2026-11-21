"""Build summary and fake_deps hints."""
from mechanicus.build import _print_build_summary, _print_fake_deps_hint
from mechanicus.store import record_adopt_result, save_generation


def test_print_build_summary_lists_counts_and_blocked(project, capsys):
    save_generation("b", "Do a thing", "x = 1\n", "import impl\n", "import impl\n")
    record_adopt_result("b", "failed", "fake_deps", "probe", 1)
    _print_build_summary(
        {
            "a": ("built", "adopted and wrote disk"),
            "b": ("failed", "was not adopted"),
            "c": ("blocked", "uses b, which is not adopted"),
            "d": ("unchanged", "already adopted"),
        }
    )
    out = capsys.readouterr().out
    assert "--- build summary ---" in out
    assert "built=1" in out
    assert "failed=1" in out
    assert "blocked=1" in out
    assert "unchanged=1" in out
    assert "Failed fake_deps: b." in out
    assert "Blocked: c." in out


def test_print_fake_deps_hint(project, capsys):
    save_generation("widget", "Do a thing", "x = 1\n", "import impl\n", "import impl\n")
    record_adopt_result("widget", "failed", "fake_deps", "probe detail", 1)
    _print_fake_deps_hint("widget")
    out = capsys.readouterr().out
    assert "Hint for 'widget'" in out
    assert "/build widget" in out


def test_print_fake_deps_hint_skips_other_reasons(project, capsys):
    save_generation("widget", "Do a thing", "x = 1\n", "import impl\n", "import impl\n")
    record_adopt_result("widget", "failed", "harness", "tests failed", 1)
    _print_fake_deps_hint("widget")
    assert capsys.readouterr().out == ""
