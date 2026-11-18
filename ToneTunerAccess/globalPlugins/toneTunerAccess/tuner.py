# ToneTunerAccess: what is played and when. Nothing here touches NVDA, so it can be tested alone.
import array
import os
import tempfile
import wave

SOUNDS = os.path.join(os.path.dirname(__file__), 'sounds')

# Strings 1 to 6, from the high E to the low E, in standard tuning.
STRING_FILES = ['string%d.wav' % number for number in range(1, 7)]
CHORD_G_MAJOR = 'chord_g_major.wav'
CHORD_E_MAJOR = 'chord_e_major.wav'
ALL_STRINGS = [5, 4, 3, 2, 1, 0]   # from the 6th string to the 1st

STEP_MS = 1000             # between two strings, when all of them are played
LOOP_DEFAULT_MS = 2000     # between two repetitions of the loop
LOOP_MIN_MS = 1000
LOOP_MAX_MS = 10000
LOOP_STEP_MS = 250

VOLUME_DEFAULT = 100       # percent of the recorded level, which is the loudest possible
VOLUME_MIN = 10
VOLUME_MAX = 100
VOLUME_STEP = 10
CACHE = os.path.join(tempfile.gettempdir(), 'ToneTunerAccess')


def loop_choices():
    """Every pause the loop can have, in milliseconds."""
    return list(range(LOOP_MIN_MS, LOOP_MAX_MS + 1, LOOP_STEP_MS))


def clamp_loop(milliseconds):
    """A pause within the limits, rounded to the nearest step."""
    milliseconds = max(LOOP_MIN_MS, min(LOOP_MAX_MS, int(milliseconds)))
    return LOOP_MIN_MS + round((milliseconds - LOOP_MIN_MS) / LOOP_STEP_MS) * LOOP_STEP_MS


def seconds_value(milliseconds, decimal=','):
    """'2', '2,25', '10' for 2000, 2250, 10000 milliseconds."""
    text = ('%.2f' % (milliseconds / 1000)).rstrip('0').rstrip('.')
    return text.replace('.', decimal)


def clamp_volume(percent):
    percent = max(VOLUME_MIN, min(VOLUME_MAX, int(percent)))
    return VOLUME_MIN + round((percent - VOLUME_MIN) / VOLUME_STEP) * VOLUME_STEP


def volume_choices():
    return list(range(VOLUME_MAX, VOLUME_MIN - 1, -VOLUME_STEP))


def scaled_sound(path, percent, cache=None):
    """The path of the sound at this volume. Quieter copies are made once and kept in a temporary folder.

    Windows plays a sound file as it is, so the volume is set in the file itself.
    """
    cache = cache or CACHE
    percent = clamp_volume(percent)
    if percent >= VOLUME_MAX:
        return path
    stem = os.path.splitext(os.path.basename(path))[0]
    # The size of the original is in the name, so that a sound changed by an update is scaled again.
    target = os.path.join(cache, '%s_%d_%d.wav' % (stem, os.path.getsize(path), percent))
    if os.path.isfile(target):
        return target
    with wave.open(path, 'rb') as source:
        params = source.getparams()
        frames = source.readframes(source.getnframes())
    if params.sampwidth != 2:
        return path   # only 16-bit sounds are scaled; the add-on's sounds all are
    samples = array.array('h')
    samples.frombytes(frames)
    factor = percent / 100
    samples = array.array('h', (int(sample * factor) for sample in samples))
    os.makedirs(cache, exist_ok=True)
    partial = target + '.tmp'
    with wave.open(partial, 'wb') as out:
        out.setparams(params)
        out.writeframes(samples.tobytes())
    os.replace(partial, target)
    return target


def file_of(item):
    """A string number (0 to 5) or a file name."""
    return STRING_FILES[item] if isinstance(item, int) else item


class Player:
    """Plays a sequence of sounds, once or in a loop.

    play(path) starts a sound without waiting, stop() silences it, and schedule(ms, callback)
    calls back later and returns an object with Stop(). They are given by the caller, so that
    the sequencing can be tested without sound or windows.
    """

    def __init__(self, play, stop, schedule):
        self._play, self._stop, self._schedule = play, stop, schedule
        self.queue = []
        self.index = 0
        self.loop = False
        self.loop_ms = LOOP_DEFAULT_MS
        self.volume = VOLUME_DEFAULT
        self._timer = None
        self.on_item = None   # called with each item as it starts, on the first pass only
        self._first_pass = True

    @property
    def playing(self):
        return bool(self.queue)

    def start(self, items):
        self.stop()
        self.queue = list(items)
        self.index = 0
        self._first_pass = True
        self._step()

    def stop(self):
        if self._timer is not None:
            self._timer.Stop()
            self._timer = None
        self._stop()
        self.queue = []
        self.index = 0

    def _step(self):
        self._timer = None
        if not self.queue:
            return
        item = self.queue[self.index]
        try:
            path = scaled_sound(os.path.join(SOUNDS, file_of(item)), self.volume)
        except (OSError, wave.Error, EOFError):
            path = os.path.join(SOUNDS, file_of(item))   # at worst, the sound at its full level
        if not self._play(path):
            self.stop()
            return
        if self._first_pass and self.on_item is not None:
            self.on_item(item)
        if self.index < len(self.queue) - 1:
            self.index += 1
            self._timer = self._schedule(STEP_MS, self._step)
        elif self.loop:
            self.index = 0
            self._first_pass = False
            self._timer = self._schedule(self.loop_ms, self._step)
        else:
            # The last sound goes on by itself: only the sequence is over.
            self.queue = []
            self.index = 0

    def set_loop(self, enabled):
        self.loop = enabled
        if not enabled:
            self.stop()
