# Self-modify guide

This guide is for an operator who runs Mechanicus on the Mechanicus repository. The goal is to change one regenerable leaf at a time, keep the bootstrap safe, and recover when a change fails.

For install and API keys, read [QUICK-START.md](QUICK-START.md). For every command, read [OPERATOR-GUIDE.md](OPERATOR-GUIDE.md).

## What self-host means

Mechanicus can treat its own package as the project under the startup directory. Specs and generations live in `.mechanicus/seed.db` in that directory. Adopted code writes under `src/mechanicus/` when `package_root` is set.

The package has two layers:

| Layer | Role |
| --- | --- |
| Bootstrap | Hand-written core. Mechanicus refuses to rebuild these names. |
| Leaf | Regenerable module with a `.module` file. You may import, unpin, change, generate, and pin these. |

Bootstrap names include `bootstrap`, `bootstrap_guard`, `config`, `models`, `paths`, `seed`, `seed_import`, `smoke`, `store`, `__main__`, and `__init__`.

Regenerable leaves with `.module` files today:

- `jev`
- `harness`
- `codegen`
- `lifecycle`
- `plan_build`
- `draft`

Do not rebuild the whole package in one session. Change one leaf. Restart. Check. Then pin.

## Before you start

1. Commit or stash a clean git tree. Git is the outer recovery path.
2. Install the package in editable mode from the repository root.
3. Open `~/Library/Application Support/mechanicus/config.yaml`.
4. Set `package_root` to `src/mechanicus`.
5. Save the file.
6. Go to the Mechanicus repository root.
7. Start Mechanicus: `.venv/bin/python -m mechanicus`.

If `package_root` is empty, Mechanicus writes `.py` and `.module` files at the startup directory. That layout is wrong for this repository. Set `package_root` first.

Spec names stay short. Use `jev`, not `src/mechanicus/jev`.

## Seed the database

`/import` reads the current `.py` file under the code root. It saves that text as a new generation. It marks the generation adopted. It pins the specification. Use `/import` before the first change so SQLite matches disk.

1. Start Mechanicus from the repository root.
2. Enter `/import jev`.
3. Repeat `/import` for each leaf you plan to touch: `harness`, `codegen`, `lifecycle`, `plan_build`, `draft`.

Mechanicus refuses `/import` for a bootstrap name.

## Safe change loop

Use this loop for every leaf change.

1. Enter `/show` and the leaf name. Confirm the adopted generation and the pin flag.
2. Enter `/unpin` and the leaf name.
3. Change the leaf through one of these paths:
   - Edit the `.module` file, then enter `/build` when you want Mechanicus to regenerate from the behavior text.
   - Or edit the `.py` file by hand, then enter `/import` and the name to store that source as the new adopted generation.
4. Enter `/generate` and the leaf name. Confirm the path under `src/mechanicus/`.
5. Enter `/quit`.
6. Run `python -m mechanicus.smoke`.
7. Start Mechanicus again.
8. Exercise a command that uses that leaf.
9. Enter `/pin` and the leaf name.

Prefer `/import` when the leaf already has correct package imports and you only need history and a pin. Prefer `/build` only when you intend the writing model to produce a new implementation from the `.module` text.

The pytest harness still loads generated code as `impl` in a temporary directory. A leaf that imports `mechanicus.config` or other package modules may fail that path. Until package-aware tests exist, treat `/import` as the reliable self-host write path, and treat LLM `/build` of package leaves as experimental.

## Recovery

### Revert one generation

1. Enter `/unpin` and the name when the specification is pinned.
2. Enter `/revert`, the name, and the generation number from `/show`.
3. Enter `/generate` and the name so disk matches the database.
4. Enter `/quit`.
5. Run `python -m mechanicus.smoke`.
6. Start Mechanicus again and check the leaf.
7. Enter `/pin` and the name.

`/revert` does not run pytest. `/revert` does not write disk by itself.

### Git restore

1. Enter `/quit`.
2. Restore the leaf file with git.
3. Start Mechanicus.
4. Enter `/import` and the name so the database matches the restored file.
5. Enter `/pin` and the name when you want the pin back.

## Commands that matter here

| Command | Self-host use |
| --- | --- |
| `/import <name>` | Copy current package source into SQLite. Adopt and pin. |
| `/unpin <name>` | Allow a new generation for that leaf. |
| `/pin <name>` | Freeze the leaf after a good change. |
| `/show <name>` | Read behavior, pin flag, adopted code, and generation numbers. |
| `/generate <name>` | Write adopted code (and tests) under `package_root`. |
| `/revert <name> <n>` | Point adopt at an earlier generation. Then `/generate`. |
| `/build` | Build every `.module` under the code root. Skip pinned leaves. Refuse bootstrap names. |
| `/brownfield` | Inspect this repository while you draft a description. Do not accept a plan that rebuilds bootstrap modules. |

## Rules

- Start Mechanicus from the repository root when `package_root` is `src/mechanicus`.
- Keep bootstrap modules out of `/build` and `/import`. The guard refuses those names.
- Change one leaf per loop.
- Restart the process after `/generate` before you trust the change.
- After `/revert`, always enter `/generate`.
- Keep git clean enough to restore a leaf file if SQLite and disk disagree.

## Smoke check

Run this from the repository root after a generate step:

```text
.venv/bin/python -m mechanicus.smoke
```

The smoke check imports core modules. It does not call OpenAI. It does not call Jev.

## Example: refresh `jev` from disk

1. Set `package_root` to `src/mechanicus`.
2. Go to the repository root.
3. Start Mechanicus.
4. Enter `/import jev`.
5. Enter `/show jev`.
6. Enter `/generate jev`.
7. Enter `/quit`.
8. Run `python -m mechanicus.smoke`.

You now have generation history for `jev`, a pin, and disk written through the self-host path.
