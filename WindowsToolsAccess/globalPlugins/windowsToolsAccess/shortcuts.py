# WindowsToolsAccess: assigning a key to a command, by simply pressing it (as in KeyboardAccess).
# The keys are kept in NVDA's own list of input gestures (gestures.ini), as if added in its Input Gestures dialog.

import wx

import addonHandler
import gui
import inputCore
import keyboardHandler
import scriptHandler
import ui
from logHandler import log

addonHandler.initTranslation()

# Keys that, alone, are needed to type or to move around: they cannot be assigned alone.
TYPING_KEYS = {
	'enter', 'numpadenter', 'tab', 'space', 'backspace', 'escape', 'delete', 'insert', 'home', 'end',
	'pageup', 'pagedown', 'uparrow', 'downarrow', 'leftarrow', 'rightarrow',
}


def key_name(identifier):
	try:
		return inputCore.getDisplayTextForGestureIdentifier(identifier)[1]
	except Exception:
		return identifier.partition(':')[2]


def is_typing_key(identifier):
	parts = identifier.partition(':')[2].split('+')
	return len(parts) == 1 and (len(parts[0]) == 1 or parts[0] in TYPING_KEYS)


class Shortcuts:
	"""The keys of the commands of one global plugin class."""

	def __init__(self, plugin_class):
		self.cls = plugin_class

	def keys(self, name):
		found = []
		try:
			for cls, identifier, script in inputCore.manager.userGestureMap.getScriptsForAllGestures():
				if cls is self.cls and script == name:
					found.append(identifier)
		except Exception:
			log.error('WindowsToolsAccess: cannot read the input gestures', exc_info=True)
		return found

	def _save(self):
		try:
			inputCore.manager.userGestureMap.save()
		except Exception:
			log.error('WindowsToolsAccess: cannot save the input gestures', exc_info=True)

	def assign(self, name, identifier, names):
		"""The key becomes the only one of this command; it is taken away from the plugin's other commands."""
		gestureMap = inputCore.manager.userGestureMap
		for other in names:
			for old in self.keys(other):
				if other == name or old == identifier:
					gestureMap.remove(old, self.cls.__module__, self.cls.__name__, other)
		gestureMap.add(identifier, self.cls.__module__, self.cls.__name__, name)
		self._save()

	def unassign(self, name):
		removed = self.keys(name)
		for identifier in removed:
			inputCore.manager.userGestureMap.remove(identifier, self.cls.__module__, self.cls.__name__, name)
		if removed:
			self._save()
		return removed


class ShortcutsDialog(wx.Dialog):
	"""The commands, each with its key: Enter assigns a key, captured as NVDA's Input Gestures dialog does."""

	def __init__(self, parent, title, commands, shortcuts):
		super().__init__(parent, title=title)
		self.commands = commands   # [(name, label)]
		self.shortcuts = shortcuts
		self.capturing = None
		sizer = wx.BoxSizer(wx.VERTICAL)
		# Translators: Label of the list of commands of the shortcuts window.
		sizer.Add(wx.StaticText(self, label=_('Co&mmands (Enter to assign a key):')), 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
		self.list = wx.ListBox(self, style=wx.LB_SINGLE)
		sizer.Add(self.list, 1, wx.ALL | wx.EXPAND, 10)
		buttons = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: A button of the shortcuts window.
		assign_button = wx.Button(self, label=_('Assi&gn a key'))
		# Translators: A button of the shortcuts window.
		remove_button = wx.Button(self, label=_('&Remove the key'))
		close = wx.Button(self, wx.ID_CLOSE, label=_('&Close'))
		for button in (assign_button, remove_button, close):
			buttons.Add(button, 0, wx.RIGHT, 10)
		sizer.Add(buttons, 0, wx.ALL, 10)
		self.SetSizer(sizer)
		self.refresh(0)
		self.Fit()
		assign_button.Bind(wx.EVT_BUTTON, lambda event: self.start_capture())
		remove_button.Bind(wx.EVT_BUTTON, lambda event: self.remove())
		close.Bind(wx.EVT_BUTTON, lambda event: self.Close())
		self.list.Bind(wx.EVT_LISTBOX_DCLICK, lambda event: self.start_capture())
		self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
		self.Bind(wx.EVT_CLOSE, self.on_close)
		self.list.SetFocus()

	def refresh(self, selection=None):
		if selection is None:
			selection = max(0, self.list.GetSelection())
		entries = []
		for name, label in self.commands:
			found = [key_name(identifier) for identifier in self.shortcuts.keys(name)]
			# Translators: Between two keys of the same command.
			keys_text = _(' or ').join(found) if found else _('no key')
			# Translators: An entry of the list of commands, as "Sound on or off: print screen".
			entries.append(_('{action}: {key}').format(action=label, key=keys_text))
		self.list.Set(entries)
		self.list.SetSelection(selection)

	def selected(self):
		index = self.list.GetSelection()
		return self.commands[index] if index != wx.NOT_FOUND else None

	def start_capture(self):
		command = self.selected()
		if command is None:
			return
		self.capturing = command
		# Translators: Said when the add-on waits for the key to assign.
		ui.message(_('Press the key for {action}, or Escape to cancel.').format(action=command[1]))
		inputCore.manager._captureFunc = self._captor

	def stop_capture(self):
		self.capturing = None
		if inputCore.manager._captureFunc == self._captor:
			inputCore.manager._captureFunc = None

	def _captor(self, gesture):
		if gesture.isModifier:
			return False
		if not isinstance(gesture, keyboardHandler.KeyboardInputGesture):
			# Translators: Said when something else than a keyboard key is pressed while waiting for a key.
			wx.CallAfter(ui.message, _('Press a key of the keyboard.'))
			return False
		identifiers = gesture.normalizedIdentifiers
		identifier = next((item for item in identifiers if item.startswith('kb:')), identifiers[-1])
		if identifier == 'kb:escape':
			self.stop_capture()
			# Translators: Said when the assignment of a key is cancelled.
			wx.CallAfter(ui.message, _('Cancelled.'))
			return False
		if is_typing_key(identifier):
			# Translators: Said when the key pressed is needed to type or move around; the add-on keeps waiting.
			wx.CallAfter(ui.message, _('{key} is needed to type or to move around. Press another key, '
				'or a combination with Ctrl or Alt.').format(key=key_name(identifier)))
			return False
		try:
			script = scriptHandler.findScript(gesture)
		except Exception:
			script = None
		command = self.capturing
		self.stop_capture()
		wx.CallAfter(self.captured, command, identifier, script)
		return False

	def captured(self, command, identifier, script):
		name, label = command
		key = key_name(identifier)
		if identifier in self.shortcuts.keys(name):
			# Translators: Said when the key pressed is already the one of this command.
			ui.message(_('{key} is already the key of {action}.').format(key=key, action=label))
			return
		if script is not None and not isinstance(getattr(script, '__self__', None), self.shortcuts.cls):
			used_for = (script.__doc__ or script.__name__.replace('script_', '')).strip()
			# Translators: Asked before taking a key that already does something else.
			question = _('{key} is already used for: {command}\n\nUse it for {action} instead?').format(
				key=key, command=used_for, action=label)
			if gui.messageBox(question, self.GetTitle(), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING, self) != wx.YES:
				ui.message(_('Cancelled.'))
				return
		self.shortcuts.assign(name, identifier, [item for item, text in self.commands])
		self.refresh()
		# Translators: Said when a key has been assigned.
		ui.message(_('{key} now does: {action}.').format(key=key, action=label))

	def remove(self):
		command = self.selected()
		if command is None:
			return
		if self.shortcuts.unassign(command[0]):
			self.refresh()
			# Translators: Said when the key of a command has been removed.
			ui.message(_('Key removed from {action}.').format(action=command[1]))
		else:
			# Translators: Said when the command has no key to remove.
			ui.message(_('{action} has no key.').format(action=command[1]))

	def on_key(self, event):
		key = event.GetKeyCode()
		focus_on_list = self.FindFocus() is self.list and not event.HasAnyModifiers()
		if key == wx.WXK_ESCAPE and not event.HasAnyModifiers():
			self.Close()
		elif key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and focus_on_list:
			self.start_capture()
		elif key in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE) and focus_on_list:
			self.remove()
		else:
			event.Skip()

	def on_close(self, event):
		self.stop_capture()
		if self.IsModal():
			self.EndModal(wx.ID_CLOSE)
		else:
			self.Destroy()
