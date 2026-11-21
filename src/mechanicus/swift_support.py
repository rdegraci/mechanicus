"""Swift package paths, scaffold, and in-place swift test harness."""
import os
import re
import shutil
import subprocess
from pathlib import Path

from mechanicus.config import code_root, log
from mechanicus.paths import write_text_file
from mechanicus.project import project_package_name


_RESERVED_TOP = frozenset(
    {"sources", "tests", ".build", "package.swift", "package.resolved"}
)


def swift_type_name(spec_name):
    """Map score_calculator or path/to_name to ScoreCalculator / PathToName."""
    text = str(spec_name).strip().replace("\\", "/")
    relative = Path(text)
    if relative.suffix.lower() in (".swift", ".module", ".py"):
        relative = relative.with_suffix("")
    parts = []
    for part in relative.parts:
        for piece in re.split(r"[_\-\s]+", part):
            if not piece:
                continue
            parts.append(piece[:1].upper() + piece[1:])
    if not parts:
        return None
    return "".join(parts)


def package_dir(root):
    return Path(code_root(root)).resolve()


def sources_dir(root):
    name = project_package_name(root)
    return package_dir(root) / "Sources" / name


def tests_dir(root):
    name = project_package_name(root)
    return package_dir(root) / "Tests" / f"{name}Tests"


def swift_source_path(root, name):
    type_name = swift_type_name(name)
    if type_name is None:
        return None
    destination = (sources_dir(root) / f"{type_name}.swift").resolve()
    base = package_dir(root)
    try:
        destination.relative_to(base)
    except ValueError:
        return None
    return destination


def swift_test_path(root, name):
    type_name = swift_type_name(name)
    if type_name is None:
        return None
    destination = (tests_dir(root) / f"{type_name}Tests.swift").resolve()
    base = package_dir(root)
    try:
        destination.relative_to(base)
    except ValueError:
        return None
    return destination


def swift_check_path(root, name):
    type_name = swift_type_name(name)
    if type_name is None:
        return None
    destination = (tests_dir(root) / f"{type_name}CheckTests.swift").resolve()
    base = package_dir(root)
    try:
        destination.relative_to(base)
    except ValueError:
        return None
    return destination


def swift_layout_conflict(root, name):
    """Return a message if name is reserved or collides with Sources/Tests."""
    relative = Path(str(name))
    if relative.suffix.lower() in (".swift", ".module", ".py"):
        relative = relative.with_suffix("")
    if not relative.parts:
        return None
    first = relative.parts[0]
    if first.lower() in _RESERVED_TOP or first.lower() == "package.swift":
        return f"'{name}' conflicts with Swift package layout ({first})"
    type_name = swift_type_name(name)
    if type_name is None:
        return f"'{name}' is not a valid Swift module name"
    source = sources_dir(root)
    # A top-level directory that is not Sources/Tests but blocks the type file is fine.
    # Block only when a non-file path would replace Sources/<pkg>/<Type>.swift oddly.
    if source.is_dir():
        conflict = source / type_name
        if conflict.is_dir():
            return f"'{name}' conflicts with directory {conflict.relative_to(package_dir(root)).as_posix()}/"
    return None


def missing_swift_dependency_types(code, required):
    """Return Swift type names from required that do not appear in code."""
    text = str(code or "")
    missing = []
    for spec_name in required or []:
        type_name = swift_type_name(spec_name)
        label = type_name or str(spec_name)
        if not type_name or not re.search(rf"\b{re.escape(type_name)}\b", text):
            missing.append(label)
    return missing


def empty_stub(root, name):
    type_name = swift_type_name(name) or "Generated"
    return (
        f"// Mechanicus stub: behavior not implemented yet.\n"
        f"public struct {type_name} {{\n"
        f"}}\n"
    )


def default_package_swift(package_name):
    return f"""// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "{package_name}",
    platforms: [
        .iOS(.v16),
        .macOS(.v13),
    ],
    products: [
        .library(name: "{package_name}", targets: ["{package_name}"]),
    ],
    targets: [
        .target(name: "{package_name}"),
        .testTarget(
            name: "{package_name}Tests",
            dependencies: ["{package_name}"]
        ),
    ]
)
"""


def ensure_package_scaffold(root):
    """Create Package.swift and source/test dirs once. Does not overwrite Package.swift."""
    base = package_dir(root)
    name = project_package_name(root)
    base.mkdir(parents=True, exist_ok=True)
    manifest = base / "Package.swift"
    created_manifest = False
    if not manifest.is_file():
        write_text_file(manifest, default_package_swift(name))
        created_manifest = True
        log.info("wrote %s", manifest)
    sources = sources_dir(root)
    tests = tests_dir(root)
    sources.mkdir(parents=True, exist_ok=True)
    tests.mkdir(parents=True, exist_ok=True)
    return {
        "package_dir": base,
        "package_name": name,
        "created_manifest": created_manifest,
        "manifest": manifest,
        "sources": sources,
        "tests": tests,
    }


def tests_for_disk_swift(tests, root):
    """Normalize XCTest sources and force a legal @testable import."""
    text = str(tests or "")
    package = project_package_name(root)
    if not package:
        if text and not text.endswith("\n"):
            text += "\n"
        return text
    kept = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if re.match(r"^import\s+XCTest\b", stripped):
            continue
        if re.match(r"^@testable\s+import\b", stripped):
            continue
        if re.match(rf"^import\s+{re.escape(package)}\b", stripped):
            continue
        # Drop illegal hyphenated module imports (e.g. scriptor-fidelis).
        if re.match(r"^import\s+[A-Za-z_][A-Za-z0-9_]*-[A-Za-z0-9_-]*\b", stripped):
            continue
        kept.append(line)
    body = "".join(kept).lstrip("\n")
    text = f"import XCTest\n@testable import {package}\n{body}"
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def check_for_disk_swift(check, root):
    return tests_for_disk_swift(check, root)


def swift_code_is_empty(code):
    """True when generated Swift has no meaningful implementation lines."""
    for line in str(code or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        return False
    return True


def is_swift_test_suite_module_name(name):
    """True for plan names like Foo/Tests that should not be built as logic."""
    relative = Path(str(name).strip().replace("\\", "/"))
    if relative.suffix.lower() in (".swift", ".module", ".py"):
        relative = relative.with_suffix("")
    if not relative.parts:
        return False
    leaf = relative.parts[-1].lower()
    return leaf in {"tests", "test"}


def write_swift_artifacts(root, name, code, tests, check=""):
    """Write source, tests, and optional check tests in place."""
    ensure_package_scaffold(root)
    source = swift_source_path(root, name)
    test_path = swift_test_path(root, name)
    check_path = swift_check_path(root, name)
    if source is None or test_path is None or check_path is None:
        return None
    write_text_file(source, code if str(code).strip() else empty_stub(root, name))
    write_text_file(test_path, tests_for_disk_swift(tests, root))
    if str(check or "").strip():
        write_text_file(check_path, check_for_disk_swift(check, root))
    elif check_path.is_file():
        check_path.unlink()
    return source, test_path, check_path


def read_swift_artifacts(root, name):
    source = swift_source_path(root, name)
    test_path = swift_test_path(root, name)
    check_path = swift_check_path(root, name)
    return {
        "code": source.read_text() if source is not None and source.is_file() else None,
        "tests": test_path.read_text() if test_path is not None and test_path.is_file() else None,
        "check": check_path.read_text() if check_path is not None and check_path.is_file() else None,
    }


def restore_swift_artifacts(root, name, snapshot):
    """Restore files from a snapshot dict, or remove generated files when empty."""
    source = swift_source_path(root, name)
    test_path = swift_test_path(root, name)
    check_path = swift_check_path(root, name)
    if source is None or test_path is None or check_path is None:
        return
    code = (snapshot or {}).get("code")
    tests = (snapshot or {}).get("tests")
    check = (snapshot or {}).get("check")
    if code is None:
        if source.is_file():
            source.unlink()
    else:
        write_text_file(source, code)
    if tests is None:
        if test_path.is_file():
            test_path.unlink()
    else:
        write_text_file(test_path, tests)
    if check is None:
        if check_path.is_file():
            check_path.unlink()
    else:
        write_text_file(check_path, check)


def count_swift_asserts(text):
    return len(re.findall(r"\bXCTAssert\w*\b", str(text or "")))


def sniff_swift_test_sources(tests, check, root, name, behavior=""):
    """Return a reason when Swift tests look too weak, else None."""
    package = project_package_name(root)
    type_name = swift_type_name(name) or ""
    for label, text in (("tests", tests), ("check", check)):
        body = str(text or "")
        if not body.strip():
            return f"empty_{label}"
        if "import XCTest" not in body and "XCTest" not in body:
            return f"no_xctest_{label}"
        if package and f"import {package}" not in body:
            return f"no_import_{label}"
        if not re.search(r"XCTAssert|XCTFail|XCTUnwrap", body):
            return f"no_assert_{label}"
        if type_name and type_name not in body:
            return f"no_type_{label}"
        # Pending-only: XCTFail without XCTAssert means tests don't specify behavior.
        if re.search(r"\bXCTFail\b", body) and not re.search(r"\bXCTAssert", body):
            return f"pending_only_{label}"
        # Must exercise the type API (member or initializer), not only name it in strings.
        if type_name:
            api_call = (
                re.search(rf"\b{re.escape(type_name)}\s*\.", body) is not None
                or re.search(rf"\b{re.escape(type_name)}\s*\(", body) is not None
            )
            if not api_call:
                return f"no_api_call_{label}"
    from mechanicus.harness import sniff_suite_thinness

    return sniff_suite_thinness(tests, check, behavior, swift=True)


def classify_swift_before_code(passed, output):
    """Classify swift test against an empty/stub impl. Returns (kind, reason)."""
    if passed:
        return "pass", "passed"
    text = str(output or "")
    if re.search(r"error:", text, re.I) or re.search(r"failed|XCTAssert|fatal error", text, re.I):
        return "red", "red"
    if "error:" in text.lower():
        return "red", "compile_error"
    return "broken", "broken"


def run_swift_tests(root, name=None):
    """Run `swift test` in the package directory. Optional filter by type name."""
    from mechanicus.activity import activity_spinner

    base = package_dir(root)
    if not (base / "Package.swift").is_file():
        return False, f"No Package.swift in {base}"
    command = ["swift", "test", "--package-path", str(base)]
    if name is not None:
        type_name = swift_type_name(name)
        if type_name:
            command.extend(["--filter", type_name])
    log.info("swift test %s", " ".join(command))
    env = os.environ.copy()
    with activity_spinner("Running tests"):
        result = subprocess.run(
            command,
            cwd=base,
            env=env,
            capture_output=True,
            text=True,
        )
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode == 0:
        return True, output
    return False, output


def swift_toolchain_available():
    return shutil.which("swift") is not None
