"""The window listing the translations made on request since NVDA started."""
from .i18n import _, set_window_direction
import wx


class HistoryDialog(wx.Dialog):
    """
    items:  [(original, translation)], the most recent first.
    copy:   callable(text) putting a text on the clipboard; returns True on success.
    announce: callable(text) speaking a short confirmation.
    """

    def __init__(self, parent, items, copy, announce):
        # Translators: Title of the window listing the translations made on request.
        super().__init__(parent, title=_('Historique des traductions'), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        set_window_direction(self)
        self.items, self.copy, self.announce = items, copy, announce
        sizer = wx.BoxSizer(wx.VERTICAL)
        if not items:
            sizer.Add(wx.StaticText(self, label=_('L’historique des traductions est vide.')), 0, wx.ALL, 12)
            close = wx.Button(self, wx.ID_CANCEL, label=_('Fermer'))
            sizer.Add(close, 0, wx.ALL | wx.ALIGN_RIGHT, 8)
            self.SetSizerAndFit(sizer)
            close.SetFocus()
            return
        # Translators: Label of the list of translations, the most recent first.
        sizer.Add(wx.StaticText(self, label=_('&Traductions, la plus récente en premier :')), 0, wx.ALL, 8)
        self.list = wx.ListBox(self, size=(520, 260), choices=[' '.join(translation.split()) for original, translation in items])
        sizer.Add(self.list, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 8)
        # Translators: Label of the field showing the text that was translated.
        sizer.Add(wx.StaticText(self, label=_('Texte d’&origine :')), 0, wx.ALL, 8)
        self.original = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(520, 90))
        sizer.Add(self.original, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 8)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.copy_translation = wx.Button(self, label=_('&Copier la traduction'))
        self.copy_original = wx.Button(self, label=_('Copier le texte d’o&rigine'))
        close = wx.Button(self, wx.ID_CANCEL, label=_('Fermer'))
        for button in (self.copy_translation, self.copy_original, close):
            buttons.Add(button, 0, wx.ALL, 8)
        sizer.Add(buttons, 0, wx.ALIGN_RIGHT)
        self.SetSizerAndFit(sizer)
        self.copy_translation.SetDefault()   # Enter in the list copies the translation
        self.list.Bind(wx.EVT_LISTBOX, self.show_original)
        self.list.Bind(wx.EVT_LISTBOX_DCLICK, self.on_copy_translation)
        self.copy_translation.Bind(wx.EVT_BUTTON, self.on_copy_translation)
        self.copy_original.Bind(wx.EVT_BUTTON, self.on_copy_original)
        self.list.SetSelection(0)
        self.show_original()
        self.list.SetFocus()

    def selected(self):
        index = self.list.GetSelection()
        return self.items[index] if 0 <= index < len(self.items) else None

    def show_original(self, event=None):
        item = self.selected()
        self.original.SetValue(item[0] if item else '')

    def _copy(self, text, message):
        if text and self.copy(text):
            self.announce(message)

    def on_copy_translation(self, event=None):
        item = self.selected()
        if item:
            self._copy(item[1], _('Traduction copiée dans le presse-papiers.'))

    def on_copy_original(self, event=None):
        item = self.selected()
        if item:
            self._copy(item[0], _('Texte d’origine copié dans le presse-papiers.'))
