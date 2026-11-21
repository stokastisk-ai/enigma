"""Vercel entrypoint: Vercel's Python runtime serves the top-level ASGI ``app``."""
from enigma_api.main import create_app

app = create_app()
