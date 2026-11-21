""" /plan mvp vs full: parse, enrich path, description Jev gate."""
from mechanicus.jev import (
    clear_last_jev,
    description_breadth,
    last_jev_result,
)
from mechanicus.models import ApplicationPlan, DescriptionEnrichment, PlannedModule
from mechanicus.plan_build import parse_plan_args, plan_command


def test_parse_plan_args_modes():
    assert parse_plan_args("") == ("mvp", "")
    assert parse_plan_args("mvp") == ("mvp", "")
    assert parse_plan_args("full") == ("full", "")
    assert parse_plan_args("mvp Build a greeter") == ("mvp", "Build a greeter")
    assert parse_plan_args("full Build a greeter") == ("full", "Build a greeter")
    assert parse_plan_args("Build a greeter") == ("mvp", "Build a greeter")


def test_description_breadth_skipped_without_key(project, monkeypatch, capsys):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    clear_last_jev()
    gaps = description_breadth("A tiny app.")
    assert gaps is None
    assert last_jev_result()["status"] == "skipped"
    assert last_jev_result()["gate"] == "description"
    assert "Jev: skipped" in capsys.readouterr().out


def test_description_breadth_reports_gaps(project, monkeypatch, capsys):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")

    def fake_ask(state, questions):
        return {key: {"noul": 0.2} for key in questions}

    monkeypatch.setattr("mechanicus.jev.ask_jev", fake_ask)
    clear_last_jev()
    gaps = description_breadth("Do a thing.")
    assert gaps
    assert "outcomes" in gaps
    outcome = last_jev_result()
    assert outcome["status"] == "ok"
    assert outcome["reason"] == "thin"
    assert outcome["gate"] == "description"
    assert "Description gap" in capsys.readouterr().out


def test_plan_mvp_does_not_enrich(project, monkeypatch, capsys):
    called = {"enrich": 0, "breadth": 0, "mode": None}

    monkeypatch.setattr(
        "mechanicus.plan_build.description_breadth",
        lambda description: called.__setitem__("breadth", called["breadth"] + 1) or [],
    )
    monkeypatch.setattr(
        "mechanicus.plan_build.enrich_application_description",
        lambda description, gaps=None: called.__setitem__("enrich", called["enrich"] + 1),
    )

    def fake_plan(description, mode="mvp"):
        called["mode"] = mode
        return ApplicationPlan(
            modules=[
                PlannedModule(name="greeter", behavior="Return hello", uses=[]),
            ],
            explanation="mvp plan",
        )

    monkeypatch.setattr("mechanicus.plan_build.plan_application", fake_plan)
    monkeypatch.setattr(
        "mechanicus.plan_build._cover_plan",
        lambda root, description, normalized: normalized,
    )
    plan_command(project, "A greeter app", mode="mvp")
    assert called["enrich"] == 0
    assert called["breadth"] == 0
    assert called["mode"] == "mvp"
    assert (project / ".mechanicus" / "application.txt").read_text().strip() == (
        "A greeter app"
    )
    assert not (project / ".mechanicus" / "application.full.txt").exists()
    assert (project / "greeter.module").is_file()
    out = capsys.readouterr().out
    assert "Planning MVP" in out


def test_plan_full_enriches_and_saves_full_brief(project, monkeypatch, capsys):
    called = {"enrich": 0, "plan_desc": None, "mode": None}

    monkeypatch.setattr(
        "mechanicus.plan_build.description_breadth",
        lambda description: (
            ["outcomes", "failures"]
            if "Assumed" not in description and "export" not in description
            else []
        ),
    )

    def fake_enrich(description, gaps=None):
        called["enrich"] += 1
        return DescriptionEnrichment(
            additions=["Export results as CSV", "Reject empty input"],
            enriched_description=(
                description.strip()
                + "\n\nExport results as CSV. Reject empty input."
            ),
            explanation="Filled implied export and validation.",
        )

    monkeypatch.setattr(
        "mechanicus.plan_build.enrich_application_description",
        fake_enrich,
    )

    def fake_plan(description, mode="mvp"):
        called["plan_desc"] = description
        called["mode"] = mode
        return ApplicationPlan(
            modules=[
                PlannedModule(name="core", behavior="Run the product", uses=[]),
            ],
            explanation="full plan",
        )

    monkeypatch.setattr("mechanicus.plan_build.plan_application", fake_plan)
    monkeypatch.setattr(
        "mechanicus.plan_build._cover_plan",
        lambda root, description, normalized: normalized,
    )
    original = "A notebook that stores pages."
    plan_command(project, original, mode="full")
    assert called["enrich"] >= 1
    assert called["mode"] == "full"
    assert "Export results as CSV" in called["plan_desc"]
    assert (project / ".mechanicus" / "application.txt").read_text().strip() == original
    full = (project / ".mechanicus" / "application.full.txt").read_text()
    assert "Export results as CSV" in full
    out = capsys.readouterr().out
    assert "Planning full product" in out
    assert "Assumed additions:" in out
    assert "application.full.txt" in out
