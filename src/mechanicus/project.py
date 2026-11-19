"""Per-project target settings in .mechanicus/project.yaml."""
from pathlib import Path

import yaml

from mechanicus.config import log

TARGET_PYTHON = "python"
TARGET_SWIFT_PACKAGE = "swift-package"
_VALID_TARGETS = frozenset({TARGET_PYTHON, TARGET_SWIFT_PACKAGE})


def project_yaml_path(root):
    return Path(root).resolve() / ".mechanicus" / "project.yaml"


def default_project_settings():
    return {
        "target": TARGET_PYTHON,
        "package_name": "",
        "package_root": "",
    }


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
        if not package_name:
            package_name = "MyAppLogic"
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
    if target == TARGET_SWIFT_PACKAGE:
        if not package_name:
            package_name = "MyAppLogic"
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
    path = project_yaml_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(settings, default_flow_style=False, sort_keys=False)
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
