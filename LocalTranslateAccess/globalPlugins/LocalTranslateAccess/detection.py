from .i18n import _
import os
from pathlib import Path
import struct
import sys
import threading

BASE=Path(__file__).resolve().parent
_lock=threading.Lock()
_handles=[]

def libraries(include_translation=True):
    if sys.version_info[:2]!=(3,13) or struct.calcsize('P')!=8:
        raise RuntimeError('NVDA 2026.1, Python 3.13 64 bits requis.')
    with _lock:
        if not _handles:
            for p in (BASE/'vendor',BASE/'vendor/ctranslate2',BASE/'vendor/numpy.libs'):
                _handles.append(os.add_dll_directory(str(p)))
        path=str(BASE/'vendor');sys.path.insert(0,path)
        try:
            import fasttext
            if not include_translation:return None,None,fasttext
            import ctypes
            if not ctypes.WinDLL('kernel32').IsProcessorFeaturePresent(37):
                raise RuntimeError(_('Le moteur CTranslate2 demande SSE4.1. Essayez Firefox/Bergamot.'))
            import ctranslate2,sentencepiece,yaml
            return ctranslate2,sentencepiece,fasttext
        finally:sys.path.remove(path)

def bpe_libraries():
    with _lock:
        path=str(BASE/'vendor');sys.path.insert(0,path)
        try:
            from sacremoses.normalize import MosesPunctNormalizer
            from sacremoses.tokenize import MosesTokenizer,MosesDetokenizer
            return MosesPunctNormalizer,MosesTokenizer,MosesDetokenizer
        finally:sys.path.remove(path)

def nllb_libraries():
    with _lock:
        path=str(BASE/'vendor');sys.path.insert(0,path)
        try:
            import tokenizers
            return tokenizers
        finally:sys.path.remove(path)

class Detector:
    def __init__(self):self.model=None;self.lock=threading.RLock()
    def detect(self,text):
        with self.lock:
            if self.model is None:
                _unused,_unused,fasttext=libraries(False)
                self.model=fasttext.load_model(str(BASE/'assets/lid.176.ftz'))
            labels,probabilities=self.model.predict(' '.join(text.split())[:4000],k=1)
            if not labels:raise RuntimeError(_('Détection de langue indisponible'))
            return labels[0].removeprefix('__label__'),float(probabilities[0])
    def candidates(self,text,k=4):
        """The most likely languages, best first, as [(code, probability)]."""
        with self.lock:
            if self.model is None:
                _unused,_unused,fasttext=libraries(False)
                self.model=fasttext.load_model(str(BASE/'assets/lid.176.ftz'))
            labels,probabilities=self.model.predict(' '.join(text.split())[:4000],k=k)
            return [(label.removeprefix('__label__'),float(probability)) for label,probability in zip(labels,probabilities)]
    def close(self):
        with self.lock:self.model=None
