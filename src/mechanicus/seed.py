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
from pydantic import BaseModel

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
Do not import any other module name. Do not start a subprocess to import the code.
The check calls the functions in impl from the same process.
"""

_TEST_INSTRUCTIONS = _SHARED_INSTRUCTIONS + """
Write the tests before the implementation. impl.py is empty.
The tests and the check must fail because the behavior is not implemented yet.
Do not write the implementation.

Output fields:
- tests: pytest unit tests that import impl
- check: a higher-level pytest behavioral check that imports impl
- explanation: one short paragraph
"""

_CODE_INSTRUCTIONS = _SHARED_INSTRUCTIONS + """
The tests and the check are already written. They are fixed.
Write the shortest possible working implementation (fewest lines) that makes them pass.
Do not change the tests or the check.

Output fields:
- code: the shortest working Python implementation
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


def generate_code(behavior: str, tests: str, check: str) -> CodeResult:
    return _request_generation(
        "Behavioral specification:\n"
        f"{behavior}\n\n"
        "The tests and the check are fixed.\n"
        "Write the shortest implementation that makes them pass.\n\n"
        f"Tests:\n{tests}\n\n"
        f"Check:\n{check}",
        _CODE_INSTRUCTIONS,
        CodeResult,
    )


def repair_code(behavior: str, code: str, tests: str, check: str, output: str) -> CodeResult:
    return _request_generation(
        "Behavioral specification:\n"
        f"{behavior}\n\n"
        "The tests failed. The tests and the check are fixed.\n"
        "Change only the code so pytest passes.\n"
        "Keep the code as short as possible.\n\n"
        f"Current code:\n{code}\n\n"
        f"Tests:\n{tests}\n\n"
        f"Check:\n{check}\n\n"
        f"Pytest output:\n{_failure_text(output)}",
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


def run_tests(code: str, tests: str, check: str) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
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


def adopt_spec(name):
    log.info("adopt %s", name)
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
        passed, output = run_tests(code["content"], tests["content"], check["content"])
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
        log.info("tests failed for generation %s", code["generation"])
        log.info("pytest output:\n%s", _failure_text(output))
        if repairs_left <= 0:
            if spec["pinned"]:
                log.info("repair skipped: %s is pinned", name)
            print("Tests failed. Not adopted.")
            return
        repair_number += 1
        repairs_left -= 1
        print(f"Repairing code ({repair_number} of {repair_limit})...")
        log.info("repair code %s of %s for %s", repair_number, repair_limit, name)
        try:
            result = repair_code(behavior, code["content"], tests["content"], check["content"], output)
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
        text = re.sub(r"\bimpl\b", module, text)
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


def tests_until_red(behavior):
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
            passed, output = run_tests("", tests, check)
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


def spec_command(name, behavior):
    log.info("spec %s behavior %s", name, behavior)
    existing = get_spec(name)
    if existing is not None and existing["pinned"]:
        log.info("spec refused: %s is pinned", name)
        print(f"'{name}' is pinned. It cannot be regenerated or overwritten.")
        return
    print("Writing tests...")
    try:
        written = tests_until_red(behavior)
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
        result = generate_code(behavior, tests, check)
    except Exception as error:
        log.exception("code generation failed for %s", name)
        try:
            _, generation = save_generation(name, behavior, "", tests, check)
        except Exception as save_error:
            log.exception("spec failed for %s", name)
            print(f"Error: {save_error}")
            return
        log.info("saved %s generation %s without code", name, generation)
        print(f"Error: {error}")
        print(f"Saved generation {generation} without code. Use '/adopt {name}' to write the code.")
        return
    try:
        spec_id, generation = save_generation(name, behavior, result.code, tests, check)
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


def repl():
    root = startup_dir()
    ensure_app_files()
    init_db()
    log_path = start_session_log()
    try:
        print("Seed REPL. Commands:")
        print("  /spec <name> <behavior>     - store a spec, write failing tests, then generate code")
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
