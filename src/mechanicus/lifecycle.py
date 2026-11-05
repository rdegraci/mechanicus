"""Spec, adopt, revert, run, and generate lifecycle."""
import re
import types

from mechanicus.bootstrap_guard import refuse_bootstrap
from mechanicus.codegen import generate_code, generate_tests, repair_code, repair_tests
from mechanicus.config import configured_repair_attempts, log, startup_dir
from mechanicus.harness import (
    _failure_text,
    classify_before_code,
    run_tests,
    tests_for_disk,
    uncalled_modules,
)
from mechanicus.jev import weak_call_tests
from mechanicus.paths import adopted_output_path, test_output_path, write_text_file
from mechanicus.store import (
    generation_numbers,
    get_adopted_artifact,
    get_generation_artifact,
    get_latest_artifact,
    get_spec,
    list_spec_names,
    longer_than_adopted,
    mark_adopted,
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
        from mechanicus.plan_build import dependency_modules

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
    print(f"Enter '/generate {name}' to write that generation to disk.")


def write_adopted(root, name):
    log.info("generate %s", name)
    if refuse_bootstrap(name, "generated"):
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


def _print_tests(tests, check, explanation):
    print("\n--- Tests ---")
    print(tests)
    print("\n--- Check ---")
    print(check)
    print(f"\n{explanation}")


def tests_until_red(behavior, modules=None, required=None):
    """Write tests that fail against an empty module. Return None when they do not."""
    required = list(required or [])
    result = generate_tests(behavior)
    repairs_left = configured_repair_attempts()
    repair_limit = repairs_left
    repair_number = 0
    jev_checked = False
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
        if kind == "red" and required and not jev_checked:
            jev_checked = True
            try:
                weak = weak_call_tests(behavior, tests, check, required)
            except Exception as error:
                log.exception("jev tests failed")
                print(f"Error: {error}")
                print("Jev did not judge the tests.")
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
                    result = repair_tests(behavior, tests, check, output, "uses")
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
        log.info("repair tests %s of %s", repair_number, repair_limit)
        result = repair_tests(behavior, tests, check, output, kind)


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

