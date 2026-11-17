# -*- coding: utf-8 -*-
"""
pipeline.py
Stratégie de traduction pour LocalTranslate (NVDA) :
- Segmentation sur les fins de phrase : '.', '…', '?', '!' et '...'
  → on ignore les virgules pour le découpage
- Stabilisation ultra-courts (1–2 mots) avec "coussin" en LANGUE SOURCE :
  * 1 mot  -> <prefixe_mot> + X
  * 2 mots -> <prefixe_phrase> + X (sauf si le segment finit par '?', alors pas de coussin)
- Traduction CT2 (NLLB-200 d600M) avec tags langue imposés
  * ultra-courts: n-best, préférer une sortie sans guillemets, la plus courte non vide
- Post-édition GENERIQUE pour toutes les langues :
  * suppression du préfixe “coussin” selon la LANGUE CIBLE
  * nettoyage ponctuation/espaces minimal
- Post-édition FR spécifique (NBSP avant !?:;, élisions, dé-quotage 1 mot)
- Reconstruction finale
"""

from __future__ import annotations
import re
from typing import List, Dict, Tuple, Optional

# Ces deux variables sont alimentées par worker.py via init_models()
_TOKENIZER = None   # transformers tokenizer (NLLB)
_TRANSLATOR = None  # ctranslate2.Translator

# ----------------- API publique -----------------

def init_models(tokenizer, translator) -> None:
    """Appelée une seule fois par worker.py après chargement des modèles."""
    global _TOKENIZER, _TRANSLATOR
    _TOKENIZER = tokenizer
    _TRANSLATOR = translator

# ----------------- Outils de segmentation -----------------
# PONCTUATION de fin conservée pour les règles (coussin, nettoyage…)
_PUNCT_SET = {',', '.', '!', '?', '…', ';'}

# Déclencheurs de **découpage** de phrase (on n'utilise pas la virgule)
_SENT_SPLIT_TRIGGERS = {'.', '…', '?', '!'}  # '...' géré explicitement

_CLOSERS   = {'"', "”", "»", "’", "'", ')', ']', '}'}

def _safe_split(text: str) -> List[str]:
    """Découpe sur '.', '…', '?', '!' et '...' (fin de phrase).
    Rattache la ponctuation au segment précédent, absorbe guillemets fermants et l'espace suivant."""
    s = text
    parts: List[str] = []
    i = 0
    n = len(s)
    start = 0
    while i < n:
        # ellipses '...'
        if i+2 < n and s[i] == '.' and s[i+1] == '.' and s[i+2] == '.':
            j = i + 3
            while j < n and s[j] in _CLOSERS:
                j += 1
            if j < n and s[j].isspace():
                j += 1
            chunk = s[start:j].strip()
            if chunk:
                parts.append(chunk)
            start = j
            i = j
            continue
        ch = s[i]
        # ellipsis '…', point '.', point d'interrogation '?', exclamation '!'
        if ch in _SENT_SPLIT_TRIGGERS:
            j = i + 1
            while j < n and s[j] in _CLOSERS:
                j += 1
            if j < n and s[j].isspace():
                j += 1
            chunk = s[start:j].strip()
            if chunk:
                parts.append(chunk)
            start = j
            i = j
            continue
        i += 1
    if start < n:
        tail = s[start:].strip()
        if tail:
            parts.append(tail)
    return parts

def _alpha_words(seg: str) -> List[str]:
    out: List[str] = []
    cur: List[str] = []
    for ch in seg:
        if ch.isalpha():
            cur.append(ch)
        else:
            if cur:
                out.append("".join(cur))
                cur = []
    if cur:
        out.append("".join(cur))
    return out

def _short_kind(seg: str) -> int:
    """0 = non court ; 1 = 1 mot ; 2 = 2 mots"""
    n = len(_alpha_words(seg))
    return n if 1 <= n <= 2 else 0

def _end_punct(seg: str) -> str:
    if seg.endswith('...'):
        return '...'
    if seg and seg[-1] in _PUNCT_SET:
        return seg[-1]
    return ""

def _strip_end(seg: str) -> str:
    if seg.endswith('...'):
        return seg[:-3].rstrip()
    if seg and seg[-1] in _PUNCT_SET:
        return seg[:-1].rstrip()
    return seg.strip()

def _strip_trailing_punct_any(s: str) -> str:
    if s.endswith('...'):
        return s[:-3].rstrip()
    while s and (s[-1] in _PUNCT_SET):
        s = s[:-1]
    return s.strip()

# ----------------- Détection langue (helpers simples) -----------------

def _is_french(code: str) -> bool:
    return (code or "").lower().startswith("fra_")

def _is_english(code: str) -> bool:
    return (code or "").lower().startswith("eng_")

def _lang_root(code: str) -> str:
    """Renvoie la racine NLLB (ex. 'eng' pour 'eng_Latn')."""
    return (code or "").split("_", 1)[0].lower()

# ----------------- Carte des préfixes par langue source -----------------

# Pour chaque langue NLLB, on fournit (prefix_mot, prefix_phrase).
# Objectif : fournir un contexte minimal dans LA langue source.
_CUSHION_MAP: Dict[str, Tuple[str, str]] = {
    # occidentales majeures
    "eng": ("the word is: ", "the phrase is: "),
    "fra": ("le mot est : ", "la phrase est : "),
    "deu": ("das wort ist: ", "der satz ist: "),
    "spa": ("la palabra es: ", "la frase es: "),
    "ita": ("la parola è: ", "la frase è: "),
    "por": ("a palavra é: ", "a frase é: "),
    "nld": ("het woord is: ", "de zin is: "),
    "dan": ("ordet er: ", "sætningen er: "),
    "nob": ("ordet er: ", "setningen er: "),
    "fin": ("sana on: ", "lause on: "),
    "hun": ("a szó: ", "a mondat: "),
    "pol": ("słowo to: ", "zdanie to: "),
    "ron": ("cuvântul este: ", "propoziția este: "),
    "hrv": ("riječ je: ", "rečenica je: "),
    "slk": ("slovo je: ", "veta je: "),
    "glg": ("a palabra é: ", "a frase é: "),
    "tur": ("kelime: ", "cümle: "),
    # slaves (cyrillique surtout)
    "bul": ("думата е: ", "изречението е: "),
    "rus": ("слово: ", "фраза: "),
    "ukr": ("слово: ", "речення: "),
    # serbe (deux scripts)
    "srp": ("reč je: ", "rečenica je: "),    # par défaut Latin
    "srp_Cyrl": ("реч је: ", "реченица је: "),
    "srp_Latn": ("reč je: ", "rečenica je: "),
    # proche-orient
    "heb": ("המילה היא: ", "המשפט הוא: "),
    "arb": ("الكلمة هي: ", "الجملة היא: "),
    # asie
    "vie": ("từ là: ", "câu là: "),
    "jpn": ("単語は: ", "文は: "),
    "zho": ("这个词是：", "这个短语是："),       # générique ; script précis géré plus bas
    "zho_Hans": ("这个词是：", "这个短语是："),
    "zho_Hant": ("這個詞是：", "這個短語是："),
    "tam": ("சொல்: ", "வாக்கியம்: "),
    "npi": ("शब्द: ", "वाक्य: "),
}

# ----------------- Coussin (ultra-courts) -----------------

def _cushion_prefixes_for(code: str) -> Optional[Tuple[str, str]]:
    """Retourne (prefix_mot, prefix_phrase) pour une langue NLLB donnée."""
    if not code:
        return None
    # correspondance stricte puis racine (ex. zho_Hans -> zho si absent)
    if code in _CUSHION_MAP:
        return _CUSHION_MAP[code]
    root = _lang_root(code)
    return _CUSHION_MAP.get(root)

def _cushion_segment(seg: str, kind: int, src_code: str) -> str:
    """
    Applique le coussin 1–2 mots dans LA langue source si connue.
    kind: 1=un mot, 2=deux mots ; pas de coussin si '2 mots + ?'
    """
    ends = _end_punct(seg)
    if kind == 2 and ends == "?":
        return seg  # règle : pas de coussin sur "2 mots + ?"
    prefixes = _cushion_prefixes_for(src_code)
    if not prefixes:
        return seg  # langue source non prise en charge -> pas de coussin
    core = _strip_end(seg)
    pref = prefixes[0] if kind == 1 else prefixes[1]
    return f"{pref}{core}"

# ----------------- Pré/ Post-édition & sélection -----------------

def _has_quotes(x: str) -> bool:
    # inclut guillemets « » et variantes
    return any(q in x for q in ['"', '“', '”', '«', '»', '„', '‟'])

def _norm_len(t: str) -> int:
    return sum(ch.isalnum() for ch in t)

def _best_short_variant(cands: List[str]) -> str:
    # garder au moins une lettre, préférer sans guillemets, puis la plus courte
    c = [x for x in cands if any(ch.isalpha() for ch in x)]
    if not c:
        return ""
    c.sort(key=lambda x: (_has_quotes(x), _norm_len(x)))
    return c[0].strip()

# --- Nettoyage des préfixes “coussin” dans la LANGUE CIBLE ---

def _mk_prefix_pattern(raw: str) -> re.Pattern:
    # Normalise et prépare les variantes d’espaces
    s = (raw or "").strip().replace("\u00A0", " ")
    # On échappe tout...
    esc = re.escape(s)
    # ...puis on réintroduit nos tolérances :
    #  - ":" peut être ':' ASCII ou '：' pleine chasse
    esc = esc.replace(r"\:", r"[:：]")
    #  - les espaces ordinaires acceptent aussi NBSP, et en quantité variable
    esc = esc.replace(r"\ ", r"[ \u00A0]*")
    # Insensible à la casse, au début de chaîne, avec éventuels espaces initiaux
    return re.compile(r"^\s*" + esc + r"[ \u00A0]*", flags=re.IGNORECASE)

def _strip_lang_prefix(s: str, tgt_code: str) -> str:
    """Supprime le préfixe <prefix_mot>/<prefix_phrase> au début de la sortie cible."""
    if not s or not tgt_code:
        return s
    pfx = _cushion_prefixes_for(tgt_code) or _cushion_prefixes_for(_lang_root(tgt_code))
    if not pfx:
        return s
    for pref in (pfx[0], pfx[1]):
        pat = _mk_prefix_pattern(pref)
        s2 = pat.sub("", s, count=1).strip()
        if s2 != s:
            return s2
    return s

_FR_PREFIX_RE = re.compile(
    r"^\s*(?:le\s+mot\s+est|la\s+phrase\s+est|l['’]expression\s+est|expression)\s*[:\s\u00A0-]*",
    flags=re.IGNORECASE,
)

def _strip_fr_prefix(s: str) -> str:
    return _FR_PREFIX_RE.sub("", s).strip()

def _strip_any_prefix(s: str, tgt_code: str) -> str:
    """Nettoie d'abord le préfixe dans la langue cible, puis le spécifique FR si besoin."""
    s = _strip_lang_prefix(s, tgt_code)
    if _is_french(tgt_code):
        s = _strip_fr_prefix(s)
    return s

def _transfer_capitalization(src: str, dst: str) -> str:
    """Si la 1re lettre alpha de src est majuscule, met la 1re alpha de dst en majuscule."""
    if not src or not dst:
        return dst
    src_first_alpha = next((c for c in src if c.isalpha()), None)
    if not src_first_alpha:
        return dst
    if src_first_alpha.isupper():
        i = next((i for i, c in enumerate(dst) if c.isalpha()), None)
        if i is not None:
            dst = dst[:i] + dst[i].upper() + dst[i+1:]
    return dst

# ----------------- Typographie -----------------

def _generic_typo_cleanup(s: str) -> str:
    """Nettoyage minimal commun à toutes les langues (sûr) :
    - pas d'espace avant , . ! ? : ;
    - compresser espaces multiples
    - dé-quotage si un seul mot entre guillemets (sans toucher NBSP)
    """
    if not s:
        return s
    # retirer espace avant ponctuation simple
    s = re.sub(r"\s+([,\.!\?:;])", r"\1", s)
    # compresser espaces (en conservant NBSP)
    s = re.sub(r" {2,}", " ", s)
    # dé-quotage si un seul mot entre guillemets
    m = re.match(r'^\s*[\"“”«»]?([A-Za-zÀ-ÖØ-öø-ÿŒœ]+)[\"“”«»]?\s*([\.!\?…])?\s*$', s)
    if m and ' ' not in m.group(1):
        core = m.group(1)
        p = m.group(2) or ''
        s = core + p
    return s.strip()

def _fr_typo_nbsp_once(s: str) -> str:
    """Insécable avant !?:; (pas en tête ni juste après un ouvrant) + élisions + dé-quotage 1 mot."""
    if not s:
        return s
    # jamais d'espace avant , .
    s = s.replace(" ,", ",").replace(" .", ".")
    out = []
    OPENERS = set("([{\"'«‹")
    for ch in s:
        if ch in ('!', '?', ';', ':'):
            if out:
                if out[-1] == ' ':
                    if len(out) >= 2 and out[-2] in OPENERS:
                        pass
                    else:
                        out[-1] = '\u00A0'
                elif out[-1] != '\u00A0' and out[-1] not in OPENERS:
                    out.append('\u00A0')
            out.append(ch)
        else:
            out.append(ch)
    s = "".join(out)
    s = re.sub(r" {2,}", " ", s)  # compresser espaces (hors NBSP)
    # recoller élisions (d', l', s', qu', j', t', m', n', c')
    s = re.sub(r"(?i)\b([cdjlmnstq])'\s+([aàâeéèêiîïoôuùûhœ])", r"\1'\2", s)
    # dé-quotage si un seul mot entouré de guillemets
    m = re.match(r'^\s*[\"“”«»]?([A-Za-zÀ-ÖØ-öø-ÿŒœ]+)[\"“”«»]?\s*([\.!\?…])?\s*$', s)
    if m and ' ' not in m.group(1):
        core = m.group(1)
        p = m.group(2) or ''
        s = core + p
    return s.strip()

# ----------------- NLLB / CT2 glue -----------------

def _lang_token(code: str) -> str:
    token_id = _TOKENIZER.convert_tokens_to_ids(code)
    if token_id is None or token_id == _TOKENIZER.unk_token_id:
        raise ValueError("Unsupported language: " + code)
    return code


def _encode_with_src_tag(text: str, src_code: str) -> List[str]:
    _TOKENIZER.src_lang = src_code
    ids = _TOKENIZER(text, add_special_tokens=True).input_ids
    toks = _TOKENIZER.convert_ids_to_tokens(ids)
    head = _lang_token(src_code)
    if not toks or toks[0] != head:
        toks = [head] + toks
    return toks

def _translate_ct2(text: str, nbest: int = 1, src_code: str = "eng_Latn", tgt_code: str = "fra_Latn") -> List[str]:
    """Retourne une liste d'hypothèses (1 si nbest=1)."""
    if _TOKENIZER is None or _TRANSLATOR is None:
        return [text]
    tgt_prefix = [_lang_token(tgt_code)]
    src_tokens = _encode_with_src_tag(text, src_code)
    res = _TRANSLATOR.translate_batch(
        [src_tokens],
        beam_size=max(4, nbest),
        num_hypotheses=nbest,
        target_prefix=[tgt_prefix],
        no_repeat_ngram_size=3,
        length_penalty=1.0,
        max_decoding_length=128,
        return_scores=False,
    )
    outs: List[str] = []
    for h in res[0].hypotheses[:nbest]:
        if h and h[0] == tgt_prefix[0]:
            h = h[1:]
        outs.append(_TOKENIZER.convert_tokens_to_string(h).strip())
    return outs

# ----------------- Pipeline principal -----------------

def run(text: str, src_code: str, tgt_code: str) -> str:
    """
    Traite `text` selon la stratégie, avec source/target imposés.
    - Coussin multi-langues pour 1–2 mots (sauf 2 mots + '?')
    - n-best + sélection courte/sans guillemets
    - Suppression du préfixe “coussin” DANS LA LANGUE CIBLE (toutes langues)
    - Post-édition FR spécifique sinon générique
    """
    if not text or src_code == tgt_code:
        return text

    parts = _safe_split(text)
    out_segments: List[str] = []

    for seg in parts:
        kind = _short_kind(seg)  # 0/1/2
        ends = _end_punct(seg)

        # Préparation (coussin pour ultra-courts, si supporté par la langue source)
        if kind in (1, 2):
            prepared = _cushion_segment(seg, kind, src_code)
            nbest = 6
        else:
            prepared = seg
            nbest = 1

        # Traduction
        outs = _translate_ct2(prepared, nbest=nbest, src_code=src_code, tgt_code=tgt_code)

        # Sélection + post-édition pour ultra-courts (nettoyage de préfixe cible, toutes langues)
        if kind in (1, 2):
            outs = [_strip_any_prefix(o, tgt_code) for o in outs]
            best = _best_short_variant(outs)
            if not best or not any(ch.isalpha() for ch in best):
                # fallback: retraduire sans coussin, n-best
                outs2 = _translate_ct2(_strip_end(seg), nbest=6, src_code=src_code, tgt_code=tgt_code)
                outs2 = [_strip_any_prefix(o, tgt_code) for o in outs2]
                best = _best_short_variant(outs2) or seg
        else:
            best = outs[0] if outs else seg

        # Transfert de casse (ex. "Hello" -> "Bonjour" / "Ciao")
        best = _transfer_capitalization(seg, best)

        # Remettre la ponctuation d’origine (éviter les doublons)
        best = _strip_trailing_punct_any(best)
        if ends:
            best = best + ends

        # Typo par segment
        if _is_french(tgt_code):
            best = _fr_typo_nbsp_once(best)
        else:
            best = _generic_typo_cleanup(best)

        out_segments.append(best)

    # Reconstruction finale
    out = " ".join(s for s in out_segments if s)
    if _is_french(tgt_code):
        out = _fr_typo_nbsp_once(out)
    else:
        out = _generic_typo_cleanup(out)
    return out
