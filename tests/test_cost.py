"""OpenAI session cost estimates."""
from types import SimpleNamespace

from mechanicus.cost import (
    estimate_cost,
    format_dollars,
    format_turn_line,
    print_cost_status,
    print_session_estimate,
    record_openai_response,
    reset_session_cost,
    session_total,
    session_turns,
    usage_tokens,
)


def setup_function():
    reset_session_cost()


def test_format_dollars_three_places():
    assert format_dollars(0.055) == "$0.055"
    assert format_dollars(0.275) == "$0.275"
    assert format_dollars(0) == "$0.000"


def test_format_turn_line():
    assert format_turn_line(0.055, 0.275) == "[turn $0.055 · session $0.275]"


def test_usage_tokens_from_response():
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=1000,
            output_tokens=500,
            input_tokens_details=SimpleNamespace(cached_tokens=200),
        )
    )
    assert usage_tokens(response) == {
        "input": 1000,
        "output": 500,
        "cached": 200,
    }


def test_usage_tokens_missing_returns_none():
    assert usage_tokens(SimpleNamespace()) is None


def test_estimate_cost_uses_cached_rate():
    # gpt-6.2-sol: input $2, cached $0.10, output $10 per 1M
    tokens = {"input": 1_000_000, "output": 1_000_000, "cached": 500_000}
    cost = estimate_cost("gpt-6.2-sol", tokens)
    # 500k full input + 500k cached + 1M output
    expected = 500_000 * 2.0 / 1_000_000 + 500_000 * 0.10 / 1_000_000 + 10.0
    assert abs(cost - expected) < 1e-9


def test_record_openai_response_prints_and_accumulates(capsys):
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=1_000_000,
            output_tokens=0,
            input_tokens_details=None,
        )
    )
    turn = record_openai_response("gpt-6.2-sol", response)
    assert turn == 2.0
    assert session_total() == 2.0
    assert session_turns() == 1
    assert capsys.readouterr().out == "[turn $2.000 · session $2.000]\n"

    record_openai_response("gpt-6.2-sol", response)
    assert session_total() == 4.0
    assert session_turns() == 2


def test_record_skips_when_usage_missing(capsys):
    assert record_openai_response("gpt-6.2-sol", SimpleNamespace()) is None
    assert session_turns() == 0
    assert capsys.readouterr().out == ""


def test_print_cost_and_session_estimate(capsys):
    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=1_000_000, output_tokens=0)
    )
    record_openai_response("gpt-6.2-sol", response, print_line=False)
    print_cost_status()
    out = capsys.readouterr().out
    assert "Session estimate $2.000" in out
    assert "1 OpenAI turns" in out
    print_session_estimate()
    assert capsys.readouterr().out == "Session estimate $2.000\n"


def test_request_generation_records_cost(monkeypatch, capsys):
    reset_session_cost()

    class FakeParsed:
        output_parsed = "ok"
        usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=0)

    class FakeResponses:
        def parse(self, **_kwargs):
            return FakeParsed()

    class FakeClient:
        def __init__(self):
            self.responses = FakeResponses()

    monkeypatch.setattr("mechanicus.codegen.OpenAI", FakeClient)
    monkeypatch.setattr("mechanicus.codegen.configured_model", lambda: "gpt-6.2-sol")
    monkeypatch.setattr("mechanicus.codegen.configured_reasoning_effort", lambda: "low")

    from mechanicus.codegen import _request_generation

    assert _request_generation("hi", "do it", str) == "ok"
    assert session_total() == 2.0
    assert "[turn $2.000 · session $2.000]" in capsys.readouterr().out
