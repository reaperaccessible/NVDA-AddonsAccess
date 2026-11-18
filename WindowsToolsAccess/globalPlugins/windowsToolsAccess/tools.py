# WindowsToolsAccess: the tools, adapted from AbsoluteWindows by Chai Chaimee and Peera Pilarit (GPL).
# Every tool is a function without argument; what it says goes through say(), set by the plugin.
import ctypes
import os
import subprocess
import threading
import time

import wx
import addonHandler
import core
import gui
from logHandler import log

from . import cmd_tools
from . import kill_dialog
from . import utils

addonHandler.initTranslation()

CONFIG_DIR = ''


def say(text):
	"""Replaced by the plugin (it speaks through NVDA)."""


def _failed(text=None):
	log.error('WindowsToolsAccess: a tool failed', exc_info=True)
	# Translators: Said when a tool could not be used.
	say(text or _('This could not be done.'))


def background(work, done):
	"""Run work() in the background, then done(result) in NVDA's main thread."""
	def run():
		try:
			result = work()
		except Exception:
			log.error('WindowsToolsAccess: a background task failed', exc_info=True)
			result = None
		wx.CallAfter(done, result)
	threading.Thread(target=run, daemon=True, name='WindowsToolsAccess').start()


def _shell_open(target, parameters=None):
	if ctypes.windll.shell32.ShellExecuteW(None, 'open', target, parameters, None, 1) <= 32:
		raise OSError('cannot open ' + target)


def _system_file(name):
	root = os.environ.get('SystemRoot', 'C:\\Windows')
	native = os.path.join(root, 'Sysnative', name)
	path = native if os.path.exists(native) else os.path.join(root, 'System32', name)
	if not os.path.exists(path):
		raise FileNotFoundError(path)
	return path


def opener(target, parameters=None, system=False):
	"""A tool that opens a Windows window."""
	def tool():
		try:
			_shell_open(_system_file(target) if system else target, parameters)
		except Exception:
			_failed()
	return tool


# --- long tasks: hidden, in the background, NVDA tells their start and their end
def long_task(function, name):
	"""name: what the task is called in what NVDA says ("DISM")."""
	def tool():
		# Translators: Said when a long task asks for Windows' permission. {name} is the task.
		say(_('{name}: accept the permission prompt of Windows.').format(name=name()))

		def ended(report):
			wx.CallAfter(finished, report)

		def finished(report):
			if report and os.path.isfile(report):
				try:
					os.startfile(report)
					# Translators: Said when a long task has ended and its report is opened.
					return say(_('{name}: finished. The report is open.').format(name=name()))
				except Exception:
					log.error('WindowsToolsAccess: cannot open the report', exc_info=True)
			# Translators: Said when a long task has ended.
			say(_('{name}: finished.').format(name=name()))

		def started(result):
			ok, error = result if result else (False, None)
			if ok:
				# Translators: Said when a long task has started. {name} is the task.
				say(_('{name}: running in the background. NVDA will tell you when it is finished.').format(name=name()))
			else:
				_failed(error)
		background(lambda: function(CONFIG_DIR, on_done=ended), started)
	return tool


# --- 1. quick tools
def system_tray():
	if not utils.open_system_tray():
		return _failed()

	def expand():
		user32 = ctypes.windll.user32
		user32.keybd_event(0x20, 0, 0, 0)
		user32.keybd_event(0x20, 0, 0x0002, 0)
	core.callLater(200, expand)


_internet = {'adapter': None, 'connected': None, 'time': 0.0}


def internet_connected():
	"""Kept for 5 seconds: checking takes time."""
	if _internet['connected'] is None or time.monotonic() - _internet['time'] > 5:
		_internet['connected'] = bool(utils.isInternetConnected())
		_internet['time'] = time.monotonic()
	return _internet['connected']


def internet_label():
	"""Without waiting: the state is checked in the background (see check_internet)."""
	if _internet['connected'] is None:
		return _('Internet on or off')
	return _('Disconnect Internet') if _internet['connected'] else _('Connect Internet')


def check_internet(done):
	_internet['connected'] = None
	background(internet_connected, lambda result: done())


def toggle_internet():
	def work():
		if internet_connected():
			adapter = utils.get_active_adapter_name()
			if not adapter:
				# Translators: Said when no network card is in use.
				return _('No active network adapter found.')
			if not utils.disable_adapter(adapter):
				return _('Failed to disconnect internet.')
			_internet['adapter'] = adapter
			_internet['connected'], _internet['time'] = False, time.monotonic()
			return _('Internet disconnected.')
		adapter = _internet['adapter'] or utils.get_first_disabled_adapter()
		if not adapter:
			return _('No disabled network adapter found.')
		if not utils.enable_adapter(adapter):
			return _('Failed to connect internet.')
		_internet['connected'], _internet['time'] = True, time.monotonic()
		return _('Internet connected.')
	background(work, lambda text: say(text or _('This could not be done.')))


def toggle_bluetooth():
	background(utils.toggle_bluetooth, lambda result: say(result[1] if result else _('This could not be done.')))


def wifi_password():
	def done(result):
		ssid, password = result if result else (None, None)
		if not ssid:
			return say(_('No Wi-Fi network is currently connected.'))
		if not password:
			return say(_("The Wi-Fi network '{ssid}' is open (no password).").format(ssid=ssid))
		utils.copy_to_clipboard(password)
		say(_('Wi-Fi password for {ssid} copied to clipboard.').format(ssid=ssid))
	background(utils.get_current_wifi_password, done)


def eject_usb():
	drives = utils.get_usb_drives()
	if not drives:
		return say(_('No USB drives found.'))
	if len(drives) == 1:
		drive = drives[0]
	else:
		# Translators: Title and question of the window choosing the USB drive to remove.
		dialog = wx.SingleChoiceDialog(gui.mainFrame, _('Which drive do you want to remove?'), _('Safely Remove USB'),
			['{letter} {name}'.format(letter=item['letter'], name=item['name']) for item in drives])
		try:
			if dialog.ShowModal() != wx.ID_OK:
				return
			drive = drives[dialog.GetSelection()]
		finally:
			dialog.Destroy()

	def done(ok):
		if ok:
			say(_('USB drive {drive} ejected safely.').format(drive=drive['letter']))
		else:
			say(_('Failed to eject {drive}.').format(drive=drive['letter']))
	background(lambda: utils.eject_usb(drive['letter']), done)


def kill_not_responding():
	kill_dialog.show_kill_dialog()


def restart_explorer():
	say(_('Restarting Windows Explorer, please wait...'))
	background(utils.restart_explorer,
		lambda ok: say(_('Windows Explorer has been restarted.') if ok else _('Failed to restart Windows Explorer.')))


def empty_recycle_bin():
	background(utils.empty_recycle_bin, lambda result: say(result[1] if result else _('This could not be done.')))


# --- 2. cleaning
def clean_temp():
	say(_('Cleaning temporary files, please wait...'))

	def done(result):
		ok, count = result if result else (False, 0)
		say(_('Temporary files cleaned: {count} items removed. Files in use or changed in the last day are kept.').format(count=count) if ok else _('Failed to clean temporary files.'))
	background(utils.cleanTempFolders, done)


def clean_run_history():
	def done(result):
		ok, count = result if result else (False, 0)
		say(_('Windows Run history cleared. {count} items removed.').format(count=count) if ok else _('Failed to clear Run history.'))
	background(utils.cleanRunHistory, done)




def disk_cleanup():
	try:
		try:
			_shell_open('cleanmgr.exe')
		except OSError:
			_shell_open('cleanmgr.exe', '/sagerun:1')
	except Exception:
		_failed()


# --- 6. advanced
def toggle_uac():
	def work():
		return utils.disableUAC() if utils.isUACEnabled() else utils.enableUAC()

	def done(ok):
		if ok:
			say(_('User Account Control changed. Restart Windows for the change to take effect.'))
		else:
			say(_('Failed to toggle UAC.'))
	background(work, done)


def toggle_search_web_results():
	ok, disabled = utils.toggleSearchWebResults()
	if not ok:
		return say(_('Failed to toggle search web results.'))
	if disabled:
		say(_('Search web results disabled. Restarting Explorer to apply changes...'))
	else:
		say(_('Search web results enabled. Restarting Explorer to apply changes...'))
	background(utils.restart_explorer, lambda ok: None)


def restart_to_firmware():
	# Translators: Question asked before restarting the computer into its BIOS or UEFI settings.
	question = _('The computer will restart at once into its BIOS or UEFI settings. Save your work first. Restart now?')
	if gui.messageBox(question, _('Restart to BIOS/UEFI Settings'), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING) != wx.YES:
		return
	if not utils.boot_to_uefi_firmware():
		say(_('Failed to restart to BIOS. Your system may not support this feature via Windows.'))


def backup_open_with():
	say(_('Backing up Open With associations, please wait...'))

	def done(result):
		ok, data = result if result else (False, None)
		if ok:
			say(_('Open With associations backed up successfully. Note: this restores reliably only on this same Windows installation and user account, not after a Windows reinstall.'))
		else:
			_failed(data)
	background(lambda: cmd_tools.run_backup_open_with(CONFIG_DIR), done)


def restore_open_with():
	backups = utils.list_open_with_backups(CONFIG_DIR)
	if not backups:
		return say(_('No Open With backups found'))
	# Translators: Title and question of the window choosing the backup of Open With to restore.
	dialog = wx.SingleChoiceDialog(gui.mainFrame, _('Which backup do you want to restore?'), _('Restore Open With'),
		[name for name, path in backups])
	try:
		if dialog.ShowModal() != wx.ID_OK:
			return
		path = backups[dialog.GetSelection()][1]
	finally:
		dialog.Destroy()
	say(_('Restoring Open With associations, please wait...'))

	def done(result):
		ok, error = result if result else (False, None)
		if ok:
			say(_('Open With associations restored. Restart Windows Explorer or sign out and back in for the change to take effect.'))
		else:
			_failed(error)
	background(lambda: cmd_tools.run_restore_open_with(CONFIG_DIR, path), done)


def tools():
	"""{name of the command: (label in the window, function)}, in the order of the window."""
	return {
		# 1. Quick tools
		# Translators: A tool in the window (and a command).
		'systemTray': (_('System Tray'), system_tray),
		'toggleInternet': (internet_label, toggle_internet),
		'toggleBluetooth': (_('Bluetooth on or off'), toggle_bluetooth),
		'wifiPassword': (_('Copy the Wi-Fi password'), wifi_password),
		'ejectUsb': (_('Safely Remove USB'), eject_usb),
		'killNotResponding': (_('Kill Not Responding Apps'), kill_not_responding),
		'restartExplorer': (_('Restart Windows Explorer'), restart_explorer),
		'emptyRecycleBin': (_('Empty Recycle Bin'), empty_recycle_bin),
		# 2. Cleaning
		'cleanTemp': (_('Clean Temp'), clean_temp),
		'cleanRunHistory': (_('Clean Windows Run History'), clean_run_history),
		'diskCleanup': (_('Disk Cleanup'), disk_cleanup),
		'optimizeDrives': (_('Optimize and defragment drive'), opener('dfrgui.exe', system=True)),
		# 5. Windows panels
		'controlPanel': (_('Control Panel'), opener('control.exe')),
		'programsAndFeatures': (_('Programs and Features'), opener('control.exe', 'appwiz.cpl')),
		'folderOptions': (_('Folder Options'), opener('control.exe', '/name Microsoft.FolderOptions')),
		'regedit': (_('Regedit'), opener('regedit.exe')),
		'windowsDefender': (_('Windows Defender'), opener('windowsdefender://Threatsettings')),
		'malwareRemovalTool': (_('Microsoft Windows Malicious Software Removal Tool'), opener('MRT.exe', system=True)),
		'openServices': (_('Open Services'), opener('services.msc')),
		# 6. Advanced
		'toggleUac': (lambda: _('Disable User Account Control') if utils.isUACEnabled() else _('Enable User Account Control'), toggle_uac),
		'toggleSearchWebResults': (lambda: _('Enable Search Web Results') if utils.isSearchWebResultsDisabled() else _('Disable Search Web Results'),
			toggle_search_web_results),
		'restartToFirmware': (_('Restart to BIOS/UEFI Settings'), restart_to_firmware),
		'dism': (_('Run DISM'), long_task(cmd_tools.run_dism, lambda: 'DISM')),
		'sfc': (_('Run SFC Scannow'), long_task(cmd_tools.run_sfc_scannow, lambda: 'SFC')),
		'chkdsk': (_('Run Chkdsk on C:'), long_task(cmd_tools.run_chkdsk, lambda: 'Chkdsk')),
		'diskStatus': (_('Check Disk Status'), long_task(cmd_tools.run_disk_status, lambda: _('Check Disk Status'))),
		'batteryReport': (_('Power Diagnostic (Battery Report)'), long_task(cmd_tools.run_power_diagnostic, lambda: _('Power Diagnostic (Battery Report)'))),
		'defenderFullScan': (_('Run Windows Defender Full Scan'), long_task(cmd_tools.run_defender_full_scan, lambda: _('Run Windows Defender Full Scan'))),
		'defenderFullScanLow': (_('Windows Defender Full Scan Low'), long_task(cmd_tools.run_defender_full_scan_low, lambda: _('Windows Defender Full Scan Low'))),
		'backupOpenWith': (_('Backup Open With'), backup_open_with),
		'restoreOpenWith': (_('Restore Open With'), restore_open_with),
		'environmentVariables': (_('Environment Variables'), opener('rundll32.exe', 'sysdm.cpl,EditEnvironmentVariables')),
	}


# Which tools each tab shows; the third and fourth tabs are the startup manager and the services.
TAB_TOOLS = {
	0: ['systemTray', 'toggleInternet', 'toggleBluetooth', 'wifiPassword', 'ejectUsb', 'killNotResponding', 'restartExplorer', 'emptyRecycleBin'],
	1: ['cleanTemp', 'cleanRunHistory', 'diskCleanup', 'optimizeDrives'],
	4: ['controlPanel', 'programsAndFeatures', 'folderOptions', 'regedit', 'windowsDefender', 'malwareRemovalTool', 'openServices'],
	5: ['toggleUac', 'toggleSearchWebResults', 'restartToFirmware', 'dism', 'sfc', 'chkdsk', 'diskStatus', 'batteryReport',
		'defenderFullScan', 'defenderFullScanLow', 'backupOpenWith', 'restoreOpenWith', 'environmentVariables'],
}


def label(entry):
	text = entry[0]
	return text() if callable(text) else text


def command_label(name, entry):
	"""The label of a command in the shortcuts window and Input Gestures: never one that changes."""
	return {
		# Translators: A command whose label in the window changes with the state.
		'toggleInternet': _('Internet on or off'),
		'toggleUac': _('User Account Control on or off'),
		'toggleSearchWebResults': _('Search web results on or off'),
	}.get(name) or label(entry)
