from __future__ import annotations

#
# Copyright (c) 2024 ACOAUTO Team.
# All rights reserved.
#
# Detailed license information can be found in the LICENSE file.
#
# File: workqueue.py Vehicle SOA workqueue.
#
# Author: Han.hui <hanhui@acoinfo.com>
#

import threading

# WorkQueue job
class Job:
	def __init__(self, func: callable, args: tuple, kwargs: dict) -> None:
		self.func   = func
		self.args   = args
		self.kwargs = kwargs

# WorkQueue class
class WorkQueue:
	def __init__(self) -> None:
		# Worker job
		self.__jobs : list[Job] = []

		# Worker lock
		self.__lock = threading.Lock()

		# Worker job add semphore
		self.__job_sync = threading.Semaphore(value = 0)

		# Create worker thread
		self.__worker = threading.Thread(name = 'py_vsoa_wq', target = self.__worker_loop, daemon = True)
		self.__worker.start()

	# Worker loop
	def __worker_loop(self) -> None:
		while True:
			self.__job_sync.acquire()
			with self.__lock:
				if len(self.__jobs):
					job = self.__jobs.pop(0)
				else:
					continue
			job.func(*job.args, **job.kwargs)

	# Job add
	def add(self, func: callable, args = (), kwargs = {}) -> None:
		with self.__lock:
			job = Job(func, args, kwargs)
			self.__jobs.append(job)
			self.__job_sync.release()

	# Job add if not queued
	def add_if_not_queued(self, func: callable, args = (), kwargs = {}) -> bool:
		with self.__lock:
			for job in self.__jobs:
				if job.func == func:
					return False
			job = Job(func, args, kwargs)
			self.__jobs.append(job)
			self.__job_sync.release()
		return True

	# Job delete
	def delete(self, func: callable) -> bool:
		with self.__lock:
			for i, job in enumerate(self.__jobs):
				if job.func == func:
					self.__jobs.pop(i)
					return True
		return False

	# Is the specified job in the queue?
	def is_queued(self, func: callable) -> bool:
		with self.__lock:
			for job in self.__jobs:
				if job.func == func:
					return True
		return False

# Exports
__all__ = [ 'WorkQueue' ]

# Information
__doc__ = 'VSOA workqueue module, provide `WorkQueue` class.'

# end
