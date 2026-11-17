"""Accessible translation cache browser with persistent manual corrections."""
from .i18n import _
import threading
import wx
from .cache_editor import CacheEditor
from .cache_sharing import CacheSharing
from .cache_selection import CacheSelection

class CacheSettings(wx.ScrolledWindow):
    def AcceptsFocus(self):return False
    def AcceptsFocusFromKeyboard(self):return False
    def __init__(self,parent,directory,hooks):
        super().__init__(parent)
        self.SetScrollRate(0,10)
        self.editor=CacheEditor(directory);self.hooks=hooks
        self.records=[];self.visible=[];self.app_keys=[];self.drafts={};self.selections={}
        self.loaded=False;self.loading=False;self.dead=False;self.saving=False;self.transferring=False
        self.current=None;self.search_timer=None
        layout=wx.BoxSizer(wx.VERTICAL)
        # Native accessible labels need to precede their controls in HWND order.
        layout.Add(wx.StaticText(self,label=_('Application :')),0,wx.LEFT|wx.RIGHT|wx.TOP,8)
        self.applications=wx.Choice(self,name=_('Application du cache de traduction'))
        layout.Add(self.applications,0,wx.ALL|wx.EXPAND,8)
        layout.Add(wx.StaticText(self,label=_('Rechercher dans les textes et les traductions :')),0,wx.LEFT|wx.RIGHT|wx.TOP,8)
        self.search=wx.TextCtrl(self,name=_('Rechercher dans le cache de traduction'))
        layout.Add(self.search,0,wx.ALL|wx.EXPAND,8)
        layout.Add(wx.StaticText(self,label=_('Textes en cache :')),0,wx.LEFT|wx.RIGHT|wx.TOP,8)
        self.items=wx.ListBox(self,size=(-1,130),name=_('Textes en cache'))
        layout.Add(self.items,1,wx.ALL|wx.EXPAND,8)
        layout.Add(wx.StaticText(self,label=_('Texte d’origine :')),0,wx.LEFT|wx.RIGHT|wx.TOP,8)
        self.original=wx.TextCtrl(self,style=wx.TE_MULTILINE|wx.TE_READONLY,size=(-1,70),name=_('Texte d’origine'))
        layout.Add(self.original,0,wx.ALL|wx.EXPAND,8)
        layout.Add(wx.StaticText(self,label=_('Traduction à corriger :')),0,wx.LEFT|wx.RIGHT|wx.TOP,8)
        self.translation=wx.TextCtrl(self,style=wx.TE_MULTILINE,size=(-1,95),name=_('Traduction à corriger'))
        layout.Add(self.translation,0,wx.ALL|wx.EXPAND,8)
        buttons=wx.BoxSizer(wx.HORIZONTAL)
        self.save=wx.Button(self,label=_('Enregistrer la correction'))
        self.restore=wx.Button(self,label=_('Rétablir la traduction enregistrée'))
        self.refresh=wx.Button(self,label=_('Actualiser le cache'))
        for button in (self.save,self.restore,self.refresh):buttons.Add(button,0,wx.ALL,4)
        layout.Add(buttons,0,wx.ALL,4)
        sharing=wx.BoxSizer(wx.HORIZONTAL)
        self.export=wx.Button(self,label=_('Exporter un cache'))
        self.import_cache=wx.Button(self,label=_('Importer un cache'))
        sharing.Add(self.export,0,wx.ALL,4);sharing.Add(self.import_cache,0,wx.ALL,4)
        layout.Add(sharing,0,wx.ALL,4)
        self.status=wx.StaticText(self,label=_('Sélectionnez une application, puis un texte à corriger.'))
        layout.Add(self.status,0,wx.ALL|wx.EXPAND,8);self.SetSizer(layout);self.FitInside()
        self.applications.Bind(wx.EVT_CHOICE,self.filter)
        self.search.Bind(wx.EVT_TEXT,self.on_search)
        self.items.Bind(wx.EVT_LISTBOX,self.select)
        self.translation.Bind(wx.EVT_TEXT,self.on_edit)
        self.save.Bind(wx.EVT_BUTTON,self.on_save)
        self.restore.Bind(wx.EVT_BUTTON,self.on_restore)
        self.refresh.Bind(wx.EVT_BUTTON,lambda event:self.reload())
        self.export.Bind(wx.EVT_BUTTON,self.on_export)
        self.import_cache.Bind(wx.EVT_BUTTON,self.on_import)
        self.Bind(wx.EVT_WINDOW_DESTROY,self.on_destroy)
        self.show_record(None)
    def on_destroy(self,event):
        if event.GetEventObject() is self:
            self.dead=True
            if self.search_timer:self.search_timer.Stop()
        event.Skip()
    def dispatch(self,fn,*args):
        def run():
            if not self.dead:fn(*args)
        wx.CallAfter(run)
    def reload(self):
        if self.loading or self.saving or self.transferring or self.dead:return
        self.loading=True;self.refresh.Disable();self.status.SetLabel(_('Lecture du cache en cours…'))
        def work():
            try:self.dispatch(self.loaded_entries,self.editor.entries(),None)
            except (OSError,ValueError) as exc:self.dispatch(self.loaded_entries,[],str(exc))
        threading.Thread(target=work,daemon=True).start()
    def loaded_entries(self,records,error):
        self.loading=False;self.refresh.Enable();self.loaded=True
        if error:self.status.SetLabel(_('Impossible de lire le cache : ')+error);return
        selected=self.applications.GetSelection()
        previous=self.app_keys[selected] if 0<=selected<len(self.app_keys) else None
        self.records=records
        self.app_keys=sorted({r['application'] for r in records},key=str.casefold)
        self.applications.Set([_('Traduction globale') if key=='global' else self.hooks.get('application_label',lambda key:key)(key) for key in self.app_keys])
        if self.app_keys:self.applications.SetSelection(self.app_keys.index(previous) if previous in self.app_keys else 0)
        self.applications.Enable(bool(self.app_keys));self.filter()
    def label(self,record):
        text=' '.join(record['text'].split())
        if len(text)>100:text=text[:97]+'…'
        src=self.hooks['label'](record['source']);tgt=self.hooks['label'](record['target'])
        return _('{v0} — {v1} vers {v2}').format(v0=text, v1=src, v2=tgt)+(_(' — corrigée') if record.get('manual') else '')
    def on_search(self,event):
        if self.search_timer:self.search_timer.Stop()
        self.search_timer=wx.CallLater(250,self.filter)
    def filter(self,event=None):
        if self.dead:return
        previous=self.current['key'] if self.current else None
        index=self.applications.GetSelection()
        app=self.app_keys[index] if 0<=index<len(self.app_keys) else None
        if self.current is None or self.current['application']!=app:previous=self.selections.get(app)
        query=self.search.GetValue().casefold().strip()
        matches=[r for r in self.records if r['application']==app and
            (not query or query in r['text'].casefold() or query in self.drafts.get(r['key'],r['translation']).casefold())]
        matches.sort(key=lambda r:(r['text'].casefold(),r['source'],r['target'],r['namespace']))
        self.visible=matches[:1000]
        self.items.Set([self.label(r) for r in self.visible])
        if self.visible:
            index=next((i for i,r in enumerate(self.visible) if r['key']==previous),0)
            self.items.SetSelection(index);self.show_record(self.visible[index])
        else:self.show_record(None)
        message=_('Traductions en cache : {count}.').format(count=len(matches)) if matches else _('Aucune traduction en cache pour cette recherche.')
        if len(matches)>1000:message+=_(' Les 1 000 premières sont affichées. Affinez la recherche.')
        self.status.SetLabel(message)
    def select(self,event):
        index=self.items.GetSelection()
        self.show_record(self.visible[index] if 0<=index<len(self.visible) else None)
    def show_record(self,record):
        self.current=record
        if record:self.selections[record['application']]=record['key']
        self.original.ChangeValue(record['text'] if record else '')
        self.translation.ChangeValue(self.drafts.get(record['key'],record['translation']) if record else '')
        self.original.Enable(record is not None);self.translation.Enable(record is not None and not self.saving)
        self.update_buttons()
    def update_buttons(self):
        dirty=self.current is not None and self.translation.GetValue()!=self.current['translation']
        self.save.Enable(dirty and bool(self.translation.GetValue().strip()) and not self.saving and not self.transferring)
        self.restore.Enable(dirty and not self.saving and not self.transferring)
    def on_edit(self,event):
        if self.current:
            key=self.current['key'];value=self.translation.GetValue()
            if value==self.current['translation']:self.drafts.pop(key,None)
            else:self.drafts[key]=value
        self.update_buttons()
    def on_restore(self,event):
        if self.current:self.drafts.pop(self.current['key'],None);self.show_record(self.current)
    def on_save(self,event):
        if self.saving or self.transferring or not self.current or not self.save.IsEnabled():return
        record=dict(self.current);translation=self.translation.GetValue()
        self.saving=True;self.translation.Disable();self.update_buttons()
        self.applications.Disable();self.search.Disable();self.items.Disable();self.refresh.Disable()
        def work():
            try:
                saved=self.editor.save(record,translation)
                # Apply invalidation even if the settings window was closed during the write.
                wx.CallAfter(self.hooks.get('cache_changed',lambda:None))
                self.dispatch(self.saved,saved,None)
            except (OSError,ValueError) as exc:self.dispatch(self.saved,record,str(exc))
        threading.Thread(target=work,daemon=True).start()
    def saved(self,record,error):
        self.saving=False
        self.applications.Enable(bool(self.app_keys));self.search.Enable();self.items.Enable();self.refresh.Enable()
        if error:self.show_record(self.current);self.status.SetLabel(_('Correction non enregistrée : ')+error);return
        self.drafts.pop(record['key'],None)
        self.records=[record if r['key']==record['key'] else r for r in self.records]
        self.filter();self.status.SetLabel(_('Correction enregistrée. Elle sera utilisée pour ce texte dans cette application.'))
        self.translation.SetFocus()
        self.hooks.get('announce',lambda text:None)(_('Correction enregistrée.'))
    def sharing_busy(self,value):
        self.transferring=value
        for control in (self.export,self.import_cache,self.search,self.items,self.refresh):control.Enable(not value)
        self.applications.Enable(not value and bool(self.app_keys))
        self.translation.Enable(not value and self.current is not None)
        self.update_buttons()
    def on_export(self,event):
        if self.transferring or self.saving or self.loading:return
        applications={r['application'] for r in self.records}
        if not applications:self.status.SetLabel(_('Aucune traduction à exporter.'));return
        selection=CacheSelection(self,applications,self.hooks)
        try:
            if selection.ShowModal()!=wx.ID_OK:return
            included=selection.included()
        finally:selection.Destroy()
        dialog=wx.FileDialog(self,message=_('Enregistrer le cache à partager'),defaultFile='LocalTranslate-cache.ltcache',
            wildcard='Cache LocalTranslate (*.ltcache)|*.ltcache',style=wx.FD_SAVE|wx.FD_OVERWRITE_PROMPT)
        try:
            if dialog.ShowModal()!=wx.ID_OK:return
            path=dialog.GetPath()
        finally:dialog.Destroy()
        self.sharing_busy(True);self.status.SetLabel(_('Export du cache en cours…'))
        def work():
            try:self.dispatch(self.exported,CacheSharing(self.editor.directory).export(path,included),path,None)
            except (OSError,ValueError) as exc:self.dispatch(self.exported,0,path,str(exc))
        threading.Thread(target=work,daemon=True).start()
    def exported(self,count,path,error):
        self.sharing_busy(False)
        text=_('Export impossible : ')+error if error else _('Traductions exportées : {count}. Fichier : {path}.').format(count=count,path=path)
        self.status.SetLabel(text);self.hooks.get('announce',lambda text:None)(text)
        self.export.SetFocus()
    def on_import(self,event):
        if self.transferring or self.saving or self.loading:return
        dialog=wx.FileDialog(self,message=_('Choisir un cache à importer'),wildcard='Cache LocalTranslate (*.ltcache)|*.ltcache',
            style=wx.FD_OPEN|wx.FD_FILE_MUST_EXIST)
        try:
            if dialog.ShowModal()!=wx.ID_OK:return
            path=dialog.GetPath()
        finally:dialog.Destroy()
        self.sharing_busy(True);self.status.SetLabel(_('Lecture du cache à importer…'))
        def work():
            try:self.dispatch(self.import_ready,CacheSharing(self.editor.directory).read(path),None)
            except (OSError,ValueError) as exc:self.dispatch(self.import_ready,[],str(exc))
        threading.Thread(target=work,daemon=True).start()
    def import_ready(self,records,error):
        if error:
            self.sharing_busy(False);self.status.SetLabel(_('Import impossible : ')+error);return
        if not records:
            self.sharing_busy(False);self.status.SetLabel(_('Ce fichier ne contient aucune traduction.'));return
        selection=CacheSelection(self,{r['application'] for r in records},self.hooks,importing=True)
        try:
            if selection.ShowModal()!=wx.ID_OK:
                self.sharing_busy(False);self.status.SetLabel(_('Import annulé.'));return
            included=selection.included();replace=selection.replace.GetValue()
        finally:selection.Destroy()
        self.status.SetLabel(_('Import du cache en cours…'))
        def work():
            try:
                result=CacheSharing(self.editor.directory).merge(records,included,replace)
                self.dispatch(self.imported,result,None)
            except (OSError,ValueError) as exc:self.dispatch(self.imported,None,str(exc))
            finally:wx.CallAfter(self.hooks.get('cache_changed',lambda:None))
        threading.Thread(target=work,daemon=True).start()
    def imported(self,result,error):
        self.sharing_busy(False)
        if error:
            self.status.SetLabel(_('Import interrompu : ')+error);return
        text=(_('Import terminé. Ajouts : {added}. Remplacements : {replaced}. Entrées déjà présentes : {kept}. Corrections personnelles conservées : {protected}.').format(**result))
        # Reload results off-thread, then restore the completion message.
        def work():
            try:self.dispatch(self.import_reloaded,self.editor.entries(),text)
            except (OSError,ValueError) as exc:self.dispatch(self.loaded_entries,[],str(exc))
        self.loading=True
        threading.Thread(target=work,daemon=True).start()
    def import_reloaded(self,records,text):
        self.loaded_entries(records,None);self.status.SetLabel(text)
        self.import_cache.SetFocus();self.hooks.get('announce',lambda text:None)(text)
