"""Post-adopt strengthen pass for thin suites."""
from mechanicus.lifecycle import strengthen_adopted
from mechanicus.models import CodeResult, StrengthenReview
from mechanicus.models import TestsResult as SuiteRewrite
from mechanicus.plan_build import build_one
from mechanicus.store import get_adopted_artifact, get_spec, mark_adopted, save_generation


def _seed_adopted(name="mod"):
    behavior = "Return the sum of two numbers; reject negatives."
    code = "def add(a, b):\n    return a + b\n"
    tests = "import impl\n\ndef test_add():\n    assert impl.add(1, 2) == 3\n"
    check = "import impl\n\ndef test_add():\n    assert impl.add(1, 2) == 3\n"
    spec_id, generation = save_generation(name, behavior, code, tests, check)
    mark_adopted(spec_id, generation)
    return behavior, code, tests, check, generation


def test_strengthen_rewrites_when_review_says_thin(project, monkeypatch, capsys):
    _seed_adopted()
    strong_tests = (
        "import impl\n\n"
        "def test_add():\n    assert impl.add(1, 2) == 3\n\n"
        "def test_rejects_negative():\n"
        "    try:\n        impl.add(-1, 1)\n    except ValueError:\n        return\n"
        "    assert False\n"
    )
    strong_check = (
        "import impl\n\n"
        "def test_sum_workflow():\n"
        "    assert impl.add(2, 3) == 5\n"
        "    try:\n        impl.add(-2, 0)\n    except ValueError:\n        return\n"
        "    assert False\n"
    )
    strong_code = (
        "def add(a, b):\n"
        "    if a < 0 or b < 0:\n"
        "        raise ValueError('negative')\n"
        "    return a + b\n"
    )

    monkeypatch.setattr(
        "mechanicus.lifecycle.review_suite_adequacy",
        lambda *a, **k: StrengthenReview(
            adequate=False, reason="missing_edges", gaps="negatives", explanation="thin"
        ),
    )
    # First sniff: duplicate_check on seeded suite — force review path by clearing thin sniff.
    monkeypatch.setattr("mechanicus.lifecycle.sniff_test_sources", lambda t, c: None)
    monkeypatch.setattr("mechanicus.lifecycle.sniff_suite_thinness", lambda *a, **k: None)
    monkeypatch.setattr(
        "mechanicus.lifecycle.strengthen_tests",
        lambda *a, **k: SuiteRewrite(
            tests=strong_tests, check=strong_check, explanation="stronger"
        ),
    )
    # After rewrite, sniffs must pass.
    monkeypatch.setattr(
        "mechanicus.lifecycle.generate_code",
        lambda *a, **k: CodeResult(code=strong_code, explanation="ok"),
    )
    monkeypatch.setattr(
        "mechanicus.lifecycle.adopt_spec",
        lambda name, modules=None, required=None, allow_longer=False: _fake_adopt(
            name, strong_code, strong_tests, strong_check
        ),
    )

    result = strengthen_adopted("mod", modules=[], required=[])
    assert result == "strengthened"
    out = capsys.readouterr().out
    assert "Strengthened suite adopted" in out
    spec = get_spec("mod")
    assert spec["adopt_reason"] == "strengthen_ok"
    adopted = get_adopted_artifact(spec["id"], "code")
    assert "ValueError" in adopted["content"]


def _fake_adopt(name, code, tests, check):
    behavior = get_spec(name)["behavior"]
    spec_id, generation = save_generation(name, behavior, code, tests, check)
    mark_adopted(spec_id, generation)
    from mechanicus.lifecycle import _note_adopt

    _note_adopt(name, "ok", "ok", "Tests passed. Adopted.", generation)


def test_strengthen_keeps_first_when_rewrite_fails(project, monkeypatch, capsys):
    _, _, _, _, first = _seed_adopted("keep")
    monkeypatch.setattr("mechanicus.lifecycle.sniff_test_sources", lambda t, c: None)
    monkeypatch.setattr(
        "mechanicus.lifecycle.sniff_suite_thinness",
        lambda *a, **k: "duplicate_check",
    )

    def boom(*a, **k):
        raise RuntimeError("model down")

    monkeypatch.setattr("mechanicus.lifecycle.strengthen_tests", boom)
    result = strengthen_adopted("keep", modules=[], required=[])
    assert result == "kept_first"
    assert "Keeping the first adopt" in capsys.readouterr().out
    spec = get_spec("keep")
    assert spec["adopt_reason"] == "strengthen_kept_first"
    assert get_adopted_artifact(spec["id"], "code")["generation"] == first


def test_strengthen_skips_when_adequate(project, monkeypatch, capsys):
    _seed_adopted("okmod")
    monkeypatch.setattr("mechanicus.lifecycle.sniff_test_sources", lambda t, c: None)
    monkeypatch.setattr("mechanicus.lifecycle.sniff_suite_thinness", lambda *a, **k: None)
    monkeypatch.setattr(
        "mechanicus.lifecycle.review_suite_adequacy",
        lambda *a, **k: StrengthenReview(
            adequate=True, reason="ok", gaps="", explanation="fine"
        ),
    )
    result = strengthen_adopted("okmod", modules=[], required=[])
    assert result == "adequate"
    assert "adequate" in capsys.readouterr().out.lower()


def test_build_one_invokes_strengthen(project, monkeypatch):
    calls = {"strengthen": 0}

    def fake_strengthen(name, modules=None, required=None):
        calls["strengthen"] += 1
        return "strengthened"

    monkeypatch.setattr("mechanicus.plan_build.strengthen_adopted", fake_strengthen)
    monkeypatch.setattr(
        "mechanicus.plan_build.spec_command",
        lambda name, prose, modules, canonical, required: _seed_via_spec(
            name, prose, canonical
        ),
    )
    monkeypatch.setattr(
        "mechanicus.plan_build.adopt_spec",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "mechanicus.plan_build.write_adopted",
        lambda root, name: True,
    )
    monkeypatch.setattr(
        "mechanicus.plan_build.dependency_modules",
        lambda root, name: ([], [], []),
    )
    monkeypatch.setattr(
        "mechanicus.plan_build.deps_are_current",
        lambda name, required: True,
    )

    # After spec_command, adopt path needs adopted == latest.
    def after_spec(name, prose, modules, canonical, required):
        _seed_via_spec(name, prose, canonical)

    monkeypatch.setattr("mechanicus.plan_build.spec_command", after_spec)

    record = {"name": "widget", "behavior": "Widget behavior", "text": "Widget behavior", "uses": []}
    # Seed so adopt thinks generation is adopted after fake adopt_spec.
    def adopt_and_mark(name, modules=None, required=None, allow_longer=False):
        spec = get_spec(name)
        if spec is None:
            return
        from mechanicus.store import get_latest_artifact

        latest = get_latest_artifact(spec["id"], "code")
        if latest is not None:
            mark_adopted(spec["id"], latest["generation"])

    monkeypatch.setattr("mechanicus.plan_build.adopt_spec", adopt_and_mark)

    status, reason = build_one(project, record)
    assert status == "built"
    assert calls["strengthen"] == 1
    assert "strengthened" in reason


def _seed_via_spec(name, prose, canonical):
    save_generation(
        name,
        canonical,
        "def widget():\n    return 1\n",
        "import impl\n\ndef test_w():\n    assert impl.widget() == 1\n",
        "import impl\n\ndef test_c():\n    assert impl.widget() == 1\n",
    )
