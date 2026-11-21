"""Draft inventory and suggestions context for Swift packages."""
from mechanicus.draft import codebase_inventory
from mechanicus.paths import write_text_file
from mechanicus.project import TARGET_SWIFT_PACKAGE, save_project
from mechanicus.suggestions import _context_block
from mechanicus.swift_support import ensure_package_scaffold


def test_inventory_includes_swift_and_skips_build(project):
    save_project(project, TARGET_SWIFT_PACKAGE, package_name="DemoKit")
    ensure_package_scaffold(project)
    write_text_file(
        project / "DemoKit" / "Sources" / "DemoKit" / "Greeter.swift",
        "public struct Greeter {}\n",
    )
    junk = project / "DemoKit" / ".build" / "debug"
    junk.mkdir(parents=True)
    write_text_file(junk / "description.json", '{"fake": true}\n')
    write_text_file(project / "keep.py", "x = 1\n")

    inventory, count = codebase_inventory(project)
    assert count >= 2
    assert "Package.swift" in inventory
    assert "Greeter.swift" in inventory
    assert "keep.py" in inventory
    assert ".build" not in inventory
    assert "description.json" not in inventory


def test_suggestions_context_prefers_swift_sources(project):
    save_project(project, TARGET_SWIFT_PACKAGE, package_name="DemoKit")
    ensure_package_scaffold(project)
    write_text_file(
        project / "DemoKit" / "Sources" / "DemoKit" / "Greeter.swift",
        "public struct Greeter { public init() {} }\n",
    )
    junk = project / "DemoKit" / ".build" / "debug"
    junk.mkdir(parents=True)
    write_text_file(junk / "description.json", '{"fake": true}\n')
    write_text_file(project / "noise.py", "print('ignore me')\n")

    context = _context_block(project)
    assert "Package.swift" in context
    assert "Greeter.swift" in context
    assert "public struct Greeter" in context
    assert "Swift package:" in context
    assert "description.json" not in context
    # Inventory may list .py paths; Swift targets do not sample their bodies.
    assert "print('ignore me')" not in context


def test_suggestions_context_still_samples_python(project):
    write_text_file(project / "app.py", "def main():\n    return 1\n")
    write_text_file(project / "README.md", "A python app.\n")
    context = _context_block(project)
    assert "app.py" in context
    assert "def main" in context
    assert "README.md" in context
