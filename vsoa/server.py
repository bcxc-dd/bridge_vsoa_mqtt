from __future__ import annotations

#
# Copyright (c) 2024 ACOAUTO Team.
# All rights reserved.
#
# Detailed license information can be found in the LICENSE file.
#
# File: server.py Vehicle SOA server.
#
# Author: Han.hui <hanhui@acoinfo.com>
#

import vsoa.parser as parser
import vsoa.sockopt as sockopt
import vsoa.interface as interface
import vsoa.workqueue as workqueue
import time, struct, threading, socket, ssl, select
from vsoa.sslwork import create_ssl_context_server, request_ssl_handshake, unrequest_ssl_handshake

# VSOA server backlog
VSOA_SERVER_BACKLOG = 32

# Server default send timeout (s)
SERVER_DEF_SEND_TIMEOUT = 0.1

# Server default handshake timeout (s)
SERVER_DEF_HANDSHAKE_TIMEOUT = 5.0

# Client SSL handshake timeout (s)
SERVER_SSL_HS_TIMEOUT = 1.0

# Server timer period (ms)
SERVER_TIMER_PERIOD = 100

# Server Stream
class Stream:
	def __init__(self, server: object, family: int, host: str, onlink: callable, ondata: callable, timeout: float, sslopt: dict) -> None:
		self.onlink = onlink
		self.ondata = ondata
		self._alive = int(timeout * 1000)

		# Stream socket
		self.__server  = server
		self.__listen  = sockopt.create(family, 'tcp', server = True)
		self.__clisock : socket.socket | ssl.SSLSocket = None

		# Send timeout, initialize to blocking send
		self.__sendto = -1

		# SSL context and handshake status
		if sslopt:
			self.__ssl_ctx = create_ssl_context_server(sslopt)
			self.__ssl_hss = None
		else:
			self.__ssl_ctx = self.__ssl_hss = None

		# Initialize
		self.__listen.bind((host, 0))
		self.__listen.listen(1)

		# Tunid
		_, self.__tunid = self.__listen.getsockname()

		# Add to server stream list
		with server._lock:
			server._streams.append(self)
			sockopt.event_emit(server._strevt[1])

	# Get tunid
	@property
	def tunid(self) -> int:
		return self.__tunid

	@property
	def connected(self) -> bool:
		return True if self.__clisock else False

	# Get socket need to monitor
	def _sock(self) -> socket.socket | ssl.SSLSocket:
		return self.__listen if self.__listen else self.__clisock

	# SSL handshake wakeup
	def __ssl_hs_wakeup(self, s: ssl.SSLSocket, success: bool) -> None:
		call_onlink = False

		with self.__server._lock:
			if self.__ssl_hss:
				self.__clisock = s
				self.__ssl_hss = None

				if success:
					self.__clisock.setblocking(True)
					sockopt.event_emit(self.__server._strevt[1])
					call_onlink = True
				else:
					sockopt.tcpshutdown(s)

		if call_onlink and callable(self.onlink):
			self.onlink(self, True)

	# Stream remove
	def __remove(self) -> None:
		with self.__server._lock:
			if self.__listen:
				self.__listen.close()
				self.__listen = None

			if self.__clisock:
				self.__clisock.close()
				self.__clisock = None

			if self.__ssl_hss:
				unrequest_ssl_handshake(self.__ssl_hss)
				self.__ssl_hss = None

			if self in self.__server._streams:
				self.__server._streams.remove(self)

		if callable(self.onlink):
			self.onlink(self, False)

	# Stream event
	def _event(self, sock: socket.socket) -> None:
		cli_sock: socket.socket | ssl.SSLSocket = None

		with self.__server._lock:
			if sock == self.__listen:
				cli_sock, _ = self.__listen.accept()
				assert(cli_sock)
				self.__listen.close()
				self.__listen = None

				if self.__ssl_ctx:
					cli_sock = self.__ssl_ctx.wrap_socket(cli_sock, server_side = True, do_handshake_on_connect = False)
					self.__ssl_hss = cli_sock
					request_ssl_handshake(cli_sock, SERVER_SSL_HS_TIMEOUT, self.__ssl_hs_wakeup)

				else:
					self.__clisock = cli_sock
					self.__clisock.setblocking(True)

		if cli_sock:
			if not self.__ssl_ctx and callable(self.onlink):
				self.onlink(self, True)
			return

		if sock == self.__clisock:
			try:
				data = self.__clisock.recv(parser.VSOA_MAX_PACKET_LENGTH)
			except ssl.SSLWantReadError:
				data = True
			except:
				data = None

			if data and type(data) == bytes:
				if callable(self.ondata):
					self.ondata(self, data)
			elif not data:
				self.__remove()

	# Close stream (will be called automatically when disconnecting)
	def close(self) -> None:
		with self.__server._lock:
			if self.__clisock:
				sockopt.tcpshutdown(self.__clisock)
				return

		if self.__listen:
			self.__remove()

	# Send data
	def send(self, data: bytearray | bytes) -> int:
		if self.__clisock == None:
			if self.__listen:
				raise Exception('Client not connected')
			else:
				raise Exception('Client has closed')

		return sockopt.tcpsend(self.__clisock, data, len(data), self.__sendto)

	# Set client keepalive
	def keepalive(self, idle: int) -> None:
		if self.__clisock:
			sockopt.keepalive(self.__clisock, int(idle))

	# Set send timeout
	def sendtimeout(self, timeout: float) -> None:
		if self.__clisock == None:
			raise Exception('Client not connected')

		self.__sendto = timeout

# Remote Client
class Client:
	def __init__(self, sock: socket.socket | ssl.SSLSocket, server: object, chost: str, cid: int, raw: bool = False) -> None:
		self.authed = False

		# Protected prop
		self._hsalive = int(SERVER_DEF_HANDSHAKE_TIMEOUT * 1000)
		self._qaddr   = None
		self._chost   = chost
		self._prio    = 0
		self._active  = False
		self._onconn  = False

		# Private prop
		self.__close    = False
		self.__cid      = cid
		self.__caddr    = sock.getpeername()
		self.__server   = server
		self.__unpacker = parser.Unpacker(raw = raw)
		self.__subs     : set[str] = set()

		# Socket protected
		self._sock: socket.socket | ssl.SSLSocket = sock
		self.__is_ssl = isinstance(sock, ssl.SSLSocket)

		# Socket sendtimeout
		self.__sendto = SERVER_DEF_SEND_TIMEOUT

		# User hooks
		self.onsubscribe   = lambda cli, topics: None
		self.onunsubscribe = lambda cli, topics: None

	# Client finish
	def _finish(self) -> None:
		self._sock.setblocking(True)
		self._sock.close()
		self._sock   = None
		self.__close = True

	# Send packet
	def _psend(self, packer: parser.Packer | bytearray, quick: bool) -> bool:
		if quick and self.__is_ssl:
			return False

		total = 0
		if type(packer) == parser.Packer:
			packet, plen = packer.packet()
		else:
			packet = packer
			plen   = len(packet)
		try:
			if quick:
				if self._qaddr:
					total = self.__server._quick.sendto(packet, socket.MSG_NOSIGNAL, self._qaddr)
				else:
					total = 0
			else:
				total = sockopt.tcpsend(self._sock, packet, plen, self.__sendto)
		except:
			return False
		else:
			return True if total == plen else False

	# Receive packet
	def _precv(self, packet: bytes) -> bool:
		return self.__unpacker.input(packet, self._pinput)

	# Packet input
	def _pinput(self, header: interface.Header, url: str, param: dict | list | bytes, data: bytes, quick: bool = False) -> None:
		ptype = header.type
		if ptype == parser.VSOA_TYPE_NOOP or header.flags & parser.VSOA_FLAG_REPLY:
			return

		packer  = self.__server._packer
		payload = interface.Payload(param, data)

		if not self._active:
			if self.__server._passwd:
				if type(param) == dict and ('passwd' in param) and param['passwd'] == self.__server._passwd:
					self.authed = self._active = True
				else:
					with self.__server._lock:
						packer.header(ptype, parser.VSOA_FLAG_REPLY, parser.VSOA_STATUS_PASSWORD, header.seqno)
						self._psend(packer, False)
					return
			else:
				self.authed = self._active = True

		if ptype == parser.VSOA_TYPE_DATAGRAM:
			self.__server.ondata(self, url, payload, quick)

		elif ptype == parser.VSOA_TYPE_SERVINFO:
			with self.__server._lock:
				if header.flags & parser.VSOA_FLAG_TUNNEL:
					self._qaddr = (self._chost, header.tunid)

				packer.header(ptype, parser.VSOA_FLAG_REPLY, 0, header.seqno)
				packer.payload(None, param = self.__server._info, data = struct.pack('>I', self.__cid))
				self._psend(packer, False)
				self.__server._hssuccess(self)

			if not self._onconn:
				self._onconn = True
				self.__server.onclient(self, True)

		elif ptype == parser.VSOA_TYPE_RPC:
			if not url or not url.startswith('/'):
				with self.__server._lock:
					packer.header(ptype, parser.VSOA_FLAG_REPLY, parser.VSOA_STATUS_ARGUMENTS, header.seqno)
					self._psend(packer, False)
			else:
				func, wq = self.__server._cmdfunc(url)
				if callable(func):
					args = (self, interface.Request(url, header.seqno, 1 if header.flags & parser.VSOA_FLAG_SET else 0), payload)
					if isinstance(wq, workqueue.WorkQueue):
						wq.add(func, args = args)
					else:
						func(*args)
				else:
					with self.__server._lock:
						packer.header(ptype, parser.VSOA_FLAG_REPLY, parser.VSOA_STATUS_INVALID_URL, header.seqno)
						self._psend(packer, False)

		elif ptype == parser.VSOA_TYPE_SUBSCRIBE:
			topics = []
			if url and url.startswith('/'):
				self.__subs.add(url)
				topics.append(url)
				status = 0
			elif url is None and type(param) == list:
				for sub in param:
					if type(sub) == str and sub.startswith('/'):
						self.__subs.add(sub)
						topics.append(url)
				status = 0
			else:
				status = parser.VSOA_STATUS_ARGUMENTS
			with self.__server._lock:
				packer.header(ptype, parser.VSOA_FLAG_REPLY, status, header.seqno)
				self._psend(packer, False)
			if status == 0:
				self.onsubscribe(self, topics)

		elif ptype == parser.VSOA_TYPE_UNSUBSCRIBE:
			topics = []
			if url and url.startswith('/'):
				if url in self.__subs:
					self.__subs.remove(url)
					topics.append(url)
				status = 0
			elif url is None and type(param) == list:
				for sub in param:
					if type(sub) == str and sub.startswith('/') and sub in self.__subs:
						self.__subs.remove(sub)
						topics.append(url)
				status = 0
			else:
				status = parser.VSOA_STATUS_ARGUMENTS
			with self.__server._lock:
				packer.header(ptype, parser.VSOA_FLAG_REPLY, status, header.seqno)
				self._psend(packer, False)
			if status == 0:
				self.onunsubscribe(self, topics)

		elif ptype == parser.VSOA_TYPE_PINGECHO:
			with self.__server._lock:
				packer.header(ptype, parser.VSOA_FLAG_REPLY, 0, header.seqno)
				self._psend(packer, False)

	# client.id
	@property
	def id(self) -> int:
		return self.__cid

	# client.prio
	@property
	def priority(self) -> int:
		return self._prio

	@priority.setter
	def priority(self, nprio: int) -> None:
		if nprio < 0 or nprio > 7:
			raise ValueError('Priority must in 0 ~ 7')

		packer = self.__server._packer
		sockopt.priority(self._sock, nprio)

		with self.__server._lock:
			self._prio = nprio
			prio_list  = self.__server._priocli
			if self in prio_list:
				prio_list.remove(self)
				for i in range(0, len(prio_list)):
					cli = prio_list[i]
					if self._prio > cli._prio:
						prio_list.insert(i, self)
						break
				else:
					prio_list.append(self)

			packer.header(parser.VSOA_TYPE_QOSSETUP, parser.VSOA_FLAG_REPLY, nprio, 0)
			self._psend(packer, False)

	# Client close
	def close(self) -> None:
		sockopt.tcpshutdown(self._sock)
		self.__close = True

	# Get client address
	def address(self) -> tuple[str, int]:
		return self.__caddr

	# Get client whether closed
	def is_closed(self) -> bool:
		return self.__close

	# Whether the specified URL is subscribed.
	def is_subscribed(self, url: str) -> bool:
		if self._active and self.authed:
			for sub in self.__subs:
				if len(sub) == 1 or sub == url:
					return True
				elif sub.endswith('/'):
					sub = sub[0:-1]
					if url.startswith(sub):
						ulen = len(url)
						slen = len(sub)
						if ulen == slen or (ulen > slen and url[slen] == '/'):
							return True
		return False

	# Client reply
	def reply(self, seqno: int, payload: interface.Payload | dict = None, status: int = 0, tunid: int = 0) -> bool:
		if self.__close:
			return False

		packer = self.__server._packer

		with self.__server._lock:
			packer.header(parser.VSOA_TYPE_RPC, parser.VSOA_FLAG_REPLY, status, seqno)

			if tunid > 0 and tunid < 65536:
				packer.tunid(tunid)

			if payload and isinstance(payload, (object, dict)):
				packer.payload(payload)

			return self._psend(packer, False)

	# Send datagram to client
	def datagram(self, url: str, payload: interface.Payload | dict = None, quick: bool = False) -> bool:
		if self.__close:
			return False
		if quick and self.__is_ssl:
			return False

		packer = self.__server._packer

		with self.__server._lock:
			packer.header(parser.VSOA_TYPE_DATAGRAM, 0, 0, 0)
			packer.url(url)

			if payload and isinstance(payload, (object, dict)):
				packer.payload(payload)

			return self._psend(packer, quick)

	# Set client keepalive
	def keepalive(self, idle: int) -> None:
		if self._sock:
			sockopt.keepalive(self._sock, int(idle))

	# Set send timeout
	def sendtimeout(self, timeout: float) -> None:
		self.__sendto = timeout

	# Get peer certificate
	def getpeercert(self, binary: bool = False) -> dict | None:
		if self.__is_ssl and self._sock:
			return self._sock.getpeercert(binary_form = binary)
		else:
			return None

# Server class
class Server:
	# Server sub-class
	Client = Client
	Stream = Stream

	def __init__(self, info: dict | str = '', passwd: str = '', raw: bool = False) -> None:
		# Server address
		self.__addr = None
		self.__raw  = raw

		# Protected prop
		self._running = False
		self._info    = info
		self._passwd  = passwd
		self._packer  = parser.Packer()
		self._lock    = threading.Lock()
		self._priocli : list[Client] = []

		# Remote clients
		self.__ncid   = 0
		self.__cidtbl : dict[int, Client] = {}
		self.__hslist : list[Client]      = []

		# Server commands
		self.__cmds   : dict[str, tuple[callable, workqueue.WorkQueue]] = {}
		self.__wccmds : dict[str, tuple[callable, workqueue.WorkQueue]] = {}

		# Server streams
		self._streams : list[Stream] = []
		self._strevt  = sockopt.pair()

		# Server hooks and send timeout
		self.ondata   = lambda cli, url, payload, quick: None
		self.onclient = lambda cli, connect: None

		# Server sockets
		self.__family = 0
		self.__listen : socket.socket = None
		self._quick   : socket.socket = None
		self.__sendto = SERVER_DEF_SEND_TIMEOUT

		# SSL options, context and handshake requesting list
		self.__ssl_opt   : dict           = None
		self.__ssl_ctx   : ssl.SSLContext = None
		self.__ssl_hslist: list[dict]     = []

		# Add this server to servers list
		with server_lock:
			server_list.append(self)

	# Generate new client id
	def __newcid(self) -> int:
		ncid = 0
		while True:
			ncid = self.__ncid
			self.__ncid += 1
			if ncid not in self.__cidtbl:
				break
		return ncid

	# Start server
	def __start(self, host: str, port: int, sslopt: dict) -> None:
		family = socket.AF_INET if host.find(':') < 0 else socket.AF_INET6
		addr   = (host, port)

		if sslopt:
			self.__ssl_opt = sslopt
			self.__ssl_ctx = create_ssl_context_server(sslopt)
		else:
			self.__ssl_opt = self.__ssl_ctx = None

		self.__listen = sockopt.create(family, 'tcp', server = True)
		self.__listen.bind(addr)
		self.__listen.listen(VSOA_SERVER_BACKLOG)

		if not self.__ssl_ctx:
			self._quick = sockopt.create(family, 'udp', nonblock = False)
			self._quick.bind(addr)
		else:
			self._quick = None

		# Save server protocol family and address
		self.__family, self.__addr = family, addr

	# Add a new client
	def __add_client(self, sock: socket.socket | ssl.SSLSocket, cli_addr: str) -> None:
		cid = self.__newcid()
		cli = Client(sock, self, cli_addr, cid, self.__raw)
		cli.sendtimeout(self.__sendto)

		with self._lock:
			self.__cidtbl[cid] = cli
			self._priocli.append(cli)
			if not cli._active:
				self.__hslist.append(cli)

	# Client SSL handshake wakeup
	def __ssl_hs_wakeup(self, s: ssl.SSLSocket, success: bool) -> None:
		wakeup = False

		with self._lock:
			for ssl_hs_cli in self.__ssl_hslist:
				if ssl_hs_cli['ssock'] == s:
					ssl_hs_cli['success'] = success
					ssl_hs_cli['wakeup']  = wakeup = True
					break

		if wakeup:
			sockopt.event_emit(self._strevt[1])

	# Server handshake timer
	def _hstimer(self) -> None:
		with self._lock:
			for cli in self.__hslist:
				if cli._hsalive > SERVER_TIMER_PERIOD:
					cli._hsalive -= SERVER_TIMER_PERIOD
				else:
					cli._hsalive = 0
					cli.close()

		for stream in self._streams:
			if not stream.connected:
				if stream._alive > SERVER_TIMER_PERIOD:
					stream._alive -= SERVER_TIMER_PERIOD
				else:
					stream._alive = 0
					stream.close()

	# Client handshake success (with this server lock)
	def _hssuccess(self, cli: Client) -> None:
		if cli in self.__hslist:
			self.__hslist.remove(cli)

	# Get matched command function
	def _cmdfunc(self, url: str) -> tuple[callable, workqueue.WorkQueue]:
		if url in self.__cmds:
			return self.__cmds[url]
		for cmd in self.__wccmds:
			if url.startswith(cmd) and url[len(cmd)] == '/':
				return self.__wccmds[cmd]

		defs = '/'
		if defs in self.__wccmds:
			return self.__wccmds[defs]
		else:
			return None, None

	# Clients
	def clients(self) -> list:
		with self._lock:
			clis = self._priocli[:]
		return clis

	# Get address
	def address(self) -> tuple[str, int]:
		if self.__addr is None:
			raise Exception('Server not started')
		return self.__addr

	# Set server password
	def passwd(self, passwd: str) -> None:
		self._passwd = passwd

	# Server publish
	def publish(self, url: str, payload: interface.Payload | dict = None, quick: bool = False) -> bool:
		if not url.startswith('/'):
			raise Exception('URL must start with /')
		if not self._running:
			return False
		if quick and self.__ssl_ctx:
			return False

		with self._lock:
			self._packer.header(parser.VSOA_TYPE_PUBLISH, 0, 0, 0)
			self._packer.url(url)

			if payload and isinstance(payload, (object, dict)):
				self._packer.payload(payload)

			packet, _ = self._packer.packet()
			for cli in self._priocli:
				if cli.is_subscribed(url):
					cli._psend(packet, quick)
		return True

	# Whether the specified URL is subscribed.
	def is_subscribed(self, url: str) -> bool:
		with self._lock:
			for cli in self._priocli:
				if cli.is_subscribed(url):
					return True
		return False

	# Server command decorator
	def command(self, url: str, wq: workqueue.WorkQueue = None) -> callable:
		if not url.startswith('/'):
			raise ValueError('URL must start with /')

		def decorator(func: callable) -> callable:
			if url.endswith('/'):
				self.__wccmds[url] = (func, wq)
			else:
				self.__cmds[url] = (func, wq)
			return func

		return decorator

	# Set server send timout
	def sendtimeout(self, timeout: float, sync_to_clis: bool = True) -> None:
		with self._lock:
			self.__sendto = timeout
			if sync_to_clis:
				for cli in self._priocli:
					cli.sendtimeout(timeout)

	# Create server stream
	def create_stream(self, onlink: callable, ondata: callable = None, timeout: float = SERVER_DEF_HANDSHAKE_TIMEOUT) -> Stream:
		if not self._running:
			raise Exception('Server not run')
		if not callable(onlink):
			onlink = lambda s, l: None

		return Stream(self, self.__family, self.__addr[0], onlink, ondata, timeout, self.__ssl_opt)

	# Server event loop
	def run(self, host: str, port: int, sslopt: dict = None) -> None:
		self.__start(host, port, sslopt)
		self._running = True

		while True:
			rlist = [ self.__listen, self._strevt[0] ]
			if self._quick:
				rlist.append(self._quick)

			elist = []

			for cli in self._priocli:
				if self.__ssl_ctx and cli._sock.pending() > 0:
					elist.append(cli._sock)
				else:
					rlist.append(cli._sock)

			with self._lock:
				for stream in self._streams:
					sock = stream._sock()
					if sock:
						if isinstance(sock, ssl.SSLSocket) and sock.pending() > 0:
							elist.append(sock)
						else:
							rlist.append(sock)

			if len(elist) == 0:
				elist, _, _ = select.select(rlist, (), ())

			if self._quick in elist:
				buf = self._quick.recv(parser.VSOA_MAX_QPACKET_LENGTH)
				if buf:
					try:
						header, url, param, data = parser.Unpacker.pinput(buf, raw = self.__raw)
					except:
						pass
					else:
						if header.type == parser.VSOA_TYPE_DATAGRAM:
							cid = header.seqno
							if cid in self.__cidtbl:
								cli = self.__cidtbl[cid]
								cli._pinput(header, url, param, data, True)

			if self.__listen in elist:
				sock, addr = self.__listen.accept()
				if sock:
					if self.__ssl_ctx:
						ssock = self.__ssl_ctx.wrap_socket(sock, server_side = True, do_handshake_on_connect = False)
						with self._lock:
							self.__ssl_hslist.append({ 'ssock': ssock, 'addr': addr[0], 'wakeup': False, 'success': False })
							request_ssl_handshake(ssock, SERVER_SSL_HS_TIMEOUT, self.__ssl_hs_wakeup)
					else:
						self.__add_client(sock, addr[0])

			for cli in self._priocli:
				if cli._sock in elist:
					try:
						buf = cli._sock.recv(parser.VSOA_MAX_PACKET_LENGTH)
					except ssl.SSLWantReadError:
						buf = True
					except:
						buf = None

					if buf and type(buf) == bytes:
						if not cli._precv(buf):
							buf = None

					if not buf:
						sockopt.linger(cli._sock, 0)
						cli._finish()

						if cli._onconn:
							cli._onconn = False
							self.onclient(cli, False)

						with self._lock:
							self._priocli.remove(cli)
							del self.__cidtbl[cli.id]
							if cli in self.__hslist:
								self.__hslist.remove(cli)

			for stream in self._streams:
				sock = stream._sock()
				if sock in elist:
					stream._event(sock)

			if self._strevt[0] in elist:
				sockopt.event_read(self._strevt[0])

				wakeup_list = []
				with self._lock:
					for ssl_hs_cli in self.__ssl_hslist:
						if ssl_hs_cli['wakeup']:
							wakeup_list.append(ssl_hs_cli)

					for ssl_hs_cli in wakeup_list:
						self.__ssl_hslist.remove(ssl_hs_cli)

				for ssl_hs_cli in wakeup_list:
					if ssl_hs_cli['success']:
						self.__add_client(ssl_hs_cli['ssock'], ssl_hs_cli['addr'])
					else:
						ssl_hs_cli['ssock'].close()

# Global mutex
server_lock = threading.Lock()

# All servers
server_list : list[Server] = []

# Server timer
def __server_timer() -> None:
	period = float(SERVER_TIMER_PERIOD) / 1000

	while True:
		time.sleep(period)

		with server_lock:
			for server in server_list:
				server._hstimer()

# Server timer thread
server_thread = threading.Thread(name = 'py_vsoa_srvtmr', target = __server_timer, daemon = True)
server_thread.start()

# Exports
__all__ = [ 'Server' ]

# Information
__doc__ = 'VSOA server module, provide `Server` class.'

# end
