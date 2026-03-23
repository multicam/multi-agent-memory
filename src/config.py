"""Configuration from environment variables."""

import os
from dataclasses import dataclass


@dataclass
class Config:
    pg_url: str
    nas_path: str
    server_port: int
    server_host: str

    @classmethod
    def from_env(cls) -> "Config":
        pg_url = os.environ.get("PG_URL")
        if not pg_url:
            raise ValueError("PG_URL environment variable is required")

        return cls(
            pg_url=pg_url,
            nas_path=os.environ.get("NAS_PATH", "/mnt/memory"),
            server_port=int(os.environ.get("SERVER_PORT", "8888")),
            server_host=os.environ.get("SERVER_HOST", "0.0.0.0"),
        )
