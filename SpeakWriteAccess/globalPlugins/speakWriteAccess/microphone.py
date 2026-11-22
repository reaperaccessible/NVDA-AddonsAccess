# SpeakWriteAccess: the microphone, and the cutting of what is said into phrases.
# Nothing here depends on NVDA, so it can be tested on its own.

import array
import ctypes
import io
import math
import threading
import time
import wave
from ctypes import wintypes

RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2


def to_wav(pcm):
	"""16 kHz mono 16-bit samples as the bytes of a WAV file."""
	buffer = io.BytesIO()
	with wave.open(buffer, 'wb') as out:
		out.setnchannels(1)
		out.setsampwidth(2)
		out.setframerate(RATE)
		out.writeframes(pcm)
	return buffer.getvalue()


def level(frame):
	"""The loudness of one frame (root mean square of its samples)."""
	samples = array.array('h', frame)
	if not samples:
		return 0.0
	return math.sqrt(sum(s * s for s in samples) / len(samples))


class Phrases:
	"""Cuts a stream of frames into phrases, at each pause.

	feed() takes one frame and returns the PCM of a finished phrase, or None.
	The threshold follows the background noise, so a quiet room and a noisy one both work.
	"""

	def __init__(self, pause_ms=700, longest_s=25, shortest_ms=400, before_ms=300):
		self.pause = pause_ms // FRAME_MS
		self.longest = longest_s * 1000 // FRAME_MS
		self.shortest = shortest_ms // FRAME_MS
		self.before = before_ms // FRAME_MS
		self.noise = None
		self.history = []
		self.phrase = []
		self.voiced = 0
		self.quiet = 0

	def threshold(self):
		return max(250.0, (self.noise or 0.0) * 3.0)

	def feed(self, frame):
		loud = level(frame)
		speaking = loud > self.threshold()
		if not speaking:
			# The background noise is learnt only while nobody speaks.
			self.noise = loud if self.noise is None else self.noise * 0.95 + loud * 0.05
		if not self.phrase:
			if speaking:
				self.phrase = self.history[-self.before:] + [frame]
				self.voiced, self.quiet = 1, 0
			else:
				self.history = (self.history + [frame])[-self.before:]
			return None
		self.phrase.append(frame)
		if speaking:
			self.voiced += 1
			self.quiet = 0
		else:
			self.quiet += 1
		if self.quiet >= self.pause or len(self.phrase) >= self.longest:
			return self.finish()
		return None

	def finish(self):
		"""The phrase in progress, if it holds enough speech; the cutting starts afresh."""
		phrase, voiced = self.phrase, self.voiced
		self.phrase, self.history, self.voiced, self.quiet = [], [], 0, 0
		if voiced < self.shortest:
			return None
		return b''.join(phrase)


# --- the microphone, through winmm -------------------------------------------------------

class WAVEFORMATEX(ctypes.Structure):
	_fields_ = [('wFormatTag', wintypes.WORD), ('nChannels', wintypes.WORD), ('nSamplesPerSec', wintypes.DWORD),
		('nAvgBytesPerSec', wintypes.DWORD), ('nBlockAlign', wintypes.WORD), ('wBitsPerSample', wintypes.WORD),
		('cbSize', wintypes.WORD)]


class WAVEHDR(ctypes.Structure):
	pass


WAVEHDR._fields_ = [('lpData', ctypes.c_void_p), ('dwBufferLength', wintypes.DWORD), ('dwBytesRecorded', wintypes.DWORD),
	('dwUser', ctypes.c_size_t), ('dwFlags', wintypes.DWORD), ('dwLoops', wintypes.DWORD),
	('lpNext', ctypes.POINTER(WAVEHDR)), ('reserved', ctypes.c_size_t)]

WAVE_MAPPER = 0xFFFFFFFF
WHDR_DONE = 1
BUFFERS = 8
DRV_QUERYFUNCTIONINSTANCEIDSIZE = 0x0800 + 18
DRV_QUERYFUNCTIONINSTANCEID = 0x0800 + 17
# The two halves of a recording device's name, as Windows shows it: "Microphone (Realtek Audio)".
DESCRIPTION = '{a45c254e-df1c-4efd-8020-67d146a850e0},2'
ADAPTER = '{b3f8fa53-0004-438e-9003-51a46e139bfc},6'
CAPTURE_KEY = r'SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture'


class WAVEINCAPSW(ctypes.Structure):
	_fields_ = [('wMid', wintypes.WORD), ('wPid', wintypes.WORD), ('vDriverVersion', wintypes.UINT),
		('szPname', wintypes.WCHAR * 32), ('dwFormats', wintypes.DWORD), ('wChannels', wintypes.WORD),
		('wReserved1', wintypes.WORD)]


class MicrophoneError(Exception):
	pass


def _endpoint(index):
	"""The Windows identifier of the recording device number `index`, which stays the same when others come and go."""
	winmm = ctypes.windll.winmm
	winmm.waveInMessage.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p]
	size = wintypes.ULONG()
	if winmm.waveInMessage(wintypes.HANDLE(index), DRV_QUERYFUNCTIONINSTANCEIDSIZE, ctypes.byref(size), None) or not size.value:
		return ''
	buffer = ctypes.create_unicode_buffer(size.value // 2 + 1)
	if winmm.waveInMessage(wintypes.HANDLE(index), DRV_QUERYFUNCTIONINSTANCEID, buffer, ctypes.c_void_p(size.value)):
		return ''
	return buffer.value


def _full_name(endpoint, short):
	"""The whole name of the device, which winmm cuts at 31 letters."""
	try:
		import winreg
		key = endpoint.rsplit('.', 1)[-1]
		with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CAPTURE_KEY + '\\' + key + r'\Properties') as properties:
			description = winreg.QueryValueEx(properties, DESCRIPTION)[0]
			adapter = winreg.QueryValueEx(properties, ADAPTER)[0]
		return f'{description} ({adapter})'
	except (OSError, IndexError):
		return short


def devices():
	"""The recording devices plugged in now: (identifier, name), in Windows' order."""
	winmm = ctypes.windll.winmm
	found = []
	for index in range(winmm.waveInGetNumDevs()):
		caps = WAVEINCAPSW()
		if winmm.waveInGetDevCapsW(index, ctypes.byref(caps), ctypes.sizeof(caps)):
			continue
		endpoint = _endpoint(index)
		found.append((endpoint or caps.szPname, _full_name(endpoint, caps.szPname) if endpoint else caps.szPname))
	return found


def _index_of(device):
	for index, (identifier, _name) in enumerate(devices()):
		if identifier == device:
			return index
	return None


class Microphone:
	"""Records a microphone and calls on_frame(bytes) for every 30 ms, from its own thread.
	`device` is an identifier from devices(); empty, it is Windows' default microphone.
	When that device is not plugged in, the default one is used and `missing` is True."""

	def __init__(self, on_frame, device=''):
		self.on_frame = on_frame
		self.device = device
		self.missing = False
		self.running = False
		self.thread = None
		self.winmm = ctypes.windll.winmm
		self.winmm.waveInOpen.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.UINT, ctypes.POINTER(WAVEFORMATEX),
			ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
		for name in ('waveInPrepareHeader', 'waveInUnprepareHeader', 'waveInAddBuffer'):
			getattr(self.winmm, name).argtypes = [wintypes.HANDLE, ctypes.POINTER(WAVEHDR), wintypes.UINT]
		for name in ('waveInStart', 'waveInStop', 'waveInReset', 'waveInClose'):
			getattr(self.winmm, name).argtypes = [wintypes.HANDLE]

	def start(self):
		"""Opens the microphone; raises MicrophoneError when there is none."""
		fmt = WAVEFORMATEX(1, 1, RATE, RATE * 2, 2, 16, 0)
		handle = wintypes.HANDLE()
		index = WAVE_MAPPER
		if self.device:
			found = _index_of(self.device)
			self.missing = found is None
			if found is not None:
				index = found
		if self.winmm.waveInOpen(ctypes.byref(handle), index, ctypes.byref(fmt), 0, 0, 0):
			raise MicrophoneError('no microphone')
		self.handle = handle
		self.memory = [ctypes.create_string_buffer(FRAME_BYTES) for _ in range(BUFFERS)]
		self.headers = [WAVEHDR() for _ in range(BUFFERS)]
		for header, memory in zip(self.headers, self.memory):
			header.lpData = ctypes.cast(memory, ctypes.c_void_p)
			header.dwBufferLength = FRAME_BYTES
			self.winmm.waveInPrepareHeader(handle, ctypes.byref(header), ctypes.sizeof(WAVEHDR))
			self.winmm.waveInAddBuffer(handle, ctypes.byref(header), ctypes.sizeof(WAVEHDR))
		if self.winmm.waveInStart(handle):
			self._close()
			raise MicrophoneError('the microphone could not start')
		self.running = True
		self.thread = threading.Thread(target=self._run, daemon=True)
		self.thread.start()

	def _run(self):
		turn = 0
		while self.running:
			header = self.headers[turn]
			if not header.dwFlags & WHDR_DONE:
				time.sleep(0.005)
				continue
			frame = ctypes.string_at(self.memory[turn], header.dwBytesRecorded)
			header.dwFlags &= ~WHDR_DONE
			self.winmm.waveInAddBuffer(self.handle, ctypes.byref(header), ctypes.sizeof(WAVEHDR))
			turn = (turn + 1) % BUFFERS
			if len(frame) == FRAME_BYTES:
				try:
					self.on_frame(frame)
				except Exception:
					pass

	def stop(self):
		if not self.running:
			return
		self.running = False
		if self.thread is not None:
			self.thread.join(1)
		self._close()

	def _close(self):
		self.winmm.waveInStop(self.handle)
		self.winmm.waveInReset(self.handle)
		for header in self.headers:
			self.winmm.waveInUnprepareHeader(self.handle, ctypes.byref(header), ctypes.sizeof(WAVEHDR))
		self.winmm.waveInClose(self.handle)
