"""OpenAI generation for tests, code, plans, and repairs."""
from openai import OpenAI

from mechanicus.config import configured_model, configured_reasoning_effort, log
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
The behavior is one or more sentences the user can edit.
Do not write Python.

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
The behavior is one or more sentences the user can edit.
Do not write Python.

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
    elif kind == "uses":
        reason = (
            "These tests can pass when the implementation never calls a required module. "
            "The tests and the check must fail when that call is missing. "
            "They must also fail while impl.py is empty."
        )
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


def refactor_code(behavior: str, code: str, tests: str, check: str, modules=None, required=None) -> CodeResult:
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

