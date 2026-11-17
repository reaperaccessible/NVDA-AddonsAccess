"""Personal GPU engine: local llama.cpp C++ process, no extra Python runtime."""
from .i18n import _
import json
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
import urllib.request
from .detection import libraries as _libraries

BASE=Path(__file__).resolve().parent
HY_LANGUAGES={
    'zh':'Chinese','zh-hans':'Simplified Chinese','zh-hant':'Traditional Chinese',
    'en':'English','fr':'French','pt':'Portuguese','es':'Spanish','ja':'Japanese',
    'tr':'Turkish','ru':'Russian','ar':'Arabic','ko':'Korean','th':'Thai','it':'Italian',
    'de':'German','vi':'Vietnamese','ms':'Malay','id':'Indonesian','tl':'Filipino',
    'hi':'Hindi','pl':'Polish','cs':'Czech','nl':'Dutch','km':'Khmer','my':'Burmese',
    'fa':'Persian','gu':'Gujarati','ur':'Urdu','te':'Telugu','mr':'Marathi','he':'Hebrew',
    'bn':'Bengali','ta':'Tamil','uk':'Ukrainian','bo':'Tibetan','kk':'Kazakh',
    'mn':'Mongolian','ug':'Uyghur','yue':'Cantonese',
}


class GpuEngine:
    def __init__(self, model_path_getter, executable_getter, cuda_index=0, use_gpu=True):
        self.model_path_getter=model_path_getter
        self.executable_getter=executable_getter
        self.cuda_index=int(cuda_index)
        self.use_gpu=bool(use_gpu)
        self.device='cuda' if self.use_gpu else 'cpu'
        self.lock=threading.RLock()
        self.closed=False
        self.process=None
        self.port=None
        self.key=secrets.token_urlsafe(32)
        self.lid=None
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.last_metrics={}
        self.ready=False

    def _http(self,path,data=None,timeout=30):
        headers={'Authorization':'Bearer '+self.key,'Content-Type':'application/json'}
        body=json.dumps(data,ensure_ascii=False).encode('utf-8') if data is not None else None
        request=urllib.request.Request(f'http://127.0.0.1:{self.port}'+path,data=body,headers=headers)
        with self.opener.open(request,timeout=timeout) as response:
            return json.load(response)

    def warmup(self):
        with self.lock:
            if self.closed:raise RuntimeError('GPU engine stopped')
            if self.ready and self.process and self.process.poll() is None:
                return
            if self.process and self.process.poll() is None:
                self.process.terminate();self.process.wait(timeout=5)
            self.ready=False
            model=Path(self.model_path_getter()).expanduser().resolve()
            executable=Path(self.executable_getter())
            if not model.is_file() or not executable.is_file():
                raise FileNotFoundError('GPU model or llama-server missing')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));self.port=sock.getsockname()[1]
            args=[str(executable),'--model',str(model),'--host','127.0.0.1','--port',str(self.port),
                  '--api-key',self.key,'--device',f'CUDA{self.cuda_index}' if self.use_gpu else 'none',
                  '--n-gpu-layers','999' if self.use_gpu else '0','--ctx-size','8192','--parallel','1',
                  '--jinja','--flash-attn','on' if self.use_gpu else 'off','--no-webui']
            self.process=subprocess.Popen(args,cwd=str(executable.parent),stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            # Drain logs so a full pipe can never block the inference process.
            self.startup_log=[]
            def drain(process):
                for line in process.stderr:
                    text=line.decode('utf-8','replace').rstrip()
                    if len(self.startup_log)<1000:self.startup_log.append(text)
            threading.Thread(target=drain,args=(self.process,),daemon=True).start()
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                if self.closed or self.process.poll() is not None:
                    raise RuntimeError('GPU server startup failed: '+'\n'.join(self.startup_log[-6:]))
                try:
                    self._http('/health',timeout=.5)
                    break
                except Exception:
                    time.sleep(.1)
            else:
                self.process.terminate();self.process.wait(timeout=5)
                raise TimeoutError('GPU server startup timeout')
            _unused,_unused,fasttext=_libraries(False)
            self.lid=fasttext.load_model(str(BASE/'assets/lid.176.ftz'))
            self.ready=True

    def detect(self, text):
        with self.lock:
            self.warmup()
            labels, probabilities = self.lid.predict(' '.join(text.split())[:4000], k=1)
            if not labels:
                raise RuntimeError('language detection unavailable')
            return labels[0].removeprefix('__label__'), float(probabilities[0])

    def translate(self,text,src,tgt):
        with self.lock:
            self.warmup()
            if src=='auto':
                src, confidence = self.detect(text)
            if src==tgt or {src,tgt}<= {'zh','zh-hans'}:
                self.last_metrics={'engine':'source equals target, unchanged'}
                return text
            if src not in HY_LANGUAGES or tgt not in HY_LANGUAGES:
                # Preserve the coverage of the shareable multilingual variant.
                self.last_metrics={'engine':'NLLB CPU fallback'}
                raise ValueError(_('Langue non prise en charge par ce modèle GPU'))
            prompt=(f'Translate the following text from {HY_LANGUAGES[src]} into {HY_LANGUAGES[tgt]}. '
                    'You must ONLY output the translated result without any additional explanation:\n'+text)
            reply=self._http('/v1/chat/completions',{
                'messages':[{'role':'user','content':prompt}], 'temperature':0,
                'top_p':.6,'top_k':20,'repeat_penalty':1.05,'max_tokens':512,
                'stream':False,'cache_prompt':True,
            })
            choice=reply['choices'][0]
            if choice.get('finish_reason')=='length':
                raise RuntimeError('GPU translation truncated')
            value=choice['message'].get('content','').strip()
            if not value:raise RuntimeError('empty GPU translation')
            self.last_metrics={'engine':'Hy-MT2-7B Q8 '+self.device,'usage':reply.get('usage'),'timings':reply.get('timings')}
            return value

    def _terminate_owned(self):
        process=self.process
        if process and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=3)

    def close(self):
        self.closed=True
        self.ready=False
        # Stop immediately, then check again under the startup lock: warmup
        # may have created the process just after the first snapshot.
        self._terminate_owned()
        with self.lock:
            self._terminate_owned()
            self.lid=None
