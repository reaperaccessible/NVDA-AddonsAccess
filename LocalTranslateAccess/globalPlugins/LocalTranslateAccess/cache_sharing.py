"""Portable, model-independent cache files and conservative import merging."""
from .i18n import _
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import zlib
from .cache_editor import CacheEditor
from .persistent_cache import _LOCK,SHARED_NAMESPACE,read_records,append_records,migrate_all

FORMAT='localtranslate-cache'

class CacheSharing:
    def __init__(self,directory):self.directory=Path(directory).resolve()
    def records(self):
        fields=('application','source','target','text','translation','manual')
        return [{field:r[field] for field in fields} for r in CacheEditor(self.directory).entries()]
    def export(self,path,applications):
        applications=set(applications)
        entries=[r for r in self.records() if r['application'] in applications]
        entries.sort(key=lambda r:(r['application'],r['source'],r['target'],r['text']))
        payload=dict(format=FORMAT,version=1,entries=entries)
        path=Path(path);temporary=None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent,prefix=path.name+'.',suffix='.tmp',delete=False) as stream:
                temporary=Path(stream.name)
            with gzip.open(temporary,'wt',encoding='utf-8') as stream:
                json.dump(payload,stream,ensure_ascii=False,separators=(',',':'))
            with temporary.open('r+b') as stream:os.fsync(stream.fileno())
            os.replace(temporary,path);temporary=None
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)
        return len(entries)
    def read(self,path):
        try:
            with gzip.open(path,'rb') as stream:
                data=stream.read(512*1024*1024+1)
            if len(data)>512*1024*1024:raise ValueError(_('Le fichier de cache dépasse 512 Mo après décompression.'))
            payload=json.loads(data.decode('utf-8'))
        except (OSError,EOFError,UnicodeError,ValueError,RecursionError,zlib.error) as exc:
            raise ValueError(_('Fichier de cache LocalTranslate invalide : ')+str(exc)) from exc
        if not isinstance(payload,dict) or payload.get('format')!=FORMAT or payload.get('version')!=1 or not isinstance(payload.get('entries'),list):
            raise ValueError(_('Ce fichier n’est pas un cache LocalTranslate compatible.'))
        records={}
        for raw in payload['entries']:
            if not isinstance(raw,dict):raise ValueError(_('Entrée de cache invalide.'))
            fields=('application','source','target','text','translation')
            if not all(isinstance(raw.get(k),str) for k in fields) or not all(raw[k].strip() for k in fields):
                raise ValueError(_('Une entrée contient un texte, une langue ou une application vide.'))
            if not isinstance(raw.get('manual',False),bool):raise ValueError(_('Correction de cache invalide.'))
            record={k:raw[k] for k in fields};record['manual']=raw.get('manual',False)
            key=tuple(record[k] for k in fields[:-1])
            if record['manual'] or not records.get(key,{}).get('manual'):records[key]=record
        return list(records.values())
    def merge(self,records,applications,replace=True):
        selected=set(applications);result=dict(added=0,replaced=0,kept=0,protected=0)
        migrate_all(self.directory)
        by_app={}
        for record in records:
            if record['application'] in selected:by_app.setdefault(record['application'],[]).append(record)
        for app,values in by_app.items():
            name=hashlib.sha256(app.encode('utf-8')).hexdigest()+'.jsonl'
            path=self.directory/SHARED_NAMESPACE/name
            if not path.resolve().is_relative_to(self.directory):raise ValueError(_('Chemin du cache invalide.'))
            with _LOCK:
                current=read_records(path);updates=[]
                for record in values:
                    key=(app,record['source'],record['target'],record['text']);old=current.get(key)
                    if old and old['manual']:
                        result['protected']+=1;continue
                    if old and (not replace or old==record):result['kept']+=1;continue
                    updates.append(record);current[key]=record
                    result['replaced' if old else 'added']+=1
                append_records(path,updates)
        return result
