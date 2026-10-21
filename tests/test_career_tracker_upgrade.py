from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base
from backend.models import EmailMessage
from backend.models_career import CareerOpportunity
from backend.services.career_service import career_summary, list_follow_ups


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    now = datetime.now(timezone.utc)
    email1 = EmailMessage(gmail_message_id="career-overdue", thread_id="c1", sender="careers@example.com",
                          recipient="user@example.com", subject="Developer role", snippet="Follow up", labels=[])
    email2 = EmailMessage(gmail_message_id="career-upcoming", thread_id="c2", sender="hr@example.com",
                          recipient="user@example.com", subject="Internship", snippet="Application", labels=[])
    session.add_all([email1, email2]); session.flush()
    session.add_all([
        CareerOpportunity(email_id=email1.id, company="Example", role_title="Developer", opportunity_type="JOB",
                          status="APPLIED", follow_up_at=now - timedelta(days=1)),
        CareerOpportunity(email_id=email2.id, company="Example Labs", role_title="Intern", opportunity_type="INTERNSHIP",
                          status="INTERESTED", follow_up_at=now + timedelta(days=3)),
    ])
    session.commit()
    try:
        yield session
    finally:
        session.close(); Base.metadata.drop_all(engine); engine.dispose()


def test_career_summary_reports_pipeline_and_follow_up_health(db):
    result = career_summary(db)
    assert result["total_opportunities"] == 2
    assert result["open_pipeline"] == 2
    assert result["overdue_follow_ups"] == 1
    assert result["upcoming_follow_ups_next_7_days"] == 1
    assert result["by_type"] == {"JOB": 1, "INTERNSHIP": 1}


def test_follow_up_list_labels_overdue_and_upcoming(db):
    result = list_follow_ups(db, window_days=7)
    assert {item["follow_up_state"] for item in result} == {"OVERDUE", "UPCOMING"}
    assert all("Review the original email" in item["note"] for item in result)


def test_follow_up_window_is_bounded(db):
    with pytest.raises(ValueError):
        list_follow_ups(db, window_days=100)
