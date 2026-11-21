# -*- coding: utf-8 -*-
# BeMyAssistantAccess: the step-by-step pilot. The user says one action ("click Online games", "type "hello" in the
# Name field"); the AI only finds WHERE the element is on the screen; the add-on tells what it found and asks before
# acting; then it clicks, or types the text the user gave - never a text of the AI's own; then it says what changed.
import ctypes
import json
import re
import threading
import time

import wx

import addonHandler
import config as nvda_config
import gui
import scriptHandler
import tones
import ui
from logHandler import log

from .. import vision_config
from ..ai.core import AIHandler
from ..utils.error_contract import is_ai_error
from ..utils.mouse_keyboard import MouseSimulator
from ..utils.system import clean_json_fence

addonHandler.initTranslation()

ACTIONS = ("click", "double_click", "right_click", "type", "unknown")
QUOTES = '"“”«»‘’\''


def answer_language():
	code = nvda_config.conf["BeMyAssistantAccess"].get("ai_response_language", "")
	return next((name for name, value in vision_config.TARGET_LIST if value == code), "English")


def parse_json(text):
	"""The first JSON object in an answer, or None."""
	if not text or is_ai_error(text):
		return None
	text = clean_json_fence(text)
	match = re.search(r"\{.*\}", text, re.S)
	if not match:
		return None
	try:
		data = json.loads(match.group(0))
	except ValueError:
		return None
	return data if isinstance(data, dict) else None


def quoted_texts(command):
	"""Every text the user put between quotes, the only texts the pilot may type."""
	return [m.strip() for m in re.findall(r'["“«]\s*(.+?)\s*["”»]', command) if m.strip()]


def checked_intent(data, command):
	"""The action asked, made safe: a text to type must be one the user gave between quotes."""
	if not data:
		return None
	action = str(data.get("action", "unknown")).lower()
	if action not in ACTIONS:
		action = "unknown"
	target = str(data.get("target", "")).strip()
	text = str(data.get("text", "") or "").strip(QUOTES + " ")
	if action == "type":
		given = quoted_texts(command)
		if not given:
			return {"action": "need_text", "target": target, "text": ""}
		if text not in given:
			text = given[0]  # never a text of the AI's own
	return {"action": action, "target": target, "text": text if action == "type" else ""}


def screen_point(data, width, height, scale):
	"""The physical screen point of an answer with x and y from 0 to 1000 over the whole screenshot, or None."""
	try:
		x, y = float(data["x"]), float(data["y"])
	except (KeyError, TypeError, ValueError):
		return None
	if not (0 <= x <= 1000 and 0 <= y <= 1000):
		return None
	return int(round(x / 1000 * width * scale)), int(round(y / 1000 * height * scale))


INTENT_PROMPT = (
	"A blind user asks a screen assistant to do ONE action on the screen. Their request: {command!r}\n"
	"Return only JSON: {{\"action\": one of \"click\", \"double_click\", \"right_click\", \"type\", \"unknown\", "
	"\"target\": the element on the screen to act on, as the user named it, \"text\": for \"type\" only, the text to "
	"type exactly as the user gave it between quotes, else \"\"}}. Never invent a text: if the user gave no text "
	"between quotes, keep \"text\" empty."
)
WHERE_PROMPT = (
	"Find this element on the screenshot: {target!r}. Coordinates x and y go from 0 to 1000 across the whole "
	"screenshot (0,0 is the top left corner): give the centre of the element. Return only JSON: "
	"{{\"found\": true or false, \"x\": number, \"y\": number, \"description\": what the element is, never where it is, "
	"in {language}, a few words}}. If it is not clearly visible, found is false."
)
AFTER_PROMPT = (
	"The user just did this on the screen: {done}. In {language}, in one or two short sentences, say what the "
	"screen shows now, starting with what changed if you can tell. Do not invent anything."
)


class StepPilotMixin:
	@scriptHandler.script(
		# Translators: Description of the step-by-step pilot in the Input Gestures dialog.
		description=_("Step-by-step pilot: you say one action, the AI finds the element, you confirm, the add-on clicks or types."),
		category=vision_config.ADDON_NAME,
	)
	def script_stepPilot(self, gesture):
		if getattr(self, "toggling", False):
			self.finish()
		if getattr(self, "_pilot_busy", False) or getattr(self, "_dialog_open", False):
			return
		wx.CallAfter(self._pilot_ask)

	def _pilot_ask(self):
		self._dialog_open = True
		gui.mainFrame.prePopup()
		try:
			dialog = wx.TextEntryDialog(
				gui.mainFrame,
				# Translators: Question of the step-by-step pilot, with examples.
				_("What should I do? One action at a time, for example: click Online games. To type, put the text "
				  "between quotes: type \"hello\" in the Name field."),
				# Translators: Title of the step-by-step pilot window.
				_("Step-by-step pilot"),
			)
			command = dialog.GetValue().strip() if dialog.ShowModal() == wx.ID_OK else ""
			dialog.Destroy()
		finally:
			gui.mainFrame.postPopup()
			self._dialog_open = False
		if command:
			self._pilot_busy = True
			# Translators: Said while the AI looks at the screen for the step-by-step pilot.
			ui.message(_("Looking..."))
			threading.Thread(target=self._pilot_find, args=(command,), daemon=True, name="BeMyAssistantPilot").start()

	def _pilot_fail(self, text):
		self._pilot_busy = False
		tones.beep(220, 120)
		wx.CallAfter(ui.message, text)

	def _pilot_find(self, command):
		try:
			intent = checked_intent(parse_json(AIHandler.call(
				INTENT_PROMPT.format(command=command), json_mode=True, task="chat")), command)
			if intent is None or intent["action"] == "unknown" or not intent["target"]:
				# Translators: The pilot did not understand the request.
				return self._pilot_fail(_("I did not understand which action to do. Say one action, for example: click Online games."))
			if intent["action"] == "need_text":
				# Translators: The user asked to type without giving the text between quotes.
				return self._pilot_fail(_("Put the text to type between quotes, for example: type \"hello\" in the Name field. I never type a text you did not give."))
			time.sleep(0.6)  # the pilot's dialog is closed: the screen shows the application again
			image, width, height, mime = self._capture_fullscreen_sync()
			if not image:
				# Translators: The screen could not be captured.
				return self._pilot_fail(_("The screen could not be captured."))
			where = parse_json(AIHandler.call(
				WHERE_PROMPT.format(target=intent["target"], language=answer_language()),
				attachments=[{"mime_type": mime, "data": image}], json_mode=True, task="vision"))
			point = screen_point(where, width, height, self._pilot_scale()) if where and where.get("found") else None
			if point is None:
				# Translators: The element was not found on the screen. {target} is what the user named.
				return self._pilot_fail(_("I cannot find {target} on the screen.").format(target=intent["target"]))
			wx.CallAfter(self._pilot_confirm, intent, point, str(where.get("description", intent["target"])))
		except Exception:
			log.warning("BeMyAssistantAccess: step-by-step pilot", exc_info=True)
			# Translators: Something went wrong in the pilot.
			self._pilot_fail(_("The pilot could not finish this action."))

	def _pilot_scale(self):
		try:
			hwnd = ctypes.windll.user32.GetForegroundWindow()
			dpi = int(ctypes.windll.user32.GetDpiForWindow(hwnd)) if hwnd else 96
			return (dpi or 96) / 96.0
		except Exception:
			return 1.0

	def _pilot_confirm(self, intent, point, description):
		if intent["action"] == "type":
			# Translators: Question before typing. {text} is the user's text, {target} what was found.
			question = _("Type \"{text}\" in: {target}?").format(text=intent["text"], target=description)
		elif intent["action"] == "double_click":
			# Translators: Question before a double click. {target} is what was found.
			question = _("Double-click: {target}?").format(target=description)
		elif intent["action"] == "right_click":
			# Translators: Question before a right click. {target} is what was found.
			question = _("Right-click: {target}?").format(target=description)
		else:
			# Translators: Question before a click. {target} is what was found.
			question = _("Click: {target}?").format(target=description)
		self._dialog_open = True
		gui.mainFrame.prePopup()
		try:
			# Translators: Title of the confirmation of the step-by-step pilot.
			answer = gui.messageBox(question, _("Step-by-step pilot"), wx.YES_NO | wx.ICON_QUESTION)
		finally:
			gui.mainFrame.postPopup()
			self._dialog_open = False
		if answer != wx.YES:
			self._pilot_busy = False
			# Translators: Said when the user does not confirm the action.
			ui.message(_("Cancelled."))
			return
		threading.Thread(target=self._pilot_act, args=(intent, point, description), daemon=True, name="BeMyAssistantPilotAct").start()

	def _pilot_act(self, intent, point, description):
		try:
			time.sleep(0.6)  # the confirmation is closed: the application is in front again
			x, y = point
			action = intent["action"]
			if action == "right_click":
				MouseSimulator.click(x, y, button="right")
			else:
				MouseSimulator.click(x, y, double=(action == "double_click"))
			if action == "type":
				time.sleep(0.4)
				MouseSimulator.type_text(intent["text"])
			time.sleep(1.5)
			image, width, height, mime = self._capture_fullscreen_sync()
			done = f"{action} on {description}"
			result = AIHandler.call(
				AFTER_PROMPT.format(done=done, language=answer_language()),
				attachments=[{"mime_type": mime, "data": image}], task="vision") if image else ""
			self._pilot_busy = False
			if result and not is_ai_error(result):
				wx.CallAfter(ui.message, result.strip())
			else:
				# Translators: Said after the action when the AI cannot describe the result.
				wx.CallAfter(ui.message, _("Done."))
		except Exception:
			log.warning("BeMyAssistantAccess: step-by-step pilot action", exc_info=True)
			self._pilot_fail(_("The pilot could not finish this action."))
