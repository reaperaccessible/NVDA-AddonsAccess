"""Single-window setup: language pairs, compatible engines and installation."""
from .i18n import _, set_window_direction
import threading
import time
import wx
from . import hardware
from .model_store import Cancelled
from . import gpu_support

NAMES={'bergamot':'Firefox / Bergamot',
       'nllb':_('NLLB-200 600M INT8 — multilingue'),
       'small100':_('SMaLL-100 330M INT8 — multilingue, priorité à la rapidité'),
       'm2m100':_('M2M-100 418M INT8 — multilingue, priorité à la rapidité')}

class DownloadProgress:
    """Byte-weighted progress for every runtime and model file in one operation."""
    def __init__(self,dialog,specs):
        self.dialog=dialog
        self.sizes={s['url']:s.get('bytes') or dialog.store.sizes.get(s['url']) for s in specs}
        self.received={url:0 for url in self.sizes}
    def update(self,url,current,total,title):
        if total:self.sizes[url]=total
        self.received[url]=max(self.received.get(url,0),current)
        known=all(self.sizes.values())
        self.dialog.dispatch(self.dialog.show_transfer,_('Téléchargement total'),1,1,
            sum(self.received.values()),sum(self.sizes.values()) if known else None)
    def complete(self,url,title):
        size=self.sizes.get(url) or self.received.get(url,0)
        if size:self.update(url,size,size,title)

class OperationProgress:
    def __init__(self,dialog,title,files=1,tracker=None,specs=None):
        self.dialog,self.title,self.files=dialog,title,files
        self.file=0;self.last=0
        self.tracker=tracker;self.specs=specs or []
    def phase(self,stage,detail=''):
        if stage=='download':
            if self.tracker and self.file:self.tracker.complete(self.specs[self.file-1]['url'],self.title)
            self.file+=1
        elif self.tracker and self.file and stage in ('verify','unpack','install','ready'):
            self.tracker.complete(self.specs[self.file-1]['url'],self.title)
        title=self.title
        if self.files>1 and self.file>0:title+=_(', fichier {v0} sur {v1}').format(v0=self.file, v1=self.files)
        self.dialog.dispatch(self.dialog.show_phase,stage,title)
    def __call__(self,current,total):
        now=time.monotonic()
        if now-self.last<.25 and (not total or current<total):return
        self.last=now
        if self.tracker:self.tracker.update(self.specs[self.file-1]['url'],current,total,self.title)
        else:self.dialog.dispatch(self.dialog.show_transfer,self.title,self.file,self.files,current,total)

def compatible(store,pairs,info):
    result=[]
    for family in NAMES:
        if family in ('argos','opus','nllb','small100','m2m100') and info.get('sse41') is not True:continue
        if family=='opus' and (info.get('ram_available') or 0)<3*1024**3:continue
        if family=='nllb' and (info.get('ram_available') or 0)<3*1024**3:continue
        if family in ('small100','m2m100') and (info.get('ram_available') or 0)<2*1024**3:continue
        if family=='hy' and (info.get('ram_available') or 0)<12*1024**3:
            entries,unused=store.plan_pairs(family,pairs)
            if not gpu_support.candidate(info,store,family,entries):continue
        try:store.plan_pairs(family,pairs)
        except ValueError:continue
        result.append(family)
    return result

class LayoutPanel(wx.Panel):
    def AcceptsFocus(self):return False
    def AcceptsFocusFromKeyboard(self):return False

class LayoutScroll(wx.ScrolledWindow):
    def AcceptsFocus(self):return False
    def AcceptsFocusFromKeyboard(self):return False

class SetupWizard(wx.Dialog):
    def __init__(self,parent,store,telemetry,app,hooks,activate=True,pairs=None,download_only=False):
        title=_('LocalTranslate — première utilisation') if app=='global' else _('LocalTranslate — première utilisation pour cette application')
        super().__init__(parent,title=title,size=(740,880))
        set_window_direction(self)
        self.store,self.app,self.hooks=store,app,hooks
        self.download_only=download_only;self.downloaded_family=None
        if download_only:self.SetTitle(_('LocalTranslate — télécharger un modèle'))
        self.dead=False;self.busy=False;self.finished=False;self.cancel=threading.Event()
        self.info=None;self.pairs=[];self.families=[];self.last_progress=0
        self.model_choice_changed=False
        self.gpu_choices={};self.downloaded_gpu=False;self.downloaded_cuda_index=0
        self.source_changed=False
        self.codes=sorted(store.languages(),key=lambda code:hooks['label'](code).casefold())
        outer=wx.BoxSizer(wx.VERTICAL)
        self.body=LayoutScroll(self);self.body.SetScrollRate(0,10)
        body=wx.BoxSizer(wx.VERTICAL)
        self.languages=LayoutPanel(self.body);self.models=LayoutPanel(self.body)
        first=wx.BoxSizer(wx.VERTICAL)
        # Windows derives accessible names from the preceding native label.
        # Create each label immediately before its control, in HWND order.
        first.Add(wx.StaticText(self.languages,label=_('Langue source :')),0,wx.ALL,8)
        self.source=wx.ListBox(self.languages,choices=[hooks['label'](c) for c in self.codes],style=wx.LB_MULTIPLE,size=(-1,100),name=_('Langue source'))
        first.Add(self.source,0,wx.ALL|wx.EXPAND,8)
        first.Add(wx.StaticText(self.languages,label=_('Langue cible :')),0,wx.ALL,8)
        self.target=wx.Choice(self.languages,choices=[hooks['label'](c) for c in self.codes],name=_('Langue cible'))
        first.Add(self.target,0,wx.ALL|wx.EXPAND,8)
        requested=hooks.get('source',lambda context:None)(app)
        sample=hooks.get('source_sample')
        from .app_languages import normalize
        document=normalize(sample[1]) if sample else None
        initial=requested if requested in self.codes else document or hooks.get('source_hint')
        target=hooks.get('target',lambda context:'fr')(app)
        self.target.SetSelection(self.codes.index(target if target in self.codes else 'fr'))
        target=self.codes[self.target.GetSelection()]
        source=next((code for code in (initial,'en','fr',*self.codes) if code in self.codes and code!=target),None)
        if source is not None:self.source.SetSelection(self.codes.index(source))
        self.add=wx.Button(self.languages,label=_('Ajouter'))
        self.add.Enable(source is not None)
        first.Add(self.add,0,wx.ALL,8)
        self.pair_label=wx.StaticText(self.languages,label=_('Paires ajoutées :'))
        first.Add(self.pair_label,0,wx.ALL,8)
        self.pair_list=wx.ListBox(self.languages,size=(-1,65));first.Add(self.pair_list,0,wx.ALL|wx.EXPAND,8)
        self.remove=wx.Button(self.languages,label=_('Retirer la paire sélectionnée'))
        first.Add(self.remove,0,wx.ALL,8);self.languages.SetSizer(first)
        second=wx.BoxSizer(wx.VERTICAL)
        second.Add(wx.StaticText(self.models,label=_('Choisissez un modèle compatible avec votre ordinateur et vos langues :')),0,wx.ALL|wx.EXPAND,8)
        self.model_list=wx.ListBox(self.models,style=wx.LB_SINGLE,size=(-1,140),name=_('Modèles compatibles'))
        second.Add(self.model_list,0,wx.ALL|wx.EXPAND,8)
        self.gpu=wx.CheckBox(self.models,label=_('Utiliser la carte graphique pour accélérer la traduction'))
        second.Add(self.gpu,0,wx.ALL|wx.EXPAND,8);self.gpu.Hide()
        self.download=wx.Button(self.models,label=_('Télécharger'))
        second.Add(self.download,0,wx.ALL,8);self.models.SetSizer(second)
        body.Add(self.languages,0,wx.EXPAND);body.Add(self.models,0,wx.EXPAND)
        self.body.SetSizer(body);outer.Add(self.body,1,wx.EXPAND)
        self.status=wx.StaticText(self,label=_('Ajoutez les paires de langues dont vous avez besoin.'))
        outer.Add(self.status,0,wx.ALL|wx.EXPAND,8)
        self.close=wx.Button(self,wx.ID_CANCEL,label=_('Annuler'));outer.Add(self.close,0,wx.ALL|wx.ALIGN_RIGHT,8)
        self.SetSizer(outer);self.models.Hide();self.update_pairs();self.Layout()
        self.add.Bind(wx.EVT_BUTTON,self.on_add);self.remove.Bind(wx.EVT_BUTTON,self.on_remove)
        self.source.Bind(wx.EVT_LISTBOX,self.on_source_choice)
        self.target.Bind(wx.EVT_CHOICE,self.on_target_choice)
        self.model_list.Bind(wx.EVT_LISTBOX,self.on_model_choice);self.download.Bind(wx.EVT_BUTTON,self.on_download)
        self.gpu.Bind(wx.EVT_CHECKBOX,self.on_gpu_choice)
        self.close.Bind(wx.EVT_BUTTON,self.on_close);self.Bind(wx.EVT_CLOSE,self.on_close)
        # Keep traversal across layout-only panels symmetric in both directions.
        for panel in (self.languages,self.models,self.body,self):
            panel.Bind(wx.EVT_NAVIGATION_KEY,self.on_navigation)
        self.source.SetFocus()
        if pairs is not None:
            self.pairs=list(dict.fromkeys(tuple(pair) for pair in pairs))
            self.update_pairs();self.languages.Hide();self.models.Show()
            self.model_list.Enable(False);self.download.Enable(False)
            self.waiting=True;self.waiting_focus=True;self.status.SetLabel(_('Recherche des modèles compatibles…'));self.fit_contents()
        threading.Thread(target=self.inventory,daemon=True).start()
        if pairs is None and sample and initial not in self.codes:
            preferences=hooks.get('preferences',lambda context:{})(app)
            def propose():
                from .source_confirmation import suggestion
                from .detection import Detector
                detector=Detector()
                try:
                    code,origin,confidence=suggestion(sample,preferences,detector.detect)
                    self.dispatch(self.proposed_source,code)
                except Exception:pass
                finally:detector.close()
            threading.Thread(target=propose,daemon=True).start()

    def on_navigation(self,event):
        if event.IsWindowChange():event.Skip();return
        controls=[control for control in (self.source,self.target,self.add,self.pair_list,
            self.remove,self.model_list,self.gpu,self.download,self.close)
            if control.IsShownOnScreen() and control.IsEnabled()]
        focus=wx.Window.FindFocus()
        if not controls or focus not in controls:event.Skip();return
        step=1 if event.GetDirection() else -1
        controls[(controls.index(focus)+step)%len(controls)].SetFocus()

    def dispatch(self,fn,*args):
        def run():
            if not self.dead:fn(*args)
        wx.CallAfter(run)
    def inventory(self):
        try:info=gpu_support.inspect()
        except Exception:info={}
        self.dispatch(self.inventory_done,info)
    def inventory_done(self,info):
        self.info=info
        if self.pairs:self.refresh_models(focus=getattr(self,'waiting_focus',False))
    def fit_contents(self):
        self.languages.Layout();self.models.Layout();self.body.Layout();self.body.FitInside();self.Layout()
    def restore_download_choices(self):
        sources,target,model=self.download_choices
        for index in self.source.GetSelections():self.source.Deselect(index)
        for index in sources:self.source.SetSelection(index)
        self.target.SetSelection(target);self.model_list.SetSelection(model)
    def on_target_choice(self,event):
        if self.busy:self.restore_download_choices();return
        self.exclude_target_source()
        event.Skip()
    def on_source_choice(self,event):
        if self.busy:self.restore_download_choices();return
        self.source_changed=True
        self.exclude_target_source()
        event.Skip()
    def exclude_target_source(self):
        target=self.target.GetSelection()
        if target>=0 and self.source.IsSelected(target):self.source.Deselect(target)
        self.add.Enable(bool(self.source.GetSelections()) and target>=0)
    def proposed_source(self,code):
        if self.source_changed or self.pairs or not self.languages.IsShown() or code not in self.codes:return
        if self.codes.index(code)==self.target.GetSelection():return
        for index in self.source.GetSelections():self.source.Deselect(index)
        self.source.SetSelection(self.codes.index(code))
        self.exclude_target_source()
    def on_add(self,event):
        if self.busy:return
        sources=self.source.GetSelections();target=self.target.GetSelection()
        if not sources:self.status.SetLabel(_('Sélectionnez au moins une langue source avec Espace.'));return
        if target<0:self.status.SetLabel(_('Choisissez une langue cible.'));return
        self.source_changed=True
        added=0
        for source in sources:
            pair=(self.codes[source],self.codes[target])
            if pair[0]!=pair[1] and pair not in self.pairs:
                self.pairs.append(pair);added+=1
        self.update_pairs()
        if added:self.refresh_models(focus=True)
        else:self.status.SetLabel(_('Aucune nouvelle paire : les paires existent déjà ou la source est identique à la cible.'))
    def update_pairs(self):
        self.pair_list.Set([self.hooks['label'](a)+' → '+self.hooks['label'](b) for a,b in self.pairs])
        for control in (self.pair_label,self.pair_list,self.remove):
            control.Show(bool(self.pairs))
        self.fit_contents()
    def on_remove(self,event):
        if self.busy:return
        index=self.pair_list.GetSelection()
        if index>=0:
            self.pairs.pop(index);self.update_pairs()
            self.refresh_models()
            if self.pairs:self.pair_list.SetSelection(min(index,len(self.pairs)-1))
            else:
                self.status.SetLabel(_('Ajoutez une paire de langues pour continuer.'))
                self.add.SetFocus()
    def refresh_models(self,focus=False):
        if self.busy:return
        previous=self.families[self.model_list.GetSelection()] if self.families and self.model_list.GetSelection()>=0 else None
        self.finished=False
        if not self.pairs:
            self.waiting=False;self.waiting_focus=False;self.families=[]
            self.model_choice_changed=False
            self.models.Hide();self.fit_contents();return
        self.models.Show();self.fit_contents()
        if self.info is None:
            self.waiting=True;self.waiting_focus=focus
            self.model_list.Enable(False);self.download.Enable(False)
            self.status.SetLabel(_('Recherche des modèles compatibles…'));return
        self.waiting=False;self.families=compatible(self.store,self.pairs,self.info)
        if not self.families:
            self.model_list.Set([]);self.model_list.Enable(False);self.download.Enable(False)
            self.status.SetLabel(_('Aucun modèle compatible ne couvre toutes ces paires. Modifiez les paires ajoutées.'));return
        self.model_list.Enable(True);self.download.Enable(True)
        self.size_pairs=tuple(self.pairs)
        self.refresh_model_list(previous if self.model_choice_changed else None)
        self.preview()
        if focus:self.model_list.SetFocus()
        pairs=self.size_pairs;families=list(self.families)
        def sizes():
            for family in families:
                if self.cancel.is_set() or pairs!=tuple(self.pairs):return
                entries,_unused=self.store.plan_pairs(family,pairs)
                try:self.store.resolve_download_sizes(entries,self.cancel)
                except OSError:pass
                self.dispatch(self.sizes_ready,pairs,family)
        threading.Thread(target=sizes,daemon=True).start()
    def model_label(self,family):
        entries,_unused=self.store.plan_pairs(family,self.pairs)
        known,unknown=self.store.download_size(entries)
        size=_('{v0:.1f} Mo de modèles').format(v0=known / 1000000.0)
        if unknown:size=(_('au moins {v0:.1f} Mo — taille à compléter').format(v0=known / 1000000.0) if known else _('taille à déterminer'))
        if not entries[0].get('languages'):size+=_(' pour {v0} paire(s)').format(v0=len(entries))
        return NAMES[family]+' — '+size+(_(' — installé') if self.selection_ready(family,self.pairs) else '')
    def size_order(self,family):
        entries,_unused=self.store.plan_pairs(family,self.pairs)
        total,unknown=self.store.download_size(entries)
        return bool(unknown),total if not unknown else float('inf'),NAMES[family].casefold()
    def refresh_model_list(self,selected=None):
        self.families.sort(key=self.size_order)
        self.model_list.Set([self.model_label(f) for f in self.families])
        self.model_list.SetSelection(self.families.index(selected) if selected in self.families else 0)
        self.model_list.SetFirstItem(0 if selected is None else self.model_list.GetSelection())
    def sizes_ready(self,pairs,family):
        if pairs!=tuple(self.pairs) or family not in self.families:return
        index=self.model_list.GetSelection()
        selected=self.families[index] if index>=0 else None
        if self.busy:
            self.model_list.SetString(self.families.index(family),self.model_label(family))
        else:self.refresh_model_list(selected if self.model_choice_changed else None)
        if self.models.IsShown() and not self.busy:
            self.preview(update_status=not getattr(self,'last_state','').startswith(_('Échec :')))
    def on_model_choice(self,event):
        if self.busy:self.restore_download_choices();return
        self.model_choice_changed=True
        self.preview(event)
    def on_gpu_choice(self,event):
        if self.busy:
            self.gpu.SetValue(self.download_gpu);return
        index=self.model_list.GetSelection()
        if index>=0:self.gpu_choices[self.families[index]]=self.gpu.GetValue()
        self.preview()
    def update_gpu(self,family):
        entries,unused=self.store.plan_pairs(family,self.pairs)
        self.gpu_candidate=gpu_support.candidate(self.info,self.store,family,entries)
        resident=self.hooks.get('gpu_resident',lambda entries:None)(entries)
        if self.gpu_candidate is None and resident is not None:self.gpu_candidate=gpu_support.candidate(self.info,self.store,family,entries,resident,resident=True)
        cfg=self.store.config(self.app)
        chosen=self.gpu_choices.get(family,bool(cfg.get('gpu',False) and cfg.get('family')==family))
        self.gpu.Show(bool(self.gpu_candidate));self.gpu.SetValue(bool(self.gpu_candidate and chosen))
        self.fit_contents()
    def preview(self,event=None,update_status=True):
        index=self.model_list.GetSelection()
        if index<0:return
        family=self.families[index]
        if not self.busy:
            self.update_gpu(family)
            self.finished=self.selection_ready(family,self.pairs)
            self.download.SetLabel(_('Appliquer') if self.finished else _('Télécharger'))
            self.download.Enable(True)
        if update_status:self.status.SetLabel(_('Modèle installé. Choisissez Appliquer.') if self.finished else _('Choisissez le modèle puis Télécharger.'))
        if family=='hy' and not self.gpu.GetValue() and (self.info.get('ram_available') or 0)<12*1024**3:
            self.download.Disable()
            self.status.SetLabel(_('RAM disponible insuffisante pour ce modèle sur CPU. Activez la carte graphique ou choisissez un autre modèle.'))
    def selection_ready(self,family,pairs):
        try:
            entries,_unused=self.store.plan_pairs(family,pairs)
            gpu=self.gpu.GetValue() if self.model_list.GetSelection()>=0 and self.families[self.model_list.GetSelection()]==family else False
            return self.store.runtime_ready(family,gpu) and all(self.store.installed(entry) for entry in entries)
        except ValueError:return False
    def set_editable(self,enabled):
        for control in (self.source,self.target,self.add,self.pair_list,self.remove,self.model_list,self.gpu):
            control.Enable(enabled)
    def announce(self,text):
        self.last_announcement=time.monotonic()
        self.hooks.get('announce',lambda text:None)(text)
    def set_state(self,text,announce=False):
        self.last_state=text;self.status.SetLabel(text)
        if announce:self.announce(text)
    def show_phase(self,stage,title):
        # Installation phases are tracked internally without speaking their details.
        self.stage=stage
    def show_transfer(self,title,file,files,current,total):
        if not self.busy:return
        if total:
            percent=max(getattr(self,'download_percent',0),min(100,int(100*current/total)))
            self.download_percent=percent
            self.download.SetLabel(_('{v0}% du téléchargement').format(v0=percent))
            self.set_state(f'{percent} %')
            bucket=percent//10
            if bucket>getattr(self,'spoken_bucket',-1) and time.monotonic()-getattr(self,'last_announcement',0)>=5:
                self.spoken_bucket=bucket;self.announce(f'{percent} %')
        else:
            self.download.SetLabel(_('Téléchargement en cours'))
            self.set_state(_('Téléchargement en cours'))
    def on_download(self,event):
        if self.dead or self.busy:return
        if self.finished:
            self.finish();return
        index=self.model_list.GetSelection()
        if index<0:return
        self.model_choice_changed=True
        family=self.families[index];pairs=list(self.pairs)
        self.download_gpu=self.gpu.GetValue()
        entries,_unused=self.store.plan_pairs(family,pairs)
        entries=[e for e in entries if not self.store.installed(e)]
        self.download_choices=(tuple(self.source.GetSelections()),self.target.GetSelection(),index)
        self.busy=True;self.cancel.clear()
        self.download_percent=0;self.spoken_bucket=-1;self.download.SetLabel(_('0% du téléchargement'))
        self.set_state('0 %')
        self.show_phase('download',_('Démarrage de l’opération'))
        self.download.SetFocus()
        def work():
            try:
                self.hooks['stop']()
                runtime_specs=self.store.runtime_specs(family,self.download_gpu)
                # Resolve the entire operation before transferring any file, including runtimes.
                size_entries=entries+[dict(spec,id='runtime-'+str(i)) for i,spec in enumerate(runtime_specs)]
                self.store.resolve_download_sizes(size_entries,self.cancel)
                if self.cancel.is_set():raise Cancelled(_('Téléchargement annulé.'))
                tracker=DownloadProgress(self,self.store.download_specs(size_entries))
                if runtime_specs:
                    progress=OperationProgress(self,_('Moteur de traduction'),len(runtime_specs),tracker,runtime_specs)
                    progress.phase('prepare')
                    self.store.ensure_runtime(family,self.cancel,progress,gpu=self.download_gpu)
                for step,entry in enumerate(entries,1):
                    pair=(self.hooks['label'](entry['source'])+_(' vers ')+self.hooks['label'](entry['target'])) if entry.get('source') else NAMES[family]
                    title=_('Modèle {v0} sur {v1}, ').format(v0=step, v1=len(entries))+pair
                    specs=list(entry['files'].values()) if entry.get('files') else [entry]
                    progress=OperationProgress(self,title,len(specs),tracker,specs)
                    progress.phase('prepare')
                    self.store.install(entry,self.cancel,progress)
                if self.cancel.is_set():raise Cancelled(_('Téléchargement annulé.'))
                self.dispatch(self.completed,family,pairs,None)
            except Exception as exc:
                self.hooks.get('report_error',lambda:None)()
                if self.dead:wx.CallAfter(self.hooks['changed'])
                else:self.dispatch(self.completed,family,pairs,str(exc))
        threading.Thread(target=work,daemon=True).start()
    def completed(self,family,pairs,error):
        if not error and not self.selection_ready(family,pairs):error=_('L’installation du modèle est incomplète. Réessayez le téléchargement.')
        if error:
            self.busy=False
            self.set_editable(True)
            self.download.SetLabel(_('Télécharger'));self.set_state(_('Échec : ')+error,announce=True);self.hooks['changed']();return
        self.downloaded_family=family
        self.busy=False
        self.finished=True
        self.set_editable(True)
        self.hooks['changed']();self.download.SetLabel(_('Appliquer'))
        self.set_state(_('Appliquer'),announce=True)
        self.download.SetFocus()
    def finish(self):
        if self.dead or self.busy:return
        index=self.model_list.GetSelection()
        if index<0 or index>=len(self.families):return
        family=self.families[index];pairs=list(self.pairs)
        if not self.selection_ready(family,pairs):return
        try:
            if not self.download_only:
                sources=list(dict.fromkeys(a for a,b in pairs));targets=list(dict.fromkeys(b for a,b in pairs))
                gpu=self.gpu_candidate if self.gpu.GetValue() else None
                self.store.configure(self.app,family,sources,targets,pairs[0][1],cuda_index=gpu.get('cuda_index',0) if gpu else 0,pairs=pairs,gpu=bool(gpu))
                self.hooks['configure'](self.app,sources[0] if len(sources)==1 else 'auto',False,False)
                self.hooks['changed']()
        except (OSError,ValueError):
            self.hooks.get('report_error',lambda:None)()
            self.set_state(_('Impossible d’enregistrer les langues et le modèle. Réessayez Appliquer.'),announce=True);return
        self.downloaded_family=family;self.finished=True
        self.downloaded_gpu=self.gpu.GetValue();self.downloaded_cuda_index=self.gpu_candidate.get('cuda_index',0) if self.downloaded_gpu else 0
        self.dead=True;self.EndModal(wx.ID_OK)
    def on_close(self,event):
        self.waiting=False;self.dead=True;self.cancel.set();self.EndModal(wx.ID_CANCEL)
