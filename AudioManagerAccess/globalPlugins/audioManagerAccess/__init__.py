# NVDA Add-on: AudioManagerAccess, by ReaperAccessible.
# The sound of Windows in one window: outputs, inputs, each application with its own volume and devices.
# Based on Audio Manager, Copyright huaiyinfeilong <https://github.com/huaiyinfeilong/audiomanager>.
# This add-on is free software, licensed under the terms of the GNU General Public License (version 2).

import time

import wx

import addonHandler
import api
import config
import globalPluginHandler
import globalVars
import gui
import ui
from logHandler import log

from . import audio
from .shortcuts import Shortcuts, ShortcutsDialog
from .updater import Updater

addonHandler.initTranslation()

# The add-on's own name: never translated.
ADDON_NAME = 'AudioManagerAccess'
CONFIG_SECTION = 'audioManagerAccess'
CONFIG_SPEC = {
	'micLock': 'boolean(default=false)',
	'micVolume': 'integer(default=50, min=0, max=100)',
	'checkForUpdates': 'boolean(default=true)',
}
VOLUME_STEP = 5
# After a change made in the window, its list line is updated and NVDA would read it again ("Chrome, 65%"):
# for this long, only the add-on's own answer ("65%") is spoken.
QUIET_AFTER_CHANGE = 0.4
MIC_LOCK_EVERY_MS = 1000


def settings():
	return config.conf[CONFIG_SECTION]


_state = {'quietUntil': 0.0, 'ownSpeech': False}


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


def _failed():
	log.error('AudioManagerAccess: an action failed', exc_info=True)
	# Translators: Said when an action on the sound could not be done.
	ui.message(_('This could not be done.'))


def commands():
	"""(name of the command, label in the shortcuts window and description in NVDA's Input Gestures dialog)."""
	return [
		# Translators: A command of the add-on.
		('toggleSound', _('Sound off or on')),
		('toggleMicrophone', _('Microphone off or on')),
		('nextOutput', _('Next output as the default one')),
		('previousOutput', _('Previous output as the default one')),
		('nextInput', _('Next input as the default one')),
		('previousInput', _('Previous input as the default one')),
		('toggleCurrentApplication', _('Sound of the current application off or on')),
		('toggleMicrophoneLock', _('Lock or unlock the microphone volume')),
		('nextNVDAOutput', _('Next output for NVDA')),
		('previousNVDAOutput', _('Previous output for NVDA')),
	]


# --- NVDA's own output device
def nvda_outputs():
	"""[(value in NVDA's settings, name)]: Windows' default device first."""
	from utils import mmdevice
	# The values are NVDA's own, the one of Windows' default device included.
	return [(device.id, device.friendlyName) for device in mmdevice.getOutputDevices(includeDefault=True)]


def nvda_output():
	return config.conf['audio']['outputDevice']


def set_nvda_output(value):
	if config.conf['audio']['outputDevice'] == value:
		return
	config.conf['audio']['outputDevice'] = value
	import synthDriverHandler
	import tones
	synthDriverHandler.setSynth(synthDriverHandler.getSynth().name)
	tones.terminate()
	tones.initialize()


# --- what is said and shown
def device_text(device):
	# Translators: A device in the window, as "Headphones, 60%".
	text = _('{name}, {volume}%').format(name=device.name, volume=device.volume if device.volume is not None else '?')
	if device.default:
		# Translators: Added to the default device of Windows, in the window.
		text += _(', default')
	if device.muted:
		# Translators: Added to a device or an application whose sound is cut, in the window.
		text += _(', muted')
	return text


def app_text(app, names):
	text = _('{name}, {volume}%').format(name=app.name, volume=app.volume)
	if app.muted:
		text += _(', muted')
	if app.output:
		# Translators: Added to an application sent to its own output, as ", output: Headphones".
		text += _(', output: {device}').format(device=names.get(app.output, '?'))
	if app.input:
		# Translators: Added to an application given its own input, as ", input: USB microphone".
		text += _(', input: {device}').format(device=names.get(app.input, '?'))
	return text


def system_sounds():
	# Translators: The sounds of Windows itself, in the list of applications.
	return _('System sounds')


class AppDevicesDialog(wx.Dialog):
	"""The output and input of one application."""

	def __init__(self, parent, app, outputs, inputs):
		# Translators: Title of the window choosing the devices of an application, as "Devices of Thunderbird".
		super().__init__(parent, title=_('Devices of {name}').format(name=app.name))
		# Translators: The first choice: the application follows Windows' default device.
		default = _("Windows' default device")
		self.outputs = [''] + [device.id for device in outputs]
		self.inputs = [''] + [device.id for device in inputs]
		sizer = wx.BoxSizer(wx.VERTICAL)
		# Translators: Label of the output of an application.
		sizer.Add(wx.StaticText(self, label=_('&Output:')), 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
		self.output = wx.Choice(self, choices=[default] + [device.name for device in outputs])
		self.output.SetSelection(self.outputs.index(app.output) if app.output in self.outputs else 0)
		sizer.Add(self.output, 0, wx.ALL | wx.EXPAND, 10)
		# Translators: Label of the input of an application.
		sizer.Add(wx.StaticText(self, label=_('&Input:')), 0, wx.LEFT | wx.RIGHT, 10)
		self.input = wx.Choice(self, choices=[default] + [device.name for device in inputs])
		self.input.SetSelection(self.inputs.index(app.input) if app.input in self.inputs else 0)
		sizer.Add(self.input, 0, wx.ALL | wx.EXPAND, 10)
		sizer.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 10)
		self.SetSizerAndFit(sizer)
		self.output.SetFocus()

	def chosen(self):
		return self.outputs[self.output.GetSelection()], self.inputs[self.input.GetSelection()]


class AudioWindow(wx.Dialog):

	def __init__(self, parent, plugin):
		super().__init__(parent, title=ADDON_NAME)
		self.plugin = plugin
		self.outputs, self.inputs, self.apps = [], [], []
		sizer = wx.BoxSizer(wx.VERTICAL)
		# Translators: How the lists of the window work.
		sizer.Add(wx.StaticText(self, label=_('In the lists: Left and Right arrows change the volume, Space cuts or '
			'restores the sound, Enter makes a device the default one or chooses the devices of an application.')),
			0, wx.ALL, 10)
		self.lists = {}
		# Translators: Labels of the lists of the window.
		for kind, label in (('outputs', _('&Outputs:')), ('inputs', _('&Inputs:')), ('apps', _('&Applications:'))):
			sizer.Add(wx.StaticText(self, label=label), 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
			self.lists[kind] = wx.ListBox(self, style=wx.LB_SINGLE, size=(500, 130))
			sizer.Add(self.lists[kind], 1, wx.ALL | wx.EXPAND, 10)
		# Translators: Label of the output device used by NVDA itself.
		sizer.Add(wx.StaticText(self, label=_('Output of &NVDA:')), 0, wx.LEFT | wx.RIGHT, 10)
		self.nvdaOutputs = []
		self.nvdaOutput = wx.Choice(self)
		sizer.Add(self.nvdaOutput, 0, wx.ALL | wx.EXPAND, 10)
		# Translators: A check box: the microphone volume can no longer be changed by other programs.
		self.micLock = wx.CheckBox(self, label=_('&Lock the microphone volume'))
		self.micLock.SetValue(settings()['micLock'])
		sizer.Add(self.micLock, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
		buttons = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: A button: every application back to the default devices, full volume, not muted.
		reset = wx.Button(self, label=_('&Reset the applications'))
		# Translators: A button opening the window to assign keys to the commands.
		keys_button = wx.Button(self, label=_('Assign &keys to the commands…'))
		close = wx.Button(self, wx.ID_CLOSE, label=_('&Close'))
		for button in (reset, keys_button, close):
			buttons.Add(button, 0, wx.RIGHT, 10)
		sizer.Add(buttons, 0, wx.ALL, 10)
		self.SetSizer(sizer)
		self.refresh()
		self.refresh_nvda_outputs()
		self.Fit()
		for kind, box in self.lists.items():
			box.Bind(wx.EVT_LISTBOX_DCLICK, lambda event, kind=kind: self.activate(kind))
		self.nvdaOutput.Bind(wx.EVT_CHOICE, self.on_nvda_output)
		self.micLock.Bind(wx.EVT_CHECKBOX, lambda event: self.plugin.set_mic_lock(self.micLock.GetValue()))
		reset.Bind(wx.EVT_BUTTON, lambda event: self.reset())
		keys_button.Bind(wx.EVT_BUTTON, lambda event: self.plugin.show_shortcuts(self))
		close.Bind(wx.EVT_BUTTON, lambda event: self.Close())
		self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
		self.Bind(wx.EVT_CLOSE, self.on_close)
		self.lists['outputs'].SetFocus()

	def refresh(self):
		try:
			self.outputs = audio.devices(audio.OUTPUT)
			self.inputs = audio.devices(audio.INPUT)
			self.apps = audio.applications(system_sounds())
		except Exception:
			log.error('AudioManagerAccess: cannot read the sound devices', exc_info=True)
		names = {device.id: device.name for device in self.outputs + self.inputs}
		for kind, entries in (('outputs', [device_text(d) for d in self.outputs]),
				('inputs', [device_text(d) for d in self.inputs]), ('apps', [app_text(a, names) for a in self.apps])):
			box = self.lists[kind]
			selection = box.GetSelection()
			if [box.GetString(i) for i in range(box.GetCount())] != entries:
				box.Set(entries)
			if entries:
				box.SetSelection(min(max(selection, 0), len(entries) - 1))

	def refresh_nvda_outputs(self):
		try:
			self.nvdaOutputs = nvda_outputs()
		except Exception:
			log.error('AudioManagerAccess: cannot read the outputs of NVDA', exc_info=True)
			self.nvdaOutputs = []
		self.nvdaOutput.Set([name for value, name in self.nvdaOutputs])
		values = [value for value, name in self.nvdaOutputs]
		if values:
			self.nvdaOutput.SetSelection(values.index(nvda_output()) if nvda_output() in values else 0)

	def current(self, kind):
		index = self.lists[kind].GetSelection()
		items = {'outputs': self.outputs, 'inputs': self.inputs, 'apps': self.apps}[kind]
		return items[index] if 0 <= index < len(items) else None

	def focused_list(self):
		focus = self.FindFocus()
		for kind, box in self.lists.items():
			if box is focus:
				return kind
		return None

	# --- the keys of the lists
	def change_volume(self, kind, step):
		item = self.current(kind)
		if item is None or item.volume is None:
			return
		try:
			if kind == 'apps':
				volume = audio.set_app_volume(item, item.volume + step)
			else:
				volume = audio.set_volume(item.id, item.volume + step)
				if kind == 'inputs' and item.default:
					self.plugin.remember_mic_volume(volume)
		except Exception:
			return _failed()
		quiet()
		self.refresh()
		# Translators: A volume, as "65%".
		say(_('{volume}%').format(volume=volume))

	def toggle_mute(self, kind):
		item = self.current(kind)
		if item is None:
			return
		try:
			if kind == 'apps':
				audio.set_app_mute(item, not item.muted)
			else:
				audio.set_mute(item.id, not item.muted)
		except Exception:
			return _failed()
		muted = not item.muted
		quiet()
		self.refresh()
		# Translators: Said when a sound is cut, or restored.
		say(_('Muted') if muted else _('Unmuted'))

	def activate(self, kind):
		item = self.current(kind)
		if item is None:
			return
		if kind == 'apps':
			return self.choose_app_devices(item)
		try:
			audio.set_default(item.id)
		except Exception:
			return _failed()
		quiet()
		self.refresh()
		# Translators: Said when a device becomes Windows' default one.
		say(_('{name} is now the default device.').format(name=item.name))

	def choose_app_devices(self, app):
		if app.key == 'nvda.exe':
			# NVDA chooses its device by itself: its own setting is the one that counts.
			self.nvdaOutput.SetFocus()
			# Translators: Said when Enter is pressed on NVDA in the list of applications.
			ui.message(_('NVDA chooses its output by itself: choose it here.'))
			return
		if not app.pids:
			# Translators: Said for the sounds of Windows, whose devices cannot be chosen apart.
			ui.message(_('The devices of the system sounds follow the default devices.'))
			return
		dialog = AppDevicesDialog(self, app, self.outputs, self.inputs)
		try:
			if dialog.ShowModal() != wx.ID_OK:
				return
			output, input_ = dialog.chosen()
		finally:
			dialog.Destroy()
		try:
			if output != app.output:
				audio.set_app_device(app, audio.OUTPUT, output)
			if input_ != app.input:
				audio.set_app_device(app, audio.INPUT, input_)
		except Exception:
			return _failed()
		quiet()
		self.refresh()
		# Translators: Said when the devices of an application have been changed. Most programs only use
		# the new device once restarted.
		say(_('Devices of {name} changed. Close and restart {name} to hear it on these devices.').format(name=app.name))

	def on_nvda_output(self, event):
		index = self.nvdaOutput.GetSelection()
		if 0 <= index < len(self.nvdaOutputs):
			try:
				set_nvda_output(self.nvdaOutputs[index][0])
			except Exception:
				_failed()

	def reset(self):
		try:
			audio.reset_applications()
		except Exception:
			return _failed()
		self.refresh()
		# Translators: Said when every application has been reset.
		ui.message(_('Every application is back to the default devices, at full volume.'))

	def on_key(self, event):
		key = event.GetKeyCode()
		kind = self.focused_list()
		plain = not event.HasAnyModifiers()
		if key == wx.WXK_ESCAPE and plain:
			self.Close()
		elif kind and plain and key in (wx.WXK_LEFT, wx.WXK_NUMPAD_LEFT):
			self.change_volume(kind, -VOLUME_STEP)
		elif kind and plain and key in (wx.WXK_RIGHT, wx.WXK_NUMPAD_RIGHT):
			self.change_volume(kind, VOLUME_STEP)
		elif kind and plain and key == wx.WXK_SPACE:
			self.toggle_mute(kind)
		elif kind and plain and key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
			self.activate(kind)
		elif key == wx.WXK_F5 and plain:
			self.refresh()
		else:
			event.Skip()

	def on_close(self, event):
		global _window
		_window = None
		self.Destroy()


_window = None


class AudioManagerAccessSettingsPanel(gui.settingsDialogs.SettingsPanel):
	# The add-on's own name: shown as a category of NVDA's Settings dialog, never translated.
	title = ADDON_NAME
	updater = None

	def makeSettings(self, settingsSizer):
		helper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		self.checkForUpdates = helper.addItem(wx.CheckBox(self, label=_('Check for &updates automatically when NVDA starts')))
		self.checkForUpdates.SetValue(settings()['checkForUpdates'])
		self.checkNow = helper.addItem(wx.Button(self, label=_('Check for updates &now')))
		self.checkNow.Bind(wx.EVT_BUTTON, lambda event: self.updater.check(manual=True))
		self.checkNow.Enable(self.updater is not None)

	def onSave(self):
		settings()['checkForUpdates'] = self.checkForUpdates.GetValue()


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	# The add-on's own category in NVDA's Input Gestures dialog, never translated.
	scriptCategory = ADDON_NAME

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		config.conf.spec[CONFIG_SECTION] = CONFIG_SPEC
		self.updater = None
		self.menuItem = None
		try:
			from speech.extensions import filter_speechSequence
			filter_speechSequence.register(_quiet_filter)
		except Exception:
			log.error('AudioManagerAccess: quiet period unavailable', exc_info=True)
		self.shortcuts = Shortcuts(GlobalPlugin)
		self.micTimer = wx.Timer()
		self.micTimer.Bind(wx.EVT_TIMER, lambda event: self.keep_mic_volume())
		if settings()['micLock']:
			self.micTimer.Start(MIC_LOCK_EVERY_MS)
		if globalVars.appArgs.secure:
			return
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(AudioManagerAccessSettingsPanel)
		try:
			tools = gui.mainFrame.sysTrayIcon.toolsMenu
			self.menuItem = tools.Append(wx.ID_ANY, ADDON_NAME + '…')
			gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, lambda event: self.show_window(), self.menuItem)
		except Exception:
			log.error('AudioManagerAccess: cannot add the Tools menu entry', exc_info=True)
		self.updater = Updater(addonHandler.getCodeAddon())
		AudioManagerAccessSettingsPanel.updater = self.updater
		if settings()['checkForUpdates']:
			self.updater.scheduleStartupCheck()

	def terminate(self, *args, **kwargs):
		self.micTimer.Stop()
		try:
			from speech.extensions import filter_speechSequence
			filter_speechSequence.unregister(_quiet_filter)
		except Exception:
			pass
		if self.updater is not None:
			self.updater.cancel()
		if _window:
			_window.Close()
		try:
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(AudioManagerAccessSettingsPanel)
		except ValueError:
			pass
		if self.menuItem is not None:
			try:
				gui.mainFrame.sysTrayIcon.toolsMenu.Remove(self.menuItem)
			except Exception:
				pass
		super().terminate(*args, **kwargs)

	# --- the window
	def show_window(self):
		global _window
		if _window:
			_window.Raise()
			_window.lists['outputs'].SetFocus()
			return
		gui.mainFrame.prePopup()
		try:
			_window = AudioWindow(gui.mainFrame, self)
			_window.Show()
			_window.Raise()
		finally:
			gui.mainFrame.postPopup()

	def show_shortcuts(self, parent):
		# Translators: Title of the window to assign keys to the commands.
		dialog = ShortcutsDialog(parent, _('Keys of {name}').format(name=ADDON_NAME), commands(), self.shortcuts)
		try:
			dialog.ShowModal()
		finally:
			dialog.Destroy()

	def script_openWindow(self, gesture):
		if globalVars.appArgs.secure:
			return
		wx.CallAfter(self.show_window)
	# Translators: Description of the command opening the window, in NVDA's Input Gestures dialog.
	script_openWindow.__doc__ = _('Opens the sound window: outputs, inputs and applications.')

	# --- the microphone lock
	def set_mic_lock(self, locked):
		settings()['micLock'] = bool(locked)
		if locked:
			mic = self._default_input()
			if mic is not None and mic.volume is not None:
				settings()['micVolume'] = mic.volume
			self.micTimer.Start(MIC_LOCK_EVERY_MS)
			# Translators: Said when the microphone volume is locked.
			ui.message(_('Microphone volume locked at {volume}%.').format(volume=settings()['micVolume']))
		else:
			self.micTimer.Stop()
			# Translators: Said when the microphone volume is unlocked.
			ui.message(_('Microphone volume unlocked.'))
		if _window:
			_window.micLock.SetValue(bool(locked))

	def remember_mic_volume(self, volume):
		"""The user changed the microphone volume in the window: the lock keeps the new one."""
		if settings()['micLock']:
			settings()['micVolume'] = volume

	def keep_mic_volume(self):
		try:
			mic = self._default_input()
			if mic is not None and mic.volume is not None and mic.volume != settings()['micVolume']:
				audio.set_volume(mic.id, settings()['micVolume'])
		except Exception:
			pass

	def _default_input(self):
		return next((device for device in audio.devices(audio.INPUT) if device.default), None)

	# --- the commands, without any key by default
	def _toggle_default(self, flow, off_text, on_text):
		device = next((item for item in audio.devices(flow) if item.default), None)
		if device is None:
			# Translators: Said when Windows has no default device.
			return ui.message(_('No device.'))
		audio.set_mute(device.id, not device.muted)
		ui.message(off_text if not device.muted else on_text)

	def _next_default(self, flow, step=1):
		found = audio.devices(flow)
		if not found:
			return ui.message(_('No device.'))
		index = next((i for i, device in enumerate(found) if device.default), 0)
		device = found[(index + step) % len(found)]
		audio.set_default(device.id)
		ui.message(device.name)

	def run_command(self, name):
		try:
			if name == 'toggleSound':
				# Translators: Said when the sound of the default output is cut, or restored.
				self._toggle_default(audio.OUTPUT, _('Sound off'), _('Sound on'))
			elif name == 'toggleMicrophone':
				# Translators: Said when the default microphone is cut, or restored.
				self._toggle_default(audio.INPUT, _('Microphone off'), _('Microphone on'))
			elif name in ('nextOutput', 'previousOutput'):
				self._next_default(audio.OUTPUT, 1 if name == 'nextOutput' else -1)
			elif name in ('nextInput', 'previousInput'):
				self._next_default(audio.INPUT, 1 if name == 'nextInput' else -1)
			elif name == 'toggleCurrentApplication':
				self._toggle_current_application()
			elif name == 'toggleMicrophoneLock':
				self.set_mic_lock(not settings()['micLock'])
			elif name in ('nextNVDAOutput', 'previousNVDAOutput'):
				outputs = nvda_outputs()
				values = [value for value, label in outputs]
				index = values.index(nvda_output()) if nvda_output() in values else 0
				value, label = outputs[(index + (1 if name == 'nextNVDAOutput' else -1)) % len(outputs)]
				set_nvda_output(value)
				ui.message(label)
		except Exception:
			_failed()
		if _window:
			_window.refresh()

	def _toggle_current_application(self):
		pid = getattr(api.getForegroundObject(), 'processID', None)
		app = next((item for item in audio.applications(system_sounds()) if pid in item.pids), None)
		if app is None:
			# Translators: Said when the application in use plays no sound.
			return ui.message(_('This application is not playing any sound.'))
		audio.set_app_mute(app, not app.muted)
		# Translators: Said when the sound of the application in use is cut, or restored.
		ui.message((_('{name}: muted') if app.muted else _('{name}: sound on')).format(name=app.name))

	__gestures = {
		'kb:NVDA+control+shift+p': 'openWindow',
	}


def _make_script(name, description):
	def script(self, gesture):
		self.run_command(name)
	script.__name__ = 'script_' + name
	script.__doc__ = description
	return script


for _name, _description in commands():
	setattr(GlobalPlugin, 'script_' + _name, _make_script(_name, _description))
del _name, _description
