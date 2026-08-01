"""Staff-note decks for the sheet music trainer.

Each fact is one note (or a chord) drawn on a treble or bass staff; the
answer is the letter name(s), typed bottom-to-top for chords. The staff is
rendered in text: every line and space is one terminal row, so vertical
position on screen is exactly the thing being learned.

Notes are indexed diatonically (C0 = 0, one step per line-or-space), which
makes staff geometry pure arithmetic: the bottom line of the treble staff
is E4, and every other position is an offset from it.
"""

from dataclasses import dataclass

LETTERS = "CDEFGAB"

# Diatonic index of each clef's bottom staff line.
BOTTOM_LINE = {
    "treble": 4 * 7 + LETTERS.index("E"),   # E4
    "bass":   2 * 7 + LETTERS.index("G"),   # G2
}

# Render geometry. The single-staff view runs from the first ledger line
# above the staff (rel +10) to the first ledger line below (rel -2):
# 13 rows, always all drawn, so the staff never shifts between cards.
REL_TOP, REL_BOTTOM = 10, -2
WIDTH = 15
NOTE_COL = 7
MARGIN = 7  # left margin; the clef name sits here on the middle line

# U+2B24 BLACK LARGE CIRCLE: single-width (keeps rows aligned) but drawn
# much bigger than the ordinary ● in most terminal fonts.
NOTE_HEAD = "⬤"


def note_index(name):
    """Diatonic index of a note name like 'E4'."""
    return int(name[1:]) * 7 + LETTERS.index(name[0])


def note_name(index):
    return f"{LETTERS[index % 7]}{index // 7}"


def describe_position(clef, note):
    rel = note_index(note) - BOTTOM_LINE[clef]
    if rel > 8:
        n = (rel - 8) // 2
        if rel % 2 == 0:
            return f"ledger line {n} above the staff"
        return ("sitting above the staff" if n == 0
                else f"space above ledger line {n}")
    if rel < 0:
        n = -rel // 2
        if rel % 2 == 0:
            return f"ledger line {n} below the staff"
        return ("hanging below the staff" if n == 0
                else f"space below ledger line {n}")
    kind = "line" if rel % 2 == 0 else "space"
    return f"{kind} {rel // 2 + 1}"


def _blank_row(is_line):
    return list(("─" if is_line else " ") * WIDTH)


def _ledger_segment(row):
    for c in range(NOTE_COL - 3, NOTE_COL + 4):
        row[c] = "─"


def render_staff(clef, notes, rel_top=REL_TOP, rel_bottom=REL_BOTTOM):
    """The rows of `clef`'s staff with `notes` (names) drawn on it.

    rel_top/rel_bottom widen the drawn range for decks that go further
    past the staff; every ledger line between the staff and the note is
    drawn, as in real engraving."""
    bottom = BOTTOM_LINE[clef]
    ns = {note_index(x) for x in notes}
    hi, lo = max(ns), min(ns)
    rows = []
    for rel in range(rel_top, rel_bottom - 1, -1):
        p = bottom + rel
        is_line = rel in (0, 2, 4, 6, 8)
        row = _blank_row(is_line)
        # Ledger line: an even position outside the staff gets a short line
        # segment when a note sits on or beyond it.
        if not is_line and rel % 2 == 0:
            if (rel > 8 and hi >= p) or (rel < 0 and lo <= p):
                _ledger_segment(row)
        if p in ns:
            row[NOTE_COL] = NOTE_HEAD
        margin = clef if rel == 4 else ""
        rows.append(f"{margin:>{MARGIN}}  {''.join(row)}")
    return rows


# Grand staff geometry: one continuous pitch axis from the ledger line
# above the treble staff (A5) down to the ledger line below the bass
# staff (E2), middle C on its own ledger line in the gap. 25 rows.
GRAND_TOP = note_index("A5")
GRAND_BOTTOM = note_index("E2")
MIDDLE_C = note_index("C4")
GRAND_LINES = (
    {BOTTOM_LINE["treble"] + r for r in (0, 2, 4, 6, 8)}
    | {BOTTOM_LINE["bass"] + r for r in (0, 2, 4, 6, 8)}
)
GRAND_LABELS = {
    BOTTOM_LINE["treble"] + 4: "treble",
    BOTTOM_LINE["bass"] + 4: "bass",
}


def render_grand(notes, top=GRAND_TOP, bottom=GRAND_BOTTOM):
    """The rows of the grand staff with `notes` (names) drawn on it."""
    ns = {note_index(x) for x in notes}
    hi, lo = max(ns), min(ns)
    top_line = max(GRAND_LINES)
    bottom_line = min(GRAND_LINES)
    rows = []
    for p in range(top, bottom - 1, -1):
        is_line = p in GRAND_LINES
        row = _blank_row(is_line)
        if not is_line:
            if p > top_line and p % 2 == top_line % 2 and hi >= p:
                _ledger_segment(row)          # ledger above the treble staff
            elif p < bottom_line and p % 2 == bottom_line % 2 and lo <= p:
                _ledger_segment(row)          # ledger below the bass staff
            elif p == MIDDLE_C and MIDDLE_C in ns:
                _ledger_segment(row)          # middle C's own ledger line
        if p in ns:
            row[NOTE_COL] = NOTE_HEAD
        margin = GRAND_LABELS.get(p, "")
        rows.append(f"{margin:>{MARGIN}}  {''.join(row)}")
    return rows


@dataclass(frozen=True)
class NoteFact:
    id: str          # stable key used in the progress file, e.g. "treble:E4"
    clef: str
    notes: tuple     # note names bottom-to-top, e.g. ("C4", "E4", "G4")
    answer: str      # letters to type bottom-to-top, e.g. "CEG"
    deck: str

    @property
    def label(self):
        """Short name for summaries, where the staff drawing won't fit."""
        if len(self.notes) == 1:
            return f"{self.clef} {self.notes[0]}"
        return f"{self.clef} {self.notes[0]} triad"

    @property
    def position(self):
        if len(self.notes) == 1:
            return describe_position(self.clef, self.notes[0])
        return f"root on {describe_position(self.clef, self.notes[0])}"

    def render(self, grand=False, span=None):
        """`span` fixes the drawn range for the whole session so cards
        with and without far ledger notes stay the same height: (top,
        bottom) as absolute indices for grand, rel offsets otherwise."""
        if grand:
            if span:
                return render_grand(self.notes, *span)
            return render_grand(self.notes)
        if span:
            return render_staff(self.clef, self.notes, *span)
        return render_staff(self.clef, self.notes)


def _facts(clef, rels, deck):
    bottom = BOTTOM_LINE[clef]
    out = []
    for rel in rels:
        note = note_name(bottom + rel)
        out.append(NoteFact(f"{clef}:{note}", clef, (note,), note[0], deck))
    return out


def _triads(clef, deck):
    """Root-position triads (stacked thirds) rooted on each position from
    the ledger line below the staff up to space 4, so the top note never
    goes past the first ledger line above."""
    bottom = BOTTOM_LINE[clef]
    out = []
    for rel in range(-2, 7):
        names = tuple(note_name(bottom + rel + step) for step in (0, 2, 4))
        answer = "".join(n[0] for n in names)
        out.append(NoteFact(f"{clef}:triad:{names[0]}", clef, names,
                            answer, deck))
    return out


def treble_deck():
    """The 9 staff notes E4-F5: lines EGBDF, spaces FACE."""
    return _facts("treble", range(0, 9), "treble")


def bass_deck():
    """The 9 staff notes G2-A3: lines GBDFA, spaces ACEG."""
    return _facts("bass", range(0, 9), "bass")


def core_deck():
    """Middle C position — the classic beginner hand placement, both
    thumbs on middle C: treble C4-G4 (right hand), bass F3-C4 (left).
    A small active set means each note repeats often enough per session
    to build real speed before the full staves are mixed in. Shares fact
    ids with the other decks, so progress carries over."""
    return (_facts("treble", [-2, -1, 0, 1, 2], "core")
            + _facts("bass", [6, 7, 8, 9, 10], "core"))


def middle_deck():
    """The gap between the staves: D4 and middle C below treble, B3 and
    middle C above bass. Middle C appears as both a treble and a bass
    card on purpose — it is written both ways in real music, and the two
    drawings are the same picture mirrored. Shares fact ids with the
    ledger decks, so progress is one pool."""
    return (_facts("treble", [-1, -2], "middle")
            + _facts("bass", [9, 10], "middle"))


def treble_ledger_deck():
    """Middle C and D below the treble staff, G and A above it."""
    return _facts("treble", [-2, -1, 9, 10], "treble-ledger")


def bass_ledger_deck():
    """E and F below the bass staff, B and middle C above it."""
    return _facts("bass", [-2, -1, 9, 10], "bass-ledger")


def treble_triads_deck():
    return _triads("treble", "treble-triads")


def bass_triads_deck():
    return _triads("bass", "bass-triads")


def treble_high_deck():
    """B5 up to G6: ledger lines 2-4 above treble. Printed music rarely
    goes past the 4th ledger line — beyond that engravers write 8va."""
    return _facts("treble", [11, 12, 13, 14, 15, 16], "treble-high")


def treble_low_deck():
    """B3 down to D3: ledger lines 2-4 below treble."""
    return _facts("treble", [-3, -4, -5, -6, -7, -8], "treble-low")


def bass_high_deck():
    """D4 up to B4: ledger lines 2-4 above bass."""
    return _facts("bass", [11, 12, 13, 14, 15, 16], "bass-high")


def bass_low_deck():
    """D2 down to F1: ledger lines 2-4 below bass."""
    return _facts("bass", [-3, -4, -5, -6, -7, -8], "bass-low")


NOTE_DECKS = {
    "core": core_deck,
    "treble": treble_deck,
    "bass": bass_deck,
    "middle": middle_deck,
    "treble-ledger": treble_ledger_deck,
    "bass-ledger": bass_ledger_deck,
    "treble-triads": treble_triads_deck,
    "bass-triads": bass_triads_deck,
    "treble-high": treble_high_deck,
    "treble-low": treble_low_deck,
    "bass-high": bass_high_deck,
    "bass-low": bass_low_deck,
}

DEFAULT_NOTE_DECKS = ["treble", "bass", "middle"]

NOTE_KEYS = set("ABCDEFGabcdefg")


def get_note_facts(deck_names):
    """Facts for the given decks, deduplicated by id: `middle` overlaps
    the ledger decks (same notes, same ids), so a session that includes
    both must not queue the same card twice."""
    facts, seen = [], set()
    for name in deck_names:
        for f in NOTE_DECKS[name]():
            if f.id not in seen:
                seen.add(f.id)
                facts.append(f)
    return facts


def session_span(facts, grand):
    """Drawn range covering every fact in the session (constant card
    height, so the fixed-position display never jumps). Never narrower
    than the default range."""
    if grand:
        idxs = [note_index(n) for f in facts for n in f.notes]
        return (max(GRAND_TOP, max(idxs)), min(GRAND_BOTTOM, min(idxs)))
    rels = [note_index(n) - BOTTOM_LINE[f.clef]
            for f in facts for n in f.notes]
    return (max(REL_TOP, max(rels)), min(REL_BOTTOM, min(rels)))
