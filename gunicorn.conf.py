"""
Gunicorn production config for low-spec servers.

目标：
1. 避免单 worker 单点故障；
2. 通过 max_requests 周期性回收 worker，缓解长期运行后的内存膨胀；
3. 控制并发规模，避免 2C/4G 机器因 worker 过多而被 OOM。
"""

import multiprocessing
import os


def getenv_int(name, default):
    value = os.environ.get(name, str(default)).strip()
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# 在 2 核 4G 场景下默认保守启用 2 个 worker，避免单点故障。
cpu_count = multiprocessing.cpu_count()
workers = getenv_int("GUNICORN_WORKERS", min(2, max(1, cpu_count)))

# 采用 gthread，保持 Flask 同步业务逻辑不变，同时给每个 worker 少量线程处理轻量并发。
worker_class = os.environ.get("GUNICORN_WORKER_CLASS", "gthread")
threads = getenv_int("GUNICORN_THREADS", 2)

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:5500")
backlog = getenv_int("GUNICORN_BACKLOG", 2048)
timeout = getenv_int("GUNICORN_TIMEOUT", 120)
graceful_timeout = getenv_int("GUNICORN_GRACEFUL_TIMEOUT", 30)
keepalive = getenv_int("GUNICORN_KEEPALIVE", 5)

# 周期性回收 worker，降低长时间运行后的内存碎片和隐性泄漏风险。
max_requests = getenv_int("GUNICORN_MAX_REQUESTS", 800)
max_requests_jitter = getenv_int("GUNICORN_MAX_REQUESTS_JITTER", 100)

# 当前应用存在运行时缓存和连接池，保持 preload_app 关闭以避免 fork 后资源状态复杂化。
preload_app = False
reuse_port = False
daemon = False

accesslog = os.environ.get("GUNICORN_ACCESS_LOG", "-")
errorlog = os.environ.get("GUNICORN_ERROR_LOG", "-")
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
capture_output = True


def when_ready(server):
    server.log.info(
        "Gunicorn ready. workers=%s threads=%s bind=%s max_requests=%s jitter=%s",
        workers,
        threads,
        bind,
        max_requests,
        max_requests_jitter,
    )


def post_fork(server, worker):
    worker.log.info("Worker spawned. pid=%s", worker.pid)


def worker_exit(server, worker):
    worker.log.info("Worker exit. pid=%s", worker.pid)
