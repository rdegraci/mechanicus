"""Thin/duplicate suite sniff helpers."""
from mechanicus.harness import (
    count_python_asserts,
    sniff_suite_thinness,
    suite_sources_near_duplicate,
)
from mechanicus.project import TARGET_SWIFT_PACKAGE, save_project
from mechanicus.swift_support import count_swift_asserts, sniff_swift_test_sources


def test_near_duplicate_detects_copy():
    tests = "import impl\n\ndef test_a():\n    assert impl.x() == 1\n"
    check = "import impl\n\ndef test_a():\n    assert impl.x() == 1\n"
    assert suite_sources_near_duplicate(tests, check)
    assert sniff_suite_thinness(tests, check, "short") == "duplicate_check"


def test_thin_suite_for_long_behavior():
    tests = "import impl\n\ndef test_a():\n    assert impl.x() == 1\n"
    check = "import impl\n\ndef test_b():\n    assert impl.x() == 1\n"
    assert count_python_asserts(tests) == 1
    long_behavior = "x" * 120
    # Two asserts total — not thin.
    assert sniff_suite_thinness(tests, check, long_behavior) is None
    thin_check = "import impl\n\ndef test_b():\n    pass\n"
    assert sniff_suite_thinness(tests, thin_check, long_behavior) == "thin_suite"


def test_swift_sniff_rejects_duplicate_check(project):
    save_project(project, TARGET_SWIFT_PACKAGE, package_name="MyAppLogic")
    body = """
import XCTest
@testable import MyAppLogic
final class GreeterTests: XCTestCase {
    func testHello() {
        XCTAssertEqual(Greeter.hello(), "hello")
    }
}
"""
    assert count_swift_asserts(body) == 1
    assert sniff_swift_test_sources(body, body, project, "greeter") == "duplicate_check"
