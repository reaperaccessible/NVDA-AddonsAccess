# AudioManagerAccess: the sound devices and the applications of Windows.
# Based on Audio Manager by huaiyinfeilong. Released under the GPL.
# Uses pycaw, supplied with NVDA, for the devices, the volumes and the default device. The output and input
# of one application go through Windows' own "App volume and device preferences", which Windows does not
# document: it is reached as EarTrumpet and SoundVolumeView do.
# This module does not touch NVDA's interface, so it can be tested alone.

import ctypes
import os
from ctypes import wintypes

import comtypes
from pycaw.constants import DEVICE_STATE, EDataFlow, ERole
from pycaw.utils import AudioUtilities

OUTPUT = EDataFlow.eRender.value
INPUT = EDataFlow.eCapture.value
_ROLES = (ERole.eConsole.value, ERole.eMultimedia.value)
_INTERFACE = {
	OUTPUT: '#{e6327cad-dcec-4949-ae8a-991e976a79d2}',
	INPUT: '#{2eef81be-33fa-4800-9670-1cd474972c3f}',
}
_PREFIX = '\\\\?\\SWD#MMDEVAPI#'


class Device:
	def __init__(self, identifier, name, flow, volume, muted, default):
		self.id, self.name, self.flow = identifier, name, flow
		self.volume, self.muted, self.default = volume, muted, default


def _percent(scalar):
	return int(round(scalar * 100))


def default_id(flow):
	try:
		enumerator = AudioUtilities.GetDeviceEnumerator()
		return enumerator.GetDefaultAudioEndpoint(flow, ERole.eConsole.value).GetId()
	except Exception:
		return None


def devices(flow):
	"""The active devices of one direction: OUTPUT (speakers, headphones) or INPUT (microphones)."""
	found = []
	default = default_id(flow)
	for device in AudioUtilities.GetAllDevices(data_flow=flow, device_state=DEVICE_STATE.ACTIVE.value):
		volume = device.EndpointVolume
		try:
			level, muted = _percent(volume.GetMasterVolumeLevelScalar()), bool(volume.GetMute())
		except Exception:
			level, muted = None, False
		found.append(Device(device.id, device.FriendlyName or device.id, flow, level, muted, device.id == default))
	return sorted(found, key=lambda item: item.name.casefold())


def _endpoint(identifier):
	from pycaw.api.endpointvolume import IAudioEndpointVolume
	from comtypes import CLSCTX_ALL
	device = AudioUtilities.GetDeviceEnumerator().GetDevice(identifier)
	return device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(IAudioEndpointVolume)


def set_volume(identifier, percent):
	percent = max(0, min(100, int(percent)))
	_endpoint(identifier).SetMasterVolumeLevelScalar(percent / 100.0, None)
	return percent


def set_mute(identifier, muted):
	_endpoint(identifier).SetMute(bool(muted), None)


def set_default(identifier):
	"""The device becomes Windows' default one, for every use (as in Windows' Sound settings)."""
	AudioUtilities.SetDefaultDevice(identifier)


# --- the applications playing or recording sound
class Application:
	def __init__(self, key, name):
		self.key, self.name = key, name
		self.sessions, self.pids = [], []
		self.volume, self.muted = 100, False
		self.output, self.input = '', ''   # '' = Windows' default device


def _description(path):
	"""The name a program gives itself, as "Mozilla Thunderbird"."""
	try:
		import win32api
		translations = win32api.GetFileVersionInfo(path, '\\VarFileInfo\\Translation')
		language, codepage = translations[0]
		text = win32api.GetFileVersionInfo(path, '\\StringFileInfo\\%04x%04x\\FileDescription' % (language, codepage))
		return text.strip() or None
	except Exception:
		return None


def applications(system_name='System sounds'):
	"""The applications with sound, one entry per program even when it has several sessions."""
	found = {}
	for session in AudioUtilities.GetAllSessions():
		process = session.Process
		if process is None:
			key, name = '', system_name
		else:
			try:
				key = process.name().lower()
				name = _description(process.exe()) or os.path.splitext(process.name())[0]
			except Exception:
				continue
		app = found.get(key)
		if app is None:
			app = found[key] = Application(key, name)
		app.sessions.append(session)
		if session.ProcessId:
			app.pids.append(session.ProcessId)
	for app in found.values():
		try:
			control = app.sessions[0].SimpleAudioVolume
			app.volume, app.muted = _percent(control.GetMasterVolume()), bool(control.GetMute())
		except Exception:
			pass
		if app.pids:
			app.output = app_device(app.pids[0], OUTPUT)
			app.input = app_device(app.pids[0], INPUT)
	return sorted(found.values(), key=lambda app: (app.key == '', app.name.casefold()))


def set_app_volume(app, percent):
	percent = max(0, min(100, int(percent)))
	for session in app.sessions:
		session.SimpleAudioVolume.SetMasterVolume(percent / 100.0, None)
	app.volume = percent
	return percent


def set_app_mute(app, muted):
	for session in app.sessions:
		session.SimpleAudioVolume.SetMute(bool(muted), None)
	app.muted = bool(muted)


# --- the output and input of one application: Windows' hidden "AudioPolicyConfig"
_HSTRING = ctypes.c_void_p
_combase = ctypes.WinDLL('combase')
_combase.WindowsCreateString.argtypes = (wintypes.LPCWSTR, wintypes.UINT, ctypes.POINTER(_HSTRING))
_combase.WindowsDeleteString.argtypes = (_HSTRING,)
_combase.WindowsGetStringRawBuffer.argtypes = (_HSTRING, ctypes.POINTER(wintypes.UINT))
_combase.WindowsGetStringRawBuffer.restype = wintypes.LPCWSTR
_combase.RoGetActivationFactory.argtypes = (_HSTRING, ctypes.POINTER(comtypes.GUID), ctypes.POINTER(ctypes.c_void_p))
# Windows 10 21H2 and later; then the earlier Windows 10. Both have the methods at the same place.
_FACTORY_IIDS = ('{ab3d4648-e242-459f-b02f-541c70306324}', '{2a59116d-6c4f-45e0-a74f-707e3fef9258}')
_SET, _GET, _CLEAR = 25, 26, 27   # places in the interface: IInspectable (6) + 19 methods not used here
_factory = None


def _hstring(text):
	handle = _HSTRING()
	_combase.WindowsCreateString(text, len(text), ctypes.byref(handle))
	return handle


def _policy():
	global _factory
	if _factory is None:
		name = _hstring('Windows.Media.Internal.AudioPolicyConfig')
		try:
			for iid in _FACTORY_IIDS:
				factory = ctypes.c_void_p()
				if _combase.RoGetActivationFactory(name, ctypes.byref(comtypes.GUID(iid)), ctypes.byref(factory)) == 0 and factory.value:
					_factory = factory
					break
		finally:
			_combase.WindowsDeleteString(name)
	if _factory is None:
		raise OSError('Choosing the device of an application is not available in this version of Windows.')
	table = ctypes.cast(ctypes.cast(_factory, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
	return _factory, table


def _short_id(long_id):
	"""'\\\\?\\SWD#MMDEVAPI#{0.0.0.00000000}.{…}#{…}' -> '{0.0.0.00000000}.{…}', the id of the device."""
	if not long_id:
		return ''
	middle = long_id.split('#MMDEVAPI#', 1)[-1]
	return middle.rsplit('#', 1)[0]


def app_device(pid, flow):
	"""The device chosen for this application ('' when it follows Windows' default one)."""
	try:
		factory, table = _policy()
		get = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, wintypes.UINT, ctypes.c_int, ctypes.c_int,
			ctypes.POINTER(_HSTRING))(table[_GET])
		result = _HSTRING()
		get(factory, pid, flow, ERole.eMultimedia.value, ctypes.byref(result))
		if not result.value:
			return ''
		try:
			return _short_id(_combase.WindowsGetStringRawBuffer(result, None))
		finally:
			_combase.WindowsDeleteString(result)
	except Exception:
		return ''


def set_app_device(app, flow, identifier):
	"""Send an application to a device ('' = back to Windows' default one). Windows remembers it."""
	factory, table = _policy()
	set_ = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, wintypes.UINT, ctypes.c_int, ctypes.c_int, _HSTRING)(table[_SET])
	for pid in app.pids:
		for role in _ROLES:
			handle = _hstring(_PREFIX + identifier + _INTERFACE[flow]) if identifier else _HSTRING()
			try:
				set_(factory, pid, flow, role, handle)
			finally:
				if identifier:
					_combase.WindowsDeleteString(handle)
	if flow == OUTPUT:
		app.output = identifier
	else:
		app.input = identifier


def reset_applications():
	"""Every application back to Windows' default devices, at full volume, not muted."""
	try:
		factory, table = _policy()
		ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p)(table[_CLEAR])(factory)
	except OSError:
		pass
	for app in applications():
		try:
			set_app_volume(app, 100)
			set_app_mute(app, False)
		except Exception:
			pass


# --- Windows' audio service, and its classic Sound panel
class _SHELLEXECUTEINFOW(ctypes.Structure):
	_fields_ = (('cbSize', wintypes.DWORD), ('fMask', ctypes.c_ulong), ('hwnd', wintypes.HWND), ('lpVerb', wintypes.LPCWSTR),
		('lpFile', wintypes.LPCWSTR), ('lpParameters', wintypes.LPCWSTR), ('lpDirectory', wintypes.LPCWSTR), ('nShow', ctypes.c_int),
		('hInstApp', wintypes.HINSTANCE), ('lpIDList', ctypes.c_void_p), ('lpClass', wintypes.LPCWSTR), ('hkeyClass', wintypes.HKEY),
		('dwHotKey', wintypes.DWORD), ('hIconOrMonitor', wintypes.HANDLE), ('hProcess', wintypes.HANDLE))


SEE_MASK_NOCLOSEPROCESS = 0x00000040
ERROR_CANCELLED = 1223


def restart_service(timeout_ms=60000):
	"""Stop and start Windows' audio service, with Windows' permission (it asks the user). Waits for the end.
	Returns True when done, False when it failed, None when the user refused the permission."""
	kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
	shell32 = ctypes.WinDLL('shell32', use_last_error=True)
	info = _SHELLEXECUTEINFOW()
	info.cbSize = ctypes.sizeof(info)
	info.fMask = SEE_MASK_NOCLOSEPROCESS
	info.lpVerb = 'runas'
	info.lpFile = 'cmd.exe'
	info.lpParameters = '/c net stop Audiosrv /y && net start Audiosrv'
	info.nShow = 0
	if not shell32.ShellExecuteExW(ctypes.byref(info)):
		return None if ctypes.get_last_error() == ERROR_CANCELLED else False
	try:
		kernel32.WaitForSingleObject(info.hProcess, timeout_ms)
		code = wintypes.DWORD()
		kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
		return code.value == 0
	finally:
		kernel32.CloseHandle(info.hProcess)


def open_sound_panel():
	"""The classic Sound window of Windows (mmsys.cpl): Playback, Recording, Sounds, Communications."""
	import subprocess
	subprocess.Popen(['control.exe', 'mmsys.cpl'])
