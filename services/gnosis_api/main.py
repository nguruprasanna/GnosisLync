"""ASGI entrypoint: ``uvicorn gnosis_api.main:app``."""

from gnosis_api.app import create_app

app = create_app()
