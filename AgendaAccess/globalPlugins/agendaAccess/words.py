# AgendaAccess: the appointments written as sentences, in French or in English.

import datetime

import addonHandler

from . import agenda

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


def reminder_names():
	"""In the order of agenda.REMINDERS."""
	return [
		# Translators: A choice of when a reminder is given.
		_('At the time of the appointment'), _('5 minutes before'), _('15 minutes before'), _('30 minutes before'),
		_('1 hour before'), _('2 hours before'), _('The day before'), _('2 days before'), _('3 days before'), _('1 week before')]


def appointment_text(appointment, with_details=True):
	"""'10:30, Dentist, every week on Tuesday, reminder 15 minutes before, Health, with a note'."""
	parts = [time_text(agenda.start_time(appointment)), appointment['text']]
	if with_details:
		repeat = repeat_text(appointment)
		if repeat:
			parts.append(repeat)
		names = [reminder_names()[agenda.REMINDERS.index(m)].lower() for m in appointment.get('reminders', ()) if m in agenda.REMINDERS]
		if names:
			# Translators: The reminders of an appointment, as in "reminder 15 minutes before".
			parts.append(_('reminder {when}').format(when=_(' and ').join(names)))
		else:
			# Translators: An appointment without any reminder.
			parts.append(_('no reminder'))
		if appointment.get('category'):
			parts.append(appointment['category'])
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
		parts.append(_('next: {date} at {time}').format(date=relative_date_text(upcoming, today), time=time_text(agenda.start_time(appointment))))
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
	items = '; '.join(appointment_text(a, with_details=False) for a in found)
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
		appointment=appointment_text(appointment, with_details=False))
	if appointment.get('note'):
		# Translators: An entry followed by its note, as in "...: 10:30, Dentist. Note: bring the X-rays".
		text = _('{entry}. Note: {note}').format(entry=text, note=' '.join(appointment['note'].split()))
	return text


def reminder_text(when, minutes, appointment, now):
	"""What is said when a reminder is due."""
	time = time_text(when.time())
	text = appointment['text']
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
			line = _('{date}, {time}, {text}').format(date=date_text(day), time=time_text(agenda.start_time(appointment)), text=appointment['text'])
			if appointment.get('note'):
				line += '. ' + _('Note: {note}').format(note=' '.join(appointment['note'].split()))
			lines.append(line)
	return lines
