"""Confirm an application's source language without blocking NVDA detection."""
from .i18n import _, set_window_direction
import threading
import wx
from .app_languages import normalize
from .detection import Detector

def focus_sample(obj):
    """Read only the focused line/selection; do not guess from the voice language."""
    text='';document=None
    try:
        import textInfos
        for position in (textInfos.POSITION_SELECTION,textInfos.POSITION_CARET):
            try:
                info=obj.makeTextInfo(position)
                if info.isCollapsed:info.expand(textInfos.UNIT_LINE)
                for item in info.getTextWithFields():
                    field=getattr(item,'field',None)
                    if isinstance(field,dict) and field.get('language'):
                        document=normalize(field['language'])
                        if document:break
                text=info.text[:1000]
                if text.strip():break
            except Exception:continue
    except (ImportError,AttributeError):pass
    if not text.strip():text=str(getattr(obj,'name','') or '')[:1000]
    return text,document

def suggestion(sample,preferences,detect):
    text,document=sample
    if document:return document,_('langue indiquée par le document'),None
    if preferences.get('remember') and preferences.get('detected'):
        return preferences['detected'],_('langue mémorisée pour cette application'),None
    if sum(c.isalpha() for c in text)>=3:
        code,confidence=detect(text)
        code=normalize(code)
        if code:return code,_('détection automatique fastText'),confidence
    source=preferences.get('source')
    if source not in ('auto','inherit',None):return source,_('réglage existant de cette application'),None
    return None,None,None

class SourceConfirmation(wx.Dialog):
    def __init__(self,parent,label,codes,language_label,sample,preferences,announce,global_scope=False):
        super().__init__(parent,title=_('LocalTranslate — langue source pour ')+label,size=(570,260))
        set_window_direction(self)
        self.dead=False;self.changed=False;self.language_label=language_label;self.announce=announce
        self.codes=['auto']+sorted(codes,key=lambda c:language_label(c).casefold())
        box=wx.BoxSizer(wx.VERTICAL)
        box.Add(wx.StaticText(self,label=_('Confirmez la langue à traduire pour ')+label+'. Vous pouvez modifier la proposition.'),0,wx.ALL|wx.EXPAND,10)
        box.Add(wx.StaticText(self,label=_('Langue source :')),0,wx.LEFT|wx.RIGHT|wx.TOP,10)
        self.source=wx.Choice(self,choices=[_('Automatique — suivre la langue de chaque texte')]+[language_label(c) for c in self.codes[1:]],name=_('Langue source'))
        initial=preferences.get('source')
        self.source.SetSelection(self.codes.index(initial) if initial in self.codes else 0)
        box.Add(self.source,0,wx.ALL|wx.EXPAND,10)
        scope=_('la traduction globale') if global_scope else _('cette application')
        box.Add(wx.StaticText(self,label=_('Une langue précise sera utilisée pour ')+scope+_('. Choisissez Automatique pour les contenus multilingues.')),0,wx.ALL|wx.EXPAND,10)
        buttons=wx.BoxSizer(wx.HORIZONTAL)
        self.validate=wx.Button(self,wx.ID_OK,label=_('Valider'));self.validate.SetDefault()
        self.validate.Enable(False)
        cancel=wx.Button(self,wx.ID_CANCEL,label=_('Annuler'))
        buttons.Add(self.validate,0,wx.ALL,8);buttons.Add(cancel,0,wx.ALL,8)
        box.Add(buttons,0,wx.ALIGN_RIGHT);self.SetSizer(box)
        self.source.Bind(wx.EVT_CHOICE,self.on_choice)
        self.source.SetFocus()
        def work():
            detector=Detector()
            try:
                result=suggestion(sample,preferences,detector.detect)
                wx.CallAfter(self.proposed,*result)
            except Exception:
                wx.CallAfter(self.proposed,None,None,None)
            finally:detector.close()
        threading.Thread(target=work,daemon=True).start()
    def on_choice(self,event):
        self.changed=True;self.validate.Enable(True);event.Skip()
    def proposed(self,code,origin,confidence):
        if self.dead:return
        self.validate.Enable(True)
        if code in self.codes:
            message=_('Langue proposée : ')+self.language_label(code)+'.'
            if not self.changed:self.source.SetSelection(self.codes.index(code))
            else:message+=_(' Votre choix dans la liste est conservé.')
        else:message=_('La langue n’a pas pu être déterminée. Choisissez une langue dans la liste ou conservez Automatique.')
        self.announce(message)
    def selected_source(self):return self.codes[self.source.GetSelection()]
