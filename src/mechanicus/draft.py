"""Greenfield and brownfield application-description drafting."""
import os
from pathlib import Path

from mechanicus.codegen import _request_generation
from mechanicus.config import log
from mechanicus.models import DraftSession, DraftTurn
from mechanicus.paths import write_text_file
from mechanicus.plan_build import find_module_files, plan_command


_GREENFIELD_INSTRUCTIONS = """You collaborate with the user on one application description.

The description is plain prose for a later /plan step.
Do not invent modules, uses lines, file layouts, or Python.
Ask short questions when you need detail.
Keep description as the best current draft. Leave it empty until you know enough.
read_paths must always be an empty list.
reply is what the user reads in the REPL.

Output fields:
- reply: short message to the user
- description: current application description draft
- read_paths: empty list
"""

_BROWNFIELD_INSTRUCTIONS = """You collaborate with the user on one application description for an existing codebase.

The description is plain prose for a later /plan step. It states what the application appears to do.
Do not invent modules, uses lines, or Python.
Ground answers in the inventory and in file contents you have read.
Set read_paths to relative paths from the inventory when you need more file text.
Do not request a path outside the inventory.
Keep description as the best current draft. Update it when the user corrects you or when new files change the picture.
reply is what the user reads in the REPL.

Output fields:
- reply: short message to the user
- description: current application description draft
- read_paths: relative file paths to read next, or an empty list
"""

_DRAFT_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mechanicus",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".eggs",
    ".idea",
    ".vscode",
}
_DRAFT_SKIP_SUFFIXES = (".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".bin", ".o", ".a", ".whl", ".zip", ".tar", ".gz", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".woff", ".woff2", ".ttf", ".eot")
_DRAFT_TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".rs",
    ".go",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".rb",
    ".php",
    ".css",
    ".html",
    ".htm",
    ".sql",
    ".sh",
    ".bash",
    ".zsh",
    ".rst",
    ".cfg",
    ".ini",
    ".csv",
}
_DRAFT_MAX_INVENTORY = 200
_DRAFT_MAX_FILE_BYTES = 50000
_DRAFT_MAX_READS = 5
_DRAFT_MAX_READ_ROUNDS = 3

def application_path(root):
    return Path(root).resolve() / ".mechanicus" / "application.txt"


def load_application_description(root):
    path = application_path(root)
    if not path.is_file():
        return ""
    return path.read_text().strip()


def print_description(description):
    text = description.strip()
    if not text:
        return
    print("\n--- Application description ---")
    print(text)
    print("---")


def _draft_skip_dir(name):
    return name in _DRAFT_SKIP_DIRS or name.endswith(".egg-info")


def _draft_secret_name(name):
    lower = name.lower()
    return lower == ".env" or lower.startswith(".env.")


def codebase_inventory(root):
    root = Path(root).resolve()
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name for name in dirnames if not _draft_skip_dir(name)
        )
        current = Path(dirpath)
        try:
            current.relative_to(root)
        except ValueError:
            continue
        for name in sorted(filenames):
            if _draft_secret_name(name):
                continue
            path = current / name
            suffix = path.suffix.lower()
            if suffix in _DRAFT_SKIP_SUFFIXES:
                continue
            if suffix and suffix not in _DRAFT_TEXT_SUFFIXES:
                continue
            if not suffix and name not in ("Makefile", "Dockerfile", "Procfile", "LICENSE", "README"):
                continue
            try:
                relative = path.resolve().relative_to(root).as_posix()
            except ValueError:
                continue
            if any(part in _DRAFT_SKIP_DIRS or part.endswith(".egg-info") for part in Path(relative).parts):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            entries.append((relative, size))
            if len(entries) >= _DRAFT_MAX_INVENTORY:
                break
        if len(entries) >= _DRAFT_MAX_INVENTORY:
            break
    if not entries:
        return "No text source files found under the startup directory.", 0
    lines = ["Project files (relative to the startup directory):"]
    for relative, size in entries:
        lines.append(f"- {relative} ({size} bytes)")
    if len(entries) >= _DRAFT_MAX_INVENTORY:
        lines.append(f"(Stopped after {_DRAFT_MAX_INVENTORY} files.)")
    return "\n".join(lines), len(entries)


def read_project_file(root, relative):
    root = Path(root).resolve()
    raw = str(relative).strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ".." in Path(raw).parts:
        return None, "path is not inside the startup directory"
    destination = (root / raw).resolve()
    try:
        destination.relative_to(root)
    except ValueError:
        return None, "path is not inside the startup directory"
    if any(
        part in _DRAFT_SKIP_DIRS or part.endswith(".egg-info")
        for part in destination.relative_to(root).parts
    ):
        return None, "path is skipped"
    if _draft_secret_name(destination.name):
        return None, "path is skipped"
    if not destination.is_file():
        return None, "file not found"
    try:
        size = destination.stat().st_size
    except OSError as error:
        return None, str(error)
    if size > _DRAFT_MAX_FILE_BYTES:
        return None, f"file larger than {_DRAFT_MAX_FILE_BYTES} bytes"
    try:
        text = destination.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return None, str(error)
    return text, None


def _request_draft(session, opening=False):
    chunks = []
    if session.kind == "brownfield":
        chunks.append(session.inventory or "No text source files found under the startup directory.")
        if session.reads:
            blocks = [
                f"--- {path} ---\n{text}"
                for path, text in sorted(session.reads.items())
            ]
            chunks.append("File contents:\n" + "\n\n".join(blocks))
    if session.description.strip():
        chunks.append("Current description draft:\n" + session.description.strip())
    else:
        chunks.append("Current description draft: (empty)")
    if session.transcript:
        chunks.append("Conversation:\n" + "\n".join(session.transcript))
    note = (
        "Set read_paths for files you need."
        if session.kind == "brownfield"
        else "read_paths must be empty."
    )
    if opening:
        if session.kind == "brownfield":
            chunks.append(
                "Produce an initial application description from the codebase. "
                "Set read_paths for files you need. "
                "Write reply as a short note to the user. "
                "Put the draft in description."
            )
        else:
            chunks.append(
                "Start greenfield collaboration. "
                "Ask what the user wants to build. "
                "Leave description empty until you know enough. "
                "read_paths must be empty."
            )
    elif session.transcript and "Requested files:" in session.transcript[-1]:
        chunks.append(
            "The requested file contents are available above. "
            "Continue the collaboration. "
            "Update description when the draft should change. "
            f"{note}"
        )
    else:
        chunks.append(
            "Respond to the latest user message. "
            "Update description when the draft should change. "
            f"{note}"
        )
    instructions = (
        _BROWNFIELD_INSTRUCTIONS
        if session.kind == "brownfield"
        else _GREENFIELD_INSTRUCTIONS
    )
    return _request_generation(
        "\n\n".join(chunks), instructions, DraftTurn, plan=True
    )


def draft_exchange(root, session, user_text=None, opening=False):
    if user_text:
        session.transcript.append(f"User: {user_text}")
        opening = False
    previous = session.description
    last_turn = None
    for _ in range(_DRAFT_MAX_READ_ROUNDS + 1):
        last_turn = _request_draft(session, opening=opening)
        opening = False
        if str(last_turn.description).strip():
            session.description = str(last_turn.description).strip()
        paths = []
        if session.kind == "brownfield":
            for raw in last_turn.read_paths[:_DRAFT_MAX_READS]:
                path = str(raw).strip().lstrip("./")
                if not path or path in session.reads:
                    continue
                if path not in paths:
                    paths.append(path)
        if not paths:
            break
        for path in paths:
            print(f"Reading {path}...")
            text, error = read_project_file(root, path)
            if error:
                log.info("draft read failed %s: %s", path, error)
                session.reads[path] = f"[unavailable: {error}]"
            else:
                log.info("draft read %s", path)
                session.reads[path] = text
        note = last_turn.reply.strip() or "(no reply)"
        session.transcript.append(
            f"Assistant: {note}\nRequested files: {', '.join(paths)}"
        )
    if last_turn is None:
        return session
    reply = str(last_turn.reply).strip()
    session.transcript.append(f"Assistant: {reply}")
    if reply:
        print(f"\n{reply}")
    if session.description and session.description != previous:
        print_description(session.description)
    return session


def start_greenfield(root, first_message=""):
    log.info("greenfield")
    saved = load_application_description(root)
    session = DraftSession("greenfield", description=saved)
    print("Greenfield mode. Collaborate on an application description.")
    print("Enter /accept to run /plan. Enter /abort to leave.")
    if saved:
        print_description(saved)
        print("Saved description loaded.")
    try:
        draft_exchange(
            root,
            session,
            first_message or None,
            opening=not first_message,
        )
    except Exception as error:
        log.exception("greenfield failed")
        print(f"Error: {error}")
    return session


def start_brownfield(root, first_message=""):
    log.info("brownfield")
    print("Scanning project...")
    inventory, count = codebase_inventory(root)
    log.info("brownfield inventory files=%s", count)
    saved = load_application_description(root)
    session = DraftSession(
        "brownfield",
        description=saved,
        inventory=inventory,
    )
    print("Brownfield mode. Ask about the code or refine the description.")
    print("Enter /accept to run /plan. Enter /abort to leave.")
    if count == 0:
        print("No source files found under the startup directory.")
    if saved:
        print_description(saved)
        print("Saved description loaded. A new scan can revise it.")
    try:
        draft_exchange(
            root,
            session,
            first_message or None,
            opening=not first_message,
        )
    except Exception as error:
        log.exception("brownfield failed")
        print(f"Error: {error}")
    return session


def accept_draft(root, session):
    description = session.description.strip()
    if not description:
        log.info("accept refused: empty description")
        print("No description yet.")
        return False
    print("Accepted description.")
    print_description(description)
    plan_command(root, description)
    print(
        "Enter /build after you edit the module files, "
        "or /greenfield or /brownfield to revise the description."
    )
    return True


def print_startup_hints(root):
    saved = load_application_description(root)
    has_modules = bool(find_module_files(root))
    if saved or has_modules:
        print(
            "Enter /greenfield or /brownfield to revise the application description. "
            "Enter /suggestions for numbered improvements."
        )
    else:
        print(
            "New app: /greenfield. Existing code: /brownfield. "
            "Or enter /spec or /plan when you already know the text."
        )


def draft_prompt(session):
    if session is None:
        return "> "
    return f"{session.kind}> "
