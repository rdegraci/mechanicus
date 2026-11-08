"""Fixture tests for module DAG arrange and cycle repair."""
from mechanicus.models import CycleRepair, PlannedModule
from mechanicus.plan_build import (
    apply_cycle_repair,
    arrange_modules,
    cyclic_components,
    format_module_text,
    module_records,
    parse_module_text,
    remove_cycle_uses,
    spec_name_for_module,
)
from mechanicus.paths import write_text_file


def _record(name, uses, behavior="do work", path=None, invalid=None):
    return {
        "name": name,
        "path": path,
        "uses": list(uses),
        "invalid": list(invalid or []),
        "behavior": behavior,
        "text": format_module_text(uses, behavior).strip(),
    }


def _write_module(root, name, uses, behavior="do work"):
    path = root / f"{name}.module"
    write_text_file(path, format_module_text(uses, behavior))
    return path


def test_spec_name_for_module():
    assert spec_name_for_module("cli/main.module") == "cli/main"
    assert spec_name_for_module("../x") is None
    assert spec_name_for_module("/abs") is None
    assert spec_name_for_module("has space") is None


def test_parse_and_format_module_text():
    text = format_module_text(["a", "b"], "Return a value")
    uses, behavior, invalid = parse_module_text(text)
    assert uses == ["a", "b"]
    assert behavior == "Return a value"
    assert invalid == []


def test_arrange_modules_topological_order():
    records = [
        _record("app", ["lib"]),
        _record("lib", []),
        _record("tool", ["lib"]),
    ]
    order, skipped = arrange_modules(records)
    names = [record["name"] for record in order]
    assert names.index("lib") < names.index("app")
    assert names.index("lib") < names.index("tool")
    assert skipped == []


def test_arrange_modules_skips_missing_and_blocked():
    records = [
        _record("app", ["missing"]),
        _record("ok", []),
    ]
    order, skipped = arrange_modules(records)
    assert [record["name"] for record in order] == ["ok"]
    assert any("missing" in message for message in skipped)


def test_arrange_modules_reports_cycle():
    records = [
        _record("a", ["b"]),
        _record("b", ["a"]),
    ]
    order, skipped = arrange_modules(records)
    assert order == []
    assert any("use each other" in message for message in skipped)


def test_cyclic_components_finds_scc():
    records = [
        _record("a", ["b"]),
        _record("b", ["a"]),
        _record("c", []),
    ]
    components = cyclic_components(records)
    assert components == [{"a", "b"}]


def test_remove_cycle_uses_breaks_cycle(project):
    path_a = _write_module(project, "a", ["b"])
    path_b = _write_module(project, "b", ["a"])
    members = [
        _record("a", ["b"], path=path_a),
        _record("b", ["a"], path=path_b),
    ]
    assert remove_cycle_uses(project, members) is True
    records = module_records(project)
    by_name = {record["name"]: record for record in records}
    assert cyclic_components(records) == []
    assert not (set(by_name["a"]["uses"]) & {"b"} and set(by_name["b"]["uses"]) & {"a"})


def test_apply_cycle_repair_adds_shared_module(project):
    path_a = _write_module(project, "a", ["b"], "module a")
    path_b = _write_module(project, "b", ["a"], "module b")
    records = module_records(project)
    members = [record for record in records if record["name"] in {"a", "b"}]
    repair = CycleRepair(
        name="shared",
        behavior="shared work",
        uses=[],
        modules=[
            PlannedModule(name="a", behavior="module a uses shared", uses=["shared"]),
            PlannedModule(name="b", behavior="module b uses shared", uses=["shared"]),
        ],
        explanation="Extract shared work.",
    )
    known = {record["name"] for record in records}
    assert apply_cycle_repair(project, records, members, known, repair) is True
    assert (project / "shared.module").is_file()
    updated = module_records(project)
    by_name = {record["name"]: record for record in updated}
    assert "shared" in by_name
    assert by_name["a"]["uses"] == ["shared"]
    assert by_name["b"]["uses"] == ["shared"]
    assert cyclic_components(updated) == []
    order, skipped = arrange_modules(updated)
    assert [record["name"] for record in order][0] == "shared"
    assert skipped == []
    assert path_a.exists() and path_b.exists()
