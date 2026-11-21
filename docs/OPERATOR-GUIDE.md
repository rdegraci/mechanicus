# Operator guide

Mechanicus stores a behavior. It writes the tests and the check first. It runs them against an empty module. The tests must fail. It then asks for the shortest Python that makes those tests pass. Mechanicus adopts a generation only after pytest passes.

For install steps, read [QUICK-START.md](QUICK-START.md).

## Files

The startup directory is the current directory when Mechanicus starts. Mechanicus creates `.mechanicus` there. The project database is `.mechanicus/seed.db`.

The application directory is `~/Library/Application Support/mechanicus`. The first start copies `config.yaml` and `.env` into that directory. Mechanicus does not replace a file that is already there.

`config.yaml` holds the writing model, the reasoning effort, and the Jev model. `.env` holds `OPENAI_API_KEY` and `TYPESAFE_API_KEY`. If a key is already in the environment, Mechanicus keeps that value.

`MECHANICUS_DB` selects a different database file. `MECHANICUS_APPDIR` selects a different application directory.

Each start writes a new file in `~/Library/Application Support/mechanicus/logs`. `/logfile` prints that path. Mechanicus prints the path again when it exits.

## Project target

The language target is per project in `.mechanicus/project.yaml`. When that file is missing, Mechanicus uses Python.

Enter `/target swift-package MyAppLogic` to select a Swift package. Mechanicus writes `project.yaml` and scaffolds `MyAppLogic/Package.swift` once (it does not overwrite an existing manifest). The package name must be a legal Swift identifier (letters, digits, underscore). Hyphens are normalized to underscores. Specs still use `.module` files beside `Package.swift`. Adopted logic lands under `Sources/MyAppLogic/` and XCTest under `Tests/MyAppLogicTests/`. `/build` and `/adopt` run `swift test` in that package in place. Enter `/target python` to return to Python.

Swift v1 is pure logic only (no SwiftUI). Do not plan modules named `Tests` or ending in `/Tests`; Mechanicus writes XCTest for each logic module. Link the local package from an Xcode app separately. Cross-module `uses:` call-graph proofs from the Python path are not enforced for Swift yet; compile and XCTest green are the adopt gate.

## Start

1. Go to the project directory.
2. Run `python -m mechanicus`.

You can also run the `mechanicus` command after install. Enter `/quit` to stop.

The chat loop uses prompt_toolkit. Up and Down move through prior lines. Tab completes slash commands. Mechanicus stores that history in `~/Library/Application Support/mechanicus/repl_history` (or under `MECHANICUS_APPDIR` when set).

## Commands

The name is one token. The behavior is the rest of the line. A blank line does nothing.

| Command | Effect |
| --- | --- |
| `/greenfield [note]` | Starts a draft of an application description for a new app. |
| `/brownfield [note]` | Starts a draft from the codebase. The model can read project files. |
| `/brownfield <n>` | Starts brownfield using suggestion number `n` from `/suggestions`. |
| `/suggestions` | Analyzes the tree, states its purpose, and lists improvements plus bugs and issues. |
| `/accept` | Saves the draft, leaves drafting, and runs `/plan`. |
| `/abort` | Leaves drafting without saving a new description and without `/plan`. |
| `/spec <name> <behavior>` | Stores the behavior, writes failing tests, then requests code. |
| `/target` | Shows the project target (`python` or `swift-package`). |
| `/target python` | Sets the project to Python (default when `.mechanicus/project.yaml` is missing). |
| `/target swift-package [Name]` | Sets a Swift package target and scaffolds `Package.swift` once. |
| `/plan [description]` | Writes one `.module` file for each recommended module. |
| `/add <feature>` | Adds a feature to the modules that perform it. |
| `/add <n>` | Runs `/add` with suggestion number `n` when `.module` files exist. |
| `/build` | Builds each `.module` file and writes the adopted code. Prints module status before and after. |
| `/build <name>` | Builds one `.module` file. Dependencies must already be adopted. Refuses modules still in a cycle. |
| `/modules` | Lists each `.module` file with build status, disk sync, uses, and a short reason. |
| `/import <name>` | Reads package source under the code root, saves it as adopted, and pins it. |
| `/specs` | Lists each specification with pin, adopted generation, disk sync, last adopt, and last Jev result. |
| `/refactor <name>` | Rewrites adopted code for clarity. Tests and the check stay fixed. |
| `/show <name>` | Prints the specification, disk sync, last adopt result, last Jev result, adopted generation, and a newer candidate. |
| `/adopt <name>` | Runs the tests. Repairs the code, then adopts a generation that passes. Records the last adopt result. |
| `/pin <name>` | Pins the specification and the latest code. |
| `/unpin <name>` | Clears that pin. |
| `/revert <name> <generation>` | Adopts an earlier generation and writes it to disk. |
| `/revert <name> <generation> --db-only` | Adopts an earlier generation without writing disk. |
| `/generate` | Writes every adopted specification and its tests. |
| `/generate <name>` | Writes that adopted specification and its tests. |
| `/clean` | Prints `/clean` help. Does not delete anything. |
| `/clean build` | Deletes the Swift package `.build` directory only. |
| `/clean orphans` | Dry-run list of refused `Tests` / `*/Tests` plan debris and matching DB specs. |
| `/clean orphans --apply` | Removes those `.module` files and matching unpinned DB specs. |
| `/clean disk` | Rewrites adopted files from the DB; stubs unadopted Swift modules from `.module` files. |
| `/clean all` | Runs `build`, then `orphans --apply`, then `disk`. |
| `/test` | Runs on-disk pytest, or `swift test` when the project target is `swift-package`. |
| `/test <name>` | Runs the on-disk tests for that specification. |
| `/run <name>` | Loads the adopted Python code and calls `main` when it exists. Not used for Swift packages. |
| `/logfile` | Prints the path of the session log file. |
| `/help` | Lists commands and a short description of each. |
| `/quit` | Stops Mechanicus. |
| `/exit` | Stops Mechanicus. |

Mechanicus starts in command mode. When `.mechanicus/application.txt` or a `.module` file is already present, it tells you that `/greenfield` or `/brownfield` can revise the description. When neither is present, it points you to `/greenfield`, `/brownfield`, `/spec`, or `/plan`.

`/accept` and `/abort` work only while you draft. Other slash commands do not run during a draft. Enter free text to talk to the model.

## Specifications

1. Enter `/spec`, a name, and the behavior.
2. Read the tests and the check.
3. Read the code.

Mechanicus runs the tests against an empty module. The tests must fail in a way that proves the behavior is missing. Mechanicus rejects weak tests: missing `impl` use, trivial asserts such as `assert False`, mixed pass/fail runs, and failures that do not involve `impl`. Mechanicus repairs the tests when they pass or when they are broken. `repair_attempts` in `config.yaml` sets the number of repairs. The default is 3. Mechanicus does not save a generation when the tests do not go red.

When a module names another module in `uses:`, Jev reads the tests. Jev asks whether the tests fail when the code never uses that dependency (Python import path, or Swift type name for `swift-package` projects). A score below 0.7 sends the tests to the writing model once. Mechanicus keeps those tests. Each Jev check records `ok`, `skipped` (no `TYPESAFE_API_KEY`), or `failed` (API or network error). `/show` and `/specs` print that result for the specification. A new saved generation clears it until the next Jev check on that name. Modules with no `uses:` stay `jev=none`.

`/build` also runs that uses: check when a module is already adopted and unchanged. Weak coverage forces a rebuild. Without `TYPESAFE_API_KEY`, the check records `jev=skipped` and the module stays unchanged. Leaf modules with no `uses:` stay `jev=none`.

Mechanicus does not adopt the new generation yet. Mechanicus does not replace a pinned specification. It does not call the model for that name.

`/specs` prints each specification name, whether it is pinned, the adopted generation number, disk sync (`ok`, `differ`, `missing`, or `none`), the last adopt result (`ok`, `failed`, or `none`), and the last Jev result (`ok`, `skipped`, `failed`, or `none`). When no specification is adopted yet, the generation is `none` and sync is `none`.

`/show` prints the behavior, the pin flag, and disk sync. It prints the last adopt result (`Adopt: ok|failed|none`) with a reason code and optional detail. It prints the last Jev result (`Jev: ok|skipped|failed|none`) with a reason code, gate (`tests`, `plan`, or `feature`), and optional detail. It prints the adopted code, tests, and check. If the latest generation is not adopted, `/show` also prints that candidate. A new saved generation clears the last adopt result and the last Jev result until `/adopt` or the next Jev check runs again.

A generation number starts at 1. `/show` prints the number. Use that number with `/revert`.

## Refactor

1. Enter `/unpin` and the name when the specification is pinned.
2. Enter `/refactor` and the name.
3. Read the new code.
4. Enter `/generate` and the name when the clearer code is adopted.

`/refactor` keeps the tests and the check. It asks for clearer code with the same behavior. It saves a new generation and runs the adopt path. A longer generation is allowed. A pinned specification is refused. `/build` still prefers the shortest code that passes the tests. Use `/refactor` when you want readability after a build.

## Draft

1. Enter `/greenfield` for a new app, or `/brownfield` for an existing codebase.
2. Talk with the model until the application description is ready.
3. Enter `/accept` to save the description and run `/plan`, or enter `/abort` to leave.

`/greenfield` loads `.mechanicus/application.txt` when that file exists. The model does not read the codebase.

`/brownfield` scans text source files under the startup directory. It skips `.git`, `.venv`, `.mechanicus`, dependency trees, binaries, and `.env` files. The model can request file reads while you talk. Mechanicus prints those paths as it reads them. `/brownfield` also loads a saved description when one exists, then can revise it from the scan.

The draft is only the application description. It does not write `.module` files or Python. `/accept` prints the description, saves it through `/plan`, then tells you to enter `/build` or to enter `/greenfield` or `/brownfield` again. `/abort` keeps the previous `application.txt` and modules as they are.

## Suggestions

1. Enter `/suggestions` in an existing project.
2. Read the purpose and the numbered list.
3. Enter `/brownfield` and a number to draft from that item, or enter `/add` and a number when module files already exist.

`/suggestions` scans the startup tree. It writes `.mechanicus/suggestions.txt`. It does not run `/plan` or `/build`. It prints two sections: suggestions and bugs and issues. Suggestion tags are `feature`, `integrate`, `docs`, or `packaging`. Bug tags are `bug` or `issue`. Numbers continue across both sections, so `/brownfield <n>` and `/add <n>` work for either kind. `/add <n>` refuses when there are no `.module` files. Use `/brownfield <n>` in that case. An unknown number asks you to run `/suggestions` again.

## Plan

1. Enter `/plan` and the application description, or enter `/accept` after a draft.
2. Read each `.module` file.
3. Edit a file when the recommendation is wrong.
4. Enter `/build`.

`/plan` with no description uses `.mechanicus/application.txt` when that file exists. `/plan` asks the model for the modules. It gives each job its own module. It writes one `.module` file for each module. The file text is that recommendation. `irc/parse.module` is the module `irc/parse`. Mechanicus saves the description in `.mechanicus/application.txt`.

A module that calls another module starts with `uses:` and that module name.

```text
uses: irc/parse

Read a command and join a channel.
```

Mechanicus does not write Python during `/plan`. It does not replace a `.module` file that is already there. It refuses a path outside the startup directory. It skips a new module name that would shadow a package directory (for example `cli` when `cli/` exists) or that would nest under an existing top-level `.py` module file.

Jev reads the plan after the writing model returns it. Jev checks each behavior in the description. A score below 0.7 means no module performs that behavior. The writing model adds the missing module. Mechanicus writes each recommended module.

## Add

1. Enter `/add` and the feature.
2. Read each changed `.module` file.
3. Enter `/build`.

`/add` reads the current `.module` files. The writing model changes only the modules that perform the feature. It keeps the current behavior and adds the feature. A new job gets a new `.module` file. The module that calls it gains a `uses:` line.

Jev reads the modules after that change. A score below 0.7 sends the change back to the writing model once. Mechanicus writes that change. A module that does not need the feature stays as it is. Mechanicus does not change a pinned module.

## Modules

1. Enter `/modules`.

`/modules` lists each `.module` file with its build status, disk sync, `uses:` list, and a short reason. Status values include `ready`, `pinned`, `stale`, `blocked`, `cyclic`, `empty`, `layout`, and `bootstrap`. `stale` means a `uses:` dependency’s adopted generation changed since this module was last adopted, or that an already-adopted module has no dependency fingerprint yet (`deps not recorded`). When the module is also pinned, the reason is `pinned; upstream changed` or `pinned; deps not recorded`. After a build, statuses can also be `built`, `failed`, or `unchanged`. Sync values match `/specs` (`ok`, `differ`, `missing`, `none`). `/modules` does not call the model and does not build.

## Build

1. Enter `/build`, or enter `/build` and a module name.
2. Read the module status table before the build.
3. Read the result for each module that runs.
4. Read the module status table after the build.

`/build` with no name reads every `.module` file in the startup directory. It does not read files under `.mechanicus`. It prints module status before it builds. After cycle repair it prints the table again when the graph changed. It builds a module after each module that file uses. For a new behavior, Mechanicus writes the tests, writes the code, runs pytest, and writes the adopted module. It skips a module whose path would shadow a package directory or nest under an existing top-level `.py` module file. When the build finishes, it prints module status again with outcomes such as `built`, `failed`, `unchanged`, or `pinned`.

`/build <name>` builds only that module. It does not repair cycles for the whole graph. If the module is in a cycle, Mechanicus refuses and asks for a full `/build` first. If a `uses:` dependency is not adopted, Mechanicus refuses that partial build. Use `/modules` to see which names are `ready` or `blocked` before you choose a name.

When modules use each other, Mechanicus adds one module for the shared job. The modules in the cycle do not use each other. Those modules can use the new module. Mechanicus writes the new file, then continues the build. When Mechanicus cannot use that new module, it removes `uses:` lines until the modules do not use each other. Mechanicus does not change a pinned module in that cycle.

Pytest loads the module under test as `impl.py`. It also loads each adopted module named in `uses:`, and the modules those modules use. The code must call each module named in its own `uses:` line. An unused import does not pass. A module that names a missing module is not built.

A pinned specification does not change. A module that is already adopted for the same behavior is written to disk and is not sent to the model, unless a `uses:` dependency’s adopted generation changed since this module was last adopted. In that case Mechanicus re-runs adopt against the current dependencies. If adopt fails, it regenerates the module, then adopts again. A module that fails is reported. Mechanicus does not write that module.

After a module adopts during `/build`, Mechanicus may strengthen the suite once: it reviews the tests and check against the behavior (and the adopted code), and when the suite looks thin it rewrites the tests, regenerates code, and re-adopts with a longer generation allowed. Pinned and unchanged modules are skipped. If strengthen fails, Mechanicus keeps the first adopt and reports that in the build reason (`strengthen kept first`). Python and Swift both use this path.

## Adopt

1. Enter `/adopt` and the specification name.
2. Read the pytest result.

Mechanicus writes the code to a temporary directory as `impl.py` and runs pytest. Tests and the check must import `impl`. It deletes that directory after the run.

Mechanicus adopts the generation only when the tests and the check both pass. It refuses an empty check. It refuses a longer generation when a shorter adopted generation has the same behavior. A different behavior can have more lines.

When the tests fail, Mechanicus sends the pytest output to the writing model. The writing model repairs the code. The tests and the check do not change. Mechanicus saves that generation and runs pytest again. `repair_attempts` in `config.yaml` sets the number of repairs. The default is 3. A pinned specification is not repaired. A failed run does not clear an older adopted generation.

For a `swift-package` target, `/adopt` writes sources in place and runs `swift test`. If the harness fails and the tests look weak (for example XCTFail-only “pending” suites), Mechanicus rewrites the tests once, generates new code for those tests, then continues adopt. It does not keep repairing code against tests that never assert the behavior.

If the latest code is pinned and already adopted, Mechanicus refuses `/adopt`. If that code is pinned and not adopted, Mechanicus still runs the tests.

Each `/adopt` stores a last result on the specification: `ok` or `failed`, a reason code (`longer`, `harness`, `uncalled`, `no_tests`, and others), optional detail, and the generation that was attempted. `/show` and `/specs` print that result. `/revert` records `ok` for the generation it adopts.

## Pin

`/pin` blocks `/spec` for that name. It also pins the latest code.

`/unpin` clears the pin on the specification and on its artifacts.

1. If the specification is pinned, enter `/unpin` and the name.
2. Enter `/revert`, the name, and the generation number.

`/revert` does not call the model. `/revert` does not run pytest. Mechanicus refuses `/revert` while the specification is pinned. By default `/revert` writes the adopted generation to disk the same way `/generate` does. If that write fails, the database still holds the reverted generation and Mechanicus reports that disk was not updated. Enter `/revert`, the name, the generation, and `--db-only` when you want to change adoption without writing disk. `/revert` clears the dependency fingerprint, so a module that names `uses:` becomes `stale` until `/adopt` records a new fingerprint against the current dependencies.

## Write files

`/generate` writes adopted code into the startup directory. The specification name is the relative path. `pkg/mod` becomes `pkg/mod.py`. Mechanicus adds `.py` when the name does not already end with `.py`. It creates parent directories. It refuses a path outside the startup directory. It skips a name that would shadow a package directory or nest under an existing top-level `.py` module file.

`/generate` also writes the tests under `tests/`. `greet` writes `tests/test_greet.py`. `pkg/mod` writes `tests/pkg/test_pkg_mod.py`. The file basename includes the module path so plain pytest can collect nested tests without import-mode flags. The written tests import that module as `impl`. A local name in the test stays as it is. The check remains in the project database.

`/generate` with no name writes every adopted specification.

## Clean

1. Enter `/clean` to read the modes. Bare `/clean` does not delete anything.
2. Enter `/clean build` to remove the Swift package `.build` directory.
3. Enter `/clean orphans` to list refused `Tests` / `*/Tests` `.module` files and matching DB specs. Enter `/clean orphans --apply` to remove them. Pinned specs stay.
4. Enter `/clean disk` to rewrite adopted files from the database and stub unadopted Swift modules from remaining `.module` files.
5. Enter `/clean all` to run `build`, then `orphans --apply`, then `disk`.

`/clean` does not wipe the database, delete pinned specifications, or remove ordinary logic modules.

## Test

1. Enter `/generate` when the tree needs the latest adopted files.
2. Enter `/test`, or enter `/test` and a specification name.

`/test` runs `python -m pytest` in the startup directory. It sets `PYTHONPATH` to that directory so imports match the generated layout. With a name, Mechanicus runs the matching file under `tests/` (the same path `/generate` writes). It prints the pytest output. It does not call the model. It does not adopt or repair.

`/adopt` and `/refactor` still use the internal temp-directory harness. `/test` checks what is on disk.

## Run

`/run` loads the adopted code. If the code defines `main`, Mechanicus calls `main`. Mechanicus prints the return value when `main` returns one. If there is no `main`, Mechanicus prints a load message.

If the specification is pinned, Mechanicus does not replace a module that this process already loaded.

## Configuration

1. Open `~/Library/Application Support/mechanicus/config.yaml`.
2. Set `model` to the model name.
3. Save the file.
4. Start Mechanicus again.

The example file is `src/mechanicus/config.yaml.example`. The default model is `gpt-4o`. The default reasoning effort is `medium`. The default Jev model is `jev-1.14.0`. `/spec` sends the writing model and the reasoning effort from `config.yaml`.

`package_root` is a relative path under the startup directory. When it is empty, Mechanicus writes `.py` and `.module` files at the startup directory. When it is set (for example `src/mechanicus`), Mechanicus writes those files under that directory. Spec names stay short (`jev`, not `src/mechanicus/jev`). Tests still go under `tests/` at the startup directory.

## Self-host

To run Mechanicus on this repository, read [SELF-MODIFY-GUIDE.md](SELF-MODIFY-GUIDE.md).

Set `package_root` to `src/mechanicus`. Start from the repository root. Enter `/import` for each leaf you will change. Change one leaf at a time. `/revert` writes disk by default. Run `python -m mechanicus.smoke` after a generate or revert that writes disk.

## Secrets

1. Open `~/Library/Application Support/mechanicus/.env`.
2. Set `OPENAI_API_KEY` to your OpenAI key.
3. Set `TYPESAFE_API_KEY` to your TypeSafe key.
4. Save the file.
5. Start Mechanicus again.

When `TYPESAFE_API_KEY` is empty, Mechanicus skips Jev and records `skipped` with reason `no_key`. When the Jev API fails, Mechanicus records `failed` and continues without treating the miss as approval.

The example file is `src/mechanicus/dot_env.example`.
