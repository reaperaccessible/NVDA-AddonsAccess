# AgendaAccess: the reminders sent to a phone with ntfy (https://ntfy.sh), a free notification service.
# The phone runs the ntfy app, subscribed to a secret name; whoever knows the name receives its messages.
# Nothing is sent unless the user turns it on. This module does not touch NVDA, so it can be tested alone.

import re
import secrets
import string
import threading
import urllib.error
import urllib.request

DEFAULT_SERVER = 'https://ntfy.sh'
TIMEOUT = 15   # seconds
_NAME = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def valid_name(name):
	return bool(_NAME.match(name or ''))


def new_name():
	"""A name hard to guess: 'agenda-' and 12 letters and digits."""
	alphabet = string.ascii_lowercase + string.digits
	return 'agenda-' + ''.join(secrets.choice(alphabet) for _ in range(12))


def address(server, name):
	return (server or DEFAULT_SERVER).strip().rstrip('/') + '/' + name


def send(server, name, title, text, urgent=True):
	"""Send one notification. Returns None when sent, or the reason it was not (in English, for the log)."""
	if not valid_name(name):
		return 'invalid name'
	request = urllib.request.Request(address(server, name), data=text.encode('utf-8'), method='POST', headers={
		'Title': title,   # the add-on's name: plain letters, as HTTP headers need
		'Priority': 'high' if urgent else 'default',
		'Tags': 'alarm_clock',
		'User-Agent': 'AgendaAccess',
	})
	try:
		with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
			return None if 200 <= answer.status < 300 else 'HTTP %d' % answer.status
	except urllib.error.HTTPError as error:
		return 'HTTP %d' % error.code
	except Exception as error:
		return str(error) or error.__class__.__name__


def send_later(server, name, title, text, done=None):
	"""Send in the background: a slow connection never holds NVDA. done(reason or None) is called after."""
	def work():
		result = send(server, name, title, text)
		if done:
			done(result)
	threading.Thread(target=work, daemon=True, name='AgendaAccessPhone').start()
