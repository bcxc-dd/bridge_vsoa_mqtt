from __future__ import annotations

#
# Copyright (c) 2024 ACOAUTO Team.
# All rights reserved.
#
# Detailed license information can be found in the LICENSE file.
#
# File: interface.py Vehicle SOA interface define.
#
# Author: Han.hui <hanhui@acoinfo.com>
#

# Header
class Header:
	def __init__(self, type: int, flags: int, status: int, seqno: int, tunid: int) -> None:
		self.type   = type
		self.flags  = flags
		self.status = status
		self.seqno  = seqno
		self.tunid  = tunid

	# Get item
	def __getitem__(self, item):
		return getattr(self, item)

	# Keys
	def keys(self) -> list[str]:
		return ['type', 'flags', 'status', 'seqno', 'tunid']

# Request
class Request:
	def __init__(self, url: str, seqno: int, method: int) -> None:
		self.url    = url
		self.seqno  = seqno
		self.method = method
		self.mwdata = {}

	# Get item
	def __getitem__(self, item):
		return getattr(self, item)

	# Keys
	def keys(self) -> list[str]:
		return ['url', 'seqno', 'method', 'mwdata']

# Payload
class Payload:
	def __init__(self, param: object | dict | list | str | bytes | bytearray = None, data: bytes | bytearray = None) -> None:
		self.param = param
		self.data  = data

	# Get item
	def __getitem__(self, item):
		return getattr(self, item)

		# Keys
	def keys(self) -> list[str]:
		return ['param', 'data']

# end
