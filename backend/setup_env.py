"""One-time helper: writes backend/.env and tests the AI provider.
Run from the backend folder (with the virtual environment active):  python setup_env.py
"""
import getpass
import secrets
from pathlib import Path

from openai import OpenAI

PROVIDERS = {
    "1": ("OpenAI", "", "gpt-4o-mini"),
    "2": ("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-2.0-flash"),
    "3": ("OpenRouter", "https://openrouter.ai/api/v1", "openai/gpt-4o-mini"),
    "4": ("Groq", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "5": ("Ollama (local, no key)", "http://localhost:11434/v1", "llama3.2"),
}

print("\nWhich provider is your API key from?")
for k, (name, _, _) in PROVIDERS.items():
    print(f"  {k}) {name}")
choice = input("Enter 1-5: ").strip()
if choice not in PROVIDERS:
    raise SystemExit("Please run again and enter a number from 1 to 5.")
name, base_url, model = PROVIDERS[choice]

key = "ollama" if choice == "5" else getpass.getpass("Paste your API key (it will not show as you type): ").strip().strip('"')
if not key:
    raise SystemExit("No key entered.")
m = input(f"Model name [{model}] (press Enter to keep): ").strip()
model = m or model

print(f"\nTesting {name} with model '{model}' ...")
ok = False
try:
    reply = OpenAI(api_key=key, base_url=base_url or None, timeout=30).chat.completions.create(
        model=model, max_tokens=10, messages=[{"role": "user", "content": "Say hi"}])
    print("SUCCESS - the provider replied:", (reply.choices[0].message.content or "").strip())
    ok = True
except Exception as e:  # noqa: BLE001
    print("FAILED:", type(e).__name__, "-", e)
    print("Fix the problem above (wrong key / provider / model, or no credit) and run this script again.")

if ok:
    env = Path(__file__).parent / ".env"
    example = (Path(__file__).parent / ".env.example").read_text(encoding="utf-8").splitlines()
    skip = ("LLM_API_KEY=", "LLM_MODEL=", "LLM_BASE_URL=", "SECRET_KEY=")
    rest = [line for line in example if not line.startswith(skip)]
    lines = [f"LLM_API_KEY={key}", f"LLM_BASE_URL={base_url}", f"LLM_MODEL={model}",
             f"SECRET_KEY={secrets.token_hex(32)}", *rest]
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        env.chmod(0o600)
    except OSError:
        pass
    print("\nSaved backend/.env (readable only by you). Now start the backend with:  python app.py")
