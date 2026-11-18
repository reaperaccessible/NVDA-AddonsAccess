# KeyboardAccess: the keyboard layouts of Windows.
# Windows contains every keyboard layout of the world, even those not added in its Settings. A layout
# is loaded and activated here as Windows itself would, so that every key and every combination behaves
# exactly as with a layout added in the Settings; it is removed again when going back to one's own.
# This module does not touch NVDA's interface, so it can be tested alone.

import ctypes
import functools
import os
import winreg
from ctypes import wintypes

user32 = ctypes.WinDLL('user32', use_last_error=True)
shlwapi = ctypes.WinDLL('shlwapi')

LAYOUTS_KEY = r'SYSTEM\CurrentControlSet\Control\Keyboard Layouts'
KLF_NOTELLSHELL = 0x00000080
WM_INPUTLANGCHANGEREQUEST = 0x0050
HWND_BROADCAST = 0xFFFF

user32.LoadKeyboardLayoutW.argtypes = (wintypes.LPCWSTR, wintypes.UINT)
user32.LoadKeyboardLayoutW.restype = wintypes.HKL
user32.UnloadKeyboardLayout.argtypes = (wintypes.HKL,)
user32.UnloadKeyboardLayout.restype = wintypes.BOOL
user32.GetKeyboardLayoutList.argtypes = (ctypes.c_int, ctypes.POINTER(wintypes.HKL))
user32.GetKeyboardLayoutList.restype = ctypes.c_int
user32.GetKeyboardLayout.argtypes = (wintypes.DWORD,)
user32.GetKeyboardLayout.restype = wintypes.HKL
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.PostMessageW.restype = wintypes.BOOL
shlwapi.SHLoadIndirectString.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, ctypes.c_void_p)


def _value(key, name):
	try:
		return winreg.QueryValueEx(key, name)[0]
	except OSError:
		return None


def _indirect(text):
	"""Windows' own name of a layout, in Windows' language: "@%SystemRoot%\\system32\\input.dll,-5000"."""
	buffer = ctypes.create_unicode_buffer(512)
	return buffer.value if shlwapi.SHLoadIndirectString(text, buffer, 512, None) == 0 else None


@functools.lru_cache(maxsize=1)
def all_layouts():
	"""[(identifier, name, layout id)] of every layout of Windows, sorted by name.
	The identifier is Windows' own, as "00000409" for the US layout."""
	found = []
	system32 = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32')
	with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, LAYOUTS_KEY) as root:
		index = 0
		while True:
			try:
				identifier = winreg.EnumKey(root, index)
			except OSError:
				break
			index += 1
			with winreg.OpenKey(root, identifier) as key:
				layout_file = _value(key, 'Layout File')
				if not layout_file or not os.path.exists(os.path.join(system32, layout_file)):
					continue
				display = _value(key, 'Layout Display Name')
				name = (_indirect(display) if display else None) or _value(key, 'Layout Text') or identifier
				found.append((identifier.upper(), name, (_value(key, 'Layout Id') or '').upper()))
	return tuple(sorted(found, key=lambda item: item[1].casefold()))


def identifier_of(handle):
	"""The layout identifier ("00001009") of a loaded layout, from its handle."""
	device = (handle or 0) >> 16 & 0xFFFF
	if device & 0xF000 == 0xF000:
		# A variant (as US-International): its number is the "Layout Id" of one of the layouts.
		layout_id = '%04X' % (device & 0x0FFF)
		for identifier, name, item_id in all_layouts():
			if item_id == layout_id:
				return identifier
		return None
	return '0000%04X' % device


def name_of(identifier):
	for item, name, layout_id in all_layouts():
		if item == (identifier or '').upper():
			return name
	return identifier


def installed():
	"""The handles of the layouts loaded in Windows: those added in its Settings, and those loaded here."""
	count = user32.GetKeyboardLayoutList(0, None)
	handles = (wintypes.HKL * count)()
	user32.GetKeyboardLayoutList(count, handles)
	return [handle for handle in handles if handle]


def load(identifier):
	"""Load a layout of Windows (not activated); returns its handle. Already loaded: the same handle."""
	return user32.LoadKeyboardLayoutW(identifier, KLF_NOTELLSHELL)


def unload(handle):
	return bool(user32.UnloadKeyboardLayout(handle))


def current(window=None):
	"""The layout in use in a window (the program the user works in)."""
	thread = user32.GetWindowThreadProcessId(window, None) if window else 0
	return user32.GetKeyboardLayout(thread)


def activate(handle, window=None):
	"""Ask the program the user works in, and every other one, to use this layout, as Windows+Space does."""
	value = handle if handle < 2 ** 63 else handle - 2 ** 64   # a handle such as 0xF0010409, as a signed value
	if window:
		user32.PostMessageW(window, WM_INPUTLANGCHANGEREQUEST, 0, value)
	user32.PostMessageW(HWND_BROADCAST, WM_INPUTLANGCHANGEREQUEST, 0, value)
