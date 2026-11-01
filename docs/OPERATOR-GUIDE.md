# Operator guide

Mechanicus stores a behavior. It writes the tests and the check first. It runs them against an empty module. The tests must fail. It then asks for the shortest Python that makes those tests pass. Mechanicus adopts a generation only after pytest passes.

For install steps, read [QUICK-START.md](QUICK-START.md).

## Files

The startup directory is the current directory when Mechanicus starts. Mechanicus creates `.mechanicus` there. The project database is `.mechanicus/seed.db`.

The application directory is `~/Library/Application Support/mechanicus`. The first start copies `config.yaml` and `.env` into that directory. Mechanicus does not replace a file that is already there.

`config.yaml` holds the writing model, the reasoning effort, and the Jev model. `.env` holds `OPENAI_API_KEY` and `TYPESAFE_API_KEY`. If a key is already in the environment, Mechanicus keeps that value.

`MECHANICUS_DB` selects a different database file. `MECHANICUS_APPDIR` selects a different application directory.

Each start writes a new file in `~/Library/Application Support/mechanicus/logs`. `/logfile` prints that path. Mechanicus prints the path again when it exits.

## Start

1. Go to the project directory.
2. Run `python -m mechanicus`.

You can also run the `mechanicus` command after install. Enter `/quit` to stop.

## Commands

The name is one token. The behavior is the rest of the line. A blank line does nothing.

| Command | Effect |
| --- | --- |
| `/spec <name> <behavior>` | Stores the behavior, writes failing tests, then requests code. |
| `/plan <description>` | Writes one `.module` file for each recommended module. |
| `/build` | Builds each `.module` file and writes the adopted code. |
| `/show <name>` | Prints the specification, the adopted generation, and a newer candidate. |
| `/adopt <name>` | Runs the tests. Repairs the code, then adopts a generation that passes. |
| `/pin <name>` | Pins the specification and the latest code. |
| `/unpin <name>` | Clears that pin. |
| `/revert <name> <generation>` | Adopts an earlier generation. |
| `/generate` | Writes every adopted specification and its tests. |
| `/generate <name>` | Writes that adopted specification and its tests. |
| `/run <name>` | Loads the adopted code and calls `main` when it exists. |
| `/logfile` | Prints the path of the session log file. |
| `/quit` | Stops Mechanicus. |

## Specifications

1. Enter `/spec`, a name, and the behavior.
2. Read the tests and the check.
3. Read the code.

Mechanicus runs the tests against an empty module. The tests must fail. Mechanicus repairs the tests when they pass or when they do not run. `repair_attempts` in `config.yaml` sets the number of repairs. The default is 3. Mechanicus does not save a generation when the tests do not fail.

When a module names another module in `uses:`, Jev reads the tests. Jev asks whether the tests fail when the code never calls that module. A score below 0.7 sends the tests to the writing model once. Mechanicus keeps those tests.

Mechanicus does not adopt the new generation yet. Mechanicus does not replace a pinned specification. It does not call the model for that name.

`/show` prints the behavior and the pin flag. It prints the adopted code, tests, and check. If the latest generation is not adopted, `/show` also prints that candidate.

A generation number starts at 1. `/show` prints the number. Use that number with `/revert`.

## Plan

1. Enter `/plan` and the application description.
2. Read each `.module` file.
3. Edit a file when the recommendation is wrong.
4. Enter `/build`.

`/plan` asks the model for the modules. It gives each job its own module. It writes one `.module` file for each module. The file text is that recommendation. `irc/parse.module` is the module `irc/parse`. Mechanicus saves the description in `.mechanicus/application.txt`.

A module that calls another module starts with `uses:` and that module name.

```text
uses: irc/parse

Read a command and join a channel.
```

Mechanicus does not write Python during `/plan`. It does not replace a `.module` file that is already there. It refuses a path outside the startup directory.

Jev reads the plan after the writing model returns it. Jev checks each behavior in the description. A score below 0.7 means no module performs that behavior. The writing model adds the missing module. Mechanicus writes each recommended module.

## Build

1. Enter `/build`.
2. Read the result for each module.

`/build` reads every `.module` file in the startup directory. It does not read files under `.mechanicus`. It builds a module after each module that file uses. For a new behavior, Mechanicus writes the tests, writes the code, runs pytest, and writes the adopted module.

When modules use each other, Mechanicus adds one module for the shared job. The modules in the cycle do not use each other. Those modules can use the new module. Mechanicus writes the new file, then continues the build. When Mechanicus cannot use that new module, it removes `uses:` lines until the modules do not use each other. Mechanicus does not change a pinned module in that cycle.

Pytest loads the module under test as `impl.py`. It also loads each adopted module named in `uses:`, and the modules those modules use. The code must call each module named in its own `uses:` line. An unused import does not pass. A module that names a missing module is not built.

A pinned specification does not change. A module that is already adopted for the same behavior is written to disk and is not sent to the model. A module that fails is reported. Mechanicus does not write that module.

## Adopt

1. Enter `/adopt` and the specification name.
2. Read the pytest result.

Mechanicus writes the code to a temporary directory as `impl.py` and runs pytest. Tests and the check must import `impl`. It deletes that directory after the run.

Mechanicus adopts the generation only when the tests and the check both pass. It refuses an empty check. It refuses a longer generation when a shorter adopted generation has the same behavior. A different behavior can have more lines.

When the tests fail, Mechanicus sends the pytest output to the writing model. The writing model repairs the code. The tests and the check do not change. Mechanicus saves that generation and runs pytest again. `repair_attempts` in `config.yaml` sets the number of repairs. The default is 3. A pinned specification is not repaired. A failed run does not clear an older adopted generation.

If the latest code is pinned and already adopted, Mechanicus refuses `/adopt`. If that code is pinned and not adopted, Mechanicus still runs the tests.

## Pin

`/pin` blocks `/spec` for that name. It also pins the latest code.

`/unpin` clears the pin on the specification and on its artifacts.

1. If the specification is pinned, enter `/unpin` and the name.
2. Enter `/revert`, the name, and the generation number.

`/revert` does not call the model. `/revert` does not run pytest. Mechanicus refuses `/revert` while the specification is pinned.

## Write files

`/generate` writes adopted code into the startup directory. The specification name is the relative path. `pkg/mod` becomes `pkg/mod.py`. Mechanicus adds `.py` when the name does not already end with `.py`. It creates parent directories. It refuses a path outside the startup directory.

`/generate` also writes the tests under `tests/`. `greet` writes `tests/test_greet.py`. `pkg/mod` writes `tests/pkg/test_mod.py`. The written tests import that module as `impl`. A local name in the test stays as it is. The check remains in the project database.

`/generate` with no name writes every adopted specification.

## Run

`/run` loads the adopted code. If the code defines `main`, Mechanicus calls `main`. Mechanicus prints the return value when `main` returns one. If there is no `main`, Mechanicus prints a load message.

If the specification is pinned, Mechanicus does not replace a module that this process already loaded.

## Configuration

1. Open `~/Library/Application Support/mechanicus/config.yaml`.
2. Set `model` to the model name.
3. Save the file.
4. Start Mechanicus again.

The example file is `src/mechanicus/config.yaml.example`. The default model is `gpt-4o`. The default reasoning effort is `medium`. The default Jev model is `jev-1.14.0`. `/spec` sends the writing model and the reasoning effort from `config.yaml`.

## Secrets

1. Open `~/Library/Application Support/mechanicus/.env`.
2. Set `OPENAI_API_KEY` to your OpenAI key.
3. Set `TYPESAFE_API_KEY` to your TypeSafe key.
4. Save the file.
5. Start Mechanicus again.

When `TYPESAFE_API_KEY` is empty, Mechanicus skips Jev.

The example file is `src/mechanicus/dot_env.example`.
