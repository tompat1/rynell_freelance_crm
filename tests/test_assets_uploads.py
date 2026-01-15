from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app import main
from app.main import app, session_dep
from app.models import Asset, Contact, Project


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


def test_assets_upload_skips_duplicates(tmp_path: Path) -> None:
    session = build_session()
    app.dependency_overrides[session_dep] = override_session(session)
    main.UPLOAD_DIR = tmp_path
    main.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    try:
        client = TestClient(app)
        file_data = [("files", ("dup.png", b"dupdata", "image/png"))]
        response = client.post("/assets/upload", files=file_data, allow_redirects=False)
        assert response.status_code == 303

        duplicate_response = client.post("/assets/upload", files=file_data, allow_redirects=False)
        assert duplicate_response.status_code == 303
        assert duplicate_response.headers["location"] == "/assets?duplicate=1"
        assets = session.exec(select(Asset).order_by(Asset.id)).all()
        assert len(assets) == 1
        assert (main.UPLOAD_DIR / assets[0].stored_path).exists()
    finally:
        app.dependency_overrides.clear()


def test_assets_delete_removes_file(tmp_path: Path) -> None:
    session = build_session()
    asset_path = tmp_path / "stored.png"
    asset_path.write_bytes(b"delete-me")
    asset = Asset(
        filename="stored.png",
        stored_path=asset_path.name,
        mime_type="image/png",
        size_bytes=asset_path.stat().st_size,
        created_at=main.now_utc(),
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)

    app.dependency_overrides[session_dep] = override_session(session)
    main.UPLOAD_DIR = tmp_path

    try:
        client = TestClient(app)
        response = client.post(f"/assets/{asset.id}/delete", data={"next_url": "/assets"}, allow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/assets"
        assert not asset_path.exists()
        assert session.exec(select(Asset)).first() is None
    finally:
        app.dependency_overrides.clear()


def test_assets_filtering_and_view_mode(tmp_path: Path) -> None:
    session = build_session()
    project = Project(name="Filter Project")
    contact = Contact(first_name="Ada", last_name="Lovelace")
    session.add(project)
    session.add(contact)
    session.commit()
    session.refresh(project)
    session.refresh(contact)

    image_asset = Asset(
        filename="hero.png",
        stored_path="hero.png",
        mime_type="image/png",
        size_bytes=10,
        tags="branding",
        project_id=project.id,
        created_at=main.now_utc(),
    )
    doc_asset = Asset(
        filename="brief.pdf",
        stored_path="brief.pdf",
        mime_type="application/pdf",
        size_bytes=20,
        contact_id=contact.id,
        created_at=main.now_utc(),
    )
    session.add(image_asset)
    session.add(doc_asset)
    session.commit()

    app.dependency_overrides[session_dep] = override_session(session)
    main.UPLOAD_DIR = tmp_path

    try:
        client = TestClient(app)
        response = client.get("/assets?file_type=image")
        assert response.status_code == 200
        assert "hero.png" in response.text
        assert "brief.pdf" not in response.text

        list_response = client.get("/assets?view=list")
        assert list_response.status_code == 200
        assert "uploaded" in list_response.text
    finally:
        app.dependency_overrides.clear()
