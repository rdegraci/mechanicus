# Quick start

Mechanicus stores a behavior and writes the tests first. It keeps a generation only after the tests and the check pass.

For all commands, read [OPERATOR-GUIDE.md](OPERATOR-GUIDE.md).

## Before you start

You need Python 3.10 or later. You need an OpenAI API key.

## Install

1. Go to the Mechanicus repository.
2. Create a virtual environment: `python3 -m venv .venv`.
3. Install the package: `.venv/bin/pip install -e .`.

## Add the API key

1. Start Mechanicus: `.venv/bin/python -m mechanicus`.
2. Enter `/quit`.
3. Open `~/Library/Application Support/mechanicus/.env`.
4. Set `OPENAI_API_KEY` to your key.
5. Save the file.

The first start creates that file. Mechanicus does not replace it after that.

## Start in a project

1. Go to the project directory.
2. Start Mechanicus: `.venv/bin/python -m mechanicus`.

Mechanicus creates `.mechanicus/seed.db` in that directory. Each project has its own database.

## Make one specification

1. Enter `/spec greet Return the word hello`.
2. Read the tests and the check, then read the code.
3. Enter `/adopt greet`.
4. Enter `/show greet`.
5. Enter `/generate greet`.
6. Enter `/quit`.

`/generate greet` writes `greet.py` and `tests/test_greet.py` in the project directory.
