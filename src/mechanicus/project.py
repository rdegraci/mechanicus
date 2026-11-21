"""Per-project target settings in .mechanicus/project.yaml."""
import re
from pathlib import Path

import yaml

from mechanicus.config import log

TARGET_PYTHON = "python"
TARGET_SWIFT_PACKAGE = "swift-package"
_VALID_TARGETS = frozenset({TARGET_PYTHON, TARGET_SWIFT_PACKAGE})
_SWIFT_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def project_yaml_path(root):
    return Path(root).resolve() / ".mechanicus" / "project.yaml"


def default_project_settings():
    return {
        "target": TARGET_PYTHON,
        "package_name": "",
        "package_root": "",
    }


def is_valid_swift_identifier(name):
    text = str(name or "").strip()
    return bool(text) and _SWIFT_IDENTIFIER.fullmatch(text) is not None


def normalize_swift_package_name(name):
    """Return a legal Swift module identifier.

    Hyphens and other punctuation become underscores. Digits at the start
    get a leading underscore. Empty input becomes MyAppLogic.
    """
    text = str(name or "").strip()
    if not text:
        return "MyAppLogic"
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", text)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        return "MyAppLogic"
    if cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    if not is_valid_swift_identifier(cleaned):
        return "MyAppLogic"
    return cleaned


def load_project(root):
    """Return project settings. Missing file means Python defaults."""
    path = project_yaml_path(root)
    settings = default_project_settings()
    if not path.is_file():
        return settings
    try:
        loaded = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as error:
        log.warning("project.yaml unreadable: %s", error)
        return settings
    if not isinstance(loaded, dict):
        log.warning("project.yaml must be a mapping")
        return settings
    target = str(loaded.get("target") or TARGET_PYTHON).strip().lower()
    if target not in _VALID_TARGETS:
        log.warning("project.yaml unknown target %s; using python", target)
        target = TARGET_PYTHON
    settings["target"] = target
    package_name = str(loaded.get("package_name") or "").strip()
    package_root = str(loaded.get("package_root") or "").strip().replace("\\", "/")
    if target == TARGET_SWIFT_PACKAGE:
        raw_name = package_name or "MyAppLogic"
        package_name = normalize_swift_package_name(raw_name)
        if package_name != raw_name:
            log.warning(
                "normalized package_name %s -> %s",
                raw_name,
                package_name,
            )
        if not package_root:
            package_root = package_name
    settings["package_name"] = package_name
    settings["package_root"] = package_root
    return settings


def save_project(root, target, package_name="", package_root=""):
    """Write .mechanicus/project.yaml and return the saved settings."""
    target = str(target).strip().lower()
    if target not in _VALID_TARGETS:
        raise ValueError(f"target must be one of: {', '.join(sorted(_VALID_TARGETS))}")
    package_name = str(package_name or "").strip()
    package_root = str(package_root or "").strip().replace("\\", "/")
    normalized_from = ""
    if target == TARGET_SWIFT_PACKAGE:
        raw_name = package_name or "MyAppLogic"
        package_name = normalize_swift_package_name(raw_name)
        if package_name != raw_name:
            normalized_from = raw_name
        if not is_valid_swift_identifier(package_name):
            raise ValueError(
                f"package_name {raw_name!r} is not a valid Swift module identifier"
            )
        if not package_root:
            package_root = package_name
        relative = Path(package_root)
        if relative.is_absolute() or any(part in ("", "..") for part in relative.parts):
            raise ValueError("package_root must be a relative path without ..")
    else:
        package_name = ""
        package_root = ""
    settings = {
        "target": target,
        "package_name": package_name,
        "package_root": package_root,
    }
    if normalized_from:
        settings["normalized_from"] = normalized_from
    path = project_yaml_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    to_write = {
        "target": settings["target"],
        "package_name": settings["package_name"],
        "package_root": settings["package_root"],
    }
    path.write_text(
        yaml.safe_dump(to_write, default_flow_style=False, sort_keys=False)
    )
    log.info(
        "saved project target=%s package_name=%s package_root=%s",
        target,
        package_name or "(none)",
        package_root or "(none)",
    )
    return settings


def project_target(root):
    return load_project(root)["target"]


def is_swift_package(root):
    return project_target(root) == TARGET_SWIFT_PACKAGE


def project_package_name(root):
    settings = load_project(root)
    if settings["target"] != TARGET_SWIFT_PACKAGE:
        return ""
    return settings["package_name"] or "MyAppLogic"


def project_package_root(root):
    """Relative package_root from project.yaml, or None when unset / Python default."""
    settings = load_project(root)
    text = str(settings.get("package_root") or "").strip()
    if not text or text in (".", "./"):
        if settings["target"] == TARGET_SWIFT_PACKAGE:
            name = settings.get("package_name") or "MyAppLogic"
            return name
        return None
    relative = Path(text)
    if relative.is_absolute() or any(part in ("", "..") for part in relative.parts):
        raise ValueError("package_root must be a relative path without ..")
    return relative.as_posix()
