# AgendaAccess: the appointments, their repetitions and their reminders, and the file that keeps them.
# This module does not touch NVDA, so it can be tested alone.

import calendar
import datetime
import glob
import json
import os
import shutil
import uuid

from . import tz

# How an appointment repeats. 'every' is the interval: every 2 weeks, every 3 months…
#   none          once
#   days          every N days
#   weeks         every N weeks, on the chosen days of the week
#   months        every N months, on the same day of the month
#   monthWeekday  every N months, on the same day of the week, as "the 2nd Tuesday" or "the last Friday"
#   years         every N years, on the same date
#   yearWeekday   every N years, as "the 2nd Tuesday of November"
KINDS = ('none', 'days', 'weeks', 'months', 'monthWeekday', 'years', 'yearWeekday')
# The reminders offered in a list, in minutes before the appointment; any other delay can be chosen too.
REMINDERS = (0, 5, 15, 30, 60, 120, 1440, 2880, 4320, 10080)
DEFAULT_REMINDERS = [15]
# A whole-day appointment has no time: its reminders are at a time of day, by default on the day at 8:00.
DEFAULT_REMINDER_TIMES = [{'daysBefore': 0, 'time': '08:00'}]
# The format of the file (see Docs/agendaaccess-format.md): 3 since the end time, whole days and new reminders.
FORMAT = 3
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


def new_appointment(day, time, text, repeat=None, until=None, count=None, reminders=None, note='', category='', sound=None,
		location='', all_day=False, duration=None, days=1, reminder_times=None, details=None):
	"""sound: None for the usual sound of the reminders, '' for no sound, or the path of a sound file.
	duration: minutes from the start to the end (None: no end time); days: the length of a whole-day appointment."""
	repeat = dict(no_repeat(), **(repeat or {}))
	if repeat['kind'] == 'weeks' and not repeat['weekdays']:
		repeat['weekdays'] = [day.weekday()]
	if all_day:
		time = datetime.time(0, 0)
	if reminder_times is None:
		reminder_times = DEFAULT_REMINDER_TIMES if all_day else []
	appointment = {
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
		'location': location.strip(),
		'allDay': bool(all_day),
		'duration': None if all_day or not duration else int(duration),
		'days': max(1, int(days or 1)) if all_day else 1,
		'reminderTimes': [dict(r) for r in reminder_times],
		'updated': stamp(),
	}
	appointment.update({k: (list(v) if isinstance(v, list) else v) for k, v in DETAILS.items()})
	appointment.update({k: v for k, v in (details or {}).items() if k in DETAILS})
	return appointment


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


def all_day(appointment):
	return bool(appointment.get('allDay'))


def span(appointment):
	"""How many days one occurrence covers: 1, or more for a whole-day appointment of several days,
	or a timed one whose end is after midnight."""
	if all_day(appointment):
		return max(1, int(appointment.get('days') or 1))
	minutes = appointment.get('duration') or 0
	if minutes <= 0:
		return 1
	start = datetime.datetime.combine(datetime.date(2000, 1, 1), start_time(appointment))
	end = start + datetime.timedelta(minutes=minutes) - datetime.timedelta(microseconds=1)
	return (end.date() - start.date()).days + 1


def end_of(appointment, start):
	"""The end of the occurrence starting on that day: a datetime, or None without an end time."""
	if all_day(appointment):
		return None
	minutes = appointment.get('duration')
	return datetime.datetime.combine(start, start_time(appointment)) + datetime.timedelta(minutes=minutes) if minutes else None


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


# --- the occurrences. A series (a repeating appointment) gives one occurrence per day of its repetition; an
# occurrence changed alone is kept in the series' "overrides", by the day it had: {"2026-12-03": {"time": "15:00"}}.
# An occurrence is a copy of the series with its changes, its own date, and "_original": the day it had.
# Everything that lists appointments (a day, the reminders, what comes) works on occurrences.

# The fields an occurrence can change alone (see Docs/agendaaccess-format.md).
OCCURRENCE_FIELDS = ('date', 'time', 'text', 'note', 'location', 'category', 'sound', 'allDay', 'duration', 'days',
	'reminders', 'reminderTimes', 'categories', 'importance', 'status', 'availability', 'private', 'organizer', 'link')

# The other details of an appointment (lot 3); the first value of each is the usual one.
IMPORTANCES = ('normal', 'high', 'low')
STATUSES = ('confirmed', 'tentative', 'cancelled')
AVAILABILITIES = ('busy', 'free', 'tentative', 'away')
DETAILS = {'categories': [], 'importance': 'normal', 'status': 'confirmed', 'availability': 'busy', 'private': False,
	'organizer': '', 'link': '', 'timeZone': ''}


def detail(appointment, name):
	"""A detail of an appointment, or its usual value when the appointment has none (older files)."""
	value = appointment.get(name)
	return DETAILS[name] if value is None else value


def all_categories(appointment):
	"""The main category, then the other ones."""
	found = [appointment['category']] if appointment.get('category') else []
	return found + [c for c in detail(appointment, 'categories') if c and c not in found]


def cancelled(appointment):
	return detail(appointment, 'status') == 'cancelled'


def is_occurrence(appointment):
	return '_original' in appointment


def original_day(appointment):
	"""The day an occurrence had in its series (its date, for anything else)."""
	return datetime.date.fromisoformat(appointment.get('_original') or appointment['date'])


def _series_starts_on(series, day):
	"""Whether the repetition of the series gives an occurrence on this day (before any change made alone)."""
	if day.isoformat() in series.get('skip', ()):
		return False
	if not follows_pattern(series, day):
		return False
	last = last_day(series)
	return last is None or day <= last


def _occurrence(series, day, changes=None):
	occurrence = dict(series)
	occurrence.pop('overrides', None)
	occurrence['date'] = day.isoformat()
	if changes:
		occurrence.update({k: v for k, v in changes.items() if k in OCCURRENCE_FIELDS})
		occurrence['_changed'] = True
	occurrence['_original'] = day.isoformat()
	zone = occurrence.get('timeZone')
	if zone and not all_day(occurrence):
		# An appointment in another time zone (its date and time are there): shown at the time of this computer.
		there = datetime.datetime.combine(start_day(occurrence), start_time(occurrence))
		here = tz.to_local(there, zone)
		if here != there:
			occurrence['_zoneTime'] = occurrence['time']
			occurrence['_zoneDate'] = occurrence['date']
			occurrence['date'] = here.date().isoformat()
			occurrence['time'] = here.strftime('%H:%M')
	return occurrence


def occurrences_between(appointment, first, last):
	"""The occurrences of an appointment that start between two days (included)."""
	if appointment.get('timeZone') and not is_occurrence(appointment):
		# The time of another zone can move an occurrence to the day before or after here.
		day = datetime.timedelta(days=1)
		return [o for o in _occurrences_between(appointment, first - day, last + day) if first <= start_day(o) <= last]
	return _occurrences_between(appointment, first, last)


def _occurrences_between(appointment, first, last):
	if is_occurrence(appointment):
		return [appointment] if first <= start_day(appointment) <= last else []
	overrides = appointment.get('overrides') or {}
	found = []
	if repeats(appointment) or overrides:
		day = max(first, start_day(appointment))
		while day <= last:
			if day.isoformat() not in overrides and _series_starts_on(appointment, day):
				found.append(_occurrence(appointment, day))
			day += datetime.timedelta(days=1)
	elif first <= start_day(appointment) <= last and _series_starts_on(appointment, start_day(appointment)):
		found.append(_occurrence(appointment, start_day(appointment)))
	for original, changes in overrides.items():
		try:
			day = datetime.date.fromisoformat(original)
			if not isinstance(changes, dict) or not _series_starts_on(appointment, day):
				continue   # a change made alone to a day the series no longer has
			occurrence = _occurrence(appointment, day, changes)
			span(occurrence)   # a change that cannot be read (as a duration that is not a number) is left out here
			if first <= start_day(occurrence) <= last:
				found.append(occurrence)
		except (ValueError, TypeError, AttributeError, KeyError):
			continue   # a damaged change made alone is left out
	return found


def _lookback(appointment):
	"""How many days before a day an occurrence covering it can start."""
	longest = span(appointment)
	for changes in (appointment.get('overrides') or {}).values():
		try:
			longest = max(longest, span(dict(appointment, **{k: v for k, v in changes.items() if k in OCCURRENCE_FIELDS})))
		except (AttributeError, TypeError, ValueError):
			continue   # a damaged change made alone is left out
	return longest + 1   # +1: a change made alone can move an occurrence a day later


def starts_on(appointment, day):
	"""Whether an occurrence of the appointment starts on this day."""
	if is_occurrence(appointment):
		return day == start_day(appointment)
	return bool(occurrences_between(appointment, day, day))


occurs_on = starts_on   # the name used before the appointments of several days


def occurrence_start(appointment, day):
	"""The first day of the occurrence covering this day, or None."""
	for occurrence in _covering(appointment, day):
		return start_day(occurrence)
	return None


def _covering(appointment, day):
	"""The occurrences of an appointment that cover this day."""
	first = day - datetime.timedelta(days=_lookback(appointment))
	return [o for o in occurrences_between(appointment, first, day)
		if start_day(o) <= day < start_day(o) + datetime.timedelta(days=span(o))]


def covers(appointment, day):
	return bool(_covering(appointment, day))


def _day_order(appointment, day):
	"""Whole days and appointments begun earlier first, then by time."""
	ongoing = all_day(appointment) or start_day(appointment) != day
	return (not ongoing, appointment['time'] if not ongoing else '', appointment['text'].lower())


def day_appointments(appointments, day):
	"""The occurrences of one day: whole days and those begun on an earlier day first, then in order of time."""
	found = [o for a in appointments for o in _covering(a, day)]
	return sorted(found, key=lambda a: _day_order(a, day))


def day_starts(appointments, day):
	"""The occurrences starting on one day, in order of time: for the reminders and the lists of what comes."""
	found = [o for a in appointments for o in occurrences_between(a, day, day)]
	return sorted(found, key=lambda a: (not all_day(a), a['time'], a['text'].lower()))


def has_appointments(appointments, day):
	return any(covers(a, day) for a in appointments)


# The usual value of the fields a file of an older format may not have.
FIELD_DEFAULTS = {'location': '', 'note': '', 'category': '', 'allDay': False, 'days': 1, 'duration': None,
	'reminderTimes': [], 'reminders': []}


def has_earlier(series, original):
	"""Whether the series has a day before this one, counted by the days the series gives (before any move)."""
	day = start_day(series)
	while day < original:
		if _series_starts_on(series, day):
			return True
		day += datetime.timedelta(days=1)
	return False


def occurrence_changes(series, occurrence):
	"""Only what differs from the series: a later change to the series still reaches the other fields of the day."""
	changes = {}
	for name in OCCURRENCE_FIELDS:
		if name == 'date':
			if occurrence.get('date') != occurrence.get('_original', occurrence.get('date')):
				changes['date'] = occurrence['date']
			continue
		usual = DETAILS.get(name, FIELD_DEFAULTS.get(name))
		mine = occurrence.get(name)
		theirs = series.get(name)
		if name in DETAILS or name in FIELD_DEFAULTS:
			mine, theirs = (usual if mine is None else mine), (usual if theirs is None else theirs)
		if mine != theirs:
			changes[name] = mine
	return changes


def without_occurrence(series):
	"""The series itself, for a copy shown in the window of an appointment: no changes made alone, no hidden fields.
	An occurrence of another time zone gets back its date and time of that zone, which the window shows."""
	shown = {k: v for k, v in series.items() if not k.startswith('_')}
	if series.get('_zoneTime'):
		shown['time'], shown['date'] = series['_zoneTime'], series['_zoneDate']
	return shown


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
	"""The first day of the appointment from today on (here, for one of another time zone), or None if it is over."""
	if appointment.get('timeZone') and not is_occurrence(appointment):
		one = datetime.timedelta(days=1)
		day = today - one
		for _ in range(400):
			there = _next_there(appointment, day)
			if there is None:
				return None
			here = [start_day(o) for o in occurrences_between(appointment, there - one, there + one) if start_day(o) >= today]
			if here:
				return min(here)
			day = there + one
		return None
	return _next_there(appointment, today)


def _next_there(appointment, today):
	if is_occurrence(appointment):
		return start_day(appointment) if start_day(appointment) >= today else None
	found = None
	overrides = appointment.get('overrides') or {}
	for original, changes in overrides.items():
		try:
			day = datetime.date.fromisoformat(original)
			if _series_starts_on(appointment, day):
				moved = start_day(_occurrence(appointment, day, changes))
				if moved >= today and (found is None or moved < found):
					found = moved
		except (ValueError, TypeError, AttributeError):
			continue
	day = max(today, start_day(appointment))
	last = last_day(appointment)
	for _ in range(SEARCH_DAYS * 2):
		if (last is not None and day > last) or (found is not None and day >= found):
			break
		if day.isoformat() not in overrides and _series_starts_on(appointment, day):
			return day
		day += datetime.timedelta(days=1)
	return found


def upcoming(appointments, now, days=UPCOMING_DAYS):
	"""(when, appointment) from now on, for the next days, in order."""
	found = []
	for offset in range(days + 1):
		day = now.date() + datetime.timedelta(days=offset)
		for appointment in day_starts(appointments, day):
			when = datetime.datetime.combine(day, start_time(appointment))
			if when >= now.replace(second=0, microsecond=0) or (all_day(appointment) and day == now.date()):
				found.append((when, appointment))
	return found


def past(appointments, now, days=365):
	"""(when, appointment) of the last days, the most recent first: the journal."""
	found = []
	for offset in range(days + 1):
		day = now.date() - datetime.timedelta(days=offset)
		for appointment in reversed(day_starts(appointments, day)):
			when = datetime.datetime.combine(day, start_time(appointment))
			if when < now and not (all_day(appointment) and day == now.date()):
				found.append((when, appointment))
	return found


def rings(appointment, start):
	"""(when the reminder rings, minutes before the start) of each reminder of the occurrence starting that day.
	A whole-day appointment only has reminders at a time of day; a cancelled one has none."""
	when = datetime.datetime.combine(start, start_time(appointment))
	found = []
	if cancelled(appointment):
		return found
	if not all_day(appointment):
		for minutes in appointment.get('reminders', ()):
			found.append((when - datetime.timedelta(minutes=int(minutes)), int(minutes)))
	for item in appointment.get('reminderTimes', ()):
		try:
			hour, minute = str(item['time']).split(':')
			ring = datetime.datetime.combine(start - datetime.timedelta(days=int(item.get('daysBefore', 0))), datetime.time(int(hour), int(minute)))
		except (KeyError, ValueError, TypeError):
			continue
		minutes = int((when - ring).total_seconds() // 60)
		if minutes >= 0 or all_day(appointment):   # a time of day after the start of a timed appointment: no reminder
			found.append((ring, minutes))
	return found


def _longest(appointments):
	"""How far before an appointment its earliest reminder can ring."""
	longest = max(REMINDERS)
	for appointment in appointments:
		for minutes in appointment.get('reminders', ()):
			longest = max(longest, int(minutes))
		for item in appointment.get('reminderTimes', ()):
			try:
				longest = max(longest, (int(item.get('daysBefore') or 0) + 1) * 1440)
			except (ValueError, TypeError, AttributeError):
				continue
	return datetime.timedelta(minutes=longest)


def due_reminders(appointments, since, now):
	"""(when, minutes before, appointment) of the reminders falling after since and up to now.
	Reminders later than LATE_REMINDER (computer asleep, NVDA closed) are not given."""
	since = max(since, now - LATE_REMINDER)
	if since >= now:
		return []
	found = []
	day = since.date() - datetime.timedelta(days=1)
	while day <= (now + _longest(appointments)).date():
		for appointment in day_starts(appointments, day):
			when = datetime.datetime.combine(day, start_time(appointment))
			for ring, minutes in rings(appointment, day):
				if since < ring <= now:
					found.append((when, minutes, appointment))
		day += datetime.timedelta(days=1)
	return sorted(found, key=lambda item: (item[0], -item[1]))


def missed_reminders(appointments, since, until, now):
	"""The reminders that fell between since and until while NVDA was closed, for the appointments still to come:
	(when, minutes before, appointment), one per appointment, the latest of its reminders.
	A whole-day appointment is still to come for the whole of its day."""
	if since >= until:
		return []
	latest = {}
	day = since.date() - datetime.timedelta(days=1)
	while day <= (until + _longest(appointments)).date():
		for appointment in day_starts(appointments, day):
			when = datetime.datetime.combine(day, start_time(appointment))
			if (day < now.date()) if all_day(appointment) else (when <= now):
				continue
			for ring, minutes in rings(appointment, day):
				key = (appointment['id'], when)
				if since < ring <= until and (key not in latest or minutes < latest[key][1]):
					latest[key] = (when, minutes, appointment)
		day += datetime.timedelta(days=1)
	return sorted(latest.values(), key=lambda item: item[0])


def shift_month(day, months):
	"""The same day some months later or earlier; the last day of a shorter month."""
	index = day.year * 12 + day.month - 1 + months
	year, month = divmod(index, 12)
	month += 1
	return datetime.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def categories(appointments):
	return sorted({c for a in appointments for c in all_categories(a)}, key=str.lower)


def _interval(occurrence):
	start = datetime.datetime.combine(start_day(occurrence), start_time(occurrence))
	end = end_of(occurrence, start_day(occurrence)) or start
	return start, end


def overlaps(appointments, occurrence):
	"""The busy appointments of the same day whose time overlaps this one (it must be busy and have a time)."""
	if all_day(occurrence) or detail(occurrence, 'availability') != 'busy' or cancelled(occurrence):
		return []
	start, end = _interval(occurrence)
	found = []
	for other in day_appointments(appointments, start_day(occurrence)):
		if (other['id'], other.get('_original')) == (occurrence['id'], occurrence.get('_original')) or all_day(other) or cancelled(other) or detail(other, 'availability') != 'busy':
			continue
		other_start, other_end = _interval(other)
		if other_start == start or other_start <= start < other_end or start <= other_start < end:
			found.append(other)
	return found


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
	version = max(FORMAT, int(mine.get('version') or 0), int(theirs.get('version') or 0))
	return {'version': version, 'appointments': kept, 'deleted': {i: w for i, w in deleted.items() if w >= limit}}


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

	def __init__(self, path, backups=None, read_only=False, backup_label=''):
		"""read_only: someone else's agenda shared with you: never written, never moved, never put aside."""
		self.path = path
		self.read_only = read_only
		self.backup_label = backup_label   # to tell apart the copies of your other agendas (Work…)
		self.on_saved = []   # called after each save (the public copy)
		self.backups = backups   # a folder of this computer for the daily copies, or None
		self.data = {'version': FORMAT, 'appointments': [], 'deleted': {}}
		self.signature = None
		self.problem = None   # set when the file cannot be read
		self.newer = False
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
			if first and isinstance(error, ValueError) and not self.read_only:
				# A damaged file is put aside, never overwritten: the appointments may still be recovered from it.
				os.replace(self.path, self.path + '.damaged')
			self.problem = error
			return False
		self.problem = None
		self.data = merge({'appointments': [], 'deleted': {}} if first else self.data, data)
		# Written by a newer AgendaAccess: this computer should be updated (see Docs/agendaaccess-format.md).
		self.newer = int(data.get('version') or 0) > FORMAT
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
		copies = [] if self.read_only else self.conflict_copies()
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
		if self.read_only:
			raise PermissionError('read-only agenda')
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
			self.data['version'] = max(FORMAT, int(self.data.get('version') or 0))
			json.dump(self.data, file, ensure_ascii=False, indent=1)
		os.replace(temporary, self.path)
		self.signature = _signature(self.path)
		for done in self.on_saved:
			try:
				done(self)
			except Exception:
				pass

	def backup(self):
		"""One copy a day in a folder of this computer, the last BACKUPS_KEPT kept."""
		if not self.backups or not os.path.isfile(self.path):
			return
		try:
			os.makedirs(self.backups, exist_ok=True)
			# Your agenda: agenda-<date>.json; your other agendas: other-<name>-<date>.json, never mixed with it.
			prefix = 'other-%s-' % self.backup_label if self.backup_label else 'agenda-'
			target = os.path.join(self.backups, prefix + datetime.date.today().isoformat() + '.json')
			if not os.path.exists(target):
				shutil.copy2(self.path, target)
			copies = sorted(glob.glob(os.path.join(self.backups, glob.escape(prefix) + '[0-9]*.json')))
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
		"""Remove one day of a repeating appointment, keeping the others. day: the day the occurrence had."""
		appointment = self.find(identifier)
		if appointment is not None:
			appointment.setdefault('skip', []).append(day.isoformat())
			(appointment.get('overrides') or {}).pop(day.isoformat(), None)
			appointment['updated'] = stamp()
			self.save()

	def change_occurrence(self, identifier, original, changes):
		"""Change one occurrence of a series alone. original: the day it has in the series."""
		series = self.find(identifier)
		if series is None:
			return
		kept = {k: v for k, v in changes.items() if k in OCCURRENCE_FIELDS}
		series.setdefault('overrides', {})[original.isoformat()] = kept
		series['updated'] = stamp()
		self.save()

	def split(self, identifier, original, following):
		"""Cut a series in two: it ends the day before original, and following (a new series, with its own id)
		goes on from there. The changes made alone and the days removed from original on go with it."""
		series = self.find(identifier)
		if series is None:
			return
		before = original - datetime.timedelta(days=1)
		end = until_day(series)
		series['until'] = min(end, before).isoformat() if end else before.isoformat()
		moved = {k: v for k, v in (series.get('overrides') or {}).items() if k >= original.isoformat()}
		series['overrides'] = {k: v for k, v in (series.get('overrides') or {}).items() if k < original.isoformat()}
		following['skip'] = sorted(d for d in series.get('skip', ()) if d >= original.isoformat())
		series['skip'] = [d for d in series.get('skip', ()) if d < original.isoformat()]
		if following.get('count') and series.get('count'):
			# A number of times counts from the first day: what is left goes to the new series.
			done = sum(1 for d in range((original - start_day(series)).days) if follows_pattern(series, start_day(series) + datetime.timedelta(days=d)))
			following['count'] = max(1, following['count'] - done) if following['count'] == series['count'] else following['count']
		following['overrides'] = moved
		series['updated'] = following['updated'] = stamp()
		self.data['appointments'].append(following)
		if next_occurrence(series, start_day(series) - datetime.timedelta(days=_lookback(series))) is None:
			self.data['appointments'].remove(series)   # nothing left before the cut
			self.data['deleted'][series['id']] = stamp()
		self.save()

	def clear_day(self, day):
		"""Every appointment of one day: the ones that repeat keep their other days."""
		for occurrence in day_appointments(self.appointments, day):
			series = self.find(occurrence['id'])
			if series is None:
				continue
			if repeats(series):
				original = original_day(occurrence).isoformat()
				series.setdefault('skip', []).append(original)
				(series.get('overrides') or {}).pop(original, None)
				series['updated'] = stamp()
			else:
				self.data['appointments'] = [a for a in self.appointments if a['id'] != series['id']]
				self.data['deleted'][series['id']] = stamp()
		self.save()

	def import_file(self, path):
		"""Add the appointments of another agenda file. Returns how many were new."""
		other = _read(path)
		known = {a['id'] for a in self.appointments}
		new = sum(1 for a in other['appointments'] if a['id'] not in known)
		self.data = merge(self.data, other)
		self.save()
		return new

	def import_appointments(self, appointments):
		"""Add appointments (read from an .ics file). The same event imported again changes it instead of
		adding a copy. Returns how many were new."""
		mine = {a['id']: a for a in self.appointments}
		taken = []
		for appointment in appointments:
			existing = mine.get(appointment['id'])
			if existing is not None:
				if not appointment['id'].startswith('ics-'):
					continue   # one of yours, exported then imported again: yours is kept, it has more
				appointment['sound'] = existing.get('sound')   # what .ics files do not carry
			appointment['updated'] = stamp()
			taken.append(appointment)
		new = sum(1 for a in taken if a['id'] not in mine)
		if taken:
			self.data = merge(self.data, {'appointments': taken, 'deleted': {}})
			self.save()
		return new, len(taken) - new

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
