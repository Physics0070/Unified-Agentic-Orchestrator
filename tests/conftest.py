"""Keep knowledge-workspace tests off the real (or developer's) database."""

import pytest

from backend.config import settings
from backend.rag import knowledge_base


@pytest.fixture(autouse=True)
def _isolated_knowledge_storage(monkeypatch, tmp_path):
    """Knowledge uploads persist to the EventStore database; point the global
    knowledge_base at a throwaway sqlite file so no test touches Turso or data/."""
    monkeypatch.setattr(settings, "turso_database_url", f"file:{tmp_path / 'knowledge.db'}")
    monkeypatch.setattr(settings, "turso_auth_token", "")
    knowledge_base._spaces.clear()
    yield
    knowledge_base._spaces.clear()
