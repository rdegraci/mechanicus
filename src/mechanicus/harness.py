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


def classify_before_code(passed: bool, output: str) -> str:
    """Classify pytest against an empty impl.py.

    red: every failure is a missing behavior (assertion or missing impl name).
    pass: pytest passed, so the tests do not require the behavior.
    broken: the tests did not run, or a test passed.
    """
    if passed:
        return "pass"
    if re.search(r"(?m)^\d.*\bpassed\b", output):
        return "broken"
    if re.search(r"collected 0 items|no tests ran", output, re.I):
        return "broken"
    if re.search(r"SyntaxError:|IndentationError:|NameError:|ModuleNotFoundError:", output):
        return "broken"
    import_errors = re.findall(r"ImportError:.*", output)
    for message in import_errors:
        if not re.search(r"cannot import name '.+' from 'impl'", message):
            return "broken"
    red = (
        "AssertionError" in output
        or "AttributeError" in output
        or bool(import_errors)
        or re.search(r"(?m)^E\s+assert\b", output) is not None
        or re.search(r"(?m)^E\s+Failed:", output) is not None
    )
    if red:
        return "red"
    return "broken"


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


def run_tests(code: str, tests: str, check: str, modules=None) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        _write_dependency_modules(tmp, modules)
        with open(os.path.join(tmp, "impl.py"), "w") as handle:
            handle.write(code)
        with open(os.path.join(tmp, "test_impl.py"), "w") as handle:
            handle.write(prepare_tests(tests))
        with open(os.path.join(tmp, "test_check.py"), "w") as handle:
            handle.write(prepare_tests(check))
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

def tests_for_disk(tests, name):
    text = prepare_tests(tests)
    module = module_import(name)
    if module is not None:
        # Keep the name impl in the test body so a local variable can match the module name.
        text = re.sub(r"(?m)^import impl\b.*$", f"import {module} as impl", text)
        text = re.sub(r"(?m)^from impl\b", f"from {module}", text)
        if re.search(r"\bimpl\b", text) and "as impl" not in text:
            text = f"import {module} as impl\n{text}"
    if text and not text.endswith("\n"):
        text += "\n"
    return text
