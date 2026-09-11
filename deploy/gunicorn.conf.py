"""Gunicorn config for the PUNT web service. Referenced by punt-web.service.

Two settings here are load-bearing and must not be changed without reading why.
"""

import os

bind = os.environ.get("BIND_ADDR", "127.0.0.1:8011")

# ONE worker. Not a resource decision: the live feed's poller, its event engine's
# dedupe set and its Moment buffer are all per-process. A second worker would
# poll ESPN a second time, keep its own commentary history, and give half the
# phones in the room a different feed from the other half. Scaling out means
# moving that state into Redis first, and ten people do not need it.
workers = 1

# Threads, because the SSE stream holds a connection open for the whole
# afternoon. With the default sync worker, ten phones on /stream would occupy
# every worker thread permanently and nothing else would ever be served. Twenty
# four is comfortably more than a ten-team league plus the bar screen.
worker_class = "gthread"
threads = 24

# Long, because an SSE connection is *supposed* to sit idle between events. The
# default 30s would cut the stream on every quiet stretch of a Sunday evening
# and the client would reconnect, forever.
timeout = 300
graceful_timeout = 30
keepalive = 65

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")
proc_name = "punt-web"
