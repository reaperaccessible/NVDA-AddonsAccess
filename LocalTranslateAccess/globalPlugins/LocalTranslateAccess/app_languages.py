"""Persistent source-language preferences; independent of translation journals."""
from .i18n import _
import json
import os
import threading
from .languages import NLLB
from pathlib import Path
_catalog = json.loads((Path(__file__).parent / "catalog.json").read_text(encoding="utf-8"))
_catalog_languages = {code for entry in _catalog["entries"] for code in (entry.get("languages") or [entry["source"], entry["target"]])}


def normalize(code):
    if not isinstance(code, str) or not code.strip():
        return None
    code = code.strip().lower().replace('_', '-')
    if code.startswith('zh'):
        code = 'zh-hant' if any(x in code for x in ('hant', 'tw', 'hk')) else 'zh'
    else:
        code = code.split('-', 1)[0]
    return code if code in NLLB or code in _catalog_languages else None


class AppLanguages:
    def __init__(self, path, report_error=lambda: None):
        self.path, self.report_error = os.fspath(path), report_error
        self.lock = threading.RLock()
        self.revision = 0
        self.apps = {}
        try:
            with open(self.path, encoding='utf-8') as stream:
                payload = json.load(stream)
            if payload.get('version') == 1 and isinstance(payload.get('applications'), dict):
                self.apps = {key: value for key, value in payload['applications'].items()
                             if isinstance(key, str) and isinstance(value, dict)}
        except FileNotFoundError:
            pass
        except Exception:
            self.report_error()

    def settings(self, app):
        with self.lock:
            data = self.apps.get(app, {})
            source = data.get('source', 'inherit')
            if source not in ('auto', 'inherit'):
                source = normalize(source) or 'inherit'
            return {'source': source,
                    'confirmed': bool(data.get('confirmed',False)),
                    'remember': bool(data.get('remember', False)),
                    'detected': normalize(data.get('detected'))}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            temp = self.path + '.tmp'
            with open(temp, 'w', encoding='utf-8') as stream:
                json.dump({'version': 1, 'applications': self.apps}, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
        except Exception:
            self.report_error()

    def application_sources(self):
        with self.lock:
            return {key:self.settings(key) for key in self.apps
                    if key!='global' and self.settings(key)['source']!='inherit'}

    def configure(self, app, source, remember, forget=False):
        if source not in ('inherit', 'auto') and normalize(source) is None:
            raise ValueError(_('unsupported source language'))
        with self.lock:
            data = self.settings(app)
            data.update(source=source, remember=bool(remember))
            if forget:
                data['detected'] = None
            self.apps[app] = data
            self.revision += 1
            self._save()

    def confirm(self,app,source):
        self.configure(app,source,self.settings(app)['remember'])
        with self.lock:
            self.apps[app]['confirmed']=True
            self._save()

    def resolve(self, app, source, document, text, detect, revision=None, report=None):
        """Manual > declared language > remembered language > classifier.

        Only classifier results are remembered: a document may contain many languages.
        A user reset during classification must not resurrect the previous memory.
        """
        if source != 'auto':
            if report:report(source,'manual',None)
            return source
        declared = normalize(document)
        if declared:
            if report:report(declared,'document',None)
            return declared
        with self.lock:
            data = self.settings(app)
            if data['remember'] and data['detected']:
                if report:report(data['detected'],'memory',None)
                return data['detected']
            before = self.revision if revision is None else revision
        language, confidence = detect(text)
        language = normalize(language)
        if language is None:
            raise ValueError('unsupported detected language')
        if report:report(language,'fasttext',confidence)
        # Avoid persisting an uncertain classification of a short menu label.
        if confidence >= .80 and sum(c.isalpha() for c in text) >= 12:
            with self.lock:
                if before == self.revision and self.settings(app)['remember']:
                    self.apps.setdefault(app, {})['detected'] = language
                    self._save()
        return language
