"""Application paths, config.yaml, and session logging."""
import logging
import os
from datetime import datetime
from importlib.resources import files
from pathlib import Path

import yaml

_STARTUP_DIR = None
_LOG_PATH = None
log = logging.getLogger("mechanicus")


def app_dir():
    override = os.environ.get("MECHANICUS_APPDIR")
    if override:
        return Path(override)
    return Path.home() / "Library" / "Application Support" / "mechanicus"


def _example_text(name):
    return (files("mechanicus") / name).read_text()


def _load_env(path):
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def ensure_app_files():
    directory = app_dir()
    directory.mkdir(parents=True, exist_ok=True)
    copies = (
        ("dot_env.example", directory / ".env"),
        ("config.yaml.example", directory / "config.yaml"),
    )
    for source, destination in copies:
        if not destination.exists():
            destination.write_text(_example_text(source))
    _load_env(directory / ".env")
    return directory


def load_config():
    ensure_app_files()
    loaded = yaml.safe_load((app_dir() / "config.yaml").read_text()) or {}
    if not isinstance(loaded, dict):
        raise ValueError("config.yaml must be a mapping")
    return loaded


def configured_model():
    model = load_config().get("model")
    if not model:
        return "gpt-4o"
    return str(model)


def configured_reasoning_effort():
    effort = load_config().get("reasoning_effort")
    if not effort:
        return "medium"
    return str(effort)


def configured_jev_model():
    model = load_config().get("jev_model")
    if not model:
        return "jev-1.14.0"
    return str(model)


def configured_repair_attempts():
    raw = load_config().get("repair_attempts")
    if raw is None or raw == "":
        return 3
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return 3
    if count < 0:
        return 0
    return count


def configured_package_root():
    raw = load_config().get("package_root")
    if raw is None:
        return ""
    text = str(raw).strip().replace("\\", "/")
    if not text or text in (".", "./"):
        return ""
    relative = Path(text)
    if relative.is_absolute() or any(part in ("", "..") for part in relative.parts):
        raise ValueError("package_root must be a relative path without ..")
    return relative.as_posix()


def code_root(root=None):
    """Directory where adopted code and .module files are written.

    Per-project `.mechanicus/project.yaml` package_root wins when set
    (Swift packages). Otherwise the global config.yaml package_root is used.
    """
    base = Path(root).resolve() if root is not None else startup_dir()
    from mechanicus.project import project_package_root

    package = project_package_root(base)
    if package is None:
        package = configured_package_root()
    if not package:
        return base
    destination = (base / package).resolve()
    try:
        destination.relative_to(base)
    except ValueError as error:
        raise ValueError("package_root must stay inside the startup directory") from error
    return destination


def session_log_path():
    return _LOG_PATH


def start_session_log():
    global _LOG_PATH
    directory = app_dir() / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    path = directory / f"{stamp}-{os.getpid()}.log"
    for handler in list(log.handlers):
        handler.close()
        log.removeHandler(handler)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    log.propagate = False
    _LOG_PATH = path
    log.info("session start")
    log.info("startup directory %s", startup_dir())
    log.info("database %s", database_path())
    log.info("model %s", configured_model())
    log.info("reasoning effort %s", configured_reasoning_effort())
    log.info("jev model %s", configured_jev_model())
    try:
        package = configured_package_root()
    except ValueError:
        package = ""
    log.info("package root %s", package or "(startup directory)")
    try:
        from mechanicus.project import load_project

        settings = load_project(startup_dir())
        log.info(
            "project target %s package_name %s package_root %s",
            settings.get("target"),
            settings.get("package_name") or "(none)",
            settings.get("package_root") or "(none)",
        )
    except Exception:
        log.exception("project settings unavailable")
    return path


def close_session_log():
    log.info("session end")
    for handler in list(log.handlers):
        handler.flush()
        handler.close()
        log.removeHandler(handler)


def startup_dir():
    global _STARTUP_DIR
    if _STARTUP_DIR is None:
        _STARTUP_DIR = Path.cwd().resolve()
    return _STARTUP_DIR


def database_path():
    ensure_app_files()
    override = os.environ.get("MECHANICUS_DB")
    if override:
        return Path(override)
    directory = startup_dir() / ".mechanicus"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "seed.db"
