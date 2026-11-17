"""Shared gettext catalogue for all LocalTranslate interface modules."""
from pathlib import Path
import gettext
import re
import re

ROOT=Path(__file__).resolve().parents[2]
_catalog=gettext.NullTranslations()
_language='fr'

def set_language(language=None):
    global _catalog,_language
    if language is None:
        try:
            import languageHandler
            language=languageHandler.getLanguage()
        except (ImportError,AttributeError):language='fr'
    _language=(language or 'fr').replace('-','_')
    base=_language.split('_')[0]
    # French is the source language of the messages; every other language gets English.
    languages=[] if base=='fr' else ['en']
    _catalog=gettext.translation('localtranslate',localedir=ROOT/'locale',languages=languages,fallback=True)

# The messages were written for LocalTranslate; show the add-on's current name everywhere.
_NAME=re.compile(r'LocalTranslate(?!Access)')
def _(message):return _NAME.sub('LocalTranslateAccess',_catalog.gettext(message))
def ngettext(singular,plural,count):return _NAME.sub('LocalTranslateAccess',_catalog.ngettext(singular,plural,count))

def set_window_direction(window):
    if _language.split('_')[0] in ('ar','fa','he','ur','ckb'):
        import wx
        window.SetLayoutDirection(wx.Layout_RightToLeft)

set_language()
