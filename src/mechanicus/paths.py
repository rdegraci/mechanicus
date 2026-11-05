"""Output paths and disk helpers for adopted code."""
from pathlib import Path

from mechanicus.config import code_root


def adopted_output_path(root, name):
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


def module_import(name):
    relative = Path(name)
    if relative.suffix == ".py":
        relative = relative.with_suffix("")
    parts = [part for part in relative.parts if part not in (".",)]
    if not parts or any(not part.isidentifier() for part in parts):
        return None
    return ".".join(parts)


def test_output_path(root, name):
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
