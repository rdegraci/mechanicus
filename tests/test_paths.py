"""Fixture tests for output paths and layout conflicts."""
from mechanicus.paths import (
    adopted_output_path,
    layout_conflict,
    module_import,
    write_text_file,
)
from mechanicus.paths import test_output_path as disk_test_path


def test_adopted_output_path_rejects_escape(project):
    assert adopted_output_path(project, "../escape") is None
    assert adopted_output_path(project, "/abs") is None
    path = adopted_output_path(project, "greet")
    assert path == (project / "greet.py").resolve()


def test_layout_conflict_package_directory(project):
    package = project / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    message = layout_conflict(project, "pkg")
    assert message is not None
    assert "pkg" in message
    assert layout_conflict(project, "fresh") is None


def test_layout_conflict_module_file_blocks_child(project):
    (project / "pkg.py").write_text("x = 1\n")
    message = layout_conflict(project, "pkg/child")
    assert message is not None
    assert "pkg.py" in message


def test_disk_test_path_nested_unique_basename(project):
    path = disk_test_path(project, "cli/main")
    assert path == (project / "tests" / "cli" / "test_cli_main.py").resolve()


def test_module_import():
    assert module_import("cli/main") == "cli.main"
    assert module_import("greet.py") == "greet"
    assert module_import("bad-name") is None


def test_write_text_file_adds_newline(project):
    destination = project / "out.txt"
    write_text_file(destination, "hello")
    assert destination.read_text() == "hello\n"
    write_text_file(destination, "again\n")
    assert destination.read_text() == "again\n"
