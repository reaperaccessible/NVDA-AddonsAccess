# NVDA Add-on: ResourceMonitorAccess, by ReaperAccessible.
# Based on Resource Monitor, Copyright 2013-2026 Alex Hall, Joseph Lee, Kefas Lungu, Beqa Gozalishvili,
# Tuukka Ojala, Ethin Probst, Kevin Derome and other NVDA contributors <https://github.com/kefaslungu/resourceMonitor>.
# This add-on is free software, licensed under the terms of the GNU General Public License (version 2).
# It uses psutil, included in NVDA, licensed under the 3-Clause BSD License.

import os
import threading
import winsound

import wx

import addonHandler
import api
import config
import globalPluginHandler
import globalVars
import gui
import queueHandler
import scriptHandler
import ui
from logHandler import log

from . import info
from .gpu import getGpuProviders
from .updater import Updater
from .wifi import WifiMonitor

addonHandler.initTranslation()

# The add-on's own name: never translated.
ADDON_NAME = 'ResourceMonitorAccess'
CONFIG_SECTION = 'resourceMonitorAccess'
CONFIG_SPEC = {
	'wifiNotification': "option('off', 'message', 'sound', 'both', default='both')",
	'temperatureUnit': "option('celsius', 'fahrenheit', default='celsius')",
	'checkForUpdates': 'boolean(default=true)',
}
SOUNDS = os.path.join(os.path.dirname(__file__), 'sounds')


def settings():
	return config.conf[CONFIG_SECTION]


# --- the last information said, kept for the copy
_state = {'last': ''}


def say(text, remember=True):
	if remember:
		_state['last'] = text
	ui.message(text)


def _notify_wifi(text, sound):
	"""From Windows' Wi-Fi thread: a connection or a disconnection."""
	mode = settings()['wifiNotification']
	if mode in ('message', 'both'):
		queueHandler.queueFunction(queueHandler.eventQueue, ui.message, text)
	if mode in ('sound', 'both'):
		try:
			winsound.PlaySound(os.path.join(SOUNDS, sound), winsound.SND_FILENAME | winsound.SND_ASYNC)
		except Exception:
			pass


class ResourceMonitorAccessSettingsPanel(gui.settingsDialogs.SettingsPanel):
	# The add-on's own name: shown as a category of NVDA's Settings dialog, never translated.
	title = ADDON_NAME
	updater = None

	def makeSettings(self, settingsSizer):
		helper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		self.wifiValues = ['off', 'message', 'sound', 'both']
		# Translators: A setting: how a Wi-Fi connection or disconnection is reported.
		self.wifi = helper.addLabeledControl(_('&Wi-Fi connections and disconnections:'), wx.Choice, choices=[
			# Translators: A choice of the Wi-Fi notification setting.
			_('Say nothing'), _('Announce them'), _('Play a sound'), _('Announce them and play a sound')])
		self.wifi.SetSelection(self.wifiValues.index(settings()['wifiNotification']))
		self.unitValues = ['celsius', 'fahrenheit']
		# Translators: A setting: the unit of the graphics card temperature.
		self.unit = helper.addLabeledControl(_('&Temperature of the graphics card in:'), wx.Choice, choices=[
			_('degrees Celsius'), _('degrees Fahrenheit')])
		self.unit.SetSelection(self.unitValues.index(settings()['temperatureUnit']))
		self.checkForUpdates = helper.addItem(wx.CheckBox(self, label=_('Check for &updates automatically when NVDA starts')))
		self.checkForUpdates.SetValue(settings()['checkForUpdates'])
		self.checkNow = helper.addItem(wx.Button(self, label=_('Check for updates &now')))
		self.checkNow.Bind(wx.EVT_BUTTON, lambda event: self.updater.check(manual=True))
		self.checkNow.Enable(self.updater is not None)

	def onSave(self):
		settings()['wifiNotification'] = self.wifiValues[self.wifi.GetSelection()]
		settings()['temperatureUnit'] = self.unitValues[self.unit.GetSelection()]
		settings()['checkForUpdates'] = self.checkForUpdates.GetValue()


def _open_settings():
	wx.CallAfter(gui.mainFrame.popupSettingsDialog, gui.settingsDialogs.NVDASettingsDialog, ResourceMonitorAccessSettingsPanel)


class ResourceWindow(wx.Dialog):
	"""The list of the information: Enter says the selected one, and the window stays open."""

	def __init__(self, parent, plugin):
		super().__init__(parent, title=ADDON_NAME)
		self.plugin = plugin
		# (text of the entry, what Enter does)
		self.entries = [(label, lambda gather=gather: plugin.report(gather)) for name, label, gather in plugin.topics()]
		# Translators: An entry of the resource window.
		self.entries.append((_('Copy the last information'), plugin.copy_last))
		if not globalVars.appArgs.secure:
			# Translators: An entry of the resource window: opens the add-on's settings.
			self.entries.append((_('Configuration…'), self.open_settings))
		sizer = wx.BoxSizer(wx.VERTICAL)
		# Translators: Label of the list of the resource window.
		sizer.Add(wx.StaticText(self, label=_('&Information (Enter to hear it):')), 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
		self.list = wx.ListBox(self, choices=[label for label, action in self.entries], style=wx.LB_SINGLE)
		self.list.SetSelection(0)
		sizer.Add(self.list, 1, wx.ALL | wx.EXPAND, 10)
		# Translators: The button closing the resource window.
		close = wx.Button(self, wx.ID_CLOSE, label=_('&Close'))
		sizer.Add(close, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
		self.SetSizerAndFit(sizer)
		close.Bind(wx.EVT_BUTTON, lambda event: self.Close())
		self.list.Bind(wx.EVT_LISTBOX_DCLICK, lambda event: self.activate())
		self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
		self.Bind(wx.EVT_CLOSE, self.on_close)
		self.list.SetFocus()

	def activate(self):
		index = self.list.GetSelection()
		if index != wx.NOT_FOUND:
			self.entries[index][1]()

	def open_settings(self):
		self.Close()
		_open_settings()

	def on_key(self, event):
		key = event.GetKeyCode()
		if key == wx.WXK_ESCAPE and not event.HasAnyModifiers():
			self.Close()
		elif key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and self.FindFocus() is self.list and not event.HasAnyModifiers():
			self.activate()
		elif key == ord('C') and event.GetModifiers() == wx.MOD_CONTROL:
			self.plugin.copy_last()
		else:
			event.Skip()

	def on_close(self, event):
		global _window
		_window = None
		self.Destroy()


_window = None


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	# The add-on's own category in NVDA's Input Gestures dialog, never translated.
	scriptCategory = ADDON_NAME

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		config.conf.spec[CONFIG_SECTION] = CONFIG_SPEC
		self.updater = None
		self.gpuProviders = getGpuProviders()
		self.hasBattery = info.has_battery()
		self.wifi = WifiMonitor(_notify_wifi)
		if globalVars.appArgs.secure:
			return
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(ResourceMonitorAccessSettingsPanel)
		self.updater = Updater(addonHandler.getCodeAddon())
		ResourceMonitorAccessSettingsPanel.updater = self.updater
		if settings()['checkForUpdates']:
			self.updater.scheduleStartupCheck()

	def terminate(self, *args, **kwargs):
		if self.updater is not None:
			self.updater.cancel()
		self.wifi.close()
		if _window:
			_window.Close()
		try:
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(ResourceMonitorAccessSettingsPanel)
		except ValueError:
			pass
		super().terminate(*args, **kwargs)

	# --- the information, each gathered off NVDA's thread: a network drive can take a while to answer
	def topics(self, everything=False):
		"""(name of the command, text of the entry in the window, function giving the sentence).
		The battery is only listed in the window on a computer that has one."""
		topics = [
			# Translators: An entry of the resource window.
			('announceSummary', _('Summary'), info.summary),
			('announceProcessor', _('Processor'), info.processor),
			('announceApplications', _('Applications using the most'), info.applications),
			('announceMemory', _('Memory'), info.memory),
			('announceDrives', _('Drives'), info.drives),
			('announceNetwork', _('Network connection'), info.network),
			('announceSpeedTest', _('Internet speed test'), self.speed_test),
			('announceWifi', _('Wi-Fi'), self.wifi.info),
			('announceBattery', _('Battery'), info.battery),
			('announceWindows', _('Windows version'), info.windows_version),
			('announceUptime', _('Time since Windows started'), info.uptime),
			('announceGraphicsCard', _('Graphics card'), self.graphics_card),
			('announceGraphicsMemory', _('Graphics card memory'), self.graphics_memory),
		]
		if not (everything or self.hasBattery):
			topics = [topic for topic in topics if topic[0] != 'announceBattery']
		return topics

	_speedTestRunning = False

	def speed_test(self):
		"""Runs in the background like the other information, but says first that it has started."""
		if GlobalPlugin._speedTestRunning:
			# Translators: Said when a speed test is asked for while one is running.
			wx.CallAfter(say, _('A speed test is already running.'), False)
			return None
		GlobalPlugin._speedTestRunning = True
		try:
			# Translators: Said when the speed test starts.
			wx.CallAfter(say, _('Speed test running, 15 seconds at most.'), False)
			return info.speed_test()
		finally:
			GlobalPlugin._speedTestRunning = False

	def graphics_card(self):
		if globalVars.appArgs.secure:
			# Translators: The graphics card is not read on secure screens (logon, administrator rights).
			return _('Not available on secure screens.')
		return info.gpu(self.gpuProviders, settings()['temperatureUnit'])

	def graphics_memory(self):
		if globalVars.appArgs.secure:
			return _('Not available on secure screens.')
		return info.gpu_memory(self.gpuProviders)

	def report(self, gather, copy=False):
		"""Gather a piece of information in the background, then say it, or copy it."""
		def work():
			try:
				text = gather()
				failed = False
				if text is None:   # already said
					return
			except Exception:
				log.exception('ResourceMonitorAccess: gathering information failed')
				# Translators: Said when a piece of information could not be gathered.
				text, failed = _('This information could not be read.'), True
			if copy and not failed:
				_state['last'] = text
				wx.CallAfter(self.copy_last)
			else:
				wx.CallAfter(say, text)
		threading.Thread(target=work, daemon=True, name='ResourceMonitorAccess').start()

	def run_topic(self, name, gesture=None):
		"""A command: once to hear the information, twice quickly to copy it."""
		gather = {command: function for command, label, function in self.topics(everything=True)}[name]
		# Pressed twice: the information is read again, fresh, and copied instead of being said.
		# The speed test is long: pressed twice, it is not run again (Copy the last information gives its result).
		copy = gesture is not None and scriptHandler.getLastScriptRepeatCount() > 0 and name != 'announceSpeedTest'
		self.report(gather, copy=copy)

	def copy_last(self):
		if not _state['last']:
			# Translators: Said when there is nothing to copy yet.
			say(_('No information to copy yet.'), remember=False)
		elif api.copyToClip(_state['last']):
			# Translators: Said when the last information was copied.
			say(_('Copied to the clipboard.'), remember=False)

	def show_window(self):
		"""Open the resource window, or bring it back if it is already open."""
		global _window
		if _window:
			_window.Raise()
			_window.list.SetFocus()
			return
		gui.mainFrame.prePopup()
		try:
			_window = ResourceWindow(gui.mainFrame, self)
			_window.Show()
			_window.Raise()
		finally:
			gui.mainFrame.postPopup()

	def script_resourceWindow(self, gesture):
		wx.CallAfter(self.show_window)
	# Translators: Description of the command opening the window, in NVDA's Input Gestures dialog.
	script_resourceWindow.__doc__ = _('Opens the resource window: processor, memory, drives, Wi-Fi, Windows, graphics card.')

	def script_copyLastInformation(self, gesture):
		self.copy_last()
	# Translators: Description of a command in NVDA's Input Gestures dialog.
	script_copyLastInformation.__doc__ = _('Copies the last information to the clipboard.')
	script_copyLastInformation.speakOnDemand = True

	__gestures = {
		'kb:NVDA+control+shift+m': 'resourceWindow',
	}


def _make_topic_script(name, description):
	def script(self, gesture):
		self.run_topic(name, gesture)
	script.__name__ = 'script_' + name
	script.__doc__ = description
	script.speakOnDemand = True
	return script


_DESCRIPTIONS = {
	# Translators: Descriptions of the commands in NVDA's Input Gestures dialog. Pressed twice, each copies its information.
	'announceSummary': _('Announces the memory used and the processor load. Pressed twice, copies it.'),
	'announceProcessor': _('Announces the processor load, on average and for each core. Pressed twice, copies it.'),
	'announceApplications': _('Announces the applications using the most processor and memory. Pressed twice, copies it.'),
	'announceMemory': _('Announces the physical and virtual memory used. Pressed twice, copies it.'),
	'announceNetwork': _('Announces how the computer is connected, whether the Internet is reachable, and the current speed. Pressed twice, copies it.'),
	'announceSpeedTest': _('Tests the speed of the Internet connection, in 15 seconds at most.'),
	'announceBattery': _('Announces the battery level of a laptop. Pressed twice, copies it.'),
	'announceDrives': _('Announces the space used on each drive. Pressed twice, copies it.'),
	'announceWifi': _('Announces the Wi-Fi network, its signal and its security. Pressed twice, copies it.'),
	'announceWindows': _('Announces the version of Windows. Pressed twice, copies it.'),
	'announceUptime': _('Announces how long ago Windows started. Pressed twice, copies it.'),
	'announceGraphicsCard': _('Announces the use and temperature of the graphics card. Pressed twice, copies it.'),
	'announceGraphicsMemory': _('Announces the memory used on the graphics card. Pressed twice, copies it.'),
}
for _name, _description in _DESCRIPTIONS.items():
	setattr(GlobalPlugin, 'script_' + _name, _make_topic_script(_name, _description))
del _name, _description
