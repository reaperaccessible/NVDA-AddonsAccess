# AgendaAccess: the appointments written as sentences, in French or in English.

import datetime

import addonHandler

from . import agenda, tz

addonHandler.initTranslation()


def weekday_names():
	# Translators: The days of the week, from Monday.
	return [_('Monday'), _('Tuesday'), _('Wednesday'), _('Thursday'), _('Friday'), _('Saturday'), _('Sunday')]


def month_names():
	# Translators: The months of the year.
	return [_('January'), _('February'), _('March'), _('April'), _('May'), _('June'), _('July'),
		_('August'), _('September'), _('October'), _('November'), _('December')]


def date_text(day):
	"""'Monday, November 19, 2026' in English, 'lundi 19 novembre 2026' in French."""
	# Translators: A full date. {weekday} and {month} are names, {day} and {year} numbers.
	return _('{weekday}, {month} {day}, {year}').format(
		weekday=weekday_names()[day.weekday()], month=month_names()[day.month - 1], day=day.day, year=day.year)


def relative_date_text(day, today):
	"""Today, tomorrow and yesterday by their name, before the date."""
	gap = (day - today).days
	# Translators: Before a date, as in "Today, Monday, November 19, 2026".
	names = {0: _('Today'), 1: _('Tomorrow'), -1: _('Yesterday')}
	if gap in names:
		# Translators: A date with its name, as in "Today, Monday, November 19, 2026".
		return _('{name}, {date}').format(name=names[gap], date=date_text(day))
	return date_text(day)


def time_text(time):
	"""'10:30' in English, '10 h 30' in French; '10 h' on the hour."""
	if time.minute == 0:
		# Translators: A time on the hour, as in 10:00. French: "{hour} h".
		return _('{hour}:00').format(hour=time.hour)
	# Translators: A time, as in 10:30. French: "{hour} h {minute}".
	return _('{hour}:{minute}').format(hour=time.hour, minute='%02d' % time.minute)


def ordinal(number):
	"""1st, 2nd… for the weeks of a month; the last ones counted from the end."""
	return {
		# Translators: Which day of the week of a month, as in "the 2nd Tuesday".
		1: _('1st'), 2: _('2nd'), 3: _('3rd'), 4: _('4th'), 5: _('5th'),
		# Translators: Which day of the week of a month, counted from its end, as in "the last Friday".
		-1: _('last'), -2: _('second to last'), -3: _('third to last'), -4: _('fourth to last'), -5: _('fifth to last'),
	}[number]


def repeat_choices():
	"""The choices of the Repeat list of an appointment: (label, kind, preset)."""
	return [
		# Translators: A choice of how an appointment repeats.
		(_('Does not repeat'), 'none', {}),
		(_('Every day, or every few days'), 'days', {}),
		(_('Monday to Friday'), 'weeks', {'weekdays': [0, 1, 2, 3, 4], 'every': 1}),
		(_('Every week, or every few weeks, on chosen days'), 'weeks', {}),
		(_('Every month, on the same date'), 'months', {}),
		(_('Every month, on the same day of the week (as the 2nd Tuesday)'), 'monthWeekday', {}),
		(_('Every year, on the same date'), 'years', {}),
		(_('Every year, on the same day of the week (as the 2nd Tuesday of November)'), 'yearWeekday', {}),
	]


def repeat_text(appointment):
	"""'every week on Tuesday and Thursday', 'every 2 months on the last Friday'…; '' when it does not repeat."""
	rule = agenda.repeat_of(appointment)
	kind, every = rule['kind'], max(1, int(rule.get('every') or 1))
	start = agenda.start_day(appointment)
	if kind == 'none':
		return ''
	if kind == 'days':
		# Translators: How an appointment repeats.
		return _('every day') if every == 1 else _('every {count} days').format(count=every)
	if kind == 'weeks':
		days = sorted(rule['weekdays'] or [start.weekday()])
		if days == [0, 1, 2, 3, 4] and every == 1:
			return _('Monday to Friday')
		names = _(' and ').join(weekday_names()[d] for d in days)
		# Translators: How an appointment repeats, as in "every week on Tuesday and Thursday".
		return (_('every week on {days}') if every == 1 else _('every {count} weeks on {days}')).format(count=every, days=names)
	if kind == 'months':
		# Translators: How an appointment repeats, as in "every month on the 15th".
		return (_('every month on day {day}') if every == 1 else _('every {count} months on day {day}')).format(count=every, day=start.day)
	weekday = weekday_names()[start.weekday()]
	which = ordinal(agenda.week_of_month(start, bool(rule.get('fromEnd'))))
	if kind == 'monthWeekday':
		# Translators: How an appointment repeats, as in "every month on the 2nd Tuesday".
		return (_('every month on the {which} {weekday}') if every == 1 else _('every {count} months on the {which} {weekday}')).format(
			count=every, which=which, weekday=weekday)
	month = month_names()[start.month - 1]
	if kind == 'years':
		# Translators: How an appointment repeats, as in "every year on November 19".
		return (_('every year on {month} {day}') if every == 1 else _('every {count} years on {month} {day}')).format(
			count=every, month=month, day=start.day)
	# Translators: How an appointment repeats, as in "every year on the 2nd Tuesday of November".
	return (_('every year on the {which} {weekday} of {month}') if every == 1 else _('every {count} years on the {which} {weekday} of {month}')).format(
		count=every, which=which, weekday=weekday, month=month)


def end_text(appointment):
	if appointment.get('count'):
		# Translators: The end of a repetition, as in "12 times".
		return _('{count} times').format(count=appointment['count'])
	end = agenda.until_day(appointment)
	if end:
		# Translators: The end of a repetition, as in "until Thursday, December 10, 2026".
		return _('until {date}').format(date=date_text(end))
	return ''


def default_categories():
	"""The categories offered from the start; any other can be typed."""
	return [
		# Translators: A category offered for the appointments.
		_('Personal'), _('Work'), _('Family'), _('Friends'), _('Medical'), _('Appointment'), _('Meeting'), _('Birthday'),
		_('Party'), _('Holidays'), _('Travel'), _('Day off'), _('Sport'), _('Leisure'), _('School'), _('Training'),
		_('Bank and money'), _('Home'), _('Car'), _('Shopping'), _('Volunteering'), _('Show'), _('Important')]


def importance_names():
	"""In the order of agenda.IMPORTANCES."""
	# Translators: The importance of an appointment.
	return [_('Normal'), _('High'), _('Low')]


def status_names():
	"""In the order of agenda.STATUSES."""
	# Translators: The status of an appointment.
	return [_('Confirmed'), _('Tentative'), _('Cancelled')]


def availability_names():
	"""In the order of agenda.AVAILABILITIES."""
	# Translators: How available one is during an appointment.
	return [_('Busy'), _('Free'), _('Tentative'), _('Away')]


def details_text(appointment):
	"""The details said after an appointment: cancelled, important, free, private, organizer, link."""
	parts = []
	status = agenda.detail(appointment, 'status')
	if status == 'cancelled':
		# Translators: Said after a cancelled appointment.
		parts.append(_('cancelled'))
	elif status == 'tentative':
		# Translators: Said after an appointment not confirmed yet.
		parts.append(_('to be confirmed'))
	importance = agenda.detail(appointment, 'importance')
	if importance == 'high':
		# Translators: Said after an important appointment.
		parts.append(_('important'))
	elif importance == 'low':
		# Translators: Said after an appointment of low importance.
		parts.append(_('of low importance'))
	availability = agenda.detail(appointment, 'availability')
	if availability != 'busy':
		# Translators: Said after an appointment during which one is not busy, as "available: free".
		parts.append(_('available: {state}').format(state=availability_names()[agenda.AVAILABILITIES.index(availability)].lower()))
	if agenda.detail(appointment, 'private'):
		# Translators: Said after a private appointment.
		parts.append(_('private'))
	if agenda.detail(appointment, 'organizer'):
		# Translators: Said after an appointment with an organizer, as "organized by Marie".
		parts.append(_('organized by {name}').format(name=agenda.detail(appointment, 'organizer')))
	if agenda.detail(appointment, 'link'):
		# Translators: Said after an appointment with a meeting link (O opens it).
		parts.append(_('with a meeting link'))
	return parts


def reminder_names():
	"""In the order of agenda.REMINDERS."""
	return [
		# Translators: A choice of when a reminder is given.
		_('At the time of the appointment'), _('5 minutes before'), _('15 minutes before'), _('30 minutes before'),
		_('1 hour before'), _('2 hours before'), _('The day before'), _('2 days before'), _('3 days before'), _('1 week before')]


def delay_text(minutes):
	"""A delay before an appointment, as '45 minutes before', '4 hours before', '10 days before'."""
	if minutes in agenda.REMINDERS:
		return reminder_names()[agenda.REMINDERS.index(minutes)].lower()
	if minutes % 10080 == 0:
		# Translators: A reminder given weeks before an appointment.
		return _('{count} weeks before').format(count=minutes // 10080)
	if minutes % 1440 == 0:
		# Translators: A reminder given days before an appointment.
		return _('{count} days before').format(count=minutes // 1440)
	if minutes % 60 == 0:
		# Translators: A reminder given hours before an appointment.
		return _('{count} hours before').format(count=minutes // 60)
	# Translators: A reminder given minutes before an appointment.
	return _('{count} minutes before').format(count=minutes)


def time_of_day_text(item):
	"""A reminder at a time of day, as 'the day before at 20:00', 'on the day at 8:00', '3 days before at 9:00'."""
	hour, minute = str(item.get('time', '08:00')).split(':')
	time = time_text(datetime.time(int(hour), int(minute)))
	days = int(item.get('daysBefore', 0))
	if days == 0:
		# Translators: A reminder at a time of day, on the day of the appointment.
		return _('on the day at {time}').format(time=time)
	if days == 1:
		# Translators: A reminder at a time of day, the day before the appointment.
		return _('the day before at {time}').format(time=time)
	# Translators: A reminder at a time of day, days before the appointment.
	return _('{count} days before at {time}').format(count=days, time=time)


def reminders_text(appointment):
	"""Every reminder of an appointment, in words."""
	names = [] if agenda.all_day(appointment) else [delay_text(int(m)) for m in sorted(appointment.get('reminders', ()))]
	names += [time_of_day_text(item) for item in appointment.get('reminderTimes', ())]
	return names


def when_text(appointment, day=None):
	"""When the appointment is, for one day of it: '13:30', '13:30 to 14:30', 'all day',
	'all day, day 2 of 8', '13:30, until Friday, July 10, 2026 at 17:00'."""
	start_day = agenda.occurrence_start(appointment, day) if day else None
	start_day = start_day or agenda.start_day(appointment)
	length = agenda.span(appointment)
	if agenda.all_day(appointment):
		if length == 1:
			# Translators: An appointment that lasts the whole day.
			return _('all day')
		if day:
			# Translators: One day of a whole-day appointment of several days, as "all day, day 2 of 8".
			return _('all day, day {number} of {count}').format(number=(day - start_day).days + 1, count=length)
		last = start_day + datetime.timedelta(days=length - 1)
		# Translators: A whole-day appointment of several days, as "all day, from Friday, July 3 to Friday, July 10, 2026".
		return _('all day, from {start} to {end}').format(start=date_text(start_day), end=date_text(last))
	start = time_text(agenda.start_time(appointment))
	if appointment.get('_zoneTime'):
		hour, minute = appointment['_zoneTime'].split(':')
		# Translators: The time of an appointment in another time zone, as "10:00 (16:00 Brussels, Copenhagen, Madrid, Paris time)".
		start = _('{here} ({there} {zone} time)').format(here=start, there=time_text(datetime.time(int(hour), int(minute))),
			zone=tz.short_name(appointment['timeZone']))
	end = agenda.end_of(appointment, start_day)
	if end is None:
		return start
	if length == 1:
		# Translators: An appointment with an end time, as "13:30 to 14:30".
		return _('{start} to {end}').format(start=start, end=time_text(end.time()))
	if day and day != start_day:
		# Translators: A later day of an appointment of several days, as "continued, until 17:00, day 2 of 3".
		return _('continued, until {end}, day {number} of {count}').format(
			end=time_text(end.time()) if end.date() == day else date_text(end.date()) + ' ' + time_text(end.time()),
			number=(day - start_day).days + 1, count=length)
	# Translators: An appointment ending on a later day, as "13:30, until Friday, July 10, 2026 at 17:00".
	return _('{start}, until {date} at {end}').format(start=start, date=date_text(end.date()), end=time_text(end.time()))


def appointment_text(appointment, with_details=True, day=None):
	"""'10:30 to 11:00, Dentist, 30 Ferland street, every week on Tuesday, reminder 15 minutes before, Health, with a note'."""
	parts = [when_text(appointment, day), appointment['text']]
	if appointment.get('location'):
		parts.append(appointment['location'])
	if agenda.cancelled(appointment) and not with_details:
		parts.append(_('cancelled'))
	if with_details:
		parts += details_text(appointment)
		repeat = repeat_text(appointment)
		if repeat:
			parts.append(repeat)
		names = reminders_text(appointment)
		if names:
			# Translators: The reminders of an appointment, as in "reminder 15 minutes before".
			parts.append(_('reminder {when}').format(when=_(' and ').join(names)))
		else:
			# Translators: An appointment without any reminder.
			parts.append(_('no reminder'))
		if appointment.get('_changed'):
			# Translators: Said after one day of a repeating appointment that was changed alone.
			parts.append(_('changed for this day'))
		parts += agenda.all_categories(appointment)
		if appointment.get('_agenda'):
			# Translators: Said after an appointment of another agenda, as "agenda Marie".
			parts.append(_('agenda {name}').format(name=appointment['_agenda']))
		if appointment.get('note'):
			# Translators: Said after an appointment that has a note.
			parts.append(_('with a note'))
	return ', '.join(parts)


def series_text(appointment, today):
	"""An appointment of the list of all the appointments: when it comes next, or when it was."""
	parts = [appointment['text']]
	upcoming = agenda.next_occurrence(appointment, today)
	if upcoming:
		# Translators: In the list of all appointments, as in "next: Thursday, November 26, 2026 at 10:30".
		parts.append(_('next: {date}, {time}').format(date=relative_date_text(upcoming, today), time=when_text(appointment)))
	else:
		# Translators: In the list of all appointments, an appointment that is over.
		parts.append(_('over, last on {date}').format(date=date_text(agenda.last_day(appointment) or agenda.start_day(appointment))))
	for extra in (repeat_text(appointment), end_text(appointment), appointment.get('category', '')):
		if extra:
			parts.append(extra)
	if appointment.get('note'):
		parts.append(_('with a note'))
	return ', '.join(parts)


def day_summary(appointments, day, today):
	"""What is said for a day: its date, then its appointments."""
	found = agenda.day_appointments(appointments, day)
	date = relative_date_text(day, today)
	if not found:
		# Translators: A day without any appointment, as in "Tomorrow, ...: no appointment."
		return _('{date}: no appointment.').format(date=date)
	items = '; '.join(appointment_text(a, with_details=False, day=day) for a in found)
	if len(found) == 1:
		# Translators: A day with one appointment.
		return _('{date}: 1 appointment: {items}.').format(date=date, items=items)
	# Translators: A day with several appointments.
	return _('{date}: {count} appointments: {items}.').format(date=date, count=len(found), items=items)


def day_heading(appointments, day, today):
	"""'Tomorrow, Tuesday, November 20, 2026: 2 appointments.'"""
	date = relative_date_text(day, today)
	count = len(agenda.day_appointments(appointments, day))
	if not count:
		return _('{date}: no appointment.').format(date=date)
	if count == 1:
		# Translators: The day shown in the agenda window, with one appointment.
		return _('{date}: 1 appointment.').format(date=date)
	# Translators: The day shown in the agenda window, with several appointments.
	return _('{date}: {count} appointments.').format(date=date, count=count)


def upcoming_text(when, appointment, today):
	"""An appointment of the upcoming list or of the journal: 'Tomorrow, Tuesday, ...: 10:30, Dentist'."""
	# Translators: An entry of the upcoming appointments, as in "Tomorrow, Tuesday, November 20, 2026: 10:30, Dentist".
	text = _('{date}: {appointment}').format(date=relative_date_text(when.date(), today),
		appointment=appointment_text(appointment, with_details=False, day=when.date()))
	if appointment.get('note'):
		# Translators: An entry followed by its note, as in "...: 10:30, Dentist. Note: bring the X-rays".
		text = _('{entry}. Note: {note}').format(entry=text, note=' '.join(appointment['note'].split()))
	return text


def reminder_text(when, minutes, appointment, now, hide=False):
	"""What is said when a reminder is due. hide: a private appointment without its subject (for the phone)."""
	sentence = _reminder_sentence(when, minutes, appointment, now, hide)
	if agenda.detail(appointment, 'importance') == 'high':
		# Translators: Before the reminder of an important appointment, as "Important. Reminder: ...".
		sentence = _('Important. {reminder}').format(reminder=sentence)
	return sentence


def _reminder_sentence(when, minutes, appointment, now, hide):
	time = time_text(when.time())
	# Translators: What a private appointment is called in the reminders sent to the phone.
	text = _('private appointment') if hide and agenda.detail(appointment, 'private') else appointment['text']
	if agenda.all_day(appointment):
		days = (when.date() - now.date()).days
		if days <= 0:
			# Translators: A reminder of a whole-day appointment that is today.
			return _('Reminder: {text}, today, all day.').format(text=text)
		if days == 1:
			# Translators: A reminder of a whole-day appointment that is tomorrow.
			return _('Reminder: {text}, tomorrow, all day.').format(text=text)
		# Translators: A reminder of a whole-day appointment in some days.
		return _('Reminder: {text}, in {days} days, on {date}, all day.').format(text=text, days=days, date=date_text(when.date()))
	if minutes == 0:
		# Translators: A reminder given at the time of the appointment.
		return _('Reminder: {text}, now, at {time}.').format(text=text, time=time)
	days = (when.date() - now.date()).days
	if days == 1:
		# Translators: A reminder given the day before.
		return _('Reminder: {text}, tomorrow at {time}.').format(text=text, time=time)
	if days > 1:
		# Translators: A reminder given days before, as in "Reminder: Dentist, in 3 days, on Thursday, ... at 10:30."
		return _('Reminder: {text}, in {days} days, on {date} at {time}.').format(text=text, days=days, date=date_text(when.date()), time=time)
	left = max(1, round((when - now).total_seconds() / 60))
	if left >= 60 and left % 60 == 0:
		hours = left // 60
		# Translators: A reminder given hours before. {hours} is a number.
		delay = _('in 1 hour') if hours == 1 else _('in {hours} hours').format(hours=hours)
	else:
		# Translators: A reminder given minutes before. {minutes} is a number.
		delay = _('in 1 minute') if left == 1 else _('in {minutes} minutes').format(minutes=left)
	# Translators: A reminder, as in "Reminder: Dentist, in 15 minutes, at 10:30."
	return _('Reminder: {text}, {delay}, at {time}.').format(text=text, delay=delay, time=time)


def next_text(appointments, now):
	"""The next appointment, said by a command."""
	found = agenda.upcoming(appointments, now)
	if not found:
		# Translators: Said when there is no appointment in the coming days.
		return _('No appointment in the next {days} days.').format(days=agenda.UPCOMING_DAYS)
	when, appointment = found[0]
	# Translators: The next appointment, as in "Next appointment: Today, ...: 10:30, Dentist."
	return _('Next appointment: {entry}.').format(entry=upcoming_text(when, appointment, now.date()))


def report_lines(appointments, today, days):
	"""The text report: one line per appointment, day after day, with the notes."""
	lines = []
	for offset in range(days + 1):
		day = today + datetime.timedelta(days=offset)
		for appointment in agenda.day_appointments(appointments, day):
			# Translators: A line of the text report, as in "Thursday, November 19, 2026, 10:30, Dentist".
			private = agenda.detail(appointment, 'private')
			line = _('{date}, {time}, {text}').format(date=date_text(day), time=when_text(appointment, day),
				text=_('private appointment') if private else appointment['text'])
			if appointment.get('location') and not private:
				line += ', ' + appointment['location']
			if agenda.cancelled(appointment):
				line += ', ' + _('cancelled')
			if appointment.get('note') and not private:
				line += '. ' + _('Note: {note}').format(note=' '.join(appointment['note'].split()))
			lines.append(line)
	return lines
