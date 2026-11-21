"""Tests for dependency-aware test generation and repair prompts."""
import mechanicus.codegen as codegen
import mechanicus.lifecycle as lifecycle


def _sample_result():
    return type(
        "R",
        (),
        {
            "tests": "import impl\n\ndef test_x():\n    assert impl.x() == 1\n",
            "check": "import impl\n\ndef test_check():\n    assert impl.x()\n",
            "explanation": "ok",
        },
    )()


def _capture(monkeypatch):
    captured = {}

    def fake_request(user_input, instructions, text_format):
        captured["user_input"] = user_input
        captured["instructions"] = instructions
        captured["text_format"] = text_format
        return _sample_result()

    monkeypatch.setattr(codegen, "_request_generation", fake_request)
    return captured


def test_generate_tests_without_deps_import_only_impl(monkeypatch):
    captured = _capture(monkeypatch)
    codegen.generate_tests("say hello")
    assert "Do not import any module except impl." in captured["instructions"]
    assert "Do not invent neighbor APIs" not in captured["instructions"]
    assert "Module lib:" not in captured["user_input"]
    assert "Required modules" not in captured["user_input"]


def test_generate_tests_with_uses_passes_adopted_sources(monkeypatch):
    captured = _capture(monkeypatch)
    modules = [("lib", "VALUE = 1\n\ndef get():\n    return VALUE\n")]
    codegen.generate_tests("use lib", modules=modules, required=["lib"])
    instructions = captured["instructions"]
    prompt = captured["user_input"]
    assert "Do not import any module except impl." not in instructions
    assert "You may import adopted dependency modules" in instructions
    assert "Do not invent neighbor APIs" in instructions
    assert "must fail when the implementation never calls" in instructions
    assert "Required modules the implementation must call: lib." in prompt
    assert "Module lib:" in prompt
    assert "def get():" in prompt
    assert "Do not invent neighbor APIs" in prompt


def test_repair_tests_with_uses_includes_adopted_sources(monkeypatch):
    captured = _capture(monkeypatch)
    modules = [("lib", "def helper():\n    return 2\n")]
    codegen.repair_tests(
        "use lib",
        "import impl\n\ndef test_x():\n    assert impl.x()\n",
        "import impl\n\ndef test_check():\n    assert impl.x()\n",
        "never calls lib",
        "uses",
        modules=modules,
        required=["lib"],
    )
    instructions = captured["instructions"]
    prompt = captured["user_input"]
    assert "Do not import any module except impl." not in instructions
    assert "may import the adopted dependency modules" in prompt
    assert "clearly wrong relative to the adopted dependency sources" in prompt
    assert "Module lib:" in prompt
    assert "def helper():" in prompt
    assert "Required modules the implementation must call: lib." in prompt


def test_repair_tests_without_deps_stays_impl_only(monkeypatch):
    captured = _capture(monkeypatch)
    codegen.repair_tests(
        "say hello",
        "import impl\n\ndef test_x():\n    assert True\n",
        "import impl\n\ndef test_check():\n    assert True\n",
        "passed",
        "pass",
    )
    assert "Do not import any module except impl." in captured["instructions"]
    assert "They must import impl." in captured["user_input"]
    assert "adopted dependency" not in captured["user_input"]


def test_tests_until_red_forwards_adopted_deps(monkeypatch):
    seen = {}

    def fake_generate(behavior, modules=None, required=None, name=None):
        seen["modules"] = modules
        seen["required"] = required
        return _sample_result()

    monkeypatch.setattr(lifecycle, "generate_tests", fake_generate)
    monkeypatch.setattr(lifecycle, "sniff_test_sources", lambda tests, check: None)
    monkeypatch.setattr(
        lifecycle,
        "run_tests",
        lambda code, tests, check, modules=None: (False, "AssertionError: impl"),
    )
    monkeypatch.setattr(
        lifecycle,
        "classify_before_code_with_reason",
        lambda passed, output, tests, check: ("red", "red"),
    )
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    modules = [("lib", "VALUE = 1\n")]
    written = lifecycle.tests_until_red("use lib", modules=modules, required=["lib"])
    assert written is not None
    assert seen["modules"] == modules
    assert seen["required"] == ["lib"]


def test_tests_until_red_uses_repair_forwards_deps(monkeypatch):
    repairs = []
    calls = {"jev": 0}

    def fake_generate(behavior, modules=None, required=None, name=None):
        return _sample_result()

    def fake_repair(behavior, tests, check, output, kind, modules=None, required=None, name=None):
        repairs.append(
            {
                "kind": kind,
                "modules": modules,
                "required": required,
                "output": output,
            }
        )
        return _sample_result()

    def fake_weak(behavior, tests, check, required, swift=False):
        calls["jev"] += 1
        calls["swift"] = swift
        if calls["jev"] == 1:
            return [("lib", 0.2)]
        return []

    monkeypatch.setattr(lifecycle, "generate_tests", fake_generate)
    monkeypatch.setattr(lifecycle, "repair_tests", fake_repair)
    monkeypatch.setattr(lifecycle, "sniff_test_sources", lambda tests, check: None)
    monkeypatch.setattr(
        lifecycle,
        "run_tests",
        lambda code, tests, check, modules=None: (False, "AssertionError: impl"),
    )
    monkeypatch.setattr(
        lifecycle,
        "classify_before_code_with_reason",
        lambda passed, output, tests, check: ("red", "red"),
    )
    monkeypatch.setattr(lifecycle, "weak_call_tests", fake_weak)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    modules = [("lib", "def helper():\n    return 1\n")]
    written = lifecycle.tests_until_red("use lib", modules=modules, required=["lib"])
    assert written is not None
    assert repairs
    assert repairs[0]["kind"] == "uses"
    assert repairs[0]["modules"] == modules
    assert repairs[0]["required"] == ["lib"]
    assert calls.get("swift") is False


def test_tests_until_red_swift_forwards_jev_swift_flag(project, monkeypatch):
    from mechanicus.project import TARGET_SWIFT_PACKAGE, save_project

    save_project(project, TARGET_SWIFT_PACKAGE, package_name="DemoKit")
    seen = {}

    def fake_generate(behavior, modules=None, required=None, name=None):
        return _sample_result()

    def fake_weak(behavior, tests, check, required, swift=False):
        seen["swift"] = swift
        seen["required"] = required
        return []

    monkeypatch.setattr(lifecycle, "generate_tests", fake_generate)
    monkeypatch.setattr(lifecycle, "weak_call_tests", fake_weak)
    monkeypatch.setattr(
        lifecycle,
        "sniff_swift_test_sources",
        lambda tests, check, root, name, behavior=None: None,
    )
    monkeypatch.setattr(lifecycle, "swift_toolchain_available", lambda: True)
    monkeypatch.setattr(
        lifecycle,
        "write_swift_artifacts",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        lifecycle,
        "run_swift_tests",
        lambda root, name=None: (False, "XCTAssert failed"),
    )
    monkeypatch.setattr(
        lifecycle,
        "classify_swift_before_code",
        lambda passed, output: ("red", "red"),
    )
    monkeypatch.setattr(
        lifecycle,
        "read_swift_artifacts",
        lambda root, name: None,
    )
    modules = [("SFCore/Domain", "public struct SFCoreDomain {}\n")]
    written = lifecycle.tests_until_red(
        "use domain",
        modules=modules,
        required=["SFCore/Domain"],
        name="SFExport/ExportService",
    )
    assert written is not None
    assert seen.get("swift") is True
    assert seen.get("required") == ["SFCore/Domain"]
