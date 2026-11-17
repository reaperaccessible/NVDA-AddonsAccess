"""Languages as the user sees them.

A language is installed or it is not. Which translation engine serves it, and
which files that takes, is decided here so that no window has to ask.
Nothing in this module touches NVDA or wx.
"""
import ctypes

GIB = 1024 ** 3
BERGAMOT = 'bergamot'
# Engines covering many languages in one download, smallest first.
MULTILINGUAL = ('small100', 'm2m100', 'nllb')
ORDER = (BERGAMOT,) + MULTILINGUAL
# family -> (needs the SSE4.1 instructions, available memory needed)
NEEDS = {BERGAMOT: (False, 0), 'small100': (True, 2 * GIB), 'm2m100': (True, 2 * GIB), 'nllb': (True, 3 * GIB)}


def machine():
    """(processor has SSE4.1, available memory in bytes or None when unknown)."""
    try:
        kernel = ctypes.WinDLL('kernel32')
        sse41 = bool(kernel.IsProcessorFeaturePresent(37))

        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in
                ('total', 'available', 'totalPage', 'availablePage', 'totalVirtual', 'availableVirtual', 'extended')]
        status = MemoryStatus(); status.length = ctypes.sizeof(MemoryStatus)
        available = status.available if kernel.GlobalMemoryStatusEx(ctypes.byref(status)) else None
        return sse41, available
    except (OSError, AttributeError):
        return True, None


class Plan:
    """What has to be on the disk for one language, and what is still to download."""

    def __init__(self, family, entries, missing, runtimes, size, unknown, both_ways):
        self.family = family
        self.entries = entries      # every model the language uses
        self.missing = missing      # those not installed yet
        self.runtimes = runtimes    # engine files not installed yet
        self.size = size            # bytes still to download, as far as known
        self.unknown = unknown      # number of files whose size is not known
        self.both_ways = both_ways  # translation works from and into the language

    @property
    def ready(self):
        return not self.missing and not self.runtimes


class Item:
    """One line of the list of installed languages."""

    def __init__(self, kind, code, entries, size, count=1):
        self.kind = kind          # 'language', or 'multilingual' for a pack of many languages
        self.code = code          # the language code, or the engine name for a multilingual pack
        self.entries = entries    # the files that removing this line deletes
        self.size = size
        self.count = count        # number of languages a multilingual pack brings

    @property
    def removable(self):
        return bool(self.entries)


def _owner(entry):
    """The language a fast-engine file belongs to: the one of its pair that is not English."""
    return entry['target'] if entry['source'] == 'en' else entry['source']


class Packs:
    def __init__(self, store, machine=machine):
        self.store = store
        self._machine = machine

    def usable(self, family):
        """Whether this computer can run the engine."""
        sse41, available = self._machine()
        needs_sse41, needs_memory = NEEDS.get(family, (True, 0))
        if needs_sse41 and not sse41:
            return False
        return available is None or available >= needs_memory

    def family_for(self, source, target, preferred=None):
        """The installed engine able to translate source into target right now, or None."""
        families = ([preferred] if preferred else []) + [family for family in ORDER if family != preferred]
        for family in families:
            if not self.store.runtime_ready(family):
                continue
            try:
                self.store.route(family, source, target, installed_only=True)
                return family
            except ValueError:
                continue
        return None

    def installed(self, code, target):
        return code == target or self.family_for(code, target) is not None

    def plan(self, code, target):
        """How to make `code` translatable into `target`, and back when possible. None when impossible."""
        if code == target:
            return None
        candidates = []
        for family in ORDER:
            if not self.usable(family):
                continue
            for pairs in ([(code, target), (target, code)], [(code, target)]):
                try:
                    entries, _routes = self.store.plan_pairs(family, pairs)
                except ValueError:
                    continue
                missing = [entry for entry in entries if not self.store.installed(entry)]
                runtimes = self.store.runtime_specs(family)
                size, unknown = self.store.download_size(
                    missing + [dict(spec, id='runtime-' + spec['url']) for spec in runtimes])
                candidates.append(Plan(family, entries, missing, runtimes, size, unknown, len(pairs) == 2))
                break
        if not candidates:
            return None
        # The fast engine made for one language pair, whenever it knows the language.
        for plan in candidates:
            if plan.family == BERGAMOT:
                return plan
        # Otherwise an engine already on the disk, else the smallest download.
        return min(candidates, key=lambda plan: (not plan.ready, plan.size, ORDER.index(plan.family)))

    def languages(self, target):
        """Every language that can be offered, as (code, plan). Languages nothing can translate are left out."""
        result = []
        for code in self.store.languages():
            plan = self.plan(code, target)
            if plan is not None:
                result.append((code, plan))
        return result

    def _installed_entries(self):
        return [entry for entry in self.store.installed_entries() if not entry.get('outdated')]

    def continuous(self, target):
        """(engine, languages) that continuous translation into `target` can use right now.

        Continuous translation runs one engine at a time: the fast one whenever it has
        a language to offer, otherwise a multilingual one. (None, []) when nothing is installed.
        """
        installed = self._installed_entries()
        for family in ORDER:
            if not self.store.runtime_ready(family):
                continue
            entries = [entry for entry in installed if entry['family'] == family]
            if not entries:
                continue
            sources = []
            for code in self.store.languages(family):
                if code == target:
                    continue
                try:
                    self.store._route(entries, code, target)
                    sources.append(code)
                except ValueError:
                    pass
            if sources:
                return family, sources
        return None, []

    def inventory(self, target):
        """What is installed, as the user sees it: languages, then multilingual packs."""
        installed = self._installed_entries()
        fast = [entry for entry in installed if entry['family'] == BERGAMOT]
        items = []
        if self.store.runtime_ready(BERGAMOT):
            for code in self.store.languages(BERGAMOT):
                if code == target:
                    continue
                try:
                    self.store._route(fast, code, target)
                except ValueError:
                    continue
                # A fast-engine file links English and one other language: it belongs to that
                # other language. English itself owns none, so it can never be removed.
                own = [entry for entry in fast if _owner(entry) == code]
                items.append(Item('language', code, own, self._size(own)))
        for family in MULTILINGUAL:
            for entry in installed:
                if entry['family'] == family:
                    items.append(Item('multilingual', family, [entry], self._size([entry]), len(entry.get('languages', []))))
        return items

    def _size(self, entries):
        total = 0
        for entry in entries:
            try:
                total += sum(path.stat().st_size for path in self.store.path(entry['id']).rglob('*') if path.is_file())
            except OSError:
                pass
        return total

    def remove(self, item):
        for entry in item.entries:
            self.store.remove(entry)

    def download(self, plans, cancel, progress):
        """Install what the plans still miss. `progress(done, total)` gets bytes over all the files."""
        specs = {}
        for plan in plans:
            for spec in self.store.download_specs(
                    plan.missing + [dict(spec, id='runtime-' + spec['url']) for spec in plan.runtimes]):
                specs[spec['url']] = spec
        tracker = _Tracker(sum(spec.get('bytes') or 0 for spec in specs.values()), progress)
        installed = set()
        for plan in plans:
            if cancel.is_set():
                break
            self.store.ensure_runtime(plan.family, cancel, tracker)
            for entry in plan.missing:
                if entry['id'] not in installed:
                    self.store.install(entry, cancel, tracker)
                    installed.add(entry['id'])


class _Tracker:
    """Turns the per-file progress of the model store into one figure for the whole download."""

    def __init__(self, total, report):
        self.total, self.report = total, report
        self.finished = 0   # bytes of the files already complete
        self.current = 0    # bytes of the file being downloaded

    def phase(self, stage, detail=''):
        if stage == 'download':
            # A new file starts: the previous one is complete.
            self.finished += self.current
            self.current = 0

    def __call__(self, current, total):
        self.current = current
        done = self.finished + current
        self.report(min(done, self.total) if self.total else done, self.total)
