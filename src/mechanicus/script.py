"""Run Mechanicus slash commands from a text file."""
from pathlib import Path

from mechanicus.config import (
    close_session_log,
    ensure_app_files,
    log,
    start_session_log,
    startup_dir,
)
from mechanicus.console import blank
from mechanicus.cost import print_session_estimate, reset_session_cost
from mechanicus.store import init_db


def iter_script_commands(text):
    """Yield non-empty command lines. Skip blanks and # comments."""
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        yield line


def load_script_commands(path):
    """Read a script file. Return (commands, error_message)."""
    destination = Path(path).expanduser()
    if not destination.is_file():
        return None, f"Script not found: {destination}"
    try:
        text = destination.read_text(encoding="utf-8")
    except OSError as error:
        return None, f"Could not read script: {error}"
    return list(iter_script_commands(text)), None


def execute_script_commands(root, commands, log_path):
    """Run slash commands in order. Return 0 on success, 1 on stop/error."""
    from mechanicus.repl import handle_line

    draft = None
    for line in commands:
        print(f"> {line}")
        blank()
        log.info("script command %s", line)
        try:
            draft, status = handle_line(
                root, line, draft, log_path, mode="script"
            )
        except Exception as error:
            log.exception("script command failed")
            print(f"Error: {error}")
            print(f"Script stopped at: {line}")
            return 1
        if status == "quit":
            return 0
        if status == "error":
            print(f"Script stopped at: {line}")
            return 1
    return 0


def run_script(path):
    """Initialize a session, run the script file, print session cost. Return exit code."""
    root = startup_dir()
    ensure_app_files()
    init_db()
    reset_session_cost()
    log_path = start_session_log()
    commands, error = load_script_commands(path)
    try:
        if error:
            print(error)
            return 1
        shown = Path(path).expanduser()
        print(f"Running script: {shown}")
        blank()
        if not commands:
            print("Script has no commands.")
            return 0
        return execute_script_commands(root, commands, log_path)
    finally:
        close_session_log()
        print_session_estimate()
        print(f"Log file: {log_path}")
