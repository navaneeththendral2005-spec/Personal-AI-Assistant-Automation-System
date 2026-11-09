"""Offline API integration smoke tests for Phase 5 release readiness."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import gmail_intelligence, gmail_sync_router


def _app_with_gmail_routers():
    app = FastAPI()
    app.include_router(gmail_sync_router.router)
    app.include_router(gmail_intelligence.router)

    class FakeDB:
        def rollback(self):
            pass

    def fake_db():
        yield FakeDB()

    app.dependency_overrides[gmail_sync_router.get_sync_db] = fake_db
    app.dependency_overrides[gmail_intelligence.get_db] = fake_db
    return app


def test_sync_and_analyze_route_connects_sync_then_analysis(monkeypatch):
    calls = []

    def fake_sync(db, *, max_results):
        calls.append(("sync", max_results))
        return {"status": "success", "inserted": 2}

    def fake_analyze(db, *, limit):
        calls.append(("analyze", limit))
        return {"status": "success", "analyzed": 2}

    monkeypatch.setattr("backend.services.gmail_sync_service.sync_gmail_messages", fake_sync)
    monkeypatch.setattr(gmail_intelligence, "analyze_stored_emails", fake_analyze)

    with TestClient(_app_with_gmail_routers()) as client:
        response = client.post("/gmail/intelligence/sync-and-analyze?max_results=2")

    assert response.status_code == 200
    assert response.json() == {
        "sync": {"status": "success", "inserted": 2},
        "analysis": {"status": "success", "analyzed": 2},
    }
    assert calls == [("sync", 2), ("analyze", 2)]


def test_sync_and_analyze_returns_safe_error_without_provider_details(monkeypatch, caplog):
    def fail_sync(db, *, max_results):
        raise RuntimeError("private provider detail must not reach the client")

    monkeypatch.setattr("backend.services.gmail_sync_service.sync_gmail_messages", fail_sync)

    with TestClient(_app_with_gmail_routers()) as client:
        response = client.post("/gmail/intelligence/sync-and-analyze?max_results=1")

    assert response.status_code == 502
    assert response.json()["detail"] == "Gmail sync or email analysis failed. Check server logs."
    assert "private provider detail" not in response.text
    assert "private provider detail" not in caplog.text
    assert "error_type=RuntimeError" in caplog.text


def test_sync_route_rejects_out_of_range_batch_size():
    with TestClient(_app_with_gmail_routers()) as client:
        response = client.post("/gmail/sync?max_results=0")

    assert response.status_code == 422


def test_reminder_logs_do_not_include_task_title(monkeypatch, caplog):
    from datetime import datetime, timezone
    import logging

    from backend.services import reminder_service

    class DueTask:
        id = 17
        title = "PRIVATE EMAIL SUBJECT SHOULD NOT APPEAR IN LOGS"
        due_at = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
        updated_at = None
        reminder_sent = False

    task = DueTask()

    class ScalarResult:
        def all(self):
            return [task]

    class FakeSession:
        def scalars(self, statement):
            return ScalarResult()
        def commit(self):
            pass
        def rollback(self):
            pass
        def close(self):
            pass

    monkeypatch.setattr(reminder_service, "SessionLocal", FakeSession)
    with caplog.at_level(logging.WARNING, logger="friday.reminders"):
        assert reminder_service.check_due_reminders() == 1

    assert "task_id=17" in caplog.text
    assert task.title not in caplog.text
    assert task.reminder_sent is True


def test_alembic_schema_upgrades_and_downgrades_on_isolated_sqlite(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "phase5_migrations.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")

    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    command.upgrade(config, "head")

    # The final revision must create every index declared by the ORM models.
    from sqlalchemy import create_engine, inspect

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    inspector = inspect(engine)
    task_indexes = {item["name"] for item in inspector.get_indexes("tasks")}
    assert {"ix_tasks_title", "ix_tasks_is_completed", "ix_tasks_due_at"} <= task_indexes

    # Keep the unique constraints and their separately declared lookup indexes
    # aligned with the historical migrations (rather than replacing them with
    # unique indexes during autogenerate).
    email_constraints = {
        item["name"] for item in inspector.get_unique_constraints("emails")
    }
    analysis_constraints = inspector.get_unique_constraints("email_analyses")
    assert "uq_emails_gmail_message_id" in email_constraints
    assert any(item["column_names"] == ["email_id"] for item in analysis_constraints)

    email_indexes = {item["name"]: item for item in inspector.get_indexes("emails")}
    analysis_indexes = {
        item["name"]: item for item in inspector.get_indexes("email_analyses")
    }
    assert not email_indexes["ix_emails_gmail_message_id"]["unique"]
    assert not analysis_indexes["ix_email_analyses_email_id"]["unique"]
    engine.dispose()

    command.downgrade(config, "base")
    command.upgrade(config, "head")
