"""Import-level smoke checks that do not call external APIs."""
from mechanicus import bootstrap, config, harness, jev, paths, store
from mechanicus.bootstrap_guard import BOOTSTRAP_NAMES, is_bootstrap_name


def main() -> int:
    assert config.startup_dir()
    assert "jev" not in BOOTSTRAP_NAMES
    assert is_bootstrap_name("config")
    assert paths.module_import("jev") == "jev"
    assert harness.classify_before_code(True, "") == "pass"
    assert jev._noul({}, "x") == 0.0
    assert callable(store.init_db)
    assert callable(bootstrap.main)
    print("smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
