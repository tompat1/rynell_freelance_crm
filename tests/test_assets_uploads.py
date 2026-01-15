from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app import main
from app.main import app, session_dep
from app.models import Asset, Project


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


def test_assets_upload_accepts_multiple_files(tmp_path: Path) -> None:
    session = build_session()
    app.dependency_overrides[session_dep] = override_session(session)
    main.UPLOAD_DIR = tmp_path
    main.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    try:
        client = TestClient(app)
        files = [
            ("files", ("one.png", b"pngdata", "image/png")),
            ("files", ("two.png", b"morepng", "image/png")),
        ]
        response = client.post("/assets/upload", files=files, data={"tags": "test"}, allow_redirects=False)

        assert response.status_code == 303
        assets = session.exec(select(Asset).order_by(Asset.id)).all()
        assert len(assets) == 2
        assert all(Path(main.UPLOAD_DIR / a.stored_path).exists() for a in assets)
    finally:
        app.dependency_overrides.clear()


def test_project_assets_upload_links_project(tmp_path: Path) -> None:
    session = build_session()
    project = Project(name="Demo Project")
    session.add(project)
    session.commit()
    session.refresh(project)

    app.dependency_overrides[session_dep] = override_session(session)
    main.UPLOAD_DIR = tmp_path
    main.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    try:
        client = TestClient(app)
        files = [
            ("files", ("project.png", b"imgdata", "image/png")),
            ("files", ("second.png", b"imgdata2", "image/png")),
        ]
        response = client.post(
            f"/projects/{project.id}/assets/upload",
            files=files,
            data={"tags": "project"},
            allow_redirects=False,
        )

        assert response.status_code == 303
        assets = session.exec(select(Asset).order_by(Asset.id)).all()
        assert len(assets) == 2
        assert all(a.project_id == project.id for a in assets)
    finally:
        app.dependency_overrides.clear()
