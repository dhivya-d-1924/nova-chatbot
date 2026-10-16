"""Start the real backend (serving frontend/dist) wired to the fake OpenAI-compatible server, on a fresh database.
Used by the Playwright end-to-end tests:  python tests/e2e_server.py [port]
"""
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

from tests.fake_llm import serve_in_thread  # noqa: E402

port = int(sys.argv[1]) if len(sys.argv) > 1 else 5055
llm_url, _ = serve_in_thread()
db_dir = tempfile.mkdtemp(prefix="nova-e2e-")
origin = f"http://localhost:{port}"
os.environ.update({
    "APP_ENV": "development", "DATABASE_URL": f"sqlite:///{db_dir}/e2e.db", "LLM_API_KEY": "e2e", "LLM_BASE_URL": llm_url,
    "LLM_MODEL": "fake-model", "LLM_MAX_RETRIES": "0", "APP_URL": origin, "ALLOWED_ORIGINS": f"{origin},http://127.0.0.1:{port}",
    "SEARCH_PROVIDER": "none", "RATE_LIMIT_PER_MINUTE": "1000", "AUTH_RATE_LIMIT_PER_MINUTE": "1000",
    "REGISTER_RATE_LIMIT_PER_HOUR": "1000", "API_RATE_LIMIT_PER_MINUTE": "100000", "UPLOAD_RATE_LIMIT_PER_MINUTE": "1000",
    "SECRET_KEY": "e2e-" + "x" * 40, "LOG_LEVEL": "WARNING",
    "BOOTSTRAP_FIRST_ADMIN": "false", "ADMIN_EMAILS": "admin@e2e.test",
})
from nova import create_app  # noqa: E402
from nova.config import settings  # noqa: E402

app = create_app({"_SKIP_ENV_FILE": "1"})
print(f"e2e server on {origin} (fake LLM {llm_url}, database in {db_dir}, env {settings.APP_ENV})", flush=True)
app.run(host="127.0.0.1", port=port, threaded=True)
