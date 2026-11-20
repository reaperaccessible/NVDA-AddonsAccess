# AgendaAccess: time zones, read from Windows itself (NVDA has no time zone data of its own).
# A zone is named by its Windows key, as "Romance Standard Time"; Windows gives its name in the user's language,
# as "(UTC+01:00) Bruxelles, Copenhague, Madrid, Paris". The names of other programs (".ics" files from Google,
# as "Europe/Paris") are turned into Windows keys by the ICU library of Windows 10 and later.

import ctypes
import datetime
from ctypes import wintypes


class SYSTEMTIME(ctypes.Structure):
	_fields_ = [(name, wintypes.WORD) for name in
		('wYear', 'wMonth', 'wDayOfWeek', 'wDay', 'wHour', 'wMinute', 'wSecond', 'wMilliseconds')]


class DYNAMIC_TIME_ZONE_INFORMATION(ctypes.Structure):
	_fields_ = [
		('Bias', wintypes.LONG), ('StandardName', wintypes.WCHAR * 32), ('StandardDate', SYSTEMTIME),
		('StandardBias', wintypes.LONG), ('DaylightName', wintypes.WCHAR * 32), ('DaylightDate', SYSTEMTIME),
		('DaylightBias', wintypes.LONG), ('TimeZoneKeyName', wintypes.WCHAR * 128),
		('DynamicDaylightTimeDisabled', wintypes.BOOLEAN)]


_zones = None


def _all():
	"""{Windows key: DYNAMIC_TIME_ZONE_INFORMATION} of every zone Windows knows."""
	global _zones
	if _zones is None:
		_zones = {}
		try:
			enum = ctypes.windll.advapi32.EnumDynamicTimeZoneInformation
			index = 0
			while True:
				info = DYNAMIC_TIME_ZONE_INFORMATION()
				if enum(index, ctypes.byref(info)) != 0:
					break
				_zones[info.TimeZoneKeyName] = info
				index += 1
		except Exception:
			pass
	return _zones


def display_names():
	"""(Windows key, name in the user's language), in the order of their offset, as Windows lists them."""
	import winreg
	found = []
	for key in _all():
		try:
			with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Time Zones\\' + key) as entry:
				display = winreg.QueryValueEx(entry, 'Display')[0]
		except OSError:
			display = key
		found.append((key, display))
	return sorted(found, key=lambda item: (-_all()[item[0]].Bias, item[1]))


def short_name(key):
	"""The places of a zone, without its offset: "Bruxelles, Copenhague, Madrid, Paris"."""
	for found, display in display_names():
		if found == key:
			return display.split(') ', 1)[-1]
	return key


def local_key():
	"""The Windows key of the computer's own zone."""
	info = DYNAMIC_TIME_ZONE_INFORMATION()
	try:
		ctypes.windll.kernel32.GetDynamicTimeZoneInformation(ctypes.byref(info))
		return info.TimeZoneKeyName
	except Exception:
		return ''


def known(key):
	return bool(key) and key in _all()


def _system(moment):
	return SYSTEMTIME(moment.year, moment.month, 0, moment.day, moment.hour, moment.minute, moment.second, 0)


def to_local(moment, key):
	"""A date and time in the zone key, as a date and time of this computer. Unchanged if the zone is unknown."""
	if not known(key) or key == local_key():
		return moment
	utc = SYSTEMTIME()
	if not ctypes.windll.kernel32.TzSpecificLocalTimeToSystemTimeEx(ctypes.byref(_all()[key]), ctypes.byref(_system(moment)), ctypes.byref(utc)):
		return moment
	instant = datetime.datetime(utc.wYear, utc.wMonth, utc.wDay, utc.wHour, utc.wMinute, utc.wSecond, tzinfo=datetime.timezone.utc)
	return instant.astimezone().replace(tzinfo=None)


def _key(key):
	return key if known(key) else local_key()


def to_utc(moment, key=''):
	"""A date and time of the zone key ('' or unknown: this computer's zone), as a date and time in UTC."""
	key = _key(key)
	utc = SYSTEMTIME()
	try:
		if known(key) and ctypes.windll.kernel32.TzSpecificLocalTimeToSystemTimeEx(ctypes.byref(_all()[key]), ctypes.byref(_system(moment)), ctypes.byref(utc)):
			return datetime.datetime(utc.wYear, utc.wMonth, utc.wDay, utc.wHour, utc.wMinute, utc.wSecond)
	except Exception:
		pass
	return moment.astimezone(datetime.timezone.utc).replace(tzinfo=None)


def from_utc_to(moment, key=''):
	"""A date and time in UTC, as a date and time of the zone key ('' or unknown: this computer's zone)."""
	key = _key(key)
	local = SYSTEMTIME()
	try:
		if known(key) and ctypes.windll.kernel32.SystemTimeToTzSpecificLocalTimeEx(ctypes.byref(_all()[key]), ctypes.byref(_system(moment)), ctypes.byref(local)):
			return datetime.datetime(local.wYear, local.wMonth, local.wDay, local.wHour, local.wMinute, local.wSecond)
	except Exception:
		pass
	return from_utc(moment)


def convert(moment, from_key, to_key):
	"""A date and time of one zone, as a date and time of another ('' or 'UTC' understood)."""
	instant = moment if from_key == 'UTC' else to_utc(moment, from_key)
	return instant if to_key == 'UTC' else from_utc_to(instant, to_key)


def from_utc(moment):
	"""A date and time in UTC (the "Z" times of .ics files), as a date and time of this computer."""
	return moment.replace(tzinfo=datetime.timezone.utc).astimezone().replace(tzinfo=None)


def windows_key(name):
	"""The Windows key of a zone named by another program: a Windows key already, or a name such as "Europe/Paris"."""
	if not name:
		return ''
	if known(name):
		return name
	try:
		icu = ctypes.windll.icu
		get = icu.ucal_getWindowsTimeZoneID
		get.restype = ctypes.c_int32
		buffer = ctypes.create_unicode_buffer(128)
		status = ctypes.c_int(0)
		length = get(ctypes.c_wchar_p(name), len(name), buffer, 128, ctypes.byref(status))
		if status.value <= 0 and length > 0 and known(buffer.value):
			return buffer.value
	except Exception:
		pass
	return ''
