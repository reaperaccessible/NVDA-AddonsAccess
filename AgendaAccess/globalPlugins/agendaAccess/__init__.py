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

from . import agenda, phone, sound, words
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
	'phoneEnabled': 'boolean(default=false)',
	'phoneName': 'string(default="")',
	'phoneServer': 'string(default="")',
	'checkForUpdates': 'boolean(default=true)',
}
STATE_FILE = 'agendaAccess-state.json'  # in NVDA's configuration folder: when the reminders were last looked at
DATA_FILE = 'agendaAccess.json'           # in NVDA's configuration folder, unless another place is chosen
BACKUP_FOLDER = 'agendaAccess-backups'    # always in NVDA's configuration folder: copies of this computer
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


_state = {'quietUntil': 0.0, 'ownSpeech': False, 'store': None}


def store():
	return _state['store']


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

	def __init__(self, parent, day, appointment=None):
		# Translators: Title of the window of an appointment.
		super().__init__(parent, title=_('Edit the appointment') if appointment else _('New appointment'))
		self.appointment = appointment
		self.result = None
		a = appointment or {}
		start = agenda.start_day(appointment) if appointment else day
		at = agenda.start_time(appointment) if appointment else datetime.time(9, 0)
		rule = agenda.repeat_of(appointment) if appointment else agenda.no_repeat()
		sizer = wx.BoxSizer(wx.VERTICAL)
		pad = wx.LEFT | wx.RIGHT

		# Translators: Label of the description of an appointment.
		sizer.Add(wx.StaticText(self, label=_('&What:')), 0, pad | wx.TOP, 10)
		self.text = wx.TextCtrl(self, value=a.get('text', ''), size=(460, -1))
		sizer.Add(self.text, 0, pad | wx.EXPAND, 10)
		# Translators: Label of the category of an appointment: type one, or choose one already used.
		sizer.Add(wx.StaticText(self, label=_('Cate&gory (optional):')), 0, pad | wx.TOP, 10)
		self.category = wx.ComboBox(self, value=a.get('category', ''), choices=agenda.categories(store().appointments))
		sizer.Add(self.category, 0, pad | wx.EXPAND, 10)
		self.date = DateFields(self, sizer, start, date_labels())
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
		# Translators: Label of the sound of the reminders of an appointment.
		sizer.Add(wx.StaticText(self, label=_('&Sound of its reminders:')), 0, pad | wx.TOP, 10)
		self.sound = SoundChoice(self, a.get('sound'), usual=True)
		sizer.Add(self.sound.choice, 0, pad | wx.EXPAND, 10)
		# Translators: Label of the note of an appointment: as long as needed.
		sizer.Add(wx.StaticText(self, label=_('No&te (optional):')), 0, pad | wx.TOP, 10)
		self.note = wx.TextCtrl(self, value=a.get('note', ''), size=(460, 70), style=wx.TE_MULTILINE)
		sizer.Add(self.note, 0, pad | wx.EXPAND, 10)
		sizer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 10)
		self.SetSizerAndFit(sizer)

		for control in (self.repeat, self.end):
			control.Bind(wx.EVT_CHOICE, lambda event: self.update_fields())
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
		model = agenda.latest_of_category(store().appointments, self.category.GetValue())
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
		reminders = [minutes for index, minutes in enumerate(agenda.REMINDERS) if self.reminders.IsChecked(index)]
		result = agenda.new_appointment(start, datetime.time(self.hour.GetValue(), self.minute.GetValue()),
			self.text.GetValue(), rule, until, count, reminders, self.note.GetValue(), self.category.GetValue(), self.sound.value())
		if self.appointment:
			result['id'] = self.appointment['id']
			# Days removed alone stay removed, unless the appointment no longer repeats.
			result['skip'] = list(self.appointment.get('skip', ())) if kind != 'none' else []
		self.result = result
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
		names = [_('All')] + agenda.categories(store().appointments)
		self.category.Set(names)
		self.category.SetSelection(names.index(chosen) if chosen in names else 0)

	def fill(self, select=None):
		found = list(store().appointments)
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

	def __init__(self, parent, texts, on_close):
		super().__init__(parent, title=ADDON_NAME)
		self.on_closed = on_close
		sizer = wx.BoxSizer(wx.VERTICAL)
		# Translators: Label of the reminders in their window.
		sizer.Add(wx.StaticText(self, label=_('&Reminders:')), 0, wx.ALL, 8)
		self.text = wx.TextCtrl(self, value='\n'.join(texts), size=(480, 120), style=wx.TE_MULTILINE | wx.TE_READONLY)
		sizer.Add(self.text, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 8)
		ok = wx.Button(self, wx.ID_OK)
		ok.SetDefault()
		sizer.Add(ok, 0, wx.ALL | wx.ALIGN_RIGHT, 8)
		self.SetSizerAndFit(sizer)
		self.SetEscapeId(wx.ID_OK)
		self.Bind(wx.EVT_BUTTON, lambda event: self.Close(), id=wx.ID_OK)
		self.Bind(wx.EVT_CLOSE, self.on_close)

	def add(self, texts):
		self.text.AppendText('\n' + '\n'.join(texts))

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
		self.shown = agenda.day_appointments(store().appointments, self.day)
		date = words.date_text(self.day)
		date = date[:1].upper() + date[1:]   # 'Mercredi 9 décembre 2026' at the start of the line
		# Translators: A line of the agenda window: the date, then the appointment.
		lines = [_('{date}, {appointment}').format(date=date, appointment=words.appointment_text(a)) for a in self.shown]
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
		say(words.day_heading(store().appointments, self.day, today()))
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
		finally:
			dialog.Destroy()
		store().add(appointment)
		self.after_change(appointment)
		# Translators: Said when an appointment has been added.
		say(_('Appointment added: {date}, {appointment}.').format(
			date=words.relative_date_text(agenda.start_day(appointment), today()),
			appointment=words.appointment_text(appointment, with_details=False)))

	def edit(self):
		appointment = self.selected()
		if appointment is None:
			self.new()
		else:
			self.edit_appointment(appointment)

	def edit_appointment(self, appointment, parent=None):
		dialog = AppointmentDialog(parent or self, self.day, appointment)
		try:
			if show_modal(dialog) != wx.ID_OK:
				return False
			changed = dialog.result
		finally:
			dialog.Destroy()
		store().replace(changed)
		if parent is None:
			self.after_change(changed)
		else:
			self.fill()
		# Translators: Said when an appointment has been changed.
		say(_('Appointment saved.'))
		return True

	def after_change(self, appointment):
		"""Show the day of the appointment, unless it still happens on the day shown."""
		if not agenda.occurs_on(appointment, self.day):
			self.day = agenda.next_occurrence(appointment, agenda.start_day(appointment)) or agenda.start_day(appointment)
		quiet()
		self.fill(select=appointment['id'])
		self.list.SetFocus()

	def delete(self):
		appointment = self.selected()
		if appointment is None:
			# Translators: Said when Delete is pressed on a day without appointments.
			say(_('No appointment to delete.'))
			return
		if self.delete_appointment(appointment, self.day):
			quiet()
			self.fill()
			self.list.SetFocus()
			# Translators: Said when an appointment has been deleted.
			say(_('Deleted. {heading}').format(heading=words.day_heading(store().appointments, self.day, today())))

	def delete_appointment(self, appointment, day, parent=None):
		"""Ask, then delete. day: the day it was chosen on, to delete only that day of a repetition."""
		parent = parent or self
		name = words.appointment_text(appointment, with_details=False)
		if not agenda.repeats(appointment) or day is None:
			# Translators: The question before deleting an appointment (every day of it, if it repeats).
			question = _('Delete {appointment}?') if not agenda.repeats(appointment) else _('Delete {appointment} and every day it repeats?')
			if gui.messageBox(question.format(appointment=name), ADDON_NAME, wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, parent) != wx.YES:
				return False
			store().remove(appointment['id'])
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
			store().skip_day(appointment['id'], day)
		elif answer == wx.ID_NO:
			store().remove(appointment['id'])
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
		store().clear_day(self.day)
		quiet()
		self.fill()
		self.list.SetFocus()
		say(_('Deleted. {heading}').format(heading=words.day_heading(store().appointments, self.day, today())))

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
		lines = words.report_lines(store().appointments, today(), days)
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
		appointments = store().appointments
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
		# Translators: Said when the agenda uses another file; an agenda already there was merged with this one.
		say(_('The agenda now uses this file. The appointments already in it were kept and joined to yours.') if existed
			else _('The agenda now uses this file.'))

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
	def tick(self):
		try:
			if store().changed_elsewhere() and _window:
				_window.refresh()
		except Exception:
			log.error('AgendaAccess: reading the agenda file again failed', exc_info=True)
		self.check_reminders()

	def announce_at_start(self):
		if store() is not None and agenda.day_appointments(store().appointments, today()):
			say(words.day_summary(store().appointments, today(), today()))

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
			missed = agenda.missed_reminders(store().appointments, since, self.startedAt, moment)
		except Exception:
			log.error('AgendaAccess: checking the missed reminders failed', exc_info=True)
			return
		if missed:
			# The minutes 1 gives "in 20 minutes" from now, not "now": the time is said as it is when given.
			self.deliver([words.reminder_text(when, minutes or 1, appointment, moment) for when, minutes, appointment in missed], missed[0][2], moment)

	def check_reminders(self):
		moment = now()
		try:
			due = agenda.due_reminders(store().appointments, self.lastCheck, moment)
		except Exception:
			log.error('AgendaAccess: checking the reminders failed', exc_info=True)
			due = []
		self.lastCheck = moment
		self.write_state()
		if paused_until():
			self.repeating = None
			return
		if due:
			self.deliver([words.reminder_text(when, minutes, appointment, moment) for when, minutes, appointment in due], due[0][2], moment)
		elif self.repeating and moment >= self.repeating['next']:
			self.give(self.repeating['texts'], self.repeating['sound'])
			self.repeating['left'] -= 1
			self.repeating['next'] = moment + REPEAT_EVERY
			if self.repeating['left'] <= 0:
				self.repeating = None

	def deliver(self, texts, appointment, moment):
		"""Give reminders: said, with their sound, sent to the phone, shown in their window, repeated if chosen."""
		night = self.is_night(moment)
		chosen = False if night else self.sound_of(appointment)
		self.give(texts, chosen)
		self.send_to_phone(texts)
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
			self.reminderWindow = ReminderDialog(gui.mainFrame, texts, self.reminders_closed)
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
		return _window

	def choose_occurrence(self, parent, upcoming=True):
		"""The upcoming appointments, or the journal of the past ones. Returns (when, appointment) chosen with Enter."""
		moment = now()
		found = agenda.upcoming(store().appointments, moment) if upcoming else agenda.past(store().appointments, moment)
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
			say(words.day_summary(store().appointments, today(), today()))
		elif name == 'announceTomorrow':
			say(words.day_summary(store().appointments, today() + datetime.timedelta(days=1), today()))
		elif name == 'announceNext':
			say(words.next_text(store().appointments, now()))

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
