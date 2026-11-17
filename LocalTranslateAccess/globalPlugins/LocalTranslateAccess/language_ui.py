"""Simple windows about languages: download one, or tick the ones to add.

No window here mentions engines or models: only language names and sizes.
"""
from .i18n import _, set_window_direction
import threading
import time
import wx
from .model_store import Cancelled


def size_text(size):
    """'26 Mo' for a download size in bytes."""
    megabytes = max(1, int(round(size / 1e6)))
    # Translators: A download size. {megabytes} is a whole number.
    return _('{megabytes} Mo').format(megabytes=megabytes)


class DownloadDialog(wx.Dialog):
    """Downloads languages and shows one progress figure. ShowModal gives wx.ID_OK on success."""
    ANNOUNCE_EVERY = 5.0   # seconds between two spoken percentages

    def __init__(self, parent, packs, plans, title, announce=lambda text: None, report_error=lambda: None):
        super().__init__(parent, title=title)
        set_window_direction(self)
        self.packs, self.plans, self.announce, self.report_error = packs, plans, announce, report_error
        self.cancel = threading.Event()
        self.error = None
        self.spoken = -1
        self.last_spoken = 0.0
        sizer = wx.BoxSizer(wx.VERTICAL)
        # Translators: Status shown while languages are being downloaded.
        self.status = wx.StaticText(self, label=_('Téléchargement en cours…'))
        sizer.Add(self.status, 0, wx.ALL | wx.EXPAND, 12)
        self.gauge = wx.Gauge(self, range=100, size=(360, -1))
        sizer.Add(self.gauge, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 12)
        self.cancel_button = wx.Button(self, wx.ID_CANCEL, label=_('Annuler'))
        sizer.Add(self.cancel_button, 0, wx.ALL | wx.ALIGN_RIGHT, 12)
        self.SetSizerAndFit(sizer)
        self.cancel_button.Bind(wx.EVT_BUTTON, self.on_cancel)
        self.Bind(wx.EVT_CLOSE, self.on_cancel)
        self.cancel_button.SetFocus()
        self.started = False

    def ShowModal(self):
        # The download starts once the window is really modal: a download that ends at
        # once could otherwise try to close a window that is not open yet, and block it.
        wx.CallAfter(self.start)
        return super().ShowModal()

    def start(self):
        if self.started:
            return
        self.started = True
        threading.Thread(target=self.work, daemon=True, name='LocalTranslateAccessDownload').start()

    def work(self):
        error = None
        try:
            self.packs.download(self.plans, self.cancel, self.progress)
            if self.cancel.is_set():
                raise Cancelled()
        except Cancelled:
            error = 'cancelled'
        except Exception:
            self.report_error()
            error = 'failed'
        wx.CallAfter(self.done, error)

    def progress(self, done, total):
        wx.CallAfter(self.show_progress, done, total)

    def show_progress(self, done, total):
        if not self or self.cancel.is_set():
            return
        if not total:
            self.gauge.Pulse()
            return
        percent = min(100, int(done * 100 / total))
        self.gauge.SetValue(percent)
        # Translators: Progress of a download. {percent} is a number from 0 to 100.
        text = _('Téléchargement : {percent} %').format(percent=percent)
        self.status.SetLabel(text)
        now = time.monotonic()
        if percent // 10 > self.spoken and now - self.last_spoken >= self.ANNOUNCE_EVERY:
            self.spoken, self.last_spoken = percent // 10, now
            self.announce(text)

    def done(self, error):
        if not self:
            return
        self.error = error
        self.finished = True
        if self.IsModal():
            self.EndModal(wx.ID_OK if error is None else wx.ID_CANCEL)

    def on_cancel(self, event):
        # The worker stops at its next step and closes the window itself.
        self.cancel.set()
        self.cancel_button.Enable(False)
        self.status.SetLabel(_('Téléchargement annulé.'))


def download(parent, packs, plans, title, announce=lambda text: None, report_error=lambda: None):
    """Download the plans behind a progress window. Returns None on success, else 'cancelled' or 'failed'."""
    plans = [plan for plan in plans if not plan.ready]
    if not plans:
        return None
    dialog = DownloadDialog(parent, packs, plans, title, announce, report_error)
    try:
        dialog.ShowModal()
        return dialog.error
    finally:
        dialog.Destroy()


class AddLanguagesDialog(wx.Dialog):
    """A list of languages to tick, then one Download button."""

    def __init__(self, parent, packs, target, label, announce=lambda text: None, report_error=lambda: None, changed=lambda: None):
        # Translators: Title of the window where languages are chosen for download.
        super().__init__(parent, title=_('Ajouter des langues'))
        set_window_direction(self)
        self.packs, self.target, self.label = packs, target, label
        self.announce, self.report_error, self.changed = announce, report_error, changed
        self.added = False
        sizer = wx.BoxSizer(wx.VERTICAL)
        # Translators: Label of the list of languages that can be downloaded.
        sizer.Add(wx.StaticText(self, label=_('Cochez les langues à ajouter :')), 0, wx.ALL, 8)
        self.list = wx.CheckListBox(self, size=(420, 320), name=_('Langues à ajouter'))
        sizer.Add(self.list, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 8)
        self.status = wx.StaticText(self, label='')
        sizer.Add(self.status, 0, wx.ALL | wx.EXPAND, 8)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.download_button = wx.Button(self, label=_('Télécharger'))
        close = wx.Button(self, wx.ID_CANCEL, label=_('Fermer'))
        buttons.Add(self.download_button, 0, wx.ALL, 8)
        buttons.Add(close, 0, wx.ALL, 8)
        sizer.Add(buttons, 0, wx.ALIGN_RIGHT)
        self.SetSizerAndFit(sizer)
        self.list.Bind(wx.EVT_CHECKLISTBOX, self.update)
        self.download_button.Bind(wx.EVT_BUTTON, self.on_download)
        self.reload()
        self.list.SetFocus()

    def reload(self):
        """List the languages not installed yet, in alphabetical order, with their download size."""
        choices = []
        for code, plan in self.packs.languages(self.target):
            if not plan.ready:
                choices.append((self.label(code), code, plan))
        choices.sort(key=lambda choice: choice[0].casefold())
        self.choices = choices
        # Translators: An entry of the list of languages to add: the language, then its download size.
        self.list.Set([_('{language} — {size}').format(language=name, size=size_text(plan.size)) for name, code, plan in choices])
        if choices:
            self.list.SetSelection(0)
        self.update()

    def chosen(self):
        return [self.choices[index] for index in self.list.GetCheckedItems()]

    def update(self, event=None):
        chosen = self.chosen()
        if not self.choices:
            self.status.SetLabel(_('Toutes les langues disponibles sont déjà installées.'))
        elif not chosen:
            self.status.SetLabel(_('Aucune langue cochée.'))
        else:
            self.status.SetLabel('')
        total = self.total_size(chosen)
        # Translators: The Download button once languages are ticked; {size} is the total download size.
        self.download_button.SetLabel(_('Télécharger ({size})').format(size=size_text(total)) if chosen else _('Télécharger'))
        self.download_button.Enable(bool(chosen))
        self.Layout()

    def total_size(self, chosen):
        """Languages often share files: count each file once."""
        specs = {}
        for name, code, plan in chosen:
            for spec in self.packs.store.download_specs(
                    plan.missing + [dict(spec, id='runtime-' + spec['url']) for spec in plan.runtimes]):
                specs[spec['url']] = spec.get('bytes') or 0
        return sum(specs.values())

    def on_download(self, event):
        chosen = self.chosen()
        if not chosen:
            return
        names = ', '.join(name for name, code, plan in chosen)
        # Translators: Title of the download progress window.
        error = download(self, self.packs, [plan for name, code, plan in chosen], _('Téléchargement des langues'),
            self.announce, self.report_error)
        self.changed()
        self.reload()
        if error is None:
            self.added = True
            # Translators: Message after languages were installed. {languages} is their list.
            message = _('Langues installées : {languages}.').format(languages=names)
        elif error == 'cancelled':
            message = _('Téléchargement annulé.')
        else:
            message = _('Le téléchargement a échoué. Vérifiez votre connexion Internet, puis réessayez.')
        self.status.SetLabel(message)
        self.announce(message)
        self.list.SetFocus()
