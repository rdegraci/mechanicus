"""Console spacing helpers."""
from mechanicus.console import blank, phase


def test_blank_prints_empty_line(capsys):
    blank()
    assert capsys.readouterr().out == "\n"


def test_phase_blank_then_message(capsys):
    phase("Building widget...")
    assert capsys.readouterr().out == "\nBuilding widget...\n"
