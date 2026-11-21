"""Mechanicus REPL."""
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory

from mechanicus.config import (
    app_dir,
    close_session_log,
    ensure_app_files,
    log,
    start_session_log,
    startup_dir,
)
from mechanicus.draft import (
    accept_draft,
    draft_exchange,
    draft_prompt,
    load_application_description,
    print_startup_hints,
    start_brownfield,
    start_greenfield,
)
from mechanicus.clean import clean_command
from mechanicus.console import blank
from mechanicus.cost import print_cost_status, print_session_estimate, reset_session_cost
from mechanicus.lifecycle import (
    adopt_spec,
    generate_command,
    list_specs,
    refactor_command,
    revert_spec,
    run_adopted,
    show_spec,
    spec_command,
    target_command,
    test_command,
)
from mechanicus.project import load_project
from mechanicus.plan_build import (
    add_command,
    build_command,
    find_module_files,
    modules_command,
    parse_plan_args,
    plan_command,
)
from mechanicus.seed_import import import_source
from mechanicus.store import init_db, pin_spec, unpin_spec
from mechanicus.suggestions import get_suggestion, suggestions_command


_COMMAND_HELP = (
    (
        "Draft",
        (
            ("/greenfield [note]", "Draft an application description for a new app."),
            ("/brownfield [note|n]", "Draft from the codebase, or open suggestion n."),
            ("/suggestions", "Analyze the tree and list numbered improvements."),
            ("/accept", "Save the draft, leave drafting, and run /plan."),
            ("/abort", "Leave drafting without planning."),
        ),
    ),
    (
        "Plan and build",
        (
            ("/target [python|swift-package] [Name]", "Show or set the project language target."),
            ("/plan [mvp|full] [description]", "Plan modules; mvp default, full enriches."),
            ("/add <feature|n>", "Add a feature to modules, or apply suggestion n."),
            ("/modules", "List .module files with build status and disk sync."),
            ("/build", "Build each .module file and write the adopted code."),
            ("/build <name>", "Build one .module (dependencies must already be adopted)."),
        ),
    ),
    (
        "Spec lifecycle",
        (
            ("/spec <name> <behavior>", "Store a spec, write failing tests, then generate code."),
            ("/adopt <name>", "Run tests, repair the code, and adopt when they pass."),
            ("/show <name>", "Show the spec, sync, last adopt, last Jev, rendering, and candidate."),
            ("/specs", "List specs with pin, adopted generation, sync, adopt, and Jev."),
            ("/pin <name>", "Pin a spec so it cannot be regenerated or overwritten."),
            ("/unpin <name>", "Clear that pin."),
            ("/revert <name> <generation> [--db-only]", "Adopt an earlier generation; write disk unless --db-only."),
            ("/refactor <name>", "Rewrite adopted code for clarity; tests stay fixed."),
        ),
    ),
    (
        "Disk and run",
        (
            ("/import <name>", "Import package source as adopted and pinned."),
            ("/generate [name]", "Write adopted code and tests into the startup directory."),
            ("/clean [build|orphans|disk|all]", "Safe cleanup; no args prints help."),
            ("/test [name]", "Run on-disk pytest or swift test for the project target."),
            ("/run <name>", "Run the adopted code for a Python spec."),
        ),
    ),
    (
        "Session",
        (
            ("/script <path>", "Run slash commands from a text file."),
            ("/cost", "Print the OpenAI session cost estimate and turn count."),
            ("/logfile", "Print the path to the session log file."),
            ("/help", "List commands and a short description of each."),
            ("/quit", "Exit the REPL."),
            ("/exit", "Exit the REPL."),
        ),
    ),
)

_SCRIPT_REFUSED = frozenset({
    "/greenfield",
    "/brownfield",
    "/accept",
    "/abort",
    "/script",
})


def _suggestion_index(text):
    raw = str(text).strip()
    if raw.isdigit():
        return int(raw)
    return None


def _help_entries():
    for _group, entries in _COMMAND_HELP:
        for command, blurb in entries:
            yield command, blurb


def _slash_commands():
    commands = set()
    for command, _blurb in _help_entries():
        commands.add(command.split()[0])
    return sorted(commands)


def _make_prompt_session():
    history_path = app_dir() / "repl_history"
    completer = WordCompleter(_slash_commands(), ignore_case=False, sentence=True)
    return PromptSession(
        history=FileHistory(str(history_path)),
        completer=completer,
        complete_while_typing=False,
    )


def print_help():
    print("Commands:")
    width = max(len(command) for command, _blurb in _help_entries())
    for index, (group, entries) in enumerate(_COMMAND_HELP):
        if index:
            print()
        print(group)
        for command, blurb in entries:
            print(f"  {command:<{width}}  {blurb}")
    print()
    print(
        "Line editing uses prompt_toolkit. "
        "Up/Down recall history. Tab completes slash commands."
    )


def handle_line(root, line, draft=None, log_path=None, *, mode="repl"):
    """Process one REPL or script line.

    Returns (draft, status) where status is "ok", "quit", or "error".
    Script mode refuses interactive draft commands and stops on unknowns.
    """
    script = mode == "script"
    text = str(line or "").strip()
    if not text:
        return draft, "ok"
    if text in ("/quit", "/exit"):
        return draft, "quit"

    if script:
        if not text.startswith("/"):
            print("Scripts only accept slash commands.")
            return draft, "error"
        cmd_name = text.split(maxsplit=1)[0]
        if cmd_name in _SCRIPT_REFUSED:
            print(
                f"'{cmd_name}' is interactive. "
                "Use /plan, /build, and other non-draft commands in scripts."
            )
            return draft, "error"

    if draft is not None:
        if text == "/abort":
            log.info("abort %s", draft.kind)
            print("Left without saving a new description.")
            return None, "ok"
        if text == "/accept":
            log.info("accept %s", draft.kind)
            if accept_draft(root, draft):
                return None, "ok"
            return draft, "ok"
        if text == "/help":
            log.info("help")
            print_help()
            return draft, "ok"
        if text.startswith("/"):
            log.info("draft refused other command")
            print(
                "Enter /accept to plan, /abort to leave, "
                "/help for commands, or /quit to stop."
            )
            return draft, "error" if script else "ok"
        print("Thinking...")
        try:
            draft_exchange(root, draft, text)
        except Exception as error:
            log.exception("draft turn failed")
            print(f"Error: {error}")
            return draft, "error" if script else "ok"
        return draft, "ok"

    parts = text.split(maxsplit=2)
    cmd = parts[0]

    if cmd == "/greenfield":
        note = text.split(maxsplit=1)[1].strip() if " " in text else ""
        return start_greenfield(root, note), "ok"
    if cmd == "/brownfield":
        note = text.split(maxsplit=1)[1].strip() if " " in text else ""
        index = _suggestion_index(note) if note else None
        if index is not None:
            item, error = get_suggestion(root, index)
            if error:
                log.info("brownfield refused: %s", error)
                print(error)
                return draft, "error" if script else "ok"
            print(f"Using suggestion {index}: {item['text']}")
            return start_brownfield(root, item["text"]), "ok"
        return start_brownfield(root, note), "ok"
    if cmd == "/suggestions" and len(parts) == 1:
        suggestions_command(root)
        return draft, "ok"
    if cmd in ("/accept", "/abort"):
        log.info("%s refused: not drafting", cmd)
        print("Enter /greenfield or /brownfield first.")
        return draft, "error" if script else "ok"
    if cmd == "/script" and len(parts) == 2:
        from mechanicus.script import execute_script_commands, load_script_commands

        commands, error = load_script_commands(parts[1])
        if error:
            print(error)
            return draft, "error" if script else "ok"
        print(f"Running script: {parts[1]}")
        blank()
        code = execute_script_commands(root, commands or [], log_path)
        if code != 0:
            return draft, "error" if script else "ok"
        return draft, "ok"
    if cmd == "/spec" and len(parts) == 3:
        spec_command(parts[1], parts[2])
        return draft, "ok"
    if cmd == "/target" and len(parts) == 1:
        target_command(root)
        return draft, "ok"
    if cmd == "/target" and len(parts) in (2, 3):
        package = parts[2] if len(parts) == 3 else ""
        target_command(root, parts[1], package)
        return draft, "ok"
    if cmd == "/plan":
        rest = text.split(maxsplit=1)[1].strip() if " " in text else ""
        mode_name, description = parse_plan_args(rest)
        if not description:
            description = load_application_description(root)
        if not description:
            log.info("plan refused: no description")
            print("Enter an application description, or /greenfield or /brownfield.")
            return draft, "error" if script else "ok"
        plan_command(root, description, mode=mode_name)
        return draft, "ok"
    if cmd == "/add":
        feature = text.split(maxsplit=1)[1].strip() if " " in text else ""
        if not feature:
            log.info("add refused: no feature")
            print("Enter a feature, or /add <n> after /suggestions.")
            return draft, "error" if script else "ok"
        index = _suggestion_index(feature)
        if index is not None:
            if not find_module_files(root):
                log.info("add refused: no module files for suggestion")
                print("No module files. Enter /brownfield <n> or /plan first.")
                return draft, "error" if script else "ok"
            item, error = get_suggestion(root, index)
            if error:
                log.info("add refused: %s", error)
                print(error)
                return draft, "error" if script else "ok"
            print(f"Using suggestion {index}: {item['text']}")
            add_command(root, item["text"])
        else:
            add_command(root, feature)
        return draft, "ok"
    if cmd == "/build" and len(parts) == 1:
        build_command(root)
        return draft, "ok"
    if cmd == "/build" and len(parts) == 2:
        build_command(root, parts[1])
        return draft, "ok"
    if cmd == "/modules" and len(parts) == 1:
        modules_command(root)
        return draft, "ok"
    if cmd == "/import" and len(parts) == 2:
        import_source(root, parts[1])
        return draft, "ok"
    if cmd == "/specs" and len(parts) == 1:
        list_specs(root)
        return draft, "ok"
    if cmd == "/refactor" and len(parts) == 2:
        refactor_command(parts[1])
        return draft, "ok"
    if cmd == "/pin" and len(parts) == 2:
        if pin_spec(parts[1]):
            log.info("pinned %s", parts[1])
            print(f"Pinned '{parts[1]}'.")
        else:
            log.info("pin refused: spec not found")
            print("Spec not found.")
            return draft, "error" if script else "ok"
        return draft, "ok"
    if cmd == "/unpin" and len(parts) == 2:
        if unpin_spec(parts[1]):
            log.info("unpinned %s", parts[1])
            print(f"Unpinned '{parts[1]}'.")
        else:
            log.info("unpin refused: spec not found")
            print("Spec not found.")
            return draft, "error" if script else "ok"
        return draft, "ok"
    if cmd == "/adopt" and len(parts) == 2:
        adopt_spec(parts[1])
        return draft, "ok"
    if cmd == "/revert" and len(parts) in (3, 4):
        try:
            generation = int(parts[2])
        except ValueError:
            log.info("revert refused: generation is not an integer")
            print("Generation must be an integer.")
            return draft, "error" if script else "ok"
        write_disk = True
        if len(parts) == 4:
            if parts[3] != "--db-only":
                log.info("revert refused: unknown option %s", parts[3])
                print("Unknown option. Use --db-only to skip writing disk.")
                return draft, "error" if script else "ok"
            write_disk = False
        revert_spec(parts[1], generation, root, write_disk)
        return draft, "ok"
    if cmd == "/show" and len(parts) == 2:
        show_spec(parts[1], root)
        return draft, "ok"
    if cmd == "/generate" and len(parts) == 1:
        generate_command(root)
        return draft, "ok"
    if cmd == "/generate" and len(parts) == 2:
        generate_command(root, parts[1])
        return draft, "ok"
    if cmd == "/clean" and len(parts) == 1:
        clean_command(root)
        return draft, "ok"
    if cmd == "/clean" and len(parts) in (2, 3):
        apply = False
        if len(parts) == 3:
            if parts[2] != "--apply":
                log.info("clean refused: unknown option %s", parts[2])
                print("Unknown option. Use --apply only with /clean orphans.")
                return draft, "error" if script else "ok"
            apply = True
        clean_command(root, parts[1], apply=apply)
        return draft, "ok"
    if cmd == "/test" and len(parts) == 1:
        test_command(root)
        return draft, "ok"
    if cmd == "/test" and len(parts) == 2:
        test_command(root, parts[1])
        return draft, "ok"
    if cmd == "/run" and len(parts) == 2:
        run_adopted(parts[1])
        return draft, "ok"
    if cmd == "/cost" and len(parts) == 1:
        print_cost_status()
        return draft, "ok"
    if cmd == "/logfile" and len(parts) == 1:
        print(f"Log file: {log_path}")
        return draft, "ok"
    if cmd == "/help" and len(parts) == 1:
        print_help()
        return draft, "ok"

    log.info("unknown command %s", cmd)
    print("Unknown command. Enter /help for the command list.")
    return draft, "error" if script else "ok"


def repl():
    root = startup_dir()
    ensure_app_files()
    init_db()
    reset_session_cost()
    log_path = start_session_log()
    prompt = _make_prompt_session()
    draft = None
    try:
        print("Seed REPL.")
        blank()
        print_help()
        settings = load_project(root)
        if settings["target"] != "python":
            blank()
            print(
                f"Project target: {settings['target']} "
                f"({settings.get('package_name') or settings.get('package_root')})"
            )
        blank()
        print_startup_hints(root)
        blank()
        while True:
            try:
                line = prompt.prompt(draft_prompt(draft)).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            log.info("command %s", line)
            draft, status = handle_line(root, line, draft, log_path, mode="repl")
            if status == "quit":
                break
    finally:
        close_session_log()
        print_session_estimate()
        print(f"Log file: {log_path}")
