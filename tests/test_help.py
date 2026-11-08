"""Fixture tests for /help command listing."""
from mechanicus.repl import _COMMAND_HELP, _help_entries, print_help


def test_command_help_includes_help_and_build():
    commands = {command for command, _blurb in _help_entries()}
    assert "/help" in commands
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
