"""Production server settings:  gunicorn -c gunicorn.conf.py app:app"""
import os

bind = os.getenv("BIND", "0.0.0.0:8000")
workers = int(os.getenv("WEB_CONCURRENCY", "2"))
worker_class = "gthread"            # threads keep long-lived streaming replies from blocking a worker
threads = int(os.getenv("GUNICORN_THREADS", "8"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "300"))   # agent runs can take minutes; heartbeats keep proxies alive
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
forwarded_allow_ips = os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1")
control_socket_disable = True       # gunicorn 26 control socket is not used and its default path is not writable in Docker
limit_request_line = 8190
