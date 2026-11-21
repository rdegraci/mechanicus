"""Safe, mode-based /clean for build junk, plan orphans, and disk resync."""
import shutil
from pathlib import Path

from mechanicus.config import code_root, log
from mechanicus.lifecycle import generate_command
from mechanicus.plan_build import find_module_files, module_output_path, module_records
from mechanicus.project import is_swift_package
from mechanicus.store import delete_spec, get_adopted_artifact, get_spec, list_spec_names
from mechanicus.swift_support import (
    empty_stub,
    ensure_package_scaffold,
    is_swift_test_suite_module_name,
    package_dir,
    write_swift_artifacts,
)


_CLEAN_HELP = """\
/clean modes (no args prints this help; nothing is deleted):

  /clean build
      Delete the Swift package .build directory only.

  /clean orphans
      Dry-run: list refused test-suite plan debris (Tests, */Tests.module)
      and matching unpinned DB specs.

  /clean orphans --apply
      Delete those .module files and matching unpinned DB specs.
      Pinned specs are skipped.

  /clean disk
      Rewrite adopted sources/tests from the DB, and stub unadopted
      Swift modules from .module files so in-place swift test stays clean.

  /clean all
      Run build, then orphans --apply, then disk.
"""


def print_clean_help():
    print(_CLEAN_HELP.strip())


def _build_dir(root):
    root = Path(root).resolve()
    if not is_swift_package(root):
        return None
    return package_dir(root) / ".build"


def clean_build(root):
    """Remove Swift package .build. Return True when something was removed."""
    root = Path(root).resolve()
    if not is_swift_package(root):
        print("Project target is not swift-package. Nothing to clean for build.")
        log.info("clean build skipped: not swift-package")
        return False
    build = _build_dir(root)
    if build is None or not build.exists():
        print("No .build directory.")
        log.info("clean build: no .build")
        return False
    shutil.rmtree(build)
    shown = build.relative_to(root).as_posix()
    print(f"Removed {shown}/")
    log.info("clean build removed %s", build)
    return True


def find_orphan_test_suites(root):
    """Return orphan test-suite plan entries: name, path (or None), pinned."""
    root = Path(root).resolve()
    found = []
    seen = set()
    base = code_root(root)
    for path in find_module_files(root):
        try:
            relative = path.relative_to(base)
        except ValueError:
            continue
        spec_name, destination = module_output_path(root, relative.as_posix())
        if spec_name is None or destination != path:
            continue
        if not is_swift_test_suite_module_name(spec_name):
            continue
        seen.add(spec_name)
        spec = get_spec(spec_name)
        found.append(
            {
                "name": spec_name,
                "path": path,
                "pinned": bool(spec is not None and spec["pinned"]),
                "in_db": spec is not None,
            }
        )
    for name in list_spec_names():
        if name in seen:
            continue
        if not is_swift_test_suite_module_name(name):
            continue
        spec = get_spec(name)
        found.append(
            {
                "name": name,
                "path": None,
                "pinned": bool(spec is not None and spec["pinned"]),
                "in_db": spec is not None,
            }
        )
    found.sort(key=lambda item: item["name"])
    return found


def clean_orphans(root, apply=False):
    """List or remove test-suite module debris. Dry-run unless apply."""
    root = Path(root).resolve()
    orphans = find_orphan_test_suites(root)
    if not orphans:
        print("No test-suite orphans.")
        log.info("clean orphans: none")
        return True
    if not apply:
        print("Test-suite orphans (dry-run; enter /clean orphans --apply to remove):")
        for item in orphans:
            bits = []
            if item["path"] is not None:
                try:
                    bits.append(item["path"].relative_to(root).as_posix())
                except ValueError:
                    bits.append(str(item["path"]))
            if item["in_db"]:
                bits.append("db" + (" pinned" if item["pinned"] else ""))
            print(f"  {item['name']}" + (f" ({', '.join(bits)})" if bits else ""))
        return True

    removed_files = 0
    removed_specs = 0
    skipped_pinned = 0
    for item in orphans:
        if item["path"] is not None and item["path"].is_file():
            item["path"].unlink()
            removed_files += 1
            try:
                shown = item["path"].relative_to(root).as_posix()
            except ValueError:
                shown = str(item["path"])
            print(f"Removed {shown}")
            log.info("clean orphans removed file %s", item["path"])
        if item["in_db"]:
            if item["pinned"]:
                skipped_pinned += 1
                print(f"Skipped pinned spec '{item['name']}'.")
                log.info("clean orphans skipped pinned %s", item["name"])
                continue
            if delete_spec(item["name"]):
                removed_specs += 1
                print(f"Removed spec '{item['name']}'.")
                log.info("clean orphans removed spec %s", item["name"])
    print(
        f"Orphans: removed {removed_files} file(s), "
        f"{removed_specs} spec(s)"
        + (f", skipped {skipped_pinned} pinned" if skipped_pinned else "")
        + "."
    )
    return True


def clean_disk(root):
    """Rewrite adopted artifacts; stub unadopted Swift modules from .module files."""
    root = Path(root).resolve()
    print("Rewriting adopted files from the database...")
    generate_command(root)
    if not is_swift_package(root):
        log.info("clean disk: python generate done")
        return True
    ensure_package_scaffold(root)
    stubbed = 0
    for record in module_records(root):
        name = record["name"]
        if is_swift_test_suite_module_name(name):
            continue
        spec = get_spec(name)
        if spec is not None and get_adopted_artifact(spec["id"], "code") is not None:
            continue
        written = write_swift_artifacts(
            root, name, empty_stub(root, name), None, None
        )
        if written is None:
            continue
        stubbed += 1
        source = written[0] if written else None
        if source is not None and source.is_file():
            try:
                print(f"Stubbed {source.relative_to(root).as_posix()}")
            except ValueError:
                print(f"Stubbed {source}")
    if stubbed:
        print(f"Stubbed {stubbed} unadopted Swift module(s).")
    else:
        print("No unadopted Swift modules to stub.")
    log.info("clean disk: stubbed %s", stubbed)
    return True


def clean_command(root, mode=None, apply=False):
    """Dispatch /clean. mode None prints help and mutates nothing."""
    root = Path(root).resolve()
    text = str(mode or "").strip().lower()
    if not text:
        print_clean_help()
        return True
    if text == "build":
        if apply:
            print("Unknown option for /clean build. Enter /clean for help.")
            return False
        return clean_build(root)
    if text == "orphans":
        return clean_orphans(root, apply=apply)
    if text == "disk":
        if apply:
            print("Unknown option for /clean disk. Enter /clean for help.")
            return False
        return clean_disk(root)
    if text == "all":
        if apply:
            print("Unknown option for /clean all. Enter /clean for help.")
            return False
        print("Cleaning build...")
        clean_build(root)
        print("Cleaning orphans...")
        clean_orphans(root, apply=True)
        print("Cleaning disk...")
        clean_disk(root)
        return True
    print(f"Unknown /clean mode '{mode}'. Enter /clean for help.")
    log.info("clean refused: unknown mode %s", mode)
    return False
