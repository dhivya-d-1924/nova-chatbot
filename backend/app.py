"""Entry point.  Development: python app.py   Production: gunicorn -c gunicorn.conf.py app:app
The application itself lives in the nova/ package."""
import os
import sys

from nova import create_app
from nova.config import ConfigError, settings

try:
    app = create_app()
except ConfigError as e:
    print(f"\n!! {e}\n", file=sys.stderr)
    raise SystemExit(2)

if __name__ == "__main__":
    if settings.IS_PROD:
        print("Refusing to use the development server in production. Run: gunicorn -c gunicorn.conf.py app:app")
        raise SystemExit(2)
    # HOST=0.0.0.0 lets phones and tablets on your network reach the dev server (see README)
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=settings.PORT, debug=settings.FLASK_DEBUG, threaded=True)
