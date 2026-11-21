# SpeakWriteAccess: the local AI (llama.cpp's server with Gemma 4), which hears the voice and corrects the text.
# It is installed by the ReaperAccessible Installer Manager, never downloaded by the add-on.
# Nothing here depends on NVDA, so it can be tested on its own.

import base64
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request

FOLDER = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'ReaperAccessible', 'SpeakWriteAccess', 'local-ai')
# llama.cpp's server under its own name, so that stopping it never touches another add-on's.
SERVER = os.path.join(FOLDER, 'engine', 'speakwrite-server.exe')
MODEL = os.path.join(FOLDER, 'model.gguf')
AUDIO = os.path.join(FOLDER, 'audio.gguf')
PORT = 8097
URL = f'http://127.0.0.1:{PORT}'
START_TIMEOUT = 240
MANAGER_URL = 'https://reaperaccessible.fr/reaperaccessible_installer_manager/download/ReaperAccessible_Installer_Manager_Setup.exe'

CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


class EngineError(Exception):
	pass


def installed():
	return all(os.path.isfile(path) for path in (SERVER, MODEL, AUDIO))


class Engine:
	"""Starts the server when needed, and stops it after it has been idle for a while."""

	def __init__(self, keep='15'):
		self.lock = threading.Lock()
		self.process = None
		self.last_use = time.monotonic()
		self.keep = keep
		self.watcher = None

	def set_keep(self, keep):
		"""'15' or '60' minutes after the last use, or 'always'."""
		self.keep = keep

	def running(self):
		return self.process is not None and self.process.poll() is None

	def _healthy(self):
		try:
			with urllib.request.urlopen(URL + '/health', timeout=2) as answer:
				return json.load(answer).get('status') == 'ok'
		except Exception:
			return False

	def ensure_running(self):
		"""Blocks until the server answers; raises EngineError otherwise."""
		with self.lock:
			self.last_use = time.monotonic()
			if self.running() and self._healthy():
				return
			if not installed():
				raise EngineError('not installed')
			if not self.running():
				arguments = [SERVER, '-m', MODEL, '--mmproj', AUDIO, '--port', str(PORT), '--host', '127.0.0.1',
					'-c', '8192', '-np', '1', '--chat-template-kwargs', '{"enable_thinking":false}']
				self.process = subprocess.Popen(arguments, cwd=os.path.dirname(SERVER),
					stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
					creationflags=CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS)
			deadline = time.monotonic() + START_TIMEOUT
			while time.monotonic() < deadline:
				if not self.running():
					self.process = None
					raise EngineError('the server stopped while starting')
				if self._healthy():
					self._watch()
					return
				time.sleep(0.3)
			self._kill()
			raise EngineError('the server did not start in time')

	def start_in_background(self):
		threading.Thread(target=self._start_quietly, daemon=True).start()

	def _start_quietly(self):
		try:
			self.ensure_running()
		except EngineError:
			pass

	def _watch(self):
		if self.watcher is None or not self.watcher.is_alive():
			self.watcher = threading.Thread(target=self._idle_loop, daemon=True)
			self.watcher.start()

	def _idle_loop(self):
		while self.running():
			time.sleep(20)
			if self.keep == 'always':
				continue
			if time.monotonic() - self.last_use > int(self.keep) * 60 and not self.lock.locked():
				self.stop()

	def touch(self):
		self.last_use = time.monotonic()

	def _kill(self):
		if self.process is not None:
			try:
				self.process.kill()
				self.process.wait(5)
			except Exception:
				pass
		self.process = None

	def stop(self):
		self._kill()

	def chat(self, messages, max_tokens=2048, timeout=180):
		self.ensure_running()
		body = {'messages': messages, 'temperature': 0, 'max_tokens': max_tokens}
		request = urllib.request.Request(URL + '/v1/chat/completions', json.dumps(body).encode('utf-8'),
			{'Content-Type': 'application/json'})
		try:
			with urllib.request.urlopen(request, timeout=timeout) as answer:
				text = json.load(answer)['choices'][0]['message']['content'] or ''
		except (urllib.error.URLError, OSError, KeyError, ValueError) as error:
			raise EngineError(str(error))
		finally:
			self.touch()
		return text


def audio_part(wav):
	return {'type': 'input_audio', 'input_audio': {'data': base64.b64encode(wav).decode('ascii'), 'format': 'wav'}}
