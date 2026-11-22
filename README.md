# Mechanicus

Mechanicus is a specification-driven coding REPL for Python 3.10 and later. You describe a behavior, and Mechanicus stores it and writes the tests and the check first. Those tests must fail against an empty module. Mechanicus then asks the model for the smallest working Python that makes the tests pass, and it adopts a generation only after pytest passes.

Each project keeps its own history in `.mechanicus/seed.db` in the directory where you start Mechanicus. The interactive session uses prompt_toolkit, so Up and Down recall history and Tab completes slash commands.

## Requirements

You need Python 3.10 or later. You need an OpenAI API key for every command that calls a model. A TypeSafe API key is optional, and without it Mechanicus skips the Jev checks described under Configure. A Swift package target also needs `swift` on your `PATH`.

## Install

1. Go to the Mechanicus repository.
2. Create a virtual environment with `python3 -m venv .venv`.
3. Install the package with `.venv/bin/pip install -e .`.

The install adds a `mechanicus` command to the virtual environment. You can also start the REPL with `.venv/bin/python -m mechanicus`.

## Configure

Mechanicus keeps its settings in an app directory. The default is `~/Library/Application Support/mechanicus`. Set `MECHANICUS_APPDIR` to use a different directory.

On the first start, Mechanicus copies `config.yaml` and `.env` into the app directory. It never replaces a file that is already there, so your edits are kept. The session log files and the REPL history are also written to this directory.

To add your keys, follow these steps.

1. Start Mechanicus with `.venv/bin/mechanicus`.
2. Enter `/quit`.
3. Open `.env` in the app directory.
4. Set `OPENAI_API_KEY` to your OpenAI key.
5. Set `TYPESAFE_API_KEY` to your TypeSafe key, if you have one.
6. Save the file.

A key that is already set in your environment wins over the value in `.env`.

### config.yaml

The copied `config.yaml` sets these keys.

- `model`: coder model for tests, implementation, repair, strengthen, and refactor (default `gpt-6.2-sol`)
- `reasoning_effort`: coder reasoning effort (default `medium`)
- `plan_model`: planner model for `/plan`, `/add`, cycle repair, drafting, and `/suggestions` (default `gpt-6.2-astra`)
- `plan_reasoning_effort`: planner reasoning effort (default `high`)
- `repair_attempts`: number of repair rounds (default `3`)
- `jev_model`: Jev model (default `jev-1.14.0`)
- `package_root`: relative directory for written `.py` and `.module` files (default empty, which means the startup directory)

When `plan_model` or `plan_reasoning_effort` is unset, the planner uses the coder values. When `model` is unset, Mechanicus uses `gpt-6.2-sol`.

### Jev checks

Jev is the TypeSafe checker. Mechanicus uses it to check plan coverage, feature coverage, description breadth for `/plan full`, and whether tests require each `uses:` dependency. When `TYPESAFE_API_KEY` is empty, Mechanicus skips each Jev check and records `skipped` with reason `no_key`. When a Jev call fails, Mechanicus records `failed` and continues without treating the failed call as approval. `/show` and `/specs` print the last Jev result for each spec.

## Quick start

Go to your project directory and start Mechanicus.

```text
cd path/to/project
path/to/mechanicus/.venv/bin/mechanicus
```

Then enter these commands.

```text
/spec greet Return the word hello
/adopt greet
/show greet
/generate greet
/quit
```

`/spec greet` stores the behavior and writes the tests and the check. Mechanicus runs them against an empty `impl.py`. When the tests do not fail, it sends them back to the model for repair, up to `repair_attempts` times. When the tests fail as they should, it prints "Tests fail before the implementation." and asks for the code. When the tests never fail, it prints "Tests did not fail for the behavior. Not saved." and keeps nothing.

`/adopt greet` runs pytest on the saved generation and repairs the code when the tests fail. The tests and the check stay fixed during repair. A generation is adopted when pytest passes, the code calls every module named in its `uses:` line, the tests exercise the real dependencies instead of stand-ins (for Python specs), and the code is no longer than the generation already adopted for the same behavior. On success Mechanicus prints "Tests passed. Adopted."

`/show greet` prints the spec, disk sync, the last adopt and Jev results, the adopted code, tests, and check, and any newer candidate.

`/generate greet` writes `greet.py` and `tests/test_greet.py` in the startup directory.

After each OpenAI call, Mechanicus prints a cost estimate for the turn and the session. Enter `/cost` to see the session estimate and turn count. Jev calls are not included in the estimate.

### Planning a whole application

For a larger program, enter `/greenfield` to draft an application description for a new app, or `/brownfield` to draft one from an existing codebase. Enter `/suggestions` for numbered improvements, then `/brownfield <n>` or `/add <n>`. Enter `/accept` to save the draft and run `/plan`, or `/abort` to leave without planning. You can also enter `/plan` with a description directly.

`/plan` writes one `.module` file for each module. Edit a file when you want a different behavior, then enter `/build` to write the Python. When modules use each other, `/build` adds one module for the shared job before it writes the code. Enter `/add` with a feature to update the modules that perform it.

## Commands

`/help` prints every command in these groups.

### Draft

- `/greenfield [note]`: Draft an application description for a new app.
- `/brownfield [note|n]`: Draft from the codebase, or open suggestion n.
- `/suggestions`: Analyze the tree and list numbered improvements.
- `/accept`: Save the draft, leave drafting, and run /plan.
- `/abort`: Leave drafting without planning.

### Plan and build

- `/target [python|swift-package] [Name]`: Show or set the project language target.
- `/plan [mvp|full] [description]`: Plan modules; mvp default, full enriches.
- `/add <feature|n>`: Add a feature to modules, or apply suggestion n.
- `/modules`: List .module files with build status and disk sync.
- `/build`: Build each .module file and write the adopted code.
- `/build <name>`: Build one .module (dependencies must already be adopted).

### Spec lifecycle

- `/spec <name> <behavior>`: Store a spec, write failing tests, then generate code.
- `/adopt <name>`: Run tests, repair the code, and adopt when they pass.
- `/show <name>`: Show the spec, sync, last adopt, last Jev, rendering, and candidate.
- `/specs`: List specs with pin, adopted generation, sync, adopt, and Jev.
- `/pin <name>`: Pin a spec so it cannot be regenerated or overwritten.
- `/unpin <name>`: Clear that pin.
- `/revert <name> <generation> [--db-only]`: Adopt an earlier generation; write disk unless --db-only.
- `/refactor <name>`: Rewrite adopted code for clarity; tests stay fixed.

### Disk and run

- `/import <name>`: Import package source as adopted and pinned.
- `/generate [name]`: Write adopted code and tests into the startup directory.
- `/clean [build|orphans|disk|all]`: Safe cleanup; no args prints help.
- `/test [name]`: Run on-disk pytest or swift test for the project target.
- `/run <name>`: Run the adopted code for a Python spec.

### Session

- `/script <path>`: Run slash commands from a text file.
- `/cost`: Print the OpenAI session cost estimate and turn count.
- `/logfile`: Print the path to the session log file.
- `/help`: List commands and a short description of each.
- `/quit`: Exit the REPL.
- `/exit`: Exit the REPL.

The [operator guide](docs/OPERATOR-GUIDE.md) explains each command in detail.

## Script mode

You can run a text file of slash commands without the interactive REPL.

```text
mechanicus -s path/to/commands.mech
mechanicus --script path/to/commands.mech
```

Inside the REPL, `/script path/to/commands.mech` does the same thing. Mechanicus skips blank lines and lines that start with `#`, and it runs each remaining line in order. A script accepts only slash commands. The interactive commands `/greenfield`, `/brownfield`, `/accept`, `/abort`, and `/script` are refused. The script stops at the first refused, unknown, or failing command, and `/quit` or `/exit` ends it early. When the script finishes, Mechanicus prints the session cost estimate and the log file path.

[examples/coding-harness.mech](examples/coding-harness.mech) is a complete example that plans and builds a small app. The [script-authoring guide](docs/SKILL.md) covers the script grammar and common command sequences.

## Project database

Mechanicus creates `.mechanicus/seed.db` in the startup directory, so each project has its own database. Set `MECHANICUS_DB` to use a different database file.

The `specs` table holds each spec name, its behavior, and its pin flag. The `artifacts` table holds the code, tests, and check for each numbered generation, along with the line count and whether that generation is adopted. Each `/spec`, repair, or rebuild saves a new generation, and older generations stay in the database. Use `/revert <name> <generation>` to adopt an earlier one, and `/pin <name>` to stop a spec from being regenerated or overwritten.

Per-project settings such as the language target live in `.mechanicus/project.yaml`.

## Bootstrap guard

Mechanicus keeps a small set of core modules hand-written and refuses to rebuild them. `src/mechanicus/bootstrap_guard.py` lists these names.

- `bootstrap`
- `bootstrap_guard`
- `config`
- `models`
- `paths`
- `seed`
- `seed_import`
- `smoke`
- `store`
- `__main__`
- `__init__`

`/spec`, `/build`, `/generate`, `/import`, and `/refactor` refuse these names and print that the module is a bootstrap module. The regenerable modules `jev`, `harness`, `codegen`, `lifecycle`, `plan_build`, and `draft` have matching `.module` files. To run Mechanicus on this repository, set `package_root: src/mechanicus` and use `/import <name>` to seed the database from disk. The [self-modify guide](docs/SELF-MODIFY-GUIDE.md) walks through that process.

## Swift package target

Enter `/target swift-package MyAppLogic` to switch a project to a Swift package. Mechanicus writes `.mechanicus/project.yaml` and creates `MyAppLogic/Package.swift` once, leaving an existing manifest in place. The package name must be a legal Swift identifier, and Mechanicus turns hyphens and other punctuation into underscores. Adopted logic goes under `Sources/MyAppLogic/` and XCTest suites go under `Tests/MyAppLogicTests/`. With this target, `/spec`, `/build`, `/adopt`, and `/test` run `swift test` in the package instead of pytest. Enter `/target python` to switch back.

The Swift target covers pure logic packages. You link the package from an Xcode app yourself. After `swift test` passes, adoption still requires the implementation to use the Swift type name of each `uses:` dependency.

## Running the tests

Run the Mechanicus test suite from the repository root.

```text
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m pytest
```

The Swift package test that needs a toolchain is skipped when `swift` is not on your `PATH`.

## Documentation

- [Quick start](docs/QUICK-START.md) covers install, the API keys, and a first spec.
- [Operator guide](docs/OPERATOR-GUIDE.md) lists every command and the pin, adopt, revert, write, and run rules.
- [Self-modify guide](docs/SELF-MODIFY-GUIDE.md) shows how to run Mechanicus on this repository.
- [Script-authoring guide](docs/SKILL.md) covers writing and running `.mech` command scripts.
- [Coding harness example](examples/coding-harness.mech) is a sample script that plans and builds an app.
