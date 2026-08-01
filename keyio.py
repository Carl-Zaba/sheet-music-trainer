"""Character-at-a-time keyboard input with auto-submit.

Zetamac-style: the answer submits the instant you type the last character.
No Enter key, so keystroke count is the only input overhead.

Auto-submit fires when the typed buffer reaches the length of the expected
answer. That works for any fixed-length answer string, so future decks with
answers like "13/8" or "0.25" need no changes here. Because we know the
expected length, a wrong answer of the right length ("62" for 63) is caught
immediately rather than hanging until you type more.

Falls back to line-based input() when stdin is not a TTY (piped input,
CI, some IDE consoles), so the program still works everywhere.
"""

import os
import select
import sys
import time

try:
    import termios
    import tty
    HAVE_TERMIOS = True
except ImportError:  # Windows
    HAVE_TERMIOS = False

# Characters that may appear in an answer. Extend if a future deck needs
# more (e.g. a space for mixed numbers).
ANSWER_CHARS = set("0123456789./-")

BACKSPACE = ("\x7f", "\x08")
ENTER = ("\r", "\n")
ESCAPE = "\x1b"
CTRL_C = "\x03"
CTRL_D = "\x04"

QUIT = object()  # sentinel returned when the user wants out


def raw_available():
    return HAVE_TERMIOS and sys.stdin.isatty()


class _Cbreak:
    """Put the terminal in cbreak mode: no line buffering, no echo, but
    Ctrl+C still raises KeyboardInterrupt and \\n still works on output."""

    def __enter__(self):
        self.fd = sys.stdin.fileno()
        self.saved = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        return self

    def __exit__(self, *exc):
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
        return False


def _peek_pending(fd, timeout=0.02):
    """True if more input is already waiting (an escape sequence, not a
    deliberate Esc press)."""
    r, _, _ = select.select([fd], [], [], timeout)
    return bool(r)


def _drain_escape(fd):
    """Consume the remainder of a terminal escape sequence."""
    while _peek_pending(fd, 0.005):
        try:
            if os.read(fd, 1) == b"":
                return
        except OSError:
            return


def read_answer(prompt, expected, raw=True, allowed=None):
    """Display `prompt`, collect an answer, return (typed, elapsed_seconds).

    Returns (QUIT, 0.0) if the user asked to quit. The clock starts the
    moment the prompt is on screen and stops on the final keystroke.

    `allowed` restricts which keys count as answer input; defaults to
    ANSWER_CHARS (digits and friends). Decks whose answers are letters —
    e.g. note names A-G — pass their own set.
    """
    if raw and raw_available():
        return _read_raw(prompt, expected, allowed or ANSWER_CHARS)
    return _read_line(prompt)


def _read_raw(prompt, expected, allowed=ANSWER_CHARS):
    target_len = len(expected)
    buf = ""
    with _Cbreak() as term:
        fd = term.fd
        # Discard anything typed before this prompt appeared. Without this,
        # a stray keystroke mashed during the previous card's feedback would
        # be read as an instant answer here, recording a fake ~0.00s time and
        # potentially retiring a fact the user never actually recalled.
        # Note: we read via os.read on the raw fd rather than sys.stdin,
        # because tcflush only clears the kernel's tty buffer -- Python's
        # buffered stdin would hand back stale bytes it had already slurped.
        termios.tcflush(fd, termios.TCIFLUSH)
        sys.stdout.write(prompt)
        sys.stdout.flush()
        start = time.perf_counter()
        while True:
            try:
                data = os.read(fd, 1)
            except OSError:
                data = b""
            if data == b"":
                sys.stdout.write("\n")
                sys.stdout.flush()
                return QUIT, 0.0
            ch = data.decode("latin-1")
            if ch == ESCAPE:
                # Arrow keys and friends arrive as "\x1b[A". Swallow the rest
                # of such a sequence so they do not read as a quit request;
                # a lone Esc really does mean quit.
                if _peek_pending(fd):
                    _drain_escape(fd)
                    continue
                sys.stdout.write("\n")
                sys.stdout.flush()
                return QUIT, 0.0
            if ch in (CTRL_C, CTRL_D):
                sys.stdout.write("\n")
                sys.stdout.flush()
                return QUIT, 0.0
            if ch in BACKSPACE:
                if buf:
                    buf = buf[:-1]
                    # erase one character from the display
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            if ch in ENTER:
                if buf:
                    elapsed = time.perf_counter() - start
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return buf, elapsed
                continue
            if ch not in allowed:
                continue
            buf += ch
            sys.stdout.write(ch)
            sys.stdout.flush()
            if len(buf) >= target_len:
                elapsed = time.perf_counter() - start
                sys.stdout.write("\n")
                sys.stdout.flush()
                return buf, elapsed


def read_key(prompt=""):
    """Wait for a single keypress and return it (e.g. "\\r" for Enter).

    Returns QUIT on Esc, Ctrl+C, or Ctrl+D. Used for between-session
    prompts like "Enter for another round". Falls back to line input when
    raw mode is unavailable: an empty line reads as Enter.
    """
    if not raw_available():
        try:
            line = input(prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            return QUIT
        return line.strip()[:1] or "\r"
    sys.stdout.write(prompt)
    sys.stdout.flush()
    with _Cbreak() as term:
        fd = term.fd
        termios.tcflush(fd, termios.TCIFLUSH)
        try:
            data = os.read(fd, 1)
        except OSError:
            data = b""
        sys.stdout.write("\n")
        sys.stdout.flush()
        if data == b"":
            return QUIT
        ch = data.decode("latin-1")
        if ch == ESCAPE:
            if _peek_pending(fd):
                _drain_escape(fd)  # arrow key etc.: swallow, treat as Esc
            return QUIT
        if ch in (CTRL_C, CTRL_D):
            return QUIT
        return ch


def _read_line(prompt):
    """Fallback: read a whole line, Enter required."""
    start = time.perf_counter()
    while True:
        try:
            raw = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return QUIT, 0.0
        if raw.lower() in ("q", "quit", "exit"):
            return QUIT, 0.0
        if raw == "":
            continue
        return raw, time.perf_counter() - start
