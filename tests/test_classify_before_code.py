"""Fixture corpus for empty-impl red classification (ML5)."""
from mechanicus.harness import (
    classify_before_code,
    classify_before_code_with_reason,
    sniff_test_sources,
)


GOOD_TESTS = """
import impl

def test_greet():
    assert impl.greet() == "hello"
"""

GOOD_CHECK = """
import impl

def test_check():
    assert impl.greet() == "hello"
"""


def test_sniff_accepts_impl_behavior_asserts():
    assert sniff_test_sources(GOOD_TESTS, GOOD_CHECK) is None


def test_sniff_rejects_empty():
    assert sniff_test_sources("", GOOD_CHECK) == "empty"
    assert sniff_test_sources(GOOD_TESTS, "   ") == "empty"


def test_sniff_rejects_no_impl_import():
    tests = "def test_x():\n    assert 1 == 2\n"
    assert sniff_test_sources(tests, GOOD_CHECK) == "no_impl"


def test_sniff_rejects_trivial_assert_only():
    tests = "import impl\n\ndef test_x():\n    assert False\n"
    check = "import impl\n\ndef test_check():\n    assert False\n"
    assert sniff_test_sources(tests, check) == "trivial_assert"


def test_sniff_allows_assert_false_with_impl_behavior():
    tests = """
import impl

def test_greet():
    assert impl.greet() == "hello"

def test_should_raise():
    try:
        impl.greet("bad")
        assert False
    except TypeError:
        pass
"""
    assert sniff_test_sources(tests, GOOD_CHECK) is None


def test_sniff_rejects_assert_without_impl_use():
    tests = "import impl\n\ndef test_x():\n    assert 1 == 2\n"
    assert sniff_test_sources(tests, GOOD_CHECK) == "no_impl"


def test_sniff_accepts_from_impl_import():
    tests = "from impl import greet\n\ndef test_greet():\n    assert greet() == 'hello'\n"
    check = "from impl import greet\n\ndef test_check():\n    assert greet() == 'hello'\n"
    assert sniff_test_sources(tests, check) is None


def test_classify_pass():
    assert classify_before_code(True, "") == "pass"
    kind, reason = classify_before_code_with_reason(True, "", GOOD_TESTS, GOOD_CHECK)
    assert kind == "pass"
    assert reason == "passed"


def test_classify_red_attribute_error():
    output = "E   AttributeError: module 'impl' has no attribute 'greet'\n"
    kind, reason = classify_before_code_with_reason(False, output, GOOD_TESTS, GOOD_CHECK)
    assert kind == "red"
    assert reason == "red"


def test_classify_red_import_name_from_impl():
    output = "ImportError: cannot import name 'greet' from 'impl'\n"
    kind, reason = classify_before_code_with_reason(False, output, GOOD_TESTS, GOOD_CHECK)
    assert kind == "red"


def test_classify_red_assertion_mentions_impl():
    output = "E       assert impl.greet() == 'hello'\nE       AssertionError\n"
    kind, reason = classify_before_code_with_reason(False, output, GOOD_TESTS, GOOD_CHECK)
    assert kind == "red"


def test_classify_broken_mixed_pass():
    output = "1 failed, 1 passed in 0.01s\n"
    kind, reason = classify_before_code_with_reason(False, output, GOOD_TESTS, GOOD_CHECK)
    assert kind == "broken"
    assert reason == "mixed_pass"


def test_classify_broken_no_tests_collected():
    output = "collected 0 items\n"
    kind, reason = classify_before_code_with_reason(False, output, GOOD_TESTS, GOOD_CHECK)
    assert kind == "broken"
    assert reason == "no_tests"


def test_classify_broken_syntax():
    output = "SyntaxError: invalid syntax\n"
    kind, reason = classify_before_code_with_reason(False, output, GOOD_TESTS, GOOD_CHECK)
    assert kind == "broken"
    assert reason == "collect_error"


def test_classify_broken_trivial_assert_output():
    output = "E       assert False\nE       AssertionError\n"
    kind, reason = classify_before_code_with_reason(False, output, GOOD_TESTS, GOOD_CHECK)
    assert kind == "broken"
    assert reason == "trivial_assert"


def test_classify_broken_assertion_without_impl():
    output = "E       assert 1 == 2\nE       AssertionError\n"
    kind, reason = classify_before_code_with_reason(False, output, GOOD_TESTS, GOOD_CHECK)
    assert kind == "broken"
    assert reason == "no_impl"


def test_classify_broken_from_sniff_without_running_rules():
    weak = "import impl\n\ndef test_x():\n    assert False\n"
    weak_check = "import impl\n\ndef test_check():\n    assert False\n"
    kind, reason = classify_before_code_with_reason(
        False,
        "E   AttributeError: module 'impl' has no attribute 'x'\n",
        weak,
        weak_check,
    )
    assert kind == "broken"
    assert reason == "trivial_assert"


def test_classify_red_when_impl_tied_despite_trivial_line_in_output():
    output = (
        "E   AttributeError: module 'impl' has no attribute 'greet'\n"
        "E       assert False\n"
    )
    kind, reason = classify_before_code_with_reason(False, output, GOOD_TESTS, GOOD_CHECK)
    assert kind == "red"
    assert reason == "red"
