"""Window lifetime monitoring, independent of foreground/focus and minimization."""
import ctypes
from ctypes import wintypes
import os
import threading


def normalize(path):
    return os.path.normcase(os.path.normpath(path)) if path else None


class WindowsSnapshot:
    def __init__(self):
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self.user.EnumWindows.argtypes = [self.callback_type, wintypes.LPARAM]
        self.user.EnumWindows.restype = wintypes.BOOL
        self.user.EnumChildWindows.argtypes = [wintypes.HWND, self.callback_type, wintypes.LPARAM]
        self.user.EnumChildWindows.restype = wintypes.BOOL
        self.user.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user.IsWindowVisible.restype = wintypes.BOOL
        self.user.IsIconic.argtypes = [wintypes.HWND]
        self.user.IsIconic.restype = wintypes.BOOL
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL

    def process_path(self, pid):
        handle = self.kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if self.kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return normalize(buffer.value)
            return None
        finally:
            self.kernel.CloseHandle(handle)

    def __call__(self):
        pids = set()
        def collect(hwnd, unused):
            pid = wintypes.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                pids.add(pid.value)
            return True
        child_callback = self.callback_type(collect)
        def root(hwnd, unused):
            # A minimized window is still open; foreground and occlusion do not
            # enter this decision. Children cover UWP/hosted application windows.
            if self.user.IsWindowVisible(hwnd) or self.user.IsIconic(hwnd):
                collect(hwnd, unused)
                self.user.EnumChildWindows(hwnd, child_callback, 0)
            return True
        callback = self.callback_type(root)
        if not self.user.EnumWindows(callback, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return {pid: self.process_path(pid) for pid in pids}


class AppMonitor:
    def __init__(self, schedule, changed, snapshot=None, interval=1.0, missing_limit=3, report_error=None):
        self.schedule, self.changed = schedule, changed
        self.snapshot = snapshot
        self.interval, self.missing_limit = interval, missing_limit
        self.report_error = report_error or (lambda: None)
        self.targets = {}
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.version = 0
        self.warned = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def track(self, key, pid, path=None):
        with self.lock:
            target = self.targets.setdefault(key, dict(pids=set(), paths=set(), active=True, missing=0))
            if pid:
                target['pids'].add(int(pid))
            if path:
                target['paths'].add(normalize(path))
            target['active'], target['missing'] = True, 0
            self.version += 1

    def untrack(self, key):
        with self.lock:
            self.targets.pop(key, None)
            self.version += 1

    def has_live_applications(self):
        with self.lock:
            return any(t['active'] for t in self.targets.values())

    def close(self):
        self.stop_event.set()
        with self.lock:
            self.version += 1

    def _notify(self, version):
        if not self.stop_event.is_set() and version == self.version:
            self.changed()

    def poll(self):
        try:
            if self.snapshot is None:
                self.snapshot = WindowsSnapshot()
            windows = self.snapshot()
        except Exception:
            # Enumeration errors, including a desktop switch, are not closure.
            if not self.warned:
                self.warned = True
                self.report_error()
            return
        changed = False
        with self.lock:
            for target in self.targets.values():
                for pid in target['pids']:
                    if windows.get(pid):
                        if not target['paths']:
                            target['paths'].add(windows[pid])
                paths = target['paths']
                live = any(path in paths for path in windows.values() if path)
                live = live or any(pid in windows and (not paths or windows[pid] is None)
                                   for pid in target['pids'])
                # No usable identity: preserve activation rather than guess.
                if not paths and not target['pids']:
                    continue
                target['missing'] = 0 if live else target['missing'] + 1
                active = live or target['missing'] < self.missing_limit
                if active != target['active']:
                    target['active'] = active
                    changed = True
            version = self.version
        if changed:
            self.schedule(self._notify, version)

    def _run(self):
        while not self.stop_event.wait(self.interval):
            with self.lock:
                watched = bool(self.targets)
            if watched:
                self.poll()
