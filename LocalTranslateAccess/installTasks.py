"""Remove remnants from editions that stored data outside the addon."""
from pathlib import Path
import shutil
import os
import globalVars
from logHandler import log

def onInstall():
    # Preserve addon-owned data on an upgrade. Hard links avoid copying large
    # weights while NVDA runs; both copies remain inside NVDA's addon folders.
    pending=Path(__file__).resolve().parent
    installed=pending.parent/'LocalTranslateAccess'
    if installed.resolve()==pending:return
    source=installed/'data';destination=pending/'data'
    if not source.is_dir():return
    if not destination.resolve().is_relative_to(pending):
        raise ValueError('Destination de données LocalTranslate inattendue.')
    if not source.resolve().is_relative_to(installed.resolve()):
        raise ValueError('Chemin de données LocalTranslate inattendu.')
    for file in source.rglob('*'):
        if not file.resolve().is_relative_to(source.resolve()) or file.is_symlink():
            raise ValueError('Lien inattendu dans les données LocalTranslate.')
        if not file.is_file():continue
        target=destination/file.relative_to(source)
        if not target.resolve().is_relative_to(destination.resolve()):
            raise ValueError('Destination de fichier LocalTranslate inattendue.')
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists():
            try:os.link(file,target)
            except OSError:shutil.copy2(file,target)

def onUninstall():
    profile=Path(globalVars.appArgs.configPath).resolve()
    for name in ('LocalTranslate-models','LocalTranslate-cache','localTranslate.ini'):
        target=profile/name
        if not target.exists():continue
        if target.is_symlink() or target.resolve().parent!=profile:
            log.error('LocalTranslate: refusing unexpected legacy data path %s',target)
            continue
        try:
            if target.is_dir():shutil.rmtree(target)
            else:target.unlink()
        except OSError:
            log.exception('LocalTranslate: legacy data cleanup failed')
