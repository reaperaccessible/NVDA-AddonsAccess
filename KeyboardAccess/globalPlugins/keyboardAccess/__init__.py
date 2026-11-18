# NVDA Add-on: KeyboardAccess, by ReaperAccessible.
# The Applications key, clicks on the selected item, and the keyboard layouts of Windows.
# Based on Remap Applications Key, Copyright (C) 2021 Héctor J. Benítez Corredera,
# 2023-2026 Rui Fontes and Ângelo Abrantes <https://github.com/ruifontes/RemapKeyAplication-para-NVDA>.
# This add-on is free software, licensed under the terms of the GNU General Public License (version 2).

import os
import time

import wx

import addonHandler
import api
import config
import globalPluginHandler
import globalVars
import gui
import inputCore
import keyboardHandler
import scriptHandler
import ui
import winUser
from logHandler import log

from . import keys
from . import layouts
from .updater import Updater

addonHandler.initTranslation()

# The add-on's own name: never translated.
ADDON_NAME = 'KeyboardAccess'
CONFIG_SECTION = 'keyboardAccess'
CONFIG_SPEC = {
	'clickSound': 'boolean(default=true)',
	# A key that NVDA cannot assign, pressed alone, used as the Applications key: 'rightAlt' (AltGr) or 'rightControl'.
	'applicationsAlone': "string(default='')",
	# The keyboard layouts: the user's own (as "00001009"), the last one chosen in the window, and those
	# the add-on loaded in Windows (removed when going back to the user's own), separated by commas.
	'myLayout': "string(default='')",
	'otherLayout': "string(default='')",
	'loadedLayouts': "string(default='')",
	'checkForUpdates': 'boolean(default=true)',
}
CLICK_SOUND = os.path.join(os.path.dirname(__file__), 'sounds', 'click.wav')
# The key that triggers an action is often pressed with Ctrl, Alt or Shift: the Applications key is sent
# once they are released, at most this long after, so that Windows does not receive Ctrl+Applications.
RELEASE_WAIT_MS = 1000
RELEASE_POLL_MS = 25
# Keys that NVDA treats like Ctrl or Shift, held with another key: NVDA never runs a command for them
# pressed alone. The add-on watches them itself, for the Applications key only. Name -> Windows key code.
ALONE_KEYS = {'rightAlt': 0xA5, 'rightControl': 0xA3}
# Held longer than this, the key pressed alone is taken as a change of mind: nothing is sent.
ALONE_MAX_SECONDS = 1.0
# Keys that, alone, are needed to type or to move around: they cannot replace the Applications key.
TYPING_KEYS = {
	'enter', 'numpadenter', 'tab', 'space', 'backspace', 'escape', 'delete', 'insert', 'home', 'end',
	'pageup', 'pagedown', 'uparrow', 'downarrow', 'leftarrow', 'rightarrow',
}


def settings():
	return config.conf[CONFIG_SECTION]


def actions(everything=False):
	"""(name of the command, name of the action, description in NVDA's Input Gestures dialog).
	Shift+Applications key is not listed in the window: it follows the key of the Applications key."""
	listed = [
		# Translators: An action of the add-on, as listed in its window.
		('applicationsKey', _('Applications key'),
			# Translators: Description of a command in NVDA's Input Gestures dialog.
			_('Presses the Applications key: opens the context menu of the selected item.')),
		('leftClick', _('Left click'), _('Clicks the selected item with the left mouse button.')),
		('doubleClick', _('Double click'), _('Double-clicks the selected item with the left mouse button.')),
		('rightClick', _('Right click'), _('Clicks the selected item with the right mouse button.')),
		('switchLayout', _('Switch keyboard'),
			_('Switches between your keyboard and the last keyboard language chosen in the window.')),
	]
	if everything:
		# Translators: An action of the add-on: the Applications key with Shift.
		listed.insert(1, ('shiftApplicationsKey', _('Shift+Applications key'),
			_('Presses Shift+Applications key: opens the extended context menu of the selected item.')))
	return listed


# --- the keys assigned, kept in NVDA's own list of input gestures (gestures.ini)
def _module():
	return GlobalPlugin.__module__


def assigned_keys(name):
	"""The identifiers of the keys assigned to an action, as "kb:printscreen"."""
	found = []
	try:
		for cls, identifier, script in inputCore.manager.userGestureMap.getScriptsForAllGestures():
			if cls is GlobalPlugin and script == name:
				found.append(identifier)
	except Exception:
		log.error('KeyboardAccess: cannot read the input gestures', exc_info=True)
	return found


def key_name(identifier):
	"""The name NVDA gives a key, as "print screen" or "control+alt+k"."""
	try:
		return inputCore.getDisplayTextForGestureIdentifier(identifier)[1]
	except Exception:
		return identifier.partition(':')[2]


def _save_gestures():
	try:
		inputCore.manager.userGestureMap.save()
	except Exception:
		log.error('KeyboardAccess: cannot save the input gestures', exc_info=True)


def with_shift(identifier):
	"""The same key with Shift: "kb:printscreen" -> "kb:shift+printscreen"."""
	return 'kb:shift+' + identifier.partition(':')[2]


def bindings(name, identifier):
	"""What assigning this key to this action means: the key of the Applications key also gives,
	with Shift, Shift+Applications key, as on a keyboard that has one."""
	if name == 'applicationsKey':
		return [(identifier, 'applicationsKey'), (with_shift(identifier), 'shiftApplicationsKey')]
	return [(identifier, name)]


def alone_key():
	"""'rightAlt' or 'rightControl' when such a key, pressed alone, is the Applications key; '' otherwise."""
	name = settings()['applicationsAlone']
	return name if name in ALONE_KEYS else ''


def assign_alone(name):
	"""AltGr or Right Ctrl, pressed alone, becomes the Applications key (it replaces any other key of it)."""
	unassign('applicationsKey')
	settings()['applicationsAlone'] = name


def assign(name, identifier):
	"""The key becomes the only one of this action; it is taken away from the add-on's other actions."""
	if name == 'applicationsKey':
		settings()['applicationsAlone'] = ''
	gestureMap = inputCore.manager.userGestureMap
	new = bindings(name, identifier)
	taken = {key for key, script in new}
	scripts = {script for key, script in new}
	for other, label, description in actions(everything=True):
		for old in assigned_keys(other):
			if other in scripts or old in taken:
				gestureMap.remove(old, _module(), 'GlobalPlugin', other)
	for key, script in new:
		gestureMap.add(key, _module(), 'GlobalPlugin', script)
	_save_gestures()


def unassign(name):
	removed = []
	if name == 'applicationsKey' and alone_key():
		removed.append('kb:' + alone_key())
		settings()['applicationsAlone'] = ''
	for script in ([name, 'shiftApplicationsKey'] if name == 'applicationsKey' else [name]):
		for identifier in assigned_keys(script):
			inputCore.manager.userGestureMap.remove(identifier, _module(), 'GlobalPlugin', script)
			removed.append(identifier)
	if removed:
		_save_gestures()
	return removed


def is_combination(identifier):
	return '+' in identifier.partition(':')[2]


def is_typing_key(identifier):
	"""A key needed to type or to move around, pressed alone: a letter, a digit, Enter, an arrow…"""
	parts = identifier.partition(':')[2].split('+')
	return len(parts) == 1 and (len(parts[0]) == 1 or parts[0] in TYPING_KEYS)


# --- the actions
def _when_keys_released(action, waited=0, shift=False):
	"""Shift, pressed with the key of the Applications key, is part of the key sent: no wait for it."""
	if keys.modifiers_down(ignore_shift=shift) and waited < RELEASE_WAIT_MS:
		wx.CallLater(RELEASE_POLL_MS, _when_keys_released, action, waited + RELEASE_POLL_MS, shift)
	else:
		action()


def _mouse_to_focus():
	"""Put the mouse pointer on the selected item; False if it has no place on the screen."""
	obj = api.getFocusObject()
	location = getattr(obj, 'location', None)
	if not location or location.width <= 0 or location.height <= 0:
		return False
	api.moveMouseToNVDAObject(obj)
	return True


def _click(button, count=1):
	if not _mouse_to_focus():
		# Translators: Said when a click cannot be made, because the selected item is not on the screen.
		ui.message(_('This item has no place on the screen: it cannot be clicked.'))
		return
	keys.click(button, count)
	if settings()['clickSound']:
		try:
			import nvwave
			nvwave.playWaveFile(CLICK_SOUND)
		except Exception:
			pass


def _applications(shift):
	_mouse_to_focus()   # some programs open the menu where the mouse pointer is
	keys.applications(shift)


def _find_script(gesture):
	try:
		return scriptHandler.findScript(gesture)
	except Exception:
		return None


class AloneKeyWatcher:
	"""Watches AltGr or Right Ctrl: pressed and released without any other key, it is the Applications key
	(with Shift, Shift+Applications key). With another key, as AltGr+2, it keeps working as usual.
	NVDA asks it about each key before handling it, from its keyboard thread: it must answer at once."""

	def __init__(self):
		self.pressed = None   # when the watched key went down, while no other key has come

	def decide(self, gesture=None, **kwargs):
		try:
			name = alone_key()
			if not name or inputCore.manager._captureFunc is not None:
				return True
			if not isinstance(gesture, keyboardHandler.KeyboardInputGesture):
				return True
			if getattr(gesture, 'vkCode', None) == keys.VK_MENU_MASK:
				# The add-on's own key that does nothing (see below): NVDA sees it too, it is not a combination.
				return True
			if gesture.isModifier and getattr(gesture, 'mainKeyName', None) == name:
				if self.pressed is None:
					self.pressed = time.monotonic()
					if name == 'rightAlt':
						# Alt released alone would select the menu bar of some programs: a key that does
						# nothing, pressed meanwhile, prevents it.
						keys.mask_menu()
					wx.CallAfter(self.poll)
			elif not gesture.isModifier:
				self.pressed = None   # a combination, as AltGr+2: not the Applications key
		except Exception:
			log.error('KeyboardAccess: watching the key failed', exc_info=True)
		return True

	def poll(self):
		if self.pressed is None:
			return
		name = alone_key()
		if name and keys.key_down(ALONE_KEYS[name]):
			if time.monotonic() - self.pressed > ALONE_MAX_SECONDS:
				self.pressed = None
			else:
				wx.CallLater(RELEASE_POLL_MS, self.poll)
			return
		self.pressed = None
		if name:
			_applications(keys.key_down(keys.VK_SHIFT))


watcher = AloneKeyWatcher()


# --- the keyboard layouts
LAYOUT_SETTLE_MS = 500   # time for the programs to switch layout before the previous one is removed


def my_layout(window=None):
	"""The user's own layout: the one in use before the add-on first changed it."""
	mine = settings()['myLayout']
	if not mine:
		mine = layouts.identifier_of(layouts.current(window)) or ''
	return mine


def _loaded():
	return [item for item in settings()['loadedLayouts'].split(',') if item]


def _remove_loaded():
	"""Remove from Windows the layouts the add-on loaded, once the programs no longer use them."""
	home = settings()['myLayout']
	for identifier in _loaded():
		if identifier == home:
			continue
		try:
			handle = layouts.load(identifier)
			if handle:
				layouts.unload(handle)
		except Exception:
			log.error('KeyboardAccess: cannot remove a keyboard layout', exc_info=True)
	settings()['loadedLayouts'] = ''


def use_layout(identifier, window=None):
	"""Use a layout of Windows in every program, as if it had been added in Windows' Settings."""
	if not settings()['myLayout']:
		settings()['myLayout'] = my_layout(window)
	if identifier == settings()['myLayout']:
		go_home(window)
		return
	already = {layouts.identifier_of(handle) for handle in layouts.installed()}
	handle = layouts.load(identifier)
	if not handle:
		# Translators: Said when a keyboard layout cannot be loaded.
		ui.message(_('This keyboard could not be loaded.'))
		return
	if identifier not in already and identifier not in _loaded():
		settings()['loadedLayouts'] = ','.join(_loaded() + [identifier])
	layouts.activate(handle, window)
	settings()['otherLayout'] = identifier
	# Translators: Said when a keyboard layout is now in use, as "Keyboard: English (United States)."
	ui.message(_('Keyboard: {name}.').format(name=layouts.name_of(identifier)))


def go_home(window=None):
	home = my_layout(window)
	handle = layouts.load(home) if home else None
	if not handle:
		ui.message(_('This keyboard could not be loaded.'))
		return
	layouts.activate(handle, window)
	wx.CallLater(LAYOUT_SETTLE_MS, _remove_loaded)
	# Translators: Said when going back to the user's own keyboard layout.
	ui.message(_('Back to your keyboard: {name}.').format(name=layouts.name_of(home)))


def switch_layout():
	"""The "Switch keyboard" action: from the user's keyboard to the last one chosen, and back."""
	window = winUser.getForegroundWindow()
	other = settings()['otherLayout']
	if not other:
		# Translators: Said when no keyboard language has been chosen yet.
		ui.message(_('Choose a keyboard language in the window first: NVDA+Ctrl+Shift+K.'))
		return
	if layouts.identifier_of(layouts.current(window)) == my_layout(window):
		use_layout(other, window)
	else:
		go_home(window)


ACTIONS = {
	'applicationsKey': lambda: _applications(False),
	'shiftApplicationsKey': lambda: _applications(True),
	'leftClick': lambda: _click('left'),
	'doubleClick': lambda: _click('left', 2),
	'rightClick': lambda: _click('right'),
	'switchLayout': switch_layout,
}


class KeyboardAccessSettingsPanel(gui.settingsDialogs.SettingsPanel):
	# The add-on's own name: shown as a category of NVDA's Settings dialog, never translated.
	title = ADDON_NAME
	updater = None

	def makeSettings(self, settingsSizer):
		helper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		# Translators: A setting: a sound confirms each click.
		self.clickSound = helper.addItem(wx.CheckBox(self, label=_('Play a &sound for each click')))
		self.clickSound.SetValue(settings()['clickSound'])
		self.checkForUpdates = helper.addItem(wx.CheckBox(self, label=_('Check for &updates automatically when NVDA starts')))
		self.checkForUpdates.SetValue(settings()['checkForUpdates'])
		self.checkNow = helper.addItem(wx.Button(self, label=_('Check for updates &now')))
		self.checkNow.Bind(wx.EVT_BUTTON, lambda event: self.updater.check(manual=True))
		self.checkNow.Enable(self.updater is not None)

	def onSave(self):
		settings()['clickSound'] = self.clickSound.GetValue()
		settings()['checkForUpdates'] = self.checkForUpdates.GetValue()


class KeysWindow(wx.Dialog):
	"""The list of the actions, each with its key: Enter assigns a new key, captured as NVDA does."""

	def __init__(self, parent, target=None):
		super().__init__(parent, title=ADDON_NAME)
		self.target = target   # the window of the program the user works in
		self.capturing = None   # the action waiting for its key
		self.pendingAlone = None   # AltGr or Right Ctrl pressed while waiting, not yet released
		sizer = wx.BoxSizer(wx.VERTICAL)
		# Translators: Label of the list of the add-on's window.
		sizer.Add(wx.StaticText(self, label=_('&Actions (Enter to assign a key):')), 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
		self.list = wx.ListBox(self, style=wx.LB_SINGLE)
		sizer.Add(self.list, 1, wx.ALL | wx.EXPAND, 10)
		buttons = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: A button of the add-on's window.
		assign_button = wx.Button(self, label=_('Assi&gn a key'))
		# Translators: A button of the add-on's window.
		remove_button = wx.Button(self, label=_('&Remove the key'))
		buttons.Add(assign_button, 0, wx.RIGHT, 10)
		buttons.Add(remove_button, 0)
		sizer.Add(buttons, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
		self.clickSound = wx.CheckBox(self, label=_('Play a &sound for each click'))
		self.clickSound.SetValue(settings()['clickSound'])
		sizer.Add(self.clickSound, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
		# Translators: Label of the list of keyboard languages of the add-on's window.
		sizer.Add(wx.StaticText(self, label=_('&Keyboard language (Enter to use it):')), 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
		self.layoutList = wx.ListBox(self, style=wx.LB_SINGLE, size=(-1, 200))
		sizer.Add(self.layoutList, 1, wx.ALL | wx.EXPAND, 10)
		close = wx.Button(self, wx.ID_CLOSE, label=_('&Close'))
		sizer.Add(close, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
		self.SetSizer(sizer)
		self.refresh(0)
		self.refresh_layouts(0)
		self.Fit()
		assign_button.Bind(wx.EVT_BUTTON, lambda event: self.start_capture())
		remove_button.Bind(wx.EVT_BUTTON, lambda event: self.remove())
		self.clickSound.Bind(wx.EVT_CHECKBOX, lambda event: settings().__setitem__('clickSound', self.clickSound.GetValue()))
		close.Bind(wx.EVT_BUTTON, lambda event: self.Close())
		self.list.Bind(wx.EVT_LISTBOX_DCLICK, lambda event: self.start_capture())
		self.layoutList.Bind(wx.EVT_LISTBOX_DCLICK, lambda event: self.use_selected_layout())
		self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
		self.Bind(wx.EVT_CLOSE, self.on_close)
		self.list.SetFocus()

	def refresh(self, selection=None):
		if selection is None:
			selection = max(0, self.list.GetSelection())
		entries = []
		for name, label, description in actions():
			found = [key_name(identifier) for identifier in assigned_keys(name)]
			if name == 'applicationsKey' and alone_key():
				found.insert(0, key_name('kb:' + alone_key()))
			# Translators: Between two keys assigned to the same action.
			keys_text = _(' or ').join(found) if found else _('no key')
			# Translators: An entry of the list, as "Applications key: print screen".
			entries.append(_('{action}: {key}').format(action=label, key=keys_text))
		self.list.Set(entries)
		self.list.SetSelection(selection)

	def refresh_layouts(self, selection=None):
		"""Your own keyboard first, then every keyboard of Windows; the one in use is marked."""
		if selection is None:
			selection = max(0, self.layoutList.GetSelection())
		in_use = layouts.identifier_of(layouts.current(self.target))
		home = my_layout(self.target)
		self.layoutIds = [home] + [identifier for identifier, name, layout_id in layouts.all_layouts()]
		entries = []
		for index, identifier in enumerate(self.layoutIds):
			name = layouts.name_of(identifier)
			# Translators: The first entry of the list of keyboards, as "Your keyboard: French (Canada)".
			text = _('Your keyboard: {name}').format(name=name) if index == 0 else name
			if identifier == in_use and (index == 0 or identifier != home):
				# Translators: Added to the keyboard in use, in the list of keyboards.
				text += _(' (in use)')
			entries.append(text)
		self.layoutList.Set(entries)
		self.layoutList.SetSelection(min(selection, len(entries) - 1))

	def use_selected_layout(self):
		index = self.layoutList.GetSelection()
		if index == wx.NOT_FOUND:
			return
		if index == 0:
			go_home(self.target)
		else:
			use_layout(self.layoutIds[index], self.target)
		wx.CallLater(LAYOUT_SETTLE_MS, lambda: self and self.refresh_layouts())

	def selected(self):
		index = self.list.GetSelection()
		return actions()[index] if index != wx.NOT_FOUND else None

	# --- capturing the next key, as NVDA's Input Gestures dialog does
	def start_capture(self):
		action = self.selected()
		if action is None:
			return
		self.capturing = action
		# Translators: Said when the add-on waits for the key to assign.
		ui.message(_('Press the key to use as {action}, or Escape to cancel.').format(action=action[1]))
		inputCore.manager._captureFunc = self._captor

	def stop_capture(self):
		self.capturing = None
		self.pendingAlone = None
		if inputCore.manager._captureFunc == self._captor:
			inputCore.manager._captureFunc = None

	def _captor(self, gesture):
		"""Called by NVDA for each key while waiting; False keeps the key from doing anything else."""
		if gesture.isModifier:
			name = getattr(gesture, 'mainKeyName', None)
			if self.capturing and self.capturing[0] == 'applicationsKey' and name in ALONE_KEYS and self.pendingAlone is None:
				# Taken only if released without any other key.
				self.pendingAlone = name
				wx.CallAfter(self.poll_alone, time.monotonic())
			return False
		self.pendingAlone = None
		if not isinstance(gesture, keyboardHandler.KeyboardInputGesture):
			# Translators: Said when something else than a keyboard key is pressed while waiting for a key.
			wx.CallAfter(ui.message, _('Press a key of the keyboard.'))
			return False
		identifiers = gesture.normalizedIdentifiers
		# The form valid for every keyboard layout, as "kb:printscreen".
		identifier = next((item for item in identifiers if item.startswith('kb:')), identifiers[-1])
		if identifier == 'kb:escape':
			self.stop_capture()
			# Translators: Said when the assignment of a key is cancelled.
			wx.CallAfter(ui.message, _('Cancelled.'))
			return False
		action = self.capturing
		if action[0] == 'applicationsKey' and is_combination(identifier):
			# Translators: Said when a combination is pressed for the Applications key; the add-on keeps waiting.
			wx.CallAfter(ui.message, _('For the Applications key, press a key alone, without Ctrl, Alt, Shift or NVDA: '
				'Shift with this key will open the extended menu, as on a keyboard that has the Applications key.'))
			return False
		if is_typing_key(identifier):
			# Translators: Said when the key pressed is needed to type or move around; the add-on keeps waiting.
			wx.CallAfter(ui.message, _('{key} is needed to type or to move around. Press another key, '
				'for example Print Screen, or a combination with Ctrl or Alt.').format(key=key_name(identifier)))
			return False
		scripts = [_find_script(gesture)]
		if action[0] == 'applicationsKey':
			try:
				scripts.append(_find_script(keyboardHandler.KeyboardInputGesture.fromName(with_shift(identifier)[3:])))
			except Exception:
				scripts.append(None)
		self.stop_capture()
		wx.CallAfter(self.captured, action, identifier, scripts)
		return False

	def poll_alone(self, started):
		name = self.pendingAlone
		if name is None or not self.capturing:
			return
		if keys.key_down(ALONE_KEYS[name]):
			wx.CallLater(RELEASE_POLL_MS, self.poll_alone, started)
			return
		self.stop_capture()
		if name == alone_key():
			ui.message(_('{key} is already the key of {action}.').format(key=key_name('kb:' + name), action=_('Applications key')))
			return
		assign_alone(name)
		self.refresh()
		ui.message(_('{key} is now the Applications key. With Shift, it opens the extended menu.').format(key=key_name('kb:' + name)))

	def captured(self, action, identifier, scripts):
		name, label, description = action
		key = key_name(identifier)
		if identifier in assigned_keys(name):
			# Translators: Said when the key pressed is already the one of this action.
			ui.message(_('{key} is already the key of {action}.').format(key=key, action=label))
			return
		# A command of NVDA or of another add-on already using the key (or Shift with it).
		others = [(gid, script) for (gid, script_name), script in zip(bindings(name, identifier), scripts)
			if script is not None and not isinstance(getattr(script, '__self__', None), GlobalPlugin)]
		if others:
			gid, script = others[0]
			key = key_name(gid)
			used_for = (script.__doc__ or script.__name__.replace('script_', '')).strip()
			# Translators: Asked before taking a key that already does something else.
			question = _('{key} is already used for: {command}\n\nUse it for {action} instead?').format(
				key=key, command=used_for, action=label)
			if gui.messageBox(question, ADDON_NAME, wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING, self) != wx.YES:
				ui.message(_('Cancelled.'))
				return
		key = key_name(identifier)
		assign(name, identifier)
		self.refresh()
		if name == 'applicationsKey':
			# Translators: Said when a key has been assigned to the Applications key.
			ui.message(_('{key} is now the Applications key. With Shift, it opens the extended menu.').format(key=key))
		else:
			# Translators: Said when a key has been assigned.
			ui.message(_('{key} now does: {action}.').format(key=key, action=label))

	def remove(self):
		action = self.selected()
		if action is None:
			return
		if unassign(action[0]):
			self.refresh()
			# Translators: Said when the key of an action has been removed.
			ui.message(_('Key removed from {action}.').format(action=action[1]))
		else:
			# Translators: Said when the action has no key to remove.
			ui.message(_('{action} has no key.').format(action=action[1]))

	def on_key(self, event):
		key = event.GetKeyCode()
		if key == wx.WXK_ESCAPE and not event.HasAnyModifiers():
			self.Close()
		elif key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and self.FindFocus() is self.layoutList and not event.HasAnyModifiers():
			self.use_selected_layout()
		elif key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and self.FindFocus() is self.list and not event.HasAnyModifiers():
			self.start_capture()
		elif key in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE) and self.FindFocus() is self.list and not event.HasAnyModifiers():
			self.remove()
		else:
			event.Skip()

	def on_close(self, event):
		self.stop_capture()
		global _window
		_window = None
		self.Destroy()


_window = None


def open_window(target=None):
	"""Open the window, or bring it back if it is already open."""
	global _window
	if _window:
		_window.Raise()
		_window.list.SetFocus()
		return
	gui.mainFrame.prePopup()
	try:
		_window = KeysWindow(gui.mainFrame, target)
		_window.Show()
		_window.Raise()
	finally:
		gui.mainFrame.postPopup()


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	# The add-on's own category in NVDA's Input Gestures dialog, never translated.
	scriptCategory = ADDON_NAME

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		config.conf.spec[CONFIG_SECTION] = CONFIG_SPEC
		self.updater = None
		inputCore.decide_executeGesture.register(watcher.decide)
		self.menuItem = None
		if globalVars.appArgs.secure:
			return
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(KeyboardAccessSettingsPanel)
		try:
			tools = gui.mainFrame.sysTrayIcon.toolsMenu
			self.menuItem = tools.Append(wx.ID_ANY, ADDON_NAME + '…')
			gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, lambda event: open_window(), self.menuItem)
		except Exception:
			log.error('KeyboardAccess: cannot add the Tools menu entry', exc_info=True)
		self.updater = Updater(addonHandler.getCodeAddon())
		KeyboardAccessSettingsPanel.updater = self.updater
		if settings()['checkForUpdates']:
			self.updater.scheduleStartupCheck()

	def terminate(self, *args, **kwargs):
		try:
			inputCore.decide_executeGesture.unregister(watcher.decide)
		except Exception:
			pass
		if self.updater is not None:
			self.updater.cancel()
		if _window:
			_window.Close()
		try:
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(KeyboardAccessSettingsPanel)
		except ValueError:
			pass
		if self.menuItem is not None:
			try:
				gui.mainFrame.sysTrayIcon.toolsMenu.Remove(self.menuItem)
			except Exception:
				pass
		super().terminate(*args, **kwargs)

	def script_openWindow(self, gesture):
		if globalVars.appArgs.secure:
			return
		# The program the user works in, before the window takes the focus: its keyboard is the one changed.
		wx.CallAfter(open_window, winUser.getForegroundWindow())
	# Translators: Description of the command opening the window, in NVDA's Input Gestures dialog.
	script_openWindow.__doc__ = _('Opens the keyboard window: Applications key, clicks and keyboard language.')

	__gestures = {
		'kb:NVDA+control+shift+k': 'openWindow',
	}


def _make_script(name, description):
	def script(self, gesture):
		_when_keys_released(ACTIONS[name], shift=name == 'shiftApplicationsKey')
	script.__name__ = 'script_' + name
	script.__doc__ = description
	return script


for _name, _label, _description in actions(everything=True):
	setattr(GlobalPlugin, 'script_' + _name, _make_script(_name, _description))
del _name, _label, _description
