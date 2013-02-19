#!/usr/bin/env python
#
# Copyright (c) 2011-2013, Shopkick Inc.
# All rights reserved.
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# ---
# Author: John Egan <john@shopkick.com>

import functools
import threading
import traceback
import linecache
import os.path
import socket
import sys
import urllib2
import warnings


import flawless.client.default
import flawless.lib.config
import flawless.server.api as api


config = flawless.lib.config.get()

MAX_STACK_REPR = 500
MAX_LOCALS = 100
NUM_FRAMES_TO_SAVE = 20

def _send_request(req):
  f = urllib2.urlopen(req, timeout=config.client_timeout)
  f.close()

def _get_backend_host():
  return config.flawless_hostport or flawless.client.default.hostport

def set_hostport(hostport):
  flawless.client.default.hostport = hostport

def record_error(hostname, tb, exception_message, preceding_stack=None,
                 error_threshold=None, additional_info=None):
  ''' Helper function to record errors to the flawless backend '''
  try:
    stack = []
    while tb is not None:
      stack.append(tb)
      tb = tb.tb_next

    stack_lines = []
    for row in preceding_stack or []:
      stack_lines.append(
        api.StackLine(filename= os.path.abspath(row[0]), line_number=row[1],
                      function_name=row[2], text=row[3])
      )

    for index, tb in enumerate(stack):
      filename = tb.tb_frame.f_code.co_filename
      func_name = tb.tb_frame.f_code.co_name
      lineno = tb.tb_lineno
      line = linecache.getline(filename, lineno, tb.tb_frame.f_globals)
      frame_locals = None
      if index >= (len(stack) - NUM_FRAMES_TO_SAVE):
        # Include some limits on max string length & number of variables to keep things from getting
        # out of hand
        frame_locals = dict((k, repr(v)[:MAX_STACK_REPR]) for k,v in
                            tb.tb_frame.f_locals.items()[:MAX_LOCALS] if k != "self")
        if "self" in tb.tb_frame.f_locals and hasattr(tb.tb_frame.f_locals["self"], "__dict__"):
          frame_locals.update(dict(("self." + k, repr(v)[:MAX_STACK_REPR]) for k,v in
                              tb.tb_frame.f_locals["self"].__dict__.items()[:MAX_LOCALS] if k != "self"))

      # TODO (john): May need to prepend site-packages to filename to get correct path
      stack_lines.append(
        api.StackLine(filename=os.path.abspath(filename), line_number=lineno,
                      function_name=func_name, text=line, frame_locals=frame_locals)
      )

    data = api.RecordErrorRequest(
        traceback=stack_lines,
        exception_message=exception_message,
        hostname=hostname,
        error_threshold=error_threshold,
        additional_info=additional_info,
    )

    req = urllib2.Request(url="http://%s/record_error" % _get_backend_host(),
                          data=data.dumps())
    _send_request(req)
  except:
    raise


def _safe_wrap(func):
  safe_attrs = []
  for attr in functools.WRAPPER_ASSIGNMENTS:
    if hasattr(func, attr):
      safe_attrs.append(attr)
  return functools.wraps(func, safe_attrs)

def _wrap_function_with_error_decorator(func,
                                        save_current_stack_trace=True,
                                        reraise_exception=True,
                                        error_threshold=None):
  preceding_stack = []
  if save_current_stack_trace:
    preceding_stack = traceback.extract_stack()

  @_safe_wrap(func)
  def wrapped_func_with_error_reporting(*args, **kwargs):
    if not _get_backend_host():
      warnings.warn("flawless server hostport not set", RuntimeWarning, stacklevel=2)
    try:
      return func(*args, **kwargs)
    except:
      type, value, tb = sys.exc_info()

      # Check to try and prevent multiple reports of the same exception
      if hasattr(value, "_flawless_already_caught"):
        if reraise_exception:
          raise value, None, tb
        else:
          return

      # Get trackback & report it
      hostname = socket.gethostname()
      record_error(
          hostname=hostname,
          tb=tb,
          preceding_stack=preceding_stack,
          exception_message=repr(value),
          error_threshold=error_threshold)

      # Reraise exception if so desired
      if reraise_exception:
        setattr(value, "_flawless_already_caught", True)
        raise value, None, tb
  return wrapped_func_with_error_reporting
