"""Native multiple-selection application list used for cache export and import."""
from .i18n import _, set_window_direction
import wx

class CacheSelection(wx.Dialog):
    def __init__(self,parent,applications,hooks,importing=False):
        super().__init__(parent,title=_('Importer un cache de traduction') if importing else _('Exporter le cache de traduction'),size=(620,540))
        set_window_direction(self)
        self.keys=sorted(set(applications)-{'global'},key=str.casefold)
        outer=wx.BoxSizer(wx.VERTICAL)
        outer.Add(wx.StaticText(self,label=_('Choisissez les applications à inclure. Les textes, traductions et corrections sont inclus.')),0,wx.ALL|wx.EXPAND,10)
        self.all_apps=wx.CheckBox(self,label=_('Toutes les applications'));self.all_apps.SetValue(importing and bool(self.keys))
        self.all_apps.Enable(bool(self.keys));outer.Add(self.all_apps,0,wx.ALL,10)
        self.global_cache=wx.CheckBox(self,label=_('Inclure le cache global'));self.global_cache.SetValue('global' in applications)
        self.global_cache.Enable('global' in applications);outer.Add(self.global_cache,0,wx.ALL,10)
        outer.Add(wx.StaticText(self,label=_('Applications (sélectionnez avec Espace) :')),0,wx.LEFT|wx.RIGHT|wx.TOP,10)
        self.applications=wx.ListBox(self,choices=[hooks.get('application_label',lambda key:key)(key) for key in self.keys],
            style=wx.LB_MULTIPLE,name=_('Applications à inclure'),size=(-1,230))
        if importing:
            for index in range(len(self.keys)):self.applications.SetSelection(index)
        outer.Add(self.applications,1,wx.ALL|wx.EXPAND,10)
        self.replace=None
        if importing:
            self.replace=wx.CheckBox(self,label=_('Remplacer les traductions automatiques déjà présentes'))
            self.replace.SetValue(True);outer.Add(self.replace,0,wx.ALL,10)
            outer.Add(wx.StaticText(self,label=_('Vos corrections personnelles déjà enregistrées sont conservées.')),0,wx.ALL|wx.EXPAND,10)
        row=wx.BoxSizer(wx.HORIZONTAL)
        self.action=wx.Button(self,wx.ID_OK,label=_('Importer') if importing else _('Exporter'))
        cancel=wx.Button(self,wx.ID_CANCEL,label=_('Annuler'))
        row.Add(self.action,0,wx.ALL,5);row.Add(cancel,0,wx.ALL,5);outer.Add(row,0,wx.ALIGN_RIGHT|wx.ALL,5)
        self.SetSizer(outer)
        self.all_apps.Bind(wx.EVT_CHECKBOX,self.toggle_all)
        self.applications.Bind(wx.EVT_LISTBOX,self.selected)
        self.global_cache.Bind(wx.EVT_CHECKBOX,self.selected)
        self.selected(None)
        if not importing and self.keys:self.all_apps.SetFocus()
        elif 'global' in applications:self.global_cache.SetFocus()
        else:self.applications.SetFocus()
    def included(self):
        keys=[self.keys[i] for i in self.applications.GetSelections()]
        if self.global_cache.GetValue():keys.append('global')
        return keys
    def toggle_all(self,event):
        for index in range(len(self.keys)):
            if self.all_apps.GetValue():self.applications.SetSelection(index)
            else:self.applications.Deselect(index)
        self.selected(None)
    def selected(self,event):
        self.all_apps.SetValue(bool(self.keys) and len(self.applications.GetSelections())==len(self.keys))
        self.action.Enable(bool(self.included()))
