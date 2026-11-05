"""Plan, add, and build .module applications."""
import os
import re
from pathlib import Path

from mechanicus.codegen import (
    add_missing_modules,
    list_behaviors,
    plan_application,
    propose_feature,
    repair_cycle,
)
from mechanicus.bootstrap_guard import refuse_bootstrap
from mechanicus.config import code_root, log
from mechanicus.jev import _JEV_YES, _note_jev_skipped, feature_score, missing_behaviors
from mechanicus.lifecycle import adopt_spec, spec_command, write_adopted
from mechanicus.paths import layout_conflict, write_text_file
from mechanicus.store import get_adopted_artifact, get_latest_artifact, get_spec


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
    base = code_root(root)
    destination = (base / Path(spec_name)).with_suffix(".module").resolve()
    try:
        destination.relative_to(base)
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
    base = code_root(root)
    found = []
    if not base.is_dir():
        return found
    for path in sorted(base.rglob("*.module")):
        relative = path.resolve().relative_to(base)
        if any(part in _SKIP_DIR_NAMES or part.startswith(".") for part in relative.parts[:-1]):
            continue
        if spec_name_for_module(relative.as_posix()) is None:
            continue
        found.append(path.resolve())
    return found


def _planned_module_records(root, modules, seen_names):
    base = code_root(root)
    records = []
    for module in modules:
        spec_name, destination = module_output_path(root, module.name)
        behavior = str(module.behavior).strip()
        if spec_name is None or destination is None or not behavior:
            log.info("plan skipped %s", module.name)
            print(f"Skipped '{module.name}'.")
            continue
        conflict = layout_conflict(root, spec_name)
        if conflict and not destination.exists():
            log.info("plan skipped layout %s", conflict)
            print(f"Skipped {conflict}")
            continue
        if spec_name in seen_names:
            relative = destination.relative_to(base).as_posix()
            log.info("plan skipped duplicate %s", relative)
            print(f"Skipped duplicate '{relative}'.")
            continue
        seen_names.add(spec_name)
        uses = []
        for raw in module.uses or []:
            norm = spec_name_for_module(raw)
            if norm and norm != spec_name and norm not in uses:
                uses.append(norm)
        records.append({
            "name": spec_name,
            "path": destination,
            "relative": destination.relative_to(base).as_posix(),
            "behavior": behavior,
            "uses": uses,
            "exists": destination.exists(),
        })
    return records


def _cover_plan(root, description, normalized):
    """Add modules for behaviors Jev says the plan does not cover."""
    if not os.environ.get("TYPESAFE_API_KEY", "").strip():
        _note_jev_skipped()
        return normalized
    try:
        listed = list_behaviors(description)
    except Exception as error:
        log.exception("behavior list failed")
        print(f"Error: {error}")
        return normalized
    parts = []
    for part in listed.parts:
        text = str(part).strip()
        if text and text not in parts:
            parts.append(text)
    if not parts:
        log.info("description has no behaviors")
        return normalized
    try:
        missing = missing_behaviors(description, normalized, parts)
    except Exception as error:
        log.exception("jev plan failed")
        print(f"Error: {error}")
        print("Jev did not judge the plan.")
        return normalized
    if not missing:
        return normalized
    print("Adding the missing modules...")
    try:
        added = add_missing_modules(description, normalized, missing)
    except Exception as error:
        log.exception("add modules failed")
        print(f"Error: {error}")
        return normalized
    if str(added.explanation).strip():
        print(added.explanation.strip())
    seen = {item["name"] for item in normalized}
    extra = _planned_module_records(root, added.modules, seen)
    for item in extra:
        if item["exists"]:
            log.info("plan left %s in place", item["relative"])
            print(f"Left {item['relative']} in place.")
    return normalized + extra


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
    seen_names = set()
    normalized = _planned_module_records(root, plan.modules, seen_names)
    for item in normalized:
        if item["exists"]:
            log.info("plan left %s in place", item["relative"])
            print(f"Left {item['relative']} in place.")
    normalized = _cover_plan(root, description, normalized)
    if not normalized:
        log.info("plan recommended no modules")
        print("No modules recommended.")
        return
    on_disk = {record["name"] for record in module_records(root)}
    available = {item["name"] for item in normalized} | on_disk
    wrote = False
    for item in normalized:
        if item["exists"]:
            continue
        conflict = layout_conflict(root, item["name"])
        if conflict:
            log.info("plan refused layout %s", conflict)
            print(f"Skipped {conflict}")
            continue
        uses = []
        for use in item["uses"]:
            if use not in available:
                log.info("plan removed uses %s -> %s", item["name"], use)
                print(f"Removed uses: {use} from {item['name']}. The plan has no {use} module.")
                continue
            uses.append(use)
        text = format_module_text(uses, item["behavior"])
        write_text_file(item["path"], text)
        wrote = True
        log.info("wrote %s", item["relative"])
        print(f"\n--- {item['relative']} ---")
        print(text)
    if wrote:
        print("\nEdit the module files, then enter /build.")
    else:
        print("No new module files.")


def _merge_records(existing, updates):
    by_name = {item["name"]: item for item in existing}
    for item in updates:
        if _module_is_pinned(item["name"]):
            continue
        by_name[item["name"]] = item
    return list(by_name.values())


def _write_feature_updates(root, existing, proposal_modules, updates=None):
    if updates is None:
        updates = _planned_module_records(root, proposal_modules, set())
    names = {item["name"] for item in existing} | {item["name"] for item in updates}
    wrote = False
    for item in updates:
        if _module_is_pinned(item["name"]):
            log.info("add skipped pinned %s", item["name"])
            print(f"'{item['name']}' is pinned. It was not changed.")
            continue
        conflict = layout_conflict(root, item["name"])
        if conflict:
            log.info("add skipped layout %s", conflict)
            print(f"Skipped {conflict}")
            continue
        uses = []
        for use in item["uses"]:
            if use not in names:
                log.info("add removed uses %s -> %s", item["name"], use)
                print(f"Removed uses: {use} from {item['name']}. The plan has no {use} module.")
                continue
            uses.append(use)
        text = format_module_text(uses, item["behavior"])
        write_text_file(item["path"], text)
        wrote = True
        action = "updated" if item["exists"] else "added"
        log.info("add %s %s", action, item["relative"])
        print(f"\n--- {item['relative']} ---")
        print(text)
    return wrote


def add_command(root, feature):
    log.info("add %s", feature)
    root = Path(root).resolve()
    existing = module_records(root)
    if not existing:
        log.info("add found no module files")
        print("No module files.")
        return
    print("Adding...")
    try:
        proposal = propose_feature(feature, existing)
    except Exception as error:
        log.exception("add failed")
        print(f"Error: {error}")
        return
    if str(proposal.explanation).strip():
        print(proposal.explanation.strip())
    preview_updates = []
    try:
        preview_updates = _planned_module_records(root, proposal.modules, set())
        score = feature_score(feature, _merge_records(existing, preview_updates))
    except Exception as error:
        log.exception("jev feature failed")
        print(f"Error: {error}")
        print("Jev did not judge the feature.")
        score = None
    chosen = proposal
    if score is not None and score < _JEV_YES:
        print(f"Feature probability is {score:.2f}.")
        print("Mechanicus asks for another change.")
        try:
            chosen = propose_feature(feature, existing, preview_updates)
        except Exception as error:
            log.exception("add repair failed")
            print(f"Error: {error}")
            chosen = proposal
        if str(chosen.explanation).strip():
            print(chosen.explanation.strip())
    prepared = preview_updates if chosen is proposal else None
    if _write_feature_updates(root, existing, chosen.modules, prepared):
        print("\nEdit the module files, then enter /build.")
    else:
        print("No module changes.")


def module_records(root):
    base = code_root(root)
    records = []
    seen = set()
    for path in find_module_files(root):
        relative = path.relative_to(base).as_posix()
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
    conflict = layout_conflict(root, shared)
    if conflict:
        log.info("cycle refused layout %s", conflict)
        print(f"Skipped {conflict}")
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
    if refuse_bootstrap(spec_name, "built"):
        return
    conflict = layout_conflict(root, spec_name)
    if conflict:
        log.info("build skipped layout %s", conflict)
        print(f"Skipped {conflict}")
        return
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
