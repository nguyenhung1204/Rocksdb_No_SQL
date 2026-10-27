"""Entry point for the local Flask demo."""

import atexit
import os

from webapp import create_app
from waitress import serve


app = create_app()
atexit.register(lambda: app.extensions["logstore"].close())


def server_options(environ=None):
    """Read bounded Waitress settings while keeping the default loopback-only."""
    env = os.environ if environ is None else environ
    host = env.get("LOGSTORE_HOST", "127.0.0.1").strip()
    if not host:
        raise ValueError("LOGSTORE_HOST khong duoc de trong")
    try:
        port = int(env.get("LOGSTORE_PORT", "5000"))
        threads = int(env.get("LOGSTORE_THREADS", "8"))
    except (TypeError, ValueError) as error:
        raise ValueError("LOGSTORE_PORT va LOGSTORE_THREADS phai la so nguyen") from error
    if not 1 <= port <= 65535:
        raise ValueError("LOGSTORE_PORT phai trong khoang 1-65535")
    if not 1 <= threads <= 64:
        raise ValueError("LOGSTORE_THREADS phai trong khoang 1-64")
    return {"host": host, "port": port, "threads": threads}


if __name__ == "__main__":
    try:
        serve(app, **server_options())
    except ValueError as error:
        raise SystemExit(str(error)) from error
