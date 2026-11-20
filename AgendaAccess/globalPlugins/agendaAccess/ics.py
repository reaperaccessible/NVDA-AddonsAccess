# AgendaAccess: the ".ics" format (iCalendar, RFC 5545), understood by Outlook, Thunderbird, Google and the iPhone.
# Writing covers everything AgendaAccess has; reading takes the common cases, and keeps a simpler form of the rest
# (a repetition it does not know becomes a single appointment, said so in its note).
# This module does not touch NVDA, so it can be tested alone.

import datetime
import hashlib
import re

import addonHandler

from . import agenda, tz

addonHandler.initTranslation()

PRODUCT = '-//ReaperAccessible//AgendaAccess//EN'
WEEKDAYS = ('MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU')


# --- writing

def _escape(text):
	return str(text).replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\r\n', '\\n').replace('\n', '\\n')


def _fold(line):
	"""Lines of at most 75 bytes, the rest on lines starting with a space."""
	data = line.encode('utf-8')
	if len(data) <= 75:
		return line
	parts, current = [], b''
	for char in line:
		piece = char.encode('utf-8')
		if len(current) + len(piece) > (75 if not parts else 74):
			parts.append(current.decode('utf-8'))
			current = b''
		current += piece
	parts.append(current.decode('utf-8'))
	return '\r\n '.join(parts)


def _date(day):
	return day.strftime('%Y%m%d')


def _datetime(moment):
	return moment.strftime('%Y%m%dT%H%M%S')


def _start_lines(appointment, day):
	"""DTSTART and DTEND (or DURATION) of an occurrence starting that day, in its own zone if it has one."""
	if agenda.all_day(appointment):
		last = day + datetime.timedelta(days=agenda.span(appointment))
		return ['DTSTART;VALUE=DATE:' + _date(day), 'DTEND;VALUE=DATE:' + _date(last)]
	zone = appointment.get('timeZone') if tz.known(appointment.get('timeZone')) else ''
	where = ';TZID=' + zone if zone else ''
	start = datetime.datetime.combine(day, agenda.start_time(appointment))
	lines = ['DTSTART%s:%s' % (where, _datetime(start))]
	if appointment.get('duration'):
		lines.append('DTEND%s:%s' % (where, _datetime(start + datetime.timedelta(minutes=appointment['duration']))))
	return lines


def _rrule(appointment):
	rule = agenda.repeat_of(appointment)
	kind, every = rule['kind'], max(1, int(rule.get('every') or 1))
	start = agenda.start_day(appointment)
	if kind == 'none':
		return None
	parts = []
	if kind == 'days':
		parts.append('FREQ=DAILY')
	elif kind == 'weeks':
		parts += ['FREQ=WEEKLY', 'BYDAY=' + ','.join(WEEKDAYS[d] for d in sorted(rule['weekdays'] or [start.weekday()]))]
	elif kind == 'months':
		parts += ['FREQ=MONTHLY', 'BYMONTHDAY=%d' % start.day]
	elif kind == 'monthWeekday':
		parts += ['FREQ=MONTHLY', 'BYDAY=%d%s' % (agenda.week_of_month(start, bool(rule.get('fromEnd'))), WEEKDAYS[start.weekday()])]
	elif kind == 'years':
		parts += ['FREQ=YEARLY', 'BYMONTH=%d' % start.month, 'BYMONTHDAY=%d' % start.day]
	elif kind == 'yearWeekday':
		parts += ['FREQ=YEARLY', 'BYMONTH=%d' % start.month, 'BYDAY=%d%s' % (agenda.week_of_month(start, bool(rule.get('fromEnd'))), WEEKDAYS[start.weekday()])]
	if every > 1:
		parts.append('INTERVAL=%d' % every)
	if appointment.get('count'):
		parts.append('COUNT=%d' % appointment['count'])
	elif appointment.get('until'):
		last = agenda.until_day(appointment)
		if agenda.all_day(appointment):
			parts.append('UNTIL=' + _date(last))
		else:
			end = datetime.datetime.combine(last, datetime.time(23, 59, 59))
			zone = appointment.get('timeZone') if tz.known(appointment.get('timeZone')) else ''
			parts.append('UNTIL=' + (_datetime(tz.to_utc(end, zone)) + 'Z' if zone else _datetime(end)))
	return 'RRULE:' + ';'.join(parts)


def _event(appointment, day, recurrence_id=None):
	uid = appointment.get('uid') or appointment['id'] + '@agendaaccess'
	lines = ['BEGIN:VEVENT', 'UID:' + uid, 'DTSTAMP:' + datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')]
	if recurrence_id:
		lines.append(recurrence_id)
	lines += _start_lines(appointment, day)
	lines.append('SUMMARY:' + _escape(appointment['text']))
	if appointment.get('location'):
		lines.append('LOCATION:' + _escape(appointment['location']))
	if appointment.get('note'):
		lines.append('DESCRIPTION:' + _escape(appointment['note']))
	names = agenda.all_categories(appointment)
	if names:
		lines.append('CATEGORIES:' + ','.join(_escape(n) for n in names))
	lines.append('STATUS:' + {'confirmed': 'CONFIRMED', 'tentative': 'TENTATIVE', 'cancelled': 'CANCELLED'}[agenda.detail(appointment, 'status')])
	lines.append('PRIORITY:%d' % {'high': 1, 'normal': 5, 'low': 9}[agenda.detail(appointment, 'importance')])
	lines.append('CLASS:' + ('PRIVATE' if agenda.detail(appointment, 'private') else 'PUBLIC'))
	lines.append('TRANSP:' + ('TRANSPARENT' if agenda.detail(appointment, 'availability') == 'free' else 'OPAQUE'))
	if agenda.detail(appointment, 'organizer'):
		lines.append('ORGANIZER;CN=%s:mailto:%s' % (_escape(agenda.detail(appointment, 'organizer')).replace(':', ''),
			agenda.detail(appointment, 'organizer') if '@' in agenda.detail(appointment, 'organizer') else 'unknown@example.invalid'))
	if agenda.detail(appointment, 'link'):
		lines.append('URL:' + agenda.detail(appointment, 'link'))
	when = datetime.datetime.combine(day, agenda.start_time(appointment))
	for ring, minutes in agenda.rings(dict(appointment, status='confirmed'), day):
		lines += ['BEGIN:VALARM', 'ACTION:DISPLAY', 'DESCRIPTION:' + _escape(appointment['text']),
			'TRIGGER:%sPT%dM' % ('-' if minutes >= 0 else '', abs(minutes)), 'END:VALARM']
	lines.append('END:VEVENT')
	return lines


def export(appointments):
	"""The text of an .ics file with every appointment: series with their repetition, days removed, days changed alone."""
	lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:' + PRODUCT, 'CALSCALE:GREGORIAN']
	for appointment in appointments:
		start = agenda.start_day(appointment)
		event = _event(appointment, start)
		rule = _rrule(appointment)
		if rule:
			event.insert(-1, rule)
			skipped = sorted(set(appointment.get('skip', ())) | set((appointment.get('overrides') or {}).keys()))
			for day in skipped:
				if day in (appointment.get('overrides') or {}):
					continue
				event.insert(-1, _exdate(appointment, datetime.date.fromisoformat(day)))
		lines += event
		for original, changes in (appointment.get('overrides') or {}).items():
			try:
				day = datetime.date.fromisoformat(original)
			except ValueError:
				continue
			occurrence = agenda._occurrence(appointment, day, changes)
			occurrence.pop('timeZone', None) if occurrence.get('_zoneTime') else None
			lines += _event(dict(appointment, **{k: v for k, v in changes.items() if k in agenda.OCCURRENCE_FIELDS}),
				datetime.date.fromisoformat(changes.get('date') or original), recurrence_id=_recurrence(appointment, day))
	lines.append('END:VCALENDAR')
	return '\r\n'.join(_fold(line) for line in lines) + '\r\n'


def _exdate(appointment, day):
	if agenda.all_day(appointment):
		return 'EXDATE;VALUE=DATE:' + _date(day)
	zone = appointment.get('timeZone') if tz.known(appointment.get('timeZone')) else ''
	return 'EXDATE%s:%s' % (';TZID=' + zone if zone else '', _datetime(datetime.datetime.combine(day, agenda.start_time(appointment))))


def _recurrence(appointment, day):
	return _exdate(appointment, day).replace('EXDATE', 'RECURRENCE-ID', 1)


# --- reading

def _unfold(text):
	return re.sub(r'\r?\n[ \t]', '', text.replace('\r\n', '\n')).split('\n')


def _unescape(text):
	return re.sub(r'\\([\\;,nN])', lambda m: '\n' if m.group(1) in 'nN' else m.group(1), text)


def _split(line):
	"""(NAME, {PARAM: value}, value) of a line such as 'DTSTART;TZID=Europe/Paris:20261120T160000'."""
	head, sep, value = line.partition(':')
	while head.count('"') % 2 and ':' in value:   # a ":" inside a quoted parameter (an unclosed quote stops here)
		more, sep, value = value.partition(':')
		head += ':' + more
	pieces = head.split(';')
	params = {}
	for piece in pieces[1:]:
		key, sep, val = piece.partition('=')
		params[key.upper()] = val.strip('"')
	return pieces[0].upper(), params, value


def _moment(value, params):
	"""(date, time or None for a whole day, zone key or ''), the time given in the zone of the value."""
	value = value.strip()
	if params.get('VALUE') == 'DATE' or len(value) == 8:
		return datetime.datetime.strptime(value[:8], '%Y%m%d').date(), None, ''
	moment = datetime.datetime.strptime(value[:15], '%Y%m%dT%H%M%S')
	if value.endswith('Z'):
		return moment.date(), moment.time(), 'UTC'
	return moment.date(), moment.time(), tz.windows_key(params.get('TZID', ''))


def _in_zone(value, params, zone):
	"""(date, time or None) of a date-time value, in the zone of the series (zone: a key, or '' for this computer).
	A value without a zone of its own is taken in the zone of the series."""
	day, time, own = _moment(value, params)
	if time is None:
		return day, None
	moment = datetime.datetime.combine(day, time)
	if own and own != zone:
		moment = tz.convert(moment, own, zone)
	return moment.date(), moment.time()


def _duration(value):
	"""Minutes of an iCalendar duration, as 'PT1H30M', '-P1D', 'P2W'."""
	match = re.match(r'([+-]?)P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$', value.strip())
	if not match:
		return None
	sign, weeks, days, hours, minutes, seconds = match.groups()
	total = int(weeks or 0) * 10080 + int(days or 0) * 1440 + int(hours or 0) * 60 + int(minutes or 0) + int(seconds or 0) // 60
	return -total if sign == '-' else total


def _repeat(rule, start, zone=''):
	"""(repeat, until, count, understood) from an RRULE value. Anything odd: not understood."""
	try:
		return _read_repeat(rule, start, zone)
	except (ValueError, TypeError, KeyError, IndexError):
		return None, None, None, False


def _read_repeat(rule, start, zone):
	parts = dict(p.split('=', 1) for p in rule.upper().split(';') if '=' in p)
	frequency = parts.get('FREQ', '')
	every = int(parts.get('INTERVAL', '1') or 1)
	until = count = None
	if 'UNTIL' in parts:
		until = _in_zone(parts['UNTIL'], {}, zone)[0]
	if 'COUNT' in parts:
		count = int(parts['COUNT'])
	byday = [d for d in parts.get('BYDAY', '').split(',') if d]
	known_keys = {'FREQ', 'INTERVAL', 'UNTIL', 'COUNT', 'BYDAY', 'BYMONTHDAY', 'BYMONTH', 'BYSETPOS', 'WKST'}
	understood = set(parts) <= known_keys and every >= 1
	# What AgendaAccess repeats only on the day of the start: several days of the month or months, another day.
	if 'BYMONTHDAY' in parts and parts['BYMONTHDAY'] != str(start.day):
		understood = False
	if 'BYMONTH' in parts and parts['BYMONTH'] != str(start.month):
		understood = False
	if parts.get('WKST', 'MO') != 'MO' and frequency == 'WEEKLY' and every > 1:
		understood = False   # weeks counted from Sunday: not the same days every other week
	repeat = None
	if frequency == 'DAILY' and not byday:
		repeat = {'kind': 'days', 'every': every}
	elif frequency == 'WEEKLY':
		days = [WEEKDAYS.index(d[-2:]) for d in byday if d[-2:] in WEEKDAYS] or [start.weekday()]
		repeat = {'kind': 'weeks', 'every': every, 'weekdays': sorted(set(days))}
	elif frequency in ('MONTHLY', 'YEARLY'):
		which = None
		if len(byday) == 1:
			match = re.match(r'([+-]?\d+)?([A-Z]{2})$', byday[0])
			if match:
				which = int(match.group(1) or parts.get('BYSETPOS', '0') or 0)
		if byday and which:
			repeat = {'kind': 'monthWeekday' if frequency == 'MONTHLY' else 'yearWeekday', 'every': every, 'fromEnd': which < 0}
		elif not byday:
			repeat = {'kind': 'months' if frequency == 'MONTHLY' else 'years', 'every': every}
	if repeat is None:
		return None, None, None, False
	return repeat, until, count, understood


def _stable_id(uid):
	"""The same .ics event always gets the same id: importing it again, or reading a subscription again,
	changes the appointment instead of adding a copy."""
	return 'ics-' + hashlib.sha1(uid.encode('utf-8')).hexdigest()[:24]


def parse(text):
	"""The appointments of an .ics file (format 3 of AgendaAccess)."""
	events, current, alarm = [], None, None
	for line in _unfold(text):
		if not line.strip():
			continue
		name, params, value = _split(line)
		if name == 'BEGIN' and value.upper() == 'VEVENT':
			current = {'alarms': []}
		elif name == 'BEGIN' and value.upper() == 'VALARM' and current is not None:
			alarm = {}
		elif name == 'END' and value.upper() == 'VALARM':
			if current is not None and alarm is not None:
				current['alarms'].append(alarm)
			alarm = None
		elif name == 'END' and value.upper() == 'VEVENT':
			if current is not None:
				events.append(current)
			current = None
		elif alarm is not None:
			alarm[name] = (params, value)
		elif current is not None:
			if name in ('EXDATE', 'CATEGORIES'):
				current.setdefault(name, []).append((params, value))
			else:
				current[name] = (params, value)
	masters, changes = {}, []
	for event in events:
		if 'DTSTART' not in event:
			continue
		if 'RECURRENCE-ID' in event:
			changes.append(event)
			continue
		try:
			appointment = _appointment(event)
		except Exception:
			appointment = None   # an event that cannot be read is left out, never the whole file
		if appointment:
			masters[event.get('UID', ({}, ''))[1] or appointment['id']] = appointment
	for event in changes:
		try:
			uid = event.get('UID', ({}, ''))[1]
			series = masters.get(uid)
			changed = _appointment(event)
			if not changed:
				continue
			if series is None:
				changed['id'] = _stable_id(uid + event['RECURRENCE-ID'][1])
				masters[changed['id']] = changed
				continue
			zone = series.get('timeZone', '')
			original = _in_zone(event['RECURRENCE-ID'][1], event['RECURRENCE-ID'][0], zone)[0]
			if not changed.get('allDay'):
				# The new date and time of the day, in the zone of the series.
				moment = tz.convert(datetime.datetime.combine(agenda.start_day(changed), agenda.start_time(changed)), changed.get('timeZone', ''), zone)
				changed['date'], changed['time'] = moment.date().isoformat(), moment.strftime('%H:%M')
			series.setdefault('overrides', {})[original.isoformat()] = {k: changed[k] for k in agenda.OCCURRENCE_FIELDS if k in changed}
		except Exception:
			continue   # a change that cannot be read is left out
	return list(masters.values())


def _appointment(event):
	try:
		day, time, zone = _moment(event['DTSTART'][1], event['DTSTART'][0])
	except ValueError:
		return None
	if zone == 'UTC':   # a time in UTC: at the time of this computer
		moment = tz.from_utc(datetime.datetime.combine(day, time))
		day, time, zone = moment.date(), moment.time(), ''
	uid = event.get('UID', ({}, ''))[1] or event['DTSTART'][1] + event.get('SUMMARY', ({}, ''))[1]
	whole = time is None
	duration, days = None, 1
	if 'DTEND' in event:
		try:
			end_day, end_time, end_zone = _moment(event['DTEND'][1], event['DTEND'][0])
			if whole:
				days = max(1, (end_day - day).days)
			elif end_time is not None:
				start_utc = tz.convert(datetime.datetime.combine(day, time), zone, 'UTC')
				end_utc = tz.convert(datetime.datetime.combine(end_day, end_time), end_zone or zone, 'UTC')
				duration = int((end_utc - start_utc).total_seconds() // 60)
		except ValueError:
			pass
	elif 'DURATION' in event:
		minutes = _duration(event['DURATION'][1])
		if minutes:
			if whole:
				days = max(1, minutes // 1440)
			else:
				duration = minutes
	note = _unescape(event.get('DESCRIPTION', ({}, ''))[1])
	repeat, until, count, understood = (None, None, None, True)
	if 'RRULE' in event:
		repeat, until, count, understood = _repeat(event['RRULE'][1], day, zone)
		if not understood or repeat is None:
			# Translators: Added to the note of an appointment whose repetition could not be read from an .ics file.
			note = (note + '\n' if note else '') + _('This appointment repeated in a way AgendaAccess cannot read: only its first day is here.')
			repeat, until, count = None, None, None
	reminders, times = [], []
	for alarm in event['alarms']:
		params, value = alarm.get('TRIGGER', ({}, ''))
		if params.get('VALUE') == 'DATE-TIME' or params.get('RELATED') == 'END':
			continue
		minutes = _duration(value)
		if minutes is not None:
			if whole:
				ring = datetime.datetime.combine(day, datetime.time(0, 0)) + datetime.timedelta(minutes=minutes)
				times.append({'daysBefore': max(0, (day - ring.date()).days), 'time': ring.strftime('%H:%M')})
			else:
				reminders.append(max(0, -minutes))
	names = []
	for params, value in event.get('CATEGORIES', []):
		names += [_unescape(n).strip() for n in re.split(r'(?<!\\),', value) if n.strip()]
	status = {'TENTATIVE': 'tentative', 'CANCELLED': 'cancelled'}.get(event.get('STATUS', ({}, ''))[1].upper(), 'confirmed')
	priority = event.get('PRIORITY', ({}, '0'))[1]
	importance = 'high' if priority in ('1', '2', '3', '4') else 'low' if priority in ('6', '7', '8', '9') else 'normal'
	organizer = ''
	if 'ORGANIZER' in event:
		params, value = event['ORGANIZER']
		organizer = params.get('CN') or value.replace('mailto:', '')
	details = {
		'categories': names[1:], 'importance': importance, 'status': status,
		'availability': 'free' if event.get('TRANSP', ({}, ''))[1].upper() == 'TRANSPARENT' else 'busy',
		'private': event.get('CLASS', ({}, ''))[1].upper() in ('PRIVATE', 'CONFIDENTIAL'),
		'organizer': organizer, 'link': event.get('URL', ({}, ''))[1],
	}
	appointment = agenda.new_appointment(day, time or datetime.time(0, 0), _unescape(event.get('SUMMARY', ({}, ''))[1]) or '?',
		repeat, until, count, sorted(set(reminders)) if not whole else [], note, names[0] if names else '', None,
		location=_unescape(event.get('LOCATION', ({}, ''))[1]), all_day=whole, duration=duration if duration and duration > 0 else None,
		days=days, reminder_times=times, details=details)
	if uid.endswith('@agendaaccess') and len(uid) > len('@agendaaccess'):
		appointment['id'] = uid[:-len('@agendaaccess')]   # exported by AgendaAccess: the same appointment again
	else:
		appointment['id'] = _stable_id(uid)
		appointment['uid'] = uid
	if zone and zone != tz.local_key():
		appointment['timeZone'] = zone
	for params, value in event.get('EXDATE', []):
		for piece in value.split(','):
			try:
				appointment.setdefault('skip', []).append(_in_zone(piece, params, zone if zone != tz.local_key() else '')[0].isoformat())
			except ValueError:
				pass
	return appointment
