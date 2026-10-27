"""A key written in .env must reach the provider layer (it used to be invisible)."""

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_env_file_keys_configure_providers(tmp_path):
    (tmp_path / ".env").write_text(
        "GROQ_API_KEY=gsk_fake_key_1234567890\nOLLAMA_API_KEY=ollama_fake_1234567890\n"
    )
    code = (
        "from backend.providers.secret_broker import configured_providers, get_handle\n"
        "print('groq' in configured_providers(), bool(get_handle('ollama').auth_headers()))\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    env["PYTHONPATH"] = str(REPO)
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True,
        timeout=60, check=True,
    ).stdout.split()
    assert out == ["True", "True"]
