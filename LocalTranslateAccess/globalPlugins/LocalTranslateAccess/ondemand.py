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
    if swapped:
        back = source if source != 'auto' else (others[0] if others else None)
        return (target, back) if back else (target, target)
    if source == 'auto':
        source = detect() or (others[0] if others else target)
    if source == target and others:
        # The text is already in the target language: translate it the other way round.
        return target, others[0]
    return source, target


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
