# NVDA Add-on: SpeakWriteAccess
# Corrects the selected text, and writes what you say into the microphone, with an AI that runs on your computer.
# Copyright (C) 2026 ReaperAccessible
# This add-on is free software, licensed under the terms of the GNU General Public License (version 3).
# See the file LICENSE for more details.

import ctypes
import os
import queue
import threading

import wx

import addonHandler
import api
import config
import controlTypes
import core
import globalPluginHandler
import globalVars
import gui
import languageHandler
import textInfos
import ui
from keyboardHandler import KeyboardInputGesture
from logHandler import log
from scriptHandler import script

from . import engine, microphone, writing
from .updater import Updater

addonHandler.initTranslation()

CONFIG_SECTION = 'speakWriteAccess'
CONFIG_SPEC = {
	'language': 'string(default=nvda)',
	'correctDictation': 'boolean(default=true)',
	'readDictation': 'boolean(default=false)',
	'keep': 'option("15", "60", "always", default="15")',
	'checkForUpdates': 'boolean(default=true)',
}

# Keys that must be up before the add-on presses Control+V itself:
# Shift, Control, Alt, Insert, Caps Lock and both Windows keys.
MODIFIERS = (0x10, 0x11, 0x12, 0x2D, 0x14, 0x5B, 0x5C)
CLIPBOARD_RESTORE_MS = 800


def _setting(name):
	return config.conf[CONFIG_SECTION][name]


def _language():
	"""The language chosen in the settings, NVDA's own when it is 'nvda'."""
	code = _setting('language')
	if code == 'nvda':
		code = languageHandler.getLanguage().split('_')[0]
	return code if code in writing.ENGLISH_NAMES else 'en'


def _language_names():
	# Translators: the names of the languages SpeakWriteAccess can write and correct.
	return {'fr': _('French'), 'en': _('English'), 'es': _('Spanish'), 'pt': _('Portuguese'), 'it': _('Italian'),
		'de': _('German'), 'nl': _('Dutch'), 'pl': _('Polish'), 'ro': _('Romanian'), 'ru': _('Russian'),
		'uk': _('Ukrainian'), 'tr': _('Turkish'), 'ar': _('Arabic'), 'fa': _('Persian'), 'hi': _('Hindi'),
		'ja': _('Japanese'), 'zh': _('Chinese'), 'ko': _('Korean'), 'vi': _('Vietnamese'), 'id': _('Indonesian')}


_engine = engine.Engine()


def _manager_message():
	# Translators: shown when the AI of SpeakWriteAccess is not installed yet.
	return _('SpeakWriteAccess needs its AI, which is installed separately with the ReaperAccessible Installer Manager. '
		'In the Manager, choose the type NVDA Add-on, then SpeakWriteAccess, local AI. '
		'If you do not have the Manager on your computer, use the Download the Manager button.')


class ManagerDialog(wx.Dialog):
	"""Says how to install the AI, with a button that downloads the Manager."""

	def __init__(self, parent):
		# Translators: the title of the window that explains how to install the AI.
		super().__init__(parent, title=_('Install the AI'))
		sizer = wx.BoxSizer(wx.VERTICAL)
		text = wx.TextCtrl(self, value=_manager_message(), style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2, size=(500, 120))
		sizer.Add(text, 1, wx.EXPAND | wx.ALL, 10)
		buttons = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: a button that downloads the ReaperAccessible Installer Manager.
		download = wx.Button(self, label=_('&Download the Manager'))
		# Translators: the button that closes a window.
		close = wx.Button(self, wx.ID_CANCEL, label=_('&Close'))
		buttons.Add(download, 0, wx.RIGHT, 10)
		buttons.Add(close)
		sizer.Add(buttons, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
		download.Bind(wx.EVT_BUTTON, self.on_download)
		self.SetSizerAndFit(sizer)
		self.CentreOnScreen()
		text.SetFocus()

	def on_download(self, event):
		try:
			os.startfile(engine.MANAGER_URL)
		except OSError:
			log.exception('SpeakWriteAccess: could not open the Manager link')
		self.Close()


def _show_manager_dialog():
	gui.mainFrame.prePopup()
	dialog = ManagerDialog(gui.mainFrame)
	dialog.ShowModal()
	dialog.Destroy()
	gui.mainFrame.postPopup()


def _modifiers_down():
	return any(ctypes.windll.user32.GetAsyncKeyState(key) & 0x8000 for key in MODIFIERS)


def _paste(text, then=None, tries=40):
	"""Writes the text where the cursor is, through the clipboard, which gets its own text back afterwards.
	Waits for the user's keys to be up, so that Control+V is not changed by a Shift still held."""
	if _modifiers_down() and tries:
		core.callLater(50, _paste, text, then, tries - 1)
		return
	try:
		previous = api.getClipData()
	except Exception:
		previous = None
	if not api.copyToClip(text, notify=False):
		# Translators: said when the text could not be written.
		ui.message(_('The text could not be written.'))
		return
	KeyboardInputGesture.fromName('control+v').send()
	if isinstance(previous, str):
		core.callLater(CLIPBOARD_RESTORE_MS, api.copyToClip, previous, False)
	if then:
		then()


def _editable(obj):
	"""True when the text of the object can be changed (an edit field or a document being written)."""
	if obj is None:
		return False
	interceptor = obj.treeInterceptor
	if interceptor is not None and not interceptor.passThrough:
		return False
	return controlTypes.State.EDITABLE in obj.states or obj.role in (controlTypes.Role.EDITABLETEXT, controlTypes.Role.DOCUMENT)


def _selection():
	"""The selected text, and whether it can be replaced."""
	obj = api.getFocusObject()
	source = obj
	interceptor = obj.treeInterceptor
	if interceptor is not None and not interceptor.passThrough:
		source = interceptor
	try:
		info = source.makeTextInfo(textInfos.POSITION_SELECTION)
		text = '' if info.isCollapsed else info.text
	except Exception:
		text = ''
	return text, _editable(obj)


def _changes_message(count):
	if count == 0:
		# Translators: said when the selected text has no mistake.
		return _('No mistake.')
	if count == 1:
		# Translators: said after the selected text was corrected in one place.
		return _('1 correction.')
	# Translators: said after the selected text was corrected; {count} is the number of corrections.
	return _('{count} corrections.').format(count=count)


class Dictation:
	"""The microphone, cut into phrases; each phrase is written as soon as the AI has heard it."""

	def __init__(self):
		self.active = False
		self.microphone = None
		self.cutter = None
		self.phrases = queue.Queue()
		self.worker = None
		self.written = False

	def start(self):
		self.cutter = microphone.Phrases()
		self.microphone = microphone.Microphone(self._on_frame)
		self.microphone.start()
		self.active = True
		self.written = False
		self.worker = threading.Thread(target=self._work, daemon=True)
		self.worker.start()
		self.phrases.put(('warm', None))

	def stop(self):
		if not self.active:
			return
		self.active = False
		self.microphone.stop()
		last = self.cutter.finish()
		if last:
			self.phrases.put(('speech', last))
		self.phrases.put(('end', None))

	def _on_frame(self, frame):
		phrase = self.cutter.feed(frame)
		if phrase:
			self.phrases.put(('speech', phrase))

	def _work(self):
		while True:
			kind, pcm = self.phrases.get()
			if kind == 'end':
				return
			try:
				if kind == 'warm':
					# The first sound the AI hears takes it a few seconds: it hears a short silence first.
					_engine.ensure_running()
					writing.transcribe(_engine, microphone.to_wav(bytes(microphone.RATE)), _language(), False)
					continue
				text = writing.transcribe(_engine, microphone.to_wav(pcm), _language(), _setting('correctDictation'))
			except engine.EngineError:
				log.exception('SpeakWriteAccess: dictation')
				# Translators: said when the AI could not be used.
				wx.CallAfter(ui.message, _('The AI could not start.'))
				continue
			if text:
				wx.CallAfter(self._write, text)

	def _write(self, text):
		if self.written and not text[:1] in '.,;:!?)' and not text[:1].isspace():
			text = ' ' + text
		self.written = True
		_paste(text, then=(lambda: ui.message(text.strip())) if _setting('readDictation') else None)


_dictation = Dictation()
_correcting = threading.Event()


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	# The add-on's own category in NVDA's Input Gestures dialog, never translated.
	scriptCategory = 'SpeakWriteAccess'

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		config.conf.spec[CONFIG_SECTION] = CONFIG_SPEC
		self.updater = None
		if globalVars.appArgs.secure:
			return
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(SettingsPanel)
		_engine.set_keep(_setting('keep'))
		if _setting('keep') == 'always' and engine.installed():
			_engine.start_in_background()
		self.updater = Updater(addonHandler.getCodeAddon())
		if _setting('checkForUpdates'):
			self.updater.scheduleStartupCheck()
		SettingsPanel.updater = self.updater

	def terminate(self, *args, **kwargs):
		super().terminate(*args, **kwargs)
		_dictation.stop()
		_engine.stop()
		if self.updater is not None:
			self.updater.cancel()
		if SettingsPanel in gui.settingsDialogs.NVDASettingsDialog.categoryClasses:
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(SettingsPanel)

	@script(
		# Translators: the description of the command that corrects the selected text, in Input Gestures.
		description=_('Corrects the selected text.'),
		gesture='kb:NVDA+control+shift+c')
	def script_correctSelection(self, gesture):
		if globalVars.appArgs.secure:
			return
		if not engine.installed():
			wx.CallAfter(_show_manager_dialog)
			return
		if _correcting.is_set():
			# Translators: said when a correction is asked while another one is not finished.
			ui.message(_('A correction is already in progress.'))
			return
		text, editable = _selection()
		if not text.strip():
			# Translators: said when there is no selected text to correct.
			ui.message(_('No selection.'))
			return
		if not _engine.running():
			# Translators: said when the AI must be loaded before it can correct, which takes a few seconds.
			ui.message(_('Loading the AI...'))
		_correcting.set()
		threading.Thread(target=self._correct, args=(text, editable), daemon=True).start()

	def _correct(self, text, editable):
		try:
			result, count = writing.correct(_engine, text, _language())
		except writing.Unsure:
			log.info('SpeakWriteAccess: correction refused', exc_info=True)
			# Translators: said when the AI's answer did not look like a correction, so nothing was changed.
			wx.CallAfter(ui.message, _('The text was not changed: the correction did not look right.'))
			return
		except engine.EngineError:
			log.exception('SpeakWriteAccess: correction')
			wx.CallAfter(ui.message, _('The AI could not start.'))
			return
		finally:
			_correcting.clear()
		wx.CallAfter(self._apply, text, result, count, editable)

	def _apply(self, text, result, count, editable):
		message = _changes_message(count)
		if count == 0:
			ui.message(message)
		elif editable:
			_paste(result, then=lambda: ui.message(message))
		elif api.copyToClip(result, notify=False):
			# Translators: said when the corrected text could not replace the selection and was copied instead.
			ui.message(message + ' ' + _('Copied to the clipboard.'))

	@script(
		# Translators: the description of the command that starts or stops the dictation, in Input Gestures.
		description=_('Starts or stops the dictation: what you say into the microphone is written where the cursor is.'),
		gesture='kb:NVDA+control+shift+l')
	def script_toggleDictation(self, gesture):
		if globalVars.appArgs.secure:
			return
		if _dictation.active:
			_dictation.stop()
			# Translators: said when the dictation stops.
			ui.message(_('Dictation off'))
			return
		if not engine.installed():
			wx.CallAfter(_show_manager_dialog)
			return
		try:
			_dictation.start()
		except microphone.MicrophoneError:
			# Translators: said when no microphone can be used for the dictation.
			ui.message(_('No microphone found.'))
			return
		# Translators: said when the dictation starts.
		ui.message(_('Dictation on'))

	@script(
		# Translators: the description of the command that opens the settings of SpeakWriteAccess, in Input Gestures.
		description=_('Opens the settings of SpeakWriteAccess.'),
		gesture='kb:NVDA+control+l')
	def script_openSettings(self, gesture):
		if globalVars.appArgs.secure:
			return
		wx.CallAfter(gui.mainFrame.popupSettingsDialog, gui.settingsDialogs.NVDASettingsDialog, SettingsPanel)


class SettingsPanel(gui.settingsDialogs.SettingsPanel):
	# The add-on's own name: shown as a category of NVDA's Settings dialog, never translated.
	title = 'SpeakWriteAccess'
	# Set by the global plugin; None on secure screens.
	updater = None
	KEEP = ('15', '60', 'always')

	def makeSettings(self, settingsSizer):
		helper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		names = _language_names()
		self.codes = ['nvda'] + [code for code, _english in writing.LANGUAGES]
		# Translators: the first choice of the language list: the language NVDA speaks.
		choices = [_("NVDA's language")] + [names[code] for code in self.codes[1:]]
		# Translators: the label of the list of languages in the settings.
		self.language = helper.addLabeledControl(_('&Language of the text:'), wx.Choice, choices=choices)
		current = _setting('language')
		self.language.SetSelection(self.codes.index(current) if current in self.codes else 0)

		# Translators: a setting: each phrase of the dictation is written with correct spelling and punctuation.
		self.correctDictation = helper.addItem(wx.CheckBox(self, label=_('Co&rrect the dictation as you speak')))
		self.correctDictation.SetValue(_setting('correctDictation'))
		# Translators: a setting: NVDA reads each phrase the dictation writes.
		self.readDictation = helper.addItem(wx.CheckBox(self, label=_('&Read aloud each phrase the dictation writes')))
		self.readDictation.SetValue(_setting('readDictation'))

		# Translators: the choices of how long the AI stays ready after it was last used.
		keepChoices = [_('15 minutes'), _('1 hour'), _('Always, from the start of NVDA')]
		# Translators: the label of the setting that keeps the AI ready, so that it answers at once.
		self.keep = helper.addLabeledControl(_('&Keep the AI ready after its last use:'), wx.Choice, choices=keepChoices)
		self.keep.SetSelection(self.KEEP.index(_setting('keep')))

		if not engine.installed():
			# Translators: a button shown when the AI is not installed; it explains how to install it.
			install = helper.addItem(wx.Button(self, label=_('How to &install the AI...')))
			install.Bind(wx.EVT_BUTTON, lambda event: _show_manager_dialog())

		# Translators: the label for the preference to look for a new version of the add-on each time NVDA starts
		self.checkForUpdates = helper.addItem(wx.CheckBox(self, label=_('Check for &updates automatically when NVDA starts')))
		self.checkForUpdates.SetValue(_setting('checkForUpdates'))
		# Translators: The label of a button in the settings panel that looks for a new version of the add-on right away.
		checkNow = helper.addItem(wx.Button(self, label=_('&Check for updates now')))
		checkNow.Bind(wx.EVT_BUTTON, lambda event: self.updater.check(manual=True))
		checkNow.Enable(self.updater is not None)

	def onSave(self):
		section = config.conf[CONFIG_SECTION]
		section['language'] = self.codes[self.language.GetSelection()]
		section['correctDictation'] = self.correctDictation.GetValue()
		section['readDictation'] = self.readDictation.GetValue()
		keep = self.KEEP[self.keep.GetSelection()]
		section['keep'] = keep
		section['checkForUpdates'] = self.checkForUpdates.GetValue()
		_engine.set_keep(keep)
		if keep == 'always' and engine.installed():
			_engine.start_in_background()
