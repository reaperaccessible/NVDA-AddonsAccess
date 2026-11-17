"""Read and correct translation journals without changing their cache identity."""
from .i18n import _
import hashlib
import json
import os
from pathlib import Path
from .persistent_cache import _LOCK, SHARED_NAMESPACE, migrate_all

class CacheEditor:
    def __init__(self,directory):self.directory=Path(directory).resolve()
    def _records(self,path):
        records={}
        with path.open('r',encoding='utf-8',errors='replace') as stream:
            for line in stream:
                try:
                    value=json.loads(line)
                    fields=[value[k] for k in ('application','source','target','text','translation')]
                    if not all(isinstance(v,str) for v in fields) or not fields[-1]:continue
                    app,src,tgt,text,translation=fields
                    if path.name!=hashlib.sha256(app.encode('utf-8')).hexdigest()+'.jsonl':continue
                    key=(app,src,tgt,text)
                    if value.get('manual') or not records.get(key,{}).get('manual'):
                        records[key]=dict(application=app,source=src,target=tgt,text=text,
                            translation=translation,manual=bool(value.get('manual')))
                except (ValueError,KeyError,TypeError,AttributeError):continue
        return records
    def entries(self):
        result=[]
        if not self.directory.exists():return result
        migrate_all(self.directory)
        for path in sorted((self.directory/SHARED_NAMESPACE).glob('*.jsonl')):
            if not path.resolve().is_relative_to(self.directory):continue
            with _LOCK:
                try:records=self._records(path)
                except FileNotFoundError:continue
            for record in records.values():
                record['namespace']=path.parent.name
                record['key']=(path.parent.name,record['application'],record['source'],record['target'],record['text'])
                result.append(record)
        return result
    def save(self,record,translation):
        if not isinstance(translation,str) or not translation.strip():
            raise ValueError(_('La traduction ne peut pas être vide.'))
        namespace=record['namespace']
        if not namespace or Path(namespace).name!=namespace:raise ValueError('Cache invalide.')
        app=record['application'];name=hashlib.sha256(app.encode('utf-8')).hexdigest()+'.jsonl'
        path=(self.directory/namespace/name).resolve()
        if not path.is_relative_to(self.directory):raise ValueError('Cache invalide.')
        key=(app,record['source'],record['target'],record['text'])
        with _LOCK:
            current=self._records(path).get(key)
            if current is None:raise ValueError(_('Cette traduction n’existe plus. Actualisez la liste.'))
            if current['translation']!=record['translation']:
                raise ValueError(_('Cette traduction a changé. Actualisez la liste avant de la corriger.'))
            value=dict(current,translation=translation,manual=True)
            data=('\n'+json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8')
            with path.open('ab') as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())
        return dict(record,translation=translation,manual=True)
