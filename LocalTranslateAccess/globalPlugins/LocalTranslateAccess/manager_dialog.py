"""Accessible native wx controls; all inventory and download work runs off the UI thread."""
from .i18n import _, set_window_direction
import threading
import time
import wx
from . import hardware
from .model_store import Cancelled
from .source_settings import SourceSettings
from .cache_settings import CacheSettings

FAMILIES=[('bergamot',_('Firefox / Bergamot — CPU')),
          ('nllb',_('NLLB-200 600M INT8 — CPU, multilingue')),
          ('small100',_('SMaLL-100 330M INT8 — CPU, multilingue, priorité à la rapidité')),
          ('m2m100',_('M2M-100 418M INT8 — CPU, multilingue, priorité à la rapidité'))]

MODEL_NAMES={'argos':'Argos Translate','bergamot':'Firefox / Bergamot','opus':'Marian / OPUS-MT',
             'hy':'Hy-MT2 7B Q8','nllb':'NLLB-200 600M INT8',
             'small100':'SMaLL-100 330M INT8','m2m100':'M2M-100 418M INT8'}

class ManagerPanel(wx.Panel):
    """The whole translation configuration: languages, installed models, cache and performance.

    It is hosted by NVDA's Settings dialog. `finish` is called with a wx result code
    when the panel asks its host to close.
    """
    def __init__(self,parent,store,telemetry,app,hooks,activate=False,finish=None):
        super().__init__(parent)
        set_window_direction(self)
        self.finish_callback=finish
        self.store,self.telemetry,self.app,self.hooks=store,telemetry,app,hooks
        self.activate=activate;self.cancel=threading.Event();self.busy=False;self.dead=False
        self.info=None;self.last_progress=0;self.user_family_changed=False
        outer=wx.BoxSizer(wx.VERTICAL)
        self.tabs=wx.Notebook(self)
        source_hooks=dict(hooks,download_model=self.download_model,apply_parent=self,cancel=self.cancel,
            installed_changed=lambda:self.refresh_installed())
        self.source_page=SourceSettings(self.tabs,store,source_hooks,self.sources_saved)
        self.tabs.AddPage(self.source_page,_('Langues'))
        # Keep the legacy controls private for shared model/test operations.
        # Language and download choices are handled by the setup wizard.
        self.page=wx.ScrolledWindow(self);self.page.Hide()
        self.models_page=wx.Panel(self.tabs);self.stats_page=wx.Panel(self.tabs)
        self.page.SetScrollRate(0,10)
        self.tabs.AddPage(self.models_page,_('Modèles installés'))
        self.cache_page=CacheSettings(self.tabs,hooks.get('cache_directory',lambda:store.root.parent/'cache')(),hooks)
        self.tabs.AddPage(self.cache_page,_('Cache de traduction'))
        self.tabs.AddPage(self.stats_page,_('Performances'))
        sizer=wx.BoxSizer(wx.VERTICAL)
        def add(label,control):
            sizer.Add(wx.StaticText(self.page,label=label),0,wx.LEFT|wx.RIGHT|wx.TOP,8)
            sizer.Add(control,0,wx.ALL|wx.EXPAND,8)
        self.scope=wx.Choice(self.page,choices=[_('Application : ')+app,_('Réglages généraux')])
        self.scope.SetSelection(1 if app=='global' else 0);add(_('Configuration à modifier :'),self.scope)
        self.machine=wx.TextCtrl(self.page,value=_('Analyse de la machine en cours…'),style=wx.TE_READONLY|wx.TE_MULTILINE,size=(-1,70))
        self.machine.Hide()
        setup=wx.Button(self.page,label=_('Ouvrir l’assistant de choix des langues et du modèle'))
        sizer.Add(setup,0,wx.ALL|wx.EXPAND,8)
        setup.Bind(wx.EVT_BUTTON,self.on_setup)
        self.family=wx.Choice(self.page,choices=[name for id,name in FAMILIES]);add(_('Famille de modèles :'),self.family)
        row=wx.BoxSizer(wx.HORIZONTAL)
        left=wx.BoxSizer(wx.VERTICAL);right=wx.BoxSizer(wx.VERTICAL)
        self.inherit=wx.CheckBox(self.page,label=_('Utiliser la langue source des réglages généraux'))
        left.Add(self.inherit,0,wx.ALL,4)
        left.Add(wx.StaticText(self.page,label=_('Langue source :')),0,wx.ALL,4)
        self.source=wx.Choice(self.page,name=_('Langue source'));left.Add(self.source,0,wx.ALL|wx.EXPAND,4)
        left.Add(wx.StaticText(self.page,label=_('Langues sources attendues en mode Auto :')),0,wx.ALL,4)
        self.sources=wx.CheckListBox(self.page,size=(-1,120),name=_('Langues sources attendues en mode Auto'));left.Add(self.sources,1,wx.ALL|wx.EXPAND,4)
        self.remember=wx.CheckBox(self.page,label=_('Mémoriser la langue détectée pour cette application'))
        left.Add(self.remember,0,wx.ALL,4)
        self.forget=wx.CheckBox(self.page,label=_('Oublier la langue mémorisée pour cette application'))
        left.Add(self.forget,0,wx.ALL,4)
        right.Add(wx.StaticText(self.page,label=_('Langues cibles à installer (plusieurs choix possibles) :')),0,wx.ALL,4)
        self.targets=wx.CheckListBox(self.page,size=(-1,150),name=_('Langues cibles à installer'));right.Add(self.targets,1,wx.ALL|wx.EXPAND,4)
        right.Add(wx.StaticText(self.page,label=_('Traduire les annonces de NVDA en :')),0,wx.ALL,4)
        self.active=wx.Choice(self.page);right.Add(self.active,0,wx.ALL|wx.EXPAND,4)
        row.Add(left,1,wx.EXPAND|wx.ALL,4);row.Add(right,1,wx.EXPAND|wx.ALL,4)
        sizer.Add(row,1,wx.EXPAND)
        self.copy=wx.CheckBox(self.page,label=_('Copier la traduction dans le presse-papiers'))
        self.copy.SetValue(self.hooks['copy_get']());sizer.Add(self.copy,0,wx.ALL,8)
        self.preview=wx.TextCtrl(self.page,style=wx.TE_READONLY|wx.TE_MULTILINE,size=(-1,105))
        add(_('Modèles nécessaires et téléchargements :'),self.preview)
        self.status=wx.StaticText(self.page,label=_('Choisissez vos langues puis téléchargez les modèles nécessaires.'))
        sizer.Add(self.status,0,wx.ALL|wx.EXPAND,8)
        self.gauge=wx.Gauge(self.page,range=100);sizer.Add(self.gauge,0,wx.ALL|wx.EXPAND,8)
        self.operation_status=wx.TextCtrl(self.page,value=_('Aucun téléchargement en cours.'),style=wx.TE_MULTILINE|wx.TE_READONLY,size=(-1,60))
        sizer.Add(self.operation_status,0,wx.ALL|wx.EXPAND,8)
        buttons=wx.BoxSizer(wx.HORIZONTAL)
        self.download=wx.Button(self.page,label=_('Télécharger les modèles nécessaires'))
        self.cancel_download=wx.Button(self.page,label=_('Annuler le téléchargement'));self.cancel_download.Enable(False)
        self.save=wx.Button(self.page,label=_('Appliquer et activer') if activate else _('Appliquer'))
        for button in (self.download,self.cancel_download,self.save):buttons.Add(button,0,wx.ALL,4)
        sizer.Add(buttons,0,wx.ALL,4);self.page.SetSizer(sizer);self.page.FitInside()
        installed=wx.BoxSizer(wx.VERTICAL)
        self.installed_list=wx.ListBox(self.models_page)
        installed.Add(wx.StaticText(self.models_page,label=_('Sélectionnez un modèle. Le cache des traductions est conservé lors de sa suppression.')),0,wx.ALL,8)
        installed.Add(self.installed_list,1,wx.ALL|wx.EXPAND,8)
        self.remove=wx.Button(self.models_page,label=_('Supprimer le modèle sélectionné'))
        installed.Add(self.remove,0,wx.ALL,8)
        self.refresh=wx.Button(self.models_page,label=_('Actualiser le catalogue des modèles'))
        installed.Add(self.refresh,0,wx.ALL,8);self.models_page.SetSizer(installed)
        stats=wx.BoxSizer(wx.VERTICAL)
        self.statistics=wx.TextCtrl(self.stats_page,style=wx.TE_MULTILINE|wx.TE_READONLY)
        self.statistics.SetName(_('Temps de chargement et temps moyen de traduction'))
        stats.Add(self.statistics,1,wx.ALL|wx.EXPAND,8)
        reset=wx.Button(self.stats_page,label=_('Effacer le journal de performances'))
        stats.Add(reset,0,wx.ALL,8);self.stats_page.SetSizer(stats)
        outer.Add(self.tabs,1,wx.ALL|wx.EXPAND,8)
        # Keep this action outside the native notebook's accessibility tree.
        outer.Add(self.source_page.apply,0,wx.ALL,8)
        self.SetSizer(outer)
        self.tabs.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED,self.on_page_changed)
        self.scope.Bind(wx.EVT_CHOICE,self.load)
        self.family.Bind(wx.EVT_CHOICE,self.change_family)
        self.source.Bind(wx.EVT_CHOICE,self.update_preview)
        self.inherit.Bind(wx.EVT_CHECKBOX,self.update_preview)
        self.sources.Bind(wx.EVT_CHECKLISTBOX,self.update_preview)
        self.targets.Bind(wx.EVT_CHECKLISTBOX,self.change_targets)
        self.active.Bind(wx.EVT_CHOICE,self.update_preview)
        self.download.Bind(wx.EVT_BUTTON,self.on_download)
        self.cancel_download.Bind(wx.EVT_BUTTON,lambda event:self.cancel.set())
        self.save.Bind(wx.EVT_BUTTON,self.on_save)
        self.remove.Bind(wx.EVT_BUTTON,self.on_remove)
        self.refresh.Bind(wx.EVT_BUTTON,self.on_refresh)
        reset.Bind(wx.EVT_BUTTON,self.on_reset)
        self.load();self.refresh_installed();self.statistics.SetValue(self.telemetry.summary(self.hooks['label']))
        threading.Thread(target=self.inventory,daemon=True).start()

    def language_label(self,code):return self.hooks['label'](code)
    def on_page_changed(self,event):
        self.source_page.apply.Show(self.tabs.GetSelection()==0)
        if self.tabs.GetSelection()==2 and not self.cache_page.loaded:self.cache_page.reload()
        self.Layout()
        event.Skip()
    def sources_saved(self):
        self.load()
    def download_model(self,context,source,target,on_done):
        if self.busy or self.dead:return
        sources=self.store.config(context)['sources'] if source=='auto' else [source]
        pairs=[(code,target) for code in sources if code!=target]
        if not pairs:
            self.source_page.status.SetLabel(_('Les langues sont identiques : aucun modèle n’est nécessaire.'));return
        self.busy=True
        def run():
            wizard=None;family=None
            try:
                if self.dead:return
                from .setup_wizard import SetupWizard
                wizard=SetupWizard(self,self.store,self.telemetry,context,self.hooks,
                    activate=False,pairs=pairs,download_only=True)
                if wizard.ShowModal()==wx.ID_OK:
                    family=wizard.downloaded_family
                    if family:self.source_page.gpu_edits[context]=dict(family=family,gpu=getattr(wizard,'downloaded_gpu',False),cuda_index=getattr(wizard,'downloaded_cuda_index',0))
            except Exception:
                self.hooks.get('report_error',lambda:None)()
                self.source_page.status.SetLabel(_('Impossible d’ouvrir le téléchargement des modèles.'))
            finally:
                if wizard is not None:
                    wizard.dead=True;wizard.cancel.set();wizard.Destroy()
                self.busy=False
            if not self.dead:
                self.tabs.SetSelection(0);self.refresh_installed()
                on_done(family)
        wx.CallAfter(run)
    def on_setup(self,event):
        if self.busy:return
        context=self.context()
        self.finish(wx.ID_CANCEL)
        wx.CallAfter(self.hooks['setup'],context)
    def context(self):return self.app if self.scope.GetSelection()==0 else 'global'
    def family_id(self):return FAMILIES[max(0,self.family.GetSelection())][0]
    def checked(self,control):return [self.codes[i] for i in control.GetCheckedItems()]
    def set_checked(self,control,values):
        control.SetCheckedItems([i for i,c in enumerate(self.codes) if c in values])
    def dispatch(self,fn,*args):
        def run():
            # `self` is false once the window is destroyed.
            if not self.dead and self:fn(*args)
        wx.CallAfter(run)
    def inventory(self):
        from . import gpu_support
        try:info=gpu_support.inspect()
        except Exception:info={'cpu':'Analyse indisponible','gpus':[]}
        self.dispatch(self.show_inventory,info)
    def show_inventory(self,info):
        self.info=info
        self.source_page.set_hardware(info)
        ram=info.get('ram_available')
        text=_('{cpu} ; {cores} processeurs logiques.').format(cpu=info['cpu'],cores=info.get('cores','?'))
        if ram is not None:text+=_(' RAM disponible : {v0:.1f} Go sur {v1:.1f} Go.').format(v0=ram / 1024 ** 3, v1=info['ram_total'] / 1024 ** 3)
        for gpu in info.get('gpus',[]):
            text+=_('\n{v0} : mémoire dédiée {v1:.1f} Go').format(v0=gpu['name'], v1=gpu['vram_total'] / 1024 ** 3)
            if gpu.get('vram_free') is not None:text+=_(', libre {free:.1f} Go').format(free=gpu['vram_free']/1024**3)
            text+=_(' ; mémoire partagée comptée séparément.')
        self.machine.SetValue(text)

    def load(self,event=None):
        config=self.store.config(self.context())
        self.family.SetSelection([id for id,name in FAMILIES].index(config.get('family','bergamot')))
        self.change_family(config=config)
        prefs=self.hooks['preferences'](self.context())
        self.inherit.SetValue(prefs['source']=='inherit');self.inherit.Enable(self.context()!='global')
        self.remember.SetValue(prefs['remember']);self.remember.Enable(self.context()!='global')
        self.forget.SetValue(False);self.forget.Enable(self.context()!='global')
        self.update_preview()
    def change_family(self,event=None,config=None):
        if event is not None:self.user_family_changed=True
        config=config or self.store.config(self.context())
        self.codes=self.store.languages(self.family_id())
        labels=[self.language_label(c) for c in self.codes]
        self.source.Set(['Auto']+labels);self.sources.Set(labels);self.targets.Set(labels)
        source=self.hooks['source'](self.context())
        self.source.SetSelection(self.codes.index(source)+1 if source in self.codes else 0)
        self.set_checked(self.sources,config.get('sources',['en']))
        targets=[c for c in config.get('targets',['fr']) if c in self.codes]
        if not targets:targets=['fr'] if 'fr' in self.codes else self.codes[:1]
        self.set_checked(self.targets,targets);self.change_targets(preferred=config.get('active','fr'))
    def change_targets(self,event=None,preferred=None):
        targets=self.checked(self.targets)
        previous=getattr(self,'active_codes',[])
        current=self.active.GetSelection()
        preferred=preferred or (previous[current] if 0<=current<len(previous) else 'fr')
        self.active_codes=targets;self.active.Set([self.language_label(c) for c in targets])
        if targets:self.active.SetSelection(targets.index(preferred) if preferred in targets else 0)
        self.update_preview()
    def selection(self):
        source_index=self.source.GetSelection()
        source='auto' if source_index<=0 else self.codes[source_index-1]
        if self.context()!='global' and self.inherit.GetValue():source=self.hooks['source']('global')
        sources=self.checked(self.sources) if source=='auto' else [source]
        targets=self.checked(self.targets)
        index=self.active.GetSelection()
        active=self.active_codes[index] if 0<=index<len(self.active_codes) else None
        if not sources or not targets or active not in targets:raise ValueError(_('Sélectionnez au moins une langue source et une langue cible.'))
        family=self.family_id()
        if family in ('argos','opus','nllb','small100','m2m100') and self.info and self.info.get('sse41') is False:
            raise ValueError(_('Ce processeur ne déclare pas SSE4.1. Choisissez Firefox/Bergamot.'))
        if family=='opus' and self.info and self.info.get('ram_available') is not None and self.info['ram_available']<3*1024**3:
            raise ValueError(_('La préparation initiale du modèle OPUS demande au moins 3 Go de RAM disponibles. Essayez Argos ou Firefox.'))
        if family=='nllb' and self.info and self.info.get('ram_available') is not None and self.info['ram_available']<3*1024**3:
            raise ValueError(_('NLLB demande au moins 3 Go de RAM disponibles. Essayez Argos ou Firefox.'))
        if family in ('small100','m2m100') and self.info and self.info.get('ram_available') is not None and self.info['ram_available']<2*1024**3:
            raise ValueError(_('Ce modèle multilingue demande au moins 2 Go de RAM disponibles. Essayez Argos ou Firefox.'))
        if family=='hy' and (self.info is None or not hardware.gpu_candidate(self.info)):
            raise ValueError(_('Ce modèle demande une carte NVIDIA dédiée compatible avec au moins 12 Go de VRAM ; la mémoire libre doit être suffisante.'))
        config=self.store.config(self.context())
        if config.get('pairs') and family==config['family'] and set(sources)==set(config['sources']) and set(targets)==set(config['targets']):
            entries,routes=self.store.plan_pairs(family,config['pairs'])
        else:entries,routes=self.store.plan(family,sources,targets)
        return family,source,sources,targets,active,entries,routes
    def update_preview(self,event=None):
        inherited=self.context()!='global' and self.inherit.GetValue()
        self.source.Enable(not self.busy and not inherited)
        self.sources.Enable(not self.busy and (self.source.GetSelection()==0 or inherited and self.hooks['source']('global')=='auto'))
        try:
            family,source,sources,targets,active,entries,routes=self.selection()
            lines=[' ; '.join(routes)]
            total=0;unknown=0
            for entry in entries:
                installed=self.store.installed(entry)
                size,missing=self.store.download_size([entry])
                lines.append(entry['id']+(_(' — installé') if installed else _(' — {size:.1f} Mo').format(size=size/1e6)+(_(' connus, taille à compléter') if missing else _(' à télécharger')) if size else _(' — taille du téléchargement non publiée')))
                if not installed:
                    total+=size;unknown+=missing
            if family=='bergamot' and not self.store.runtime_ready(family):
                size=self.store.catalog['runtimes']['node'].get('bytes',0);total+=size
                lines.append(_('Moteur Node.js : {v0:.0f} Mo supplémentaires ; Bergamot est inclus.').format(v0=size / 1000000.0))
            if family=='hy' and not self.store.runtime_ready(family):
                size=sum(x.get('bytes',0) for x in self.store.catalog['runtimes']['cuda']);total+=size
                lines.append(_('Moteur CUDA : {v0:.0f} Mo supplémentaires. Chargement GPU expérimental.').format(v0=size / 1000000.0))
            full,missing=self.store.download_size(entries)
            lines.append(_('Taille totale des modèles pour vos langues : {v0:.1f} Mo').format(v0=full / 1000000.0)+(_(' connus ; taille à compléter.') if missing else '.'))
            lines.append(_('Reste à télécharger, moteurs compris : {v0:.1f} Mo').format(v0=total / 1000000.0)+(_(' connus ; {v0} taille(s) à déterminer.').format(v0=unknown) if unknown else '.'))
            lines.append(_('Une seule langue cible sera annoncée : ')+self.language_label(active)+'.')
            self.preview.SetValue('\n'.join(lines))
        except ValueError as exc:self.preview.SetValue(str(exc))
    def set_busy(self,value):
        self.source_page.set_busy(value)
        self.busy=value
        for control in (self.scope,self.family,self.sources,self.targets,self.active,self.inherit,self.remember,self.forget,self.copy,self.download,self.save,self.remove,self.refresh):control.Enable(not value)
        self.cancel_download.Enable(value);self.update_preview()
        if not value:
            self.inherit.Enable(self.context()!='global')
            self.remember.Enable(self.context()!='global')
            self.forget.Enable(self.context()!='global')
    def progress(self,current,total):
        now=time.monotonic()
        if now-self.last_progress<.25:return
        self.last_progress=now
        self.dispatch(self.show_progress,current,total)
    def show_progress(self,current,total):
        percent=min(100,int(current*100/total)) if total else None
        if total:
            self.gauge.SetValue(percent);self.download.SetLabel(_('{v0}% du téléchargement').format(v0=percent))
        else:self.gauge.Pulse();self.download.SetLabel(_('Téléchargement en cours'))
        text=getattr(self,'operation_title',_('Modèle'))+' : '+(f'{percent}%, ' if total else '')+_('{v0:.1f} Mo téléchargés').format(v0=current / 1000000.0)+(_(' sur {v0:.1f} Mo.').format(v0=total / 1000000.0) if total else _(' ; taille totale inconnue.'))
        self.operation_status.SetValue(text);self.status.SetLabel(text)
        now=time.monotonic()
        if now-getattr(self,'last_announcement',0)>=5 and percent is not None and percent//10>getattr(self,'spoken_bucket',-1):
            self.spoken_bucket=percent//10;self.last_announcement=now
            self.hooks.get('announce',lambda text:None)(text)
    def show_operation_stage(self,stage,title):
        names={'download':_('Téléchargement'),'verify':_('Vérification du fichier'),'unpack':_('Décompression'),
               'convert':_('Conversion du modèle en INT8'),'install':'Installation','ready':_('Installation terminée')}
        self.operation_title=title;self.spoken_bucket=-1
        text=title+' : '+names.get(stage,stage)+'.'
        self.status.SetLabel(text);self.operation_status.SetValue(text)
        self.download.SetLabel(names.get(stage,stage));self.gauge.Pulse()
        self.last_announcement=time.monotonic()
        self.hooks.get('announce',lambda text:None)(text)
    def operation_heartbeat(self):
        if self.dead or not self.busy:return
        if time.monotonic()-getattr(self,'last_announcement',0)>=15:
            self.last_announcement=time.monotonic()
            self.hooks.get('announce',lambda text:None)(self.operation_status.GetValue())
        self.heartbeat_timer=wx.CallLater(15000,self.operation_heartbeat)
    def on_download(self,event):
        try:selection=self.selection()
        except ValueError as exc:self.status.SetLabel(str(exc));return
        self.cancel.clear();self.set_busy(True)
        context=self.context();remember=self.remember.GetValue();inherit=self.inherit.GetValue()
        if hasattr(wx,'CallLater'):self.heartbeat_timer=wx.CallLater(15000,self.operation_heartbeat)
        dialog=self
        class Progress:
            def __init__(self,title):self.title=title
            def __call__(self,current,total):dialog.progress(current,total)
            def phase(self,stage,detail=''):dialog.dispatch(dialog.show_operation_stage,stage,self.title)
        def work():
            try:
                self.hooks['stop']()
                family,source,sources,targets,active,entries,routes=selection
                self.store.ensure_runtime(family,self.cancel,Progress(_('Moteur de traduction')))
                missing=[entry for entry in entries if not self.store.installed(entry)]
                for index,entry in enumerate(missing,1):
                    title=_('Modèle {v0} sur {v1}, ').format(v0=index, v1=len(missing))+entry['id']
                    self.dispatch(self.show_operation_stage,'install',title)
                    self.store.install(entry,self.cancel,Progress(title))
                self.dispatch(self.download_done,selection,context,remember,inherit,None)
            except Exception as exc:
                self.hooks.get('report_error',lambda:None)()
                self.dispatch(self.download_done,selection,context,remember,inherit,str(exc))
        threading.Thread(target=work,daemon=True).start()
    def commit(self,selection,context,remember,inherit):
        family,source,sources,targets,active,entries,routes=selection
        gpu=hardware.gpu_candidate(self.info or {}) if family=='hy' else None
        previous=self.store.config(context)
        pairs=previous.get('pairs') if family==previous['family'] and set(sources)==set(previous['sources']) and set(targets)==set(previous['targets']) else None
        self.store.configure(context,family,sources,targets,active,cuda_index=gpu.get('cuda_index',0) if gpu else 0,pairs=pairs)
        self.hooks['configure'](context,source,remember,inherit,self.forget.GetValue())
        self.hooks['copy_set'](self.copy.GetValue())
        self.hooks['changed']()
    def download_done(self,selection,context,remember,inherit,error):
        self.set_busy(False);self.refresh_installed()
        self.download.SetLabel(_('Télécharger les modèles nécessaires'))
        text=error or _('Téléchargement et installation terminés. Traduction prête.')
        self.operation_status.SetValue(text)
        self.hooks.get('announce',lambda text:None)(text)
        if error:self.hooks['changed']();self.status.SetLabel(error);return
        self.commit(selection,context,remember,inherit)
        self.status.SetLabel(_('Modèles installés. Traduction prête.'));self.gauge.SetValue(100)
        if self.activate:self.finish(wx.ID_OK)
    def on_save(self,event):
        try:
            selection=self.selection()
            if not self.store.runtime_ready(selection[0]) or not all(self.store.installed(e) for e in selection[5]):raise ValueError(_('Téléchargez d’abord les modèles nécessaires.'))
            self.commit(selection,self.context(),self.remember.GetValue(),self.inherit.GetValue())
            self.finish(wx.ID_OK)
        except ValueError as exc:self.status.SetLabel(str(exc))
    def refresh_installed(self):
        self.installed_entries=self.store.installed_entries()
        labels=[]
        for entry in self.installed_entries:
            path=self.store.path(entry['id'])
            size=sum(p.stat().st_size for p in path.rglob('*') if p.is_file())
            name=MODEL_NAMES.get(entry['family'],_('Modèle de traduction'))
            if entry.get('source') and entry.get('target'):
                name=self.language_label(entry['source'])+_(' vers ')+self.language_label(entry['target'])+' — '+name
            elif entry.get('languages'):
                name+=_(' — multilingue, {v0} langues').format(v0=len(entry['languages']))
            labels.append(_('{name} — {size:.0f} Mo').format(name=name,size=size/1e6)+(_(' — ancienne version') if entry.get('outdated') else ''))
        self.installed_list.Set(labels)
    def on_remove(self,event):
        index=self.installed_list.GetSelection()
        if index<0:return
        entry=self.installed_entries[index];self.set_busy(True)
        def work():
            try:self.hooks['stop']();self.store.remove(entry);error=None
            except Exception as exc:error=str(exc)
            self.dispatch(self.removed,error)
        threading.Thread(target=work,daemon=True).start()
    def on_refresh(self,event):
        self.set_busy(True)
        def work():
            try:self.store.refresh_catalog();error=None
            except Exception as exc:error=str(exc)
            self.dispatch(self.refreshed,error)
        threading.Thread(target=work,daemon=True).start()
    def refreshed(self,error):
        self.set_busy(False);self.load();self.refresh_installed()
        self.status.SetLabel(error or _('Catalogue actualisé. Les modèles sont téléchargés uniquement à votre demande.'))
    def removed(self,error):
        self.set_busy(False);self.refresh_installed();self.update_preview()
        if error:self.hooks['changed']()
        else:self.hooks.get('removed',lambda:None)()
        self.status.SetLabel(error or _('Modèle supprimé ; traduction désactivée, cache et réglages conservés. Réactivez la traduction pour choisir les modèles.'))
    def on_reset(self,event):
        self.set_busy(True)
        def work():
            try:self.telemetry.clear();error=None
            except OSError as exc:error=str(exc)
            self.dispatch(self.reset_done,error)
        threading.Thread(target=work,daemon=True).start()
    def reset_done(self,error):
        self.set_busy(False);self.statistics.SetValue(error or _('Journal effacé.'))
    def finish(self,result):
        if self.finish_callback is not None:self.finish_callback(result)
    def shutdown(self):
        """Stop background work before the host window goes away. Safe to call twice."""
        self.cache_page.dead=True
        self.source_page.dead=True
        self.cancel.set();self.dead=True
    def save_pending(self):
        """Apply the language choices still waiting on the Languages page, unless they need a download."""
        page=self.source_page
        if self.dead or getattr(page,'downloading',False):return
        if not (page.edits or page.target_edits or page.model_edits or page.gpu_edits):return
        try:downloads,runtimes,entries=page.download_plan()
        except ValueError:return
        if not runtimes and not entries:page.save_choices()
