# ResourceMonitorAccess: what the computer reports, written as sentences.
# Based on Resource Monitor, Copyright 2013-2026 Alex Hall, Joseph Lee, Kefas Lungu, Beqa Gozalishvili,
# Tuukka Ojala, Ethin Probst, Kevin Derome and other NVDA contributors. Released under the GPL.
# This module does not touch NVDA's interface, so it can be tested alone.

import socket
import threading
import time
from datetime import datetime

import addonHandler
import psutil

addonHandler.initTranslation()

UNIT = 1024.0   # sizes are counted like Windows Explorer counts them
# A load (processor, applications, network) cannot be read at once: Windows only counts the work done
# since it started. It is read when the user asks, then again MEASURE seconds later, and the two compared.
MEASURE = 1.0   # as the Task Manager does


def _measure(read):
	"""(seconds, value when asked, value MEASURE seconds later)."""
	then, before = time.monotonic(), read()
	time.sleep(MEASURE)
	value = read()
	return time.monotonic() - then, before, value


def _cpu_total():
	return psutil.cpu_percent()


def _cpu_cores():
	return psutil.cpu_percent(percpu=True)


def _net():
	return psutil.net_io_counters(pernic=True)


def _applications():
	"""{name: [processor, memory]}, the processes of one application put together."""
	apps = {}
	for process in psutil.process_iter(['name']):
		if process.pid == 0:   # the "System Idle Process" is the unused processor time
			continue
		try:
			load = process.cpu_percent(None)
			memory = process.memory_info().private
		except Exception:
			continue
		name = process.info['name'] or ''
		if name.lower().endswith('.exe'):
			name = name[:-4]
		entry = apps.setdefault(name.lower(), [name, 0.0, 0])
		entry[1] += load
		entry[2] += memory
	return apps


def number(value, decimals=1):
	"""12.5 -> '12,5' in French, '12.5' in English; 12.0 -> '12'."""
	text = ('%.*f' % (decimals, value)).rstrip('0').rstrip('.') if decimals else '%d' % round(value)
	# Translators: The decimal separator, as in 12.5 GB. French uses a comma.
	return text.replace('.', _('.'))


def size(amount):
	"""A size in the user's own units: '12,5 Go' in French, '12.5 GB' in English."""
	units = (
		# Translators: A size in terabytes. French: To.
		(UNIT ** 4, _('{value} TB')),
		# Translators: A size in gigabytes. French: Go.
		(UNIT ** 3, _('{value} GB')),
		# Translators: A size in megabytes. French: Mo.
		(UNIT ** 2, _('{value} MB')),
		# Translators: A size in kilobytes. French: Ko.
		(UNIT, _('{value} KB')),
	)
	for factor, template in units:
		if amount >= factor:
			return template.format(value=number(amount / factor))
	# Translators: A size in bytes, below one kilobyte.
	return _('{value} bytes').format(value=number(amount, 0))


def percent(value):
	return number(value)


def summary():
	# Translators: The overall state of the computer. {memory} and {processor} are percentages.
	return _('Memory {memory}% used, processor at {processor}%.').format(
		memory=percent(psutil.virtual_memory().percent), processor=percent(_measure(_cpu_total)[2]))


def processor():
	# The average is taken from the same reading as the cores, so that both always agree.
	loads = _measure(_cpu_cores)[2]
	average = sum(loads) / len(loads)
	if len(loads) == 1:
		# Translators: The load of a single-core processor.
		return _('Processor at {load}%.').format(load=percent(average))
	cores = ', '.join(
		# Translators: The load of one processor core, as in "core 3: 12%".
		_('core {number}: {load}%').format(number=index, load=percent(load))
		for index, load in enumerate(loads, start=1))
	# Translators: The average load of the processor, then the load of each core.
	return _('Processor at {load}% on average; {cores}.').format(load=percent(average), cores=cores)


def applications(count=5):
	apps = sorted(_measure(_applications)[2].values(), key=lambda app: (-round(app[1]), -app[2]))[:count]
	cores = psutil.cpu_count() or 1
	# Translators: One of the applications using the most, as in "reaper: processor 23%, memory 2.1 GB".
	return ' '.join(_('{name}: processor {load}%, memory {memory}.').format(
		name=name, load=percent(load / cores), memory=size(memory)) for name, load, memory in apps)


def _used(used, total):
	# Translators: How much of something is used, as in "12.5 GB of 32 GB used (39%)".
	return _('{used} of {total} used ({percent}%)').format(used=size(used), total=size(total), percent=percent(used / total * 100))


def memory():
	physical = psutil.virtual_memory()
	parts = [
		# Translators: The use of the computer's memory (RAM).
		_('Physical memory: {usage}.').format(usage=_used(physical.used, physical.total))]
	try:
		virtual = psutil._psutil_windows.virtual_mem()
		total, available = virtual[2], virtual[3]
		if total:
			# Translators: The use of the virtual memory (RAM plus the paging file).
			parts.append(_('Virtual memory: {usage}.').format(usage=_used(total - available, total)))
	except Exception:
		pass
	return ' '.join(parts)


def drive_kind(options):
	options = options.split(',')
	if 'remote' in options:
		# Translators: A kind of drive.
		return _('network drive')
	if 'removable' in options:
		return _('removable drive')
	if 'cdrom' in options:
		return _('optical drive')
	return _('fixed drive')


def drives():
	parts = []
	# all=True also lists mapped network drives.
	for drive in psutil.disk_partitions(all=True):
		# Only drives Windows says are ready: an empty card reader would freeze the query.
		if not drive.fstype:
			continue
		kind = drive_kind(drive.opts)
		try:
			usage = psutil.disk_usage(drive.mountpoint)
		except OSError:
			if 'remote' in drive.opts.split(','):
				# Translators: A network drive that cannot be reached.
				parts.append(_('{drive} ({kind}): not available.').format(drive=drive.mountpoint, kind=kind))
			continue
		# Translators: One drive, as in "C:\ (fixed drive): 120 GB of 476 GB used (25%)."
		parts.append(_('{drive} ({kind}): {usage}.').format(drive=drive.mountpoint, kind=kind, usage=_used(usage.used, usage.total)))
	# Translators: Said when no drive can be reported.
	return ' '.join(parts) or _('No drive found.')


def _active_interface():
	"""The name of the network connection Windows uses to reach the Internet, or None."""
	connection = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	try:
		# Nothing is sent: Windows only chooses the way out.
		connection.connect(('8.8.8.8', 53))
		address = connection.getsockname()[0]
	except OSError:
		return None
	finally:
		connection.close()
	for name, addresses in psutil.net_if_addrs().items():
		if any(item.address == address for item in addresses):
			return name
	return None


def _internet():
	"""True or False as Windows itself knows it, None if it cannot be asked. No server is contacted."""
	import comtypes
	import comtypes.client
	try:
		comtypes.CoInitialize()
	except OSError:
		pass
	try:
		manager = comtypes.client.CreateObject('{DCB00C01-570F-4A9B-8D69-199FDBA5723B}', dynamic=True)
		connected = bool(manager.IsConnectedToInternet)
		del manager
		return connected
	except Exception:
		return None
	finally:
		comtypes.CoUninitialize()


def network():
	name = _active_interface()
	if name is None:
		# Translators: Said when the computer has no network connection.
		return _('No network connection.')
	if any(word in name.lower() for word in ('wi-fi', 'wifi', 'wlan', 'wireless')):
		# Translators: How the computer is connected.
		way = _('Connected by Wi-Fi')
	else:
		way = _('Connected by cable')
	internet = _internet()
	if internet is True:
		# Translators: Follows "Connected by cable", as in "Connected by cable, the Internet is reachable."
		parts = [_('{way}, the Internet is reachable.').format(way=way)]
	elif internet is False:
		parts = [_('{way}, but the Internet is not reachable.').format(way=way)]
	else:
		parts = [way + '.']
	seconds, before, now = _measure(_net)
	if name in before and name in now and seconds > 0:
		received = max(0, now[name].bytes_recv - before[name].bytes_recv) / seconds
		sent = max(0, now[name].bytes_sent - before[name].bytes_sent) / seconds
		# Translators: The current speed of the connection, as in "Downloading 1.2 MB per second, sending 30 KB per second."
		parts.append(_('Downloading {received} per second, sending {sent} per second.').format(received=size(received), sent=size(sent)))
	return ' '.join(parts)


# --- the Internet speed test, with Cloudflare's speed test service. Only when the user asks for it.
SPEED_HOST = 'speed.cloudflare.com'
SPEED_HEADERS = {'User-Agent': 'ResourceMonitorAccess'}
SPEED_STREAMS = 4          # several connections at once, as a browser does: one alone underestimates fast lines
SPEED_SECONDS = 6          # each direction is measured for at most this long...
# ...or until this much has been received or sent (bytes): to spare limited connections, and because the
# service refuses for a while the downloads of an address that has asked for too much.
SPEED_DOWN_LIMIT = 150e6
SPEED_UP_LIMIT = 75e6
SPEED_PIECE = 25000000     # the largest download the service gives at once
SPEED_UP_PIECE = 1024 * 1024


def _connection():
	import http.client
	return http.client.HTTPSConnection(SPEED_HOST, timeout=15)


def _latency():
	"""The response time in milliseconds, as measured by the server itself on the connection."""
	import re
	connection = _connection()
	best = None
	try:
		for attempt in range(5):
			started = time.perf_counter()
			connection.request('GET', '/__down?bytes=0', headers=SPEED_HEADERS)
			response = connection.getresponse()
			response.read()
			elapsed = (time.perf_counter() - started) * 1000
			match = re.search(r'(?<!min_)rtt=(\d+)', response.getheader('server-timing') or '')
			value = int(match.group(1)) / 1000 if match else elapsed
			best = value if best is None else min(best, value)
	finally:
		connection.close()
	return best


def _transfer(work, limit):
	"""Run work(counter, deadline, limit) on several connections; returns megabits per second."""
	counter = {'bytes': 0, 'lock': threading.Lock(), 'refused': None}
	started = time.perf_counter()
	deadline = started + SPEED_SECONDS
	threads = [threading.Thread(target=work, args=(counter, deadline, limit), daemon=True) for stream in range(SPEED_STREAMS)]
	for thread in threads:
		thread.start()
	for thread in threads:
		thread.join(SPEED_SECONDS + 20)
	elapsed = time.perf_counter() - started
	return counter['bytes'] * 8 / elapsed / 1e6, counter['refused']


def _add(counter, amount, deadline, limit):
	"""Count what went through; False when it is time to stop."""
	with counter['lock']:
		counter['bytes'] += amount
		return counter['bytes'] < limit and time.perf_counter() < deadline


def _refused(counter, response):
	"""The service refuses (too many tests): keep how many seconds it asks to wait, 0 if it does not say."""
	if response.status == 429:
		try:
			counter['refused'] = max(counter['refused'] or 0, int(response.getheader('retry-after') or 0))
		except ValueError:
			counter['refused'] = counter['refused'] or 0


def _download(counter, deadline, limit):
	connection = _connection()
	try:
		while True:
			connection.request('GET', '/__down?bytes=%d' % SPEED_PIECE, headers=SPEED_HEADERS)
			response = connection.getresponse()
			if response.status != 200:
				_refused(counter, response)
				return
			while True:
				piece = response.read(65536)
				if not piece:
					break
				if not _add(counter, len(piece), deadline, limit):
					return
	except Exception:
		pass
	finally:
		connection.close()


def _upload(counter, deadline, limit):
	body = b'0' * SPEED_UP_PIECE
	headers = dict(SPEED_HEADERS, **{'Content-Type': 'application/octet-stream'})
	connection = _connection()
	try:
		while True:
			connection.request('POST', '/__up', body=body, headers=headers)
			response = connection.getresponse()
			response.read()
			if response.status != 200:
				_refused(counter, response)
				return
			# Only what the server confirmed it received is counted.
			if not _add(counter, len(body), deadline, limit):
				return
	except Exception:
		pass
	finally:
		connection.close()


def speed(megabits):
	# Translators: An Internet speed, as in "245 megabits per second".
	return _('{value} megabits per second').format(value=number(megabits, 0 if megabits >= 10 else 1))


def speed_test():
	"""15 seconds at most. Contacts Cloudflare's speed test service."""
	try:
		latency = _latency()
	except Exception:
		# Translators: Said when the speed test cannot reach the Internet.
		return _('The speed test could not be done: the Internet is not reachable.')
	down, refused = _transfer(_download, SPEED_DOWN_LIMIT)
	if refused is not None and down <= 0:
		return _too_frequent(refused)
	if down <= 0:
		# Translators: Said when the speed test fails although the Internet is reachable.
		return _('The speed test could not be done. Try again later.')
	up, refused = _transfer(_upload, SPEED_UP_LIMIT)
	if up <= 0:
		# Translators: The result of the speed test when the upload could not be measured.
		return _('Download: {down}. Upload: could not be measured. Response time: {latency} milliseconds.').format(
			down=speed(down), latency=number(latency, 0))
	# Translators: The result of the speed test.
	return _('Download: {down}. Upload: {up}. Response time: {latency} milliseconds.').format(
		down=speed(down), up=speed(up), latency=number(latency, 0))


def _too_frequent(seconds):
	if seconds > 0:
		# Translators: Said when the speed test service refuses for a while, as in "Try again in 58 minutes."
		return _('The speed test service limits tests that are too frequent. Try again in {duration}.').format(
			duration=duration(-(-seconds // 60) * 60))
	return _('The speed test service limits tests that are too frequent. Try again later.')


def has_battery():
	try:
		return psutil.sensors_battery() is not None
	except Exception:
		return False


def battery():
	state = psutil.sensors_battery()
	if state is None:
		# Translators: Said on a computer without a battery.
		return _('No battery.')
	level = percent(state.percent)
	if state.power_plugged:
		# Translators: The battery of a laptop, plugged in.
		return _('Battery at {level}%, plugged in.').format(level=level)
	if state.secsleft not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN) and state.secsleft > 0:
		# Translators: The battery of a laptop, not plugged in, as in "Battery at 82%, 3 hours, 12 minutes left."
		return _('Battery at {level}%, {duration} left.').format(level=level, duration=duration(state.secsleft))
	# Translators: The battery of a laptop, not plugged in, the time left being unknown.
	return _('Battery at {level}%, not plugged in.').format(level=level)


def windows_version():
	import winVersion
	version = winVersion.getWinVer()
	name = version.releaseName
	if version.productType != 'workstation':
		name = 'Windows Server ' + name.rpartition(' ')[-1]
	# Translators: The version of Windows, as in "Windows 11 24H2 (AMD64), build 26100.4061".
	return _('{name} ({architecture}), build {build}.').format(
		name=name, architecture=version.processorArchitecture, build=f'{version.build}.{version.revision}')


def duration(total_seconds):
	"""'2 days, 3 hours, 12 minutes': only what is not zero, and seconds only below a minute."""
	days, rest = divmod(int(total_seconds), 86400)
	hours, rest = divmod(rest, 3600)
	minutes, seconds = divmod(rest, 60)
	parts = []
	# Translators: Parts of a duration. The singular is used for 1, the plural otherwise.
	for value, one, many in ((days, _('1 day'), _('{count} days')), (hours, _('1 hour'), _('{count} hours')),
			(minutes, _('1 minute'), _('{count} minutes'))):
		if value:
			parts.append(one if value == 1 else many.format(count=value))
	if not parts:
		parts.append(_('1 second') if seconds == 1 else _('{count} seconds').format(count=seconds))
	return ', '.join(parts)


def uptime():
	boot = psutil.boot_time()
	if not boot:
		# Translators: Said when the time since Windows started cannot be known.
		return _('The time since Windows started is not known.')
	# Translators: How long ago Windows started, as in "Started 2 days, 3 hours ago."
	return _('Windows started {duration} ago.').format(duration=duration((datetime.now() - datetime.fromtimestamp(boot)).total_seconds()))


def temperature(celsius_text, unit):
	try:
		celsius = float(celsius_text)
	except (TypeError, ValueError):
		return celsius_text
	if unit == 'fahrenheit':
		# Translators: A temperature in degrees Fahrenheit.
		return _('{value} degrees Fahrenheit').format(value=number(celsius * 9 / 5 + 32))
	# Translators: A temperature in degrees Celsius.
	return _('{value} degrees Celsius').format(value=number(celsius))


def _gpu_items(providers):
	"""(items, problem): the cards found, or the reason why there are none."""
	found = False
	for provider in providers:
		items = provider.collect()
		if items is None:
			continue
		found = True
		if items:
			return items, None
	if not found:
		# Translators: Said when the computer has no graphics card the add-on can read (NVIDIA only).
		return [], _('No information on the graphics card: only NVIDIA cards can be read.')
	# Translators: Said when the graphics card could not be read.
	return [], _('The graphics card could not be read.')


def gpu(providers, unit):
	items, problem = _gpu_items(providers)
	if problem:
		return problem
	return ' '.join(
		# Translators: One graphics card, as in "Graphics card 1: in use at 12%, 45 degrees Celsius."
		_('Graphics card {number}: in use at {usage}%, {temperature}.').format(
			number=index, usage=item.utilization, temperature=temperature(item.temperature, unit))
		for index, item in enumerate(items, start=1))


def gpu_memory(providers):
	items, problem = _gpu_items(providers)
	if problem:
		return problem
	parts = []
	for index, item in enumerate(items, start=1):
		try:
			used, total = float(item.memoryUsed) * UNIT * UNIT, float(item.memoryTotal) * UNIT * UNIT
		except (TypeError, ValueError):
			continue
		if total > 0:
			# Translators: The memory of one graphics card, as in "Graphics card 1 memory: 2 GB of 8 GB used (25%)."
			parts.append(_('Graphics card {number} memory: {usage}.').format(number=index, usage=_used(used, total)))
	return ' '.join(parts) or _('The graphics card could not be read.')
