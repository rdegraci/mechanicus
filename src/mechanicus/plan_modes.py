"""Plan modes: mvp vs full, description enrich, write .module files."""
from pathlib import Path

from mechanicus.codegen import (
    add_missing_modules,
    enrich_application_description,
    list_behaviors,
    plan_application,
)
from mechanicus.config import log
from mechanicus.console import blank, phase
from mechanicus.jev import (
    _note_jev_failed,
    _note_jev_skipped,
    description_breadth,
    missing_behaviors,
)
from mechanicus.paths import layout_conflict, write_text_file
from mechanicus.plan_build import (
    _planned_module_records,
    format_module_text,
    module_records,
)
import os


def _cover_plan(root, description, normalized):
    """Add modules for behaviors Jev says the plan does not cover."""
    if not os.environ.get("TYPESAFE_API_KEY", "").strip():
        _note_jev_skipped(gate="plan")
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
        _note_jev_failed("error", str(error), gate="plan")
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
    return normalized + extra


def parse_plan_args(text):
    """Parse `/plan` rest into (mode, description).

    mode is 'mvp' (default) or 'full'. Leading mvp/full tokens are consumed.
    """
    rest = str(text or "").strip()
    mode = "mvp"
    lowered = rest.lower()
    if lowered == "full" or lowered.startswith("full ") or lowered.startswith("full\t"):
        mode = "full"
        rest = rest[4:].strip()
    elif lowered == "mvp" or lowered.startswith("mvp ") or lowered.startswith("mvp\t"):
        mode = "mvp"
        rest = rest[3:].strip()
    return mode, rest


def _prepare_full_description(description):
    """Breadth-check and enrich for /plan full. Return planning description."""
    original = str(description).strip()
    phase("Checking description breadth for a full plan...")
    gaps = description_breadth(original)
    if gaps is None:
        phase(
            "Jev description check skipped or unavailable. "
            "Enriching from the description alone..."
        )
        gap_labels = []
    elif gaps:
        phase(
            "Description looks thin for /plan full. "
            "Enriching with implied product additions..."
        )
        gap_labels = gaps
    else:
        phase("Description looks broad enough. Enriching any remaining gaps...")
        gap_labels = []

    enriched = original
    additions = []
    for attempt in (1, 2):
        try:
            result = enrich_application_description(enriched, gap_labels)
        except Exception as error:
            log.exception("plan full enrich failed")
            print(f"Error: {error}")
            print("Continuing with the current description.")
            break
        new_additions = [
            str(item).strip()
            for item in (result.additions or [])
            if str(item).strip()
        ]
        new_text = str(result.enriched_description or "").strip() or enriched
        if str(result.explanation or "").strip():
            blank()
            print(result.explanation.strip())
        if new_additions:
            blank()
            print("Assumed additions:")
            for item in new_additions:
                print(f"  - {item}")
                if item not in additions:
                    additions.append(item)
            blank()
        elif attempt == 1:
            blank()
            print("No assumed additions.")
        enriched = new_text
        if attempt == 1:
            phase("Re-checking enriched description...")
            second = description_breadth(enriched)
            if second is None:
                break
            if not second:
                print("Enriched description looks broad enough.")
                break
            phase(
                "Enriched description still has gaps. "
                "Enriching once more..."
            )
            gap_labels = second
            continue
        break

    if additions and enriched.strip() == original:
        # Model listed additions but forgot to merge; append them.
        enriched = (
            original.rstrip()
            + "\n\nAssumed for full plan:\n"
            + "\n".join(f"- {item}" for item in additions)
        )
    return enriched, additions

def plan_command(root, description, mode="mvp"):
    from mechanicus.activity import activity_spinner

    mode_text = str(mode or "mvp").strip().lower()
    if mode_text not in ("mvp", "full"):
        print(f"Unknown /plan mode '{mode}'. Use mvp or full.")
        log.info("plan refused: unknown mode %s", mode)
        return
    with activity_spinner("Planning"):
        _plan_command_body(root, description, mode_text)


def _plan_command_body(root, description, mode_text):
    original = str(description).strip()
    log.info("plan mode=%s %s", mode_text, original)
    planning = original
    additions = []
    if mode_text == "full":
        phase("Planning full product...")
        planning, additions = _prepare_full_description(original)
    else:
        phase("Planning MVP...")
    try:
        plan = plan_application(planning, mode=mode_text)
    except Exception as error:
        log.exception("plan failed")
        print(f"Error: {error}")
        return
    root = Path(root).resolve()
    directory = root / ".mechanicus"
    directory.mkdir(parents=True, exist_ok=True)
    write_text_file(directory / "application.txt", original)
    log.info("saved application description")
    if mode_text == "full":
        write_text_file(directory / "application.full.txt", planning.strip())
        log.info("saved full application description")
        blank()
        print("Saved enriched brief to .mechanicus/application.full.txt")
        if additions:
            print("(Original description left in .mechanicus/application.txt)")
    if plan.explanation.strip():
        blank()
        print(plan.explanation.strip())
        blank()
    seen_names = set()
    normalized = _planned_module_records(root, plan.modules, seen_names)
    for item in normalized:
        if item["exists"]:
            log.info("plan left %s in place", item["relative"])
            print(f"Left {item['relative']} in place.")
    normalized = _cover_plan(root, planning, normalized)
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
        blank()
        print(f"--- {item['relative']} ---")
        print(text)
    blank()
    if wrote:
        print("Edit the module files, then enter /build.")
    else:
        print("No new module files.")
    blank()
