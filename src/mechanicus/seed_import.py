"""Import current package source into SQLite as adopted (and pinned)."""
from mechanicus.bootstrap_guard import refuse_bootstrap
from mechanicus.config import code_root, log
from mechanicus.plan_build import format_module_text, module_output_path, parse_module_text
from mechanicus.paths import adopted_output_path
from mechanicus.store import mark_adopted, pin_spec, save_generation

_IMPORT_TESTS = """import impl


def test_module_attribute():
    assert impl is not None
"""

_IMPORT_CHECK = """import impl


def test_check_module_loads():
    assert impl is not None
"""


def import_source(root, name, pin=True):
    """Read package source for name, save as a new adopted generation."""
    log.info("import %s", name)
    if refuse_bootstrap(name, "imported"):
        return False
    destination = adopted_output_path(root, name)
    if destination is None or not destination.is_file():
        log.info("import refused: source missing")
        print(f"No source file for '{name}' under the code root.")
        return False
    code = destination.read_text()
    if not code.strip():
        log.info("import refused: empty source")
        print(f"'{name}' is empty.")
        return False
    _spec_name, module_path = module_output_path(root, name)
    if module_path is not None and module_path.is_file():
        uses, behavior, invalid = parse_module_text(module_path.read_text())
        if invalid:
            print(f"Ignored invalid uses in '{module_path.name}'.")
        recorded = format_module_text(uses, behavior).strip() or behavior.strip()
    else:
        recorded = f"Imported source for {name}."
        print(f"No .module file for '{name}'. Using a short behavior.")
    try:
        spec_id, generation = save_generation(
            name,
            recorded,
            code,
            _IMPORT_TESTS,
            _IMPORT_CHECK,
        )
    except Exception as error:
        log.exception("import failed for %s", name)
        print(f"Error: {error}")
        return False
    if not mark_adopted(spec_id, generation):
        log.info("import failed to adopt %s", name)
        print(f"Could not adopt generation {generation}.")
        return False
    if pin:
        pin_spec(name)
    relative = destination.relative_to(code_root(root)).as_posix()
    log.info("imported %s generation %s pinned=%s", name, generation, pin)
    print(f"Imported '{name}' from {relative} as generation {generation}.")
    if pin:
        print(f"Pinned '{name}'.")
    return True
