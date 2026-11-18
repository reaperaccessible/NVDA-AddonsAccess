# KeyboardAccess: the keys and clicks sent to Windows.
# Based on Remap Applications Key, Copyright (C) 2021 Héctor J. Benítez Corredera,
# 2023-2026 Rui Fontes and Ângelo Abrantes. Released under the GPL.
# This module does not touch NVDA's interface, so it can be tested alone.

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL('user32', use_last_error=True)

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
VK_SHIFT = 0x10
VK_APPS = 0x5D   # the Applications key, an "extended" key
# Keys that, still held down, would change the key sent: Shift, Ctrl, Alt, both Windows keys.
MODIFIERS = (0x10, 0x11, 0x12, 0x5B, 0x5C)

ULONG_PTR = wintypes.WPARAM


class MOUSEINPUT(ctypes.Structure):
	_fields_ = (('dx', wintypes.LONG), ('dy', wintypes.LONG), ('mouseData', wintypes.DWORD),
		('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD), ('dwExtraInfo', ULONG_PTR))


class KEYBDINPUT(ctypes.Structure):
	_fields_ = (('wVk', wintypes.WORD), ('wScan', wintypes.WORD), ('dwFlags', wintypes.DWORD),
		('time', wintypes.DWORD), ('dwExtraInfo', ULONG_PTR))


class HARDWAREINPUT(ctypes.Structure):
	_fields_ = (('uMsg', wintypes.DWORD), ('wParamL', wintypes.WORD), ('wParamH', wintypes.WORD))


class INPUT(ctypes.Structure):
	class _INPUT(ctypes.Union):
		_fields_ = (('ki', KEYBDINPUT), ('mi', MOUSEINPUT), ('hi', HARDWAREINPUT))
	_anonymous_ = ('_input',)
	_fields_ = (('type', wintypes.DWORD), ('_input', _INPUT))


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short


def _send(*inputs):
	array = (INPUT * len(inputs))(*inputs)
	return user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))


def _key(vk, up=False):
	flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk == VK_APPS else 0)
	return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, wScan=user32.MapVirtualKeyW(vk, 0), dwFlags=flags))


def modifiers_down(ignore_shift=False):
	"""True while Shift, Ctrl, Alt or a Windows key is still held down."""
	return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in MODIFIERS if not (ignore_shift and vk == VK_SHIFT))


def key_down(vk):
	"""True while this key is held down."""
	return bool(user32.GetAsyncKeyState(vk) & 0x8000)


VK_MENU_MASK = 0xE8   # a key code Windows assigns to nothing


def mask_menu():
	"""Press and release a key that does nothing: Alt, released after it, does not select the menu bar."""
	return _send(_key(VK_MENU_MASK), _key(VK_MENU_MASK, up=True))


def applications(shift=False):
	"""Press and release the Applications key, with Shift if asked: Windows' own key, sent as is."""
	keys = []
	if shift:
		keys.append(_key(VK_SHIFT))
	keys += [_key(VK_APPS), _key(VK_APPS, up=True)]
	if shift:
		keys.append(_key(VK_SHIFT, up=True))
	return _send(*keys)


def click(button='left', count=1):
	"""Click where the mouse pointer is."""
	down, up = (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP) if button == 'right' else (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)
	events = []
	for time in range(count):
		events += [INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dwFlags=down)), INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dwFlags=up))]
	return _send(*events)
