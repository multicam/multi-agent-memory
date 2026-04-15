"""Module-level entrypoint for `python -m src`.

Forwards to src.server:main(), which reads Config from env, connects to
PostgreSQL, loads the embedding model, and starts the FastMCP server on
the configured host/port.
"""

from src.server import main

main()
