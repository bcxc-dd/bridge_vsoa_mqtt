from __future__ import annotations

#
# Copyright (c) 2024 ACOAUTO Team.
# All rights reserved.
#
# Detailed license information can be found in the LICENSE file.
#
# File: parser.py Vehicle SOA parser.
#
# Author: Han.hui <hanhui@acoinfo.com>
#

import vsoa.interface as interface
import sys, struct

# Prefer using ujson library
try:
	import ujson
	json = ujson
except ImportError:
	import json

# VSOA header length
VSOA_HDR_LENGTH = 20

# VSOA max packet length (32 bits align)
VSOA_MAX_PACKET_LENGTH = 262144

# VSOA max payload length
VSOA_MAX_DATA_LENGTH = (VSOA_MAX_PACKET_LENGTH - VSOA_HDR_LENGTH)

# VSOA max quick packet length (MAX UDP packet length)
VSOA_MAX_QPACKET_LENGTH = 65507

# VSOA max payload length on quick channel
VSOA_MAX_QDATA_LENGTH = (VSOA_MAX_QPACKET_LENGTH - VSOA_HDR_LENGTH)

# VSOA magic and version
VSOA_MAGIC     = 0x9
VSOA_VERSION   = 0x2
VSOA_MAGIC_VER = (VSOA_MAGIC | (VSOA_VERSION << 4))

# VSOA header types
VSOA_TYPE_SERVINFO    = 0x00
VSOA_TYPE_RPC         = 0x01
VSOA_TYPE_SUBSCRIBE   = 0x02
VSOA_TYPE_UNSUBSCRIBE = 0x03
VSOA_TYPE_PUBLISH     = 0x04
VSOA_TYPE_DATAGRAM    = 0x05
VSOA_TYPE_QOSSETUP    = 0x06
VSOA_TYPE_NOOP        = 0xfe
VSOA_TYPE_PINGECHO    = 0xff

# VSOA header flags
VSOA_FLAG_REPLY  = 0x1
VSOA_FLAG_TUNNEL = 0x2
VSOA_FLAG_SET    = 0x4

# VSOA header pad
VSOA_PAD_MASK  = 0xc0
VSOA_PAD_SHIFT = 6

# VSOA status code
VSOA_STATUS_SUCCESS        = 0
VSOA_STATUS_PASSWORD       = 1
VSOA_STATUS_ARGUMENTS      = 2
VSOA_STATUS_INVALID_URL    = 3
VSOA_STATUS_NO_RESPONDING  = 4
VSOA_STATUS_NO_PERMISSIONS = 5
VSOA_STATUS_NO_MEMORY      = 6

# Header size bytearray with zero fill
VSOA_ZERO_HEADER = bytearray(VSOA_HDR_LENGTH)

# Get PAD size
def get_pad(packet: bytes | bytearray) -> int:
	return ((packet[2] & VSOA_PAD_MASK) >> VSOA_PAD_SHIFT)

# Set PAD size
def set_pad(packet: bytearray, pad: int) -> None:
	packet[2] |= (((pad << VSOA_PAD_SHIFT)) & VSOA_PAD_MASK)

# Send buffer packer
class Packer:
	def __init__(self) -> None:
		self.__lens = [0, 0, 0]
		self.__sbuf = bytearray(VSOA_MAX_PACKET_LENGTH)

	# Build header and clear URL and payload
	def header(self, type: int, flags: int, status: int, seqno: int) -> bytearray:
		self.__lens = [0, 0, 0]
		self.__sbuf[0:VSOA_HDR_LENGTH] = VSOA_ZERO_HEADER
		struct.pack_into('>BBBBI', self.__sbuf, 0, VSOA_MAGIC_VER, type, flags, status, seqno)
		return self.__sbuf

	# Add URL to send buffer
	def url(self, url: str) -> bytearray:
		lens = self.__lens
		if lens[1] or lens[2]:
			raise Exception('URL must add before payload')

		burl = url.encode()
		blen = len(burl)
		if blen > VSOA_MAX_DATA_LENGTH:
			raise ValueError('URL length too long')

		lens[0] = blen
		struct.pack_into(f'>HII{blen}s', self.__sbuf, 10, blen, 0, 0, burl)
		return self.__sbuf

	# Add payload to send buffer
	def payload(self, payload: dict | interface.Payload, param: object | dict | list | str | bytes | bytearray = None, data: bytes | bytearray = None) -> bytearray:
		lens = self.__lens
		if lens[1] or lens[2]:
			raise Exception('Payload has been added')

		if payload:
			if isinstance(payload, dict):
				param = payload['param'] if 'param' in payload else bytes()
				data  = payload['data']  if 'data'  in payload else bytes()
			else:
				param = payload.param if hasattr(payload, 'param') else bytes()
				data  = payload.data  if hasattr(payload, 'data')  else bytes()

		if isinstance(param, (dict, list, str)):
			param = json.dumps(param, separators = (',', ':')).encode()
		elif not isinstance(param, (bytes, bytearray)):
			param = bytes()
		lens[1] = len(param)

		if not isinstance(data, (bytes, bytearray)):
			data = bytes()
		lens[2] = len(data)

		if (lens[0] + lens[1] + lens[2]) > VSOA_MAX_DATA_LENGTH:
			raise ValueError('Payload length too long')

		if param:
			start = VSOA_HDR_LENGTH + lens[0]
			end   = start + lens[1]
			self.__sbuf[start:end] = param

		if data:
			start = VSOA_HDR_LENGTH + lens[0] + lens[1]
			end   = start + lens[2]
			self.__sbuf[start:end] = data

		struct.pack_into('>II', self.__sbuf, 12, lens[1], lens[2])
		return self.__sbuf

	# Set tunid
	def tunid(self, tunid: int) -> None:
		packet = self.__sbuf
		if tunid > 0 and tunid < 65536:
			packet[2] |= VSOA_FLAG_TUNNEL
		else:
			tunid = 0
			packet[2] &= ~VSOA_FLAG_TUNNEL

		struct.pack_into('>H', self.__sbuf, 8, tunid)
		return packet

	# Calculate packet length and set pad
	def packet(self, fixed = True) -> tuple[bytearray, int]:
		lens  = self.__lens
		total = VSOA_HDR_LENGTH + lens[0] + lens[1] + lens[2]

		unalign = total & 0x3
		if unalign > 0:
			pad = 0x3 - unalign + 1
			total += pad
			set_pad(self.__sbuf, pad)

		sbuf = self.__sbuf[0:total] if fixed else self.__sbuf
		return (sbuf, total)

# Input unpacker
class Unpacker:
	def __init__(self, raw: bool = False) -> None:
		self.raw    = raw
		self.__tot  = 0
		self.__rbuf = bytearray()

	# Get packet total length
	def __ptotal(self) -> int:
		pad   = get_pad(self.__rbuf)
		t     = struct.unpack_from('>HII', self.__rbuf, 10)
		return VSOA_HDR_LENGTH + t[0] + t[1] + t[2] + pad

	# TCP socket input
	def input(self, packet: bytes | bytearray, callback: callable) -> bool:
		rbuf = self.__rbuf
		rbuf.extend(packet)

		while True:
			cur = len(rbuf)
			if cur < VSOA_HDR_LENGTH:
				return True
			elif rbuf[0] != VSOA_MAGIC_VER:
				return False

			if self.__tot == 0:
				self.__tot = self.__ptotal()
				if self.__tot > VSOA_MAX_PACKET_LENGTH:
					return False

			if cur < self.__tot:
				return True
			else:
				try:
					header, url, param, data = Unpacker.pinput(rbuf, self.raw)
				except:
					print("Unexpected error:", sys.exc_info()[1])
					return False
				callback(header, url, param, data)
				del rbuf[0:self.__tot]
				self.__tot = 0

	# UDP socket input
	@staticmethod
	def pinput(packet: bytes | bytearray, raw: bool = False) -> tuple[interface.Header, str, dict | bytes, bytes]:
		try:
			t = struct.unpack_from('>BBBBIHHII', packet, 0)
		except:
			raise Exception(f'Bad packet received: {packet.hex()}')
		if t[0] != VSOA_MAGIC_VER:
			raise Exception(f'Bad packet magic or version: {hex(t[0])}')

		header = interface.Header(t[1], t[2] & ~VSOA_PAD_MASK, t[3], t[4], t[5])

		offset = VSOA_HDR_LENGTH
		if t[6]:
			url     = packet[offset:offset + t[6]].decode()
			offset += t[6]
		else:
			url = None

		if t[7]:
			param = packet[offset:offset + t[7]]
			offset += t[7]
			if (not raw) or (t[1] != VSOA_TYPE_SERVINFO and t[1] != VSOA_TYPE_RPC and \
							 t[1] != VSOA_TYPE_PUBLISH  and t[1] != VSOA_TYPE_DATAGRAM):
				try:
					param = json.loads(param)
				except:
					pass
		else:
			param = None

		if t[8]:
			data = packet[offset:offset + t[8]]
		else:
			data = None

		return (header, url, param, data)

# Information
__doc__ = 'VSOA parser module, provide `Packer` and `Unpacker` class.'

# end
