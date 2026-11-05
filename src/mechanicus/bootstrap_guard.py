"""Names that must not be rebuilt through /spec, /build, /generate, or /import."""

BOOTSTRAP_NAMES = frozenset({
    "bootstrap",
    "bootstrap_guard",
    "config",
    "models",
    "paths",
    "seed",
    "seed_import",
    "smoke",
    "store",
    "__main__",
    "__init__",
})


def is_bootstrap_name(name):
    text = str(name).strip().replace("\\", "/")
    if text.endswith(".py"):
        text = text[:-3]
    return text in BOOTSTRAP_NAMES


def refuse_bootstrap(name, action):
    if not is_bootstrap_name(name):
        return False
    print(f"'{name}' is a bootstrap module. It cannot be {action}.")
    return True
