"""Gunicorn entry point. Referenced by deploy/punt-web.service."""

from app import create_app

app = create_app()
