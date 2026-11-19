# AgendaAccess: the sound of the reminders. One of the add-on's own ringtones, or a WAV or MP3 file chosen by the user,
# played by Windows itself (MCI), without any library.
# A sound is written as '' (the chime), 'ringtone:<name>' (a ringtone of the add-on), or the path of a file.
# The ringtones are of two kinds: the ones made for the add-on (RINGTONES, sounds/<name>.wav),
# and the collection in the folders of sounds/ (CATEGORIES), as 'ringtone:alarms/Rush.mp3'.

import ctypes
import os
import winsound

SOUNDS = os.path.join(os.path.dirname(__file__), 'sounds')
# The ringtones made for the add-on (tools/make_sounds_ag.py); the first one is the usual sound.
RINGTONES = ('chime', 'westminster', 'bell', 'marimba', 'harp', 'notification', 'beeps', 'digital', 'gong')
# The folders of the collection, in the order they are listed.
CATEGORIES = ('alarms', 'wakeup', 'notifications', 'soft', 'standard')
PREFIX = 'ringtone:'
EXTENSIONS = ('.wav', '.mp3')
_ALIAS = 'agendaAccessSound'


def ringtone(name):
	return PREFIX + name


def is_file(value):
	return bool(value) and not value.startswith(PREFIX)


def collection():
	"""(category, name, value) of every sound of the collection, by category then by name."""
	found = []
	for category in CATEGORIES:
		folder = os.path.join(SOUNDS, category)
		try:
			names = sorted((n for n in os.listdir(folder) if n.lower().endswith(EXTENSIONS)), key=str.lower)
		except OSError:
			continue
		found += [(category, os.path.splitext(n)[0], ringtone(category + '/' + n)) for n in names]
	return found


def path_of(value):
	"""The file of a sound; the chime for '' or a ringtone that no longer exists."""
	if value and value.startswith(PREFIX):
		name = value[len(PREFIX):]
		if name in RINGTONES:
			value = os.path.join(SOUNDS, name + '.wav')
		else:
			path = os.path.normpath(os.path.join(SOUNDS, name))
			inside = path.startswith(os.path.normpath(SOUNDS) + os.sep)
			value = path if inside and os.path.isfile(path) else ''
	return value or os.path.join(SOUNDS, RINGTONES[0] + '.wav')


def _mci(command):
	buffer = ctypes.create_unicode_buffer(256)
	return ctypes.windll.winmm.mciSendStringW(command, buffer, 256, 0)


def stop():
	try:
		winsound.PlaySound(None, winsound.SND_PURGE)
	except Exception:
		pass
	try:
		_mci('close %s' % _ALIAS)
	except Exception:
		pass


def play(value='', volume=100):
	"""Play a sound at a volume from 0 to 100. A file that cannot be played gives the chime instead, and False."""
	stop()
	path = path_of(value)
	good = True
	if not (os.path.isfile(path) and path.lower().endswith(EXTENSIONS)):
		path, good = path_of(''), False
	if _open(path, volume):
		return good
	if path.lower().endswith('.mp3'):
		path, good = path_of(''), False
		if _open(path, volume):
			return good
	try:   # Windows' MCI not available: the WAV file without volume
		winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
	except Exception:
		return False
	return good


def _open(path, volume):
	"""Play through Windows' MCI, which sets the volume (0 to 1000) of both WAV and MP3."""
	try:
		if _mci('open "%s" type mpegvideo alias %s' % (path, _ALIAS)) != 0:
			return False
		_mci('setaudio %s volume to %d' % (_ALIAS, max(0, min(100, int(volume))) * 10))
		if _mci('play %s' % _ALIAS) == 0:
			return True
	except Exception:
		pass
	stop()
	return False
