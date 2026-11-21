---
name: mechanicus-scripts
description: >-
  Writes and reviews Mechanicus command scripts, and drives Mechanicus from
  Cursor to plan, build, add features, or fix bugs. Use when the user asks for
  a .mech script, a Mechanicus recipe, batch slash commands, /plan /build
  /add /pin, or describes bad vs correct behavior and wants Mechanicus to
  fix it. Covers script grammar, refused commands, MVP vs full plan, bug-fix
  flow, and finding module names.
---

# Mechanicus command scripts

Help the user author a text file of Mechanicus slash commands, or run the
same commands from the project directory via the shell. The operator runs a
file with:

```text
mechanicus --script path/to/commands.txt
mechanicus -s path/to/commands.txt
```

Or from the REPL: `/script path/to/commands.txt`.

Prefer linking to `docs/OPERATOR-GUIDE.md` for full human detail. This skill
is for **driving Mechanicus** (scripts and Cursor-led command sequences), not
for editing Mechanicus internals.

## What Mechanicus does

Mechanicus is specification-driven:

1. A `.module` file holds a name, optional `uses:`, and a behavior brief.
2. `/build` writes failing tests, then code, then adopts when tests pass.
3. Pins freeze a spec against regenerate or overwrite.
4. `/plan` only writes **new** `.module` files. It does not replace existing ones.
5. `/add` grows existing modules (and can add new ones). Unpin first if pinned.

Python is the default target. `/target swift-package Name` scaffolds a Swift
package once. Swift plans are pure logic only (no SwiftUI, no test-suite
modules named `Tests`).

There is **no `/fix` command**. Bug fixes use the same contract loop as
features: correct the behavior text, then rebuild.

## Script file format

- One slash command per line (same grammar as the interactive REPL)
- Blank lines ignored
- Lines starting with `#` are comments
- Mechanicus prints `> /command…` before each line runs
- Stop on first error or unknown command
- `/quit` or `/exit` ends the script successfully
- Any text file works; `.mech` or `.mechanicus/script.txt` are fine names

### Refused in scripts (interactive only)

Do **not** put these in a script file:

- `/greenfield`
- `/brownfield`
- `/accept`
- `/abort`
- `/script` (no nesting)

Draft clarifying questions belong in a human REPL session, or in the
description text you put on `/plan` after editing by hand.

### Safe script commands

Use these freely when they match the job:

`/target`, `/plan`, `/add`, `/modules`, `/build`, `/build <name>`, `/spec`,
`/adopt`, `/show`, `/specs`, `/pin`, `/unpin`, `/revert`, `/refactor`,
`/import`, `/generate`, `/clean` (avoid `--apply` unless the user asked),
`/test`, `/run` (Python only), `/cost`, `/logfile`, `/help`, `/quit`

## Writing a good `/plan` description

Put the product brief on the same line (or rely on saved
`.mechanicus/application.txt` with bare `/plan`).

**MVP** (`/plan` or `/plan mvp`): short brief of core jobs and boundaries.
Do not invent unrelated product scope.

**Full** (`/plan full`): richer brief (outcomes, flows, failures, persistence,
boundaries). Mechanicus may enrich implied gaps into
`.mechanicus/application.full.txt`.

After `/plan`, the operator should read `.module` files before `/build`.
If the script must proceed unattended, keep the description tight and the
module graph small.

## Fixing bugs (no `/fix` command)

The user often knows only bad behavior vs correct behavior, not the module
name. Discover the name, then correct the contract and rebuild.

1. Find the module:
   - `rg -n "phrase from the bug" *.module **/*.module`
   - or `/modules` / `/specs`, then `/show <name>`
2. `/unpin <name>` when that spec is pinned
3. Put the **correct** behavior in the contract:
   - `/add <corrected behavior text>` (planner picks modules), or
   - `/add <n>` after `/suggestions` for a listed bug, or
   - edit the `.module` brief by hand
4. `/build` or `/build <name>`
5. `/pin <name>` when the fix is good

Do **not** use `/refactor` for bugs. Refactor keeps the same tests and only
clarifies code. If tests encode the wrong behavior, change the module text
(or `/add`) first. `/adopt` alone repairs code against current tests; it does
not redefine the product contract.

### Bug-fix script example

```text
# After rg /modules found context_budget
/unpin context_budget
/add When the transcript exceeds the budget, never drop the system instructions; drop or truncate the oldest tool payloads first; keep the latest user message and the last K tool results.
/build context_budget
/pin context_budget
```

## Cursor driving Mechanicus

When the user describes a feature or bug in plain language, Cursor may:

1. Work in the **project directory** (where `.mechanicus/` and `.module` files live)
2. Look up module names with `rg`, `/modules`, or `/specs` — do not ask the user for `<name>` if it is findable
3. Write a small `.mech` script or run `mechanicus -s …` / equivalent commands
4. Skim updated `.module` files before an expensive full `/build` when the graph is large
5. Confirm before `/unpin` on core modules and before `/clean … --apply`

The user should not need to memorize Mechanicus ceremony. Prefer product
language in chat; translate to `/add`, `/build`, `/pin` in the tool layer.

## Recommended recipes

### New Python MVP

```text
# Edit this description before running
/target python
/plan mvp <one or two paragraphs of core behavior>
/modules
/build
/specs
```

### New Swift package MVP

```text
/target swift-package AppLogic
/plan mvp <pure logic product brief; no UI>
/modules
/build
```

### Grow an existing graph

```text
# Existing .module files stay; /add updates the ones that need the feature
/add <feature text>
/modules
/build
```

### Pin then expand later

```text
/build
/pin core_loop
# later, after unpin if needed:
# /unpin core_loop
# /add <widening feature>
# /build core_loop
```

## Common mistakes to avoid

- Using `/greenfield` in a script — refuse; put the finished brief on `/plan`
- Expecting a second `/plan` to rewrite existing `.module` files — use `/add`
- Planning separate `Tests` modules on Swift — Mechanicus writes XCTest itself
- `/build <name>` before dependencies are adopted — use full `/build` or order leaves first
- `/clean orphans --apply` or `/clean all` without explicit user approval
- Huge `/plan full` scripts when the user asked for a minimum slice — prefer mvp
- Using `/refactor` to fix incorrect behavior — change the contract, then rebuild
- Asking the user for a module name before searching `.module` files

## How to respond when asked for a script

1. Ask only for missing product facts that change modules (or state assumptions).
2. Emit a complete script in a fenced block the user can save and edit.
3. Keep descriptions concrete and testable.
4. Prefer `/plan mvp` unless the user asked for full product scope.
5. Remind them to run from the **project directory** and review `.module` files before a costly `/build` when the graph is large.

## Further reading

- `docs/OPERATOR-GUIDE.md` — full command reference and lifecycle
- `docs/QUICK-START.md` — install and first run
