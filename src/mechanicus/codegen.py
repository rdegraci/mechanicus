"""OpenAI generation for tests, code, plans, and repairs."""
from openai import OpenAI

from mechanicus.config import configured_model, configured_reasoning_effort, log, startup_dir
from mechanicus.harness import _failure_text
from mechanicus.jev import _plan_state
from mechanicus.models import (
    ApplicationPlan,
    BehaviorParts,
    CodeResult,
    CycleRepair,
    TestsResult,
)
from mechanicus.paths import module_import
from mechanicus.project import is_swift_package, project_package_name
from mechanicus.swift_support import swift_type_name


_FORMATTING_RULES = """
Formatting (required):
- Use normal newlines so a human can read the file. Do not minify.
- Prefer one statement per line. Do not chain statements with semicolons.
- Put each type, function, and control-flow brace structure on separate lines.
- Put a blank line between top-level declarations (types, free functions, imports groups).
- Indent nested bodies. Keep lines reasonably short; wrap long signatures and calls.
- Prefer a small API and short logic over packing many statements onto fewer lines.
"""

_SHARED_INSTRUCTIONS = """You are the generator for a minimal specification-driven coding system.

Purpose of the system:
- Users interact through a CLI REPL.
- Behavioral specifications are stored in a SQLite database.
- Tests and a behavioral check are written before the implementation.
- The implementation is the smallest working Python that makes those tests pass.
- Generated code, tests, and the behavioral check are saved back into SQLite.
- Self-modification is allowed only through the spec → regenerate → test → explicit adopt path.
- Critical bootstrap pieces can be pinned and must not be overwritten.

Long-term aim: the system should be able to generate and improve parts of itself while remaining recoverable.

The implementation module name is impl. Pytest loads the code from impl.py.
tests and check must import that module with `import impl` or `from impl import ...`.
Do not start a subprocess to import the code.
The check calls the functions in impl from the same process.
""" + _FORMATTING_RULES

_SWIFT_SHARED_INSTRUCTIONS = """You are the generator for a minimal specification-driven coding system.

Purpose of the system:
- Users interact through a CLI REPL.
- Behavioral specifications are stored in a SQLite database.
- Tests and a behavioral check are written before the implementation.
- The implementation is the smallest working pure Swift that makes those XCTest suites pass.
- Generated code, tests, and the behavioral check are saved back into SQLite.
- Code is written into a local Swift package (Sources + Tests). No SwiftUI. No UIKit.
- Self-modification is allowed only through the spec → regenerate → test → explicit adopt path.

The package product name is given in the request. It is a legal Swift identifier (letters, digits, underscore only).
tests and check must `import XCTest` and `@testable import <PackageName>` using that exact name.
Never write a hyphenated import.
Mark public API `public` so an iOS app can import the package.
Prefer structs, enums, and pure functions. Do not use SwiftUI or UIKit.
""" + _FORMATTING_RULES


def _swift_mode(root=None):
    return is_swift_package(root if root is not None else startup_dir())


def _test_instructions(modules=None, required=None) -> str:
    has_deps = bool(modules) or bool(required)
    if has_deps:
        import_rule = (
            "You may import adopted dependency modules named in the request, "
            "in addition to impl.\n"
            "Use only the real APIs from those adopted sources. "
            "Do not invent neighbor APIs.\n"
            "The tests and the check must fail when the implementation never "
            "calls a required module, or calls it incorrectly relative to "
            "those adopted sources.\n"
        )
        tests_field = (
            "pytest unit tests that import impl "
            "(and may import adopted dependencies)"
        )
        check_field = (
            "a higher-level pytest behavioral check that imports impl "
            "(and may import adopted dependencies)"
        )
    else:
        import_rule = "Do not import any module except impl.\n"
        tests_field = "pytest unit tests that import impl"
        check_field = "a higher-level pytest behavioral check that imports impl"
    return _SHARED_INSTRUCTIONS + f"""
Write the tests before the implementation. impl.py is empty.
The tests and the check must fail because the behavior is not implemented yet.
Do not write the implementation.
{import_rule}
Output fields:
- tests: {tests_field}
- check: {check_field}
- explanation: one short paragraph
"""


def _swift_test_instructions(package_name, type_name, modules=None, required=None) -> str:
    has_deps = bool(modules) or bool(required)
    if has_deps:
        import_rule = (
            "Dependency types live in the same Swift package target. "
            "Call them by their public API from the adopted sources below. "
            "Do not invent neighbor APIs.\n"
        )
    else:
        import_rule = "Do not reference types outside this package and XCTest.\n"
    return _SWIFT_SHARED_INSTRUCTIONS + f"""
Package name: {package_name}
Primary public type name: {type_name}
Write XCTest unit tests and a higher-level XCTest behavioral check first.
The implementation file for {type_name} is an empty stub (no real behavior).
The tests and the check must fail because the behavior is not implemented yet.
Do not write the implementation.
Each of tests and check must be a complete XCTest source file:
import XCTest
@testable import {package_name}
Call the public API of {type_name} (initializers and members).
Do not use XCTFail alone to say the API is missing or unspecified.
Prefer XCTAssert* against returned values so a real implementation can pass.
A stub without the needed members should fail to compile or fail assertions.
{import_rule}
Output fields:
- tests: XCTest unit tests for {type_name}
- check: a higher-level XCTest behavioral check for {type_name}
- explanation: one short paragraph
"""


_CODE_INSTRUCTIONS = _SHARED_INSTRUCTIONS + """
The tests and the check are already written. They are fixed.
Write the smallest working implementation that makes them pass.
Keep the logic small. Do not crush statements onto one line to reduce the line count.
Do not change the tests or the check.
Call each adopted module the request names. An unused import does not count.
Do not reimplement a listed module.
When the request lists no adopted modules, do not import another project module.

Output fields:
- code: a complete readable Python implementation
- explanation: one short paragraph
"""

_SWIFT_CODE_INSTRUCTIONS = _SWIFT_SHARED_INSTRUCTIONS + """
The tests and the check are already written. They are fixed.
Write the smallest working Swift implementation that makes them pass.
Keep the logic small. Do not crush statements onto one line to reduce the line count.
Do not change the tests or the check.
Use the primary public type name from the request.
Mark public API public.
The code field must contain a complete non-empty Swift source file with normal newlines.
Do not return an empty string, comments only, or a type with no members the tests need.
Call each adopted dependency type the request names when uses: requires it.
Do not reimplement a listed dependency.
Do not use SwiftUI or UIKit.

Output fields:
- code: a complete readable Swift source file
- explanation: one short paragraph
"""

_REFACTOR_INSTRUCTIONS = _SHARED_INSTRUCTIONS + """
The tests and the check are already written. They are fixed.
Rewrite the implementation so it is easier to read and to parse.
Keep the same behavior. Do not add a feature. Do not remove a behavior.
Keep the public names the tests already call.
Prefer clear structure and names over the fewest lines.
Do not make the code verbose without a readability reason.
Do not change the tests or the check.
Call each adopted module the request names. An unused import does not count.
Do not reimplement a listed module.
When the request lists no adopted modules, do not import another project module.

Output fields:
- code: the clearer working Python implementation
- explanation: one short paragraph
"""

_SWIFT_REFACTOR_INSTRUCTIONS = _SWIFT_SHARED_INSTRUCTIONS + """
The tests and the check are already written. They are fixed.
Rewrite the Swift implementation so it is easier to read.
Keep the same behavior. Do not add a feature. Do not remove a behavior.
Keep the public names the tests already call.
Prefer clear structure and names over the fewest lines.
Do not change the tests or the check.
Do not use SwiftUI or UIKit.

Output fields:
- code: the clearer working Swift implementation
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
Do not use a top-level name that matches an existing package directory. Nest under that package instead.
Do not use a child path under a name that already exists as a top-level .py module file.
Do not use the top-level names Sources, Tests, or Package.
Do not create modules whose job is only testing, CI, or packaging manifests.
Do not name a module Tests or end a path with /Tests or /Test.
The system writes XCTest for each logic module automatically; do not plan separate test-suite modules.
Plan pure logic and contracts only. Leave SwiftUI screens, Xcode projects, and Package.swift out.
The behavior is your recommendation for that module. Write one or more sentences the user can edit.
Do not write implementation code.

Output fields:
- modules: a list of name, behavior, and uses
- explanation: one short paragraph naming the architecture
"""

_PARTS_INSTRUCTIONS = """You list the behaviors in an application description.

Split the description into separate behaviors.
Keep each behavior in the words of the description.
Do not add a behavior the description does not state.
Do not write Python.

Output fields:
- parts: one string for each behavior
- explanation: one short paragraph
"""

_ADD_INSTRUCTIONS = """You add modules for behaviors that a plan does not cover.

Add one module for each missing behavior.
Do not repeat a module name that already exists.
A new module may call a module already in the plan. List that name in uses.
The name is a relative path with no spaces and no file extension. Use slashes for directories.
Do not use a top-level name that matches an existing package directory. Nest under that package instead.
Do not use a child path under a name that already exists as a top-level .py module file.
Do not create modules named Tests or ending with /Tests.
Do not add modules whose only job is testing, CI, or Package.swift.
The behavior is one or more sentences the user can edit.
Do not write implementation code.

Output fields:
- modules: only the new modules
- explanation: one short paragraph
"""

_FEATURE_INSTRUCTIONS = """You add one feature to an existing application.

Change only the modules that must perform the feature or call a module that performs it.
Keep the current behavior of each module you change, and add the feature.
When the feature is a new job, add one new module for that job.
A module that calls the new module must name it in uses.
Do not return a module that stays the same.
Do not remove a behavior that is already in a module.
The name is a relative path with no spaces and no file extension. Use slashes for directories.
Do not use a top-level name that matches an existing package directory. Nest under that package instead.
Do not use a child path under a name that already exists as a top-level .py module file.
Do not create modules named Tests or ending with /Tests.
The behavior is one or more sentences the user can edit.
Do not write implementation code.

Output fields:
- modules: only the modules that change, and any new module
- explanation: one short paragraph
"""

_CYCLE_INSTRUCTIONS = """You split modules that use each other so a build can order them.

Add one new module for the job those modules share.
Keep the job of each listed module.
A listed module must not use another listed module.
A listed module may use the new module. Name the new module in uses when it calls that job.
The new module must not use a listed module.
The name is a relative path with no spaces and no file extension. Use slashes for directories.
Do not use a top-level name that matches an existing package directory. Nest under that package instead.
Do not use a child path under a name that already exists as a top-level .py module file.
Do not name the new module Tests or end it with /Tests.
The behavior is one or more sentences the user can edit.
Do not write implementation code.

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


def _test_modules_prompt(modules, required=None, swift=False) -> str:
    """Prompt block for test generation when adopted uses: deps are present."""
    if not modules and not required:
        return ""
    if swift:
        names = [swift_type_name(name) or name for name in (required or [])]
        lines = [
            "These types are already adopted in the same Swift package target.",
            "Use only their real APIs as written below. Do not invent neighbor APIs.",
        ]
        if names:
            lines.append(
                "Required types the implementation should use: " + ", ".join(names) + "."
            )
        for name, content in modules or []:
            shown = swift_type_name(name) or name
            lines.append(f"\nType {shown}:\n{str(content).rstrip()}")
        return "\n".join(lines) + "\n\n"
    names = [module_import(name) or name for name in (required or [])]
    lines = [
        "These modules are already adopted and available in the test harness.",
        "Use only their real APIs as written below. Do not invent neighbor APIs.",
        "You may import these dependency modules in addition to impl.",
        "Do not import other project modules.",
        "The tests and the check must fail when the implementation never calls "
        "a required module, or calls it incorrectly relative to these adopted sources.",
    ]
    if names:
        called = ", ".join(names)
        lines.append(f"Required modules the implementation must call: {called}.")
    for name, content in modules or []:
        imported = module_import(name) or name
        lines.append(f"\nModule {imported}:\n{str(content).rstrip()}")
    return "\n".join(lines) + "\n\n"


def generate_tests(behavior: str, modules=None, required=None, name=None) -> TestsResult:
    if _swift_mode():
        root = startup_dir()
        package = project_package_name(root)
        type_name = swift_type_name(name) if name else "Generated"
        if not type_name:
            type_name = "Generated"
        return _request_generation(
            "Behavioral specification:\n"
            f"{behavior}\n\n"
            f"Package name: {package}\n"
            f"Primary public type name: {type_name}\n\n"
            f"{_test_modules_prompt(modules, required, swift=True)}"
            "Write the XCTest unit tests and the behavioral check first.\n"
            f"{type_name} is an empty stub.\n"
            "The tests and the check must fail because the behavior is not implemented yet.",
            _swift_test_instructions(package, type_name, modules, required),
            TestsResult,
        )
    return _request_generation(
        "Behavioral specification:\n"
        f"{behavior}\n\n"
        f"{_test_modules_prompt(modules, required)}"
        "Write the unit tests and the behavioral check first.\n"
        "impl.py is empty.\n"
        "The tests and the check must fail because the behavior is not implemented yet.",
        _test_instructions(modules, required),
        TestsResult,
    )


def repair_tests(
    behavior: str,
    tests: str,
    check: str,
    output: str,
    kind: str,
    modules=None,
    required=None,
    name=None,
) -> TestsResult:
    if _swift_mode():
        root = startup_dir()
        package = project_package_name(root)
        type_name = swift_type_name(name) if name else "Generated"
        if not type_name:
            type_name = "Generated"
        if kind == "pass":
            reason = "swift test passed. The tests do not require the behavior."
        else:
            reason = (
                "swift test did not fail on the missing behavior. "
                "A compile success with passing asserts means the tests are wrong."
            )
        return _request_generation(
            "Behavioral specification:\n"
            f"{behavior}\n\n"
            f"Package name: {package}\n"
            f"Primary public type name: {type_name}\n\n"
            f"{_test_modules_prompt(modules, required, swift=True)}"
            f"{type_name} is an empty stub. The tests are written before the implementation.\n"
            f"{reason}\n"
            "Fix the tests and the check.\n"
            f"They must import XCTest and @testable import {package}.\n"
            "They must fail because the behavior is not implemented.\n"
            "Do not write the implementation.\n\n"
            f"Current tests:\n{tests}\n\n"
            f"Current check:\n{check}\n\n"
            f"swift test output:\n{_failure_text(output)}",
            _swift_test_instructions(package, type_name, modules, required),
            TestsResult,
        )
    if kind == "pass":
        reason = "Pytest passed. The tests do not require the behavior."
    elif kind == "uses":
        reason = (
            "These tests can pass when the implementation never calls a required module. "
            "The tests and the check must fail when that call is missing or clearly wrong "
            "relative to the adopted dependency sources. "
            "They must also fail while impl.py is empty."
        )
    else:
        reason = (
            "Pytest did not fail on the missing behavior. "
            "A collection error, a missing module, a syntax error, or a passing test means the tests are wrong."
        )
    has_deps = bool(modules) or bool(required)
    import_note = (
        "They must import impl, and may import the adopted dependency modules.\n"
        if has_deps
        else "They must import impl.\n"
    )
    return _request_generation(
        "Behavioral specification:\n"
        f"{behavior}\n\n"
        f"{_test_modules_prompt(modules, required)}"
        "impl.py is empty. The tests are written before the implementation.\n"
        f"{reason}\n"
        "Fix the tests and the check.\n"
        f"{import_note}"
        "They must fail because the behavior is not implemented.\n"
        "Do not write the implementation.\n\n"
        f"Current tests:\n{tests}\n\n"
        f"Current check:\n{check}\n\n"
        f"Pytest output:\n{_failure_text(output)}",
        _test_instructions(modules, required),
        TestsResult,
    )


def _modules_prompt(modules, required=None, swift=False) -> str:
    if not modules and not required:
        if swift:
            return "No other project types are required. Keep the API self-contained.\n\n"
        return "No other project modules are available. Do not import another project module.\n\n"
    if swift:
        names = [swift_type_name(name) or name for name in (required or [])]
        lines = ["These types are already adopted in the same package. Do not reimplement them."]
        if names:
            lines.append(
                "Use each of these types while performing the behavior: " + ", ".join(names) + "."
            )
        for name, content in modules or []:
            shown = swift_type_name(name) or name
            lines.append(f"\nType {shown}:\n{str(content).rstrip()}")
        return "\n".join(lines) + "\n\n"
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


def generate_code(behavior: str, tests: str, check: str, modules=None, required=None, name=None) -> CodeResult:
    if _swift_mode():
        root = startup_dir()
        package = project_package_name(root)
        type_name = swift_type_name(name) if name else "Generated"
        if not type_name:
            type_name = "Generated"
        return _request_generation(
            "Behavioral specification:\n"
            f"{behavior}\n\n"
            f"Package name: {package}\n"
            f"Primary public type name: {type_name}\n\n"
            f"{_modules_prompt(modules, required, swift=True)}"
            "The tests and the check are fixed.\n"
            "Write a small, readable Swift implementation that makes them pass.\n"
            "Use normal newlines; do not minify.\n\n"
            f"Tests:\n{tests}\n\n"
            f"Check:\n{check}",
            _SWIFT_CODE_INSTRUCTIONS,
            CodeResult,
        )
    return _request_generation(
        "Behavioral specification:\n"
        f"{behavior}\n\n"
        f"{_modules_prompt(modules, required)}"
        "The tests and the check are fixed.\n"
        "Write a small, readable implementation that makes them pass.\n"
        "Use normal newlines; do not minify.\n\n"
        f"Tests:\n{tests}\n\n"
        f"Check:\n{check}",
        _CODE_INSTRUCTIONS,
        CodeResult,
    )


def list_behaviors(description: str) -> BehaviorParts:
    return _request_generation(
        "Application description:\n"
        f"{description}\n\n"
        "List each behavior in this description.",
        _PARTS_INSTRUCTIONS,
        BehaviorParts,
    )


def add_missing_modules(description: str, modules, missing) -> ApplicationPlan:
    missing_lines = "\n".join(f"- {part}" for part in missing)
    return _request_generation(
        f"{_plan_state(description, modules)}\n\n"
        "These behaviors have no module:\n"
        f"{missing_lines}\n\n"
        "Add one module for each missing behavior.",
        _ADD_INSTRUCTIONS,
        ApplicationPlan,
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


def propose_feature(feature: str, modules, failed=None) -> ApplicationPlan:
    note = ""
    if failed:
        note = (
            "\n\nThe last change did not perform the feature.\n"
            f"{_plan_state(feature, failed, 'Last change')}\n\n"
            "Change the modules so that one of them performs the feature.\n"
            "Keep the current behavior of each module you change."
        )
    return _request_generation(
        f"{_plan_state(feature, modules, 'Feature')}\n\n"
        "Add this feature to the modules that should perform it.\n"
        "Keep the current behavior of each module you change.\n"
        "Return only the modules that change, and any new module."
        f"{note}",
        _FEATURE_INSTRUCTIONS,
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

def _repair_request(behavior, code, tests, check, output, modules, required, tests_passed, swift=False, name=None):
    if swift:
        root = startup_dir()
        package = project_package_name(root)
        type_name = swift_type_name(name) if name else "Generated"
        if not type_name:
            type_name = "Generated"
        if tests_passed:
            failure = (
                "swift test passed, but the code should still use the adopted dependency types.\n"
                "Keep the tests passing.\n"
                "Do not change the tests or the check.\n"
                "Keep the logic small, and keep normal newlines (do not minify).\n\n"
            )
        else:
            failure = (
                "The tests failed. The tests and the check are fixed.\n"
                "Change only the Swift code so swift test passes.\n"
                "Keep the logic small, and keep normal newlines (do not minify).\n\n"
            )
        return (
            "Behavioral specification:\n"
            f"{behavior}\n\n"
            f"Package name: {package}\n"
            f"Primary public type name: {type_name}\n\n"
            f"{_modules_prompt(modules, required, swift=True)}"
            f"{failure}"
            f"Current code:\n{code}\n\n"
            f"Tests:\n{tests}\n\n"
            f"Check:\n{check}\n\n"
            f"swift test output:\n{_failure_text(output)}"
        )
    if tests_passed:
        failure = (
            "Pytest passed.\n"
            "The code does not call every adopted module named below.\n"
            "Call each missing module while performing the behavior.\n"
            "Keep the tests passing.\n"
            "Do not change the tests or the check.\n"
            "Keep the logic small, and keep normal newlines (do not minify).\n\n"
        )
    else:
        failure = (
            "The tests failed. The tests and the check are fixed.\n"
            "Change only the code so pytest passes.\n"
            "Keep the logic small, and keep normal newlines (do not minify).\n\n"
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


def repair_code(
    behavior: str,
    code: str,
    tests: str,
    check: str,
    output: str,
    modules=None,
    required=None,
    tests_passed=False,
    name=None,
) -> CodeResult:
    swift = _swift_mode()
    return _request_generation(
        _repair_request(
            behavior, code, tests, check, output, modules, required, tests_passed, swift, name
        ),
        _SWIFT_CODE_INSTRUCTIONS if swift else _CODE_INSTRUCTIONS,
        CodeResult,
    )


def refactor_code(
    behavior: str, code: str, tests: str, check: str, modules=None, required=None, name=None
) -> CodeResult:
    if _swift_mode():
        root = startup_dir()
        package = project_package_name(root)
        type_name = swift_type_name(name) if name else "Generated"
        if not type_name:
            type_name = "Generated"
        return _request_generation(
            "Behavioral specification:\n"
            f"{behavior}\n\n"
            f"Package name: {package}\n"
            f"Primary public type name: {type_name}\n\n"
            f"{_modules_prompt(modules, required, swift=True)}"
            "The tests and the check are fixed.\n"
            "Rewrite the Swift implementation so it is easier to read.\n"
            "Keep the same behavior.\n\n"
            f"Current code:\n{code}\n\n"
            f"Tests:\n{tests}\n\n"
            f"Check:\n{check}",
            _SWIFT_REFACTOR_INSTRUCTIONS,
            CodeResult,
        )
    return _request_generation(
        "Behavioral specification:\n"
        f"{behavior}\n\n"
        f"{_modules_prompt(modules, required)}"
        "The tests and the check are fixed.\n"
        "Rewrite the implementation so it is easier to read.\n"
        "Keep the same behavior.\n\n"
        f"Current code:\n{code}\n\n"
        f"Tests:\n{tests}\n\n"
        f"Check:\n{check}",
        _REFACTOR_INSTRUCTIONS,
        CodeResult,
    )

