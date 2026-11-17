from .i18n import _
import json
from pathlib import Path
import re
from .detection import libraries

class BPETokenizer:
    def __init__(self,path,source,target):
        from .detection import bpe_libraries
        MosesPunctNormalizer,MosesTokenizer,MosesDetokenizer=bpe_libraries()
        from .argos_bpe import BPE
        self.normalizer=MosesPunctNormalizer(source)
        self.tokenizer=MosesTokenizer(source)
        self.detokenizer=MosesDetokenizer(target)
        try:
            with Path(path).open(encoding='utf-8') as codes:self.bpe=BPE(codes)
        except SystemExit as exc:
            raise ValueError(_('Le fichier de découpage BPE du modèle Argos est invalide.')) from exc
    def encode(self,text,out_type=str):
        normalized=self.normalizer.normalize(text)
        tokenized=' '.join(self.tokenizer.tokenize(normalized))
        return self.bpe.segment_tokens(tokenized.strip('\r\n ').split(' '))
    def decode(self,tokens):
        return self.detokenizer.detokenize(' '.join(tokens).replace('@@ ','').split(' '))

class CT2Backend:
    def __init__(self,path,family,device='cpu',device_index=0,runtime=None):
        self.path=Path(path);self.family=family;self.translator=None
        self.device,self.device_index,self.runtime=device,device_index,runtime
    def warmup(self):
        if self.translator is not None:return
        ct2,spm,_unused=libraries()
        if (self.path/'bpe.model').is_file():
            info=json.loads((self.path/'tokenizer.json').read_text(encoding='utf-8'))
            self.source=self.target=BPETokenizer(self.path/'bpe.model',info['source'],info['target'])
        else:
            self.source=spm.SentencePieceProcessor();self.target=spm.SentencePieceProcessor()
            self.source.LoadFromSerializedProto((self.path/'source.spm').read_bytes())
            self.target.LoadFromSerializedProto((self.path/'target.spm').read_bytes())
        config=json.loads((self.path/'model/config.json').read_text(encoding='utf-8'))
        self.append_eos=not config.get('add_source_eos',False)
        from .gpu_support import create_translator
        self.translator=create_translator(self.path/'model',self.device,self.device_index,self.runtime)
    def translate(self,text,src,tgt):
        self.warmup();output=[]
        for part in re.split(r'(\r\n|\n|\r)',text):
            if not part.strip():output.append(part);continue
            tokens=self.source.encode(part,out_type=str);translated=[]
            for offset in range(0,len(tokens),384):
                chunk=tokens[offset:offset+384]
                if self.append_eos:chunk=chunk+['</s>']
                result=self.translator.translate_batch([chunk],beam_size=4,num_hypotheses=1,
                    length_penalty=.2 if self.family=='argos' else 1.0,replace_unknowns=True,
                    max_input_length=0,max_decoding_length=max(256,len(chunk)*2+32))[0]
                translated.extend(p for p in result.hypotheses[0] if p not in ('</s>','<pad>','<s>'))
            output.append(self.target.decode(translated).replace('▁',' ').lstrip())
        return ''.join(output)
    def close(self):
        if self.translator is not None:self.translator.unload_model()
        self.translator=None

class Engine:
    def __init__(self,store,telemetry,application,error=None):
        import threading
        from .detection import Detector
        self.store,self.telemetry,self.application=store,telemetry,application
        self.error=error or (lambda message:None)
        self.lock=threading.RLock();self.closed=False;self.backends={};self.detector=Detector();self.warned=False;self.failed={}
        self.device='cpu';self.device_index=0;self.signature=None;self.gpu_warned=False
        self.fallback_epoch=0
        self.family=None
        self.load_barrier=None
    def _wait_for_release(self):
        if self.load_barrier is not None:
            if not self.load_barrier.wait(90):raise RuntimeError('Previous translation model did not unload in time')
            self.load_barrier=None
        if self.closed:raise RuntimeError(_('Moteur arrêté'))
    def _use_cpu(self):
        self.fallback_epoch+=1
        for backend in self.backends.values():backend.close()
        self.backends.clear();self.failed.clear();self.device='cpu'
        if self.family=='hy':
            from . import hardware
            available=hardware.inspect().get('ram_available')
            if available is not None and available<12*hardware.GIB:
                message=_('GPU indisponible et RAM insuffisante pour ce modèle. Choisissez un modèle plus léger.')
                self.error(message);raise RuntimeError(message)
        if not self.gpu_warned:
            self.gpu_warned=True
            self.error(_('Accélération GPU indisponible. La traduction utilise le processeur et la RAM.'))
    def detect(self,text):
        import time
        with self.lock:
            if self.closed:raise RuntimeError(_('Moteur arrêté'))
            start=time.perf_counter()
            try:return self.detector.detect(text)
            finally:self.telemetry.record('detection',milliseconds=(time.perf_counter()-start)*1000,characters=len(text))
    def _backend(self,entry):
        import time
        if entry['id'] in self.failed:raise RuntimeError(self.failed[entry['id']])
        if entry['id'] in self.backends:return self.backends[entry['id']]
        if not self.store.installed(entry):raise RuntimeError(_('Modèle manquant : ')+entry['id'])
        path=self.store.path(entry['id']);family=entry['family']
        options=dict(device=self.device,device_index=self.device_index,runtime=self.store.root/'runtimes/ct2_cuda')
        if family in ('argos','opus'):backend=CT2Backend(path,family,**options)
        elif family in ('small100','m2m100'):
            from .multilingual_backend import MultilingualBackend
            backend=MultilingualBackend(path,family,**options)
        elif family=='nllb':
            from .nllb_backend import NLLBBackend
            backend=NLLBBackend(path,**options)
        elif family=='bergamot':
            from .bergamot_backend import BergamotBackend
            backend=BergamotBackend(path,self.store.root/'runtimes/node/node.exe',entry['source'],entry['target'])
        elif family=='hy':
            from .gpu_backend import GpuEngine
            gpu=self.device=='cuda'
            backend=GpuEngine(lambda:str(path/'model.gguf'),lambda:str(self.store.llama_executable(gpu)),
                              self.device_index,use_gpu=gpu)
        else:raise ValueError('Moteur inconnu')
        start=time.perf_counter()
        self.backends[entry['id']]=backend
        try:
            if self.closed:raise RuntimeError(_('Moteur arrêté'))
            backend.warmup()
            if self.closed:raise RuntimeError(_('Moteur arrêté'))
        except Exception as exc:
            backend.close();self.backends.pop(entry['id'],None)
            if self.closed:raise
            if self.device=='cuda':
                self._use_cpu()
                return self._backend(entry)
            self.failed[entry['id']]=str(exc)
            if not self.warned:
                self.warned=True
                self.error(_('Modèle indisponible. Choisissez un autre modèle dans les préférences. ')+str(exc))
            raise
        self.telemetry.record('load',model=entry['id'],family=family,device=self.device,source=entry.get('source'),target=entry.get('target'),milliseconds=(time.perf_counter()-start)*1000)
        return backend
    def warmup(self):
        with self.lock:
            self._wait_for_release()
            if self.closed:raise RuntimeError(_('Moteur arrêté'))
            config=self.store.config(self.application())
            if not self.store.ready(self.application()):return
            self._prepare(self.store.route(config['family'],config['sources'][0],config['active']),config)
    def _prepare(self,route,config):
        self.family=config['family']
        signature=(tuple(e['id'] for e in route),bool(config.get('gpu',False)),config.get('cuda_index',0))
        if signature!=self.signature:
            for backend in self.backends.values():backend.close()
            self.backends.clear();self.failed.clear();self.device='cpu'
            self.signature=signature;self.device_index=config.get('cuda_index',0)
            if config.get('gpu',False):
                from .gpu_support import inspect,candidate
                gpu=candidate(inspect(),self.store,config['family'],route,self.device_index)
                if gpu and self.store.runtime_ready(config['family'],True):self.device='cuda'
                else:self._use_cpu()
        retained={e['id'] for e in route}
        for id in list(self.backends):
            if id not in retained:self.backends.pop(id).close()
        for entry in route:self._backend(entry)
    def translate(self,text,src,tgt,app=None,source_mode='manual',detection_ms=0.0,source_system='manual'):
        import time
        with self.lock:
            self._wait_for_release()
            if self.closed:raise RuntimeError(_('Moteur arrêté'))
            if src==tgt:return text
            config=self.store.config(app or self.application())
            route=self.store.route(config['family'],src,tgt)
            self._prepare(route,config)
            translation_start=time.perf_counter()
            fallback_epoch=self.fallback_epoch
            characters=len(text)
            for entry in route:
                start=time.perf_counter();success=False
                try:
                    backend=self._backend(entry)
                    try:text=backend.translate(text,entry.get('source',src),entry.get('target',tgt))
                    except Exception as exc:
                        if self.closed:raise
                        if self.device!='cuda' or not any(word in str(exc).lower() for word in ('cuda','cublas','gpu','out of memory')):raise
                        self._use_cpu()
                        text=self._backend(entry).translate(text,entry.get('source',src),entry.get('target',tgt))
                    success=True
                except Exception as exc:
                    if not self.warned:self.warned=True;self.error(str(exc))
                    raise
                finally:self.telemetry.record('translation',model=entry['id'],family=entry['family'],
                    device=self.device,source=entry.get('source',src),target=entry.get('target',tgt),milliseconds=(time.perf_counter()-start)*1000,success=success)
            self.telemetry.record('translation_total',model=' + '.join(entry['id'] for entry in route),
                family=config['family'],device=self.device,source=src,target=tgt,steps=len(route),characters=characters,
                milliseconds=(time.perf_counter()-translation_start)*1000,success=self.fallback_epoch==fallback_epoch,
                source_mode=source_mode,detection_ms=detection_ms,source_system=source_system)
            return text
    def close(self):
        self.closed=True
        # Interrupt child processes before waiting for an in-flight translation.
        for backend in list(self.backends.values()):
            if hasattr(backend,'closed'):backend.closed=True
            process=getattr(backend,'process',None)
            if process is not None and process.poll() is None:
                try:process.kill()
                except OSError:pass
        with self.lock:
            for backend in self.backends.values():backend.close()
            self.backends.clear();self.detector.close()
