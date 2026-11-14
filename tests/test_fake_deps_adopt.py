"""Adopt must refuse suites that only pass via fake/monkeypatched deps."""
from mechanicus.harness import real_deps_exercised, run_tests
from mechanicus.lifecycle import adopt_spec
from mechanicus.store import get_adopted_artifact, get_spec, save_generation


LIB_CODE = "def compute(x):\n    return x + 1\n"
IMPL_CODE = "import lib\n\ndef run(x):\n    return lib.compute(x)\n"
REAL_TESTS = (
    "import impl\n\n"
    "def test_run():\n"
    "    assert impl.run(1) == 2\n"
)
REAL_CHECK = (
    "import impl\n\n"
    "def test_check():\n"
    "    assert impl.run(3) == 4\n"
)
FAKE_TESTS = (
    "import impl\n"
    "import lib\n\n"
    "def test_run(monkeypatch):\n"
    "    monkeypatch.setattr(lib, 'compute', lambda x: x + 1)\n"
    "    assert impl.run(1) == 2\n"
)
FAKE_CHECK = (
    "import impl\n"
    "import lib\n\n"
    "def test_check(monkeypatch):\n"
    "    monkeypatch.setattr(lib, 'compute', lambda x: x + 10)\n"
    "    assert impl.run(1) == 11\n"
)
INVENTED_ON_IMPL = (
    "import impl\n\n"
    "def test_invented(monkeypatch):\n"
    "    monkeypatch.setattr(impl, 'helper', lambda: 42, raising=False)\n"
    "    assert impl.helper() == 42\n"
)


def test_real_deps_still_written_and_exercised():
    modules = [("lib", LIB_CODE)]
    passed, _output = run_tests(IMPL_CODE, REAL_TESTS, REAL_CHECK, modules)
    assert passed
    exercised, _detail = real_deps_exercised(
        IMPL_CODE, REAL_TESTS, REAL_CHECK, modules, ["lib"]
    )
    assert exercised


def test_probe_refuses_monkeypatched_dep_api():
    modules = [("lib", LIB_CODE)]
    passed, _output = run_tests(IMPL_CODE, FAKE_TESTS, FAKE_CHECK, modules)
    assert passed  # would previously look adoptable
    exercised, detail = real_deps_exercised(
        IMPL_CODE, FAKE_TESTS, FAKE_CHECK, modules, ["lib"]
    )
    assert not exercised
    assert "real adopted dependencies" in detail


def test_probe_refuses_invented_impl_helpers():
    modules = [("lib", LIB_CODE)]
    # Suite never calls impl.run / lib; invents a helper instead.
    passed, _output = run_tests(IMPL_CODE, INVENTED_ON_IMPL, INVENTED_ON_IMPL, modules)
    assert passed
    exercised, detail = real_deps_exercised(
        IMPL_CODE, INVENTED_ON_IMPL, INVENTED_ON_IMPL, modules, ["lib"]
    )
    assert not exercised
    assert "real adopted dependencies" in detail


def test_probe_skipped_without_required():
    exercised, detail = real_deps_exercised(
        "def run():\n    return 1\n",
        "import impl\n\ndef test_run():\n    assert impl.run() == 1\n",
        "import impl\n\ndef test_check():\n    assert impl.run() == 1\n",
        [],
        [],
    )
    assert exercised
    assert detail == ""


def test_adopt_refuses_fake_dep_suite(project, monkeypatch):
    monkeypatch.setattr("mechanicus.lifecycle.configured_repair_attempts", lambda: 0)
    modules = [("lib", LIB_CODE)]
    save_generation("consumer", "Add one via lib", IMPL_CODE, FAKE_TESTS, FAKE_CHECK)
    adopt_spec("consumer", modules=modules, required=["lib"])
    spec = get_spec("consumer")
    assert spec["adopt_status"] == "failed"
    assert spec["adopt_reason"] == "fake_deps"
    assert get_adopted_artifact(spec["id"], "code") is None


def test_adopt_accepts_real_dep_suite(project, monkeypatch):
    monkeypatch.setattr("mechanicus.lifecycle.configured_repair_attempts", lambda: 0)
    modules = [("lib", LIB_CODE)]
    save_generation("consumer", "Add one via lib", IMPL_CODE, REAL_TESTS, REAL_CHECK)
    adopt_spec("consumer", modules=modules, required=["lib"])
    spec = get_spec("consumer")
    assert spec["adopt_status"] == "ok"
    adopted = get_adopted_artifact(spec["id"], "code")
    assert adopted is not None
    assert "lib.compute" in adopted["content"]


def test_adopt_without_uses_unchanged(project, monkeypatch):
    monkeypatch.setattr("mechanicus.lifecycle.configured_repair_attempts", lambda: 0)
    code = "def greet():\n    return 'hello'\n"
    tests = "import impl\n\ndef test_greet():\n    assert impl.greet() == 'hello'\n"
    check = "import impl\n\ndef test_check():\n    assert impl.greet()\n"
    save_generation("greet", "Return hello", code, tests, check)
    adopt_spec("greet", modules=[], required=[])
    spec = get_spec("greet")
    assert spec["adopt_status"] == "ok"
    assert spec["adopt_reason"] == "ok"
