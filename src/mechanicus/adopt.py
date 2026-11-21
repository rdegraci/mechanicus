"""Adopt, strengthen, and uses-coverage review."""
from mechanicus.bootstrap_guard import refuse_bootstrap
from mechanicus.codegen import (
    generate_code,
    repair_code,
    review_suite_adequacy,
    strengthen_tests,
)
from mechanicus.config import configured_repair_attempts, log, startup_dir
from mechanicus.harness import (
    _failure_text,
    real_deps_exercised,
    run_tests,
    sniff_suite_thinness,
    sniff_test_sources,
    uncalled_modules,
)
from mechanicus.jev import (
    clear_last_jev,
    last_jev_result,
    note_jev_result,
    weak_call_tests,
)
from mechanicus.project import is_swift_package
from mechanicus.store import (
    get_adopted_artifact,
    get_generation_artifact,
    get_latest_artifact,
    get_spec,
    longer_than_adopted,
    mark_adopted,
    record_adopt_result,
    record_deps_fingerprint,
    record_jev_result,
    save_generation,
)
from mechanicus.swift_support import (
    empty_stub,
    missing_swift_dependency_types,
    restore_swift_artifacts,
    run_swift_tests,
    sniff_swift_test_sources,
    swift_code_is_empty,
    swift_toolchain_available,
    write_swift_artifacts,
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
    behavior = spec["behavior"]
    if is_swift_package(startup_dir()) and swift_code_is_empty(code["content"]):
        log.info("adopt found empty swift code; regenerating")
        print("Code was empty. Writing code...")
        try:
            regenerated = generate_code(
                behavior,
                tests["content"],
                check["content"],
                modules,
                required,
                name=name,
            )
            if swift_code_is_empty(regenerated.code):
                print("Code stayed empty. Not adopted.")
                _note_adopt(
                    name,
                    "failed",
                    "empty_code",
                    "Generated Swift code stayed empty.",
                    code["generation"],
                )
                return
            spec_id, generation = save_generation(
                name, behavior, regenerated.code, tests["content"], check["content"]
            )
            code = get_generation_artifact(spec_id, generation, "code")
            tests = get_generation_artifact(spec_id, generation, "tests")
            check = get_generation_artifact(spec_id, generation, "check")
            print(f"\n--- Code ({code['lines']} lines) ---")
            print(regenerated.code)
            print(f"\n{regenerated.explanation}")
            print(f"\nSaved generation {generation}.")
        except Exception as error:
            log.exception("adopt empty-code regenerate failed for %s", name)
            print(f"Error: {error}")
            print("Code stayed empty. Not adopted.")
            _note_adopt(
                name,
                "failed",
                "empty_code",
                f"Failed to regenerate empty code: {error}",
                code["generation"],
            )
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
    repairs_left = 0 if spec["pinned"] else configured_repair_attempts()
    repair_limit = repairs_left
    repair_number = 0
    root = startup_dir()
    swift = is_swift_package(root)
    swift_tests_regenerated = False

    def _restore_swift_adopted():
        if not swift:
            return
        adopted_code = get_adopted_artifact(spec["id"], "code")
        if adopted_code is not None and str(adopted_code["content"]).strip():
            adopted_tests = get_adopted_artifact(spec["id"], "tests")
            adopted_check = get_adopted_artifact(spec["id"], "check")
            write_swift_artifacts(
                root,
                name,
                adopted_code["content"],
                adopted_tests["content"] if adopted_tests is not None else "",
                adopted_check["content"] if adopted_check is not None else "",
            )
            return
        restore_swift_artifacts(
            root,
            name,
            {"code": empty_stub(root, name), "tests": None, "check": None},
        )

    def _regenerate_swift_tests_and_code():
        """Rewrite weak Swift tests, then generate code. Return True on success."""
        nonlocal code, tests, check, swift_tests_regenerated
        print("Tests look weak. Rewriting tests...")
        log.info(
            "adopt regenerating weak swift tests for %s generation %s",
            name,
            code["generation"],
        )
        try:
            from mechanicus.lifecycle import tests_until_red
            written = tests_until_red(behavior, modules, required, name=name)
        except Exception as error:
            log.exception("adopt test rewrite failed for %s", name)
            print(f"Error: {error}")
            return False
        if written is None:
            print("Could not rewrite tests that fail before the implementation.")
            return False
        new_tests, new_check = written
        print("Writing code for the new tests...")
        try:
            result = generate_code(
                behavior, new_tests, new_check, modules, required, name=name
            )
            code_text = result.code
            if swift_code_is_empty(code_text):
                attempts_left = configured_repair_attempts()
                while attempts_left > 0 and swift_code_is_empty(code_text):
                    attempts_left -= 1
                    print("Code was empty. Writing code again...")
                    result = generate_code(
                        behavior, new_tests, new_check, modules, required, name=name
                    )
                    code_text = result.code
            if swift_code_is_empty(code_text):
                print("Code stayed empty after rewriting tests.")
                return False
            spec_id, generation = save_generation(
                name, behavior, code_text, new_tests, new_check
            )
            _persist_last_jev(name)
        except Exception as error:
            log.exception("adopt code after test rewrite failed for %s", name)
            print(f"Error: {error}")
            return False
        code = get_generation_artifact(spec_id, generation, "code")
        tests = get_generation_artifact(spec_id, generation, "tests")
        check = get_generation_artifact(spec_id, generation, "check")
        swift_tests_regenerated = True
        log.info(
            "saved test-rewrite generation %s (%s lines) for %s",
            generation,
            code["lines"],
            name,
        )
        print(f"\n--- Code ({code['lines']} lines) ---")
        print(code_text)
        print(f"\n{result.explanation}")
        print(f"\nSaved generation {generation} with rewritten tests.")
        return True

    while True:
        print("Running tests...")
        log.info("running tests for generation %s", code["generation"])
        if swift:
            if not swift_toolchain_available():
                print("swift is not available on PATH. Not adopted.")
                _note_adopt(
                    name,
                    "failed",
                    "no_swift",
                    "swift is not available on PATH.",
                    code["generation"],
                )
                _restore_swift_adopted()
                return
            write_swift_artifacts(
                root, name, code["content"], tests["content"], check["content"]
            )
            passed, output = run_swift_tests(root, name)
            pytest_passed = passed
            if passed and required:
                unused = missing_swift_dependency_types(code["content"], required)
                if unused:
                    passed = False
                    output = (
                        "The code does not use these adopted types: "
                        + ", ".join(unused)
                        + ".\nUse each type while performing the behavior."
                    )
                    log.info(
                        "generation %s does not use %s",
                        code["generation"],
                        ", ".join(unused),
                    )
                    print(output)
        else:
            passed, output = run_tests(
                code["content"], tests["content"], check["content"], modules
            )
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
                    log.info(
                        "generation %s does not call %s",
                        code["generation"],
                        ", ".join(unused),
                    )
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
                    _restore_swift_adopted()
                    return
            mark_adopted(spec["id"], code["generation"])
            record_deps_fingerprint(name, required)
            log.info("adopted generation %s", code["generation"])
            print("Tests passed. Adopted.")
            _note_adopt(name, "ok", "ok", "Tests passed. Adopted.", code["generation"])
            return
        if not pytest_passed:
            log.info("tests failed for generation %s", code["generation"])
        log.info("test output:\n%s", _failure_text(output))
        # Swift: weak/pending tests cannot be fixed by code repair alone.
        if (
            swift
            and not pytest_passed
            and not spec["pinned"]
            and not swift_tests_regenerated
        ):
            sniff = sniff_swift_test_sources(
                tests["content"], check["content"], root, name
            )
            if sniff is not None:
                print(f"Tests are not usable for adopt ({sniff}).")
                log.info("adopt weak swift tests sniff=%s", sniff)
                if _regenerate_swift_tests_and_code():
                    continue
                print("Tests failed. Not adopted.")
                _note_adopt(
                    name,
                    "failed",
                    "weak_tests",
                    f"Could not rewrite weak tests ({sniff}).",
                    code["generation"],
                )
                _restore_swift_adopted()
                return
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
            _restore_swift_adopted()
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
                name=name,
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
            _restore_swift_adopted()
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


def strengthen_adopted(name, modules=None, required=None):
    """Review an adopted suite once; strengthen tests/code when thin.

    Returns one of: "adequate", "strengthened", "kept_first", "skipped".
    Soft-fail: on strengthen failure keep the already-adopted generation.
    """
    log.info("strengthen %s", name)
    if modules is None or required is None:
        from mechanicus.plan_build import dependency_modules

        modules, required, missing = dependency_modules(startup_dir(), name)
        if missing:
            log.info("strengthen skipped: missing deps for %s", name)
            return "skipped"
    required = list(required or [])
    modules = modules or []
    spec = get_spec(name)
    if spec is None or spec["pinned"]:
        return "skipped"
    code = get_adopted_artifact(spec["id"], "code")
    tests = get_adopted_artifact(spec["id"], "tests")
    check = get_adopted_artifact(spec["id"], "check")
    if (
        code is None
        or tests is None
        or check is None
        or not str(code["content"]).strip()
        or not str(tests["content"]).strip()
        or not str(check["content"]).strip()
    ):
        return "skipped"
    first_generation = code["generation"]
    behavior = spec["behavior"]
    root = startup_dir()
    swift = is_swift_package(root)

    thin = None
    if swift:
        thin = sniff_swift_test_sources(
            tests["content"], check["content"], root, name, behavior=behavior
        )
    else:
        thin = sniff_test_sources(tests["content"], check["content"])
        if thin is None:
            thin = sniff_suite_thinness(
                tests["content"], check["content"], behavior, swift=False
            )

    gaps = ""
    if thin is not None:
        print(f"Suite looks thin ({thin}). Strengthening tests...")
        log.info("strengthen sniff=%s for %s", thin, name)
        gaps = thin
    else:
        print("Reviewing suite adequacy...")
        try:
            review = review_suite_adequacy(
                behavior,
                code["content"],
                tests["content"],
                check["content"],
                name=name,
            )
        except Exception as error:
            log.exception("strengthen review failed for %s", name)
            print(f"Strengthen review failed: {error}")
            print("Keeping the first adopt.")
            _note_adopt(
                name,
                "ok",
                "strengthen_kept_first",
                f"Review failed: {error}",
                first_generation,
            )
            return "kept_first"
        if review.adequate:
            print("Suite is adequate. Keeping the first adopt.")
            log.info("strengthen adequate for %s", name)
            return "adequate"
        print(f"Suite needs strengthening ({review.reason}).")
        log.info(
            "strengthen review reason=%s gaps=%s for %s",
            review.reason,
            review.gaps,
            name,
        )
        gaps = review.gaps or review.reason

    try:
        stronger = strengthen_tests(
            behavior,
            code["content"],
            tests["content"],
            check["content"],
            gaps=gaps,
            modules=modules,
            required=required,
            name=name,
        )
    except Exception as error:
        log.exception("strengthen tests failed for %s", name)
        print(f"Could not strengthen tests: {error}")
        print("Keeping the first adopt.")
        _note_adopt(
            name,
            "ok",
            "strengthen_kept_first",
            f"Test rewrite failed: {error}",
            first_generation,
        )
        return "kept_first"

    new_tests = stronger.tests
    new_check = stronger.check
    if swift:
        sniff = sniff_swift_test_sources(
            new_tests, new_check, root, name, behavior=behavior
        )
    else:
        sniff = sniff_test_sources(new_tests, new_check)
        if sniff is None:
            sniff = sniff_suite_thinness(new_tests, new_check, behavior, swift=False)
    if sniff is not None:
        print(f"Strengthened tests still look weak ({sniff}). Keeping the first adopt.")
        _note_adopt(
            name,
            "ok",
            "strengthen_kept_first",
            f"Rewritten tests still weak ({sniff}).",
            first_generation,
        )
        return "kept_first"

    print("Writing code for the strengthened tests...")
    try:
        result = generate_code(
            behavior, new_tests, new_check, modules, required, name=name
        )
        code_text = result.code
        if swift and swift_code_is_empty(code_text):
            print("Strengthened code was empty. Keeping the first adopt.")
            _note_adopt(
                name,
                "ok",
                "strengthen_kept_first",
                "Generated code stayed empty.",
                first_generation,
            )
            return "kept_first"
        save_generation(name, behavior, code_text, new_tests, new_check)
    except Exception as error:
        log.exception("strengthen code failed for %s", name)
        print(f"Could not generate strengthened code: {error}")
        print("Keeping the first adopt.")
        _note_adopt(
            name,
            "ok",
            "strengthen_kept_first",
            f"Code rewrite failed: {error}",
            first_generation,
        )
        return "kept_first"

    print("Adopting strengthened generation...")
    adopt_spec(name, modules, required, allow_longer=True)
    spec = get_spec(name)
    adopted = None if spec is None else get_adopted_artifact(spec["id"], "code")
    if (
        spec is not None
        and adopted is not None
        and adopted["generation"] != first_generation
        and spec["adopt_status"] == "ok"
    ):
        print("Strengthened suite adopted.")
        _note_adopt(
            name,
            "ok",
            "strengthen_ok",
            "Strengthened tests and code adopted.",
            adopted["generation"],
        )
        return "strengthened"

    print("Strengthen adopt did not replace the first generation. Keeping the first adopt.")
    if spec is not None:
        current = get_adopted_artifact(spec["id"], "code")
        if current is None or current["generation"] != first_generation:
            mark_adopted(spec["id"], first_generation)
        _note_adopt(
            name,
            "ok",
            "strengthen_kept_first",
            "Strengthen adopt failed; kept first adopt.",
            first_generation,
        )
    return "kept_first"



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


def review_adopted_uses(name, required=None, swift=None):
    """Run test-gate Jev on the adopted suite for uses: deps.

    Returns:
      - [] when coverage looks fine or required is empty
      - a non-empty list of (label, probability) when weak
      - None when skipped (no key / no adopted suite) or Jev errored soft
    Persists ok/skipped/failed onto the specification when Jev runs.
    """
    required = list(required or [])
    if not required:
        return []
    spec = get_spec(name)
    if spec is None:
        return None
    tests = get_adopted_artifact(spec["id"], "tests")
    check = get_adopted_artifact(spec["id"], "check")
    if (
        tests is None
        or check is None
        or not str(tests["content"]).strip()
        or not str(check["content"]).strip()
    ):
        return None
    root = startup_dir()
    if swift is None:
        swift = is_swift_package(root)
    clear_last_jev()
    try:
        weak = weak_call_tests(
            spec["behavior"],
            tests["content"],
            check["content"],
            required,
            swift=swift,
        )
    except Exception as error:
        log.exception("jev adopted uses failed for %s", name)
        print(f"Error: {error}")
        note_jev_result("failed", "error", str(error), gate="tests")
        weak = None
    _persist_last_jev(name)
    return weak

