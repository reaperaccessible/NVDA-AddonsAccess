# -*- coding: utf-8 -*-
# BeMyAssistantAccess: the simple settings shown in NVDA's Settings, and the window of the advanced options,
# which holds the complete original settings panel unchanged.
import os

import wx

import addonHandler
import config as nvda_config
import gui
import ui
from logHandler import log

from .. import vision_config
from .. import plugin_state
from ..ai import local_ai
from ..ai.core import provider_key_name
from .settings import SettingsPanel

addonHandler.initTranslation()

SECTION = "BeMyAssistantAccess"
FREE_KEY_URL = "https://aistudio.google.com/apikey"
LOCAL = "local"


def _providers():
	return [
		# Translators: Name of the AI that runs entirely on the user's computer.
		(_("Local AI, on this computer"), LOCAL),
		# Translators: Name of the Google Gemini AI provider
		(_("Google Gemini"), "gemini"),
		# Translators: Name of the OpenAI provider
		(_("OpenAI"), "openai"),
		# Translators: Name of the Mistral AI provider
		(_("Mistral AI"), "mistral"),
		# Translators: Name of the Groq AI provider
		(_("Groq"), "groq"),
		# Translators: Name of the MiniMax AI provider
		(_("MiniMax"), "minimax"),
		# Translators: Option for a user-defined custom AI provider
		(_("Custom"), "custom"),
	]


def manager_message():
	# Translators: Explains how to get the local AI. The name of the product in the Manager is not translated.
	return _("To use the local AI, download and install it with the ReaperAccessible Installer Manager: choose the type "
			 "NVDA Add-on, then BeMyAssistantAccess, local AI. If you do not have the Manager on your computer, "
			 "activate the button Download the Manager.")


class ManagerDialog(wx.Dialog):
	"""Tells how to get the local AI, with a button that downloads the Manager."""

	def __init__(self, parent):
		# Translators: Title of the window that explains how to get the local AI.
		super().__init__(parent, title=_("Local AI"))
		sizer = wx.BoxSizer(wx.VERTICAL)
		text = wx.TextCtrl(self, value=manager_message(), style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_NO_VSCROLL, size=(500, 110))
		sizer.Add(text, 1, wx.EXPAND | wx.ALL, 10)
		buttons = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: Button that downloads the ReaperAccessible Installer Manager.
		download = wx.Button(self, label=_("&Download the Manager"))
		download.Bind(wx.EVT_BUTTON, self.onDownload)
		buttons.Add(download, 0, wx.ALL, 5)
		buttons.Add(wx.Button(self, wx.ID_CLOSE), 0, wx.ALL, 5)
		self.Bind(wx.EVT_BUTTON, lambda event: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
		self.SetEscapeId(wx.ID_CLOSE)
		sizer.Add(buttons, 0, wx.ALIGN_CENTER)
		self.SetSizerAndFit(sizer)
		text.SetFocus()
		self.CentreOnScreen()

	def onDownload(self, event):
		try:
			os.startfile(local_ai.MANAGER_URL)
		except Exception:
			log.debug("BeMyAssistantAccess: Manager download", exc_info=True)
		self.EndModal(wx.ID_OK)


class AdvancedSettingsDialog(wx.Dialog):
	"""Every option of the add-on, in the original tabs."""

	def __init__(self, parent):
		# Translators: Title of the window of the advanced options.
		super().__init__(parent, title=_("BeMyAssistantAccess: advanced options"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		sizer = wx.BoxSizer(wx.VERTICAL)
		self.keepChoices = [
			# Translators: Choice of how long the local AI stays loaded: 15 minutes after the last request.
			("15", _("15 minutes after the last request")),
			# Translators: Choice of how long the local AI stays loaded: 1 hour after the last request.
			("60", _("1 hour after the last request")),
			# Translators: Choice of how long the local AI stays loaded: from the start of NVDA, never stopped.
			("always", _("Always, from the start of NVDA")),
		]
		keepSizer = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: Label of the choice of how long the local AI stays loaded. A loaded AI answers at once but uses memory.
		keepSizer.Add(wx.StaticText(self, label=_("&Keep the local AI ready:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
		self.keep = wx.Choice(self, choices=[label for _code, label in self.keepChoices])
		current = nvda_config.conf[SECTION].get("local_ai_keep", "15")
		self.keep.SetSelection(next((i for i, (code, _label) in enumerate(self.keepChoices) if code == current), 0))
		keepSizer.Add(self.keep, 0)
		sizer.Add(keepSizer, 0, wx.ALL, 10)
		self.panel = SettingsPanel(self)
		sizer.Add(self.panel, 1, wx.EXPAND | wx.ALL, 5)
		sizer.Add(self.CreateSeparatedButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 5)
		self.SetSizerAndFit(sizer)
		self.Bind(wx.EVT_BUTTON, self.onOk, id=wx.ID_OK)
		self.Bind(wx.EVT_BUTTON, self.onCancel, id=wx.ID_CANCEL)
		self.CentreOnScreen()

	def onOk(self, event):
		if not self.panel.isValid():
			return
		self.panel.onSave()
		choice = self.keepChoices[self.keep.GetSelection()][0]
		nvda_config.conf[SECTION]["local_ai_keep"] = choice
		local_ai.set_keep(choice)
		if choice == "always" and nvda_config.conf[SECTION].get("local_ai", False) and local_ai.installed():
			local_ai.start_in_background()
		self.EndModal(wx.ID_OK)

	def onCancel(self, event):
		try:
			self.panel.onDiscard()
		except Exception:
			log.debug("BeMyAssistantAccess: discard of the advanced options", exc_info=True)
		self.EndModal(wx.ID_CANCEL)


class SimpleSettingsPanel(gui.settingsDialogs.SettingsPanel):
	title = vision_config.ADDON_NAME

	def makeSettings(self, settingsSizer):
		helper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		self.providers = _providers()
		self.keys = {}
		self.service = helper.addLabeledControl(
			# Translators: Label of the choice of the AI service in the simple settings.
			_("AI &service:"),
			wx.Choice,
			choices=[label for label, _code in self.providers],
		)
		self.service.Bind(wx.EVT_CHOICE, self.onServiceChange)
		# Translators: Button that explains how to install the local AI with the Manager.
		self.localButton = helper.addItem(wx.Button(self, label=_("&How to install the local AI...")))
		self.localButton.Bind(wx.EVT_BUTTON, self.onLocalButton)
		self.key = helper.addLabeledControl(
			# Translators: Label of the access key of an online AI service.
			_("&Access key of this service:"),
			wx.TextCtrl,
			style=wx.TE_PASSWORD,
		)
		# Translators: Button that opens the Google page where a free Gemini access key is created.
		self.freeKey = helper.addItem(wx.Button(self, label=_("Get a &free Gemini key...")))
		self.freeKey.Bind(wx.EVT_BUTTON, self.onFreeKey)
		self.language = helper.addLabeledControl(
			# Translators: Label of the language the AI answers in.
			_("&Language of the answers:"),
			wx.Choice,
			choices=vision_config.TARGET_NAMES,
		)
		# Translators: Checkbox of the automatic check for updates.
		self.checkUpdates = helper.addItem(wx.CheckBox(self, label=_("Check for &updates automatically")))
		# Translators: Button that checks for an update now.
		checkNow = helper.addItem(wx.Button(self, label=_("Check &now")))
		checkNow.Bind(wx.EVT_BUTTON, self.onCheckNow)
		# Translators: Button that opens every option of the add-on.
		advanced = helper.addItem(wx.Button(self, label=_("Ad&vanced options...")))
		advanced.Bind(wx.EVT_BUTTON, self.onAdvanced)
		self.load()

	def _code(self):
		index = self.service.GetSelection()
		return self.providers[index][1] if index != wx.NOT_FOUND else "gemini"

	def _online_codes(self):
		return [code for _label, code in self.providers if code != LOCAL]

	def load(self):
		conf = nvda_config.conf[SECTION]
		current = LOCAL if conf.get("local_ai", False) else conf["active_provider"]
		codes = [code for _label, code in self.providers]
		self.service.SetSelection(codes.index(current) if current in codes else 1)
		self.keys = {code: conf.get(provider_key_name(code), "") for code in self._online_codes()}
		self.key.SetValue(self.keys.get(self._code(), ""))
		language = conf["ai_response_language"]
		self.language.SetSelection(next((i for i, x in enumerate(vision_config.TARGET_LIST) if x[1] == language), 0))
		self.checkUpdates.SetValue(bool(conf["check_update_startup"]))
		self._previous = self._code()
		self.showService()

	def showService(self):
		local = self._code() == LOCAL
		self.key.Enable(not local)
		self.freeKey.Enable(self._code() == "gemini")
		self.localButton.Show(local and not local_ai.installed())
		self.Layout()

	def onServiceChange(self, event):
		if self._previous != LOCAL:
			self.keys[self._previous] = self.key.GetValue().strip()
		self._previous = self._code()
		self.key.SetValue(self.keys.get(self._code(), ""))
		self.showService()

	def onLocalButton(self, event):
		dialog = ManagerDialog(self)
		try:
			dialog.ShowModal()
		finally:
			dialog.Destroy()

	def onFreeKey(self, event):
		try:
			os.startfile(FREE_KEY_URL)
		except Exception:
			log.debug("BeMyAssistantAccess: free key page", exc_info=True)

	def onCheckNow(self, event):
		plugin = plugin_state.plugin_instance
		if plugin is not None and getattr(plugin, "updater", None) is not None:
			plugin.updater.check_for_updates(silent=False)

	def onAdvanced(self, event):
		self.save()
		dialog = AdvancedSettingsDialog(self)
		try:
			dialog.ShowModal()
		finally:
			dialog.Destroy()
		self.load()

	def save(self):
		conf = nvda_config.conf[SECTION]
		code = self._code()
		if code != LOCAL:
			self.keys[code] = self.key.GetValue().strip()
		for name, value in self.keys.items():
			conf[provider_key_name(name)] = value
		if code == LOCAL:
			conf["local_ai"] = True
			conf["active_provider"] = "custom"
			conf["custom_api_type"] = "openai"
			conf["custom_upload_support"] = False
			conf["custom_model_name"] = local_ai.MODEL_NAME
			if conf.get("ocr_engine", "chrome") == "chrome":
				conf["ocr_engine"] = "gemini"  # "AI (Advanced)": the Chrome engine would send the pages to Google
		else:
			conf["local_ai"] = False
			conf["active_provider"] = code
		conf["ai_response_language"] = vision_config.TARGET_LIST[self.language.GetSelection()][1]
		conf["check_update_startup"] = self.checkUpdates.GetValue()

	def onSave(self):
		self.save()
		if self._code() == LOCAL and not local_ai.installed():
			wx.CallLater(500, self._remindManager)

	def _remindManager(self):
		gui.mainFrame.prePopup()
		dialog = ManagerDialog(gui.mainFrame)
		try:
			dialog.ShowModal()
		finally:
			dialog.Destroy()
			gui.mainFrame.postPopup()
