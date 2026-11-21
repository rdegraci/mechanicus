"""Tests for activity spinners on long-running work."""
import io
import time
from contextlib import contextmanager

from mechanicus.activity import ActivitySpinner, activity_spinner, current_spinner
from mechanicus.plan_build import build_command, format_module_text
from mechanicus.paths import write_text_file


class _TTYBuffer(io.StringIO):
    def isatty(self):
        return True


def test_spinner_draws_and_clears():
    stream = _TTYBuffer()
    spinner = ActivitySpinner(message="Building", stream=stream, interval=0.05, enabled=True)
    spinner.start()
    time.sleep(0.12)
    spinner.stop()
    text = stream.getvalue()
    assert "Building" in text
    assert any(frame in text for frame in ("|", "/", "-", "\\"))
    assert text.endswith("\r") or "\r" in text


def test_spinner_stops_on_failure():
    stream = _TTYBuffer()
    stopped = []

    class TrackingSpinner(ActivitySpinner):
        def stop(self):
            stopped.append(True)
            super().stop()

    @contextmanager
    def tracking_spinner(message="Building", stream=None, interval=0.1, enabled=None):
        spinner = TrackingSpinner(
            message=message,
            stream=stream,
            interval=interval,
            enabled=True,
        )
        spinner.start()
        try:
            yield spinner
        finally:
            spinner.stop()

    try:
        with tracking_spinner(stream=stream):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert stopped == [True]


def test_activity_spinner_context_restores_stdout():
    import sys

    original = sys.stdout
    with activity_spinner("Building", stream=_TTYBuffer(), enabled=True):
        assert sys.stdout is not original
    assert sys.stdout is original


def test_nested_spinner_updates_outer_message():
    stream = _TTYBuffer()
    with activity_spinner("Building", stream=stream, enabled=True) as outer:
        assert current_spinner() is outer
        assert outer.message == "Building"
        with activity_spinner("Calling model", stream=stream, enabled=True) as inner:
            assert inner is outer
            assert outer.message == "Calling model"
        assert outer.message == "Building"
    assert current_spinner() is None


def test_set_message_updates_label():
    stream = _TTYBuffer()
    spinner = ActivitySpinner(message="Building", stream=stream, interval=0.05, enabled=True)
    spinner.start()
    time.sleep(0.06)
    spinner.set_message("Adopting")
    time.sleep(0.06)
    spinner.stop()
    assert "Adopting" in stream.getvalue()


def test_build_command_uses_activity_spinner(project, monkeypatch):
    seen = []

    @contextmanager
    def fake_spinner(message="Building", stream=None, interval=0.1, enabled=None):
        seen.append(message)
        yield None

    monkeypatch.setattr("mechanicus.build.activity_spinner", fake_spinner)
    write_text_file(project / "lib.module", format_module_text([], "library"))
    build_command(project, "lib")
    assert seen == ["Building"]


def test_build_command_spinner_stops_when_build_raises(project, monkeypatch):
    stopped = []

    @contextmanager
    def fake_spinner(message="Building", stream=None, interval=0.1, enabled=None):
        try:
            yield None
        finally:
            stopped.append(message)

    monkeypatch.setattr("mechanicus.build.activity_spinner", fake_spinner)

    def boom(*_args, **_kwargs):
        raise RuntimeError("build failed")

    monkeypatch.setattr("mechanicus.build._build_one_module", boom)
    try:
        build_command(project, "lib")
    except RuntimeError:
        pass
    assert stopped == ["Building"]


def test_request_generation_uses_spinner(monkeypatch):
    seen = []

    @contextmanager
    def fake_spinner(message="Working", stream=None, interval=0.1, enabled=None):
        seen.append(message)
        yield None

    class FakeParsed:
        output_parsed = "ok"
        usage = None

    class FakeResponses:
        def parse(self, **_kwargs):
            return FakeParsed()

    class FakeClient:
        def __init__(self):
            self.responses = FakeResponses()

    monkeypatch.setattr("mechanicus.activity.activity_spinner", fake_spinner)
    monkeypatch.setattr("mechanicus.codegen.OpenAI", FakeClient)
    monkeypatch.setattr("mechanicus.codegen.configured_model", lambda: "test-model")
    monkeypatch.setattr("mechanicus.codegen.configured_reasoning_effort", lambda: "low")

    from mechanicus.codegen import _request_generation

    assert _request_generation("hi", "do it", str) == "ok"
    assert seen == ["Calling model"]


def test_plan_command_uses_spinner(project, monkeypatch):
    seen = []

    @contextmanager
    def fake_spinner(message="Working", stream=None, interval=0.1, enabled=None):
        seen.append(message)
        yield None

    class Plan:
        modules = []
        explanation = ""

    monkeypatch.setattr("mechanicus.activity.activity_spinner", fake_spinner)
    monkeypatch.setattr(
        "mechanicus.plan_modes.plan_application",
        lambda *_a, **_k: Plan(),
    )
    monkeypatch.setattr(
        "mechanicus.plan_modes._cover_plan",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        "mechanicus.plan_modes._planned_module_records",
        lambda *_a, **_k: [],
    )

    from mechanicus.plan_modes import plan_command

    plan_command(project, "a tiny app")
    assert "Planning" in seen
