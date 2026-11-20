# AgendaAccess: the other agendas shown with yours. Each one is
#   - a file of AgendaAccess (.json), yours (Work, Family…) or someone else's shared with you, perhaps read only;
#   - an .ics file, read only;
#   - an online agenda (an .ics address given by Outlook, Google…), read only, read again every 30 minutes.
# And the public copy of your agenda, written for the people you share it with: private appointments become "Busy".
# This module does not touch NVDA, so it can be tested alone.

import hashlib
import json
import os
import threading
import time
import urllib.request

from . import agenda, ics

REFRESH = 30 * 60   # seconds between two readings of an online agenda
TIMEOUT = 20


def describe(info):
	"""The settings of an agenda: {'name', 'kind': 'file' or 'url', 'path' or 'url', 'readOnly', 'show', 'reminders'}."""
	info = dict(info)
	info.setdefault('kind', 'file')
	info.setdefault('show', True)
	info['readOnly'] = bool(info.get('readOnly')) or info['kind'] == 'url' or str(info.get('path', '')).lower().endswith('.ics')
	info.setdefault('reminders', not info['readOnly'])
	return info


class IcsSource:
	"""An .ics file or an online .ics agenda: read only, read again when it changes."""

	def __init__(self, info, cache_folder):
		self.info = info
		self.appointments = []
		self.problem = None
		self.newer = False
		self.signature = None
		self.fetched = 0
		self.busy = False
		if info['kind'] == 'url':
			name = hashlib.sha1(info['url'].encode('utf-8')).hexdigest()[:16] + '.ics'
			self.path = os.path.join(cache_folder, name)
		else:
			self.path = info['path']
		self.read()

	def read(self):
		try:
			info = os.stat(self.path)
		except OSError:
			return False
		signature = (info.st_mtime_ns, info.st_size)
		if signature == self.signature:
			return False
		try:
			with open(self.path, encoding='utf-8-sig', errors='replace') as file:
				self.appointments = ics.parse(file.read())
			self.problem = None
		except Exception as error:
			self.problem = error
			return False
		self.signature = signature
		return True

	def fetch(self, done=None):
		"""Download an online agenda in the background, at most every REFRESH seconds."""
		if self.info['kind'] != 'url' or self.busy or time.time() - self.fetched < REFRESH:
			return
		self.busy = True
		self.fetched = time.time()

		def work():
			try:
				address = self.info['url'].strip()
				if address.lower().startswith('webcal://'):
					address = 'https://' + address[9:]
				request = urllib.request.Request(address, headers={'User-Agent': 'AgendaAccess'})
				with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
					data = answer.read()
				if b'BEGIN:VCALENDAR' not in data[:2000].upper():
					raise ValueError('not an .ics agenda')
				os.makedirs(os.path.dirname(self.path), exist_ok=True)
				with open(self.path + '.tmp', 'wb') as file:
					file.write(data)
				os.replace(self.path + '.tmp', self.path)
				self.problem = None
			except Exception as error:
				self.problem = error
			finally:
				self.busy = False
				if done:
					done(self)
		threading.Thread(target=work, daemon=True, name='AgendaAccessFeed').start()

	def find(self, identifier):
		return next((a for a in self.appointments if a['id'] == identifier), None)

	def changed_elsewhere(self):
		return self.read()


def open_agenda(info, backups, cache_folder):
	"""The store of an agenda described by its settings."""
	info = describe(info)
	if info['kind'] == 'url' or info.get('path', '').lower().endswith('.ics'):
		return IcsSource(info, cache_folder)
	# The letters of its name, and a mark of its file: two agendas never share their daily copies.
	label = (''.join(c for c in info['name'] if c.isalnum()) or 'agenda') + hashlib.sha1(info['path'].encode('utf-8')).hexdigest()[:6]
	return agenda.Store(info['path'], None if info['readOnly'] else backups, read_only=info['readOnly'], backup_label=label)


def public_copy(appointments):
	"""The appointments as the people you share your agenda with may see them: a private one is only "Busy"."""
	shown = []
	for appointment in appointments:
		copy = json.loads(json.dumps(appointment))
		copy['reminders'], copy['reminderTimes'], copy['sound'] = [], [], ''
		private = agenda.detail(appointment, 'private')
		if private:
			_hide(copy)
		for original, changes in list((copy.get('overrides') or {}).items()):
			if not isinstance(changes, dict):
				copy['overrides'].pop(original)
				continue
			for name in ('reminders', 'reminderTimes', 'sound'):
				changes.pop(name, None)
			if private or changes.get('private'):
				_hide(changes)   # a day private alone, or a day of a private series
		shown.append(copy)
	return shown


PRIVATE_TEXT = ['Busy']   # set to the translated word by the plugin
HIDDEN = ('note', 'location', 'organizer', 'link', 'category')


def _hide(fields):
	for name in HIDDEN:
		fields[name] = ''
	fields['categories'] = []
	fields['text'] = PRIVATE_TEXT[0]


def write_public_copy(store, path):
	data = {'version': agenda.FORMAT, 'appointments': public_copy(store.appointments), 'deleted': dict(store.data.get('deleted', {}))}
	folder = os.path.dirname(path)
	if folder:
		os.makedirs(folder, exist_ok=True)
	with open(path + '.tmp', 'w', encoding='utf-8') as file:
		json.dump(data, file, ensure_ascii=False, indent=1)
	os.replace(path + '.tmp', path)
