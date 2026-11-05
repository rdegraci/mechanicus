# Mechanicus

Mechanicus stores a behavior and writes the tests first. The tests must fail against an empty module. It then asks for the shortest Python that makes those tests pass. It adopts a generation only after pytest passes.

The project database is `.mechanicus/seed.db` in the directory where you start Mechanicus. Each project has its own database.

## Requirements

You need Python 3.10 or later. You need an OpenAI API key. You need a TypeSafe API key.

## Install

1. Go to the Mechanicus repository.
2. Create a virtual environment: `python3 -m venv .venv`.
3. Install the package: `.venv/bin/pip install -e .`.

## Configure

1. Start Mechanicus: `.venv/bin/python -m mechanicus`.
2. Enter `/quit`.
3. Open `~/Library/Application Support/mechanicus/.env`.
4. Set `OPENAI_API_KEY` to your OpenAI key.
5. Set `TYPESAFE_API_KEY` to your TypeSafe key.
6. Save the file.

The first start copies `.env` and `config.yaml` into that directory. Mechanicus does not replace a file that is already there. `/spec` uses the model name and the reasoning effort in `config.yaml`. The default model is `gpt-4o`. The default reasoning effort is `medium`. The default Jev model is `jev-1.14.0`.

## Usage

1. Go to the project directory.
2. Start Mechanicus: `.venv/bin/python -m mechanicus`.

```text
/spec greet Return the word hello
/adopt greet
/show greet
/generate greet
/quit
```

`/generate greet` writes `greet.py` and `tests/test_greet.py` in the startup directory.

Enter `/greenfield` to draft an application description for a new app. Enter `/brownfield` to draft one from the codebase and ask about the code. Enter `/accept` to save that draft and run `/plan`. Enter `/abort` to leave without planning. You can also enter `/plan` and a description. Mechanicus writes one `.module` file for each module. Edit a file when you want a different behavior. Enter `/build` to write the Python. When modules use each other, `/build` adds one module for the shared job before it writes the Python. Jev checks that the modules cover the description. The writing model adds a missing module. Enter `/add` and a feature to update the modules that perform it.

You can also run the `mechanicus` command after install.

## Documentation

- [Quick start](docs/QUICK-START.md) shows install, the API key, and one specification.
- [Operator guide](docs/OPERATOR-GUIDE.md) lists every command and the pin, adopt, revert, write, and run rules.
- [Self-modify guide](docs/SELF-MODIFY-GUIDE.md) shows how to run Mechanicus on this repository.

## Package layout

The runtime is split under `src/mechanicus/`. Bootstrap modules (`bootstrap`, `config`, `store`, `paths`, and related guards) stay hand-written. Regenerable leaves have matching `.module` files. Set `package_root: src/mechanicus` when you self-host. Enter `/import <name>` to seed SQLite from disk.
