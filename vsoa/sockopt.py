from __future__ import annotations

#
# Copyright (c) 2024 ACOAUTO Team.
# All rights reserved.
#
# Detailed license information can be found in the LICENSE file.
#
# File: sockopt.py Vehicle SOA socket common library.
#
# Author: Han.hui <hanhui@acoinfo.com>
#

import socket, ssl, struct, select
from platform import system

# Fixed socket.MSG_DONTWAIT in other OS
if not hasattr(socket, 'MSG_DONTWAIT'):
	setattr(socket, 'MSG_DONTWAIT', 0)

# Fixed socket.MSG_NOSIGNAL in other OS
if not hasattr(socket, 'MSG_NOSIGNAL'):
	if system() == 'Darwin':
		setattr(socket, 'MSG_NOSIGNAL', 0x80000)
	else:
		setattr(socket, 'MSG_NOSIGNAL', 0)

# Create socket
def create(family: int, proto: str, nonblock: bool = True, server: bool = False) -> socket.socket:
	param = (socket.SOCK_STREAM, socket.IPPROTO_TCP) \
			if proto == 'tcp' else (socket.SOCK_DGRAM, socket.IPPROTO_UDP)
	if hasattr(socket, 'SOCK_NONBLOCK'):
		s = socket.socket(family, param[0] | \
				(socket.SOCK_NONBLOCK if nonblock else 0), param[1])
	else:
		s = socket.socket(family, param[0], param[1])
		if s and nonblock:
			s.setblocking(True)
	en = 1
	if proto == 'tcp':
		s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, en)
	if server:
		s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, en)
	return s

# Socket set linger time
def linger(s: socket.socket | ssl.SSLSocket, time: int = 0) -> None:
	param = struct.pack('ii', 1, time)
	try:
		s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, param)
	except:
		pass

# Socket set priority
def priority(s: socket.socket | ssl.SSLSocket, prio: int) -> None:
	dscp = prio << 5
	if dscp:
		dscp |= 0x08
	s.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, dscp)

# Socket set keepalive
def keepalive(s: socket.socket | ssl.SSLSocket, idle: int, mcnt: int = 3) -> None:
	s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE,  idle)
	s.setsockopt(socket.SOL_TCP,    socket.TCP_KEEPINTVL, idle)
	s.setsockopt(socket.SOL_TCP,    socket.TCP_KEEPCNT,   mcnt)

# TCP socket shutdown
def tcpshutdown(s: socket.socket | ssl.SSLSocket, how: int = socket.SHUT_RDWR) -> None:
	try:
		s.shutdown(how)
	except:
		pass

# TCP socket send
def tcpsend(s: socket.socket | ssl.SSLSocket, packet: bytes | bytearray, total: int = 0, timeout: float = -1) -> int:
	flags = 0 if isinstance(s, ssl.SSLSocket) else socket.MSG_NOSIGNAL
	alrdy = 0

	while True:
		if timeout > 0:
			r, w, e = select.select((), (s, ), (), timeout)
		try:
			num = s.send(packet, flags)
		except:
			num = -1

		if num > 0:
			alrdy += num
			if alrdy >= total:
				break
			else:
				packet = packet[alrdy:]
		else:
			linger(s, 0)
			tcpshutdown(s)
			break

	return alrdy

# Default event data
DEF_EVENT_DATA = bytes(4)

# Create event pair
def pair() -> tuple[socket.socket, socket.socket]:
	return socket.socketpair()

# Event read
def event_read(sread: socket.socket) -> bytes:
	return sread.recv(4, socket.MSG_DONTWAIT)

# Event emit
def event_emit(swrite: socket.socket, data: bytes | bytearray = None) -> None:
	if not data:
		data = DEF_EVENT_DATA
	swrite.send(data, socket.MSG_NOSIGNAL)

# Information
__doc__ = 'VSOA socket operation additional functions.'

# end
