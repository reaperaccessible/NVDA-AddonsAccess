# WindowsToolsAccess: Windows maintenance tools in one window with tabs.
# Based on AbsoluteWindows by Chai Chaimee and Peera Pilarit (GPL).
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

from . import tools
from . import utils
from .shortcuts import Shortcuts, ShortcutsDialog
from .updater import Updater

addonHandler.initTranslation()

# The add-on's own name: never translated.
ADDON_NAME = 'WindowsToolsAccess'
CONFIG_SECTION = 'windowsToolsAccess'
CONFIG_SPEC = {
	'lastTab': 'integer(default=0, min=0, max=5)',
	'checkForUpdates': 'boolean(default=true)',
}
# After a change made in the window, its list line is updated and NVDA would read it again:
# for this long, only the add-on's own answer is spoken.
QUIET_AFTER_CHANGE = 0.4
STARTUP_TAB, SERVICES_TAB = 2, 3


def settings():
	return config.conf[CONFIG_SECTION]


_state = {'quietUntil': 0.0, 'ownSpeech': False}


def say(text):
	"""Speak one of the add-on's answers: it gets through the quiet period."""
	if not text:
		return
	_state['ownSpeech'] = True
	try:
		ui.message(text)
	finally:
		_state['ownSpeech'] = False
	# A tool has done something: the labels that tell a state (Internet, UAC…) follow it.
	if _window:
		wx.CallAfter(lambda: _window and _window.refresh())


def _quiet_filter(speechSequence, *args, **kwargs):
	if _state['ownSpeech'] or time.monotonic() >= _state['quietUntil']:
		return speechSequence
	return []


def quiet():
	_state['quietUntil'] = time.monotonic() + QUIET_AFTER_CHANGE


tools.say = say


def tab_titles():
	return [
		# Translators: Title of a tab of the window.
		_('Quick tools'),
		_('Cleaning'),
		_('Startup'),
		_('Services'),
		_('Windows panels'),
		_('Advanced'),
	]


def list_labels():
	"""Label of the list of each tab of tools: the name of its tab, so that each list is told apart."""
	return {
		# Translators: Label of the list of a tab of the window.
		0: _('&Quick tools (Enter to run):'),
		1: _('C&leaning (Enter to run):'),
		4: _('Windows &panels (Enter to open):'),
		5: _('&Advanced (Enter to run):'),
	}


def commands():
	"""(name of the command, label in the shortcuts window and description in NVDA's Input Gestures dialog)."""
	found = [(name, tools.command_label(name, entry)) for name, entry in tools.tools().items()]
	return found + [
		# Translators: A command of the add-on, opening the window on one of its tabs.
		('startupManager', _('Startup Manager')),
		('manageServices', _('Manage Services')),
	]


class ToolsPage(wx.Panel):
	"""A tab showing tools: Enter runs the selected one."""

	def __init__(self, parent, window, names, label):
		super().__init__(parent)
		self.window = window
		self.names = names
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(wx.StaticText(self, label=label), 0, wx.ALL, 5)
		self.list = wx.ListBox(self, style=wx.LB_SINGLE)
		sizer.Add(self.list, 1, wx.ALL | wx.EXPAND, 5)
		self.SetSizer(sizer)
		self.list.Bind(wx.EVT_LISTBOX_DCLICK, lambda event: self.run())
		self.refresh()
		self.list.SetSelection(0)

	def refresh(self):
		every = tools.tools()
		labels = [tools.label(every[name]) for name in self.names]
		selection = max(self.list.GetSelection(), 0)
		if labels != self.list.GetItems():
			if self.list.GetCount():
				quiet()
			self.list.Set(labels)
			self.list.SetSelection(min(selection, len(labels) - 1))

	def run(self):
		index = self.list.GetSelection()
		if index >= 0:
			self.window.plugin.run_command(self.names[index])

	def on_enter(self):
		self.run()


class StartupPage(wx.Panel):
	"""The programs started with Windows: Enter enables or disables the selected one."""

	def __init__(self, parent, window):
		super().__init__(parent)
		self.window = window
		self.items = []
		self.backupDir = os.path.join(tools.CONFIG_DIR, 'StartupBackup')
		sizer = wx.BoxSizer(wx.VERTICAL)
		# Translators: Label of the list of programs started with Windows.
		sizer.Add(wx.StaticText(self, label=_('&Programs started with Windows (Enter to enable or disable):')), 0, wx.ALL, 5)
		self.list = wx.ListBox(self, style=wx.LB_SINGLE)
		sizer.Add(self.list, 1, wx.ALL | wx.EXPAND, 5)
		self.SetSizer(sizer)
		self.list.Bind(wx.EVT_LISTBOX_DCLICK, lambda event: self.on_enter())
		self.refresh()

	def refresh(self):
		try:
			self.items = utils.get_startup_items()
		except Exception:
			log.error('WindowsToolsAccess: cannot read the startup programs', exc_info=True)
			self.items = []
		selection = max(self.list.GetSelection(), 0)
		# Translators: A program started with Windows: its name, its state (enabled, disabled) and where it is set.
		labels = [_('{name}, {status}, {type}').format(name=item['name'], status=item['status'], type=item['type']) for item in self.items]
		if not labels:
			# Translators: Shown when no program starts with Windows.
			labels = [_('No program')]
		if self.list.GetCount():
			quiet()
		self.list.Set(labels)
		self.list.SetSelection(min(selection, len(labels) - 1))

	def on_enter(self):
		index = self.list.GetSelection()
		if not (0 <= index < len(self.items)):
			return
		item = self.items[index]
		try:
			os.makedirs(self.backupDir, exist_ok=True)
			enabled = item['status'] == _('Enabled')
			if enabled:
				ok = utils.disable_startup_item(item, self.backupDir)
			else:
				ok = utils.enable_startup_item(item, self.backupDir)
		except Exception:
			log.error('WindowsToolsAccess: cannot change a startup program', exc_info=True)
			ok = False
		if not ok:
			return say(_('This could not be done.'))
		self.refresh()
		# Translators: Said when a program started with Windows is disabled, or enabled.
		say((_('{name} disabled') if enabled else _('{name} enabled')).format(name=item['name']))


class ServicesPage(wx.Panel):
	"""Windows services: Enter disables or enables the selected one; the buttons do the rest."""

	def __init__(self, parent, window):
		super().__init__(parent)
		self.window = window
		self.services = []
		self.busy = False
		self.loaded = False
		self.first = True
		sizer = wx.BoxSizer(wx.VERTICAL)
		# Translators: Label of the list of Windows services.
		sizer.Add(wx.StaticText(self, label=_('&Services (Enter to disable or enable):')), 0, wx.ALL, 5)
		self.list = wx.ListBox(self, style=wx.LB_SINGLE)
		sizer.Add(self.list, 1, wx.ALL | wx.EXPAND, 5)
		buttons = wx.BoxSizer(wx.HORIZONTAL)
		self.buttons = []
		for text, handler in (
			# Translators: A button of the Services tab.
			(_('&Details'), self.details),
			(_('Disable &all safe services'),self.disable_all_safe),
			(_('&Enable all'), self.enable_all),
			(_('&Refresh'), self.refresh),
		):
			button = wx.Button(self, label=text)
			button.Bind(wx.EVT_BUTTON, lambda event, handler=handler: handler())
			buttons.Add(button, 0, wx.ALL, 5)
			self.buttons.append(button)
		sizer.Add(buttons)
		self.SetSizer(sizer)
		self.list.Bind(wx.EVT_LISTBOX_DCLICK, lambda event: self.on_enter())
		self.list.Set([_('Loading services, please wait...')])

	def shown(self):
		if not self.loaded:
			self.refresh()

	def set_busy(self, busy):
		self.busy = busy
		for button in self.buttons:
			button.Enable(not busy)

	def refresh(self):
		if self.busy:
			return
		self.loaded = True
		self.set_busy(True)
		tools.background(utils.get_service_states, self.loaded_services)

	def loaded_services(self, services):
		if not self:
			return
		self.set_busy(False)
		self.services = services or []
		selection = max(self.list.GetSelection(), 0)
		labels = []
		for service in self.services:
			# Translators: A Windows service: its name, startup type, state, and whether it is safe to disable.
			safety = _('Safe') if service['safety'] == 'safe' else _('Caution')
			labels.append(', '.join(part for part in (service['display_name'], service['start_type'], service['status'], safety) if part))
		quiet()
		self.list.Set(labels or [_('This could not be done.')])
		self.list.SetSelection(min(selection, len(self.list.GetItems()) - 1))
		if self.first:
			self.first = False
			say(_('{count} services listed.').format(count=len(self.services)))

	def selected(self):
		index = self.list.GetSelection()
		return self.services[index] if 0 <= index < len(self.services) else None

	def details(self):
		service = self.selected()
		if service is None:
			return
		if service['start_type_key'] == 'missing':
			text = _('{name} is not installed on this system.').format(name=service['display_name'])
		else:
			lines = [
				_('Service name: {name}').format(name=service['name']),
				_('Startup type: {value}').format(value=service['start_type']),
				_('Status: {value}').format(value=service['status']),
			]
			if service['note']:
				lines.append(_('Caution: {note}').format(note=service['note']))
			lines.append(service['description'] or _('No description available.'))
			text = '\n'.join(lines)
		gui.messageBox(text, service['display_name'], wx.OK | wx.ICON_INFORMATION, self.window)

	def on_enter(self):
		service = self.selected()
		if service is None or self.busy:
			return
		if service['start_type_key'] == 'missing':
			return say(_('{name} is not installed on this system.').format(name=service['display_name']))
		action = 'enable' if service['start_type_key'] == 'disabled' else 'disable'
		if action == 'disable' and service['safety'] != 'safe':
			# Translators: Question asked before disabling a service marked Caution.
			question = _('{name} is marked Caution: {note}\n\nDisable it anyway?').format(name=service['display_name'], note=service['note'])
			if gui.messageBox(question, _('Confirm'), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING, self.window) != wx.YES:
				return
		self.apply([service], action)

	def disable_all_safe(self):
		targets = [service for service in self.services if service['safety'] == 'safe' and service['start_type_key'] not in ('missing', 'disabled')]
		if not targets or self.busy:
			return say(_('Nothing to change.'))
		question = _('Disable {count} services rated Safe? Services marked Caution are not included. A Windows permission prompt will appear.').format(count=len(targets))
		if gui.messageBox(question, _('Confirm'), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING, self.window) == wx.YES:
			self.apply(targets, 'disable')

	def enable_all(self):
		targets = [service for service in self.services if service['start_type_key'] == 'disabled']
		if not targets or self.busy:
			return say(_('Nothing to change.'))
		self.apply(targets, 'enable')

	def apply(self, targets, action):
		self.set_busy(True)
		say(_('Applying changes. A Windows permission prompt may appear.'))
		tools.background(lambda: utils.apply_service_action(tools.CONFIG_DIR, targets, action), self.applied)

	def applied(self, result):
		if not self:
			return
		self.set_busy(False)
		ok, payload = result if result else (False, _('This could not be done.'))
		if not ok:
			return say(payload)
		failed = [name for name, success in payload.items() if not success]
		changed = len(payload) - len(failed)
		if failed:
			say(_('{changed} changed, {failed} failed: {names}').format(changed=changed, failed=len(failed), names=', '.join(failed)))
		elif changed == 0:
			say(_('Nothing to change.'))
		else:
			say(_('{changed} services changed.').format(changed=changed))
		self.refresh()


class ToolsWindow(wx.Dialog):

	def __init__(self, parent, plugin, tab):
		# The add-on's own name: never translated.
		super().__init__(parent, title=ADDON_NAME, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		self.plugin = plugin
		sizer = wx.BoxSizer(wx.VERTICAL)
		self.notebook = wx.Notebook(self)
		self.pages = []
		for index, title in enumerate(tab_titles()):
			if index == STARTUP_TAB:
				page = StartupPage(self.notebook, self)
			elif index == SERVICES_TAB:
				page = ServicesPage(self.notebook, self)
			else:
				page = ToolsPage(self.notebook, self, tools.TAB_TOOLS[index], list_labels()[index])
			self.notebook.AddPage(page, title)
			self.pages.append(page)
		sizer.Add(self.notebook, 1, wx.ALL | wx.EXPAND, 5)
		buttons = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: A button at the bottom of the window.
		shortcutsButton = wx.Button(self, label=_('Assign &keys to the commands…'))
		shortcutsButton.Bind(wx.EVT_BUTTON, lambda event: self.plugin.show_shortcuts(self))
		closeButton = wx.Button(self, wx.ID_CLOSE, label=_('&Close'))
		closeButton.Bind(wx.EVT_BUTTON, lambda event: self.Close())
		buttons.Add(shortcutsButton, 0, wx.ALL, 5)
		buttons.Add(closeButton, 0, wx.ALL, 5)
		sizer.Add(buttons, 0, wx.ALIGN_CENTER)
		self.SetSizer(sizer)
		self.SetSize((700, 500))
		self.SetEscapeId(wx.ID_CLOSE)
		self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
		self.Bind(wx.EVT_CLOSE, self.on_close)
		self.notebook.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self.on_tab)
		self.CentreOnScreen()
		self.select_tab(tab)
		tools.check_internet(lambda: self and self.pages[0].refresh())

	def select_tab(self, index):
		if self.notebook.GetSelection() != index:
			self.notebook.ChangeSelection(index)
			self.tab_changed(index)
		else:
			self.tab_changed(index)
		# The focus stays on the tabs: Tab goes into the tab.
		self.notebook.SetFocus()

	def tab_changed(self, index):
		settings()['lastTab'] = index
		page = self.pages[index]
		if hasattr(page, 'shown'):
			page.shown()

	def on_tab(self, event):
		event.Skip()
		index = event.GetSelection()
		if 0 <= index < len(self.pages):
			self.tab_changed(index)

	def refresh(self):
		"""After a tool ran: the labels that tell a state (Internet, UAC…) follow it."""
		for page in self.pages:
			if isinstance(page, ToolsPage):
				page.refresh()

	def on_key(self, event):
		key = event.GetKeyCode()
		control = event.GetModifiers() == wx.MOD_CONTROL
		if key == wx.WXK_ESCAPE and not event.HasAnyModifiers():
			self.Close()
		elif control and ord('1') <= key <= ord('6'):
			self.select_tab(key - ord('1'))
		elif key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and not event.HasAnyModifiers() and self.FindFocus() is self.current_page().list:
			self.current_page().on_enter()
		else:
			event.Skip()

	def current_page(self):
		return self.pages[self.notebook.GetSelection()]

	def on_close(self, event):
		global _window
		_window = None
		self.Destroy()


_window = None


class WindowsToolsAccessSettingsPanel(gui.settingsDialogs.SettingsPanel):
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
		tools.CONFIG_DIR = os.path.join(globalVars.appArgs.configPath, ADDON_NAME)
		self.updater = None
		self.menuItem = None
		try:
			from speech.extensions import filter_speechSequence
			filter_speechSequence.register(_quiet_filter)
		except Exception:
			log.error('WindowsToolsAccess: quiet period unavailable', exc_info=True)
		self.shortcuts = Shortcuts(GlobalPlugin)
		if globalVars.appArgs.secure:
			return
		try:
			os.makedirs(tools.CONFIG_DIR, exist_ok=True)
		except OSError:
			log.error('WindowsToolsAccess: cannot create the settings folder', exc_info=True)
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(WindowsToolsAccessSettingsPanel)
		try:
			toolsMenu = gui.mainFrame.sysTrayIcon.toolsMenu
			self.menuItem = toolsMenu.Append(wx.ID_ANY, ADDON_NAME + '…')
			gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, lambda event: self.show_window(), self.menuItem)
		except Exception:
			log.error('WindowsToolsAccess: cannot add the Tools menu entry', exc_info=True)
		self.updater = Updater(addonHandler.getCodeAddon())
		WindowsToolsAccessSettingsPanel.updater = self.updater
		if settings()['checkForUpdates']:
			self.updater.scheduleStartupCheck()

	def terminate(self, *args, **kwargs):
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
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(WindowsToolsAccessSettingsPanel)
		except ValueError:
			pass
		if self.menuItem is not None:
			try:
				gui.mainFrame.sysTrayIcon.toolsMenu.Remove(self.menuItem)
			except Exception:
				pass
		super().terminate(*args, **kwargs)

	# --- the window
	def show_window(self, tab=None):
		global _window
		if tab is None:
			tab = settings()['lastTab']
		if _window:
			_window.Raise()
			_window.select_tab(tab)
			return
		gui.mainFrame.prePopup()
		try:
			_window = ToolsWindow(gui.mainFrame, self, tab)
			_window.Show()
			_window.Raise()
			_window.notebook.SetFocus()
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
	script_openWindow.__doc__ = _('Opens the window of Windows tools.')

	def run_command(self, name):
		if globalVars.appArgs.secure:
			return
		if name == 'startupManager':
			return self.show_window(STARTUP_TAB)
		if name == 'manageServices':
			return self.show_window(SERVICES_TAB)
		if name == 'systemTray' and _window:
			# The notification area takes the keyboard: the window would be in its way.
			_window.Close()
		entry = tools.tools().get(name)
		if entry is None:
			return
		try:
			entry[1]()
		except Exception:
			tools._failed()

	__gestures = {
		'kb:NVDA+control+shift+w': 'openWindow',
	}


def _make_script(name, description):
	def script(self, gesture):
		wx.CallAfter(self.run_command, name)
	script.__name__ = 'script_' + name
	script.__doc__ = description
	return script


for _name, _description in commands():
	setattr(GlobalPlugin, 'script_' + _name, _make_script(_name, _description))
del _name, _description
