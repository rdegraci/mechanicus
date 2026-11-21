"""Mode-based /clean: build junk, orphans, disk resync."""
from mechanicus.clean import (
    clean_build,
    clean_command,
    clean_disk,
    clean_orphans,
    find_orphan_test_suites,
)
from mechanicus.paths import write_text_file
from mechanicus.plan_build import format_module_text
from mechanicus.project import TARGET_SWIFT_PACKAGE, save_project
from mechanicus.store import (
    delete_spec,
    get_spec,
    list_spec_names,
    mark_adopted,
    pin_spec,
    save_generation,
)
from mechanicus.swift_support import ensure_package_scaffold, swift_source_path


def test_clean_help_mutates_nothing(project, capsys):
    assert clean_command(project) is True
    out = capsys.readouterr().out
    assert "/clean build" in out
    assert "orphans" in out


def test_clean_build_removes_swift_build_dir(project, capsys):
    save_project(project, TARGET_SWIFT_PACKAGE, package_name="DemoKit")
    ensure_package_scaffold(project)
    build = project / "DemoKit" / ".build"
    build.mkdir(parents=True)
    (build / "marker").write_text("x")
    assert clean_build(project) is True
    assert not build.exists()
    assert "Removed" in capsys.readouterr().out


def test_clean_build_noop_on_python(project, capsys):
    assert clean_build(project) is False
    assert "not swift-package" in capsys.readouterr().out


def test_orphans_dry_run_and_apply(project, capsys):
    save_project(project, TARGET_SWIFT_PACKAGE, package_name="DemoKit")
    ensure_package_scaffold(project)
    package = project / "DemoKit"
    orphan = package / "SFCore" / "Tests.module"
    orphan.parent.mkdir(parents=True)
    write_text_file(orphan, format_module_text([], "XCTest everything"))
    save_generation("SFCore/Tests", "XCTest everything", "x", "t", "c")
    save_generation("Tests", "suite", "x", "t", "c")

    orphans = find_orphan_test_suites(project)
    names = {item["name"] for item in orphans}
    assert "SFCore/Tests" in names
    assert "Tests" in names

    clean_orphans(project, apply=False)
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert orphan.is_file()
    assert "SFCore/Tests" in list_spec_names()

    clean_orphans(project, apply=True)
    assert not orphan.is_file()
    assert "SFCore/Tests" not in list_spec_names()
    assert "Tests" not in list_spec_names()


def test_orphans_skip_pinned_spec(project, capsys):
    save_project(project, TARGET_SWIFT_PACKAGE, package_name="DemoKit")
    ensure_package_scaffold(project)
    save_generation("SFCore/Tests", "suite", "x", "t", "c")
    pin_spec("SFCore/Tests")
    clean_orphans(project, apply=True)
    assert get_spec("SFCore/Tests") is not None
    assert "pinned" in capsys.readouterr().out.lower()


def test_delete_spec_refuses_pinned(project):
    save_generation("keep", "beh", "x", "t", "c")
    pin_spec("keep")
    assert delete_spec("keep") is False
    assert get_spec("keep") is not None


def test_clean_disk_stubs_unadopted_swift(project, monkeypatch, capsys):
    save_project(project, TARGET_SWIFT_PACKAGE, package_name="DemoKit")
    ensure_package_scaffold(project)
    package = project / "DemoKit"
    write_text_file(
        package / "Greeter.module",
        format_module_text([], "Return hello"),
    )
    # Adopted module rewritten via generate.
    spec_id, generation = save_generation(
        "Greeter",
        "Return hello",
        "public struct Greeter { public static func hello() -> String { \"hello\" } }\n",
        "import XCTest\n@testable import DemoKit\n",
        "import XCTest\n@testable import DemoKit\n",
    )
    mark_adopted(spec_id, generation)
    # Unadopted neighbor.
    write_text_file(
        package / "Other.module",
        format_module_text([], "Other behavior"),
    )
    monkeypatch.setattr(
        "mechanicus.clean.generate_command",
        lambda root, name=None: None,
    )
    clean_disk(project)
    stub = swift_source_path(project, "Other")
    assert stub is not None and stub.is_file()
    assert "stub" in stub.read_text().lower()
    out = capsys.readouterr().out
    assert "Stubbed" in out


def test_clean_all_runs_modes(project, monkeypatch, capsys):
    calls = []

    monkeypatch.setattr(
        "mechanicus.clean.clean_build",
        lambda root: calls.append("build") or True,
    )
    monkeypatch.setattr(
        "mechanicus.clean.clean_orphans",
        lambda root, apply=False: calls.append(("orphans", apply)) or True,
    )
    monkeypatch.setattr(
        "mechanicus.clean.clean_disk",
        lambda root: calls.append("disk") or True,
    )
    assert clean_command(project, "all") is True
    assert calls == ["build", ("orphans", True), "disk"]
