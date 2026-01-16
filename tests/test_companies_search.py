from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.main import app, session_dep
from app.models import Company


def build_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def override_session(session: Session):
    def _override():
        try:
            yield session
        finally:
            session.close()

    return _override


def test_companies_search_matches_name_and_excludes_others() -> None:
    session = build_session()
    session.add(Company(name="Acme Studio", website="https://acme.example", notes="Brand partner"))
    session.add(Company(name="Beta Labs", website="https://beta.example", notes="Research partner"))
    session.commit()

    app.dependency_overrides[session_dep] = override_session(session)
    try:
        client = TestClient(app)
        response = client.get("/companies?q=Acme")
        assert response.status_code == 200
        assert "Acme Studio" in response.text
        assert "Beta Labs" not in response.text
    finally:
        app.dependency_overrides.clear()


def test_companies_search_matches_website_and_notes() -> None:
    session = build_session()
    session.add(Company(name="Signal Co", website="https://signal.example", notes="Brand refresh"))
    session.add(Company(name="Gamma Works", website="https://gamma.example", notes="Onboarding"))
    session.commit()

    app.dependency_overrides[session_dep] = override_session(session)
    try:
        client = TestClient(app)
        website_response = client.get("/companies?q=signal.example")
        assert website_response.status_code == 200
        assert "Signal Co" in website_response.text
        assert "Gamma Works" not in website_response.text

        notes_response = client.get("/companies?q=refresh")
        assert notes_response.status_code == 200
        assert "Signal Co" in notes_response.text
        assert "Gamma Works" not in notes_response.text
    finally:
        app.dependency_overrides.clear()
