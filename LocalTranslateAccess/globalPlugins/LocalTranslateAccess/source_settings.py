"""Accessible global and per-application source-language settings."""
from .i18n import _
import wx
import threading
import time
from . import gpu_support
from .control_roles import register_button
from .model_store import Cancelled
MODEL_NAMES={'argos':'Argos Translate','bergamot':'Firefox / Bergamot','opus':'Marian / OPUS-MT',
    'hy':'Hy-MT2 7B Q8','nllb':'NLLB-200 600M INT8','small100':'SMaLL-100 INT8','m2m100':'M2M-100 INT8'}

class SourceSettings(wx.ScrolledWindow):
    def __init__(self,parent,store,hooks,on_saved):
        super().__init__(parent)
        self.store,self.hooks,self.on_saved=store,hooks,on_saved
        self.SetScrollRate(0,10)
        self.edits={};self.target_edits={};self.model_edits={};self.model_options={}
        self.gpu_edits={};self.info=None;self.dead=False;self.cancel=hooks.get('cancel',threading.Event())
        self.codes=['auto']+sorted(store.languages(),key=lambda c:hooks['label'](c).casefold())
        choices=[_('Détection automatique')]+[hooks['label'](c) for c in self.codes[1:]]
        self.target_codes=self.codes[1:]
        target_choices=[hooks['label'](c) for c in self.target_codes]
        box=wx.BoxSizer(wx.VERTICAL)
        box.Add(wx.StaticText(self,label=_('Langue source pour la traduction globale :')),0,wx.ALL,8)
        self.global_source=wx.Choice(self,choices=choices,name=_('Langue source pour la traduction globale'))
        box.Add(self.global_source,0,wx.ALL|wx.EXPAND,8)
        box.Add(wx.StaticText(self,label=_('Langue cible pour la traduction globale :')),0,wx.ALL,8)
        self.global_target=wx.Choice(self,choices=target_choices,name=_('Langue cible pour la traduction globale'))
        box.Add(self.global_target,0,wx.ALL|wx.EXPAND,8)
        self.global_model_label=wx.StaticText(self,label=_('Modèle pour la traduction globale :'))
        box.Add(self.global_model_label,0,wx.ALL,8)
        self.global_model=wx.Choice(self,name=_('Modèle pour la traduction globale'))
        box.Add(self.global_model,0,wx.ALL|wx.EXPAND,8)
        self.global_gpu=wx.CheckBox(self,label=_('Utiliser la carte graphique pour accélérer la traduction'))
        box.Add(self.global_gpu,0,wx.ALL|wx.EXPAND,8);self.global_gpu.Hide()
        self.global_download=wx.Button(self,label=_('Télécharger un modèle pour la traduction globale'))
        register_button(self.global_download);box.Add(self.global_download,0,wx.ALL|wx.EXPAND,8)
        box.Add(wx.StaticText(self,label=_('Applications avec une langue source configurée :')),0,wx.ALL,8)
        self.applications=wx.ListBox(self,size=(-1,170),name=_('Applications avec une langue source configurée'))
        box.Add(self.applications,1,wx.ALL|wx.EXPAND,8)
        box.Add(wx.StaticText(self,label=_('Langue source pour cette application :')),0,wx.ALL,8)
        self.application_source=wx.Choice(self,choices=choices,name=_('Langue source pour cette application'))
        box.Add(self.application_source,0,wx.ALL|wx.EXPAND,8)
        box.Add(wx.StaticText(self,label=_('Langue cible pour cette application :')),0,wx.ALL,8)
        self.application_target=wx.Choice(self,choices=target_choices,name=_('Langue cible pour cette application'))
        box.Add(self.application_target,0,wx.ALL|wx.EXPAND,8)
        self.application_model_label=wx.StaticText(self,label=_('Modèle pour cette application :'))
        box.Add(self.application_model_label,0,wx.ALL,8)
        self.application_model=wx.Choice(self,name=_('Modèle pour cette application'))
        box.Add(self.application_model,0,wx.ALL|wx.EXPAND,8)
        self.application_gpu=wx.CheckBox(self,label=_('Utiliser la carte graphique pour accélérer la traduction'))
        box.Add(self.application_gpu,0,wx.ALL|wx.EXPAND,8);self.application_gpu.Hide()
        self.application_download=wx.Button(self,label=_('Télécharger un modèle pour cette application'))
        register_button(self.application_download);box.Add(self.application_download,0,wx.ALL|wx.EXPAND,8)
        apply_parent=hooks.get('apply_parent',self)
        self.apply=wx.Button(apply_parent,label=_('Appliquer'))
        register_button(self.apply)
        if apply_parent is self:box.Add(self.apply,0,wx.ALL,8)
        self.status=wx.StaticText(self,label=_('Modifiez une langue, puis choisissez Appliquer.'))
        box.Add(self.status,0,wx.ALL|wx.EXPAND,8);self.SetSizer(box);self.FitInside()
        self.global_source.Bind(wx.EVT_CHOICE,self.change_global)
        self.global_target.Bind(wx.EVT_CHOICE,self.change_global_target)
        self.applications.Bind(wx.EVT_LISTBOX,self.select_application)
        self.application_source.Bind(wx.EVT_CHOICE,self.change_application)
        self.application_target.Bind(wx.EVT_CHOICE,self.change_application_target)
        self.global_model.Bind(wx.EVT_CHOICE,lambda event:self.change_model('global',self.global_model))
        self.application_model.Bind(wx.EVT_CHOICE,self.change_application_model)
        self.global_gpu.Bind(wx.EVT_CHECKBOX,lambda event:self.change_gpu('global',self.global_gpu))
        self.application_gpu.Bind(wx.EVT_CHECKBOX,self.change_application_gpu)
        self.global_download.Bind(wx.EVT_BUTTON,lambda event:self.download_model('global'))
        self.application_download.Bind(wx.EVT_BUTTON,self.download_application_model)
        self.apply.Bind(wx.EVT_BUTTON,self.save)
        self.reload()
    def reload(self):
        index=self.applications.GetSelection()
        previous=self.keys[index] if hasattr(self,'keys') and 0<=index<len(self.keys) else None
        source=self.hooks['preferences']('global').get('source','auto')
        self.global_source.SetSelection(self.codes.index(source) if source in self.codes else 0)
        target=self.hooks.get('target',lambda context:self.store.config(context)['active'])('global')
        self.global_target.SetSelection(self.target_codes.index(target))
        self.update_models('global',self.global_model,source,target)
        self.app_preferences=self.hooks.get('application_sources',lambda:{})()
        self.keys=sorted(self.app_preferences,key=lambda key:self.hooks.get('application_label',lambda k:k)(key).casefold())
        self.applications.Set([self.hooks.get('application_label',lambda k:k)(key) for key in self.keys])
        if self.keys:self.applications.SetSelection(self.keys.index(previous) if previous in self.keys else 0)
        self.applications.Enable(bool(self.keys));self.select_application()
        if not self.keys:self.status.SetLabel(_('Aucune application n’a encore de langue source configurée. La langue globale reste modifiable.'))
    def select_application(self,event=None):
        index=self.applications.GetSelection()
        self.application_source.Enable(index>=0)
        self.application_target.Enable(index>=0)
        if index<0:
            self.application_source.SetSelection(wx.NOT_FOUND);self.application_target.SetSelection(wx.NOT_FOUND)
            self.application_model.Set([]);self.application_model.Enable(False)
            self.application_model.Show();self.application_model_label.Show();self.application_download.Hide()
            self.application_gpu.Hide()
            self.Layout();self.FitInside();return
        key=self.keys[index]
        source=self.edits.get(key,self.app_preferences[key].get('source','auto'))
        self.application_source.SetSelection(self.codes.index(source) if source in self.codes else 0)
        target=self.target_edits.get(key,self.hooks.get('target',lambda context:self.store.config(context)['active'])(key))
        self.application_target.SetSelection(self.target_codes.index(target))
        self.update_models(key,self.application_model,source,target)
    def pairs_for(self,context,source,target):
        if source not in self.codes:source='auto'
        sources=self.store.config(context)['sources'] if source=='auto' else [source]
        return [(code,target) for code in sources if code!=target]
    def compatible_models(self,context,source,target):
        """Installed models first, then models compatible with the machine that can be downloaded."""
        installed=self.store.available_models(context,source,target)
        pairs=self.pairs_for(context,source,target)
        others=[]
        if pairs and self.info is not None:
            from .setup_wizard import compatible
            try:others=[family for family in compatible(self.store,pairs,self.info) if family not in installed]
            except ValueError:others=[]
        return installed,others
    def update_models(self,context,control,source,target):
        installed,others=self.compatible_models(context,source,target)
        families=installed+others
        self.model_options[context]=families
        preferred=self.model_edits.get(context,self.store.config(context)['family'])
        if context in self.model_edits and preferred not in families:self.model_edits.pop(context)
        labels=[MODEL_NAMES.get(family,family)+('' if family in installed else _(' — à télécharger')) for family in families]
        if source=='auto':
            sources=[self.hooks['label'](code) for code in self.store.config(context)['sources'] if code!=target]
            labels=[label+' — Auto : '+', '.join(sources) for label in labels]
        empty=_('Aucun modèle installé compatible avec ces langues') if self.info is None else _('Aucun modèle compatible avec ces langues')
        control.Set(labels or [empty])
        control.SetSelection(families.index(preferred) if preferred in families else (0 if not families else wx.NOT_FOUND))
        control.Enable(bool(families))
        label=self.global_model_label if context=='global' else self.application_model_label
        button=self.global_download if context=='global' else self.application_download
        control.Show(bool(families));label.Show(bool(families));button.Show(not families)
        button.Enable('download_model' in self.hooks)
        self.update_gpu(context,control,source,target)
        self.update_apply()
        self.Layout();self.FitInside()
    def set_hardware(self,info):
        self.info=info;self.refresh_models()
    def update_gpu(self,context,control,source,target):
        checkbox=self.global_gpu if context=='global' else self.application_gpu
        index=control.GetSelection();families=self.model_options.get(context,[])
        if not families or index<0:checkbox.Hide();return
        family=families[index]
        try:
            sources=self.store.config(context)['sources'] if source=='auto' else [source]
            entries,unused=self.store.plan_pairs(family,[(code,target) for code in sources if code!=target])
            gpu=gpu_support.candidate(self.info,self.store,family,entries)
            resident=self.hooks.get('gpu_resident',lambda entries:None)(entries)
            if gpu is None and resident is not None:gpu=gpu_support.candidate(self.info,self.store,family,entries,resident,resident=True)
        except ValueError:gpu=None
        cfg=self.gpu_edits.get(context,self.store.config(context))
        checkbox.Show(bool(gpu));checkbox.SetValue(bool(gpu and cfg.get('family')==family and cfg.get('gpu',False)))
        checkbox.gpu_candidate=gpu;checkbox.family=family
    def change_gpu(self,context,checkbox):
        gpu=getattr(checkbox,'gpu_candidate',None)
        if gpu:self.gpu_edits[context]=dict(family=checkbox.family,gpu=checkbox.GetValue(),cuda_index=gpu['cuda_index'])
        self.update_apply()
    def change_application_gpu(self,event):
        index=self.applications.GetSelection()
        if index>=0:self.change_gpu(self.keys[index],self.application_gpu)
    def download_application_model(self,event=None):
        index=self.applications.GetSelection()
        if index>=0:self.download_model(self.keys[index])
    def download_model(self,context):
        global_scope=context=='global'
        source_control=self.global_source if global_scope else self.application_source
        target_control=self.global_target if global_scope else self.application_target
        source=self.codes[source_control.GetSelection()]
        target=self.target_codes[target_control.GetSelection()]
        button=self.global_download if global_scope else self.application_download
        def done(family):
            if family:self.model_edits[context]=family
            self.refresh_models()
            control=self.global_model if global_scope else self.application_model
            if family and family in self.model_options.get(context,[]):
                control.SetSelection(self.model_options[context].index(family))
                self.status.SetLabel(_('Modèle installé. Choisissez Appliquer pour enregistrer vos langues et votre modèle.'))
                control.SetFocus()
            else:button.SetFocus()
        self.hooks['download_model'](context,source,target,done)
    def refresh_models(self):
        self.update_models('global',self.global_model,self.codes[self.global_source.GetSelection()],
            self.target_codes[self.global_target.GetSelection()])
        index=self.applications.GetSelection()
        if index>=0:
            self.update_models(self.keys[index],self.application_model,self.codes[self.application_source.GetSelection()],
                self.target_codes[self.application_target.GetSelection()])
    def change_model(self,context,control):
        index=control.GetSelection();families=self.model_options.get(context,[])
        if 0<=index<len(families):
            self.model_edits[context]=families[index]
            self.gpu_edits.pop(context,None)
            global_scope=context=='global'
            source_control=self.global_source if global_scope else self.application_source
            target_control=self.global_target if global_scope else self.application_target
            source=self.codes[source_control.GetSelection()];target=self.target_codes[target_control.GetSelection()]
            self.update_gpu(context,control,source,target)
            if families[index] not in self.store.available_models(context,source,target):
                self.status.SetLabel(_('Choisissez le modèle puis Télécharger.'))
            self.update_apply()
            self.Layout();self.FitInside()
    def change_application_model(self,event=None):
        index=self.applications.GetSelection()
        if index>=0:self.change_model(self.keys[index],self.application_model)
    def change_global(self,event=None):
        self.edits['global']=self.codes[self.global_source.GetSelection()]
        self.refresh_models()
    def change_application(self,event=None):
        index=self.applications.GetSelection();source=self.application_source.GetSelection()
        if index>=0 and source>=0:self.edits[self.keys[index]]=self.codes[source]
        self.refresh_models()
    def change_global_target(self,event=None):
        self.target_edits['global']=self.target_codes[self.global_target.GetSelection()]
        self.refresh_models()
    def change_application_target(self,event=None):
        index=self.applications.GetSelection();target=self.application_target.GetSelection()
        if index>=0 and target>=0:self.target_edits[self.keys[index]]=self.target_codes[target]
        self.refresh_models()
    def save(self,event=None):
        if getattr(self,'downloading',False):return
        if not self.edits and not self.target_edits and not self.model_edits and not self.gpu_edits:
            self.status.SetLabel(_('Aucune modification à enregistrer.'));return
        try:downloads,runtimes,entries=self.download_plan()
        except ValueError:
            self.status.SetLabel(_('Impossible d’enregistrer les langues et les modèles. Vérifiez les modèles installés.'));return
        ram=(self.info or {}).get('ram_available') or 0
        if any(family=='hy' and not gpu and ram<12*1024**3 for family,items,gpu in downloads):
            # Refuse before a large download that could not run on this machine's CPU.
            self.status.SetLabel(_('RAM disponible insuffisante pour ce modèle sur CPU. Activez la carte graphique ou choisissez un autre modèle.'));return
        if runtimes or entries:
            # Like the setup wizard: the button shows the progress, then becomes Appliquer.
            self.downloading=True
            self.set_busy(True);self.apply.Enable(True);self.apply.SetLabel(_('0% du téléchargement'));self.apply.SetFocus()
            self.download_percent=0;self.spoken_bucket=-1;self.status.SetLabel('0 %')
            def work():
                error=None;installed=False
                try:
                    from .setup_wizard import DownloadProgress,OperationProgress
                    if entries:self.hooks.get('stop',lambda:None)()
                    specs=[]
                    for family,gpu in runtimes:specs.extend(self.store.runtime_specs(family,gpu))
                    size_entries=entries+[dict(spec,id='runtime-'+spec['url']) for spec in specs]
                    self.store.resolve_download_sizes(size_entries,self.cancel)
                    if self.cancel.is_set():raise Cancelled(_('Téléchargement annulé.'))
                    tracker=DownloadProgress(self,self.store.download_specs(size_entries))
                    for family,gpu in runtimes:
                        current=self.store.runtime_specs(family,gpu)
                        if current:self.store.ensure_runtime(family,self.cancel,OperationProgress(self,_('Moteur de traduction'),len(current),tracker,current),gpu=gpu)
                    for step,entry in enumerate(entries,1):
                        files=list(entry['files'].values()) if entry.get('files') else [entry]
                        progress=OperationProgress(self,_('Modèle {v0} sur {v1}').format(v0=step,v1=len(entries)),len(files),tracker,files)
                        progress.phase('prepare')
                        self.store.install(entry,self.cancel,progress);installed=True
                    if self.cancel.is_set():raise Cancelled(_('Téléchargement annulé.'))
                except Exception as exc:
                    error=str(exc);self.hooks.get('report_error',lambda:None)()
                if installed:self.dispatch(self.models_installed)
                self.dispatch(self.runtime_completed,error)
            threading.Thread(target=work,daemon=True).start();return
        self.save_choices()
    def download_plan(self):
        """Pending models, runtimes to install as (family, gpu), and catalog entries to install."""
        downloads=self.pending_downloads()
        runtimes=[(cfg['family'],True) for context,cfg in self.gpu_edits.items() if cfg['gpu'] and not self.store.runtime_ready(cfg['family'],True)]
        runtimes+=[(family,gpu) for family,entries,gpu in downloads if not self.store.runtime_ready(family,gpu)]
        runtimes=list(dict.fromkeys(runtimes))
        entries=list({entry['id']:entry for family,items,gpu in downloads for entry in items}.values())
        return downloads,runtimes,entries
    def update_apply(self):
        """The action button reads Télécharger while a chosen model or engine is missing."""
        if getattr(self,'downloading',False):return
        try:downloads,runtimes,entries=self.download_plan()
        except ValueError:runtimes=entries=[]
        self.apply.SetLabel(_('Télécharger') if runtimes or entries else _('Appliquer'))
    def pending_downloads(self):
        """Chosen models that are compatible but not yet installed for the edited languages."""
        result=[]
        for context,family in self.model_edits.items():
            # Same source resolution as the save_languages hook, which validates the model afterwards.
            fallback=self.hooks.get('source',lambda key:self.hooks['preferences'](key).get('source','auto'))
            source=self.edits[context] if context in self.edits else fallback(context)
            if source not in self.codes:source='auto'
            target=self.target_edits.get(context) or self.hooks.get('target',lambda key:self.store.config(key)['active'])(context)
            if family in self.store.available_models(context,source,target):continue
            entries,unused=self.store.plan_pairs(family,self.pairs_for(context,source,target))
            cfg=self.gpu_edits.get(context,{})
            gpu=bool(cfg.get('family')==family and cfg.get('gpu'))
            result.append((family,[entry for entry in entries if not self.store.installed(entry)],gpu))
        return result
    def models_installed(self):
        self.hooks.get('changed',lambda:None)()
        self.hooks.get('installed_changed',lambda:None)()
    def dispatch(self,fn,*args):
        def run():
            if not self.dead and not self.cancel.is_set() and not self.IsBeingDeleted():fn(*args)
        wx.CallAfter(run)
    def announce(self,text):
        self.last_announcement=time.monotonic()
        self.hooks.get('announce',lambda text:None)(text)
    def show_transfer(self,title,file,files,current,total):
        # Same feedback as the setup wizard: label, status line and a spoken step every 10 %.
        if total:
            percent=max(getattr(self,'download_percent',0),min(100,int(100*current/total)))
            self.download_percent=percent
            self.apply.SetLabel(_('{v0}% du téléchargement').format(v0=percent))
            self.status.SetLabel(f'{percent} %')
            bucket=percent//10
            if bucket>getattr(self,'spoken_bucket',-1) and time.monotonic()-getattr(self,'last_announcement',0)>=5:
                self.spoken_bucket=bucket;self.announce(f'{percent} %')
        else:
            self.apply.SetLabel(_('Téléchargement en cours'))
            self.status.SetLabel(_('Téléchargement en cours'))
    def show_phase(self,*args):pass
    def runtime_completed(self,error):
        self.downloading=False;self.set_busy(False)
        if not error:
            try:
                downloads,runtimes,entries=self.download_plan()
                if runtimes or entries:error=_('L’installation du modèle est incomplète. Réessayez le téléchargement.')
            except ValueError:error=_('L’installation du modèle est incomplète. Réessayez le téléchargement.')
        if error:
            self.apply.SetLabel(_('Télécharger'))
            message=_('Échec : ')+error
            self.status.SetLabel(message);self.announce(message);return
        # Same ending as the setup wizard: the button becomes Appliquer and keeps the focus.
        self.refresh_models();self.apply.SetLabel(_('Appliquer'))
        self.status.SetLabel(_('Modèle installé. Choisissez Appliquer.'))
        self.announce(_('Appliquer'));self.apply.SetFocus()
    def save_choices(self):
        try:
            changes=dict(self.edits)
            targets=dict(self.target_edits)
            models=dict(self.model_edits)
            for context in set(changes)|set(targets)|set(self.gpu_edits):
                source=changes.get(context,self.hooks['preferences'](context).get('source','auto'))
                target=targets.get(context,self.store.config(context)['active'])
                current=self.gpu_edits.get(context,{}).get('family',self.store.config(context)['family'])
                if current in self.store.available_models(context,source,target):models.setdefault(context,current)
            for context,family in models.items():
                source=changes.get(context,self.hooks['preferences'](context).get('source','auto'))
                target=targets.get(context,self.store.config(context)['active'])
                if family not in self.store.available_models(context,source,target):raise ValueError(_('Modèle indisponible'))
                cfg=self.gpu_edits.get(context,self.store.config(context))
                if family=='hy' and not (cfg.get('family')==family and cfg.get('gpu',False)) and self.info is not None and (self.info.get('ram_available') or 0)<12*1024**3:
                    self.status.SetLabel(_('RAM disponible insuffisante pour ce modèle sur CPU. Activez la carte graphique ou choisissez un autre modèle.'));return
            if 'save_languages' in self.hooks:
                if self.hooks.get('gpu_settings'):self.hooks['save_languages'](changes,targets,models,self.gpu_edits)
                else:
                    self.hooks['save_languages'](changes,targets,models)
                    for context,cfg in self.gpu_edits.items():self.store.set_gpu(context,cfg['gpu'],cfg['cuda_index'])
            else:
                for context,code in targets.items():self.store.set_target(context,code)
                for context,family in models.items():
                    self.store.set_model(context,family,changes.get(context,self.hooks['preferences'](context).get('source','auto')),
                        targets.get(context,self.store.config(context)['active']))
                self.hooks['save_sources'](changes)
                for context,cfg in self.gpu_edits.items():self.store.set_gpu(context,cfg['gpu'],cfg['cuda_index'])
            self.edits.clear();self.target_edits.clear();self.model_edits.clear();self.gpu_edits.clear();self.reload();self.on_saved()
            message=_('Langues et modèles enregistrés.')
            missing=False
            for context in set(changes)|set(targets):
                source=changes.get(context,self.hooks['preferences'](context).get('source','auto'))
                cfg=self.store.config(context)
                try:
                    entries=[e for src in (cfg['sources'] if source=='auto' else [source])
                             for e in self.store.route(cfg['family'],src,cfg['active'])]
                    if not all(self.store.installed(e) for e in entries):missing=True
                except ValueError:missing=True
            if missing:message+=_(' Pour certaines langues, ouvrez l’assistant afin de télécharger les modèles nécessaires.')
            self.status.SetLabel(message);self.hooks.get('announce',lambda text:None)(message)
        except (OSError,ValueError):
            self.status.SetLabel(_('Impossible d’enregistrer les langues et les modèles. Vérifiez les modèles installés.'))
    def set_busy(self,busy):
        self.global_source.Enable(not busy);self.apply.Enable(not busy)
        self.global_target.Enable(not busy)
        self.applications.Enable(not busy and bool(self.keys))
        self.application_source.Enable(not busy and self.applications.GetSelection()>=0)
        self.application_target.Enable(not busy and self.applications.GetSelection()>=0)
        self.global_model.Enable(not busy and bool(self.model_options.get('global')))
        index=self.applications.GetSelection()
        self.application_model.Enable(not busy and index>=0 and bool(self.model_options.get(self.keys[index])))
        self.global_download.Enable(not busy and 'download_model' in self.hooks)
        self.global_gpu.Enable(not busy);self.application_gpu.Enable(not busy)
        self.application_download.Enable(not busy and index>=0 and 'download_model' in self.hooks)
