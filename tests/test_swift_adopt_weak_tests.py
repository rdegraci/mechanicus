"""Adopt regenerates weak Swift tests instead of only repairing code."""
from mechanicus.lifecycle import adopt_spec
from mechanicus.project import TARGET_SWIFT_PACKAGE, save_project
from mechanicus.store import get_adopted_artifact, get_spec, save_generation


def test_adopt_rewrites_pending_only_swift_tests(project, monkeypatch, capsys):
    save_project(project, TARGET_SWIFT_PACKAGE, package_name="MyAppLogic")
    pending_tests = """
import XCTest
@testable import MyAppLogic
final class GreeterTests: XCTestCase {
    func testPending() {
        XCTFail("Greeter has no specified public behavior or API yet.")
    }
}
"""
    pending_check = """
import XCTest
@testable import MyAppLogic
final class GreeterCheckTests: XCTestCase {
    func testPending() {
        XCTFail("Greeter has no specified public behavior or API yet.")
    }
}
"""
    good_tests = """
import XCTest
@testable import MyAppLogic
final class GreeterTests: XCTestCase {
    func testHello() {
        XCTAssertEqual(Greeter.hello(), "hello")
    }
}
"""
    good_check = """
import XCTest
@testable import MyAppLogic
final class GreeterCheckTests: XCTestCase {
    func testHello() {
        XCTAssertEqual(Greeter.hello(), "hello")
    }
}
"""
    stub_code = "public struct Greeter {}\n"
    good_code = "public struct Greeter { public static func hello() -> String { \"hello\" } }\n"
    # Non-empty stub so empty-code regenerate does not fire before the harness.
    save_generation("greeter", "return hello", stub_code, pending_tests, pending_check)

    calls = {"tests_until_red": 0, "generate_code": 0, "repair_code": 0, "swift": 0}

    def fake_sniff(tests, check, root, name):
        body = str(tests or "")
        if "XCTFail" in body and "XCTAssert" not in body:
            return "pending_only_tests"
        return None

    def fake_tests_until_red(behavior, modules=None, required=None, name=None):
        calls["tests_until_red"] += 1
        return good_tests, good_check

    def fake_generate_code(behavior, tests, check, modules=None, required=None, name=None):
        calls["generate_code"] += 1
        return type("R", (), {"code": good_code, "explanation": "ok"})()

    def fake_repair_code(*args, **kwargs):
        calls["repair_code"] += 1
        raise AssertionError("code-only repair should not run for weak tests")

    def fake_run_swift(root, name=None):
        calls["swift"] += 1
        # First run uses pending tests → fail. After rewrite, pass.
        if calls["tests_until_red"] == 0:
            return False, "XCTFail: Greeter has no specified public behavior"
        return True, "Test Suite passed"

    monkeypatch.setattr("mechanicus.lifecycle.is_swift_package", lambda root=None: True)
    monkeypatch.setattr("mechanicus.lifecycle.swift_toolchain_available", lambda: True)
    monkeypatch.setattr("mechanicus.lifecycle.sniff_swift_test_sources", fake_sniff)
    monkeypatch.setattr("mechanicus.lifecycle.tests_until_red", fake_tests_until_red)
    monkeypatch.setattr("mechanicus.lifecycle.generate_code", fake_generate_code)
    monkeypatch.setattr("mechanicus.lifecycle.repair_code", fake_repair_code)
    monkeypatch.setattr("mechanicus.lifecycle.run_swift_tests", fake_run_swift)
    monkeypatch.setattr(
        "mechanicus.lifecycle.write_swift_artifacts",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "mechanicus.lifecycle.ensure_package_scaffold",
        lambda root: None,
    )

    adopt_spec("greeter", modules=[], required=[])
    out = capsys.readouterr().out
    assert "Tests look weak" in out or "not usable for adopt" in out
    assert "rewritten tests" in out.lower() or "Rewriting tests" in out
    assert calls["tests_until_red"] == 1
    assert calls["generate_code"] == 1
    assert calls["repair_code"] == 0
    spec = get_spec("greeter")
    adopted = get_adopted_artifact(spec["id"], "code")
    assert adopted is not None
    assert "hello" in adopted["content"]
    assert spec["adopt_status"] == "ok"


def test_adopt_refuses_swift_code_that_omits_required_type(project, monkeypatch, capsys):
    save_project(project, TARGET_SWIFT_PACKAGE, package_name="DemoKit")
    tests = """
import XCTest
@testable import DemoKit
final class ExportServiceTests: XCTestCase {
    func testRun() { XCTAssertEqual(ExportService.run(), "ok") }
}
"""
    check = """
import XCTest
@testable import DemoKit
final class ExportServiceCheckTests: XCTestCase {
    func testRun() { XCTAssertEqual(ExportService.run(), "ok") }
}
"""
    save_generation(
        "SFExport/ExportService",
        "export rows",
        "public struct ExportService { public static func run() -> String { \"ok\" } }\n",
        tests,
        check,
    )
    monkeypatch.setattr("mechanicus.lifecycle.configured_repair_attempts", lambda: 0)
    monkeypatch.setattr("mechanicus.lifecycle.is_swift_package", lambda root=None: True)
    monkeypatch.setattr("mechanicus.lifecycle.swift_toolchain_available", lambda: True)
    monkeypatch.setattr(
        "mechanicus.lifecycle.run_swift_tests",
        lambda root, name=None: (True, "passed"),
    )
    monkeypatch.setattr(
        "mechanicus.lifecycle.write_swift_artifacts",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "mechanicus.lifecycle.ensure_package_scaffold",
        lambda root: None,
    )
    adopt_spec(
        "SFExport/ExportService",
        modules=[("SFCore/Domain", "public struct SFCoreDomain {}\n")],
        required=["SFCore/Domain"],
    )
    spec = get_spec("SFExport/ExportService")
    assert spec["adopt_status"] == "failed"
    assert spec["adopt_reason"] == "uncalled"
    assert get_adopted_artifact(spec["id"], "code") is None
    assert "SFCoreDomain" in capsys.readouterr().out
