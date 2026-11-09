import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import timeutil  # noqa: E402
from config import Settings  # noqa: E402
from database.db import Database  # noqa: E402
from database.models import Repository  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_clock():
    yield
    timeutil.set_now_provider(None)


@pytest.fixture
def test_settings(tmp_path):
    return Settings(
        database_path=tmp_path / "agent.db",
        download_dir=tmp_path / "downloads",
        backup_dir=tmp_path / "backups",
        log_dir=tmp_path / "logs",
        profile_dir=tmp_path / "profile",
        headless=True,
        whatsapp_provider="console",
        enable_scheduler=False,
        student_name="",
        allowed_numbers=[],
        recipient_number="",
    )


@pytest.fixture
def repo(tmp_path):
    return Repository(Database(tmp_path / "unit.db"))
