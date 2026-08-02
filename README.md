# sheet-music-trainer

Adaptive sheet music trainer in terminal with treble, bass and grand staff. Midi compatible. Will probably scale to include chords, after that just sit at the keyboard.

The staff is drawn in the terminal; you name the note with one keypress
(a–g) or by playing it on a MIDI keyboard. Answers submit instantly — no
Enter key. Response time drives spaced repetition: notes retire once they
are fast, and come back when they are not.

Python 3, no dependencies (MIDI input needs two optional packages).

## Quick start

```bash
./notedrill
```

A two-minute session on treble, bass, and the notes between them. At the
summary, Enter starts another session and any other key stops. Esc quits
mid-session.

To run it from any directory, add an alias:

```bash
echo "alias notedrill='\"$PWD/notedrill\"'" >> ~/.zshrc && source ~/.zshrc
```

## Commands

Deck names and shortcuts are bare words — no flags needed.

| Command | What it does |
|---|---|
| `notedrill` | 2-minute session, default decks |
| `notedrill core` | middle C position only — the recommended start |
| `notedrill treble` | one clef at a time |
| `notedrill grand` | draw every card on the grand staff |
| `notedrill trouble` | your worst notes, picked from your own stats |
| `notedrill ledger` | both ledger decks (also `triads`, `extended`, `all`) |
| `notedrill sprint` | 60-second scored run |
| `notedrill stats` | mastery progress and trouble notes |
| `notedrill midi` | list MIDI ports and echo-test the keyboard |
| `notedrill reset` | wipe saved progress |

Words combine freely: `notedrill treble bass grand`, `notedrill sprint core`.

Common flags: `--minutes 5`, `--cards 40`, `--level automatic`, `--grand`,
`--midi`.

## Decks

| Deck | Notes |
|---|---|
| `core` | middle C position, F3–G4 — 10 notes, the best starting point |
| `treble` | E4–F5, the nine staff notes |
| `bass` | G2–A3, the nine staff notes |
| `middle` | the gap between the staves: B3, middle C, D4 |
| `treble-ledger`, `bass-ledger` | first ledger line each way |
| `treble-high`, `treble-low` | 2nd–4th ledger lines above/below treble |
| `bass-high`, `bass-low` | 2nd–4th ledger lines above/below bass |
| `treble-triads`, `bass-triads` | root-position triads, answered bottom-to-top (`ceg`) |

Shortcuts: `ledger`, `triads`, `extended`, `all`, `trouble`.

`trouble` is rebuilt from your progress each run — the 8 worst unmastered
notes, ranked by average time weighted by miss rate.

## Speed levels

`--level NAME`, remembered between runs.

| Level | Threshold |
|---|---|
| `foundation` | 1.5s |
| `fluent` | 1.0s (default) |
| `automatic` | 0.7s |
| `elite` | 0.5s |

Thresholds are wall clock, prompt to keypress. A note retires after 3
answers in a row under the threshold — 10 in a row if it has ever been
missed. Answer a retired note slowly and it drops back into rotation.

## MIDI

```bash
python3 -m pip install --user mido python-rtmidi
```

```bash
notedrill midi            # on its own: list ports, echo-test the keyboard
notedrill core midi       # with a deck: drill, answering on the keyboard
notedrill core --midi     # same thing, spelled as a flag
```

`midi` alone is the echo test — it prints the name of each key you press
and never shows a card. Put it next to a deck to actually drill.

Exact pitch is required: E4 on the card means the E above middle C. For a
keyboard too small to span a deck, declare its range once and only
reachable notes are dealt:

```bash
notedrill --midi --range C3:C5
```

`--any-octave` matches the letter in any octave instead. MIDI progress is
kept in a separate file, since reading-to-key and reading-to-letter are
different skills.

## Files

| File | Role |
|---|---|
| `notes.py` | CLI, drill and sprint loops |
| `staffdecks.py` | note decks and staff rendering |
| `keyio.py` | raw keyboard input, auto-submit |
| `midiio.py` | MIDI input |
| `scheduler.py` | timing, mastery, queue order |
