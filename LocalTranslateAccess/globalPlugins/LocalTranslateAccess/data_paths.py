"""Addon-owned storage and one-time relocation of older profile data."""
from .i18n import _
from pathlib import Path
import shutil

LEGACY={'LocalTranslate-models':'models','LocalTranslate-cache':'cache','localTranslate.ini':'localTranslate.ini'}

def legacy_paths(profile):
    profile=Path(profile).resolve()
    for name in LEGACY:
        source=profile/name
        if source.exists():
            # Never follow a link or junction out of the NVDA configuration folder.
            if source.resolve().parent!=profile or source.is_symlink():
                raise ValueError(_('Chemin de données LocalTranslate inattendu : ')+str(source))
            yield source

def prepare(addon_root,profile):
    addon_root=Path(addon_root).resolve()
    data=addon_root/'data'
    if not data.resolve().is_relative_to(addon_root):
        raise ValueError(_('Le dossier de données doit rester dans l’addon.'))
    data.mkdir(parents=True,exist_ok=True)
    for source in legacy_paths(profile):
        destination=data/LEGACY[source.name]
        if destination.exists():
            # Keep newer data authoritative, but retain the older copy inside the
            # addon too so uninstall removes both without overwriting either.
            destination=data/('legacy-'+LEGACY[source.name])
            index=1
            while destination.exists():
                destination=data/(f'legacy-{index}-'+LEGACY[source.name]);index+=1
        if not destination.resolve().is_relative_to(data.resolve()):
            raise ValueError(_('Destination de données invalide.'))
        shutil.move(str(source),str(destination))
    return data
