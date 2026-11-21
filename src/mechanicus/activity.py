"""Live activity indicator for long-running operator commands."""
import sys
import threading
from contextlib import contextmanager


_FRAMES = ("|", "/", "-", "\\")
_ACTIVE = []


class _SpinnerAwareStream:
    """Clear the spinner line before another stream writes."""

    def __init__(self, real, spinner):
        self._real = real
        self._spinner = spinner

    def write(self, data):
        if data:
            self._spinner.clear()
        return self._real.write(data)

    def flush(self):
        return self._real.flush()

    def isatty(self):
        return self._real.isatty()

    def __getattr__(self, name):
        return getattr(self._real, name)


class ActivitySpinner:
    """Rotate a one-line indicator while work is in progress."""

    def __init__(self, message="Working", stream=None, interval=0.1, enabled=None):
        self.message = message
        self.stream = sys.stderr if stream is None else stream
        self.interval = interval
        if enabled is None:
            enabled = bool(getattr(self.stream, "isatty", lambda: False)())
        self.enabled = enabled
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._visible = False
        self._frame = 0

    def set_message(self, message):
        """Change the label shown next to the spinner."""
        with self._lock:
            self.message = message
            if self.enabled and self._visible:
                self._draw()

    def start(self):
        if not self.enabled:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="activity-spinner", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=1.0)
        self.clear()

    def clear(self):
        if not self.enabled:
            return
        with self._lock:
            if not self._visible:
                return
            width = len(self.message) + 8
            self.stream.write("\r" + (" " * width) + "\r")
            self.stream.flush()
            self._visible = False

    def _draw(self):
        frame = _FRAMES[self._frame % len(_FRAMES)]
        self.stream.write(f"\r{frame} {self.message}...")
        self.stream.flush()
        self._visible = True
        self._frame += 1

    def _run(self):
        while not self._stop.is_set():
            with self._lock:
                if not self._stop.is_set():
                    self._draw()
            self._stop.wait(self.interval)


def current_spinner():
    """Return the active spinner, or None."""
    return _ACTIVE[-1] if _ACTIVE else None


@contextmanager
def activity_spinner(message="Working", stream=None, interval=0.1, enabled=None):
    """Show a spinner until the block ends, success or failure.

    Nested calls reuse the outer spinner and temporarily change its message.
    Stdout writes clear the spinner line so status prints stay readable.
    When the stream is not a TTY, the spinner is a no-op unless enabled=True.
    """
    if _ACTIVE:
        outer = _ACTIVE[-1]
        previous = outer.message
        outer.set_message(message)
        _ACTIVE.append(outer)
        try:
            yield outer
        finally:
            _ACTIVE.pop()
            if _ACTIVE:
                outer.set_message(previous)
        return

    spinner = ActivitySpinner(
        message=message,
        stream=stream,
        interval=interval,
        enabled=enabled,
    )
    real_stdout = sys.stdout
    if spinner.enabled:
        sys.stdout = _SpinnerAwareStream(real_stdout, spinner)
        spinner.start()
    _ACTIVE.append(spinner)
    try:
        yield spinner
    finally:
        spinner.stop()
        sys.stdout = real_stdout
        if _ACTIVE and _ACTIVE[-1] is spinner:
            _ACTIVE.pop()
