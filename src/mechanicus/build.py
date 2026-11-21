"""Build .module files into adopted code."""
from pathlib import Path

from mechanicus.activity import activity_spinner
from mechanicus.adopt import adopt_spec, review_adopted_uses, strengthen_adopted
from mechanicus.bootstrap_guard import is_bootstrap_name, refuse_bootstrap
from mechanicus.config import log
from mechanicus.lifecycle import spec_command, write_adopted
from mechanicus.paths import layout_conflict
from mechanicus.plan_build import (
    arrange_modules,
    dependency_modules,
    module_records,
    module_statuses,
    print_module_statuses,
    separate_cycles,
    spec_name_for_module,
)
from mechanicus.project import is_swift_package
from mechanicus.store import (
    deps_are_current,
    get_adopted_artifact,
    get_latest_artifact,
    get_spec,
)
from mechanicus.swift_support import is_swift_test_suite_module_name


def build_one(root, record):
    root = Path(root).resolve()
    spec_name = record["name"]
    if refuse_bootstrap(spec_name, "built"):
        return "bootstrap", "bootstrap module"
    if is_swift_package(root) and is_swift_test_suite_module_name(spec_name):
        log.info("build skipped test-suite module %s", spec_name)
        print(
            f"Skipped '{spec_name}'. "
            "Delete this .module or rename it; Mechanicus does not build /Tests modules."
        )
        return "skipped", "test-suite module"
    conflict = layout_conflict(root, spec_name)
    if conflict:
        log.info("build skipped layout %s", conflict)
        print(f"Skipped {conflict}")
        return "layout", conflict
    prose = record["behavior"]
    canonical = record["text"]
    if not prose:
        log.info("build skipped empty %s", spec_name)
        print(f"'{spec_name}' is empty.")
        return "empty", "empty behavior"
    spec = get_spec(spec_name)
    if spec is not None and spec["pinned"]:
        log.info("build skipped pinned %s", spec_name)
        print(f"'{spec_name}' is pinned. It was not changed.")
        return "pinned", "pinned"
    modules, required, missing = dependency_modules(root, spec_name)
    if missing:
        names = ", ".join(missing)
        log.info("build skipped %s uses %s", spec_name, names)
        print(f"'{spec_name}' uses {names}, which is not adopted.")
        return "blocked", f"uses {names}, which is not adopted"
    adopted = None if spec is None else get_adopted_artifact(spec["id"], "code")
    same = spec is not None and str(spec["behavior"]).strip() == canonical
    has_adopted = adopted is not None and str(adopted["content"]).strip()
    deps_ok = deps_are_current(spec_name, required)
    force_rebuild = False
    if same and has_adopted and deps_ok:
        log.info("build writes adopted %s", spec_name)
        print(f"'{spec_name}' is already adopted.")
        if not write_adopted(root, spec_name):
            return "failed", "already adopted; disk was not updated"
        if required:
            print(f"Checking uses: coverage for '{spec_name}'...")
            weak = review_adopted_uses(spec_name, required)
            if weak:
                details = ", ".join(
                    f"{dep} ({probability:.2f})" for dep, probability in weak
                )
                print(
                    f"Tests do not require these uses: {details}. Rebuilding..."
                )
                log.info("build force rebuild weak uses %s: %s", spec_name, details)
                force_rebuild = True
            else:
                return "unchanged", "already adopted"
        else:
            return "unchanged", "already adopted"
    rebuilt_upstream = False
    if same and has_adopted and not deps_ok and not force_rebuild:
        rebuilt_upstream = True
        log.info("build re-adopts stale %s", spec_name)
        print(f"'{spec_name}' has upstream changes. Re-adopting...")
        adopt_spec(spec_name, modules, required)
        spec = get_spec(spec_name)
        adopted = None if spec is None else get_adopted_artifact(spec["id"], "code")
        if (
            deps_are_current(spec_name, required)
            and adopted is not None
            and str(adopted["content"]).strip()
            and str(adopted["behavior"] or "").strip() == canonical
        ):
            strengthen_result = strengthen_adopted(spec_name, modules, required)
            strengthen_suffix = ""
            if strengthen_result == "strengthened":
                strengthen_suffix = "; strengthened"
            elif strengthen_result == "kept_first":
                strengthen_suffix = "; strengthen kept first"
            if write_adopted(root, spec_name):
                return "built", f"re-adopted after upstream change{strengthen_suffix}"
            return "failed", "re-adopted; disk was not updated"
        print(f"Building {spec_name}...")
        spec_command(spec_name, prose, modules, canonical, required)
        spec = get_spec(spec_name)
        if spec is None or str(spec["behavior"]).strip() != canonical:
            print(f"'{spec_name}' was not saved.")
            return "failed", "was not saved"
    elif not same or force_rebuild:
        print(f"Building {spec_name}...")
        prior_generation = None
        if force_rebuild and spec is not None:
            prior_latest = get_latest_artifact(spec["id"], "code")
            if prior_latest is not None:
                prior_generation = prior_latest["generation"]
        spec_command(spec_name, prose, modules, canonical, required)
        spec = get_spec(spec_name)
        if spec is None or str(spec["behavior"]).strip() != canonical:
            print(f"'{spec_name}' was not saved.")
            return "failed", "was not saved"
        if force_rebuild:
            latest = get_latest_artifact(spec["id"], "code")
            if latest is None or latest["generation"] == prior_generation:
                print(f"'{spec_name}' was not rebuilt.")
                log.info("build force rebuild saved nothing %s", spec_name)
                return "failed", "uses coverage rebuild was not saved"
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
        return "failed", "was not adopted"
    if str(adopted["behavior"] or "").strip() != canonical:
        print(f"'{spec_name}' was not adopted.")
        return "failed", "adopted behavior does not match module"
    if not deps_are_current(spec_name, required):
        print(f"'{spec_name}' was not adopted.")
        return "failed", "was not adopted"
    strengthen_suffix = ""
    strengthen_result = strengthen_adopted(spec_name, modules, required)
    if strengthen_result == "strengthened":
        strengthen_suffix = "; strengthened"
    elif strengthen_result == "kept_first":
        strengthen_suffix = "; strengthen kept first"
    if write_adopted(root, spec_name):
        if rebuilt_upstream:
            return "built", f"rebuilt after upstream change{strengthen_suffix}"
        return "built", f"adopted and wrote disk{strengthen_suffix}"
    return "failed", "adopted; disk was not updated"


def _record_for_name(records, name):
    target = spec_name_for_module(name)
    if target is None:
        return None
    for record in records:
        if record["name"] == target:
            return record
    return None


def _build_one_module(root, name):
    """Build a single .module without cycle repair or a full-graph walk."""
    log.info("build one %s", name)
    root = Path(root).resolve()
    records = module_records(root)
    if not records:
        log.info("build refused: no module files")
        print("No module files.")
        return
    record = _record_for_name(records, name)
    if record is None:
        shown = spec_name_for_module(name) or str(name).strip()
        log.info("build refused: no module %s", shown)
        print(f"No module file for '{shown}'.")
        return
    spec_name = record["name"]
    before = [row for row in module_statuses(root) if row["name"] == spec_name]
    print_module_statuses(before, heading=f"--- module {spec_name} before build ---")
    if before and before[0]["status"] == "cyclic":
        log.info("build refused: %s is cyclic", spec_name)
        print(f"'{spec_name}' is in a cycle. Enter /build to repair cycles, then build again.")
        return
    status, reason = build_one(root, record)
    log.info("build %s -> %s (%s)", spec_name, status, reason)
    after = module_statuses(root, outcomes={spec_name: (status, reason)})
    print_module_statuses(
        [row for row in after if row["name"] == spec_name],
        heading=f"--- module {spec_name} after build ---",
    )


def build_command(root, name=None):
    with activity_spinner("Building"):
        if name is not None:
            _build_one_module(root, name)
            return
        log.info("build")
        root = Path(root).resolve()
        records = module_records(root)
        if not records:
            log.info("build found no module files")
            print("No module files.")
            return
        print_module_statuses(module_statuses(root), heading="--- modules before build ---")
        if separate_cycles(root, records):
            records = module_records(root)
            print_module_statuses(
                module_statuses(root),
                heading="--- modules after cycle repair ---",
            )
        order, skipped = arrange_modules(records)
        for message in skipped:
            log.info("build %s", message)
            print(message)
        outcomes = {}
        for record in order:
            status, reason = build_one(root, record)
            outcomes[record["name"]] = (status, reason)
            log.info("build %s -> %s (%s)", record["name"], status, reason)
        print_module_statuses(
            module_statuses(root, outcomes=outcomes),
            heading="--- modules after build ---",
        )
