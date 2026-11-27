# BookmarksAccess: the add-on's own list of bookmarks, kept in one JSON file.
# Copyright (C) 2026 ReaperAccessible
# This add-on is free software, licensed under the terms of the GNU General Public License (version 3).
# See the file LICENSE for more details.
#
# No NVDA module is used, so this file can be tested on its own.

import datetime
import json
import os
import shutil
import unicodedata
import uuid
from urllib.parse import urlsplit, urlunsplit

FORMAT = 1


def new_id():
	return uuid.uuid4().hex[:12]


def normalize(url):
	"""The form used to tell whether two addresses are the same one."""
	url = (url or '').strip()
	try:
		parts = urlsplit(url)
	except ValueError:
		return url
	scheme, netloc, path = parts.scheme.lower(), parts.netloc.lower(), parts.path
	if scheme in ('http', 'https') and not path:
		path = '/'
	return urlunsplit((scheme, netloc, path, parts.query, parts.fragment))


def make_folder(name, source=None):
	node = {'type': 'folder', 'id': new_id(), 'name': name, 'children': []}
	if source:
		node['source'] = source
	return node


def make_address(name, url, added=None):
	"""`added`: when it was bookmarked ('2024-05-01T10:30:00'); now, if not given."""
	return {'type': 'url', 'id': new_id(), 'name': name or url, 'url': url.strip(),
		'added': added or datetime.datetime.now().isoformat(timespec='seconds')}


# The orders of the tree. In every one, the folders come first in each folder.
SORT_ORDERS = ('manual', 'name', 'name_reversed', 'newest', 'oldest', 'most_opened')


def name_key(node):
	"""Sort by name the way people expect: no difference between capitals, accents or not."""
	text = unicodedata.normalize('NFKD', (node.get('name') or node.get('url') or '').casefold())
	return ''.join(c for c in text if not unicodedata.combining(c))


def sorted_children(node, order='manual'):
	"""The children of a folder in the order shown: its folders first, then its addresses."""
	children = node.get('children', [])
	folders = [child for child in children if child['type'] == 'folder']
	addresses = [child for child in children if child['type'] != 'folder']
	if order == 'manual':
		return folders + addresses
	reverse = order == 'name_reversed'
	folders.sort(key=name_key, reverse=reverse)
	if order in ('name', 'name_reversed'):
		addresses.sort(key=name_key, reverse=reverse)
	elif order in ('newest', 'oldest'):
		addresses.sort(key=name_key)
		addresses.sort(key=lambda child: child.get('added') or '', reverse=order == 'newest')
	elif order == 'most_opened':
		addresses.sort(key=name_key)
		addresses.sort(key=lambda child: child.get('opened', 0), reverse=True)
	return folders + addresses


def walk_sorted(node, order='manual', depth=0):
	"""Like walk, in the order shown in the tree."""
	for child in sorted_children(node, order):
		yield child, node, depth
		if child['type'] == 'folder':
			yield from walk_sorted(child, order, depth + 1)


def walk(node, depth=0):
	"""(node, parent, depth) for every node under `node`, in the order of the list."""
	for child in node.get('children', []):
		yield child, node, depth
		if child['type'] == 'folder':
			yield from walk(child, depth + 1)


def count_addresses(node):
	if node['type'] == 'url':
		return 1
	return sum(1 for child, _parent, _depth in walk(node) if child['type'] == 'url')


class Store:
	def __init__(self, path):
		self.path = path
		self.data = self.empty()
		# How many addresses got an older date from the browsers at the last import.
		self.dates_changed = 0
		self.load()

	@staticmethod
	def empty():
		return {'format': FORMAT, 'root': {'type': 'folder', 'id': 'root', 'name': '', 'children': []},
			'imported': [], 'lastFolder': 'root'}

	# ------------------------------------------------------------ file

	def load(self):
		for path in (self.path, self.path + '.bak'):
			try:
				with open(path, encoding='utf-8') as stream:
					data = json.load(stream)
				if isinstance(data, dict) and isinstance(data.get('root'), dict):
					base = self.empty()
					base.update(data)
					self.data = base
					return True
			except (OSError, ValueError):
				continue
		return False

	def save(self):
		folder = os.path.dirname(self.path)
		if folder:
			os.makedirs(folder, exist_ok=True)
		temporary = self.path + '.tmp'
		with open(temporary, 'w', encoding='utf-8') as stream:
			json.dump(self.data, stream, ensure_ascii=False, indent=1)
		if os.path.isfile(self.path):
			shutil.copyfile(self.path, self.path + '.bak')
		os.replace(temporary, self.path)

	# ------------------------------------------------------------ reading

	@property
	def root(self):
		return self.data['root']

	def find(self, ident):
		"""(node, parent) for an id; the root has no parent."""
		if ident == 'root':
			return self.root, None
		for node, parent, _depth in walk(self.root):
			if node['id'] == ident:
				return node, parent
		return None, None

	def folders(self, exclude=None):
		"""(folder, depth) for the root and every folder, in the order of the list.

		`exclude`: a folder id left out with everything inside it (a folder cannot go into itself).
		"""
		result = [(self.root, -1)]
		skip_depth = None
		for node, _parent, depth in walk(self.root):
			if skip_depth is not None:
				if depth > skip_depth:
					continue
				skip_depth = None
			if node['type'] != 'folder':
				continue
			if node['id'] == exclude:
				skip_depth = depth
				continue
			result.append((node, depth))
		return result

	def path_of(self, ident):
		"""The names of the folders leading to a node, the root excluded."""
		names = []
		while ident and ident != 'root':
			node, parent = self.find(ident)
			if node is None:
				break
			names.insert(0, node['name'])
			ident = parent['id'] if parent else None
		return names

	def find_url(self, url):
		key = normalize(url)
		for node, parent, _depth in walk(self.root):
			if node['type'] == 'url' and normalize(node['url']) == key:
				return node, parent
		return None, None

	def is_inside(self, ident, folder_id):
		"""Whether the node `ident` is `folder_id` itself or somewhere inside it."""
		folder, _parent = self.find(folder_id)
		if folder is None:
			return False
		if folder['id'] == ident:
			return True
		return any(node['id'] == ident for node, _p, _d in walk(folder))

	# ------------------------------------------------------------ changes

	def _folder(self, ident):
		node, _parent = self.find(ident or 'root')
		if node is None or node['type'] != 'folder':
			node = self.root
		return node

	def add_folder(self, parent_id, name):
		node = make_folder(name.strip())
		self._folder(parent_id)['children'].append(node)
		return node

	def add_address(self, parent_id, name, url):
		node = make_address(name.strip(), url)
		folder = self._folder(parent_id)
		folder['children'].append(node)
		self.data['lastFolder'] = folder['id']
		return node

	def rename(self, ident, name):
		node, _parent = self.find(ident)
		if node is not None and ident != 'root':
			node['name'] = name.strip()
		return node

	def set_url(self, ident, url):
		node, _parent = self.find(ident)
		if node is not None and node['type'] == 'url':
			node['url'] = url.strip()
		return node

	def delete(self, ident):
		node, parent = self.find(ident)
		if node is None or parent is None:
			return None
		parent['children'].remove(node)
		return node

	def move(self, ident, folder_id):
		node, parent = self.find(ident)
		if node is None or parent is None or self.is_inside(folder_id, ident):
			return None
		target = self._folder(folder_id)
		parent['children'].remove(node)
		target['children'].append(node)
		return target

	# ------------------------------------------------------------ import

	def merge(self, sources, role_names=None):
		"""Add the addresses of the browsers that are not in the list yet.

		`sources`: importers.Source objects. Each browser profile gets a folder of its own, and the
		browser's folders are kept inside it. A folder already made by an earlier import is found
		again by its origin, even if it was renamed or moved since. An address already in the list,
		or imported once and deleted since, is never added again.
		Returns {source label: number of new addresses}.
		"""
		role_names = role_names or {}
		known = {normalize(node['url']) for node, _p, _d in walk(self.root) if node['type'] == 'url'}
		# An empty list is a fresh start: the addresses deleted before come back too.
		imported = set(self.data.get('imported') or []) if self.root['children'] else set()
		known |= imported
		by_source = {node['source']: node for node, _p, _d in walk(self.root) if node['type'] == 'folder' and node.get('source')}
		added = {}

		def ensure(key, parent_getter, name):
			node = by_source.get(key)
			if node is None:
				node = make_folder(name, key)
				parent_getter()['children'].append(node)
				by_source[key] = node
			return node

		def copy(items, prefix, getter, label):
			for item in items:
				if item['kind'] == 'url':
					key = normalize(item['url'])
					if key in known:
						continue
					getter()['children'].append(make_address(item['name'], item['url'], item.get('added')))
					known.add(key)
					imported.add(key)
					added[label] = added.get(label, 0) + 1
				else:
					key = f"{prefix}:{item['key']}"
					name = item['name'] or role_names.get(item.get('role'), '') or item['key']
					if item.get('role') in role_names:
						name = role_names[item['role']]
					copy(item['children'], prefix, lambda key=key, getter=getter, name=name: ensure(key, getter, name), label)

		for source in sources:
			top = lambda source=source: ensure(source.key, lambda: self.root, source.label)
			copy(source.folders, source.key, top, source.label)
		self.data['imported'] = sorted(imported)
		self.dates_changed = self.take_dates(sources)
		return added

	def take_dates(self, sources):
		"""Give the addresses already in the list the date they were bookmarked in a browser, when it is older.

		Returns the number of addresses whose date changed.
		"""
		dates = {}

		def gather(items):
			for item in items:
				if item['kind'] == 'url':
					if item.get('added'):
						key = normalize(item['url'])
						if key not in dates or item['added'] < dates[key]:
							dates[key] = item['added']
				else:
					gather(item['children'])

		for source in sources:
			gather(source.folders)
		changed = 0
		for node, _parent, _depth in walk(self.root):
			if node['type'] == 'url':
				date = dates.get(normalize(node['url']))
				if date and date < (node.get('added') or '9'):
					node['added'] = date
					changed += 1
		return changed
