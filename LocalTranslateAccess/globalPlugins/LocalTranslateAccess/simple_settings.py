"""The settings everybody needs, on one page: the language to translate into and the
languages installed. Nothing here mentions engines or models."""
from .i18n import _, set_window_direction
import wx
from .language_ui import size_text


class SimpleSettings(wx.Panel):
    """
    tools is a dict of callables supplied by the add-on:
      label(code) -> language name          target() -> current target language code
      add_languages(parent)                 remove(item) -> error text or None
      advanced(parent)                      announce(text)
    """

    def __init__(self, parent, packs, tools):
        super().__init__(parent)
        set_window_direction(self)
        self.packs, self.tools = packs, tools
        self.items = []
        sizer = wx.BoxSizer(wx.VERTICAL)
        # Translators: Label of the choice of the language translations are made into.
        sizer.Add(wx.StaticText(self, label=_('&Traduire vers :')), 0, wx.ALL, 8)
        self.codes = sorted(packs.store.languages(), key=lambda code: tools['label'](code).casefold())
        self.target = wx.Choice(self, choices=[tools['label'](code) for code in self.codes], name=_('Traduire vers'))
        sizer.Add(self.target, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 8)
        # Translators: Label of the list of installed languages.
        sizer.Add(wx.StaticText(self, label=_('&Langues installées :')), 0, wx.ALL, 8)
        self.list = wx.ListBox(self, size=(-1, 200), name=_('Langues installées'))
        sizer.Add(self.list, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 8)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        # Translators: Button opening the list of languages that can be downloaded.
        self.add_button = wx.Button(self, label=_('A&jouter des langues…'))
        # Translators: Button removing the language selected in the list of installed languages.
        self.remove_button = wx.Button(self, label=_('&Supprimer la langue sélectionnée'))
        buttons.Add(self.add_button, 0, wx.ALL, 8)
        buttons.Add(self.remove_button, 0, wx.ALL, 8)
        sizer.Add(buttons, 0)
        self.status = wx.StaticText(self, label='')
        sizer.Add(self.status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 8)
        # Translators: Button opening the settings most people never need.
        self.advanced_button = wx.Button(self, label=_('&Options avancées…'))
        sizer.Add(self.advanced_button, 0, wx.ALL, 8)
        self.SetSizer(sizer)
        self.list.Bind(wx.EVT_LISTBOX, self.update_buttons)
        self.add_button.Bind(wx.EVT_BUTTON, self.on_add)
        self.remove_button.Bind(wx.EVT_BUTTON, self.on_remove)
        self.advanced_button.Bind(wx.EVT_BUTTON, self.on_advanced)
        self.select_target(tools['target']())
        self.reload()

    # --- the language to translate into
    def select_target(self, code):
        self.target.SetSelection(self.codes.index(code) if code in self.codes else wx.NOT_FOUND)

    def chosen_target(self):
        index = self.target.GetSelection()
        return self.codes[index] if 0 <= index < len(self.codes) else None

    # --- the installed languages
    def reload(self, keep=None):
        self.items = self.packs.inventory(self.tools['target']())
        labels = []
        for item in self.items:
            if item.kind == 'multilingual':
                # Translators: A pack bringing many languages at once. {count} is their number.
                name = _('Pack multilingue : {count} langues').format(count=item.count)
            else:
                name = self.tools['label'](item.code)
            labels.append(_('{language} — {size}').format(language=name, size=size_text(item.size)) if item.size else name)
        order = sorted(range(len(labels)), key=lambda index: (self.items[index].kind != 'language', labels[index].casefold()))
        self.items = [self.items[index] for index in order]
        self.list.Set([labels[index] for index in order])
        if self.items:
            self.list.SetSelection(min(keep if keep is not None else 0, len(self.items) - 1))
        self.update_buttons()

    def selected(self):
        index = self.list.GetSelection()
        return self.items[index] if 0 <= index < len(self.items) else None

    def update_buttons(self, event=None):
        item = self.selected()
        self.remove_button.Enable(bool(item and item.removable))
        if item is not None and not item.removable:
            # Translators: Shown when the selected language cannot be removed.
            self.status.SetLabel(_('Cette langue est nécessaire aux autres : elle ne peut pas être supprimée.'))
        elif self.status.GetLabel() == _('Cette langue est nécessaire aux autres : elle ne peut pas être supprimée.'):
            self.status.SetLabel('')

    def say(self, message):
        self.status.SetLabel(message)
        self.tools['announce'](message)

    def on_add(self, event):
        self.tools['add_languages'](self)
        self.reload(self.list.GetSelection())
        self.add_button.SetFocus()

    def on_remove(self, event):
        item = self.selected()
        if item is None or not item.removable:
            return
        index = self.list.GetSelection()
        name = self.list.GetString(index).split(' — ')[0]
        error = self.tools['remove'](item)
        self.reload(index)
        # Translators: Message after a language was removed. {language} is its name.
        self.say(error or _('Langue supprimée : {language}.').format(language=name))
        self.list.SetFocus()

    def on_advanced(self, event):
        self.tools['advanced'](self)
        self.select_target(self.tools['target']())
        self.reload(self.list.GetSelection())
        self.advanced_button.SetFocus()
