from __future__ import annotations

from pathlib import Path
from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy import text

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "crm.db"
DATA_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False, connect_args={"check_same_thread": False})

def ensure_contact_flag_columns() -> None:
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(contact)"))
        existing_columns = {row[1] for row in result}
        missing_columns = []
        if "is_lead" not in existing_columns:
            missing_columns.append("ALTER TABLE contact ADD COLUMN is_lead BOOLEAN NOT NULL DEFAULT 0")
        if "is_prospect" not in existing_columns:
            missing_columns.append("ALTER TABLE contact ADD COLUMN is_prospect BOOLEAN NOT NULL DEFAULT 0")
        for statement in missing_columns:
            conn.execute(text(statement))
        if missing_columns:
            conn.commit()

def ensure_company_flag_columns() -> None:
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(company)"))
        existing_columns = {row[1] for row in result}
        missing_columns = []
        if "is_lead" not in existing_columns:
            missing_columns.append("ALTER TABLE company ADD COLUMN is_lead BOOLEAN NOT NULL DEFAULT 0")
        if "is_prospect" not in existing_columns:
            missing_columns.append("ALTER TABLE company ADD COLUMN is_prospect BOOLEAN NOT NULL DEFAULT 0")
        if "is_magazine" not in existing_columns:
            missing_columns.append("ALTER TABLE company ADD COLUMN is_magazine BOOLEAN NOT NULL DEFAULT 0")
        if "is_newspaper" not in existing_columns:
            missing_columns.append("ALTER TABLE company ADD COLUMN is_newspaper BOOLEAN NOT NULL DEFAULT 0")
        for statement in missing_columns:
            conn.execute(text(statement))
        if missing_columns:
            conn.commit()

def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)
    ensure_contact_flag_columns()
    ensure_company_flag_columns()

def get_session() -> Session:
    return Session(engine)
