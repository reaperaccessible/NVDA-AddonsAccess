"""NVDA's own words: "blank", "check box", "not checked", "heading level 2"...

NVDA says them in its own language, whatever the language of what is being read. They must
never be guessed at: their language is known. Nothing in this module touches NVDA.
"""
import gettext
import re

MAX_LENGTH = 80      # NVDA's short announcements; longer texts are help messages
PLACEHOLDER = re.compile(r'%\([^)]*\)[sd]|%[sd]|\{[^{}]*\}')
MIN_LITERAL_LETTERS = 4   # "heading level {level}" yes, "{text}" alone no


def _simplify(text):
    return ' '.join(text.replace('&', '').split()).casefold()


class OwnMessages:
    """The set of NVDA's interface messages in one language."""

    def __init__(self, strings):
        exact, patterns = set(), []
        for text in strings:
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_LENGTH or '\n' in text.strip():
                continue
            simple = _simplify(text)
            if not PLACEHOLDER.search(text):
                exact.add(simple)
                continue
            literal = PLACEHOLDER.sub('', text)
            if sum(1 for character in literal if character.isalpha()) < MIN_LITERAL_LETTERS:
                continue
            # A placeholder stands for a number or a short word: "heading level 2", "row 3".
            parts = [re.escape(_simplify(part)) for part in PLACEHOLDER.split(text)]
            pattern = r'\s*[\w.,:%-]{1,20}\s*'.join(part for part in parts)
            patterns.append(pattern)
        self._exact = exact
        self._pattern = re.compile('|'.join(f'(?:{pattern})' for pattern in sorted(set(patterns)))) if patterns else None

    def __len__(self):
        return len(self._exact)

    def __contains__(self, text):
        simple = _simplify(text)
        if not simple:
            return False
        if simple in self._exact:
            return True
        return bool(self._pattern and len(simple) <= MAX_LENGTH and self._pattern.fullmatch(simple))


def load(locale_directory, language):
    """NVDA's messages in `language` ('fr_FR', 'fr', 'en'...), read from NVDA's own translation files."""
    base = (language or 'en').replace('-', '_')
    if base.split('_')[0] == 'en':
        # English is the language the messages are written in: any translation file lists them.
        for other in ('fr', 'de', 'es'):
            path = gettext.find('nvda', locale_directory, languages=[other])
            if path:
                with open(path, 'rb') as stream:
                    catalog = gettext.GNUTranslations(stream)._catalog
                return OwnMessages(key[0] if isinstance(key, tuple) else key for key in catalog)
        return OwnMessages(())
    path = gettext.find('nvda', locale_directory, languages=[base, base.split('_')[0]])
    if not path:
        return OwnMessages(())
    with open(path, 'rb') as stream:
        catalog = gettext.GNUTranslations(stream)._catalog
    return OwnMessages(catalog.values())
