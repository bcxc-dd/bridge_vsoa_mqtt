from __future__ import annotations

#
# Copyright (c) 2024 ACOAUTO Team.
# All rights reserved.
#
# Detailed license information can be found in the LICENSE file.
#
# File: events.py Python event emiter.
#
# Author: Han.hui <hanhui@acoinfo.com>
#

# EventEmitter
class EventEmitter:
	def __init__(self) -> None:
		self.__events: dict[any, list] = {}

	# Add listerner internal
	def __add(self, event, listener: callable, once: bool) -> None:
		if self.__events is None:
			self.__events = {}

		self.emit('new_listener', args = (event, listener))
		work = (listener, once)
		if event in self.__events:
			self.__events[event].append(work)
		else:
			self.__events[event] = [ work ]

	# Add listerner alias
	def on(self, event, listener: callable) -> None:
		return self.__add(event, listener, False)

	# Add listerner once
	def once(self, event, listener: callable) -> None:
		return self.__add(event, listener, True)

	# Add listerner
	def add_listener(self, event, listener: callable) -> None:
		return self.__add(event, listener, False)

	# Remove listerner
	def remove_listener(self, event, listener: callable = None) -> bool:
		if self.__events is None:
			return False
		if event not in self.__events:
			return False

		howmany = len(self.__events[event])
		for i in range(howmany - 1, -1, -1):
			work = self.__events[event][i]
			if listener is None or work[0] == listener:
				self.emit('remove_listener', args = (event, work[0]))
				self.__events[event].pop(i)

		if event in self.__events and len(self.__events[event]) == 0:
			del self.__events[event]

	# Remove all listeners
	def remove_all_listeners(self, event = None) -> None:
		if self.__events is None:
			return

		if event:
			self.remove_listener(event)
		else:
			for event in self.__events.keys():
				self.remove_listener(event)

	# Get listener count
	def listener_count(self, event) -> int:
		if self.__events is None:
			return 0
		if event not in self.__events:
			return 0

		return len(self.__events[event])

	# Get listeners
	def listeners(self, event) -> list[callable]:
		if self.__events is None:
			return []
		if event not in self.__events:
			return []

		listeners = []
		for work in self.__events[event]:
			listeners.append(work[0])
		return listeners

	# Emit event
	def emit(self, event, args = (), kwargs = {}) -> bool:
		if self.__events is None:
			return False
		if event not in self.__events:
			return False

		ret = False
		for work in self.__events[event]:
			listener = work[0]
			once     = work[1]
			if once:
				self.emit('remove_listener', args = (event, work[0]))
				self.__events[event].remove(work)

			listener(*args, **kwargs)
			ret = True

		if event in self.__events and len(self.__events[event]) == 0:
			del self.__events[event]
		return ret

# Exports
__all__ = [ 'EventEmitter' ]

# Information
__doc__ = 'VSOA EventEmitter module, provide `EventEmitter` class.'

# end
