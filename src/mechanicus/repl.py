"""Mechanicus REPL."""
from mechanicus.config import close_session_log, ensure_app_files, log, start_session_log, startup_dir
from mechanicus.draft import (
    accept_draft,
    draft_exchange,
    draft_prompt,
    load_application_description,
    print_startup_hints,
    start_brownfield,
    start_greenfield,
)
from mechanicus.lifecycle import (
    adopt_spec,
    generate_command,
    list_specs,
    refactor_command,
    revert_spec,
    run_adopted,
    show_spec,
    spec_command,
    test_command,
)
from mechanicus.plan_build import add_command, build_command, plan_command
from mechanicus.seed_import import import_source
from mechanicus.store import init_db, pin_spec, unpin_spec


def repl():
    root = startup_dir()
    ensure_app_files()
    init_db()
    log_path = start_session_log()
    draft = None
    try:
        print("Seed REPL. Commands:")
        print("  /greenfield [note]          - draft an application description for a new app")
        print("  /brownfield [note]          - draft a description from the codebase")
        print("  /accept                     - save the draft, exit drafting, and run /plan")
        print("  /abort                      - leave drafting without planning")
        print("  /spec <name> <behavior>     - store a spec, write failing tests, then generate code")
        print("  /plan [description]         - write a .module file for each recommended module")
        print("  /add <feature>              - add a feature to the modules that perform it")
        print("  /build                      - build each .module file and write the adopted code")
        print("  /import <name>              - import package source as adopted and pinned")
        print("  /specs                      - list specifications with pin and adopted generation")
        print("  /refactor <name>            - rewrite adopted code for clarity; tests stay fixed")
        print("  /pin <name>                 - pin a spec so it cannot be regenerated or overwritten")
        print("  /unpin <name>               - clear that pin")
        print("  /adopt <name>               - run tests, repair the code, and adopt when they pass")
        print("  /revert <name> <generation> - adopt an earlier generation without generating")
        print("  /show <name>                - show the spec, the adopted rendering, and any newer candidate")
        print("  /generate [name]            - write adopted code and tests into the startup directory")
        print("  /test [name]                - run on-disk pytest with PYTHONPATH=startup")
        print("  /run <name>                 - run the adopted code for a spec")
        print("  /logfile                    - print the path to the session log file")
        print("  /quit                       - exit the REPL")
        print("  /exit                       - exit the REPL")
        print_startup_hints(root)
        while True:
            try:
                line = input(draft_prompt(draft)).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            log.info("command %s", line)
            if line in ("/quit", "/exit"):
                break

            if draft is not None:
                if line == "/abort":
                    log.info("abort %s", draft.kind)
                    draft = None
                    print("Left without saving a new description.")
                    continue
                if line == "/accept":
                    log.info("accept %s", draft.kind)
                    if accept_draft(root, draft):
                        draft = None
                    continue
                if line.startswith("/"):
                    log.info("draft refused other command")
                    print("Enter /accept to plan, /abort to leave, or /quit to stop.")
                    continue
                print("Thinking...")
                try:
                    draft_exchange(root, draft, line)
                except Exception as error:
                    log.exception("draft turn failed")
                    print(f"Error: {error}")
                continue

            parts = line.split(maxsplit=2)
            cmd = parts[0]

            if cmd == "/greenfield":
                note = line.split(maxsplit=1)[1].strip() if " " in line else ""
                draft = start_greenfield(root, note)
            elif cmd == "/brownfield":
                note = line.split(maxsplit=1)[1].strip() if " " in line else ""
                draft = start_brownfield(root, note)
            elif cmd in ("/accept", "/abort"):
                log.info("%s refused: not drafting", cmd)
                print("Enter /greenfield or /brownfield first.")
            elif cmd == "/spec" and len(parts) == 3:
                spec_command(parts[1], parts[2])
            elif cmd == "/plan":
                description = line.split(maxsplit=1)[1].strip() if " " in line else ""
                if not description:
                    description = load_application_description(root)
                if not description:
                    log.info("plan refused: no description")
                    print("Enter an application description, or /greenfield or /brownfield.")
                else:
                    plan_command(root, description)
            elif cmd == "/add":
                feature = line.split(maxsplit=1)[1].strip() if " " in line else ""
                if not feature:
                    log.info("add refused: no feature")
                    print("Enter a feature.")
                else:
                    add_command(root, feature)
            elif cmd == "/build" and len(parts) == 1:
                build_command(root)
            elif cmd == "/import" and len(parts) == 2:
                import_source(root, parts[1])
            elif cmd == "/specs" and len(parts) == 1:
                list_specs()
            elif cmd == "/refactor" and len(parts) == 2:
                refactor_command(parts[1])
            elif cmd == "/pin" and len(parts) == 2:
                if pin_spec(parts[1]):
                    log.info("pinned %s", parts[1])
                    print(f"Pinned '{parts[1]}'.")
                else:
                    log.info("pin refused: spec not found")
                    print("Spec not found.")
            elif cmd == "/unpin" and len(parts) == 2:
                if unpin_spec(parts[1]):
                    log.info("unpinned %s", parts[1])
                    print(f"Unpinned '{parts[1]}'.")
                else:
                    log.info("unpin refused: spec not found")
                    print("Spec not found.")
            elif cmd == "/adopt" and len(parts) == 2:
                adopt_spec(parts[1])
            elif cmd == "/revert" and len(parts) == 3:
                try:
                    generation = int(parts[2])
                except ValueError:
                    log.info("revert refused: generation is not an integer")
                    print("Generation must be an integer.")
                    continue
                revert_spec(parts[1], generation)
            elif cmd == "/show" and len(parts) == 2:
                show_spec(parts[1])
            elif cmd == "/generate" and len(parts) == 1:
                generate_command(root)
            elif cmd == "/generate" and len(parts) == 2:
                generate_command(root, parts[1])
            elif cmd == "/test" and len(parts) == 1:
                test_command(root)
            elif cmd == "/test" and len(parts) == 2:
                test_command(root, parts[1])
            elif cmd == "/run" and len(parts) == 2:
                run_adopted(parts[1])
            elif cmd == "/logfile" and len(parts) == 1:
                print(f"Log file: {log_path}")
            else:
                log.info("unknown command %s", cmd)
                print("Unknown command.")
    finally:
        close_session_log()
        print(f"Log file: {log_path}")

