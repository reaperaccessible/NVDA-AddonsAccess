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



# --- deciding the language of a text
STRONG = 0.8            # the detector is sure
CLEAR = 0.5             # the detector leans clearly one way...
CLEAR_RATIO = 2.0       # ...when its first guess is at least twice as likely as the second
CONTEXT = 0.15          # enough for a language that was being read just before
PLAUSIBLE = 0.35        # the least to accept a guess when nothing else is known
MIN_LETTERS = 12        # below this, only a sure answer or the context can decide
SHORT_LETTERS = 4       # below this, a text says nothing about its language
RECENT_SECONDS = 120    # how long "just before" lasts


def decide_language(candidates, letters, fallback=None, recent=None):
    """The language of a text, and whether that is sure.

    candidates: the detector's guesses, best first, as (code, probability); the code is
                None for a language the add-on does not know.
    letters:    number of letters in the text.
    fallback:   the language configured for the application or declared by the page, if any.
    recent:     the language surely recognised in the same application a moment ago, if any.

    Returns (code or None, sure). `sure` is True only for a decision solid enough to be
    remembered as context and to justify asking the user about a missing language.
    """
    usable = [(code, probability) for code, probability in candidates if code]
    if not usable:
        return fallback, False
    best, probability = usable[0]
    second = usable[1][1] if len(usable) > 1 else 0.0
    first_is_known = candidates[0][0] == best
    if first_is_known and letters >= SHORT_LETTERS and probability >= STRONG:
        # The detector is sure, even of a few words such as a greeting at the end of a message.
        return best, letters >= CONTINUOUS_MIN_LETTERS
    if letters >= MIN_LETTERS and first_is_known:
        if probability >= CLEAR and probability >= CLEAR_RATIO * second:
            # A sentence quoting a few words of another language lands here. It is not sure
            # enough to ask the user anything, but it does tell what is being read.
            _clear_decisions.add(best)
            return best, False
    if recent and letters >= SHORT_LETTERS:
        # The same message goes on: an unsure sentence keeps the language of the previous one.
        for code, chance in usable:
            if code == recent and (chance >= CONTEXT or code == best):
                return recent, False
    if fallback:
        return fallback, False
    if letters >= MIN_LETTERS and first_is_known and probability >= PLAUSIBLE:
        return best, False
    return None, False


class _ClearDecisions(threading.local):
    """Marks, for the thread deciding, that the last decision leaned clearly one way."""
    language = None
    def add(self, code): self.language = code
    def take(self):
        code, self.language = self.language, None
        return code


_clear_decisions = _ClearDecisions()


def gives_context(code, sure, letters):
    """Whether a decision is solid enough to serve as context for the lines that follow."""
    clear = _clear_decisions.take()
    return bool(code) and letters >= CONTINUOUS_MIN_LETTERS and (sure or clear == code)


class RecentLanguage:
    """The language surely recognised a moment ago, for each application."""

    def __init__(self, clock=time.monotonic, lifetime=RECENT_SECONDS):
        self._clock, self._lifetime = clock, lifetime
        self._seen = {}
        self._lock = threading.Lock()

    def note(self, app, code):
        with self._lock:
            self._seen[app] = (code, self._clock())

    def get(self, app):
        with self._lock:
            entry = self._seen.get(app)
            if entry is None or self._clock() - entry[1] > self._lifetime:
                return None
            return entry[0]


# --- the history of translations made on request
HISTORY_SIZE = 100


class TranslationHistory:
    """The translations made on request since NVDA started, the most recent first. Memory only."""

    def __init__(self, size=HISTORY_SIZE):
        self._items = deque(maxlen=size)
        self._lock = threading.Lock()
        self._cursor = None

    def add(self, original, translation):
        with self._lock:
            self._items.appendleft((original, translation))
            self._cursor = None   # stepping starts again from the newest

    def items(self):
        with self._lock:
            return list(self._items)

    def step(self, older):
        """Move one translation older (or newer). Returns (item, moved): item is None when empty."""
        with self._lock:
            if not self._items:
                return None, False
            if self._cursor is None:
                self._cursor = 0
                return self._items[0], True
            target = self._cursor + (1 if older else -1)
            if not 0 <= target < len(self._items):
                return self._items[self._cursor], False
            self._cursor = target
            return self._items[target], True


# --- favourite languages: "source>target", the source being 'auto' when detected
MAX_FAVORITES = 10


def parse_favorites(values):
    result = []
    for value in values or ():
        if not isinstance(value, str) or '>' not in value:
            continue
        source, target = (part.strip() for part in value.split('>', 1))
        if target and (source, target) not in result:
            result.append((source or 'auto', target))
    return result[:MAX_FAVORITES]


def format_favorites(favorites):
    return [f'{source}>{target}' for source, target in favorites[:MAX_FAVORITES]]


def favorite_key(index):
    """The digit that chooses a favourite in the menu: 1 to 9, then 0 for the tenth."""
    return str((index + 1) % 10)


# --- progress of a translation on request, which can be stopped between two pieces
class Progress:
    def __init__(self):
        self._lock = threading.Lock()
        self.stop = threading.Event()
        self._done = self._total = 0
        self.running = False

    def begin(self, total):
        with self._lock:
            self.stop.clear(); self._done, self._total, self.running = 0, total, True

    def set_total(self, total):
        """Once the text is cut into pieces; a stop asked meanwhile is kept."""
        with self._lock:
            self._total = total

    def advance(self):
        with self._lock:
            self._done += 1

    def end(self):
        with self._lock:
            self.running = False

    def status(self):
        """(done, total, percent), or None when nothing is being translated."""
        with self._lock:
            if not self.running:
                return None
            percent = int(self._done * 100 / self._total) if self._total else 0
            return self._done, self._total, percent


def without_underscores(text):
    return text.replace('_', ' ')
