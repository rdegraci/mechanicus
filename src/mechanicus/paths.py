"""Output paths and disk helpers for adopted code."""
from pathlib import Path

from mechanicus.config import code_root
from mechanicus.project import is_swift_package


def adopted_output_path(root, name):
    if is_swift_package(root):
        from mechanicus.swift_support import swift_source_path

        return swift_source_path(root, name)
    base = code_root(root)
    relative = Path(name)
    if relative.is_absolute() or any(part in ("", "..") for part in relative.parts):
        return None
    if relative.suffix != ".py":
        relative = Path(f"{relative}.py")
    destination = (base / relative).resolve()
    try:
        destination.relative_to(base)
    except ValueError:
        return None
    return destination


def layout_conflict(root, name):
    """Return a message if name would shadow a package or module file.

    Writing name.py conflicts when name/ already exists as a directory.
    Writing name/child.py conflicts when name.py already exists as a file.
    """
    if is_swift_package(root):
        from mechanicus.swift_support import swift_layout_conflict

        return swift_layout_conflict(root, name)
    base = code_root(root)
    relative = Path(str(name))
    if relative.suffix.lower() in (".py", ".module"):
        relative = relative.with_suffix("")
    if relative.is_absolute() or any(part in ("", "..") for part in relative.parts):
        return None
    try:
        package_dir = (base / relative).resolve()
        package_dir.relative_to(base)
    except ValueError:
        return None
    if package_dir.is_dir():
        try:
            shown = package_dir.relative_to(base).as_posix()
        except ValueError:
            shown = str(package_dir)
        return f"'{name}' conflicts with package directory {shown}/"
    for length in range(1, len(relative.parts)):
        parent = Path(*relative.parts[:length])
        parent_file = (base / parent).with_suffix(".py").resolve()
        try:
            parent_file.relative_to(base)
        except ValueError:
            continue
        if parent_file.is_file():
            try:
                shown = parent_file.relative_to(base).as_posix()
            except ValueError:
                shown = str(parent_file)
            return f"'{name}' conflicts with module file {shown}"
    return None


def module_import(name):
    relative = Path(name)
    if relative.suffix == ".py":
        relative = relative.with_suffix("")
    parts = [part for part in relative.parts if part not in (".",)]
    if not parts or any(not part.isidentifier() for part in parts):
        return None
    return ".".join(parts)


def test_output_path(root, name):
    if is_swift_package(root):
        from mechanicus.swift_support import swift_test_path

        return swift_test_path(root, name)
    code_path = adopted_output_path(root, name)
    if code_path is None:
        return None
    base = code_root(root)
    relative = code_path.relative_to(base)
    startup = Path(root).resolve()
    # Unique basenames so plain pytest works without --import-mode=importlib.
    # cli/main -> tests/cli/test_cli_main.py (not tests/cli/test_main.py).
    parts = relative.with_suffix("").parts
    filename = "test_" + "_".join(parts) + ".py"
    destination = (startup / "tests" / relative.parent / filename).resolve()
    try:
        destination.relative_to(startup)
    except ValueError:
        return None
    return destination


def write_text_file(destination, text):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if text and not text.endswith("\n"):
        text += "\n"
    destination.write_text(text)
