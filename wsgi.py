"""Gunicorn/WGI 入口，保持现有 Flask 业务逻辑不变。"""

from app import app


application = app
