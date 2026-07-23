from __future__ import annotations

#
# Copyright (c) 2024 ACOAUTO Team.
# All rights reserved.
#
# Detailed license information can be found in the LICENSE file.
#
# File: position.py Vehicle SOA position.
#
# Author: Han.hui <hanhui@acoinfo.com>
#

import vsoa.parser as parser
import vsoa.sockopt as sockopt
import socket, select, ipaddress, os, platform

# Packet buffer size
MAX_QUERY_BUFFER = 4096

# Packet check
def check(server, chkname = False) -> None:
	if type(server) != dict:
		raise TypeError('Packet error')
	if chkname and (type(server['name']) != str or not server['name']):
		raise TypeError('Invalid name')
	if type(server['port']) != int or server['port'] < 0 or server['port'] > 65535:
		raise ValueError('Invalid port')
	if 'security' in server and type(server['security']) != bool:
		raise TypeError('Invalid security')

	addr = ipaddress.ip_address(server['addr'])
	if server['domain'] == socket.AF_INET:
		if addr.version != 4:
			raise ValueError('Address IP error, should be IPv4')
	elif server['domain'] == socket.AF_INET6:
		if addr.version != 6:
			raise ValueError('Address IP error, should be IPv6')

# Position server class
class Position:
	def __init__(self, onquery: callable) -> None:
		self.onquery = onquery

	# Position server start
	def __start(self, host: str, port: int) -> None:
		family = socket.AF_INET if host.find(':') < 0 else socket.AF_INET6
		addr   = (host, port)

		self.__psock = sockopt.create(family, 'udp', nonblock = False)
		self.__psock.bind(addr)

	# Event loop
	def run(self, host: str, port: int) -> None:
		self.__start(host, port)

		while True:
			rlist = [ self.__psock ]
			elist, _, _ = select.select(rlist, (), ())
			if self.__psock in elist:
				buf, remote = self.__psock.recvfrom(MAX_QUERY_BUFFER)
				if buf:
					def reply(result) -> None:
						if result:
							check(result)
						else:
							result = {}
						self.__psock.sendto(parser.json.dumps(result).encode(), remote)

					try:
						query = parser.json.loads(buf)
						assert(query['name'] and type(query['name'] == str))
					except:
						print('Warning: VSOA position query packet error, from:', remote[0])
					else:
						self.onquery(query, reply)

# VSOA position query timeout
VSOA_QUERY_TIMEOUT = 2

# VSOA position query max try count
VSOA_QUERY_MAX_CNT = 2

# Position query server (tuple[addr, port])
vsoa_server_addr = None

# Set position server
def pos(addr: str, port: int) -> None:
	ipaddress.ip_address(addr)
	if port < 1 or port > 65535:
		raise ValueError('Invalid port')

	global vsoa_server_addr
	vsoa_server_addr = (addr, port)

# Query
def query(name: str, pserver: tuple[str, int] | str, domain: int) -> tuple[str, int]:
	family = socket.AF_INET if pserver[0].find(':') < 0 else socket.AF_INET6
	psock  = sockopt.create(family, 'udp', nonblock = False)
	packet = parser.json.dumps({ 'name': name, 'domain': family })
	
	try:
		psock.sendto(packet.encode(), pserver)
	except:
		psock.close()
		return None

	rlist = (psock, )
	elist, _, _ = select.select(rlist, (), (), float(VSOA_QUERY_TIMEOUT))
	if psock in elist:
		buf, remote = psock.recvfrom(MAX_QUERY_BUFFER)
		psock.close()
		if not buf or remote[0] != pserver[0]:
			return None
	else:
		psock.close()
		return None

	try:
		answer = parser.json.loads(buf)
		assert(type(answer['addr'] == str) and type(answer['port'] == int))
		ipaddress.ip_address(answer['addr'])
	except:
		return None
	else:
		return (answer['addr'], answer['port'])

# Default position server config file
if platform.system() == 'Windows':
	POS_SERVER_CONF = 'C:\\Windows\\System32\\drivers\\etc\\vsoa.pos'
else:
	POS_SERVER_CONF = '/etc/vsoa.pos'

# Query server list
def qlist(name: str, confs: str, splite: str, domain: int) -> tuple[str, int]:
	servers = []
	for each in confs:
		try:
			if splite in each:
				l = each.split(splite)
				servers.append((l[0], int(l[1])))
			else:
				servers.append((each, 54))
		except:
			pass
	for server in servers:
		answer = query(name, server, domain)
		if answer:
			return answer
	return None

# VSOA host query
def lookup(name: str, domain: int = -1) -> tuple[str, int]:
	if type(name) != str or not name:
		raise TypeError('Invalid server name')

	if vsoa_server_addr:
		return query(name, vsoa_server_addr, domain)

	if 'VSOA_POS_SERVER' in os.environ:
		enservs = os.environ['VSOA_POS_SERVER'].split(',')
		answer  = qlist(name, enservs, ':', domain)
		if answer:
			return answer

	if os.path.exists(POS_SERVER_CONF):
		with open(POS_SERVER_CONF) as file:
			config = file.read()

		if config:
			lines  = config.splitlines()
			answer = qlist(name, lines, ' ', domain)
			if answer:
				return answer

# Exports
__all__ = [ 'Position', 'pos', 'lookup' ]

# Information
__doc__ = 'VSOA position related functions.'

# end
