# SpeakWriteAccess: reads the sound of an audio or video file, with Windows' own decoders (Media Foundation).
# Nothing here depends on NVDA, so it can be tested on its own.

import ctypes
from ctypes import wintypes

RATE = 16000   # what the AI hears: 16 kHz, mono, 16-bit

# The files offered in the Open window: those Windows reads by itself. It may read others too.
EXTENSIONS = ('wav', 'mp3', 'm4a', 'aac', 'wma', 'flac', 'mp4', 'm4v', 'mov', 'wmv', 'avi', 'mkv', '3gp')


class AudioFileError(Exception):
	"""The file cannot be read by Windows."""


class GUID(ctypes.Structure):
	_fields_ = (('Data1', wintypes.DWORD), ('Data2', wintypes.WORD), ('Data3', wintypes.WORD), ('Data4', ctypes.c_ubyte * 8))

	def __init__(self, text):
		super().__init__()
		ctypes.oledll.ole32.CLSIDFromString(text, ctypes.byref(self))


MF_MT_MAJOR_TYPE = GUID('{48eba18e-f8c9-4687-bf11-0a74c9f96a8f}')
MF_MT_SUBTYPE = GUID('{f7e34c9a-42e8-4714-b74b-cb29d72c35e5}')
MF_MT_AUDIO_NUM_CHANNELS = GUID('{37e48bf5-645e-4c5b-89de-ada9e29b696a}')
MF_MT_AUDIO_SAMPLES_PER_SECOND = GUID('{5faeeae7-0290-4c31-9e8a-c534f68d9dba}')
MF_MT_AUDIO_BITS_PER_SAMPLE = GUID('{f2deb57f-40fa-4764-aa33-ed4f2d1ff669}')
MF_MT_AUDIO_BLOCK_ALIGNMENT = GUID('{322de230-9eeb-43bd-ab7a-ff412251541d}')
MF_MT_AUDIO_AVG_BYTES_PER_SECOND = GUID('{1aab75c8-cfef-451c-ab95-ac034b8e1731}')
MF_PD_DURATION = GUID('{6c990d33-bb8e-477a-8598-0d5d96fcd88a}')
MFMediaType_Audio = GUID('{73647561-0000-0010-8000-00AA00389B71}')
MFAudioFormat_PCM = GUID('{00000001-0000-0010-8000-00AA00389B71}')

MF_VERSION = 0x00020070
FIRST_AUDIO_STREAM = 0xFFFFFFFD
ALL_STREAMS = 0xFFFFFFFE
MEDIA_SOURCE = 0xFFFFFFFF
END_OF_STREAM = 0x2

_mf = ctypes.WinDLL('mfplat')
_mfreadwrite = ctypes.WinDLL('mfreadwrite')
_ole32 = ctypes.WinDLL('ole32')
_started = False


def _method(pointer, index, *types):
	"""The COM method number `index` of the object, ready to call (the object comes first)."""
	table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
	return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *types)(table[index])


def _release(pointer):
	if pointer:
		table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
		ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(table[2])(pointer)


# The methods used, by their place in each COM interface.
_SET_UINT32 = (21, (ctypes.POINTER(GUID), wintypes.UINT))           # IMFAttributes::SetUINT32
_SET_GUID = (24, (ctypes.POINTER(GUID), ctypes.POINTER(GUID)))     # IMFAttributes::SetGUID
_SET_STREAM_SELECTION = (4, (wintypes.DWORD, wintypes.BOOL))       # IMFSourceReader
_SET_CURRENT_MEDIA_TYPE = (7, (wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p))
_READ_SAMPLE = (9, (wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
	ctypes.POINTER(ctypes.c_longlong), ctypes.POINTER(ctypes.c_void_p)))
_GET_PRESENTATION_ATTRIBUTE = (12, (wintypes.DWORD, ctypes.POINTER(GUID), ctypes.c_void_p))
_TO_CONTIGUOUS_BUFFER = (41, (ctypes.POINTER(ctypes.c_void_p),))   # IMFSample
_LOCK = (3, (ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)))  # IMFMediaBuffer
_UNLOCK = (4, ())


class _PROPVARIANT(ctypes.Structure):
	_fields_ = (('vt', wintypes.WORD), ('r1', wintypes.WORD), ('r2', wintypes.WORD), ('r3', wintypes.WORD),
		('value', ctypes.c_ulonglong), ('pad', ctypes.c_ulonglong))


def _start():
	global _started
	if not _started:
		_mf.MFStartup(MF_VERSION, 0)
		_started = True


class AudioFile:
	"""The sound of a file, as 16 kHz mono 16-bit samples, read piece by piece.
	Must be used in one thread, which it prepares for COM itself."""

	def __init__(self, path):
		# The reader's thread; a thread already prepared differently, as NVDA's own, keeps its way.
		self.com = _ole32.CoInitializeEx(None, 0) >= 0
		self.reader = ctypes.c_void_p()
		self.seconds = 0.0
		try:
			_start()
			if _mfreadwrite.MFCreateSourceReaderFromURL(ctypes.c_wchar_p(path), None, ctypes.byref(self.reader)) < 0:
				raise AudioFileError(path)
			reader = self.reader.value
			_method(reader, _SET_STREAM_SELECTION[0], *_SET_STREAM_SELECTION[1])(reader, ALL_STREAMS, False)
			_method(reader, _SET_STREAM_SELECTION[0], *_SET_STREAM_SELECTION[1])(reader, FIRST_AUDIO_STREAM, True)
			wanted = ctypes.c_void_p()
			if _mf.MFCreateMediaType(ctypes.byref(wanted)) < 0:
				raise AudioFileError(path)
			try:
				set_guid = _method(wanted.value, _SET_GUID[0], *_SET_GUID[1])
				set_uint = _method(wanted.value, _SET_UINT32[0], *_SET_UINT32[1])
				set_guid(wanted.value, ctypes.byref(MF_MT_MAJOR_TYPE), ctypes.byref(MFMediaType_Audio))
				set_guid(wanted.value, ctypes.byref(MF_MT_SUBTYPE), ctypes.byref(MFAudioFormat_PCM))
				for key, value in ((MF_MT_AUDIO_NUM_CHANNELS, 1), (MF_MT_AUDIO_SAMPLES_PER_SECOND, RATE),
						(MF_MT_AUDIO_BITS_PER_SAMPLE, 16), (MF_MT_AUDIO_BLOCK_ALIGNMENT, 2),
						(MF_MT_AUDIO_AVG_BYTES_PER_SECOND, RATE * 2)):
					set_uint(wanted.value, ctypes.byref(key), value)
				# Windows decodes and converts to what the AI hears.
				_method(reader, _SET_CURRENT_MEDIA_TYPE[0], *_SET_CURRENT_MEDIA_TYPE[1])(reader, FIRST_AUDIO_STREAM, None, wanted.value)
			finally:
				_release(wanted.value)
			duration = _PROPVARIANT()
			try:
				_method(reader, _GET_PRESENTATION_ATTRIBUTE[0], *_GET_PRESENTATION_ATTRIBUTE[1])(
					reader, MEDIA_SOURCE, ctypes.byref(MF_PD_DURATION), ctypes.byref(duration))
				self.seconds = duration.value / 10_000_000
			except OSError:
				self.seconds = 0.0
		except OSError as error:
			self.close()
			raise AudioFileError(path) from error
		except AudioFileError:
			self.close()
			raise

	def chunks(self):
		"""Yields the samples, as bytes, as Windows decodes them."""
		reader = self.reader.value
		read = _method(reader, _READ_SAMPLE[0], *_READ_SAMPLE[1])
		while True:
			stream, flags, stamp, sample = wintypes.DWORD(), wintypes.DWORD(), ctypes.c_longlong(), ctypes.c_void_p()
			try:
				read(reader, FIRST_AUDIO_STREAM, 0, ctypes.byref(stream), ctypes.byref(flags), ctypes.byref(stamp), ctypes.byref(sample))
			except OSError as error:
				raise AudioFileError('read') from error
			if sample.value:
				try:
					yield self._bytes(sample.value)
				finally:
					_release(sample.value)
			if flags.value & END_OF_STREAM:
				return

	@staticmethod
	def _bytes(sample):
		buffer = ctypes.c_void_p()
		_method(sample, _TO_CONTIGUOUS_BUFFER[0], *_TO_CONTIGUOUS_BUFFER[1])(sample, ctypes.byref(buffer))
		try:
			data, largest, length = ctypes.c_void_p(), wintypes.DWORD(), wintypes.DWORD()
			_method(buffer.value, _LOCK[0], *_LOCK[1])(buffer.value, ctypes.byref(data), ctypes.byref(largest), ctypes.byref(length))
			try:
				return ctypes.string_at(data.value, length.value)
			finally:
				_method(buffer.value, _UNLOCK[0])(buffer.value)
		finally:
			_release(buffer.value)

	def close(self):
		if self.reader:
			_release(self.reader.value)
			self.reader = ctypes.c_void_p()
		if self.com:
			self.com = False
			_ole32.CoUninitialize()


def readable(path):
	"""True when Windows can read the sound of the file."""
	try:
		AudioFile(path).close()
		return True
	except AudioFileError:
		return False


def phrases(audio, cutter, frame_bytes):
	"""The phrases said in the file, cut at each pause by `cutter`, with the time reached, in seconds."""
	reached = 0.0
	for frame, reached in frames(audio, frame_bytes):
		phrase = cutter.feed(frame)
		if phrase:
			yield phrase, reached
	last = cutter.finish()
	if last:
		yield last, reached


def frames(audio, frame_bytes):
	"""The samples of the file cut into frames of `frame_bytes` bytes, with the time reached, in seconds."""
	pending = bytearray()
	done = 0
	for chunk in audio.chunks():
		pending += chunk
		while len(pending) >= frame_bytes:
			frame = bytes(pending[:frame_bytes])
			del pending[:frame_bytes]
			done += frame_bytes
			yield frame, done / (RATE * 2)
	if pending:
		pending += bytes(frame_bytes - len(pending))
		yield bytes(pending), (done + frame_bytes) / (RATE * 2)
