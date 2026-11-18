# -*- coding: utf-8 -*-
"""Offline translation using NVDA Python, with persistent application preferences."""

import os, sys, time, threading, atexit, locale, ctypes, configparser
import globalPluginHandler, ui, speech, api, config, gui, wx, languageHandler, tones, addonHandler, gettext, globalVars

# === i18n ==============================================================
# On force notre domaine de traduction pour la langue NVDA courante.
# IMPORTANT : les msgid sont en FR -> si NVDA est fr_FR/fr, on NE met PAS 'en' en fallback.
DOMAIN = "localtranslate"
_ADDON_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DATA_ROOT = os.path.join(_ADDON_ROOT, "data")
_LOCALE_DIR = os.path.join(_ADDON_ROOT, "locale")

addonHandler.initTranslation()  # laisse NVDA initialiser ses catalogues

def _install_catalog_for_current_language():
    """Use the same catalogue and regional fallbacks as the settings dialogs."""
    from . import i18n
    i18n.set_language()
    globals()["_"] = i18n._
    globals()["ngettext"] = i18n.ngettext

_install_catalog_for_current_language()

# === Noms localisés pour les langues/variantes =========================
# Les msgid sont en FR et seront traduits par gettext selon la langue de l’addon.
LANG_DISPLAY = {
    "af": _("Afrikaans"),
    "ast": _("Asturien"),
    "az": _("Azéri"),
    "ba": _("Bachkir"),
    "be": _("Biélorusse"),
    "bo": _("Tibétain"),
    "br": _("Breton"),
    "ceb": _("Cebuano"),
    "cy": _("Gallois"),
    "eo": _("Espéranto"),
    "et": _("Estonien"),
    "eu": _("Basque"),
    "ff": _("Peul"),
    "fy": _("Frison occidental"),
    "gd": _("Gaélique écossais"),
    "gu": _("Gujarati"),
    "ha": _("Haoussa"),
    "hbs": _("Serbo-croate"),
    "ht": _("Créole haïtien"),
    "hy": _("Arménien"),
    "ig": _("Igbo"),
    "ilo": _("Ilocano"),
    "jv": _("Javanais"),
    "kk": _("Kazakh"),
    "km": _("Khmer"),
    "ku": _("Kurde"),
    "lb": _("Luxembourgeois"),
    "lg": _("Luganda"),
    "ln": _("Lingala"),
    "lo": _("Lao"),
    "lv": _("Letton"),
    "mg": _("Malgache"),
    "ml": _("Malayalam"),
    "mr": _("Marathi"),
    "ms": _("Malais"),
    "no": _("Norvégien"),
    "ns": _("Sotho du Nord"),
    "oc": _("Occitan"),
    "or": _("Odia (oriya)"),
    "pb": _("Portugais (Brésil)"),
    "ps": _("Pachto"),
    "sd": _("Sindhi"),
    "si": _("Singhalais"),
    "ss": _("Swati"),
    "su": _("Soundanais"),
    "sw": _("Swahili"),
    "te": _("Télougou"),
    "tl": _("Tagalog"),
    "tn": _("Tswana"),
    "ug": _("Ouïghour"),
    "uz": _("Ouzbek"),
    "wo": _("Wolof"),
    "xh": _("Xhosa"),
    "yi": _("Yiddish"),
    "yo": _("Yoruba"),
    "yue": _("Cantonais"),
    "zh-hans": _("Chinois (simplifié)"),
    "zh-hant": _("Chinois (traditionnel)"),
    "zu": _("Zoulou"),
    "am": _("Amharique"),
    "an": _("Aragonais"),
    "ar": _("Arabe"),
    "bg": _("Bulgare"),
    "bn": _("Bengali"),
    "bs": _("Bosnien"),
    "ca": _("Catalan"),
    "ckb": _("Kurde central"),
    "cs": _("Tchèque"),
    "da": _("Danois"),
    "de": _("Allemand"),
    "el": _("Grec"),
    "en": _("Anglais"),
    "es": _("Espagnol"),
    "fa": _("Persan"),
    "fi": _("Finnois"),
    "fr": _("Français"),
    "ga": _("Irlandais"),
    "gl": _("Galicien"),
    "he": _("Hébreu"),
    "hi": _("Hindi"),
    "hr": _("Croate"),
    "hu": _("Hongrois"),
    "id": _("Indonésien"),
    "is": _("Islandais"),
    "it": _("Italien"),
    "ja": _("Japonais"),
    "ka": _("Géorgien"),
    "kmr": _("Kurde (kurmanji)"),
    "kn": _("Kannada"),
    "ko": _("Coréen"),
    "ky": _("Kirghize"),
    "lt": _("Lituanien"),
    "mk": _("Macédonien"),
    "mn": _("Mongol"),
    "my": _("Birman"),
    "nb": _("Norvégien (Bokmål)"),
    "ne": _("Népalais"),
    "nl": _("Néerlandais"),
    "nn": _("Norvégien (Nynorsk)"),
    "pa": _("Pendjabi"),
    "pl": _("Polonais"),
    "pt": _("Portugais"),
    "ro": _("Roumain"),
    "ru": _("Russe"),
    "sk": _("Slovaque"),
    "sl": _("Slovène"),
    "so": _("Somali"),
    "sq": _("Albanais"),
    "sr": _("Serbe"),
    "sv": _("Suédois"),
    "ta": _("Tamoul"),
    "th": _("Thaï"),
    "tr": _("Turc"),
    "uk": _("Ukrainien"),
    "ur": _("Ourdou"),
    "vi": _("Vietnamien"),
    "zh": _("Chinois"),
}
REGION_DISPLAY = {
    "ZA": _("Afrique du Sud"),
    "CH": _("Suisse"),
    "CO": _("Colombie"),
    "BR": _("Brésil"),
    "PT": _("Portugal"),
    "CN": _("Chine"),
    "HK": _("Hong Kong"),
    "TW": _("Taïwan"),
    "NO": _("Norvège"),
}

def _split_lang_region(code: str):
    """'pt_BR' -> ('pt','BR'), 'zh-CN' -> ('zh','CN'), 'fr' -> ('fr', None)."""
    s = str(code).strip().replace("-", "_")
    if "_" in s:
        base, region = s.split("_", 1)
        return base.lower(), region.upper()
    return s.lower(), None

def _label_for_ui(code: str) -> str:
    """
    Libellé affiché dans la boîte de dialogue (localisé).
    - 'auto' -> 'Auto (détection)'
    - 'fr'   -> 'Français'
    - 'pt_BR'-> 'Portugais (Brésil)'
    """
    if code == "auto":
        return _("Auto (détection)")
    direct = LANG_DISPLAY.get(str(code).strip().lower().replace("_", "-"))
    if direct is not None:return direct
    base, region = _split_lang_region(code)
    base_label = LANG_DISPLAY.get(base, code)
    if region:
        region_label = REGION_DISPLAY.get(region, region)
        try:
            return _("{lang} ({region})").format(lang=base_label, region=region_label)
        except Exception:
            return f"{base_label} ({region_label})"
    return base_label

def _index_for_stored_code(choices, stored_code, default=0):
    """
    Trouve l'index à sélectionner dans 'choices' (liste de codes de dossiers, ex: 'de_CH').
    Si la valeur stockée est une base (ex: 'de'), on cherche la première entrée dont la base
    correspond (ex: 'de_CH' ou 'de'). Sinon, on tente l’égalité stricte.
    """
    try:
        if stored_code in choices:
            return choices.index(stored_code)
        base = _map_lang_to_iso(stored_code)  # ex: 'de_CH' -> 'de'
        for i, c in enumerate(choices):
            if _map_lang_to_iso(c) == base:
                return i
    except Exception:
        pass
    return default

# === Listes dynamiques : alignées sur NVDA (nvda.mo requis) ===========
def _nvda_core_locale_dir():
    """
    Tente de localiser le dossier 'locale' du NVDA en cours (installé ou portable).
    Renvoie le chemin si trouvé, sinon None.
    """
    # 1) à partir du processus NVDA courant (si dispo)
    try:
        import psutil  # protégé si non présent
        for p in psutil.process_iter(attrs=["name","exe"]):
            if (p.info.get("name") or "").lower().startswith("nvda"):
                exe_dir = os.path.dirname(p.info.get("exe") or "")
                if exe_dir:
                    cand = os.path.join(exe_dir, "locale")
                    if os.path.isdir(cand):
                        return cand
    except Exception:
        pass

    # 2) à partir de sys.executable (NVDA embarque Python)
    try:
        exe_dir = os.path.dirname(sys.executable)
        cand = os.path.join(exe_dir, "locale")
        if os.path.isdir(cand):
            return cand
        cand = os.path.abspath(os.path.join(exe_dir, "..", "locale"))
        if os.path.isdir(cand):
            return cand
    except Exception:
        pass

    # 3) chemins classiques d'installation
    for var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if not base:
            continue
        cands = [
            os.path.join(base, "NVDA", "locale"),
            os.path.join(base, "Programs", "NVDA", "locale"),  # LOCALAPPDATA\Programs\NVDA\locale
        ]
        for p in cands:
            try:
                if os.path.isdir(p):
                    return p
            except Exception:
                pass
    return None

def _list_ui_locale_codes():
    """
    Liste EXACTEMENT les langues d’interface NVDA (celles visibles dans ses paramètres) :
    - on parcourt NVDA\\locale\\*
    - on ne garde que les dossiers qui ont LC_MESSAGES\\nvda.mo
    - ⚠️ on ajoute toujours 'en' (langue de base NVDA, souvent sans .mo)
    - tri case-insensible, sans doublons.
    """
    codes = []
    root = _nvda_core_locale_dir()
    try:
        if root and os.path.isdir(root):
            for name in os.listdir(root):
                path = os.path.join(root, name)
                if not os.path.isdir(path) or name.startswith("."):
                    continue
                lcm = os.path.join(path, "LC_MESSAGES")
                if os.path.isdir(lcm) and os.path.exists(os.path.join(lcm, "nvda.mo")):
                    codes.append(name)

        # Toujours inclure l’anglais
        if "en" not in codes:
            codes.append("en")

        if not codes:
            codes = ["en", "fr"]  # repli

        codes = sorted(set(codes), key=lambda s: s.lower())
    except Exception:
        codes = ["en", "fr"]
    return codes

# === configuration persistée (profil NVDA) ============================
# On mémorise les préférences dans %APPDATA%\\nvda\\localTranslate.ini
INI_NAME = "localTranslate.ini"

def _map_lang_to_iso(lang: str) -> str:
    """Normalise 'fr_FR'/'fr-FR' -> 'fr' ; regroupe zh*, nb/nn, etc."""
    if not lang:
        return "fr"
    s = lang.strip().lower().replace("_", "-")
    # Grouper quelques familles usuelles
    if s.startswith("zh"):
        return "zh-hant" if any(x in s for x in ("hant", "tw", "hk")) else "zh"
    if s.startswith("nb"):
        return "nb"
    if s.startswith("nn"):
        return "nn"
    return s.split("-", 1)[0]

def _get_os_ui_lang_win32() -> str:
    """Locale Windows si NVDA ne fournit rien (cas de repli)."""
    try:
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        winloc = locale.windows_locale.get(langid)
        if winloc:
            return winloc
    except Exception:
        pass
    for f in (locale.getlocale,):
        try:
            loc = f()[0]
            if loc:
                return loc
        except Exception:
            pass
    return "fr_FR"

def _ini_path():
    return os.path.join(_DATA_ROOT, INI_NAME)

def _save_ini(cfg):
    try:
        with open(_ini_path(), "w", encoding="utf-8") as f:
            cfg.write(f)
    except Exception:
        pass

_config_cache = None

def _load_ini():
    """Charge/initialise la section [localTranslate]."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    cfg = configparser.ConfigParser()
    p = _ini_path()
    if os.path.exists(p):
        try:
            cfg.read(p, encoding="utf-8")
        except Exception:
            pass
    changed = False
    if "localTranslate" not in cfg:
        cfg["localTranslate"] = {}
        changed = True

    # Cible par défaut = langue NVDA (ou OS) réduite en code court (fr/en/...).
    if not cfg["localTranslate"].get("targetLang"):
        try:
            nvda_ui = languageHandler.getLanguage()
        except Exception:
            nvda_ui = None
        cfg["localTranslate"]["targetLang"] = _map_lang_to_iso(nvda_ui or _get_os_ui_lang_win32()) or "fr"
        changed = True

    # Source par défaut = auto (détection LID).
    if not cfg["localTranslate"].get("sourceLang"):
        cfg["localTranslate"]["sourceLang"] = "auto"
        changed = True

    # (GPU supprimé)

    if "copyToClipboard" not in cfg["localTranslate"]:
        cfg["localTranslate"]["copyToClipboard"] = "false"
        changed = True

    if changed:
        _save_ini(cfg)
    _config_cache = cfg
    return cfg

_truthy = {"1","true","yes","on","y","t"}
_falsy  = {"0","false","no","off","n","f"}

def _as_bool(v: str, default: bool=False) -> bool:
    if v is None: return default
    s = str(v).strip().lower()
    if s in _truthy: return True
    if s in _falsy:  return False
    return default

def get_source_lang(app=None):
    app = app or _get_app_key()
    source = _app_languages.settings(app)['source']
    return (_load_ini()["localTranslate"].get("sourceLang", "auto")
            if source == 'inherit' else source)
def set_source_lang(c): cfg=_load_ini(); cfg["localTranslate"]["sourceLang"]=c; _save_ini(cfg)
def _ensure_default_target():
    from .target_defaults import default_target
    cfg=_store.config('global')
    if not cfg.get('configured') and not cfg.get('target_initialized'):
        _store.initialize_target(default_target(_store.languages()))

def _target_for(context):
    _ensure_default_target()
    return _store.config(context)['active']

def get_target_lang():return _target_for(_get_app_key())
def set_target_lang(c): cfg=_load_ini(); cfg["localTranslate"]["targetLang"]=c; _save_ini(cfg)
def get_copy_to_cb():   return _as_bool(_load_ini()["localTranslate"].get("copyToClipboard","false"), False)
def set_copy_to_cb(b):  cfg=_load_ini(); cfg["localTranslate"]["copyToClipboard"]="true" if b else "false"; _save_ini(cfg)

# === utilitaires divers ===============================================
_beep_stop_evt = threading.Event()
_beep_thread = None

def _beep_worker():
    while not _beep_stop_evt.is_set():
        try:
            tones.beep(880, 60)
        except Exception:
            pass
        _beep_stop_evt.wait(1.0)

def _start_beeper():
    """Petit feedback sonore pendant un démarrage potentiellement long."""
    global _beep_thread
    if _beep_thread and _beep_thread.is_alive():
        return
    _beep_stop_evt.clear()
    t = threading.Thread(target=_beep_worker, daemon=True)
    _beep_thread = t
    try:
        t.start()
    except Exception:
        pass

def _stop_beeper():
    try:
        _beep_stop_evt.set()
        if _beep_thread and _beep_thread.is_alive():
            _beep_thread.join(timeout=0.2)
    except Exception:
        pass

def _get_app_key():
    """Identifie l’app courante (clé courte pour le cache et les toggles)."""
    try:
        fo = api.getFocusObject()
        if fo and getattr(fo, "appModule", None):
            exe_path = getattr(fo.appModule, "appPath", None) or getattr(fo.appModule, "processPath", None)
            if exe_path:
                return os.path.splitext(os.path.basename(exe_path))[0].lower()
            appName = getattr(fo.appModule, "appName", None)
            if appName:
                return str(appName).lower()
        return "global"
    except Exception:
        return "global"

def _get_app_label():
    """Stable application name, independent of window/document titles."""
    key = _get_app_key()
    return key if key != "global" else _("application")

def _get_window_label():
    """Capture the foreground title before opening an NVDA dialog."""
    try:
        title = str(getattr(api.getForegroundObject(), "name", "") or "").strip()
        if title:
            return title
    except Exception:
        pass
    return _get_app_label()

def _should_skip_text(s: str) -> bool:
    """Ignore les chaînes vides/balises/punct-only (gain perf)."""
    if not s or not s.strip():
        return True
    t = s.strip()
    if len(t) <= 1:
        return True
    if all(ch in " .,:;!?()[]{}«»'\"—-·•\u200b" for ch in t):
        return True
    return False

# === hook du moteur de parole =========================================
_translationCache = {}   # { appKey: { (srcCode,tgtCode,srcText): dstText } }
_reentryFlag = False
_manager_hooked = False
_orig_manager_speak = None

_global_enabled = False
_per_app_enabled = set()

_last_clip_copy_ts = 0.0
def _copy_to_clip_safely(text: str):
    """Optionnel: copie la traduction dans le presse-papiers (antirebond)."""
    if not get_copy_to_cb():
        return
    if not text or not text.strip():
        return
    try:
        global _last_clip_copy_ts
        now = time.time()
        if now - _last_clip_copy_ts < 0.15:
            return
        api.copyToClip(text)
        _last_clip_copy_ts = now
    except Exception:
        pass

def _is_translation_enabled_now() -> bool:
    if _configuration_open():
        return False
    return _global_enabled or (_get_app_key() in _per_app_enabled)

def _cache_get(app, src, src_code, tgt_code):
    return _translationCache.get(app, {}).get((src_code, tgt_code, src))

def _cache_set(app, src, dst, src_code, tgt_code):
    _translationCache.setdefault(app, {})[(src_code, tgt_code, src)] = dst

def _translate_and_cache(srcText: str) -> str:
    """Pipeline : skip -> cache -> worker -> copie CB éventuelle."""
    if _should_skip_text(srcText):
        return srcText
    src_code = get_source_lang().strip().lower()
    tgt_code = get_target_lang().strip().lower()
    if src_code == tgt_code and src_code != "auto":
        return srcText
    app = _get_app_key()
    cached = _cache_get(app, srcText, src_code, tgt_code)
    if cached:
        _copy_to_clip_safely(cached)
        return cached
    out = _request_translate(srcText, src_code, tgt_code) or srcText
    _cache_set(app, srcText, out, src_code, tgt_code)
    if out != srcText:
        _copy_to_clip_safely(out)
    return out

# === Non-blocking NVDA 2026 adapter ====================================
from .backends import Engine
from .model_store import Store
from .telemetry import Telemetry
from .data_paths import prepare
from logHandler import log

try:
    prepare(_ADDON_ROOT,globalVars.appArgs.configPath)
except Exception:
    log.exception('LocalTranslate: moving legacy data into the addon failed')
    wx.CallAfter(ui.message,_("LocalTranslate : impossible de déplacer les anciennes données. Consultez le journal NVDA."))
_store = Store(os.path.join(_DATA_ROOT, "models"))
_telemetry = Telemetry(_store.root)
_plugin_instance = None

def NativeEngine():
    return Engine(_store, _telemetry, _get_app_key,
                  lambda message: wx.CallAfter(_announce, "LocalTranslate : " + message))
from .dispatch import SpeechDispatcher
from .persistent_cache import PersistentCache
from .app_lifecycle import AppMonitor
from .app_languages import AppLanguages
from speech.commands import LangChangeCommand
SUPPORTED_LANGUAGES = {code:code for code in _store.languages()}
import queueHandler
from speech.extensions import pre_speechCanceled, post_speechPaused
from logHandler import log

_app_languages = AppLanguages(
    os.path.join(_DATA_ROOT, "cache", "application-languages.json"),
    lambda: log.exception("LocalTranslate: application language preferences unavailable"))
_last_application = "global"
_speech_context = threading.local()
_orig_high_speak = None
_high_speech_module = None
_exported_high_speak = None
_engine = NativeEngine()
_dispatcher = None
_app_monitor = None

def _lifetime_error():
    log.exception("LocalTranslate: window monitoring unavailable; retaining model")

def _track_current_app(key):
    if _app_monitor is None:
        return
    try:
        mod = api.getFocusObject().appModule
        pid = getattr(mod, "processID", None)
        path = None
        for attribute in ("processExecutablePath", "appPath", "processPath"):
            try:
                path = getattr(mod, attribute, None)
            except Exception:
                continue
            if path:
                break
        _app_monitor.track(key, pid, path)
    except Exception:
        log.exception("LocalTranslate: cannot identify application lifetime")
        _app_monitor.track(key, None)

def _sync_model_lifetime():
    needed = _global_enabled or (_per_app_enabled and
        (_app_monitor is None or _app_monitor.has_live_applications()))
    if needed:
        if not _manager_hooked:
            _install_manager_hook()
            threading.Thread(target=_bootstrap, daemon=True).start()
    elif _manager_hooked:
        _remove_manager_hook()
        _stop_worker()


def _cache_directory():
    return os.path.join(_DATA_ROOT, "cache")

def _cache_error():
    log.exception("LocalTranslate: persistent translation cache unavailable; using memory cache")


def _request_translate(text, src, tgt, timeout=None, app=None):
    if src == tgt or _should_skip_text(text):
        return text
    resolution = getattr(_speech_context, 'resolution', None)
    _speech_context.resolution=None
    timing = resolution['timing'] if resolution and resolution['app']==app and resolution['source']==src else {}
    # Any installed engine able to translate this language is used, the configured one first.
    family = _packs.family_for(src, tgt, preferred=_store.config(app or _get_app_key())['family'])
    if family is None:
        _continuous_language_missing(text, app, src, tgt)
        raise ValueError('language not installed')
    if _setting('replaceUnderscores'):
        text = ondemand.without_underscores(text)
    return _engine.translate(text, src, tgt, app=app, family=family, **timing)

_continuous_offered = set()

def _continuous_language_missing(text, app, source, target):
    """Called from the translation thread when what NVDA says is in a language that is not installed."""
    report = _source_reports.get(app) or (source, 'manual', None)
    origin, confidence = (report[1], report[2]) if report[0] == source else ('manual', None)
    if not ondemand.worth_offering(text, origin, confidence, source in _continuous_offered):
        return
    # Asked once per language until NVDA restarts, whatever the answer.
    _continuous_offered.add(source)
    wx.CallAfter(_offer_language_install, None, app, source, target)

def _warmup_if_auto():
    engine = _engine
    if _manager_hooked:
        try:
            engine.warmup()
        except Exception:
            log.exception("LocalTranslate: native engine unavailable")

def _stop_worker():
    global _engine
    old = _engine
    _engine = NativeEngine()
    released=threading.Event()
    _engine.load_barrier=released
    def close_old():
        try:old.close()
        finally:released.set()
    threading.Thread(target=close_old, daemon=True).start()

def _resident_gpu_index(entries):
    engine=_engine
    if getattr(engine,'device','cpu')!='cuda':return None
    for entry in entries:
        backend=engine.backends.get(entry['id'])
        if backend is None:return None
        if getattr(backend,'translator',None) is None and not getattr(backend,'ready',False):return None
    return engine.device_index

def _schedule(callback, *args):
    queueHandler.queueFunction(queueHandler.eventQueue, callback, *args)

def _metrics(**values):
    # No source text in the NVDA log; this measures arrival through delivery,
    # not the acoustic onset of the synthesizer.
    log.debug("LocalTranslate: %.1f ms to speech manager; %d cache hits", values['total_ms'], values['cache_hits'])
    _telemetry.record('delivery', milliseconds=values['total_ms'], cache_hits=values['cache_hits'])

def _deliver_translation(sequence, args, kwargs, app, src, tgt):
    if (not _manager_hooked or not _is_translation_enabled_now()
            or _get_app_key() != app or get_source_lang(app) != src or get_target_lang() != tgt):
        return
    _orig_manager_speak(sequence, *args, **kwargs)
    if get_copy_to_cb():
        _copy_to_clip_safely(" ".join(x for x in sequence if isinstance(x, str)))

def _cancel_pending():
    if _dispatcher:
        _dispatcher.cancel()

def _pause_pending(switch):
    if switch:
        _cancel_pending()

def _wrap_manager_speak(seq, *args, **kwargs):
    if not _is_translation_enabled_now() or _reentryFlag or _dispatcher is None:
        return _orig_manager_speak(seq, *args, **kwargs)
    if not isinstance(seq, (list, tuple)):
        return _orig_manager_speak(seq, *args, **kwargs)
    if not any(isinstance(x, str) and x.strip() for x in seq):
        return _orig_manager_speak(seq, *args, **kwargs)
    app = _get_app_key()
    _remember_application(app)
    context = getattr(_speech_context, 'languages', None)
    hints = context if context is not None and len(context) == sum(isinstance(x, str) for x in seq) else None
    _dispatcher.submit(list(seq), args, kwargs, app, get_source_lang(app), get_target_lang(), hints)

def _remember_application(app):
    global _last_application
    if app not in ('nvda', 'global'):
        _last_application = app


def _settings_application():
    app = _get_app_key()
    _remember_application(app)
    return _last_application if app in ('nvda', 'global') else app


def _wrap_high_speak(speechSequence, *args, **kwargs):
    # Read explicit document tags BEFORE speech.speak adds the default voice language.
    previous = getattr(_speech_context, 'languages', None)
    languages, declared = [], None
    for item in speechSequence:
        if isinstance(item, LangChangeCommand):
            declared = item.lang
        elif isinstance(item, str) and item:
            languages.append(declared)
    _speech_context.languages = languages
    try:
        return _orig_high_speak(speechSequence, *args, **kwargs)
    finally:
        _speech_context.languages = previous


def _install_language_hook():
    global _orig_high_speak, _high_speech_module, _exported_high_speak
    import importlib
    _high_speech_module = importlib.import_module('speech.speech')
    _orig_high_speak = _high_speech_module.speak
    _exported_high_speak = speech.speak
    _high_speech_module.speak = _wrap_high_speak
    if speech.speak is _orig_high_speak:
        speech.speak = _wrap_high_speak


def _remove_language_hook():
    if _high_speech_module is not None and _high_speech_module.speak is _wrap_high_speak:
        _high_speech_module.speak = _orig_high_speak
    if getattr(speech, 'speak', None) is _wrap_high_speak:
        speech.speak = _exported_high_speak


def _resolve_source(app, source, document, text):
    timing={'source_mode':'auto' if source=='auto' else 'manual','detection_ms':0.0}
    _speech_context.resolution=None
    def detect(value):
        start=time.perf_counter()
        try:return _engine.detect(value)
        finally:timing['detection_ms']=(time.perf_counter()-start)*1000
    def report(code,system,confidence):
        timing['source_system']=system
        _source_report(app,code,system,confidence)
    # The language of the text itself comes first: an application configured for English, or a
    # page declaring English, may well show a message written in another language.
    # NVDA's own words ("blank", "check box", "not checked"...) are in NVDA's language, whatever
    # is being read: their language is known, there is nothing to guess.
    own=_nvda_own_language(text)
    if own:
        report(own,'manual',None)
        _speech_context.resolution={'app':app,'source':own,'timing':timing}
        return own
    # A favourite may impose the language of the texts: then nothing is detected.
    forced=_forced_source()
    if forced:
        report(forced,'manual',None)
        _speech_context.resolution={'app':app,'source':forced,'timing':timing}
        return forced
    # What is configured for the application or declared by the page: only a fallback.
    try:
        fallback=_app_languages.resolve(app, source, document, text, detect,report=report)
    except Exception:
        fallback=None
    try:
        start=time.perf_counter()
        candidates=[(_normalize_language(code),chance) for code,chance in _engine.candidates(text)]
        timing['detection_ms']+=(time.perf_counter()-start)*1000
    except Exception:
        candidates=[]
    letters=sum(1 for character in text if character.isalpha())
    resolved,sure=ondemand.decide_language(candidates,letters,fallback,_recent_language.get(app))
    if resolved is None:
        raise ValueError('language of the text unknown')
    if ondemand.gives_context(resolved,sure,letters):
        # The lines that follow in the same message keep this language when they are unsure.
        _recent_language.note(app,resolved)
    if resolved!=fallback:
        timing['source_mode']='auto'
        report(resolved,'fasttext',dict(candidates).get(resolved) if sure else 0.0)
    _speech_context.resolution={'app':app,'source':resolved,'timing':timing}
    return resolved

_source_reports = {}
_nvda_own_messages = None

def _nvda_own_language(text):
    """NVDA's language when the text is one of NVDA's own interface messages, else None."""
    global _nvda_own_messages
    if _nvda_own_messages is None:
        from . import nvda_messages
        try:
            language=languageHandler.getLanguage()
            directory=os.path.join(getattr(globalVars,'appDir',None) or os.path.dirname(sys.executable),'locale')
            if not os.path.isdir(directory):
                directory=_nvda_core_locale_dir()
            messages=nvda_messages.load(directory,language)
            code=_normalize_language(language)
        except Exception:
            log.exception('LocalTranslateAccess: NVDA messages unavailable')
            messages,code=(),None
        _nvda_own_messages=(messages,code)
    messages,code=_nvda_own_messages
    return code if code and text in messages else None

_activation_tokens = {}

def _source_report(app,code,system,confidence):
    value=(code,system,confidence)
    previous=_source_reports.get(app)
    _source_reports[app]=value
    if previous is None or previous[:2]!=value[:2]:
        log.debug('LocalTranslate: application=%s source=%s origine=%s confiance=%s',app,code,system,confidence)

def _activation_feedback(app,label=None):
    # Announce after closing the modal window and processing restored-focus events.
    token=object();_activation_tokens[app]=token
    def announce():
        if _activation_tokens.get(app) is not token:return
        if _configuration_open() or not (_global_enabled if app=='global' else app in _per_app_enabled):return
        message=_('Traduction globale activée.') if app=='global' else _('Traduction activée pour {app}.').format(app=label or app)
        _announce(message,priority=True)
    wx.CallLater(300,lambda:_schedule(announce))


def _install_manager_hook():
    global _orig_manager_speak, _manager_hooked, _dispatcher
    if _manager_hooked:
        return True
    _orig_manager_speak = speech._manager.speak
    _dispatcher = SpeechDispatcher(_request_translate, _schedule, _deliver_translation, _metrics,
                                   PersistentCache(_cache_directory, _cache_error, _store.namespace,shared=True),
                                   _resolve_source, LangChangeCommand,
                                   cache_application=lambda app:'global' if _global_enabled and app not in _per_app_enabled else app)
    _install_language_hook()
    pre_speechCanceled.register(_cancel_pending)
    post_speechPaused.register(_pause_pending)
    speech._manager.speak = _wrap_manager_speak
    _manager_hooked = True
    return True

def _remove_manager_hook():
    global _manager_hooked, _dispatcher
    if not _manager_hooked:
        return
    _manager_hooked = False
    _remove_language_hook()
    pre_speechCanceled.unregister(_cancel_pending)
    post_speechPaused.unregister(_pause_pending)
    if _dispatcher:
        _dispatcher.close()
        _dispatcher = None
    if speech._manager.speak is _wrap_manager_speak:
        speech._manager.speak = _orig_manager_speak

def _announce(message,priority=False):
    global _reentryFlag
    _reentryFlag = True
    try:
        if priority:ui.message(message,speechPriority=speech.Spri.NOW)
        else:ui.message(message)
    finally:
        _reentryFlag = False

def _bootstrap():
    engine = _engine
    if not _manager_hooked:
        return
    try:
        engine.warmup()
    except Exception:
        if _manager_hooked and engine is _engine:
            log.exception("LocalTranslate: native model loading failed")
            _schedule(_announce, _("LocalTranslate : échec du chargement. Le texte original sera annoncé."))

# === UI (dialogue de réglages) ========================================

def _show_application_settings_dialog(parent=None):
    """Boîte de dialogue : source, cible, options — listes dynamiques triées par libellé localisé."""
    app = _settings_application()
    app_settings = _app_languages.settings(app)
    parent = parent or gui.mainFrame

    # Récupère toutes les locales disponibles depuis NVDA (celles avec nvda.mo)
    ui_codes = _list_ui_locale_codes()
    ui_codes = [c for c in ui_codes if _map_lang_to_iso(c) in SUPPORTED_LANGUAGES]
    if not ui_codes:
        ui_codes = ["en", "fr"]

    # Clé de tri par libellé (locale-aware) ; fallback si locale non configurée
    def _sort_key_label(code: str) -> str:
        try:
            return locale.strxfrm(_label_for_ui(code))
        except Exception:
            return _label_for_ui(code).lower()

    # Tri alphabétique par nom affiché (dans la langue de l'addon)
    sorted_codes = sorted(ui_codes, key=_sort_key_label)

    # Construit les listes (Auto tout en haut pour la source)
    source_choices = ["auto"] + sorted_codes[:]   # 'auto' + toutes les locales triées
    target_choices = _store.config(app).get("targets", ["fr"])              # toutes les locales triées

    dlg = wx.Dialog(parent, title=_("LocalTranslate — essai OPUS-MT anglais vers français"))
    sizer = wx.BoxSizer(wx.VERTICAL)

    scope = wx.Choice(dlg, choices=[_("Application : {app}").format(app=app), _("Réglages généraux")])
    scope.SetSelection(0)
    sizer.Add(wx.StaticText(dlg, label=_("Configuration à modifier :")), 0, wx.ALL, 10)
    sizer.Add(scope, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)

    # Source
    st_src = wx.StaticText(dlg, label=_("Langue source :"))
    src_labels = [_label_for_ui(c) for c in source_choices]
    if app_settings['detected'] and app_settings['remember']:
        src_labels[0] = _("Auto — {lang} mémorisé pour cette application").format(
            lang=_label_for_ui(app_settings['detected']))
    choice_src = wx.Choice(dlg, choices=src_labels)

    cur_src = (get_source_lang(app) or "auto").strip().lower()
    try:
        if cur_src == "auto":
            idx_src = 0
        else:
            idx_src = _index_for_stored_code(source_choices, cur_src, default=0)
    except Exception:
        idx_src = 0
    choice_src.SetSelection(idx_src)

    row_src = wx.BoxSizer(wx.HORIZONTAL)
    row_src.Add(st_src, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
    row_src.Add(choice_src, 1, wx.EXPAND)
    sizer.Add(row_src, 0, wx.ALL | wx.EXPAND, 10)

    inherit = wx.CheckBox(dlg, label=_("Utiliser la langue source des réglages généraux"))
    inherit.SetValue(app_settings['source'] == 'inherit')
    sizer.Add(inherit, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
    remember = wx.CheckBox(dlg, label=_("Mémoriser la langue détectée pour cette application"))
    remember.SetValue(app_settings['remember'])
    sizer.Add(remember, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
    detected = app_settings['detected']
    status_label = (_("Langue mémorisée pour {app} : {lang}").format(
        app=app, lang=_label_for_ui(detected)) if detected else
        _("Aucune langue mémorisée pour {app}").format(app=app))
    if detected and not app_settings['remember']:
        status_label += _(" — utilisation désactivée ; détection à chaque texte")
    status = wx.StaticText(dlg, label=status_label)
    sizer.Add(status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
    forget = wx.CheckBox(dlg, label=_("Oublier la langue mémorisée (nouvelle détection en mode Auto)"))
    sizer.Add(forget, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

    def update_scope(event=None):
        is_app = scope.GetSelection() == 0
        inherit.Enable(is_app)
        remember.Enable(is_app)
        forget.Enable(is_app)
        source = get_source_lang(app) if is_app else _load_ini()["localTranslate"].get("sourceLang", "auto")
        if event is not None and event.GetEventObject() is scope:
            choice_src.SetSelection(_index_for_stored_code(source_choices, source))
        choice_src.Enable(not (is_app and inherit.GetValue()))

    scope.Bind(wx.EVT_CHOICE, update_scope)
    inherit.Bind(wx.EVT_CHECKBOX, update_scope)
    update_scope()

    # Cible
    st_tgt = wx.StaticText(dlg, label=_("Langue cible (toutes les applications) :"))
    tgt_labels = [_label_for_ui(c) for c in target_choices]
    choice_tgt = wx.Choice(dlg, choices=tgt_labels)

    cur_tgt = (get_target_lang() or "fr").strip().lower()
    try:
        default_tgt = target_choices.index("fr") if "fr" in target_choices else 0
    except Exception:
        default_tgt = 0
    try:
        idx_tgt = _index_for_stored_code(target_choices, cur_tgt, default=default_tgt)
    except Exception:
        idx_tgt = default_tgt
    choice_tgt.SetSelection(idx_tgt)

    row_tgt = wx.BoxSizer(wx.HORIZONTAL)
    row_tgt.Add(st_tgt, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
    row_tgt.Add(choice_tgt, 1, wx.EXPAND)
    sizer.Add(row_tgt, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)

    # Options (sans GPU)
    copy_cb = wx.CheckBox(dlg, label=_("Copier la traduction dans le presse-papiers"))
    copy_cb.SetValue(get_copy_to_cb())
    sizer.Add(copy_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

    btns = dlg.CreateSeparatedButtonSizer(wx.OK | wx.CANCEL)
    sizer.Add(btns, 0, wx.ALL | wx.EXPAND, 10)

    dlg.SetSizerAndFit(sizer)
    ret = dlg.ShowModal()
    if ret == wx.ID_OK:
        old_src, old_tgt = get_source_lang(app), get_target_lang()

        # Codes affichés (ex. 'de_CH'); pour la config on stocke la base ISO
        sel_src_display = source_choices[choice_src.GetSelection()]
        sel_tgt_display = target_choices[choice_tgt.GetSelection()]

        new_src = "auto" if sel_src_display == "auto" else _map_lang_to_iso(sel_src_display)
        new_tgt = _map_lang_to_iso(sel_tgt_display)

        if scope.GetSelection() == 0:
            _app_languages.configure(app, 'inherit' if inherit.GetValue() else new_src,
                                     remember.GetValue(), forget.GetValue())
        else:
            set_source_lang(new_src)
        # Also cancel when only remembering/reset preferences changed.
        _cancel_pending()
        set_target_lang(new_tgt)
        set_copy_to_cb(bool(copy_cb.GetValue()))

        if (new_src != old_src) or (new_tgt != old_tgt):
            _translationCache.clear()
            _cancel_pending()

        try:
            ui.message(_("Source : {src} — Cible : {tgt}").format(
                src=_label_for_ui(sel_src_display),
                tgt=_label_for_ui(sel_tgt_display)
            ))
            if new_src == "auto":
                ui.message(_("Note : détection automatique activée."))
                threading.Thread(target=_warmup_if_auto, daemon=True).start()
        except Exception:
            pass
    dlg.Destroy()

def _install_preferences_menu_item(self):
    """Ajoute l’entrée 'LocalTranslate…' au menu Préférences NVDA."""
    try:
        menu = gui.mainFrame.sysTrayIcon.preferencesMenu
        self._ltMenuItem = menu.Append(wx.ID_ANY, _("LocalTranslate…"))
        gui.mainFrame.sysTrayIcon.Bind(
            wx.EVT_MENU,
            lambda evt: _show_settings_dialog(gui.mainFrame),
            self._ltMenuItem
        )
    except Exception:
        self._ltMenuItem = None

def _remove_preferences_menu_item(self):
    """Retire l’entrée de menu si présente (nettoyage)."""
    try:
        if getattr(self, "_ltMenuItem", None):
            gui.mainFrame.sysTrayIcon.preferencesMenu.Remove(self._ltMenuItem)
            self._ltMenuItem = None
    except Exception:
        self._ltMenuItem = None

def _manager_stop():
    # Called by a background download/removal thread.
    _remove_manager_hook()
    _engine.close()
    _stop_worker()

def _manager_changed():
    global SUPPORTED_LANGUAGES
    SUPPORTED_LANGUAGES = {code:code for code in _store.languages()}
    if _dispatcher is not None:
        _dispatcher.cancel()
    _remove_manager_hook()
    _stop_worker()
    _sync_model_lifetime()

def _manager_cache_changed():
    _translationCache.clear()
    if _dispatcher is not None:_dispatcher.invalidate_cache()

def _manager_removed():
    global _global_enabled
    _global_enabled = False
    for key in tuple(_per_app_enabled):
        if _app_monitor is not None:
            _app_monitor.untrack(key)
    _per_app_enabled.clear()
    if _plugin_instance is not None:
        _plugin_instance.translationGlobalEnabled = False
        _plugin_instance._perAppEnabled.clear()
    if _dispatcher is not None:
        _dispatcher.cancel()
    _sync_model_lifetime()

_manager_open = False
# The configuration panel shown in NVDA's Settings dialog, while it exists.
_settings_manager = None

def _configuration_open():
    """True while the first-use wizard or the configuration panel is open: translation pauses meanwhile."""
    return _manager_open or bool(_settings_manager)

def _manager_hooks(app, activate=False, source_sample=None, source_hint=None):
    """What the configuration windows need from the add-on: reading and saving the preferences."""
    def preferences(context):
        return (_app_languages.settings(context) if context != 'global' else
                {'source':_load_ini()['localTranslate'].get('sourceLang','auto'),'remember':False})
    def source(context):
        if activate and context==app and context!='global':
            prefs=_app_languages.settings(context)
            if not prefs['confirmed'] and prefs['source']=='inherit':return 'auto'
        return (_load_ini()['localTranslate'].get('sourceLang','auto') if context == 'global' else get_source_lang(context))
    def configure(context, code, remember, inherit, forget=False):
        if context == 'global':
            set_source_lang(code)
        else:
            _app_languages.configure(context, 'inherit' if inherit else code, remember, forget=forget)
        _app_languages.confirm(context,'inherit' if inherit and context!='global' else code)
    def save_sources(changes):
        for context,code in changes.items():
            if context=='global':set_source_lang(code)
            else:_app_languages.configure(context,code,False if code=='auto' else _app_languages.settings(context)['remember'])
            _app_languages.confirm(context,code)
        _manager_changed()
    def save_languages(sources,targets,models,gpu_settings=None):
        for context,code in targets.items():_store.set_target(context,code)
        for context,family in models.items():
            _store.set_model(context,family,sources.get(context,source(context)),targets.get(context,_target_for(context)))
        for context,cfg in (gpu_settings or {}).items():_store.set_gpu(context,cfg['gpu'],cfg['cuda_index'])
        save_sources(sources)
    hooks={'label':_label_for_ui,'preferences':preferences,'source':source,
           'cache_directory':_cache_directory,'cache_changed':_manager_cache_changed,
           'source_sample':source_sample,'source_hint':source_hint,
           'target':_target_for,'save_languages':save_languages,'gpu_settings':True,
           'gpu_resident':_resident_gpu_index,
           'application_sources':_app_languages.application_sources,
           'application_label':lambda key:key,
           'save_sources':save_sources,
           'configure':configure,'stop':_manager_stop,'changed':_manager_changed,'removed':_manager_removed,
           'setup':lambda context:_show_model_manager(app=context,activate=True),
           'announce':_announce,
           'report_error':lambda:log.exception('LocalTranslate: model download or installation failed'),
           'copy_get':get_copy_to_cb,'copy_set':set_copy_to_cb,
           'add_languages':lambda parent:_show_add_languages(parent)}
    return hooks

def _show_model_manager(parent=None, app=None, activate=False, on_done=None):
    global _manager_open
    if not activate:
        # The configuration lives in NVDA's Settings dialog.
        _show_settings_dialog()
        return
    if _configuration_open():
        _announce(_("La fenêtre LocalTranslate est déjà ouverte."))
        return
    _manager_open = True
    if _dispatcher is not None:
        _dispatcher.cancel()
    from .setup_wizard import SetupWizard
    app = app or _settings_application()
    source_sample=None;source_hint=None
    if activate:
        from .source_confirmation import focus_sample
        try:source_sample=focus_sample(api.getFocusObject())
        except Exception:source_sample=('',None)
        report=_source_reports.get(app) or _source_reports.get(_get_app_key())
        if report and report[1]!='manual':source_hint=report[0]
        prefs=_app_languages.settings(app)
        if source_hint is None and prefs['remember']:source_hint=prefs['detected']
    hooks = _manager_hooks(app, activate, source_sample, source_hint)
    def run():
        global _manager_open
        dlg = None
        accepted = False
        prepared = False
        try:
            gui.mainFrame.prePopup()
            prepared = True
            # A window is about to open: it must be announced, so the quiet period after the menu ends now.
            _quiet.close()
            dlg=SetupWizard(parent or gui.mainFrame,_store,_telemetry,app,hooks,activate)
            accepted = dlg.ShowModal() == wx.ID_OK
        except Exception:
            log.exception('LocalTranslate: model manager failed')
            _announce(_("Impossible d’ouvrir la fenêtre LocalTranslate. Consultez le journal NVDA."))
        finally:
            if dlg is not None:
                dlg.dead=True
                dlg.cancel.set()
                dlg.Destroy()
            if prepared:
                gui.mainFrame.postPopup()
            _manager_open = False
        if on_done is not None:
            on_done(accepted)
    # NVDA's core timer is non-reentrant: never enter a modal loop from a script.
    # Returning first lets the core process focus, keyboard and speech events.
    wx.CallAfter(run)

def _show_settings_dialog(parent=None):
    """Open NVDA's Settings dialog on this add-on's category."""
    wx.CallAfter(gui.mainFrame.popupSettingsDialog, gui.settingsDialogs.NVDASettingsDialog,
        LocalTranslateAccessSettingsPanel)

def _source_models_ready(app):
    if not _store.ready(app):return False
    source=(_load_ini()['localTranslate'].get('sourceLang','auto') if app=='global' else get_source_lang(app))
    if source=='auto':return True
    try:
        cfg=_store.config(app)
        return all(_store.installed(e) for e in _store.route(cfg['family'],source,cfg['active']))
    except ValueError:return False

# === Catégorie dans les Paramètres de NVDA ==============================
from .updater import Updater
CONFIG_SECTION = 'LocalTranslateAccess'

class LocalTranslateAccessSettingsPanel(gui.settingsDialogs.SettingsPanel):
    # The add-on's own name: shown as a category of NVDA's Settings dialog, never translated.
    title = 'LocalTranslateAccess'
    # Set by the global plugin; None on secure screens.
    updater = None

    def makeSettings(self, settingsSizer):
        from .simple_settings import SimpleSettings
        self.simple = SimpleSettings(self, _packs, dict(
            label=_label_for_ui, target=lambda: _target_for('global'), announce=_announce,
            add_languages=_show_add_languages, remove=_remove_language, advanced=_show_advanced))
        settingsSizer.Add(self.simple, 1, wx.EXPAND)
        from .favorites_ui import FavoritesEditor
        self.favorites = FavoritesEditor(self, _favorites(), list(_store.languages()), _label_for_ui, _announce)
        settingsSizer.Add(self.favorites, 0, wx.EXPAND)
        helper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
        self.copyCB = helper.addItem(wx.CheckBox(self, label=_('&Copier automatiquement chaque traduction à la demande dans le presse-papiers')))
        self.copyCB.SetValue(bool(_setting('copyTranslation')))
        self.underscoresCB = helper.addItem(wx.CheckBox(self, label=_('Remplacer les tirets &bas par des espaces avant de traduire')))
        self.underscoresCB.SetValue(bool(_setting('replaceUnderscores')))
        self.checkForUpdatesCB = helper.addItem(wx.CheckBox(self, label=_('Vérifier a&utomatiquement les mises à jour au démarrage de NVDA')))
        self.checkForUpdatesCB.SetValue(config.conf[CONFIG_SECTION]['checkForUpdates'])
        self.checkNowButton = helper.addItem(wx.Button(self, label=_('&Vérifier les mises à jour maintenant')))
        self.Bind(wx.EVT_BUTTON, lambda event: self.updater.check(manual=True), self.checkNowButton)
        self.checkNowButton.Enable(self.updater is not None)

    def onSave(self):
        config.conf[CONFIG_SECTION]['checkForUpdates'] = self.checkForUpdatesCB.GetValue()
        config.conf[CONFIG_SECTION]['copyTranslation'] = self.copyCB.GetValue()
        config.conf[CONFIG_SECTION]['replaceUnderscores'] = self.underscoresCB.GetValue()
        config.conf[CONFIG_SECTION]['favorites'] = ondemand.format_favorites(self.favorites.favorites)
        code = self.simple.chosen_target()
        if code and code != _target_for('global'):
            # A language chosen here is detected again in the texts, whatever a favourite imposed.
            config.conf[CONFIG_SECTION]['activeSource'] = 'auto'
            # May ask to download the language; if refused, the choice goes back to what it was.
            if not _change_target(code, self):
                self.simple.select_target(_target_for('global'))
            self.simple.reload()

# === Traduction à la demande (menu) ====================================
import textInfos
from . import ondemand
# The language surely recognised a moment ago in each application: context for unsure sentences.
_recent_language = ondemand.RecentLanguage()
from .app_languages import normalize as _normalize_language

_speech_memory = ondemand.SpeechMemory()
_ondemand = {'engine': None, 'swapped': False, 'last': '', 'busy': False, 'timer': None}
ONDEMAND_IDLE_MS = 120000   # the models used on request are unloaded after two idle minutes
ONDEMAND_SPEECH_DELAY = 0.5 # lets NVDA announce the focus coming back from the menu first

_quiet = ondemand.QuietWindow()
_history = ondemand.TranslationHistory()
_progress = ondemand.Progress()

def _setting(key):
    try:
        return config.conf[CONFIG_SECTION][key]
    except Exception:
        return None

def _forced_source():
    """The language imposed by a favourite for the texts to translate, or None when it is detected."""
    value = _setting('activeSource')
    return None if not value or value == 'auto' else value

def _favorites():
    return ondemand.parse_favorites(_setting('favorites'))

def _remember_speech(speechSequence=None, **kwargs):
    # The menu's own items and the focus coming back are not announcements worth translating.
    if not _reentryFlag and not _quiet.remembering_paused():
        _speech_memory.remember(speechSequence)

def _quiet_filter(speechSequence, *args, **kwargs):
    """Drop what NVDA wants to say while the quiet window is open, except this add-on's own messages."""
    if _reentryFlag or not _quiet.active():
        return speechSequence
    return []

def _selected_text():
    """The selection under the caret, read before the menu takes the focus."""
    try:
        obj = api.getFocusObject()
        interceptor = getattr(obj, 'treeInterceptor', None)
        if interceptor is not None and hasattr(interceptor, 'TextInfo') and not getattr(interceptor, 'passThrough', True):
            obj = interceptor
        else:
            obj = api.getCaretObject()
        info = obj.makeTextInfo(textInfos.POSITION_SELECTION)
        return '' if info.isCollapsed else (info.text or '')
    except Exception:
        return ''

def _ondemand_direction(app, detect=lambda: None):
    config = _store.config(app)
    forced = _forced_source()
    source = forced or get_source_lang(app).strip().lower()
    return ondemand.choose_languages(source, _target_for(app).strip().lower(),
        config.get('sources', []), _ondemand['swapped'], (lambda: forced) if forced else detect)

def _ondemand_release():
    engine, _ondemand['engine'], _ondemand['timer'] = _ondemand['engine'], None, None
    if engine is not None:
        threading.Thread(target=engine.close, daemon=True).start()

def _ondemand_say(message, started=None):
    """Speak from any thread, after the focus announcement that follows the menu."""
    wait = max(0.0, ONDEMAND_SPEECH_DELAY - (time.monotonic() - started)) if started else 0.0
    wx.CallAfter(wx.CallLater, int(wait * 1000) + 1, _announce, message)

def _ondemand_translate(text, app, started):
    def detect():
        try:
            candidates = [(_normalize_language(code), chance) for code, chance in _ondemand['engine'].candidates(text)]
            letters = sum(1 for character in text if character.isalpha())
            code, sure = ondemand.decide_language(candidates, letters, None, _recent_language.get(app))
            return code
        except Exception:
            return None
    _progress.begin(1)
    try:
        if _ondemand['engine'] is None:
            _ondemand['engine'] = NativeEngine()
        engine = _ondemand['engine']
        source, target = _ondemand_direction(app, detect)
        if source == target:
            _ondemand_say(_('Ce texte est déjà en {language}.').format(language=_label_for_ui(target)), started)
            return
        family = _ondemand_family(app, source, target)
        if family is None:
            # Nothing installed translates this pair: offer to install it, on the main thread.
            wx.CallAfter(_offer_language_install, text, app, source, target)
            return
        work = ondemand.without_underscores(text) if _setting('replaceUnderscores') else text
        pieces = ondemand.split_text(work)
        _progress.set_total(len(pieces))
        if len(pieces) > 1:
            _ondemand_say(_('Traduction en cours…'), started)
        result = []
        for piece in pieces:
            if _progress.stop.is_set():
                return   # "Traduction arrêtée." was already said by the stop command
            body = piece.rstrip()
            result.append((engine.translate(body, source, target, app=app, family=family) if body.strip() else body) + piece[len(body):])
            _progress.advance()
        if _progress.stop.is_set():
            return
        translation = ''.join(result)
        _ondemand['last'] = translation
        _history.add(text, translation)
        if _setting('copyTranslation'):
            wx.CallAfter(api.copyToClip, translation)
        if ondemand.needs_window(translation):
            wx.CallAfter(_show_translation_window, translation)
        else:
            _ondemand_say(translation, started)
    except Exception:
        log.exception('LocalTranslateAccess: translation on request failed')
        _ondemand_say(_('La traduction a échoué.'), started)
    finally:
        _progress.end()
        _ondemand['busy'] = False
        wx.CallAfter(_ondemand_idle)

from .language_packs import Packs
_packs = Packs(_store)

def _ondemand_family(app, source, target):
    """The installed engine able to translate this pair, the configured one first."""
    return _packs.family_for(source, target, preferred=_store.config(app)['family'])

def _language_name(code):
    """A language name inside a sentence: French does not capitalise it."""
    label = _label_for_ui(code)
    from . import i18n
    return label.lower() if i18n._language.split('_')[0] == 'fr' else label

def _language_tools(parent=None):
    """What the language windows need: where to speak, what to do on errors and after a change."""
    return dict(announce=_announce,
        report_error=lambda: log.exception('LocalTranslateAccess: language download failed'),
        changed=_languages_changed)

def _offer_language_install(text, app, source, target):
    """A text is in a language that is not installed: one plain question, then a direct download."""
    global _manager_open
    from . import language_ui
    # The question must be heard: end the quiet period that follows the menu.
    _quiet.close()
    if _configuration_open():
        if text:
            _announce(_("La fenêtre LocalTranslate est déjà ouverte."))
        else:
            # Not the moment to ask: the question may come back the next time this language is heard.
            _continuous_offered.discard(source)
        return
    plan = _packs.plan(source, target)
    if plan is None:
        gui.messageBox(_('Ce texte est en {language}. Cette langue ne peut pas être traduite sur cet ordinateur.').format(
            language=_language_name(source)), _('Langue non installée'), wx.OK | wx.ICON_INFORMATION)
        return
    # The language to install is the one that is not the user's own.
    missing = target if _packs.installed(source, _target_for(app)) else source
    message = _('Ce texte est en {language}, une langue qui n’est pas installée. Voulez-vous la télécharger ? ({size})') if missing == source \
        else _('La traduction vers cette langue ({language}) n’est pas installée. Voulez-vous la télécharger ? ({size})')
    message = message.format(language=_language_name(missing), size=language_ui.size_text(plan.size))
    if gui.messageBox(message, _('Langue non installée'), wx.YES_NO | wx.ICON_QUESTION) != wx.YES:
        return
    tools = _language_tools()
    _manager_open = True
    prepared = False
    error = 'failed'
    try:
        gui.mainFrame.prePopup()
        prepared = True
        # Translators: Title of the download progress window. {language} is the language being downloaded.
        error = language_ui.download(gui.mainFrame, _packs, [plan],
            _('Téléchargement : {language}').format(language=_label_for_ui(missing)), tools['announce'], tools['report_error'])
    except Exception:
        tools['report_error']()
    finally:
        if prepared:
            gui.mainFrame.postPopup()
        _manager_open = False
    _languages_changed()
    if error == 'cancelled':
        _announce(_('Téléchargement annulé.'))
    elif error is not None or _ondemand_family(app, source, target) is None:
        gui.messageBox(_('Le téléchargement a échoué. Vérifiez votre connexion Internet, puis réessayez.'),
            _('Langue non installée'), wx.OK | wx.ICON_ERROR)
    elif text:
        # The language is there now: do what was asked in the first place.
        _ondemand_start(text, app, '', delayed=True)
    else:
        # Asked during continuous translation: what NVDA says next in this language is translated.
        _announce(_('Langue installée : {language}.').format(language=_label_for_ui(missing)))

def _show_add_languages(parent=None):
    """The list of languages to tick. Returns True when at least one language was added."""
    from . import language_ui
    tools = _language_tools()
    # Opened on its own (from the menu), the window has to be brought to the front like any NVDA dialog.
    standalone = parent is None
    if standalone:
        gui.mainFrame.prePopup()
    dialog = None
    try:
        dialog = language_ui.AddLanguagesDialog(parent or gui.mainFrame, _packs, _target_for('global'), _label_for_ui,
            tools['announce'], tools['report_error'], tools['changed'])
        dialog.ShowModal()
        return dialog.added
    finally:
        if dialog is not None:
            dialog.Destroy()
        if standalone:
            gui.mainFrame.postPopup()

def _show_translation_window(translation):
    # The window must be announced: end the quiet period that follows the menu.
    _quiet.close()
    ui.browseableMessage(translation, _('Traduction'))

def _languages_changed():
    """Languages were added or removed: the configurations the add-on manages follow."""
    _resync_simple()
    _manager_changed()

def _resync_simple():
    target = _target_for('global')
    family, sources = _packs.continuous(target)
    if not sources:
        return
    for app in ['global'] + list(_store.settings['apps']):
        if _store.config(app).get('simple'):
            try:
                _store.configure_simple(app, family, sources, target)
            except ValueError:
                log.exception('LocalTranslateAccess: automatic configuration failed for %s', app)

def _simple_configure(app):
    """Prepare continuous translation by itself: every installed language, into the user's language.

    Returns False when no language is installed for that language yet.
    """
    target = _target_for('global')
    family, sources = _packs.continuous(target)
    if not sources:
        return False
    _store.configure_simple(app, family, sources, target)
    # The language of what NVDA says is detected; nothing has to be confirmed by the user.
    _manager_hooks(app)['configure'](app, 'auto', False, False)
    return True

def _remove_language(item):
    """Remove an installed language. Returns an error text, or None."""
    try:
        # Nothing may still be using the files that are about to be deleted.
        _ondemand_release()
        _manager_stop()
        _packs.remove(item)
    except Exception:
        log.exception('LocalTranslateAccess: removing a language failed')
        return _('La langue n’a pas pu être supprimée.')
    finally:
        _languages_changed()
    return None

def _change_target(code, parent=None):
    """The user chose another language to translate into. Returns True when it is in use."""
    from . import language_ui
    previous = _target_for('global')
    if code == previous:
        return True
    family, sources = _packs.continuous(code)
    if not sources:
        # Nothing installed translates into this language yet: English is the way in.
        plan = _packs.plan('en', code) if code != 'en' else _packs.plan(previous, code)
        if plan is None:
            gui.messageBox(_('La langue choisie ({language}) ne peut pas être utilisée sur cet ordinateur.').format(
                language=_language_name(code)), _('Langue non installée'), wx.OK | wx.ICON_INFORMATION)
            return False
        message = _('La langue choisie ({language}) n’est pas installée. Voulez-vous la télécharger ? ({size})').format(
            language=_language_name(code), size=language_ui.size_text(plan.size))
        if gui.messageBox(message, _('Langue non installée'), wx.YES_NO | wx.ICON_QUESTION) != wx.YES:
            return False
        tools = _language_tools()
        error = language_ui.download(parent or gui.mainFrame, _packs, [plan],
            _('Téléchargement : {language}').format(language=_label_for_ui(code)), tools['announce'], tools['report_error'])
        if error is not None or not _packs.continuous(code)[1]:
            if error != 'cancelled':
                gui.messageBox(_('Le téléchargement a échoué. Vérifiez votre connexion Internet, puis réessayez.'),
                    _('Langue non installée'), wx.OK | wx.ICON_ERROR)
            return False
    _store.set_target('global', code)
    for app in list(_store.settings['apps']):
        if _store.config(app).get('simple'):
            _store.set_target(app, code)
    if not _store.config('global').get('configured') or _store.config('global').get('simple'):
        _simple_configure('global')
    _languages_changed()
    return True

def _show_advanced(parent=None):
    """The settings most people never need: languages per application, cache, performance."""
    global _manager_open
    if _configuration_open():
        _announce(_("La fenêtre LocalTranslate est déjà ouverte."))
        return
    from .manager_dialog import ManagerDialog
    _manager_open = True
    if _dispatcher is not None:
        _dispatcher.cancel()
    dialog = None
    try:
        app = _settings_application()
        dialog = ManagerDialog(parent or gui.mainFrame, _store, _telemetry, app, _manager_hooks(app))
        dialog.ShowModal()
    except Exception:
        log.exception('LocalTranslateAccess: advanced options failed')
    finally:
        if dialog is not None:
            dialog.panel.shutdown()
            dialog.Destroy()
        _manager_open = False

def _offer_first_language(app, retry):
    """Translation was turned on but no language is installed for the user's language."""
    _quiet.close()
    message = _('Aucune langue n’est installée pour traduire vers votre langue ({language}). Voulez-vous en ajouter ?').format(
        language=_language_name(_target_for('global')))
    if gui.messageBox(message, _('Langue non installée'), wx.YES_NO | wx.ICON_QUESTION) != wx.YES:
        return
    if _show_add_languages() and _packs.continuous(_target_for('global'))[1]:
        retry()

def _ondemand_idle():
    if _ondemand['timer'] is not None:
        _ondemand['timer'].Stop()
    _ondemand['timer'] = wx.CallLater(ONDEMAND_IDLE_MS, _ondemand_release)

def _ondemand_start(text, app, empty_message, delayed=True):
    started = time.monotonic() if delayed else None
    if not text or not text.strip():
        _ondemand_say(empty_message, started)
        return
    if _ondemand['busy']:
        _ondemand_say(_('Une traduction est déjà en cours.'), started)
        return
    _ondemand['busy'] = True
    threading.Thread(target=_ondemand_translate, args=(text, app, started), daemon=True).start()

def _ondemand_languages_message(app, swapped_notice=False):
    # The language of each text is detected when it is translated; only the target is fixed.
    source, target = _ondemand_direction(app)
    source_label = _label_for_ui(source) if _forced_source() or _ondemand['swapped'] else _label_for_ui('auto')
    message = _('Langues inversées : de {source} vers {target}.') if swapped_notice else _('Traduction : de {source} vers {target}.')
    return message.format(source=source_label, target=_label_for_ui(target))

def _capture_app_context():
    """The focused application, read while it still has the focus."""
    key = _get_app_key()
    label = _get_window_label()
    try:
        mod = api.getFocusObject().appModule
        pid = getattr(mod, 'processID', None)
        path = getattr(mod, 'processExecutablePath', None) or getattr(mod, 'appPath', None)
    except Exception:
        pid, path = None, None
    return {'key': key, 'label': label, 'pid': pid, 'path': path}

MENU_TOGGLE_DELAY_MS = 400  # lets NVDA announce the focus coming back from the menu first

# Each action exists once, used both by the menu (delayed=True) and by its own command.
def _ondemand_clipboard_text():
    try:
        value = api.getClipData()
    except Exception:
        value = ''
    return value if isinstance(value, str) else ''

def _action_translate_selection(app, selection, delayed=True):
    _ondemand_start(selection, app, _('Aucune sélection.'), delayed)

def _action_translate_clipboard(app, delayed=True):
    _ondemand_start(_ondemand_clipboard_text(), app, _('Le presse-papiers ne contient pas de texte.'), delayed)

def _action_translate_last(app, spoken, delayed=True):
    _ondemand_start(spoken, app, _('Aucune annonce à traduire.'), delayed)

def _action_copy_last(delayed=True):
    started = time.monotonic() if delayed else None
    if _ondemand['last'] and api.copyToClip(_ondemand['last']):
        _ondemand_say(_('Traduction copiée dans le presse-papiers.'), started)
    else:
        _ondemand_say(_('Aucune traduction à copier.'), started)

def _action_swap(app, delayed=True):
    _ondemand['swapped'] = not _ondemand['swapped']
    _ondemand_say(_ondemand_languages_message(app, swapped_notice=True), time.monotonic() if delayed else None)

def _action_announce_languages(app, delayed=True):
    _ondemand_say(_ondemand_languages_message(app), time.monotonic() if delayed else None)

def _action_history(older, delayed=True):
    """Step through the history of translations, as AbsoluteTranslate did."""
    started = time.monotonic() if delayed else None
    item, moved = _history.step(older)
    if item is None:
        _ondemand_say(_('L’historique des traductions est vide.'), started)
    elif not moved:
        _ondemand_say(_('Aucune traduction plus ancienne.') if older else _('Aucune traduction plus récente.'), started)
    else:
        _ondemand_say(item[1], started)

def _show_history():
    from .history_ui import HistoryDialog
    _quiet.close()
    dialog = None
    gui.mainFrame.prePopup()
    try:
        dialog = HistoryDialog(gui.mainFrame, _history.items(), api.copyToClip, _announce)
        dialog.ShowModal()
    except Exception:
        log.exception('LocalTranslateAccess: history window failed')
    finally:
        if dialog is not None:
            dialog.Destroy()
        gui.mainFrame.postPopup()

def _use_favorite(index, started=None):
    """Translate into a favourite language from now on, everywhere."""
    favorites = _favorites()
    if index >= len(favorites):
        _ondemand_say(_('Aucune langue favorite numéro {number}.').format(number=index + 1), started)
        return
    source, target = favorites[index]
    if target != _target_for('global') and not _packs.continuous(target)[1]:
        _quiet.close()   # the language has to be downloaded first: that question must be heard
    if not _change_target(target):
        return
    config.conf[CONFIG_SECTION]['activeSource'] = source
    _ondemand['swapped'] = False
    if source == 'auto':
        message = _('Langue de traduction : {target}.').format(target=_label_for_ui(target))
    else:
        message = _('Langue de traduction : {target}, depuis : {source}.').format(target=_label_for_ui(target), source=_label_for_ui(source))
    _ondemand_say(message, started)

def _action_status(delayed=True):
    started = time.monotonic() if delayed else None
    status = _progress.status()
    if status is None:
        _ondemand_say(_('Aucune traduction en cours.'), started)
    elif status[1] > 1:
        _ondemand_say(_('Traduction en cours : {done} parties sur {total}, {percent} %.').format(
            done=status[0], total=status[1], percent=status[2]), started)
    else:
        _ondemand_say(_('Traduction en cours.'), started)

def _action_stop(delayed=True):
    started = time.monotonic() if delayed else None
    if _progress.status() is None:
        _ondemand_say(_('Aucune traduction en cours.'), started)
        return
    _progress.stop.set()
    _ondemand_say(_('Traduction arrêtée.'), started)

def _favorites_menu(chosen):
    """The favourite languages, chosen with the digits 1 to 0."""
    from .favorites_ui import favorite_label
    submenu = wx.Menu()
    favorites = _favorites()
    current = (_forced_source() or 'auto', _target_for('global'))
    for index, (source, target) in enumerate(favorites):
        item = submenu.AppendCheckItem(wx.ID_ANY, '&' + ondemand.favorite_key(index) + ' ' + favorite_label(source, target, _label_for_ui))
        item.Check((source, target) == current)
        submenu.Bind(wx.EVT_MENU, lambda event, index=index: chosen.append(lambda: _use_favorite(index, time.monotonic())), item)
    if not favorites:
        item = submenu.Append(wx.ID_ANY, _('Aucune langue favorite : en ajouter…'))
        submenu.Bind(wx.EVT_MENU, lambda event: chosen.append(lambda: (_quiet.close(), _show_settings_dialog(gui.mainFrame))), item)
    return submenu

def _translation_menu(plugin, context, selection, spoken):
    """Build and show the menu. Everything it needs was read before it took the focus."""
    app = context['key']
    toggles = [
        (_('Traduction pour cette &application'), app in plugin._perAppEnabled,
            lambda: wx.CallLater(MENU_TOGGLE_DELAY_MS, plugin.script_toggleTranslateForApp, None, context)),
        (_('Traduction &globale'), plugin.translationGlobalEnabled,
            lambda: wx.CallLater(MENU_TOGGLE_DELAY_MS, plugin.script_toggleTranslateGlobal, None)),
    ]
    status = _progress.status()
    actions = [] if status is None else [
        (_('A&rrêter la traduction ({percent} %)').format(percent=status[2]) if status[1] > 1 else _('A&rrêter la traduction'),
            _action_stop), None]
    actions += [
        (_('Traduire la &sélection'), lambda: _action_translate_selection(app, selection)),
        (_('Traduire le &presse-papiers'), lambda: _action_translate_clipboard(app)),
        (_('Traduire la &dernière annonce'), lambda: _action_translate_last(app, spoken)),
        (_('&Copier la dernière traduction'), _action_copy_last),
        (_('&Historique des traductions…'), lambda: (_quiet.close(), wx.CallAfter(_show_history))),
        (_('&Inverser les langues'), lambda: _action_swap(app)),
        (_('Annoncer les &langues'), lambda: _action_announce_languages(app)),
        'favorites',
        None,
        (_('Ajouter des lang&ues…'), lambda: (_quiet.close(), wx.CallAfter(_show_add_languages))),
        (_('Con&figuration de la traduction…'), lambda: (_quiet.close(), _show_settings_dialog(gui.mainFrame))),
    ]
    menu = wx.Menu()
    chosen = []
    for label, enabled, run in toggles:
        item = menu.AppendCheckItem(wx.ID_ANY, label)
        item.Check(bool(enabled))
        menu.Bind(wx.EVT_MENU, lambda event, run=run: chosen.append(run), item)
    menu.AppendSeparator()
    for action in actions:
        if action is None:
            menu.AppendSeparator()
            continue
        if action == 'favorites':
            menu.AppendSubMenu(_favorites_menu(chosen), _('Traduire &vers'))
            continue
        item = menu.Append(wx.ID_ANY, action[0])
        menu.Bind(wx.EVT_MENU, lambda event, run=action[1]: chosen.append(run), item)
    gui.mainFrame.prePopup()
    _quiet.hold()   # nothing said while the menu is open is remembered as an announcement
    try:
        gui.mainFrame.sysTrayIcon.PopupMenu(menu)
    finally:
        gui.mainFrame.postPopup()
        menu.Destroy()
        _quiet.release()
    if chosen:
        # An action was chosen: keep NVDA from re-reading the window, the application and
        # the selection when the focus comes back, so that only the result is heard.
        _quiet.open(ondemand.QUIET_AFTER_MENU)
        chosen[0]()

# === GlobalPlugin (gestes + cycle de vie) ==============================
class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    """Point d’entrée NVDA : gère les raccourcis, le hook de parole, et le worker."""
    __gestures = {
        # The menu holds everything; translating the selection is used so often that it has its own key
        # (Lee's choice, 2026-11-18). Every other command has no key by default.
        "kb:NVDA+control+shift+t": "translationMenu",
        "kb:NVDA+shift+t": "translateSelection",
    }
    # The add-on's own category in NVDA's Input Gestures dialog, never translated.
    scriptCategory = 'LocalTranslateAccess'

    def __init__(self):
        super().__init__()
        global _plugin_instance
        _plugin_instance = self
        self._terminated = False
        _stop_beeper()  # sécurité au chargement
        self.translationGlobalEnabled = False
        self._perAppEnabled = set()
        global _app_monitor
        _app_monitor = AppMonitor(_schedule, _sync_model_lifetime, report_error=_lifetime_error)
        # No entry in the Preferences menu: every setting is in NVDA's Settings dialog.
        # No update check on secure screens (logon, UAC), where nothing may be installed.
        config.conf.spec[CONFIG_SECTION] = {'checkForUpdates': 'boolean(default=true)',
            'copyTranslation': 'boolean(default=false)', 'replaceUnderscores': 'boolean(default=false)',
            'favorites': 'string_list(default=list())', 'activeSource': 'string(default=auto)'}
        self.updater = None
        if not globalVars.appArgs.secure:
            self.updater = Updater(addonHandler.getCodeAddon())
            if config.conf[CONFIG_SECTION]['checkForUpdates']:
                self.updater.scheduleStartupCheck()
        LocalTranslateAccessSettingsPanel.updater = self.updater
        # Remember what NVDA says, so that the last announcement can be translated on request.
        try:
            from speech.extensions import pre_speech, filter_speechSequence
            pre_speech.register(_remember_speech)
            filter_speechSequence.register(_quiet_filter)
        except Exception:
            log.exception('LocalTranslateAccess: last announcement unavailable for translation')
        gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(LocalTranslateAccessSettingsPanel)

    def event_gainFocus(self, obj, nextHandler):
        from .control_roles import correct_button_role
        correct_button_role(obj)
        try:
            _remember_application(str(obj.appModule.appName).lower())
        except Exception:
            pass
        nextHandler()

    def event_NVDAObject_init(self, obj):
        from .control_roles import correct_button_role
        correct_button_role(obj)

    def terminate(self):
        """Nettoyage lors du déchargement de l’addon."""
        global _app_monitor
        self._terminated = True
        if _app_monitor is not None:
            _app_monitor.close()
            _app_monitor = None
        _remove_preferences_menu_item(self)
        if self.updater is not None:
            self.updater.cancel()
        try:
            from speech.extensions import pre_speech, filter_speechSequence
            pre_speech.unregister(_remember_speech)
            filter_speechSequence.unregister(_quiet_filter)
        except Exception:
            pass
        if _ondemand['timer'] is not None:
            _ondemand['timer'].Stop()
        _ondemand_release()
        try:
            gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(LocalTranslateAccessSettingsPanel)
        except ValueError:
            pass
        _stop_beeper()
        _remove_manager_hook()
        _stop_worker()
        global _global_enabled, _per_app_enabled
        _global_enabled = False
        _per_app_enabled.clear()
        _telemetry.close()
        return super().terminate()

    def _ensure_started(self) -> bool:
        """Démarre worker + hook si pas encore initialisés."""
        app = getattr(self, '_starting_app', _get_app_key())
        if not _source_models_ready(app):
            return False
        installed = _install_manager_hook()
        threading.Thread(target=_bootstrap, daemon=True).start()
        return installed

    def _start_translation(self, app, on_started):
        if getattr(self, '_terminated', False):
            return
        if not _app_languages.settings(app)['confirmed'] or not _source_models_ready(app):
            # No setup window: the add-on prepares itself with the languages already installed.
            _stop_beeper()
            if not _simple_configure(app):
                _offer_first_language(app, lambda: self._start_translation(app, on_started))
                return
            if not _source_models_ready(app):
                _announce(_("Impossible d’activer la traduction."))
                return
        self._starting_app = app
        _start_beeper()
        started = self._ensure_started()
        _stop_beeper()
        if started:
            on_started()
        else:
            _announce(_("Impossible d’activer la traduction."))

    def _maybe_shutdown(self):
        _sync_model_lifetime()

    def script_toggleTranslateGlobal(self, gesture):
        global _global_enabled
        if self.translationGlobalEnabled:
            self.translationGlobalEnabled = False
            _global_enabled = False
            _activation_tokens.pop('global',None)
            _announce(_("Traduction globale désactivée"))
            self._maybe_shutdown()
            return
        def activated():
            global _global_enabled
            self.translationGlobalEnabled = True
            _global_enabled = True
            _activation_feedback('global')
        def start():
            if not getattr(self,'_terminated',False):self._start_translation('global',activated)
        start()

    # The commands below have no key by default: the menu gives access to all of them.
    # They exist so that a shortcut can be assigned to each one in NVDA's Input gestures dialog.
    def script_translateSelection(self, gesture):
        _action_translate_selection(_get_app_key(), _selected_text(), delayed=False)
    script_translateSelection.__doc__ = _('Traduit le texte sélectionné.')

    def script_translateClipboard(self, gesture):
        _action_translate_clipboard(_get_app_key(), delayed=False)
    script_translateClipboard.__doc__ = _('Traduit le texte du presse-papiers.')

    def script_translateLastAnnouncement(self, gesture):
        _action_translate_last(_get_app_key(), _speech_memory.latest(), delayed=False)
    script_translateLastAnnouncement.__doc__ = _('Traduit la dernière annonce de NVDA.')

    def script_copyLastTranslation(self, gesture):
        _action_copy_last(delayed=False)
    script_copyLastTranslation.__doc__ = _('Copie la dernière traduction dans le presse-papiers.')

    def script_swapLanguages(self, gesture):
        _action_swap(_get_app_key(), delayed=False)
    script_swapLanguages.__doc__ = _('Inverse la langue source et la langue cible des traductions à la demande.')

    def script_announceLanguages(self, gesture):
        _action_announce_languages(_get_app_key(), delayed=False)
    script_announceLanguages.__doc__ = _('Annonce la langue source et la langue cible actuelles.')

    def script_openConfiguration(self, gesture):
        _show_settings_dialog()
    script_openConfiguration.__doc__ = _('Ouvre la configuration de la traduction dans les Paramètres de NVDA.')

    def script_previousTranslation(self, gesture):
        _action_history(True, delayed=False)
    script_previousTranslation.__doc__ = _('Annonce la traduction précédente de l’historique.')

    def script_nextTranslation(self, gesture):
        _action_history(False, delayed=False)
    script_nextTranslation.__doc__ = _('Annonce la traduction suivante de l’historique.')

    def script_showTranslationHistory(self, gesture):
        wx.CallAfter(_show_history)
    script_showTranslationHistory.__doc__ = _('Ouvre l’historique des traductions.')

    def script_translationStatus(self, gesture):
        _action_status(delayed=False)
    script_translationStatus.__doc__ = _('Annonce l’état de la traduction en cours.')

    def script_stopTranslation(self, gesture):
        _action_stop(delayed=False)
    script_stopTranslation.__doc__ = _('Arrête la traduction en cours.')

    def script_translationMenu(self, gesture):
        # Read the context now: the menu takes the focus and NVDA announces its items.
        wx.CallAfter(_translation_menu, self, _capture_app_context(), _selected_text(), _speech_memory.latest())
    script_translationMenu.__doc__ = _('Ouvre le menu de traduction : traduction pour l’application ou globale, sélection, presse-papiers, dernière annonce, copie, langues et configuration.')

    def script_toggleTranslateForApp(self, gesture, context=None):
        global _per_app_enabled
        # From the menu, the application was identified before the menu took the focus.
        context = context or _capture_app_context()
        key = context['key']
        window_label = context['label']
        if key in self._perAppEnabled:
            self._perAppEnabled.remove(key)
            _per_app_enabled.discard(key)
            _activation_tokens.pop(key,None)
            if _app_monitor is not None:
                _app_monitor.untrack(key)
            _announce(_("Traduction désactivée pour {app}").format(app=window_label))
            self._maybe_shutdown()
            return
        self._starting_app = key
        pid, path = context['pid'], context['path']
        def activated():
            if _app_monitor is not None:
                _app_monitor.track(key, pid, path)
            self._perAppEnabled.add(key)
            _per_app_enabled.add(key)
            _activation_feedback(key,window_label)
        def start():
            if not getattr(self,'_terminated',False):self._start_translation(key,activated)
        start()

GlobalPlugin.script_toggleTranslateForApp.__doc__ = _('Active ou désactive la traduction pour l’application en cours.')
GlobalPlugin.script_toggleTranslateGlobal.__doc__ = _('Active ou désactive la traduction globale.')

def _make_favorite_script(index):
    def script(self, gesture):
        # A question may appear if the language is missing: never open it from inside a script.
        wx.CallAfter(_use_favorite, index, None)
    script.__name__ = 'script_useFavorite%d' % (index + 1)
    script.__doc__ = _('Traduit vers la langue favorite {number}.').format(number=index + 1)
    return script

for _index in range(ondemand.MAX_FAVORITES):
    _script = _make_favorite_script(_index)
    setattr(GlobalPlugin, _script.__name__, _script)
del _index, _script

# === sortie propre =====================================================
def _cleanup_on_exit():
    try:
        if _app_monitor is not None:
            _app_monitor.close()
        _stop_beeper()
        _remove_manager_hook()
        _stop_worker()
    except Exception:
        pass

atexit.register(_cleanup_on_exit)
