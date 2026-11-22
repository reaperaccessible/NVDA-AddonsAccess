# SpeakWriteAccess: the transcription of an audio or video file, phrase by phrase.
# Nothing here depends on NVDA, so it can be tested on its own.

import threading

from . import audiofile, engine, microphone, writing

DONE, CANCELLED, UNREADABLE, NO_AI = 'done', 'cancelled', 'unreadable', 'no AI'


class FileTranscription:
	"""Reads the file in its own thread and sends each phrase to the AI.
	The callbacks are called from that thread: on_text(text) for each phrase written down,
	on_progress(fraction) as the file is read, from 0 to 1, and on_end(status) once, at the end."""

	def __init__(self, ai, path, code, corrected, on_text, on_progress, on_end):
		self.ai, self.path, self.code, self.corrected = ai, path, code, corrected
		self.on_text, self.on_progress, self.on_end = on_text, on_progress, on_end
		self.cancelled = threading.Event()
		self.thread = threading.Thread(target=self._run, daemon=True)

	def start(self):
		self.thread.start()

	def cancel(self):
		self.cancelled.set()

	def _run(self):
		status = DONE
		try:
			audio = audiofile.AudioFile(self.path)
		except audiofile.AudioFileError:
			self.on_end(UNREADABLE)
			return
		try:
			self.ai.ensure_running()
			cutter = microphone.Phrases()
			for pcm, reached in audiofile.phrases(audio, cutter, microphone.FRAME_BYTES):
				if self.cancelled.is_set():
					status = CANCELLED
					break
				text = writing.transcribe(self.ai, microphone.to_wav(pcm), self.code, self.corrected)
				if self.cancelled.is_set():
					status = CANCELLED
					break
				if text:
					self.on_text(text)
				if audio.seconds:
					self.on_progress(min(1.0, reached / audio.seconds))
		except audiofile.AudioFileError:
			status = UNREADABLE
		except engine.EngineError:
			status = NO_AI
		finally:
			audio.close()
		if status == DONE:
			self.on_progress(1.0)
		self.on_end(status)
