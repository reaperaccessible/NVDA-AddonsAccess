"""Editing the favourite languages: up to ten, each a language to translate into, and
optionally the language of the texts when it should not be detected."""
from .i18n import _, set_window_direction
import wx
from .ondemand import MAX_FAVORITES, favorite_key


def favorite_label(source, target, label):
    """'Espagnol', or 'Espagnol, depuis l'allemand' when the source is imposed."""
    if source == 'auto':
        return label(target)
    # Translators: A favourite language whose source language is imposed. {target} and {source} are language names.
    return _('{target}, depuis : {source}').format(target=label(target), source=label(source))


class AddFavoriteDialog(wx.Dialog):
    def __init__(self, parent, codes, label):
        # Translators: Title of the window adding a favourite language.
        super().__init__(parent, title=_('Ajouter une langue favorite'))
        set_window_direction(self)
        self.codes = sorted(codes, key=lambda code: label(code).casefold())
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label=_('Traduire &vers :')), 0, wx.ALL, 8)
        self.target = wx.Choice(self, choices=[label(code) for code in self.codes])
        sizer.Add(self.target, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 8)
        # Translators: Label of the choice of the language of the texts, for a favourite language.
        sizer.Add(wx.StaticText(self, label=_('Langue des textes à traduire :')), 0, wx.ALL, 8)
        self.source = wx.Choice(self, choices=[_('Détection automatique')] + [label(code) for code in self.codes])
        sizer.Add(self.source, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 8)
        self.source.SetSelection(0)
        if self.codes:
            self.target.SetSelection(0)
        sizer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        self.SetSizerAndFit(sizer)
        self.target.SetFocus()

    def chosen(self):
        target = self.codes[self.target.GetSelection()] if self.target.GetSelection() >= 0 else None
        index = self.source.GetSelection()
        source = 'auto' if index <= 0 else self.codes[index - 1]
        if not target or source == target:
            return None
        return source, target


class FavoritesEditor(wx.Panel):
    def __init__(self, parent, favorites, codes, label, announce=lambda text: None):
        super().__init__(parent)
        set_window_direction(self)
        self.favorites, self.codes, self.label, self.announce = list(favorites), codes, label, announce
        sizer = wx.BoxSizer(wx.VERTICAL)
        # Translators: Label of the list of favourite languages.
        sizer.Add(wx.StaticText(self, label=_('Langues &favorites :')), 0, wx.ALL, 8)
        self.list = wx.ListBox(self, size=(-1, 150), name=_('Langues favorites'))
        sizer.Add(self.list, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 8)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.add = wx.Button(self, label=_('&Ajouter une langue favorite…'))
        self.remove = wx.Button(self, label=_('&Retirer'))
        self.up = wx.Button(self, label=_('&Monter'))
        self.down = wx.Button(self, label=_('&Descendre'))
        for button in (self.add, self.remove, self.up, self.down):
            buttons.Add(button, 0, wx.ALL, 8)
        sizer.Add(buttons, 0)
        self.status = wx.StaticText(self, label='')
        sizer.Add(self.status, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 8)
        self.SetSizer(sizer)
        self.list.Bind(wx.EVT_LISTBOX, self.update)
        self.add.Bind(wx.EVT_BUTTON, self.on_add)
        self.remove.Bind(wx.EVT_BUTTON, self.on_remove)
        self.up.Bind(wx.EVT_BUTTON, lambda event: self.move(-1))
        self.down.Bind(wx.EVT_BUTTON, lambda event: self.move(1))
        self.refresh(0)

    def refresh(self, select=None):
        # Translators: An entry of the list of favourite languages. {key} is the digit that chooses it in the menu.
        self.list.Set([_('{key} : {language}').format(key=favorite_key(index), language=favorite_label(source, target, self.label))
                       for index, (source, target) in enumerate(self.favorites)])
        if self.favorites and select is not None:
            self.list.SetSelection(max(0, min(select, len(self.favorites) - 1)))
        self.update()

    def update(self, event=None):
        index = self.list.GetSelection()
        full = len(self.favorites) >= MAX_FAVORITES
        self.add.Enable(not full)
        self.remove.Enable(index >= 0)
        self.up.Enable(index > 0)
        self.down.Enable(0 <= index < len(self.favorites) - 1)
        if not self.favorites:
            self.status.SetLabel(_('Aucune langue favorite.'))
        elif full:
            self.status.SetLabel(_('Dix langues favorites au maximum.'))
        else:
            self.status.SetLabel('')

    def on_add(self, event):
        dialog = AddFavoriteDialog(self, self.codes, self.label)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            favorite = dialog.chosen()
        finally:
            dialog.Destroy()
        if favorite is None:
            return
        if favorite in self.favorites:
            self.announce(_('Cette langue favorite existe déjà.'))
            self.refresh(self.favorites.index(favorite))
        else:
            self.favorites.append(favorite)
            self.refresh(len(self.favorites) - 1)
        self.list.SetFocus()

    def on_remove(self, event):
        index = self.list.GetSelection()
        if index < 0:
            return
        del self.favorites[index]
        self.refresh(index)
        (self.list if self.favorites else self.add).SetFocus()

    def move(self, offset):
        index = self.list.GetSelection()
        other = index + offset
        if index < 0 or not 0 <= other < len(self.favorites):
            return
        self.favorites[index], self.favorites[other] = self.favorites[other], self.favorites[index]
        self.refresh(other)
        self.list.SetFocus()
