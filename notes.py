#!/usr/bin/env python3
"""Sheet music note trainer: name the note on the staff.

The staff is drawn in the terminal; you type the note's letter name (A-G).
One keystroke, no Enter — the answer submits the instant you press it.

Usage (bare words work — no --flags needed for the common cases):
  notedrill                     # 2-minute session, treble + bass + middle
  notedrill core                # middle C position only — start here
  notedrill treble              # one clef at a time
  notedrill grand               # every card on the grand staff
  notedrill treble bass grand   # combine freely
  notedrill trouble             # your worst notes, picked from progress
  notedrill ledger              # both ledger decks; also: triads, all
  notedrill extended            # far ledger lines (treble-high, bass-low, ...)
  notedrill sprint              # 60s scored run
  notedrill sprint bass         # sprint on one clef
  notedrill stats               # mastery progress and trouble notes
  notedrill reset               # wipe saved note progress
  notedrill --minutes 5 --level automatic   # full flags still available

After a session's summary, press Enter to launch the next session
immediately; any other key stops.

Shares the scheduler, speed levels, and keyboard input with the math
trainer (trainer.py) but keeps its own progress file, so the two programs
never touch each other's stats.

Press Esc (or Ctrl+C) to quit. Progress is saved after every answer.
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path

from keyio import QUIT, raw_available, read_answer, read_key
from scheduler import Progress, build_queue, schedule_next
from staffdecks import (
    DEFAULT_NOTE_DECKS,
    NOTE_DECKS,
    NOTE_KEYS,
    get_note_facts,
    session_span,
)

DEFAULT_DATA_FILE = Path(__file__).resolve().parent / "notes-progress.json"

# MIDI answers are a different skill (notation -> key, not notation ->
# letter), so they get their own progress pool by default.
MIDI_DATA_FILE = Path(__file__).resolve().parent / "notes-midi-progress.json"

# Note-specific speed ladder, tighter than the math one (levels.py):
# the answer is a single keystroke, so almost all of the wall-clock time
# is recognition. ~0.15s of it is pressing the key; the rest is reading.
LEVELS = {
    "foundation": 1.5,   # deliberate but nothing is being counted up
    "fluent":     1.0,   # recognized, not worked out
    "automatic":  0.7,   # sight-reading territory
    "elite":      0.5,   # the note names itself; near reaction-time floor
}
DEFAULT_LEVEL = "fluent"

USE_COLOR = sys.stdout.isatty()
GREEN = "\033[32m" if USE_COLOR else ""
RED = "\033[31m" if USE_COLOR else ""
YELLOW = "\033[33m" if USE_COLOR else ""
DIM = "\033[2m" if USE_COLOR else ""
BOLD = "\033[1m" if USE_COLOR else ""
RESET = "\033[0m" if USE_COLOR else ""

PROMPT_PAD = " " * 9  # aligns the answer prompt with the staff margin


def erase_lines(n):
    """Erase the previous n lines and leave the cursor where they began, so
    each card draws in a fixed screen position."""
    for _ in range(n):
        sys.stdout.write("\x1b[1A\x1b[2K")
    sys.stdout.write("\r")
    sys.stdout.flush()


def settings_path(data_file):
    return Path(data_file).with_name("notes-settings.json")


def load_settings(data_file):
    p = settings_path(data_file)
    if p.exists():
        try:
            d = json.loads(p.read_text())
            if isinstance(d, dict):
                return d
        except (ValueError, OSError):
            pass
    return {}


def save_setting(data_file, key, value):
    s = load_settings(data_file)
    if value is None:
        s.pop(key, None)
    else:
        s[key] = value
    try:
        settings_path(data_file).write_text(json.dumps(s, indent=1))
    except OSError:
        pass


def load_level(data_file):
    saved = load_settings(data_file).get("level")
    return saved if saved in LEVELS else DEFAULT_LEVEL


def save_level(data_file, level):
    save_setting(data_file, "level", level)


def parse_range(spec):
    """'C3:C5' (or 'c3-c5') -> (low_midi, high_midi, 'C3–C5')."""
    import re
    m = re.fullmatch(r"([A-Ga-g][0-8])[:\-]([A-Ga-g][0-8])", spec.strip())
    if not m:
        sys.exit(f"Bad --range '{spec}'. Use note names like C3:C5, "
                 f"or 'off' to clear it.")
    from midiio import name_to_midi
    lo, hi = m.group(1).upper(), m.group(2).upper()
    a, b = name_to_midi(lo), name_to_midi(hi)
    if a > b:
        a, b, lo, hi = b, a, hi, lo
    return a, b, f"{lo}–{hi}"


def resolve_range(args):
    """Keyboard span for exact-pitch MIDI sessions. Remembered like
    --level, so a small keyboard is declared once. Returns None when no
    range is set (or it was cleared with --range off)."""
    if args.range is not None:
        if args.range.lower() in ("off", "none", "full"):
            save_setting(args.data_file, "range", None)
            return None
        rng = parse_range(args.range)
        save_setting(args.data_file, "range", args.range)
        return rng
    saved = load_settings(args.data_file).get("range")
    return parse_range(saved) if saved else None


def fit_to_range(facts, rng):
    """Facts whose every note is playable within (low, high) MIDI pitch."""
    from midiio import name_to_midi
    lo, hi, _ = rng
    return [f for f in facts
            if all(lo <= name_to_midi(n) <= hi for n in f.notes)]


def resolve_threshold(args):
    if args.level is not None:
        save_level(args.data_file, args.level)
        level = args.level
    else:
        level = load_level(args.data_file)
    if args.threshold is not None:
        return args.threshold, f"{args.threshold:.2f}s"
    return LEVELS[level], f"{LEVELS[level]:.2f}s ({level})"


def show_card(fact, grand=False, span=None):
    """Draw one staff card. Returns the number of lines printed."""
    rows = fact.render(grand=grand, span=span)
    print()
    for row in rows:
        print(row)
    print()
    return 2 + len(rows)


def streak_target(progress, fact, args):
    """Fast-in-a-row needed to retire this note. A note that has ever
    been missed carries a much higher bar (--lapse-streak): one mistake
    means the association is shaky, and three quick answers right after
    the correction prove very little."""
    if progress.stats(fact.id)["lapses"] > 0:
        return args.lapse_streak
    return args.streak


def ask(fact, prompt_prefix, enter):
    typed, elapsed = read_answer(
        f"{PROMPT_PAD}{prompt_prefix}{BOLD}name:{RESET} ",
        fact.answer,
        raw=not enter,
        allowed=NOTE_KEYS,
    )
    if typed is QUIT:
        return QUIT, elapsed
    return typed.strip().upper(), elapsed


def open_midi(args):
    """Open the MIDI port when --midi is on; exit with a clear message
    when it cannot be opened."""
    if not getattr(args, "midi", False):
        return None
    import midiio
    try:
        return midiio.MidiIn(args.midi_port)
    except RuntimeError as exc:
        sys.exit(str(exc))


def answer_card(fact, prefix, args, midi):
    """Collect one answer, typed or played. Returns (correct, elapsed);
    `correct` is the QUIT sentinel if the user asked to quit."""
    if midi:
        prompt = f"{PROMPT_PAD}{prefix}{BOLD}play:{RESET} "
        return midi.read(prompt, fact.notes, args.any_octave)
    typed, elapsed = ask(fact, prefix, args.enter)
    if typed is QUIT:
        return QUIT, elapsed
    return typed == fact.answer, elapsed


# Pseudo-deck: not a fixed set of notes, but whichever ones your own
# progress file says are giving you trouble.
TROUBLE = "trouble"

# A note needs this many attempts before it can be called trouble. With
# fewer, its average is one or two answers of noise -- a barely-seen note
# looks identical to a genuinely hard one.
TROUBLE_MIN_ATTEMPTS = 6


def trouble_facts(progress, n):
    """The n worst unmastered notes you have actually attempted, ranked
    by average time weighted by miss rate — so a note that is slow *and*
    error-prone outranks one that is merely slow."""
    scored = []
    for f in get_note_facts(list(NOTE_DECKS)):
        s = progress.data.get(f.id)
        if not s or s["mastered"] or s["attempts"] < TROUBLE_MIN_ATTEMPTS:
            continue
        avg = s["avg_time"] or 0.0
        scored.append((avg * (1 + s["lapses"] / s["attempts"]), f))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [f for _, f in scored[:n]]


def resolve_decks(args, progress):
    """Expand the deck list, replacing the `trouble` pseudo-deck with the
    notes your progress file flags."""
    names = [d for d in args.decks if d != TROUBLE]
    facts = get_note_facts(names) if names else []
    if TROUBLE in args.decks:
        picked = trouble_facts(progress, args.trouble_n)
        if not picked:
            sys.exit("No trouble notes yet — every note you have tried is "
                     "either mastered or unseen. Run a normal drill first.")
        have = {f.id for f in facts}
        facts += [f for f in picked if f.id not in have]
        print(f"{DIM}Trouble deck: {', '.join(f.label for f in picked)}"
              f"{RESET}")
    return facts


def apply_range(facts, args, midi):
    """Trim the session to the keyboard's declared span (exact-pitch MIDI
    only; typed and any-octave sessions always get every card)."""
    if not midi or args.any_octave:
        return facts
    rng = resolve_range(args)
    if not rng:
        return facts
    fit = fit_to_range(facts, rng)
    if not fit:
        sys.exit(f"No notes in these decks fit the keyboard range "
                 f"{rng[2]}. Widen --range, use --any-octave, or pick "
                 f"other decks.")
    print(f"{DIM}Keyboard range {rng[2]}: {len(fit)}/{len(facts)} notes "
          f"fit; the rest are skipped.{RESET}")
    return fit


def cmd_drill(args):
    progress = Progress(args.data_file)
    facts = resolve_decks(args, progress)
    threshold, label = resolve_threshold(args)
    midi = open_midi(args)
    facts = apply_range(facts, args, midi)
    # Back-to-back sessions: after each summary a bare Enter starts the
    # next one, any other key (or Esc) stops.
    while True:
        outcome = run_drill_session(args, facts, progress, threshold, label,
                                    midi)
        if outcome != "done":
            break
        key = read_key(f"{DIM}  ⏎ next session · any other key to stop{RESET} ")
        if key is QUIT or key not in ("\r", "\n"):
            break


def run_drill_session(args, facts, progress, threshold, label, midi=None):
    """One timed session. Returns "done" (ran to its cap), "quit" (Esc
    mid-session), or "empty" (nothing left to drill)."""
    queue, mastered_count = build_queue(facts, progress, maintenance_n=8)

    if not queue:
        print("Every note in these decks is mastered at this level. Try a "
              "faster --level or add more decks (ledger, triads).")
        return "empty"

    if args.minutes > 0:
        cap = f"{args.minutes:g} min"
    elif args.cards > 0:
        cap = f"{args.cards} cards"
    else:
        cap = "until the queue empties"
    print(f"\nNotes: {', '.join(args.decks)}  |  threshold {label}, "
          f"retire after {args.streak} fast in a row "
          f"({args.lapse_streak} for any note ever missed)  |  {cap}")
    print(f"{len(facts) - mastered_count} notes in the learning list, "
          f"{mastered_count} already mastered.")
    if midi:
        octaves = " (any octave)" if args.any_octave else ""
        print(f"{DIM}MIDI: play the note on {midi.name}{octaves}. "
              f"Esc quits.{RESET}")
    elif raw_available() and not args.enter:
        print(f"{DIM}Type the note's letter (a-g) - one key, no Enter. "
              f"Esc quits.{RESET}")
    else:
        print(f"{DIM}Line mode: type the letter and press Enter. "
              f"q quits.{RESET}")

    cards = 0
    n_correct = 0
    times = []
    newly_mastered = []
    demoted = []
    missed = []

    show_full = args.feedback == "full"
    show_errors = args.feedback in ("full", "errors")

    recovery = {}
    paused_total = 0.0

    clear_mode = (raw_available() and not args.enter and not args.scroll
                  and sys.stdout.isatty())
    prev_lines = 0
    span = session_span(facts, args.grand)

    session_start = time.perf_counter()
    deadline = session_start + args.minutes * 60 if args.minutes > 0 else None
    quit_early = False

    while queue:
        if deadline is not None and time.perf_counter() >= deadline:
            print(f"{DIM}  -- time --{RESET}")
            break
        if args.cards > 0 and cards >= args.cards:
            break
        fact = queue.pop(0)
        if clear_mode and prev_lines:
            erase_lines(prev_lines)
        prev_lines = show_card(fact, args.grand, span) + 1  # + answer line
        correct, elapsed = answer_card(fact, "", args, midi)
        if correct is QUIT:
            quit_early = True
            break
        cards += 1
        target = streak_target(progress, fact, args)
        was_new, was_demoted = progress.record(
            fact.id, correct, elapsed, threshold, target
        )
        progress.save()

        fast = correct and elapsed <= threshold

        if correct:
            n_correct += 1
            times.append((elapsed, fact))
            if was_new:
                newly_mastered.append(fact)
                recovery.pop(fact.id, None)
                if show_full:
                    print(f"{PROMPT_PAD}{GREEN}✓ {elapsed:.2f}s — mastered, "
                          f"retired from learning list{RESET}")
                    prev_lines += 1
            elif fast:
                if show_full:
                    streak = progress.stats(fact.id)["fast_streak"]
                    print(f"{PROMPT_PAD}{GREEN}✓ {elapsed:.2f}s{RESET} "
                          f"{DIM}(streak {streak}/{target}){RESET}")
                    prev_lines += 1
                schedule_next(queue, fact, True, True, recovery)
            else:
                if show_full:
                    note = " — back to learning list" if was_demoted else ""
                    print(f"{PROMPT_PAD}{YELLOW}✓ {elapsed:.2f}s — over "
                          f"{threshold:.2f}s, streak reset{note}{RESET}")
                    prev_lines += 1
                if was_demoted:
                    demoted.append(fact)
                schedule_next(queue, fact, True, False, recovery)
        else:
            missed.append(fact)
            if show_errors:
                print(f"{PROMPT_PAD}{RED}✗  {' '.join(fact.answer)} — "
                      f"{fact.clef} {fact.position}{RESET}")
                prev_lines += 1
            if was_demoted:
                demoted.append(fact)
            schedule_next(queue, fact, False, False, recovery)
            if show_errors and args.error_pause > 0:
                time.sleep(args.error_pause)
                paused_total += args.error_pause
                if deadline is not None:
                    deadline += args.error_pause

    session_len = time.perf_counter() - session_start - paused_total

    print(f"\n{BOLD}Session summary{RESET}")
    print(f"  Time           : {session_len / 60:.1f} min")
    print(f"  Cards answered : {cards}", end="")
    if session_len > 0 and cards:
        print(f"  ({cards / (session_len / 60):.0f}/min)")
    else:
        print()
    if cards:
        print(f"  Accuracy       : {n_correct}/{cards} "
              f"({100 * n_correct / cards:.0f}%)")
    if times:
        secs = [t for t, _ in times]
        under = sum(1 for t in secs if t <= threshold)
        print(f"  Avg time       : {sum(secs) / len(secs):.2f}s  "
              f"(fastest {min(secs):.2f}s, slowest {max(secs):.2f}s)")
        print(f"  Under {threshold:.2f}s     : {under}/{len(secs)} "
              f"({100 * under / len(secs):.0f}% of correct answers)")
    if missed:
        seen_ids = set()
        uniq = [f for f in missed
                if not (f.id in seen_ids or seen_ids.add(f.id))]
        print(f"  {RED}Missed         : "
              f"{',  '.join(f'{f.label} ({f.answer})' for f in uniq)}{RESET}")
    worst = {}
    for t, f in times:
        if t > threshold and (f.id not in worst or t > worst[f.id][0]):
            worst[f.id] = (t, f)
    slow = sorted(worst.values(), key=lambda x: x[0], reverse=True)[:3]
    if slow:
        print(f"  {YELLOW}Slowest        : "
              f"{',  '.join(f'{f.label} ({t:.1f}s)' for t, f in slow)}"
              f"{RESET}")
    if newly_mastered:
        print(f"  {GREEN}Newly mastered : "
              f"{', '.join(f.label for f in newly_mastered)}{RESET}")
    if demoted:
        print(f"  {YELLOW}Demoted        : "
              f"{', '.join(f.label for f in demoted)}{RESET}")
    total_mastered = sum(1 for f in facts if progress.is_mastered(f.id))
    print(f"  Mastered       : {total_mastered}/{len(facts)} in these decks\n")
    return "quit" if quit_early else "done"


def cmd_sprint(args):
    progress = Progress(args.data_file)
    facts = resolve_decks(args, progress)
    threshold, _ = resolve_threshold(args)
    midi = open_midi(args)
    facts = apply_range(facts, args, midi)

    print(f"\nSprint: {args.seconds}s, decks: {', '.join(args.decks)}")
    if midi:
        print(f"{DIM}MIDI: play each note on {midi.name}. A wrong note "
              f"re-prompts until you get it. Esc quits.{RESET}")
    elif raw_available() and not args.enter:
        print(f"{DIM}One key per note. A wrong answer re-prompts until you "
              f"get it. Esc quits.{RESET}")
    print(f"{DIM}Press Enter to start.{RESET}")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        return

    while True:
        quit_early = run_sprint_round(args, facts, progress, threshold, midi)
        if quit_early:
            break
        key = read_key(f"{DIM}  ⏎ another sprint · any other key to "
                       f"stop{RESET} ")
        if key is QUIT or key not in ("\r", "\n"):
            break


def run_sprint_round(args, facts, progress, threshold, midi=None):
    """One timed sprint. Returns True if the user quit mid-run."""
    clear_mode = (raw_available() and not args.enter and not args.scroll
                  and sys.stdout.isatty())
    prev_lines = 0
    span = session_span(facts, args.grand)

    end_time = time.perf_counter() + args.seconds
    score = 0
    quit_early = False
    while not quit_early:
        remaining = end_time - time.perf_counter()
        if remaining <= 0:
            break
        fact = random.choice(facts)
        q_start = time.perf_counter()
        first_try = True
        solved = False
        while True:
            remaining = max(0.0, end_time - time.perf_counter())
            if clear_mode and prev_lines:
                erase_lines(prev_lines)
            prev_lines = show_card(fact, args.grand, span) + 1
            ok, _ = answer_card(fact, f"[{remaining:3.0f}s] ", args, midi)
            if ok is QUIT:
                quit_early = True
                break
            if ok:
                solved = True
                break
            first_try = False
        elapsed = time.perf_counter() - q_start
        progress.record(fact.id, solved and first_try, elapsed,
                        threshold, streak_target(progress, fact, args))
        progress.save()
        if solved and time.perf_counter() <= end_time:
            score += 1

    rate = score * 60 / args.seconds if args.seconds else 0
    print(f"\n{BOLD}Sprint score: {score}{RESET} in {args.seconds}s "
          f"(≈ {rate:.0f} notes/min; sight-reading pace starts around 60)\n")
    return quit_early


def cmd_stats(args):
    progress = Progress(args.data_file)
    print()
    all_facts = []
    for name, gen in NOTE_DECKS.items():
        facts = gen()
        all_facts.extend(facts)
        seen = [f for f in facts if f.id in progress.data
                and progress.data[f.id]["attempts"] > 0]
        mastered = [f for f in facts if progress.is_mastered(f.id)]
        line = (f"  {name:<14} {len(mastered):>2}/{len(facts):<2} mastered, "
                f"{len(seen)} seen")
        avg = [progress.data[f.id]["avg_time"] for f in seen
               if progress.data[f.id]["avg_time"] is not None]
        if avg:
            line += f", avg {sum(avg) / len(avg):.2f}s"
        print(line)

    trouble = []
    for f in all_facts:
        s = progress.data.get(f.id)
        if not s or s["attempts"] == 0 or s["mastered"]:
            continue
        trouble.append((s["lapses"], s["avg_time"] or 0.0, f))
    trouble.sort(key=lambda t: (-t[0], -t[1]))
    if trouble:
        print(f"\n  {BOLD}Trouble notes (learning list){RESET}")
        for lapses, avg_t, f in trouble[:10]:
            print(f"    {f.label:<12} {lapses} wrong, avg {avg_t:.2f}s   "
                  f"{DIM}{f.position}{RESET}")
    print()


def cmd_midi(args):
    """List MIDI inputs and echo whatever is played, to verify the setup."""
    import midiio
    try:
        ports = midiio.list_ports()
    except RuntimeError as exc:
        sys.exit(str(exc))
    if not ports:
        print("No MIDI input ports found. Is the keyboard plugged in?")
        return
    print("MIDI inputs: " + ", ".join(ports))
    if not sys.stdin.isatty():
        return
    try:
        m = midiio.MidiIn(args.midi_port)
    except RuntimeError as exc:
        sys.exit(str(exc))
    print(f"Echo test on {m.name} — play some keys; Esc to stop.")
    while True:
        res, _ = m.read("  ", ("C4",), any_octave=False)
        if res is QUIT:
            break


def cmd_reset(args):
    path = Path(args.data_file)
    if not path.exists():
        print("No note progress file to reset.")
        return
    try:
        confirm = input(f"Delete all progress in {path}? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if confirm == "y":
        path.unlink()
        print("Progress reset.")
    else:
        print("Cancelled.")


def parse_decks(values):
    if "all" in values:
        return list(NOTE_DECKS)
    for v in values:
        if v not in NOTE_DECKS and v != TROUBLE:
            sys.exit(f"Unknown deck '{v}'. "
                     f"Available: {', '.join(NOTE_DECKS)}, {TROUBLE}, all")
    return values


def build_parser():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command")

    def common(p, with_cards=False):
        p.add_argument("--decks", nargs="+", default=DEFAULT_NOTE_DECKS,
                       help=f"decks to include: {', '.join(NOTE_DECKS)}, or 'all'")
        p.add_argument("--level", choices=list(LEVELS), default=None,
                       help="speed level; remembered for next time")
        p.add_argument("--threshold", type=float, default=None,
                       help="explicit threshold in seconds; overrides --level")
        p.add_argument("--streak", type=int, default=3,
                       help="fast answers in a row needed to retire a note (default 3)")
        p.add_argument("--lapse-streak", type=int, default=10,
                       help="fast answers in a row needed to retire a note "
                            "that has ever been missed (default 10)")
        p.add_argument("--enter", action="store_true",
                       help="require Enter to submit (line mode)")
        p.add_argument("--data-file", default=str(DEFAULT_DATA_FILE),
                       help="progress file (default: notes-progress.json "
                            "next to this script)")
        p.add_argument("--error-pause", type=float, default=0.7,
                       help="seconds to hold after a wrong answer (default 0.7)")
        p.add_argument("--feedback", choices=["errors", "full", "none"],
                       default="errors",
                       help="per-card output: 'errors' shows only corrections "
                            "(default), 'full' shows every result, 'none' is "
                            "silent until the summary")
        p.add_argument("--scroll", action="store_true",
                       help="keep answered cards on screen instead of the "
                            "fixed-position display")
        p.add_argument("--grand", action="store_true",
                       help="draw every card on the grand staff (both "
                            "staves, like piano music); needs a terminal "
                            "at least ~30 rows tall")
        p.add_argument("--midi", action="store_true",
                       help="answer by playing a MIDI keyboard instead of "
                            "typing; uses its own progress file")
        p.add_argument("--midi-port", default=None,
                       help="substring of the MIDI input port to use "
                            "(default: first real port)")
        p.add_argument("--any-octave", action="store_true",
                       help="MIDI: accept the right letter in any octave, "
                            "for small keyboards that can't span the deck")
        p.add_argument("--trouble-n", type=int, default=8, metavar="N",
                       help="how many notes the 'trouble' deck picks "
                            "(default 8)")
        p.add_argument("--range", default=None, metavar="LOW:HIGH",
                       help="MIDI: your keyboard's span, e.g. C3:C5 — "
                            "exact-pitch sessions then only deal cards you "
                            "can reach. Remembered; 'off' clears it")
        if with_cards:
            p.add_argument("--minutes", type=float, default=2.0,
                           help="session length in minutes, 0 = no time cap "
                                "(default 2)")
            p.add_argument("--cards", type=int, default=0,
                           help="max cards this session, 0 = no card cap")

    p_drill = sub.add_parser("drill", help="spaced-repetition note drill")
    common(p_drill, with_cards=True)
    p_drill.set_defaults(func=cmd_drill)

    p_sprint = sub.add_parser("sprint", help="timed scored run")
    common(p_sprint)
    p_sprint.add_argument("--seconds", type=int, default=60,
                          help="sprint length in seconds (default 60)")
    p_sprint.set_defaults(func=cmd_sprint)

    p_stats = sub.add_parser("stats", help="show mastery progress")
    p_stats.add_argument("--data-file", default=str(DEFAULT_DATA_FILE))
    p_stats.add_argument("--midi", action="store_true",
                         help="show the MIDI progress pool instead")
    p_stats.set_defaults(func=cmd_stats)

    p_reset = sub.add_parser("reset", help="delete saved note progress")
    p_reset.add_argument("--data-file", default=str(DEFAULT_DATA_FILE))
    p_reset.add_argument("--midi", action="store_true",
                         help="reset the MIDI progress pool instead")
    p_reset.set_defaults(func=cmd_reset)

    p_midi = sub.add_parser("midi", help="list MIDI ports and echo-test "
                                         "the keyboard")
    p_midi.add_argument("--midi-port", default=None)
    p_midi.set_defaults(func=cmd_midi)

    return parser, p_drill


# Bare-word shortcuts: deck names, aliases, and "grand" work as plain
# arguments, so `notedrill treble`, `notedrill grand`, or
# `notedrill sprint bass` need no --flags.
SUBCOMMANDS = ("drill", "sprint", "stats", "reset", "midi")
DECK_ALIASES = {
    "ledger": ["treble-ledger", "bass-ledger"],
    "triads": ["treble-triads", "bass-triads"],
    "chords": ["treble-triads", "bass-triads"],
    "extended": ["treble-high", "treble-low", "bass-high", "bass-low"],
}


def rewrite_argv(argv):
    """Translate leading bare words into subcommand/--decks/--grand."""
    head, tail = [], []
    for i, tok in enumerate(argv):
        if tok.startswith("-"):
            tail = argv[i:]
            break
        head.append(tok)

    cmd = None
    decks, flags = [], []
    for tok in head:
        t = tok.lower()
        if t in SUBCOMMANDS and cmd is None:
            cmd = t
        elif t == "grand":
            flags.append("--grand")
        elif t == "all" or t == TROUBLE or t in NOTE_DECKS:
            decks.append(t)
        elif t in DECK_ALIASES:
            decks.extend(DECK_ALIASES[t])
        else:
            sys.exit(f"Unknown word '{tok}'. Try a deck "
                     f"({', '.join(NOTE_DECKS)}), a shortcut "
                     f"({', '.join(DECK_ALIASES)}, {TROUBLE}, all, grand), "
                     f"or a command ({', '.join(SUBCOMMANDS)}).")

    new = [cmd] if cmd else []
    if decks:
        new += ["--decks"] + decks
    return new + flags + tail


def main():
    parser, p_drill = build_parser()
    argv = rewrite_argv(sys.argv[1:])
    if not argv or argv[0].startswith("-"):
        if argv and argv[0] in ("-h", "--help"):
            parser.parse_args(argv)
            return
        args = p_drill.parse_args(argv)
        args.func = cmd_drill
    else:
        args = parser.parse_args(argv)

    if hasattr(args, "decks"):
        args.decks = parse_decks(args.decks)
    # --midi keeps its own progress pool unless a file was named explicitly.
    if (getattr(args, "midi", False)
            and getattr(args, "data_file", None) == str(DEFAULT_DATA_FILE)):
        args.data_file = str(MIDI_DATA_FILE)
    args.func(args)


if __name__ == "__main__":
    main()
