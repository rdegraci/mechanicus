# Quick start

Mechanicus stores a behavior and writes the tests first. It keeps a generation only after the tests and the check pass.

For all commands, read [OPERATOR-GUIDE.md](OPERATOR-GUIDE.md).

## Before you start

You need Python 3.10 or later. You need an OpenAI API key. A TypeSafe API key is optional, and without it Mechanicus skips the Jev checks and records them as skipped with reason no_key.

## Install

1. Go to the Mechanicus repository.
2. Create a virtual environment: `python3 -m venv .venv`.
3. Install the package: `.venv/bin/pip install -e .`.

## Add the API key

1. Start Mechanicus: `.venv/bin/python -m mechanicus`.
2. Enter `/quit`.
3. Open `~/Library/Application Support/mechanicus/.env`.
4. Set `OPENAI_API_KEY` to your OpenAI key.
5. Set `TYPESAFE_API_KEY` to your TypeSafe key.
6. Save the file.

The first start creates that file. Mechanicus does not replace it after that.

## Start in a project

1. Go to the project directory.
2. Start Mechanicus: `.venv/bin/python -m mechanicus`.

Mechanicus creates `.mechanicus/seed.db` in that directory. Each project has its own database. The REPL uses prompt_toolkit: Up/Down for history, Tab for slash-command completion.

To run a file of slash commands without the interactive REPL:

```text
.venv/bin/mechanicus --script path/to/commands.txt
```

See [OPERATOR-GUIDE.md](OPERATOR-GUIDE.md) for script rules.

## Make one specification

1. Enter `/spec greet Return the word hello`.
2. Read the tests and the check, then read the code.
3. Enter `/adopt greet`.
4. Enter `/show greet`.
5. Enter `/generate greet`.
6. Enter `/quit`.

`/generate greet` writes `greet.py` and `tests/test_greet.py` in the project directory.

## Plan an application

1. Enter `/greenfield` for a new app, or `/brownfield` when the project already has code.
2. Talk until the description is ready, then enter `/accept`.
3. Edit a `.module` file when you want a different behavior.
4. Enter `/build`.
5. Enter `/quit`.

You can also enter `/plan` and a description. `/accept` runs `/plan` with the draft. `/abort` leaves the draft without planning. `/brownfield` can read project files while you talk.

`/build` writes one `.py` file for each module that passes. It also writes the tests under `tests/`. A module that calls another module starts with `uses:` and that module name. When modules use each other, `/build` adds one module for the shared job and then builds. Jev checks that the modules cover the description. The writing model adds a missing module.

## Add a feature

1. Enter `/add` and the feature.
2. Read each changed `.module` file.
3. Enter `/build`.

`/add` updates the modules that perform the feature. A module that does not need the feature stays as it is.
