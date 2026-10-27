"""Entry point for the local Flask demo."""

import atexit

from webapp import create_app


app = create_app()
atexit.register(lambda: app.extensions["logstore"].close())


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
