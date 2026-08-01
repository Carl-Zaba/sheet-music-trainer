"""Progress tracking and response-time-based scheduling.

Each fact accumulates stats in a JSON file. A fact is "mastered" (removed
from the learning list) once it is answered correctly under the time
threshold `streak_target` times in a row. A wrong answer, or a slow answer,
resets the streak — and demotes a mastered fact back into the learning list.
"""

import json
import os
import random
import sys
import time
from pathlib import Path

EMA_ALPHA = 0.3  # weight of the newest response time in the running average

# Retention schedule for mastered facts: first check ~1 day after mastery,
# doubling on each passed check (1, 2, 4, 8, ... days), capped. A slow or
# wrong answer at any point demotes the fact and the schedule restarts.
DAY_SECONDS = 86400
REVIEW_START_DAYS = 1.0
REVIEW_MAX_DAYS = 60.0

# How soon a card resurfaces in the session queue after an answer.
GAP_SLOW = 8
GAP_FAST = 15

# After a wrong answer a fact enters a short recovery sequence: it comes back
# almost immediately, then at an expanding interval, then rejoins the normal
# rotation -- 2, then 6, then 15. A successful retrieval shortly after an
# error is what keeps the wrong association from setting. The printed
# correction cannot do that job, because by the time it is on screen the
# next prompt already has the user's attention.
RECOVERY_GAPS = [2, 6]


def next_gap(fact_id, correct, fast, recovery):
    """Reinsertion gap for a fact just answered.

    `recovery` is a per-session dict of fact_id -> position in
    RECOVERY_GAPS, mutated in place. Facts not in it use the normal gaps.
    """
    if not correct:
        recovery[fact_id] = 0
        return RECOVERY_GAPS[0]
    if fact_id in recovery:
        step = recovery[fact_id]
        if not fast:
            # Correct but slow: hold at the current short interval rather
            # than promoting, so the fact gets another close repetition.
            return RECOVERY_GAPS[step]
        step += 1
        if step < len(RECOVERY_GAPS):
            recovery[fact_id] = step
            return RECOVERY_GAPS[step]
        del recovery[fact_id]          # recovered; back to normal rotation
        return GAP_FAST
    return GAP_FAST if fast else GAP_SLOW


def blank_stats():
    return {
        "attempts": 0,
        "correct": 0,
        "fast_streak": 0,
        "avg_time": None,
        "mastered": False,
        "lapses": 0,
        "last_seen": None,
    }


class Progress:
    def __init__(self, path):
        self.path = Path(path)
        self.data = {}
        if self.path.exists():
            self.data = self._load()

    def _load(self):
        """Read the progress file, surviving an empty or damaged one.

        Raising here would block every drill until the file was fixed by
        hand, so anything unparseable is moved aside instead of deleted:
        the session starts fresh and the original is still on disk to
        inspect or recover.
        """
        try:
            data = json.loads(self.path.read_text())
        except (ValueError, OSError) as exc:
            return self._quarantine(f"could not be read ({type(exc).__name__})")
        if not isinstance(data, dict):
            return self._quarantine("is not a JSON object")
        return self._normalize(data)

    def _quarantine(self, reason):
        backup = self.path.with_name(self.path.name + ".corrupt")
        try:
            os.replace(self.path, backup)
            note = f"moved it to {backup.name}"
        except OSError:
            note = "could not move it aside"
        print(f"warning: {self.path.name} {reason}; {note}. Starting fresh.",
              file=sys.stderr)
        return {}

    @staticmethod
    def _normalize(data):
        """Fill in missing fields on each entry so a hand-edited file cannot
        crash the drill mid-session. Unknown keys (due, review_interval) are
        preserved; entries that are not objects are dropped."""
        clean = {}
        dropped = 0
        for fact_id, entry in data.items():
            if not isinstance(entry, dict):
                dropped += 1
                continue
            merged = blank_stats()
            merged.update(entry)
            clean[fact_id] = merged
        if dropped:
            print(f"warning: dropped {dropped} malformed entr"
                  f"{'y' if dropped == 1 else 'ies'} from progress file.",
                  file=sys.stderr)
        return clean

    def stats(self, fact_id):
        return self.data.setdefault(fact_id, blank_stats())

    def is_mastered(self, fact_id):
        s = self.data.get(fact_id)
        return bool(s and s.get("mastered"))

    def save(self):
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=1))
        os.replace(tmp, self.path)

    def record(self, fact_id, correct, elapsed, threshold, streak_target):
        """Update stats for one answer. Returns (newly_mastered, demoted)."""
        s = self.stats(fact_id)
        s["attempts"] += 1
        now = time.time()
        s["last_seen"] = time.strftime("%Y-%m-%d %H:%M:%S")
        newly_mastered = False
        demoted = False
        if correct:
            s["correct"] += 1
            prev = s["avg_time"]
            s["avg_time"] = round(
                elapsed if prev is None else (1 - EMA_ALPHA) * prev + EMA_ALPHA * elapsed, 3
            )
            if elapsed <= threshold:
                if s["mastered"]:
                    # A retention check passed at (or past) its due date earns
                    # a doubled interval -- spaced retrieval. Passing early
                    # (e.g. the fact came up in a sprint) leaves the schedule
                    # alone: credit requires the spacing.
                    if now >= s.get("due", 0):
                        interval = min(
                            s.get("review_interval", REVIEW_START_DAYS) * 2,
                            REVIEW_MAX_DAYS,
                        )
                        s["review_interval"] = interval
                        s["due"] = round(now + interval * DAY_SECONDS)
                s["fast_streak"] += 1
                if s["fast_streak"] >= streak_target and not s["mastered"]:
                    s["mastered"] = True
                    newly_mastered = True
                    s["review_interval"] = REVIEW_START_DAYS
                    s["due"] = round(now + REVIEW_START_DAYS * DAY_SECONDS)
            else:
                s["fast_streak"] = 0
                if s["mastered"]:
                    s["mastered"] = False
                    demoted = True
                    s["review_interval"] = REVIEW_START_DAYS
                    s.pop("due", None)
        else:
            s["lapses"] += 1
            s["fast_streak"] = 0
            if s["mastered"]:
                s["mastered"] = False
                demoted = True
                s["review_interval"] = REVIEW_START_DAYS
                s.pop("due", None)
        return newly_mastered, demoted


# Ordering bias. Each fact's sort key is random() * bias, so a smaller bias
# pulls a fact earlier on average without ever fixing the order. Strict
# priority tiers were the old approach and they made every session open with
# the same set of already-seen cards, which starved new material and felt
# rehearsed. These are soft nudges instead.
BIAS_LAPSED = 0.4    # answered wrong at some point: usually early
BIAS_SEEN = 0.8      # seen but not yet mastered: slightly early
BIAS_NEW = 1.0       # never seen


def build_queue(facts, progress, maintenance_n=8):
    """Session queue: unmastered facts in a fresh random order each session,
    biased so trouble facts tend to come earlier, plus retention checks on
    mastered facts that are due (most overdue first, up to maintenance_n).
    A mastered fact whose next check lies in the future is not shown at all:
    re-testing before the interval has elapsed adds nothing to retention."""
    learning, mastered_pool = [], []
    for f in facts:
        (mastered_pool if progress.is_mastered(f.id) else learning).append(f)

    # Facts mastered before due-dates existed have no "due" key; .get(0)
    # makes them due immediately, folding them into the schedule.
    now = time.time()
    due_pool = [f for f in mastered_pool
                if progress.data.get(f.id, {}).get("due", 0) <= now]
    due_pool.sort(key=lambda f: progress.data.get(f.id, {}).get("due", 0))
    maintenance = due_pool[:maintenance_n]

    def sort_key(f):
        s = progress.data.get(f.id)
        if s and s["lapses"] > 0:
            bias = BIAS_LAPSED
        elif s and s["attempts"] > 0:
            bias = BIAS_SEEN
        else:
            bias = BIAS_NEW
        return random.random() * bias

    learning.sort(key=sort_key)

    queue = learning
    for f in maintenance:
        queue.insert(random.randrange(len(queue) + 1), f)
    return queue, len(mastered_pool)


def reinsert(queue, fact, gap):
    queue.insert(min(gap, len(queue)), fact)


def schedule_next(queue, fact, correct, fast, recovery):
    """Reinsert `fact` after an answer, with spread on the normal gaps.

    Recovery gaps stay exact: the come-back-after-2-then-6 rhythm right
    after an error is the mechanism and must not drift. But the normal
    rotation gaps (GAP_SLOW/GAP_FAST) act as a minimum, not a fixed
    position: the card lands at a uniformly random spot no sooner than
    the gap. A fixed position starves everything behind it — when every
    answer is slow (normal while a deck is being learned), each card
    re-enters at position 8, the queue beyond position 8 never advances,
    and the session degenerates into the same few cards cycling.
    """
    gap = next_gap(fact.id, correct, fast, recovery)
    if fact.id in recovery and gap == RECOVERY_GAPS[0]:
        # First post-error retrieval: exactly 2 cards later. This one is
        # the mechanism; it does not drift.
        reinsert(queue, fact, gap)
    elif fact.id in recovery:
        # Later recovery steps keep the short interval but jitter it, so
        # several recovering facts cannot braid into a fixed repeating
        # pattern (miss A, miss B, miss C -> A B C A B C ...).
        reinsert(queue, fact, gap + random.randint(-1, 2))
    else:
        # The nominal gap cannot exceed what the deck can sustain: in a
        # 10-card deck a minimum of 8 leaves the random spread nowhere
        # to go, and every card lands at the back -- a fixed rotation.
        # Half the queue is the widest spacing that still leaves real
        # entropy in the insertion point.
        lo = min(gap, max(2, len(queue) // 2), len(queue))
        queue.insert(random.randint(lo, len(queue)), fact)
