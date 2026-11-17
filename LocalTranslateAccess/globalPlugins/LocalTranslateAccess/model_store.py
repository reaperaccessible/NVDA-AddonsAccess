"""Model catalogue, path planning, cancellable downloads and atomic installations."""
from .i18n import _
from collections import deque
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import ssl
import subprocess
import time
import urllib.error
import urllib.request
import zipfile

BASE=Path(__file__).resolve().parent
REPOSITORY='reaperaccessible/NVDA-AddonsAccess'
PACKS_URL=f'https://github.com/{REPOSITORY}/releases/download/translation-models/'
CATALOG_URL=f'https://raw.githubusercontent.com/{REPOSITORY}/main/LocalTranslateAccess/globalPlugins/LocalTranslateAccess/catalog.json'
USER_AGENT='LocalTranslateAccess-NVDA'

def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    with temp.open('w',encoding='utf-8') as out:
        json.dump(value,out,ensure_ascii=False,indent=2);out.flush();os.fsync(out.fileno())
    os.replace(temp,path)

class Cancelled(Exception):pass

def phase(progress,stage,detail=''):
    callback=getattr(progress,'phase',None)
    if callback:callback(stage,detail)

class Store:
    def __init__(self,root):
        self.root=Path(root).resolve()
        self.lock=threading.RLock()
        self.install_lock=threading.Lock()
        self.sizes={}
        try:
            saved=json.loads((self.root/'download-sizes.json').read_text(encoding='utf-8'))
            if isinstance(saved,dict):self.sizes={k:v for k,v in saved.items() if isinstance(v,int) and v>0}
        except (OSError,ValueError):pass
        self.catalog=json.loads((BASE/'catalog.json').read_text(encoding='utf-8'))
        shipped={entry['id']:entry for entry in self.catalog['entries']}
        try:
            updated=json.loads((self.root/'catalog.json').read_text(encoding='utf-8'))
            if updated.get('schema')==1 and isinstance(updated.get('entries'),list):
                for entry in updated['entries']:
                    pinned=shipped.get(entry.get('id'))
                    if pinned and pinned['version']==entry.get('version') and pinned.get('sha256'):
                        entry.update(sha256=pinned['sha256'],bytes=pinned.get('bytes'))
                families={entry['family'] for entry in updated['entries']}
                updated['entries'].extend(entry for entry in shipped.values() if entry['family'] not in families)
                updated['runtimes']={**updated.get('runtimes',{}),**self.catalog['runtimes']}
                self.catalog=updated
        except (OSError,ValueError,AttributeError):pass
        self.settings={'global':{'family':'bergamot','sources':['en'],'targets':['fr'],'active':'fr','configured':False},'apps':{}}
        try:
            saved=json.loads((self.root/'settings.json').read_text(encoding='utf-8'))
            if isinstance(saved.get('global'),dict) and isinstance(saved.get('apps'),dict):self.settings=saved
        except (OSError,ValueError,AttributeError):pass

    def path(self,id):
        if not re.fullmatch(r'[a-zA-Z0-9.-]+',id):raise ValueError(_('Identifiant de modèle invalide'))
        path=(self.root/'models'/id).resolve()
        if not path.is_relative_to(self.root/'models'):raise ValueError(_('Chemin de modèle invalide'))
        return path

    def config(self,app='global'):
        with self.lock:return dict(self.settings['apps'].get(app,self.settings['global']) if app!='global' else self.settings['global'])

    def initialize_target(self,code):
        with self.lock:
            value=self.settings['global']
            if value.get('configured') or value.get('target_initialized'):return
            value.update(active=code,targets=[code],target_initialized=True)
            atomic_json(self.root/'settings.json',self.settings)

    def set_target(self,context,code):
        if code not in self.languages():raise ValueError(_('Langue cible non prise en charge.'))
        with self.lock:
            value=self.config(context)
            value['active']=code
            value['targets']=list(dict.fromkeys(value.get('targets',[])+[code]))
            if value.get('pairs'):
                value['pairs']=[list(pair) for pair in dict.fromkeys(tuple(pair) for pair in value['pairs']+
                    [[source,code] for source in value['sources'] if source!=code])]
            value['target_initialized']=True
            if context=='global':self.settings['global']=value
            else:self.settings['apps'][context]=value
            atomic_json(self.root/'settings.json',self.settings)

    def configure(self,app,family,sources,targets,active,cuda_index=0,pairs=None,gpu=False):
        if not sources or not targets or active not in targets:raise ValueError(_('Choisissez les langues sources et cibles.'))
        value=dict(family=family,sources=list(dict.fromkeys(sources)),targets=list(dict.fromkeys(targets)),active=active,configured=True)
        if pairs is not None:
            self.plan_pairs(family,pairs)
            value['pairs']=[list(pair) for pair in dict.fromkeys(tuple(pair) for pair in pairs)]
        from .gpu_support import FAMILIES
        value['gpu']=bool(gpu and family in FAMILIES)
        value['cuda_index']=int(cuda_index)
        with self.lock:
            if app=='global':self.settings['global']=value
            else:self.settings['apps'][app]=value
            atomic_json(self.root/'settings.json',self.settings)

    def configure_simple(self,app,family,sources,target):
        """A configuration the add-on keeps up to date itself: every installed language, into one target."""
        self.configure(app,family,sources,[target],target,pairs=[[source,target] for source in sources])
        with self.lock:
            value=self.settings['global'] if app=='global' else self.settings['apps'][app]
            value['simple']=True
            atomic_json(self.root/'settings.json',self.settings)

    def entries(self,family):return [x for x in self.catalog['entries'] if x['family']==family]

    def download_specs(self,entries):
        # Model IDs and shared URLs are counted once, including pivot pairs.
        specs={}
        for entry in {e['id']:e for e in entries}.values():
            for spec in entry.get('files',{}).values() if entry.get('files') else [entry]:
                specs[spec['url']]=spec
        return list(specs.values())

    def download_size(self,entries):
        known=unknown=0
        with self.lock:
            for spec in self.download_specs(entries):
                size=spec.get('bytes') or self.sizes.get(spec['url'])
                if size:known+=size
                else:unknown+=1
        return known,unknown

    def resolve_download_sizes(self,entries,cancel):
        """Retrieve HTTP metadata only; callers must run this off the UI thread."""
        for spec in self.download_specs(entries):
            if cancel.is_set():return
            url=spec['url']
            if spec.get('bytes') or url in self.sizes:continue
            if not url.startswith('https://'):continue
            size=None
            try:
                request=urllib.request.Request(url,method='HEAD',headers={'Accept-Encoding':'identity','User-Agent':USER_AGENT})
                with urllib.request.urlopen(request,timeout=8) as response:
                    if response.geturl().startswith('https://'):size=int(response.headers.get('Content-Length','0'))
            except (OSError,ValueError):
                # Windows certificate store fallback, still a HEAD request.
                executable=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/curl.exe'
                try:
                    response=subprocess.run([str(executable),'--fail','--head','--location','--silent','--show-error',
                        '--proto','=https','--proto-redir','=https','--max-time','8',url],capture_output=True,
                        timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
                    values=re.findall(r'^content-length:\s*(\d+)\s*$',response.stdout.decode('ascii','ignore'),re.M|re.I)
                    if response.returncode==0 and values:size=int(values[-1])
                except (OSError,subprocess.SubprocessError):pass
            if size and not cancel.is_set():
                with self.lock:
                    self.sizes[url]=size
                    atomic_json(self.root/'download-sizes.json',self.sizes)

    def languages(self,family=None):
        entries=self.catalog['entries'] if family is None else self.entries(family)
        return sorted({code for x in entries for code in (x.get('languages') or [x['source'],x['target']])})

    def route(self,family,source,target,installed_only=False):
        if source==target:return []
        entries=self.entries(family)
        for entry in entries:
            if ((entry.get('languages') and source in entry['languages'] and target in entry['languages'])
                or (entry.get('source'),entry.get('target'))==(source,target)) and self.installed(entry):return [entry]
        # Prefer an already installed route, including installed pivot packages.
        installed={e['id'] for e in self.installed_entries() if not e.get('outdated')}
        try:return self._route([e for e in entries if e['id'] in installed],source,target)
        except ValueError:
            if installed_only:raise
        return self._route(entries,source,target)

    def _route(self,entries,source,target):
        if source==target:return []
        for entry in entries:
            if entry.get('languages') and source in entry['languages'] and target in entry['languages']:return [entry]
            if (entry.get('source'),entry.get('target'))==(source,target):return [entry]
        # Prefer a two-stage English pivot before considering other routes.
        first=next((e for e in entries if (e.get('source'),e.get('target'))==(source,'en')),None)
        second=next((e for e in entries if (e.get('source'),e.get('target'))==('en',target)),None)
        if first and second:return [first,second]
        queue=deque([(source,[])]);seen={source}
        while queue:
            language,path=queue.popleft()
            if len(path)>=3:continue
            for entry in entries:
                if entry.get('source')!=language:continue
                next_=entry['target'];chain=path+[entry]
                if next_==target:return chain
                if next_ not in seen:seen.add(next_);queue.append((next_,chain))
        raise ValueError(_('Aucune traduction disponible : {v0} → {v1}.').format(v0=source, v1=target))

    def available_models(self,context,source,target):
        sources=self.config(context)['sources'] if source=='auto' else [source]
        required=[code for code in sources if code!=target]
        if not required:return []
        result=[]
        installed={e['id'] for e in self.installed_entries() if not e.get('outdated')}
        for family in sorted({e['family'] for e in self.catalog['entries']}):
            if not self.runtime_ready(family):continue
            try:
                entries=[e for e in self.entries(family) if e['id'] in installed]
                for code in required:self._route(entries,code,target)
            except ValueError:continue
            result.append(family)
        return result

    def set_model(self,context,family,source,target):
        if family not in self.available_models(context,source,target):
            raise ValueError(_('Le modèle choisi n’est pas installé pour toutes les langues nécessaires.'))
        previous=self.config(context)
        sources=previous['sources'] if source=='auto' else [source]
        self.configure(context,family,sources,[target],target,cuda_index=previous.get('cuda_index',0),
            pairs=[[code,target] for code in sources if code!=target],gpu=previous.get('gpu',False) and previous.get('family')==family)

    def set_gpu(self,context,gpu,index=0):
        from .gpu_support import FAMILIES
        with self.lock:
            value=dict(self.config(context));value['gpu']=bool(gpu and value['family'] in FAMILIES);value['cuda_index']=int(index)
            if context=='global':self.settings['global']=value
            else:self.settings['apps'][context]=value
            atomic_json(self.root/'settings.json',self.settings)

    def plan(self,family,sources,targets):
        entries={};routes=[]
        for source in sources:
            for target in targets:
                chain=self.route(family,source,target)
                for entry in chain:entries[entry['id']]=entry
                codes=[source]+[e.get('target',target) for e in chain]
                routes.append(' → '.join(codes))
        return list(entries.values()),routes

    def installed(self,entry):
        try:
            path=self.path(entry['id'])
            marker=json.loads((path/'installed.json').read_text(encoding='utf-8'))
            return marker.get('version')==entry['version'] and all((path/name).is_file() for name in marker.get('files',[])) and bool(marker.get('files'))
        except (OSError,ValueError):return False

    def plan_pairs(self,family,pairs):
        entries={};routes=[]
        if not pairs:raise ValueError(_('Ajoutez au moins une paire de langues.'))
        for source,target in pairs:
            if source==target:raise ValueError(_('Choisissez deux langues différentes.'))
            chain=self.route(family,source,target)
            for entry in chain:entries[entry['id']]=entry
            routes.append(' → '.join([source]+[e.get('target',target) for e in chain]))
        return list(entries.values()),routes

    def ready(self,app):
        config=self.config(app)
        if not config.get('configured'):return False
        if not self.runtime_ready(config['family'],config.get('gpu',False)):return False
        try:
            plan,_unused=(self.plan_pairs(config['family'],config['pairs']) if config.get('pairs') else self.plan(config['family'],config['sources'],config['targets']))
            return all(self.installed(x) for x in plan)
        except ValueError:return False

    def runtime_names(self,family,gpu=False):
        if family=='bergamot':return ['node']
        if family=='hy':return ['llama_cpu']+(['cuda'] if gpu else [])
        return ['ct2_cuda'] if gpu and family in ('argos','opus','nllb','small100','m2m100') else []

    def runtime_files(self,name):
        return {'node':['node.exe'],'llama_cpu':['llama-server.exe'],
            'cuda':['llama-server.exe','ggml-cuda.dll','cudart64_13.dll'],
            'ct2_cuda':['cublasLt64_12.dll','cublas64_12.dll','nvrtc-builtins64_129.dll','nvrtc64_120_0.dll']}[name]

    def runtime_installed(self,name):
        if name=='llama_cpu' and self.runtime_installed('cuda'):return True
        return all((self.root/'runtimes'/name/file).is_file() for file in self.runtime_files(name))

    def runtime_ready(self,family,gpu=False):return all(self.runtime_installed(name) for name in self.runtime_names(family,gpu))

    def runtime_specs(self,family,gpu=False):
        result=[]
        for name in self.runtime_names(family,gpu):
            if self.runtime_installed(name):continue
            specs=self.catalog['runtimes'][name]
            result.extend(specs if isinstance(specs,list) else [specs])
        return result

    def llama_executable(self,gpu=False):
        name='cuda' if gpu or not (self.root/'runtimes/llama_cpu/llama-server.exe').is_file() else 'llama_cpu'
        return self.root/'runtimes'/name/'llama-server.exe'

    def installed_entries(self):
        result=[];current={e['id']:e for e in self.catalog['entries']}
        directory=self.root/'models'
        if not directory.exists():return result
        for path in directory.iterdir():
            try:
                if not path.is_dir():continue
                self.path(path.name)
                marker=json.loads((path/'installed.json').read_text(encoding='utf-8'))
                entry=current.get(path.name) or marker.get('entry')
                if not isinstance(entry,dict):continue
                entry=dict(entry)
                if entry['version']!=marker['version']:entry['outdated']=True
                entry['version']=marker['version']
                if self.installed(entry):result.append(entry)
            except (OSError,ValueError,KeyError):continue
        return result

    def refresh_catalog(self):
        request=urllib.request.Request(CATALOG_URL,headers={'User-Agent':USER_AGENT})
        with urllib.request.urlopen(request,timeout=20) as response:updated=json.load(response)
        if updated.get('schema')!=1 or not isinstance(updated.get('entries'),list):raise ValueError(_('Adresse de registre inattendue'))
        for entry in updated['entries']:
            self.path(entry['id'])
            for spec in entry.get('files',{}).values():
                if not spec['url'].startswith(PACKS_URL):raise ValueError(_('Adresse de registre inattendue'))
        with self.lock:
            self.catalog={**self.catalog,'entries':updated['entries'],
                'runtimes':{**self.catalog['runtimes'],**updated.get('runtimes',{})}}
            atomic_json(self.root/'catalog.json',self.catalog)

    def namespace(self,app):
        family=self.config(app)['family']
        versions=sorted((e['id'],e['version']) for e in self.entries(family))
        digest=hashlib.sha256(json.dumps(versions).encode()).hexdigest()[:16]
        return 'adaptive-'+family+'-'+digest

    def remove(self,entry):
        with self.install_lock:
            path=self.path(entry['id'])
            if path.exists():shutil.rmtree(path)

    def _check(self,cancel):
        if cancel.is_set():raise Cancelled(_('Téléchargement annulé.'))

    def download(self,url,destination,cancel,progress,sha256=None,expected=None):
        phase(progress,'download',Path(destination).name)
        try:return self._download(url,destination,cancel,progress,sha256,expected)
        except (TimeoutError,ConnectionError):
            self._check(cancel)
            destination=Path(destination);part=destination.with_suffix(destination.suffix+'.part')
            self._windows_download(url,part,cancel,progress,expected)
            return self._finish_download(part,destination,cancel,sha256,expected,progress)

    def _download(self,url,destination,cancel,progress,sha256=None,expected=None):
        if not url.startswith('https://'):raise ValueError(_('Téléchargement HTTPS requis'))
        destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
        if destination.exists() and sha256:
            with destination.open('rb') as stream:existing=hashlib.file_digest(stream,'sha256').hexdigest()
            if existing==sha256 and (not expected or destination.stat().st_size==expected):return destination
        part=destination.with_suffix(destination.suffix+'.part')
        offset=part.stat().st_size if part.exists() else 0
        if expected and offset==expected and sha256:
            return self._finish_download(part,destination,cancel,sha256,expected,progress)
        if expected and offset>expected:part.unlink();offset=0
        headers={'User-Agent':USER_AGENT,'Accept-Encoding':'identity'}
        if offset:headers['Range']=f'bytes={offset}-'
        self._check(cancel)
        try:
            response=urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=15)
        except urllib.error.URLError as exc:
            # Schannel can retrieve missing intermediate certificates through Windows.
            # Certificate verification remains enabled; never use an insecure SSL context.
            if not isinstance(exc.reason,ssl.SSLCertVerificationError):raise
            self._windows_download(url,part,cancel,progress,expected)
            return self._finish_download(part,destination,cancel,sha256,expected,progress)
        with response:
            if not response.geturl().startswith('https://'):raise ValueError('Redirection HTTPS requise')
            append=bool(offset and response.status==206)
            if not append:offset=0
            length=response.headers.get('Content-Length')
            total=offset+int(length) if length else expected
            with part.open('ab' if append else 'wb') as out:
                current=offset
                while True:
                    self._check(cancel)
                    chunk=response.read(1024*512)
                    if not chunk:break
                    out.write(chunk);current+=len(chunk)
                    if expected and current>expected:raise ValueError(_('Taille du téléchargement inattendue'))
                    progress(current,total)
                out.flush();os.fsync(out.fileno())
        return self._finish_download(part,destination,cancel,sha256,expected,progress)

    def _windows_download(self,url,part,cancel,progress,expected):
        executable=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/curl.exe'
        if not executable.is_file():raise RuntimeError(_('Certificat TLS non reconnu ; téléchargement Windows indisponible.'))
        process=subprocess.Popen([str(executable),'--fail','--location','--silent','--show-error',
            '--proto','=https','--proto-redir','=https','--connect-timeout','15',
            '--speed-limit','1','--speed-time','60',
            '--continue-at','-','--output',str(part),url],stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            while process.poll() is None:
                self._check(cancel)
                progress(part.stat().st_size if part.exists() else 0,expected)
                time.sleep(.2)
            error=process.stderr.read().decode('utf-8','replace')
            if process.returncode:raise RuntimeError(_('Téléchargement Windows : ')+error[:500])
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=5)
            process.stderr.close()

    def _finish_download(self,part,destination,cancel,sha256,expected,progress=None):
        phase(progress,'verify')
        if expected and part.stat().st_size!=expected:raise ValueError(_('Téléchargement incomplet'))
        if sha256:
            with part.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
            if digest!=sha256:
                part.unlink();raise ValueError(_('Somme de contrôle incorrecte'))
        self._check(cancel);os.replace(part,destination)
        return destination

    def extract(self,archive,destination,cancel,select=lambda name:True):
        destination=Path(destination).resolve();destination.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(archive) as package:
            expanded=0
            for item in package.infolist():
                self._check(cancel)
                # Reject symlinks, drive-qualified filenames and traversal before extracting.
                name=item.filename.replace('\\','/')
                path=(destination/name).resolve()
                if not path.is_relative_to(destination) or ':' in name or (item.external_attr>>16)&0o170000==0o120000:
                    raise ValueError(_('Chemin interdit dans une archive'))
                if item.is_dir() or not select(name):continue
                expanded+=item.file_size
                if expanded>4*1024**3:raise ValueError('Archive trop volumineuse')
                path.parent.mkdir(parents=True,exist_ok=True)
                with package.open(item) as src,path.open('wb') as out:
                    while True:
                        self._check(cancel);chunk=src.read(1024*512)
                        if not chunk:break
                        out.write(chunk)

    def install(self,entry,cancel,progress):
        with self.install_lock:
            if self.installed(entry):return
            self.root.mkdir(parents=True,exist_ok=True)
            downloads=self.root/'downloads';downloads.mkdir(exist_ok=True)
            staging=Path(tempfile.mkdtemp(prefix='install-',dir=self.root)).resolve()
            try:
                family=entry['family']
                if family in ('nllb','small100','m2m100'):
                    model=staging/'model';model.mkdir()
                    for name,file in entry['files'].items():
                        if name not in ('model.bin','config.json','shared_vocabulary.json','tokenizer.json','README.md','sentencepiece.bpe.model','LICENSE','NOTICE','conversion.json'):
                            raise ValueError(_('Fichier de modèle multilingue inattendu.'))
                        downloaded=self.download(file['url'],downloads/(entry['id']+'-'+name),cancel,progress,file['sha256'],file['bytes'])
                        phase(progress,'install')
                        target=staging/name if name in ('tokenizer.json','README.md','sentencepiece.bpe.model','LICENSE','NOTICE','conversion.json') else model/name
                        shutil.copy2(downloaded,target)
                    # Keep verified downloads until the complete model is committed.
                elif family=='bergamot':
                    filenames={'model':'model.bin','lexicalShortlist':'lexicalShortlist.bin','vocab':'vocab.bin'}
                    for key,file in entry['files'].items():
                        downloaded=self.download(file['url'],downloads/(entry['id']+'-'+key+'.gz'),cancel,progress)
                        phase(progress,'unpack')
                        target=staging/filenames[key]
                        with gzip.open(downloaded,'rb') as src,target.open('wb') as out:
                            shutil.copyfileobj(src,out)
                        if file.get('sha256'):
                            phase(progress,'verify')
                            with target.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
                            if digest!=file['sha256']:raise ValueError(_('Somme de contrôle du modèle incorrecte'))
                        downloaded.unlink()
                elif family=='hy':
                    downloaded=self.download(entry['url'],downloads/(entry['id']+'.gguf'),cancel,progress,entry['sha256'],entry['bytes'])
                    phase(progress,'install')
                    shutil.move(str(downloaded),str(staging/'model.gguf'))
                else:
                    archive=self.download(entry['url'],downloads/(entry['id']+'.zip'),cancel,progress,entry.get('sha256'),entry.get('bytes'))
                    raw=staging/'raw'
                    phase(progress,'unpack')
                    if family=='argos':
                        self.extract(archive,raw,cancel,lambda n:'/stanza/' not in '/'+n)
                        metadata=next(raw.rglob('metadata.json'))
                        package=metadata.parent
                        shutil.copytree(package/'model',staging/'model')
                        if (package/'sentencepiece.model').is_file():
                            shutil.copy2(package/'sentencepiece.model',staging/'source.spm')
                            shutil.copy2(package/'sentencepiece.model',staging/'target.spm')
                        elif (package/'bpe.model').is_file():
                            shutil.copy2(package/'bpe.model',staging/'bpe.model')
                            info=json.loads(metadata.read_text(encoding='utf-8'))
                            atomic_json(staging/'tokenizer.json',dict(kind='bpe',source=info.get('from_code',entry['source']),target=info.get('to_code',entry['target'])))
                        else:
                            raise ValueError(_('Le paquet Argos ne contient ni sentencepiece.model ni bpe.model. Choisissez un autre modèle et signalez cette paire.'))
                        for name in ('README.md','metadata.json'):
                            if (package/name).exists():shutil.copy2(package/name,staging/name)
                    elif family=='opus':
                        self.extract(archive,raw,cancel)
                        decoder=next(raw.rglob('decoder.yml'));package=decoder.parent
                        from .detection import libraries
                        ct2,_unused,_unused=libraries()
                        phase(progress,'convert')
                        ct2.converters.OpusMTConverter(str(package)).convert(str(staging/'model'),quantization='int8')
                        spms=sorted(package.glob('*.spm'))
                        source=package/'source.spm' if (package/'source.spm').exists() else next((p for p in spms if '.en.' in p.name or p.name.endswith('en.spm')),None)
                        target=package/'target.spm' if (package/'target.spm').exists() else next((p for p in spms if '.fr.' in p.name or p.name.endswith('fr.spm')),None)
                        if source is None or target is None:raise ValueError('Tokenizers OPUS manquants')
                        shutil.copy2(source,staging/'source.spm');shutil.copy2(target,staging/'target.spm')
                        for name in ('README.md','LICENSE'):
                            if (package/name).exists():shutil.copy2(package/name,staging/name)
                    shutil.rmtree(raw);archive.unlink()
                self._check(cancel)
                phase(progress,'install')
                files=[p.relative_to(staging).as_posix() for p in staging.rglob('*') if p.is_file()]
                if not files:raise ValueError(_('Modèle vide'))
                atomic_json(staging/'installed.json',dict(id=entry['id'],entry=entry,version=entry['version'],files=files,
                    bytes=sum((staging/p).stat().st_size for p in files)))
                destination=self.path(entry['id']);destination.parent.mkdir(parents=True,exist_ok=True)
                if destination.exists():shutil.rmtree(destination)
                os.replace(staging,destination)
                if family in ('nllb','small100','m2m100'):
                    for name in entry['files']:(downloads/(entry['id']+'-'+name)).unlink(missing_ok=True)
                phase(progress,'ready')
            finally:
                if staging.exists() and staging.is_relative_to(self.root):shutil.rmtree(staging)

    def ensure_runtime(self,family,cancel,progress,gpu=False):
        for name in self.runtime_names(family,gpu):
            if not self.runtime_installed(name):self._install_runtime(name,cancel,progress)

    def _install_runtime(self,name,cancel,progress):
        directory=self.root/'runtimes'/name
        required=self.runtime_files(name)
        if all((directory/p).exists() for p in required):return
        specs=self.catalog['runtimes'][name]
        if not isinstance(specs,list):specs=[specs]
        self.root.mkdir(parents=True,exist_ok=True)
        staging=Path(tempfile.mkdtemp(prefix='runtime-',dir=self.root)).resolve()
        try:
            for index,spec in enumerate(specs):
                archive=self.download(spec['url'],staging/f'archive-{index}.zip',cancel,progress,spec['sha256'],spec.get('bytes'))
                unpack=staging/f'raw-{index}'
                phase(progress,'unpack')
                self.extract(archive,unpack,cancel)
                for file in unpack.rglob('*'):
                    if file.is_file() and (name=='node' and file.name in ('node.exe','LICENSE') or name!='node' and (file.suffix.lower() in ('.dll','.exe') or 'license' in file.name.lower())):
                        shutil.copy2(file,staging/file.name)
                shutil.rmtree(unpack);archive.unlink()
            if not all((staging/p).exists() for p in required):raise ValueError(_('Moteur téléchargé incomplet'))
            phase(progress,'install')
            self._check(cancel);directory.parent.mkdir(parents=True,exist_ok=True)
            if directory.exists():shutil.rmtree(directory)
            os.replace(staging,directory)
        finally:
            if staging.exists() and staging.is_relative_to(self.root):shutil.rmtree(staging)
