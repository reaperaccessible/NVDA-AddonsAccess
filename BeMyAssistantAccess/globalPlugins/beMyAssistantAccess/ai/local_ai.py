# -*- coding: utf-8 -*-
# BeMyAssistantAccess: the AI that runs entirely on this computer.
# The engine and the model are installed by the ReaperAccessible Installer Manager, never downloaded by the add-on.
# The engine is started when a request needs it and stopped after a while without use.
# This module does not touch NVDA, so it can be tested alone.
import ctypes
import json
import os
import subprocess
import threading
import time
import urllib.request

PORT = 8091
URL = f"http://127.0.0.1:{PORT}"
MODEL_NAME = "qwen3.5-35b-a3b"
IDLE_STOP = 15 * 60  # seconds without a request before the engine is stopped; None: never stopped
KEEP_CHOICES = ("15", "60", "always")  # the setting "keep the local AI ready"
START_TIMEOUT = 300  # seconds to load the model
MIN_RAM_GB = 30  # the model is 21 GB; most of it stays in the computer's memory
MIN_VRAM_GB = 6

# Installed by the "BeMyAssistantAccess, local AI" installer of the ReaperAccessible Installer Manager:
# the engine (llama.cpp, Vulkan build) in engine\, and the model (Qwen 3.5 35B-A3B, which sees images).
MODEL_FILES = [
	{"name": "vision.gguf", "size": 899283648},
	{"name": "model.gguf", "size": 22016023168},
]
TOTAL_SIZE = 38716745 + sum(item["size"] for item in MODEL_FILES)
MANAGER_URL = "https://reaperaccessible.fr/reaperaccessible_installer_manager/download/ReaperAccessible_Installer_Manager_Setup.exe"
CREATE_NO_WINDOW = 0x08000000


def folder():
	base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
	return os.path.join(base, "ReaperAccessible", "BeMyAssistantAccess", "local-ai")


def server_exe():
	return os.path.join(folder(), "engine", "llama-server.exe")


def installed():
	"""True when the engine and every model file are in place, complete."""
	if not os.path.isfile(server_exe()):
		return False
	for item in MODEL_FILES:
		path = os.path.join(folder(), item["name"])
		if not os.path.isfile(path) or os.path.getsize(path) != item["size"]:
			return False
	return True


# --- what the computer has

def ram_gb():
	class Status(ctypes.Structure):
		_fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
					("free", ctypes.c_ulonglong), ("pageTotal", ctypes.c_ulonglong), ("pageFree", ctypes.c_ulonglong),
					("virtualTotal", ctypes.c_ulonglong), ("virtualFree", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]
	status = Status()
	status.length = ctypes.sizeof(Status)
	ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
	return status.total / 2 ** 30


def graphics_cards():
	"""[(name, memory in GB)] of the graphics cards that report their own memory."""
	import winreg
	cards = []
	try:
		root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
							  r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}")
	except OSError:
		return cards
	index = 0
	while True:
		try:
			name = winreg.EnumKey(root, index)
		except OSError:
			break
		index += 1
		try:
			key = winreg.OpenKey(root, name)
			memory = winreg.QueryValueEx(key, "HardwareInformation.qwMemorySize")[0]
			description = winreg.QueryValueEx(key, "DriverDesc")[0]
			cards.append((description, memory / 2 ** 30))
		except OSError:
			pass
	return cards


def best_card():
	cards = graphics_cards()
	return max(cards, key=lambda card: card[1]) if cards else ("", 0.0)


def missing_requirements():
	"""The reasons this computer cannot run the local AI, as (code, value) pairs; empty when it can."""
	problems = []
	ram = ram_gb()
	if ram < MIN_RAM_GB:
		problems.append(("ram", ram))
	name, vram = best_card()
	if vram < MIN_VRAM_GB:
		problems.append(("vram", vram))
	return problems


# --- the engine

_process = None
_lock = threading.Lock()
_last_use = 0.0
_watcher = None


def _healthy():
	try:
		with urllib.request.urlopen(URL + "/health", timeout=2) as answer:
			return b"ok" in answer.read()
	except Exception:
		return False


def running():
	return _process is not None and _process.poll() is None


def _arguments():
	name, vram = best_card()
	arguments = [server_exe(), "-m", os.path.join(folder(), "model.gguf"),
				 "--mmproj", os.path.join(folder(), "vision.gguf"),
				 "-ngl", "99", "-c", "16384", "--image-min-tokens", "1024",
				 "--host", "127.0.0.1", "--port", str(PORT), "--alias", MODEL_NAME,
				 "--chat-template-kwargs", json.dumps({"enable_thinking": False})]
	if vram < 24:
		arguments.append("--cpu-moe")  # the experts stay in the computer's memory: about 4 GB on the card
	return arguments


def ensure_running():
	"""Start the engine if needed and wait until the model is loaded. Returns True when it answers."""
	global _process, _last_use
	_last_use = time.time()
	with _lock:
		if running() and _healthy():
			return True
		if not installed():
			return False
		if not running():
			log_path = os.path.join(folder(), "engine.log")
			_process = subprocess.Popen(_arguments(), cwd=os.path.dirname(server_exe()),
										stdout=open(log_path, "wb"), stderr=subprocess.STDOUT,
										creationflags=CREATE_NO_WINDOW)
			_start_watcher()
		started = time.time()
		while time.time() - started < START_TIMEOUT:
			if not running():
				return False
			if _healthy():
				_last_use = time.time()
				return True
			time.sleep(1)
		return False


def start_in_background():
	threading.Thread(target=ensure_running, daemon=True, name="BeMyAssistantLocalAI").start()


def set_keep(choice):
	"""Apply the setting "keep the local AI ready": "15" or "60" minutes after the last request, or "always"."""
	global IDLE_STOP
	IDLE_STOP = None if choice == "always" else int(choice if choice in KEEP_CHOICES else "15") * 60


def touch():
	global _last_use
	_last_use = time.time()


def _start_watcher():
	global _watcher
	if _watcher is not None and _watcher.is_alive():
		return

	def watch():
		while running():
			time.sleep(30)
			if IDLE_STOP is not None and time.time() - _last_use > IDLE_STOP:
				stop()
	_watcher = threading.Thread(target=watch, daemon=True, name="BeMyAssistantLocalAIIdle")
	_watcher.start()


def stop():
	global _process
	process = _process
	_process = None
	if process is not None and process.poll() is None:
		process.terminate()
		try:
			process.wait(10)
		except Exception:
			process.kill()
