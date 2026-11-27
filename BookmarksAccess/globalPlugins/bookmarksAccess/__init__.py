# NVDA Add-on: BookmarksAccess
# Gathers the bookmarks of Chrome, Edge, Brave and Firefox in one list of its own, organised in folders.
# Copyright (C) 2026 ReaperAccessible
# This add-on is free software, licensed under the terms of the GNU General Public License (version 3).
# See the file LICENSE for more details.

import os
import threading

import wx

import addonHandler
import api
import config
import globalPluginHandler
import globalVars
import gui
import treeInterceptorHandler
import ui
import winUser
from logHandler import log

from . import importers
from .store import SORT_ORDERS, Store, count_addresses, sorted_children, walk, walk_sorted
from .updater import Updater

addonHandler.initTranslation()

# The add-on's own name: never translated.
ADDON_NAME = 'BookmarksAccess'
CONFIG_SECTION = 'bookmarksAccess'
CONFIG_SPEC = {
	'copyAddress': 'boolean(default=true)',
	'checkForUpdates': 'boolean(default=true)',
	'sortOrder': 'string(default=manual)',
}


def sort_labels():
	"""The words of the list Sort by, in the order of SORT_ORDERS."""
	return [
		# Translators: A choice of the list Sort by: the order in which the user put the items.
		_('My order'),
		# Translators: A choice of the list Sort by.
		_('Name, from A to Z'),
		# Translators: A choice of the list Sort by.
		_('Name, from Z to A'),
		# Translators: A choice of the list Sort by: the addresses bookmarked last come first.
		_('Date added, newest first'),
		# Translators: A choice of the list Sort by.
		_('Date added, oldest first'),
		# Translators: A choice of the list Sort by: the addresses opened most often from BookmarksAccess come first.
		_('Most opened first'),
	]
# The programs whose current page can be added, by the name of their executable.
BROWSERS = ('chrome', 'msedge', 'brave', 'firefox')
# Between the folders of a path, as in "Chrome > Bookmarks bar". NVDA reads it as a short pause.
PATH_SEPARATOR = ' > '
# The keys that move in the tree: pressed without Control, they end the selection made with Control+arrows.
NAVIGATION_KEYS = (wx.WXK_UP, wx.WXK_DOWN, wx.WXK_LEFT, wx.WXK_RIGHT, wx.WXK_HOME, wx.WXK_END,
	wx.WXK_PAGEUP, wx.WXK_PAGEDOWN, wx.WXK_NUMPAD_UP, wx.WXK_NUMPAD_DOWN, wx.WXK_NUMPAD_LEFT,
	wx.WXK_NUMPAD_RIGHT, wx.WXK_NUMPAD_HOME, wx.WXK_NUMPAD_END, wx.WXK_NUMPAD_PAGEUP, wx.WXK_NUMPAD_PAGEDOWN)


def settings():
	return config.conf[CONFIG_SECTION]


def role_names():
	return {
		# Translators: The name given to the bar of bookmarks of a browser, once imported.
		'toolbar': _('Bookmarks bar'),
		# Translators: The name given to the menu of bookmarks of Firefox, once imported.
		'menu': _('Bookmarks menu'),
		# Translators: The name given to the other bookmarks of a browser, once imported.
		'other': _('Other bookmarks'),
		# Translators: The name given to the bookmarks of the phone, once imported.
		'mobile': _('Mobile bookmarks'),
	}


_store = None


def store():
	global _store
	if _store is None:
		_store = Store(os.path.join(globalVars.appArgs.configPath, 'bookmarksAccess.json'))
	return _store


def save():
	try:
		store().save()
	except OSError:
		log.error('BookmarksAccess: cannot save the list', exc_info=True)
		# Translators: Said when the list of bookmarks cannot be written to the disk.
		ui.message(_('The list could not be saved.'))


def edge_message(first, text):
	# Translators: Said when the up arrow is pressed on the first item of a list. {name} is that item.
	# Translators: Said when the down arrow is pressed on the last item of a list.
	return (_('First, {name}') if first else _('Last, {name}')).format(name=text)


def bind_list_edges(control):
	"""At the edges of a list box, say "First," or "Last," instead of nothing."""
	def on_key(event):
		key = event.GetKeyCode()
		count = control.GetCount()
		index = control.GetSelection()
		if not event.HasAnyModifiers() and count and index != wx.NOT_FOUND:
			if key in (wx.WXK_UP, wx.WXK_NUMPAD_UP) and index == 0:
				ui.message(edge_message(True, control.GetString(index)))
				return
			if key in (wx.WXK_DOWN, wx.WXK_NUMPAD_DOWN) and index == count - 1:
				ui.message(edge_message(False, control.GetString(index)))
				return
		event.Skip()
	control.Bind(wx.EVT_KEY_DOWN, on_key)


def folder_choices(exclude=None):
	"""(ids, texts) of the folders where something can go, the top of the list first."""
	ids, texts = [], []
	book = store()
	for folder, _depth in book.folders(exclude=exclude):
		ids.append(folder['id'])
		if folder['id'] == 'root':
			# Translators: The top of the list of bookmarks, outside any folder.
			texts.append(_('Top of the list'))
		else:
			texts.append(PATH_SEPARATOR.join(book.path_of(folder['id'])))
	return ids, texts


# ---------------------------------------------------------------- reading the current page

def current_page():
	"""(url, title) of the page shown in the browser that has the focus, or None.

	The address comes from the document itself, as NVDA sees it: the address bar is never touched.
	"""
	focus = api.getFocusObject()
	interceptor = getattr(focus, 'treeInterceptor', None)
	if interceptor is None:
		# The focus is outside the page (address bar, tabs…): take the visible page of this window.
		try:
			window = api.getForegroundObject().windowHandle
			for candidate in list(treeInterceptorHandler.runningTable):
				root = candidate.rootNVDAObject
				if winUser.getAncestor(root.windowHandle, winUser.GA_ROOT) != window:
					continue
				states = root.states
				if any(getattr(state, 'name', '') in ('INVISIBLE', 'OFFSCREEN') for state in states):
					continue
				interceptor = candidate
				break
		except Exception:
			log.debugWarning('BookmarksAccess: no page found in the foreground window', exc_info=True)
	if interceptor is None:
		return None
	root = interceptor.rootNVDAObject
	url = getattr(interceptor, 'documentConstantIdentifier', None)
	if not url:
		try:
			url = root.value
		except Exception:
			url = None
	if not url or not importers.keep(url):
		return None
	try:
		title = (root.name or '').strip()
	except Exception:
		title = ''
	return url.strip(), title


# ---------------------------------------------------------------- dialogs

class AddressDialog(wx.Dialog):
	"""Add an address: its name, the address, and the folder where it goes."""

	def __init__(self, parent, name='', url='', folder='root', focus_folder=False):
		# Translators: Title of the window to add an address.
		super().__init__(parent, title=_('Add'))
		self.ids, texts = folder_choices()
		sizer = wx.BoxSizer(wx.VERTICAL)
		helper = gui.guiHelper.BoxSizerHelper(self, sizer=sizer)
		# Translators: Label of the name of an address, in the window to add an address.
		self.name = helper.addLabeledControl(_('&Name:'), wx.TextCtrl, value=name, size=(420, -1))
		# Translators: Label of the address of a web page.
		self.url = helper.addLabeledControl(_('&Address:'), wx.TextCtrl, value=url, size=(420, -1))
		# Translators: Label of the list of folders, in the window to add an address.
		self.folder = helper.addLabeledControl(_('&Folder:'), wx.ListBox, choices=texts, size=(420, 200))
		self.folder.SetSelection(self.ids.index(folder) if folder in self.ids else 0)
		bind_list_edges(self.folder)
		# Translators: A button of the window to add an address: makes a new folder for it.
		new_folder = helper.addItem(wx.Button(self, label=_('New fol&der...')))
		new_folder.Bind(wx.EVT_BUTTON, self.on_new_folder)
		helper.addDialogDismissButtons(self.CreateButtonSizer(wx.OK | wx.CANCEL))
		outer = wx.BoxSizer(wx.VERTICAL)
		outer.Add(sizer, 1, wx.ALL | wx.EXPAND, 10)
		self.SetSizerAndFit(outer)
		self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
		(self.folder if focus_folder else self.name).SetFocus()

	def selected_folder(self):
		index = self.folder.GetSelection()
		return self.ids[index] if index != wx.NOT_FOUND else 'root'

	def on_new_folder(self, event):
		name = ask_text(self, _('New folder'), _('Name of the new folder:'), '')
		if not name:
			return
		node = store().add_folder(self.selected_folder(), name)
		save()
		self.ids, texts = folder_choices()
		self.folder.Set(texts)
		self.folder.SetSelection(self.ids.index(node['id']))
		self.folder.SetFocus()

	def on_ok(self, event):
		if not self.url.GetValue().strip():
			# Translators: Said when the address field is empty in the window to add an address.
			ui.message(_('Type the address first.'))
			self.url.SetFocus()
			return
		event.Skip()


class FolderDialog(wx.Dialog):
	"""Choose a folder: where to move an address or a folder."""

	def __init__(self, parent, title, exclude=None, current='root'):
		super().__init__(parent, title=title)
		self.ids, texts = folder_choices(exclude)
		sizer = wx.BoxSizer(wx.VERTICAL)
		helper = gui.guiHelper.BoxSizerHelper(self, sizer=sizer)
		# Translators: Label of the list of folders, in the window to move something.
		self.folder = helper.addLabeledControl(_('&Folder:'), wx.ListBox, choices=texts, size=(420, 240))
		self.folder.SetSelection(self.ids.index(current) if current in self.ids else 0)
		bind_list_edges(self.folder)
		helper.addDialogDismissButtons(self.CreateButtonSizer(wx.OK | wx.CANCEL))
		outer = wx.BoxSizer(wx.VERTICAL)
		outer.Add(sizer, 1, wx.ALL | wx.EXPAND, 10)
		self.SetSizerAndFit(outer)
		self.folder.Bind(wx.EVT_LISTBOX_DCLICK, lambda event: self.EndModal(wx.ID_OK))
		self.folder.SetFocus()

	def selected(self):
		index = self.folder.GetSelection()
		return self.ids[index] if index != wx.NOT_FOUND else None


class KeysDialog(wx.Dialog):
	def __init__(self, parent):
		# Translators: Title of the window that lists the keys of BookmarksAccess.
		super().__init__(parent, title=_('Keys of BookmarksAccess'))
		sizer = wx.BoxSizer(wx.VERTICAL)
		# Translators: Label of the list of keys.
		sizer.Add(wx.StaticText(self, label=_('&Keys of the window:')), 0, wx.ALL, 8)
		lines = [f'{text} ({key})' for _command, text, key in commands()]
		# The command of the web browsers, which works outside this window too.
		# Translators: The key that adds the current web page, in a web browser.
		lines.append(f"{_('Adds the current web page to BookmarksAccess, and copies its address')} ({_('NVDA+Control+Shift+U')})")
		self.list = wx.ListBox(self, choices=lines, size=(480, 240))
		self.list.SetSelection(0)
		bind_list_edges(self.list)
		sizer.Add(self.list, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 8)
		# Translators: The button that closes a window.
		close = wx.Button(self, wx.ID_CANCEL, label=_('&Close'))
		sizer.Add(close, 0, wx.ALL | wx.ALIGN_RIGHT, 8)
		self.SetSizerAndFit(sizer)
		self.list.SetFocus()


def ask_text(parent, title, label, value):
	dialog = wx.TextEntryDialog(parent, label, title, value)
	try:
		if dialog.ShowModal() == wx.ID_OK:
			return dialog.GetValue().strip()
		return None
	finally:
		dialog.Destroy()


def commands():
	"""(method, text, key) of every command of the window, in the order of the Applications menu.

	The text is the same in the menu, in the list of keys and in the manual.
	"""
	return [
		# Translators: A command of the window: opens the address in the web browser.
		('open', _('Open in the browser'), _('Enter')),
		# Translators: A command of the window.
		('add', _('Add an address'), _('Control+N')),
		# Translators: A command of the window.
		('new_folder', _('New folder'), _('Control+Shift+N')),
		# Translators: A command of the window: changes the name of a folder or of an address.
		('rename', _('Rename'), _('F2')),
		# Translators: A command of the window: changes the address of a web page.
		('edit_url', _('Edit the address'), _('Control+E')),
		# Translators: A command of the window: moves the item into another folder.
		('move', _('Move to a folder'), _('Control+M')),
		# Translators: A command of the window: adds the next item to the selection, to cut or delete several items at once.
		('select_next', _('Select the next item too'), _('Shift+Down arrow')),
		# Translators: A command of the window: adds the previous item to the selection.
		('select_previous', _('Select the previous item too'), _('Shift+Up arrow')),
		# Translators: A command of the window: takes the selected items, to put them into another folder with Paste.
		('cut', _('Cut'), _('Control+X')),
		# Translators: A command of the window: puts the items cut into the selected folder.
		('paste', _('Paste into this folder'), _('Control+V')),
		# Translators: A command of the window: copies the address to the clipboard.
		('copy', _('Copy the address'), _('Control+C')),
		# Translators: A command of the window.
		('delete', _('Delete'), _('Del')),
		# Translators: A command of the window: goes to the list that chooses the order of the tree.
		# Its key must use the letter given to the label "&Sort by:".
		('focus_sort', _('Sort by'), _('Alt+S')),
		# Translators: A command of the window: brings the bookmarks of Chrome, Edge, Brave and Firefox.
		('import_browsers', _('Import from the browsers'), _('Control+I')),
		# Translators: A command of the window: shows this list of keys.
		('show_keys', _('List of keys'), _('Control+Shift+H')),
		# Translators: A command of the window.
		('close', _('Close'), _('Escape')),
	]


# ---------------------------------------------------------------- the main window

class BookmarksWindow(wx.Dialog):
	def __init__(self, parent):
		super().__init__(parent, title=ADDON_NAME, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		self.items = {}
		sizer = wx.BoxSizer(wx.VERTICAL)
		# Translators: The first line of the window.
		sizer.Add(wx.StaticText(self, label=_('The Applications key shows the commands; Control+Shift+H, the list of keys.')), 0, wx.ALL, 10)
		# Translators: Label of the tree of folders and addresses.
		row = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: Label of the list that chooses the order of the tree. Its letter is the one of the command Sort by.
		row.Add(wx.StaticText(self, label=_('&Sort by:')), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
		self.sort = wx.Choice(self, choices=sort_labels())
		self.sort.SetSelection(SORT_ORDERS.index(self.order()))
		self.sort.Bind(wx.EVT_CHOICE, self.on_sort)
		row.Add(self.sort, 0)
		sizer.Add(row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
		sizer.Add(wx.StaticText(self, label=_('&Bookmarks:')), 0, wx.LEFT | wx.RIGHT, 10)
		self.tree = wx.TreeCtrl(self, size=(520, 360), style=wx.TR_HAS_BUTTONS | wx.TR_HIDE_ROOT | wx.TR_LINES_AT_ROOT | wx.TR_SINGLE)
		sizer.Add(self.tree, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
		buttons = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: A button of the window.
		for label, method in ((_('&Open'), 'open'), (_('&Add an address...'), 'add'),
				# Translators: A button of the window.
				(_('&Import from the browsers'), 'import_browsers'), (_('&Close'), 'close')):
			button = wx.Button(self, label=label)
			button.Bind(wx.EVT_BUTTON, lambda event, method=method: getattr(self, method)())
			buttons.Add(button, 0, wx.RIGHT, 8)
		sizer.Add(buttons, 0, wx.ALL, 10)
		# The ids selected with Control+arrows, in the order chosen; the ids cut with Control+X.
		self.marked = []
		self.cut_ids = []
		# The item where the selection started.
		self.anchor = None
		self.SetSizerAndFit(sizer)
		self.build()
		self.tree.Bind(wx.EVT_TREE_KEY_DOWN, self.on_tree_key)
		self.tree.Bind(wx.EVT_CONTEXT_MENU, self.on_menu)
		self.tree.Bind(wx.EVT_TREE_ITEM_ACTIVATED, lambda event: self.open())
		self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
		self.Bind(wx.EVT_CLOSE, self.on_close)
		self.importing = False

	# ------------------------------------------------------------ the tree

	def label(self, node):
		if node['type'] == 'url':
			text = node['name'] or node['url']
		elif not node['children']:
			# Translators: A folder with nothing inside, in the tree. {name} is its name.
			text = _('{name} (empty)').format(name=node['name'])
		else:
			text = node['name']
		if node['id'] in self.marked:
			# Translators: An item selected with Shift+arrows, in the tree. {name} is the item.
			return _('{name}, selected').format(name=text)
		return text

	@staticmethod
	def order():
		order = settings()['sortOrder']
		return order if order in SORT_ORDERS else 'manual'

	def build(self):
		# Emptying a tree whose item is selected gives the focus to the tree, out of the list Sort by
		# (measured with wxPython): nothing is selected while it is emptied.
		self.tree.UnselectAll()
		self.tree.DeleteAllItems()
		self.items = {'root': self.tree.AddRoot('')}
		self.sync()

	def rebuild(self, focus=True):
		"""Show the tree again in the order chosen, with the same folders open and the same item selected."""
		selected = self.selected()
		opened = [ident for ident, item in self.items.items() if ident != 'root' and item.IsOk()
			and self.tree.ItemHasChildren(item) and self.tree.IsExpanded(item)]
		self.build()
		for ident in opened:
			if ident in self.items:
				self.tree.Expand(self.items[ident])
		if selected is not None and selected['id'] in self.items:
			if focus:
				self.select(selected['id'])
			else:
				self.tree.SelectItem(self.items[selected['id']])
				self.tree.EnsureVisible(self.items[selected['id']])

	def on_sort(self, event):
		settings()['sortOrder'] = SORT_ORDERS[self.sort.GetSelection()]
		# The focus stays in the list Sort by: NVDA says the new choice, and nothing else.
		self.rebuild(focus=False)

	def focus_sort(self):
		self.sort.SetFocus()

	def sync(self):
		"""Add to the tree what is in the list and not in the tree yet, in its place, without moving the selection."""
		changed = set()
		order = self.order()
		for node, parent, _depth in walk_sorted(store().root, order):
			if node['id'] in self.items:
				continue
			position = 0
			for sibling in sorted_children(parent, order):
				if sibling is node:
					break
				if sibling['id'] in self.items:
					position += 1
			item = self.tree.InsertItem(self.items[parent['id']], position, self.label(node))
			self.tree.SetItemData(item, node['id'])
			self.items[node['id']] = item
			changed.add(parent['id'])
		for ident in changed:
			self.refresh(ident)

	def refresh(self, ident):
		node, _parent = store().find(ident)
		if node is not None and ident != 'root' and ident in self.items:
			self.tree.SetItemText(self.items[ident], self.label(node))

	def forget(self, node):
		"""Remove a node and everything inside it from the map of tree items."""
		self.items.pop(node['id'], None)
		for child, _p, _d in walk(node):
			self.items.pop(child['id'], None)

	def selected(self):
		item = self.tree.GetSelection()
		if not item.IsOk():
			return None
		node, _parent = store().find(self.tree.GetItemData(item))
		return node

	def select(self, ident):
		item = self.items.get(ident)
		if item is not None and item.IsOk():
			parent = self.tree.GetItemParent(item)
			while parent.IsOk() and parent != self.tree.GetRootItem():
				self.tree.Expand(parent)
				parent = self.tree.GetItemParent(parent)
			self.tree.SelectItem(item)
			self.tree.EnsureVisible(item)
		self.tree.SetFocus()

	def select_start(self):
		root = self.tree.GetRootItem()
		first, _cookie = self.tree.GetFirstChild(root)
		if first.IsOk():
			self.tree.SelectItem(first)
		self.tree.SetFocus()

	def target_folder(self):
		"""The folder where something new goes: the selected folder, or the folder of the selected address."""
		node = self.selected()
		if node is None:
			return 'root'
		if node['type'] == 'folder':
			return node['id']
		_node, parent = store().find(node['id'])
		return parent['id'] if parent else 'root'

	def last_visible(self):
		item, _cookie = self.tree.GetFirstChild(self.tree.GetRootItem())
		if not item.IsOk():
			return item
		item = self.tree.GetLastChild(self.tree.GetRootItem())
		while self.tree.IsExpanded(item) and self.tree.GetChildrenCount(item, False):
			item = self.tree.GetLastChild(item)
		return item

	def on_tree_key(self, event):
		key = event.GetKeyCode()
		item = self.tree.GetSelection()
		if item.IsOk() and not event.GetKeyEvent().HasAnyModifiers():
			if key in (wx.WXK_UP, wx.WXK_NUMPAD_UP):
				first, _cookie = self.tree.GetFirstChild(self.tree.GetRootItem())
				if item == first:
					ui.message(edge_message(True, self.tree.GetItemText(item)))
					return
			elif key in (wx.WXK_DOWN, wx.WXK_NUMPAD_DOWN):
				if item == self.last_visible():
					ui.message(edge_message(False, self.tree.GetItemText(item)))
					return
		event.Skip()

	# ------------------------------------------------------------ keys and menu

	def on_key(self, event):
		if self.FindFocus() is not self.tree:
			if event.GetKeyCode() == wx.WXK_ESCAPE:
				self.close()
				return
			event.Skip()
			return
		key = event.GetKeyCode()
		control, shift, alt = event.ControlDown(), event.ShiftDown(), event.AltDown()
		if self.marked and not control and not shift and not alt and key in NAVIGATION_KEYS:
			# Moving with the arrows alone ends the selection, as in the lists of Windows.
			self.clear_marks()
		table = {
			(wx.WXK_DOWN, False, True): 'select_next', (wx.WXK_NUMPAD_DOWN, False, True): 'select_next',
			(wx.WXK_UP, False, True): 'select_previous', (wx.WXK_NUMPAD_UP, False, True): 'select_previous',
			(ord('X'), True, False): 'cut', (ord('V'), True, False): 'paste',
			(wx.WXK_RETURN, False, False): 'open', (wx.WXK_NUMPAD_ENTER, False, False): 'open',
			(ord('N'), True, False): 'add', (ord('N'), True, True): 'new_folder',
			(wx.WXK_F2, False, False): 'rename', (ord('E'), True, False): 'edit_url',
			(ord('M'), True, False): 'move', (ord('C'), True, False): 'copy',
			(wx.WXK_DELETE, False, False): 'delete', (wx.WXK_NUMPAD_DELETE, False, False): 'delete',
			(ord('I'), True, False): 'import_browsers', (ord('H'), True, True): 'show_keys',
			(wx.WXK_F1, False, False): 'show_keys', (wx.WXK_ESCAPE, False, False): 'close',
		}
		method = None if alt else table.get((key, control, shift))
		if method is None:
			event.Skip()
			return
		getattr(self, method)()

	def on_menu(self, event):
		menu = wx.Menu()
		node = self.selected()
		for method, text, key in commands():
			item = menu.Append(wx.ID_ANY, f'{text} ({key})')
			menu.Bind(wx.EVT_MENU, lambda event, method=method: wx.CallAfter(getattr(self, method)), item)
			if node is None and method in ('open', 'rename', 'edit_url', 'move', 'copy', 'delete', 'select_next', 'select_previous', 'cut'):
				item.Enable(False)
			elif method == 'paste' and not self.cut_ids:
				item.Enable(False)
			elif node is not None and node['type'] == 'folder' and method in ('edit_url', 'copy'):
				item.Enable(False)
		self.tree.PopupMenu(menu)
		menu.Destroy()

	# ------------------------------------------------------------ commands

	def nothing_selected(self):
		# Translators: Said when a command needs an item and the list is empty.
		ui.message(_('The list is empty.'))

	def open(self):
		node = self.selected()
		if node is None:
			return self.nothing_selected()
		if node['type'] == 'folder':
			item = self.items[node['id']]
			self.tree.Toggle(item)
			return
		try:
			os.startfile(node['url'])
		except OSError:
			log.error('BookmarksAccess: cannot open %s' % node['url'], exc_info=True)
			# Translators: Said when the web browser cannot open an address.
			ui.message(_('This address could not be opened.'))
			return
		# Counted for the order Most opened first; the tree is not reordered now, so the focus stays put.
		node['opened'] = node.get('opened', 0) + 1
		save()

	def add(self, name='', url='', focus_folder=False):
		folder = self.target_folder() if not focus_folder else store().data.get('lastFolder') or 'root'
		dialog = AddressDialog(self, name, url, folder, focus_folder)
		try:
			result = dialog.ShowModal()
			self.sync()
			if result != wx.ID_OK:
				if self.tree.GetSelection().IsOk():
					self.tree.SetFocus()
				else:
					self.select_start()
				return
			node = store().add_address(dialog.selected_folder(), dialog.name.GetValue() or dialog.url.GetValue(), dialog.url.GetValue())
		finally:
			dialog.Destroy()
		save()
		self.sync()
		self.select(node['id'])

	def new_folder(self):
		# Translators: Title of the window to make a new folder.
		name = ask_text(self, _('New folder'), _('Name of the new folder:'), '')
		if not name:
			self.tree.SetFocus()
			return
		node = store().add_folder(self.target_folder(), name)
		save()
		self.sync()
		self.select(node['id'])

	def rename(self):
		node = self.selected()
		if node is None:
			return self.nothing_selected()
		# Translators: Title of the window to rename a folder or an address.
		name = ask_text(self, _('Rename'), _('New name:'), node['name'])
		if name:
			store().rename(node['id'], name)
			save()
			self.refresh(node['id'])
			if self.order() != 'manual':
				# The new name may have another place in the order.
				self.rebuild()
		self.tree.SetFocus()

	def edit_url(self):
		node = self.selected()
		if node is None:
			return self.nothing_selected()
		if node['type'] != 'url':
			# Translators: Said when a command for an address is used on a folder.
			ui.message(_('This is a folder, not an address.'))
			return
		# Translators: Title of the window to change an address.
		url = ask_text(self, _('Edit the address'), _('&Address:'), node['url'])
		if url:
			store().set_url(node['id'], url)
			save()
		self.tree.SetFocus()

	# ------------------------------------------------------------ several items at once

	def mark(self, ident):
		if ident and ident != 'root' and ident not in self.marked:
			self.marked.append(ident)
			self.refresh(ident)

	def clear_marks(self):
		marked, self.marked = self.marked, []
		self.anchor = None
		for ident in marked:
			self.refresh(ident)

	def shown_ids(self):
		"""The ids of the items as the tree shows them, from the top (the content of closed folders left out)."""
		ids = []
		item, _cookie = self.tree.GetFirstChild(self.tree.GetRootItem())
		while item.IsOk():
			ids.append(self.tree.GetItemData(item))
			item = self.next_item(item)
		return ids

	def next_item(self, item):
		"""The item below in the tree as it is shown (open folders included), or an invalid item."""
		if self.tree.IsExpanded(item) and self.tree.GetChildrenCount(item, False):
			child, _cookie = self.tree.GetFirstChild(item)
			return child
		root = self.tree.GetRootItem()
		while item.IsOk() and item != root:
			sibling = self.tree.GetNextSibling(item)
			if sibling.IsOk():
				return sibling
			item = self.tree.GetItemParent(item)
		return wx.TreeItemId()

	def previous_item(self, item):
		sibling = self.tree.GetPrevSibling(item)
		if not sibling.IsOk():
			parent = self.tree.GetItemParent(item)
			return parent if parent.IsOk() and parent != self.tree.GetRootItem() else wx.TreeItemId()
		while self.tree.IsExpanded(sibling) and self.tree.GetChildrenCount(sibling, False):
			sibling = self.tree.GetLastChild(sibling)
		return sibling

	def extend(self, forward):
		"""Shift+arrow or Control+arrow: select from the item where the selection started to the next one, and go there.

		As in the lists of Windows, going back the other way takes the last items out of the selection.
		"""
		item = self.tree.GetSelection()
		if not item.IsOk():
			return self.nothing_selected()
		current = self.tree.GetItemData(item)
		if not self.marked or self.anchor not in self.items:
			self.anchor = current
		following = self.next_item(item) if forward else self.previous_item(item)
		if not following.IsOk():
			self.mark(current)
			ui.message(edge_message(not forward, self.tree.GetItemText(item)))
			return
		shown = self.shown_ids()
		if self.anchor not in shown:
			# The start of the selection is now inside a closed folder: start again here.
			self.anchor = current
		target = self.tree.GetItemData(following)
		start, end = sorted((shown.index(self.anchor), shown.index(target)))
		old, self.marked = self.marked, shown[start:end + 1]
		# The new names are set before the focus moves: NVDA reads the item with its new name, in one voice.
		for ident in set(old) | set(self.marked):
			self.refresh(ident)
		self.tree.SelectItem(following)
		self.tree.EnsureVisible(following)

	def select_next(self):
		self.extend(True)

	def select_previous(self):
		self.extend(False)

	def chosen(self):
		"""The items a command acts on: those selected with Control+arrows, or else the focused one.

		In the order of the list; an item inside a chosen folder goes with its folder.
		"""
		if not self.marked:
			node = self.selected()
			return [node] if node is not None else []
		marked = set(self.marked)
		nodes, skip_depth = [], None
		for node, _parent, depth in walk(store().root):
			if skip_depth is not None:
				if depth > skip_depth:
					continue
				skip_depth = None
			if node['id'] in marked:
				nodes.append(node)
				if node['type'] == 'folder':
					skip_depth = depth
		return nodes

	def move_nodes(self, idents, target):
		"""Move the items into the folder `target`. Returns the ids really moved."""
		moved = []
		for ident in idents:
			node, parent = store().find(ident)
			if node is None or parent is None or parent['id'] == target or store().is_inside(target, ident):
				continue
			if store().move(ident, target) is None:
				continue
			item = self.items.get(ident)
			self.forget(node)
			if item is not None and item.IsOk():
				self.tree.Delete(item)
			self.refresh(parent['id'])
			moved.append(ident)
		if moved:
			save()
			self.marked = [ident for ident in self.marked if ident not in moved]
			self.sync()
			self.refresh(target)
		return moved

	def moved_message(self, count, target):
		folder, _parent = store().find(target)
		name = folder['name'] if folder is not None and folder['id'] != 'root' else _('Top of the list')
		if count == 1:
			# Translators: Said after an item has been moved. {folder} is the folder where it went.
			return _('Moved to {folder}').format(folder=name)
		# Translators: Said after several items have been moved. {folder} is the folder where they went.
		return _('{count} items moved to {folder}').format(count=count, folder=name)

	def cut(self):
		nodes = self.chosen()
		if not nodes:
			return self.nothing_selected()
		self.cut_ids = [node['id'] for node in nodes]
		if len(nodes) == 1:
			ui.message(_('Cut'))
		else:
			# Translators: Said after Control+X on several items. {count} is their number.
			ui.message(_('{count} items cut').format(count=len(nodes)))

	def paste(self):
		if not self.cut_ids:
			# Translators: Said when Control+V is pressed and nothing was cut.
			ui.message(_('Nothing to paste. Cut with Control+X first.'))
			return
		target = self.target_folder()
		# The focus stays where it is, so that NVDA says the message and nothing cuts it.
		moved = self.move_nodes(self.cut_ids, target)
		self.cut_ids = []
		self.clear_marks()
		if moved:
			ui.message(self.moved_message(len(moved), target))
		else:
			# Translators: Said when the items cut are already in the folder chosen, or would go into themselves.
			ui.message(_('Nothing moved: the items are already there.'))
		self.tree.SetFocus()

	def move(self):
		nodes = self.chosen()
		if not nodes:
			return self.nothing_selected()
		node = nodes[0]
		_node, parent = store().find(node['id'])
		if len(nodes) == 1:
			# Translators: Title of the window to move an item. {name} is the item moved.
			title = _('Move {name}').format(name=node['name'])
		else:
			# Translators: Title of the window to move several items. {count} is their number.
			title = _('Move {count} items').format(count=len(nodes))
		dialog = FolderDialog(self, title, exclude=node['id'] if len(nodes) == 1 and node['type'] == 'folder' else None,
			current=parent['id'] if parent else 'root')
		try:
			target = dialog.selected() if dialog.ShowModal() == wx.ID_OK else None
		finally:
			dialog.Destroy()
		if target is None:
			self.tree.SetFocus()
			return
		moved = self.move_nodes([item['id'] for item in nodes], target)
		self.clear_marks()
		if not moved:
			self.tree.SetFocus()
			return
		ui.message(self.moved_message(len(moved), target))
		self.select(moved[0])

	def copy(self):
		node = self.selected()
		if node is None:
			return self.nothing_selected()
		if node['type'] != 'url':
			ui.message(_('This is a folder, not an address.'))
			return
		if api.copyToClip(node['url']):
			# Translators: Said when the address has been copied to the clipboard.
			ui.message(_('Address copied'))

	def delete(self):
		nodes = self.chosen()
		if not nodes:
			return self.nothing_selected()
		node = nodes[0]
		if len(nodes) > 1:
			# Translators: The question asked before deleting several items. {count} is their number.
			question = _('Delete the {count} selected items, and everything inside the folders?').format(count=len(nodes))
		elif node['type'] == 'folder':
			count = count_addresses(node)
			# Translators: The question asked before deleting a folder. {name} is the folder, {count} the number of addresses inside.
			question = _('Delete the folder {name} and the {count} addresses inside it?').format(name=node['name'], count=count) if count \
				else _('Delete the folder {name}?').format(name=node['name'])
		else:
			# Translators: The question asked before deleting an address.
			question = _('Delete {name}?').format(name=node['name'])
		# Translators: Title of the question asked before deleting.
		answer = gui.messageBox(question, _('Delete'), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self)
		if answer != wx.YES:
			self.tree.SetFocus()
			return
		parents = set()
		for node in nodes:
			_node, parent = store().find(node['id'])
			if store().delete(node['id']) is None:
				continue
			gone = {node['id']} | {child['id'] for child, _p, _d in walk(node)}
			self.marked = [ident for ident in self.marked if ident not in gone]
			self.cut_ids = [ident for ident in self.cut_ids if ident not in gone]
			parents.add(parent['id'])
			item = self.items.get(node['id'])
			self.forget(node)
			if item is not None and item.IsOk():
				self.tree.Delete(item)
		save()
		self.clear_marks()
		# Translators: Said after something has been deleted.
		ui.message(_('Deleted'))
		for ident in parents:
			self.refresh(ident)
		self.tree.SetFocus()

	def import_browsers(self):
		if self.importing:
			return
		self.importing = True
		# Translators: Said when the import starts.
		ui.message(_('Importing...'))

		def work():
			try:
				sources = importers.all_sources()
			except Exception:
				log.error('BookmarksAccess: import failed', exc_info=True)
				sources = []
			wx.CallAfter(self.import_done, sources)
		threading.Thread(target=work, name='BookmarksAccess import', daemon=True).start()

	def import_done(self, sources):
		self.importing = False
		if not sources:
			# Translators: Said when no browser has any bookmark.
			ui.message(_('No bookmarks found in Chrome, Edge, Brave or Firefox.'))
			return
		added = store().merge(sources, role_names())
		if added or store().dates_changed:
			save()
			if store().dates_changed and self.order() in ('newest', 'oldest'):
				self.rebuild(focus=False)
			else:
				self.sync()
		total = sum(added.values())
		if not total:
			# Translators: Said when the import finds nothing that is not already in the list.
			ui.message(_('No new address.'))
		elif total == 1:
			# Translators: Said at the end of the import. {browsers} is a list such as "Chrome, Firefox".
			ui.message(_('1 new address, from {browsers}.').format(browsers=', '.join(added)))
		else:
			# Translators: Said at the end of the import.
			ui.message(_('{count} new addresses, from {browsers}.').format(count=total, browsers=', '.join(added)))
		if self.tree.GetSelection().IsOk():
			self.tree.SetFocus()
		else:
			self.select_start()

	def show_keys(self):
		dialog = KeysDialog(self)
		try:
			dialog.ShowModal()
		finally:
			dialog.Destroy()
		self.tree.SetFocus()

	def close(self):
		self.Close()

	def on_close(self, event):
		global _window
		_window = None
		self.Destroy()


_window = None


def open_window(add=None, select=None, offer_import=False):
	"""Open the window, or bring it back. `add`: (title, url) of a page to add; `select`: an id to show."""
	global _window
	if _window is None:
		gui.mainFrame.prePopup()
		try:
			_window = BookmarksWindow(gui.mainFrame)
			_window.Show()
			_window.Raise()
		finally:
			gui.mainFrame.postPopup()
	else:
		_window.Raise()
	window = _window
	if add is not None:
		wx.CallAfter(window.add, add[0], add[1], True)
	elif select is not None:
		window.select(select)
	elif window.selected() is None:
		window.select_start()
		if offer_import and not store().root['children']:
			wx.CallAfter(ask_first_import, window)
	else:
		window.tree.SetFocus()


def ask_first_import(window):
	# Translators: The question asked when the window opens and the list is empty.
	answer = gui.messageBox(_('Your list is empty. Import the bookmarks of your web browsers now?'),
		ADDON_NAME, wx.YES_NO | wx.ICON_QUESTION, window)
	if answer == wx.YES:
		window.import_browsers()
	window.tree.SetFocus()


# ---------------------------------------------------------------- settings

class AdvancedDialog(wx.Dialog):
	"""The settings most people never change, behind the button Advanced options."""

	def __init__(self, parent, values):
		# Translators: Title of the window of the advanced options.
		super().__init__(parent, title=_('Advanced options of BookmarksAccess'))
		sizer = wx.BoxSizer(wx.VERTICAL)
		helper = gui.guiHelper.BoxSizerHelper(self, sizer=sizer)
		# Translators: An advanced setting.
		self.copyAddress = helper.addItem(wx.CheckBox(self, label=_('&Copy the address to the clipboard when adding the current page')))
		self.copyAddress.SetValue(values['copyAddress'])
		helper.addDialogDismissButtons(self.CreateButtonSizer(wx.OK | wx.CANCEL))
		outer = wx.BoxSizer(wx.VERTICAL)
		outer.Add(sizer, 1, wx.ALL | wx.EXPAND, 10)
		self.SetSizerAndFit(outer)
		self.copyAddress.SetFocus()


class BookmarksAccessSettingsPanel(gui.settingsDialogs.SettingsPanel):
	# The add-on's own name: shown as a category of NVDA's Settings dialog, never translated.
	title = ADDON_NAME
	# Set by the global plugin; None on secure screens.
	updater = None

	def makeSettings(self, settingsSizer):
		helper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		self.checkForUpdates = helper.addItem(wx.CheckBox(self, label=_('Check for &updates automatically when NVDA starts')))
		self.checkForUpdates.SetValue(settings()['checkForUpdates'])
		self.checkNow = helper.addItem(wx.Button(self, label=_('Check for updates &now')))
		self.checkNow.Bind(wx.EVT_BUTTON, lambda event: self.updater.check(manual=True))
		self.checkNow.Enable(self.updater is not None)
		self.advanced = {'copyAddress': settings()['copyAddress']}
		# Translators: The button that opens the advanced options.
		advanced = helper.addItem(wx.Button(self, label=_('&Advanced options...')))
		advanced.Bind(wx.EVT_BUTTON, self.on_advanced)

	def on_advanced(self, event):
		dialog = AdvancedDialog(self, self.advanced)
		try:
			if dialog.ShowModal() == wx.ID_OK:
				self.advanced['copyAddress'] = dialog.copyAddress.GetValue()
		finally:
			dialog.Destroy()

	def onSave(self):
		settings()['checkForUpdates'] = self.checkForUpdates.GetValue()
		settings()['copyAddress'] = self.advanced['copyAddress']


# ---------------------------------------------------------------- the global plugin

class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	# The add-on's own category in NVDA's Input Gestures dialog, never translated.
	scriptCategory = ADDON_NAME

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		config.conf.spec[CONFIG_SECTION] = CONFIG_SPEC
		self.menuItem = None
		self.updater = None
		if globalVars.appArgs.secure:
			return
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(BookmarksAccessSettingsPanel)
		try:
			tools = gui.mainFrame.sysTrayIcon.toolsMenu
			self.menuItem = tools.Append(wx.ID_ANY, ADDON_NAME + '…')
			gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, lambda event: wx.CallAfter(open_window, offer_import=True), self.menuItem)
		except Exception:
			log.error('BookmarksAccess: cannot add the Tools menu entry', exc_info=True)
		self.updater = Updater(addonHandler.getCodeAddon())
		BookmarksAccessSettingsPanel.updater = self.updater
		if settings()['checkForUpdates']:
			self.updater.scheduleStartupCheck()

	def script_addCurrentPage(self, gesture):
		if globalVars.appArgs.secure:
			return
		focus = api.getFocusObject()
		app = (getattr(getattr(focus, 'appModule', None), 'appName', '') or '').lower()
		if app not in BROWSERS:
			# Translators: Said when the command to add the current page is used outside a web browser.
			ui.message(_('Not in a web browser.'))
			return
		page = current_page()
		if page is None:
			# Translators: Said when no web page can be found in the browser.
			ui.message(_('No web page found. Move to the page first.'))
			return
		url, title = page
		copied = settings()['copyAddress'] and api.copyToClip(url)
		existing, _parent = store().find_url(url)
		if existing is not None:
			# Translators: Said when the current page is already in the list; its address was copied.
			ui.message(_('Address copied. Already in your bookmarks.') if copied else _('Already in your bookmarks.'))
			wx.CallAfter(open_window, select=existing['id'])
			return
		if copied:
			ui.message(_('Address copied'))
		wx.CallAfter(open_window, add=(title or url, url))
	# Translators: Description of a command, in NVDA's Input Gestures dialog.
	script_addCurrentPage.__doc__ = _('Adds the current web page to BookmarksAccess, and copies its address')

	def script_openWindow(self, gesture):
		if globalVars.appArgs.secure:
			return
		wx.CallAfter(open_window, offer_import=True)
	# Translators: Description of a command, in NVDA's Input Gestures dialog.
	script_openWindow.__doc__ = _('Opens BookmarksAccess, your list of bookmarks')

	def terminate(self, *args, **kwargs):
		if self.updater is not None:
			self.updater.cancel()
		if _window:
			_window.Close()
		try:
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(BookmarksAccessSettingsPanel)
		except ValueError:
			pass
		if self.menuItem is not None:
			try:
				gui.mainFrame.sysTrayIcon.toolsMenu.Remove(self.menuItem)
			except Exception:
				pass
		super().terminate(*args, **kwargs)

	__gestures = {
		'kb:NVDA+control+shift+u': 'addCurrentPage',
	}
