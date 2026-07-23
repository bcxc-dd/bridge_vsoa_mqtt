from __future__ import annotations

#
# Copyright (c) 2024 ACOAUTO Team.
# All rights reserved.
#
# Detailed license information can be found in the LICENSE file.
#
# File: sslwork.py Vehicle SOA SSL work.
#
# Author: Han.hui <hanhui@acoinfo.com>
#

import select, time, ssl, threading

# Handshake node
class HandshakeNode:
	OK = 0
	ERROR = -1
	AGAIN = 1

	def __init__(self, s: ssl.SSLSocket, timeout: float, wakeup: callable, args) -> None:
		# Socket and handshake deadline
		self.s : ssl.SSLSocket = s
		self.fd: int           = s.fileno()

		# Set nonblock
		self.__old_sock_to = s.gettimeout()
		s.settimeout(0)

		# Status
		self.wakeup   = wakeup
		self.args     = args
		self.deadline = time.monotonic() + timeout
		self.status   = HandshakeNode.AGAIN

	# Do handshake
	def do_handshake(self) -> int:
		try:
			self.s.do_handshake()
		except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
			self.status = HandshakeNode.AGAIN
		except Exception as e:
			self.status = HandshakeNode.ERROR
			if self.s.context.handsake_error_log:
				print('<vsoa> SSL handshake failed:', e)
		else:
			self.status = HandshakeNode.OK

	# Restore timeout
	def restore_timeout(self) -> None:
		try:
			self.s.settimeout(self.__old_sock_to)
		except:
			pass

# All handshake waiting list
hs_list: list[HandshakeNode] = []

# Handshake waiting list lock
hs_lock = threading.Lock()

# Wait semaphore
hs_wsem = threading.Semaphore(value = 0)

# Handshake thread handle
hs_thread: threading.Thread = None

# Handshake very short wait
SSL_HS_WAIT_SHORT = 0.05

# Handshake thread
def __handshake_proc() -> None:
	global hs_list

	while True:
		if len(hs_list) == 0:
			hs_wsem.acquire()
			continue

		with hs_lock:
			if len(hs_list) == 0:
				continue
			fds = tuple(map(lambda n: n.fd, hs_list))

		# Select all handshake nodes
		try:
			select.select(fds, (), (), SSL_HS_WAIT_SHORT)
		except:
			pass

		rm: list[HandshakeNode] = []
		with hs_lock:
			now = time.monotonic()
			for node in hs_list:
				node.do_handshake()
				if node.status != HandshakeNode.AGAIN or now > node.deadline:
					rm.append(node)

			for node in rm:
				hs_list.remove(node)

		# Wakeup
		for node in rm:
			node.restore_timeout()
			node.wakeup(node.s, node.status == HandshakeNode.OK, *node.args)

# Handshake thread init
def __handshake_proc_init() -> None:
	global hs_thread

	if hs_thread is None:
		hs_thread = threading.Thread(daemon = True, target = __handshake_proc, name = 'vsoa_ssl_hs')
		hs_thread.start()

# Request SSL handshake
def request_ssl_handshake(s: ssl.SSLSocket, hs_timeout: float, wakeup: callable, args = ()) -> None:
	__handshake_proc_init()
	with hs_lock:
		node = HandshakeNode(s, hs_timeout, wakeup, args)
		hs_list.append(node)
		if len(hs_list) == 1:
			hs_wsem.release()

# Unrequest SSL handshake
def unrequest_ssl_handshake(s: ssl.SSLSocket) -> None:
	with hs_lock:
		for node in hs_list:
			if node.s == s:
				hs_list.remove(node)
				break

# Synchronous handshake
def do_ssl_handshake_sync(s: ssl.SSLSocket, hs_timeout: float) -> bool:
	time_left = hs_timeout
	while True:
		time_last = time.monotonic()

		try:
			s.do_handshake(block = False)
		except ssl.SSLWantReadError:
			select.select((s, ), (), (), time_left)
		except ssl.SSLWantWriteError:
			select.select((), (s, ), (), time_left)
		except Exception as e:
			if s.context.handsake_error_log:
				print('<vsoa> SSL handshake failed:', e)
				return False
		else:
			break

		time_diff = time.monotonic() - time_last
		if time_diff >= time_left:
			if s.context.handsake_error_log:
				print('<vsoa> SSL handshake failed: timedout')
				return False
		else:
			time_left -= time_diff

	# Handshake success
	return True

# Synchronous receive
def do_ssl_recv(s: ssl.SSLSocket, bufsize: int, timeout: float) -> bytes:
	time_left = timeout
	while True:
		time_last = time.monotonic()

		try:
			buf = s.recv(bufsize)
		except ssl.SSLWantReadError:
			select.select((s, ), (), (), time_left)
		else:
			break

		time_diff = time.monotonic() - time_last
		if time_diff >= time_left:
			raise TimeoutError('Timed out')
		else:
			time_left -= time_diff

	# Received
	return buf

# Create SSL context server
def create_ssl_context_server(sslopt: dict) -> ssl.SSLContext:
	ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
	ssl_ctx.load_cert_chain(sslopt['cert'], sslopt['key'], sslopt['passwd'] if 'passwd' in sslopt else None)

	if 'load_default_certs' not in sslopt or sslopt['load_default_certs']:
		ssl_ctx.load_default_certs()

	if 'ca_cert' in sslopt:
		# Mutual authentication
		ssl_ctx.load_verify_locations(sslopt['ca_cert'])

	if 'sni_callback' in sslopt:
		# SNI callback
		ssl_ctx.sni_callback = sslopt['sni_callback']

	if 'verify_mode' in sslopt:
		# Set verify mode
		ssl_ctx.verify_mode = sslopt['verify_mode']
	else:
		# Verify client cert optionally
		ssl_ctx.verify_mode = ssl.CERT_OPTIONAL

	if 'handsake_error_log' in sslopt:
		# Set handsake error log
		setattr(ssl_ctx, 'handsake_error_log', sslopt['handsake_error_log'])
	else:
		setattr(ssl_ctx, 'handsake_error_log', False)

	return ssl_ctx

# Create SSL context client
def create_ssl_context_client(sslopt: dict) -> tuple[ssl.SSLContext, str]:
	hostname = sslopt['hostname'] if 'hostname' in sslopt else None
	ssl_ctx  = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

	if 'load_default_certs' not in sslopt or sslopt['load_default_certs']:
		ssl_ctx.load_default_certs()

	if 'ca_cert' in sslopt:
		ssl_ctx.load_verify_locations(sslopt['ca_cert'])

	if 'cert' in sslopt:
		# Mutual authentication
		ssl_ctx.load_cert_chain(sslopt['cert'], sslopt['key'], sslopt['passwd'] if 'passwd' in sslopt else None)

	if 'verify_mode' in sslopt:
		if sslopt['verify_mode'] == ssl.CERT_NONE:
			# Disable hostname check
			ssl_ctx.check_hostname = False
		# Set verify mode
		ssl_ctx.verify_mode = sslopt['verify_mode']
	else:
		# Must verify server cert
		ssl_ctx.verify_mode = ssl.CERT_REQUIRED

	if 'handsake_error_log' in sslopt:
		# Set handsake error log
		setattr(ssl_ctx, 'handsake_error_log', sslopt['handsake_error_log'])
	else:
		setattr(ssl_ctx, 'handsake_error_log', False)

	return ssl_ctx, hostname
