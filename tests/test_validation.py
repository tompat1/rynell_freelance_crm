from __future__ import annotations

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.main import leads_create, projects_create, tasks_create
from app.models import Lead, Project, Task


def build_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_leads_ignore_invalid_date_and_budget() -> None:
    session = build_session()
    response = leads_create(
        title="Bad Lead",
        status="NEW",
        source="",
        value_estimate="not-a-number",
        company_id=None,
        contact_id=None,
        next_step="",
        due_date="not-a-date",
        notes="",
        session=session,
    )
    assert response.status_code == 303
    lead = session.exec(select(Lead)).one()
    assert lead.value_estimate is None
    assert lead.due_date is None
    session.close()


def test_projects_ignore_invalid_dates_and_budget() -> None:
    session = build_session()
    response = projects_create(
        name="Bad Project",
        status="ACTIVE",
        company_id=None,
        contact_id=None,
        start_date="invalid-start",
        end_date="invalid-end",
        budget="invalid-budget",
        notes="",
        session=session,
    )
    assert response.status_code == 303
    project = session.exec(select(Project)).one()
    assert project.start_date is None
    assert project.end_date is None
    assert project.budget is None
    session.close()


def test_tasks_ignore_invalid_due_dates() -> None:
    session = build_session()
    project = Project(name="Test Project")
    session.add(project)
    session.commit()
    session.refresh(project)

    response = tasks_create(
        project_id=project.id,
        title="Bad Task",
        due_date="invalid-date",
        status="TODO",
        notes="",
        session=session,
    )
    assert response.status_code == 303
    task = session.exec(select(Task)).one()
    assert task.due_date is None
    session.close()
