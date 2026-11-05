"""Compatibility facade for the Mechanicus package."""
from mechanicus.bootstrap import main
from mechanicus.repl import repl

__all__ = ["main", "repl"]


if __name__ == "__main__":
    repl()
