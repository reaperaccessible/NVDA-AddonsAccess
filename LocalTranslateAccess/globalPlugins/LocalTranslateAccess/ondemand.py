"""Translation on request: pure helpers shared by the translation menu.

Nothing here touches NVDA, so that these rules can be tested outside it.
"""
from collections import deque
import re
import threading
import time

# A model call stays short: the engines translate sentence by sentence and
# Bergamot gives up after a minute on a single call.
CHUNK_LIMIT = 1200
# Above this, or with several lines, the result is shown in a window instead of only spoken.
SPOKEN_LIMIT = 400
HISTORY_LENGTH = 30
# After an action is chosen in the menu, NVDA would re-announce the window, the application
# and the selection as the focus comes back. It stays quiet for this long instead (seconds).
QUIET_AFTER_MENU = 1.0


def split_text(text, limit=CHUNK_LIMIT):
    """Cut a text into pieces of at most `limit` characters, on line then sentence boundaries.

    Joining the pieces gives back the original text exactly.
    """
    pieces = []
    current = ''
    for line in re.findall(r'[^\r\n]*(?:\r\n|\n|\r)|[^\r\n]+', text):
        while len(line) > limit:
            # Prefer the end of a sentence, then a weaker pause, as long as the piece stays sizeable.
            cut = limit
            for marks in (('. ', '! ', '? '), ('; ', ', '), (' ',)):
                found = max(line.rfind(mark, 0, limit) for mark in marks)
                if found >= limit // 2:
                    cut = found + 1
                    break
            if current:
                pieces.append(current)
                current = ''
            pieces.append(line[:cut])
            line = line[cut:]
        if current and len(current) + len(line) > limit:
            pieces.append(current)
            current = ''
        current += line
    if current:
        pieces.append(current)
    return pieces


def choose_languages(source, target, configured_sources, swapped, detect):
    """Decide the direction of a translation on request.

    source: the configured source language, or 'auto'.
    target: the configured target language.
    configured_sources: the source languages prepared for this context, most important first.
    swapped: True when the user inverted the direction.
    detect: callable returning the language of the text, or None when it cannot tell.

    Returns (source, target). Both are equal when nothing has to be translated.
    """
    others = [code for code in configured_sources if code != target]
    # The language to fall back on when the text is already in the target language,
    # and the one the user gets by inverting the direction.
    other = source if source not in ('auto', target) else (others[0] if others else None)
    # The language of the text is always detected, whatever the configured source: the
    # setting only serves when detection cannot tell.
    detected = detect()
    if swapped:
        spoken = detected or target
        wanted = [other, target]
    else:
        spoken = detected or other or target
        wanted = [target, other]
    for goal in wanted:
        if goal and goal != spoken:
            return spoken, goal
    return spoken, spoken


# Below this, the detector's answer is too uncertain to overrule the configured language.
DETECTION_CONFIDENCE = 0.5
# Too short a text gives the detector nothing to work on.
DETECTION_MIN_LETTERS = 12


def trusted_detection(text, code, confidence):
    """The detected language, or None when the detection should not be relied on."""
    letters = sum(1 for character in text if character.isalpha())
    if not code or letters < DETECTION_MIN_LETTERS or confidence < DETECTION_CONFIDENCE:
        return None
    return code


# Continuous translation sees many short labels: before disturbing the user about a missing
# language, the detection must be surer and the text longer than for a translation on request.
CONTINUOUS_CONFIDENCE = 0.8
CONTINUOUS_MIN_LETTERS = 20


def trusted_continuous(text, code, confidence):
    """The detected language when it is sure enough to overrule a configured or declared one
    during continuous translation, else None."""
    letters = sum(1 for character in text if character.isalpha())
    if not code or letters < CONTINUOUS_MIN_LETTERS or (confidence or 0) < CONTINUOUS_CONFIDENCE:
        return None
    return code


def worth_offering(text, origin, confidence, already_offered):
    """Whether a language missing during continuous translation deserves a question.

    origin: how the language was found ('fasttext' for detection, otherwise it was stated).
    already_offered: True when this language was already offered since NVDA started.
    """
    if already_offered:
        return False
    if origin != 'fasttext':
        return True
    letters = sum(1 for character in text if character.isalpha())
    return letters >= CONTINUOUS_MIN_LETTERS and (confidence or 0) >= CONTINUOUS_CONFIDENCE


def needs_window(text):
    return len(text) > SPOKEN_LIMIT or '\n' in text.strip()


class SpeechMemory:
    """Remembers the last things NVDA said, so that one of them can be translated."""

    def __init__(self, length=HISTORY_LENGTH):
        self._items = deque(maxlen=length)
        self._lock = threading.Lock()

    def remember(self, sequence):
        text = ' '.join(item for item in sequence or () if isinstance(item, str)).strip()
        if text:
            with self._lock:
                self._items.append((time.monotonic(), text))

    def latest(self):
        with self._lock:
            return self._items[-1][1] if self._items else ''


class QuietWindow:
    """A short period during which NVDA's own speech is dropped.

    It always ends by itself: it is only a deadline, so nothing has to be restored
    and speech can never stay muted if something goes wrong.
    """
    LONGEST = 3.0   # seconds; a safety limit whatever is asked

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._until = 0.0
        self._held = False

    def open(self, seconds):
        self._until = self._clock() + min(max(seconds, 0.0), self.LONGEST)

    def close(self):
        self._until = 0.0

    def hold(self):
        """The menu is open: its items are spoken normally, but not remembered as announcements."""
        self._held = True

    def release(self):
        self._held = False

    def active(self):
        return self._clock() < self._until

    def remembering_paused(self):
        return self._held or self.active()

