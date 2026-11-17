"""Initial translation target from the active synthesizer, then NVDA UI."""
from .app_languages import normalize

def default_target(supported):
    supported=set(supported)
    try:
        import synthDriverHandler
        synth=synthDriverHandler.getSynth()
        candidates=[]
        if synth is not None:
            try:candidates.append(synth.language)
            except (AttributeError,KeyError,NotImplementedError):pass
            try:candidates.append(synth.availableVoices[synth.voice].language)
            except (AttributeError,KeyError,NotImplementedError):pass
        for value in candidates:
            code=normalize(value)
            if code in supported:return code
    except (ImportError,RuntimeError):pass
    try:
        import languageHandler
        code=normalize(languageHandler.getLanguage())
        if code in supported:return code
    except (ImportError,RuntimeError):pass
    return 'en' if 'en' in supported else sorted(supported)[0]
