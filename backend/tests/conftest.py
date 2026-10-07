import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ.setdefault("DISCOVERY_SCHEDULER_ENABLED", "false")
# Generated resume files go to a throwaway folder, never the real data directory.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="unjob-test-data-")
for _name in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "TAVILY_API_KEY"):
    os.environ.pop(_name, None)

import pytest  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.discovery.http import reset_politeness_state  # noqa: E402


@pytest.fixture(autouse=True)
async def fresh_database():
    reset_politeness_state()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    # Each test runs on its own event loop; never reuse a connection across loops.
    await engine.dispose()
    reset_politeness_state()
