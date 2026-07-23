from __future__ import annotations

#
# Copyright (c) 2024 ACOAUTO Team.
# All rights reserved.
#
# Detailed license information can be found in the LICENSE file.
#
# File: timer.py Vehicle SOA timer.
#
# Author: Han.hui <hanhui@acoinfo.com>
#

import time, threading

# Timer class
class Timer:
	def __init__(self) -> None:
		self._inqueue  = False
		self._count    = 0.0
		self._interval = 0.0
		self._callback = None
		self._args     = None
		self._kwargs   = None

	# Finish timer (private method and called in timer lock state)
	def _finish(self, adj: bool = True) -> None:
		global timer_last_time
		self._inqueue  = False

		if adj:
			im = timer_working.index(self)
			try:
				tr = timer_working[im + 1]
				tr._count += self._count
			except:
				tr = None

			if im > 0:
				try:
					timer_working[im - 1]
				except:
					pass
				else:
					cur_time = time.monotonic()
					diff = cur_time - timer_last_time
					timer_last_time = cur_time

					if tr:
						if tr._count > diff:
							tr._count -= diff
						else:
							tr._count = 0

		timer_working.remove(self)

	# Start working (private method and called in timer lock state)
	def _start(self) -> None:
		global timer_last_time
		self._inqueue = True

		if len(timer_working) > 0:
			first = timer_working[0]
			cur_time = time.monotonic()
			diff = cur_time - timer_last_time
			timer_last_time = cur_time

			if first._count > diff:
				first._count -= diff
			else:
				first._count = 0

			for i, t in enumerate(timer_working):
				if self._count >= t._count:
					self._count -= t._count
					if i + 1 == len(timer_working):
						timer_working.append(self)
						break

				else:
					timer_working.insert(i, self)
					t._count -= self._count
					break

		else:
			timer_working.append(self)
			timer_last_time = time.monotonic()

	# Start timer
	def start(self, timeout: float, callback: callable, interval: float = 0, args = (), kwargs = {}) -> None:
		if timeout < 0:
			raise ValueError('Timeout must > 0')
		if not callable(callback):
			raise TypeError('Callback must callable')

		with timer_lock:
			if self._inqueue:
				self._finish()

			self._count    = timeout
			self._interval = interval
			self._callback = callback
			self._args     = args
			self._kwargs   = kwargs
			self._start()

		timer_event.release()

	# Stop timer
	def stop(self) -> None:
		with timer_lock:
			if self._inqueue:
				self._finish()

	# Is working
	def is_started(self) -> bool:
		with timer_lock:
			return self._inqueue

# Timer last time
timer_last_time = 0.0

# Timer working
timer_working: list[Timer] = []

# Timer loop thread
timer_thread = None

# Timer global lock
timer_lock = threading.Lock()

# Timer wait event
timer_event = threading.Semaphore(value = 0)

# Timer thread
def __timer_loop():
	global timer_last_time
	t: Timer
	count: float
	no_timer: bool
	cur_time: float

	while True:
		timer_lock.acquire()

		timer_last_time = time.monotonic()

		if len(timer_working):
			t        = timer_working[0]
			count    = t._count
			no_timer = False
		else:
			count    = 999999.0
			no_timer = True

		if count > 0:
			timer_lock.release()

			timer_event.acquire(timeout = count)
			if no_timer:
				continue

			timer_lock.acquire()

			cur_time = time.monotonic()
			count = cur_time - timer_last_time
			timer_last_time = cur_time

		while len(timer_working):
			t = timer_working[0]
			if t._count > count:
				t._count -= count
				break

			count -= t._count
			t._count = 0
			t._finish(adj = False)

			if t._interval > 0:
				t._count = t._interval
				t._start()

			timer_lock.release()
			t._callback(*t._args, **t._kwargs)
			timer_lock.acquire()

			cur_time = time.monotonic()
			if cur_time > timer_last_time:
				count += cur_time - timer_last_time
				timer_last_time = cur_time

		timer_lock.release()

# Start loop
timer_thread = threading.Thread(name = 'py_vsoa_tloop', target = __timer_loop, daemon = True)
timer_thread.start()

# Exports
__all__ = [ 'Timer' ]

# Information
__doc__ = 'VSOA timer module, provide `Timer` class.'

# end
