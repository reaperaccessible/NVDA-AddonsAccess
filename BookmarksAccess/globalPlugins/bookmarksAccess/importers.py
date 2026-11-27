# BookmarksAccess: reads the bookmarks of the web browsers installed on this computer.
# Copyright (C) 2026 ReaperAccessible
# This add-on is free software, licensed under the terms of the GNU General Public License (version 3).
# See the file LICENSE for more details.
#
# Everything here only READS the browsers' files: nothing is ever written to them.
# No NVDA module is used, so this file can be tested on its own.

import configparser
import datetime
import glob
import json
import os
import shutil
import tempfile

# Addresses worth keeping: the others (javascript:, place:, chrome://…) cannot be opened from here.
SCHEMES = ('http://', 'https://', 'ftp://', 'file:')

# The browsers built on Chromium, and where each keeps its profiles under %LOCALAPPDATA%.
CHROMIUM_BROWSERS = (
	('chrome', 'Chrome', ('Google', 'Chrome', 'User Data')),
	('edge', 'Edge', ('Microsoft', 'Edge', 'User Data')),
	('brave', 'Brave', ('BraveSoftware', 'Brave-Browser', 'User Data')),
)
CHROMIUM_ROOTS = (('bookmark_bar', 'toolbar'), ('other', 'other'), ('synced', 'mobile'))
FIREFOX_ROOTS = {
	'toolbar_____': 'toolbar', 'menu________': 'menu', 'unfiled_____': 'other', 'mobile______': 'mobile',
}
FIREFOX_ORDER = ('toolbar_____', 'menu________', 'unfiled_____', 'mobile______')


def folder(key, name, role=None):
	return {'kind': 'folder', 'key': key, 'name': name or '', 'role': role, 'children': []}


def address(name, url, added=None):
	item = {'kind': 'url', 'name': name or '', 'url': url}
	if added:
		item['added'] = added
	return item


# Seconds between 1601-01-01, where Chromium counts its dates from, and 1970-01-01.
CHROMIUM_EPOCH = 11644473600


def local_date(seconds):
	"""A date in seconds since 1970, as the add-on keeps it ('2024-05-01T10:30:00', local time), or None."""
	try:
		if not seconds or seconds <= 0:
			return None
		return datetime.datetime.fromtimestamp(seconds).isoformat(timespec='seconds')
	except (OSError, OverflowError, ValueError, TypeError):
		return None


def chromium_date(value):
	"""Chromium counts microseconds since 1601, in a string."""
	try:
		return local_date(int(value) / 1e6 - CHROMIUM_EPOCH)
	except (TypeError, ValueError):
		return None


def firefox_date(value):
	"""Firefox counts microseconds since 1970."""
	try:
		return local_date(int(value) / 1e6)
	except (TypeError, ValueError):
		return None


def keep(url):
	return isinstance(url, str) and url.strip().lower().startswith(SCHEMES)


def count_addresses(node):
	if node['kind'] == 'url':
		return 1
	return sum(count_addresses(child) for child in node['children'])


class Source:
	"""The bookmarks of one profile of one browser: `folders` are its main folders (bar, menu, other…)."""

	def __init__(self, key, browser, profile, folders):
		self.key = key  # stable: 'chrome:Default', 'firefox:Profiles/abcd.default-release'
		self.browser = browser  # 'Chrome', 'Edge', 'Brave', 'Firefox'
		self.profile = profile  # the profile's name, as the browser shows it
		self.folders = [item for item in folders if count_addresses(item)]
		self.label = browser

	@property
	def count(self):
		return sum(count_addresses(item) for item in self.folders)


# ---------------------------------------------------------------- Chrome, Edge, Brave

def _chromium_node(node):
	kind = node.get('type')
	if kind == 'url':
		return address(node.get('name'), node.get('url'), chromium_date(node.get('date_added'))) if keep(node.get('url')) else None
	if kind == 'folder' or 'children' in node:
		item = folder(node.get('guid') or node.get('id') or node.get('name'), node.get('name'))
		for child in node.get('children') or []:
			if isinstance(child, dict):
				converted = _chromium_node(child)
				if converted is not None:
					item['children'].append(converted)
		return item
	return None


def parse_chromium(data):
	"""The main folders of a Chromium 'Bookmarks' file (its JSON text or bytes)."""
	if isinstance(data, bytes):
		data = data.decode('utf-8-sig')
	roots = json.loads(data).get('roots') or {}
	result = []
	for name, role in CHROMIUM_ROOTS:
		node = roots.get(name)
		if isinstance(node, dict):
			item = _chromium_node(node)
			item['key'] = name  # the same in every profile, whatever its guid
			item['role'] = role
			result.append(item)
	return result


def chromium_profiles(user_data):
	"""[(folder name, profile name)] of a browser's 'User Data' folder, the main profile first."""
	names = {}
	try:
		with open(os.path.join(user_data, 'Local State'), encoding='utf-8-sig') as stream:
			cache = json.load(stream).get('profile', {}).get('info_cache', {})
		names = {key: (value or {}).get('name') or key for key, value in cache.items()}
	except (OSError, ValueError, AttributeError):
		pass
	found = []
	for path in glob.glob(os.path.join(user_data, '*', 'Bookmarks')):
		directory = os.path.basename(os.path.dirname(path))
		found.append((directory, names.get(directory, directory)))
	found.sort(key=lambda item: (item[0] != 'Default', item[0]))
	return found


def chromium_sources(local_appdata):
	sources = []
	for code, browser, parts in CHROMIUM_BROWSERS:
		user_data = os.path.join(local_appdata, *parts)
		for directory, profile in chromium_profiles(user_data):
			try:
				with open(os.path.join(user_data, directory, 'Bookmarks'), 'rb') as stream:
					folders = parse_chromium(stream.read())
			except (OSError, ValueError, AttributeError, TypeError):
				continue
			sources.append(Source(f'{code}:{directory}', browser, profile, folders))
	return sources


# ---------------------------------------------------------------- Firefox

def lz4_block(source, size_hint=0):
	"""Decode one LZ4 block (the format inside Firefox's .jsonlz4 files)."""
	out = bytearray()
	i, n = 0, len(source)
	while i < n:
		token = source[i]
		i += 1
		length = token >> 4
		if length == 15:
			while True:
				byte = source[i]
				i += 1
				length += byte
				if byte != 255:
					break
		out += source[i:i + length]
		i += length
		if i >= n:
			break
		offset = source[i] | (source[i + 1] << 8)
		i += 2
		if offset == 0 or offset > len(out):
			raise ValueError('corrupt LZ4 data')
		length = token & 15
		if length == 15:
			while True:
				byte = source[i]
				i += 1
				length += byte
				if byte != 255:
					break
		length += 4
		start = len(out) - offset
		if offset >= length:
			out += out[start:start + length]
		else:
			for k in range(length):
				out.append(out[start + k])
	if size_hint and len(out) != size_hint:
		raise ValueError('LZ4 data of the wrong size')
	return bytes(out)


def mozlz4(data):
	"""The content of a Firefox .jsonlz4 file."""
	if data[:8] != b'mozLz40\0':
		raise ValueError('not a mozlz4 file')
	return lz4_block(data[12:], int.from_bytes(data[8:12], 'little'))


def _firefox_node(node):
	kind = node.get('type')
	if kind == 'text/x-moz-place':
		return address(node.get('title'), node.get('uri'), firefox_date(node.get('dateAdded'))) if keep(node.get('uri')) else None
	if kind == 'text/x-moz-place-container':
		item = folder(node.get('guid') or str(node.get('id')), node.get('title'))
		for child in node.get('children') or []:
			if isinstance(child, dict):
				converted = _firefox_node(child)
				if converted is not None:
					item['children'].append(converted)
		return item
	return None


def _firefox_roots(by_guid):
	result = []
	for guid in FIREFOX_ORDER:
		item = by_guid.get(guid)
		if item is not None:
			item['key'] = guid
			item['role'] = FIREFOX_ROOTS[guid]
			result.append(item)
	return result


def parse_firefox_json(data):
	"""The main folders of a Firefox bookmarks backup (JSON text or bytes, already decompressed)."""
	if isinstance(data, bytes):
		data = data.decode('utf-8')
	root = json.loads(data)
	by_guid = {}
	for child in root.get('children') or []:
		if isinstance(child, dict) and child.get('guid') in FIREFOX_ROOTS:
			by_guid[child['guid']] = _firefox_node(child)
	return _firefox_roots(by_guid)


def parse_firefox_places(path):
	"""The main folders of a copy of places.sqlite. Needs Python's sqlite3, which NVDA does not ship."""
	import sqlite3
	connection = sqlite3.connect(path)
	try:
		rows = connection.execute(
			'SELECT b.id, b.type, b.parent, b.title, b.guid, p.url, b.dateAdded FROM moz_bookmarks b '
			'LEFT JOIN moz_places p ON b.fk = p.id ORDER BY b.parent, b.position').fetchall()
	finally:
		connection.close()
	nodes, children = {}, {}
	for ident, kind, parent, title, guid, url, added in rows:
		if kind == 2:
			nodes[ident] = folder(guid or str(ident), title)
		elif kind == 1 and keep(url):
			nodes[ident] = address(title, url, firefox_date(added))
		else:
			continue
		children.setdefault(parent, []).append(ident)
	for parent, idents in children.items():
		if parent in nodes and nodes[parent]['kind'] == 'folder':
			nodes[parent]['children'] = [nodes[ident] for ident in idents]
	by_guid = {item['key']: item for item in nodes.values() if item['kind'] == 'folder' and item['key'] in FIREFOX_ROOTS}
	return _firefox_roots(by_guid)


def read_places_copy(profile_dir, work_dir=None):
	"""Copy places.sqlite and its -wal file (Firefox locks them while it runs), then read the copy."""
	source = os.path.join(profile_dir, 'places.sqlite')
	if not os.path.isfile(source):
		raise FileNotFoundError(source)
	folder_path = tempfile.mkdtemp(prefix='bookmarksAccess-', dir=work_dir)
	try:
		copy = os.path.join(folder_path, 'places.sqlite')
		shutil.copyfile(source, copy)
		if os.path.isfile(source + '-wal'):
			shutil.copyfile(source + '-wal', copy + '-wal')
		return parse_firefox_places(copy)
	finally:
		shutil.rmtree(folder_path, ignore_errors=True)


def newest_backup(profile_dir):
	backups = glob.glob(os.path.join(profile_dir, 'bookmarkbackups', '*.jsonlz4'))
	return max(backups, key=os.path.getmtime) if backups else None


def have_sqlite():
	try:
		import sqlite3  # noqa: F401
		return True
	except ImportError:
		return False


def read_firefox_profile(profile_dir, use_sqlite=None, work_dir=None):
	"""The main folders of a Firefox profile: places.sqlite when sqlite3 exists, else the newest backup."""
	if use_sqlite is None:
		use_sqlite = have_sqlite()
	if use_sqlite:
		try:
			return read_places_copy(profile_dir, work_dir)
		except Exception:
			pass
	backup = newest_backup(profile_dir)
	if backup is None:
		return []
	with open(backup, 'rb') as stream:
		return parse_firefox_json(mozlz4(stream.read()))


def firefox_profiles(appdata):
	"""[(key, folder, profile name)] of every Firefox profile."""
	base = os.path.join(appdata, 'Mozilla', 'Firefox')
	found, seen = [], set()
	parser = configparser.RawConfigParser()
	try:
		parser.read(os.path.join(base, 'profiles.ini'), encoding='utf-8')
	except (configparser.Error, OSError, UnicodeError):
		pass
	for section in parser.sections():
		if not section.lower().startswith('profile') or not parser.has_option(section, 'Path'):
			continue
		relative = parser.get(section, 'IsRelative', fallback='1') == '1'
		path = parser.get(section, 'Path').replace('/', os.sep)
		full = os.path.normpath(os.path.join(base, path) if relative else path)
		if os.path.isdir(full) and full.lower() not in seen:
			seen.add(full.lower())
			found.append((parser.get(section, 'Path'), full, parser.get(section, 'Name', fallback=os.path.basename(full))))
	for full in glob.glob(os.path.join(base, 'Profiles', '*')):
		if os.path.isdir(full) and os.path.normpath(full).lower() not in seen:
			seen.add(os.path.normpath(full).lower())
			found.append(('Profiles/' + os.path.basename(full), full, os.path.basename(full)))
	return found


def firefox_sources(appdata, use_sqlite=None, work_dir=None):
	sources = []
	for key, full, name in firefox_profiles(appdata):
		try:
			folders = read_firefox_profile(full, use_sqlite, work_dir)
		except Exception:
			continue
		sources.append(Source('firefox:' + key, 'Firefox', name, folders))
	return sources


# ---------------------------------------------------------------- all browsers

def all_sources(local_appdata=None, appdata=None, use_sqlite=None, work_dir=None):
	"""Every profile of every browser that has at least one address, with a label for each.

	A browser with one such profile is called by its own name ('Chrome'); with several,
	each profile adds its name ('Chrome (Work)').
	"""
	local_appdata = local_appdata or os.environ.get('LOCALAPPDATA', '')
	appdata = appdata or os.environ.get('APPDATA', '')
	sources = [s for s in chromium_sources(local_appdata) + firefox_sources(appdata, use_sqlite, work_dir) if s.count]
	for source in sources:
		same = [s for s in sources if s.browser == source.browser]
		source.label = source.browser if len(same) == 1 else f'{source.browser} ({source.profile})'
	return sources
