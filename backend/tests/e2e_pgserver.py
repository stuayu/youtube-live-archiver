"""Run an isolated pgserver instance for E2E when no PostgreSQL URL is supplied."""
from pathlib import Path
import signal
import sys
from pgserver import get_server

server = get_server(Path(sys.argv[1]), cleanup_mode="delete")
uri = server.get_uri("postgres").replace("postgresql://", "postgresql+psycopg://", 1)
print(uri, flush=True)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
signal.pause()
