"""Fixture tests for rewriting harness imports onto disk modules."""
import re

from mechanicus.harness import tests_for_disk as rewrite_tests_for_disk


def test_rewrite_top_level_import():
    text = rewrite_tests_for_disk(
        "import impl\n\ndef test_x():\n    assert impl.x() == 1\n",
        "greet",
    )
    assert "import greet as impl" in text
    assert "import impl\n" not in text.replace("import greet as impl", "")


def test_rewrite_indented_import():
    source = """
import impl

def install(monkeypatch):
    monkeypatch.delitem(__import__('sys').modules, 'impl')
    import impl
    return impl

def test_main(monkeypatch):
    module = install(monkeypatch)
    assert module.main
"""
    text = rewrite_tests_for_disk(source, "irc_client/main")
    assert "import irc_client.main as impl" in text
    assert re.search(r"(?m)^[ \t]+import impl\b", text) is None


def test_rewrite_indented_from_import():
    source = """
from impl import main

def helper():
    from impl import main as entry
    return entry
"""
    text = rewrite_tests_for_disk(source, "irc_client/main")
    assert "from irc_client.main import main" in text
    assert "from irc_client.main import main as entry" in text
    assert "from impl" not in text
