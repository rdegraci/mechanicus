import ast
import logging
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import types
from datetime import datetime
from importlib.resources import files
from pathlib import Path

import yaml
from openai import OpenAI
from pydantic import BaseModel, Field

_LOADED_MODULES = {}
_STARTUP_DIR = None
_LOG_PATH = None
log = logging.getLogger("mechanicus")


class TestsResult(BaseModel):
    tests: str
    check: str
    explanation: str


class CodeResult(BaseModel):
    code: str
    explanation: str


class PlannedModule(BaseModel):
    name: str
    behavior: str
    uses: list[str] = Field(default_factory=list)


class ApplicationPlan(BaseModel):
    modules: list[PlannedModule]
    explanation: str


class CycleRepair(BaseModel):
    name: str
    behavior: str
    uses: list[str] = Field(default_factory=list)
    modules: list[PlannedModule]
    explanation: str


def app_dir():
    override = os.environ.get("MECHANICUS_APPDIR")
    if override:
        return Path(override)
    return Path.home() / "Library" / "Application Support" / "mechanicus"


def _example_text(name):
    return (files("mechanicus") / name).read_text()


def _load_env(path):
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def ensure_app_files():
    directory = app_dir()
    directory.mkdir(parents=True, exist_ok=True)
    copies = (
        ("dot_env.example", directory / ".env"),
        ("config.yaml.example", directory / "config.yaml"),
    )
    for source, destination in copies:
        if not destination.exists():
            destination.write_text(_example_text(source))
    _load_env(directory / ".env")
    return directory


def load_config():
    ensure_app_files()
    loaded = yaml.safe_load((app_dir() / "config.yaml").read_text()) or {}
    if not isinstance(loaded, dict):
        raise ValueError("config.yaml must be a mapping")
    return loaded


def configured_model():
    model = load_config().get("model")
    if not model:
        return "gpt-4o"
    return str(model)


def configured_reasoning_effort():
    effort = load_config().get("reasoning_effort")
    if not effort:
        return "medium"
    return str(effort)


def configured_repair_attempts():
    raw = load_config().get("repair_attempts")
    if raw is None or raw == "":
        return 3
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return 3
    if count < 0:
        return 0
    return count


def session_log_path():
    return _LOG_PATH


def start_session_log():
    global _LOG_PATH
    directory = app_dir() / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    path = directory / f"{stamp}-{os.getpid()}.log"
    for handler in list(log.handlers):
        handler.close()
        log.removeHandler(handler)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    log.propagate = False
    _LOG_PATH = path
    log.info("session start")
    log.info("startup directory %s", startup_dir())
    log.info("database %s", database_path())
    log.info("model %s", configured_model())
    log.info("reasoning effort %s", configured_reasoning_effort())
    return path


def close_session_log():
    log.info("session end")
    for handler in list(log.handlers):
        handler.flush()
        handler.close()
        log.removeHandler(handler)


def startup_dir():
    global _STARTUP_DIR
    if _STARTUP_DIR is None:
        _STARTUP_DIR = Path.cwd().resolve()
    return _STARTUP_DIR


def database_path():
    ensure_app_files()
    override = os.environ.get("MECHANICUS_DB")
    if override:
        return Path(override)
    directory = startup_dir() / ".mechanicus"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "seed.db"


def connect():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn, table, name, definition):
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if name not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _backfill_generations(conn):
    specs = conn.execute("SELECT id, behavior FROM specs").fetchall()
    for spec in specs:
        rows = conn.execute(
            """SELECT id, kind, generation, behavior FROM artifacts
               WHERE spec_id = ? ORDER BY id""",
            (spec["id"],),
        ).fetchall()
        generation = 0
        for artifact in rows:
            if artifact["generation"] is not None:
                generation = artifact["generation"]
                if artifact["behavior"] is None:
                    conn.execute(
                        "UPDATE artifacts SET behavior = ? WHERE id = ?",
                        (spec["behavior"], artifact["id"]),
                    )
                continue
            if artifact["kind"] == "code" or generation == 0:
                generation += 1
            conn.execute(
                """UPDATE artifacts
                   SET generation = ?, behavior = COALESCE(behavior, ?)
                   WHERE id = ?""",
                (generation, spec["behavior"], artifact["id"]),
            )


def init_db():
    conn = connect()
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS specs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE,
                    behavior TEXT,
                    pinned INTEGER DEFAULT 0,
                    created_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    spec_id INTEGER,
                    kind TEXT,
                    content TEXT,
                    lines INTEGER,
                    pinned INTEGER DEFAULT 0,
                    adopted INTEGER DEFAULT 0,
                    generation INTEGER,
                    behavior TEXT,
                    created_at TEXT,
                    FOREIGN KEY (spec_id) REFERENCES specs(id)
                )
            """)
            _ensure_column(conn, "artifacts", "adopted", "INTEGER DEFAULT 0")
            _ensure_column(conn, "artifacts", "generation", "INTEGER")
            _ensure_column(conn, "artifacts", "behavior", "TEXT")
            _backfill_generations(conn)
    finally:
        conn.close()


def save_generation(name, behavior, code, tests, check):
    artifacts = (("code", code), ("tests", tests), ("check", check))
    conn = connect()
    try:
        with conn:
            now = datetime.utcnow().isoformat()
            existing = conn.execute("SELECT id FROM specs WHERE name = ?", (name,)).fetchone()
            if existing:
                spec_id = existing["id"]
                conn.execute(
                    "UPDATE specs SET behavior = ? WHERE id = ?",
                    (behavior, spec_id),
                )
            else:
                cursor = conn.execute(
                    """INSERT INTO specs (name, behavior, pinned, created_at)
                       VALUES (?, ?, 0, ?)""",
                    (name, behavior, now),
                )
                spec_id = cursor.lastrowid
            current = conn.execute(
                "SELECT COALESCE(MAX(generation), 0) FROM artifacts WHERE spec_id = ?",
                (spec_id,),
            ).fetchone()
            generation = current[0] + 1
            for kind, content in artifacts:
                if not isinstance(content, str):
                    raise TypeError(f"{kind} must be a string")
                conn.execute(
                    """INSERT INTO artifacts
                       (spec_id, kind, content, lines, pinned, adopted, generation, behavior, created_at)
                       VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?)""",
                    (spec_id, kind, content, len(content.splitlines()), generation, behavior, now),
                )
            return spec_id, generation
    finally:
        conn.close()


def get_spec(name):
    conn = connect()
    try:
        return conn.execute(
            "SELECT id, behavior, pinned FROM specs WHERE name = ?",
            (name,),
        ).fetchone()
    finally:
        conn.close()


def _fetch_artifact(spec_id, kind, generation=None, adopted_only=False):
    sql = """
        SELECT content, pinned, adopted, lines, generation, behavior, kind, id
        FROM artifacts
        WHERE spec_id = ? AND kind = ?
    """
    params = [spec_id, kind]
    if generation is not None:
        sql += " AND generation = ?"
        params.append(generation)
    if adopted_only:
        sql += " AND adopted = 1"
    sql += " ORDER BY generation DESC, id DESC LIMIT 1"
    conn = connect()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def get_latest_artifact(spec_id, kind):
    return _fetch_artifact(spec_id, kind)


def get_adopted_artifact(spec_id, kind):
    return _fetch_artifact(spec_id, kind, adopted_only=True)


def get_generation_artifact(spec_id, generation, kind):
    return _fetch_artifact(spec_id, kind, generation=generation)


def list_spec_names():
    conn = connect()
    try:
        rows = conn.execute("SELECT name FROM specs ORDER BY name").fetchall()
    finally:
        conn.close()
    return [row["name"] for row in rows]


def generation_numbers(spec_id):
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT DISTINCT generation FROM artifacts
               WHERE spec_id = ? AND generation IS NOT NULL
               ORDER BY generation""",
            (spec_id,),
        ).fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows]


def mark_adopted(spec_id, generation):
    conn = connect()
    try:
        with conn:
            found = conn.execute(
                """SELECT 1 FROM artifacts
                   WHERE spec_id = ? AND generation = ? AND kind = 'code'""",
                (spec_id, generation),
            ).fetchone()
            if not found:
                return False
            conn.execute("UPDATE artifacts SET adopted = 0 WHERE spec_id = ?", (spec_id,))
            conn.execute(
                """UPDATE artifacts SET adopted = 1
                   WHERE spec_id = ? AND generation = ?""",
                (spec_id, generation),
            )
        return True
    finally:
        conn.close()


def longer_than_adopted(spec_id, candidate):
    adopted = get_adopted_artifact(spec_id, "code")
    if adopted is None:
        return None
    if adopted["generation"] == candidate["generation"]:
        return None
    if adopted["behavior"] != candidate["behavior"]:
        return None
    if candidate["lines"] > adopted["lines"]:
        return adopted
    return None


def pin_spec(name):
    spec = get_spec(name)
    if spec is None:
        return False
    conn = connect()
    try:
        with conn:
            conn.execute("UPDATE specs SET pinned = 1 WHERE id = ?", (spec["id"],))
            conn.execute(
                """UPDATE artifacts SET pinned = 1
                   WHERE id = (
                       SELECT id FROM artifacts
                       WHERE spec_id = ? AND kind = 'code'
                       ORDER BY generation DESC, id DESC LIMIT 1
                   )""",
                (spec["id"],),
            )
        return True
    finally:
        conn.close()


def unpin_spec(name):
    spec = get_spec(name)
    if spec is None:
        return False
    conn = connect()
    try:
        with conn:
            conn.execute("UPDATE specs SET pinned = 0 WHERE id = ?", (spec["id"],))
            conn.execute(
                "UPDATE artifacts SET pinned = 0 WHERE spec_id = ?",
                (spec["id"],),
            )
        return True
    finally:
        conn.close()


_SHARED_INSTRUCTIONS = """You are the generator for a minimal specification-driven coding system.

Purpose of the system:
- Users interact through a CLI REPL.
- Behavioral specifications are stored in a SQLite database.
- Tests and a behavioral check are written before the implementation.
- The implementation is the shortest Python that makes those tests pass.
- Generated code, tests, and the behavioral check are saved back into SQLite.
- Self-modification is allowed only through the spec → regenerate → test → explicit adopt path.
- Critical bootstrap pieces can be pinned and must not be overwritten.

Long-term aim: the system should be able to generate and improve parts of itself while remaining recoverable.

The implementation module name is impl. Pytest loads the code from impl.py.
tests and check must import that module with `import impl` or `from impl import ...`.
Do not start a subprocess to import the code.
The check calls the functions in impl from the same process.
"""

_TEST_INSTRUCTIONS = _SHARED_INSTRUCTIONS + """
Write the tests before the implementation. impl.py is empty.
The tests and the check must fail because the behavior is not implemented yet.
Do not write the implementation.
Do not import any module except impl.

Output fields:
- tests: pytest unit tests that import impl
- check: a higher-level pytest behavioral check that imports impl
- explanation: one short paragraph
"""

_CODE_INSTRUCTIONS = _SHARED_INSTRUCTIONS + """
The tests and the check are already written. They are fixed.
Write the shortest possible working implementation (fewest lines) that makes them pass.
Do not change the tests or the check.
Call each adopted module the request names. An unused import does not count.
Do not reimplement a listed module.
When the request lists no adopted modules, do not import another project module.

Output fields:
- code: the shortest working Python implementation
- explanation: one short paragraph
"""

_PLAN_INSTRUCTIONS = """You plan the modules of an application for a specification-driven coding system.

Break the description into modules that form a clear architecture.
Give each module one job.
When the description has more than one job, return one module for each job.
Do not put those jobs in one module.
A module may call other modules in this plan. List those names in uses.
uses is empty when the module calls no other module.
The name is a relative path with no spaces and no file extension. Use slashes for directories.
The behavior is your recommendation for that module. Write one or more sentences the user can edit.
Do not write Python.

Output fields:
- modules: a list of name, behavior, and uses
- explanation: one short paragraph naming the architecture
"""

_CYCLE_INSTRUCTIONS = """You split modules that use each other so a build can order them.

Add one new module for the job those modules share.
Keep the job of each listed module.
A listed module must not use another listed module.
A listed module may use the new module. Name the new module in uses when it calls that job.
The new module must not use a listed module.
The name is a relative path with no spaces and no file extension. Use slashes for directories.
The behavior is one or more sentences the user can edit.
Do not write Python.

Output fields:
- name: the new module
- behavior: the shared job
- uses: modules the new module calls. This list is empty when it calls none.
- modules: each listed module, with its name, behavior, and uses
- explanation: one short paragraph
"""


def _request_generation(user_input: str, instructions: str, text_format):
    model = configured_model()
    effort = configured_reasoning_effort()
    client = OpenAI()  # uses OPENAI_API_KEY from the environment or Application Support .env
    log.info("model request model=%s reasoning_effort=%s", model, effort)
    response = client.responses.parse(
        model=model,
        reasoning={"effort": effort},
        instructions=instructions,
        input=user_input,
        text_format=text_format,
    )
    log.info("model response received")
    return response.output_parsed


def generate_tests(behavior: str) -> TestsResult:
    return _request_generation(
        "Behavioral specification:\n"
        f"{behavior}\n\n"
        "Write the unit tests and the behavioral check first.\n"
        "impl.py is empty.\n"
        "The tests and the check must fail because the behavior is not implemented yet.",
        _TEST_INSTRUCTIONS,
        TestsResult,
    )


def repair_tests(behavior: str, tests: str, check: str, output: str, kind: str) -> TestsResult:
    if kind == "pass":
        reason = "Pytest passed. The tests do not require the behavior."
    else:
        reason = (
            "Pytest did not fail on the missing behavior. "
            "A collection error, a missing module, a syntax error, or a passing test means the tests are wrong."
        )
    return _request_generation(
        "Behavioral specification:\n"
        f"{behavior}\n\n"
        "impl.py is empty. The tests are written before the implementation.\n"
        f"{reason}\n"
        "Fix the tests and the check.\n"
        "They must import impl.\n"
        "They must fail because the behavior is not implemented.\n"
        "Do not write the implementation.\n\n"
        f"Current tests:\n{tests}\n\n"
        f"Current check:\n{check}\n\n"
        f"Pytest output:\n{_failure_text(output)}",
        _TEST_INSTRUCTIONS,
        TestsResult,
    )


def _modules_prompt(modules, required=None) -> str:
    if not modules and not required:
        return "No other project modules are available. Do not import another project module.\n\n"
    names = [module_import(name) or name for name in (required or [])]
    lines = ["These modules are already adopted. Do not reimplement them."]
    if names:
        called = ", ".join(names)
        lines.append(
            f"Call each of these modules while performing the behavior: {called}."
        )
        lines.append("An unused import does not count.")
    for name, content in modules or []:
        imported = module_import(name) or name
        lines.append(f"\nModule {imported}:\n{str(content).rstrip()}")
    return "\n".join(lines) + "\n\n"


def generate_code(behavior: str, tests: str, check: str, modules=None, required=None) -> CodeResult:
    return _request_generation(
        "Behavioral specification:\n"
        f"{behavior}\n\n"
        f"{_modules_prompt(modules, required)}"
        "The tests and the check are fixed.\n"
        "Write the shortest implementation that makes them pass.\n\n"
        f"Tests:\n{tests}\n\n"
        f"Check:\n{check}",
        _CODE_INSTRUCTIONS,
        CodeResult,
    )


def plan_application(description: str) -> ApplicationPlan:
    return _request_generation(
        "Application description:\n"
        f"{description}\n\n"
        "Recommend the modules for this application.\n"
        "Give each job its own module.\n"
        "A module that calls another module must name it in uses.",
        _PLAN_INSTRUCTIONS,
        ApplicationPlan,
    )


def repair_cycle(members) -> CycleRepair:
    listed = ", ".join(sorted(record["name"] for record in members))
    blocks = []
    for record in sorted(members, key=lambda item: item["name"]):
        blocks.append(f"--- {record['name']} ---\n{record['text']}")
    return _request_generation(
        "These modules use each other:\n"
        f"{listed}\n\n"
        + "\n\n".join(blocks)
        + "\n\nAdd one module for the job they share.\n"
        "A listed module must not use another listed module.\n"
        "The new module must not use a listed module.",
        _CYCLE_INSTRUCTIONS,
        CycleRepair,
    )


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


def _repair_request(behavior, code, tests, check, output, modules, required, tests_passed):
    if tests_passed:
        failure = (
            "Pytest passed.\n"
            "The code does not call every adopted module named below.\n"
            "Call each missing module while performing the behavior.\n"
            "Keep the tests passing.\n"
            "Do not change the tests or the check.\n"
            "Keep the code as short as possible.\n\n"
        )
    else:
        failure = (
            "The tests failed. The tests and the check are fixed.\n"
            "Change only the code so pytest passes.\n"
            "Keep the code as short as possible.\n\n"
        )
    return (
        "Behavioral specification:\n"
        f"{behavior}\n\n"
        f"{_modules_prompt(modules, required)}"
        f"{failure}"
        f"Current code:\n{code}\n\n"
        f"Tests:\n{tests}\n\n"
        f"Check:\n{check}\n\n"
        f"Pytest output:\n{_failure_text(output)}"
    )


def repair_code(behavior: str, code: str, tests: str, check: str, output: str, modules=None, required=None, tests_passed=False) -> CodeResult:
    return _request_generation(
        _repair_request(behavior, code, tests, check, output, modules, required, tests_passed),
        _CODE_INSTRUCTIONS,
        CodeResult,
    )


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


def _module_name(name):
    safe = re.sub(r"\W", "_", name)
    return f"mechanicus_adopted_{safe}"


def load_adopted(name):
    spec = get_spec(name)
    if spec is None:
        return None
    # A pinned spec keeps the module already loaded in this process.
    if spec["pinned"] and name in _LOADED_MODULES:
        return _LOADED_MODULES[name]
    code = get_adopted_artifact(spec["id"], "code")
    if code is None:
        return None
    module_name = _module_name(name)
    module = types.ModuleType(module_name)
    module.__dict__["__name__"] = module_name
    exec(compile(code["content"], f"{name}.py", "exec"), module.__dict__)
    _LOADED_MODULES[name] = module
    return module


def run_adopted(name):
    log.info("run %s", name)
    if get_spec(name) is None:
        log.info("run refused: spec not found")
        print("Spec not found.")
        return
    try:
        module = load_adopted(name)
    except Exception as error:
        log.exception("run failed for %s", name)
        print(f"Error: {error}")
        return
    if module is None:
        log.info("run refused: no adopted code")
        print("No adopted code.")
        return
    main = getattr(module, "main", None)
    if not callable(main):
        log.info("loaded %s", name)
        print(f"Loaded '{name}'.")
        return
    try:
        result = main()
    except Exception as error:
        log.exception("run failed for %s", name)
        print(f"Error: {error}")
        return
    log.info("ran %s", name)
    if result is not None:
        print(result)


def _print_generation(spec_id, generation, heading):
    print(heading)
    titles = (("code", "Code"), ("tests", "Tests"), ("check", "Check"))
    for kind, title in titles:
        artifact = get_generation_artifact(spec_id, generation, kind)
        print(f"--- {title} ---")
        if artifact is None or not str(artifact["content"]).strip():
            print("(none)")
        else:
            print(str(artifact["content"]).rstrip("\n"))


def show_spec(name):
    log.info("show %s", name)
    spec = get_spec(name)
    if spec is None:
        log.info("show refused: spec not found")
        print("Not found.")
        return
    print(f"Spec: {spec['behavior']} (pinned={bool(spec['pinned'])})")
    adopted = get_adopted_artifact(spec["id"], "code")
    latest = get_latest_artifact(spec["id"], "code")
    if adopted is None:
        print("No adopted generation.")
    else:
        suffix = ""
        if adopted["behavior"] and adopted["behavior"] != spec["behavior"]:
            suffix = f" for behavior: {adopted['behavior']}"
        _print_generation(
            spec["id"],
            adopted["generation"],
            f"Adopted generation {adopted['generation']} ({adopted['lines']} lines){suffix}",
        )
    if latest is None:
        if adopted is None:
            print("No artifacts.")
        return
    if adopted is None or latest["generation"] != adopted["generation"]:
        _print_generation(
            spec["id"],
            latest["generation"],
            f"Latest candidate generation {latest['generation']} ({latest['lines']} lines, not adopted)",
        )


def adopt_spec(name, modules=None, required=None):
    log.info("adopt %s", name)
    if modules is None:
        modules, required, missing = dependency_modules(startup_dir(), name)
        if missing:
            names = ", ".join(missing)
            log.info("adopt refused: %s uses %s", name, names)
            print(f"'{name}' uses {names}, which is not adopted.")
            return
    if required is None:
        required = []
    spec = get_spec(name)
    if spec is None:
        log.info("adopt refused: spec not found")
        print("Spec not found.")
        return
    code = get_latest_artifact(spec["id"], "code")
    if code is None:
        log.info("adopt refused: no artifacts")
        print("No generated artifacts.")
        return
    # Pin blocks replacing an adopted version. The first adopt of pinned code still runs.
    if code["pinned"] and code["adopted"]:
        log.info("adopt refused: generation %s is pinned and adopted", code["generation"])
        print("Code is pinned; cannot adopt over it.")
        return
    tests = get_generation_artifact(spec["id"], code["generation"], "tests")
    check = get_generation_artifact(spec["id"], code["generation"], "check")
    if tests is None or not tests["content"].strip():
        log.info("adopt refused: no tests")
        print("No generated tests.")
        return
    if check is None or not check["content"].strip():
        log.info("adopt refused: no check")
        print("No behavioral check.")
        return
    adopted = longer_than_adopted(spec["id"], code)
    if adopted is not None:
        log.info(
            "adopt refused: generation %s has %s lines; adopted generation %s has %s lines",
            code["generation"],
            code["lines"],
            adopted["generation"],
            adopted["lines"],
        )
        print(
            f"Refused. Generation {code['generation']} is {code['lines']} lines; "
            f"adopted generation {adopted['generation']} is {adopted['lines']} lines "
            f"for the same behavior."
        )
        return
    behavior = spec["behavior"]
    repairs_left = 0 if spec["pinned"] else configured_repair_attempts()
    repair_limit = repairs_left
    repair_number = 0
    while True:
        print("Running tests...")
        log.info("running tests for generation %s", code["generation"])
        passed, output = run_tests(code["content"], tests["content"], check["content"], modules)
        pytest_passed = passed
        if passed:
            unused = uncalled_modules(code["content"], required)
            if unused:
                passed = False
                output = (
                    "The code does not call these adopted modules: "
                    + ", ".join(unused)
                    + ".\nImport each module and call it while performing the behavior."
                )
                log.info("generation %s does not call %s", code["generation"], ", ".join(unused))
                print(output)
        if passed:
            blocked = longer_than_adopted(spec["id"], code)
            if blocked is not None:
                log.info(
                    "adopt refused: generation %s has %s lines; adopted generation %s has %s lines",
                    code["generation"],
                    code["lines"],
                    blocked["generation"],
                    blocked["lines"],
                )
                print(
                    f"Refused. Generation {code['generation']} is {code['lines']} lines; "
                    f"adopted generation {blocked['generation']} is {blocked['lines']} lines "
                    f"for the same behavior."
                )
                return
            mark_adopted(spec["id"], code["generation"])
            log.info("adopted generation %s", code["generation"])
            print("Tests passed. Adopted.")
            return
        if not pytest_passed:
            log.info("tests failed for generation %s", code["generation"])
        log.info("pytest output:\n%s", _failure_text(output))
        if repairs_left <= 0:
            if spec["pinned"]:
                log.info("repair skipped: %s is pinned", name)
            print("Tests failed. Not adopted." if not pytest_passed else "Modules were not called. Not adopted.")
            return
        repair_number += 1
        repairs_left -= 1
        print(f"Repairing code ({repair_number} of {repair_limit})...")
        log.info("repair code %s of %s for %s", repair_number, repair_limit, name)
        try:
            result = repair_code(
                behavior,
                code["content"],
                tests["content"],
                check["content"],
                output,
                modules,
                required,
                pytest_passed,
            )
            spec_id, generation = save_generation(
                name, behavior, result.code, tests["content"], check["content"]
            )
        except Exception as error:
            log.exception("repair failed for %s", name)
            print(f"Error: {error}")
            print("Tests failed. Not adopted.")
            return
        code = get_generation_artifact(spec_id, generation, "code")
        tests = get_generation_artifact(spec_id, generation, "tests")
        check = get_generation_artifact(spec_id, generation, "check")
        log.info("saved repair generation %s (%s lines)", generation, code["lines"])
        print(f"\n--- Code ({code['lines']} lines) ---")
        print(result.code)
        print("\nTests and check are unchanged.")
        print(f"\n{result.explanation}")
        print(f"\nSaved generation {generation}.")


def revert_spec(name, generation):
    log.info("revert %s generation %s", name, generation)
    spec = get_spec(name)
    if spec is None:
        log.info("revert refused: spec not found")
        print("Spec not found.")
        return
    if spec["pinned"]:
        log.info("revert refused: spec is pinned")
        print("Spec is pinned. Unpin it before reverting.")
        return
    if generation not in generation_numbers(spec["id"]):
        available = ", ".join(str(number) for number in generation_numbers(spec["id"])) or "none"
        print(f"Generation {generation} not found. Available: {available}.")
        return
    mark_adopted(spec["id"], generation)
    log.info("reverted %s to generation %s", name, generation)
    print(f"Reverted '{name}' to generation {generation}.")


def adopted_output_path(root, name):
    relative = Path(name)
    if relative.is_absolute() or any(part in ("", "..") for part in relative.parts):
        return None
    if relative.suffix != ".py":
        relative = Path(f"{relative}.py")
    destination = (root / relative).resolve()
    try:
        destination.relative_to(root)
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
    relative = code_path.relative_to(root)
    destination = (root / "tests" / relative.parent / f"test_{relative.stem}.py").resolve()
    try:
        destination.relative_to(root)
    except ValueError:
        return None
    return destination


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


def write_text_file(destination, text):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if text and not text.endswith("\n"):
        text += "\n"
    destination.write_text(text)


def write_adopted(root, name):
    log.info("generate %s", name)
    spec = get_spec(name)
    if spec is None:
        log.info("generate refused: spec not found")
        print("Spec not found.")
        return False
    code = get_adopted_artifact(spec["id"], "code")
    if code is None:
        log.info("generate refused: no adopted code")
        print(f"No adopted code for '{name}'.")
        return False
    destination = adopted_output_path(root, name)
    if destination is None:
        print(f"'{name}' is not a path inside the startup directory.")
        return False
    write_text_file(destination, code["content"])
    log.info("wrote %s", destination)
    print(destination.relative_to(root).as_posix())
    tests = get_adopted_artifact(spec["id"], "tests")
    if tests is None or not str(tests["content"]).strip():
        log.info("no adopted tests for %s", name)
        print(f"No adopted tests for '{name}'.")
        return True
    test_destination = test_output_path(root, name)
    if test_destination is None:
        print(f"'{name}' is not a path inside the startup directory.")
        return False
    write_text_file(test_destination, tests_for_disk(tests["content"], name))
    log.info("wrote %s", test_destination)
    print(test_destination.relative_to(root).as_posix())
    return True


def generate_command(root, name=None):
    if name is None:
        names = []
        for spec_name in list_spec_names():
            spec = get_spec(spec_name)
            if spec is not None and get_adopted_artifact(spec["id"], "code") is not None:
                names.append(spec_name)
        if not names:
            log.info("generate refused: no adopted code")
            print("No adopted code.")
            return
    else:
        names = [name]
    for spec_name in names:
        write_adopted(root, spec_name)


def _print_tests(tests, check, explanation):
    print("\n--- Tests ---")
    print(tests)
    print("\n--- Check ---")
    print(check)
    print(f"\n{explanation}")


def tests_until_red(behavior, modules=None):
    """Write tests that fail against an empty module. Return None when they do not."""
    result = generate_tests(behavior)
    repairs_left = configured_repair_attempts()
    repair_limit = repairs_left
    repair_number = 0
    while True:
        tests = result.tests
        check = result.check
        _print_tests(tests, check, result.explanation)
        if not str(tests).strip() or not str(check).strip():
            passed = False
            output = "The tests or the check are empty."
            kind = "broken"
            log.info("tests are empty before the implementation")
        else:
            print("Running tests...")
            passed, output = run_tests("", tests, check, modules)
            kind = classify_before_code(passed, output)
            log.info("tests before the implementation: %s", kind)
            log.info("pytest output:\n%s", _failure_text(output))
        if kind == "red":
            log.info("tests fail before the implementation")
            print("Tests fail before the implementation.")
            return tests, check
        if repairs_left <= 0:
            log.info("tests did not fail for the behavior")
            return None
        repair_number += 1
        repairs_left -= 1
        print(f"Repairing tests ({repair_number} of {repair_limit})...")
        log.info("repair tests %s of %s", repair_number, repair_limit)
        result = repair_tests(behavior, tests, check, output, kind)


def spec_command(name, behavior, modules=None, record=None, required=None):
    log.info("spec %s behavior %s", name, behavior)
    recorded = behavior if record is None else record
    if modules is None:
        modules, required, missing = dependency_modules(startup_dir(), name)
        if missing:
            names = ", ".join(missing)
            log.info("spec refused: %s uses %s", name, names)
            print(f"'{name}' uses {names}, which is not adopted.")
            return
    if required is None:
        required = []
    existing = get_spec(name)
    if existing is not None and existing["pinned"]:
        log.info("spec refused: %s is pinned", name)
        print(f"'{name}' is pinned. It cannot be regenerated or overwritten.")
        return
    print("Writing tests...")
    try:
        written = tests_until_red(behavior, modules)
    except Exception as error:
        log.exception("spec failed for %s", name)
        print(f"Error: {error}")
        return
    if written is None:
        print("Tests did not fail for the behavior. Not saved.")
        return
    tests, check = written
    print("Writing code...")
    try:
        result = generate_code(behavior, tests, check, modules, required)
    except Exception as error:
        log.exception("code generation failed for %s", name)
        try:
            _, generation = save_generation(name, recorded, "", tests, check)
        except Exception as save_error:
            log.exception("spec failed for %s", name)
            print(f"Error: {save_error}")
            return
        log.info("saved %s generation %s without code", name, generation)
        print(f"Error: {error}")
        print(f"Saved generation {generation} without code. Use '/adopt {name}' to write the code.")
        return
    try:
        spec_id, generation = save_generation(name, recorded, result.code, tests, check)
    except Exception as error:
        log.exception("spec failed for %s", name)
        print(f"Error: {error}")
        return
    code = get_generation_artifact(spec_id, generation, "code")
    print(f"\n--- Code ({code['lines']} lines) ---")
    print(result.code)
    print(f"\n{result.explanation}")
    log.info("saved %s generation %s (%s lines)", name, generation, code["lines"])
    print(f"\nSaved generation {generation}. Use '/adopt {name}' to run tests and accept.")


_SKIP_DIR_NAMES = {".mechanicus", ".git", ".venv", "venv", "__pycache__", ".pytest_cache"}


def spec_name_for_module(name):
    text = str(name).strip().replace("\\", "/")
    if not text or text.startswith("/"):
        return None
    relative = Path(text)
    if relative.is_absolute():
        return None
    if relative.suffix.lower() in (".py", ".module"):
        relative = relative.with_suffix("")
    if not relative.parts:
        return None
    for part in relative.parts:
        if part in ("", ".", "..") or part.startswith("."):
            return None
        if any(character.isspace() for character in part):
            return None
    return relative.as_posix()


def module_output_path(root, name):
    spec_name = spec_name_for_module(name)
    if spec_name is None:
        return None, None
    root = Path(root).resolve()
    destination = (root / Path(spec_name)).with_suffix(".module").resolve()
    try:
        destination.relative_to(root)
    except ValueError:
        return None, None
    return spec_name, destination


def parse_module_text(text):
    uses = []
    invalid = []
    lines = str(text).splitlines()
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            index += 1
            break
        match = re.match(r"^uses:\s*(.*?)\s*$", stripped)
        if not match:
            break
        norm = spec_name_for_module(match.group(1))
        if norm is None:
            invalid.append(match.group(1))
        elif norm not in uses:
            uses.append(norm)
        index += 1
    behavior = "\n".join(lines[index:]).strip()
    return uses, behavior, invalid


def format_module_text(uses, behavior):
    lines = [f"uses: {name}" for name in uses]
    body = str(behavior).strip()
    if lines:
        return "\n".join(lines) + "\n\n" + body
    return body


def _module_uses(root, spec_name):
    _, destination = module_output_path(root, spec_name)
    if destination is None or not destination.is_file():
        return []
    uses, _, _invalid = parse_module_text(destination.read_text())
    return [use for use in uses if use != spec_name]


def _adopted_code(spec_name):
    spec = get_spec(spec_name)
    if spec is None:
        return None
    adopted = get_adopted_artifact(spec["id"], "code")
    if adopted is None or not str(adopted["content"]).strip():
        return None
    return adopted["content"]


def dependency_modules(root, spec_name):
    """Return adopted modules, the direct uses the code must call, and any missing uses.

    The module list includes the modules those uses themselves use.
    """
    root = Path(root).resolve()
    direct = _module_uses(root, spec_name)
    modules = []
    missing = []
    seen = {spec_name}
    pending = list(direct)
    while pending:
        use = pending.pop(0)
        if use in seen:
            continue
        seen.add(use)
        content = _adopted_code(use)
        if content is None:
            missing.append(use)
            continue
        modules.append((use, content))
        for nested in _module_uses(root, use):
            if nested not in seen:
                pending.append(nested)
    return modules, direct, missing


def find_module_files(root):
    root = Path(root).resolve()
    found = []
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("*.module")):
        relative = path.resolve().relative_to(root)
        if any(part in _SKIP_DIR_NAMES or part.startswith(".") for part in relative.parts[:-1]):
            continue
        if spec_name_for_module(relative.as_posix()) is None:
            continue
        found.append(path.resolve())
    return found


def plan_command(root, description):
    log.info("plan %s", description)
    print("Planning...")
    try:
        plan = plan_application(description)
    except Exception as error:
        log.exception("plan failed")
        print(f"Error: {error}")
        return
    root = Path(root).resolve()
    directory = root / ".mechanicus"
    directory.mkdir(parents=True, exist_ok=True)
    write_text_file(directory / "application.txt", description.strip())
    log.info("saved application description")
    if plan.explanation.strip():
        print(f"\n{plan.explanation.strip()}")
    if not plan.modules:
        log.info("plan recommended no modules")
        print("No modules recommended.")
        return
    seen = set()
    wrote = False
    for module in plan.modules:
        spec_name, destination = module_output_path(root, module.name)
        behavior = str(module.behavior).strip()
        if spec_name is None or destination is None or not behavior:
            log.info("plan skipped %s", module.name)
            print(f"Skipped '{module.name}'.")
            continue
        if destination in seen:
            relative = destination.relative_to(root).as_posix()
            log.info("plan skipped duplicate %s", relative)
            print(f"Skipped duplicate '{relative}'.")
            continue
        seen.add(destination)
        relative = destination.relative_to(root).as_posix()
        if destination.exists():
            log.info("plan left %s in place", relative)
            print(f"Left {relative} in place.")
            continue
        uses = []
        for raw in module.uses or []:
            norm = spec_name_for_module(raw)
            if norm and norm != spec_name and norm not in uses:
                uses.append(norm)
        text = format_module_text(uses, behavior)
        write_text_file(destination, text)
        wrote = True
        log.info("wrote %s", relative)
        print(f"\n--- {relative} ---")
        print(text)
    if wrote:
        print("\nEdit the module files, then enter /build.")
    else:
        print("No new module files.")


def module_records(root):
    root = Path(root).resolve()
    records = []
    seen = set()
    for path in find_module_files(root):
        relative = path.relative_to(root).as_posix()
        spec_name, destination = module_output_path(root, relative)
        if spec_name is None or destination != path or spec_name in seen:
            continue
        seen.add(spec_name)
        uses, behavior, invalid = parse_module_text(path.read_text())
        uses = [use for use in uses if use != spec_name]
        records.append({
            "name": spec_name,
            "path": path,
            "uses": uses,
            "invalid": invalid,
            "behavior": behavior,
            "text": format_module_text(uses, behavior).strip(),
        })
    return records


def _cycle_nodes(records, names):
    by_name = {record["name"]: record for record in records}
    state = {name: 0 for name in names}
    stack = []
    cycles = set()

    def visit(name):
        state[name] = 1
        stack.append(name)
        for use in by_name[name]["uses"]:
            if use not in names:
                continue
            if state[use] == 1:
                cycles.update(stack[stack.index(use):])
            elif state[use] == 0:
                visit(use)
        stack.pop()
        state[name] = 2

    for name in names:
        if state[name] == 0:
            visit(name)
    return cycles


def _blocked_names(records):
    by_name = {record["name"]: record for record in records}
    missing_for = {}
    invalid_for = {}
    for record in records:
        if record["invalid"]:
            invalid_for[record["name"]] = record["invalid"]
        missing = [use for use in record["uses"] if use not in by_name]
        if missing:
            missing_for[record["name"]] = missing
    blocked = set(missing_for) | set(invalid_for)
    changed = True
    while changed:
        changed = False
        for record in records:
            if record["name"] in blocked:
                continue
            if any(use in blocked for use in record["uses"]):
                blocked.add(record["name"])
                changed = True
    return by_name, missing_for, invalid_for, blocked


def arrange_modules(records):
    by_name, missing_for, invalid_for, blocked = _blocked_names(records)
    remaining = {record["name"] for record in records if record["name"] not in blocked}
    dependents = {name: [] for name in remaining}
    indegree = {name: 0 for name in remaining}
    for name in remaining:
        for use in by_name[name]["uses"]:
            if use in remaining:
                indegree[name] += 1
                dependents[use].append(name)
    ready = sorted(name for name in remaining if indegree[name] == 0)
    order_names = []
    while ready:
        name = ready.pop(0)
        order_names.append(name)
        for other in sorted(dependents[name]):
            indegree[other] -= 1
            if indegree[other] == 0:
                ready.append(other)
                ready.sort()
    cyclic = _cycle_nodes(records, [name for name in remaining if name not in order_names])
    skipped = []
    for name in sorted(invalid_for):
        quoted = ", ".join(f"'{raw}'" for raw in invalid_for[name])
        skipped.append(f"'{name}' has an invalid module name {quoted}.")
    for name in sorted(missing_for):
        if name in invalid_for:
            continue
        names = ", ".join(missing_for[name])
        skipped.append(f"'{name}' uses {names}, which has no module file.")
    for name in sorted(blocked - set(missing_for) - set(invalid_for)):
        skipped.append(f"'{name}' was not built. A module it uses was not built.")
    for name in sorted(cyclic):
        skipped.append(f"'{name}' was not built. The modules use each other.")
    for name in sorted(set(remaining) - set(order_names) - cyclic):
        skipped.append(f"'{name}' was not built. A module it uses was not built.")
    return [by_name[name] for name in order_names], skipped


def cyclic_components(records):
    by_name, _missing_for, _invalid_for, blocked = _blocked_names(records)
    eligible = {name for name in by_name if name not in blocked}
    index = 0
    stack = []
    indices = {}
    low = {}
    on_stack = set()
    components = []

    def strongconnect(name):
        nonlocal index
        indices[name] = index
        low[name] = index
        index += 1
        stack.append(name)
        on_stack.add(name)
        for use in by_name[name]["uses"]:
            if use not in eligible:
                continue
            if use not in indices:
                strongconnect(use)
                low[name] = min(low[name], low[use])
            elif use in on_stack:
                low[name] = min(low[name], indices[use])
        if low[name] == indices[name]:
            component = set()
            while True:
                node = stack.pop()
                on_stack.remove(node)
                component.add(node)
                if node == name:
                    break
            if len(component) > 1:
                components.append(component)

    for name in sorted(eligible):
        if name not in indices:
            strongconnect(name)
    components.sort(key=lambda names: sorted(names))
    return components


def _module_is_pinned(name):
    spec = get_spec(name)
    return spec is not None and bool(spec["pinned"])


def _clean_uses(raw_uses, allowed):
    uses = []
    for raw in raw_uses or []:
        norm = spec_name_for_module(raw)
        if norm and norm in allowed and norm not in uses:
            uses.append(norm)
    return uses


def apply_cycle_repair(root, records, members, known, repair):
    """Write the shared module and stop the listed modules from using each other."""
    root = Path(root).resolve()
    component = {record["name"] for record in members}
    shared = spec_name_for_module(repair.name)
    behavior = str(repair.behavior).strip()
    if shared is None or not behavior or shared in known:
        return False
    _spec_name, destination = module_output_path(root, shared)
    if destination is None or destination.exists():
        return False
    by_repair = {}
    for module in repair.modules:
        name = spec_name_for_module(module.name)
        text = str(module.behavior).strip()
        if name is None or not text or name in by_repair:
            return False
        by_repair[name] = (text, module.uses)
    if set(by_repair) != component:
        return False
    outside = set(known) - component
    shared_uses = _clean_uses(repair.uses, outside)
    rewritten = []
    users = 0
    for record in members:
        prose, raw_uses = by_repair[record["name"]]
        uses = _clean_uses(raw_uses, outside | {shared})
        if shared in uses:
            users += 1
        rewritten.append((record, uses, prose))
    if users == 0:
        return False
    proposed = []
    replaced = {record["name"]: uses for record, uses, _prose in rewritten}
    for record in records:
        item = dict(record)
        if record["name"] in replaced:
            item["uses"] = replaced[record["name"]]
        proposed.append(item)
    proposed.append({"name": shared, "uses": shared_uses, "invalid": [], "behavior": behavior})
    involved = _cycle_nodes(proposed, [item["name"] for item in proposed])
    if involved & (component | {shared}):
        return False
    write_text_file(destination, format_module_text(shared_uses, behavior))
    relative = destination.relative_to(root).as_posix()
    log.info("cycle added %s", relative)
    print(f"Added {relative}.")
    for record, uses, prose in rewritten:
        write_text_file(record["path"], format_module_text(uses, prose))
        relative = record["path"].relative_to(root).as_posix()
        log.info("cycle updated %s", relative)
        print(f"Updated {relative}.")
    if str(repair.explanation).strip():
        print(repair.explanation.strip())
    return True


def _cycle_edges(graph, names):
    edges = []

    def reaches(start):
        seen = set()
        pending = [start]
        while pending:
            node = pending.pop()
            if node in seen or node not in names:
                continue
            seen.add(node)
            for use in graph.get(node, []):
                if use in names and use not in seen:
                    pending.append(use)
        return seen

    for source in names:
        for target in graph.get(source, []):
            if target in names and source in reaches(target):
                edges.append((source, target))
    return edges


def remove_cycle_uses(root, members):
    """Remove uses lines inside one cycle until those modules do not use each other."""
    root = Path(root).resolve()
    names = {record["name"] for record in members}
    graph = {record["name"]: list(record["uses"]) for record in members}
    removed = []
    while True:
        edges = _cycle_edges(graph, names)
        if not edges:
            break
        source, target = max(edges)
        graph[source] = [use for use in graph[source] if use != target]
        removed.append((source, target))
    if not removed:
        return False
    by_name = {record["name"]: record for record in members}
    for source, _target in removed:
        record = by_name[source]
        write_text_file(record["path"], format_module_text(graph[source], record["behavior"]))
    for source, target in removed:
        log.info("cycle removed uses %s -> %s", source, target)
        print(f"Removed uses: {target} from {source}.")
    return True


def separate_cycles(root, records):
    """Break module cycles before a build orders the modules."""
    root = Path(root).resolve()
    changed = False
    skipped = set()
    current = records
    while True:
        progressed = False
        known = {record["name"] for record in current}
        for component in cyclic_components(current):
            frozen = frozenset(component)
            if frozen in skipped:
                continue
            members = [record for record in current if record["name"] in component]
            label = ", ".join(sorted(component))
            if any(_module_is_pinned(record["name"]) for record in members):
                log.info("cycle not changed, pinned module: %s", label)
                print(f"{label} use each other.")
                print("Mechanicus did not change a pinned module.")
                skipped.add(frozen)
                continue
            log.info("cycle %s", label)
            print(f"{label} use each other.")
            repair = None
            try:
                repair = repair_cycle(members)
            except Exception as error:
                log.exception("cycle repair failed")
                print(f"Error: {error}")
            if repair is not None and apply_cycle_repair(root, current, members, known, repair):
                changed = True
                progressed = True
                break
            if repair is not None:
                print("Mechanicus did not use the new module.")
            if remove_cycle_uses(root, members):
                changed = True
                progressed = True
                break
            skipped.add(frozen)
        if not progressed:
            break
        current = module_records(root)
    return changed


def build_one(root, record):
    root = Path(root).resolve()
    spec_name = record["name"]
    prose = record["behavior"]
    canonical = record["text"]
    if not prose:
        log.info("build skipped empty %s", spec_name)
        print(f"'{spec_name}' is empty.")
        return
    spec = get_spec(spec_name)
    if spec is not None and spec["pinned"]:
        log.info("build skipped pinned %s", spec_name)
        print(f"'{spec_name}' is pinned. It was not changed.")
        return
    modules, required, missing = dependency_modules(root, spec_name)
    if missing:
        names = ", ".join(missing)
        log.info("build skipped %s uses %s", spec_name, names)
        print(f"'{spec_name}' uses {names}, which is not adopted.")
        return
    adopted = None if spec is None else get_adopted_artifact(spec["id"], "code")
    same = spec is not None and str(spec["behavior"]).strip() == canonical
    if same and adopted is not None and str(adopted["content"]).strip():
        log.info("build writes adopted %s", spec_name)
        print(f"'{spec_name}' is already adopted.")
        write_adopted(root, spec_name)
        return
    if not same:
        print(f"Building {spec_name}...")
        spec_command(spec_name, prose, modules, canonical, required)
        spec = get_spec(spec_name)
        if spec is None or str(spec["behavior"]).strip() != canonical:
            print(f"'{spec_name}' was not saved.")
            return
    else:
        print(f"Adopting {spec_name}...")
    adopted = get_adopted_artifact(spec["id"], "code")
    latest = get_latest_artifact(spec["id"], "code")
    if adopted is None or latest is None or adopted["generation"] != latest["generation"]:
        adopt_spec(spec_name, modules, required)
        spec = get_spec(spec_name)
    adopted = None if spec is None else get_adopted_artifact(spec["id"], "code")
    if adopted is None or not str(adopted["content"]).strip():
        print(f"'{spec_name}' was not adopted.")
        return
    if str(adopted["behavior"] or "").strip() != canonical:
        print(f"'{spec_name}' was not adopted.")
        return
    write_adopted(root, spec_name)


def build_command(root):
    log.info("build")
    root = Path(root).resolve()
    records = module_records(root)
    if not records:
        log.info("build found no module files")
        print("No module files.")
        return
    if separate_cycles(root, records):
        records = module_records(root)
    order, skipped = arrange_modules(records)
    for message in skipped:
        log.info("build %s", message)
        print(message)
    for record in order:
        build_one(root, record)


def repl():
    root = startup_dir()
    ensure_app_files()
    init_db()
    log_path = start_session_log()
    try:
        print("Seed REPL. Commands:")
        print("  /spec <name> <behavior>     - store a spec, write failing tests, then generate code")
        print("  /plan <description>         - write a .module file for each recommended module")
        print("  /build                      - build each .module file and write the adopted code")
        print("  /pin <name>                 - pin a spec so it cannot be regenerated or overwritten")
        print("  /unpin <name>               - clear that pin")
        print("  /adopt <name>               - run tests, repair the code, and adopt when they pass")
        print("  /revert <name> <generation> - adopt an earlier generation without generating")
        print("  /show <name>                - show the spec, the adopted rendering, and any newer candidate")
        print("  /generate [name]            - write adopted code and tests into the startup directory")
        print("  /run <name>                 - run the adopted code for a spec")
        print("  /logfile                    - print the path to the session log file")
        print("  /quit                       - exit the REPL")
        while True:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            log.info("command %s", line)
            if line == "/quit":
                break
            parts = line.split(maxsplit=2)
            cmd = parts[0]

            if cmd == "/spec" and len(parts) == 3:
                spec_command(parts[1], parts[2])
            elif cmd == "/plan":
                description = line.split(maxsplit=1)[1].strip() if " " in line else ""
                if not description:
                    log.info("plan refused: no description")
                    print("Enter an application description.")
                else:
                    plan_command(root, description)
            elif cmd == "/build" and len(parts) == 1:
                build_command(root)
            elif cmd == "/pin" and len(parts) == 2:
                if pin_spec(parts[1]):
                    log.info("pinned %s", parts[1])
                    print(f"Pinned '{parts[1]}'.")
                else:
                    log.info("pin refused: spec not found")
                    print("Spec not found.")
            elif cmd == "/unpin" and len(parts) == 2:
                if unpin_spec(parts[1]):
                    log.info("unpinned %s", parts[1])
                    print(f"Unpinned '{parts[1]}'.")
                else:
                    log.info("unpin refused: spec not found")
                    print("Spec not found.")
            elif cmd == "/adopt" and len(parts) == 2:
                adopt_spec(parts[1])
            elif cmd == "/revert" and len(parts) == 3:
                try:
                    generation = int(parts[2])
                except ValueError:
                    log.info("revert refused: generation is not an integer")
                    print("Generation must be an integer.")
                    continue
                revert_spec(parts[1], generation)
            elif cmd == "/show" and len(parts) == 2:
                show_spec(parts[1])
            elif cmd == "/generate" and len(parts) == 1:
                generate_command(root)
            elif cmd == "/generate" and len(parts) == 2:
                generate_command(root, parts[1])
            elif cmd == "/run" and len(parts) == 2:
                run_adopted(parts[1])
            elif cmd == "/logfile" and len(parts) == 1:
                print(f"Log file: {log_path}")
            else:
                log.info("unknown command %s", cmd)
                print("Unknown command.")
    finally:
        close_session_log()
        print(f"Log file: {log_path}")


if __name__ == "__main__":
    repl()
