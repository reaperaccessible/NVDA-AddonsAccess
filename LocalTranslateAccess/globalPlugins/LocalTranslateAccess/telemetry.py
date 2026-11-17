"""Timing-only journal: never writes source or translated text."""
from .i18n import _
import json
from pathlib import Path
import threading
import time
import queue

class Telemetry:
    def __init__(self,root):
        self.path=Path(root)/'performances.jsonl'
        self.lock=threading.RLock()
        self.pending=queue.Queue(maxsize=2048)
        self.closed=threading.Event()
        self.worker=threading.Thread(target=self._run,daemon=True)
        self.worker.start()
    def record(self,kind,**values):
        if self.closed.is_set():return
        allowed={'model','family','source','target','milliseconds','cache_hits','characters','steps','success',
            'source_mode','detection_ms','source_system','device'}
        row={k:v for k,v in values.items() if k in allowed}
        row.update(kind=kind,time=time.time())
        try:self.pending.put_nowait(row)
        except queue.Full:pass
    def _run(self):
        while True:
            try:row=self.pending.get(timeout=0.5)
            except queue.Empty:
                if self.closed.is_set():return
                continue
            try:self._write(row)
            finally:self.pending.task_done()
    def flush(self):self.pending.join()
    def close(self):self.closed.set()
    def clear(self):
        self.flush()
        with self.lock:self.path.unlink(missing_ok=True)
    def _write(self,row):
        try:
            with self.lock:
                self.path.parent.mkdir(parents=True,exist_ok=True)
                if self.path.exists() and self.path.stat().st_size>5*1024**2:
                    tail=self.path.read_bytes().splitlines()[-5000:]
                    self.path.write_bytes(b'\n'.join(tail)+b'\n')
                with self.path.open('a',encoding='utf-8') as out:out.write(json.dumps(row,ensure_ascii=True)+'\n')
        except OSError:pass
    def summary(self,language_label=None):
        import math
        language_label=language_label or (lambda code:code)
        families={'argos':'Argos Translate','bergamot':'Firefox / Bergamot','opus':'Marian / OPUS-MT',
            'hy':'Hy-MT2 7B','nllb':'NLLB-200 600M','small100':'SMaLL-100','m2m100':'M2M-100'}
        groups={};loads={}
        with self.lock:
            try:rows=self.path.read_text(encoding='utf-8').splitlines()
            except OSError:rows=[]
        for line in rows:
            try:row=json.loads(line)
            except ValueError:continue
            if not isinstance(row,dict) or not row.get('success',True):continue
            milliseconds=row.get('milliseconds')
            if isinstance(milliseconds,bool) or not isinstance(milliseconds,(int,float)) or not math.isfinite(milliseconds) or milliseconds<0:continue
            model=row.get('model');family=row.get('family','')
            if not isinstance(model,str) or not model:continue
            device=row.get('device','')
            if device not in ('cpu','cuda'):device=''
            if row.get('kind')=='load':loads[(model,family,device)]=(milliseconds,row.get('source'),row.get('target'))
            # Older step timings cannot reconstruct a complete pivot translation.
            if row.get('kind')!='translation_total':continue
            source=row.get('source');target=row.get('target')
            if not isinstance(source,str) or not isinstance(target,str) or not source or not target or source==target:continue
            mode=row.get('source_mode','unknown')
            if mode not in ('auto','manual'):mode='unknown'
            groups.setdefault((model,family,source,target,mode,device),[]).append(row)
        def device_label(device):return {'cpu':_(' — CPU, RAM'),'cuda':_(' — GPU NVIDIA, VRAM'),'':''}[device]
        def duration(milliseconds):
            return f'{milliseconds/1000:.3f}'.replace('.',',')+(_(' seconde') if milliseconds<2000 else _(' secondes'))
        lines=[_('Temps de chargement du modèle :')]
        if loads:
            for (model,family,device),(milliseconds,source,target) in sorted(loads.items()):
                name=families.get(family,model)+device_label(device)
                if isinstance(source,str) and isinstance(target,str):
                    name+=' — '+language_label(source)+_(' vers ')+language_label(target)
                lines.append(_('{v0} : {v1} — dernier chargement.').format(v0=name, v1=duration(milliseconds)))
        else:lines.append(_('Aucun chargement mesuré pour le moment.'))
        lines.extend(['',_('Temps moyen de traduction par langue source et langue cible :')])
        for (model,family,source,target,mode,device),values in sorted(groups.items()):
            name=families.get(family,model)+device_label(device)
            lines.extend(['',_('{v0} vers {v1} — {v2}').format(v0=language_label(source), v1=language_label(target), v2=name)])
            lines.append(_('Langue source : ')+{'auto':_('Automatique'),'manual':_('Choisie manuellement'),
                'unknown':_('mode non enregistré dans les anciennes mesures')}[mode]+'.')
            if mode=='auto':
                detections=[row['detection_ms'] for row in values if isinstance(row.get('detection_ms'),(int,float))
                    and not isinstance(row['detection_ms'],bool) and math.isfinite(row['detection_ms']) and row['detection_ms']>=0]
                if detections:
                    lines.append(_('Temps moyen de détection de la langue : ')+duration(sum(detections)/len(detections))+'.')
                else:lines.append(_('Temps de détection de la langue : aucune mesure disponible.'))
                if all(row.get('source_system') in ('document','memory') for row in values):
                    lines.append(_('Langue fournie par le document ou mémorisée : aucune détection du texte nécessaire.'))
            message=_('Temps moyen de traduction : {duration}, sur {count} traduction.') if len(values)==1 else _('Temps moyen de traduction : {duration}, sur {count} traductions.')
            lines.append(message.format(duration=duration(sum(row['milliseconds'] for row in values)/len(values)),count=len(values)))
        if not groups:lines.append(_('Aucune traduction mesurée avec ce nouveau calcul pour le moment.'))
        lines.extend(['',_('Les moyennes sont séparées par modèle et par paire de langues. Elles excluent le cache, le chargement et la détection de la langue. Une traduction passant par une langue intermédiaire compte une seule fois, avec le temps de toutes ses étapes.'),
            _('La durée dépend aussi de la longueur des textes traduits.')])
        return '\n'.join(lines)
