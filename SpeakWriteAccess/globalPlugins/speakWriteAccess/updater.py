# SpeakWriteAccess: update checker.
# Looks for a newer release of this add-on in a GitHub repository and installs it,
# without going through NVDA's Add-on Store.
#
# A release is recognised by its tag, "<add-on name>-<version>" (for example
# "SpeechHistoryAccess-2026.2"), and must carry one ".nvda-addon" file.

import json
import os
import re
import tempfile
import threading
import time
import urllib.request

import wx

import addonHandler
import core
import gui
import ui
from logHandler import log

addonHandler.initTranslation()

REPOSITORY = 'reaperaccessible/NVDA-AddonsAccess'
RELEASES_URL = f'https://api.github.com/repos/{REPOSITORY}/releases?per_page=100'
DOWNLOAD_PREFIX = f'https://github.com/{REPOSITORY}/releases/download/'
ADDON_EXTENSION = '.nvda-addon'
REQUEST_HEADERS = {'Accept': 'application/vnd.github+json', 'User-Agent': 'NVDA-AddonsAccess-updater'}
TIMEOUT = 20 # seconds
STARTUP_DELAY = 20000 # ms
RETRY_DELAY = 3000 # ms
PROGRESS_EVERY = 5.0 # seconds between two spoken percentages of a download
# Shared by every add-on using this checker, so that only one of them updates at a time.
SHARED_FLAG = '_addonsAccessUpdateInProgress'
# Names of the add-ons waiting for their turn, and whether an installed update needs a restart.
SHARED_WAITING = '_addonsAccessUpdateWaiting'
SHARED_RESTART = '_addonsAccessRestartNeeded'


def parseVersion(version):
	"""'2026.10.1' -> (2026, 10, 1). Anything that is not a dotted number gives None."""
	if not isinstance(version, str) or not re.fullmatch(r'\d+(\.\d+)*', version.strip()):
		return None
	return tuple(int(part) for part in version.strip().split('.'))


def isNewer(candidate, current):
	"""Compare two parsed versions, so that 2026.1 and 2026.1.0 are the same version."""
	length = max(len(candidate), len(current))
	pad = lambda version: version + (0,) * (length - len(version))
	return pad(candidate) > pad(current)


def findUpdate(releases, addonName, currentVersion):
	"""Return (version, downloadUrl) of the newest release above currentVersion, or None."""
	current = parseVersion(currentVersion)
	if current is None or not isinstance(releases, list):
		return None
	prefix = addonName + '-'
	best = None
	for release in releases:
		if not isinstance(release, dict) or release.get('draft') or release.get('prerelease'):
			continue
		tag = release.get('tag_name')
		if not isinstance(tag, str) or not tag.startswith(prefix):
			continue
		version = parseVersion(tag[len(prefix):])
		if version is None or not isNewer(version, current):
			continue
		url = next((
			asset.get('browser_download_url') for asset in release.get('assets') or []
			if isinstance(asset, dict) and str(asset.get('name', '')).endswith(ADDON_EXTENSION)
			and str(asset.get('browser_download_url', '')).startswith(DOWNLOAD_PREFIX)
		), None)
		if url and (best is None or isNewer(version, best[0])):
			best = (version, url, tag[len(prefix):].strip())
	return (best[2], best[1]) if best else None


class DownloadProgress:
	"""Decides when a download's progress is worth saying: every ten percent, a few seconds apart.

	A download that ends within a few seconds says nothing, and 100 % is never said:
	the message that follows the installation tells it better.
	"""

	def __init__(self, total, say, clock=time.monotonic, every=PROGRESS_EVERY):
		self.total, self.say, self.clock, self.every = total, say, clock, every
		self.bucket = 0
		self.last = clock()

	def update(self, done):
		if not self.total:
			return
		percent = min(99, int(done * 100 / self.total))
		now = self.clock()
		if percent // 10 > self.bucket and now - self.last >= self.every:
			self.bucket, self.last = percent // 10, now
			self.say(percent)


class Updater:
	def __init__(self, addon):
		self.name = addon.manifest['name']
		self.version = addon.manifest['version']
		self._busy = threading.Lock()
		self._timer = None

	def scheduleStartupCheck(self):
		self._timer = wx.CallLater(STARTUP_DELAY, self.check)

	def cancel(self):
		if self._timer is not None:
			self._timer.Stop()
			self._timer = None
		self._waiting().discard(self.name)

	def check(self, manual=False):
		"""Look for an update in the background. Only a manual check reports 'up to date' or errors."""
		if self._waitingForRestart():
			# The new version is already installed: offering it again would install it twice.
			if manual:
				# Translators: Message shown when the new version of the add-on is installed but NVDA has not restarted yet.
				self._inform(_('The update is already installed. Restart NVDA to finish.'), wx.ICON_INFORMATION)
			return
		if not self._busy.acquire(blocking=False):
			return
		if manual:
			# Translators: Message spoken when the user asks the add-on to look for an update.
			ui.message(_('Checking for updates...'))
		threading.Thread(target=self._checkWorker, args=(manual,), daemon=True, name=self.name + 'UpdateCheck').start()

	def _checkWorker(self, manual):
		try:
			request = urllib.request.Request(RELEASES_URL, headers=REQUEST_HEADERS)
			with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
				releases = json.load(response)
			update = findUpdate(releases, self.name, self.version)
		except Exception:
			log.debugWarning('%s: update check failed' % self.name, exc_info=True)
			self._busy.release()
			if manual:
				# Translators: Message shown when the add-on could not look for an update.
				wx.CallAfter(self._inform, _('Could not check for updates. Check your Internet connection.'), wx.ICON_ERROR)
			return
		if update is None:
			self._busy.release()
			if manual:
				# Translators: Message shown when no newer version of the add-on exists. {name} is the name of the add-on, {version} its version.
				wx.CallAfter(self._inform, _('{name} is up to date (version {version}).').format(name=self.name, version=self.version), wx.ICON_INFORMATION)
			return
		wx.CallAfter(self._offer, *update)

	def _waitingForRestart(self):
		"""True when a version of this add-on is installed or removed but NVDA has not restarted yet."""
		try:
			return any(
				addon.name == self.name and (addon.isPendingInstall or addon.isPendingRemove)
				for addon in addonHandler.getAvailableAddons()
			)
		except Exception:
			return False

	@staticmethod
	def _waiting():
		"""The add-ons that found an update while another one was being updated."""
		waiting = getattr(addonHandler, SHARED_WAITING, None)
		if waiting is None:
			waiting = set()
			setattr(addonHandler, SHARED_WAITING, waiting)
		return waiting

	def _finish(self, installed=False):
		"""End of an update attempt, on the main thread: give the turn to the next add-on.

		NVDA's restart is asked only once every waiting add-on has had its turn,
		so that several updates end with a single question.
		"""
		if installed:
			setattr(addonHandler, SHARED_RESTART, True)
		if getattr(addonHandler, SHARED_FLAG, None) == self.name:
			setattr(addonHandler, SHARED_FLAG, None)
		self._busy.release()
		if not getattr(addonHandler, SHARED_RESTART, False) or self._waiting():
			return
		# Keep the turn while asking, so that no other window opens over this question.
		setattr(addonHandler, SHARED_FLAG, self.name)
		setattr(addonHandler, SHARED_RESTART, False)
		try:
			# Translators: Question asked once the new version of the add-on is installed.
			message = _('The update is installed. NVDA must restart to finish. Restart now?')
			restart = gui.messageBox(message, self._title(), wx.YES_NO | wx.ICON_QUESTION) == wx.YES
		finally:
			setattr(addonHandler, SHARED_FLAG, None)
		if restart:
			core.restart()

	def _title(self):
		# Translators: Title of the add-on's update windows. {name} is the name of the add-on.
		return _('{name} update').format(name=self.name)

	def _inform(self, message, icon):
		gui.messageBox(message, self._title(), wx.OK | icon)

	def _offer(self, version, url):
		# Translators: Question asked when a newer version of the add-on exists. {name} is the name of the add-on, {new} the new version, {current} the installed one.
		message = _('{name} {new} is available. You have version {current}. Do you want to install it now?').format(
			name=self.name, new=version, current=self.version)
		if self._waitingForRestart():
			self._waiting().discard(self.name)
			self._busy.release()
			return
		if getattr(addonHandler, SHARED_FLAG, None):
			# Another add-on is being updated: keep this update and offer it once the other has finished.
			self._waiting().add(self.name)
			self._timer = wx.CallLater(RETRY_DELAY, self._offer, version, url)
			return
		self._waiting().discard(self.name)
		setattr(addonHandler, SHARED_FLAG, self.name)
		if gui.messageBox(message, self._title(), wx.YES_NO | wx.ICON_QUESTION) != wx.YES:
			self._finish()
			return
		# Translators: Message spoken while the new version of the add-on is being downloaded.
		ui.message(_('Downloading the update...'))
		threading.Thread(target=self._downloadWorker, args=(url,), daemon=True, name=self.name + 'UpdateDownload').start()

	def _downloadWorker(self, url):
		path = None
		try:
			if not url.startswith(DOWNLOAD_PREFIX):
				raise ValueError('Unexpected download address')
			handle, path = tempfile.mkstemp(prefix=self.name + '-', suffix=ADDON_EXTENSION)
			request = urllib.request.Request(url, headers={'User-Agent': REQUEST_HEADERS['User-Agent']})
			with os.fdopen(handle, 'wb') as out, urllib.request.urlopen(request, timeout=TIMEOUT) as response:
				if not response.geturl().startswith('https://'):
					raise ValueError('Unexpected redirection')
				progress = DownloadProgress(int(response.headers.get('Content-Length') or 0), self._sayProgress)
				done = 0
				while True:
					block = response.read(1 << 16)
					if not block:
						break
					out.write(block)
					done += len(block)
					progress.update(done)
			# Refuse a file that is not this add-on, whatever the release says.
			if addonHandler.AddonBundle(path).manifest['name'] != self.name:
				raise ValueError('The downloaded file is another add-on')
		except Exception:
			log.error('%s: update download failed' % self.name, exc_info=True)
			self._remove(path)
			wx.CallAfter(self._finish)
			# Translators: Message shown when the new version of the add-on could not be downloaded.
			wx.CallAfter(self._inform, _('The update could not be downloaded.'), wx.ICON_ERROR)
			return
		wx.CallAfter(self._install, path)

	def _sayProgress(self, percent):
		# Translators: Progress of the download of a new version. {percent} is a number from 10 to 99.
		wx.CallAfter(ui.message, _('Downloading the update: {percent}%').format(percent=percent))

	def _install(self, path):
		try:
			try:
				from addonStore.install import installAddon
			except ImportError:
				installAddon = self._legacyInstall
			installAddon(path)
		except Exception:
			log.error('%s: update installation failed' % self.name, exc_info=True)
			self._remove(path)
			# Translators: Message shown when the new version of the add-on could not be installed.
			self._inform(_('The update could not be installed.'), wx.ICON_ERROR)
			self._finish()
			return
		self._remove(path)
		self._finish(installed=True)

	def _legacyInstall(self, path):
		"""For versions of NVDA older than the Add-on Store (before 2023.2)."""
		bundle = addonHandler.AddonBundle(path)
		for addon in addonHandler.getAvailableAddons():
			if addon.name == self.name and not addon.isPendingRemove:
				addon.requestRemove()
		addonHandler.installAddonBundle(bundle)

	@staticmethod
	def _remove(path):
		try:
			if path:
				os.remove(path)
		except OSError:
			pass
