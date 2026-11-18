# ResourceMonitorAccess: the Wi-Fi connection, and its connect and disconnect notifications.
# Taken from Resource Monitor (Joseph Lee and contributors), itself based on WlanReporter. Released under the GPL.

from ctypes import POINTER, addressof, byref, string_at, wintypes

import addonHandler

addonHandler.initTranslation()

# Windows Server before 2025 has no wlanapi.dll: Wi-Fi is then simply not reported.
try:
	from . import wlanapi
	available = True
except (OSError, AttributeError):
	wlanapi = None
	available = False


def _security_types():
	if not available:
		return {}
	return {
		# Translators: A Wi-Fi network without a password.
		wlanapi.DOT11_AUTH_ALGO_80211_OPEN: _('open network, no password'),
		wlanapi.DOT11_AUTH_ALGO_80211_SHARED_KEY: 'WEP',
		wlanapi.DOT11_AUTH_ALGO_WPA: 'WPA-Enterprise',
		wlanapi.DOT11_AUTH_ALGO_WPA_PSK: 'WPA-PSK',
		wlanapi.DOT11_AUTH_ALGO_RSNA: 'WPA2-Enterprise',
		wlanapi.DOT11_AUTH_ALGO_RSNA_PSK: 'WPA2-PSK',
		wlanapi.DOT11_AUTH_ALGO_WPA3_ENT_192: 'WPA3-Enterprise-192bit',
		wlanapi.DOT11_AUTH_ALGO_WPA3_SAE: 'WPA3-SAE',
		wlanapi.DOT11_AUTH_ALGO_OWE: 'OWE',
		wlanapi.DOT11_AUTH_ALGO_WPA3_ENT: 'WPA3-Enterprise',
	}


def _resize(array, size):
	return (array._type_ * size).from_address(addressof(array))


def _ssid(ssid):
	length = min(ssid.SSIDLength, 32)
	return string_at(addressof(ssid) + wlanapi.DOT11_SSID.SSID.offset, length).decode('utf-8', errors='replace')


class WifiMonitor:
	"""Opens Windows' Wi-Fi service once, reports the connection, and passes on connect and disconnect events.

	notify(message, sound_name) is called from Windows' thread; the caller moves it to NVDA's.
	"""

	def __init__(self, notify):
		self.notify = notify
		self.handle = None
		self._callback = None
		if not available:
			return
		version, handle = wintypes.DWORD(), wintypes.HANDLE()
		try:
			wlanapi.WlanOpenHandle(wlanapi.CLIENT_VERSION_WINDOWS_VISTA_OR_LATER, None, byref(version), byref(handle))
		except OSError:
			return
		self.handle = handle
		# Kept on the object: Windows calls it for as long as the registration lasts.
		self._callback = wlanapi.WLAN_NOTIFICATION_CALLBACK(self._on_event)
		try:
			wlanapi.WlanRegisterNotification(handle, wlanapi.WLAN_NOTIFICATION_SOURCE_ACM, True, self._callback, None, None, None)
		except OSError:
			pass

	def _on_event(self, data, context):
		try:
			event = data.contents
			if event.NotificationSource != wlanapi.WLAN_NOTIFICATION_SOURCE_ACM:
				return
			code = event.NotificationCode
			if code == wlanapi.wlan_notification_acm_connection_complete:
				details = wlanapi.WLAN_CONNECTION_NOTIFICATION_DATA.from_address(event.pData)
				if details.wlanReasonCode == wlanapi.ERROR_SUCCESS:
					# Translators: Said when the computer connects to a Wi-Fi network.
					self.notify(_('Connected to {network}').format(network=_ssid(details.dot11Ssid)), 'connect.wav')
			elif code == wlanapi.wlan_notification_acm_disconnected:
				details = wlanapi.WLAN_CONNECTION_NOTIFICATION_DATA.from_address(event.pData)
				# Translators: Said when the computer leaves a Wi-Fi network.
				self.notify(_('Disconnected from {network}').format(network=_ssid(details.dot11Ssid)), 'disconnect.wav')
			elif code == wlanapi.wlan_notification_acm_interface_arrival:
				# Translators: Said when a Wi-Fi adapter is turned on or plugged in.
				self.notify(_('A Wi-Fi adapter has been turned on'), 'connect.wav')
			elif code == wlanapi.wlan_notification_acm_interface_removal:
				# Translators: Said when a Wi-Fi adapter is turned off or unplugged.
				self.notify(_('A Wi-Fi adapter has been turned off'), 'disconnect.wav')
		except Exception:
			pass

	def info(self):
		"""The current connection, as a sentence."""
		# Translators: Said when the computer has no Wi-Fi adapter.
		none = _('No Wi-Fi adapter.')
		if not self.handle:
			return none
		# Translators: Said when Wi-Fi information cannot be read.
		failed = _('The Wi-Fi information could not be read.')
		interfaces = POINTER(wlanapi.WLAN_INTERFACE_INFO_LIST)()
		try:
			try:
				wlanapi.WlanEnumInterfaces(self.handle, None, byref(interfaces))
			except OSError:
				return failed
			if interfaces.contents.NumberOfItems == 0:
				return none
			# Translators: Said when the Wi-Fi adapter is not connected to any network.
			result = _('Not connected to any Wi-Fi network.')
			security = _security_types()
			for interface in _resize(interfaces.contents.InterfaceInfo, interfaces.contents.NumberOfItems):
				if interface.isState != wlanapi.wlan_interface_state_connected:
					continue
				networks = POINTER(wlanapi.WLAN_AVAILABLE_NETWORK_LIST)()
				try:
					wlanapi.WlanGetAvailableNetworkList(self.handle, byref(interface.InterfaceGuid), 0, None, byref(networks))
				except OSError:
					return failed
				try:
					for network in _resize(networks.contents.Network, networks.contents.NumberOfItems):
						if network.Flags & wlanapi.WLAN_AVAILABLE_NETWORK_CONNECTED:
							# Translators: The current Wi-Fi connection, as in "Connected to Home, signal 80%, security WPA2-PSK."
							result = _('Connected to {network}, signal {signal}%, security {security}.').format(
								network=_ssid(network.dot11Ssid), signal=network.wlanSignalQuality,
								security=security.get(network.dot11DefaultAuthAlgorithm, '?'))
							break
				finally:
					if networks:
						wlanapi.WlanFreeMemory(networks)
			return result
		finally:
			if interfaces:
				wlanapi.WlanFreeMemory(interfaces)

	def close(self):
		if not self.handle:
			return
		try:
			wlanapi.WlanRegisterNotification(self.handle, wlanapi.WLAN_NOTIFICATION_SOURCE_NONE, True, self._callback, None, None, None)
			wlanapi.WlanCloseHandle(self.handle, None)
		except OSError:
			pass
		self.handle = None
