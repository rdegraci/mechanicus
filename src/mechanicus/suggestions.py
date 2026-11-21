"""Analyze a codebase and suggest improvements, bugs, and issues."""
import os
import re
from pathlib import Path

from mechanicus.codegen import _request_generation
from mechanicus.config import log
from mechanicus.draft import (
    codebase_inventory,
    load_application_description,
    read_project_file,
)
from mechanicus.models import SuggestionsResult
from mechanicus.paths import write_text_file
from mechanicus.plan_build import find_module_files, module_records

_IMPROVEMENT_TAGS = ("feature", "integrate", "docs", "packaging")
_BUG_TAGS = ("bug", "issue")
_ALL_TAGS = _IMPROVEMENT_TAGS + _BUG_TAGS
_MAX_IMPROVEMENTS = 7
_MAX_BUGS = 7
_MAX_CONTEXT_CHARS = 24000

_SUGGESTIONS_INSTRUCTIONS = """You analyze an existing codebase for a specification-driven tool.

Infer the purpose of the project from the inventory and any file text provided.
Return two lists grounded in what you see. Do not invent files that are not implied.

suggestions: improvements and enhancements (1 to 7 items).
Allowed tags for suggestions: feature, integrate, docs, packaging.
Prefer integrate when layers exist but are not wired.
Prefer feature when behavior is missing.
Prefer docs or packaging when the gap is documentation or install/run layout.

bugs: defects, broken wiring, mismatches, and concrete risks (0 to 7 items).
Allowed tags for bugs: bug, issue.
Prefer bug when behavior is wrong or incomplete relative to the code and specs.
Prefer issue when something is inconsistent, unsafe, or likely to fail at runtime.
Do not repeat the same item in both lists.

Each item has a short title, one detail sentence, and a tag.
Do not write Python.
Do not propose rewriting the whole application.

Output fields:
- purpose: one short sentence
- suggestions: list of title, detail, and tag
- bugs: list of title, detail, and tag
- explanation: one short paragraph
"""

_SESSION = None


def suggestions_path(root):
    return Path(root).resolve() / ".mechanicus" / "suggestions.txt"


def _normalize_tag(tag, allowed, default):
    text = str(tag or "").strip().lower()
    if text in allowed:
        return text
    return default


def _item_text(item):
    title = str(item.get("title", "")).strip()
    detail = str(item.get("detail", "")).strip()
    if title and detail:
        return f"{title}. {detail}"
    return title or detail


def _normalize_raw(raw, allowed, default_tag, kind):
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    item = {
        "title": str(raw.get("title", "")).strip(),
        "detail": str(raw.get("detail", "")).strip(),
        "tag": _normalize_tag(raw.get("tag"), allowed, default_tag),
        "kind": kind,
    }
    item["text"] = _item_text(item)
    return item


def _store_items(purpose, suggestions, bugs):
    global _SESSION
    items = []
    index = 1
    for raw in list(suggestions or [])[:_MAX_IMPROVEMENTS]:
        item = _normalize_raw(raw, _IMPROVEMENT_TAGS, "feature", "suggestion")
        if not item["text"]:
            continue
        item["index"] = index
        items.append(item)
        index += 1
    for raw in list(bugs or [])[:_MAX_BUGS]:
        item = _normalize_raw(raw, _BUG_TAGS, "bug", "bug")
        if not item["text"]:
            continue
        item["index"] = index
        items.append(item)
        index += 1
    _SESSION = {"purpose": str(purpose).strip(), "suggestions": items}
    return _SESSION


def _format_file(purpose, suggestions):
    lines = [f"Purpose: {purpose}", ""]
    improvements = [item for item in suggestions if item.get("kind") != "bug"]
    bugs = [item for item in suggestions if item.get("kind") == "bug"]
    if improvements:
        lines.append("Suggestions:")
        for item in improvements:
            lines.append(f"{item['index']}. [{item['tag']}] {item['title']}")
            if item["detail"]:
                lines.append(f"   {item['detail']}")
        lines.append("")
    if bugs:
        lines.append("Bugs and issues:")
        for item in bugs:
            lines.append(f"{item['index']}. [{item['tag']}] {item['title']}")
            if item["detail"]:
                lines.append(f"   {item['detail']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _parse_file(text):
    purpose = ""
    suggestions = []
    match = re.search(r"(?m)^Purpose:\s*(.*)$", text)
    if match:
        purpose = match.group(1).strip()
    kind = "suggestion"
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line == "Suggestions:":
            kind = "suggestion"
            continue
        if line == "Bugs and issues:":
            kind = "bug"
            continue
        found = re.match(r"^(\d+)\.\s*\[(\w+)\]\s*(.+)$", line)
        if found:
            tag = found.group(2).lower()
            item_kind = "bug" if tag in _BUG_TAGS or kind == "bug" else "suggestion"
            allowed = _BUG_TAGS if item_kind == "bug" else _IMPROVEMENT_TAGS
            default = "bug" if item_kind == "bug" else "feature"
            suggestions.append({
                "index": int(found.group(1)),
                "tag": _normalize_tag(tag, allowed, default),
                "title": found.group(3).strip(),
                "detail": "",
                "kind": item_kind,
            })
            continue
        if suggestions and line.startswith("   "):
            suggestions[-1]["detail"] = line.strip()
    if not suggestions:
        return None
    suggestions.sort(key=lambda item: item["index"])
    for index, item in enumerate(suggestions, start=1):
        item["index"] = index
        item["text"] = _item_text(item)
    return {"purpose": purpose, "suggestions": suggestions}


def save_suggestions(root, payload):
    path = suggestions_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_file(
        path,
        _format_file(payload["purpose"], payload["suggestions"]),
    )
    log.info("saved suggestions %s", path)


def load_suggestions(root):
    global _SESSION
    if _SESSION and _SESSION.get("suggestions"):
        return _SESSION
    path = suggestions_path(root)
    if not path.is_file():
        return None
    parsed = _parse_file(path.read_text())
    if parsed is None:
        return None
    _SESSION = parsed
    return _SESSION


def get_suggestion(root, index):
    payload = load_suggestions(root)
    if payload is None or not payload.get("suggestions"):
        return None, "No suggestions yet. Enter /suggestions first."
    for item in payload["suggestions"]:
        if item["index"] == index:
            return item, None
    return None, "Unknown suggestion number. Enter /suggestions again."


def _context_block(root):
    chunks = []
    inventory, count = codebase_inventory(root)
    chunks.append(inventory)
    log.info("suggestions inventory files=%s", count)
    saved = load_application_description(root)
    if saved:
        chunks.append("Application description:\n" + saved)
    records = module_records(root)
    if records:
        lines = ["Module files:"]
        for record in records:
            lines.append(f"--- {record['name']} ---\n{record['text']}")
        chunks.append("\n\n".join(lines))
    for relative in ("README.md", "README.txt", "pyproject.toml"):
        text, error = read_project_file(root, relative)
        if error or not text:
            continue
        chunks.append(f"--- {relative} ---\n{text[:8000]}")
    modules = find_module_files(root)
    for path in modules[:20]:
        try:
            relative = path.relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            continue
        text, error = read_project_file(root, relative)
        if error or not text:
            continue
        chunks.append(f"--- {relative} ---\n{text[:4000]}")
    # Sample a few .py sources for bug finding
    root_path = Path(root).resolve()
    sampled = 0
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in {".git", ".venv", "venv", "__pycache__", ".mechanicus", "tests"}
        ]
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            path = Path(dirpath) / name
            try:
                relative = path.relative_to(root_path).as_posix()
            except ValueError:
                continue
            if relative.endswith(".module"):
                continue
            text, error = read_project_file(root, relative)
            if error or not text:
                continue
            chunks.append(f"--- {relative} ---\n{text[:6000]}")
            sampled += 1
            if sampled >= 12:
                break
        if sampled >= 12:
            break
    joined = "\n\n".join(chunks)
    if len(joined) > _MAX_CONTEXT_CHARS:
        return joined[:_MAX_CONTEXT_CHARS] + "\n\n(Truncated.)"
    return joined


def _print_section(title, items):
    if not items:
        return
    print(f"\n{title}")
    for item in items:
        print(f"{item['index']}. [{item['tag']}] {item['title']}")
        if item["detail"]:
            print(f"   {item['detail']}")


def suggestions_command(root):
    from mechanicus.activity import activity_spinner

    log.info("suggestions")
    print("Analyzing project...")
    with activity_spinner("Analyzing"):
        context = _context_block(root)
        try:
            result = _request_generation(
                "Codebase context:\n"
                f"{context}\n\n"
                "State the purpose. List improvements. List bugs and issues.",
                _SUGGESTIONS_INSTRUCTIONS,
                SuggestionsResult,
                plan=True,
            )
        except Exception as error:
            log.exception("suggestions failed")
            print(f"Error: {error}")
            return
    payload = _store_items(result.purpose, result.suggestions, result.bugs)
    if not payload["suggestions"]:
        log.info("suggestions empty")
        print("No suggestions or bugs returned.")
        return
    save_suggestions(root, payload)
    print(f"\nPurpose: {payload['purpose']}")
    if str(result.explanation).strip():
        print(result.explanation.strip())
    improvements = [item for item in payload["suggestions"] if item.get("kind") != "bug"]
    bugs = [item for item in payload["suggestions"] if item.get("kind") == "bug"]
    _print_section("Suggestions:", improvements)
    _print_section("Bugs and issues:", bugs)
    if not improvements and not bugs:
        print("\nNo items.")
    print("\nEnter /brownfield <n> or /add <n> (when module files exist).")
