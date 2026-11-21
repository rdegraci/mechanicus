"""Pytest harness and call-graph checks."""
import ast
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from mechanicus.paths import module_import


def _used_names(tree):
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            name = node
            parts = []
            while isinstance(name, ast.Attribute):
                parts.append(name.attr)
                name = name.value
            if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Load):
                parts.append(name.id)
                parts.reverse()
                for index in range(1, len(parts) + 1):
                    used.add(".".join(parts[:index]))
    return used


def _mentions(module, used):
    return module in used or any(name.startswith(module + ".") for name in used)


def uncalled_modules(code, required):
    """Return import names in required that code does not call."""
    if not required:
        return []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return [module_import(name) or name for name in required]
    used = _used_names(tree)
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _mentions(alias.name, used) or (alias.asname and alias.asname in used):
                    called.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                if local not in used:
                    continue
                called.add(node.module)
                called.add(f"{node.module}.{alias.name}")
    missing = []
    for spec_name in required:
        imported = module_import(spec_name) or spec_name
        if imported not in called and not any(name.startswith(imported + ".") for name in called):
            missing.append(imported)
    return missing


def _failure_text(output: str) -> str:
    limit = 12000
    if len(output) <= limit:
        return output
    return output[-limit:]


_TRIVIAL_CONSTANTS = {True, False, 0, 1}


def _impl_bound_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "impl" or alias.name.startswith("impl."):
                    names.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and node.module == "impl":
            for alias in node.names:
                if alias.name == "*":
                    continue
                names.add(alias.asname or alias.name)
    return names


def _refers_to_impl(node, impl_names):
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in impl_names:
            return True
        if isinstance(child, ast.Attribute):
            current = child
            while isinstance(current, ast.Attribute):
                current = current.value
            if isinstance(current, ast.Name) and current.id in impl_names:
                return True
    return False


def _is_trivial_assert(node):
    if not isinstance(node, ast.Assert):
        return False
    test = node.test
    if isinstance(test, ast.Constant) and test.value in _TRIVIAL_CONSTANTS:
        return True
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        operand = test.operand
        if isinstance(operand, ast.Constant) and operand.value in _TRIVIAL_CONSTANTS:
            return True
    return False


def _is_trivial_fail_call(node):
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return False
    func = node.value.func
    if isinstance(func, ast.Attribute) and func.attr == "fail":
        return True
    if isinstance(func, ast.Name) and func.id == "fail":
        return True
    return False


def _sniff_one_source(text):
    source = str(text or "")
    if not source.strip():
        return "empty"
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "syntax"
    impl_names = _impl_bound_names(tree)
    if not impl_names:
        return "no_impl"
    uses_impl = False
    trivial = False
    for node in ast.walk(tree):
        if _is_trivial_assert(node) or _is_trivial_fail_call(node):
            trivial = True
        if isinstance(node, ast.Assert) and _refers_to_impl(node, impl_names):
            uses_impl = True
        if isinstance(node, ast.Call) and _refers_to_impl(node, impl_names):
            uses_impl = True
    # Allow assert False / pytest.fail alongside real impl checks (e.g. should-raise).
    # Reject only when the file has no impl-tied assert/call.
    if not uses_impl:
        if trivial:
            return "trivial_assert"
        return "no_impl"
    return None


def sniff_test_sources(tests, check):
    """Return a broken reason if tests/check are too weak, else None."""
    for text in (tests, check):
        reason = _sniff_one_source(text)
        if reason is not None:
            return reason
    return None


def _normalize_for_similarity(text):
    return re.sub(r"\s+", "", str(text or ""))


def suite_sources_near_duplicate(tests, check):
    """True when check is essentially a copy of the unit tests."""
    left = _normalize_for_similarity(tests)
    right = _normalize_for_similarity(check)
    if not left or not right:
        return False
    if left == right:
        return True
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if len(shorter) < 40:
        return False
    return shorter in longer and len(shorter) / len(longer) >= 0.85


def count_python_asserts(text):
    body = str(text or "")
    return len(re.findall(r"(?m)^\s*assert\b", body)) + len(
        re.findall(r"\b(?:assert\w+|pytest\.raises)\s*\(", body)
    )


def sniff_suite_thinness(tests, check, behavior="", *, swift=False):
    """Return a reason when the suite looks too thin or duplicated, else None.

    Used after basic sniff passes, especially for post-adopt strengthen.
    """
    if suite_sources_near_duplicate(tests, check):
        return "duplicate_check"
    if swift:
        from mechanicus.swift_support import count_swift_asserts

        asserts = count_swift_asserts(tests) + count_swift_asserts(check)
    else:
        asserts = count_python_asserts(tests) + count_python_asserts(check)
    behavior_text = str(behavior or "").strip()
    if asserts < 1:
        return "thin_suite"
    # Longer behaviors should not clear the gate with a single assert pair.
    if asserts < 2 and len(behavior_text) >= 120:
        return "thin_suite"
    return None


def _trivial_failure_output(output: str) -> bool:
    patterns = (
        r"(?m)^E\s+assert False\s*$",
        r"(?m)^E\s+assert True\s*$",
        r"(?m)^E\s+assert 0\s*$",
        r"(?m)^E\s+assert 1\s*$",
        r"(?m)^E\s+AssertionError: False\s*$",
        r"(?m)^E\s+AssertionError: True\s*$",
        r"(?m)^E\s+AssertionError: 0\s*$",
        r"(?m)^E\s+AssertionError: 1\s*$",
        r"(?m)^E\s+Failed:\s*$",
        r"Failed:\s*Failed without message",
    )
    return any(re.search(pattern, output) for pattern in patterns)


def _impl_tied_failure(output: str, import_errors) -> bool:
    if import_errors:
        return True
    if re.search(r"AttributeError:.*\bimpl\b", output):
        return True
    if re.search(r"ImportError:.*\bimpl\b", output):
        return True
    if "AssertionError" in output or re.search(r"(?m)^E\s+assert\b", output):
        return bool(re.search(r"\bimpl\b", output))
    if re.search(r"(?m)^E\s+Failed:", output) and re.search(r"\bimpl\b", output):
        return True
    return False


def classify_before_code_with_reason(passed: bool, output: str, tests=None, check=None):
    """Classify pytest against an empty impl.py.

    Returns (kind, reason) where kind is red, pass, or broken.
    red: failures show missing behavior on impl (not a trivial assert).
    pass: pytest passed, so the tests do not require the behavior.
    broken: weak tests, collection/syntax problems, or mixed results.
    """
    if tests is not None or check is not None:
        sniff = sniff_test_sources(tests or "", check or "")
        if sniff is not None:
            return "broken", sniff
    if passed:
        return "pass", "passed"
    if re.search(r"(?m)^\d.*\bpassed\b", output):
        return "broken", "mixed_pass"
    if re.search(r"collected 0 items|no tests ran", output, re.I):
        return "broken", "no_tests"
    if re.search(r"SyntaxError:|IndentationError:|NameError:|ModuleNotFoundError:", output):
        return "broken", "collect_error"
    import_errors = re.findall(r"ImportError:.*", output)
    for message in import_errors:
        if not re.search(r"cannot import name '.+' from 'impl'", message):
            return "broken", "import_error"
    impl_tied = _impl_tied_failure(output, import_errors)
    if _trivial_failure_output(output) and not impl_tied:
        return "broken", "trivial_assert"
    if not impl_tied:
        has_failure = (
            "AssertionError" in output
            or "AttributeError" in output
            or bool(import_errors)
            or re.search(r"(?m)^E\s+assert\b", output) is not None
            or re.search(r"(?m)^E\s+Failed:", output) is not None
        )
        if has_failure:
            return "broken", "no_impl"
        return "broken", "broken"
    return "red", "red"


def classify_before_code(passed: bool, output: str, tests=None, check=None) -> str:
    kind, _reason = classify_before_code_with_reason(passed, output, tests, check)
    return kind


def prepare_tests(tests: str) -> str:
    if re.search(r"(?m)^(?:import impl\b|from impl\b)", tests):
        return tests
    # from __future__ imports must stay ahead of every other statement.
    lines = tests.splitlines(keepends=True)
    index = 0
    while index < len(lines) and (not lines[index].strip() or lines[index].lstrip().startswith("#")):
        index += 1
    if index < len(lines):
        stripped = lines[index].lstrip()
        quote = '"""' if stripped.startswith('"""') else "'''" if stripped.startswith("'''") else ""
        if quote:
            if stripped.count(quote) >= 2:
                index += 1
            else:
                index += 1
                while index < len(lines) and quote not in lines[index]:
                    index += 1
                if index < len(lines):
                    index += 1
    while index < len(lines) and (not lines[index].strip() or lines[index].lstrip().startswith("#")):
        index += 1
    while index < len(lines) and lines[index].lstrip().startswith("from __future__ import "):
        index += 1
        while index < len(lines) and (not lines[index].strip() or lines[index].lstrip().startswith("#")):
            index += 1
    lines.insert(index, "import impl\n")
    return "".join(lines)


def _write_dependency_modules(tmp, modules):
    root = Path(tmp).resolve()
    for name, content in modules or []:
        relative = Path(str(name))
        if relative.suffix != ".py":
            relative = relative.with_suffix(".py")
        destination = (root / relative).resolve()
        try:
            destination.relative_to(root)
        except ValueError:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        parent = destination.parent
        while parent != root:
            init = parent / "__init__.py"
            if not init.exists():
                init.write_text("")
            parent = parent.parent
        text = str(content)
        if text and not text.endswith("\n"):
            text += "\n"
        destination.write_text(text)


_PROBE_STUB = '''\
"""Probe stub: import succeeds; any attribute use fails until replaced."""

def __getattr__(name):
    raise RuntimeError(
        f"mechanicus probe stub: real adopted dependency required ({name})"
    )
'''

_FAKE_DEPS_DETAIL = (
    "Tests passed without exercising real adopted dependencies. "
    "Do not monkeypatch or invent neighbor APIs; call the real "
    "adopted modules written into the harness."
)


def _probe_stub_modules(modules):
    """Replace adopted sources with call-failing stubs for a real-deps probe."""
    stubs = []
    for name, _content in modules or []:
        stubs.append((name, _PROBE_STUB))
    return stubs


def _required_import_names(required):
    names = set()
    for spec_name in required or []:
        imported = module_import(spec_name) or spec_name
        names.add(imported)
        names.add(imported.split(".", 1)[0])
    return names


def _alias_map(tree):
    """Map local names to imported module roots (e.g. lib, or alias → lib)."""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".", 1)[0]
                aliases[local] = alias.name.split(".", 1)[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", 1)[0]
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                aliases[local] = root
    return aliases


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            parts.reverse()
            return ".".join(parts)
    return None


def _targets_required(value, aliases, required_roots):
    if isinstance(value, ast.Name):
        root = aliases.get(value.id, value.id)
        return root in required_roots or value.id in required_roots
    if isinstance(value, ast.Attribute):
        current = value
        while isinstance(current, ast.Attribute):
            current = current.value
        if isinstance(current, ast.Name):
            root = aliases.get(current.id, current.id)
            return root in required_roots
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        text = value.value
        root = text.split(".", 1)[0]
        return root in required_roots or any(
            text == name or text.startswith(name + ".") for name in required_roots
        )
    return False


def _patches_required_deps(source, required_roots):
    """True when tests monkeypatch/mock a required adopted dependency."""
    if not required_roots or not str(source or "").strip():
        return False
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    aliases = _alias_map(tree)
    aliases.setdefault("impl", "impl")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func)
        if name is None:
            continue
        short = name.rsplit(".", 1)[-1]
        if short not in {"setattr", "patch", "patch.object"} and name not in {
            "setattr",
            "patch",
            "unittest.mock.patch",
            "mock.patch",
            "monkeypatch.setattr",
        }:
            if not (
                short == "setattr"
                or name.endswith(".setattr")
                or name.endswith(".patch")
                or name.endswith(".patch.object")
            ):
                continue
        if not node.args:
            continue
        target = node.args[0]
        # monkeypatch.setattr(lib, "compute", ...) or patch("lib.compute")
        if _targets_required(target, aliases, required_roots):
            return True
        # monkeypatch.setattr("lib.compute", ...)
        if (
            len(node.args) >= 1
            and isinstance(target, ast.Constant)
            and isinstance(target.value, str)
            and _targets_required(target, aliases, required_roots)
        ):
            return True
    return False


def sniff_fake_dependency_tests(tests, check, required):
    """Return a reason when tests replace required dependency APIs, else None."""
    roots = _required_import_names(required)
    if not roots:
        return None
    for source in (tests, check):
        if _patches_required_deps(source, roots):
            return "patched_deps"
        # Invented helpers on impl (setattr with raising=False, or plain assign).
        if _invents_impl_helpers(source):
            return "invented_impl"
    return None


def _invents_impl_helpers(source):
    if not str(source or "").strip():
        return False
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    aliases = _alias_map(tree)
    impl_names = {local for local, root in aliases.items() if root == "impl"}
    impl_names.add("impl")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name is None:
                continue
            if not (name == "setattr" or name.endswith(".setattr")):
                continue
            if not node.args:
                continue
            target = node.args[0]
            if isinstance(target, ast.Name) and target.id in impl_names:
                # raising=False invents attributes; default raising also replaces.
                return True
            if (
                isinstance(target, ast.Constant)
                and isinstance(target.value, str)
                and (
                    target.value == "impl"
                    or target.value.startswith("impl.")
                )
            ):
                return True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id in impl_names
                ):
                    return True
    return False


def _run_pytest_harness(code: str, tests: str, check: str, modules=None) -> tuple[bool, str]:
    from mechanicus.activity import activity_spinner

    with tempfile.TemporaryDirectory() as tmp:
        _write_dependency_modules(tmp, modules)
        with open(os.path.join(tmp, "impl.py"), "w") as handle:
            handle.write(code)
        with open(os.path.join(tmp, "test_impl.py"), "w") as handle:
            handle.write(prepare_tests(tests))
        with open(os.path.join(tmp, "test_check.py"), "w") as handle:
            handle.write(prepare_tests(check))
        with activity_spinner("Running tests"):
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "test_impl.py", "test_check.py", "-q"],
                cwd=tmp,
                capture_output=True,
                text=True,
            )
        print(result.stdout)
        if result.stderr:
            print(result.stderr)
        output = result.stdout or ""
        if result.stderr:
            output = f"{output}\n{result.stderr}" if output else result.stderr
        return result.returncode == 0, output


def run_tests(code: str, tests: str, check: str, modules=None) -> tuple[bool, str]:
    return _run_pytest_harness(code, tests, check, modules)


def real_deps_exercised(code: str, tests: str, check: str, modules, required) -> tuple[bool, str]:
    """Return whether tests need the real adopted dependency sources.

    Refuses when tests monkeypatch or invent dependency APIs, or when the suite
    still passes against stub dependencies that fail on use.
    """
    if not required:
        return True, ""
    fake = sniff_fake_dependency_tests(tests, check, required)
    if fake is not None:
        return False, _FAKE_DEPS_DETAIL
    stubs = _probe_stub_modules(modules)
    if not stubs:
        stubs = [(name, _PROBE_STUB) for name in required]
    passed, output = _run_pytest_harness(code, tests, check, stubs)
    if passed:
        return False, _FAKE_DEPS_DETAIL
    return True, output

def tests_for_disk(tests, name):
    text = prepare_tests(tests)
    module = module_import(name)
    if module is not None:
        # Keep the name impl in the test body so a local variable can match the module name.
        # Rewrite indented imports too (helpers often re-import impl after monkeypatching).
        text = re.sub(
            r"(?m)^([ \t]*)import impl\b.*$",
            rf"\1import {module} as impl",
            text,
        )
        text = re.sub(
            r"(?m)^([ \t]*)from impl\b",
            rf"\1from {module}",
            text,
        )
        if re.search(r"\bimpl\b", text) and "as impl" not in text:
            text = f"import {module} as impl\n{text}"
    if text and not text.endswith("\n"):
        text += "\n"
    return text
