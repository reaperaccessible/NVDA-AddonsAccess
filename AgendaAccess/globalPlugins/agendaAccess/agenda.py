# AgendaAccess: the appointments, their repetitions and their reminders, and the file that keeps them.
# This module does not touch NVDA, so it can be tested alone.

import calendar
import datetime
import glob
import json
import os
import shutil
import uuid

# How an appointment repeats. 'every' is the interval: every 2 weeks, every 3 months…
#   none          once
#   days          every N days
#   weeks         every N weeks, on the chosen days of the week
#   months        every N months, on the same day of the month
#   monthWeekday  every N months, on the same day of the week, as "the 2nd Tuesday" or "the last Friday"
#   years         every N years, on the same date
#   yearWeekday   every N years, as "the 2nd Tuesday of November"
KINDS = ('none', 'days', 'weeks', 'months', 'monthWeekday', 'years', 'yearWeekday')
# When a reminder can be given, in minutes before the appointment: up to a week.
REMINDERS = (0, 5, 15, 30, 60, 120, 1440, 2880, 4320, 10080)
DEFAULT_REMINDERS = [15]
UPCOMING_DAYS = 30
# A reminder missed while the computer was asleep is still given if it is at most this late.
LATE_REMINDER = datetime.timedelta(minutes=10)
# How far the keys looking for a day with appointments go, in days.
SEARCH_DAYS = 5 * 366
# Days a deleted appointment is remembered, so that another computer does not bring it back.
FORGET_DELETED = 120
BACKUPS_KEPT = 14


def stamp():
	return datetime.datetime.now().isoformat(timespec='seconds')


def no_repeat():
	return {'kind': 'none', 'every': 1, 'weekdays': [], 'fromEnd': False}


def new_appointment(day, time, text, repeat=None, until=None, count=None, reminders=None, note='', category='', sound=None):
	"""sound: None for the usual sound of the reminders, '' for no sound, or the path of a sound file."""
	repeat = dict(no_repeat(), **(repeat or {}))
	if repeat['kind'] == 'weeks' and not repeat['weekdays']:
		repeat['weekdays'] = [day.weekday()]
	return {
		'id': uuid.uuid4().hex,
		'date': day.isoformat(),
		'time': time.strftime('%H:%M'),
		'text': text.strip(),
		'note': note.strip(),
		'category': category.strip(),
		'repeat': repeat,
		'until': until.isoformat() if until else None,
		'count': count or None,
		'reminders': sorted(set(DEFAULT_REMINDERS if reminders is None else reminders)),
		'sound': sound,
		'skip': [],
		'updated': stamp(),
	}


def start_day(appointment):
	return datetime.date.fromisoformat(appointment['date'])


def start_time(appointment):
	hour, minute = appointment['time'].split(':')
	return datetime.time(int(hour), int(minute))


def until_day(appointment):
	return datetime.date.fromisoformat(appointment['until']) if appointment.get('until') else None


def repeat_of(appointment):
	return dict(no_repeat(), **(appointment.get('repeat') or {}))


def repeats(appointment):
	return repeat_of(appointment)['kind'] != 'none'


# --- the rules of the repetitions

def last_day_of_month(day):
	return calendar.monthrange(day.year, day.month)[1]


def week_of_month(day, from_end=False):
	"""1 for the first Tuesday of the month, 2 for the second…; from the end: -1 for the last, -2 for the one before."""
	if from_end:
		return -((last_day_of_month(day) - day.day) // 7 + 1)
	return (day.day - 1) // 7 + 1


def _same_day_of_month(start, day):
	"""On the 31st: the last day of the shorter months. On February 29: the 28th in the other years."""
	return day.day == min(start.day, last_day_of_month(day))


def _months_between(start, day):
	return (day.year - start.year) * 12 + day.month - start.month


def follows_pattern(appointment, day):
	"""Whether the repetition falls on this day, without its end, its skipped days, or its number of times."""
	start = start_day(appointment)
	if day < start:
		return False
	rule = repeat_of(appointment)
	kind, every = rule['kind'], max(1, int(rule.get('every') or 1))
	if kind == 'none':
		return day == start
	if kind == 'days':
		return (day - start).days % every == 0
	if kind == 'weeks':
		monday = start - datetime.timedelta(days=start.weekday())
		weeks = (day - monday).days // 7
		return day.weekday() in (rule['weekdays'] or [start.weekday()]) and weeks % every == 0
	if kind in ('months', 'monthWeekday'):
		if _months_between(start, day) % every:
			return False
		if kind == 'months':
			return _same_day_of_month(start, day)
		from_end = bool(rule.get('fromEnd'))
		return day.weekday() == start.weekday() and week_of_month(day, from_end) == week_of_month(start, from_end)
	if kind in ('years', 'yearWeekday'):
		if day.month != start.month or (day.year - start.year) % every:
			return False
		if kind == 'years':
			return _same_day_of_month(start, day)
		from_end = bool(rule.get('fromEnd'))
		return day.weekday() == start.weekday() and week_of_month(day, from_end) == week_of_month(start, from_end)
	return False


_last_days = {}


def last_day(appointment):
	"""The last day of the appointment: its end date, or the day of its last time when a number of times is set."""
	end = until_day(appointment)
	count = appointment.get('count')
	if not count:
		return end
	key = (appointment['id'], appointment.get('updated'), appointment['date'], json.dumps(appointment.get('repeat'), sort_keys=True), count)
	if key not in _last_days:
		day, found, last = start_day(appointment), 0, None
		limit = day + datetime.timedelta(days=SEARCH_DAYS * 10)
		while day <= limit and found < count:
			if follows_pattern(appointment, day):
				found += 1
				last = day
			day += datetime.timedelta(days=1)
		_last_days[key] = last
	counted = _last_days[key]
	return min(counted, end) if end and counted else (counted or end)


def occurs_on(appointment, day):
	if day.isoformat() in appointment.get('skip', ()):
		return False
	if not follows_pattern(appointment, day):
		return False
	last = last_day(appointment)
	return last is None or day <= last


def day_appointments(appointments, day):
	"""The appointments of one day, in order of time."""
	found = [a for a in appointments if occurs_on(a, day)]
	return sorted(found, key=lambda a: (a['time'], a['text'].lower()))


def has_appointments(appointments, day):
	return any(occurs_on(a, day) for a in appointments)


def find_day(appointments, day, step, wanted=True, limit=SEARCH_DAYS):
	"""The next day (step days at a time) that has appointments (wanted) or none (not wanted); None if not found."""
	for _ in range(max(1, limit // abs(step))):
		try:
			day += datetime.timedelta(days=step)
		except OverflowError:
			return None
		if has_appointments(appointments, day) == wanted:
			return day
	return None


def next_occurrence(appointment, today):
	"""The first day of the appointment from today on, or None if it is over."""
	day = max(today, start_day(appointment))
	last = last_day(appointment)
	for _ in range(SEARCH_DAYS * 2):
		if last is not None and day > last:
			return None
		if occurs_on(appointment, day):
			return day
		day += datetime.timedelta(days=1)
	return None


def upcoming(appointments, now, days=UPCOMING_DAYS):
	"""(when, appointment) from now on, for the next days, in order."""
	found = []
	for offset in range(days + 1):
		day = now.date() + datetime.timedelta(days=offset)
		for appointment in day_appointments(appointments, day):
			when = datetime.datetime.combine(day, start_time(appointment))
			if when >= now.replace(second=0, microsecond=0):
				found.append((when, appointment))
	return found


def past(appointments, now, days=365):
	"""(when, appointment) of the last days, the most recent first: the journal."""
	found = []
	for offset in range(days + 1):
		day = now.date() - datetime.timedelta(days=offset)
		for appointment in reversed(day_appointments(appointments, day)):
			when = datetime.datetime.combine(day, start_time(appointment))
			if when < now:
				found.append((when, appointment))
	return found


def due_reminders(appointments, since, now):
	"""(when, minutes before, appointment) of the reminders falling after since and up to now.
	Reminders later than LATE_REMINDER (computer asleep, NVDA closed) are not given."""
	since = max(since, now - LATE_REMINDER)
	if since >= now:
		return []
	found = []
	longest = datetime.timedelta(minutes=max(REMINDERS))
	day = since.date()
	while day <= (now + longest).date():
		for appointment in day_appointments(appointments, day):
			when = datetime.datetime.combine(day, start_time(appointment))
			for minutes in appointment.get('reminders', ()):
				ring = when - datetime.timedelta(minutes=minutes)
				if since < ring <= now:
					found.append((when, minutes, appointment))
		day += datetime.timedelta(days=1)
	return sorted(found, key=lambda item: (item[0], -item[1]))


def shift_month(day, months):
	"""The same day some months later or earlier; the last day of a shorter month."""
	index = day.year * 12 + day.month - 1 + months
	year, month = divmod(index, 12)
	month += 1
	return datetime.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def categories(appointments):
	return sorted({a.get('category', '') for a in appointments if a.get('category')}, key=str.lower)


def latest_of_category(appointments, category):
	"""The appointment of this category changed last: its settings serve as a model."""
	found = [a for a in appointments if a.get('category', '').lower() == category.lower()]
	return max(found, key=lambda a: a.get('updated', '')) if found else None


# --- several computers sharing one file (Dropbox, OneDrive…): changes are merged, never overwritten

def merge(mine, theirs):
	"""Both versions of the agenda put together: for each appointment, the one changed last wins;
	an appointment deleted after its last change stays deleted."""
	deleted = dict(mine.get('deleted', {}))
	for identifier, when in theirs.get('deleted', {}).items():
		deleted[identifier] = max(when, deleted.get(identifier, ''))
	appointments = {}
	for appointment in mine.get('appointments', []) + theirs.get('appointments', []):
		known = appointments.get(appointment['id'])
		if known is None or appointment.get('updated', '') > known.get('updated', ''):
			appointments[appointment['id']] = appointment
	kept = [a for a in appointments.values() if deleted.get(a['id'], '') < a.get('updated', '')]
	limit = (datetime.datetime.now() - datetime.timedelta(days=FORGET_DELETED)).isoformat(timespec='seconds')
	return {'version': 2, 'appointments': kept, 'deleted': {i: w for i, w in deleted.items() if w >= limit}}


def _read(path):
	with open(path, encoding='utf-8') as file:
		data = json.load(file)
	if not isinstance(data, dict):
		raise ValueError('not an agenda')
	data['appointments'] = [a for a in data.get('appointments', []) if isinstance(a, dict) and 'id' in a and 'date' in a]
	data.setdefault('deleted', {})
	return data


def _signature(path):
	try:
		info = os.stat(path)
		return (info.st_mtime_ns, info.st_size)
	except OSError:
		return None


class Store:
	"""The appointments, kept in one file. The file can be put in a shared folder (Dropbox…):
	it is read again when another computer has changed it, and every change is merged into its latest version."""

	def __init__(self, path, backups=None):
		self.path = path
		self.backups = backups   # a folder of this computer for the daily copies, or None
		self.data = {'version': 2, 'appointments': [], 'deleted': {}}
		self.signature = None
		self.problem = None   # set when the file cannot be read
		self.load(first=True)
		self.backup()

	@property
	def appointments(self):
		return self.data['appointments']

	def load(self, first=False):
		"""Read the file. A file missing or half written (being synchronised) leaves the appointments as they are."""
		try:
			data = _read(self.path)
		except FileNotFoundError:
			self.problem = None
			return False
		except (ValueError, OSError) as error:
			if first and isinstance(error, ValueError):
				# A damaged file is put aside, never overwritten: the appointments may still be recovered from it.
				os.replace(self.path, self.path + '.damaged')
			self.problem = error
			return False
		self.problem = None
		self.data = merge({'appointments': [], 'deleted': {}}, data)
		self.signature = _signature(self.path)
		self.merge_conflicts()
		return True

	def changed_elsewhere(self):
		"""Read the file again if another computer changed it. True if the appointments changed."""
		signature = _signature(self.path)
		if signature is None or signature == self.signature:
			return False
		before = json.dumps(self.data, sort_keys=True)
		self.load()
		return json.dumps(self.data, sort_keys=True) != before

	def conflict_copies(self):
		"""The copies made by Dropbox and the like when two computers wrote at the same time."""
		stem, extension = os.path.splitext(self.path)
		found = []
		for path in glob.glob(glob.escape(stem) + '*' + extension):
			name = os.path.basename(path).lower()
			if os.path.normcase(path) != os.path.normcase(self.path) and ('conflict' in name or 'conflit' in name):
				found.append(path)
		return found

	def merge_conflicts(self):
		copies = self.conflict_copies()
		if not copies:
			return
		for path in copies:
			try:
				self.data = merge(self.data, _read(path))
			except (ValueError, OSError):
				continue
			self._put_aside(path)
		self.save(reload=False)

	def _put_aside(self, path):
		try:
			folder = self.backups or os.path.dirname(path)
			os.makedirs(folder, exist_ok=True)
			shutil.move(path, os.path.join(folder, 'merged ' + os.path.basename(path)))
		except OSError:
			pass

	def save(self, reload=True):
		"""Merged into the latest version of the file, then written to a new file put in place:
		a crash never leaves half a file."""
		if reload and _signature(self.path) not in (None, self.signature):
			try:
				self.data = merge(self.data, _read(self.path))
			except (ValueError, OSError):
				pass
		folder = os.path.dirname(self.path)
		if folder:
			os.makedirs(folder, exist_ok=True)
		temporary = self.path + '.tmp'
		with open(temporary, 'w', encoding='utf-8') as file:
			json.dump(self.data, file, ensure_ascii=False, indent=1)
		os.replace(temporary, self.path)
		self.signature = _signature(self.path)

	def backup(self):
		"""One copy a day in a folder of this computer, the last BACKUPS_KEPT kept."""
		if not self.backups or not os.path.isfile(self.path):
			return
		try:
			os.makedirs(self.backups, exist_ok=True)
			target = os.path.join(self.backups, 'agenda-%s.json' % datetime.date.today().isoformat())
			if not os.path.exists(target):
				shutil.copy2(self.path, target)
			copies = sorted(glob.glob(os.path.join(self.backups, 'agenda-*.json')))
			for old in copies[:-BACKUPS_KEPT]:
				os.remove(old)
		except OSError:
			pass

	# --- the changes
	def find(self, identifier):
		return next((a for a in self.appointments if a['id'] == identifier), None)

	def add(self, appointment):
		appointment['updated'] = stamp()
		self.data['appointments'].append(appointment)
		self.save()

	def replace(self, appointment):
		appointment['updated'] = stamp()
		self.data['appointments'] = [appointment if a['id'] == appointment['id'] else a for a in self.appointments]
		self.save()

	def remove(self, identifier):
		self.data['appointments'] = [a for a in self.appointments if a['id'] != identifier]
		self.data['deleted'][identifier] = stamp()
		self.save()

	def skip_day(self, identifier, day):
		"""Remove one day of a repeating appointment, keeping the others."""
		appointment = self.find(identifier)
		if appointment is not None:
			appointment.setdefault('skip', []).append(day.isoformat())
			appointment['updated'] = stamp()
			self.save()

	def clear_day(self, day):
		"""Every appointment of one day: the ones that repeat keep their other days."""
		for appointment in day_appointments(self.appointments, day):
			if repeats(appointment):
				appointment.setdefault('skip', []).append(day.isoformat())
				appointment['updated'] = stamp()
			else:
				self.data['appointments'].remove(appointment)
				self.data['deleted'][appointment['id']] = stamp()
		self.save()

	def import_file(self, path):
		"""Add the appointments of another agenda file. Returns how many were new."""
		other = _read(path)
		known = {a['id'] for a in self.appointments}
		new = sum(1 for a in other['appointments'] if a['id'] not in known)
		self.data = merge(self.data, other)
		self.save()
		return new

	def export_file(self, path):
		with open(path, 'w', encoding='utf-8') as file:
			json.dump(self.data, file, ensure_ascii=False, indent=1)

	def move_to(self, path):
		"""Use another file. If one is already there (another computer's), both agendas are merged into it."""
		if os.path.isfile(path):
			self.data = merge(self.data, _read(path))
		self.path = path
		self.signature = None
		self.save(reload=False)
