"""Swift package target, paths, scaffold, and harness helpers."""
import pytest

from mechanicus.lifecycle import target_command
from mechanicus.paths import adopted_output_path, layout_conflict
from mechanicus.paths import test_output_path as disk_test_path
from mechanicus.plan_build import build_one
from mechanicus.project import (
    TARGET_PYTHON,
    TARGET_SWIFT_PACKAGE,
    is_swift_package,
    is_valid_swift_identifier,
    load_project,
    normalize_swift_package_name,
    project_package_name,
    save_project,
)
from mechanicus import swift_support
from mechanicus.swift_support import (
    classify_swift_before_code,
    empty_stub,
    ensure_package_scaffold,
    is_swift_test_suite_module_name,
    missing_swift_dependency_types,
    run_swift_tests,
    sniff_swift_test_sources,
    swift_check_path,
    swift_code_is_empty,
    swift_toolchain_available,
    swift_type_name,
    write_swift_artifacts,
)


def test_default_project_is_python(project):
    settings = load_project(project)
    assert settings["target"] == TARGET_PYTHON
    assert not is_swift_package(project)
    assert adopted_output_path(project, "greet") == (project / "greet.py").resolve()


def test_save_and_load_swift_package_target(project):
    settings = save_project(project, TARGET_SWIFT_PACKAGE, package_name="AppLogic")
    assert settings["target"] == TARGET_SWIFT_PACKAGE
    assert settings["package_name"] == "AppLogic"
    assert settings["package_root"] == "AppLogic"
    loaded = load_project(project)
    assert loaded == settings
    assert is_swift_package(project)
    assert project_package_name(project) == "AppLogic"


def test_swift_type_name_mapping():
    assert swift_type_name("score_calculator") == "ScoreCalculator"
    assert swift_type_name("greeter") == "Greeter"
    assert swift_type_name("path/to_name") == "PathToName"


def test_target_command_scaffolds_package(project):
    settings = target_command(project, "swift-package", "DemoKit")
    assert settings["package_name"] == "DemoKit"
    package = project / "DemoKit"
    assert (package / "Package.swift").is_file()
    assert (package / "Sources" / "DemoKit").is_dir()
    assert (package / "Tests" / "DemoKitTests").is_dir()
    text = (package / "Package.swift").read_text()
    assert 'name: "DemoKit"' in text
    # Second call leaves manifest in place.
    (package / "Package.swift").write_text(text + "// kept\n")
    target_command(project, "swift-package", "DemoKit")
    assert "// kept" in (package / "Package.swift").read_text()


def test_swift_output_paths(project):
    save_project(project, TARGET_SWIFT_PACKAGE, package_name="MyAppLogic")
    ensure_package_scaffold(project)
    assert adopted_output_path(project, "score_calculator") == (
        project / "MyAppLogic" / "Sources" / "MyAppLogic" / "ScoreCalculator.swift"
    ).resolve()
    assert disk_test_path(project, "score_calculator") == (
        project
        / "MyAppLogic"
        / "Tests"
        / "MyAppLogicTests"
        / "ScoreCalculatorTests.swift"
    ).resolve()
    assert swift_check_path(project, "score_calculator") == (
        project
        / "MyAppLogic"
        / "Tests"
        / "MyAppLogicTests"
        / "ScoreCalculatorCheckTests.swift"
    ).resolve()


def test_swift_layout_conflict_reserved(project):
    save_project(project, TARGET_SWIFT_PACKAGE, package_name="MyAppLogic")
    message = layout_conflict(project, "Sources")
    assert message is not None
    assert "Sources" in message
    assert layout_conflict(project, "score_calculator") is None


def test_sniff_and_classify_swift(project):
    save_project(project, TARGET_SWIFT_PACKAGE, package_name="MyAppLogic")
    weak = "import XCTest\n@testable import MyAppLogic\n"
    assert sniff_swift_test_sources(weak, weak, project, "greeter") is not None
    tests = """
import XCTest
@testable import MyAppLogic
final class GreeterTests: XCTestCase {
    func testHello() {
        XCTAssertEqual(Greeter.hello(), "hello")
    }
}
"""
    check = """
import XCTest
@testable import MyAppLogic
final class GreeterCheckTests: XCTestCase {
    func testBehavior() {
        XCTAssertEqual(Greeter.hello(), "hello")
    }
}
"""
    assert sniff_swift_test_sources(tests, check, project, "greeter") is None
    assert classify_swift_before_code(True, "ok") == ("pass", "passed")
    kind, reason = classify_swift_before_code(False, "error: missing hello\nXCTAssertEqual failed")
    assert kind == "red"


@pytest.mark.skipif(not swift_toolchain_available(), reason="swift toolchain not installed")
def test_swift_test_red_then_green(project):
    save_project(project, TARGET_SWIFT_PACKAGE, package_name="MyAppLogic")
    ensure_package_scaffold(project)
    tests = """
import XCTest
@testable import MyAppLogic
final class GreeterTests: XCTestCase {
    func testHello() {
        XCTAssertEqual(Greeter.hello(), "hello")
    }
}
"""
    check = """
import XCTest
@testable import MyAppLogic
final class GreeterCheckTests: XCTestCase {
    func testBehavior() {
        XCTAssertEqual(Greeter.hello(), "hello")
    }
}
"""
    write_swift_artifacts(project, "greeter", empty_stub(project, "greeter"), tests, check)
    passed, output = run_swift_tests(project, "greeter")
    assert not passed
    kind, _reason = classify_swift_before_code(passed, output)
    assert kind == "red"

    code = """
public struct Greeter {
    public static func hello() -> String { "hello" }
}
"""
    write_swift_artifacts(project, "greeter", code, tests, check)
    passed, output = run_swift_tests(project, "greeter")
    assert passed, output


def test_python_paths_unchanged_without_project_yaml(project):
    assert load_project(project)["target"] == TARGET_PYTHON
    path = adopted_output_path(project, "cli/main")
    assert path == (project / "cli" / "main.py").resolve()
    assert disk_test_path(project, "cli/main") == (
        project / "tests" / "cli" / "test_cli_main.py"
    ).resolve()


def test_normalize_hyphenated_package_name(project):
    assert normalize_swift_package_name("scriptor-fidelis") == "scriptor_fidelis"
    assert is_valid_swift_identifier("scriptor_fidelis")
    assert not is_valid_swift_identifier("scriptor-fidelis")
    settings = save_project(project, TARGET_SWIFT_PACKAGE, package_name="scriptor-fidelis")
    assert settings["package_name"] == "scriptor_fidelis"
    assert settings.get("normalized_from") == "scriptor-fidelis"
    assert project_package_name(project) == "scriptor_fidelis"


def test_tests_for_disk_rewrites_illegal_import(project):
    save_project(project, TARGET_SWIFT_PACKAGE, package_name="scriptor_fidelis")
    raw = (
        "import XCTest\n"
        "@testable import scriptor-fidelis\n"
        "final class GreeterTests: XCTestCase {}\n"
    )
    fixed = swift_support.tests_for_disk_swift(raw, project)
    assert "@testable import scriptor_fidelis" in fixed
    assert "scriptor-fidelis" not in fixed


def test_sniff_rejects_pending_only_and_no_api_call(project):
    save_project(project, TARGET_SWIFT_PACKAGE, package_name="MyAppLogic")
    pending = """
import XCTest
@testable import MyAppLogic
final class MappingTests: XCTestCase {
    func testPending() {
        XCTFail("SFPersistenceMapping has no specified public behavior or API yet.")
    }
}
"""
    assert sniff_swift_test_sources(pending, pending, project, "SFPersistence/Mapping") == (
        "pending_only_tests"
    )
    no_api = """
import XCTest
@testable import MyAppLogic
final class MappingTests: XCTestCase {
    func testMention() {
        XCTAssertTrue(true, "SFPersistenceMapping exists in docs only")
    }
}
"""
    assert sniff_swift_test_sources(no_api, no_api, project, "SFPersistence/Mapping") == (
        "no_api_call_tests"
    )


def test_missing_swift_dependency_types():
    code = "public struct ExportService { let domain: SFCoreDomain }\n"
    assert missing_swift_dependency_types(code, ["SFCore/Domain"]) == []
    assert missing_swift_dependency_types(code, ["SFExport/CSVFormatting"]) == [
        "SFExportCSVFormatting"
    ]
    assert missing_swift_dependency_types(
        "let domain = 1\n", ["SFCore/Domain"]
    ) == ["SFCoreDomain"]


def test_swift_code_is_empty_and_test_suite_names():
    assert swift_code_is_empty("")
    assert swift_code_is_empty("// only comment\n")
    assert not swift_code_is_empty("public struct Foo {}\n")
    assert is_swift_test_suite_module_name("SFCore/Tests")
    assert is_swift_test_suite_module_name("Tests")
    assert not is_swift_test_suite_module_name("SFCore/Domain")


def test_build_skips_swift_test_suite_module(project):
    save_project(project, TARGET_SWIFT_PACKAGE, package_name="MyAppLogic")
    ensure_package_scaffold(project)
    record = {
        "name": "SFCore/Tests",
        "behavior": "Test everything with Swift Testing.",
        "text": "Test everything with Swift Testing.",
        "uses": [],
        "path": project / "MyAppLogic" / "SFCore" / "Tests.module",
    }
    status, reason = build_one(project, record)
    assert status == "skipped"
    assert "test-suite" in reason
