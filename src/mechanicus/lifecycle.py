"""Spec, adopt, revert, run, and generate lifecycle."""
import os
import re
import subprocess
import sys
import types
from pathlib import Path

from mechanicus.bootstrap_guard import refuse_bootstrap
from mechanicus.codegen import (
    generate_code,
    generate_tests,
    refactor_code,
    repair_code,
    repair_tests,
)
from mechanicus.config import configured_repair_attempts, log, startup_dir
from mechanicus.harness import (
    _failure_text,
    classify_before_code_with_reason,
    real_deps_exercised,
    run_tests,
    sniff_test_sources,
    tests_for_disk,
    uncalled_modules,
)
from mechanicus.jev import (
    clear_last_jev,
    last_jev_result,
    note_jev_result,
    weak_call_tests,
)
from mechanicus.paths import adopted_output_path, layout_conflict, test_output_path, write_text_file
from mechanicus.store import (
    generation_numbers,
    get_adopted_artifact,
    get_generation_artifact,
    get_latest_artifact,
    get_spec,
    list_spec_names,
    longer_than_adopted,
    mark_adopted,
    record_adopt_result,
    clear_deps_fingerprint,
    record_deps_fingerprint,
    record_jev_result,
    save_generation,
)


_LOADED_MODULES = {}


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


def _normalize_text(text):
    text = str(text)
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def disk_sync_status(root, name):
    """Compare adopted artifacts to on-disk files write_adopted would use.

    Returns one of: none, missing, differ, ok.
    """
    root = Path(root).resolve()
    spec = get_spec(name)
    if spec is None:
        return "none"
    code = get_adopted_artifact(spec["id"], "code")
    if code is None or not str(code["content"]).strip():
        return "none"
    destination = adopted_output_path(root, name)
    if destination is None or not destination.is_file():
        return "missing"
    if destination.read_text() != _normalize_text(code["content"]):
        return "differ"
    tests = get_adopted_artifact(spec["id"], "tests")
    if tests is not None and str(tests["content"]).strip():
        test_destination = test_output_path(root, name)
        if test_destination is None or not test_destination.is_file():
            return "differ"
        expected = _normalize_text(tests_for_disk(tests["content"], name))
        if test_destination.read_text() != expected:
            return "differ"
    return "ok"


def show_spec(name, root=None):
    log.info("show %s", name)
    root = Path(root).resolve() if root is not None else startup_dir()
    spec = get_spec(name)
    if spec is None:
        log.info("show refused: spec not found")
        print("Not found.")
        return
    sync = disk_sync_status(root, name)
    print(f"Spec: {spec['behavior']} (pinned={bool(spec['pinned'])} sync={sync})")
    adopt_status = spec["adopt_status"] or "none"
    if adopt_status != "none":
        adopt_line = f"Adopt: {adopt_status}"
        if spec["adopt_reason"]:
            adopt_line += f" reason={spec['adopt_reason']}"
        if spec["adopt_generation"] is not None:
            adopt_line += f" generation={spec['adopt_generation']}"
        print(adopt_line)
        if spec["adopt_detail"]:
            print(f"detail: {spec['adopt_detail']}")
    else:
        print("Adopt: none")
    jev_status = spec["jev_status"] or "none"
    if jev_status != "none":
        jev_line = f"Jev: {jev_status}"
        if spec["jev_reason"]:
            jev_line += f" reason={spec['jev_reason']}"
        if spec["jev_gate"]:
            jev_line += f" gate={spec['jev_gate']}"
        print(jev_line)
        if spec["jev_detail"]:
            print(f"detail: {spec['jev_detail']}")
    else:
        print("Jev: none")
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


def list_specs(root=None):
    log.info("specs")
    root = Path(root).resolve() if root is not None else startup_dir()
    names = list_spec_names()
    if not names:
        print("No specifications.")
        return
    width = max(len(name) for name in names)
    for name in names:
        spec = get_spec(name)
        pinned = "yes" if spec is not None and spec["pinned"] else "no"
        adopted = get_adopted_artifact(spec["id"], "code") if spec is not None else None
        generation = adopted["generation"] if adopted is not None else "none"
        sync = disk_sync_status(root, name)
        adopt = (spec["adopt_status"] or "none") if spec is not None else "none"
        jev = (spec["jev_status"] or "none") if spec is not None else "none"
        print(
            f"{name:<{width}}  pinned={pinned}  adopted={generation}  "
            f"sync={sync}  adopt={adopt}  jev={jev}"
        )


def _note_adopt(name, status, reason, detail="", generation=None):
    recorded = record_adopt_result(name, status, reason, detail, generation)
    if recorded:
        log.info(
            "adopt result %s status=%s reason=%s generation=%s",
            name,
            status,
            reason,
            generation,
        )
    return recorded


def adopt_spec(name, modules=None, required=None, allow_longer=False):
    log.info("adopt %s", name)
    if modules is None:
        from mechanicus.plan_build import dependency_modules

        modules, required, missing = dependency_modules(startup_dir(), name)
        if missing:
            names = ", ".join(missing)
            log.info("adopt refused: %s uses %s", name, names)
            print(f"'{name}' uses {names}, which is not adopted.")
            _note_adopt(name, "failed", "missing_deps", f"uses {names}, which is not adopted")
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
        _note_adopt(name, "failed", "no_artifacts", "No generated artifacts.")
        return
    # Pin blocks replacing an adopted version. The first adopt of pinned code still runs.
    if code["pinned"] and code["adopted"]:
        log.info("adopt refused: generation %s is pinned and adopted", code["generation"])
        print("Code is pinned; cannot adopt over it.")
        _note_adopt(
            name,
            "failed",
            "pinned",
            "Code is pinned; cannot adopt over it.",
            code["generation"],
        )
        return
    tests = get_generation_artifact(spec["id"], code["generation"], "tests")
    check = get_generation_artifact(spec["id"], code["generation"], "check")
    if tests is None or not tests["content"].strip():
        log.info("adopt refused: no tests")
        print("No generated tests.")
        _note_adopt(name, "failed", "no_tests", "No generated tests.", code["generation"])
        return
    if check is None or not check["content"].strip():
        log.info("adopt refused: no check")
        print("No behavioral check.")
        _note_adopt(name, "failed", "no_check", "No behavioral check.", code["generation"])
        return
    if not allow_longer:
        adopted = longer_than_adopted(spec["id"], code)
        if adopted is not None:
            detail = (
                f"Generation {code['generation']} is {code['lines']} lines; "
                f"adopted generation {adopted['generation']} is {adopted['lines']} lines "
                f"for the same behavior."
            )
            log.info(
                "adopt refused: generation %s has %s lines; adopted generation %s has %s lines",
                code["generation"],
                code["lines"],
                adopted["generation"],
                adopted["lines"],
            )
            print(f"Refused. {detail}")
            _note_adopt(name, "failed", "longer", detail, code["generation"])
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
        if passed and required:
            exercised, probe_detail = real_deps_exercised(
                code["content"], tests["content"], check["content"], modules, required
            )
            if not exercised:
                log.info("adopt refused: fake dependency path for %s", name)
                print(probe_detail)
                _note_adopt(
                    name,
                    "failed",
                    "fake_deps",
                    probe_detail,
                    code["generation"],
                )
                return
        if passed:
            if not allow_longer:
                blocked = longer_than_adopted(spec["id"], code)
                if blocked is not None:
                    detail = (
                        f"Generation {code['generation']} is {code['lines']} lines; "
                        f"adopted generation {blocked['generation']} is {blocked['lines']} lines "
                        f"for the same behavior."
                    )
                    log.info(
                        "adopt refused: generation %s has %s lines; adopted generation %s has %s lines",
                        code["generation"],
                        code["lines"],
                        blocked["generation"],
                        blocked["lines"],
                    )
                    print(f"Refused. {detail}")
                    _note_adopt(name, "failed", "longer", detail, code["generation"])
                    return
            mark_adopted(spec["id"], code["generation"])
            record_deps_fingerprint(name, required)
            log.info("adopted generation %s", code["generation"])
            print("Tests passed. Adopted.")
            _note_adopt(name, "ok", "ok", "Tests passed. Adopted.", code["generation"])
            return
        if not pytest_passed:
            log.info("tests failed for generation %s", code["generation"])
        log.info("pytest output:\n%s", _failure_text(output))
        if repairs_left <= 0:
            if spec["pinned"]:
                log.info("repair skipped: %s is pinned", name)
            if pytest_passed:
                print("Modules were not called. Not adopted.")
                _note_adopt(
                    name,
                    "failed",
                    "uncalled",
                    "Modules were not called. Not adopted.",
                    code["generation"],
                )
            else:
                print("Tests failed. Not adopted.")
                _note_adopt(
                    name,
                    "failed",
                    "harness",
                    "Tests failed. Not adopted.",
                    code["generation"],
                )
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
            _note_adopt(
                name,
                "failed",
                "error",
                f"Repair failed: {error}",
                code["generation"],
            )
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


def refactor_command(name):
    log.info("refactor %s", name)
    if refuse_bootstrap(name, "refactored"):
        return
    spec = get_spec(name)
    if spec is None:
        log.info("refactor refused: spec not found")
        print("Spec not found.")
        return
    if spec["pinned"]:
        log.info("refactor refused: spec is pinned")
        print(f"'{name}' is pinned. Unpin it before refactoring.")
        return
    code = get_adopted_artifact(spec["id"], "code")
    if code is None or not str(code["content"]).strip():
        log.info("refactor refused: no adopted code")
        print(f"No adopted code for '{name}'.")
        return
    tests = get_adopted_artifact(spec["id"], "tests")
    check = get_adopted_artifact(spec["id"], "check")
    if tests is None or not str(tests["content"]).strip():
        log.info("refactor refused: no tests")
        print("No adopted tests.")
        return
    if check is None or not str(check["content"]).strip():
        log.info("refactor refused: no check")
        print("No behavioral check.")
        return
    from mechanicus.plan_build import dependency_modules

    modules, required, missing = dependency_modules(startup_dir(), name)
    if missing:
        names = ", ".join(missing)
        log.info("refactor refused: %s uses %s", name, names)
        print(f"'{name}' uses {names}, which is not adopted.")
        return
    behavior = spec["behavior"]
    print("Refactoring...")
    try:
        result = refactor_code(
            behavior,
            code["content"],
            tests["content"],
            check["content"],
            modules,
            required,
        )
    except Exception as error:
        log.exception("refactor failed for %s", name)
        print(f"Error: {error}")
        return
    try:
        spec_id, generation = save_generation(
            name, behavior, result.code, tests["content"], check["content"]
        )
    except Exception as error:
        log.exception("refactor save failed for %s", name)
        print(f"Error: {error}")
        return
    saved = get_generation_artifact(spec_id, generation, "code")
    log.info("saved refactor generation %s (%s lines)", generation, saved["lines"])
    print(f"\n--- Code ({saved['lines']} lines) ---")
    print(result.code)
    print("\nTests and check are unchanged.")
    print(f"\n{result.explanation}")
    print(f"\nSaved generation {generation}.")
    adopt_spec(name, modules, required, allow_longer=True)
    adopted = get_adopted_artifact(spec["id"], "code")
    if adopted is not None and adopted["generation"] == generation:
        print(f"Enter '/generate {name}' to write the clearer code to disk.")


def revert_spec(name, generation, root=None, write_disk=True):
    log.info("revert %s generation %s write_disk=%s", name, generation, write_disk)
    root = Path(root).resolve() if root is not None else startup_dir()
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
    # Do not stamp current dependency generations: this generation was not
    # re-checked against them. Clearing marks the module stale until adopt.
    clear_deps_fingerprint(name)
    log.info("reverted %s to generation %s", name, generation)
    print(f"Reverted '{name}' to generation {generation}.")
    _note_adopt(name, "ok", "ok", f"Reverted to generation {generation}.", generation)
    if not write_disk:
        print(f"Database only. Enter '/generate {name}' to write that generation to disk.")
        return
    if write_adopted(root, name):
        print(f"Wrote generation {generation} to disk.")
        return
    log.info("revert left disk unchanged for %s", name)
    print("Database reverted. Disk was not updated.")


def write_adopted(root, name):
    log.info("generate %s", name)
    if refuse_bootstrap(name, "generated"):
        return False
    conflict = layout_conflict(root, name)
    if conflict:
        log.info("generate refused layout %s", conflict)
        print(f"Skipped {conflict}")
        return False
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


def test_command(root, name=None):
    """Run on-disk pytest with PYTHONPATH set to the startup directory."""
    root = Path(root).resolve()
    log.info("test %s", name or "(all)")
    if name is None:
        tests_dir = root / "tests"
        if not tests_dir.is_dir():
            log.info("test refused: no tests directory")
            print("No tests directory.")
            return False
        target = "tests"
        print("Running pytest...")
    else:
        destination = test_output_path(root, name)
        if destination is None:
            log.info("test refused: bad name %s", name)
            print(f"'{name}' is not a path inside the startup directory.")
            return False
        if not destination.is_file():
            log.info("test refused: missing %s", destination)
            print(f"No test file for '{name}'. Expected {destination.relative_to(root).as_posix()}.")
            return False
        target = destination.relative_to(root).as_posix()
        print(f"Running pytest for {target}...")
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(root) if not existing else f"{root}{os.pathsep}{existing}"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n")
    log.info("pytest exit %s", result.returncode)
    if result.returncode == 0:
        print("Tests passed.")
        return True
    if result.returncode == 5:
        print("No tests collected.")
        return False
    print("Tests failed.")
    return False


def _print_tests(tests, check, explanation):
    print("\n--- Tests ---")
    print(tests)
    print("\n--- Check ---")
    print(check)
    print(f"\n{explanation}")


def _persist_last_jev(name):
    outcome = last_jev_result()
    status = outcome.get("status") or "none"
    if status == "none":
        return False
    recorded = record_jev_result(
        name,
        status,
        outcome.get("reason") or "",
        outcome.get("detail") or "",
        outcome.get("gate") or "",
    )
    if recorded:
        log.info(
            "jev result %s status=%s reason=%s gate=%s",
            name,
            status,
            outcome.get("reason") or "",
            outcome.get("gate") or "",
        )
    return recorded


def tests_until_red(behavior, modules=None, required=None):
    """Write tests that fail against an empty module. Return None when they do not."""
    required = list(required or [])
    clear_last_jev()
    result = generate_tests(behavior, modules, required)
    repairs_left = configured_repair_attempts()
    repair_limit = repairs_left
    repair_number = 0
    jev_checked = False
    while True:
        tests = result.tests
        check = result.check
        _print_tests(tests, check, result.explanation)
        reason = ""
        if not str(tests).strip() or not str(check).strip():
            passed = False
            output = "The tests or the check are empty."
            kind = "broken"
            reason = "empty"
            log.info("tests are empty before the implementation")
        else:
            sniff = sniff_test_sources(tests, check)
            if sniff is not None:
                passed = False
                output = f"Tests are too weak to prove the behavior is missing ({sniff})."
                kind = "broken"
                reason = sniff
                log.info("tests sniffed before the implementation: %s", sniff)
                print(f"Tests are not red ({sniff}).")
            else:
                print("Running tests...")
                passed, output = run_tests("", tests, check, modules)
                kind, reason = classify_before_code_with_reason(passed, output, tests, check)
                log.info("tests before the implementation: %s (%s)", kind, reason)
                log.info("pytest output:\n%s", _failure_text(output))
                if kind != "red":
                    print(f"Tests are not red ({reason}).")
        if kind == "red" and required and not jev_checked:
            jev_checked = True
            try:
                weak = weak_call_tests(behavior, tests, check, required)
            except Exception as error:
                log.exception("jev tests failed")
                print(f"Error: {error}")
                note_jev_result("failed", "error", str(error), gate="tests")
                weak = []
            if weak:
                details = ", ".join(f"{name} ({probability:.2f})" for name, probability in weak)
                print(f"Tests do not require these calls: {details}.")
                output = (
                    "These tests do not fail when the implementation never calls: "
                    + ", ".join(name for name, _probability in weak)
                    + "."
                )
                print("Repairing tests for the missing calls...")
                log.info("repair tests for calls %s", ", ".join(name for name, _probability in weak))
                try:
                    result = repair_tests(
                        behavior, tests, check, output, "uses", modules, required
                    )
                except Exception as error:
                    log.exception("repair tests for calls failed")
                    print(f"Error: {error}")
                    log.info("tests fail before the implementation")
                    print("Tests fail before the implementation.")
                    return tests, check
                continue
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
        log.info("repair tests %s of %s reason=%s", repair_number, repair_limit, reason or kind)
        if reason and reason not in str(output):
            output = f"{reason}\n{output}"
        result = repair_tests(
            behavior, tests, check, output, kind, modules, required
        )


def spec_command(name, behavior, modules=None, record=None, required=None):
    log.info("spec %s behavior %s", name, behavior)
    if refuse_bootstrap(name, "regenerated"):
        return
    recorded = behavior if record is None else record
    if modules is None:
        from mechanicus.plan_build import dependency_modules

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
        written = tests_until_red(behavior, modules, required)
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
            _persist_last_jev(name)
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
        _persist_last_jev(name)
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

