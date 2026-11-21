"""Fixture tests for /help command listing."""
from mechanicus.repl import (
    _COMMAND_HELP,
    _help_entries,
    _make_prompt_session,
    _slash_commands,
    print_help,
)


def test_command_help_includes_help_and_build():
    commands = {command for command, _blurb in _help_entries()}
    assert "/help" in commands
    assert "/cost" in commands
    assert "/script <path>" in commands
    assert "/target [python|swift-package] [Name]" in commands
    assert "/build" in commands
    assert "/build <name>" in commands
    groups = [group for group, _entries in _COMMAND_HELP]
    assert groups == [
        "Draft",
        "Plan and build",
        "Spec lifecycle",
        "Disk and run",
        "Session",
    ]
    for command, blurb in _help_entries():
        assert command.startswith("/")
        assert blurb.strip()


def test_slash_commands_are_first_tokens():
    commands = _slash_commands()
    assert "/help" in commands
    assert "/build" in commands
    assert "/build <name>" not in commands
    assert commands == sorted(commands)


def test_make_prompt_session_uses_history(tmp_path, monkeypatch):
    monkeypatch.setenv("MECHANICUS_APPDIR", str(tmp_path))
    session = _make_prompt_session()
    assert session.history is not None
    session.history.append_string("/help")
    assert (tmp_path / "repl_history").is_file()
    assert any("/help" in line for line in session.history.get_strings())


def test_print_help_lists_groups_and_blurbs(capsys):
    print_help()
    out = capsys.readouterr().out
    assert "Commands:" in out
    assert "Draft" in out
    assert "Plan and build" in out
    assert "Spec lifecycle" in out
    assert "Disk and run" in out
    assert "Session" in out
    assert "/help" in out
    assert "List commands" in out
    assert "prompt_toolkit" in out
    assert "Tab completes" in out
