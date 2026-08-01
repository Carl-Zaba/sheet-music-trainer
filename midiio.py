"""MIDI keyboard input for the note trainer.

Answers arrive as note-on events instead of keystrokes: the card shows a
note, you play the key. Exact pitch is required by default — E4 on the
card means the E above middle C on the keyboard — because octave accuracy
is part of the skill. --any-octave relaxes matching to the letter only,
for small keyboards whose two octaves cannot cover a whole deck.

Chord cards collect as many note-ons as the chord has notes; order does
not matter (play it as a chord or rolled), but every pitch must be right.

Requires mido + python-rtmidi (installed per-user via pip). Imported
lazily so the typed-input trainer keeps working without them.
"""

import os
import select
import sys
import time

from keyio import QUIT, raw_available

try:
    import termios
    HAVE_TERMIOS = True
except ImportError:
    HAVE_TERMIOS = False

SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Ports that exist on most Macs but are not instruments.
IGNORED_PORTS = ("IAC", "Through", "Midi Through")


def name_to_midi(name):
    """'C4' -> 60 (middle C), matching the trainer's note names."""
    return (int(name[1:]) + 1) * 12 + SEMITONES[name[0]]


def midi_to_name(n):
    return f"{SHARP_NAMES[n % 12]}{n // 12 - 1}"


def _load_mido():
    try:
        import mido
    except ImportError:
        raise RuntimeError(
            "MIDI support needs the mido library. Install it with:\n"
            "  python3 -m pip install --user --break-system-packages "
            "mido python-rtmidi"
        )
    mido.set_backend("mido.backends.rtmidi")
    return mido


def list_ports():
    return _load_mido().get_input_names()


class MidiIn:
    """One opened MIDI input port."""

    def __init__(self, port_substr=None):
        mido = _load_mido()
        names = mido.get_input_names()
        real = [n for n in names
                if not any(x.lower() in n.lower() for x in IGNORED_PORTS)]
        pool = real or names
        if not pool:
            raise RuntimeError(
                "No MIDI input ports found. Is the keyboard plugged in "
                "and powered on?"
            )
        if port_substr:
            matches = [n for n in pool
                       if port_substr.lower() in n.lower()]
            if not matches:
                raise RuntimeError(
                    f"No MIDI port matching '{port_substr}'. "
                    f"Available: {', '.join(names)}"
                )
            name = matches[0]
        else:
            name = pool[0]
        self.port = mido.open_input(name)
        self.name = name

    def close(self):
        self.port.close()

    def _flush(self):
        """Discard notes played before the prompt appeared, same as the
        keystroke flush in keyio: mashing keys between cards must not
        register as an instant answer."""
        for _ in self.port.iter_pending():
            pass

    def read(self, prompt, expected_names, any_octave=False):
        """Show `prompt`, collect len(expected_names) note-ons, and return
        (correct, elapsed). Returns (QUIT, 0.0) on Esc or Ctrl+C. The
        clock stops on the last note-on."""
        targets = sorted(name_to_midi(n) for n in expected_names)
        want = len(targets)
        self._flush()
        sys.stdout.write(prompt)
        sys.stdout.flush()
        start = time.perf_counter()
        got = []
        # Esc on the computer keyboard still quits; poll stdin alongside
        # the MIDI port when a real terminal is attached.
        use_tty = HAVE_TERMIOS and raw_available()
        saved = None
        if use_tty:
            import tty
            fd = sys.stdin.fileno()
            saved = termios.tcgetattr(fd)
            tty.setcbreak(fd)
            termios.tcflush(fd, termios.TCIFLUSH)
        try:
            while len(got) < want:
                for msg in self.port.iter_pending():
                    if msg.type == "note_on" and msg.velocity > 0:
                        got.append(msg.note)
                        sys.stdout.write(midi_to_name(msg.note) + " ")
                        sys.stdout.flush()
                        if len(got) >= want:
                            break
                if len(got) >= want:
                    break
                if use_tty:
                    r, _, _ = select.select([fd], [], [], 0)
                    if r:
                        ch = os.read(fd, 1).decode("latin-1", "replace")
                        if ch == "\x1b":
                            # swallow the rest of an arrow-key sequence
                            while select.select([fd], [], [], 0.005)[0]:
                                os.read(fd, 1)
                            sys.stdout.write("\n")
                            return QUIT, 0.0
                        if ch in ("\x03", "\x04"):
                            sys.stdout.write("\n")
                            return QUIT, 0.0
                time.sleep(0.004)
        except KeyboardInterrupt:
            sys.stdout.write("\n")
            return QUIT, 0.0
        finally:
            if saved is not None:
                termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        elapsed = time.perf_counter() - start
        sys.stdout.write("\n")
        sys.stdout.flush()
        if any_octave:
            ok = sorted(t % 12 for t in targets) == sorted(g % 12 for g in got)
        else:
            ok = targets == sorted(got)
        return ok, elapsed
