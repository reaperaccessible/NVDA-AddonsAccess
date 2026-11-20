# NVDA Add-on: AgendaAccess, by ReaperAccessible.
# An agenda made for NVDA: appointments, repetitions and reminders. Inspired by Agenda,
# by Abel Passos do Nascimento Jr, Ângelo Abrantes and Rui Fontes <https://github.com/ruifontes/agenda-for-NVDA>,
# and by Mon agenda, by Louis-Rock Lampron.
# This add-on is free software, licensed under the terms of the GNU General Public License (version 2).
# See the file LICENSE for more details.

import calendar
import datetime
import json
import os
import time

import wx

import addonHandler
import config
import globalPluginHandler
import globalVars
import gui
import ui
from logHandler import log

from . import agenda, ics, phone, sound, sources, tz, words
from .shortcuts import Shortcuts, ShortcutsDialog
from .updater import Updater

addonHandler.initTranslation()

# The add-on's own name: never translated.
ADDON_NAME = 'AgendaAccess'
CONFIG_SECTION = 'agendaAccess'
CONFIG_SPEC = {
	'dataFile': 'string(default="")',
	'announceAtStart': 'boolean(default=true)',
	'reminderSound': 'boolean(default=true)',
	'reminderWindow': 'boolean(default=true)',
	'soundFile': 'string(default="")',
	'volume': 'integer(default=80, min=10, max=100)',
	'repeatTimes': 'integer(default=1, min=1, max=10)',
	'nightMode': 'boolean(default=false)',
	'nightStart': 'integer(default=22, min=0, max=23)',
	'nightEnd': 'integer(default=7, min=0, max=23)',
	'pausedUntil': 'string(default="")',
	'agendas': 'string(default="[]")',
	'mainName': 'string(default="")',
	'publicCopy': 'string(default="")',
	'phoneEnabled': 'boolean(default=false)',
	'phoneName': 'string(default="")',
	'phoneServer': 'string(default="")',
	'checkForUpdates': 'boolean(default=true)',
}
STATE_FILE = 'agendaAccess-state.json'  # in NVDA's configuration folder: when the reminders were last looked at
DATA_FILE = 'agendaAccess.json'           # in NVDA's configuration folder, unless another place is chosen
BACKUP_FOLDER = 'agendaAccess-backups'    # always in NVDA's configuration folder: copies of this computer
FEED_FOLDER = 'agendaAccess-online'       # the online agendas, as last read
CHECK_EVERY = 15000       # ms between two looks at the reminders and at the file
STARTUP_ANNOUNCE = 8000   # ms after NVDA has started: today's appointments
REPEAT_EVERY = datetime.timedelta(minutes=1)
REPEAT_CHOICES = (1, 3, 5, 10)
PAUSES = (30, 60, 120, 240, 480)   # minutes
# After the day of the window changes, its list is filled again and NVDA would read it:
# for this long, only the add-on's own answer is spoken.
QUIET_AFTER_CHANGE = 0.4
YEARS = (1900, 2200)


def settings():
	return config.conf[CONFIG_SECTION]


_state = {'quietUntil': 0.0, 'ownSpeech': False, 'store': None, 'others': []}


def store():
	"""Your own agenda: the one new appointments go to unless another is chosen."""
	return _state['store']


def main_name():
	# Translators: The name of your own agenda, when several agendas are shown.
	return settings()['mainName'] or _('My agenda')


def agenda_infos():
	try:
		found = json.loads(settings()['agendas'] or '[]')
	except ValueError:
		found = []
	return [sources.describe(i) for i in found if isinstance(i, dict) and i.get('name')]


def _tagged(info, appointments):
	return [dict(a, _agenda=info['name'], _readOnly=info['readOnly']) for a in appointments]


def shown_appointments():
	"""Your appointments and those of the other agendas shown."""
	found = list(store().appointments)
	for info, source in _state['others']:
		if info['show']:
			found += _tagged(info, source.appointments)
	return found


def remindable_appointments():
	"""The appointments that give reminders: yours, and those of the agendas shown with their reminders on."""
	found = list(store().appointments)
	for info, source in _state['others']:
		if info['show'] and info['reminders']:
			found += _tagged(info, source.appointments)
	return found


def writable_agendas():
	"""(name, store) of the agendas new appointments can go to: yours first."""
	return [(main_name(), store())] + [(info['name'], source) for info, source in _state['others'] if not info['readOnly']]


def store_of(appointment):
	"""The store holding an appointment, or None when its agenda is read only."""
	name = appointment.get('_agenda')
	if not name:
		return store()
	for info, source in _state['others']:
		if info['name'] == name:
			return None if info['readOnly'] else source
	return None


def read_only_said():
	# Translators: Said when an appointment of an agenda shared in read only is to be changed.
	say(_('This appointment belongs to an agenda you can only read.'))


def say(text):
	"""Speak one of the add-on's answers: it gets through the quiet period."""
	_state['ownSpeech'] = True
	try:
		ui.message(text)
	finally:
		_state['ownSpeech'] = False


def _quiet_filter(speechSequence, *args, **kwargs):
	if _state['ownSpeech'] or time.monotonic() >= _state['quietUntil']:
		return speechSequence
	return []


def quiet():
	_state['quietUntil'] = time.monotonic() + QUIET_AFTER_CHANGE


def now():
	return datetime.datetime.now()


def today():
	return datetime.date.today()


def default_data_file():
	return os.path.join(globalVars.appArgs.configPath, DATA_FILE)


def data_file():
	return settings()['dataFile'] or default_data_file()


def play(value):
	"""A sound of the reminders, at the volume of the settings."""
	return sound.play(value, settings()['volume'])


def paused_until():
	try:
		until = datetime.datetime.fromisoformat(settings()['pausedUntil'])
	except ValueError:
		return None
	return until if until > now() else None


def commands():
	"""(name of the command, label in the keys window and description in NVDA's Input Gestures dialog)."""
	return [
		# Translators: A command of AgendaAccess.
		('openAgenda', _('Open the agenda')),
		('newAppointment', _('New appointment')),
		('announceToday', _("Announce today's appointments")),
		('announceTomorrow', _("Announce tomorrow's appointments")),
		('announceNext', _('Announce the next appointment')),
		('showUpcoming', _('Show the upcoming appointments')),
		('showAll', _('Show all the appointments')),
		('showJournal', _('Show the journal of past appointments')),
		('pauseReminders', _('Pause the reminders')),
	]


def help_lines():
	return [
		# Translators: A line of the help of the agenda window. The key comes first.
		_('Up and down arrows: the appointments of the day'),
		_('Left and right arrows: the day before or after'),
		_('Control with left and right arrows: the week before or after'),
		_('Page up and page down: the month before or after'),
		_('Control with page up and page down: the year before or after'),
		_('J and Shift+J: the next or previous day with appointments'),
		_('S and Shift+S: week after week, up to a day with appointments'),
		_('Space: the next free day'),
		_('Control+Home: today'),
		_('Control+G: go to a date'),
		_('Enter: edit the appointment, or create one on an empty day'),
		_('Control+N: new appointment on the day shown'),
		_('N: read the note of the appointment'),
		_('O: open the meeting link of the appointment'),
		_('Delete: delete the appointment'),
		_('Shift+Delete: delete every appointment of the day'),
		_('Control+U: the upcoming appointments'),
		_('Control+L: all the appointments, with a search'),
		_('Control+J: the journal of past appointments'),
		_('Control+P: pause the reminders'),
		_('F1: this help'),
		_('Escape: close the window'),
	]


def show_modal(dialog):
	gui.mainFrame.prePopup()
	try:
		return dialog.ShowModal()
	finally:
		gui.mainFrame.postPopup()


def ask_file(parent, message, wildcard, save, default='', overwrite_prompt=True):
	"""A file to open (save False) or to write (save True). Returns its path, or None."""
	style = (wx.FD_SAVE | (wx.FD_OVERWRITE_PROMPT if overwrite_prompt else 0)) if save else (wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
	dialog = wx.FileDialog(parent, message, defaultFile=default, wildcard=wildcard, style=style)
	try:
		return dialog.GetPath() if show_modal(dialog) == wx.ID_OK else None
	finally:
		dialog.Destroy()


def sound_wildcard():
	# Translators: The kind of files in the window to choose a sound.
	return _('Sounds (*.wav, *.mp3)') + '|*.wav;*.mp3'


def agenda_wildcard():
	# Translators: The kind of files in the windows to choose an agenda file.
	return _('Agenda (*.json)') + '|*.json'


def ringtone_names():
	"""In the order of sound.RINGTONES."""
	return [
		# Translators: The name of a ringtone of AgendaAccess.
		_('Chime'), _('Westminster bells'), _('Bell'), _('Marimba'), _('Harp'), _('Notification'),
		_('Soft beeps'), _('Digital alarm clock'), _('Gong')]


def category_names():
	"""The kinds of sounds of the collection, after their name in the lists, by folder of sound.CATEGORIES."""
	return {
		# Translators: The kind of a sound of the collection, said after its name, as in "Rush (alarm)".
		'alarms': _('alarm'), 'wakeup': _('wake-up'), 'notifications': _('notification'), 'soft': _('soft'), 'standard': _('ringtone')}


class SoundChoice:
	"""A list of sounds: each one is heard as soon as the arrows reach it.
	usual: the list of an appointment, which starts with the usual sound and no sound."""

	def __init__(self, parent, value, usual):
		self.parent = parent
		self.usual = usual
		self.custom = value if sound.is_file(value) else ''
		self.choice = wx.Choice(parent)
		self.fill(value)
		self.choice.Bind(wx.EVT_CHOICE, lambda event: self.on_choice())

	def fill(self, value):
		entries = []
		if self.usual:
			# Translators: The first choices of the sound of the reminders of an appointment.
			entries += [(_('The usual sound'), None), (_('No sound'), '')]
		entries += [(name, sound.ringtone(key)) for name, key in zip(ringtone_names(), sound.RINGTONES)]
		kinds = category_names()
		# Translators: A sound of the collection in the lists, as in "Rush (alarm)".
		entries += [(_('{name} ({kind})').format(name=name, kind=kinds.get(category, category)), value_)
			for category, name, value_ in sound.collection()]
		if self.custom:
			entries.append((os.path.basename(self.custom), self.custom))
		entries.append((_('Choose a sound file…'), 'choose'))
		self.entries = entries
		self.choice.Set([label for label, entry in entries])
		if not self.usual and not value:
			value = sound.ringtone(sound.RINGTONES[0])   # the usual sound of the settings: the chime
		values = [entry for label, entry in entries]
		self.choice.SetSelection(values.index(value) if value in values else 0)

	def on_choice(self):
		chosen = self.value()
		if chosen == 'choose':
			# Translators: Title of the window to choose a sound file.
			path = ask_file(self.parent, _('Choose a sound'), sound_wildcard(), save=False)
			if path:
				self.custom = path
			self.fill(path or self.custom or (None if self.usual else ''))
			chosen = self.value()
		if chosen is None:
			play(settings()['soundFile'])
		elif chosen == '' and self.usual:
			sound.stop()
		else:
			play(chosen)

	def value(self):
		return self.entries[self.choice.GetSelection()][1]


# --- the fields of a date, read well by NVDA: numbers, and the names of the months

class DateFields:
	"""Day, month and year: three fields, each read alone by NVDA."""

	def __init__(self, parent, sizer, day, labels):
		row = wx.BoxSizer(wx.HORIZONTAL)
		day_label, month_label, year_label = labels
		self.day = self._spin(parent, row, day_label, 1, 31, day.day)
		row.Add(wx.StaticText(parent, label=month_label), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)
		self.month = wx.Choice(parent, choices=words.month_names())
		self.month.SetSelection(day.month - 1)
		row.Add(self.month, 0, wx.LEFT, 4)
		row.AddSpacer(10)
		self.year = self._spin(parent, row, year_label, *YEARS, day.year)
		sizer.Add(row, 0, wx.ALL, 5)

	@staticmethod
	def _spin(parent, row, label, low, high, value):
		row.Add(wx.StaticText(parent, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
		spin = wx.SpinCtrl(parent, min=low, max=high, initial=value, size=(70, -1))
		row.Add(spin, 0, wx.LEFT, 4)
		return spin

	def value(self):
		"""The date chosen; the 31st of a shorter month becomes its last day."""
		year, month = self.year.GetValue(), self.month.GetSelection() + 1
		return datetime.date(year, month, min(self.day.GetValue(), calendar.monthrange(year, month)[1]))

	def set_value(self, day):
		self.day.SetValue(day.day)
		self.month.SetSelection(day.month - 1)
		self.year.SetValue(day.year)

	def enable(self, enabled):
		for control in (self.day, self.month, self.year):
			control.Enable(enabled)


def date_labels():
	# Translators: Labels of the fields of a date.
	return _('&Day:'), _('M&onth:'), _('&Year:')


class AppointmentDialog(wx.Dialog):
	"""A new appointment, or one being edited: everything in one window. Control+Enter saves.
	The fields that do not apply to the chosen repetition are greyed out, and Tab skips them."""

	def __init__(self, parent, day, appointment=None, single=False):
		"""single: one occurrence of a series, changed alone: its repetition cannot be changed."""
		# Translators: Title of the window of an appointment.
		title = _('Edit this day only') if single else _('Edit the appointment') if appointment else _('New appointment')
		super().__init__(parent, title=title)
		self.single = single
		self.appointment = appointment
		self.result = None
		a = appointment or {}
		start = agenda.start_day(appointment) if appointment else day
		at = agenda.start_time(appointment) if appointment else datetime.time(9, 0)
		rule = agenda.repeat_of(appointment) if appointment else agenda.no_repeat()
		sizer = wx.BoxSizer(wx.VERTICAL)
		pad = wx.LEFT | wx.RIGHT

		self.agendaName = None
		self.agendas = [name for name, writable in writable_agendas()]
		if len(self.agendas) > 1 and not single:
			# Translators: Label of the agenda an appointment goes to, when you have several.
			sizer.Add(wx.StaticText(self, label=_('A&genda:')), 0, pad | wx.TOP, 10)
			self.agendaChoice = wx.Choice(self, choices=self.agendas)
			current = a.get('_agendaName') or main_name()
			self.agendaChoice.SetSelection(self.agendas.index(current) if current in self.agendas else 0)
			sizer.Add(self.agendaChoice, 0, pad | wx.EXPAND, 10)
		else:
			self.agendaChoice = None
		# Translators: Label of the description of an appointment.
		sizer.Add(wx.StaticText(self, label=_('&What:')), 0, pad | wx.TOP, 10)
		self.text = wx.TextCtrl(self, value=a.get('text', ''), size=(460, -1))
		sizer.Add(self.text, 0, pad | wx.EXPAND, 10)
		# Translators: Label of the category of an appointment: type one, or choose one already used.
		sizer.Add(wx.StaticText(self, label=_('Cate&gory (optional):')), 0, pad | wx.TOP, 10)
		offered = words.default_categories()
		offered += [c for c in agenda.categories(shown_appointments()) if c not in offered]
		self.category = wx.ComboBox(self, value=a.get('category', ''), choices=offered)
		sizer.Add(self.category, 0, pad | wx.EXPAND, 10)
		# Translators: Label of the place of an appointment.
		sizer.Add(wx.StaticText(self, label=_('&Location (optional):')), 0, pad | wx.TOP, 10)
		self.location = wx.TextCtrl(self, value=a.get('location', ''))
		sizer.Add(self.location, 0, pad | wx.EXPAND, 10)
		self.date = DateFields(self, sizer, start, date_labels())
		# Translators: A check box: the appointment has no time, it lasts the whole day (or several days).
		self.allDay = wx.CheckBox(self, label=_('All day (no time)'))
		self.allDay.SetValue(bool(a.get('allDay')))
		sizer.Add(self.allDay, 0, pad | wx.TOP, 4)
		row = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: Label of the hour of an appointment.
		row.Add(wx.StaticText(self, label=_('&Hour:')), 0, wx.ALIGN_CENTER_VERTICAL)
		self.hour = wx.SpinCtrl(self, min=0, max=23, initial=at.hour, size=(70, -1))
		row.Add(self.hour, 0, wx.LEFT, 4)
		# Translators: Label of the minutes of an appointment.
		row.Add(wx.StaticText(self, label=_('Mi&nutes:')), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)
		self.minute = wx.SpinCtrl(self, min=0, max=59, initial=at.minute, size=(70, -1))
		row.Add(self.minute, 0, wx.LEFT, 4)
		sizer.Add(row, 0, wx.ALL, 5)
		# The end: a time on the same day, or a later day (several days); for a whole day, its last day.
		ends = agenda.end_of(appointment, start) if appointment else None
		# Translators: A check box: the appointment has an end (a time, or a last day for several days).
		self.hasEnd = wx.CheckBox(self, label=_('With an end (end time, or last day for several days)'))
		self.hasEnd.SetValue(bool(ends) or (bool(a.get('allDay')) and (a.get('days') or 1) > 1))
		sizer.Add(self.hasEnd, 0, pad | wx.TOP, 4)
		last = start + datetime.timedelta(days=(a.get('days') or 1) - 1) if a.get('allDay') else None
		default_end = ends or datetime.datetime.combine(start, at) + datetime.timedelta(hours=1)
		# Translators: Labels of the fields of the end of an appointment.
		self.endWhen = DateFields(self, sizer, last or default_end.date(), (_('End day:'), _('End month:'), _('End year:')))
		row = wx.BoxSizer(wx.HORIZONTAL)
		row.Add(wx.StaticText(self, label=_('End hour:')), 0, wx.ALIGN_CENTER_VERTICAL)
		self.endHour = wx.SpinCtrl(self, min=0, max=23, initial=default_end.hour, size=(70, -1))
		row.Add(self.endHour, 0, wx.LEFT, 4)
		row.Add(wx.StaticText(self, label=_('End minutes:')), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)
		self.endMinute = wx.SpinCtrl(self, min=0, max=59, initial=default_end.minute, size=(70, -1))
		row.Add(self.endMinute, 0, wx.LEFT, 4)
		sizer.Add(row, 0, wx.ALL, 5)

		# Translators: Label of how an appointment repeats.
		sizer.Add(wx.StaticText(self, label=_('&Repeat:')), 0, pad, 10)
		self.choices = words.repeat_choices()
		self.repeat = wx.Choice(self, choices=[label for label, kind, preset in self.choices])
		self.repeat.SetSelection(self.choice_of(rule))
		sizer.Add(self.repeat, 0, pad | wx.EXPAND, 10)
		self.everyLabel = wx.StaticText(self, label=_('Interval:'))
		sizer.Add(self.everyLabel, 0, pad | wx.TOP, 6)
		self.every = wx.SpinCtrl(self, min=1, max=365, initial=max(1, int(rule.get('every') or 1)), size=(70, -1))
		sizer.Add(self.every, 0, pad, 10)
		# Translators: Label of the days of the week of a weekly appointment.
		sizer.Add(wx.StaticText(self, label=_('Days of the week:')), 0, pad | wx.TOP, 6)
		self.weekdays = wx.CheckListBox(self, choices=words.weekday_names())
		for index in rule.get('weekdays') or [start.weekday()]:
			self.weekdays.Check(index)
		self.weekdays.SetSelection(0)
		sizer.Add(self.weekdays, 0, pad | wx.EXPAND, 10)
		# Translators: A check box: "the 2nd Tuesday" becomes "the last Tuesday", counted from the end of the month.
		self.fromEnd = wx.CheckBox(self, label=_('Count the weeks from the end of the month (as the last Friday)'))
		self.fromEnd.SetValue(bool(rule.get('fromEnd')))
		sizer.Add(self.fromEnd, 0, pad | wx.TOP, 6)

		# Translators: Label of the end of a repetition.
		sizer.Add(wx.StaticText(self, label=_('&End of the repetition:')), 0, pad | wx.TOP, 10)
		# Translators: The choices of the end of a repetition.
		self.end = wx.Choice(self, choices=[_('Never'), _('On a date'), _('After a number of times')])
		self.end.SetSelection(2 if a.get('count') else 1 if a.get('until') else 0)
		sizer.Add(self.end, 0, pad, 10)
		end = agenda.until_day(appointment) if appointment else None
		# Translators: Labels of the fields of the last date of a repetition.
		self.endDate = DateFields(self, sizer, end or agenda.shift_month(start, 1), (_('Last day:'), _('Last month:'), _('Last year:')))
		# Translators: Label of the number of times an appointment happens.
		sizer.Add(wx.StaticText(self, label=_('Number of times:')), 0, pad, 10)
		self.count = wx.SpinCtrl(self, min=1, max=9999, initial=a.get('count') or 10, size=(80, -1))
		sizer.Add(self.count, 0, pad, 10)

		# Translators: Label of the reminders of an appointment.
		sizer.Add(wx.StaticText(self, label=_('Re&minders:')), 0, pad | wx.TOP, 10)
		self.reminders = wx.CheckListBox(self, choices=words.reminder_names())
		self.set_reminders(a.get('reminders', agenda.DEFAULT_REMINDERS))
		self.reminders.SetSelection(0)
		sizer.Add(self.reminders, 0, pad | wx.EXPAND, 10)
		# The reminders not in the list: any delay, or a time of day.
		self.extraMinutes = [int(m) for m in a.get('reminders', ()) if int(m) not in agenda.REMINDERS]
		self.reminderTimes = [dict(r) for r in a.get('reminderTimes', ())]
		# Translators: A button opening the reminders not in the list: any delay, or at a time of day.
		self.moreReminders = wx.Button(self, label=_('More rem&inders…'))
		sizer.Add(self.moreReminders, 0, pad | wx.TOP, 4)
		# Translators: Label of the sound of the reminders of an appointment.
		sizer.Add(wx.StaticText(self, label=_('&Sound of its reminders:')), 0, pad | wx.TOP, 10)
		self.sound = SoundChoice(self, a.get('sound'), usual=True)
		sizer.Add(self.sound.choice, 0, pad | wx.EXPAND, 10)
		# Translators: Label of the note of an appointment: as long as needed.
		sizer.Add(wx.StaticText(self, label=_('No&te (optional):')), 0, pad | wx.TOP, 10)
		self.note = wx.TextCtrl(self, value=a.get('note', ''), size=(460, 70), style=wx.TE_MULTILINE)
		sizer.Add(self.note, 0, pad | wx.EXPAND, 10)
		self.details = {k: agenda.detail(a, k) for k in agenda.DETAILS}
		# Translators: A button opening the other details of an appointment: categories, importance, status…
		self.moreDetails = wx.Button(self, label=_('Other &details…'))
		sizer.Add(self.moreDetails, 0, pad | wx.TOP, 6)
		sizer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 10)
		self.SetSizerAndFit(sizer)

		for control in (self.repeat, self.end):
			control.Bind(wx.EVT_CHOICE, lambda event: self.update_fields())
		self.allDay.Bind(wx.EVT_CHECKBOX, lambda event: self.on_all_day())
		self.hasEnd.Bind(wx.EVT_CHECKBOX, lambda event: self.update_fields())
		self.moreReminders.Bind(wx.EVT_BUTTON, lambda event: self.more_reminders())
		self.moreDetails.Bind(wx.EVT_BUTTON, lambda event: self.more_details())
		self.category.Bind(wx.EVT_COMBOBOX, lambda event: self.use_category())
		self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
		self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
		self.update_fields()
		self.text.SetFocus()

	def choice_of(self, rule):
		for index, (label, kind, preset) in enumerate(self.choices):
			if kind == rule['kind'] and all(rule.get(k) == v for k, v in preset.items()):
				return index
		return next(i for i, (label, kind, preset) in enumerate(self.choices) if kind == rule['kind'] and not preset)

	def set_reminders(self, chosen):
		for index, minutes in enumerate(agenda.REMINDERS):
			self.reminders.Check(index, minutes in chosen)

	# --- a category already used gives its settings to a new appointment
	def use_category(self):
		if self.appointment:
			return
		model = agenda.latest_of_category(shown_appointments(), self.category.GetValue())
		if model is None:
			return
		at = agenda.start_time(model)
		self.hour.SetValue(at.hour)
		self.minute.SetValue(at.minute)
		rule = agenda.repeat_of(model)
		self.repeat.SetSelection(self.choice_of(rule))
		self.every.SetValue(max(1, int(rule.get('every') or 1)))
		for index in range(7):
			self.weekdays.Check(index, index in (rule.get('weekdays') or []))
		self.fromEnd.SetValue(bool(rule.get('fromEnd')))
		self.set_reminders(model.get('reminders', []))
		if sound.is_file(model.get('sound')):
			self.sound.custom = model['sound']
		self.sound.fill(model.get('sound'))
		if not self.text.GetValue().strip():
			self.text.SetValue(model['text'])
		self.update_fields()
		# Translators: Said when a category already used fills in a new appointment.
		say(_('Settings taken from the category {name}.').format(name=self.category.GetValue()))

	def update_fields(self):
		label, kind, preset = self.choices[self.repeat.GetSelection()]
		repeats = kind != 'none'
		units = {
			# Translators: Label of the interval of a repetition, as in "every 2 days".
			'days': _('Every how many days:'), 'weeks': _('Every how many weeks:'), 'months': _('Every how many months:'),
			'monthWeekday': _('Every how many months:'), 'years': _('Every how many years:'), 'yearWeekday': _('Every how many years:')}
		self.everyLabel.SetLabel(units.get(kind, _('Interval:')))
		self.every.Enable(repeats and not preset)
		self.weekdays.Enable(kind == 'weeks' and not preset)
		self.fromEnd.Enable(kind in ('monthWeekday', 'yearWeekday'))
		self.end.Enable(repeats)
		self.endDate.enable(repeats and self.end.GetSelection() == 1)
		self.count.Enable(repeats and self.end.GetSelection() == 2)
		if self.single:
			for control in (self.repeat, self.every, self.weekdays, self.fromEnd, self.end, self.count):
				control.Enable(False)
			self.endDate.enable(False)
		whole = self.allDay.GetValue()
		for control in (self.hour, self.minute):
			control.Enable(not whole)
		self.endWhen.enable(self.hasEnd.GetValue())
		for control in (self.endHour, self.endMinute):
			control.Enable(self.hasEnd.GetValue() and not whole)
		# A whole day has no time: only reminders at a time of day (More reminders).
		self.reminders.Enable(not whole)

	def on_all_day(self):
		if self.allDay.GetValue() and not self.reminderTimes:
			self.reminderTimes = [dict(r) for r in agenda.DEFAULT_REMINDER_TIMES]
			# Translators: Said when a whole-day appointment gets its usual reminder.
			say(_('Reminder on the day at 8:00 added. More reminders changes it.'))
		self.update_fields()

	def more_details(self):
		dialog = DetailsDialog(self, self.details, self.category.GetValue())
		try:
			if show_modal(dialog) == wx.ID_OK:
				self.details = dialog.result
		finally:
			dialog.Destroy()
		self.moreDetails.SetFocus()

	def more_reminders(self):
		dialog = RemindersDialog(self, self.extraMinutes, self.reminderTimes, self.allDay.GetValue())
		try:
			if show_modal(dialog) == wx.ID_OK:
				self.extraMinutes, self.reminderTimes = dialog.minutes, dialog.times
		finally:
			dialog.Destroy()
		self.moreReminders.SetFocus()

	def on_key(self, event):
		if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and event.GetModifiers() == wx.MOD_CONTROL:
			self.on_ok(None)
		else:
			event.Skip()

	def on_ok(self, event):
		if not self.text.GetValue().strip():
			# Translators: Said when an appointment is saved without a description.
			say(_('Write what the appointment is.'))
			self.text.SetFocus()
			return
		label, kind, preset = self.choices[self.repeat.GetSelection()]
		rule = {'kind': kind, 'every': self.every.GetValue(), 'fromEnd': self.fromEnd.GetValue() and kind in ('monthWeekday', 'yearWeekday'),
			'weekdays': [i for i in range(7) if self.weekdays.IsChecked(i)] if kind == 'weeks' else []}
		rule.update(preset)
		start = self.date.value()
		if kind == 'weeks' and not rule['weekdays']:
			# Translators: Said when a weekly appointment has no day of the week checked.
			say(_('Check at least one day of the week.'))
			self.weekdays.SetFocus()
			return
		until = self.endDate.value() if kind != 'none' and self.end.GetSelection() == 1 else None
		count = self.count.GetValue() if kind != 'none' and self.end.GetSelection() == 2 else None
		if until is not None and until < start:
			# Translators: Said when the end of a repetition is before the appointment.
			say(_('The repetition cannot stop before the appointment.'))
			self.endDate.day.SetFocus()
			return
		whole = self.allDay.GetValue()
		reminders = [] if whole else [minutes for index, minutes in enumerate(agenda.REMINDERS) if self.reminders.IsChecked(index)] + self.extraMinutes
		at = datetime.time(0, 0) if whole else datetime.time(self.hour.GetValue(), self.minute.GetValue())
		duration, days = None, 1
		if self.hasEnd.GetValue():
			last = self.endWhen.value()
			if whole:
				days = (last - start).days + 1
			else:
				duration = int((datetime.datetime.combine(last, datetime.time(self.endHour.GetValue(), self.endMinute.GetValue()))
					- datetime.datetime.combine(start, at)).total_seconds() // 60)
			if days < 1 or (duration is not None and duration <= 0):
				# Translators: Said when the end of an appointment is not after its start.
				say(_('The end must be after the start.'))
				self.endWhen.day.SetFocus()
				return
		fresh = agenda.new_appointment(start, at, self.text.GetValue(), rule, until, count, sorted(set(reminders)),
			self.note.GetValue(), self.category.GetValue(), self.sound.value(), location=self.location.GetValue(),
			all_day=whole, duration=duration, days=days, reminder_times=self.reminderTimes, details=self.details)
		# Fields this version does not know (written by a newer one) are kept: see Docs/agendaaccess-format.md.
		result = dict(self.appointment or {})
		result.update(fresh)
		if self.appointment:
			result['id'] = self.appointment['id']
			# Days removed or changed alone stay so, unless the appointment no longer repeats.
			result['skip'] = list(self.appointment.get('skip', ())) if kind != 'none' else []
			if kind == 'none':
				result.pop('overrides', None)
		result.pop('_agendaName', None)
		self.result = result
		if self.agendaChoice is not None:
			self.agendaName = self.agendas[self.agendaChoice.GetSelection()]
		self.EndModal(wx.ID_OK)


class RemindersDialog(wx.Dialog):
	"""The reminders not in the list of an appointment: any delay before it, or a time of day."""

	UNITS = (1, 60, 1440, 10080)   # minutes, hours, days, weeks

	def __init__(self, parent, minutes, times, whole):
		# Translators: Title of the window of the other reminders of an appointment.
		super().__init__(parent, title=_('More reminders'))
		self.minutes = list(minutes)
		self.times = [dict(t) for t in times]
		sizer = wx.BoxSizer(wx.VERTICAL)
		pad = wx.LEFT | wx.RIGHT
		# Translators: Label of the list of the other reminders.
		sizer.Add(wx.StaticText(self, label=_('&Other reminders (Delete to remove):')), 0, pad | wx.TOP, 10)
		self.list = wx.ListBox(self, size=(420, 110))
		sizer.Add(self.list, 0, pad | wx.EXPAND, 10)
		# Translators: A button removing the reminder chosen in the list.
		remove = wx.Button(self, label=_('&Remove this reminder'))
		sizer.Add(remove, 0, pad | wx.TOP, 4)
		row = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: Labels of a reminder given any delay before the appointment, as "45 minutes before".
		row.Add(wx.StaticText(self, label=_('&Delay:')), 0, wx.ALIGN_CENTER_VERTICAL)
		self.amount = wx.SpinCtrl(self, min=1, max=999, initial=45, size=(70, -1))
		row.Add(self.amount, 0, wx.LEFT, 4)
		# Translators: The units of the delay of a reminder.
		self.unit = wx.Choice(self, choices=[_('minutes before'), _('hours before'), _('days before'), _('weeks before')])
		self.unit.SetSelection(0)
		row.Add(self.unit, 0, wx.LEFT, 4)
		self.addDelay = wx.Button(self, label=_('&Add this delay'))
		row.Add(self.addDelay, 0, wx.LEFT, 8)
		sizer.Add(row, 0, wx.ALL, 10)
		row = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: Labels of a reminder at a time of day, as "the day before at 20:00".
		row.Add(wx.StaticText(self, label=_('Days &before (0: on the day):')), 0, wx.ALIGN_CENTER_VERTICAL)
		self.daysBefore = wx.SpinCtrl(self, min=0, max=60, initial=1, size=(70, -1))
		row.Add(self.daysBefore, 0, wx.LEFT, 4)
		row.Add(wx.StaticText(self, label=_('at &hour:')), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
		self.hour = wx.SpinCtrl(self, min=0, max=23, initial=20, size=(70, -1))
		row.Add(self.hour, 0, wx.LEFT, 4)
		row.Add(wx.StaticText(self, label=_('mi&nutes:')), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
		self.minute = wx.SpinCtrl(self, min=0, max=59, initial=0, size=(70, -1))
		row.Add(self.minute, 0, wx.LEFT, 4)
		self.addTime = wx.Button(self, label=_('Add this &time'))
		row.Add(self.addTime, 0, wx.LEFT, 8)
		sizer.Add(row, 0, wx.ALL, 10)
		sizer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 10)
		self.SetSizerAndFit(sizer)
		for control in (self.amount, self.unit, self.addDelay):
			control.Enable(not whole)   # a whole day has no time to count a delay from
		remove.Bind(wx.EVT_BUTTON, lambda event: self.remove())
		self.addDelay.Bind(wx.EVT_BUTTON, lambda event: self.add_delay())
		self.addTime.Bind(wx.EVT_BUTTON, lambda event: self.add_time())
		self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
		self.fill()
		self.list.SetFocus()

	def entries(self):
		return [('minutes', m) for m in sorted(set(self.minutes))] + [('time', t) for t in self.times]

	def fill(self, select=0):
		lines = [words.delay_text(v) if kind == 'minutes' else words.time_of_day_text(v) for kind, v in self.entries()]
		# Translators: The only line of the list of the other reminders when there is none.
		self.list.Set(lines or [_('No other reminder')])
		self.list.SetSelection(max(0, min(select, self.list.GetCount() - 1)))

	def add_delay(self):
		minutes = self.amount.GetValue() * self.UNITS[self.unit.GetSelection()]
		if minutes not in self.minutes:
			self.minutes.append(minutes)
		self.fill([e for e in self.entries()].index(('minutes', minutes)))
		# Translators: Said when a reminder is added, as "Reminder added: 45 minutes before."
		say(_('Reminder added: {when}.').format(when=words.delay_text(minutes)))

	def add_time(self):
		item = {'daysBefore': self.daysBefore.GetValue(), 'time': '%02d:%02d' % (self.hour.GetValue(), self.minute.GetValue())}
		if item not in self.times:
			self.times.append(item)
		self.fill(self.entries().index(('time', item)))
		say(_('Reminder added: {when}.').format(when=words.time_of_day_text(item)))

	def remove(self):
		entries = self.entries()
		index = self.list.GetSelection()
		if not entries or index < 0:
			return
		kind, value = entries[index]
		if kind == 'minutes':
			self.minutes = [m for m in self.minutes if m != value]
		else:
			self.times.remove(value)
		self.fill(index)
		# Translators: Said when a reminder is removed.
		say(_('Reminder removed.'))
		self.list.SetFocus()

	def on_key(self, event):
		if event.GetKeyCode() in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE) and self.FindFocus() is self.list:
			self.remove()
		else:
			event.Skip()


class DetailsDialog(wx.Dialog):
	"""The details few appointments need: more categories, importance, status, availability, private, organizer, link."""

	def __init__(self, parent, details, main_category):
		# Translators: Title of the window of the other details of an appointment.
		super().__init__(parent, title=_('Other details'))
		sizer = wx.BoxSizer(wx.VERTICAL)
		pad = wx.LEFT | wx.RIGHT
		self.names = words.default_categories()
		self.names += [c for c in agenda.categories(shown_appointments()) + list(details['categories']) if c not in self.names]
		self.names = [c for c in self.names if c != main_category]
		# Translators: Label of the other categories of an appointment, checked with the space bar.
		sizer.Add(wx.StaticText(self, label=_('Other &categories (space bar to check):')), 0, pad | wx.TOP, 10)
		self.categories = wx.CheckListBox(self, choices=self.names, size=(420, 140))
		for index, name in enumerate(self.names):
			self.categories.Check(index, name in details['categories'])
		self.categories.SetSelection(0)
		sizer.Add(self.categories, 0, pad | wx.EXPAND, 10)
		row = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: Label of a new category typed by the user.
		row.Add(wx.StaticText(self, label=_('&New category:')), 0, wx.ALIGN_CENTER_VERTICAL)
		self.newName = wx.TextCtrl(self, size=(200, -1))
		row.Add(self.newName, 0, wx.LEFT, 4)
		add = wx.Button(self, label=_('&Add it'))
		row.Add(add, 0, wx.LEFT, 6)
		sizer.Add(row, 0, wx.ALL, 10)
		self.importance = self.choice(sizer, _('&Importance:'), words.importance_names(), agenda.IMPORTANCES, details['importance'])
		self.status = self.choice(sizer, _('&Status:'), words.status_names(), agenda.STATUSES, details['status'])
		# Translators: Label of how available one is during the appointment.
		self.availability = self.choice(sizer, _('&Availability:'), words.availability_names(), agenda.AVAILABILITIES, details['availability'])
		# Translators: A check box: the appointment is private (its subject is not sent to the phone, nor in the report).
		self.private = wx.CheckBox(self, label=_('&Private: subject hidden on the phone and in the text report'))
		self.private.SetValue(bool(details['private']))
		sizer.Add(self.private, 0, pad | wx.TOP, 10)
		# Translators: Label of the organizer of an appointment.
		sizer.Add(wx.StaticText(self, label=_('&Organizer (optional):')), 0, pad | wx.TOP, 10)
		self.organizer = wx.TextCtrl(self, value=details['organizer'])
		sizer.Add(self.organizer, 0, pad | wx.EXPAND, 10)
		# Translators: Label of the link of an online meeting (Teams, Zoom…).
		sizer.Add(wx.StaticText(self, label=_('Meeting &link (optional):')), 0, pad | wx.TOP, 10)
		self.link = wx.TextCtrl(self, value=details['link'])
		sizer.Add(self.link, 0, pad | wx.EXPAND, 10)
		# Translators: Label of the time zone of an appointment: its date and time are those of that zone.
		sizer.Add(wx.StaticText(self, label=_('Time &zone of the date and time:')), 0, pad | wx.TOP, 10)
		self.zones = [('', '')] + tz.display_names()
		# Translators: The first choice of the time zone: the one of this computer.
		self.zone = wx.Choice(self, choices=[_('The time of this computer')] + [d for k, d in self.zones[1:]])
		keys = [k for k, d in self.zones]
		self.zone.SetSelection(keys.index(details.get('timeZone', '')) if details.get('timeZone', '') in keys else 0)
		sizer.Add(self.zone, 0, pad | wx.EXPAND, 10)
		sizer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 10)
		self.SetSizerAndFit(sizer)
		add.Bind(wx.EVT_BUTTON, lambda event: self.add())
		self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
		self.categories.SetFocus()

	def choice(self, sizer, label, names, values, value):
		sizer.Add(wx.StaticText(self, label=label), 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
		control = wx.Choice(self, choices=names)
		control.SetSelection(values.index(value) if value in values else 0)
		control.values = values
		sizer.Add(control, 0, wx.LEFT | wx.RIGHT, 10)
		return control

	def add(self):
		name = self.newName.GetValue().strip()
		if not name:
			return
		if name not in self.names:
			self.names.append(name)
			self.categories.Append(name)
		index = self.names.index(name)
		self.categories.Check(index)
		self.categories.SetSelection(index)
		self.newName.SetValue('')
		# Translators: Said when a new category is added and checked.
		say(_('Category {name} added and checked.').format(name=name))

	def on_ok(self, event):
		self.result = {
			'categories': [n for i, n in enumerate(self.names) if self.categories.IsChecked(i)],
			'importance': self.importance.values[self.importance.GetSelection()],
			'status': self.status.values[self.status.GetSelection()],
			'availability': self.availability.values[self.availability.GetSelection()],
			'private': self.private.GetValue(),
			'organizer': self.organizer.GetValue().strip(),
			'link': self.link.GetValue().strip(),
			'timeZone': self.zones[self.zone.GetSelection()][0],
		}
		self.EndModal(wx.ID_OK)


class GoToDateDialog(wx.Dialog):
	def __init__(self, parent, day):
		# Translators: Title of the window to go to a date.
		super().__init__(parent, title=_('Go to a date'))
		sizer = wx.BoxSizer(wx.VERTICAL)
		self.date = DateFields(self, sizer, day, date_labels())
		sizer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 10)
		self.SetSizerAndFit(sizer)
		self.date.day.SetFocus()


class ListDialog(wx.Dialog):
	"""A list read with the arrows: the help, the upcoming appointments, the journal."""

	def __init__(self, parent, title, label, lines, on_enter=None):
		super().__init__(parent, title=title)
		self.on_enter = on_enter
		self.chosen = None
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(wx.StaticText(self, label=label), 0, wx.ALL, 8)
		self.list = wx.ListBox(self, choices=lines, size=(560, 240))
		self.list.SetSelection(0)
		sizer.Add(self.list, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 8)
		close = wx.Button(self, wx.ID_CANCEL, label=_('&Close'))
		sizer.Add(close, 0, wx.ALL | wx.ALIGN_RIGHT, 8)
		self.SetSizerAndFit(sizer)
		self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
		self.list.SetFocus()

	def on_key(self, event):
		if self.on_enter and event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and self.FindFocus() is self.list:
			self.chosen = self.list.GetSelection()
			self.EndModal(wx.ID_OK)
		else:
			event.Skip()


class AllDialog(wx.Dialog):
	"""Every appointment once, with a search, a category and an order. Enter edits, Delete deletes."""

	def __init__(self, parent, window):
		# Translators: Title of the window of all the appointments.
		super().__init__(parent, title=_('All the appointments'))
		self.window = window
		self.shown = []
		sizer = wx.BoxSizer(wx.VERTICAL)
		pad = wx.LEFT | wx.RIGHT
		# Translators: Label of the search field of the window of all the appointments.
		sizer.Add(wx.StaticText(self, label=_('&Search:')), 0, pad | wx.TOP, 10)
		self.search = wx.TextCtrl(self)
		sizer.Add(self.search, 0, pad | wx.EXPAND, 10)
		# Translators: Label of the category shown in the window of all the appointments.
		sizer.Add(wx.StaticText(self, label=_('&Category:')), 0, pad | wx.TOP, 6)
		self.category = wx.Choice(self)
		sizer.Add(self.category, 0, pad | wx.EXPAND, 10)
		# Translators: Label of the order of the window of all the appointments.
		sizer.Add(wx.StaticText(self, label=_('&Order:')), 0, pad | wx.TOP, 6)
		# Translators: The orders of the window of all the appointments.
		self.order = wx.Choice(self, choices=[_('By date'), _('Alphabetical')])
		self.order.SetSelection(0)
		sizer.Add(self.order, 0, pad, 10)
		# Translators: Label of the list of the window of all the appointments.
		sizer.Add(wx.StaticText(self, label=_('&Appointments (Enter to edit):')), 0, pad | wx.TOP, 10)
		self.list = wx.ListBox(self, size=(600, 240))
		sizer.Add(self.list, 1, wx.ALL | wx.EXPAND, 10)
		buttons = wx.BoxSizer(wx.HORIZONTAL)
		for label, action in ((_('&Edit'), self.edit), (_('De&lete'), self.delete)):
			button = wx.Button(self, label=label)
			button.Bind(wx.EVT_BUTTON, lambda event, action=action: action())
			buttons.Add(button, 0, wx.ALL, 3)
		buttons.Add(wx.Button(self, wx.ID_CANCEL, label=_('&Close')), 0, wx.ALL, 3)
		sizer.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 7)
		self.SetSizerAndFit(sizer)
		self.search.Bind(wx.EVT_TEXT, lambda event: self.fill())
		self.category.Bind(wx.EVT_CHOICE, lambda event: self.fill())
		self.order.Bind(wx.EVT_CHOICE, lambda event: self.fill())
		self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
		self.fill_categories()
		self.fill()
		self.list.SetFocus()

	def fill_categories(self):
		chosen = self.category.GetStringSelection()
		# Translators: The first choice of the category: every appointment.
		names = [_('All')] + agenda.categories(shown_appointments())
		self.category.Set(names)
		self.category.SetSelection(names.index(chosen) if chosen in names else 0)

	def fill(self, select=None):
		found = list(shown_appointments())
		words_searched = self.search.GetValue().lower().split()
		if words_searched:
			found = [a for a in found if all(w in (a['text'] + ' ' + a.get('note', '') + ' ' + a.get('category', '')).lower() for w in words_searched)]
		if self.category.GetSelection() > 0:
			found = [a for a in found if a.get('category', '') == self.category.GetStringSelection()]
		day = today()
		if self.order.GetSelection() == 1:
			found.sort(key=lambda a: a['text'].lower())
		else:
			far = datetime.date.max
			found.sort(key=lambda a: (agenda.next_occurrence(a, day) or far, a['time'], a['text'].lower()))
		self.shown = found
		# Translators: The only line of the list of all the appointments when nothing is found.
		self.list.Set([words.series_text(a, day) for a in found] or [_('No appointment found')])
		index = next((i for i, a in enumerate(found) if a['id'] == select), 0)
		self.list.SetSelection(index)

	def selected(self):
		index = self.list.GetSelection()
		return self.shown[index] if self.shown and 0 <= index < len(self.shown) else None

	def edit(self):
		appointment = self.selected()
		if appointment and self.window.edit_appointment(appointment, parent=self):
			self.fill_categories()
			self.fill(select=appointment['id'])
			self.list.SetFocus()

	def delete(self):
		appointment = self.selected()
		if appointment and self.window.delete_appointment(appointment, None, parent=self):
			self.fill_categories()
			self.fill()
			self.list.SetFocus()

	def on_key(self, event):
		key, modifiers = event.GetKeyCode(), event.GetModifiers()
		if self.FindFocus() is self.list and modifiers == wx.MOD_NONE:
			if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
				self.edit()
				return
			if key in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE):
				self.delete()
				return
		event.Skip()


class PauseDialog(wx.Dialog):
	"""Pause the reminders for a while, or start them again."""

	def __init__(self, parent):
		# Translators: Title of the window to pause the reminders.
		super().__init__(parent, title=_('Pause the reminders'))
		sizer = wx.BoxSizer(wx.VERTICAL)
		until = paused_until()
		self.values = ([0] if until else []) + list(PAUSES)
		labels = []
		if until:
			# Translators: The first choice when the reminders are paused.
			labels.append(_('Start the reminders again now (paused until {time})').format(time=words.time_text(until.time())))
		for minutes in PAUSES:
			# Translators: A length of the pause of the reminders.
			labels.append(_('30 minutes') if minutes == 30 else _('1 hour') if minutes == 60 else _('{hours} hours').format(hours=minutes // 60))
		# Translators: Label of the length of the pause of the reminders.
		sizer.Add(wx.StaticText(self, label=_('&No reminder for:')), 0, wx.ALL, 10)
		self.choice = wx.Choice(self, choices=labels)
		self.choice.SetSelection(0)
		sizer.Add(self.choice, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)
		sizer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 10)
		self.SetSizerAndFit(sizer)
		self.choice.SetFocus()

	def minutes(self):
		return self.values[self.choice.GetSelection()]


class ReportDialog(wx.Dialog):
	def __init__(self, parent):
		# Translators: Title of the window of the text report.
		super().__init__(parent, title=_('Text report'))
		sizer = wx.BoxSizer(wx.VERTICAL)
		self.days = (30, 92, 366)
		# Translators: Label of the period of the text report.
		sizer.Add(wx.StaticText(self, label=_('&Appointments of the next:')), 0, wx.ALL, 10)
		# Translators: The periods of the text report.
		self.choice = wx.Choice(self, choices=[_('30 days'), _('3 months'), _('12 months')])
		self.choice.SetSelection(0)
		sizer.Add(self.choice, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)
		sizer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 10)
		self.SetSizerAndFit(sizer)
		self.choice.SetFocus()


class ReminderDialog(wx.Dialog):
	"""The reminders, kept on screen until closed: a reminder is never missed when away."""

	SNOOZES = (5, 10, 15, 30, 60)   # minutes

	def __init__(self, parent, texts, on_close, on_snooze=None):
		super().__init__(parent, title=ADDON_NAME)
		self.on_closed = on_close
		self.on_snooze = on_snooze
		self.texts = list(texts)
		sizer = wx.BoxSizer(wx.VERTICAL)
		# Translators: Label of the reminders in their window.
		sizer.Add(wx.StaticText(self, label=_('&Reminders:')), 0, wx.ALL, 8)
		self.text = wx.TextCtrl(self, value='\n'.join(texts), size=(480, 120), style=wx.TE_MULTILINE | wx.TE_READONLY)
		sizer.Add(self.text, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 8)
		row = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: Label of the delay after which the reminders are given again.
		row.Add(wx.StaticText(self, label=_('Remind me again &in:')), 0, wx.ALIGN_CENTER_VERTICAL)
		# Translators: The delays after which the reminders are given again.
		self.delay = wx.Choice(self, choices=[_('5 minutes'), _('10 minutes'), _('15 minutes'), _('30 minutes'), _('1 hour')])
		self.delay.SetSelection(0)
		row.Add(self.delay, 0, wx.LEFT, 4)
		# Translators: The button giving the reminders again after the delay chosen.
		snooze = wx.Button(self, label=_('Re&mind me again'))
		row.Add(snooze, 0, wx.LEFT, 8)
		sizer.Add(row, 0, wx.ALL, 8)
		# Translators: The button closing the reminders: they are not given again.
		ok = wx.Button(self, wx.ID_OK, label=_('&Dismiss'))
		ok.SetDefault()
		sizer.Add(ok, 0, wx.ALL | wx.ALIGN_RIGHT, 8)
		self.SetSizerAndFit(sizer)
		self.SetEscapeId(wx.ID_OK)
		snooze.Bind(wx.EVT_BUTTON, lambda event: self.snooze())
		self.Bind(wx.EVT_BUTTON, lambda event: self.Close(), id=wx.ID_OK)
		self.Bind(wx.EVT_CLOSE, self.on_close)

	def add(self, texts):
		self.texts += texts
		self.text.AppendText('\n' + '\n'.join(texts))

	def snooze(self):
		minutes = self.SNOOZES[self.delay.GetSelection()]
		if self.on_snooze:
			self.on_snooze(self.texts, minutes)
		# Translators: Said when the reminders will be given again, as "Reminded again in 5 minutes."
		say(_('Reminded again in {delay}.').format(delay=self.delay.GetStringSelection()))
		self.Close()

	def on_close(self, event):
		self.on_closed()
		self.Destroy()


class AgendaWindow(wx.Dialog):
	"""The appointments of one day; the arrows change the day. The window stays open."""

	def __init__(self, parent, plugin):
		super().__init__(parent, title=ADDON_NAME)
		self.plugin = plugin
		self.day = today()
		self.shown = []
		sizer = wx.BoxSizer(wx.VERTICAL)
		# Translators: Label of the list of the agenda window.
		sizer.Add(wx.StaticText(self, label=_('&Appointments (Enter to edit):')), 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
		self.list = wx.ListBox(self, style=wx.LB_SINGLE, size=(600, 240))
		sizer.Add(self.list, 1, wx.ALL | wx.EXPAND, 10)
		buttons = wx.BoxSizer(wx.HORIZONTAL)
		for label, action in (
				# Translators: A button of the agenda window.
				(_('&New appointment'), self.new),
				(_('&Edit'), self.edit),
				(_('De&lete'), self.delete),
				(_('Go to a &date…'), self.go_to_date),
				(_('&Upcoming appointments…'), self.show_upcoming),
				(_('&More actions…'), self.more),
				(_('&Help (F1)'), self.show_help)):
			button = wx.Button(self, label=label)
			button.Bind(wx.EVT_BUTTON, lambda event, action=action: action())
			buttons.Add(button, 0, wx.ALL, 3)
			if action == self.more:
				self.moreButton = button
		close = wx.Button(self, wx.ID_CLOSE, label=_('&Close'))
		close.Bind(wx.EVT_BUTTON, lambda event: self.Close())
		buttons.Add(close, 0, wx.ALL, 3)
		sizer.Add(buttons, 0, wx.ALL, 7)
		self.SetSizerAndFit(sizer)
		self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
		self.Bind(wx.EVT_CLOSE, self.on_close)
		self.fill()
		self.list.SetFocus()

	# --- the day shown
	def fill(self, select=None):
		previous = self.selected()
		select = select or (previous['id'] if previous else None)
		self.shown = agenda.day_appointments(shown_appointments(), self.day)
		date = words.date_text(self.day)
		date = date[:1].upper() + date[1:]   # 'Mercredi 9 décembre 2026' at the start of the line
		# Translators: A line of the agenda window: the date, then the appointment.
		lines = [_('{date}, {appointment}').format(date=date, appointment=words.appointment_text(a, day=self.day)) for a in self.shown]
		if not lines:
			# Translators: The only line of the list of a day without appointments.
			lines = [_('{date}, no appointment').format(date=date)]
		self.list.Set(lines)
		self.SetTitle('%s - %s' % (ADDON_NAME, date))
		index = next((i for i, a in enumerate(self.shown) if a['id'] == select), 0)
		self.list.SetSelection(index)

	def show_day(self, day, select=None):
		if day is None or not YEARS[0] <= day.year <= YEARS[1]:
			return False
		self.day = day
		quiet()
		self.fill(select=select)
		say(words.day_heading(shown_appointments(), self.day, today()))
		return True

	def selected(self):
		index = self.list.GetSelection() if self.list else -1
		return self.shown[index] if self.shown and 0 <= index < len(self.shown) else None

	def refresh(self):
		"""The file changed on another computer: the day shown follows, silently."""
		self.fill()

	# --- the actions
	def new(self):
		dialog = AppointmentDialog(self, self.day)
		try:
			if show_modal(dialog) != wx.ID_OK:
				return
			appointment = dialog.result
			destination = dict(writable_agendas()).get(dialog.agendaName, store()) if dialog.agendaName else store()
		finally:
			dialog.Destroy()
		destination.add(appointment)
		self.after_change(appointment)
		# Translators: Said when an appointment has been added.
		say(_('Appointment added: {date}, {appointment}.').format(
			date=words.relative_date_text(agenda.start_day(appointment), today()),
			appointment=words.appointment_text(appointment, with_details=False)))
		self.warn_overlap(appointment)

	def edit(self):
		appointment = self.selected()
		if appointment is None:
			self.new()
		else:
			self.edit_appointment(appointment)

	def choose_scope(self, occurrence, parent):
		"""For one day of a series: change this day only, this day and the following ones, or every day."""
		date = words.date_text(agenda.start_day(occurrence))
		choices = [
			# Translators: The choices when editing one day of a repeating appointment.
			_('Only this one, on {date}').format(date=date),
			_('This one and the following ones'),
			_('Every one, the whole series')]
		# Translators: The question when editing one day of a repeating appointment.
		dialog = wx.SingleChoiceDialog(parent, _('This appointment repeats. What do you want to change?'), ADDON_NAME, choices)
		try:
			if show_modal(dialog) != wx.ID_OK:
				return None
			return ('one', 'following', 'all')[dialog.GetSelection()]
		finally:
			dialog.Destroy()

	def edit_appointment(self, appointment, parent=None):
		"""appointment: an occurrence (from a day) or a series (from the list of all the appointments)."""
		target = store_of(appointment)
		if target is None:
			read_only_said()
			return False
		series = target.find(appointment['id'])
		if series is None:
			return False
		scope = 'all'
		if agenda.is_occurrence(appointment) and agenda.repeats(series):
			scope = self.choose_scope(appointment, parent or self)
			if scope is None:
				return False
		original = agenda.original_day(appointment)
		if scope == 'following' and not agenda.has_earlier(series, original):
			scope = 'from'   # no day before this one: the whole series, starting from this day
		if scope == 'one':
			shown = agenda.without_occurrence(appointment)
			shown['repeat'] = agenda.no_repeat()
		elif scope in ('following', 'from'):
			shown = dict(agenda.without_occurrence(series), date=original.isoformat())
			shown.pop('overrides', None) if scope == 'following' else None
		else:
			shown = agenda.without_occurrence(series)
		shown['_agendaName'] = appointment.get('_agenda') or main_name()
		dialog = AppointmentDialog(parent or self, self.day, shown, single=(scope == 'one'))
		if scope == 'following' and dialog.agendaChoice is not None:
			dialog.agendaChoice.Enable(False)   # the days that follow stay in the same agenda
		try:
			if show_modal(dialog) != wx.ID_OK:
				return False
			changed = dialog.result
			agenda_name = dialog.agendaName
		finally:
			dialog.Destroy()
		if scope == 'one':
			changed['_original'] = original.isoformat()
			changes = agenda.occurrence_changes(series, changed)
			target.change_occurrence(series['id'], original, changes)
			moved = datetime.date.fromisoformat(changes.get('date') or original.isoformat())
			changed = target.find(series['id'])
			if parent is None:
				self.day = moved
		elif scope == 'following':
			changed['id'] = agenda.new_appointment(original, datetime.time(0, 0), 'x')['id']
			target.split(series['id'], original, changed)
		else:
			destination = dict(writable_agendas()).get(agenda_name, target) if agenda_name else target
			if destination is not target:
				target.remove(changed['id'])   # moved to another of your agendas
				destination.add(changed)
			else:
				target.replace(changed)
		if parent is None:
			if scope == 'one':
				quiet()
				self.fill(select=changed['id'])
				self.list.SetFocus()
			else:
				self.after_change(changed)
		else:
			self.fill()
		# Translators: Said when an appointment has been changed.
		say(_('Appointment saved.'))
		if changed and scope != 'one':
			self.warn_overlap(changed)
		return True

	def after_change(self, appointment):
		"""Show the day of the appointment, unless it still happens on the day shown: its next day from the one shown."""
		if not agenda.covers(appointment, self.day):
			self.day = (agenda.next_occurrence(appointment, self.day) or agenda.next_occurrence(appointment, agenda.start_day(appointment) - datetime.timedelta(days=7))
				or agenda.start_day(appointment))
		quiet()
		self.fill(select=appointment['id'])
		self.list.SetFocus()

	def delete(self):
		appointment = self.selected()
		if appointment is None:
			# Translators: Said when Delete is pressed on a day without appointments.
			say(_('No appointment to delete.'))
			return
		if self.delete_appointment(appointment, agenda.original_day(appointment)):
			quiet()
			self.fill()
			self.list.SetFocus()
			# Translators: Said when an appointment has been deleted.
			say(_('Deleted. {heading}').format(heading=words.day_heading(shown_appointments(), self.day, today())))

	def delete_appointment(self, appointment, day, parent=None):
		"""Ask, then delete. day: the day the occurrence has in its series, to delete only that day of a repetition."""
		parent = parent or self
		name = words.appointment_text(appointment, with_details=False)
		target = store_of(appointment)
		if target is None:
			read_only_said()
			return False
		appointment = target.find(appointment['id']) or appointment
		if not agenda.repeats(appointment) or day is None:
			# Translators: The question before deleting an appointment (every day of it, if it repeats).
			question = _('Delete {appointment}?') if not agenda.repeats(appointment) else _('Delete {appointment} and every day it repeats?')
			if gui.messageBox(question.format(appointment=name), ADDON_NAME, wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, parent) != wx.YES:
				return False
			target.remove(appointment['id'])
			if parent is not self:
				# Translators: Said when an appointment has been deleted from the list of all the appointments.
				say(_('Deleted.'))
			self.fill()
			return True
		# Translators: The question before deleting a repeating appointment.
		dialog = wx.MessageDialog(parent, _('{appointment} repeats. Delete only this day, or every day?').format(appointment=name),
			ADDON_NAME, wx.YES_NO | wx.CANCEL | wx.CANCEL_DEFAULT | wx.ICON_QUESTION)
		# Translators: The buttons of the question before deleting a repeating appointment.
		dialog.SetYesNoCancelLabels(_('Only &this day'), _('&Every day'), _('&Cancel'))
		try:
			answer = show_modal(dialog)
		finally:
			dialog.Destroy()
		if answer == wx.ID_YES:
			target.skip_day(appointment['id'], day)
		elif answer == wx.ID_NO:
			target.remove(appointment['id'])
		else:
			return False
		return True

	def clear_day(self):
		if not self.shown:
			say(_('No appointment to delete.'))
			return
		# Translators: The question before deleting every appointment of a day.
		question = _('Delete the {count} appointments of {date}? The ones that repeat keep their other days.').format(
			count=len(self.shown), date=words.date_text(self.day))
		if gui.messageBox(question, ADDON_NAME, wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) != wx.YES:
			return
		for name, writable in writable_agendas():
			writable.clear_day(self.day)   # the agendas you can only read keep their appointments
		quiet()
		self.fill()
		self.list.SetFocus()
		say(_('Deleted. {heading}').format(heading=words.day_heading(shown_appointments(), self.day, today())))

	def open_link(self):
		appointment = self.selected()
		link = agenda.detail(appointment, 'link') if appointment else ''
		if not link:
			# Translators: Said when O is pressed on an appointment without a meeting link.
			say(_('No meeting link.'))
			return
		try:
			os.startfile(link if '://' in link else 'https://' + link)
			# Translators: Said when the meeting link is opened.
			say(_('Opening the meeting link.'))
		except OSError:
			log.error('AgendaAccess: cannot open the link', exc_info=True)
			say(_('This link cannot be opened.'))

	def warn_overlap(self, appointment):
		"""Say which busy appointments are at the same time as this one, on its first day."""
		for occurrence in agenda.day_starts([appointment], agenda.start_day(appointment)) or []:
			others = agenda.overlaps(shown_appointments(), occurrence)
			if others:
				# Translators: Said when a new or changed appointment is at the same time as others.
				say(_('Careful: at the same time as {others}.').format(others='; '.join(words.appointment_text(o, with_details=False) for o in others)))
			return

	def read_note(self):
		appointment = self.selected()
		if appointment is None:
			return
		if appointment.get('note'):
			say(appointment['note'])
		else:
			# Translators: Said when the appointment has no note.
			say(_('No note.'))

	def go_to_date(self):
		dialog = GoToDateDialog(self, self.day)
		try:
			if show_modal(dialog) != wx.ID_OK:
				return
			day = dialog.date.value()
		finally:
			dialog.Destroy()
		self.list.SetFocus()
		self.show_day(day)

	def show_occurrence(self, chosen):
		if chosen is not None:
			when, appointment = chosen
			self.list.SetFocus()
			self.show_day(when.date(), select=appointment['id'])

	def show_upcoming(self):
		self.show_occurrence(self.plugin.choose_occurrence(self, upcoming=True))

	def show_journal(self):
		self.show_occurrence(self.plugin.choose_occurrence(self, upcoming=False))

	def show_all(self):
		dialog = AllDialog(self, self)
		try:
			show_modal(dialog)
		finally:
			dialog.Destroy()
		quiet()
		self.fill()
		self.list.SetFocus()

	def pause(self):
		dialog = PauseDialog(self)
		try:
			if show_modal(dialog) == wx.ID_OK:
				self.plugin.pause(dialog.minutes())
		finally:
			dialog.Destroy()
		self.list.SetFocus()

	def report(self):
		dialog = ReportDialog(self)
		try:
			if show_modal(dialog) != wx.ID_OK:
				return
			days = dialog.days[dialog.choice.GetSelection()]
		finally:
			dialog.Destroy()
		# Translators: Title of the window to save the text report, and the name of its file.
		path = ask_file(self, _('Save the text report'), _('Text (*.txt)') + '|*.txt', save=True, default=_('agenda.txt'))
		if not path:
			return
		lines = words.report_lines(shown_appointments(), today(), days)
		try:
			with open(path, 'w', encoding='utf-8-sig') as file:
				file.write('\r\n'.join(lines) + '\r\n')
		except OSError:
			log.error('AgendaAccess: cannot write the report', exc_info=True)
			# Translators: Said when a file cannot be written.
			say(_('This file cannot be written.'))
			return
		# Translators: Said when the text report is saved.
		say(_('Report saved: {count} appointments.').format(count=len(lines)))

	def export(self):
		# Translators: Title of the window to export the agenda, and the name of its file.
		path = ask_file(self, _('Export the agenda'), agenda_wildcard(), save=True, default=_('agenda copy.json'))
		if path:
			try:
				store().export_file(path)
				# Translators: Said when the agenda is exported.
				say(_('Agenda exported.'))
			except OSError:
				say(_('This file cannot be written.'))

	def import_agenda(self):
		# Translators: Title of the window to import an agenda.
		path = ask_file(self, _('Import an agenda'), agenda_wildcard(), save=False)
		if not path:
			return
		try:
			new = store().import_file(path)
		except (ValueError, OSError):
			# Translators: Said when the file chosen is not an agenda of AgendaAccess.
			say(_('This file is not an agenda of AgendaAccess.'))
			return
		quiet()
		self.fill()
		# Translators: Said when an agenda is imported.
		say(_('{count} appointments added.').format(count=new))

	def export_ics(self):
		# Translators: Title of the window to export to an .ics file, and the name of the file.
		path = ask_file(self, _('Export to an .ics file'), _('Calendar (*.ics)') + '|*.ics', save=True, default=_('agenda.ics'))
		if not path:
			return
		appointments = [{k: v for k, v in a.items() if not k.startswith('_')} for a in shown_appointments()]
		try:
			with open(path, 'w', encoding='utf-8', newline='') as file:
				file.write(ics.export(appointments))
		except OSError:
			say(_('This file cannot be written.'))
			return
		# Translators: Said when the agenda is exported to an .ics file.
		say(_('{count} appointments exported.').format(count=len(appointments)))

	def import_ics(self):
		# Translators: Title of the window to import an .ics file.
		path = ask_file(self, _('Import an .ics file'), _('Calendar (*.ics)') + '|*.ics', save=False)
		if not path:
			return
		try:
			with open(path, encoding='utf-8-sig', errors='replace') as file:
				appointments = ics.parse(file.read())
		except OSError:
			say(_('This file cannot be read.'))
			return
		if not appointments:
			# Translators: Said when an .ics file has no appointment that can be read.
			say(_('No appointment found in this file.'))
			return
		new, changed = store().import_appointments(appointments)
		quiet()
		self.fill()
		if not new and not changed:
			# Translators: Said when every appointment of an .ics file is already in the agenda.
			say(_('These appointments are already in your agenda.'))
			return
		# Translators: Said when an .ics file is imported: new ones, and ones already there that were updated.
		say(_('{count} appointments added, {changed} updated.').format(count=new, changed=changed))

	def more(self):
		menu = wx.Menu()
		for label, action in (
				# Translators: An entry of the More actions menu of the agenda window, with its key.
				(_('All the appointments, with a search\tCtrl+L'), self.show_all),
				(_('Journal of past appointments\tCtrl+J'), self.show_journal),
				(_('Pause the reminders…\tCtrl+P'), self.pause),
				(_('Save a text report…'), self.report),
				(_('Export the agenda…'), self.export),
				(_('Import an agenda…'), self.import_agenda),
				(_('Export to an .ics file (Outlook, Google, iPhone)…'), self.export_ics),
				(_('Import an .ics file…'), self.import_ics),
				(_('Assign keys to the commands…'), lambda: self.plugin.show_shortcuts(self))):
			item = menu.Append(wx.ID_ANY, label)
			self.Bind(wx.EVT_MENU, lambda event, action=action: wx.CallAfter(action), item)
		self.PopupMenu(menu, self.moreButton.GetPosition())
		menu.Destroy()

	def show_help(self):
		# Translators: Title and label of the help of the agenda window.
		dialog = ListDialog(self, _('{name} help').format(name=ADDON_NAME), _('&Keys of the agenda window:'), help_lines())
		try:
			show_modal(dialog)
		finally:
			dialog.Destroy()
		self.list.SetFocus()

	# --- the keys
	def on_key(self, event):
		key, modifiers = event.GetKeyCode(), event.GetModifiers()
		if key == wx.WXK_ESCAPE and modifiers == wx.MOD_NONE:
			self.Close()
			return
		if key == wx.WXK_F1 and modifiers == wx.MOD_NONE:
			self.show_help()
			return
		control = {ord('N'): self.new, ord('G'): self.go_to_date, ord('U'): self.show_upcoming, ord('L'): self.show_all,
			ord('J'): self.show_journal, ord('P'): self.pause, wx.WXK_HOME: lambda: self.show_day(today())}
		if modifiers == wx.MOD_CONTROL and key in control:
			control[key]()
			return
		if self.FindFocus() is self.list and self.list_key(key, modifiers):
			return
		event.Skip()

	def list_key(self, key, modifiers):
		appointments = shown_appointments()
		day = datetime.timedelta(days=1)
		moves = {
			(wx.WXK_LEFT, wx.MOD_NONE): lambda: self.day - day,
			(wx.WXK_RIGHT, wx.MOD_NONE): lambda: self.day + day,
			(wx.WXK_LEFT, wx.MOD_CONTROL): lambda: self.day - 7 * day,
			(wx.WXK_RIGHT, wx.MOD_CONTROL): lambda: self.day + 7 * day,
			(wx.WXK_PAGEUP, wx.MOD_NONE): lambda: agenda.shift_month(self.day, -1),
			(wx.WXK_PAGEDOWN, wx.MOD_NONE): lambda: agenda.shift_month(self.day, 1),
			(wx.WXK_PAGEUP, wx.MOD_CONTROL): lambda: agenda.shift_month(self.day, -12),
			(wx.WXK_PAGEDOWN, wx.MOD_CONTROL): lambda: agenda.shift_month(self.day, 12),
			(ord('J'), wx.MOD_NONE): lambda: agenda.find_day(appointments, self.day, 1),
			(ord('J'), wx.MOD_SHIFT): lambda: agenda.find_day(appointments, self.day, -1),
			(ord('S'), wx.MOD_NONE): lambda: agenda.find_day(appointments, self.day, 7),
			(ord('S'), wx.MOD_SHIFT): lambda: agenda.find_day(appointments, self.day, -7),
			(wx.WXK_SPACE, wx.MOD_NONE): lambda: agenda.find_day(appointments, self.day, 1, wanted=False),
		}
		if (key, modifiers) in moves:
			try:
				target = moves[(key, modifiers)]()
			except (OverflowError, ValueError):
				target = None
			if not self.show_day(target):
				# Translators: Said when J, S or Space find no day to go to.
				say(_('No day found.'))
			return True
		if key in (wx.WXK_UP, wx.WXK_DOWN) and modifiers == wx.MOD_NONE:
			# At the first or last line (or the only one), the list would stay silent: its line is said again.
			index, last = self.list.GetSelection(), self.list.GetCount() - 1
			if (key == wx.WXK_UP and index <= 0) or (key == wx.WXK_DOWN and index >= last):
				say(self.list.GetString(max(0, index)))
				return True
			return False
		actions = {
			(wx.WXK_RETURN, wx.MOD_NONE): self.edit, (wx.WXK_NUMPAD_ENTER, wx.MOD_NONE): self.edit,
			(wx.WXK_DELETE, wx.MOD_NONE): self.delete, (wx.WXK_NUMPAD_DELETE, wx.MOD_NONE): self.delete,
			(wx.WXK_DELETE, wx.MOD_SHIFT): self.clear_day, (wx.WXK_NUMPAD_DELETE, wx.MOD_SHIFT): self.clear_day,
			(ord('N'), wx.MOD_NONE): self.read_note,
			(ord('O'), wx.MOD_NONE): self.open_link,
		}
		if (key, modifiers) in actions:
			actions[(key, modifiers)]()
			return True
		return False

	def on_close(self, event):
		global _window
		_window = None
		self.Destroy()


_window = None


def bad_name():
	# Translators: Said when the secret name for the phone is not valid.
	return _('The secret name can only have letters without accents, digits, - and _, up to 64.')


class AgendasDialog(wx.Dialog):
	"""Your agenda and the other ones shown with it: yours (Work, Family), someone else's shared with you,
	an online agenda. And the public copy of yours, for the people you share it with."""

	def __init__(self, parent):
		# Translators: Title of the window of the agendas.
		super().__init__(parent, title=_('Agendas'))
		self.infos = agenda_infos()
		self.mainName = main_name()
		self.publicCopy = settings()['publicCopy']
		sizer = wx.BoxSizer(wx.VERTICAL)
		pad = wx.LEFT | wx.RIGHT
		# Translators: Label of the list of the agendas.
		sizer.Add(wx.StaticText(self, label=_('&Agendas:')), 0, pad | wx.TOP, 10)
		self.list = wx.ListBox(self, size=(520, 150))
		sizer.Add(self.list, 0, pad | wx.EXPAND, 10)
		buttons = wx.GridSizer(2, 4, 4)
		for label, action in (
				# Translators: Buttons of the window of the agendas.
				(_('Add an agenda &file (.json or .ics)…'), self.add_file),
				(_('Add an &online agenda (.ics address)…'), self.add_online),
				(_('&Rename…'), self.rename),
				(_('&Show or hide'), self.toggle_show),
				(_('Reminders &on or off'), self.toggle_reminders),
				(_('Re&move from the list'), self.remove)):
			button = wx.Button(self, label=label)
			button.Bind(wx.EVT_BUTTON, lambda event, action=action: action())
			buttons.Add(button, 0, wx.EXPAND)
		sizer.Add(buttons, 0, wx.ALL | wx.EXPAND, 10)
		# Translators: A check box: a copy of your agenda is written for the people you share it with.
		self.public = wx.CheckBox(self, label=_('Write a &public copy of my agenda, private appointments shown as Busy'))
		self.public.SetValue(bool(self.publicCopy))
		sizer.Add(self.public, 0, pad | wx.TOP, 6)
		self.publicPath = wx.TextCtrl(self, value=self.publicCopy, style=wx.TE_READONLY)
		sizer.Add(self.publicPath, 0, pad | wx.EXPAND, 10)
		# Translators: A button choosing where the public copy of your agenda is written.
		choose = wx.Button(self, label=_('&Choose where to write it…'))
		sizer.Add(choose, 0, pad | wx.TOP, 4)
		sizer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 10)
		self.SetSizerAndFit(sizer)
		self.public.Bind(wx.EVT_CHECKBOX, lambda event: self.on_public())
		choose.Bind(wx.EVT_BUTTON, lambda event: self.choose_public())
		self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
		self.fill()
		self.list.SetFocus()

	def line(self, info):
		if info['kind'] == 'url':
			# Translators: The kind of an agenda in the list.
			kind = _('online, read only')
		elif info['readOnly']:
			kind = _('file, read only')
		else:
			kind = _('file')
		# Translators: The state of an agenda in the list.
		state = [_('shown') if info['show'] else _('hidden'), _('reminders on') if info['reminders'] else _('reminders off')]
		return ', '.join([info['name'], kind] + state)

	def fill(self, select=0):
		# Translators: Your own agenda in the list of the agendas.
		lines = [_('{name}, your agenda, always shown').format(name=self.mainName)] + [self.line(i) for i in self.infos]
		self.list.Set(lines)
		self.list.SetSelection(max(0, min(select, len(lines) - 1)))

	def chosen(self):
		index = self.list.GetSelection()
		return index - 1 if index > 0 else None

	def ask(self, message, value=''):
		dialog = wx.TextEntryDialog(self, message, ADDON_NAME, value)
		try:
			return dialog.GetValue().strip() if show_modal(dialog) == wx.ID_OK else None
		finally:
			dialog.Destroy()

	def unique(self, name):
		names = [self.mainName] + [i['name'] for i in self.infos]
		base, number = name, 2
		while name in names:
			name = '%s %d' % (base, number)
			number += 1
		return name

	def add_file(self):
		# Translators: Title of the window to choose the file of another agenda.
		path = ask_file(self, _('Choose an agenda file'), _('Agenda (*.json; *.ics)') + '|*.json;*.ics', save=False)
		if not path:
			return
		# Translators: The question asking the name of an agenda added.
		name = self.ask(_('Name of this agenda (as Work, or the name of the person):'), os.path.splitext(os.path.basename(path))[0])
		if not name:
			return
		read_only = path.lower().endswith('.ics')
		if not read_only:
			# Translators: The question when adding an agenda file of AgendaAccess.
			read_only = gui.messageBox(_('Is this agenda someone else\'s, that you only read? Yes: you can only read it. No: it is one of your agendas, that you can change.'),
				ADDON_NAME, wx.YES_NO | wx.ICON_QUESTION, self) == wx.YES
		self.infos.append(sources.describe({'name': self.unique(name), 'kind': 'file', 'path': path, 'readOnly': read_only}))
		self.fill(len(self.infos))

	def add_online(self):
		# Translators: The question asking the address of an online agenda.
		address = self.ask(_('Address of the online agenda (.ics), as given by Outlook or Google:'))
		if not address:
			return
		name = self.ask(_('Name of this agenda (as Work, or the name of the person):'))
		if not name:
			return
		self.infos.append(sources.describe({'name': self.unique(name), 'kind': 'url', 'url': address}))
		self.fill(len(self.infos))

	def rename(self):
		index = self.chosen()
		current = self.mainName if index is None else self.infos[index]['name']
		# Translators: The question asking the new name of an agenda.
		name = self.ask(_('New name of this agenda:'), current)
		if not name or name == current:
			return
		name = self.unique(name)
		if index is None:
			self.mainName = name
		else:
			self.infos[index]['name'] = name
		self.fill(self.list.GetSelection())

	def toggle(self, field):
		index = self.chosen()
		if index is None:
			# Translators: Said when trying to hide your own agenda, or turn off its reminders.
			say(_('Your own agenda is always shown, with its reminders.'))
			return
		self.infos[index][field] = not self.infos[index][field]
		self.fill(index + 1)
		say(self.list.GetString(index + 1))

	def toggle_show(self):
		self.toggle('show')

	def toggle_reminders(self):
		self.toggle('reminders')

	def remove(self):
		index = self.chosen()
		if index is None:
			say(_('Your own agenda cannot be removed.'))
			return
		removed = self.infos.pop(index)
		self.fill(index)
		# Translators: Said when an agenda is removed from the list: its file is not deleted.
		say(_('{name} removed from the list. Its file is not deleted.').format(name=removed['name']))

	def on_public(self):
		if self.public.GetValue() and not self.publicCopy:
			self.choose_public()
		if not self.publicCopy:
			self.public.SetValue(False)

	def choose_public(self):
		# Translators: Title of the window to choose where the public copy is written, and its file name.
		path = ask_file(self, _('Where to write the public copy'), agenda_wildcard(), save=True, default=_('agenda public.json'), overwrite_prompt=False)
		if path:
			self.publicCopy = path
			self.publicPath.SetValue(path)
			self.public.SetValue(True)

	def on_ok(self, event):
		settings()['agendas'] = json.dumps(self.infos, ensure_ascii=False)
		settings()['mainName'] = '' if self.mainName == _('My agenda') else self.mainName
		settings()['publicCopy'] = self.publicCopy if self.public.GetValue() else ''
		self.EndModal(wx.ID_OK)


class AdvancedDialog(wx.Dialog):
	"""The options few people need: the sound file, the repetition of the reminders, the night."""

	def __init__(self, parent):
		# Translators: Title of the window of the advanced options.
		super().__init__(parent, title=_('Advanced options of {name}').format(name=ADDON_NAME))
		sizer = wx.BoxSizer(wx.VERTICAL)
		pad = wx.LEFT | wx.RIGHT
		# Translators: Label of the usual sound of the reminders.
		sizer.Add(wx.StaticText(self, label=_('Usual &sound of the reminders:')), 0, pad | wx.TOP, 10)
		self.sound = SoundChoice(self, settings()['soundFile'], usual=False)
		sizer.Add(self.sound.choice, 0, pad | wx.EXPAND, 10)
		# Translators: A button that plays the sound chosen.
		test = wx.Button(self, label=_('&Play it'))
		sizer.Add(test, 0, pad | wx.TOP, 6)
		# Translators: Label of how many times a reminder is given.
		sizer.Add(wx.StaticText(self, label=_('&Give each reminder:')), 0, pad | wx.TOP, 10)
		labels = [
			# Translators: The choices of how many times a reminder is given.
			_('Once'),
			_('3 times, a minute apart, until its window is closed'),
			_('5 times, a minute apart, until its window is closed'),
			_('10 times, a minute apart, until its window is closed')]
		self.repeat = wx.Choice(self, choices=labels)
		self.repeat.SetSelection(REPEAT_CHOICES.index(settings()['repeatTimes']) if settings()['repeatTimes'] in REPEAT_CHOICES else 0)
		sizer.Add(self.repeat, 0, pad | wx.EXPAND, 10)
		# Translators: A check box: the reminders make no sound at night.
		self.night = wx.CheckBox(self, label=_('&Night mode: no sound and no repetition at night'))
		self.night.SetValue(settings()['nightMode'])
		sizer.Add(self.night, 0, pad | wx.TOP, 10)
		row = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: Labels of the hours of the night mode.
		row.Add(wx.StaticText(self, label=_('Night &from (hour):')), 0, wx.ALIGN_CENTER_VERTICAL)
		self.nightStart = wx.SpinCtrl(self, min=0, max=23, initial=settings()['nightStart'], size=(70, -1))
		row.Add(self.nightStart, 0, wx.LEFT, 4)
		row.Add(wx.StaticText(self, label=_('&to (hour):')), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)
		self.nightEnd = wx.SpinCtrl(self, min=0, max=23, initial=settings()['nightEnd'], size=(70, -1))
		row.Add(self.nightEnd, 0, wx.LEFT, 4)
		sizer.Add(row, 0, wx.ALL, 10)
		# Translators: A check box: the reminders are also sent to a phone, with the free ntfy app.
		self.phone = wx.CheckBox(self, label=_('Also send the reminders to my p&hone, with the ntfy app'))
		self.phone.SetValue(settings()['phoneEnabled'])
		sizer.Add(self.phone, 0, pad | wx.TOP, 10)
		# Translators: Label of the secret name the phone subscribes to in the ntfy app.
		sizer.Add(wx.StaticText(self, label=_('Secret na&me to type in the ntfy app:')), 0, pad | wx.TOP, 6)
		self.phoneName = wx.TextCtrl(self, value=settings()['phoneName'] or phone.new_name())
		sizer.Add(self.phoneName, 0, pad | wx.EXPAND, 10)
		buttons = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: Buttons of the reminders sent to a phone.
		self.newName = wx.Button(self, label=_('Create a ne&w secret name'))
		self.test = wx.Button(self, label=_('S&end a test to the phone'))
		buttons.Add(self.newName, 0, wx.RIGHT, 6)
		buttons.Add(self.test, 0)
		sizer.Add(buttons, 0, pad | wx.TOP, 6)
		# Translators: Label of the address of the ntfy server; only people with their own server change it.
		sizer.Add(wx.StaticText(self, label=_('Ser&ver (leave it as it is, unless you have your own):')), 0, pad | wx.TOP, 6)
		self.phoneServer = wx.TextCtrl(self, value=settings()['phoneServer'] or phone.DEFAULT_SERVER)
		sizer.Add(self.phoneServer, 0, pad | wx.EXPAND, 10)
		sizer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 10)
		self.SetSizerAndFit(sizer)
		test.Bind(wx.EVT_BUTTON, lambda event: play(self.sound.value()))
		self.night.Bind(wx.EVT_CHECKBOX, lambda event: self.update())
		self.phone.Bind(wx.EVT_CHECKBOX, lambda event: self.update())
		self.newName.Bind(wx.EVT_BUTTON, lambda event: self.new_name())
		self.test.Bind(wx.EVT_BUTTON, lambda event: self.send_test())
		self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
		self.update()
		self.sound.choice.SetFocus()

	def update(self):
		self.nightStart.Enable(self.night.GetValue())
		self.nightEnd.Enable(self.night.GetValue())
		for control in (self.phoneName, self.newName, self.test, self.phoneServer):
			control.Enable(self.phone.GetValue())

	def new_name(self):
		self.phoneName.SetValue(phone.new_name())
		self.phoneName.SetFocus()
		# Translators: Said when a new secret name is created; the user then types it in the ntfy app.
		say(_('New secret name: {name}. Type it in the ntfy app of your phone.').format(name=' '.join(self.phoneName.GetValue())))

	def send_test(self):
		name = self.phoneName.GetValue().strip()
		if not phone.valid_name(name):
			say(bad_name())
			self.phoneName.SetFocus()
			return
		# Translators: Said while the test is being sent to the phone.
		say(_('Sending the test…'))
		def done(problem):
			if problem:
				log.warning('AgendaAccess: the test to the phone was not sent: %s' % problem)
				# Translators: Said when the test cannot be sent to the phone.
				wx.CallAfter(say, _('The test could not be sent. Check the Internet connection and the server.'))
			else:
				# Translators: Said when the test has been sent to the phone.
				wx.CallAfter(say, _('Test sent. If your phone did not get it, check that the ntfy app is subscribed to this secret name.'))
		# Translators: The text of the test sent to the phone.
		phone.send_later(self.phoneServer.GetValue(), name, ADDON_NAME, _('Test of AgendaAccess: your reminders will come here.'), done)

	def on_ok(self, event):
		name = self.phoneName.GetValue().strip()
		if self.phone.GetValue() and not phone.valid_name(name):
			say(bad_name())
			self.phoneName.SetFocus()
			return
		settings()['phoneEnabled'] = self.phone.GetValue()
		settings()['phoneName'] = name
		server = self.phoneServer.GetValue().strip()
		settings()['phoneServer'] = '' if server.rstrip('/') == phone.DEFAULT_SERVER else server
		settings()['soundFile'] = self.sound.value()
		settings()['repeatTimes'] = REPEAT_CHOICES[self.repeat.GetSelection()]
		settings()['nightMode'] = self.night.GetValue()
		settings()['nightStart'] = self.nightStart.GetValue()
		settings()['nightEnd'] = self.nightEnd.GetValue()
		self.EndModal(wx.ID_OK)


class AgendaAccessSettingsPanel(gui.settingsDialogs.SettingsPanel):
	# The add-on's own name: shown as a category of NVDA's Settings dialog, never translated.
	title = ADDON_NAME
	# Set by the global plugin; None on secure screens.
	updater = None
	plugin = None

	def makeSettings(self, settingsSizer):
		helper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		# Translators: Label of the file that keeps the appointments.
		helper.addItem(wx.StaticText(self, label=_('File of the agenda:')))
		self.file = helper.addItem(wx.TextCtrl(self, value=data_file(), style=wx.TE_READONLY, size=(460, -1)))
		# Translators: A button to choose where the file of the agenda is kept (as a Dropbox folder).
		self.chooseFile = helper.addItem(wx.Button(self, label=_('&Choose where to keep the agenda…')))
		self.chooseFile.Bind(wx.EVT_BUTTON, lambda event: self.choose_file())
		self.chooseFile.Enable(store() is not None)
		# Translators: A setting: today's appointments are said when NVDA starts.
		self.announceAtStart = helper.addItem(wx.CheckBox(self, label=_("&Announce today's appointments when NVDA starts")))
		self.announceAtStart.SetValue(settings()['announceAtStart'])
		# Translators: A setting: a sound is played with each reminder.
		self.reminderSound = helper.addItem(wx.CheckBox(self, label=_('Play a &sound with the reminders')))
		self.reminderSound.SetValue(settings()['reminderSound'])
		# Translators: A setting: the volume of every sound of the reminders.
		self.volumes = list(range(10, 101, 10))
		self.volume = helper.addLabeledControl(_('V&olume of the reminder sounds:'), wx.Choice,
			# Translators: A volume in the settings, as in 80%.
			choices=[_('{percent}%').format(percent=v) for v in self.volumes])
		self.volume.SetSelection(self.volumes.index(min(self.volumes, key=lambda v: abs(v - settings()['volume']))))
		# Each volume is heard with the usual sound as soon as the arrows reach it.
		self.volume.Bind(wx.EVT_CHOICE, lambda event: sound.play(settings()['soundFile'], self.volumes[self.volume.GetSelection()]))
		# Translators: A setting: the reminders are also shown in a window that stays until closed.
		self.reminderWindow = helper.addItem(wx.CheckBox(self, label=_('Also show the reminders in a &window that stays until closed')))
		self.reminderWindow.SetValue(settings()['reminderWindow'])
		# Translators: A button opening the advanced options.
		advanced = helper.addItem(wx.Button(self, label=_('Ad&vanced options…')))
		advanced.Bind(wx.EVT_BUTTON, lambda event: self.advanced())
		# Translators: A button opening the list of the agendas shown: yours, and others (Work, someone else's, online).
		agendas = helper.addItem(wx.Button(self, label=_('A&gendas…')))
		agendas.Bind(wx.EVT_BUTTON, lambda event: self.agendas())
		agendas.Enable(store() is not None)
		self.checkForUpdates = helper.addItem(wx.CheckBox(self, label=_('Check for &updates automatically when NVDA starts')))
		self.checkForUpdates.SetValue(settings()['checkForUpdates'])
		self.checkNow = helper.addItem(wx.Button(self, label=_('Check for updates &now')))
		self.checkNow.Bind(wx.EVT_BUTTON, lambda event: self.updater.check(manual=True))
		self.checkNow.Enable(self.updater is not None)

	def choose_file(self):
		"""Another place for the file: a Dropbox folder, to share the agenda between computers."""
		# Translators: Title of the window to choose where the agenda is kept.
		# An agenda file already there (another computer's) is chosen as well as a new name: no question about replacing it.
		path = ask_file(self, _('Where to keep the agenda'), agenda_wildcard(), save=True,
			default=os.path.basename(data_file()), overwrite_prompt=False)
		if not path:
			return
		existed = os.path.isfile(path)
		try:
			store().move_to(path)
		except (ValueError, OSError):
			log.error('AgendaAccess: cannot use %s' % path, exc_info=True)
			say(_('This file is not an agenda of AgendaAccess.'))
			return
		settings()['dataFile'] = '' if os.path.normcase(path) == os.path.normcase(default_data_file()) else path
		self.file.SetValue(path)
		if _window:
			_window.refresh()
		self.plugin.load_sources() if getattr(self, 'plugin', None) else None
		# Translators: Said when the agenda uses another file; an agenda already there was merged with this one.
		say(_('The agenda now uses this file. The appointments already in it were kept and joined to yours.') if existed
			else _('The agenda now uses this file.'))

	def agendas(self):
		dialog = AgendasDialog(self)
		try:
			if show_modal(dialog) == wx.ID_OK and self.plugin:
				self.plugin.load_sources()
				if _window:
					_window.refresh()
		finally:
			dialog.Destroy()

	def advanced(self):
		dialog = AdvancedDialog(self)
		try:
			show_modal(dialog)
		finally:
			dialog.Destroy()

	def onSave(self):
		settings()['announceAtStart'] = self.announceAtStart.GetValue()
		settings()['reminderSound'] = self.reminderSound.GetValue()
		settings()['volume'] = self.volumes[self.volume.GetSelection()]
		settings()['reminderWindow'] = self.reminderWindow.GetValue()
		settings()['checkForUpdates'] = self.checkForUpdates.GetValue()


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	# The add-on's own category in NVDA's Input Gestures dialog, never translated.
	scriptCategory = ADDON_NAME

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		config.conf.spec[CONFIG_SECTION] = CONFIG_SPEC
		self.updater = None
		self.timer = None
		self.reminderWindow = None
		self.repeating = None
		self.shortcuts = Shortcuts(GlobalPlugin)
		self.snoozed = []   # (when, texts, sound, texts for the phone) of the reminders to give again
		self.windowSound = False
		self.windowPhoneTexts = []
		if globalVars.appArgs.secure:
			return   # nothing on secure screens: the appointments are private
		try:
			_state['store'] = agenda.Store(data_file(), os.path.join(globalVars.appArgs.configPath, BACKUP_FOLDER))
		except Exception:
			log.error('AgendaAccess: cannot open the agenda file', exc_info=True)
			return
		try:
			from speech.extensions import filter_speechSequence
			filter_speechSequence.register(_quiet_filter)
		except Exception:
			log.error('AgendaAccess: cannot register the speech filter', exc_info=True)
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(AgendaAccessSettingsPanel)
		self.load_sources()
		self.lastCheck = now()
		# The reminders missed while NVDA was closed are given a few seconds after it starts.
		self.closedSince = self.read_state()
		self.startedAt = self.lastCheck
		self.timer = wx.Timer()
		self.timer.Bind(wx.EVT_TIMER, lambda event: self.tick())
		self.timer.Start(CHECK_EVERY)
		if settings()['announceAtStart']:
			wx.CallLater(STARTUP_ANNOUNCE, self.announce_at_start)
		wx.CallLater(STARTUP_ANNOUNCE, self.give_missed)
		self.updater = Updater(addonHandler.getCodeAddon())
		AgendaAccessSettingsPanel.updater = self.updater
		AgendaAccessSettingsPanel.plugin = self
		if settings()['checkForUpdates']:
			self.updater.scheduleStartupCheck()

	def terminate(self, *args, **kwargs):
		if self.timer is not None:
			self.timer.Stop()
			self.write_state()
		if self.updater is not None:
			self.updater.cancel()
		sound.stop()
		if _window:
			_window.Close()
		if self.reminderWindow:
			try:
				self.reminderWindow.Destroy()
			except RuntimeError:
				pass
		try:
			from speech.extensions import filter_speechSequence
			filter_speechSequence.unregister(_quiet_filter)
		except Exception:
			pass
		try:
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(AgendaAccessSettingsPanel)
		except ValueError:
			pass
		super().terminate(*args, **kwargs)

	# --- every few seconds: the file, then the reminders
	def load_sources(self):
		"""Open the other agendas, and the public copy of yours, from the settings."""
		folder = globalVars.appArgs.configPath
		found = []
		for info in agenda_infos():
			try:
				found.append((info, sources.open_agenda(info, os.path.join(folder, BACKUP_FOLDER), os.path.join(folder, FEED_FOLDER))))
			except Exception:
				log.error('AgendaAccess: cannot open the agenda %s' % info.get('name'), exc_info=True)
		_state['others'] = found
		sources.PRIVATE_TEXT[0] = _('Busy')
		store().on_saved = [self.write_public_copy] if settings()['publicCopy'] else []
		self.write_public_copy(store())
		self.fetch_feeds()

	def write_public_copy(self, saved):
		if settings()['publicCopy']:
			try:
				sources.write_public_copy(store(), settings()['publicCopy'])
			except Exception:
				log.error('AgendaAccess: cannot write the public copy', exc_info=True)

	def fetch_feeds(self):
		def done(source):
			wx.CallAfter(self.refresh_others)
		for info, source in _state['others']:
			if hasattr(source, 'fetch'):
				source.fetch(done)

	def refresh_others(self):
		changed = False
		for info, source in _state['others']:
			try:
				changed = source.changed_elsewhere() or changed
			except Exception:
				log.error('AgendaAccess: reading the agenda %s again failed' % info['name'], exc_info=True)
		if changed and _window:
			_window.refresh()

	def tick(self):
		try:
			if store().changed_elsewhere():
				self.write_public_copy(store())   # a change from another computer reaches the public copy too
				if _window:
					_window.refresh()
		except Exception:
			log.error('AgendaAccess: reading the agenda file again failed', exc_info=True)
		self.refresh_others()
		self.fetch_feeds()
		self.check_reminders()

	def announce_at_start(self):
		if store() is not None and agenda.day_appointments(shown_appointments(), today()):
			say(words.day_summary(shown_appointments(), today(), today()))

	def is_night(self, moment):
		if not settings()['nightMode']:
			return False
		start, end, hour = settings()['nightStart'], settings()['nightEnd'], moment.hour
		return start <= hour < end if start < end else hour >= start or hour < end

	def sound_of(self, appointment):
		"""The sound to play, or False for no sound."""
		chosen = appointment.get('sound')
		if chosen == '':
			return False
		if chosen:
			return chosen
		if not settings()['reminderSound']:
			return False
		return settings()['soundFile']

	def state_path(self):
		return os.path.join(globalVars.appArgs.configPath, STATE_FILE)

	def read_state(self):
		"""When the reminders were last looked at, before NVDA was closed; None the first time."""
		try:
			with open(self.state_path(), encoding='utf-8') as file:
				return datetime.datetime.fromisoformat(json.load(file)['lastCheck'])
		except Exception:
			return None

	def write_state(self):
		try:
			with open(self.state_path(), 'w', encoding='utf-8') as file:
				json.dump({'lastCheck': self.lastCheck.isoformat(timespec='seconds')}, file)
		except OSError:
			pass

	def give_missed(self):
		"""The reminders that fell while NVDA was closed, for the appointments that are still to come."""
		since, self.closedSince = self.closedSince, None
		if since is None or store() is None or paused_until():
			return
		moment = now()
		try:
			missed = agenda.missed_reminders(remindable_appointments(), since, self.startedAt, moment)
		except Exception:
			log.error('AgendaAccess: checking the missed reminders failed', exc_info=True)
			return
		if missed:
			# The minutes 1 gives "in 20 minutes" from now, not "now": the time is said as it is when given.
			self.deliver([words.reminder_text(when, minutes or 1, appointment, moment) for when, minutes, appointment in missed], missed[0][2], moment,
				phone_texts=[words.reminder_text(when, minutes or 1, appointment, moment, hide=True) for when, minutes, appointment in missed])

	def check_reminders(self):
		moment = now()
		try:
			due = agenda.due_reminders(remindable_appointments(), self.lastCheck, moment)
		except Exception:
			log.error('AgendaAccess: checking the reminders failed', exc_info=True)
			due = []
		self.lastCheck = moment
		self.write_state()
		if paused_until():
			self.repeating = None
			return
		again = [item for item in self.snoozed if item[0] <= moment]
		if again:
			self.snoozed = [item for item in self.snoozed if item[0] > moment]
			for when, texts, chosen, phone_texts in again:
				self.deliver(texts, None, moment, chosen, phone_texts)
		if due:
			self.deliver([words.reminder_text(when, minutes, appointment, moment) for when, minutes, appointment in due], due[0][2], moment,
				phone_texts=[words.reminder_text(when, minutes, appointment, moment, hide=True) for when, minutes, appointment in due])
		elif self.repeating and not again and moment >= self.repeating['next']:
			self.give(self.repeating['texts'], self.repeating['sound'])
			self.repeating['left'] -= 1
			self.repeating['next'] = moment + REPEAT_EVERY
			if self.repeating['left'] <= 0:
				self.repeating = None

	def deliver(self, texts, appointment, moment, chosen=None, phone_texts=None):
		"""Give reminders: said, with their sound, sent to the phone, shown in their window, repeated if chosen.
		chosen: the sound, when given again after Remind me again; otherwise the appointment's."""
		night = self.is_night(moment)
		chosen = False if night else (chosen if appointment is None else self.sound_of(appointment))
		self.windowSound = chosen
		self.give(texts, chosen)
		phone_texts = phone_texts or texts
		self.send_to_phone(phone_texts)
		if self.reminderWindow:
			self.windowPhoneTexts += phone_texts
		else:
			self.windowPhoneTexts = list(phone_texts)
		if settings()['reminderWindow']:
			self.show_reminders(texts)
		times = 1 if night else settings()['repeatTimes']
		self.repeating = {'texts': texts, 'sound': chosen, 'left': times - 1, 'next': moment + REPEAT_EVERY} if times > 1 else None

	def send_to_phone(self, texts):
		"""With ntfy, when turned on: in the background, so that a slow connection never holds NVDA."""
		if not settings()['phoneEnabled'] or not phone.valid_name(settings()['phoneName']):
			return
		def done(problem):
			if problem:
				log.warning('AgendaAccess: a reminder was not sent to the phone: %s' % problem)
		phone.send_later(settings()['phoneServer'], settings()['phoneName'], ADDON_NAME, ' '.join(texts), done)

	def give(self, texts, chosen):
		if chosen is not False:
			play(chosen)
		say(' '.join(texts))

	def snooze(self, texts, minutes):
		"""Give these reminders again after some minutes (the Remind me again button of their window)."""
		self.snoozed.append((now() + datetime.timedelta(minutes=minutes), list(texts), self.windowSound, list(self.windowPhoneTexts)))

	def reminders_closed(self):
		"""Closing the reminder window stops its repetition."""
		self.reminderWindow = None
		self.repeating = None
		sound.stop()

	def show_reminders(self, texts):
		if self.reminderWindow:
			try:
				self.reminderWindow.add(texts)
				return
			except RuntimeError:   # closed meanwhile
				pass
		gui.mainFrame.prePopup()
		try:
			self.reminderWindow = ReminderDialog(gui.mainFrame, texts, self.reminders_closed, self.snooze)
			self.reminderWindow.Show()
			self.reminderWindow.Raise()
		finally:
			gui.mainFrame.postPopup()

	def pause(self, minutes):
		if minutes:
			until = now() + datetime.timedelta(minutes=minutes)
			settings()['pausedUntil'] = until.isoformat(timespec='seconds')
			self.repeating = None
			sound.stop()
			# Translators: Said when the reminders are paused.
			say(_('No reminder until {time}.').format(time=words.time_text(until.time())))
		else:
			settings()['pausedUntil'] = ''
			# Translators: Said when the reminders start again.
			say(_('The reminders are back.'))

	# --- the windows
	def show_window(self):
		"""Open the agenda, or bring it back if it is already open."""
		global _window
		if store() is None:
			return None
		if _window:
			_window.Raise()
			_window.list.SetFocus()
			return _window
		gui.mainFrame.prePopup()
		try:
			_window = AgendaWindow(gui.mainFrame, self)
			_window.Show()
			_window.Raise()
		finally:
			gui.mainFrame.postPopup()
		if store().newer:
			# Translators: Said when the agenda file was written by a newer version of AgendaAccess.
			say(_('This agenda comes from a newer version of AgendaAccess: update AgendaAccess on this computer, or some details could be lost.'))
		return _window

	def choose_occurrence(self, parent, upcoming=True):
		"""The upcoming appointments, or the journal of the past ones. Returns (when, appointment) chosen with Enter."""
		moment = now()
		found = agenda.upcoming(shown_appointments(), moment) if upcoming else agenda.past(shown_appointments(), moment)
		lines = [words.upcoming_text(when, appointment, moment.date()) for when, appointment in found]
		if upcoming:
			empty = _('No appointment in the next {days} days.').format(days=agenda.UPCOMING_DAYS)
			# Translators: Title and label of the window of the upcoming appointments.
			title, label = _('Upcoming appointments'), _('&Upcoming appointments (Enter to show the day):')
		else:
			# Translators: The only line of the journal when there is no past appointment.
			empty = _('No past appointment this last year.')
			# Translators: Title and label of the journal of past appointments.
			title, label = _('Journal of past appointments'), _('&Past appointments, the most recent first (Enter to show the day):')
		dialog = ListDialog(parent, title, label, lines or [empty], on_enter=bool(found))
		try:
			if show_modal(dialog) == wx.ID_OK and found:
				return found[dialog.chosen]
		finally:
			dialog.Destroy()
		return None

	def in_window(self, action):
		window = self.show_window()
		if window:
			getattr(window, action)()

	def show_shortcuts(self, parent):
		# Translators: Title of the window to assign keys to the commands.
		dialog = ShortcutsDialog(parent, _('Keys of {name}').format(name=ADDON_NAME), commands(), self.shortcuts)
		try:
			show_modal(dialog)
		finally:
			dialog.Destroy()

	# --- the commands: only opening the agenda has a key; the others get one in the keys window
	def run_command(self, name):
		if store() is None:
			return
		windows = {'openAgenda': None, 'newAppointment': 'new', 'showUpcoming': 'show_upcoming', 'showAll': 'show_all',
			'showJournal': 'show_journal', 'pauseReminders': 'pause'}
		if name in windows:
			action = windows[name]
			wx.CallAfter(self.show_window if action is None else lambda: self.in_window(action))
		elif name == 'announceToday':
			say(words.day_summary(shown_appointments(), today(), today()))
		elif name == 'announceTomorrow':
			say(words.day_summary(shown_appointments(), today() + datetime.timedelta(days=1), today()))
		elif name == 'announceNext':
			say(words.next_text(shown_appointments(), now()))

	__gestures = {
		'kb:NVDA+control+shift+enter': 'openAgenda',
	}


def _make_script(name, description):
	def script(self, gesture):
		self.run_command(name)
	script.__doc__ = description
	return script


for _name, _description in commands():
	setattr(GlobalPlugin, 'script_' + _name, _make_script(_name, _description))
