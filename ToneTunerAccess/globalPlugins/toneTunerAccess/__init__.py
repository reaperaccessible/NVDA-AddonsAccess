# NVDA Add-on: ToneTunerAccess, by ReaperAccessible.
# Based on Reference Tone Tuner, Copyright (C) Leandro Souza <https://github.com/leandro-sds/Reference-Tone-Tuner>.
# This add-on is free software, licensed under the terms of the GNU General Public License (version 2).
# See the file LICENSE for more details.

import winsound

import wx

import addonHandler
import config
import globalPluginHandler
import globalVars
import gui
import ui
from logHandler import log

from . import tuner
from .updater import Updater

addonHandler.initTranslation()

# The add-on's own name: never translated.
ADDON_NAME = 'ToneTunerAccess'
CONFIG_SECTION = 'toneTunerAccess'
CONFIG_SPEC = {
	'announce': 'boolean(default=true)',
	'loopAtStart': 'boolean(default=false)',
	'loopPause': f'integer(default={tuner.LOOP_DEFAULT_MS}, min={tuner.LOOP_MIN_MS}, max={tuner.LOOP_MAX_MS})',
	'volume': f'integer(default={tuner.VOLUME_DEFAULT}, min={tuner.VOLUME_MIN}, max={tuner.VOLUME_MAX})',
	'checkForUpdates': 'boolean(default=true)',
}


def settings():
	return config.conf[CONFIG_SECTION]


def string_names():
	"""What is said when a string is played: short, so as not to cover the sound."""
	return [
		# Translators: The 1st string of a guitar, in standard tuning.
		_('high E'),
		# Translators: The 2nd string of a guitar.
		_('B'),
		# Translators: The 3rd string of a guitar.
		_('G'),
		# Translators: The 4th string of a guitar.
		_('D'),
		# Translators: The 5th string of a guitar.
		_('A'),
		# Translators: The 6th string of a guitar, in standard tuning.
		_('low E'),
	]


def item_name(item):
	if isinstance(item, int):
		return string_names()[item]
	# Translators: Said when the G major chord is played.
	return _('G major chord') if item == tuner.CHORD_G_MAJOR else _('E major chord')


def seconds_text(milliseconds):
	# Translators: The decimal separator, as in 2.25 seconds. French uses a comma.
	value = tuner.seconds_value(milliseconds, decimal=_('.'))
	if milliseconds == 1000:
		# Translators: A pause of exactly one second.
		return _('1 second')
	# Translators: A pause in seconds. {seconds} is a number such as 2 or 2.25.
	return _('{seconds} seconds').format(seconds=value)


def play_file(path):
	try:
		winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
		return True
	except Exception:
		log.error('ToneTunerAccess: cannot play %s' % path, exc_info=True)
		return False


def stop_sound():
	try:
		winsound.PlaySound(None, winsound.SND_PURGE)
	except Exception:
		pass


def help_lines():
	return [
		# Translators: A line of the help of the tuner window. The key comes first.
		_('1 to 6: play a string, from 1 for the high E to 6 for the low E'),
		_('T: play all the strings, from the 6th to the 1st'),
		_('S: play the G major chord'),
		_('M: play the E major chord'),
		_('R: repeat in a loop, or stop the loop'),
		_('A: lengthen the pause of the loop'),
		_('D: shorten the pause of the loop'),
		_('Up and down arrows: raise or lower the volume'),
		_('F1: this help'),
		_('Escape: stop the sound, or close the window when nothing is playing'),
	]


class HelpDialog(wx.Dialog):
	def __init__(self, parent):
		# Translators: Title of the help window of the tuner.
		super().__init__(parent, title=_('ToneTunerAccess help'))
		sizer = wx.BoxSizer(wx.VERTICAL)
		# Translators: Label of the list of keys in the help window.
		sizer.Add(wx.StaticText(self, label=_('&Keys of the tuner window:')), 0, wx.ALL, 8)
		self.list = wx.ListBox(self, choices=help_lines(), size=(480, 200))
		self.list.SetSelection(0)
		sizer.Add(self.list, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 8)
		close = wx.Button(self, wx.ID_CANCEL, label=_('&Close'))
		sizer.Add(close, 0, wx.ALL | wx.ALIGN_RIGHT, 8)
		self.SetSizerAndFit(sizer)
		self.list.SetFocus()


class TunerDialog(wx.Dialog):
	"""The tuner. Every key works wherever the focus is in the window."""

	def __init__(self, parent):
		super().__init__(parent, title=ADDON_NAME)
		self.player = tuner.Player(play_file, stop_sound, lambda ms, callback: wx.CallLater(ms, callback))
		self.player.loop = settings()['loopAtStart']
		self.player.loop_ms = tuner.clamp_loop(settings()['loopPause'])
		self.player.volume = tuner.clamp_volume(settings()['volume'])
		self.player.on_item = self.announce_item
		sizer = wx.BoxSizer(wx.VERTICAL)
		# Translators: The first line of the tuner window.
		sizer.Add(wx.StaticText(self, label=_('Press 1 to 6 to play a string, or F1 for the list of keys.')), 0, wx.ALL, 10)
		# Translators: A check box of the tuner window.
		self.loop = wx.CheckBox(self, label=_('&Repeat in a loop'))
		self.loop.SetValue(self.player.loop)
		sizer.Add(self.loop, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
		# Translators: Label of the volume control of the tuner window.
		sizer.Add(wx.StaticText(self, label=_('&Volume:')), 0, wx.LEFT | wx.RIGHT, 10)
		self.volume = wx.Slider(self, value=self.player.volume, minValue=tuner.VOLUME_MIN, maxValue=tuner.VOLUME_MAX,
			name=_('Volume'))
		self.volume.SetLineSize(tuner.VOLUME_STEP)
		self.volume.SetPageSize(tuner.VOLUME_STEP)
		sizer.Add(self.volume, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
		buttons = wx.BoxSizer(wx.HORIZONTAL)
		help_button = wx.Button(self, label=_('&Help (F1)'))
		close = wx.Button(self, wx.ID_CLOSE, label=_('&Close'))
		buttons.Add(help_button, 0, wx.RIGHT, 10)
		buttons.Add(close, 0)
		sizer.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
		self.SetSizerAndFit(sizer)
		self.loop.Bind(wx.EVT_CHECKBOX, lambda event: self.toggle_loop(from_box=True))
		self.volume.Bind(wx.EVT_SLIDER, lambda event: self.set_volume(self.volume.GetValue(), announce=False))
		help_button.Bind(wx.EVT_BUTTON, lambda event: self.show_help())
		close.Bind(wx.EVT_BUTTON, lambda event: self.Close())
		self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
		self.Bind(wx.EVT_CLOSE, self.on_close)
		self.loop.SetFocus()

	def announce_item(self, item):
		if settings()['announce']:
			ui.message(item_name(item))

	def toggle_loop(self, from_box=False):
		if not from_box:
			self.loop.SetValue(not self.loop.GetValue())
		enabled = self.loop.GetValue()
		self.player.set_loop(enabled)
		# Translators: Said when the loop is turned on or off in the tuner window.
		ui.message(_('Loop on') if enabled else _('Loop off'))

	def change_pause(self, step):
		before = self.player.loop_ms
		self.player.loop_ms = tuner.clamp_loop(before + step)
		settings()['loopPause'] = self.player.loop_ms   # remembered for the next time
		text = seconds_text(self.player.loop_ms)
		if self.player.loop_ms == before:
			# Translators: Said when the pause of the loop cannot go further.
			message = _('Pause of the loop already at its maximum: {pause}') if step > 0 else _('Pause of the loop already at its minimum: {pause}')
		else:
			# Translators: Said when the pause of the loop changes.
			message = _('Pause of the loop: {pause}')
		ui.message(message.format(pause=text))

	def set_volume(self, percent, announce=True):
		before = self.player.volume
		self.player.volume = tuner.clamp_volume(percent)
		settings()['volume'] = self.player.volume   # remembered for the next time
		if self.volume.GetValue() != self.player.volume:
			self.volume.SetValue(self.player.volume)
		if not announce:
			return
		if self.player.volume == before:
			# Translators: Said when the volume cannot go further.
			message = _('Volume already at its maximum: {percent}%') if percent > before else _('Volume already at its minimum: {percent}%')
		else:
			# Translators: Said when the volume changes in the tuner window.
			message = _('Volume: {percent}%')
		ui.message(message.format(percent=self.player.volume))

	def show_help(self):
		dialog = HelpDialog(self)
		try:
			dialog.ShowModal()
		finally:
			dialog.Destroy()

	def on_key(self, event):
		key = event.GetKeyCode()
		if event.HasAnyModifiers() and not (key == wx.WXK_F4 and event.AltDown()):
			event.Skip()
			return
		if key == wx.WXK_ESCAPE:
			if self.player.playing:
				self.player.stop()
				# Translators: Said when Escape stops the sound in the tuner window.
				ui.message(_('Stopped'))
			else:
				self.Close()
			return
		actions = {
			wx.WXK_F1: self.show_help,
			ord('R'): self.toggle_loop,
			ord('A'): lambda: self.change_pause(tuner.LOOP_STEP_MS),
			ord('D'): lambda: self.change_pause(-tuner.LOOP_STEP_MS),
			ord('T'): lambda: self.player.start(tuner.ALL_STRINGS),
			ord('S'): lambda: self.player.start([tuner.CHORD_G_MAJOR]),
			ord('M'): lambda: self.player.start([tuner.CHORD_E_MAJOR]),
		}
		for number in range(6):
			actions[ord('1') + number] = lambda number=number: self.player.start([number])
			actions[wx.WXK_NUMPAD1 + number] = lambda number=number: self.player.start([number])
		if key in (wx.WXK_UP, wx.WXK_DOWN, wx.WXK_NUMPAD_UP, wx.WXK_NUMPAD_DOWN):
			if self.FindFocus() is self.volume:
				event.Skip()   # the control moves itself, and NVDA reads its new value
			else:
				step = tuner.VOLUME_STEP if key in (wx.WXK_UP, wx.WXK_NUMPAD_UP) else -tuner.VOLUME_STEP
				self.set_volume(self.player.volume + step)
			return
		if self.FindFocus() is self.volume and key in (wx.WXK_LEFT, wx.WXK_RIGHT, wx.WXK_PAGEUP, wx.WXK_PAGEDOWN, wx.WXK_HOME, wx.WXK_END):
			event.Skip()
			return
		action = actions.get(key)
		if action is not None:
			action()
			return
		# Moving between the controls and pressing them stays possible; other keys are ignored.
		if key in (wx.WXK_TAB, wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER, wx.WXK_SPACE, wx.WXK_F4):
			event.Skip()

	def on_close(self, event):
		self.player.stop()
		global _window
		_window = None
		self.Destroy()


_window = None


def open_tuner():
	"""Open the tuner, or bring it back if it is already open."""
	global _window
	if _window:
		_window.Raise()
		_window.loop.SetFocus()
		return
	gui.mainFrame.prePopup()
	try:
		_window = TunerDialog(gui.mainFrame)
		_window.Show()
		_window.Raise()
	finally:
		gui.mainFrame.postPopup()


class ToneTunerAccessSettingsPanel(gui.settingsDialogs.SettingsPanel):
	# The add-on's own name: shown as a category of NVDA's Settings dialog, never translated.
	title = ADDON_NAME
	# Set by the global plugin; None on secure screens.
	updater = None

	def makeSettings(self, settingsSizer):
		helper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		# Translators: A setting: say the name of the string or chord being played.
		self.announce = helper.addItem(wx.CheckBox(self, label=_('&Announce the name of the string played')))
		self.announce.SetValue(settings()['announce'])
		# Translators: A setting: the loop is on when the tuner window opens.
		self.loopAtStart = helper.addItem(wx.CheckBox(self, label=_('Turn the &loop on when the tuner opens')))
		self.loopAtStart.SetValue(settings()['loopAtStart'])
		self.pauses = tuner.loop_choices()
		# Translators: A setting: the time between two repetitions of the loop.
		self.pause = helper.addLabeledControl(_('&Pause between two repetitions of the loop:'), wx.Choice,
			choices=[seconds_text(value) for value in self.pauses])
		self.pause.SetSelection(self.pauses.index(tuner.clamp_loop(settings()['loopPause'])))
		self.volumes = tuner.volume_choices()
		# Translators: A setting: the volume of the reference sounds.
		self.volume = helper.addLabeledControl(_('&Volume of the sounds:'), wx.Choice,
			# Translators: A volume in the settings, such as 70%.
			choices=[_('{percent}%').format(percent=value) for value in self.volumes])
		self.volume.SetSelection(self.volumes.index(tuner.clamp_volume(settings()['volume'])))
		self.checkForUpdates = helper.addItem(wx.CheckBox(self, label=_('Check for &updates automatically when NVDA starts')))
		self.checkForUpdates.SetValue(settings()['checkForUpdates'])
		self.checkNow = helper.addItem(wx.Button(self, label=_('Check for updates &now')))
		self.checkNow.Bind(wx.EVT_BUTTON, lambda event: self.updater.check(manual=True))
		self.checkNow.Enable(self.updater is not None)

	def onSave(self):
		settings()['announce'] = self.announce.GetValue()
		settings()['loopAtStart'] = self.loopAtStart.GetValue()
		settings()['loopPause'] = self.pauses[self.pause.GetSelection()]
		settings()['volume'] = self.volumes[self.volume.GetSelection()]
		settings()['checkForUpdates'] = self.checkForUpdates.GetValue()


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	# The add-on's own category in NVDA's Input Gestures dialog, never translated.
	scriptCategory = ADDON_NAME

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		config.conf.spec[CONFIG_SECTION] = CONFIG_SPEC
		self.menuItem = None
		self.updater = None
		if globalVars.appArgs.secure:
			return
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(ToneTunerAccessSettingsPanel)
		try:
			tools = gui.mainFrame.sysTrayIcon.toolsMenu
			self.menuItem = tools.Append(wx.ID_ANY, ADDON_NAME + '…')
			gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, lambda event: open_tuner(), self.menuItem)
		except Exception:
			log.error('ToneTunerAccess: cannot add the Tools menu entry', exc_info=True)
		self.updater = Updater(addonHandler.getCodeAddon())
		ToneTunerAccessSettingsPanel.updater = self.updater
		if settings()['checkForUpdates']:
			self.updater.scheduleStartupCheck()

	def script_openTuner(self, gesture):
		# Opening a window from inside a script is done once the script has returned.
		wx.CallAfter(open_tuner)
	# Translators: Description of the command opening the tuner, in NVDA's Input Gestures dialog.
	script_openTuner.__doc__ = _('Opens the tuner.')

	def terminate(self, *args, **kwargs):
		if self.updater is not None:
			self.updater.cancel()
		if _window:
			_window.Close()
		try:
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(ToneTunerAccessSettingsPanel)
		except ValueError:
			pass
		if self.menuItem is not None:
			try:
				gui.mainFrame.sysTrayIcon.toolsMenu.Remove(self.menuItem)
			except Exception:
				pass
		super().terminate(*args, **kwargs)

	__gestures = {
		'kb:NVDA+control+shift+a': 'openTuner',
	}
