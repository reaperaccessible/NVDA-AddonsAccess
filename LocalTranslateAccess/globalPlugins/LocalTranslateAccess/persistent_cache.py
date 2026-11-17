"""Per-application translation journals in the NVDA profile, using stdlib only.

Accessed by the translation worker, never by the speech/UI thread.
Successful entries are flushed before returning. Journals keep all entries;
the dispatcher separately keeps a bounded hot cache.
"""
from .i18n import _
import hashlib
import json
import os
from pathlib import Path
import threading
import re

_LOCK = threading.RLock()
MODEL_NAMESPACE = 'opus-enfr-236b63e3-int8-beam4-v1'
SHARED_NAMESPACE = 'shared-v1'

def read_records(path):
    entries={}
    if not path.exists():return entries
    with path.open('r',encoding='utf-8',errors='replace') as stream:
        for line in stream:
            try:
                record=json.loads(line)
                values=[record[k] for k in ('application','source','target','text','translation')]
                if not all(isinstance(v,str) for v in values) or not values[-1].strip():continue
                app,src,tgt,text,translation=values
                if path.name!=hashlib.sha256(app.encode('utf-8')).hexdigest()+'.jsonl':continue
                key=(app,src,tgt,text)
                if record.get('manual') or not entries.get(key,{}).get('manual'):
                    entries[key]=dict(application=app,source=src,target=tgt,text=text,
                        translation=translation,manual=bool(record.get('manual')))
            except (ValueError,KeyError,TypeError,AttributeError):continue
    return entries

def append_records(path,records):
    if not records:return
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('ab') as stream:
        for record in records:
            stream.write(('\n'+json.dumps(record,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8'))
        stream.flush();os.fsync(stream.fileno())

def migrate_journal(directory,name):
    """Merge historical model caches once; retain originals and personal corrections."""
    if not re.fullmatch(r'[a-f0-9]{64}\.jsonl',name):return
    directory=Path(directory).resolve();shared=directory/SHARED_NAMESPACE
    if not shared.resolve().is_relative_to(directory):raise ValueError(_('Chemin du cache invalide.'))
    path=shared/name;marker=shared/(name+'.migration.json')
    with _LOCK:
        try:seen=json.loads(marker.read_text(encoding='utf-8'))
        except (OSError,ValueError):seen={}
        if not isinstance(seen,dict):seen={}
        legacy=[]
        for source in directory.glob('*/'+name):
            if source.parent.name==SHARED_NAMESPACE or not source.resolve().is_relative_to(directory):continue
            stat=source.stat();signature=[stat.st_mtime_ns,stat.st_size]
            if seen.get(source.parent.name)!=signature:legacy.append((stat.st_mtime_ns,source,signature))
        if not legacy:return
        current=read_records(path);candidates={}
        for _unused,source,signature in sorted(legacy):
            for key,record in read_records(source).items():
                if record['manual'] or not candidates.get(key,{}).get('manual'):candidates[key]=record
            seen[source.parent.name]=signature
        additions=[]
        for key,record in candidates.items():
            old=current.get(key)
            if old is None or (record['manual'] and not old['manual']):additions.append(record)
        append_records(path,additions)
        shared.mkdir(parents=True,exist_ok=True)
        temp=marker.with_suffix('.tmp')
        with temp.open('w',encoding='utf-8') as stream:
            json.dump(seen,stream);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,marker)

def migrate_all(directory):
    directory=Path(directory)
    if directory.exists():
        for name in {p.name for p in directory.glob('*/*.jsonl')}:migrate_journal(directory,name)


class PersistentCache:
    def __init__(self, directory_getter, report_error=None, namespace_getter=None, shared=False):
        self.directory_getter = directory_getter
        self.namespace_getter = namespace_getter or (lambda app: MODEL_NAMESPACE)
        self.report_error = report_error or (lambda: None)
        self.warned = False
        self.application = None
        self.entries = {}
        self.manual = set()
        self.signature = None
        self.shared=shared;self.migrated=set()

    def _error(self):
        if not self.warned:
            self.warned = True
            self.report_error()

    def _path(self, application):
        name = hashlib.sha256(application.encode('utf-8')).hexdigest() + '.jsonl'
        if self.shared:
            if application not in self.migrated:
                migrate_journal(self.directory_getter(),name);self.migrated.add(application)
            return Path(self.directory_getter())/SHARED_NAMESPACE/name
        return Path(self.directory_getter()) / self.namespace_getter(application) / name

    def _load(self, application, path):
        signature = None
        if path.exists():
            stat = path.stat()
            signature = (str(path), stat.st_mtime_ns, stat.st_size)
        if application == self.application and signature == self.signature:
            return
        entries = {};manual=set()
        if signature is not None:
            with path.open('r', encoding='utf-8', errors='replace') as stream:
                for line in stream:
                    try:
                        record = json.loads(line)
                        if record.get('application') != application:
                            continue
                        src, tgt, text, translation = (record[k] for k in ('source', 'target', 'text', 'translation'))
                        if not all(isinstance(v, str) for v in (src, tgt, text, translation)) or not translation:
                            continue
                        key=(src,tgt,text)
                        if record.get('manual'):
                            manual.add(key);entries[key]=translation
                        elif key not in manual:entries[key]=translation
                    except (ValueError, KeyError, TypeError, AttributeError):
                        # An incomplete final write or malformed record must not
                        # hide earlier valid entries or break translation.
                        continue
        self.application, self.entries, self.signature = application, entries, signature
        self.manual=manual

    def get(self, key):
        application, src, tgt, text = key
        with _LOCK:
            try:
                self._load(application, self._path(application))
                return self.entries.get((src, tgt, text))
            except OSError:
                self._error()
                return None

    def put(self, key, translation):
        application, src, tgt, text = key
        with _LOCK:
            try:
                path = self._path(application)
                self._load(application, path)
                if (src,tgt,text) in self.manual:return
                if self.entries.get((src, tgt, text)) == translation:
                    return
                path.parent.mkdir(parents=True, exist_ok=True)
                record = dict(application=application, source=src, target=tgt,
                              text=text, translation=translation)
                # Leading newline isolates even a truncated existing last record.
                data = ('\n' + json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
                with path.open('ab') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                self.entries[(src, tgt, text)] = translation
                stat = path.stat()
                self.signature = (str(path), stat.st_mtime_ns, stat.st_size)
            except OSError:
                self._error()
