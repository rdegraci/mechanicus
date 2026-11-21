"""Codegen prompts require readable newlines, not minified sources."""
from mechanicus import codegen


def test_code_instructions_require_readable_formatting():
    for text in (
        codegen._CODE_INSTRUCTIONS,
        codegen._SWIFT_CODE_INSTRUCTIONS,
        codegen._SHARED_INSTRUCTIONS,
        codegen._SWIFT_SHARED_INSTRUCTIONS,
    ):
        assert "Do not minify" in text
        assert "one statement per line" in text
        assert "fewest lines" not in text


def test_repair_request_keeps_newlines_guidance():
    request = codegen._repair_request(
        "beh",
        "code",
        "tests",
        "check",
        "failed",
        modules=[],
        required=[],
        tests_passed=False,
        swift=False,
    )
    assert "do not minify" in request
    assert "as short as possible" not in request
