"""Batch slash-command scripts."""
from mechanicus.bootstrap import main
from mechanicus.paths import write_text_file
from mechanicus.script import (
    execute_script_commands,
    iter_script_commands,
    load_script_commands,
    run_script,
)
from mechanicus.store import init_db


def test_iter_script_commands_skips_blanks_and_comments():
    text = """
# setup
/target python

/modules
# done
"""
    assert list(iter_script_commands(text)) == ["/target python", "/modules"]


def test_load_script_missing_file(tmp_path):
    commands, error = load_script_commands(tmp_path / "missing.mech")
    assert commands is None
    assert "not found" in error


def test_script_refuses_greenfield(project, capsys):
    init_db()
    code = execute_script_commands(
        project, ["/greenfield a note"], log_path="unused.log"
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "interactive" in out
    assert "Script stopped" in out


def test_script_runs_target_and_modules(project, capsys):
    init_db()
    write_text_file(project / "lib.module", "library\n")
    code = execute_script_commands(
        project,
        ["/target python", "/modules", "/quit"],
        log_path="unused.log",
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "> /target python" in out
    assert "> /modules" in out
    assert "lib" in out


def test_script_stops_on_unknown_command(project, capsys):
    init_db()
    code = execute_script_commands(
        project, ["/nope"], log_path="unused.log"
    )
    assert code == 1
    assert "Unknown command" in capsys.readouterr().out


def test_run_script_file(project, capsys):
    path = project / "build.mech"
    write_text_file(
        path,
        "# smoke\n/help\n/cost\n",
    )
    assert run_script(path) == 0
    out = capsys.readouterr().out
    assert "Running script:" in out
    assert "Commands:" in out
    assert "Session estimate" in out


def test_main_script_flag(project, monkeypatch, capsys):
    path = project / "ok.mech"
    write_text_file(path, "/modules\n")
    monkeypatch.setattr("mechanicus.bootstrap.run_script", lambda p: 0 if p == str(path) else 1)
    try:
        main(["--script", str(path)])
        assert False, "expected SystemExit"
    except SystemExit as exit_info:
        assert exit_info.code == 0


def test_main_short_script_flag(project, monkeypatch):
    seen = []

    def fake_run(path):
        seen.append(path)
        return 3

    monkeypatch.setattr("mechanicus.bootstrap.run_script", fake_run)
    try:
        main(["-s", "recipe.mech"])
        assert False, "expected SystemExit"
    except SystemExit as exit_info:
        assert exit_info.code == 3
    assert seen == ["recipe.mech"]
